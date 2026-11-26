import pytest
from factor_cases import run_formula_case
from factor_stage02_expected import EXPECTED


@pytest.mark.parametrize(
    "factor_id",
    [
        "sma",
        "sma_filter",
        "sma_cross",
        "absolute_momentum",
        "price_momentum",
        "risk_adjusted_momentum",
        "short_reversal",
        "price_deviation_z",
        "donchian_breakout",
        "relative_strength",
    ],
)
def test_price_case(tmp_path, factor_evidence_root, request, factor_id):
    run_formula_case(tmp_path, factor_evidence_root, request.node.nodeid, factor_id, *EXPECTED[factor_id])


def test_sma_does_not_shift_when_an_interior_session_loses_calendar_proof(tmp_path):
    import json

    from factor_cases import example_query
    from hetu_quant.factors import TeachingSource, compute_factor

    source, document, query = example_query("sma")
    calendar = document["tables"]["calendar"]
    removed = calendar.pop(1)["date"]
    calendar.insert(0, dict(calendar[0], date="2024-01-26"))
    bars = document["tables"]["daily_bars"]
    bars.insert(0, dict(bars[0], date="2024-01-26", close=2, close_raw=2))
    (tmp_path / f"{source.case_id}.json").write_text(json.dumps(document))
    result = compute_factor(None, TeachingSource(source.case_id, tmp_path), query)
    assert result.status == "unavailable"
    assert (result.gaps[0]["field"], result.gaps[0]["period"]) == ("calendar", removed)
