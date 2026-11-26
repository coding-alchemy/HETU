import json

import pytest
from factor_cases import example_query, local_fact_source, run_formula_case
from factor_stage03_expected import EXPECTED
from hetu_quant.factors import TeachingSource, compute_factor


@pytest.mark.parametrize(
    "factor_id",
    [
        "buyback_plan",
        "buyback_progress",
        "buyback_price_cap",
        "buyback_cancellation_purpose",
        "insider_net_purchase",
        "planned_sale_pressure",
        "holder_type",
        "dividend_yield",
        "dividend_persistence",
        "ex_distribution_return",
    ],
)
def test_shareholder_case(tmp_path, factor_evidence_root, request, factor_id):
    run_formula_case(tmp_path, factor_evidence_root, request.node.nodeid, factor_id, *EXPECTED[factor_id])


@pytest.mark.parametrize("local", [False, True], ids=["teaching", "licensed-local"])
@pytest.mark.parametrize(
    ("amount", "currency", "unit", "gap_field"),
    [(0.2, "USD", "USD/股", "currency"), (20, "CNY", "分/股", None)],
    ids=["foreign-cash", "cash-in-fen"],
)
def test_dividend_yield_cash_denomination(tmp_path, local, amount, currency, unit, gap_field):
    sample, document, query = example_query("dividend_yield")
    tables = document["tables"]
    tables["daily_bars"][0].update(currency="CNY", unit="元/股")
    tables["corporate_actions"][1].update(currency="CNY", unit="元/股")
    tables["corporate_actions"][0].update(d=amount, currency=currency, unit=unit)
    if local:
        store, source = local_fact_source(tmp_path / "local", tables)
    else:
        (tmp_path / f"{sample.case_id}.json").write_text(json.dumps(document), encoding="utf-8")
        store, source = None, TeachingSource(sample.case_id, tmp_path)

    result = compute_factor(store, source, query)
    if gap_field:
        assert result.status == "unavailable"
        assert not result.rows
        gap = result.gaps[0]
        assert (gap["subject"], gap["event"], gap["period"], gap["field"]) == ("A", "D1", "2023-08-01", gap_field)
        inputs = gap["evidence"]
    else:
        assert result.status == "available"
        assert not result.gaps
        row = result.rows[0]
        assert row["value"] == pytest.approx(0.05)
        assert row["components"]["cash"] == pytest.approx(0.5)
        inputs = row["inputs"]
    original = next(row for row in inputs if row.get("event_id") == "D1")
    assert (original["d"], original["currency"], original["unit"]) == (amount, currency, unit)
    assert original["source_ref"] == "合成公开事项原件"
