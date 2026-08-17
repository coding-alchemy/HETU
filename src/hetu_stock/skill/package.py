import json
import re
from pathlib import Path, PureWindowsPath
from typing import Any
from urllib.parse import unquote, urlparse

from markdown_it import MarkdownIt


class SkillValidationError(ValueError):
    """Raised when the canonical Agent Skill package is incomplete or invalid."""


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
FORBIDDEN_PHASE_ONE_PATHS = (
    "references/controller.md",
    "references/workflow.md",
    "references/pause-resume.md",
    "references/stages",
    "references/examples/stage-result.example.json",
    "references/schema",
    "templates/report.md.j2",
)


def _is_windows_drive_scheme(scheme: str) -> bool:
    return len(scheme) == 1 and scheme.isalpha()


class _PermissiveMarkdownIt(MarkdownIt):
    def validateLink(self, url: str) -> bool:
        return True


def _extract_targets(text: str) -> list[str]:
    md = _PermissiveMarkdownIt()
    tokens: Any = md.parse(text, {})
    targets: list[str] = []

    def walk(token_list: Any) -> None:
        for token in token_list:
            token_type = getattr(token, "type", None)
            if token_type == "link_open":
                href = token.attrGet("href")
                if href is not None:
                    targets.append(href)
            elif token_type == "image":
                src = token.attrGet("src")
                if src is not None:
                    targets.append(src)
            children = getattr(token, "children", None)
            if children:
                walk(children)

    walk(tokens)
    return targets


def _load_manifest_files(root: Path) -> frozenset[str]:
    manifest_path = root / "MANIFEST.json"
    try:
        payload: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SkillValidationError(f"MANIFEST.json is invalid: {exc}") from exc

    if not isinstance(payload, dict) or "files" not in payload:
        raise SkillValidationError("MANIFEST.json must contain a 'files' object")
    entries = payload["files"]
    if not isinstance(entries, dict):
        raise SkillValidationError("MANIFEST.json 'files' must be a mapping")
    return frozenset(entries)


def validate_skill_package(
    root: Path,
    *,
    require_manifest: bool = False,
) -> None:
    # Imported locally because work_packages raises this module's public error.
    from hetu_stock.skill.work_packages import (
        validate_phase2_frontmatter,
        validate_work_package_contract,
    )

    root_resolved = root.resolve()
    if require_manifest and not (root / "MANIFEST.json").is_file():
        raise SkillValidationError(
            "MANIFEST.json is required for deployable Skill validation"
        )

    skill_file = (root / "SKILL.md").resolve()
    if not skill_file.is_file() or not skill_file.is_relative_to(root_resolved):
        raise SkillValidationError("SKILL.md is required and must be inside the skill root")

    validate_phase2_frontmatter(root)
    manifest_files: frozenset[str] | None = None
    if require_manifest:
        manifest_files = _load_manifest_files(root)
    try:
        validate_work_package_contract(
            root,
            manifest_files=manifest_files,
            expected_official_count=0,
        )
    except ValueError as exc:
        raise SkillValidationError(str(exc)) from exc
    for relative in FORBIDDEN_PHASE_ONE_PATHS:
        if root.joinpath(relative).exists():
            raise SkillValidationError(f"forbidden phase-one path: {relative}")

    text = skill_file.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(text)
    if match is None:  # validate_phase2_frontmatter has already rejected this case.
        raise SkillValidationError("SKILL.md requires valid closed frontmatter")
    body = text[match.end():]
    for target in _extract_targets(body):
        parsed = urlparse(target)
        if parsed.scheme == "file":
            raise SkillValidationError(f"file: scheme is not allowed: {target}")
        if _is_windows_drive_scheme(parsed.scheme):
            raise SkillValidationError(f"Windows drive paths are not allowed: {target}")
        if parsed.scheme or parsed.netloc:
            continue
        path = unquote(parsed.path)
        if not path:
            continue
        if "\\" in path:
            raise SkillValidationError(f"backslash paths are not allowed: {target}")
        windows_path = PureWindowsPath(path)
        if windows_path.drive or windows_path.is_absolute():
            raise SkillValidationError(
                f"Windows absolute or drive-relative paths are not allowed: {target}"
            )
        if path.startswith("/"):
            raise SkillValidationError(f"absolute paths are not allowed: {target}")
        resolved = (root_resolved / path).resolve()
        if not resolved.is_relative_to(root_resolved):
            raise SkillValidationError(f"referenced file is outside the skill root: {target}")
        if not resolved.is_file():
            raise SkillValidationError(f"missing referenced file: {target}")
