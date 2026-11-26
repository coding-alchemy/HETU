import pytest
from factor_cases import run_formula_case
from factor_stage02_expected import EXPECTED


@pytest.mark.parametrize(
    "factor_id",
    [
        "momentum_rank",
        "robust_cross_section_z",
        "weighted_factor_score",
        "cross_section_clip",
        "neutralized_score",
    ],
)
def test_cross_section_case(tmp_path, factor_evidence_root, request, factor_id):
    run_formula_case(tmp_path, factor_evidence_root, request.node.nodeid, factor_id, *EXPECTED[factor_id])


def test_weighted_factor_score_reports_benchmark_exposures_and_missing_weight(tmp_path):
    import json
    from dataclasses import replace
    from math import exp

    from factor_cases import example_query
    from hetu_quant.factors import TeachingSource, compute_factor

    source, doc, query = example_query("weighted_factor_score")
    tables = doc["tables"]
    t = str(query.trade_date)
    days = ("2024-01-26", "2024-01-29", "2024-01-30", t)
    tables["calendar"] = [dict(tables["calendar"][-1], date=day) for day in days]
    tables["daily_bars"] = []
    for code, shifts in dict(
        A=(-0.06, 0.02, 0.1), B=(0.06, 0.18, 0.3), C=(0.02, 0.02, 0.02), INDEX=(-0.1, 0, 0.1)
    ).items():
        level = 100.0
        for i, day in enumerate(days):
            if i:
                level *= 1 + shifts[i - 1]
            tables["daily_bars"].append(
                dict(
                    code=code,
                    date=day,
                    close=level,
                    close_raw=level,
                    volume_shares={"A": 1, "B": 2, "C": 3, "INDEX": 0}[code],
                    available_at=f"{day}T15:00:00+08:00",
                )
            )
    now = "2024-01-01T10:00:00+08:00"
    tables["memberships"] += [
        dict(code="REFERENCE", member=code, weight=weight, valid_from=t, available_at=now)
        for code, weight in (("A", 0.25), ("B", 0.75))
    ]
    tables["membership_coverage"].append(dict(code="REFERENCE", start=t, end=t, complete=True, available_at=now))
    tables["classifications"] = [
        dict(code=code, industry=industry, valid_from=t, available_at=now)
        for code, industry in zip("ABC", ("I", "II", "II"), strict=True)
    ]
    tables["market_cap"] = [
        dict(code=code, date=t, market_cap_cny=exp(n), available_at=now)
        for code, n in zip("ABC", (1, 2, 1), strict=True)
    ]
    path = tmp_path / f"{source.case_id}.json"
    path.write_text(json.dumps(doc))
    source = TeachingSource(source.case_id, tmp_path)
    query = replace(query, parameters={"exposure_benchmark": "REFERENCE", "benchmark": "INDEX", "beta_window": 3})
    result = compute_factor(None, source, query)
    evidence = result.rows[0]["components"]["benchmark_exposure"]
    assert evidence["difference"]["industry"] == {"I": 0.75, "II": -0.75}
    assert evidence["difference"]["size"] == pytest.approx(-0.75)
    assert evidence["difference"]["beta"] == pytest.approx(-0.3)
    assert evidence["difference"]["style"] == pytest.approx(-0.15)
    tables["memberships"][-1].pop("weight")
    path.write_text(json.dumps(doc))
    result = compute_factor(None, source, query)
    assert result.status == "partial" and result.gaps[0]["field"] == "weight"
