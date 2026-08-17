from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from announcement_index import normalize_announcement_page


def test_normalize_announcement_page_filters_after_as_of_and_uses_https() -> None:
    timezone = ZoneInfo("Asia/Shanghai")
    payload = {
        "totalAnnouncement": 2,
        "announcements": [
            {
                "announcementTitle": "<em>2025年</em>年度报告",
                "announcementTime": 1776441600000,
                "adjunctUrl": "finalpage/2026-04-18/annual.PDF",
                "announcementTypeName": ["年度报告"],
            },
            {
                "announcementTitle": "时点后公告",
                "announcementTime": 1786982400000,
                "adjunctUrl": "finalpage/2026-08-18/future.PDF",
                "announcementTypeName": None,
            },
        ],
    }

    result = normalize_announcement_page(
        payload,
        as_of=datetime(2026, 8, 16, 17, 52, 28, tzinfo=timezone),
    )

    assert result["page_complete"] is True
    assert result["returned_count"] == 2
    assert result["usable_count"] == 1
    assert result["announcements"] == [
        {
            "title": "2025年年度报告",
            "published_at": "2026-04-18T00:00:00+08:00",
            "url": "https://static.cninfo.com.cn/finalpage/2026-04-18/annual.PDF",
            "category": "年度报告",
        }
    ]


def test_normalize_announcement_page_requires_timezone_aware_as_of() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        normalize_announcement_page(
            {"totalAnnouncement": 0, "announcements": []},
            as_of=datetime(2026, 8, 16, 17, 52, 28),
        )
