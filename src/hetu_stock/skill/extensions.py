"""Third-party extension management (phase 5, stage 06, design §4).

Install only saves and validates a candidate (disabled by default).  The
registry records real package locations, integrity digests, provided and
required package IDs, and per-host enablement bindings.  Published versions
are read-only; updates prepare a new candidate.  ``context_for_host`` is the
only research-facing surface and returns metadata of enabled, intact,
compatible candidates — never package bodies.

Nothing here executes package content: scripts, hooks, install requirements,
undeclared files, symlink escapes and out-of-scope capability requests are
refused at validation time.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import Any

from hetu_stock.skill.package import SkillValidationError
from hetu_stock.skill.work_packages import (
    CORE_WORK_PACKAGE_IDS,
    load_work_package,
)

CURRENT_COMPAT = "hetu-skill-v1"
ALLOWED_CAPABILITIES = frozenset({"local-files-read", "public-web-read"})
SCRIPT_SUFFIXES = frozenset(
    {
        ".py",
        ".sh",
        ".bash",
        ".zsh",
        ".js",
        ".ts",
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".jar",
        ".ps1",
        ".bat",
        ".cmd",
    }
)
HOOK_COMPONENTS = frozenset({"hooks", "hook"})
INSTALL_REQUIREMENT_NAMES = frozenset(
    {"install", "setup", "requirements.txt", "package.json"}
)
_EXTENSION_ID = re.compile(r"^x\.[a-z0-9][a-z0-9-]*\.[a-z0-9][a-z0-9-]*$")
_RESERVED_ID = re.compile(r"^(W\d+|WX)(\..*)?$")
HOSTS = ("codex", "claude", "opencode", "zcode")


class ExtensionError(Exception):
    """An extension candidate or registry operation is refused."""


@dataclass(frozen=True)
class ExtensionSummary:
    id: str
    version: str
    source: str
    summary: str
    compatibility: str
    capabilities: tuple[str, ...]
    files: dict[str, str]
    provides: tuple[str, ...]
    requires: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "source": self.source,
            "summary": self.summary,
            "compatibility": self.compatibility,
            "capabilities": list(self.capabilities),
            "files": dict(self.files),
            "provides": list(self.provides),
            "requires": list(self.requires),
        }


def extensions_root() -> Path:
    data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(data_home) if data_home else Path.home() / ".local" / "share"
    return base / "hetu-stock" / "extensions"


def _registry_path(root: Path | None = None) -> Path:
    return (root or extensions_root()) / "registry.json"


def _empty_registry() -> dict[str, Any]:
    return {"extensions": {}, "bindings": {host: {} for host in HOSTS}}


def _read_registry(root: Path | None = None) -> dict[str, Any]:
    path = _registry_path(root)
    if not path.is_file():
        return _empty_registry()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExtensionError(f"登记表不可读: {path} ({exc})") from exc
    if not isinstance(data, dict) or "extensions" not in data:
        raise ExtensionError(f"登记表格式无效: {path}")
    data.setdefault("bindings", {})
    for host in HOSTS:
        data["bindings"].setdefault(host, {})
    return data


class _registry_lock:
    def __init__(self, root: Path) -> None:
        self._root = root

    def __enter__(self) -> _registry_lock:
        self._root.mkdir(parents=True, exist_ok=True)
        self._handle = (self._root / "registry.lock").open("a+")
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc_info: object) -> None:
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()


def _commit_registry(root: Path, registry: dict[str, Any]) -> None:
    payload = json.dumps(registry, ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix="registry.", suffix=".tmp", dir=str(root)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, _registry_path(root))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def _load_metadata(source: Path) -> dict[str, Any]:
    source = Path(source)
    manifest = source / "extension.json"
    if not manifest.is_file():
        raise ExtensionError(f"缺少 extension.json: {source}")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExtensionError(f"extension.json 不可读: {exc}") from exc
    if not isinstance(data, dict):
        raise ExtensionError("extension.json 必须是 JSON 对象")
    return data


def _check_declared_paths(source: Path, declared: list[str]) -> dict[str, str]:
    """Verify declared files exist, stay inside the package, and digest them.

    Returns ``{relative_path: sha256}`` including ``extension.json``.
    """
    files: dict[str, str] = {}
    seen: set[str] = set()
    for relative in [*declared, "extension.json"]:
        if not isinstance(relative, str) or not relative:
            raise ExtensionError("文件定位必须是相对路径字符串")
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise ExtensionError(f"不安全路径: {relative}")
        normalized = path.as_posix()
        if normalized in seen:
            raise ExtensionError(f"重复声明的文件: {normalized}")
        seen.add(normalized)
        target = source / path
        if target.is_symlink():
            raise ExtensionError(f"符号链接越界被拒绝: {normalized}")
        if not target.is_file():
            raise ExtensionError(f"声明的文件不存在: {normalized}")
        files[normalized] = hashlib.sha256(target.read_bytes()).hexdigest()
    return files


def _check_package_surface(source: Path, declared: set[str]) -> None:
    """Reject undeclared files, scripts, hooks and install requirements."""
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if any(part in HOOK_COMPONENTS for part in relative.parts):
            raise ExtensionError(f"包含安装钩子，超出内容边界: {relative.as_posix()}")
        if not path.is_file() and not path.is_dir():
            raise ExtensionError(f"特殊文件被拒绝: {relative.as_posix()}")
        if path.is_symlink():
            raise ExtensionError(f"符号链接越界被拒绝: {relative.as_posix()}")
        if not path.is_file():
            continue
        normalized = relative.as_posix()
        if normalized not in declared:
            raise ExtensionError(f"未声明的文件: {normalized}")
        if path.suffix.lower() in SCRIPT_SUFFIXES or path.stem.lower() in {
            name.split(".")[0] for name in INSTALL_REQUIREMENT_NAMES
        }:
            raise ExtensionError(
                f"包含程序或脚本，超出内容边界: {normalized}"
            )


def _resolve_provides_requires(
    source: Path, work_package_paths: list[str], known_provides: set[str]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Load declared work packages and derive provided/required package IDs.

    Hard dependencies must be core IDs, IDs of packages inside the same
    extension, or IDs provided by extensions already registered; revisit
    relations are never hard dependencies.
    """
    provided: list[str] = []
    requires: set[str] = set()
    for relative in work_package_paths:
        try:
            spec = load_work_package(source / relative)
        except SkillValidationError as exc:
            raise ExtensionError(f"工作包校验失败 ({relative}): {exc}") from exc
        if spec.kind != "third-party" or spec.coverage_role != "supplemental":
            raise ExtensionError(
                f"第三方工作包 kind/coverage_role 非法: {spec.id}"
            )
        if Path(relative).stem != spec.id:
            raise ExtensionError(f"文件名与工作包 ID 不一致: {relative}")
        provided.append(spec.id)
        requires.update(spec.start_requires)
        requires.update(spec.finalize_requires)
    provided_set = set(provided)
    if len(provided_set) != len(provided):
        raise ExtensionError("同一扩展内工作包 ID 重复")
    unknown = [
        dep
        for dep in sorted(requires)
        if dep not in set(CORE_WORK_PACKAGE_IDS) | provided_set | known_provides
    ]
    if unknown:
        raise ExtensionError(f"硬依赖缺失: {', '.join(unknown)}")
    # 硬依赖环路（回访关系不作为硬依赖参与）。
    edges: dict[str, set[str]] = {wp_id: set() for wp_id in provided}
    for relative in work_package_paths:
        spec = load_work_package(source / relative)
        edges[spec.id] = {
            dep
            for dep in (*spec.start_requires, *spec.finalize_requires)
            if dep in provided_set
        }
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(node: str, trail: tuple[str, ...]) -> None:
        if node in done:
            return
        if node in visiting:
            cycle = " -> ".join((*trail, node))
            raise ExtensionError(f"硬依赖存在环: {cycle}")
        visiting.add(node)
        for dep in sorted(edges[node]):
            visit(dep, (*trail, node))
        visiting.discard(node)
        done.add(node)

    for wp_id in provided:
        visit(wp_id, ())
    return tuple(provided), tuple(
        dep for dep in sorted(requires) if dep not in CORE_WORK_PACKAGE_IDS
    )


def _registered_provides() -> set[str]:
    with contextlib.suppress(ExtensionError):
        registry = _read_registry()
        return {
            wp
            for entry in registry["extensions"].values()
            for record in entry["versions"].values()
            for wp in record.get("provides", [])
        }
    return set()


def _checked_version(version: Any) -> str:
    """版本只能是安全的单一路径段：拒绝绝对路径、分隔符、"."、".."。

    版本之后会拼接成安装目标并参与清理，越界值可使安装流程删除受管
    目录外的文件，因此在校验入口直接拒绝。
    """
    if not isinstance(version, str):
        raise ExtensionError(f"版本必须是字符串: {version!r}")
    candidate = version.strip()
    if (
        not candidate
        or candidate in {".", ".."}
        or Path(candidate).is_absolute()
        or "/" in candidate
        or "\\" in candidate
    ):
        raise ExtensionError(f"版本必须是安全的单一路径段: {version!r}")
    return candidate


def _managed_version_dir(root: Path, extension_id: str, version: str) -> Path:
    """install/update 共用的受管版本目录；创建、复制、清理前确认目标边界。

    扩展目录本身若是链接，解析后必须仍是受管根内的真实目录，否则安装
    合法版本也会删除受管范围外的同名目录。
    """
    root_resolved = root.resolve()
    base = (root_resolved / extension_id).resolve()
    target = base / version
    if base != root_resolved / extension_id or target.parent != base:
        raise ExtensionError(f"版本目标越出受管目录，拒绝: {extension_id}@{version}")
    return target


def validate_extension(source: Path) -> dict[str, Any]:
    """Validate a candidate package and return its checked summary.

    Never executes package content; unsafe candidates raise ExtensionError.
    Hard dependencies may be satisfied by extensions already registered.
    """
    source = Path(source)
    if not source.is_dir():
        raise ExtensionError(f"资料包目录不存在: {source}")
    metadata = _load_metadata(source)

    extension_id = metadata.get("id")
    if not isinstance(extension_id, str) or not _EXTENSION_ID.match(extension_id):
        raise ExtensionError(f"扩展 ID 必须是 x.<命名空间>.<名称>: {extension_id!r}")
    if _RESERVED_ID.match(extension_id):
        raise ExtensionError(f"扩展 ID 与核心或官方 ID 冲突: {extension_id}")

    version = _checked_version(metadata.get("version"))
    summary = metadata.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise ExtensionError(f"摘要损坏或缺失: {summary!r}")
    package_source = metadata.get("source")
    if not isinstance(package_source, str) or not package_source.strip():
        raise ExtensionError(f"来源必须是字符串: {package_source!r}")
    compatibility = metadata.get("compatibility")
    if not isinstance(compatibility, str) or not compatibility.strip():
        raise ExtensionError(f"兼容性声明必须是字符串: {compatibility!r}")

    capabilities = metadata.get("capabilities")
    if not isinstance(capabilities, list) or any(
        not isinstance(item, str) for item in capabilities
    ):
        raise ExtensionError("能力声明必须是字符串列表")
    overflow = [item for item in capabilities if item not in ALLOWED_CAPABILITIES]
    if overflow:
        raise ExtensionError(f"请求的权限超出范围: {', '.join(overflow)}")

    work_packages = metadata.get("work_packages")
    if (
        not isinstance(work_packages, list)
        or not work_packages
        or any(not isinstance(item, str) for item in work_packages)
    ):
        raise ExtensionError("work_packages 必须是非空相对路径列表")
    references = metadata.get("references", [])
    if not isinstance(references, list) or any(
        not isinstance(item, str) for item in references
    ):
        raise ExtensionError("references 必须是相对路径列表")

    declared = [*work_packages, *references]
    files = _check_declared_paths(source, declared)
    _check_package_surface(source, {*declared, "extension.json"})
    provides, requires = _resolve_provides_requires(
        source, work_packages, _registered_provides()
    )

    checked = ExtensionSummary(
        id=extension_id,
        version=version,
        source=package_source.strip(),
        summary=summary.strip(),
        compatibility=compatibility.strip(),
        capabilities=tuple(capabilities),
        files=files,
        provides=provides,
        requires=requires,
    )
    return checked.as_dict()


def _stored_integrity_ok(entry: dict[str, Any]) -> bool:
    base = Path(str(entry.get("path", "")))
    recorded = entry.get("files")
    if not base.is_dir() or not isinstance(recorded, dict):
        return False
    for relative, digest in recorded.items():
        target = base / relative
        if target.is_symlink() or not target.is_file():
            return False
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            return False
    return True


def install_extension(source: Path) -> dict[str, Any]:
    """Save and register a candidate; it stays disabled on every host."""
    checked = validate_extension(source)
    root = extensions_root()
    with _registry_lock(root):
        registry = _read_registry(root)
        entry = registry["extensions"].setdefault(checked["id"], {"versions": {}})
        if checked["version"] in entry["versions"]:
            raise ExtensionError(
                f"版本已发布且只读，不能覆盖: {checked['id']}@{checked['version']}"
            )
        target = _managed_version_dir(root, checked["id"], checked["version"])
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        for relative in checked["files"]:
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(Path(source) / relative, destination)
        record = {**checked, "path": str(target)}
        entry["versions"][checked["version"]] = record
        _commit_registry(root, registry)
    return {"id": checked["id"], "version": checked["version"], "enabled": False}


def update_extension(source: Path) -> dict[str, Any]:
    """Prepare a new candidate version; existing bindings stay untouched."""
    checked = validate_extension(source)
    root = extensions_root()
    with _registry_lock(root):
        registry = _read_registry(root)
        entry = registry["extensions"].get(checked["id"])
        if entry is None:
            raise ExtensionError(f"扩展未安装: {checked['id']}")
        if checked["version"] in entry["versions"]:
            raise ExtensionError(
                f"版本已发布且只读，不能覆盖: {checked['id']}@{checked['version']}"
            )
        previous_version = sorted(entry["versions"])[-1]
        previous = entry["versions"][previous_version]
        target = _managed_version_dir(root, checked["id"], checked["version"])
        target.mkdir(parents=True)
        for relative in checked["files"]:
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(Path(source) / relative, destination)
        entry["versions"][checked["version"]] = {**checked, "path": str(target)}
        _commit_registry(root, registry)
    previous_capabilities = set(previous.get("capabilities", []))
    current_capabilities = set(checked["capabilities"])
    return {
        "id": checked["id"],
        "previous_version": previous_version,
        "version": checked["version"],
        "summary_changed": previous.get("summary") != checked["summary"],
        "capability_changes": {
            "added": sorted(current_capabilities - previous_capabilities),
            "removed": sorted(previous_capabilities - current_capabilities),
        },
        "source": checked["source"],
    }


def _find_version(entry: dict[str, Any], version: str | None) -> tuple[str, dict[str, Any]]:
    versions = entry["versions"]
    if version is None:
        chosen = sorted(versions)[-1]
        return chosen, versions[chosen]
    if version not in versions:
        raise ExtensionError(f"版本未登记: {version}")
    return version, versions[version]


def _resolve_bindings(
    registry: dict[str, Any], bindings: dict[str, Any]
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """enable/context 共用的加载集合判定，返回 (有效记录, 失败原因)。

    依赖只能由实际有效（登记完整、完整性校验通过、兼容）的提供者满足，
    包内 provides 满足自身 requires，失效沿依赖传播到直接和传递依赖方；
    同一加载集合中重复的工作包 ID 全部拒绝，不靠顺序保留任何一个。
    单个候选失败不影响无关候选。
    """
    effective: dict[str, dict[str, Any]] = {}
    failures: dict[str, str] = {}
    for ext_id, binding in bindings.items():
        entry = registry["extensions"].get(ext_id)
        label = f"{ext_id}@{binding.get('version')}"
        if entry is None or binding.get("version") not in entry["versions"]:
            failures[ext_id] = f"{label}: 登记缺失"
            continue
        record = entry["versions"][binding["version"]]
        if record.get("compatibility") != CURRENT_COMPAT:
            failures[ext_id] = (
                f"{label}: 兼容范围不满足（{record.get('compatibility')}）"
            )
            continue
        if not _stored_integrity_ok(record):
            failures[ext_id] = f"{label}: 包完整性校验失败"
            continue
        effective[ext_id] = record

    owners: dict[str, list[str]] = {}
    for ext_id, record in effective.items():
        for wp in record.get("provides", []):
            owners.setdefault(wp, []).append(ext_id)
    conflicting = {wp for wp, ids in owners.items() if len(ids) > 1}
    if conflicting:
        for ext_id in list(effective):
            hit = conflicting & set(effective[ext_id].get("provides", []))
            if hit:
                label = f"{ext_id}@{bindings[ext_id]['version']}"
                failures[ext_id] = (
                    f"{label}: 工作包 ID 冲突（{', '.join(sorted(hit))}），"
                    "同一加载集合拒绝重复身份"
                )
                del effective[ext_id]

    changed = True
    while changed:
        changed = False
        pool = {
            wp for record in effective.values() for wp in record.get("provides", [])
        }
        for ext_id in list(effective):
            missing = [
                dep
                for dep in effective[ext_id].get("requires", [])
                if dep not in pool
            ]
            if missing:
                label = f"{ext_id}@{bindings[ext_id]['version']}"
                failures[ext_id] = f"{label}: 硬依赖未启用: {', '.join(missing)}"
                del effective[ext_id]
                changed = True
    return effective, failures


def enable_extension(host: str, extension_id: str, *, version: str | None = None) -> dict[str, Any]:
    if host not in HOSTS:
        raise ExtensionError(f"未知宿主: {host}")
    root = extensions_root()
    with _registry_lock(root):
        registry = _read_registry(root)
        entry = registry["extensions"].get(extension_id)
        if entry is None:
            raise ExtensionError(f"扩展未安装或已卸载: {extension_id}")
        chosen_version, record = _find_version(entry, version)
        trial_bindings = {
            **registry["bindings"][host],
            extension_id: {"version": chosen_version},
        }
        effective, failures = _resolve_bindings(registry, trial_bindings)
        if extension_id not in effective:
            raise ExtensionError(
                f"拒绝启用: {failures.get(extension_id, '未知原因')}"
            )
        bindings = registry["bindings"][host]
        # 新增权限不得继承旧授权：绑定记录本次实际授予范围。
        bindings[extension_id] = {
            "version": chosen_version,
            "granted_capabilities": list(record.get("capabilities", [])),
            "enabled_at": _utc_now(),
        }
        _commit_registry(root, registry)
    return {
        "host": host,
        "id": extension_id,
        "version": chosen_version,
        "capabilities": list(record.get("capabilities", [])),
    }


def disable_extension(host: str, extension_id: str) -> dict[str, Any]:
    if host not in HOSTS:
        raise ExtensionError(f"未知宿主: {host}")
    root = extensions_root()
    with _registry_lock(root):
        registry = _read_registry(root)
        bindings = registry["bindings"][host]
        if extension_id not in bindings:
            raise ExtensionError(f"该宿主未启用此扩展: {host}/{extension_id}")
        removed = bindings.pop(extension_id)
        _commit_registry(root, registry)
    return {"host": host, "id": extension_id, "version": removed["version"], "enabled": False}


def uninstall_extension(extension_id: str) -> dict[str, Any]:
    root = extensions_root()
    with _registry_lock(root):
        registry = _read_registry(root)
        if extension_id not in registry["extensions"]:
            raise ExtensionError(f"扩展未安装: {extension_id}")
        bound_hosts = [
            host
            for host in HOSTS
            if extension_id in registry["bindings"][host]
        ]
        if bound_hosts:
            raise ExtensionError(
                f"其他宿主仍绑定该扩展，未授权删除: {', '.join(bound_hosts)}"
            )
        del registry["extensions"][extension_id]
        shutil.rmtree(root / extension_id, ignore_errors=True)
        _commit_registry(root, registry)
    return {"id": extension_id, "uninstalled": True}


def list_extensions() -> dict[str, Any]:
    registry = _read_registry()
    return {
        "extensions": {
            ext_id: {"versions": sorted(entry["versions"])}
            for ext_id, entry in sorted(registry["extensions"].items())
        },
        "bindings": {
            host: {
                ext_id: {"version": binding["version"]}
                for ext_id, binding in sorted(bindings.items())
            }
            for host, bindings in registry["bindings"].items()
        },
    }


def inspect_extension(extension_id: str, *, version: str | None = None) -> dict[str, Any]:
    """Escorted metadata only — package bodies are never returned."""
    registry = _read_registry()
    entry = registry["extensions"].get(extension_id)
    if entry is None:
        raise ExtensionError(f"扩展未安装: {extension_id}")
    chosen, record = _find_version(entry, version)
    return {
        "id": extension_id,
        "version": chosen,
        "source": record.get("source", ""),
        "summary": record.get("summary", ""),
        "compatibility": record.get("compatibility", ""),
        "capabilities": list(record.get("capabilities", [])),
        "provides": list(record.get("provides", [])),
        "requires": list(record.get("requires", [])),
        "location": record.get("path", ""),
    }


def context_for_host(host: str) -> dict[str, Any]:
    """Enabled, intact, compatible candidates for this host — metadata only.

    Dependency and integrity failures close the candidate for this run and
    are reported as reasons; core research can continue without candidates.
    """
    if host not in HOSTS:
        raise ExtensionError(f"未知宿主: {host}")
    registry = _read_registry()
    bindings = registry["bindings"][host]
    effective, failures = _resolve_bindings(registry, bindings)
    candidates: list[dict[str, Any]] = [
        {
            "id": ext_id,
            "version": bindings[ext_id]["version"],
            "source": record.get("source", ""),
            "summary": record.get("summary", ""),
            "capabilities": list(record.get("capabilities", [])),
            "location": record.get("path", ""),
        }
        for ext_id, record in sorted(effective.items())
    ]
    reason = "；".join(failures[ext_id] for ext_id in sorted(failures))
    if not candidates and not reason:
        reason = "本宿主没有已启用且完整兼容的扩展候选"
    return {
        "host": host,
        "candidates": candidates,
        "reason": reason,
    }


def _utc_now() -> str:
    from datetime import datetime

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
