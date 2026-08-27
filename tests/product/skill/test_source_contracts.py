import hashlib
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from tests.product.skill.deterministic_tool_loader import load_script

# ---------------------------------------------------------------------------
# Stage-04 offline replay regressions (Task 4)
#
# Each scenario replays a saved synthetic input through the stage-03 tools and
# the stage-04 source adapter and asserts three replay invariants: the
# envelope's ``raw_input_sha256`` equals the SHA-256 of the canonical JSON
# serialization computed in-test, identical input adapted twice yields
# byte-identical envelopes, and changing exactly one input aspect changes the
# artifact identity. All payloads are synthetic; no network is touched.
# ---------------------------------------------------------------------------

source_adapter = load_script("source_adapter.py")
announcement_index = load_script("announcement_index.py")
financial_statements = load_script("financial_statements.py")
numeric_consistency = load_script("numeric_consistency.py")

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "source_contracts"
CNINFO_PAGES = json.loads(
    (FIXTURES_DIR / "cninfo-pages.json").read_text(encoding="utf-8")
)
FINANCIAL_REPORT = json.loads(
    (FIXTURES_DIR / "financial-report.json").read_text(encoding="utf-8")
)
MARKET_SNAPSHOT = json.loads(
    (FIXTURES_DIR / "market-snapshot.json").read_text(encoding="utf-8")
)

DOMAIN_ANNOUNCEMENTS = "公告及附件"
DOMAIN_FINANCIAL = "财务报表"
DOMAIN_MARKET_SNAPSHOT = "交易状态与市场快照"

SUBJECT = MARKET_SNAPSHOT["subject"]
AS_OF = datetime.fromisoformat(CNINFO_PAGES["as_of"])
PRIMARY_SOURCE = "synthetic-source-a"
SECOND_QUOTE_SOURCE = MARKET_SNAPSHOT["source"]
CASH_FLOW_ITEM = "经营活动产生的现金流量净额"


def _saved_payload(body: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "source_metadata": {
            "provider": "synthetic-provider",
            "entry": "https://synthetic.example/snapshot",
            "captured_at": "2026-06-30T15:05:00+08:00",
            "field_basis": "合成字段依据（非真实来源）",
            "license": "public",
        },
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


def _request(**overrides: Any) -> dict[str, Any]:
    request: dict[str, Any] = {
        "subject": SUBJECT,
        "as_of": AS_OF,
        "period": None,
        "scope": None,
        "unit": None,
        "purpose": "合成用途：交叉核验",
    }
    request.update(overrides)
    return request


def _financial_body() -> dict[str, Any]:
    body = dict(FINANCIAL_REPORT)
    body["format"] = "sina_report_list"
    return body


def _financial_saved_payload(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "period": "2026-03-31",
        "scope": "合并",
        "unit": "CNY",
    }
    defaults.update(overrides)
    return _saved_payload(_financial_body(), **defaults)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _adapt_with_replay_invariants(
    domain: str,
    source_id: str,
    payload: dict[str, Any],
    request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Adapt a saved payload while asserting the three replay invariants."""
    resolved = _request() if request is None else request
    first = source_adapter.adapt_saved_response(
        domain, source_id, payload, disabled_sources=(), **resolved
    )
    second = source_adapter.adapt_saved_response(
        domain, source_id, payload, disabled_sources=(), **resolved
    )
    assert _canonical_bytes(second) == _canonical_bytes(first)
    assert first["raw_input_sha256"] == hashlib.sha256(
        _canonical_bytes(payload)
    ).hexdigest()
    return first


def _checks_by_name(envelope: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        check["check"]: check for check in envelope["equivalence"]["checks"]
    }


def test_offline_replay_announcement_pagination_completeness() -> None:
    pages = CNINFO_PAGES["pages"]

    full_index = announcement_index.normalize_announcement_pages(
        pages, as_of=AS_OF
    )
    assert full_index["page_complete"] is True
    assert full_index["total_count"] == 3
    assert full_index["returned_count"] == 3
    assert full_index["usable_count"] == 2

    full_envelope = _adapt_with_replay_invariants(
        DOMAIN_ANNOUNCEMENTS, PRIMARY_SOURCE, _saved_payload({"pages": pages})
    )
    assert full_envelope["status"] == "success"
    assert full_envelope["normalized"]["page_complete"] is True
    assert full_envelope["normalized"]["usable_count"] == 2

    truncated_pages = pages[:1]
    truncated_index = announcement_index.normalize_announcement_pages(
        truncated_pages, as_of=AS_OF
    )
    assert truncated_index["page_complete"] is False
    assert truncated_index["total_count"] == 3
    assert truncated_index["returned_count"] == 2

    truncated_envelope = _adapt_with_replay_invariants(
        DOMAIN_ANNOUNCEMENTS,
        PRIMARY_SOURCE,
        _saved_payload({"pages": truncated_pages}),
    )
    assert truncated_envelope["status"] == "incomplete_pagination"
    assert truncated_envelope["normalized"]["page_complete"] is False
    assert truncated_envelope["normalized"]["returned_count"] == 2
    assert truncated_envelope["normalized"]["total_count"] == 3
    # Pagination did not close, so the result is not "no announcements":
    # the usable announcements from the saved page are still carried.
    assert truncated_envelope["normalized"]["usable_count"] == 2

    # Changing exactly one input aspect (dropping the second saved page)
    # changes the artifact identity: payload hash and envelope bytes.
    assert (
        truncated_envelope["raw_input_sha256"]
        != full_envelope["raw_input_sha256"]
    )
    assert (
        _canonical_bytes(truncated_envelope) != _canonical_bytes(full_envelope)
    )


def test_offline_replay_financial_substitute_equivalence_boundaries() -> None:
    agreement_request = _request(period="2026-03-31", scope="合并", unit="CNY")
    agreement = _adapt_with_replay_invariants(
        DOMAIN_FINANCIAL,
        PRIMARY_SOURCE,
        _financial_saved_payload(),
        agreement_request,
    )
    assert agreement["status"] == "success"
    assert agreement["equivalence"]["axes"]["period"]["match"] is True
    assert agreement["equivalence"]["axes"]["scope"]["match"] is True

    # The adapter delegates sina bodies to the stage-03 Sina-only normalizer:
    # the substitute rows are exactly the rows that tool produces directly.
    direct_rows = financial_statements.normalize_finance_report(
        FINANCIAL_REPORT, limit=2
    )
    assert agreement["normalized"]["rows"] == direct_rows

    # Agreement path: the structured substitute value equals the L0 synthetic
    # value, so the explicit-unit L0 check passes and the value may enter
    # candidates.
    row = next(
        row
        for row in agreement["normalized"]["rows"]
        if row["报告期"] == "2026-03-31"
    )
    l0_check = numeric_consistency.compare_metric(
        metric="营业收入",
        unit="CNY",
        claimed="1234567.89",
        recomputed=row["营业收入"],
        tolerance="0",
    )
    assert l0_check.consistent is True
    assert l0_check.absolute_difference == Decimal("0")

    # A different 报告期 maps to scope_mismatch with both original values.
    period_payload = _financial_saved_payload()
    period_envelope = _adapt_with_replay_invariants(
        DOMAIN_FINANCIAL,
        PRIMARY_SOURCE,
        period_payload,
        _request(period="2025-12-31", scope="合并", unit="CNY"),
    )
    assert period_envelope["status"] == "scope_mismatch"
    assert period_envelope["equivalence"]["axes"]["period"] == {
        "requested": "2025-12-31",
        "payload": "2026-03-31",
        "body": ["2025-12-31", "2026-03-31"],
        "match": False,
    }

    # A different 合并范围 maps to scope_mismatch with both original values.
    scope_envelope = _adapt_with_replay_invariants(
        DOMAIN_FINANCIAL,
        PRIMARY_SOURCE,
        _financial_saved_payload(scope="合并"),
        _request(period="2026-03-31", scope="单体", unit="CNY"),
    )
    assert scope_envelope["status"] == "scope_mismatch"
    assert scope_envelope["equivalence"]["axes"]["scope"] == {
        "requested": "单体",
        "payload": "合并",
        "match": False,
    }

    # Changing exactly one input aspect (the requested period now matches the
    # payload) changes the artifact identity even though the saved payload —
    # and therefore its hash — is unchanged.
    matched_envelope = _adapt_with_replay_invariants(
        DOMAIN_FINANCIAL, PRIMARY_SOURCE, period_payload, agreement_request
    )
    assert matched_envelope["status"] == "success"
    assert (
        matched_envelope["raw_input_sha256"]
        == period_envelope["raw_input_sha256"]
    )
    assert (
        _canonical_bytes(matched_envelope)
        != _canonical_bytes(period_envelope)
    )


def test_offline_replay_second_quote_source_market_cap_conflict() -> None:
    price = Decimal(MARKET_SNAPSHOT["price"])
    recomputed_total = format(
        price * Decimal(MARKET_SNAPSHOT["total_shares"]), "f"
    )
    recomputed_float = format(
        price * Decimal(MARKET_SNAPSHOT["float_shares"]), "f"
    )
    # Second quote source whose labeled caps contradict price times shares
    # (the known label-swap precedent, in synthetic form): the total cap label
    # carries the float cap and vice versa.
    body = {
        "trade_date": "2026-06-30",
        "price": MARKET_SNAPSHOT["price"],
        "price_unit": "元",
        "total_shares": MARKET_SNAPSHOT["total_shares"],
        "total_shares_unit": "股",
        "float_shares": MARKET_SNAPSHOT["float_shares"],
        "float_shares_unit": "股",
        "market_cap_unit": "元",
        "total_market_cap": recomputed_float,
        "float_market_cap": recomputed_total,
    }

    envelope = _adapt_with_replay_invariants(
        DOMAIN_MARKET_SNAPSHOT,
        SECOND_QUOTE_SOURCE,
        _saved_payload(body, unit="元"),
        _request(unit="元"),
    )
    assert envelope["status"] == "success"

    # The contradiction is carried as an explicit inconsistency signal and
    # both original values are preserved; labels are never swapped.
    checks = _checks_by_name(envelope)
    total_check = checks["total_market_cap_vs_price_times_shares"]
    float_check = checks["float_market_cap_vs_price_times_shares"]
    assert total_check["consistent"] is False
    assert float_check["consistent"] is False
    assert total_check["labeled"] == {"value": recomputed_float, "unit": "元"}
    assert total_check["recomputed"] == {
        "value": recomputed_total,
        "unit": "元",
    }
    assert float_check["labeled"] == {"value": recomputed_total, "unit": "元"}
    assert float_check["recomputed"] == {
        "value": recomputed_float,
        "unit": "元",
    }
    assert envelope["normalized"]["total_market_cap"] == recomputed_float
    assert envelope["normalized"]["float_market_cap"] == recomputed_total

    # The formal recompute path is the stage-03 numeric_consistency tool with
    # explicit units; it also keeps both sides and reports the conflict.
    total_cap, float_cap = numeric_consistency.market_cap_values(
        price=body["price"],
        price_unit="元",
        total_shares=body["total_shares"],
        total_shares_unit="股",
        float_shares=body["float_shares"],
        float_shares_unit="股",
    )
    total_conflict = numeric_consistency.compare_metric(
        metric="总市值",
        unit="元",
        claimed=body["total_market_cap"],
        recomputed=total_cap,
        tolerance="0",
    )
    float_conflict = numeric_consistency.compare_metric(
        metric="流通市值",
        unit="元",
        claimed=body["float_market_cap"],
        recomputed=float_cap,
        tolerance="0",
    )
    assert total_conflict.consistent is False
    assert float_conflict.consistent is False
    assert total_conflict.absolute_difference > 0
    assert float_conflict.absolute_difference > 0
    assert format(total_cap, "f") == recomputed_total
    assert format(float_cap, "f") == recomputed_float

    # Changing exactly one input aspect (correcting only the labeled total
    # cap) changes the artifact identity and flips exactly that check.
    corrected = dict(body, total_market_cap=recomputed_total)
    corrected_envelope = _adapt_with_replay_invariants(
        DOMAIN_MARKET_SNAPSHOT,
        SECOND_QUOTE_SOURCE,
        _saved_payload(corrected, unit="元"),
        _request(unit="元"),
    )
    assert (
        corrected_envelope["raw_input_sha256"]
        != envelope["raw_input_sha256"]
    )
    corrected_checks = _checks_by_name(corrected_envelope)
    assert (
        corrected_checks["total_market_cap_vs_price_times_shares"][
            "consistent"
        ]
        is True
    )
    assert (
        corrected_checks["float_market_cap_vs_price_times_shares"][
            "consistent"
        ]
        is False
    )


def test_offline_replay_same_domain_schema_versions_stay_distinct() -> None:
    # Two saved responses for the same 财务报表 domain under two different
    # registered payload schemas: the saved sina report_list (which covers the
    # cash-flow item) and the eastmoney push2 indicators schema (which does
    # not). Both are adapted separately.
    sina_payload = _financial_saved_payload()
    eastmoney_payload = _saved_payload(
        {
            "format": "eastmoney_push2_indicators",
            "period": "2026-03-31",
            "data": {"f183": "123456.78", "f186": "45.67", "f188": "12.34"},
        },
        period="2026-03-31",
        scope="合并",
        unit="CNY",
    )
    request = _request(period="2026-03-31", scope="合并", unit="CNY")

    sina_envelope = _adapt_with_replay_invariants(
        DOMAIN_FINANCIAL, "synthetic-source-sina", sina_payload, request
    )
    eastmoney_envelope = _adapt_with_replay_invariants(
        DOMAIN_FINANCIAL,
        "synthetic-source-eastmoney",
        eastmoney_payload,
        request,
    )
    assert sina_envelope["status"] == "success"
    assert eastmoney_envelope["status"] == "success"

    # Two distinct envelopes: no merge and no overwrite of one by the other.
    assert sina_envelope["source_id"] != eastmoney_envelope["source_id"]
    assert (
        sina_envelope["raw_input_sha256"]
        != eastmoney_envelope["raw_input_sha256"]
    )
    assert (
        _canonical_bytes(sina_envelope)
        != _canonical_bytes(eastmoney_envelope)
    )
    assert set(sina_envelope["normalized"]) == {"rows", "period_count"}
    assert set(eastmoney_envelope["normalized"]) == {"period", "indicators"}

    # The coverage difference stays explicit divergence evidence: only the
    # sina schema covers the cash-flow item, and neither artifact carries the
    # union, so neither side silently impersonates complete coverage.
    sina_coverage = {
        key
        for row in sina_envelope["normalized"]["rows"]
        for key in row
        if key != "报告期"
    }
    eastmoney_coverage = {
        indicator["name"]
        for indicator in eastmoney_envelope["normalized"]["indicators"]
    }
    assert CASH_FLOW_ITEM in sina_coverage
    assert CASH_FLOW_ITEM not in eastmoney_coverage
    assert not sina_coverage.issubset(eastmoney_coverage)
    assert not eastmoney_coverage.issubset(sina_coverage)
    assert CASH_FLOW_ITEM not in json.dumps(
        eastmoney_envelope["normalized"], ensure_ascii=False
    )

    # Adapting the second schema does not overwrite or contaminate the first
    # artifact: replaying the sina payload afterwards is byte-identical.
    sina_replay = source_adapter.adapt_saved_response(
        DOMAIN_FINANCIAL,
        "synthetic-source-sina",
        sina_payload,
        disabled_sources=(),
        **request,
    )
    assert _canonical_bytes(sina_replay) == _canonical_bytes(sina_envelope)

    # Changing exactly one input aspect (one eastmoney indicator value)
    # changes the artifact identity of that response only.
    changed = json.loads(_canonical_bytes(eastmoney_payload))
    changed["body"]["data"]["f183"] = "223456.78"
    changed_envelope = _adapt_with_replay_invariants(
        DOMAIN_FINANCIAL, "synthetic-source-eastmoney", changed, request
    )
    assert (
        changed_envelope["raw_input_sha256"]
        != eastmoney_envelope["raw_input_sha256"]
    )
    assert (
        _canonical_bytes(changed_envelope)
        != _canonical_bytes(eastmoney_envelope)
    )
    assert (
        changed_envelope["normalized"]["indicators"][0]["value"]
        == "223456.78"
    )
