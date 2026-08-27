"""Normalize saved CNINFO announcement pages into one deterministic index.

The official interface is ``normalize_announcement_pages(payloads, as_of=...)``:
it merges already-saved announcement index pages, deduplicates announcements by
attachment URL across pages, orders them by ``published_at`` descending, and
keeps only announcements published no later than the timezone-aware ``as_of``.
Attachment URLs are only ever joined onto ``https://static.cninfo.com.cn/``;
absolute, scheme-carrying and leading-slash URLs are rejected. Pagination
gaps — disagreeing totals, missing or duplicate page numbers, non-consecutive
page numbers (they must run 1..max without holes), or a returned announcement
count that disagrees with the reported total in either direction — set
``page_complete`` to ``False``; the tool never concludes "no announcements" on
the caller's behalf.

CLI: ``announcement_index.py --input IN --output OUT`` where the input JSON
object holds ``pages`` (list of saved page payloads) and a timezone-aware
``as_of`` ISO-8601 datetime. Exit 0 writes a success envelope; exit 1 writes a
failure envelope for unparseable input or a rejected payload; exit 2 covers
argument errors, unreadable input, an output path equal to the input path, or
an existing output file.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import _artifact_io

ATTACHMENT_BASE = "https://static.cninfo.com.cn/"
# Any RFC 3986-style scheme prefix (http, https, ftp, javascript, data, ...)
# marks an absolute/carried URL, not a relative attachment path.
_SCHEME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")


def _decode_percent(value: str) -> str:
    """Decode percent escapes to a fixed point (bounded) so encoded traversal
    like ``%2e%2e/`` or ``..%2f`` is validated in its decoded form too."""
    current = value.strip()
    for _ in range(3):
        decoded = unquote(current)
        if decoded == current:
            break
        current = decoded
    return current


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _plain_text(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    return "".join(parser.parts).strip()


def normalize_announcement_pages(
    payloads: list[dict[str, object]],
    *,
    as_of: datetime,
) -> dict[str, object]:
    """Merge saved CNINFO pages into an index filtered at ``as_of``."""
    if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    if not isinstance(payloads, list):
        raise ValueError("payloads must be a list of page objects")

    total_count = 0
    returned_count = 0
    page_numbers: set[int] = set()
    totals: set[int] = set()
    page_complete = bool(payloads)
    seen_urls: set[str] = set()
    usable: list[tuple[datetime, dict[str, str | None]]] = []

    for position, payload in enumerate(payloads):
        if not isinstance(payload, dict):
            raise ValueError(f"payloads[{position}] must be an object")

        page_number = payload.get("pageNum")
        if (
            not isinstance(page_number, int)
            or isinstance(page_number, bool)
            or page_number in page_numbers
        ):
            page_complete = False
        else:
            page_numbers.add(page_number)

        total = payload.get("totalAnnouncement")
        if not isinstance(total, int) or isinstance(total, bool) or total < 0:
            raise ValueError(
                f"payloads[{position}] totalAnnouncement must be a non-negative integer"
            )
        totals.add(total)
        total_count = max(total_count, total)

        announcements = payload.get("announcements")
        if not isinstance(announcements, list):
            raise ValueError(f"payloads[{position}] announcements must be a list")
        returned_count += len(announcements)

        for item in announcements:
            if not isinstance(item, dict):
                raise ValueError(f"payloads[{position}] announcements contains a non-object item")
            title = item.get("announcementTitle")
            timestamp_ms = item.get("announcementTime")
            relative_url = item.get("adjunctUrl")
            categories = item.get("announcementTypeName")
            if not isinstance(title, str) or not title.strip():
                raise ValueError(
                    f"payloads[{position}] announcementTitle must be a non-empty string"
                )
            if not isinstance(timestamp_ms, int) or isinstance(timestamp_ms, bool):
                raise ValueError(
                    f"payloads[{position}] announcementTime must be an integer "
                    "millisecond timestamp"
                )
            if not isinstance(relative_url, str) or not relative_url.strip():
                raise ValueError(
                    f"payloads[{position}] adjunctUrl must be a non-empty relative path"
                )
            candidates = (relative_url.strip(), _decode_percent(relative_url))
            for candidate in candidates:
                if _SCHEME_PATTERN.match(candidate) or candidate.startswith("//"):
                    raise ValueError(
                        f"payloads[{position}] adjunctUrl must be a relative path"
                    )
                if candidate.startswith(("/", "\\")):
                    raise ValueError(
                        f"payloads[{position}] adjunctUrl must be a relative path"
                    )
                if "\\" in candidate:
                    raise ValueError(
                        f"payloads[{position}] adjunctUrl must not contain backslashes"
                    )
                if any(segment == ".." for segment in candidate.split("/")):
                    raise ValueError(
                        f"payloads[{position}] adjunctUrl must not traverse parent directories"
                    )
            if categories is not None and not (
                isinstance(categories, list)
                and all(isinstance(category, str) for category in categories)
            ):
                raise ValueError(
                    f"payloads[{position}] announcementTypeName must be a string list or null"
                )

            published_at = datetime.fromtimestamp(timestamp_ms / 1000, tz=as_of.tzinfo)
            if published_at > as_of:
                continue
            url = ATTACHMENT_BASE + relative_url.strip().lstrip("/")
            if url in seen_urls:
                continue
            seen_urls.add(url)
            category = "、".join(categories) if categories else None
            usable.append(
                (
                    published_at,
                    {
                        "title": _plain_text(title),
                        "published_at": published_at.isoformat(),
                        "url": url,
                        "category": category,
                    },
                )
            )

    if len(totals) > 1:
        page_complete = False
    if returned_count < total_count:
        page_complete = False
    if returned_count > total_count:
        page_complete = False
    if page_numbers and sorted(page_numbers) != list(
        range(1, max(page_numbers) + 1)
    ):
        page_complete = False

    usable.sort(key=lambda pair: pair[0], reverse=True)
    return {
        "total_count": total_count,
        "returned_count": returned_count,
        "usable_count": len(usable),
        "page_complete": page_complete,
        "announcements": [entry for _, entry in usable],
    }


def _transform(payload: dict[str, Any]) -> object:
    pages = payload.get("pages")
    if not isinstance(pages, list):
        raise ValueError("input must contain a pages list")
    as_of_raw = payload.get("as_of")
    if not isinstance(as_of_raw, str):
        raise ValueError("input must contain an as_of ISO-8601 datetime string")
    try:
        as_of = datetime.fromisoformat(as_of_raw)
    except ValueError as exc:
        raise ValueError("as_of must be an ISO-8601 datetime") from exc
    return normalize_announcement_pages(pages, as_of=as_of)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Normalize saved CNINFO announcement pages into one index."
    )
    parser.add_argument("--input", required=True, help="saved pages JSON path")
    parser.add_argument("--output", required=True, help="envelope JSON path to create")
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    output_path = Path(args.output)
    if input_path.resolve() == output_path.resolve():
        print("output_path must differ from input_path", file=sys.stderr)
        return 2
    if output_path.exists():
        print("output_path already exists", file=sys.stderr)
        return 2
    if not input_path.is_file():
        print(f"input file not found: {input_path}", file=sys.stderr)
        return 2

    try:
        _artifact_io.run_transform(
            tool_name="announcement_index",
            input_path=input_path,
            output_path=output_path,
            transform=_transform,
        )
    except _artifact_io.OutputConflictError as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 2
    except _artifact_io.InputUnreadableError as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 2
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
