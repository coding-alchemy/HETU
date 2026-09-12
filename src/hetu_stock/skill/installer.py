"""Transactional Skill installation for HETU (phase 5, design §3.1).

Overwrite installs no longer delete the installed package first. A new
package is prepared and validated in a managed staging area outside the
host discovery directory, then published with a single native atomic
directory exchange (macOS ``renameatx_np(RENAME_SWAP)`` / Linux
``renameat2(RENAME_EXCHANGE)``). The replaced package stays in the managed
area as a rollback backup, and a minimal transaction record survives
process interruptions so the next maintenance can finish or undo the
operation based on real directories and digests, never on trust alone.
"""

from __future__ import annotations

import contextlib
import ctypes
import errno
import fcntl
import hashlib
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from hetu_stock.skill.package import SkillValidationError, validate_skill_package

SKILL_DIR_NAME = "hetu-stock-analysis"
MAINTENANCE_DIR_NAME = ".hetu-skill-maintenance"
_AT_FDCWD = -100
_RENAME_SWAP = 0x2
_RENAME_EXCHANGE = 0x2


class HostTarget(StrEnum):
    CODEX = "codex"
    CLAUDE = "claude"
    OPENCODE = "opencode"
    ZCODE = "zcode"


def default_user_skill_root(host: HostTarget) -> Path:
    home = Path(os.environ["HOME"])
    if host is HostTarget.CODEX:
        return Path(os.environ.get("CODEX_HOME", home / ".codex")) / "skills"
    if host is HostTarget.CLAUDE:
        return home / ".claude" / "skills"
    if host is HostTarget.ZCODE:
        # Evidence for this default: the user's ZCode client discovers its
        # skills from ~/.zcode/skills (the client's own skill listings load
        # from there; see phase-5 stage-01 records).
        return home / ".zcode" / "skills"
    xdg = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    return xdg / "opencode" / "skills"


def exchange_directories(left: Path, right: Path) -> None:
    """Atomically swap two directories via the platform's native syscall.

    Only the kernel's atomic exchange is used: macOS ``renameatx_np`` with
    ``RENAME_SWAP`` and Linux ``renameat2`` with ``RENAME_EXCHANGE``. When
    the platform or filesystem does not support it, a clear ``OSError`` is
    raised; this implementation never degrades into two renames and never
    replaces the installed directory with a symlink.
    """
    left_bytes = os.fsencode(str(left))
    right_bytes = os.fsencode(str(right))
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        if not hasattr(libc, "renameatx_np"):
            raise OSError(errno.ENOSYS, "renameatx_np is not available on this platform")
        libc.renameatx_np.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        result = libc.renameatx_np(
            _AT_FDCWD, left_bytes, _AT_FDCWD, right_bytes, _RENAME_SWAP
        )
    else:
        if not hasattr(libc, "renameat2"):
            raise OSError(errno.ENOSYS, "renameat2 is not available on this platform")
        libc.renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        result = libc.renameat2(
            _AT_FDCWD, left_bytes, _AT_FDCWD, right_bytes, _RENAME_EXCHANGE
        )
    if result != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(left), None, str(right))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validated_package_files(root: Path) -> tuple[Path, ...]:
    root_resolved = root.resolve()
    if root.is_symlink():
        raise SkillValidationError("symbolic link is not allowed as the Skill package root")

    files: list[Path] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise SkillValidationError(
                f"symbolic link is not allowed in Skill package: {relative}"
            )
        if "__pycache__" in path.parts:
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(root_resolved):
            raise SkillValidationError(f"Skill package path resolves outside root: {relative}")
        if path.is_file():
            files.append(path)
    return tuple(sorted(files, key=lambda file: file.relative_to(root).as_posix()))


def build_skill_manifest(root: Path) -> dict[str, dict[str, str]]:
    """Build the canonical, deterministic manifest payload for a Skill root."""
    files = tuple(
        file
        for file in _validated_package_files(root)
        if file.relative_to(root).as_posix() != "MANIFEST.json"
    )
    return {
        "files": {
            file.relative_to(root).as_posix(): _file_sha256(file)
            for file in files
        }
    }


def verify_skill_manifest(source: Path) -> None:
    """Verify package integrity against MANIFEST.json (SHA-256 per file).

    MANIFEST.json itself is excluded from the file set (it is the root of
    trust); every other file must be listed and hash-match.
    """
    package_files = _validated_package_files(source)
    manifest_path = source / "MANIFEST.json"
    if not manifest_path.is_file():
        raise SkillValidationError("MANIFEST.json is required for installation")

    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise SkillValidationError(f"MANIFEST.json is invalid: {exc}") from exc

    if not isinstance(payload, dict) or "files" not in payload:
        raise SkillValidationError("MANIFEST.json must contain a 'files' object")

    entries = payload["files"]
    if not isinstance(entries, dict):
        raise SkillValidationError("MANIFEST.json 'files' must be a mapping")

    expected_files = set(entries)
    actual_files = {
        file.relative_to(source).as_posix()
        for file in package_files
        if file.relative_to(source).as_posix() != "MANIFEST.json"
    }

    missing = expected_files - actual_files
    if missing:
        raise SkillValidationError(
            f"manifest files missing from package: {sorted(missing)}"
        )

    extra = actual_files - expected_files
    if extra:
        raise SkillValidationError(
            f"package files missing from manifest: {sorted(extra)}"
        )

    for rel_path, expected_hash in entries.items():
        if not isinstance(expected_hash, str):
            raise SkillValidationError(
                f"invalid hash for {rel_path}: {expected_hash!r}"
            )
        actual_hash = _file_sha256(source / rel_path)
        if actual_hash != expected_hash:
            raise SkillValidationError(
                f"sha256 mismatch for {rel_path}: expected {expected_hash}, got {actual_hash}"
            )


class _MaintenanceArea:
    """Managed staging/backup/lock area on the target's filesystem.

    Lives outside the host discovery directory (as a sibling of the skills
    root), is addressed by a digest of the normalized target so every
    maintenance operation on one target shares the same lock and records,
    and never stores anything besides installation state.
    """

    def __init__(
        self, destination_root: Path, target: Path, *, create: bool = True
    ) -> None:
        self.target = target
        normalized = hashlib.sha256(
            str(target.resolve()).encode("utf-8")
        ).hexdigest()[:16]
        self.root = destination_root.parent / MAINTENANCE_DIR_NAME / normalized
        if not create:
            self.lock_path = self.root / "lock"
            return
        destination_root.mkdir(parents=True, exist_ok=True)
        self.root.mkdir(parents=True, exist_ok=True)
        if os.stat(self.root).st_dev != os.stat(destination_root).st_dev:
            raise OSError(
                errno.EXDEV,
                "maintenance area is not on the same filesystem as the target; "
                f"area={self.root} target={target}",
            ) from None
        self.lock_path = self.root / "lock"

    def __enter__(self) -> _MaintenanceArea:
        self._handle = self.lock_path.open("a+")
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc_info: object) -> None:
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()

    def _write_record(self, record: dict[str, Any]) -> None:
        record_path = self.root / "transaction.json"
        with record_path.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, indent=1))
            handle.flush()
            os.fsync(handle.fileno())

    def read_record(self) -> dict[str, Any] | None:
        record_path = self.root / "transaction.json"
        if not record_path.is_file():
            return None
        try:
            payload = json.loads(record_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise OSError(
                errno.EINVAL,
                f"maintenance record is unreadable ({exc}); inspect {self.root} "
                "manually before any further maintenance",
            ) from exc
        return payload if isinstance(payload, dict) else None

    def new_staging(self) -> Path:
        staging_parent = self.root / f"staging-{int(time.time() * 1000):x}-{os.getpid():x}"
        staging_parent.mkdir()
        return staging_parent / SKILL_DIR_NAME

    def prepare_staging(self, source: Path) -> tuple[Path, dict[str, dict[str, str]]]:
        staging = self.new_staging()
        shutil.copytree(source, staging, ignore=shutil.ignore_patterns("__pycache__"))
        validate_skill_package(staging, require_manifest=True)
        verify_skill_manifest(staging)
        manifest = build_skill_manifest(staging)
        return staging, manifest

    def discard(self, staging: Path) -> None:
        shutil.rmtree(staging.parent, ignore_errors=True)

    def backup_dir(self, token: str) -> Path:
        return self.root / f"backup-{token}"


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _recover_pending(area: _MaintenanceArea) -> None:
    """Finish or undo an interrupted transaction using real directories.

    The record's state string is only a hint: staging/target contents and
    digests decide what actually happened. Ambiguous states raise with the
    concrete next action instead of guessing a version.
    """
    record = area.read_record()
    if record is None:
        return
    state = record.get("state")
    recorded_new = record.get("new_manifest")
    recorded_new = recorded_new if isinstance(recorded_new, dict) else None
    staging = Path(str(record.get("staging", "")))
    target = Path(str(record.get("target", "")))
    if not staging or not target:
        raise OSError(
            errno.EINVAL,
            f"incomplete maintenance record in {area.root}; inspect and resolve manually",
        )

    if state == "prepared":
        # The exchange may or may not have happened; digests decide.
        if not target.exists():
            # Nothing was published; finish by renaming validated staging in.
            validate_skill_package(staging, require_manifest=True)
            verify_skill_manifest(staging)
            os.rename(staging, target)
            _fsync_dir(target.parent)
            area._write_record({**record, "state": "finalized",
                                "backup": str(staging.parent), "staging": str(staging)})
            return
        current = build_skill_manifest(target)
        if recorded_new is not None and current == recorded_new:
            # Target already carries the new package: the exchange happened
            # but the record was not updated. Keep the old copy as backup.
            area._write_record({**record, "state": "finalized",
                                "backup": str(staging.parent), "staging": str(staging)})
            return
        if current == record.get("old_manifest"):
            # Target is still the old package; validate staging then publish.
            validate_skill_package(staging, require_manifest=True)
            verify_skill_manifest(staging)
            exchange_directories(staging, target)
            area._write_record({**record, "state": "exchanged"})
            _finalize_exchange(area, record, staging, target, recorded_new)
            return
        raise OSError(
            errno.EINVAL,
            "ambiguous interrupted installation: target digest matches neither "
            f"the recorded old package nor the staged new package; inspect "
            f"{area.root} (staging={staging}, target={target}) and resolve manually",
        ) from None

    if state == "exchanged":
        _finalize_exchange(area, record, staging, target, recorded_new)
        return

    if state in {"finalized", "rolled_back"}:
        return

    raise OSError(
        errno.EINVAL,
        f"unknown maintenance record state {state!r} in {area.root}; resolve manually",
    )


def _finalize_exchange(
    area: _MaintenanceArea,
    record: dict[str, Any],
    staging: Path,
    target: Path,
    new_manifest: dict[str, dict[str, str]] | None,
) -> None:
    """Verify the exchanged target; roll back to the backup when it is bad."""
    try:
        validate_skill_package(target, require_manifest=True)
        verify_skill_manifest(target)
        if new_manifest is not None and build_skill_manifest(target) != new_manifest:
            raise SkillValidationError("installed package does not match staged manifest")
    except Exception:
        if staging.exists():
            exchange_directories(staging, target)
            restored = build_skill_manifest(target)
            if record.get("old_manifest") is not None and restored != record["old_manifest"]:
                raise OSError(
                    errno.EINVAL,
                    "rollback finished but the restored package does not match the "
                    f"recorded old digest; both copies kept in {area.root}",
                ) from None
            area._write_record({**record, "state": "rolled_back"})
            raise
        raise OSError(
            errno.EINVAL,
            "exchanged target failed verification and no backup staging directory "
            f"exists; inspect {area.root} (staging={staging}) and resolve manually",
        ) from None
    area._write_record({**record, "state": "finalized", "staging": str(staging)})


def install_skill(source: Path, destination_root: Path, *, force: bool = False) -> Path:
    source = Path(source)
    verify_skill_manifest(source)
    validate_skill_package(source, require_manifest=True)

    destination_root = Path(destination_root)
    target = destination_root / SKILL_DIR_NAME
    area = _MaintenanceArea(destination_root, target)
    with area:
        _recover_pending(area)

        staging, new_manifest = area.prepare_staging(source)

        if not target.exists():
            os.rename(staging, target)
            _fsync_dir(target.parent)
            try:
                validate_skill_package(target, require_manifest=True)
                verify_skill_manifest(target)
            except Exception:
                # First install has no previous version to preserve; removing
                # the just-published directory restores the pre-install state.
                shutil.rmtree(target, ignore_errors=True)
                raise
            area._write_record(
                {
                    "state": "finalized",
                    "target": str(target),
                    "staging": str(staging),
                    "backup": str(staging.parent),
                    "new_manifest": new_manifest,
                    "old_manifest": None,
                    "mode": "first-install",
                }
            )
            return target

        if not force:
            area.discard(staging)
            raise FileExistsError(target)

        old_manifest = build_skill_manifest(target)
        record = {
            "state": "prepared",
            "target": str(target),
            "staging": str(staging),
            "backup": "",
            "old_manifest": old_manifest,
            "new_manifest": new_manifest,
            "mode": "overwrite-exchange",
        }
        area._write_record(record)
        # The staging directory becomes the rollback backup; record which
        # helper combination the displaced version belongs to.
        _write_combo_association(staging.parent)
        exchange_directories(staging, target)
        exchanged: dict[str, object] = {
            **record,
            "state": "exchanged",
            "backup": str(staging.parent),
        }
        area._write_record(exchanged)
        _finalize_exchange(area, exchanged, staging, target, new_manifest)
        return target


# --- Maintenance commands (phase 5, design §3.3) -----------------------------


@dataclass
class BackupInfo:
    name: str
    path: Path
    package: Path
    valid: bool
    reason: str = ""


@dataclass
class LauncherInfo:
    path: Path
    state: str  # "managed" | "unmanaged" | "missing"
    target: str = ""


@dataclass
class HelperInfo:
    env_root: Path
    envs: list[Path]
    current: Path | None
    state: str  # "available" | "missing" | "none"
    reason: str = ""


@dataclass
class InstallationInspection:
    target: Path
    target_state: str  # "valid" | "invalid" | "missing"
    target_reason: str
    record_state: str
    backups: list[BackupInfo]
    launcher: LauncherInfo
    helper: HelperInfo
    next_actions: list[str] = field(default_factory=list)


@dataclass
class UninstallScope:
    target: Path
    target_exists: bool
    backups: list[BackupInfo]
    launcher: LauncherInfo
    helper: HelperInfo
    other_host_targets: list[Path]
    notes: list[str] = field(default_factory=list)


def _read_area(destination_root: Path) -> _MaintenanceArea | None:
    destination_root = Path(destination_root)
    if not destination_root.is_dir():
        return None
    area = _MaintenanceArea(destination_root, destination_root / SKILL_DIR_NAME, create=False)
    if not area.root.is_dir():
        return None
    return area


def display_path(path: Path) -> str:
    """Desensitized location: the user's home directory renders as ``~``."""
    rendered = str(Path(path))
    home = str(Path(os.environ["HOME"]))
    if rendered.startswith(home):
        return "~" + rendered[len(home):]
    return rendered


def managed_launcher_path() -> Path:
    return Path(os.environ["HOME"]) / ".local" / "bin" / "hetu-stock"


def managed_env_root() -> Path:
    home = Path(os.environ["HOME"])
    data_root = Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share"))
    return data_root / "hetu-stock"


def _managed_root_for_env(env_dir: Path) -> Path | None:
    """The managed ``hetu-stock`` root owning an environment directory.

    Covers the versioned layout (``<root>/envs/env-<token>``) and the v0.2-era
    official layout (``<root>/venv``).
    """
    if env_dir.parent.name == "envs" and env_dir.name.startswith("env-"):
        return env_dir.parent.parent
    if env_dir.name == "venv":
        return env_dir.parent
    return None


def managed_env_evidence(env_dir: Path) -> bool:
    """Reliable ownership evidence for a managed environment directory.

    Directory-name shape alone is NOT evidence: another project can use the
    same ``envs/env-*`` layout, and deleting it would destroy user data.
    Accepted proof, cheapest first: the combo record in the owning managed
    root names this environment, or the environment contains an installed
    ``hetu_stock`` distribution (dist-info metadata or the package itself)
    together with its CLI entry point.
    """
    env_dir = Path(env_dir)
    root = _managed_root_for_env(env_dir)
    if root is None or not env_dir.is_dir():
        return False
    record = root / "installation.json"
    if record.is_file():
        try:
            data = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict):
            for key in ("env", "previous_env"):
                value = data.get(key)
                if isinstance(value, str) and value and Path(value) == env_dir:
                    return True
    if not (env_dir / "bin" / "hetu-stock").is_file():
        return False
    for site in env_dir.glob("lib/python3.*/site-packages"):
        if any(site.glob("hetu_stock-*.dist-info")) or (site / "hetu_stock").is_dir():
            return True
    return False


def _env_dist_version(env_dir: Path) -> str | None:
    """Installed hetu_stock version in an environment, from dist-info names."""
    for site in Path(env_dir).glob("lib/python3.*/site-packages"):
        for dist in site.glob("hetu_stock-*.dist-info"):
            return dist.name[: -len(".dist-info")][len("hetu_stock-"):]
    return None


def _write_combo_association(backup_parent: Path) -> None:
    """Record which helper combination a backup belongs to.

    Written into the backup's directory whenever a version is displaced, so a
    later rollback can restore the launcher/environment that version actually
    shipped with — instead of guessing from environment counts or version
    strings. ``env: null`` records explicitly that there was no managed
    combination at the time.
    """
    launcher = _launcher_info(None)
    env_dir: Path | None = None
    if launcher.state == "managed":
        env_dir = Path(launcher.target).parent.parent
    payload = {
        "env": str(env_dir) if env_dir is not None else None,
        "launcher": str(launcher.path),
        "version": _env_dist_version(env_dir) if env_dir is not None else None,
    }
    with contextlib.suppress(OSError):
        Path(backup_parent).mkdir(parents=True, exist_ok=True)
        (Path(backup_parent) / "combo.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )


def read_combo_association(backup_parent: Path) -> dict[str, Any] | None:
    """The helper combination recorded for a backup, or None when absent."""
    path = Path(backup_parent) / "combo.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _confirmed_combo(backup_parent: Path) -> tuple[Path, Path] | None:
    """Validate a backup's recorded combination before anything is touched.

    Returns ``(env_dir, launcher_path)`` when the combination is confirmed,
    ``None`` when the backup explicitly recorded "no managed combination".
    Raises when the record is missing or does not match what is on disk:
    rollback must never guess an environment.
    """
    assoc = read_combo_association(backup_parent)
    if assoc is None:
        raise OSError(
            errno.EINVAL,
            "backup has no helper-combination record; cannot confirm which "
            "environment and launcher it belongs to; inspect the maintenance "
            "area manually instead of guessing",
        )
    env_value = assoc.get("env")
    if env_value is None:
        return None
    env_dir = Path(str(env_value))
    if not env_dir.is_dir() or not (env_dir / "bin" / "hetu-stock").is_file():
        raise OSError(
            errno.EINVAL,
            f"backup's recorded environment is missing or unusable: {env_dir}",
        )
    recorded_version = assoc.get("version")
    if recorded_version:
        actual_version = _env_dist_version(env_dir)
        if actual_version != str(recorded_version):
            raise OSError(
                errno.EINVAL,
                "backup's recorded environment version does not match "
                f"(recorded {recorded_version}, found {actual_version}); "
                "refusing to restore an unconfirmed combination",
            )
    return env_dir, Path(str(assoc.get("launcher") or _launcher_info(None).path))


def update_host_ref(
    host: str,
    env_dir: Path | None,
    *,
    version: str | None = None,
    skill_target: Path | None = None,
) -> None:
    """Persist the environment a host's installed Skill version belongs to.

    Pruning consults these references so environments still needed by
    installed-but-not-recently-updated hosts are never removed.
    """
    if env_dir is None:
        return
    root = _managed_root_for_env(Path(env_dir))
    if root is None:
        return
    with contextlib.suppress(OSError):
        hosts = root / "hosts"
        hosts.mkdir(parents=True, exist_ok=True)
        payload = {
            "env": str(env_dir),
            "version": version or "",
            "skill_target": str(skill_target or ""),
        }
        (hosts / f"{host}.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )


def remove_host_ref(host: str) -> None:
    """Drop a host's retained-environment reference (best effort)."""
    roots: list[Path] = []
    launcher = _launcher_info(None)
    if launcher.state == "managed":
        roots.append(Path(launcher.target).parent.parent.parent)
    with contextlib.suppress(OSError):
        roots.append(managed_env_root())
    for root in roots:
        with contextlib.suppress(OSError):
            (root / "hosts" / f"{host}.json").unlink()


def managed_env_owned(env_dir: Path) -> bool:
    """Whole-environment ownership: only the combo record counts.

    ``managed_env_evidence`` (which also accepts an installed hetu_stock
    distribution) is enough to recognize a managed CLI target or a rollback
    candidate, because pointing at or choosing an environment destroys
    nothing. Deleting an entire environment is different: holding this
    package does not make the environment ours — user data may live there.
    Deletion therefore requires the combo record in the owning managed root
    to name this environment.
    """
    env_dir = Path(env_dir)
    root = _managed_root_for_env(env_dir)
    if root is None or not env_dir.is_dir():
        return False
    record = root / "installation.json"
    if not record.is_file():
        return False
    try:
        data = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    for key in ("env", "previous_env"):
        value = data.get(key)
        if isinstance(value, str) and value and Path(value) == env_dir:
            return True
    return False


def _launcher_shape(resolved: Path) -> Path | None:
    """Return the environment directory for a managed-shaped CLI target."""
    if resolved.name != "hetu-stock" or resolved.parent.name != "bin":
        return None
    env_dir = resolved.parent.parent
    if _managed_root_for_env(env_dir) is None:
        return None
    return env_dir


def _launcher_info(launcher: Path | None) -> LauncherInfo:
    launcher = managed_launcher_path() if launcher is None else Path(launcher)
    if not launcher.is_symlink():
        if launcher.exists():
            return LauncherInfo(launcher, "unmanaged", "")
        return LauncherInfo(launcher, "missing", "")
    try:
        target = os.readlink(launcher)
    except OSError:
        return LauncherInfo(launcher, "unmanaged", "")
    resolved = Path(target)
    if not resolved.is_absolute():
        resolved = (launcher.parent / resolved).resolve()
    # A managed launcher points into a versioned environment directory
    # (<envs root>/env-<token>/bin/hetu-stock, or the v0.2-era
    # <root>/venv/bin/hetu-stock); the envs root itself may live under any
    # XDG_DATA_HOME the installer used, so the structure, not the current
    # environment variables, decides. Shape alone is never enough: ownership
    # evidence is required before anything calls this environment managed.
    env_dir = _launcher_shape(resolved)
    if env_dir is not None and managed_env_evidence(env_dir):
        return LauncherInfo(launcher, "managed", str(resolved))
    return LauncherInfo(launcher, "unmanaged", str(resolved))


def _helper_info(launcher: LauncherInfo) -> HelperInfo:
    current: Path | None = None
    env_root = managed_env_root()
    if launcher.state == "managed":
        # .../envs/env-<token>/bin/hetu-stock -> env directory and envs root.
        current = Path(launcher.target).parent.parent
        env_root = current.parent.parent
    envs_root = env_root / "envs"
    envs = (
        sorted(path for path in envs_root.iterdir() if path.is_dir())
        if envs_root.is_dir()
        else []
    )
    if current is not None:
        python = current / "bin" / "python"
        cli = current / "bin" / "hetu-stock"
        if not python.is_file() or not os.access(python, os.X_OK):
            return HelperInfo(
                env_root, envs, current, "missing",
                "managed environment has no executable Python",
            )
        if not cli.is_file():
            return HelperInfo(
                env_root, envs, current, "missing",
                "managed environment has no hetu-stock command",
            )
        try:
            shebang = cli.open("rb").readline().decode("utf-8", "replace").strip()
        except OSError as exc:
            return HelperInfo(
                env_root, envs, current, "missing",
                f"cannot read launcher command: {exc}",
            )
        if shebang.startswith("#!"):
            interpreter = Path(shebang[2:].split()[0])
            if not interpreter.is_absolute():
                interpreter = cli.parent / interpreter
            if not interpreter.exists():
                return HelperInfo(
                    env_root, envs, current, "missing",
                    f"shebang interpreter does not exist: {interpreter}",
                )
        return HelperInfo(env_root, envs, current, "available")
    if envs:
        return HelperInfo(
            env_root, envs, None, "missing",
            "no managed launcher points at an environment",
        )
    return HelperInfo(env_root, envs, None, "none", "no managed environment found")


def list_skill_backups(destination_root: Path) -> list[BackupInfo]:
    """Every complete package kept in the maintenance area, newest last.

    A backup is any directory in the maintenance area that holds a full
    ``hetu-stock-analysis`` package: both installer staging directories (the
    pre-exchange copy kept as the previous version) and explicit ``backup-*``
    directories qualify. Directories referenced by a pending transaction are
    mid-operation state, not backups, and are excluded.
    """
    area = _read_area(destination_root)
    if area is None:
        return []
    pending: set[Path] = set()
    try:
        record = area.read_record()
    except OSError:
        record = None
    if record is not None and record.get("state") in {"prepared", "exchanged"}:
        staging = Path(str(record.get("staging", "")))
        if staging:
            pending.add(staging.parent)
    backups: list[BackupInfo] = []
    for path in sorted(dir_path for dir_path in area.root.iterdir() if dir_path.is_dir()):
        if path in pending:
            continue
        package = path / SKILL_DIR_NAME
        if not package.is_dir():
            continue
        try:
            validate_skill_package(package, require_manifest=True)
            verify_skill_manifest(package)
        except (OSError, SkillValidationError) as exc:
            backups.append(BackupInfo(path.name, path, package, False, str(exc)))
        else:
            backups.append(BackupInfo(path.name, path, package, True))
    return backups


def _target_integrity(target: Path) -> tuple[str, str]:
    if target.is_symlink() or not target.is_dir():
        return "missing", "no installed Skill directory at the target"
    try:
        validate_skill_package(target, require_manifest=True)
        verify_skill_manifest(target)
    except (OSError, SkillValidationError) as exc:
        return "invalid", str(exc)
    return "valid", ""


def _record_state(area: _MaintenanceArea | None) -> str:
    if area is None:
        return "none"
    try:
        record = area.read_record()
    except OSError as exc:
        return f"unreadable: {exc}"
    if record is None:
        return "none"
    state = record.get("state")
    return str(state) if state else "unknown"


def inspect_installation(
    destination_root: Path, *, launcher: Path | None = None
) -> InstallationInspection:
    """Read-only status/diagnose data; never modifies anything."""
    destination_root = Path(destination_root)
    target = destination_root / SKILL_DIR_NAME
    area = _read_area(destination_root)
    target_state, target_reason = _target_integrity(target)
    backups = list_skill_backups(destination_root)
    launcher_info = _launcher_info(launcher)
    helper = _helper_info(launcher_info)

    actions: list[str] = []
    if target_state == "missing":
        actions.append(
            "install: rerun ./scripts/install.sh --host <host> "
            "or hetu-stock skill install --host <host>"
        )
    elif target_state == "invalid":
        actions.append(
            "restore: hetu-stock skill rollback --host <host> "
            "selects the newest complete backup"
        )
    if any(not backup.valid for backup in backups):
        actions.append(
            "inspect: an invalid backup is kept on disk; "
            "resolve it manually before further maintenance"
        )
    if launcher_info.state == "unmanaged":
        actions.append(
            "leave the unmanaged launcher file untouched; "
            "remove it manually if it is no longer needed"
        )
    if helper.state == "missing":
        actions.append(
            "repair: rerun ./scripts/install.sh --host <host> "
            "to build a fresh managed environment"
        )
    if target_state == "valid" and not actions:
        actions.append("no action needed; the installation is intact")
    return InstallationInspection(
        target=target,
        target_state=target_state,
        target_reason=target_reason,
        record_state=_record_state(area),
        backups=backups,
        launcher=launcher_info,
        helper=helper,
        next_actions=actions,
    )


def rollback_skill(
    destination_root: Path,
    backup: Path | None = None,
    *,
    restore_combo: bool = True,
) -> Path:
    """Publish a complete backup back over the target, atomically.

    The replaced version stays on disk as the newest backup. Without an
    explicit ``backup`` the newest complete backup is used; when none exists
    the call fails before touching the target. With ``restore_combo`` the
    helper combination recorded for the chosen backup (environment, launcher,
    version) is validated before anything is touched and restored after the
    exchange; when the combination cannot be confirmed the call fails without
    modifying the current installation.
    """
    destination_root = Path(destination_root)
    target = destination_root / SKILL_DIR_NAME
    if target.is_symlink() or not target.is_dir():
        raise FileNotFoundError(
            errno.ENOENT, "no installed Skill directory to roll back", str(target)
        )
    backups = list_skill_backups(destination_root)
    if backup is not None:
        backup = Path(backup)
        chosen = next(
            (info for info in backups if info.path == backup or info.package == backup),
            None,
        )
        if chosen is None:
            raise FileNotFoundError(
                errno.ENOENT,
                "backup is not registered in the maintenance area; run diagnose to list backups",
                str(backup),
            )
        if not chosen.valid:
            raise SkillValidationError(
                f"backup is not a complete valid package: {chosen.reason}"
            )
    else:
        valid = [info for info in backups if info.valid]
        if not valid:
            raise FileNotFoundError(
                errno.ENOENT,
                "no complete backup available for rollback",
                str(destination_root),
            )
        chosen = valid[-1]

    combo: tuple[Path, Path] | None = None
    if restore_combo:
        # Confirms the association before the exchange; raises when the
        # backup's combination is missing or unverifiable.
        combo = _confirmed_combo(chosen.path)

    area = _MaintenanceArea(destination_root, target)
    with area:
        _recover_pending(area)
        staged = chosen.package
        new_manifest = build_skill_manifest(staged)
        old_manifest = build_skill_manifest(target)
        record = {
            "state": "prepared",
            "target": str(target),
            "staging": str(staged),
            "backup": "",
            "old_manifest": old_manifest,
            "new_manifest": new_manifest,
            "mode": "rollback-exchange",
        }
        area._write_record(record)
        exchange_directories(staged, target)
        exchanged = {**record, "state": "exchanged", "backup": str(staged.parent)}
        area._write_record(exchanged)
        _finalize_exchange(area, exchanged, staged, target, new_manifest)
        # The displaced (newer) version becomes the newest backup; record the
        # combination it belongs to while the launcher still describes it.
        _write_combo_association(staged.parent)
    if combo is not None:
        env_dir, launcher_path = combo
        previous_env: Path | None = None
        current = _launcher_info(None)
        if current.state == "managed":
            previous_env = Path(current.target).parent.parent
        _repoint_launcher(launcher_path, env_dir / "bin" / "hetu-stock")
        _set_combo_record(env_dir, previous_env)
    return target


def _repoint_launcher(launcher_path: Path, cli_path: Path) -> None:
    """Atomically point a managed launcher symlink at a CLI path."""
    launcher_path = Path(launcher_path)
    tmp_link = launcher_path.with_name(f".{launcher_path.name}.hetu-rollback")
    tmp_link.unlink(missing_ok=True)
    os.symlink(str(cli_path), tmp_link)
    os.replace(tmp_link, launcher_path)


def _set_combo_record(env_dir: Path, previous_env: Path | None) -> None:
    """Make ``env_dir`` the recorded current combination."""
    root = _managed_root_for_env(Path(env_dir))
    if root is None:
        return
    record = root / "installation.json"
    if not record.is_file():
        return
    try:
        data = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, dict):
        return
    data["env"] = str(env_dir)
    if previous_env is not None and previous_env != env_dir:
        data["previous_env"] = str(previous_env)
    with contextlib.suppress(OSError):
        record.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def plan_uninstall(
    destination_root: Path, *, launcher: Path | None = None
) -> UninstallScope:
    """Read-only uninstall scope; nothing is deleted here."""
    destination_root = Path(destination_root)
    target = destination_root / SKILL_DIR_NAME
    launcher_info = _launcher_info(launcher)
    helper = _helper_info(launcher_info)
    other_hosts = [
        default_user_skill_root(host) / SKILL_DIR_NAME
        for host in HostTarget
        if default_user_skill_root(host) != destination_root
    ]
    other_installed = [path for path in other_hosts if path.is_dir()]
    notes: list[str] = []
    if other_installed:
        notes.append(
            "shared: other hosts still have the Skill installed; the managed environment must stay"
        )
    notes.append(
        "kept: research, archived reports, authorization configuration, "
        "and unmanaged files"
    )
    return UninstallScope(
        target=target,
        target_exists=target.is_dir() and not target.is_symlink(),
        backups=list_skill_backups(destination_root),
        launcher=launcher_info,
        helper=helper,
        other_host_targets=other_installed,
        notes=notes,
    )


def uninstall_skill(
    destination_root: Path,
    *,
    remove_env: bool = False,
    launcher: Path | None = None,
    host: str | None = None,
) -> UninstallScope:
    """Remove the managed Skill and its maintenance area, keeping user data.

    The launcher is removed only when it is a HETU-managed symlink. The shared
    managed environment is removed only on explicit request and only while no
    other host still has the Skill installed.
    """
    destination_root = Path(destination_root)
    scope = plan_uninstall(destination_root, launcher=launcher)
    target = scope.target
    if target.is_symlink():
        raise OSError(
            errno.EINVAL,
            f"refusing to uninstall a symbolic link target: {target}",
        )
    if not target.is_dir():
        raise FileNotFoundError(errno.ENOENT, "nothing to uninstall", str(target))
    if remove_env and scope.other_host_targets:
        # Fail before touching anything: the shared environment must survive
        # while any other host still has the Skill installed.
        raise OSError(
            errno.EBUSY,
            "managed environment is still referenced by other hosts; keeping it",
        )

    area = _read_area(destination_root)
    if area is not None:
        with area:
            _recover_pending(area)

    shutil.rmtree(target)
    if area is not None:
        shutil.rmtree(area.root, ignore_errors=True)
        with contextlib.suppress(OSError):
            area.root.parent.rmdir()
    _fsync_dir(destination_root)

    # The launcher is shared across hosts: remove it only when no other host
    # still has the Skill installed.
    if scope.launcher.state == "managed" and not scope.other_host_targets:
        Path(scope.launcher.path).unlink(missing_ok=True)

    if remove_env:
        # Only record-owned environments are deletion candidates: having our
        # package installed is not ownership, and shape alone is never enough
        # (see managed_env_owned).
        if scope.helper.current is not None and managed_env_owned(
            scope.helper.current
        ):
            shutil.rmtree(scope.helper.current, ignore_errors=True)
        for env in scope.helper.envs:
            if env != scope.helper.current and managed_env_owned(env):
                shutil.rmtree(env, ignore_errors=True)
        envs_root = scope.helper.env_root / "envs"
        if envs_root.is_dir() and not any(envs_root.iterdir()):
            envs_root.rmdir()
        record = scope.helper.env_root / "installation.json"
        record.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            scope.helper.env_root.rmdir()
    if host is not None:
        remove_host_ref(host)
    return scope
