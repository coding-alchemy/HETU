"""Mechanical contracts for phase-two Skill work packages.

This module intentionally validates structure only.  It does not decide when a
package applies or interpret the explanations written by an Agent.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlsplit

import yaml
from markdown_it import MarkdownIt
from markdown_it.token import Token

from hetu_stock.skill.package import SkillValidationError

CORE_WORK_PACKAGE_IDS = tuple(f"W{index}" for index in range(11))
REQUIRED_WORK_PACKAGE_FIELDS = {
    "id",
    "name",
    "kind",
    "required_when",
    "start_requires",
    "finalize_requires",
    "may_reopen",
    "coverage_role",
}
OPTIONAL_WORK_PACKAGE_FIELDS = {"shared_baseline"}
REQUIRED_SECTIONS = (
    "## 研究目标",
    "## 适用边界",
    "## 依赖",
    "## 必须回答",
    "## 证据要求",
    "## 主张边界",
    "## 反证与冲突",
    "## 缺口与降级",
    "## 产物更新",
    "## 回访触发",
)


@dataclass(frozen=True)
class WorkPackageSpec:
    id: str
    name: str
    kind: Literal["core", "official-extension"]
    required_when: str
    start_requires: tuple[str, ...]
    finalize_requires: tuple[str, ...]
    may_reopen: tuple[str, ...]
    coverage_role: Literal["required", "supplemental"]
    shared_baseline: tuple[str, ...] = ()


def _closed_frontmatter(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise SkillValidationError(f"{path}: closed YAML frontmatter is required")
    raw, body = text[4:].split("\n---\n", 1)
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise SkillValidationError(f"{path}: frontmatter is not valid YAML") from exc
    if not isinstance(parsed, dict):
        raise SkillValidationError(f"{path}: frontmatter must be a mapping")
    return cast(dict[str, Any], parsed), body


def _required_headings(body: str, path: Path) -> None:
    tokens = MarkdownIt("commonmark").parse(body)
    headings = {
        f"{'#' * int(token.tag.removeprefix('h'))} {tokens[index + 1].content}"
        for index, token in enumerate(tokens[:-1])
        if token.type == "heading_open"
        and token.level == 0
        and tokens[index + 1].type == "inline"
    }
    missing = [heading for heading in REQUIRED_SECTIONS if heading not in headings]
    if missing:
        raise SkillValidationError(f"{path}: missing sections: {missing}")


def _revisit_content(
    body: str,
) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    """Return real paragraphs and parsed direct target items from the revisit H2."""

    tokens = MarkdownIt("commonmark").parse(body)
    section_start: int | None = None
    for index, token in enumerate(tokens[:-1]):
        if (
            token.type == "heading_open"
            and token.tag == "h2"
            and token.level == 0
            and tokens[index + 1].type == "inline"
            and tokens[index + 1].content.strip() == "回访触发"
        ):
            section_start = index + 1
            break
    if section_start is None:
        return (), ()

    section_end = len(tokens)
    for index in range(section_start, len(tokens)):
        token = tokens[index]
        if token.type == "heading_open" and token.tag == "h2" and token.level == 0:
            section_end = index
            break

    paragraphs: list[str] = []
    target_items: list[tuple[str, str]] = []
    for index in range(section_start, section_end):
        token = tokens[index]
        if (
            token.type == "paragraph_open"
            and token.level == 0
            and index + 1 < section_end
            and tokens[index + 1].type == "inline"
        ):
            paragraphs.extend(
                line.strip()
                for line in tokens[index + 1].content.splitlines()
                if line.strip()
            )
        if token.type != "list_item_open" or token.level != 1:
            continue
        item_found = False
        for child in tokens[index + 1 : section_end]:
            if child.type == "list_item_close" and child.level == token.level:
                break
            if child.type == "inline" and child.level == token.level + 2:
                target, separator, explanation = child.content.strip().partition(":")
                target = target.strip()
                explanation = explanation.strip()
                if not separator or not target or not explanation:
                    raise SkillValidationError("malformed reopen explanation item")
                target_items.append((target, explanation))
                item_found = True
                break
        if not item_found:
            raise SkillValidationError("malformed reopen explanation item")
    return tuple(paragraphs), tuple(target_items)


def _first_duplicate(values: tuple[str, ...]) -> str | None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            return value
        seen.add(value)
    return None


def _validate_revisit_contract(
    may_reopen: tuple[str, ...],
    paragraphs: tuple[str, ...],
    target_items: tuple[tuple[str, str], ...],
) -> None:
    duplicate_declared = _first_duplicate(may_reopen)
    if duplicate_declared is not None:
        raise SkillValidationError(
            f"may_reopen targets must be unique: {duplicate_declared}"
        )

    has_not_applicable = "不适用" in paragraphs
    if not may_reopen:
        if not has_not_applicable:
            raise SkillValidationError("empty may_reopen requires exact 不适用 line")
        if target_items:
            raise SkillValidationError(
                "empty may_reopen must not contain reopen targets"
            )
        return

    if has_not_applicable:
        raise SkillValidationError("nonempty may_reopen must not contain 不适用")

    body_targets = tuple(target for target, _explanation in target_items)
    duplicate_body = _first_duplicate(body_targets)
    if duplicate_body is not None:
        raise SkillValidationError(
            f"duplicate reopen explanation for {duplicate_body}"
        )
    declared_targets = set(may_reopen)
    for target in body_targets:
        if target not in declared_targets:
            raise SkillValidationError(
                f"undeclared reopen explanation target: {target}"
            )
    body_target_set = set(body_targets)
    for target in may_reopen:
        if target not in body_target_set:
            raise SkillValidationError(f"missing reopen explanation for {target}")


def load_work_package(path: Path) -> WorkPackageSpec:
    payload, body = _closed_frontmatter(path)
    fields = set(payload)
    if not REQUIRED_WORK_PACKAGE_FIELDS.issubset(payload) or not fields.issubset(
        REQUIRED_WORK_PACKAGE_FIELDS | OPTIONAL_WORK_PACKAGE_FIELDS
    ):
        raise SkillValidationError(f"{path}: work-package fields do not match the closed contract")
    scalar_fields = ("id", "name", "kind", "required_when", "coverage_role")
    if any(not isinstance(payload[field], str) for field in scalar_fields):
        raise SkillValidationError(f"{path}: scalar frontmatter fields must be strings")
    sequence_fields = ("start_requires", "finalize_requires", "may_reopen")
    if any(
        not isinstance(payload[field], list)
        or any(not isinstance(value, str) for value in payload[field])
        for field in sequence_fields
    ):
        raise SkillValidationError(f"{path}: dependency fields must be string lists")
    if "shared_baseline" in payload and (
        not isinstance(payload["shared_baseline"], list)
        or any(not isinstance(value, str) for value in payload["shared_baseline"])
    ):
        raise SkillValidationError(f"{path}: shared_baseline must be a string list when present")
    _required_headings(body, path)
    may_reopen = tuple(payload["may_reopen"])
    revisit_paragraphs, revisit_items = _revisit_content(body)
    try:
        _validate_revisit_contract(may_reopen, revisit_paragraphs, revisit_items)
    except SkillValidationError as exc:
        raise SkillValidationError(f"{path}: {exc}") from exc
    return WorkPackageSpec(
        id=payload["id"],
        name=payload["name"],
        kind=cast(Literal["core", "official-extension"], payload["kind"]),
        required_when=payload["required_when"],
        start_requires=tuple(payload["start_requires"]),
        finalize_requires=tuple(payload["finalize_requires"]),
        may_reopen=may_reopen,
        coverage_role=cast(Literal["required", "supplemental"], payload["coverage_role"]),
        shared_baseline=tuple(payload.get("shared_baseline", ())),
    )


_CATALOG_COLUMNS = ("ID", "名称", "覆盖角色", "适用条件", "链接")
CatalogCell = tuple[str, tuple[str, ...], str]
CatalogRow = tuple[CatalogCell, ...]
CatalogTableRow = tuple[CatalogRow, int]


def _table_row_cells(
    tokens: list[Token], start: int, end: int
) -> CatalogRow:
    cells: list[CatalogCell] = []
    for index in range(start, end):
        token = tokens[index]
        if token.type not in {"th_open", "td_open"}:
            continue
        text = ""
        links: list[str] = []
        for child in tokens[index + 1 : end]:
            if child.type == f"{token.tag}_close" and child.level == token.level:
                break
            if child.type != "inline":
                continue
            text = child.content.strip()
            for inline_child in child.children or ():
                if inline_child.type == "link_open":
                    href = inline_child.attrGet("href")
                    if isinstance(href, str):
                        links.append(href)
        cells.append((text, tuple(links), token.tag))
    return tuple(cells)


def _markdown_table_cell_count(line: str) -> int:
    """Count unescaped Markdown table cells before markdown-it truncates extras."""

    row = line.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]

    separators = 0
    escaped = False
    for character in row:
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == "|":
            separators += 1
    return separators + 1


def _is_local_relative_catalog_target(target: str) -> bool:
    parsed = urlsplit(target)
    path = Path(target)
    return bool(
        target
        and not parsed.scheme
        and not parsed.netloc
        and not parsed.query
        and not parsed.fragment
        and not path.is_absolute()
        and ".." not in path.parts
    )


def _catalog_rows(text: str) -> tuple[tuple[str, str], ...]:
    tokens = MarkdownIt("commonmark").enable("table").parse(text)
    lines = text.splitlines()
    recognized_tables: list[tuple[CatalogTableRow, ...]] = []
    index = 0
    while index < len(tokens):
        if tokens[index].type != "table_open":
            index += 1
            continue
        table_level = tokens[index].level
        table_end = next(
            cursor
            for cursor in range(index + 1, len(tokens))
            if tokens[cursor].type == "table_close" and tokens[cursor].level == table_level
        )
        header: tuple[str, ...] | None = None
        body_rows: list[CatalogTableRow] = []
        cursor = index + 1
        while cursor < table_end:
            if tokens[cursor].type != "tr_open":
                cursor += 1
                continue
            row_level = tokens[cursor].level
            row_end = next(
                row_cursor
                for row_cursor in range(cursor + 1, table_end)
                if tokens[row_cursor].type == "tr_close"
                and tokens[row_cursor].level == row_level
            )
            cells = _table_row_cells(tokens, cursor + 1, row_end)
            if cells and all(cell[2] == "th" for cell in cells):
                header = tuple(" ".join(cell[0].split()) for cell in cells)
            elif cells and all(cell[2] == "td" for cell in cells):
                row_map = tokens[cursor].map
                raw_cell_count = (
                    _markdown_table_cell_count(lines[row_map[0]])
                    if row_map is not None
                    else len(cells)
                )
                body_rows.append((cells, raw_cell_count))
            cursor = row_end + 1
        if header == _CATALOG_COLUMNS:
            recognized_tables.append(tuple(body_rows))
        index = table_end + 1

    if len(recognized_tables) != 1:
        raise SkillValidationError("catalog must contain exactly one registration table")

    registrations: list[tuple[str, str]] = []
    for cells, raw_cell_count in recognized_tables[0]:
        if len(cells) != len(_CATALOG_COLUMNS) or raw_cell_count != len(_CATALOG_COLUMNS):
            raise SkillValidationError("malformed catalog row")
        package_id = cells[0][0].strip()
        if not package_id:
            raise SkillValidationError("malformed catalog row")
        links = cells[-1][1]
        if len(links) != 1:
            raise SkillValidationError("catalog file cell must contain exactly one link")
        target = links[0].strip()
        if not _is_local_relative_catalog_target(target):
            raise SkillValidationError("catalog target must be a local relative path")
        registrations.append((package_id, target))
    return tuple(registrations)


def _catalog_entries(catalog: Path, packages_root: Path) -> dict[str, str]:
    if not catalog.is_file():
        raise SkillValidationError("catalog is required")
    entries: dict[str, str] = {}
    for package_id, target in _catalog_rows(catalog.read_text(encoding="utf-8")):
        if package_id in entries:
            raise SkillValidationError(f"duplicate catalog registration: {package_id}")
        target_path = packages_root / target
        if not target_path.is_file():
            raise SkillValidationError(f"catalog target does not exist: {target}")
        entries[package_id] = target
    return entries


def _is_official_work_package_id(package_id: str) -> bool:
    if not package_id.startswith("WX-"):
        return False
    domain, separator, name = package_id.removeprefix("WX-").partition("-")
    return bool(domain and separator and name)


def _validate_no_cycles(specifications: dict[str, WorkPackageSpec]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(package_id: str) -> None:
        if package_id in visiting:
            raise SkillValidationError(f"dependency graph contains a cycle at {package_id}")
        if package_id in visited:
            return
        visiting.add(package_id)
        spec = specifications[package_id]
        for target in (*spec.start_requires, *spec.finalize_requires):
            visit(target)
        visiting.remove(package_id)
        visited.add(package_id)

    for package_id in specifications:
        visit(package_id)


def validate_work_package_contract(
    root: Path,
    *,
    manifest_files: frozenset[str] | None,
    expected_official_count: int,
) -> None:
    packages_root = root / "references" / "work-packages"
    core_paths = sorted((packages_root / "core").glob("*.md"))
    official_paths = sorted((packages_root / "official").glob("*.md"))
    allowed_markdown_paths = {
        packages_root / "catalog.md",
        *core_paths,
        *official_paths,
    }
    unexpected_paths = sorted(
        path.relative_to(root).as_posix()
        for path in packages_root.rglob("*")
        if path.is_file() and path not in allowed_markdown_paths
    )
    if unexpected_paths:
        formatted_paths = "\n".join(f"- {path}" for path in unexpected_paths)
        raise SkillValidationError(
            f"unexpected work-package paths:\n{formatted_paths}"
        )
    paths = core_paths + official_paths
    specs_by_path = {path: load_work_package(path) for path in paths}
    specifications: dict[str, WorkPackageSpec] = {}
    for _path, spec in specs_by_path.items():
        if spec.id in specifications:
            raise SkillValidationError(f"duplicate work-package ID: {spec.id}")
        specifications[spec.id] = spec
    for path, spec in specs_by_path.items():
        expected_prefix = f"{spec.id}-" if path.parent.name == "core" else spec.id
        filename_matches = (
            path.stem.startswith(expected_prefix)
            if path.parent.name == "core"
            else path.stem == expected_prefix
        )
        if not filename_matches:
            raise SkillValidationError(f"filename does not match work-package ID: {path.name}")

    core_ids = tuple(spec.id for path, spec in specs_by_path.items() if path.parent.name == "core")
    if set(core_ids) != set(CORE_WORK_PACKAGE_IDS) or len(core_ids) != len(CORE_WORK_PACKAGE_IDS):
        raise SkillValidationError("core work-package IDs must be exactly W0-W10")
    official_specs = [
        spec for path, spec in specs_by_path.items() if path.parent.name == "official"
    ]
    if len(official_specs) != expected_official_count:
        raise SkillValidationError("official work-package count does not match contract")

    for path, spec in specs_by_path.items():
        if path.parent.name == "core" and (spec.kind != "core" or spec.coverage_role != "required"):
            raise SkillValidationError(f"core coverage contract violated: {spec.id}")
        if path.parent.name == "official" and (
            spec.kind != "official-extension" or spec.coverage_role != "supplemental"
        ):
            raise SkillValidationError(f"official coverage contract violated: {spec.id}")
        if path.parent.name == "official" and not _is_official_work_package_id(spec.id):
            raise SkillValidationError(
                f"official extension ID must be WX-<DOMAIN>-<NAME>: {spec.id}"
            )
        for target in (*spec.start_requires, *spec.finalize_requires, *spec.may_reopen):
            if target not in specifications:
                raise SkillValidationError(f"unknown dependency target: {target}")
        for target in spec.shared_baseline:
            if target not in specifications:
                raise SkillValidationError(f"unknown shared baseline target: {target}")
            if target == spec.id:
                raise SkillValidationError(
                    f"shared baseline must not reference itself: {spec.id}"
                )
            if spec.id not in specifications[target].shared_baseline:
                raise SkillValidationError(
                    f"shared baseline must be symmetric: {spec.id} -> {target}"
                )
    if specifications["W2"].shared_baseline != ("W5",) or specifications["W5"].shared_baseline != (
        "W2",
    ):
        raise SkillValidationError("W2/W5 shared baseline must be symmetric")
    _validate_no_cycles(specifications)

    catalog = packages_root / "catalog.md"
    catalog_entries = _catalog_entries(catalog, packages_root)
    expected_entries = {
        spec.id: path.relative_to(packages_root).as_posix() for path, spec in specs_by_path.items()
    }
    if catalog_entries != expected_entries:
        raise SkillValidationError("catalog registrations do not match work packages")

    if manifest_files is not None:
        required_paths = {path.relative_to(root).as_posix() for path in [catalog, *specs_by_path]}
        missing = required_paths - manifest_files
        if missing:
            raise SkillValidationError(f"manifest coverage missing: {sorted(missing)}")


def validate_phase2_frontmatter(root: Path) -> None:
    text = root.joinpath("SKILL.md").read_text(encoding="utf-8")
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise SkillValidationError("SKILL.md requires closed YAML frontmatter")
    raw, _body = text[4:].split("\n---\n", 1)
    try:
        frontmatter = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise SkillValidationError("SKILL.md frontmatter must be a mapping") from exc
    if not isinstance(frontmatter, dict):
        raise SkillValidationError("SKILL.md frontmatter must be a mapping")
    if set(frontmatter) != {"name", "description"}:
        raise SkillValidationError("SKILL.md frontmatter fields must be exactly: name, description")
    if frontmatter["name"] != root.name or frontmatter["name"] != "hetu-stock-analysis":
        raise SkillValidationError("SKILL.md name must match the canonical directory")
    description = frontmatter["description"]
    if not isinstance(description, str) or not 1 <= len(description.strip()) <= 1024:
        raise SkillValidationError("SKILL.md description must contain 1-1024 characters")
