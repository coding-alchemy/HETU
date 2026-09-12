"""Read-only inspection and safe export of legacy (schema-version-3) runs.

Phase-5 stage 06.3: old run directories are plain JSON.  This module reads
``state.json``, ``stage_results/*.json`` and existing reports as data — it
never imports the retired RunState/StageResult modules, never executes
embedded content, never downloads linked material, and never mutates the
source directory.  Unknown fields are preserved verbatim; broken JSON,
missing references and unknown schema versions are reported with a concrete
scope while the recoverable originals stay in place.

``export_archive`` copies authorized records to a fresh target with a
readable index.  Records containing secret-like fields are not copied: the
originals stay where they are and the index records the allowed locator,
the redaction and the reason.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "3"

STATE_KNOWN_KEYS = frozenset(
    {
        "schema_version",
        "request",
        "evidence_ledger",
        "thesis_state",
        "workflow_state",
        "stage_attempts",
        "active_stage_attempts",
        "evaluation_time",
        "authorization_attestation",
    }
)
STAGE_KNOWN_KEYS = frozenset(
    {
        "stage_id",
        "conclusions",
        "evidence",
        "claims",
        "thesis_updates",
        "issues",
        "monitoring_rules",
    }
)
REFERENCE_KEYS = frozenset({"locator", "path", "file"})
_SECRET_KEY_PATTERN = re.compile(
    r"secret|password|passwd|token|api[_-]?key|credential", re.IGNORECASE
)
# Path-like references only: URI-shaped locators (e.g. "monitoring:plan")
# and absolute paths are not directory references.
_REFERENCE_PATH_PATTERN = re.compile(r"^[\w./-]+?(?:#[\w.-]+)?$")


class ArchiveError(ValueError):
    """Raised when an archive directory or export target is not usable."""


def _is_secret_key(key: Any) -> bool:
    return isinstance(key, str) and _SECRET_KEY_PATTERN.search(key) is not None


def _collect_secret_keys(value: Any, found: set[str], prefix: str = "") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            locator = f"{prefix}.{key}" if prefix else str(key)
            if _is_secret_key(key):
                found.add(locator)
            _collect_secret_keys(item, found, locator)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _collect_secret_keys(item, found, f"{prefix}[{index}]")


def _collect_references(value: Any, found: set[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in REFERENCE_KEYS and isinstance(item, str):
                found.add(item.split("#", 1)[0])
            _collect_references(item, found)
    elif isinstance(value, list):
        for item in value:
            _collect_references(item, found)


def _collect_attachments(value: Any, found: set[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "attachments" and isinstance(item, list):
                for entry in item:
                    if isinstance(entry, str):
                        found.add(entry.split("#", 1)[0])
            _collect_attachments(item, found)
    elif isinstance(value, list):
        for item in value:
            _collect_attachments(item, found)


def _is_inside(path: Path, root: Path) -> bool:
    resolved = path.resolve()
    return resolved == root or root in resolved.parents


def _report_escape(
    issues: list[dict[str, str]], locator: str, kind: str
) -> None:
    issues.append(
        {
            "file": locator,
            "message": (
                f"{kind}符号链接越界（指向源目录外），不可读取；"
                "外部内容未进入读取结果，原件保留原位置"
            ),
        }
    )


def _json_files(
    source: Path, source_resolved: Path, issues: list[dict[str, str]]
) -> list[Path]:
    """源内 JSON 清单；读取前检查链接解析后的路径仍在源目录内。"""
    files: list[Path] = []
    for path in sorted(source.glob("*.json")):
        if not _is_inside(path, source_resolved):
            _report_escape(issues, path.name, "文件")
            continue
        files.append(path)
    stage_results = source / "stage_results"
    if stage_results.is_symlink() or stage_results.is_dir():
        if not _is_inside(stage_results, source_resolved):
            _report_escape(issues, "stage_results", "目录")
        else:
            for path in sorted(stage_results.glob("*.json")):
                if not _is_inside(path, source_resolved):
                    _report_escape(issues, path.relative_to(source).as_posix(), "文件")
                    continue
                files.append(path)
    return files


def _present_state_fields(raw: dict[str, Any]) -> dict[str, Any]:
    thesis = raw.get("thesis_state")
    workflow = raw.get("workflow_state")
    fields: dict[str, Any] = {"请求": raw.get("request")}
    if isinstance(thesis, dict):
        adopted = thesis.get("adopted_claim_ids")
        fields["原采用"] = (
            {"adopted_claim_ids": adopted} if adopted is not None else None
        )
        fields["论点"] = {
            key: value for key, value in thesis.items() if key != "adopted_claim_ids"
        }
    else:
        fields["原采用"] = None
        fields["论点"] = thesis
    fields["用户决定"] = (
        workflow.get("user_decisions") if isinstance(workflow, dict) else None
    )
    fields["阶段尝试"] = raw.get("stage_attempts")
    return fields


def inspect_archive(source: Path) -> dict[str, Any]:
    """Inspect a legacy run directory without mutating or executing anything."""
    source = Path(source)
    if not source.is_dir():
        raise ArchiveError(f"源目录不存在或不是目录: {source}")
    source_resolved = source.resolve()

    raw_records: dict[str, dict[str, Any]] = {}
    records: dict[str, dict[str, Any]] = {}
    issues: list[dict[str, str]] = []
    schema_version: str | None = None
    references: set[str] = set()

    for path in _json_files(source, source_resolved, issues):
        relative = path.relative_to(source).as_posix()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            issues.append(
                {
                    "file": relative,
                    "message": f"损坏 JSON，无法解析（{exc}）；原文件保留可取回",
                }
            )
            continue
        if not isinstance(raw, dict):
            issues.append(
                {
                    "file": relative,
                    "message": "JSON 顶层不是对象；原文件保留可取回",
                }
            )
            continue
        raw_records[relative] = raw

        known = STATE_KNOWN_KEYS if path.name == "state.json" else STAGE_KNOWN_KEYS
        secret_keys: set[str] = set()
        _collect_secret_keys(raw, secret_keys)
        file_references: set[str] = set()
        _collect_references(raw, file_references)
        _collect_attachments(raw, file_references)
        references |= file_references

        if path.name == "state.json":
            schema_version = raw.get("schema_version")
            if schema_version != SCHEMA_VERSION:
                issues.append(
                    {
                        "file": relative,
                        "message": (
                            f"未知版本 schema_version（原记录 {schema_version!r}，"
                            f"按 schema {SCHEMA_VERSION} 普通 JSON 读取，原文已保留）"
                        ),
                    }
                )
            fields = _present_state_fields(raw)
        else:
            fields = {"证据": raw.get("evidence"), "主张": raw.get("claims")}

        records[relative] = {
            "fields": fields,
            "unknown_fields": sorted(set(raw) - known),
            "restricted_keys": sorted(secret_keys),
        }

    for reference in sorted(references):
        if not reference or not _REFERENCE_PATH_PATTERN.fullmatch(reference):
            continue
        referenced = Path(reference)
        if referenced.is_absolute() or ".." in referenced.parts:
            continue
        if not (source / referenced).exists():
            issues.append(
                {
                    "file": reference,
                    "message": "源目录内缺引用；原记录保留可取回，未重建研究结论",
                }
            )

    reports = sorted(p.name for p in source.glob("*.md"))
    return {
        "source": str(source),
        "schema_version": schema_version,
        "fields": records.get("state.json", {}).get("fields", {}),
        "records": records,
        "raw_records": raw_records,
        "reports": reports,
        "issues": issues,
    }


def _safe_json_value(path: Path) -> Any:
    """读取 JSON（对象或数组）用于安全判定；不可解析时返回 None（调用方按原样复制）。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _strip_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_secrets(item)
            for key, item in value.items()
            if not _is_secret_key(key)
        }
    if isinstance(value, list):
        return [_strip_secrets(item) for item in value]
    return value


def _render_index(result: dict[str, Any], redacted: list[dict[str, str]]) -> str:
    lines = [
        "# 旧成果导出索引",
        "",
        f"- 来源目录：`{result['source']}`",
        f"- schema_version：{result['schema_version']!r}（按原记录，未迁移）",
        "",
        "## 字段定位",
        "",
    ]
    request = result["fields"].get("请求") or {}
    subject = request.get("subject") or {}
    lines.append(
        f"- 请求：subject={subject.get('symbol')!r}，as_of={request.get('as_of')!r}，"
        f"depth={request.get('depth')!r}（state.json#request）"
    )
    decisions = result["fields"].get("用户决定") or []
    lines.append(
        f"- 用户决定：{len(decisions)} 条（state.json#workflow_state.user_decisions）"
    )
    attempts = result["fields"].get("阶段尝试") or {}
    lines.append(
        "- 阶段尝试："
        + ", ".join(
            f"S{stage}×{len(items)}" for stage, items in sorted(attempts.items())
        )
        + "（state.json#stage_attempts）"
    )
    lines.extend(["", "## 脱敏与未导出范围", ""])
    if redacted:
        for entry in redacted:
            lines.append(f"- `{entry['locator']}`：{entry['reason']}，原件保留原位置")
    else:
        lines.append("- 无")
    lines.append(
        "- 保留缺口：秘密识别仅覆盖 JSON 中已支持的秘密字段；其余文件按原样复制，"
        "未做内容级秘密扫描，本导出不宣称无损全量导出"
    )
    if result["issues"]:
        lines.extend(["", "## 读取范围", ""])
        for issue in result["issues"]:
            lines.append(f"- `{issue['file']}`：{issue['message']}")
    lines.append("")
    return "\n".join(lines)


def export_archive(source: Path, output: Path) -> dict[str, Any]:
    """Export readable copies of a legacy run into a fresh target directory."""
    source = Path(source)
    output = Path(output)
    if not source.is_dir():
        raise ArchiveError(f"源目录不存在或不是目录: {source}")
    if output.exists():
        raise ArchiveError(f"导出目标已存在: {output}")
    source_resolved = source.resolve()
    output_resolved = output.resolve()
    if output_resolved == source_resolved or source_resolved in output_resolved.parents:
        raise ArchiveError("导出目标不得位于源目录内部或覆盖源文件")
    if output_resolved in source_resolved.parents:
        raise ArchiveError("导出目标不得包含源目录")

    result = inspect_archive(source)
    restricted_relatives = {
        relative
        for relative, record in result["records"].items()
        if record["restricted_keys"]
    }

    redacted: list[dict[str, str]] = []
    for relative in sorted(restricted_relatives):
        record = result["records"][relative]
        for key in record["restricted_keys"]:
            redacted.append(
                {
                    "locator": f"{relative}#{key.split('.')[-1]}",
                    "reason": "原记录含疑似秘密字段，已脱敏未复制",
                }
            )

    exported: list[str] = []
    issues = list(result["issues"])
    for path in sorted(source.rglob("*")):
        if not path.is_file() and not path.is_symlink():
            continue
        relative = path.relative_to(source)
        relative_text = relative.as_posix()
        resolved = path.resolve()
        if path.is_symlink() and not (
            source_resolved == resolved or source_resolved in resolved.parents
        ):
            raise ArchiveError(f"符号链接越界，拒绝导出: {relative_text}")
        if not resolved.is_file():
            continue
        # 安全判定针对解析后的实际对象：别名、目录位置或文件后缀不能绕过。
        actual_relative = resolved.relative_to(source_resolved).as_posix()
        if relative_text in restricted_relatives:
            continue  # 脱敏范围已在上方登记
        if actual_relative in restricted_relatives:
            redacted.append(
                {
                    "locator": relative_text,
                    "reason": (
                        f"链接解析后指向含秘密的原记录 {actual_relative}，"
                        "已脱敏未复制"
                    ),
                }
            )
            continue
        if resolved.suffix == ".json":
            payload = _safe_json_value(resolved)
            if payload is not None:
                nested_secrets: set[str] = set()
                _collect_secret_keys(payload, nested_secrets)
                if nested_secrets:
                    for key in sorted(nested_secrets):
                        redacted.append(
                            {
                                "locator": f"{relative_text}#{key.split('.')[-1]}",
                                "reason": "原记录含疑似秘密字段，已脱敏未复制",
                            }
                        )
                    continue
        target = output_resolved / "records" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if (
            resolved.suffix == ".json"
            and relative_text in result["raw_records"]
            and actual_relative == relative_text
        ):
            stripped = _strip_secrets(result["raw_records"][relative_text])
            target.write_text(
                json.dumps(stripped, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        else:
            shutil.copyfile(resolved, target)
        exported.append(relative_text)

    index = _render_index(result, redacted)
    (output_resolved / "index.md").write_text(index, encoding="utf-8")
    return {
        "output": str(output_resolved),
        "exported": exported,
        "redacted": redacted,
        "issues": issues,
    }
