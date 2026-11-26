"""Numeric and categorical counterexamples distinguished by the approved formulas."""

import json
from dataclasses import replace

import pytest
from factor_assertions import checked
from factor_cases import example_query
from hetu_quant.factors import TeachingSource, compute_factor

# The new input is an original fact at the same cutoff. Expected outputs are hand-calculated.
CHANGES = (
    ("sma_filter", "daily_bars", [("close", 10)] * 3, 0),
    ("sma_cross", "daily_bars", [(2, "close", 1)], -1),
    ("donchian_breakout", "daily_bars", [(2, "close", 11)], 0),
    ("short_reversal", "daily_bars", [(0, "close", 8), (1, "close", 10)], -0.25),
    ("sma", "daily_bars", [(2, "close", 18)], 12),
    ("pair_hedge_beta", "daily_bars", [(4, "close", 500)], 2),
    ("roe", "financial_reports", [(0, "parent_equity", 60)], 0.1),
    ("leverage", "financial_reports", [(0, "interest_debt", 99)], 0.7),
    ("accruals", "financial_reports", [(4, "consolidated_profit", 4.5)], -0.02),
    ("roic", "financial_reports", [("tax_rate", 0.5)] * 5, 0.08),
    ("profit_growth_yoy", "financial_reports", [(1, "parent_net_profit", -2)], 0),
    ("turnover_rate", "daily_bars", [(0, "volume_shares", 0)], 0),
    ("breadth_advance_share", "daily_bars", [(2, "close", 10)], 0.4),
    ("breadth_decline_share", "daily_bars", [(14, "close", 10)], 0),
    ("breadth_new_high_count", "daily_bars", [(2, "close", 10)], 1),
    ("breadth_new_low_count", "daily_bars", [(14, "close", 10)], 0),
    ("breadth_above_ma_share", "daily_bars", [(2, "close", 10)], 0.4),
    ("limit_up_count", "daily_bars", [(2, "close_raw", 10.99)], 0),
    ("limit_down_count", "daily_bars", [(8, "close_raw", 9.01)], 0),
    ("limit_streak_distribution", "daily_bars", [(1, "close_raw", 10.99)], {"1": 1}),
    ("failed_limit_up_share", "daily_bars", [(5, "close_raw", 11)], 0),
    ("market_amount", "daily_bars", [(3, "amount_cny", 0)], 40),
    ("market_turnover", "shares", [(3, "float_shares", 180)], 0.025),
    ("market_amount_percentile", "daily_bars", [(3, "amount_cny", 40)], 50),
    ("market_turnover_percentile", "daily_bars", [(3, "volume_shares", 2)], 0),
    ("sector_flow_proxy", "sector_flows", [(0, "net_flow", 0)], 0),
    ("etf_premium", "etf_nav", [(0, "nav", 2.125)], -0.04),
    ("buyback_progress", "events", [(0, "paid", 0)], 0),
    ("buyback_cancellation_purpose", "events", [(0, "purpose", "用于员工持股")], "其他目的"),
    ("planned_sale_pressure", "events", [(0, "state", "已撤销")], 0),
    ("holder_type", "events", [(0, "holder_type", "董事")], "董事"),
    ("dividend_persistence", "dividend_years", [(1, "cash_distributions", 0)], 1),
    ("ex_distribution_return", "corporate_actions", [(0, "d", 0)], -0.02),
    ("index_rebalance_change", "events", [(0, "new_weight", None), (0, "direction", "纳入")], "纳入"),
    ("corporate_event_type", "events", [(0, "stage", "洽谈中")], "重大合同／洽谈中"),
    ("event_prior_success_rate", "events", [(1, "available_at", "2024-02-01T08:00:00+08:00")], 0.5),
    ("average_true_range", "daily_bars", [(0, "close", 11)], 1.5),
    ("positive_score_vol_weight", "declared_scores", [(0, "score", -0.4)], 0),
)


MULTI_OUTPUT = {
    "sma_cross": {"previous_gap": 0, "current_gap": -0.5},
    "breadth_advance_share": {"count": 2, "denominator": 5},
    "breadth_decline_share": {"count": 0, "denominator": 5},
    "breadth_new_high_count": {"count": 1, "denominator": 5},
    "breadth_new_low_count": {"count": 0, "denominator": 5},
    "breadth_above_ma_share": {"count": 2, "denominator": 5},
    "failed_limit_up_share": {"touched": ["A", "B"], "failed": []},
    "limit_down_count": {"matches": []},
    "limit_up_count": {"matches": []},
    "buyback_progress": {"paid": 0, "plan_amount": 1000000, "basis": "plan_amount_upper", "event_id": "EV1"},
    "ex_distribution_return": {"raw_return": -0.51, "adjusted": -0.02, "ratio": 2, "cash": 0},
    "index_rebalance_change": {"weight_known": False},
    "event_prior_success_rate": {"known": 2, "pending": 2, "success": 1},
    "average_true_range": {"true_ranges": [2, 1], "window": 2},
    "positive_score_vol_weight": {"score": -0.4, "volatility": 0.199},
}


@pytest.mark.parametrize(("factor_id", "table", "changes", "expected"), CHANGES, ids=[v[0] for v in CHANGES])
def test_changed_known_fact(tmp_path, request, factor_evidence_root, factor_id, table, changes, expected):
    source, document, query = example_query(factor_id)
    rows = document["tables"][table]
    for pos, change in enumerate(changes):
        if len(change) == 3:
            index, field, value = change
        else:
            index, (field, value) = pos, change
        rows[index][field] = value
    (tmp_path / f"{source.case_id}.json").write_text(json.dumps(document))
    inputs = {
        "case": source.case_id,
        "table": table,
        "changes": changes,
        "subjects": query.subjects,
        "day": str(query.trade_date),
        "parameters": query.parameters,
    }
    components = MULTI_OUTPUT.get(factor_id, {})
    with checked(
        factor_evidence_root,
        request.node.nodeid,
        factor_id,
        "formula:changed_fact",
        {"value": expected, "components": components},
        inputs=inputs,
        replay=(query, document),
    ) as observed:
        result = compute_factor(None, TeachingSource(source.case_id, tmp_path), query)
        assert result.rows, result.gaps
        row = result.rows[0]
        actual = row["value"]
        observed.update(value=actual, unit=row["unit"], components=row["components"])
        if isinstance(expected, (int, float)):
            assert actual == pytest.approx(expected)
        else:
            assert actual == expected
        for field, answer in components.items():
            value = row["components"][field]
            if type(answer) in (int, float):
                assert value == pytest.approx(answer)
            else:
                assert value == answer


@pytest.mark.parametrize(
    ("factor_id", "parameters", "expected"),
    [
        ("grid_geometric_level", {"level": -1}, 100 / 1.1),
        ("atr_position_size", {"direction": -1}, -250),
        ("absolute_momentum", {"threshold": 0.12}, -0.02),
    ],
)
def test_parameter_changes_use_the_same_formula(factor_id, parameters, expected):
    source, _, query = example_query(factor_id)
    result = compute_factor(None, source, replace(query, parameters=parameters))
    assert result.rows and result.rows[0]["value"] == pytest.approx(expected)
