"""Normalize an already-fetched CNINFO announcement index page."""

from __future__ import annotations

from datetime import datetime
from html.parser import HTMLParser
from typing import Any


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


def normalize_announcement_page(
    payload: dict[str, Any],
    *,
    as_of: datetime,
) -> dict[str, Any]:
    """Filter a CNINFO page to announcements published no later than ``as_of``."""
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")

    total = payload.get("totalAnnouncement")
    announcements = payload.get("announcements")
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        raise ValueError("totalAnnouncement must be a non-negative integer")
    if not isinstance(announcements, list):
        raise ValueError("announcements must be a list")

    usable: list[dict[str, str | None]] = []
    for item in announcements:
        if not isinstance(item, dict):
            raise ValueError("announcements contains a non-object item")
        title = item.get("announcementTitle")
        timestamp_ms = item.get("announcementTime")
        relative_url = item.get("adjunctUrl")
        categories = item.get("announcementTypeName")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("announcementTitle must be a non-empty string")
        if not isinstance(timestamp_ms, int) or isinstance(timestamp_ms, bool):
            raise ValueError("announcementTime must be an integer millisecond timestamp")
        if not isinstance(relative_url, str) or not relative_url.strip():
            raise ValueError("adjunctUrl must be a non-empty relative path")
        if relative_url.startswith(("http://", "https://", "//")):
            raise ValueError("adjunctUrl must be a relative path")
        if categories is not None and not (
            isinstance(categories, list)
            and all(isinstance(category, str) for category in categories)
        ):
            raise ValueError("announcementTypeName must be a string list or null")

        published_at = datetime.fromtimestamp(
            timestamp_ms / 1000,
            tz=as_of.tzinfo,
        )
        if published_at > as_of:
            continue
        category = "、".join(categories) if categories else None
        usable.append(
            {
                "title": _plain_text(title),
                "published_at": published_at.isoformat(),
                "url": (
                    "https://static.cninfo.com.cn/" + relative_url.lstrip("/")
                ),
                "category": category,
            }
        )

    return {
        "page_complete": len(announcements) >= total,
        "returned_count": len(announcements),
        "usable_count": len(usable),
        "announcements": usable,
    }
