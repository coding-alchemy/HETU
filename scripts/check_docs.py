#!/usr/bin/env python3
"""Check tracked Markdown links and current obsolete CLI references."""

from __future__ import annotations

import argparse
import re
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt
from markdown_it.token import Token

LINK_SCOPE = ("README.md", "docs", "specs", "skills")
CURRENT_DOCUMENTS = (
    Path("README.md"),
    Path("docs/agent-skill-usage.md"),
)
CANONICAL_SKILL_ROOT = Path("skills/hetu-stock-analysis")

_PREFIXED_OBSOLETE_COMMAND = re.compile(
    r"\bhetu-stock\s+(?:run\s+(?:init|submit|resume)|report\s+render)\b"
)
_CODE_OBSOLETE_COMMAND = re.compile(
    r"\b(?:hetu-stock\s+)?(?:run\s+(?:init|submit|resume)|report\s+render)\b"
)
_V01_TITLE = re.compile(r"^v0\.1\b", re.IGNORECASE)
_HISTORICAL_EXAMPLE_PREFIXES = (
    "legacy example",
    "historical example",
    "历史示例",
)


@dataclass(frozen=True, slots=True)
class _MarkdownSection:
    lines: tuple[str, ...]
    tokens: tuple[Token, ...]
    start_line: int

    @classmethod
    def parse(cls, text: str, *, start_line: int = 1) -> _MarkdownSection:
        return cls(tuple(text.splitlines()), tuple(MarkdownIt().parse(text)), start_line)

    def slice(self, start: int, end: int) -> _MarkdownSection:
        return self.parse("\n".join(self.lines[start:end]), start_line=self.start_line + start)


@dataclass(frozen=True, slots=True)
class _Heading:
    level: int
    line_index: int
    title: str


def _tracked_paths(root: Path, pathspecs: Sequence[str]) -> tuple[Path, ...]:
    completed = subprocess.run(
        ["git", "ls-files", "-z", "--", *pathspecs],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return tuple(
        sorted(
            Path(item.decode("utf-8"))
            for item in completed.stdout.split(b"\0")
            if item
        )
    )


def _markdown_destinations(tokens: Iterable[Token]) -> Iterable[str]:
    for token in tokens:
        if token.type == "link_open":
            destination = token.attrGet("href")
            if destination is not None:
                yield destination
        elif token.type == "image":
            destination = token.attrGet("src")
            if destination is not None:
                yield destination
        if token.children:
            yield from _markdown_destinations(token.children)


def _relative_target(document: Path, destination: str) -> Path | None:
    parsed = urlsplit(destination)
    if parsed.scheme or parsed.netloc:
        return None
    path = unquote(parsed.path)
    if not path:
        return document
    if path.startswith("/"):
        return Path(path.removeprefix("/"))
    return document.parent / path


def _link_failures(root: Path, documents: Sequence[Path]) -> tuple[str, ...]:
    failures: list[str] = []
    repository_root = root.resolve()
    for document in documents:
        text = repository_root.joinpath(document).read_text(encoding="utf-8")
        section = _MarkdownSection.parse(text)
        for destination in _markdown_destinations(section.tokens):
            target = _relative_target(document, destination)
            if target is None:
                continue
            try:
                resolved_target = repository_root.joinpath(target).resolve(strict=False)
            except (OSError, RuntimeError, ValueError):
                failures.append(f"{document.as_posix()}: unresolved {destination}")
                continue
            if not resolved_target.is_relative_to(repository_root):
                failures.append(
                    f"{document.as_posix()}: outside repository {destination}"
                )
            elif not resolved_target.exists():
                failures.append(f"{document.as_posix()}: missing {destination}")
    return tuple(sorted(failures))


def _normalized_inline_text(token: Token) -> str:
    content = "".join(child.content for child in token.children or ())
    return " ".join(content.casefold().split())


def _headings(section: _MarkdownSection) -> tuple[_Heading, ...]:
    headings: list[_Heading] = []
    for index, token in enumerate(section.tokens[:-1]):
        if token.type != "heading_open" or token.map is None:
            continue
        inline = section.tokens[index + 1]
        if inline.type != "inline":
            continue
        headings.append(
            _Heading(
                level=int(token.tag.removeprefix("h")),
                line_index=token.map[0],
                title=_normalized_inline_text(inline),
            )
        )
    return tuple(headings)


def _current_v01(document: _MarkdownSection) -> _MarkdownSection | None:
    headings = _headings(document)
    start_heading = next(
        (heading for heading in headings if heading.level == 2 and _V01_TITLE.match(heading.title)),
        None,
    )
    if start_heading is None:
        return None
    end_heading = next(
        (
            heading
            for heading in headings
            if heading.level == 2 and heading.line_index > start_heading.line_index
        ),
        None,
    )
    end = end_heading.line_index if end_heading is not None else len(document.lines)
    return document.slice(start_heading.line_index, end)


def _is_historical_example_heading(title: str) -> bool:
    return any(
        title == label or title.startswith((f"{label}:", f"{label}："))
        for label in _HISTORICAL_EXAMPLE_PREFIXES
    )


def _historical_example_ranges(section: _MarkdownSection) -> tuple[range, ...]:
    headings = _headings(section)
    ranges: list[range] = []
    for index, heading in enumerate(headings):
        if not _is_historical_example_heading(heading.title):
            continue
        next_heading = next(
            (
                candidate
                for candidate in headings[index + 1 :]
                if candidate.level <= heading.level
            ),
            None,
        )
        end = next_heading.line_index if next_heading is not None else len(section.lines)
        ranges.append(range(heading.line_index, end))
    return tuple(ranges)


def _logical_code_lines(
    lines: Sequence[str],
    *,
    start_line: int,
) -> Iterable[tuple[int, str]]:
    first_line: int | None = None
    parts: list[str] = []
    for offset, line in enumerate(lines):
        line_number = start_line + offset
        trimmed = line.rstrip()
        trailing_backslashes = len(trimmed) - len(trimmed.rstrip("\\"))
        continued = trailing_backslashes % 2 == 1
        if first_line is None:
            first_line = line_number
        parts.append(trimmed[:-1] if continued else line)
        if continued:
            continue
        if len(parts) == 1:
            yield first_line, parts[0]
        else:
            yield first_line, " ".join(part.strip() for part in parts)
        first_line = None
        parts = []
    if parts and first_line is not None:
        yield first_line, " ".join(part.strip() for part in parts)


def _code_contexts(section: _MarkdownSection) -> Iterable[tuple[int, str]]:
    for token in section.tokens:
        if token.map is None:
            continue
        if token.type in {"fence", "code_block"}:
            offset = 1 if token.type == "fence" else 0
            yield from _logical_code_lines(
                token.content.splitlines(),
                start_line=section.start_line + token.map[0] + offset,
            )
        elif token.type == "inline":
            line_number = section.start_line + token.map[0]
            for child in token.children or ():
                if child.type == "code_inline":
                    yield line_number, child.content


def _command_occurrences(section: _MarkdownSection) -> tuple[tuple[int, str], ...]:
    occurrences: set[tuple[int, str]] = set()
    for line_number, line in enumerate(section.lines, start=section.start_line):
        occurrences.update(
            (line_number, match.group(0))
            for match in _PREFIXED_OBSOLETE_COMMAND.finditer(line)
        )
    for line_number, content in _code_contexts(section):
        occurrences.update(
            (line_number, match.group(0))
            for match in _CODE_OBSOLETE_COMMAND.finditer(content)
        )
    return tuple(sorted(occurrences))


def _obsolete_failures(
    root: Path,
    tracked_markdown: Sequence[Path],
) -> tuple[tuple[str, ...], int]:
    scoped: list[tuple[Path, _MarkdownSection]] = []
    for document in CURRENT_DOCUMENTS:
        if document in tracked_markdown:
            scoped.append(
                (
                    document,
                    _MarkdownSection.parse(root.joinpath(document).read_text(encoding="utf-8")),
                )
            )

    changelog = Path("CHANGELOG.md")
    if changelog in tracked_markdown:
        changelog_document = _MarkdownSection.parse(
            root.joinpath(changelog).read_text(encoding="utf-8")
        )
        current = _current_v01(changelog_document)
        if current is None:
            return (("CHANGELOG.md: missing current V0.1 section",), len(scoped))
        scoped.append((changelog, current))

    scoped.extend(
        (
            document,
            _MarkdownSection.parse(root.joinpath(document).read_text(encoding="utf-8")),
        )
        for document in tracked_markdown
        if document.suffix == ".md"
        and document != CANONICAL_SKILL_ROOT
        and CANONICAL_SKILL_ROOT in document.parents
    )

    failures: list[str] = []
    for document, section in scoped:
        historical_ranges = _historical_example_ranges(section)
        for line_number, command in _command_occurrences(section):
            relative_line = line_number - section.start_line
            if any(relative_line in item for item in historical_ranges):
                continue
            failures.append(
                f"{document.as_posix()}:{line_number}: obsolete command {command}"
            )
    return tuple(sorted(failures)), len(scoped)


def check(root: Path) -> int:
    tracked = _tracked_paths(root, (*LINK_SCOPE, "CHANGELOG.md"))
    link_documents = tuple(
        path
        for path in tracked
        if path.suffix.lower() == ".md"
        and (path == Path("README.md") or path.parts[0] in {"docs", "specs", "skills"})
    )
    link_failures = _link_failures(root, link_documents)
    obsolete_failures, obsolete_scope_count = _obsolete_failures(root, tracked)

    if link_failures:
        print("Markdown links: FAIL")
        for failure in link_failures:
            print(f"- {failure}")
    else:
        print(f"Markdown links: PASS ({len(link_documents)} tracked files)")

    if obsolete_failures:
        print("Obsolete commands: FAIL")
        for failure in obsolete_failures:
            print(f"- {failure}")
    else:
        print(f"Obsolete commands: PASS ({obsolete_scope_count} scoped documents)")

    return int(bool(link_failures or obsolete_failures))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    arguments = parser.parse_args(argv)
    return check(arguments.root.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
