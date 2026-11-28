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


def _paid_supplementary_ep(tmp_path, report_period):
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
                row["period"] = report_period
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
    return store, ref, query, grant_path, grant


@pytest.mark.parametrize("report_period", ["2023-12-31", "2023Q4", "unknown-period"])
def test_paid_supplementary_original_cannot_use_out_of_grant_code_or_period(
    tmp_path, monkeypatch, report_period
):
    import json

    store, ref, query, grant_path, grant = _paid_supplementary_ep(tmp_path, report_period)
    initial = compute_factor(store, ref, query)
    if report_period == "unknown-period":
        assert not initial.rows and any(gap["field"] == "net_profit_ex" for gap in initial.gaps)
        print("unknown financial period: no value; net_profit_ex unavailable")
        return
    assert initial.rows[0]["value"] == pytest.approx(0.06)
    from hetu_quant.factors.workbench_inputs import visible_evidence
    from hetu_quant.ui import factor_page
    from test_factor_workbench import open_factors, run_trial

    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
    formula = factor_page._compute_trial
    calls = []

    def compute(trial, root):
        calls.append(trial)
        return formula(trial, root)

    monkeypatch.setattr(factor_page, "_compute_trial", compute)
    page.selectbox(key="factor_id").set_value("earnings_yield").run()
    page.selectbox(key="factor_source_earnings_yield").set_value(ref.snapshot_id).run()
    page.text_input(key="factor_subjects_earnings_yield").set_value("A.SH").run()
    run_trial(page)
    attempt = next(iter(page.session_state["factor_attempts"].values()))
    assert page.dataframe[0].value["读数"].tolist() == [pytest.approx(0.06)]
    assert any(row.get("period") == report_period for row in attempt.result.rows[0]["inputs"])
    page.button(key="factor_evidence_row_0").click().run()
    assert any("net_profit_ex" in section.value for section in page.json)
    grant_path.write_text(json.dumps({"grants": [dict(grant, start="2024-01-01")]}))
    direct = compute_factor(store, ref, query)
    assert not direct.rows and any(gap["field"] == "net_profit_ex" for gap in direct.gaps)
    page.run()
    assert any("当前许可不足" in message.value for message in page.info)
    assert not any("net_profit_ex" in section.value for section in page.json)
    assert not any(item.label == "下载该项获准证据" for item in page.get("download_button"))
    assert not visible_evidence(
        store, attempt.trial, attempt.result, row_index=0, operation="download"
    )["permitted"]
    page.button(key="factor_return_to_draft").click().run()
    assert not any("读数" in frame.value for frame in page.dataframe)
    grant_path.write_text(json.dumps({"grants": [grant]}))
    page.run()
    assert page.dataframe[0].value["读数"].tolist() == [pytest.approx(0.06)]
    assert len(calls) == 1
    print(f"paid {report_period}: original 6%, revoked current and historical, restored 6%; one formula call")
    for scope in ({"codes": ["B.SH"]}, {"start": "2024-02-01"}):
        grant_path.write_text(json.dumps({"grants": [dict(grant, **scope)]}))
        result = compute_factor(store, ref, query)
        assert result.status == "unavailable" and not result.rows


@pytest.mark.parametrize("period", ["2023Q4", "unknown-period"])
def test_browser_reads_paid_registrations_only_inside_business_grant(tmp_path, monkeypatch, period):
    import json
    from datetime import date

    from test_factor_workbench import open_factors, run_trial
    from test_factor_workbench_review import _browse, _query

    store, ref, query, grant_path, grant = _paid_supplementary_ep(tmp_path, period)
    result = compute_factor(store, ref, query)
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
    start, end = date(2024, 1, 1), date(2024, 2, 1)

    def browse(code="A.SH"):
        _browse(page, ref, "knowledge_evidence", code=code, day=start)
        page.date_input(key="factor_browser_dates").set_value((start, end))
        _query(page)
        rows = page.dataframe[0].value if page.dataframe else None
        gaps = [gap["reason"] for section in page.json for gap in json.loads(section.value)]
        print("paid registrations", code, [] if rows is None else
              rows[["product_id", "record_key", "published_at"]].to_dict("records"), gaps)
        return rows, gaps

    rows, gaps = browse()
    if period == "unknown-period":
        assert not result.rows and any(gap["field"] == "net_profit_ex" for gap in result.gaps)
        assert rows is not None and rows["record_key"].tolist() == ["market_cap|A.SH|0", "market_cap|A.SH|1"]
        assert any("业务期间" in gap for gap in gaps)
        return
    assert result.rows[0]["value"] == pytest.approx(0.06)
    assert rows is not None and len(rows) == 4
    assert set(rows["product_id"]) == {"factor_input"}
    assert sorted(rows["record_key"]) == [
        "financial_reports|A.SH|2023Q4-扣非利润", "financial_reports|A.SH|2023Q4-扣非利润",
        "market_cap|A.SH|0", "market_cap|A.SH|1",
    ]
    assert "2024-01-31T18:00:00+08:00" in rows["published_at"].tolist()
    page.sidebar.radio(key="page_nav").set_value("因子库").run()
    page.selectbox(key="factor_id").set_value("earnings_yield").run()
    page.selectbox(key="factor_source_earnings_yield").set_value(ref.snapshot_id).run()
    page.text_input(key="factor_subjects_earnings_yield").set_value("A.SH").run()
    run_trial(page)
    page.button(key="factor_evidence_row_0").click().run()
    assert all("2024-01-31T18:00:00+08:00" not in section.value for section in page.json)
    page.button(key="factor_return_to_draft").click().run()
    config_path = store.root / "data-products.json"
    config = json.loads(config_path.read_text())
    permission = config["products"]["factor_input"]["permission"]
    permission["allowed_fields"] = ["table_name", "record_key", "published_at", "product_id", "market_cap_cny"]
    config_path.write_text(json.dumps(config))
    rows, gaps = browse()
    assert rows is not None and set(rows["record_key"]) == {"market_cap|A.SH|0", "market_cap|A.SH|1"}
    assert "凭据业务字段未获准" in gaps
    permission.pop("allowed_fields")
    config_path.write_text(json.dumps(config))
    grant_path.write_text(json.dumps({"grants": [dict(grant, start="2024-01-01")]}))
    rows, gaps = browse()
    assert rows is not None and rows["record_key"].tolist() == ["market_cap|A.SH|0", "market_cap|A.SH|1"]
    assert any("业务期间" in gap for gap in gaps)
    rows, gaps = browse("B.SH")
    assert rows is None and any("对象" in gap for gap in gaps)


def test_paid_registration_publication_day_does_not_limit_business_grant(tmp_path, monkeypatch):
    import json
    from datetime import date

    from test_factor_workbench import open_factors
    from test_factor_workbench_review import _browse, _query

    store, ref, _, grant_path, grant = _paid_supplementary_ep(tmp_path, "2023Q4")
    business, published = date(2024, 1, 31), date(2024, 2, 1)
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))

    def readback(day, left, right):
        grant_path.write_text(json.dumps({"grants": [dict(grant, start=str(left), end=str(right))]}))
        _browse(page, ref, "knowledge_evidence", day=day)
        _query(page)
        rows = page.dataframe[0].value if page.dataframe else None
        records = [] if rows is None else rows[["record_key", "published_at"]].to_dict("records")
        print("registration business scope", left, right, "query", day, "records", records)
        return records

    original = [{"record_key": "market_cap|A.SH|1", "published_at": "2024-02-01T14:50:00+08:00"}]
    assert readback(published, grant["start"], grant["end"]) == original
    actual = readback(published, business, business)
    assert readback(published, published, published) == []
    assert readback(business, business, business) == []
    assert actual == original
