import pytest
from factor_cases import run_formula_case
from factor_stage03_expected import EXPECTED


@pytest.mark.parametrize(
    "factor_id",
    [
        "grid_linear_level",
        "grid_geometric_level",
        "average_true_range",
        "atr_position_size",
        "vol_target_scale",
        "positive_score_vol_weight",
        "risk_budget_vol_weight",
        "trend_vol_weight",
        "rolling_return_correlation",
    ],
)
def test_risk_case(tmp_path, factor_evidence_root, request, factor_id):
    run_formula_case(tmp_path, factor_evidence_root, request.node.nodeid, factor_id, *EXPECTED[factor_id])
