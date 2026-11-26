import pytest
from factor_cases import run_formula_case
from factor_stage02_expected import EXPECTED


@pytest.mark.parametrize(
    "factor_id",
    [
        "realized_volatility",
        "market_beta",
        "residual_volatility",
        "max_drawdown",
        "low_volatility_36m",
        "total_market_cap",
        "free_float_market_cap",
        "turnover_rate",
        "amihud_illiquidity",
        "traded_amount",
    ],
)
def test_stock_risk_case(tmp_path, factor_evidence_root, request, factor_id):
    run_formula_case(tmp_path, factor_evidence_root, request.node.nodeid, factor_id, *EXPECTED[factor_id])


def test_raw_and_continuous_volatility_use_same_split_facts(tmp_path):
    import json
    from dataclasses import replace

    from factor_cases import example_query
    from hetu_quant.factors import TeachingSource, compute_factor

    source, document, query = example_query("realized_volatility")
    tables = document["tables"]
    for row, price in zip(tables["daily_bars"], (10, 5, 5), strict=True):
        row.pop("close")
        row["close_raw"] = price
    split_day = tables["daily_bars"][1]["date"]
    tables["corporate_actions"] = [
        dict(code="A", ex_date=split_day, m=2, d=0, available_at=f"{split_day}T09:00:00+08:00")
    ]
    tables["action_coverage"] = [
        dict(
            code="A",
            start=tables["calendar"][0]["date"],
            end=str(query.trade_date),
            available_at="2024-01-31T12:00:00+08:00",
        )
    ]
    (tmp_path / f"{source.case_id}.json").write_text(json.dumps(document))
    source = TeachingSource(source.case_id, tmp_path)
    assert compute_factor(None, source, query).rows[0]["value"] == 0
    raw_request = replace(query, parameters={"basis": "raw"})
    assert compute_factor(None, source, raw_request).rows[0]["value"] == pytest.approx(0.5 * (126**0.5))


def test_low_volatility_does_not_drop_an_undated_dividend(tmp_path):
    import json

    from factor_cases import example_query
    from hetu_quant.factors import TeachingSource, compute_factor

    source, document, query = example_query("low_volatility_36m")
    source = TeachingSource(source.case_id, tmp_path)
    path = tmp_path / f"{source.case_id}.json"
    document["tables"]["corporate_actions"].append(
        dict(code="A", m=1, d=1, ex_date="待定", available_at="2024-01-30T09:00:00+08:00", source_ref="合成分红公告")
    )
    path.write_text(json.dumps(document))
    result = compute_factor(None, source, query)
    assert result.status == "unavailable"
    assert result.gaps[0]["field"] == "ex_date"
    document["tables"]["corporate_actions"][0]["earliest_ex_date"] = "2024-02-02"
    path.write_text(json.dumps(document))
    assert compute_factor(None, source, query).rows[0]["value"] == pytest.approx(0.01 * (36 / 35) ** 0.5)
