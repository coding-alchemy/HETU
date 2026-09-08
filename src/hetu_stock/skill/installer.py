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

import ctypes
import errno
import fcntl
import hashlib
import json
import os
import shutil
import sys
import time
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

    def __init__(self, destination_root: Path, target: Path) -> None:
        destination_root.mkdir(parents=True, exist_ok=True)
        self.target = target
        normalized = hashlib.sha256(
            str(target.resolve()).encode("utf-8")
        ).hexdigest()[:16]
        self.root = destination_root.parent / MAINTENANCE_DIR_NAME / normalized
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
        exchange_directories(staging, target)
        exchanged: dict[str, object] = {
            **record,
            "state": "exchanged",
            "backup": str(staging.parent),
        }
        area._write_record(exchanged)
        _finalize_exchange(area, exchanged, staging, target, new_manifest)
        return target
