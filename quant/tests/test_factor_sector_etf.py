import pytest
from factor_cases import run_formula_case
from factor_stage03_expected import EXPECTED


@pytest.mark.parametrize(
    "factor_id",
    [
        "sector_profit_growth",
        "sector_revision_up_share",
        "sector_activity_yoy",
        "sector_valuation_percentile",
        "sector_amount_share_change",
        "sector_turnover_percentile",
        "sector_flow_proxy",
        "sector_rotation_score",
        "etf_dual_momentum",
        "etf_assets",
        "etf_premium",
        "etf_tracking_error",
    ],
)
def test_sector_etf_case(tmp_path, factor_evidence_root, request, factor_id):
    run_formula_case(tmp_path, factor_evidence_root, request.node.nodeid, factor_id, *EXPECTED[factor_id])


def test_historical_valuation_rebuilds_prior_cap_at_prior_cutoff(tmp_path):
    import json

    from factor_cases import example_query
    from hetu_quant.factors import TeachingSource, compute_factor

    source, document, query = example_query("sector_valuation_percentile")
    caps = document["tables"]["market_cap"]
    caps[0]["revision_id"] = "original"
    caps.append(
        dict(
            caps[0],
            market_cap_cny=200e8,
            available_at="2024-01-31T15:00:00+08:00",
            revision_id="revised",
            supersedes="original",
        )
    )
    (tmp_path / f"{source.case_id}.json").write_text(json.dumps(document))
    result = compute_factor(None, TeachingSource(source.case_id, tmp_path), query)
    assert result.rows[0]["value"] == 100
    assert result.rows[0]["components"]["values"] == [8, 12]


def test_sector_trend_uses_proxy_or_independently_weighted_members(tmp_path):
    import json
    from dataclasses import replace

    from factor_cases import example_query
    from hetu_quant.factors import TeachingSource, compute_factor

    source, document, query = example_query("relative_strength")
    query = replace(query, subjects=("S",))
    source = TeachingSource(source.case_id, tmp_path)
    path = tmp_path / f"{source.case_id}.json"
    facts = document["tables"]
    last, first = str(query.trade_date), facts["calendar"][0]["date"]
    clock = "2024-01-01T08:00:00+08:00"
    for kind in ("index", "etf"):
        facts["sector_proxies"] = [dict(code="S", kind=kind, symbol="A", valid_from=first, available_at=clock)]
        path.write_text(json.dumps(document))
        result = compute_factor(None, source, query)
        assert result.rows[0]["value"] == pytest.approx(0.1)
        assert result.rows[0]["inputs"][0]["sector_proxy"]["kind"] == kind
    facts["sector_proxies"] = [dict(code="S", kind="weighted", valid_from=first, available_at=clock)]
    facts["memberships"] = [
        dict(code="S", member=code, valid_from=first, weight=weight, available_at=clock)
        for code, weight in (("A", 0.25), ("B", 0.75))
    ]
    facts["membership_coverage"] = [dict(code="S", start=first, end=last, complete=True, available_at=clock)]
    path.write_text(json.dumps(document))
    result = compute_factor(None, source, query)
    assert result.rows[0]["value"] == pytest.approx(0.025)
    facts["memberships"][-1].pop("weight")
    path.write_text(json.dumps(document))
    result = compute_factor(None, source, query)
    assert result.status == "unavailable" and result.gaps[0]["field"] == "weight"


def test_etf_assets_from_same_day_shares_and_nav(tmp_path):
    import json

    from factor_cases import example_query
    from hetu_quant.factors import TeachingSource, compute_factor

    source, doc, query = example_query("etf_assets")
    tables = doc["tables"]
    tables["etf_nav"][0].pop("net_assets")
    tables["etf_nav"][0]["nav"] = 2.5
    tables["etf_shares"] = [
        dict(
            code="A", date=str(query.trade_date), shares=2_000_000, unit="份", available_at="2024-01-31T14:00:00+08:00"
        )
    ]
    path = tmp_path / f"{source.case_id}.json"
    source = TeachingSource(source.case_id, tmp_path)
    path.write_text(json.dumps(doc))
    assert compute_factor(None, source, query).rows[0]["value"] == 5_000_000
    tables["etf_shares"][0]["date"] = "2024-01-30"
    path.write_text(json.dumps(doc))
    result = compute_factor(None, source, query)
    assert result.status == "unavailable"
    assert result.gaps[0]["field"] == "etf_shares"
