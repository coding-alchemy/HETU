"""Deterministic tool tests for the canonical multi-page announcement index."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from tests.product.skill.deterministic_tool_loader import load_script

announcements = load_script("announcement_index.py")

SCRIPT_PATH = (
    Path(__file__).resolve().parents[3]
    / "skills/hetu-stock-analysis/scripts/announcement_index.py"
)
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _announcement(
    title: str,
    timestamp_ms: int,
    url: str,
    categories: list[str] | None,
) -> dict[str, Any]:
    return {
        "announcementTitle": title,
        "announcementTime": timestamp_ms,
        "adjunctUrl": url,
        "announcementTypeName": categories,
    }


def _write_json(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_pages_filter_after_as_of_strip_html_and_use_https() -> None:
    page = {
        "pageNum": 1,
        "totalAnnouncement": 2,
        "announcements": [
            _announcement(
                "<em>2025年</em>年度报告",
                1776441600000,
                "finalpage/2026-04-18/annual.PDF",
                ["年度报告"],
            ),
            _announcement(
                "时点后公告",
                1786982400000,
                "finalpage/2026-08-18/future.PDF",
                None,
            ),
        ],
    }

    result = announcements.normalize_announcement_pages(
        [page], as_of=datetime(2026, 8, 16, 17, 52, 28, tzinfo=SHANGHAI)
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


def test_announcement_pages_expose_incomplete_pagination() -> None:
    result = announcements.normalize_announcement_pages(
        [
            {
                "pageNum": 1,
                "totalAnnouncement": 2,
                "announcements": [
                    _announcement(
                        "公告一",
                        1786982400000,
                        "finalpage/a.PDF",
                        None,
                    )
                ],
            }
        ],
        as_of=datetime(2026, 8, 18, tzinfo=SHANGHAI),
    )
    assert result["returned_count"] == 1
    assert result["page_complete"] is False


def test_announcement_pages_flag_incomplete_when_more_returned_than_total() -> None:
    # totalAnnouncement=0 with one returned announcement contradicts the
    # pagination totals in the other direction and must not read as complete.
    result = announcements.normalize_announcement_pages(
        [
            {
                "pageNum": 1,
                "totalAnnouncement": 0,
                "announcements": [
                    _announcement(
                        "多出公告", 1786982400000, "finalpage/extra.PDF", None
                    )
                ],
            }
        ],
        as_of=datetime(2026, 8, 18, tzinfo=SHANGHAI),
    )
    assert result["total_count"] == 0
    assert result["returned_count"] == 1
    assert result["page_complete"] is False


@pytest.mark.parametrize(
    "url",
    (
        "http://example.test/a.PDF",
        "HTTP://static.cninfo.com.cn/a.PDF",
        "/absolute.PDF",
    ),
    ids=(
        "test_announcement_pages_reject_absolute_attachment_url",
        "test_announcement_pages_reject_uppercase_scheme_absolute_url",
        "test_announcement_pages_reject_leading_slash_attachment_url",
    ),
)
def test_announcement_pages_reject_absolute_attachment_urls(url: str) -> None:
    page = {
        "pageNum": 1,
        "totalAnnouncement": 1,
        "announcements": [
            _announcement("公告一", 1786982400000, url, None)
        ],
    }
    with pytest.raises(ValueError, match="relative path"):
        announcements.normalize_announcement_pages(
            [page],
            as_of=datetime(2026, 8, 18, tzinfo=SHANGHAI),
        )


@pytest.mark.parametrize(
    "url",
    (
        "ftp://files.example.test/a.PDF",
        "javascript:alert(1)",
        "data:text/html,x",
        "../escape/a.PDF",
        "finalpage/2026-08-18/../../secret.PDF",
        "back\\slash.PDF",
        "finalpage/%2e%2e/secret.PDF",
        "..%2Fescape.PDF",
        "%2e%2e/x.PDF",
        "%252e%252e/double-encoded.PDF",
    ),
)
def test_announcement_pages_reject_non_relative_attachment_urls(url: str) -> None:
    page = {
        "pageNum": 1,
        "totalAnnouncement": 1,
        "announcements": [_announcement("公告一", 1786982400000, url, None)],
    }
    with pytest.raises(ValueError):
        announcements.normalize_announcement_pages(
            [page],
            as_of=datetime(2026, 8, 18, tzinfo=SHANGHAI),
        )


def test_invalid_url_failure_envelope_is_stable_across_hash_seeds(
    tmp_path: Path,
) -> None:
    input_path = _write_json(
        tmp_path / "input.json",
        {
            "as_of": "2026-08-18T00:00:00+08:00",
            "pages": [
                {
                    "pageNum": 1,
                    "totalAnnouncement": 1,
                    "announcements": [
                        _announcement(
                            "公告一",
                            1786982400000,
                            "%2F%2Fevil\\x",
                            None,
                        )
                    ],
                }
            ],
        },
    )
    envelopes: list[bytes] = []
    for seed in ("1", "3"):
        output_path = tmp_path / f"output-{seed}.json"
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--input",
                str(input_path),
                "--output",
                str(output_path),
            ],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "PYTHONHASHSEED": seed},
        )
        assert result.returncode == 1
        envelopes.append(output_path.read_bytes())

    assert envelopes[0] == envelopes[1]


def test_page_number_gap_marks_pagination_incomplete() -> None:
    shared = _announcement("跨页公告", 1786982400000, "finalpage/shared.PDF", None)
    pages = [
        {"pageNum": 1, "totalAnnouncement": 2, "announcements": [shared]},
        {"pageNum": 3, "totalAnnouncement": 2, "announcements": [shared]},
    ]
    result = announcements.normalize_announcement_pages(
        pages,
        as_of=datetime(2026, 8, 18, tzinfo=SHANGHAI),
    )
    assert result["page_complete"] is False


def test_missing_first_page_marks_pagination_incomplete() -> None:
    page = {
        "pageNum": 2,
        "totalAnnouncement": 1,
        "announcements": [
            _announcement("第二页公告", 1786982400000, "finalpage/second.PDF", None)
        ],
    }
    result = announcements.normalize_announcement_pages(
        [page],
        as_of=datetime(2026, 8, 18, tzinfo=SHANGHAI),
    )
    assert result["page_complete"] is False


def test_announcement_pages_keep_exact_as_of_boundary() -> None:
    boundary_ms = 1786982400000
    result = announcements.normalize_announcement_pages(
        [
            {
                "pageNum": 1,
                "totalAnnouncement": 1,
                "announcements": [
                    _announcement(
                        "边界公告", boundary_ms, "finalpage/boundary.PDF", []
                    )
                ],
            }
        ],
        as_of=datetime.fromtimestamp(boundary_ms / 1000, tz=SHANGHAI),
    )
    assert result["usable_count"] == 1


def test_announcement_pages_reject_naive_as_of() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        announcements.normalize_announcement_pages(
            [{"pageNum": 1, "totalAnnouncement": 0, "announcements": []}],
            as_of=datetime(2026, 8, 18, 0, 0, 0),
        )


def test_announcement_pages_reject_date_as_of() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        announcements.normalize_announcement_pages(
            [{"pageNum": 1, "totalAnnouncement": 0, "announcements": []}],
            as_of=date(2026, 8, 18),  # type: ignore[arg-type]
        )


def test_announcement_pages_merge_dedupe_and_sort_descending() -> None:
    result = announcements.normalize_announcement_pages(
        [
            {
                "pageNum": 1,
                "totalAnnouncement": 3,
                "announcements": [
                    _announcement(
                        "公告甲", 1776441600000, "finalpage/first.PDF", ["年报"]
                    ),
                    _announcement(
                        "公告乙", 1786982400000, "finalpage/second.PDF", None
                    ),
                ],
            },
            {
                "pageNum": 2,
                "totalAnnouncement": 3,
                "announcements": [
                    _announcement(
                        "公告乙重复", 1786982400000, "finalpage/second.PDF", None
                    ),
                    _announcement(
                        "公告丙", 1764931200000, "finalpage/third.PDF", None
                    ),
                ],
            },
        ],
        as_of=datetime(2026, 8, 18, tzinfo=SHANGHAI),
    )

    assert result["total_count"] == 3
    assert result["returned_count"] == 4
    assert result["usable_count"] == 3
    # The overlapping pages return 4 rows against a reported total of 3; a
    # returned count that disagrees with the total in either direction keeps
    # the pagination formally incomplete.
    assert result["page_complete"] is False
    assert [item["title"] for item in result["announcements"]] == [
        "公告乙",
        "公告甲",
        "公告丙",
    ]
    assert [item["url"] for item in result["announcements"]] == [
        "https://static.cninfo.com.cn/finalpage/second.PDF",
        "https://static.cninfo.com.cn/finalpage/first.PDF",
        "https://static.cninfo.com.cn/finalpage/third.PDF",
    ]


def test_announcement_pages_flag_incomplete_when_page_number_missing() -> None:
    result = announcements.normalize_announcement_pages(
        [
            {
                "totalAnnouncement": 1,
                "announcements": [
                    _announcement("公告一", 1786982400000, "finalpage/a.PDF", None)
                ],
            }
        ],
        as_of=datetime(2026, 8, 18, tzinfo=SHANGHAI),
    )
    assert result["usable_count"] == 1
    assert result["page_complete"] is False


def test_announcement_pages_flag_incomplete_on_duplicate_page_number() -> None:
    result = announcements.normalize_announcement_pages(
        [
            {
                "pageNum": 1,
                "totalAnnouncement": 2,
                "announcements": [
                    _announcement("公告一", 1786982400000, "finalpage/a.PDF", None)
                ],
            },
            {
                "pageNum": 1,
                "totalAnnouncement": 2,
                "announcements": [
                    _announcement("公告二", 1786982400000, "finalpage/b.PDF", None)
                ],
            },
        ],
        as_of=datetime(2026, 8, 18, tzinfo=SHANGHAI),
    )
    assert result["returned_count"] == 2
    assert result["page_complete"] is False


def test_announcement_pages_flag_incomplete_when_totals_disagree() -> None:
    result = announcements.normalize_announcement_pages(
        [
            {
                "pageNum": 1,
                "totalAnnouncement": 2,
                "announcements": [
                    _announcement("公告一", 1786982400000, "finalpage/a.PDF", None)
                ],
            },
            {
                "pageNum": 2,
                "totalAnnouncement": 3,
                "announcements": [
                    _announcement("公告二", 1786982400000, "finalpage/b.PDF", None)
                ],
            },
        ],
        as_of=datetime(2026, 8, 18, tzinfo=SHANGHAI),
    )
    assert result["total_count"] == 3
    assert result["page_complete"] is False


def test_announcement_pages_with_no_pages_are_never_complete() -> None:
    result = announcements.normalize_announcement_pages(
        [], as_of=datetime(2026, 8, 18, tzinfo=SHANGHAI)
    )
    assert result["total_count"] == 0
    assert result["returned_count"] == 0
    assert result["usable_count"] == 0
    assert result["page_complete"] is False


def test_announcement_pages_reject_invalid_total_count() -> None:
    with pytest.raises(ValueError, match="totalAnnouncement"):
        announcements.normalize_announcement_pages(
            [{"pageNum": 1, "totalAnnouncement": -1, "announcements": []}],
            as_of=datetime(2026, 8, 18, tzinfo=SHANGHAI),
        )


def test_cli_success_writes_success_envelope(tmp_path: Path) -> None:
    input_path = _write_json(
        tmp_path / "input.json",
        {
            "pages": [
                {
                    "pageNum": 1,
                    "totalAnnouncement": 1,
                    "announcements": [
                        _announcement(
                            "公告一", 1786982400000, "finalpage/a.PDF", None
                        )
                    ],
                }
            ],
            "as_of": "2026-08-18T00:00:00+08:00",
        },
    )
    output_path = tmp_path / "out" / "envelope.json"

    completed = _run_cli("--input", str(input_path), "--output", str(output_path))

    assert completed.returncode == 0, completed.stderr
    envelope = json.loads(output_path.read_text(encoding="utf-8"))
    assert envelope["schema_version"] == "1.0"
    assert envelope["tool"] == "announcement_index"
    assert envelope["status"] == "success"
    assert (
        envelope["input_sha256"]
        == hashlib.sha256(input_path.read_bytes()).hexdigest()
    )
    result = envelope["result"]
    assert result["total_count"] == 1
    assert result["usable_count"] == 1
    assert result["announcements"][0]["url"] == (
        "https://static.cninfo.com.cn/finalpage/a.PDF"
    )


def test_cli_transform_failure_exits_1_with_failure_envelope(tmp_path: Path) -> None:
    input_path = _write_json(
        tmp_path / "input.json",
        {
            "pages": [
                {
                    "pageNum": 1,
                    "totalAnnouncement": 1,
                    "announcements": [
                        _announcement(
                            "公告一", 1786982400000, "http://example.test/a.PDF", None
                        )
                    ],
                }
            ],
            "as_of": "2026-08-18T00:00:00+08:00",
        },
    )
    output_path = tmp_path / "envelope.json"

    completed = _run_cli("--input", str(input_path), "--output", str(output_path))

    assert completed.returncode == 1
    envelope = json.loads(output_path.read_text(encoding="utf-8"))
    assert envelope["status"] == "failed"
    assert "relative path" in envelope["error_message"]
    assert "result" not in envelope


def test_cli_naive_as_of_exits_1_with_failure_envelope(tmp_path: Path) -> None:
    input_path = _write_json(
        tmp_path / "input.json",
        {
            "pages": [
                {"pageNum": 1, "totalAnnouncement": 0, "announcements": []}
            ],
            "as_of": "2026-08-18T00:00:00",
        },
    )
    output_path = tmp_path / "envelope.json"

    completed = _run_cli("--input", str(input_path), "--output", str(output_path))

    assert completed.returncode == 1
    envelope = json.loads(output_path.read_text(encoding="utf-8"))
    assert envelope["status"] == "failed"
    assert "timezone-aware" in envelope["error_message"]
