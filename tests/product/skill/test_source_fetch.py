"""Offline contract tests for the closed three-source fetch helper."""

from __future__ import annotations

import json
import os
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from tests.product.skill.deterministic_tool_loader import load_script


class FakeTransport:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, bytes | None, dict[str, str], float]] = []

    def __call__(
        self,
        method: str,
        url: str,
        body: bytes | None,
        headers: dict[str, str],
        timeout: float,
    ) -> object:
        self.calls.append((method, url, body, headers, timeout))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


@pytest.fixture()
def fetcher() -> Any:
    return load_script("source_fetch.py")


def _request(source_id: str, **request: object) -> dict[str, object]:
    purposes = {
        "cninfo-announcement-index": "公告及附件",
        "sina-financial-statements": "财务报表",
        "tencent-quote-snapshot": "交易状态与市场快照",
    }
    return {
        "schema_version": "1",
        "source_id": source_id,
        "subject": "000001.SZ",
        "as_of": "2026-06-30T18:00:00+08:00",
        "purpose": purposes[source_id],
        "request": request,
    }


NOW = datetime.fromisoformat("2026-06-30T18:00:00+08:00")
CNINFO = "cninfo-announcement-index"
SINA = "sina-financial-statements"
TENCENT = "tencent-quote-snapshot"
PURPOSES = {
    CNINFO: "公告及附件",
    SINA: "财务报表",
    TENCENT: "交易状态与市场快照",
}
def _http(
    fetcher: Any,
    payload: object,
    *,
    url: str,
    status: int = 200,
) -> object:
    return fetcher.HttpResult(
        status_code=status,
        final_url=url,
        content_type="application/json",
        body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    )


def _cninfo_identity(fetcher: Any) -> object:
    return _http(
        fetcher,
        {
            "stockList": [
                {"code": "000002", "orgId": "synthetic-wrong-org"},
                {"code": "000001", "orgId": "synthetic-right-org"},
            ]
        },
        url="https://www.cninfo.com.cn/new/data/szse_stock.json",
    )


def _announcement(index: int) -> dict[str, object]:
    return {
        "announcementTitle": f"合成公告 {index}",
        "announcementTime": 1782806400000 - index * 1000,
        "adjunctUrl": f"finalpage/synthetic-{index}.pdf",
    }


def _cninfo_page(
    fetcher: Any,
    *,
    page: int,
    has_more: bool,
    total: int = 2,
    announcements: list[dict[str, object]] | None = None,
) -> object:
    return _http(
        fetcher,
        {
            "totalAnnouncement": total,
            "totalRecordNum": total,
            "hasMore": has_more,
            "announcements": announcements
            if announcements is not None
            else [_announcement(page)],
            "syntheticPageMarker": page,
        },
        url="https://www.cninfo.com.cn/new/hisAnnouncement/query",
    )


def _sina_result(fetcher: Any, report_list: object) -> object:
    return _http(
        fetcher,
        {"result": {"data": {"report_list": report_list}}},
        url=(
            "https://quotes.sina.cn/cn/api/openapi.php/"
            "CompanyFinanceService.getFinanceReport2022"
        ),
    )


def _tencent_result(
    fetcher: Any,
    *,
    float_market_cap: str = "100.00",
    total_market_cap: str = "200.00",
) -> tuple[object, str]:
    fields = [""] * 46
    fields[0] = "51"
    fields[1] = "合成证券"
    fields[2] = "000001"
    fields[3] = "10.25"
    fields[30] = "20260630150000"
    fields[44] = float_market_cap
    fields[45] = total_market_cap
    raw_text = f'v_sz000001="{"~".join(fields)}";\n'
    return (
        fetcher.HttpResult(
            status_code=200,
            final_url="https://qt.gtimg.cn/q=sz000001",
            content_type="text/html; charset=GBK",
            body=raw_text.encode("gbk"),
        ),
        raw_text,
    )


def _with_content_type(
    fetcher: Any,
    result: Any,
    content_type: str,
    *,
    body: bytes | None = None,
) -> object:
    return fetcher.HttpResult(
        status_code=result.status_code,
        final_url=result.final_url,
        content_type=content_type,
        body=result.body if body is None else body,
    )


def _cninfo_request(**overrides: object) -> dict[str, object]:
    request = _request(
        CNINFO,
        start_date="2026-06-01",
        end_date="2026-06-30",
    )
    request.update(overrides)
    return request


def _sina_request(**overrides: object) -> dict[str, object]:
    request = _request(SINA, statement="profit", period_count=2)
    request.update(overrides)
    return request


def _tencent_request(**overrides: object) -> dict[str, object]:
    request = _request(TENCENT)
    request.update(overrides)
    return request


def test_unknown_source_is_rejected(fetcher: Any) -> None:
    payload = _request(TENCENT)
    payload["source_id"] = "unknown-source"
    transport = FakeTransport([])

    with pytest.raises(fetcher.SourceFetchError, match="unknown-source"):
        fetcher.fetch_saved_response(payload, transport=transport, now=NOW)

    assert transport.calls == []


@pytest.mark.parametrize("location", ["top", "request"])
def test_unknown_top_level_or_request_field_is_rejected(
    fetcher: Any, location: str
) -> None:
    payload = _tencent_request()
    if location == "top":
        payload["unexpected"] = True
    else:
        payload["request"] = {"unexpected": True}

    with pytest.raises(fetcher.SourceFetchError, match="unknown"):
        fetcher.fetch_saved_response(payload, transport=FakeTransport([]), now=NOW)


def test_subject_requires_canonical_a_share_code(fetcher: Any) -> None:
    for subject in ("000001", "000001.sz", "000001.HK"):
        payload = _tencent_request(subject=subject)
        with pytest.raises(fetcher.SourceFetchError, match="subject"):
            fetcher.fetch_saved_response(
                payload, transport=FakeTransport([]), now=NOW
            )

    response, _ = _tencent_result(fetcher)
    transport = FakeTransport([response])
    result = fetcher.fetch_saved_response(
        _tencent_request(), transport=transport, now=NOW
    )
    assert result["subject"] == "000001.SZ"
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    "as_of",
    ["2026-06-30T18:00:00", "not-a-datetime", "2026-07-01T00:00:00+08:00"],
)
def test_as_of_requires_timezone_and_not_future(fetcher: Any, as_of: str) -> None:
    payload = _tencent_request(as_of=as_of)
    with pytest.raises(fetcher.SourceFetchError, match="as_of"):
        fetcher.fetch_saved_response(payload, transport=FakeTransport([]), now=NOW)


@pytest.mark.parametrize("source_id", [CNINFO, SINA, TENCENT])
def test_purpose_must_match_source(fetcher: Any, source_id: str) -> None:
    requests = {
        CNINFO: {"start_date": "2026-06-01", "end_date": "2026-06-30"},
        SINA: {"statement": "profit", "period_count": 2},
        TENCENT: {},
    }
    payload = _request(source_id, **requests[source_id])
    payload["purpose"] = "不匹配用途"

    with pytest.raises(fetcher.SourceFetchError, match="purpose"):
        fetcher.fetch_saved_response(payload, transport=FakeTransport([]), now=NOW)


@pytest.mark.parametrize("contents", [None, "[]"])
def test_input_unreadable_or_non_object_exits_2_without_output(
    fetcher: Any, tmp_path: Path, contents: str | None
) -> None:
    input_path = tmp_path / "input.json"
    if contents is not None:
        input_path.write_text(contents, encoding="utf-8")
    output_path = tmp_path / "output.json"

    assert (
        fetcher.main(["--input", str(input_path), "--output", str(output_path)])
        == 2
    )
    assert not output_path.exists()


def test_request_validation_failure_exits_1_with_failure_envelope(
    fetcher: Any, tmp_path: Path
) -> None:
    payload = _tencent_request()
    payload["source_id"] = "unknown-source"
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")
    input_bytes = input_path.read_bytes()
    output_path = tmp_path / "output.json"

    assert (
        fetcher.main(["--input", str(input_path), "--output", str(output_path)])
        == 1
    )
    envelope = json.loads(output_path.read_text(encoding="utf-8"))
    assert set(envelope) == {
        "schema_version",
        "source_id",
        "status",
        "error_type",
        "error_message",
    }
    assert envelope["schema_version"] == "1"
    assert envelope["source_id"] == "unknown-source"
    assert envelope["status"] == "parse_error"
    assert envelope["error_type"] == "SourceFetchError"
    assert "unknown-source" in envelope["error_message"]
    assert input_path.read_bytes() == input_bytes


def test_input_output_same_or_existing_output_exits_2_without_overwrite(
    fetcher: Any, tmp_path: Path
) -> None:
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(_tencent_request()), encoding="utf-8")
    input_bytes = input_path.read_bytes()

    assert (
        fetcher.main(["--input", str(input_path), "--output", str(input_path)])
        == 2
    )
    assert input_path.read_bytes() == input_bytes

    output_path = tmp_path / "output.json"
    output_path.write_bytes(b"first writer\n")
    output_bytes = output_path.read_bytes()
    assert (
        fetcher.main(["--input", str(input_path), "--output", str(output_path)])
        == 2
    )
    assert input_path.read_bytes() == input_bytes
    assert output_path.read_bytes() == output_bytes


def test_concurrent_output_creation_never_overwrites(
    fetcher: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(_tencent_request()), encoding="utf-8")
    input_bytes = input_path.read_bytes()
    output_path = tmp_path / "output.json"
    first_bytes = b"concurrent first writer\n"

    def racing_fetch(
        payload: dict[str, object], *, transport: object
    ) -> dict[str, object]:
        assert transport is fetcher._https_request
        output_path.write_bytes(first_bytes)
        return {"synthetic": payload["source_id"]}

    monkeypatch.setattr(fetcher, "fetch_saved_response", racing_fetch)

    assert (
        fetcher.main(["--input", str(input_path), "--output", str(output_path)])
        == 2
    )
    assert input_path.read_bytes() == input_bytes
    assert output_path.read_bytes() == first_bytes


def test_atomic_output_temp_write_failure_leaves_no_target_or_residue(
    fetcher: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_path = tmp_path / "output.json"

    def failing_fsync(file_descriptor: int) -> None:
        raise OSError("synthetic fsync failure")

    monkeypatch.setattr(os, "fsync", failing_fsync)

    with pytest.raises(OSError, match="synthetic fsync failure"):
        fetcher._write_json_exclusive(output_path, {"synthetic": True})

    assert not output_path.exists()
    assert list(tmp_path.iterdir()) == []


def test_atomic_output_publish_conflict_keeps_winner_and_cleans_temp(
    fetcher: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_path = tmp_path / "output.json"
    winner_bytes = b"concurrent winner\n"

    def racing_link(source: object, destination: object) -> None:
        Path(destination).write_bytes(winner_bytes)
        raise FileExistsError("synthetic publish conflict")

    monkeypatch.setattr(os, "link", racing_link)

    with pytest.raises(FileExistsError, match="synthetic publish conflict"):
        fetcher._write_json_exclusive(output_path, {"synthetic": True})

    assert output_path.read_bytes() == winner_bytes
    assert list(tmp_path.iterdir()) == [output_path]


def test_cli_success_writes_stable_adapter_ready_json(
    fetcher: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response, _ = _tencent_result(fetcher)
    transport = FakeTransport([response, response])

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return NOW

    def forbidden_opener(*args: object, **kwargs: object) -> object:
        raise AssertionError("real transport path must not be used")

    monkeypatch.setattr(fetcher, "_https_request", transport)
    monkeypatch.setattr(fetcher, "datetime", FixedDateTime)
    monkeypatch.setattr(fetcher.urllib.request, "build_opener", forbidden_opener)

    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(_tencent_request()), encoding="utf-8")
    first_output = tmp_path / "first.json"
    second_output = tmp_path / "second.json"

    assert (
        fetcher.main(
            ["--input", str(input_path), "--output", str(first_output)]
        )
        == 0
    )
    assert (
        fetcher.main(
            ["--input", str(input_path), "--output", str(second_output)]
        )
        == 0
    )

    first_bytes = first_output.read_bytes()
    payload = json.loads(first_bytes)
    assert set(payload) == {
        "schema_version",
        "source_id",
        "subject",
        "purpose",
        "request",
        "source_metadata",
        "response",
        "body",
    }
    assert payload["source_id"] == TENCENT
    assert payload["subject"] == "000001.SZ"
    assert payload["response"] == {"status_code": 200}
    assert payload["body"]["security"] == "sz000001"
    assert datetime.fromisoformat(payload["source_metadata"]["captured_at"]) == NOW
    assert first_bytes == (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    assert second_output.read_bytes() == first_bytes
    assert len(transport.calls) == 2
    assert set(tmp_path.iterdir()) == {input_path, first_output, second_output}


def test_cninfo_fetch_resolves_org_id_and_closes_pagination(fetcher: Any) -> None:
    first_page = _cninfo_page(
        fetcher, page=1, has_more=True, announcements=[_announcement(1)]
    )
    second_page = _cninfo_page(
        fetcher, page=2, has_more=False, announcements=[_announcement(2)]
    )
    transport = FakeTransport([_cninfo_identity(fetcher), first_page, second_page])

    result = fetcher.fetch_saved_response(
        _cninfo_request(), transport=transport, now=NOW
    )

    assert [call[0] for call in transport.calls] == ["GET", "POST", "POST"]
    assert "szse_stock.json" in transport.calls[0][1]
    first_form = urllib.parse.parse_qs(transport.calls[1][2].decode("ascii"))
    second_form = urllib.parse.parse_qs(transport.calls[2][2].decode("ascii"))
    assert first_form["stock"] == ["000001,synthetic-right-org"]
    assert first_form["pageNum"] == ["1"]
    assert second_form["pageNum"] == ["2"]
    assert result["body"]["pages"] == [
        json.loads(first_page.body),
        json.loads(second_page.body),
    ]
    assert result["response"] == {"status_code": 200}


def test_cninfo_rejects_invalid_window_and_stops_on_page_or_byte_limit(
    fetcher: Any,
) -> None:
    for start_date, end_date in (
        ("2026-06-30", "2026-06-01"),
        ("2026-06-01", "2026-07-01"),
    ):
        payload = _request(
            CNINFO, start_date=start_date, end_date=end_date
        )
        with pytest.raises(fetcher.SourceFetchError):
            fetcher.fetch_saved_response(
                payload, transport=FakeTransport([]), now=NOW
            )

    count_gap_transport = FakeTransport(
        [
            _cninfo_identity(fetcher),
            _cninfo_page(fetcher, page=1, has_more=False, total=2),
        ]
    )
    with pytest.raises(fetcher.SourceFetchError, match="closure"):
        fetcher.fetch_saved_response(
            _cninfo_request(), transport=count_gap_transport, now=NOW
        )
    assert len(count_gap_transport.calls) == 2

    repeated_transport = FakeTransport(
        [
            _cninfo_identity(fetcher),
            _cninfo_page(
                fetcher,
                page=1,
                has_more=True,
                announcements=[_announcement(1)],
            ),
            _cninfo_page(
                fetcher,
                page=2,
                has_more=False,
                announcements=[_announcement(1)],
            ),
        ]
    )
    with pytest.raises(fetcher.SourceFetchError, match="repeated"):
        fetcher.fetch_saved_response(
            _cninfo_request(), transport=repeated_transport, now=NOW
        )
    assert len(repeated_transport.calls) == 3

    pages = [
        _cninfo_page(
            fetcher,
            page=page,
            has_more=True,
            total=fetcher.MAX_CNINFO_PAGES,
            announcements=[_announcement(page)],
        )
        for page in range(1, fetcher.MAX_CNINFO_PAGES + 1)
    ]
    page_transport = FakeTransport([_cninfo_identity(fetcher), *pages])
    with pytest.raises(fetcher.SourceFetchError, match="page"):
        fetcher.fetch_saved_response(
            _cninfo_request(), transport=page_transport, now=NOW
        )
    assert len(page_transport.calls) == fetcher.MAX_CNINFO_PAGES + 1

    oversized = fetcher.HttpResult(
        status_code=200,
        final_url="https://www.cninfo.com.cn/new/data/szse_stock.json",
        content_type="application/json",
        body=b"x" * (fetcher.MAX_RESPONSE_BYTES + 1),
    )
    byte_transport = FakeTransport([oversized])
    with pytest.raises(fetcher.SourceFetchError, match="byte"):
        fetcher.fetch_saved_response(
            _cninfo_request(), transport=byte_transport, now=NOW
        )
    assert len(byte_transport.calls) == 1


def test_cninfo_mid_pagination_failure_publishes_no_success(fetcher: Any) -> None:
    transport = FakeTransport(
        [
            _cninfo_identity(fetcher),
            _cninfo_page(fetcher, page=1, has_more=True),
            TimeoutError("synthetic timeout"),
        ]
    )
    published: dict[str, object] | None = None

    with pytest.raises(fetcher.SourceFetchError) as caught:
        published = fetcher.fetch_saved_response(
            _cninfo_request(), transport=transport, now=NOW
        )

    assert caught.value.status == "transport_error"
    assert published is None
    assert len(transport.calls) == 3


def test_cninfo_rejects_cross_page_duplicate_announcements(fetcher: Any) -> None:
    transport = FakeTransport(
        [
            _cninfo_identity(fetcher),
            _cninfo_page(
                fetcher,
                page=1,
                has_more=True,
                total=4,
                announcements=[_announcement(1), _announcement(2)],
            ),
            _cninfo_page(
                fetcher,
                page=2,
                has_more=False,
                total=4,
                announcements=[_announcement(2), _announcement(3)],
            ),
        ]
    )

    with pytest.raises(fetcher.SourceFetchError, match="repeated"):
        fetcher.fetch_saved_response(
            _cninfo_request(), transport=transport, now=NOW
        )

    assert len(transport.calls) == 3


def test_sina_fetch_keeps_report_list_without_field_guesses(fetcher: Any) -> None:
    report_list = {
        "20260331": {
            "data": [
                {
                    "item_title": "合成指标",
                    "item_value": "123.45",
                    "opaque_source_field": {"nested": [1, None, "x"]},
                }
            ]
        }
    }
    transport = FakeTransport([_sina_result(fetcher, report_list)])

    result = fetcher.fetch_saved_response(
        _sina_request(), transport=transport, now=NOW
    )

    assert result["body"] == {
        "format": "sina_report_list",
        "result": {"data": {"report_list": report_list}},
    }
    assert set(result["body"]) == {"format", "result"}


@pytest.mark.parametrize(
    ("statement", "mapped"),
    [("profit", "lrb"), ("balance", "zcfzb"), ("cash", "xjllb")],
)
def test_sina_statement_and_period_count_are_closed(
    fetcher: Any, statement: str, mapped: str
) -> None:
    transport = FakeTransport([_sina_result(fetcher, {})])
    fetcher.fetch_saved_response(
        _request(SINA, statement=statement, period_count=1),
        transport=transport,
        now=NOW,
    )
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(transport.calls[0][1]).query)
    assert query["source"] == [mapped]
    assert query["type"] == ["0"]
    assert query["num"] == ["1"]

    for bad_statement, count in (("income", 1), ("profit", 0), ("profit", 21)):
        with pytest.raises(fetcher.SourceFetchError):
            fetcher.fetch_saved_response(
                _request(SINA, statement=bad_statement, period_count=count),
                transport=FakeTransport([]),
                now=NOW,
            )


def test_tencent_fetch_maps_one_security_and_keeps_raw_labels(fetcher: Any) -> None:
    response, raw_text = _tencent_result(fetcher)
    transport = FakeTransport([response])

    result = fetcher.fetch_saved_response(
        _tencent_request(), transport=transport, now=NOW
    )

    assert transport.calls[0][1] == "https://qt.gtimg.cn/q=sz000001"
    assert transport.calls[0][1].count("000001") == 1
    assert result["body"]["raw_text"] == raw_text
    assert result["body"]["security"] == "sz000001"
    assert result["body"]["trade_date"] == "2026-06-30"
    assert result["body"]["trade_time"] == "15:00:00"
    assert result["body"]["price"] == "10.25"
    assert result["body"]["field_labels"] == {
        "price": "当前价",
        "float_market_cap": "流通市值",
        "total_market_cap": "总市值",
    }


def test_tencent_fetch_never_swaps_market_cap_labels(fetcher: Any) -> None:
    response, _ = _tencent_result(
        fetcher, float_market_cap="999.00", total_market_cap="1.00"
    )

    result = fetcher.fetch_saved_response(
        _tencent_request(), transport=FakeTransport([response]), now=NOW
    )

    assert result["body"]["float_market_cap"] == "999.00"
    assert result["body"]["total_market_cap"] == "1.00"
    assert result["body"]["field_labels"]["float_market_cap"] == "流通市值"
    assert result["body"]["field_labels"]["total_market_cap"] == "总市值"


@pytest.mark.parametrize("source_id", [CNINFO, SINA])
@pytest.mark.parametrize("content_type", ["text/html; charset=utf-8", ""])
def test_json_sources_reject_non_json_or_missing_content_type(
    fetcher: Any, source_id: str, content_type: str
) -> None:
    if source_id == CNINFO:
        identity = _with_content_type(
            fetcher,
            _cninfo_identity(fetcher),
            content_type,
            body=b"{malformed-json",
        )
        responses = [
            identity,
            _cninfo_page(fetcher, page=1, has_more=False, total=1),
        ]
        request = _cninfo_request()
    else:
        responses = [
            _with_content_type(
                fetcher,
                _sina_result(fetcher, {}),
                content_type,
                body=b"{malformed-json",
            )
        ]
        request = _sina_request()
    source_name = {
        CNINFO: "cninfo identity",
        SINA: "sina financial statements",
    }[source_id]

    with pytest.raises(fetcher.SourceFetchError) as caught:
        fetcher.fetch_saved_response(
            request, transport=FakeTransport(responses), now=NOW
        )

    assert caught.value.status == "parse_error"
    assert str(caught.value) == (
        f"{source_name} returned an unexpected content type"
    )


@pytest.mark.parametrize("content_type", ["text/plain; charset=GBK", ""])
def test_tencent_rejects_non_text_or_missing_content_type(
    fetcher: Any, content_type: str
) -> None:
    response, _ = _tencent_result(fetcher)
    response = _with_content_type(
        fetcher, response, content_type, body=b"\x81"
    )

    with pytest.raises(fetcher.SourceFetchError) as caught:
        fetcher.fetch_saved_response(
            _tencent_request(), transport=FakeTransport([response]), now=NOW
        )

    assert caught.value.status == "parse_error"
    assert str(caught.value) == "tencent quote returned an unexpected content type"


@pytest.mark.parametrize(
    ("source_id", "content_type"),
    [
        (CNINFO, "application/json; charset=UTF-8"),
        (SINA, "Application/JSON; charset=utf-8"),
        (TENCENT, "Text/HTML; charset=GBK"),
    ],
)
def test_sources_accept_approved_content_types_with_parameters(
    fetcher: Any, source_id: str, content_type: str
) -> None:
    if source_id == CNINFO:
        responses = [
            _with_content_type(
                fetcher, _cninfo_identity(fetcher), content_type
            ),
            _with_content_type(
                fetcher,
                _cninfo_page(fetcher, page=1, has_more=False, total=1),
                content_type,
            ),
        ]
        request = _cninfo_request()
    elif source_id == SINA:
        responses = [
            _with_content_type(
                fetcher, _sina_result(fetcher, {}), content_type
            )
        ]
        request = _sina_request()
    else:
        response, _ = _tencent_result(fetcher)
        responses = [_with_content_type(fetcher, response, content_type)]
        request = _tencent_request()

    result = fetcher.fetch_saved_response(
        request, transport=FakeTransport(responses), now=NOW
    )

    assert result["source_id"] == source_id


def test_every_request_uses_exact_https_allowlist(fetcher: Any) -> None:
    cninfo = FakeTransport(
        [
            _cninfo_identity(fetcher),
            _cninfo_page(fetcher, page=1, has_more=False, total=1),
        ]
    )
    sina = FakeTransport([_sina_result(fetcher, {})])
    tencent_response, _ = _tencent_result(fetcher)
    tencent = FakeTransport([tencent_response])

    fetcher.fetch_saved_response(_cninfo_request(), transport=cninfo, now=NOW)
    fetcher.fetch_saved_response(_sina_request(), transport=sina, now=NOW)
    fetcher.fetch_saved_response(_tencent_request(), transport=tencent, now=NOW)

    for _, url, _, _, _ in [*cninfo.calls, *sina.calls, *tencent.calls]:
        parsed = urllib.parse.urlsplit(url)
        assert parsed.scheme == "https"
        assert parsed.hostname in {
            "www.cninfo.com.cn",
            "quotes.sina.cn",
            "qt.gtimg.cn",
        }


def test_redirect_outside_allowlist_is_rejected(fetcher: Any) -> None:
    response = fetcher.HttpResult(
        status_code=200,
        final_url="https://outside.invalid/redirected",
        content_type="application/json",
        body=b"{}",
    )
    transport = FakeTransport([response])

    with pytest.raises(fetcher.SourceFetchError, match="allowlist"):
        fetcher.fetch_saved_response(
            _sina_request(), transport=transport, now=NOW
        )

    assert len(transport.calls) == 1


def test_https_request_follows_redirect_within_allowlist(
    fetcher: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial_url = "https://quotes.sina.cn/approved"
    redirected_url = "https://quotes.sina.cn/redirected"
    requested: list[str] = []

    class RedirectedResponse:
        headers = {"Content-Type": "application/json"}

        def __enter__(self) -> RedirectedResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def getcode(self) -> int:
            return 200

        def geturl(self) -> str:
            return redirected_url

        def read(self, limit: int) -> bytes:
            assert limit == fetcher.MAX_RESPONSE_BYTES + 1
            return b"{}"

    class RedirectingOpener:
        def __init__(self, handler: object) -> None:
            self.handler = handler

        def open(self, request: object, timeout: float) -> object:
            requested.append(request.full_url)
            redirected = self.handler.redirect_request(
                request,
                None,
                302,
                "Found",
                {"Location": redirected_url},
                redirected_url,
            )
            if redirected is None:
                raise fetcher.urllib.error.HTTPError(
                    request.full_url,
                    302,
                    "Found",
                    {"Location": redirected_url},
                    None,
                )
            requested.append(redirected.full_url)
            return RedirectedResponse()

    monkeypatch.setattr(
        fetcher.urllib.request,
        "build_opener",
        lambda handler: RedirectingOpener(handler),
    )

    result = fetcher._https_request(
        "GET", initial_url, None, {}, fetcher.TIMEOUT_SECONDS
    )

    assert requested == [initial_url, redirected_url]
    assert result.final_url == redirected_url
    assert result.status_code == 200


def test_https_request_never_follows_redirect_before_allowlist_check(
    fetcher: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[str] = []
    followed: list[str] = []
    outside_url = "https://outside.invalid/redirected"

    class RedirectResponseOpener:
        def __init__(self, handler: object) -> None:
            self.handler = handler

        def open(self, request: object, timeout: float) -> object:
            requested.append(request.full_url)
            redirected = self.handler.redirect_request(
                request,
                None,
                302,
                "Found",
                {"Location": outside_url},
                outside_url,
            )
            if redirected is not None:
                followed.append(redirected.full_url)
            raise fetcher.urllib.error.HTTPError(
                request.full_url,
                302,
                "Found",
                {"Location": outside_url},
                None,
            )

    def build_opener(handler: object) -> RedirectResponseOpener:
        return RedirectResponseOpener(handler)

    def redirect_capable_urlopen(*args: object, **kwargs: object) -> object:
        raise AssertionError("redirect-capable urlopen must not be used")

    monkeypatch.setattr(fetcher.urllib.request, "build_opener", build_opener)
    monkeypatch.setattr(fetcher.urllib.request, "urlopen", redirect_capable_urlopen)

    with pytest.raises(fetcher.SourceFetchError) as caught:
        fetcher._https_request(
            "GET",
            "https://quotes.sina.cn/approved",
            None,
            {},
            fetcher.TIMEOUT_SECONDS,
        )

    assert caught.value.status == "transport_error"
    assert requested == ["https://quotes.sina.cn/approved"]
    assert followed == []


@pytest.mark.parametrize(
    ("response", "expected_status"),
    [
        (403, "permission_denied"),
        (429, "rate_limited"),
        (500, "transport_error"),
        (TimeoutError("synthetic timeout"), "transport_error"),
    ],
)
def test_http_and_transport_failures_have_stable_status(
    fetcher: Any, response: object, expected_status: str
) -> None:
    if isinstance(response, int):
        response = _http(
            fetcher,
            {},
            url=(
                "https://quotes.sina.cn/cn/api/openapi.php/"
                "CompanyFinanceService.getFinanceReport2022"
            ),
            status=response,
        )
    transport = FakeTransport([response])

    with pytest.raises(fetcher.SourceFetchError) as caught:
        fetcher.fetch_saved_response(
            _sina_request(), transport=transport, now=NOW
        )

    assert caught.value.status == expected_status
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    ("source_id", "source_request", "host"),
    [
        (CNINFO, {"start_date": "2026-06-01", "end_date": "2026-06-30"}, "www.cninfo.com.cn"),
        (SINA, {"statement": "profit", "period_count": 1}, "quotes.sina.cn"),
        (TENCENT, {}, "qt.gtimg.cn"),
    ],
)
def test_source_fetch_does_not_retry_or_call_fallback(
    fetcher: Any,
    source_id: str,
    source_request: dict[str, object],
    host: str,
) -> None:
    transport = FakeTransport([TimeoutError("synthetic timeout")])

    with pytest.raises(fetcher.SourceFetchError) as caught:
        fetcher.fetch_saved_response(
            _request(source_id, **source_request), transport=transport, now=NOW
        )

    assert caught.value.status == "transport_error"
    assert len(transport.calls) == 1
    assert urllib.parse.urlsplit(transport.calls[0][1]).hostname == host


def test_each_success_payload_is_accepted_by_source_adapter(fetcher: Any) -> None:
    adapter = load_script("source_adapter.py")
    cninfo_transport = FakeTransport(
        [
            _cninfo_identity(fetcher),
            _cninfo_page(fetcher, page=1, has_more=False, total=1),
        ]
    )
    sina_transport = FakeTransport(
        [
            _sina_result(
                fetcher,
                {
                    "20260331": {
                        "data": [
                            {"item_title": "合成指标", "item_value": "1.00"}
                        ]
                    }
                },
            )
        ]
    )
    tencent_response, _ = _tencent_result(fetcher)
    cases = [
        (
            CNINFO,
            fetcher.fetch_saved_response(
                _cninfo_request(), transport=cninfo_transport, now=NOW
            ),
            None,
        ),
        (
            SINA,
            fetcher.fetch_saved_response(
                _sina_request(), transport=sina_transport, now=NOW
            ),
            None,
        ),
        (
            TENCENT,
            fetcher.fetch_saved_response(
                _tencent_request(),
                transport=FakeTransport([tencent_response]),
                now=NOW,
            ),
            "元",
        ),
    ]

    for source_id, saved_payload, unit in cases:
        envelope = adapter.adapt_saved_response(
            PURPOSES[source_id],
            source_id,
            saved_payload,
            subject="000001.SZ",
            as_of=NOW,
            period=None,
            scope=None,
            unit=unit,
            purpose=PURPOSES[source_id],
        )
        assert envelope["source_id"] == source_id
        assert envelope["raw_input_sha256"]
        assert envelope["status"] == "success"
