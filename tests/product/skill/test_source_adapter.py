"""Stage-04 minimal source adapter behavior tests (RED first).

The adapter is a mechanical helper over explicitly saved raw responses: it
parses the four structured domains, classifies fetch outcomes into the
nine-value closed set, short-circuits disabled sources before any parsing,
computes mechanical equivalence boundaries (keeping both original values) and
emits a fixed-key metadata envelope. These tests also pin the ``--input``/
``--output`` CLI taxonomy (0 success, 1 malformed/rejected payload with a
failure envelope, 2 argument/unreadable/conflict errors) and assert the module
source imports no network libraries. Every payload is synthetic; no real G1/G2
securities, dates or answer values appear here.
"""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from tests.product.skill.deterministic_tool_loader import load_script

SCRIPTS_DIR = (
    Path(__file__).resolve().parents[3] / "skills/hetu-stock-analysis/scripts"
)
SCRIPT_PATH = SCRIPTS_DIR / "source_adapter.py"
FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures/source_contracts"
FIELD_DICTIONARY = Path(
    "tests/product/fixtures/forensic/eastmoney-field-dictionary.json"
)

DOMAIN_ANNOUNCEMENTS = "公告及附件"
DOMAIN_FINANCIAL = "财务报表"
DOMAIN_PEER_VALUATION = "同业市场估值"
DOMAIN_MARKET_SNAPSHOT = "交易状态与市场快照"
DOMAIN_INDUSTRY = "行业需求与竞争"

ENVELOPE_KEYS = {
    "schema_version",
    "adapter",
    "domain",
    "source_id",
    "called",
    "disabled",
    "status",
    "source_metadata",
    "normalized",
    "equivalence",
    "raw_input_sha256",
}

NETWORK_IMPORT_ROOTS = frozenset(
    {
        "urllib",
        "http",
        "requests",
        "socket",
        "ssl",
        "aiohttp",
        "httpx",
        "ftplib",
        "smtplib",
        "telnetlib",
        "xmlrpc",
        "imaplib",
        "poplib",
    }
)

_MARKET_FIXTURE = json.loads(
    (FIXTURES_DIR / "market-snapshot.json").read_text(encoding="utf-8")
)
SUBJECT = _MARKET_FIXTURE["subject"]
PRICE = _MARKET_FIXTURE["price"]
TOTAL_SHARES = _MARKET_FIXTURE["total_shares"]
FLOAT_SHARES = _MARKET_FIXTURE["float_shares"]
SOURCE_ID = "synthetic-source-a"
AS_OF = "2026-06-30T18:00:00+08:00"
TOTAL_MARKET_CAP = format(Decimal(PRICE) * Decimal(TOTAL_SHARES), "f")
FLOAT_MARKET_CAP = format(Decimal(PRICE) * Decimal(FLOAT_SHARES), "f")


@pytest.fixture()
def adapter() -> Any:
    return load_script("source_adapter.py")


def test_eastmoney_dictionary_keeps_corrected_meanings_and_old_contradictions(
    adapter: Any,
) -> None:
    dictionary = json.loads(FIELD_DICTIONARY.read_text(encoding="utf-8"))
    for code in ("183", "186", "188"):
        entry = dictionary[f"f{code}"]
        core_name = entry["meaning_zh"].split("（", 1)[0]
        assert adapter.EASTMONEY_FIELD_DICTIONARY[f"f{code}"] == core_name
        if code == "183":
            assert "元" in entry["meaning_zh"]
            assert "最新报告期累计值" in entry["meaning_zh"]
        else:
            assert "%" in entry["meaning_zh"]
        assert entry["contradicted_old_label"]
        assert entry["cross_reference_field"]
        assert entry["contradiction"]


def _fixture(name: str) -> Any:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _metadata() -> dict[str, str]:
    return {
        "provider": "synthetic-provider",
        "entry": "https://synthetic.example/snapshot",
        "captured_at": "2026-06-30T15:05:00+08:00",
        "field_basis": "合成字段依据（非真实来源）",
        "license": "public",
        "timezone": "Asia/Shanghai",
    }


def _saved_payload(body: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "source_metadata": _metadata(),
        "response": {"status_code": 200},
        "subject": SUBJECT,
        "period": None,
        "scope": None,
        "unit": None,
        "purpose": None,
        "body": body,
    }
    payload.update(overrides)
    return payload


def _announcements_body() -> dict[str, Any]:
    return {"pages": _fixture("cninfo-pages.json")["pages"]}


def _financial_body() -> dict[str, Any]:
    body: dict[str, Any] = dict(_fixture("financial-report.json"))
    body["format"] = "sina_report_list"
    return body


def _eastmoney_body() -> dict[str, Any]:
    return {
        "format": "eastmoney_push2_indicators",
        "period": "2026-03-31",
        "data": {"f183": "123456.78", "f186": "45.67", "f188": "12.34"},
    }


def _snapshot_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "trade_date": "2026-06-30",
        "price": PRICE,
        "price_unit": "元",
        "total_shares": TOTAL_SHARES,
        "total_shares_unit": "股",
        "float_shares": FLOAT_SHARES,
        "float_shares_unit": "股",
        "market_cap_unit": "元",
        "total_market_cap": TOTAL_MARKET_CAP,
        "float_market_cap": FLOAT_MARKET_CAP,
    }
    body.update(overrides)
    return body


def _financial_payload(**overrides: Any) -> dict[str, Any]:
    return _saved_payload(
        _financial_body(),
        period="2026-03-31",
        scope="合并",
        unit="CNY",
        purpose="合成用途：交叉核验",
        **overrides,
    )


def _financial_request(**overrides: Any) -> dict[str, Any]:
    request: dict[str, Any] = _request(
        period="2026-03-31", scope="合并", unit="CNY"
    )
    request.update(overrides)
    return request


def _request(**overrides: Any) -> dict[str, Any]:
    request: dict[str, Any] = {
        "subject": SUBJECT,
        "as_of": datetime.fromisoformat(AS_OF),
        "period": None,
        "scope": None,
        "unit": None,
        "purpose": "合成用途：交叉核验",
    }
    request.update(overrides)
    return request


def _adapt(
    module: Any,
    domain: str,
    payload: Any,
    request: dict[str, Any] | None = None,
    disabled: tuple[str, ...] = (),
) -> dict[str, Any]:
    return module.adapt_saved_response(
        domain,
        SOURCE_ID,
        payload,
        disabled_sources=disabled,
        **(request if request is not None else _request()),
    )


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _checks_by_name(envelope: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        check["check"]: check for check in envelope["equivalence"]["checks"]
    }


def _write_json(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _cli_input(
    domain: str,
    saved_payload: Any,
    request: dict[str, Any] | None = None,
    disabled: tuple[str, ...] = (),
) -> dict[str, Any]:
    resolved = request if request is not None else _request()
    return {
        "domain": domain,
        "source_id": SOURCE_ID,
        "saved_payload": saved_payload,
        "subject": resolved["subject"],
        "as_of": AS_OF,
        "period": resolved["period"],
        "scope": resolved["scope"],
        "unit": resolved["unit"],
        "purpose": resolved["purpose"],
        "disabled_sources": list(disabled),
    }


def _run_cli(
    input_path: Path, output_path: Path
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
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
    )


def test_adapter_exposes_adapt_saved_response_and_main(adapter: Any) -> None:
    assert callable(adapter.adapt_saved_response)
    assert callable(adapter.main)


def test_announcements_domain_parses_to_success(adapter: Any) -> None:
    payload = _saved_payload(_announcements_body())

    envelope = _adapt(adapter, DOMAIN_ANNOUNCEMENTS, payload)

    assert set(envelope) == ENVELOPE_KEYS
    assert envelope["schema_version"] == "1.0"
    assert envelope["adapter"] == "source_adapter"
    assert envelope["domain"] == DOMAIN_ANNOUNCEMENTS
    assert envelope["source_id"] == SOURCE_ID
    assert envelope["called"] is True
    assert envelope["disabled"] is False
    assert envelope["status"] == "success"
    assert envelope["source_metadata"] == payload["source_metadata"]
    normalized = envelope["normalized"]
    assert normalized["total_count"] == 3
    assert normalized["returned_count"] == 3
    assert normalized["usable_count"] == 2
    assert normalized["page_complete"] is True
    assert len(normalized["announcements"]) == 2
    assert all(
        announcement["url"].startswith("https://static.cninfo.com.cn/")
        for announcement in normalized["announcements"]
    )
    assert envelope["equivalence"]["axes"]["subject"]["match"] is True


def test_financial_domain_parses_to_success(adapter: Any) -> None:
    payload = _financial_payload()

    envelope = _adapt(adapter, DOMAIN_FINANCIAL, payload, _financial_request())

    assert envelope["status"] == "success"
    assert envelope["called"] is True
    assert envelope["source_metadata"] == payload["source_metadata"]
    normalized = envelope["normalized"]
    assert normalized["period_count"] == 2
    assert len(normalized["rows"]) == 2
    assert normalized["rows"][0]["报告期"] == "2026-03-31"
    assert normalized["rows"][0]["营业收入"] == "1234567.89"
    assert normalized["rows"][1]["报告期"] == "2025-12-31"
    axes = envelope["equivalence"]["axes"]
    assert axes["period"]["match"] is True
    assert axes["scope"]["match"] is True
    assert axes["unit"]["match"] is True


def test_peer_valuation_domain_parses_to_success(adapter: Any) -> None:
    body = _snapshot_body()
    payload = _saved_payload(body, unit="元")

    envelope = _adapt(adapter, DOMAIN_PEER_VALUATION, payload, _request(unit="元"))

    assert envelope["status"] == "success"
    assert envelope["source_metadata"] == payload["source_metadata"]
    assert envelope["normalized"] == body
    checks = _checks_by_name(envelope)
    assert checks["total_market_cap_vs_price_times_shares"]["consistent"] is True
    assert checks["float_market_cap_vs_price_times_shares"]["consistent"] is True


def test_market_snapshot_domain_parses_to_success(adapter: Any) -> None:
    body = _snapshot_body(trading_status="交易中", change_percent="1.23")
    payload = _saved_payload(body, unit="元")

    envelope = _adapt(
        adapter, DOMAIN_MARKET_SNAPSHOT, payload, _request(unit="元")
    )

    assert envelope["status"] == "success"
    assert envelope["source_metadata"] == payload["source_metadata"]
    assert envelope["normalized"]["trading_status"] == "交易中"
    assert envelope["normalized"]["change_percent"] == "1.23"
    assert envelope["equivalence"]["axes"]["subject"]["match"] is True


def test_eastmoney_body_uses_the_fixed_field_dictionary(adapter: Any) -> None:
    payload = _saved_payload(
        _eastmoney_body(), period="2026-03-31", scope="合并", unit="CNY"
    )

    envelope = _adapt(adapter, DOMAIN_FINANCIAL, payload, _financial_request())

    assert envelope["status"] == "success"
    assert envelope["normalized"]["period"] == "2026-03-31"
    assert envelope["normalized"]["indicators"] == [
        {"code": "f183", "name": "营业总收入", "value": "123456.78"},
        {"code": "f186", "name": "销售毛利率", "value": "45.67"},
        {"code": "f188", "name": "资产负债率", "value": "12.34"},
    ]


def test_eastmoney_unknown_field_code_maps_to_parse_error(adapter: Any) -> None:
    body = {
        "format": "eastmoney_push2_indicators",
        "period": "2026-03-31",
        "data": {"f999": "1.0"},
    }

    envelope = _adapt(
        adapter, DOMAIN_FINANCIAL, _saved_payload(body, period="2026-03-31")
    )

    assert envelope["status"] == "parse_error"
    assert envelope["called"] is True
    assert envelope["normalized"] is None
    assert envelope["equivalence"] is None


def test_http_404_maps_to_not_found(adapter: Any) -> None:
    payload = _saved_payload(
        _announcements_body(), response={"status_code": 404}
    )

    envelope = _adapt(adapter, DOMAIN_ANNOUNCEMENTS, payload)

    assert envelope["status"] == "not_found"
    assert envelope["called"] is True
    assert envelope["normalized"] is None


def test_closed_empty_index_maps_to_not_found(adapter: Any) -> None:
    pages = [{"pageNum": 1, "totalAnnouncement": 0, "announcements": []}]

    envelope = _adapt(
        adapter, DOMAIN_ANNOUNCEMENTS, _saved_payload({"pages": pages})
    )

    assert envelope["status"] == "not_found"
    assert envelope["normalized"]["total_count"] == 0
    assert envelope["normalized"]["page_complete"] is True


def test_http_429_maps_to_rate_limited(adapter: Any) -> None:
    payload = _saved_payload(
        _announcements_body(), response={"status_code": 429}
    )

    envelope = _adapt(adapter, DOMAIN_ANNOUNCEMENTS, payload)

    assert envelope["status"] == "rate_limited"
    assert envelope["called"] is True
    assert envelope["normalized"] is None
    assert envelope["raw_input_sha256"]


@pytest.mark.parametrize("status_code", [502, 0])
def test_transport_failures_map_to_transport_error(
    adapter: Any, status_code: int
) -> None:
    payload = _saved_payload(
        _snapshot_body(), response={"status_code": status_code}
    )

    envelope = _adapt(adapter, DOMAIN_PEER_VALUATION, payload, _request(unit="元"))

    assert envelope["status"] == "transport_error"
    assert envelope["called"] is True
    assert envelope["normalized"] is None


@pytest.mark.parametrize("status_code", [401, 403])
def test_access_denied_maps_to_permission_denied(
    adapter: Any, status_code: int
) -> None:
    payload = _saved_payload(
        _snapshot_body(), response={"status_code": status_code}
    )

    envelope = _adapt(
        adapter, DOMAIN_MARKET_SNAPSHOT, payload, _request(unit="元")
    )

    assert envelope["status"] == "permission_denied"
    assert envelope["called"] is True
    assert envelope["normalized"] is None


def test_structural_change_maps_to_parse_error(adapter: Any) -> None:
    body = {
        "format": "sina_report_list",
        "result": {"data": {"report_list": [{"item_title": "营业收入"}]}},
    }

    envelope = _adapt(adapter, DOMAIN_FINANCIAL, _saved_payload(body))

    assert envelope["status"] == "parse_error"
    assert envelope["called"] is True
    assert envelope["normalized"] is None
    assert envelope["equivalence"] is None
    assert envelope["raw_input_sha256"]


def test_missing_page_maps_to_incomplete_pagination(adapter: Any) -> None:
    pages = _fixture("cninfo-pages.json")["pages"][:1]

    envelope = _adapt(
        adapter, DOMAIN_ANNOUNCEMENTS, _saved_payload({"pages": pages})
    )

    assert envelope["status"] == "incomplete_pagination"
    assert envelope["normalized"]["page_complete"] is False
    assert envelope["normalized"]["returned_count"] == 2
    assert envelope["normalized"]["total_count"] == 3


def _synthetic_announcement(index: int) -> dict[str, Any]:
    return {
        "announcementTitle": f"合成样例：虚构公告 {index}（非真实披露）",
        "announcementTime": 1781503500000 - index * 86400000,
        "adjunctUrl": f"finalpage/2026-06/synthetic-realshape-{index}-20260615.pdf",
    }


def _real_shaped_cninfo_body(
    *, total: int, tail_has_more: bool, page_sizes: tuple[int, ...]
) -> dict[str, Any]:
    """Real hisAnnouncement shape: no body pageNum, hasMore/totalAnnouncement
    present, and a deliberately contradictory totalpages (saved evidence:
    totalpages=2 while totalAnnouncement=82) that must never close pages."""
    pages: list[dict[str, Any]] = []
    for position, size in enumerate(page_sizes):
        start = sum(page_sizes[:position])
        pages.append(
            {
                "totalAnnouncement": total,
                "totalRecordNum": total,
                "totalSecurities": 0,
                "hasMore": (
                    tail_has_more if position == len(page_sizes) - 1 else True
                ),
                "totalpages": 1,
                "announcements": [
                    _synthetic_announcement(start + offset + 1)
                    for offset in range(size)
                ],
            }
        )
    return {"pages": pages}


def test_real_shaped_pages_without_page_num_close_on_total_and_hasmore(
    adapter: Any,
) -> None:
    body = _real_shaped_cninfo_body(
        total=4, tail_has_more=False, page_sizes=(2, 2)
    )

    envelope = _adapt(adapter, DOMAIN_ANNOUNCEMENTS, _saved_payload(body))

    # The stage-03 tool alone cannot close these pages (no body pageNum);
    # the adapter closes them on totalAnnouncement + hasMore + saved order.
    tool = load_script("announcement_index.py")
    direct = tool.normalize_announcement_pages(
        body["pages"], as_of=datetime.fromisoformat(AS_OF)
    )
    assert direct["page_complete"] is False

    assert envelope["status"] == "success"
    assert envelope["normalized"]["page_complete"] is True
    assert envelope["normalized"]["total_count"] == 4
    assert envelope["normalized"]["returned_count"] == 4
    assert envelope["normalized"]["usable_count"] == 4


def test_open_tail_has_more_maps_to_incomplete_pagination(adapter: Any) -> None:
    # Counts already close (2 of 2) but the final page signals hasMore=true:
    # the tail is open, so pagination must not be judged complete.
    body = _real_shaped_cninfo_body(
        total=2, tail_has_more=True, page_sizes=(2,)
    )

    envelope = _adapt(adapter, DOMAIN_ANNOUNCEMENTS, _saved_payload(body))

    assert envelope["status"] == "incomplete_pagination"
    assert envelope["normalized"]["page_complete"] is False
    assert envelope["normalized"]["usable_count"] == 2


def test_count_gap_despite_closed_tail_maps_to_incomplete_pagination(
    adapter: Any,
) -> None:
    body = _real_shaped_cninfo_body(
        total=4, tail_has_more=False, page_sizes=(2,)
    )

    envelope = _adapt(adapter, DOMAIN_ANNOUNCEMENTS, _saved_payload(body))

    assert envelope["status"] == "incomplete_pagination"
    assert envelope["normalized"]["page_complete"] is False


def test_mixed_page_number_evidence_maps_to_incomplete_pagination(
    adapter: Any,
) -> None:
    body = _real_shaped_cninfo_body(
        total=4, tail_has_more=False, page_sizes=(2, 2)
    )
    body["pages"][0]["pageNum"] = 1

    envelope = _adapt(adapter, DOMAIN_ANNOUNCEMENTS, _saved_payload(body))

    assert envelope["status"] == "incomplete_pagination"
    assert envelope["normalized"]["page_complete"] is False


def test_empty_pages_list_maps_to_incomplete_pagination(adapter: Any) -> None:
    # Zero saved pages close nothing: the honest classification is an open
    # pagination surface, never "no announcements".
    envelope = _adapt(adapter, DOMAIN_ANNOUNCEMENTS, _saved_payload({"pages": []}))

    assert envelope["status"] == "incomplete_pagination"
    assert envelope["normalized"]["page_complete"] is False
    assert envelope["normalized"]["total_count"] == 0


def test_all_announcements_after_asof_maps_to_out_of_asof(
    adapter: Any,
) -> None:
    pages = [
        {
            "pageNum": 1,
            "totalAnnouncement": 1,
            "announcements": [
                {
                    "announcementTitle": "合成样例：as_of 后公告（非真实披露）",
                    "announcementTime": 1783476000000,
                    "adjunctUrl": "finalpage/2026-07/synthetic-after-asof.pdf",
                }
            ],
        }
    ]

    envelope = _adapt(
        adapter, DOMAIN_ANNOUNCEMENTS, _saved_payload({"pages": pages})
    )

    assert envelope["status"] == "out_of_asof"
    assert envelope["normalized"]["usable_count"] == 0
    assert envelope["normalized"]["total_count"] == 1


def test_all_report_periods_after_asof_maps_to_out_of_asof(
    adapter: Any,
) -> None:
    body = {
        "format": "sina_report_list",
        "result": {
            "data": {
                "report_list": {
                    "20260930": {
                        "data": [
                            {"item_title": "营业收入", "item_value": "999.99"}
                        ]
                    }
                }
            }
        },
    }

    envelope = _adapt(
        adapter,
        DOMAIN_FINANCIAL,
        _saved_payload(body, period="2026-09-30"),
        _financial_request(period="2026-09-30"),
    )

    assert envelope["status"] == "out_of_asof"
    assert envelope["normalized"]["rows"] == []
    assert envelope["normalized"]["period_count"] == 1


def _sina_real_shaped_body(
    items_by_period: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    return {
        "format": "sina_report_list",
        "result": {
            "data": {
                "report_list": {
                    period: {"data": items}
                    for period, items in items_by_period.items()
                }
            }
        },
    }


_SINA_REAL_SHAPED_ITEMS = [
    {
        "item_title": "营业总收入",
        "item_value": "1234567.89",
        "item_tongbi": 0.25796,
    },
    # Real responses carry null item_value rows for bank/insurance template
    # items that do not apply to the subject; they must be dropped.
    {"item_title": "利息收入", "item_value": None},
    {"item_title": "已赚保费", "item_value": None, "item_tongbi": None},
    {"item_title": "营业成本", "item_value": "987654.32", "item_tongbi": 2},
]


def test_sina_real_shaped_nulls_and_numeric_tongbi_map_to_success(
    adapter: Any,
) -> None:
    body = _sina_real_shaped_body({"20260331": _SINA_REAL_SHAPED_ITEMS})

    envelope = _adapt(
        adapter,
        DOMAIN_FINANCIAL,
        _saved_payload(body, period="2026-03-31", scope="合并", unit="CNY"),
        _financial_request(),
    )

    assert envelope["status"] == "success"
    assert envelope["normalized"]["period_count"] == 1
    row = envelope["normalized"]["rows"][0]
    assert row["报告期"] == "2026-03-31"
    assert row["营业总收入"] == "1234567.89"
    assert row["营业总收入_同比"] == "0.25796"
    assert row["营业成本_同比"] == "2"
    assert "利息收入" not in row
    assert "已赚保费" not in row

    # The stage-03 normalizer itself stays string-only: the raw real-shaped
    # payload is still formally rejected when handed over without the
    # adapter's evidenced tolerance.
    tool = load_script("financial_statements.py")
    with pytest.raises(ValueError):
        tool.normalize_finance_report(body, limit=1)


def test_sina_all_null_item_values_map_to_not_found(adapter: Any) -> None:
    body = _sina_real_shaped_body(
        {"20260331": [{"item_title": "利息收入", "item_value": None}]}
    )

    envelope = _adapt(
        adapter,
        DOMAIN_FINANCIAL,
        _saved_payload(body, period="2026-03-31", scope="合并", unit="CNY"),
        _financial_request(),
    )

    # Closed retrieval surface (report_list non-empty) but no target values.
    assert envelope["status"] == "not_found"
    assert envelope["normalized"]["period_count"] == 1
    assert envelope["normalized"]["rows"] == [{"报告期": "2026-03-31"}]


def test_sina_row_missing_item_value_key_maps_to_parse_error(
    adapter: Any,
) -> None:
    # The evidenced tolerance covers explicit null item_value rows only. A row
    # with the key entirely absent is a structural deviation outside that
    # evidence: it must reach the stage-03 rejection path (parse_error), not
    # silently disappear the way an explicit null does.
    body = _sina_real_shaped_body(
        {
            "20260331": [
                {"item_title": "营业总收入", "item_value": "1234567.89"},
                {"item_title": "合成样例：缺失取值键（非真实披露）"},
            ]
        }
    )

    envelope = _adapt(
        adapter,
        DOMAIN_FINANCIAL,
        _saved_payload(body, period="2026-03-31", scope="合并", unit="CNY"),
        _financial_request(),
    )

    assert envelope["status"] == "parse_error"
    assert envelope["normalized"] is None


@pytest.mark.parametrize(
    ("axis", "requested", "recorded"),
    (
        ("subject", "000002.SZ", SUBJECT),
        ("period", "2025-12-31", "2026-03-31"),
        ("scope", "单体", "合并"),
        ("unit", "USD", "CNY"),
        ("purpose", "另一种合成用途", "合成用途：交叉核验"),
    ),
)
def test_axis_mismatch_maps_to_scope_mismatch_with_both_values(
    adapter: Any, axis: str, requested: object, recorded: object
) -> None:
    payload = _financial_payload()
    payload[axis] = recorded
    request = _financial_request()
    request[axis] = requested

    envelope = _adapt(adapter, DOMAIN_FINANCIAL, payload, request)

    assert envelope["status"] == "scope_mismatch"
    axis_report = envelope["equivalence"]["axes"][axis]
    assert axis_report["requested"] == requested
    assert axis_report["payload"] == recorded
    assert axis_report["match"] is False


@pytest.mark.parametrize(
    "body_kwargs",
    (
        {"trade_date": "2026-07-08"},
        {"trade_date": "2026-06-30", "trade_time": "19:00:00"},
    ),
    ids=(
        "test_snapshot_after_asof_maps_to_out_of_asof",
        "test_snapshot_trade_time_after_asof_maps_to_out_of_asof",
    ),
)
def test_snapshot_content_after_asof_maps_to_out_of_asof(
    adapter: Any, body_kwargs: dict[str, Any]
) -> None:
    # Snapshot domains reach the same ladder: a trade_date past as_of with no
    # earlier content is out_of_asof, not success; a snapshot carrying
    # trade_time is judged at full moment precision (a 19:00 quote on the
    # as_of date with as_of 18:00 is look-ahead content).
    payload = _saved_payload(_snapshot_body(**body_kwargs), unit="元")

    envelope = _adapt(
        adapter, DOMAIN_PEER_VALUATION, payload, _request(unit="元")
    )

    assert envelope["status"] == "out_of_asof"


def test_snapshot_subject_mismatch_maps_to_scope_mismatch(
    adapter: Any,
) -> None:
    payload = _saved_payload(
        _snapshot_body(), unit="元", subject="000002.SZ"
    )

    envelope = _adapt(
        adapter, DOMAIN_PEER_VALUATION, payload, _request(unit="元")
    )

    assert envelope["status"] == "scope_mismatch"
    axis_report = envelope["equivalence"]["axes"]["subject"]
    assert axis_report["requested"] == SUBJECT
    assert axis_report["payload"] == "000002.SZ"
    assert axis_report["match"] is False


def test_same_day_announcement_after_asof_maps_to_out_of_asof(
    adapter: Any,
) -> None:
    # Stage-03 filters at full datetime precision, so a 19:00 announcement on
    # the as_of date (as_of 18:00) is unusable; the adapter must classify the
    # whole retrieval as out_of_asof, not success with zero usable rows.
    moment = datetime(2026, 6, 30, 19, 0, tzinfo=timezone(timedelta(hours=8)))
    pages = [
        {
            "pageNum": 1,
            "totalAnnouncement": 1,
            "announcements": [
                {
                    "announcementTitle": "合成样例：同日 as_of 后公告（非真实披露）",
                    "announcementTime": int(moment.timestamp() * 1000),
                    "adjunctUrl": "finalpage/2026-06/synthetic-same-day.pdf",
                }
            ],
        }
    ]

    envelope = _adapt(adapter, DOMAIN_ANNOUNCEMENTS, _saved_payload({"pages": pages}))

    assert envelope["status"] == "out_of_asof"
    assert envelope["normalized"]["usable_count"] == 0
    assert envelope["normalized"]["total_count"] == 1


def test_snapshot_trade_time_before_asof_on_same_day_stays_success(
    adapter: Any,
) -> None:
    payload = _saved_payload(
        _snapshot_body(trade_date="2026-06-30", trade_time="15:00:00"),
        unit="元",
    )

    envelope = _adapt(
        adapter, DOMAIN_PEER_VALUATION, payload, _request(unit="元")
    )

    assert envelope["status"] == "success"


def test_snapshot_body_unit_contradicting_declared_unit_maps_to_scope_mismatch(
    adapter: Any,
) -> None:
    # The unit axis must cross-check the body's actual price_unit, not only
    # the wrapper-declared unit: declared CNY + body USD is a mismatch with
    # every original value preserved.
    payload = _saved_payload(
        _snapshot_body(price_unit="USD", market_cap_unit="USD"), unit="CNY"
    )

    envelope = _adapt(
        adapter, DOMAIN_PEER_VALUATION, payload, _request(unit="CNY")
    )

    assert envelope["status"] == "scope_mismatch"
    axis_report = envelope["equivalence"]["axes"]["unit"]
    assert axis_report["requested"] == "CNY"
    assert axis_report["payload"] == "CNY"
    assert axis_report["body"] == "USD"
    assert axis_report["match"] is False


def test_snapshot_body_unit_same_currency_family_matches(adapter: Any) -> None:
    # 元 and CNY are one canonical family: no contradiction, match=true.
    payload = _saved_payload(_snapshot_body(price_unit="CNY"), unit="CNY")

    envelope = _adapt(
        adapter, DOMAIN_PEER_VALUATION, payload, _request(unit="元")
    )

    assert envelope["status"] == "success"
    axis_report = envelope["equivalence"]["axes"]["unit"]
    assert axis_report["match"] is True


@pytest.mark.parametrize(
    ("price_unit", "expected_body", "expected_match"),
    (
        ("价格元、市值亿元（腾讯标签）", "价格元、市值亿元（腾讯标签）", None),
        ("元", "元", True),
    ),
    ids=(
        "test_snapshot_descriptive_body_unit_label_is_unverified_not_mismatch",
        "test_snapshot_body_unit_matching_keeps_success_with_body_axis",
    ),
)
def test_snapshot_body_unit_axis_verdicts(
    adapter: Any, price_unit: str, expected_body: str, expected_match: Any
) -> None:
    # Saved batch evidence carries descriptive labels like
    # "价格元、市值亿元（腾讯标签）": same currency as the declared 元 but not
    # canonically parseable. The honest mechanical verdict is match=null
    # (unverified, cannot alone support replacement-obtained), recorded
    # verbatim — not a false contradiction and not silent equivalence.
    payload = _saved_payload(_snapshot_body(price_unit=price_unit), unit="元")

    envelope = _adapt(
        adapter, DOMAIN_PEER_VALUATION, payload, _request(unit="元")
    )

    assert envelope["status"] == "success"
    axis_report = envelope["equivalence"]["axes"]["unit"]
    assert axis_report["body"] == expected_body
    assert axis_report["match"] is expected_match


@pytest.mark.parametrize(
    "drop",
    ("provider", "entry", "captured_at", "field_basis", "license"),
)
def test_source_metadata_missing_category_is_rejected(
    adapter: Any, drop: str
) -> None:
    metadata = _metadata()
    metadata.pop(drop)
    payload = _saved_payload(_snapshot_body(), unit="元")
    payload["source_metadata"] = metadata

    with pytest.raises(ValueError):
        _adapt(adapter, DOMAIN_PEER_VALUATION, payload, _request(unit="元"))


def test_empty_source_metadata_is_rejected(adapter: Any) -> None:
    payload = _saved_payload(_snapshot_body(), unit="元")
    payload["source_metadata"] = {}

    with pytest.raises(ValueError):
        _adapt(adapter, DOMAIN_PEER_VALUATION, payload, _request(unit="元"))


def test_batch_shaped_metadata_aliases_are_accepted(adapter: Any) -> None:
    metadata = {
        "producer": "合成生产者（非真实来源）",
        "channel": "合成渠道（非真实来源）",
        "entry": "https://synthetic.example/api",
        "fetch_request_time": "2026-06-30T15:00:00+08:00",
        "raw_files": ["raw/synthetic-response.json"],
        "license": "public",
    }
    payload = _saved_payload(_snapshot_body(), unit="元")
    payload["source_metadata"] = metadata

    envelope = _adapt(
        adapter, DOMAIN_PEER_VALUATION, payload, _request(unit="元")
    )

    assert envelope["status"] == "success"
    assert envelope["source_metadata"] is metadata


def test_snapshot_body_security_contradicting_subject_maps_to_scope_mismatch(
    adapter: Any,
) -> None:
    # The subject axis cross-checks the body's actual security identifier on
    # canonical exchange-prefixed codes: sz000002 vs 000001.SZ is a real
    # contradiction even when the wrapper self-reports the right subject.
    payload = _saved_payload(
        _snapshot_body(security="sz000002"), unit="元"
    )

    envelope = _adapt(
        adapter, DOMAIN_PEER_VALUATION, payload, _request(unit="元")
    )

    assert envelope["status"] == "scope_mismatch"
    axis_report = envelope["equivalence"]["axes"]["subject"]
    assert axis_report["requested"] == SUBJECT
    assert axis_report["payload"] == SUBJECT
    assert axis_report["body"] == "sz000002"
    assert axis_report["match"] is False


def test_snapshot_body_security_same_code_matches(adapter: Any) -> None:
    payload = _saved_payload(
        _snapshot_body(security="sz000001"), unit="元", subject=SUBJECT
    )

    envelope = _adapt(
        adapter, DOMAIN_PEER_VALUATION, payload, _request(unit="元")
    )

    assert envelope["status"] == "success"
    axis_report = envelope["equivalence"]["axes"]["subject"]
    assert axis_report["body"] == "sz000001"
    assert axis_report["match"] is True


def test_snapshot_body_trade_date_contradicting_period_maps_to_scope_mismatch(
    adapter: Any,
) -> None:
    # Snapshot period axis cross-checks the body trade_date: a 06-29 quote
    # against a requested 06-30 period is a period mismatch.
    payload = _saved_payload(
        _snapshot_body(trade_date="2026-06-29"), unit="元", period="2026-06-30"
    )

    envelope = _adapt(
        adapter, DOMAIN_PEER_VALUATION, payload, _request(unit="元", period="2026-06-30")
    )

    assert envelope["status"] == "scope_mismatch"
    axis_report = envelope["equivalence"]["axes"]["period"]
    assert axis_report["body"] == ["2026-06-29"]
    assert axis_report["match"] is False


def test_financial_body_periods_contradicting_period_maps_to_scope_mismatch(
    adapter: Any,
) -> None:
    body = _sina_real_shaped_body({"20251231": _SINA_REAL_SHAPED_ITEMS})
    payload = _saved_payload(
        body, period="2026-03-31", scope="合并", unit="CNY"
    )

    envelope = _adapt(
        adapter, DOMAIN_FINANCIAL, payload, _financial_request()
    )

    assert envelope["status"] == "scope_mismatch"
    axis_report = envelope["equivalence"]["axes"]["period"]
    assert axis_report["body"] == ["2025-12-31"]
    assert axis_report["match"] is False


def test_snapshot_moment_uses_source_timezone_not_as_of_timezone(
    adapter: Any,
) -> None:
    # Source timezone Asia/Shanghai, local 19:00 quote, as_of 11:30Z
    # (= 19:30 +08:00): the content is BEFORE as_of and must be usable.
    # Binding the trade_time to as_of's UTC tzinfo misplaces it a day later.
    payload = _saved_payload(
        _snapshot_body(trade_date="2026-06-30", trade_time="19:00:00"),
        unit="元",
    )
    request = _request(unit="元", as_of=datetime(2026, 6, 30, 11, 30, tzinfo=UTC))

    envelope = _adapt(adapter, DOMAIN_PEER_VALUATION, payload, request)

    assert envelope["status"] == "success"
    assert envelope["equivalence"]["as_of"]["content_moment"] == (
        "2026-06-30T19:00:00+08:00"
    )


def test_snapshot_trade_time_without_declared_timezone_is_rejected(
    adapter: Any,
) -> None:
    payload = _saved_payload(
        _snapshot_body(trade_date="2026-06-30", trade_time="19:00:00"),
        unit="元",
    )
    payload["source_metadata"] = {
        key: value
        for key, value in _metadata().items()
        if key != "timezone"
    }

    envelope = _adapt(adapter, DOMAIN_PEER_VALUATION, payload, _request(unit="元"))

    assert envelope["status"] == "parse_error"


def test_decorated_source_timezone_declarations_resolve(adapter: Any) -> None:
    # Saved batches declare timezones in decorated prose; an embedded IANA
    # token or a closed standard-time name must resolve to the source zone.
    for declared in (
        "北京时间 (Asia/Shanghai)",
        "时间戳为交易所行情时间，按北京时间",
    ):
        payload = _saved_payload(
            _snapshot_body(trade_date="2026-06-30", trade_time="15:04"),
            unit="元",
        )
        payload["source_metadata"]["timezone"] = declared

        envelope = _adapt(
            adapter, DOMAIN_PEER_VALUATION, payload, _request(unit="元")
        )

        assert envelope["status"] == "success", declared
        assert envelope["equivalence"]["as_of"]["content_moment"] == (
            "2026-06-30T15:04:00+08:00"
        ), declared


def test_unrecognizable_source_timezone_is_rejected(adapter: Any) -> None:
    payload = _saved_payload(
        _snapshot_body(trade_date="2026-06-30", trade_time="15:04"),
        unit="元",
    )
    payload["source_metadata"]["timezone"] = "当地时间（未注明时区）"

    envelope = _adapt(adapter, DOMAIN_PEER_VALUATION, payload, _request(unit="元"))

    assert envelope["status"] == "parse_error"


def test_snapshot_hh_mm_trade_time_is_accepted(adapter: Any) -> None:
    payload = _saved_payload(
        _snapshot_body(trade_date="2026-06-30", trade_time="15:04"),
        unit="元",
    )

    envelope = _adapt(
        adapter, DOMAIN_PEER_VALUATION, payload, _request(unit="元")
    )

    assert envelope["status"] == "success"
    assert envelope["equivalence"]["as_of"]["content_moment"] == (
        "2026-06-30T15:04:00+08:00"
    )


def test_snapshot_same_day_without_time_records_indeterminate_asof(
    adapter: Any,
) -> None:
    # Same-day snapshot without a time cannot prove its instant relative to
    # an intraday as_of: the honest verdict is content_after_asof=null
    # (indeterminate, cannot alone support replacement-obtained), not a
    # silent claim that the content precedes as_of.
    payload = _saved_payload(_snapshot_body(trade_date="2026-06-30"), unit="元")

    envelope = _adapt(
        adapter, DOMAIN_PEER_VALUATION, payload, _request(unit="元")
    )

    assert envelope["status"] == "success"
    assert envelope["equivalence"]["as_of"]["content_after_asof"] is None


def test_snapshot_earlier_day_without_time_proves_before_asof(
    adapter: Any,
) -> None:
    payload = _saved_payload(_snapshot_body(trade_date="2026-06-29"), unit="元")

    envelope = _adapt(
        adapter, DOMAIN_PEER_VALUATION, payload, _request(unit="元")
    )

    assert envelope["status"] == "success"
    assert envelope["equivalence"]["as_of"]["content_after_asof"] is False


def test_disabled_source_short_circuits_before_parsing(adapter: Any) -> None:
    envelope = _adapt(
        adapter,
        DOMAIN_ANNOUNCEMENTS,
        _saved_payload(_announcements_body()),
        disabled=(SOURCE_ID,),
    )

    assert set(envelope) == ENVELOPE_KEYS
    assert envelope["called"] is False
    assert envelope["disabled"] is True
    assert envelope["status"] is None
    assert envelope["normalized"] is None
    assert envelope["equivalence"] is None
    assert envelope["source_metadata"] is None


def test_disabled_source_does_not_parse_malformed_payload(
    adapter: Any,
) -> None:
    payload = {
        "source_metadata": "garbage",
        "response": None,
        "body": "not-an-object",
    }

    envelope = _adapt(
        adapter, DOMAIN_FINANCIAL, payload, disabled=(SOURCE_ID,)
    )

    assert envelope["called"] is False
    assert envelope["disabled"] is True
    assert envelope["status"] is None
    assert envelope["normalized"] is None


def test_other_disabled_sources_do_not_affect_the_target(adapter: Any) -> None:
    envelope = _adapt(
        adapter,
        DOMAIN_ANNOUNCEMENTS,
        _saved_payload(_announcements_body()),
        disabled=("synthetic-source-other",),
    )

    assert envelope["called"] is True
    assert envelope["disabled"] is False
    assert envelope["status"] == "success"


def test_same_input_produces_byte_identical_envelopes(adapter: Any) -> None:
    payload = _saved_payload(_snapshot_body(), unit="元")

    first = _adapt(adapter, DOMAIN_PEER_VALUATION, payload, _request(unit="元"))
    second = _adapt(adapter, DOMAIN_PEER_VALUATION, payload, _request(unit="元"))

    assert first == second
    assert _canonical(first) == _canonical(second)


def test_input_change_changes_raw_input_sha256(adapter: Any) -> None:
    first = _adapt(
        adapter,
        DOMAIN_PEER_VALUATION,
        _saved_payload(_snapshot_body(price="10.25"), unit="元"),
        _request(unit="元"),
    )
    second = _adapt(
        adapter,
        DOMAIN_PEER_VALUATION,
        _saved_payload(_snapshot_body(price="10.26"), unit="元"),
        _request(unit="元"),
    )

    assert first["raw_input_sha256"] != second["raw_input_sha256"]


def test_raw_input_sha256_matches_canonical_serialization(
    adapter: Any,
) -> None:
    payload = _saved_payload(_announcements_body())

    envelope = _adapt(adapter, DOMAIN_ANNOUNCEMENTS, payload)

    assert envelope["raw_input_sha256"] == hashlib.sha256(
        _canonical(payload)
    ).hexdigest()


def test_module_source_imports_no_network_libraries() -> None:
    tree = ast.parse(SCRIPT_PATH.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])

    assert not roots & NETWORK_IMPORT_ROOTS


def test_industry_domain_is_formally_rejected(adapter: Any) -> None:
    with pytest.raises(ValueError, match="行业需求与竞争"):
        _adapt(adapter, DOMAIN_INDUSTRY, _saved_payload(_announcements_body()))


def test_unknown_domain_is_rejected(adapter: Any) -> None:
    with pytest.raises(ValueError):
        _adapt(adapter, "无此数据域", _saved_payload(_announcements_body()))


def test_swapped_market_cap_labels_keep_both_original_values(
    adapter: Any,
) -> None:
    body = _snapshot_body(
        total_market_cap=FLOAT_MARKET_CAP,
        float_market_cap=TOTAL_MARKET_CAP,
    )

    envelope = _adapt(
        adapter,
        DOMAIN_PEER_VALUATION,
        _saved_payload(body, unit="元"),
        _request(unit="元"),
    )

    assert envelope["status"] == "success"
    checks = _checks_by_name(envelope)
    total_check = checks["total_market_cap_vs_price_times_shares"]
    float_check = checks["float_market_cap_vs_price_times_shares"]
    assert total_check["consistent"] is False
    assert float_check["consistent"] is False
    assert total_check["labeled"]["value"] == FLOAT_MARKET_CAP
    assert total_check["recomputed"]["value"] == TOTAL_MARKET_CAP
    assert float_check["labeled"]["value"] == TOTAL_MARKET_CAP
    assert float_check["recomputed"]["value"] == FLOAT_MARKET_CAP
    # The adapter never swaps labels: normalized keeps the labeled values as saved.
    assert envelope["normalized"]["total_market_cap"] == FLOAT_MARKET_CAP
    assert envelope["normalized"]["float_market_cap"] == TOTAL_MARKET_CAP


def test_market_cap_unit_mismatch_records_both_values_without_verdict(
    adapter: Any,
) -> None:
    body = _snapshot_body(market_cap_unit="亿元")

    envelope = _adapt(
        adapter,
        DOMAIN_PEER_VALUATION,
        _saved_payload(body, unit="元"),
        _request(unit="元"),
    )

    checks = _checks_by_name(envelope)
    assert set(checks) == {
        "total_market_cap_vs_price_times_shares",
        "float_market_cap_vs_price_times_shares",
    }
    for check in checks.values():
        assert check["consistent"] is None
        assert check["labeled"]["unit"] == "亿元"
        assert check["recomputed"]["unit"] == "元"


def test_source_metadata_passes_through_verbatim(adapter: Any) -> None:
    metadata = _metadata()
    metadata["extra_note"] = "合成附加标注"

    envelope = _adapt(
        adapter,
        DOMAIN_ANNOUNCEMENTS,
        _saved_payload(_announcements_body(), source_metadata=metadata),
    )

    assert envelope["source_metadata"] == metadata


def test_cli_success_writes_envelope_and_replays_byte_identically(
    tmp_path: Path,
) -> None:
    input_path = _write_json(
        tmp_path / "input.json",
        _cli_input(DOMAIN_FINANCIAL, _financial_payload(), _financial_request()),
    )
    first_output = tmp_path / "run-one" / "envelope.json"
    second_output = tmp_path / "run-two" / "envelope.json"

    first = _run_cli(input_path, first_output)
    second = _run_cli(input_path, second_output)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first_output.read_bytes() == second_output.read_bytes()
    outer = json.loads(first_output.read_text(encoding="utf-8"))
    assert outer["schema_version"] == "1.0"
    assert outer["tool"] == "source_adapter"
    assert outer["status"] == "success"
    assert (
        outer["input_sha256"]
        == hashlib.sha256(input_path.read_bytes()).hexdigest()
    )
    result = outer["result"]
    assert set(result) == ENVELOPE_KEYS
    assert result["status"] == "success"
    assert result["called"] is True
    assert result["disabled"] is False


def test_cli_malformed_input_exits_1_with_failure_envelope(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "input.json"
    input_path.write_text("{not json", encoding="utf-8")
    output_path = tmp_path / "envelope.json"

    completed = _run_cli(input_path, output_path)

    assert completed.returncode == 1
    envelope = json.loads(output_path.read_text(encoding="utf-8"))
    assert envelope["tool"] == "source_adapter"
    assert envelope["status"] == "failed"
    assert envelope["error_type"]
    assert envelope["error_message"]
    assert "result" not in envelope


def test_cli_contract_only_domain_exits_1_with_failure_envelope(
    tmp_path: Path,
) -> None:
    input_path = _write_json(
        tmp_path / "input.json",
        _cli_input(DOMAIN_INDUSTRY, _saved_payload(_announcements_body())),
    )
    output_path = tmp_path / "envelope.json"

    completed = _run_cli(input_path, output_path)

    assert completed.returncode == 1
    envelope = json.loads(output_path.read_text(encoding="utf-8"))
    assert envelope["status"] == "failed"
    assert envelope["error_type"] == "ValueError"
    assert "行业需求与竞争" in envelope["error_message"]


def test_cli_missing_disabled_sources_exits_1_with_failure_envelope(
    tmp_path: Path,
) -> None:
    payload = _cli_input(
        DOMAIN_ANNOUNCEMENTS, _saved_payload(_announcements_body())
    )
    del payload["disabled_sources"]
    input_path = _write_json(tmp_path / "input.json", payload)
    output_path = tmp_path / "envelope.json"

    completed = _run_cli(input_path, output_path)

    assert completed.returncode == 1
    envelope = json.loads(output_path.read_text(encoding="utf-8"))
    assert envelope["status"] == "failed"
    assert envelope["error_type"] == "ValueError"


def test_cli_disabled_source_passes_through(tmp_path: Path) -> None:
    input_path = _write_json(
        tmp_path / "input.json",
        _cli_input(
            DOMAIN_PEER_VALUATION,
            _saved_payload(_snapshot_body(), unit="元"),
            _request(unit="元"),
            disabled=(SOURCE_ID,),
        ),
    )
    output_path = tmp_path / "envelope.json"

    completed = _run_cli(input_path, output_path)

    assert completed.returncode == 0, completed.stderr
    result = json.loads(output_path.read_text(encoding="utf-8"))["result"]
    assert result["called"] is False
    assert result["disabled"] is True
    assert result["status"] is None
    assert result["normalized"] is None


def test_cli_missing_output_argument_exits_2(tmp_path: Path) -> None:
    input_path = _write_json(
        tmp_path / "input.json",
        _cli_input(DOMAIN_FINANCIAL, _financial_payload(), _financial_request()),
    )

    completed = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--input", str(input_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2


def test_cli_missing_input_file_exits_2_without_output(tmp_path: Path) -> None:
    output_path = tmp_path / "envelope.json"

    completed = _run_cli(tmp_path / "absent.json", output_path)

    assert completed.returncode == 2
    assert not output_path.exists()


def test_cli_output_equal_to_input_exits_2(tmp_path: Path) -> None:
    input_path = _write_json(
        tmp_path / "input.json",
        _cli_input(DOMAIN_FINANCIAL, _financial_payload(), _financial_request()),
    )
    before = input_path.read_bytes()

    completed = _run_cli(input_path, input_path)

    assert completed.returncode == 2
    assert input_path.read_bytes() == before


def test_cli_existing_output_is_refused(tmp_path: Path) -> None:
    input_path = _write_json(
        tmp_path / "input.json",
        _cli_input(DOMAIN_FINANCIAL, _financial_payload(), _financial_request()),
    )
    output_path = tmp_path / "envelope.json"
    output_path.write_text("existing evidence\n", encoding="utf-8")

    completed = _run_cli(input_path, output_path)

    assert completed.returncode == 2
    assert output_path.read_text(encoding="utf-8") == "existing evidence\n"


def test_main_is_callable_in_process(adapter: Any, tmp_path: Path) -> None:
    input_path = _write_json(
        tmp_path / "input.json",
        _cli_input(
            DOMAIN_MARKET_SNAPSHOT,
            _saved_payload(_snapshot_body(trading_status="交易中"), unit="元"),
            _request(unit="元"),
        ),
    )
    output_path = tmp_path / "envelope.json"

    assert (
        adapter.main(["--input", str(input_path), "--output", str(output_path)])
        == 0
    )
    assert output_path.is_file()
