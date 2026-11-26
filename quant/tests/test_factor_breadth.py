import pytest
from factor_cases import run_formula_case
from factor_stage03_expected import EXPECTED


@pytest.mark.parametrize(
    "factor_id",
    [
        "breadth_advance_share",
        "breadth_decline_share",
        "breadth_new_high_count",
        "breadth_new_low_count",
        "breadth_above_ma_share",
        "limit_up_count",
        "limit_down_count",
        "limit_streak_distribution",
        "failed_limit_up_share",
        "market_amount",
        "market_turnover",
        "market_amount_percentile",
        "market_turnover_percentile",
        "index_contribution_gap",
    ],
)
def test_breadth_case(tmp_path, factor_evidence_root, request, factor_id):
    run_formula_case(tmp_path, factor_evidence_root, request.node.nodeid, factor_id, *EXPECTED[factor_id])


def test_tick_rounding_and_certified_ex_reference(tmp_path):
    import json

    from factor_cases import example_query
    from hetu_quant.factors import TeachingSource, compute_factor

    source, document, query = example_query("limit_up_count")
    tables = document["tables"]
    tables["price_limits"] = []
    date = str(query.trade_date)
    tables["trading_rules"] = [
        {
            "code": code,
            "date": date,
            "valid_from": date,
            "limit_pct": 0.1,
            "tick_size": 0.01,
            "limit_reference": 10.005 if code == "A" else 10,
            "available_at": "2024-01-30T09:00:00+08:00",
        }
        for code in "ABC"
    ]
    source = TeachingSource(source.case_id, tmp_path)
    path = tmp_path / f"{source.case_id}.json"
    last = next(row for row in tables["daily_bars"] if (row["code"], row["date"]) == ("A", date))
    last.update(close_raw=11.01, high_raw=11.01)
    path.write_text(json.dumps(document))
    result = compute_factor(None, source, query)
    assert result.rows[0]["value"] == 1
    last.update(close_raw=11.00, high_raw=11.01)
    path.write_text(json.dumps(document))
    result = compute_factor(None, source, query)
    assert result.rows[0]["value"] == 0


def test_index_contribution_uses_prior_weights_and_not_same_day_weights(tmp_path):
    import json

    from factor_cases import example_query
    from hetu_quant.factors import TeachingSource, compute_factor

    source, doc, query = example_query("index_contribution_gap")
    history = doc["tables"]["memberships"]
    for row, weight in zip(history[:], (0.3, 0.7), strict=True):
        history.append(dict(row, valid_from=str(query.trade_date), weight=weight))
    path = tmp_path / f"{source.case_id}.json"
    source = TeachingSource(source.case_id, tmp_path)
    path.write_text(json.dumps(doc))
    result = compute_factor(None, source, query).rows[0]
    assert result["value"] == pytest.approx(0.08)
    assert result["components"]["top"] == pytest.approx(0.06)
    assert result["components"]["rest"] == pytest.approx(-0.02)
    assert result["components"]["advance_share"] == 0.5
    history[0]["weight"], history[1]["weight"] = 0.4, 0.6
    path.write_text(json.dumps(doc))
    result = compute_factor(None, source, query).rows[0]
    assert result["components"]["top_codes"] == ["B"]
    assert result["value"] == pytest.approx(-0.07)
