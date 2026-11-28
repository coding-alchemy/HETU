"""Focused end-to-end witnesses for the workbench review."""

import json
from dataclasses import replace
from datetime import date

import pyarrow as pa
import pytest
from hetu_data.browse import BrowseSelection, browse_page
from hetu_data.library import publish_library

from backtest_cases import build_case_store
from factor_cases import example_query, local_fact_source, local_volume_source
from hetu_quant.factors import close_cutoff, compute_factor
from hetu_quant.factors.core import MissingFact
from hetu_quant.factors.workbench import build_trial
from hetu_quant.factors.workbench_inputs import preview_inputs
from hetu_quant.ui.factor_navigation import _scopes
from test_factor_workbench import open_factors, run_trial

DAY = date(2024, 4, 1)


def _library(store, *sources):
    return publish_library(
        store,
        asset_snapshots=tuple(
            dict(
                root=str(owner.root),
                snapshot_id=ref.snapshot_id,
                tables=sorted({entry["table"] for entry in owner.load_manifest(ref)["table_files"]}),
            )
            for owner, ref in sources
        ),
    )


def _browse(page, ref, table, *, code="A.SH", day=DAY):
    page.sidebar.radio(key="page_nav").set_value("数据")
    page.session_state["factor_data_mode"] = "因子输入"
    page.run()
    label = f"快照 · {ref.snapshot_id}" if hasattr(ref, "snapshot_id") else f"库 · {ref.library_id}"
    page.selectbox(key="factor_browser_source").set_value(label).run()
    page.selectbox(key="factor_browser_table").set_value(table).run()
    page.text_input(key="factor_browser_codes").set_value(code)
    page.date_input(key="factor_browser_dates").set_value((day, day))


def _query(page):
    page.button(key="factor_browser_query_submit").click().run()
    assert not page.exception


def _factor(page, factor_id):
    page.selectbox(key="factor_id").set_value(factor_id).run()


def _revoke(owner, product):
    path = owner.root / "data-products.json"
    cfg = json.loads(path.read_text())
    cfg["products"][product]["permission"]["allowed_uses"] = ["daily_archive"]
    path.write_text(json.dumps(cfg))


def test_paid_action_axes_keep_implementation_grant(tmp_path, monkeypatch):
    fact = dict(
        code="A.SH",
        available_at="2024-04-01T14:00:00+08:00",
        action_type="cash_dividend",
        same_share_basis=True,
        d=0.2,
        source_ref="临时公告",
    )
    rows = [dict(fact, ex_date="2024-04-01", coordinate="one"), dict(fact, ex_date="2024-04-02", coordinate="two")]
    path = tmp_path / "actions"
    store, _ = local_fact_source(path, {}, native_actions=rows)
    grant_path = store.root / "data-entitlements.json"
    grant = dict(
        product_id="factor_actions",
        codes=["A.SH"],
        markets=["SH"],
        start="2024-04-01",
        end="2024-04-02",
        approved_scope_ref="合成",
        license_ref="合成",
        allowed_operations=["read", "import"],
        allowed_uses=["factor_calculation"],
        acquire_until="2099-12-31",
        retained_use=True,
    )
    grant_path.write_text(json.dumps({"grants": [grant]}))
    config_path = store.root / "data-products.json"
    cfg = json.loads(config_path.read_text())
    cfg["products"]["factor_actions"]["fee_class"] = "paid"
    config_path.write_text(json.dumps(cfg))
    manifest_path = path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["mode"] = "paid"
    for item in manifest["items"]:
        if item["product_id"] == "factor_actions":
            item["source"]["fee_class"] = "paid"
    manifest_path.write_text(json.dumps(manifest))
    from hetu_data import DataStore

    store = DataStore(store.root)
    ref = store.import_files(manifest_path, tuple(path / name for item in manifest["items"] for name in item["files"]))
    grant["end"] = str(DAY)
    grant_path.write_text(json.dumps({"grants": [grant]}))
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
    _browse(page, ref, "corporate_actions")
    for axis in ("ex_date", "available_at"):
        page.selectbox(key="factor_browser_axis_corporate_actions").set_value(axis)
        _query(page)
        assert page.dataframe[0].value["ex_date"].tolist() == [str(DAY)]
        assert any("1 条获准记录" in item.value for item in page.caption)
    page.button(key="factor_browser_to_trial").click().run()
    context = page.session_state["factor_browse_context_up_amount_share"]
    assert context["axis"] == "available_at" and context["coordinate"] == fact["available_at"]
    assert page.text_input(key="factor_subjects_up_amount_share").value == "A.SH"


def test_revoked_member_masks_every_derived_rank(tmp_path, monkeypatch):
    _, document, query = example_query("momentum_rank")
    document["tables"]["calendar"][:] = [dict(row, source_ref="合成日历") for row in document["tables"]["calendar"]]
    other = [row for row in document["tables"]["daily_bars"] if row["code"] == "C"]
    document["tables"]["daily_bars"] = [row for row in document["tables"]["daily_bars"] if row["code"] != "C"]
    first, a = local_fact_source(tmp_path / "first", document["tables"])
    second, c = local_fact_source(tmp_path / "second", {"daily_bars": other})
    lib = _library(first, (first, a), (second, c))
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(first.root))
    _factor(page, "momentum_rank")
    page.selectbox(key="factor_source_momentum_rank").set_value(lib.library_id).run()
    run_trial(page)
    assert page.dataframe[0].value["读数"].tolist() == [25, 25, 100]
    attempt = next(iter(page.session_state["factor_attempts"].values()))
    _revoke(second, "factor_input")
    page.button(key="factor_evidence_row_0").click().run()
    assert any("许可不足" in item.value for item in page.info)
    page.button(key="factor_return_to_draft").click().run()
    assert all("读数" not in table.value for table in page.dataframe)
    assert [r["value"] for r in attempt.result.rows] == [25, 25, 100]
    assert not compute_factor(first, lib, query).rows


def _names(path, *codes):
    return build_case_store(
        path,
        dict(
            csvs={
                "securities": "code,name,ipo_date,out_date,status\n"
                + "".join(f"{code},样本名称,2020-01-01,,1\n" for code in codes)
            },
            table_products={"securities": ("local_names", "catalog")},
            root_config=True,
            permissions={"securities": {"allowed_uses": ["factor_calculation"]}},
            obtained_at="2024-04-01T18:00:00+08:00",
        ),
    )


@pytest.mark.parametrize("browser", [False, True], ids=["factor", "data-browser"])
def test_object_candidates_are_live_on_rerender_and_apply(tmp_path, monkeypatch, browser):
    first, a = _names(tmp_path / "names_a", "SYNTH.SH")
    second, b = _names(tmp_path / "names_b", "KEEP.SH")
    lib = _library(first, (first, a), (second, b))
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(first.root))
    if browser:
        _browse(page, lib, "securities")
        page.text_input(key="factor_browser_search").set_value("样本").run()
        button, pick, target = "factor_browser_apply_candidate", "factor_browser_match", "factor_browser_codes"
        page.button(key="factor_browser_find").click().run()
    else:
        _factor(page, "traded_amount")
        page.selectbox(key="factor_source_traded_amount").set_value(lib.library_id).run()
        page.text_input(key="factor_object_search_traded_amount").set_value("样本").run()
        button = "factor_apply_object_traded_amount"
        pick, target = "factor_pick_traded_amount", "factor_subjects_traded_amount"
        page.button(key="factor_find_object_traded_amount").click().run()
    picker = page.selectbox(key=pick)
    assert set(picker.options) == {"样本名称 · SYNTH.SH · local_names", "样本名称 · KEEP.SH · local_names"}
    picker.set_value(("SYNTH.SH", "样本名称", "local_names")).run()
    prior = page.text_input(key=target).value
    _revoke(first, "local_names")
    page.button(key=button).click().run()
    assert page.text_input(key=target).value == prior
    assert page.selectbox(key=pick).options == ["样本名称 · KEEP.SH · local_names"]
    page.button(key=button).click().run()
    assert page.text_input(key=target).value == "KEEP.SH"


def test_known_parameters_and_constraints_use_the_same_entrypoint(tmp_path, monkeypatch):
    for factor in ("buyback_plan", "buyback_progress", "buyback_price_cap", "buyback_cancellation_purpose"):
        source, _, request = example_query(factor, params={"event_id": "EV1"})
        result = compute_factor(None, source, request)
        assert result.rows, result.gaps
        if factor == "buyback_progress":
            assert result.rows[0]["value"] == pytest.approx(0.20)
        invalid = replace(request, parameters={"not_registered": 1})
        assert compute_factor(None, source, invalid).gaps[0]["field"] == "not_registered"
    for factor, params, field in (
        ("realized_volatility", {"window": 1}, "window"),
        ("price_momentum", {"months": 3, "skip": 1}, "skip"),
        (
            "weighted_factor_score",
            {"components": {"realized_volatility": {"weight": 1, "direction": 1, "parameters": {"window": 1}}}},
            "components.realized_volatility.parameters.window",
        ),
    ):
        source, _, request = example_query(factor, params=params)
        with pytest.raises(MissingFact) as error:
            build_trial(source, request)
        assert error.value.field == field
        page = open_factors(monkeypatch, tmp_path / factor)
        _factor(page, factor)
        page.text_area(key=f"factor_parameters_{factor}_默认").set_value(json.dumps(params)).run()
        assert any(field in message.value for message in page.error)
        assert not [button for button in page.button if button.key == "factor_compute"]
        assert not page.session_state["factor_attempts"]


def test_bring_in_requires_explicit_etf_benchmark(tmp_path, monkeypatch):
    first, bars = local_volume_source(tmp_path / "etf", code="ETF.SH")
    _, example, _ = example_query("etf_tracking_error")
    nav = next(rows for table, rows in example["tables"].items() if "nav" in table)
    dates = {"2024-01-29": "2024-03-28", "2024-01-30": "2024-03-29", "2024-01-31": str(DAY)}
    nav = [
        dict(
            row,
            code=("ETF.SH" if row["code"] != "B" else "B"),
            date=dates[row["date"]],
            available_at=f"{dates[row['date']]}T14:00:00+08:00",
        )
        for row in nav
    ]
    nav.extend(dict(row, code="C") for row in list(nav) if row["code"] == "B")
    names, name_ref = _names(tmp_path / "option_names", "B", "C")
    second, supplemental = local_fact_source(tmp_path / "nav", {"etf_nav": nav})
    lib = _library(first, (first, bars), (second, supplemental), (names, name_ref))
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(first.root))
    _factor(page, "etf_tracking_error")
    _browse(page, lib, "daily_bars", code="ETF.SH")
    _query(page)
    page.button(key="factor_browser_to_trial").click().run()
    assert page.text_input(key="factor_subjects_etf_tracking_error").value == "ETF.SH"
    assert page.date_input(key="factor_date_etf_tracking_error").value == DAY
    assert page.selectbox(key="factor_source_etf_tracking_error").value == lib.library_id
    assert any("请补选基准" in item.value for item in page.error)
    assert not [button for button in page.button if button.key == "factor_compute"]
    page.text_input(key="factor_object_search_etf_tracking_error").set_value("样本").run()
    page.button(key="factor_find_object_etf_tracking_error").click().run()
    picker = page.selectbox(key="factor_pick_etf_tracking_error")
    assert len(picker.options) == 2 and any(" · B · " in label for label in picker.options)
    picker.set_value(("C", "样本名称", "local_names")).run()
    page.checkbox(key="factor_use_benchmark_etf_tracking_error").set_value(True).run()
    page.button(key="factor_apply_object_etf_tracking_error").click().run()
    raw = page.text_area(key="factor_parameters_etf_tracking_error_默认")
    settings = json.loads(raw.value)
    assert settings["benchmark"] == "C"
    assert page.text_input(key="factor_imported_benchmark_etf_tracking_error").value == "C"
    assert any(json.loads(c.value).get("benchmark") == "C" for c in page.code if c.value.startswith("{"))
    raw.set_value(json.dumps(dict(settings, benchmark="B"))).run()
    assert page.text_input(key="factor_imported_benchmark_etf_tracking_error").value == "B"
    assert any(json.loads(c.value).get("benchmark") == "B" for c in page.code if c.value.startswith("{"))
    run_trial(page)
    attempt = next(iter(page.session_state["factor_attempts"].values()))
    assert attempt.trial.request.subjects == ("ETF.SH",)
    assert attempt.trial.request.parameters["benchmark"] == "B"
    assert attempt.result.rows[0]["value"] == pytest.approx(0.02 * (252 / 2) ** 0.5)
    assert [row["code"] for row in attempt.result.rows[0]["inputs"]] == ["ETF.SH"] * 3 + ["B"] * 3
    print("ETF: empty → C via selector → B via JSON; actual legs ETF.SH then B")


@pytest.mark.parametrize("condition", ["supplementary", "missing", "suspended"])
def test_preview_recognizes_daily_supplementary_source(tmp_path, condition):
    source, example, request = example_query("traded_amount")
    facts = example["tables"]
    expected = compute_factor(None, source, request).rows[0]["value"]
    if condition != "supplementary":
        facts["daily_bars"] = [dict(row, code="OTHER") for row in facts["daily_bars"]]
    if condition == "suspended":
        facts["security_status"] = [
            dict(code="A", date=str(request.trade_date), is_suspended=True, available_at="2024-01-31T14:00:00+08:00")
        ]
    store, ref = local_fact_source(tmp_path, facts)
    trial = build_trial(ref, request)
    checks = preview_inputs(store, trial).checks
    messages = [c["reason"] for c in checks if c.get("start") == request.trade_date]
    result = compute_factor(store, ref, request)
    if condition == "supplementary":
        assert "有获准日线补证，字段待核对" in messages
        assert not [check for check in checks if check.get("table") == "daily_bars" and check["state"] == "missing"]
        assert result.rows[0]["value"] == expected == 21
    elif condition == "suspended":
        assert "已证停牌" in messages and "应有交易日日线缺行" not in messages
        assert not result.rows and result.gaps[0]["reason"] == "已证停牌"
    else:
        assert "应有交易日日线缺行" in messages and not result.rows


@pytest.mark.parametrize("currency", ["CNY", "USD"])
def test_browse_hidden_sort_and_original_amount_unit(tmp_path, monkeypatch, currency):
    store, ref = local_volume_source(tmp_path / "volume", code="A.SH", amount_unit=currency)
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
    _factor(page, "traded_amount")
    _browse(page, ref, "daily_bars")
    page.multiselect(key="factor_browser_fields_daily_bars").set_value(["amount_cny"])
    page.selectbox(key="factor_browser_order_daily_bars").set_value("date")
    _query(page)
    visible = page.dataframe[0].value
    assert visible.amount_cny.tolist() == [15_000_000]
    assert visible["成交额币种"].tolist() == [currency]
    assert "date" not in visible
    assert any("实有 1，应有 1" in item.value for item in page.caption)
    page.button(key="factor_browser_to_trial").click().run()
    run_trial(page)
    if currency == "USD":
        assert "currency" in page.dataframe[0].value["field"].tolist()
    else:
        assert page.dataframe[0].value["读数"].tolist() == [15_000_000]


def test_browse_supplementary_uses_actual_registration_subject(tmp_path, monkeypatch):
    store, ref = local_volume_source(tmp_path / "volume", code="A.SH")
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
    _browse(page, ref, "knowledge_evidence", code="ZZZ.SZ")
    page.selectbox(key="factor_browser_market").set_value("SZ")
    _query(page)
    assert not page.dataframe
    page.selectbox(key="factor_browser_market").set_value("SH")
    page.text_input(key="factor_browser_codes").set_value("A.SH")
    _query(page)
    records = page.dataframe[0].value["record_key"].tolist()
    original = next(record for record in records if record.startswith("A.SH|2024-04-01|"))
    page.selectbox(key="factor_browser_row").set_value(records.index(original)).run()
    page.button(key="factor_browser_to_trial").click().run()
    assert page.text_input(key="factor_subjects_up_amount_share").value == "A.SH"
    actual_record = page.session_state["factor_browse_context_up_amount_share"]["record_key"]
    assert actual_record == original
    assert any(actual_record in item.value for item in page.caption)
    assert page.selectbox(key="factor_source_up_amount_share").value == ref.snapshot_id


def test_pagination_uses_sql_and_coverage_is_evidenced(tmp_path, monkeypatch):
    import hetu_data.browse as browse

    store, ref = local_volume_source(tmp_path, code="A.SH")
    original = browse.duckdb.connect
    fetched, statements = [], []

    class Connection:
        def __enter__(self):
            self.conn = original()
            return self

        def __exit__(self, *_):
            self.conn.close()

        def execute(self, sql, args=None):
            statements.append(sql)
            self.result = self.conn.execute(sql, args)
            return self

        def df(self):
            frame = self.result.df()
            fetched.append((len(frame), set(frame.columns)))
            return frame

        def fetchone(self):
            return self.result.fetchone()

    with monkeypatch.context() as probe:
        probe.setattr(browse.duckdb, "connect", Connection)
        selection = BrowseSelection(
            ref, "factor_calculation", "daily_bars", ("amount_cny",), ("A.SH",), date(2023, 12, 1), DAY, sort="date"
        )
        scope = tuple(s for s in _scopes(store, selection) if s["table"] == "daily_bars")
        one = browse_page(store, selection, scopes=scope, page=1, page_size=1)
        two = browse_page(store, selection, scopes=scope, page=2, page_size=1)
    assert one.total == two.total == 61
    assert one.rows.date.tolist() != two.rows.date.tolist()
    assert [n for n, columns in fetched if "amount_cny" in columns] == [1, 1]
    assert sum("LIMIT ? OFFSET ?" in sql for sql in statements) == 2
    assert one.coverage["denominator"] is None and one.coverage["actual"] == 61
    day = replace(selection, start=DAY)
    scopes = tuple(_scopes(store, day))
    assert browse_page(store, day, scopes=scopes, page=1, page_size=1).coverage["denominator"] == 1
    restricted = tuple(dict(s, fields=("amount_cny",)) if s["table"] == "daily_bars" else s for s in scopes)
    denied = browse_page(store, day, scopes=restricted, page=1, page_size=1)
    assert denied.total == 0 and any("排序字段未获准" == gap["reason"] for gap in denied.gaps)


def test_results_and_catalog_report_shapes_and_actual_samples(tmp_path, monkeypatch):
    page = open_factors(monkeypatch, tmp_path)
    _factor(page, "breadth_above_ma_share")
    run_trial(page)
    row = page.dataframe[0].value.iloc[0]
    assert (row["读数"], row["有效样本"], row["命中数"]) == (0.60, 5, 3)
    _factor(page, "momentum_rank")
    run_trial(page)
    frame = page.dataframe[0].value
    assert frame["读数"].dtype.kind in "fi"
    assert frame.sort_values("读数")["读数"].tolist() == [25, 25, 100]
    assert frame["有效样本"].tolist() == [3, 3, 3]
    _factor(page, "earnings_yield")
    run_trial(page)
    row = page.dataframe[0].value.iloc[0]
    assert row["报告期"] == "2023Q4"
    assert row["实际行情／事件日期"] == "2024-01-31"
    assert row["有效样本"] is None
    page.text_input(key="factor_search").set_value("ETF").run()
    view = page.table[0].value
    assert len(view) > 1 and all(view["用途"])
    assert {"适用对象", "主要输出", "单位"}.issubset(view.columns)
    page.selectbox(key="factor_output_kind").set_value("单值").run()
    assert set(page.table[0].value["主要输出"]) <= {"单值", "单值（含日期序列）"}
    assert len(set(page.table[0].value["单位"])) > 1
    page.text_input(key="factor_search").set_value("").run()
    page.selectbox(key="factor_output_kind").set_value("带日期序列").run()
    assert all("含日期序列" in shape for shape in page.table[0].value["主要输出"])
    assert "单值（含日期序列）" in page.table[0].value["主要输出"].tolist()


def test_two_day_amount_mean_uses_two_observations(tmp_path):
    store, ref = local_volume_source(tmp_path, code="A.SH", bars_from="2024-03-29")
    _, _, request = example_query("traded_amount")
    request = replace(
        request, subjects=("A.SH",), trade_date=DAY, cutoff=close_cutoff(DAY), parameters={"mode": "mean", "window": 2}
    )
    trial = build_trial(ref, request)
    coverage = preview_inputs(store, trial)
    assert not [check for check in coverage.checks if check["reason"] == "应有交易日日线缺行"]
    assert [(c["start"], c["end"]) for c in coverage.checks if c["field"] == "行情日期"] == [(date(2024, 3, 29), DAY)]
    result = compute_factor(store, ref, request)
    assert result.rows[0]["value"] == 15_000_000
    assert [row["date"] for row in result.rows[0]["inputs"]] == ["2024-03-29", str(DAY)]


def test_volume_field_revocation_checks_native_read_and_previous_result(tmp_path, monkeypatch):
    from hetu_data import DataStore

    from hetu_quant.factors.workbench_inputs import visible_evidence

    store, ref = local_volume_source(tmp_path / "volume", code="A.SH")
    path = store.root / "data-products.json"
    config = json.loads(path.read_text())
    for product in config["products"].values():
        product["permission"]["allow_export"] = True
    path.write_text(json.dumps(config))
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
    _factor(page, "traded_amount")
    page.selectbox(key="factor_source_traded_amount").set_value(ref.snapshot_id).run()
    page.text_input(key="factor_subjects_traded_amount").set_value("A.SH")
    page.date_input(key="factor_date_traded_amount").set_value(DAY).run()
    run_trial(page)
    attempt = next(iter(page.session_state["factor_attempts"].values()))
    assert page.dataframe[0].value["读数"].tolist() == [15_000_000]
    page.button(key="factor_evidence_row_0").click().run()
    assert any(item.label == "下载该项获准证据" for item in page.get("download_button"))
    _, _, request = example_query("price_momentum")
    price_trial = build_trial(ref, replace(
        request, subjects=("A.SH",), trade_date=DAY, cutoff=close_cutoff(DAY), parameters={"window": 1}
    ))
    price_before = compute_factor(DataStore(store.root), ref, price_trial.request)
    assert price_before.rows
    config["products"]["factor_daily"]["permission"]["allowed_fields"] = ["code", "date", "close_raw"]
    path.write_text(json.dumps(config))
    current = DataStore(store.root)
    original = current.read_range
    columns_read = []

    def track(*args, **kwargs):
        if args[1] == "daily_bars":
            columns_read.extend(kwargs["columns"])
        return original(*args, **kwargs)

    monkeypatch.setattr(current, "read_range", track)
    preview = preview_inputs(current, attempt.trial)
    result = compute_factor(current, ref, attempt.trial.request)
    assert "amount_cny" not in columns_read
    assert any(row["state"] == "denied" and row["field"] == "amount_cny" for row in preview.checks)
    assert not result.rows and any(gap["field"] == "amount_cny" for gap in result.gaps)
    selection = BrowseSelection(ref, "factor_calculation", "daily_bars", ("close_raw",), ("A.SH",), DAY, DAY)
    close = browse_page(current, selection, scopes=tuple(_scopes(current, selection)), page=1, page_size=1)
    assert close.total == 1 and close.rows["close_raw"].notna().all()
    price_now = compute_factor(current, ref, price_trial.request)
    price_old = visible_evidence(current, price_trial, price_before)
    assert price_now.rows and price_old["rows"]
    assert price_now.rows[0]["value"] == price_old["rows"][0]["value"] == price_before.rows[0]["value"]
    page.run()
    assert any("许可不足" in info.value for info in page.info)
    assert not any("15000000" in info.value for info in page.json)
    assert not any(item.label == "下载该项获准证据" for item in page.get("download_button"))
    page.button(key="factor_return_to_draft").click().run()
    assert not any("读数" in frame.value for frame in page.dataframe)
    assert not visible_evidence(
        current, attempt.trial, attempt.result, row_index=0, operation="download"
    )["permitted"]
    assert attempt.result.rows[0]["value"] == 15_000_000
    print(
        f"amount: 15,000,000 prior; read {columns_read}; preview/new/old/details/export denied;"
        f" close {close.rows['close_raw'].iloc[0]}, price momentum {price_now.rows[0]['value']:.6f} preserved"
    )


@pytest.mark.parametrize(("factor", "expected"), [("traded_amount", 15_000_000), ("price_momentum", 0.0)])
def test_independent_volume_keeps_original_calendar_witness(tmp_path, monkeypatch, factor, expected):
    first, a = local_volume_source(tmp_path / "a", code="A.SH")
    second, b = local_volume_source(tmp_path / "b", code="B.SH")
    lib = _library(first, (first, a), (second, b))
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(first.root))
    _factor(page, factor)
    page.selectbox(key=f"factor_source_{factor}").set_value(lib.library_id).run()
    page.text_input(key=f"factor_subjects_{factor}").set_value("A.SH B.SH")
    page.date_input(key=f"factor_date_{factor}").set_value(DAY)
    if factor == "price_momentum":
        page.text_area(key=f"factor_parameters_{factor}_默认").set_value(json.dumps({"window": 1}))
    page.run()
    run_trial(page)
    assert page.dataframe[0].value["读数"].tolist() == [expected, expected]
    attempt = next(iter(page.session_state["factor_attempts"].values()))
    for product in ("factor_daily", "factor_calendar", "factor_proofs"):
        _revoke(second, product)
    direct = compute_factor(first, lib, attempt.trial.request)
    assert [(r["subject"], r["value"]) for r in direct.rows] == [("A.SH", expected)]
    page.run()
    assert page.dataframe[0].value[["对象", "读数"]].values.tolist() == [["A.SH", expected]]
    page.button(key="factor_evidence_row_0").click().run()
    assert any(json.loads(section.value).get("value") == expected for section in page.json)
    assert len(attempt.result.rows) == 2 and len(page.session_state["factor_attempts"]) == 1
    _revoke(first, "factor_calendar")
    page.run()
    assert any("许可不足" in info.value for info in page.info)
    assert not page.json
    print(f"{factor}: revoke B asset → old and new retain A.SH {expected}; revoke last calendar → hidden")


def test_browse_coverage_uses_applicable_market_calendar(tmp_path, monkeypatch):
    def closed(name):
        return build_case_store(tmp_path / name, {
            "csvs": {"calendar": "date,is_trading_day\n2024-04-01,0\n"},
            "table_products": {"calendar": (name, "calendar")},
            "permissions": {"calendar": {"allowed_uses": ["factor_calculation"]}},
            "source_metadata": {"calendar": {"published_at": "2024-04-01T14:00:00+08:00"}},
            "root_config": True,
            "obtained_at": "2024-04-01T14:00:00+08:00",
        })

    first, a = local_volume_source(tmp_path / "bar", code="A.SH")
    sz, sz_ref = closed("cn_sz_archive_calendar")
    general, general_ref = closed("conflicting_calendar")
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(first.root))
    for sources, expected in (
        (((first, a),), "实有 1，应有 1"),
        (((first, a), (sz, sz_ref)), "实有 1，应有 1"),
        (((first, a), (sz, sz_ref), (general, general_ref)), "实有 1，应有数量未知"),
    ):
        ref = _library(first, *sources)
        _browse(page, ref, "daily_bars")
        _query(page)
        assert page.dataframe[0].value["amount_cny"].tolist() == [15_000_000]
        assert any(expected in item.value for item in page.caption)
        _, _, request = example_query("traded_amount")
        trial = build_trial(ref, replace(request, subjects=("A.SH",), trade_date=DAY, cutoff=close_cutoff(DAY)))
        preview = preview_inputs(first, trial)
        result = compute_factor(first, ref, trial.request)
        if expected == "实有 1，应有 1":
            assert not any(check["reason"] == "当日交易属性待证" for check in preview.checks)
            assert [row["value"] for row in result.rows] == [15_000_000]
        else:
            assert any(check["reason"] == "当日交易属性待证" for check in preview.checks)
            assert not result.rows and any(gap["field"] == "calendar" for gap in result.gaps)
        print(expected, [row["value"] for row in result.rows], result.gaps)
    assert any("交易日历缺失或冲突" in item.value for item in page.caption)
    print("A.SH 2024-04-01 1/1 with SH/general open; 1/1 with SZ closed added; unknown with applicable conflict")


@pytest.mark.parametrize(
    ("factor", "value", "series", "measurement", "unit"),
    [
        ("market_amount_percentile", 100, [80, 100], "历史成交额", "人民币元"),
        ("sector_turnover_percentile", 0, [0.10, 0.09], "历史换手率", "比例"),
    ],
)
def test_catalog_history_factors_render_their_production_series(tmp_path, monkeypatch, factor, value,
                                                                series, measurement, unit):
    page = open_factors(monkeypatch, tmp_path)
    page.selectbox(key="factor_output_kind").set_value("带日期序列").run()
    _factor(page, factor)
    run_trial(page)
    assert page.dataframe[0].value[["读数", "单位"]].values.tolist() == [[value, "百分点"]]
    chart, = page.get("vega_lite_chart")
    chart_data = pa.ipc.open_stream(chart.proto.datasets[0].data.data).read_all().to_pandas()
    points = page.dataframe[-1].value
    assert points["date"].tolist() == ["2024-01-30", "2024-01-31"]
    assert points[measurement].tolist() == pytest.approx(series)
    assert points["单位"].tolist() == [unit, unit]
    assert chart_data["date"].tolist() == points["date"].tolist()
    assert chart_data[measurement].tolist() == points[measurement].tolist()
    _factor(page, "pair_extreme_spread")
    run_trial(page)
    assert page.get("vega_lite_chart") and "z" in page.dataframe[-1].value
    page.selectbox(key="factor_output_kind").set_value("全部").run()
    _factor(page, "traded_amount")
    run_trial(page)
    assert not page.get("vega_lite_chart")
    print(f"{factor}: points {series} {unit}; percentile {value}, chart and table displayed")


def test_supplementary_amount_is_not_requested_without_field_grant(tmp_path, monkeypatch):
    _, doc, request = example_query("traded_amount")
    for row in doc["tables"]["daily_bars"]:
        row["code"] = "A.SH"
        row["amount_cny"] = 15_000_000
    request = replace(request, subjects=("A.SH",))
    store, ref = local_fact_source(tmp_path, doc["tables"])
    before = compute_factor(store, ref, request)
    assert before.rows[0]["value"] == 15_000_000
    store.config["products"]["factor_input"]["permission"]["allowed_fields"] = ["code", "date", "close_raw"]
    reads = []
    original = store.read_evidence_registrations

    def registrations(*args, **kwargs):
        reads.append(kwargs["table"])
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "read_evidence_registrations", registrations)
    after = compute_factor(store, ref, request)
    assert "factor_input.daily_bars" not in reads
    assert not after.rows and any(gap["field"] == "amount_cny" for gap in after.gaps)
    print("supplementary amount 15000000 before, denied without requesting daily registrations", reads)


def test_browse_close_does_not_infer_restricted_volume(tmp_path, monkeypatch):
    page = open_factors(monkeypatch, tmp_path)
    results = []
    for amount in ("15000000", ""):
        store, ref = build_case_store(tmp_path / ("complete" if amount else "null"), {
            "csvs": {
                "daily_bars": "code,date,open_raw,high_raw,low_raw,close_raw,volume_shares,amount_cny\n"
                f"A.SH,2024-04-01,100,100,100,100,10000,{amount}\n",
                "calendar": "date,is_trading_day\n2024-04-01,1\n",
            },
            "table_products": {"daily_bars": ("quotes", "daily"), "calendar": ("open", "calendar")},
            "permissions": {
                "daily_bars": {"allowed_uses": ["factor_calculation"], "allowed_fields": ["code", "date", "close_raw"]},
                "calendar": {"allowed_uses": ["factor_calculation"]},
            },
            "root_config": True,
            "obtained_at": "2024-04-01T14:00:00+08:00",
        })
        monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
        _browse(page, ref, "daily_bars")
        page.multiselect(key="factor_browser_fields_daily_bars").set_value(["close_raw"])
        _query(page)
        results.append((page.dataframe[0].value.to_dict("records"), [el.value for el in page.json]))
        page.multiselect(key="factor_browser_fields_daily_bars").set_value(["close_raw", "amount_cny"])
        _query(page)
        print("混合字段选择", [table.value.to_dict("records") for table in page.dataframe],
              [item.value for item in page.json])
        assert page.dataframe[0].value["close_raw"].tolist() == [100]
        assert page.dataframe[0].value.get("amount_cny", pa.array([None]).to_pandas()).isna().all()
        assert any("展示字段未获准" in item.value and "amount_cny" in item.value for item in page.caption)
        page.selectbox(key="factor_browser_order_daily_bars").set_value("amount_cny")
        _query(page)
        assert not page.dataframe
        assert any(gap["reason"] == "排序字段未获准" for item in page.json for gap in json.loads(item.value))
    assert results[0] == results[1]
    assert results[0][0] == [{"close_raw": 100.0}]
    assert "fields_missing" not in str(results)
    print("two restricted amount variants gave the same Streamlit rows and gaps:", results[0])


@pytest.mark.parametrize("fault", ["conflict", "missing"])
def test_skip_momentum_requires_unambiguous_decision_dates(tmp_path, monkeypatch, fault):
    owner, ref = local_volume_source(tmp_path / "prices", code="A.SH")
    _, sample, _ = example_query("up_amount_share")
    dates = {
        str(row["date"]) for row in sample["tables"]["calendar"] if row["is_trading_day"]
    }
    assert {"2024-04-01", "2024-03-29"} <= dates
    base = _library(owner, (owner, ref))
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(owner.root))
    _factor(page, "price_momentum")
    page.text_input(key="factor_subjects_price_momentum").set_value("A.SH")
    page.date_input(key="factor_date_price_momentum").set_value(DAY)
    page.text_area(key="factor_parameters_price_momentum_默认").set_value(json.dumps({"window": 20, "skip": 1}))
    page.selectbox(key="factor_source_price_momentum").set_value(base.library_id).run()
    run_trial(page)
    baseline = next(iter(page.session_state["factor_attempts"].values())).result
    components = baseline.rows[0]["components"]
    assert (components["anchor"], components["end"]) == ("2024-03-01", "2024-03-29")
    assert baseline.rows[0]["value"] == pytest.approx(100 / 101 - 1)
    for day in (("2024-04-01", "2024-03-29") if fault == "conflict" else ("2024-03-29",)):
        if fault == "missing":
            other, other_ref = local_volume_source(tmp_path / day, code="A.SH", calendar_omit=(day,))
            combined = _library(owner, (other, other_ref))
        else:
            other, other_ref = _calendar_source(tmp_path / day, "conflict", day)
            combined = _library(owner, (owner, ref), (other, other_ref))
        page.run()
        page.selectbox(key="factor_source_price_momentum").set_value(combined.library_id).run()
        run_trial(page)
        attempt = list(page.session_state["factor_attempts"].values())[-1]
        print(day, attempt.result.rows and attempt.result.rows[0]["value"], attempt.result.gaps)
        assert not attempt.result.rows
        assert any(gap["field"] == "calendar" and gap["period"] == day for gap in attempt.result.gaps)
        assert any("calendar" in frame.value.get("field", []).tolist()
                   for frame in page.dataframe if "field" in frame.value)
    page.selectbox(key="factor_source_price_momentum").set_value(base.library_id).run()
    run_trial(page)
    assert page.dataframe[0].value["读数"].tolist() == [pytest.approx(100 / 101 - 1)]


def _calendar_source(path, name, day):
    return build_case_store(path, {
        "csvs": {"calendar": f"date,is_trading_day\n{day},0\n"},
        "table_products": {"calendar": (name, "calendar")},
        "permissions": {"calendar": {"allowed_uses": ["factor_calculation"]}},
        "source_metadata": {"calendar": {"published_at": f"{day}T14:00:00+08:00"}},
        "root_config": True,
        "obtained_at": f"{day}T14:00:00+08:00",
    })


def test_shared_attachment_returns_only_granted_facts(tmp_path, monkeypatch):
    from hetu_quant.factors import sources

    store, ref = local_volume_source(tmp_path, code="A.SH")
    _, _, request = example_query("traded_amount")
    request = replace(request, subjects=("A.SH",), trade_date=DAY, cutoff=close_cutoff(DAY))
    assert compute_factor(store, ref, request).rows[0]["value"] == 15_000_000
    store.config["products"]["factor_proofs"]["permission"]["allowed_fields"] = ["close_raw"]
    returned = []
    reader = sources._read_close_attestations

    def capture(*args, **kwargs):
        result = reader(*args, **kwargs)
        returned.extend(fact for facts in result.values() for fact in facts)
        return result

    monkeypatch.setattr(sources, "_read_close_attestations", capture)
    result = compute_factor(store, ref, request)
    assert any(fact["record_key"].startswith("action_coverage|A.SH|") for fact in returned)
    assert all(not fact["record_key"].endswith("|amount_cny") for fact in returned)
    assert not result.rows
    assert any(gap["field"] == "amount_cny" and "补证字段未获准" in gap["reason"] for gap in result.gaps)
    assert all(gap["reason"] != "交易日行情缺失" for gap in result.gaps)
    print("shared attachment returned", [fact["record_key"] for fact in returned], result.gaps)
    _, _, momentum = example_query("price_momentum")
    momentum = replace(momentum, subjects=("A.SH",), trade_date=DAY, cutoff=close_cutoff(DAY),
                       parameters={"window": 1})
    assert compute_factor(store, ref, momentum).rows[0]["value"] == 0


def test_mixed_market_preview_keeps_each_subjects_calendar(tmp_path, monkeypatch):
    sh, a = local_volume_source(tmp_path / "sh", code="A.SH")
    sz, b = local_volume_source(tmp_path / "sz", code="B.SZ")
    calendar, c = _calendar_source(tmp_path / "calendar", "cn_sz_archive_calendar", DAY.isoformat())
    ref = _library(sh, (sh, a), (sz, b), (calendar, c))
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(sh.root))
    _factor(page, "traded_amount")
    page.date_input(key="factor_date_traded_amount").set_value(DAY)
    page.selectbox(key="factor_source_traded_amount").set_value(ref.library_id).run()

    def checks(subjects):
        page.text_input(key="factor_subjects_traded_amount").set_value(subjects).run()
        return [item.value for item in page.markdown]

    assert any("B.SZ" in line and "已证休市" in line for line in checks("B.SZ"))
    mixed = checks("A.SH B.SZ")
    print([line for line in mixed if "库表有" in line or "已证休市" in line])
    assert any("B.SZ" in line and "已证休市" in line for line in mixed)
    assert any("A.SH" in line and "库表有 1 个交易日" in line for line in mixed)
    assert not any("B.SZ" in line and "库表有 1 个交易日" in line for line in mixed)
    run_trial(page)
    assert page.dataframe[0].value[["对象", "读数"]].values.tolist() == [["A.SH", 15_000_000]]


def test_preview_reports_denied_proof_field_before_run(tmp_path, monkeypatch):
    store, ref = local_volume_source(tmp_path / "volumes", code="A.SH")
    config_path = store.root / "data-products.json"
    config = json.loads(config_path.read_text())
    config["products"]["factor_proofs"]["permission"]["allowed_fields"] = ["close_raw"]
    config_path.write_text(json.dumps(config))
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
    _factor(page, "traded_amount")
    page.selectbox(key="factor_source_traded_amount").set_value(ref.snapshot_id)
    page.date_input(key="factor_date_traded_amount").set_value(DAY)
    page.text_input(key="factor_subjects_traded_amount").set_value("A.SH").run()
    checks = [item.value for item in page.markdown if "补证字段未获准" in item.value]
    assert any("amount_cny" in line and "factor_proofs" in line for line in checks), checks
    assert not page.button(key="factor_compute").disabled
    run_trial(page)
    attempt = next(iter(page.session_state["factor_attempts"].values()))
    assert not attempt.result.rows and any("补证字段未获准" in gap["reason"] for gap in attempt.result.gaps)
    _factor(page, "price_momentum")
    page.selectbox(key="factor_source_price_momentum").set_value(ref.snapshot_id)
    page.date_input(key="factor_date_price_momentum").set_value(DAY)
    page.text_input(key="factor_subjects_price_momentum").set_value("A.SH")
    page.text_area(key="factor_parameters_price_momentum_默认").set_value(json.dumps({"window": 1})).run()
    run_trial(page)
    assert page.dataframe[0].value["读数"].tolist() == [0.0]
    print(checks, "close-only momentum", page.dataframe[0].value["读数"].tolist())


@pytest.mark.parametrize("closure", ["none", "one", "two"])
def test_pair_beta_aligns_common_sessions_in_workbench(tmp_path, monkeypatch, closure):
    dates = ("2024-03-26", "2024-03-27", "2024-03-28", "2024-03-29", "2024-04-01")
    clock = "2024-03-25T12:00:00+08:00"
    tables = {
        "calendar": [dict(date=day, is_trading_day=True, available_at=clock) for day in dates],
        "daily_bars": [
            dict(code=code, date=day, close=price, available_at=f"{day}T14:59:00+08:00")
            for day, b in zip(dates, (11, 12, 14, 20, 21), strict=True)
            for code, price in (("A.SH", b**2), ("B.SZ", b))
        ],
    }
    owner, data = local_fact_source(tmp_path / "prices", tables)
    assets = [(owner, data)]
    for day in {"none": (), "one": dates[3:4], "two": dates[2:4]}[closure]:
        assets.append(_calendar_source(tmp_path / day, "cn_sz_archive_calendar", day))
    source = _library(owner, *assets)
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(owner.root))
    _factor(page, "pair_hedge_beta")
    page.selectbox(key="factor_source_pair_hedge_beta").set_value(source.library_id)
    page.date_input(key="factor_date_pair_hedge_beta").set_value(DAY)
    page.text_input(key="factor_role_pair_hedge_beta_0").set_value("A.SH")
    page.text_input(key="factor_role_pair_hedge_beta_1").set_value("B.SZ")
    page.text_area(key="factor_parameters_pair_hedge_beta_默认").set_value(json.dumps({"window": 3})).run()
    run_trial(page)
    result = next(iter(page.session_state["factor_attempts"].values())).result
    if closure == "two":
        assert not result.rows and any("共同交易日不足" in gap["reason"] for gap in result.gaps)
        print(closure, result.gaps)
    else:
        expected = list(dates[1:4] if closure == "none" else dates[:3])
        row, = result.rows
        print(closure, row["value"], [(item["code"], item["date"]) for item in row["inputs"]])
        assert page.dataframe[0].value["读数"].tolist() == [pytest.approx(2)]
        for code in ("A.SH", "B.SZ"):
            assert [item["date"] for item in row["inputs"] if item["code"] == code] == expected


@pytest.mark.parametrize("state,expected", [("unknown", None), ("listed", 1), ("exited", 0), ("halted", 0)])
def test_missing_bar_browse_keeps_request_and_proven_latest_date(tmp_path, monkeypatch, state, expected):
    market, quote = local_volume_source(tmp_path / "quotes", code="A.SH")
    requested = date(2024, 4, 2)
    files = {"calendar": f"date,is_trading_day\n{requested},1\n"}
    products = {"calendar": ("calendar", "calendar")}
    if state in ("listed", "exited"):
        out = str(DAY) if state == "exited" else ""
        files["securities"] = f"code,name,ipo_date,out_date,status\nA.SH,样本,2020-01-01,{out},1\n"
        products["securities"] = ("status_names", "catalog")
    if state == "halted":
        files["security_status"] = f"code,date,is_st,is_suspended\nA.SH,{requested},False,True\n"
        products["security_status"] = ("status_halt", "status")
    facts, batch = build_case_store(tmp_path / state, {
        "csvs": files,
        "table_products": products,
        "permissions": {table: {"allowed_uses": ["factor_calculation"]} for table in files},
        "source_metadata": {"calendar": {"published_at": f"{requested}T12:00:00+08:00"}},
        "root_config": True,
        "obtained_at": f"{requested}T12:00:00+08:00",
    })
    denied_market, denied_quote = local_volume_source(tmp_path / "denied", code="A.SH", late=True, deny=True)
    release = _library(market, (market, quote), (facts, batch), (denied_market, denied_quote))
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(market.root))
    _browse(page, release, "daily_bars", day=requested)
    page.multiselect(key="factor_browser_fields_daily_bars").set_value(["close_raw"])
    _query(page)
    assert not page.dataframe
    captions = [item.value for item in page.caption]
    gaps = [item.value for item in page.json]
    print(state, captions[-4:], gaps)
    assert any(f"{requested} 至 {requested}" in text for text in captions)
    expected_coverage = "实有 0，应有数量未知" if expected is None else f"实有 0，应有 {expected}"
    assert any(expected_coverage in text for text in captions)
    reason = {"unknown": "lifecycle_unknown", "listed": "missing_bar",
              "exited": "after_exit", "halted": "suspended"}[state]
    assert any(reason in record for record in gaps)
    assert any("A.SH" in text and "最近有据" in text and str(DAY) in text for text in captions)


@pytest.mark.parametrize("factor,invalid,field,valid", [
    ("pair_hedge_beta", {"window": 2}, "window", {"window": 3}),
    ("pair_cointegration", {"window": 30, "lag": 20}, "lag", {"window": 5, "lag": 1}),
])
def test_pair_configuration_is_rejected_before_production(tmp_path, monkeypatch, factor, invalid, field, valid):
    from hetu_quant.ui import factor_page

    calls = []
    compute = factor_page._compute_trial

    def track(trial, root):
        calls.append(trial.request.parameters)
        return compute(trial, root)

    monkeypatch.setattr(factor_page, "_compute_trial", track)
    page = open_factors(monkeypatch, tmp_path)
    _factor(page, factor)
    key = f"factor_parameters_{factor}_默认"
    page.text_area(key=key).set_value(json.dumps(invalid)).run()
    button = next((button for button in page.button if button.key == "factor_compute"), None)
    print(factor, invalid, "可提交", button is not None and not button.disabled)
    assert button is None or button.disabled
    assert any(f"参数错误 · {field}" in error.value for error in page.error)
    assert calls == [] and not page.session_state["factor_attempts"]
    page.text_area(key=key).set_value(json.dumps(valid)).run()
    assert not page.button(key="factor_compute").disabled
    run_trial(page)
    assert calls == [valid]
    print(factor, "合法边界", calls)


def _browse_quote_asset(path, product, bars, allowed):
    return build_case_store(path, {
        "csvs": {"daily_bars": "code,date,open_raw,high_raw,low_raw,close_raw,volume_shares,amount_cny\n"
                 + "".join(f"A.SH,{day},{close},{close},{close},{close},1,{amount}\n"
                           for day, close, amount in bars)},
        "table_products": {"daily_bars": (product, "daily")},
        "permissions": {"daily_bars": {"allowed_uses": ["factor_calculation"], "allowed_fields": allowed}},
        "root_config": True,
        "obtained_at": "2024-04-03T15:00:00+08:00",
    })


def test_browse_displays_source_reference_only_for_its_permitted_product(tmp_path, monkeypatch):
    fields = ["code", "date", "close_raw"]
    first, private = _browse_quote_asset(tmp_path / "private", "private_quotes", [(DAY, 100, 700)], fields)
    second, public = _browse_quote_asset(
        tmp_path / "public", "public_quotes", [(DAY, 101, 701)], [*fields, "source_ref"],
    )
    denied_ref = "raw/private_quotes/daily_bars.csv"
    allowed_ref = "raw/public_quotes/daily_bars.csv"
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(first.root))

    def check(source, expected):
        _browse(page, source, "daily_bars")
        page.multiselect(key="factor_browser_fields_daily_bars").set_value(["close_raw", "source_ref"])
        page.selectbox(key="factor_browser_order_daily_bars").set_value("date")
        _query(page)
        visible = page.dataframe[0].value
        actual = list(visible.astype(object).where(visible.notna(), None).itertuples(index=False, name=None))
        print("出处读回", actual)
        assert actual == expected
        return visible

    check(private, [(100, None)])
    assert any("source_ref" in line.value and "展示字段未获准" in line.value for line in page.caption)
    assert not any(denied_ref in item.value for item in (*page.caption, *page.info, *page.json, *page.markdown))
    page.selectbox(key="factor_browser_order_daily_bars").set_value("source_ref")
    _query(page)
    assert not page.dataframe
    assert any(gap["reason"] == "排序字段未获准" for item in page.json for gap in json.loads(item.value))
    mixed = _library(first, (first, private), (second, public))
    check(mixed, [(100, None), (101, allowed_ref)])
    assert not any(denied_ref in item.value for item in (*page.caption, *page.info, *page.json, *page.markdown))

    config_path = first.root / "data-products.json"
    cfg = json.loads(config_path.read_text())
    cfg["products"]["private_quotes"]["permission"]["allowed_fields"].append("source_ref")
    config_path.write_text(json.dumps(cfg))
    check(private, [(100, denied_ref)])


@pytest.mark.parametrize("proof", ["matching", "missing", "conflict", "other-product", "other-path"])
def test_browse_usd_amount_keeps_independent_evidence_when_source_ref_is_restricted(tmp_path, monkeypatch, proof):
    source_ref = "raw/factor_daily/daily_bars.csv"
    attestation = dict(source_ref=source_ref, currency="USD", unit="USD")
    if proof == "other-product":
        attestation["product_id"] = "separate_daily"
    if proof == "other-path":
        attestation["source_ref"] = "raw/factor_daily/independent-import.csv"
    attestations = (attestation,)
    if proof == "conflict":
        attestations += (dict(attestation, currency="CNY", unit="CNY"),)
    store, source = local_volume_source(
        tmp_path / "quotes", code="A.SH", amount_evidence=attestations,
        missing="amount_cny" if proof == "missing" else None,
    )
    config_path = store.root / "data-products.json"
    config = json.loads(config_path.read_text())
    permission = config["products"]["factor_daily"]["permission"]
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))

    readbacks = []
    for visible_ref in (True, False, True):
        if visible_ref:
            permission.pop("allowed_fields", None)
        else:
            permission["allowed_fields"] = ["code", "date", "amount_cny"]
        config_path.write_text(json.dumps(config))
        _browse(page, source, "daily_bars")
        page.multiselect(key="factor_browser_fields_daily_bars").set_value(["amount_cny", "source_ref"])
        page.selectbox(key="factor_browser_order_daily_bars").set_value("date")
        _query(page)
        row = page.dataframe[0].value.iloc[0]
        assert row["amount_cny"] == 15_000_000
        if visible_ref:
            assert row["source_ref"] == source_ref
        else:
            assert row.isna()["source_ref"]
            assert any("展示字段未获准" in item.value and "source_ref" in item.value for item in page.caption)
            assert not any(source_ref in item.value for item in (*page.caption, *page.info, *page.json, *page.markdown))
            assert not any(source_ref in label for label in page.selectbox(key="factor_browser_row").options)
        readback = tuple(row[key] for key in ("成交额币种", "成交额单位", "成交额依据"))
        print(proof, "日线路径可见", visible_ref, "页面读回", readback)
        readbacks.append(readback)
    expected = {
        "matching": ("USD", "USD", "factor-original.json"),
        "conflict": ("待核对", "待核对", "原件冲突"),
    }.get(proof, ("未知", "未知", "缺少匹配的金额原件"))
    assert readbacks == [expected] * 3


@pytest.mark.parametrize("revision", [False, True], ids=["single", "revised"])
@pytest.mark.parametrize("next_amount", ["", "830", "0"], ids=["missing", "valid", "zero"])
def test_browse_latest_available_amount_does_not_depend_on_window_start(tmp_path, monkeypatch, next_amount, revision):
    first_day, second_day, cutoff = (date(2024, 4, day) for day in (1, 2, 3))
    fields = ["code", "date", "amount_cny"]
    owner, quotes = _browse_quote_asset(tmp_path / "quotes", "quotes", [
        (first_day, 100, 700), (second_day, 101, 830 if revision else next_amount),
    ], fields)
    denied, later = _browse_quote_asset(
        tmp_path / "later", "later", [(cutoff, 102, 990)], ["code", "date", "close_raw"],
    )
    if revision:
        _, corrected = _browse_quote_asset(tmp_path / "quotes", "quotes", [(second_day, 101, next_amount)], fields)
        rule = dict(root=str(owner.root), product_id="quotes", table="daily_bars",
                    superseded_snapshot_id=quotes.snapshot_id, selected_snapshot_id=corrected.snapshot_id,
                    codes=["A.SH"], start=str(second_day), end=str(second_day), evidence_ref="evidence/revision.json")
        (owner.root / rule["evidence_ref"]).write_text(json.dumps(rule))
        source = publish_library(owner, asset_snapshots=[
            dict(root=str(owner.root), snapshot_id=quotes.snapshot_id, tables=["daily_bars"]),
            dict(root=str(owner.root), snapshot_id=corrected.snapshot_id, tables=["daily_bars"],
                 revision_selections=[rule]),
            dict(root=str(denied.root), snapshot_id=later.snapshot_id, tables=["daily_bars"]),
        ])
    else:
        source = _library(owner, (owner, quotes), (denied, later))
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(owner.root))
    observed_dates = []
    for start in (cutoff, second_day, first_day):
        _browse(page, source, "daily_bars", day=cutoff)
        page.date_input(key="factor_browser_dates").set_value((start, cutoff))
        page.selectbox(key="factor_browser_order_daily_bars").set_value("date")
        page.multiselect(key="factor_browser_fields_daily_bars").set_value(["amount_cny"])
        _query(page)
        captions = [item.value for item in page.caption]
        assert any(f"{start} 至 {cutoff}" in text for text in captions)
        latest, = [text for text in captions if text.startswith("A.SH · 最近有据日期 ")]
        observed_dates.append(latest.rsplit(" ", 1)[-1])
        values = page.dataframe[0].value["amount_cny"] if page.dataframe else None
        expected = []
        if start == first_day:
            expected.append(700)
        if start <= second_day:
            expected.append(int(next_amount) if next_amount else None)
        actual = [] if values is None else values.astype(object).where(values.notna(), None).tolist()
        print(source.library_id, "查询", start, cutoff, "明细", actual, "最近日期", observed_dates[-1])
        assert actual == expected
    assert observed_dates == [str(second_day if next_amount else first_day)] * 3


@pytest.mark.parametrize("variant", ["oldest-first", "newest-first", "restricted", "restricted-date"])
def test_browse_uses_selected_revision_before_projecting_amount(tmp_path, monkeypatch, variant):
    from hetu_data.library import load_library, query_library

    first, revised_day, cutoff = (date(2024, 4, day) for day in (1, 2, 3))
    fields = ["code", "date", "close_raw", "amount_cny"]
    path = tmp_path / "quotes"
    owner, original = _browse_quote_asset(path, "quotes", [(first, 100, 700), (revised_day, 101, 830)], fields)
    root = str(owner.root)
    restricted = variant.startswith("restricted")
    amounts = [825] if restricted else [825, ""]
    assets = [dict(root=root, snapshot_id=original.snapshot_id, tables=["daily_bars"])]
    rules = []
    for index, amount in enumerate(amounts, 1):
        close = 102 if variant == "restricted-date" else 101
        _, current = _browse_quote_asset(path, "quotes", [(revised_day, close, amount)], fields)
        rule = dict(
            root=root, product_id="quotes", table="daily_bars",
            superseded_snapshot_id=assets[-1]["snapshot_id"], selected_snapshot_id=current.snapshot_id,
            codes=["A.SH"], start=str(revised_day), end=str(revised_day),
            evidence_ref=f"evidence/revision-{index}.json",
        )
        (owner.root / rule["evidence_ref"]).write_text(json.dumps(rule))
        rules.append(rule)
        assets.append(dict(root=root, snapshot_id=current.snapshot_id, tables=["daily_bars"]))
    if restricted:
        config = json.loads((owner.root / "data-products.json").read_text())
        permission = config["products"]["quotes"]["permission"]
        permission["allowed_fields"] = ["code", "close_raw"] if variant == "restricted-date" else [
            "code", "date", "close_raw",
        ]
        narrowed = owner.root / "revised-products.json"
        narrowed.write_text(json.dumps(config))
        assets[-1]["config_path"] = str(narrowed)
    assets[-1]["revision_selections"] = rules[::-1] if variant == "newest-first" else rules
    source = publish_library(owner, asset_snapshots=assets)
    assert load_library(owner, source)["revision_selections"] == assets[-1]["revision_selections"]
    if not restricted:
        archive = query_library(owner, source, codes=("A.SH",), start=first, end=cutoff)["bars"]
        assert archive.date.tolist() == [str(first), str(revised_day)]
        assert archive.amount_cny.iloc[0] == 700 and archive.amount_cny.isna().iloc[1]
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", root)

    def readback(start, selected, sort="date"):
        _browse(page, source, "daily_bars", day=cutoff)
        page.selectbox(key="factor_browser_product").set_value("quotes")
        page.date_input(key="factor_browser_dates").set_value((start, cutoff))
        page.selectbox(key="factor_browser_order_daily_bars").set_value(sort)
        page.multiselect(key="factor_browser_fields_daily_bars").set_value(selected)
        _query(page)
        captions = [item.value for item in page.caption]
        assert any(f"{start} 至 {cutoff}" in text for text in captions)
        latest, = [text.rsplit(" ", 1)[-1] for text in captions if text.startswith("A.SH · 最近有据日期 ")]
        values = []
        if page.dataframe:
            frame = page.dataframe[0].value.loc[:, selected]
            values = list(frame.astype(object).where(frame.notna(), None).itertuples(index=False, name=None))
        span, = [text for text in captions if "产品获准数据范围" in text]
        print(variant, source.library_id, start, selected, sort, latest, span, values)
        return latest, values

    actual = [readback(day, ["amount_cny"]) for day in (cutoff, revised_day, first)]
    if variant == "restricted-date":
        by_close = readback(first, ["close_raw"], "close_raw")
        by_date = readback(first, ["close_raw"])
        dates = readback(first, ["date"])
        assert by_close == (str(first), [(100,), (102,)])
        assert by_date == (str(first), [(100,)])
        assert dates == (str(first), [(str(first),)])
        assert any(f"产品获准数据范围 {first} 至 {first}" in item.value for item in page.caption)
        assert actual == [(str(first), values) for values in ([], [], [(700,)])]
        return
    if variant == "restricted":
        mixed = readback(first, ["close_raw", "amount_cny"])
        assert mixed == (str(revised_day), [(100, 700), (101, None)])
        assert any("展示字段未获准" in item.value and "amount_cny" in item.value for item in page.caption)
        expected = [[], [], [(700,)]]
    else:
        expected = [[], [(None,)], [(700,), (None,)]]
    assert actual == [(str(first), values) for values in expected]


def test_result_daily_source_ref_tracks_current_permission(tmp_path, monkeypatch):
    from hetu_quant.ui import factor_navigation, factor_page

    store, ref = local_volume_source(tmp_path / "volume", code="A.SH")
    config_path = store.root / "data-products.json"
    config = json.loads(config_path.read_text())
    for product in config["products"].values():
        product["permission"]["allow_export"] = True
    permission = config["products"]["factor_daily"]["permission"]
    ref_text = "raw/factor_daily/daily_bars.csv"
    config_path.write_text(json.dumps(config))
    downloads, calls = [], []
    download_button = factor_navigation.st.download_button
    compute = factor_page._compute_trial

    def record_download(label, data, **kwargs):
        if label == "下载该项获准证据":
            downloads.append(data)
        return download_button(label, data, **kwargs)

    def record_compute(trial, root):
        calls.append(trial)
        return compute(trial, root)

    monkeypatch.setattr(factor_navigation.st, "download_button", record_download)
    monkeypatch.setattr(factor_page, "_compute_trial", record_compute)
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
    _factor(page, "traded_amount")
    page.selectbox(key="factor_source_traded_amount").set_value(ref.snapshot_id).run()
    page.text_input(key="factor_subjects_traded_amount").set_value("A.SH")
    page.date_input(key="factor_date_traded_amount").set_value(DAY).run()
    run_trial(page)
    counts = []
    for allowed in (True, False, True):
        if allowed:
            permission.pop("allowed_fields", None)
        else:
            permission["allowed_fields"] = ["code", "date", "amount_cny"]
        config_path.write_text(json.dumps(config))
        page.run()
        assert page.dataframe[0].value["读数"].tolist() == [15_000_000]
        result_count = sum(section.value.count(ref_text) for section in page.json)
        page.button(key="factor_evidence_row_0").click().run()
        detail = "\n".join(section.value for section in page.json)
        assert "factor-original.json" in detail
        assert any(item.label == "下载该项获准证据" for item in page.get("download_button"))
        payload = downloads[-1]
        assert "factor-original.json" in payload
        counts.append((result_count, detail.count(ref_text), payload.count(ref_text)))
        print("source_ref allowed", allowed, "result/detail/download path counts", counts[-1])
        page.button(key="factor_return_to_draft").click().run()
    assert all(count > 0 for count in counts[0]) and counts[1] == (0, 0, 0) and counts[0] == counts[2]
    permission["allow_export"] = False
    config_path.write_text(json.dumps(config))
    page.button(key="factor_evidence_row_0").click().run()
    assert not any(item.label == "下载该项获准证据" for item in page.get("download_button"))
    assert len(calls) == len(page.session_state["factor_attempts"]) == 1


@pytest.mark.parametrize(("factor_id", "entry", "step", "value"), [
    ("grid_geometric_level", "widget", 0.1, 121),
    ("grid_geometric_level", "json", 0.1, 121),
    ("grid_linear_level", "json", 5, 110),
])
def test_grid_parameters_block_before_preview_and_execution(tmp_path, monkeypatch, factor_id, entry, step, value):
    from hetu_quant.ui import factor_page

    previews, submitted = [], []
    preview, compute = factor_page.preview_inputs, factor_page._compute_trial

    def record_preview(store, trial):
        previews.append(trial)
        return preview(store, trial)

    def record_compute(trial, root):
        submitted.append(trial)
        return compute(trial, root)

    monkeypatch.setattr(factor_page, "preview_inputs", record_preview)
    monkeypatch.setattr(factor_page, "_compute_trial", record_compute)
    page = open_factors(monkeypatch, tmp_path)
    _factor(page, factor_id)
    raw_key = f"factor_parameters_{factor_id}_默认"
    parameters = json.loads(page.text_area(key=raw_key).value)
    previews.clear()
    if entry == "widget":
        page.number_input(key=f"factor_param_{factor_id}_step").set_value(0.0).run()
    else:
        page.text_area(key=raw_key).set_value(json.dumps(dict(parameters, step=0.0))).run()
    button = next((button for button in page.button if button.key == "factor_compute"), None)
    print(entry, "invalid", [error.value for error in page.error], "preview calls", len(previews))
    assert button is None or button.disabled
    assert any("参数错误 · step" in error.value for error in page.error)
    assert not previews and not submitted and not page.session_state["factor_attempts"]
    if entry == "widget":
        controls = {key: page.number_input(key=f"factor_param_{factor_id}_{key}") for key in parameters}
        assert {key: widget.value for key, widget in controls.items()} == dict(parameters, step=0.0)
        controls["step"].set_value(step).run()
        assert not submitted and not page.session_state["factor_attempts"]
        print("same controls recovered without running", json.loads(page.text_area(key=raw_key).value))
    else:
        page.text_area(key=raw_key).set_value(json.dumps(dict(parameters, step=step))).run()
    run_trial(page)
    assert [trial.request.parameters for trial in submitted] == [dict(parameters, step=step)]
    assert page.dataframe[0].value["读数"].tolist() == [pytest.approx(value)]
    print(entry, "corrected step", submitted[0].request.parameters["step"], page.dataframe[0].value["读数"].tolist())
    if entry == "widget":
        page.text_input(key=f"factor_subjects_{factor_id}").set_value("OTHER").run()
        run_trial(page)
        gap_attempt = list(page.session_state["factor_attempts"].values())[-1]
        assert gap_attempt.result.gaps and not gap_attempt.result.rows
        assert all(gap["field"] != "step" for gap in gap_attempt.result.gaps)
        assert any("选定输入状态 · 缺证" in heading.value for heading in page.subheader)


def _paid_quote_snapshot(store, path, product, first, last, retained_start, retained_end):
    from hetu_data import DataStore

    grant = dict(
        product_id=product, codes=["A.SH"], markets=["SH"], start=str(first), end=str(last),
        approved_scope_ref="合成范围", license_ref="合成许可", allowed_operations=["read", "import"],
        allowed_uses=["factor_calculation"], acquire_until="2099-12-31", retained_use=True,
    )
    grant_path = store.root / "data-entitlements.json"
    grant_path.write_text(json.dumps({"grants": [grant]}))
    config_path = store.root / "data-products.json"
    config = json.loads(config_path.read_text())
    config["products"][product]["fee_class"] = "paid"
    config_path.write_text(json.dumps(config))
    manifest_path = path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["mode"] = "paid"
    manifest["items"][0]["source"]["fee_class"] = "paid"
    manifest_path.write_text(json.dumps(manifest))
    ref = DataStore(store.root).import_files(manifest_path, (path / "daily_bars.csv",))
    grant_path.write_text(json.dumps({"grants": [dict(grant, start=str(retained_start), end=str(retained_end))]}))
    return ref


@pytest.mark.parametrize("paid", [False, True], ids=["full-range", "narrow-grant"])
def test_selected_product_range_is_independent_of_query_and_page(tmp_path, monkeypatch, paid):
    first, second, last = (date(2024, 4, day) for day in (1, 2, 3))
    path = tmp_path / "quotes"
    store, ref = _browse_quote_asset(path, "quotes", [
        (first, 100, 700), (second, 101, 830), (last, 102, 900),
    ], ["code", "date", "close_raw"])
    span = f"{first} 至 {last}"
    if paid:
        ref = _paid_quote_snapshot(store, path, "quotes", first, last, second, second)
        span = f"{second} 至 {second}"
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
    for left, right in ((second, second), (first, last)):
        _browse(page, ref, "daily_bars", day=second)
        page.selectbox(key="factor_browser_product").set_value("quotes")
        page.date_input(key="factor_browser_dates").set_value((left, right))
        page.multiselect(key="factor_browser_fields_daily_bars").set_value(["close_raw"])
        _query(page)
        captions = [item.value for item in page.caption]
        print("product range", left, right, captions)
        assert any("产品获准数据范围" in text and span in text for text in captions)
        assert any(f"本次查询 {left} 至 {right}" in text for text in captions)
        actual_left, actual_right = (second, second) if paid else (left, right)
        assert any(f"本页实际日期 {actual_left} 至 {actual_right}" in text for text in captions)
        assert any(f"最近有据日期 {actual_right}" in text for text in captions)
    if paid:
        page.multiselect(key="factor_browser_fields_daily_bars").set_value(["amount_cny"])
        _query(page)
        assert not page.dataframe
        assert any(f"产品获准数据范围 {span}" in caption.value for caption in page.caption)


def test_daily_product_range_does_not_depend_on_displayed_field(tmp_path, monkeypatch):
    first, second, last = (date(2024, 4, day) for day in (1, 2, 3))
    store, ref = _browse_quote_asset(tmp_path / "quotes", "quotes", [
        (first, 100, ""), (second, 101, 830), (last, 102, ""),
    ], ["code", "date", "close_raw", "amount_cny"])
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
    product_spans = []
    for field, value in (("close_raw", 101), ("amount_cny", 830)):
        _browse(page, ref, "daily_bars", day=second)
        page.selectbox(key="factor_browser_product").set_value("quotes")
        page.multiselect(key="factor_browser_fields_daily_bars").set_value([field])
        _query(page)
        captions = [item.value for item in page.caption]
        assert page.dataframe[0].value[field].tolist() == [value]
        assert any(f"最近有据日期 {second}" in text for text in captions)
        assert any(f"本页实际日期 {second} 至 {second}" in text for text in captions)
        product_spans.append(next(text for text in captions if "产品获准数据范围" in text))
        print(field, product_spans[-1], "latest", second, "rows", [value])
    assert product_spans == [f"quotes · 产品获准数据范围 {first} 至 {last}"] * 2
    page.date_input(key="factor_browser_dates").set_value((last, last))
    _query(page)
    assert page.dataframe[0].value["amount_cny"].isna().all()
    assert any(f"最近有据日期 {second}" in caption.value for caption in page.caption)
    assert any(product_spans[0] == caption.value for caption in page.caption)
    print("amount blank on", last, "latest", second, product_spans[0])


def test_daily_date_display_and_range_follow_date_permission(tmp_path, monkeypatch):
    first, second, last = (date(2024, 4, day) for day in (1, 2, 3))
    store, ref = _browse_quote_asset(tmp_path / "quotes", "quotes", [
        (first, 100, 700), (second, 101, 830), (last, 102, 900),
    ], ["code", "date", "close_raw"])
    config_path = store.root / "data-products.json"
    config = json.loads(config_path.read_text())
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
    readings = {}
    for fields, display in ((["code", "close_raw"], "close_raw"), (["code", "date"], "date")):
        config["products"]["quotes"]["permission"]["allowed_fields"] = fields
        config_path.write_text(json.dumps(config))
        _browse(page, ref, "daily_bars", day=second)
        page.selectbox(key="factor_browser_product").set_value("quotes")
        page.selectbox(key="factor_browser_order_daily_bars").set_value("code")
        page.multiselect(key="factor_browser_fields_daily_bars").set_value([display])
        _query(page)
        captions = [item.value for item in page.caption]
        assert any(f"本次查询 {second} 至 {second}" in text for text in captions)
        assert page.dataframe[0].value.to_dict("records") == [
            {display: 101.0 if display == "close_raw" else str(second)}
        ]
        date_captions = [text for text in captions if any(
            label in text for label in ("产品获准数据范围", "最近有据日期", "本页实际日期")
        )]
        labels = page.selectbox(key="factor_browser_row").options
        has_transfer = any(button.key == "factor_browser_to_trial" for button in page.button)
        readings[display] = date_captions, labels, has_transfer
        print(fields, page.dataframe[0].value.to_dict("records"), date_captions, labels, has_transfer)
    hidden, labels, has_transfer = readings["close_raw"]
    assert len(hidden) == 3 and all("受限" in text and "2024-04-" not in text for text in hidden)
    assert all("2024-04-" not in label for label in labels) and not has_transfer
    shown, labels, has_transfer = readings["date"]
    assert shown == [
        f"quotes · 产品获准数据范围 {first} 至 {last}",
        f"A.SH · 最近有据日期 {second}",
        f"本页实际日期 {second} 至 {second}",
    ]
    assert any(str(second) in label for label in labels) and has_transfer


@pytest.mark.parametrize("private_day", [2, 3])
def test_daily_missing_field_diagnostics_use_the_contributors_date_grant(tmp_path, monkeypatch, private_day):
    first, hidden, last = (date(2024, 4, day) for day in (1, private_day, 4))
    public = _browse_quote_asset(
        tmp_path / "public", "public_quotes", [(first, 100, "")], ["code", "date", "close_raw", "amount_cny"],
    )
    private = _browse_quote_asset(
        tmp_path / "private", "private_quotes", [(hidden, 101, "")], ["code", "close_raw", "amount_cny"],
    )
    store = public[0]
    source = _library(store, public, private)
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
    _browse(page, source, "daily_bars", day=last)
    page.date_input(key="factor_browser_dates").set_value((first, last))
    page.selectbox(key="factor_browser_order_daily_bars").set_value("close_raw")
    page.multiselect(key="factor_browser_fields_daily_bars").set_value(["close_raw", "amount_cny"])
    _query(page)
    assert page.dataframe[0].value["close_raw"].tolist() == [100, 101]
    assert any(f"本页实际日期 {first} 至 {first}" in item.value for item in page.caption)
    details = [gap for item in page.json for gap in json.loads(item.value)]
    gaps = [gap for gap in details if gap.get("reason") == "fields_missing"]
    inferred_intervals = [gap for gap in details if gap.get("reason") == "calendar_unknown"]
    print("restricted date differs in fixture", gaps, inferred_intervals)
    assert dict(code="A.SH", start=str(first), end=str(first), reason="fields_missing") in gaps
    assert dict(code="A.SH", reason="fields_missing", date_restricted=True, fields=["amount_cny"]) in gaps
    assert any("行情字段缺失" in item.value and "amount_cny" in item.value for item in page.caption)
    assert str(hidden) not in json.dumps(gaps)
    assert inferred_intervals == [dict(code="A.SH", reason="calendar_unknown", date_restricted=True)]
    labels = page.selectbox(key="factor_browser_row").options
    assert str(first) in labels[0] and str(hidden) not in "".join(labels)
    page.selectbox(key="factor_browser_row").set_value(1).run()
    assert not any(button.key == "factor_browser_to_trial" for button in page.button)
    assert any("该行日期字段未获准" in item.value for item in page.caption)


def test_daily_history_uses_its_own_date_grant_outside_current_window(tmp_path, monkeypatch):
    first, last = date(2024, 4, 1), date(2024, 4, 3)
    path = tmp_path / "past"
    owner, _ = _browse_quote_asset(
        path, "past_quotes", [(first, 100, 700)], ["code", "date", "close_raw"],
    )
    past = _paid_quote_snapshot(owner, path, "past_quotes", first, first, first, first)
    current = _browse_quote_asset(
        tmp_path / "current", "current_quotes", [(last, 103, 900)], ["code", "close_raw"],
    )
    store = current[0]
    source = _library(store, (owner, past), current)
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
    observed = []
    for start, closes in ((first, [100, 103]), (last, [103])):
        _browse(page, source, "daily_bars", day=last)
        page.date_input(key="factor_browser_dates").set_value((start, last))
        page.selectbox(key="factor_browser_order_daily_bars").set_value("close_raw")
        page.multiselect(key="factor_browser_fields_daily_bars").set_value(["close_raw"])
        _query(page)
        captions = [item.value for item in page.caption]
        assert page.dataframe[0].value["close_raw"].tolist() == closes
        dates = [text for text in captions if "最近有据日期" in text or "本页实际日期" in text]
        print(start, closes, dates)
        observed.append(dates)
    assert observed[0] == [f"A.SH · 最近有据日期 {first}", f"本页实际日期 {first} 至 {first}"]
    assert observed[1] == [f"A.SH · 最近有据日期 {first}", "本页实际日期 受限（日期字段未获准）"]


@pytest.mark.parametrize("private_first", [True, False])
def test_equal_daily_copies_use_a_date_authorized_witness(tmp_path, monkeypatch, private_first):
    day = date(2024, 4, 2)
    public = _browse_quote_asset(
        tmp_path / "public", "quotes", [(day, 101, 830)], ["code", "date", "close_raw"],
    )
    private = _browse_quote_asset(
        tmp_path / "private", "quotes", [(day, 101, 830)], ["code", "close_raw"],
    )
    store = public[0]
    source = _library(store, *((private, public) if private_first else (public, private)))
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
    _browse(page, source, "daily_bars", day=day)
    page.selectbox(key="factor_browser_product").set_value("quotes")
    page.selectbox(key="factor_browser_order_daily_bars").set_value("close_raw")
    page.multiselect(key="factor_browser_fields_daily_bars").set_value(["close_raw"])
    _query(page)
    captions = [item.value for item in page.caption]
    labels = page.selectbox(key="factor_browser_row").options
    print("restricted copy first", private_first, page.dataframe[0].value.to_dict("records"),
          [text for text in captions if "有据日期" in text or "本页实际日期" in text], labels)
    assert page.dataframe[0].value["close_raw"].tolist() == [101]
    assert f"A.SH · 最近有据日期 {day}" in captions
    assert f"本页实际日期 {day} 至 {day}" in captions
    assert len(labels) == 1 and str(day) in labels[0]
    page.button(key="factor_browser_to_trial").click().run()
    assert page.sidebar.radio(key="page_nav").value == "因子库"
    assert page.date_input(key="factor_date_up_amount_share").value == day
    context = page.session_state["factor_browse_context_up_amount_share"]
    assert (context["source_root"], context["snapshot_id"], context["product_id"]) == (
        str(public[0].root.resolve()), public[1].snapshot_id, "quotes",
    )
    print("authorized transfer", context["snapshot_id"], context["product_id"], context["coordinate"])


def test_calendar_product_range_is_separate_from_page_dates(tmp_path, monkeypatch):
    first, second, last = (date(2024, 4, day) for day in (1, 2, 3))
    store, ref = build_case_store(tmp_path / "calendar", {
        "csvs": {"calendar": "date,is_trading_day\n" + "".join(f"{day},True\n" for day in (first, second, last))},
        "table_products": {"calendar": ("sessions", "calendar")},
        "permissions": {"calendar": {"allowed_uses": ["factor_calculation"]}},
        "root_config": True,
        "obtained_at": "2024-04-03T15:00:00+08:00",
    })
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
    _browse(page, ref, "calendar", code="", day=second)
    page.selectbox(key="factor_browser_product").set_value("sessions")
    _query(page)
    captions = [item.value for item in page.caption]
    print("calendar range", captions, "rows", page.dataframe[0].value.to_dict("records"))
    assert page.dataframe[0].value["date"].tolist() == [str(second)]
    assert any(f"本页实际日期 {second} 至 {second}" in text for text in captions)
    assert any(f"产品获准数据范围 {first} 至 {last}" in text for text in captions)
    config_path = store.root / "data-products.json"
    config = json.loads(config_path.read_text())
    config["products"]["sessions"]["permission"]["allowed_fields"] = ["is_trading_day"]
    config_path.write_text(json.dumps(config))
    page.run()
    assert any("产品获准数据范围 未知" in caption.value for caption in page.caption)
    print("calendar without date permission: product range unknown")


@pytest.mark.parametrize(("factor_id", "expected", "dates"), [
    ("dividend_persistence", 2, {"用于判断的财年": "2021、2022、2023", "实际行情／事件日期": "未知"}),
    ("planned_sale_pressure", 0.06, {"事件生效日": "2024-01-30", "股本日期": "2024-01-31"}),
    ("earnings_yield", 0.06, {"报告期": "2023Q4", "实际行情／事件日期": "2024-01-31"}),
])
def test_result_table_identifies_used_business_dates(tmp_path, monkeypatch, factor_id, expected, dates):
    page = open_factors(monkeypatch, tmp_path)
    _factor(page, factor_id)
    run_trial(page)
    record = page.dataframe[0].value.iloc[0].to_dict()
    attempt, = page.session_state["factor_attempts"].values()
    print(factor_id, record, "inputs", attempt.result.rows[0]["inputs"])
    assert record["读数"] == pytest.approx(expected)
    assert {key: record.get(key) for key in dates} == dates


@pytest.mark.parametrize("denied", [False, True], ids=["recover", "revoked"])
def test_retry_historical_failure_uses_frozen_request(tmp_path, monkeypatch, denied):
    from contextlib import suppress

    from hetu_quant.ui import factor_page

    day = DAY
    store, ref = local_volume_source(
        tmp_path / "quotes", code="A.SH", close_by_date={"2024-03-28": 10, "2024-03-29": 12, str(day): 30},
    )
    calls = []
    production = factor_page._compute_trial

    def fail_once(trial, root):
        calls.append(trial)
        if len(calls) == 1:
            raise OSError("synthetic transient failure")
        return production(trial, root)

    monkeypatch.setattr(factor_page, "_compute_trial", fail_once)
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
    _factor(page, "price_momentum")
    page.selectbox(key="factor_source_price_momentum").set_value(ref.snapshot_id).run()
    page.text_input(key="factor_subjects_price_momentum").set_value("A.SH").run()
    page.date_input(key="factor_date_price_momentum").set_value(day).run()
    raw = page.text_area(key="factor_parameters_price_momentum_默认")
    raw.set_value(json.dumps(dict(window=1, skip=1))).run()
    with suppress(OSError):
        run_trial(page)
    page.run()
    failed, = page.session_state["factor_attempts"].values()
    assert failed.state == "failed"
    raw = page.text_area(key="factor_parameters_price_momentum_默认")
    raw.set_value(json.dumps(dict(window=1, skip=0))).run()
    next(item for item in page.selectbox if item.label == "查看已提交的尝试").set_value(failed.id).run()
    detail = "\n".join(section.value for section in page.json)
    print("archived A", "shown", '"skip": 1' in detail, "buttons", [button.label for button in page.button])
    assert all(text in detail for text in ('"skip": 1', '"window": 1', ref.snapshot_id, "A.SH", str(day), "15:00"))
    assert any("无法读取" in error.value for error in page.error)
    assert len(calls) == len(page.session_state["factor_attempts"]) == 1
    if denied:
        for product in ("factor_daily", "factor_calendar", "factor_proofs"):
            _revoke(store, product)
    retry = next(button for button in page.button if button.label == "按原配置重试")
    retry.click().run()
    runs = page.session_state["factor_runs"]
    if runs.future is not None:
        runs.future.result(timeout=5)
        page.run()
    assert json.loads(page.text_area(key="factor_parameters_price_momentum_默认").value)["skip"] == 0
    assert any("待计算" in caption.value for caption in page.caption)
    if denied:
        assert any("当前许可" in item.value for item in page.warning)
        assert len(calls) == len(page.session_state["factor_attempts"]) == 1
        print("archived retry revoked; call delta 0")
        return
    archived, recovered = page.session_state["factor_attempts"].values()
    assert archived.id == failed.id and archived.state == "failed"
    assert recovered.trial == failed.trial == calls[1]
    assert recovered.result.rows and recovered.result.rows[0]["value"] == pytest.approx(0.2), recovered.result.gaps
    run_trial(page)
    current = list(page.session_state["factor_attempts"].values())[-1]
    assert len(calls) == 3 and calls[-1].request.parameters["skip"] == 0
    assert current.result.rows[0]["value"] == pytest.approx(1.5)
    print("archived retry", recovered.result.rows[0]["value"], "current draft", current.result.rows[0]["value"])
