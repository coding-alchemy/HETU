import pytest
from factor_cases import run_formula_case
from factor_stage03_expected import EXPECTED


@pytest.mark.parametrize(
    "factor_id",
    [
        "index_rebalance_change",
        "unlock_supply_pressure",
        "unlock_holder_mix",
        "corporate_event_type",
        "event_prior_success_rate",
        "event_value_impact_proxy",
    ],
)
def test_supply_event_case(tmp_path, factor_evidence_root, request, factor_id):
    run_formula_case(tmp_path, factor_evidence_root, request.node.nodeid, factor_id, *EXPECTED[factor_id])


def test_unlock_postponement_applies_only_when_public(tmp_path):
    import json

    from factor_cases import example_query
    from hetu_quant.factors import TeachingSource, compute_factor

    source, doc, query = example_query("unlock_supply_pressure")
    tables = doc["tables"]
    original = tables["events"][0]
    original["revision_id"] = "published"
    revision = dict(
        original,
        revision_id="postponed",
        supersedes="published",
        effective_date="2024-03-01",
        available_at="2024-01-31T18:00:00+08:00",
    )
    tables["events"].append(revision)
    tables["event_coverage"] = [
        dict(code="A", kind="unlock", start="2024-01-31", end="2024-02-10", available_at="2024-01-31T09:00:00+08:00")
    ]
    path = tmp_path / f"{source.case_id}.json"
    source = TeachingSource(source.case_id, tmp_path)
    path.write_text(json.dumps(doc))
    assert compute_factor(None, source, query).rows[0]["value"] == 0.1
    revision["available_at"] = "2024-01-31T14:00:00+08:00"
    path.write_text(json.dumps(doc))
    result = compute_factor(None, source, query)
    assert result.rows[0]["value"] == 0
    assert result.status == "available"
