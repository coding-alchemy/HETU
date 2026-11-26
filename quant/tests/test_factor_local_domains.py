"""Read separately licensed domain facts from a published local snapshot."""

import pytest
from factor_cases import example_query, local_fact_source
from hetu_quant.factors import compute_factor


@pytest.mark.parametrize(
    ("factor_id", "table", "field", "expected"),
    [
        ("sector_flow_proxy", "sector_flows", "net_flow", -0.05),
        ("buyback_progress", "events", "paid", 0.2),
        ("unlock_supply_pressure", "events", "prior_float", 0.1),
        ("earnings_revision", "forecasts", "forecast_net_profit", 1 / 60),
        ("sector_profit_growth", "memberships", "member", 0.3),
        ("book_to_price", "financial_reports", "parent_equity", 0.30),
    ],
)
def test_selected_local_domain_facts(tmp_path, factor_id, table, field, expected):
    _, document, query = example_query(factor_id)
    tables = document["tables"]
    store, ref = local_fact_source(tmp_path / "allowed", tables)
    result = compute_factor(store, ref, query)
    assert result.rows, result.gaps
    assert result.rows[0]["value"] == pytest.approx(expected)
    assert any(item.get("evidence_ref") == "factor-fields.json" for item in result.rows[0]["inputs"])
    for row in tables[table]:
        row.pop(field)
    store, ref = local_fact_source(tmp_path / "missing", tables)
    missing = compute_factor(store, ref, query)
    assert missing.status == "unavailable"
    assert not missing.rows
    assert any(gap["field"] == field or gap["field"] == table for gap in missing.gaps), missing.gaps


def test_paid_supplementary_original_cannot_use_out_of_grant_code_or_period(tmp_path):
    import json
    from dataclasses import replace

    from hetu_quant.data import DataStore

    _, document, query = example_query("earnings_yield")
    tables = document["tables"]
    for rows in tables.values():
        for row in rows:
            row["code"] = "A.SH"
            row["date"] = str(query.trade_date)
            if row.get("period") == "2023Q4":
                row["period"] = "2023-12-31"
    path = tmp_path / "source"
    original, _ = local_fact_source(path, tables)
    grant_path = original.root / "data-entitlements.json"
    grant = dict(
        product_id="factor_input",
        codes=["A.SH"],
        markets=["SH"],
        start="2023-01-01",
        end="2024-12-31",
        approved_scope_ref="合成用途",
        license_ref="合成许可",
        allowed_operations=["read", "import"],
        allowed_uses=["factor_calculation"],
        acquire_until="2099-12-31",
        retained_use=True,
    )
    grant_path.write_text(json.dumps({"grants": [grant]}))
    config = path / "store" / "data-products.json"
    content = json.loads(config.read_text())
    content["products"]["factor_input"]["fee_class"] = "paid"
    config.write_text(json.dumps(content))
    manifest = path / "manifest.json"
    content = json.loads(manifest.read_text())
    content["mode"] = "paid"
    content["items"][0]["source"]["fee_class"] = "paid"
    manifest.write_text(json.dumps(content))
    store = DataStore(original.root)
    ref = store.import_files(manifest, (path / "knowledge_evidence.csv",))
    query = replace(query, subjects=("A.SH",))
    assert compute_factor(store, ref, query).rows[0]["value"] == pytest.approx(0.06)
    for scope in ({"codes": ["B.SH"]}, {"start": "2024-02-01"}):
        grant_path.write_text(json.dumps({"grants": [dict(grant, **scope)]}))
        result = compute_factor(store, ref, query)
        assert result.status == "unavailable" and not result.rows
