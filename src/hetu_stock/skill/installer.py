import hashlib
import json
import os
import shutil
from enum import StrEnum
from pathlib import Path

from hetu_stock.skill.package import SkillValidationError, validate_skill_package


class HostTarget(StrEnum):
    CODEX = "codex"
    CLAUDE = "claude"
    OPENCODE = "opencode"


def default_user_skill_root(host: HostTarget) -> Path:
    home = Path(os.environ["HOME"])
    if host is HostTarget.CODEX:
        return Path(os.environ.get("CODEX_HOME", home / ".codex")) / "skills"
    if host is HostTarget.CLAUDE:
        return home / ".claude" / "skills"
    xdg = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    return xdg / "opencode" / "skills"


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


def install_skill(source: Path, destination_root: Path, *, force: bool = False) -> Path:
    verify_skill_manifest(source)
    validate_skill_package(source, require_manifest=True)
    target = destination_root / "hetu-stock-analysis"
    if target.exists():
        if not force:
            raise FileExistsError(target)
        shutil.rmtree(target)
    destination_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__"))
    try:
        validate_skill_package(target, require_manifest=True)
        verify_skill_manifest(target)
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return target
