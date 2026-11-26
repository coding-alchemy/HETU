import pytest
from factor_cases import run_formula_case
from factor_stage02_expected import EXPECTED


@pytest.mark.parametrize(
    "factor_id",
    [
        "pair_hedge_beta",
        "pair_spread",
        "pair_spread_z",
        "pair_extreme_spread",
        "pair_cointegration",
        "pair_half_life",
        "pair_beta_drift",
    ],
)
def test_pair_case(tmp_path, factor_evidence_root, request, factor_id):
    run_formula_case(tmp_path, factor_evidence_root, request.node.nodeid, factor_id, *EXPECTED[factor_id])


def test_engle_granger_preserves_fixed_lag_and_independent_statistics():
    from dataclasses import replace

    from factor_cases import example_query
    from hetu_quant.factors import compute_factor

    source, _, query = example_query("pair_cointegration")
    lag_zero = compute_factor(None, source, query).rows[0]
    assert lag_zero["components"]["statistic"] == pytest.approx(-3.9981510406, abs=1e-8)
    revised = replace(query, parameters={"lag": 1})
    lag_one = compute_factor(None, source, revised).rows[0]
    assert lag_one["components"]["lag"] == 1
    assert lag_one["components"]["statistic"] == pytest.approx(-2.6971590465, abs=1e-8)
    assert lag_one["value"] == pytest.approx(0.20060210296, abs=1e-8)
