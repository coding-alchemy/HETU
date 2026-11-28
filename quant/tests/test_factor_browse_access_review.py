"""Offline workbench regressions for published browsing and product grants."""

import json
from datetime import date, timedelta

import pytest
from hetu_data import DataStore, browse
from hetu_data.library import _read_library_range, load_library, publish_library

from backtest_cases import build_case_store
from test_factor_local_domains import _paid_supplementary_ep
from test_factor_workbench import open_factors
from test_factor_workbench_review import _browse, _browse_quote_asset, _factor, _library, _names, _query


def _product_config(store, product, fields):
    path = store.root / "data-products.json"
    config = json.loads(path.read_text())
    config["products"][product]["permission"]["allowed_fields"] = fields
    path.write_text(json.dumps(config))


def _native_asset(path, table, product, csv, kind):
    return build_case_store(path, dict(
        csvs={table: csv},
        table_products={table: (product, kind)},
        permissions={table: {"allowed_uses": ["factor_calculation"]}},
        root_config=True,
        obtained_at="2024-04-03T15:00:00+08:00",
    ))


def test_browser_applies_selected_calendar_revision_to_rows_and_count(tmp_path, monkeypatch):
    day = date(2024, 4, 2)
    path = tmp_path / "cal"
    header = "date,is_trading_day\n"
    store, old = _native_asset(path, "calendar", "sessions", header + f"{day},True\n", "calendar")
    _, corrected = _native_asset(path, "calendar", "sessions", header + f"{day},False\n", "calendar")
    rule = dict(
        root=str(store.root), product_id="sessions", table="calendar",
        superseded_snapshot_id=old.snapshot_id, selected_snapshot_id=corrected.snapshot_id,
        codes=[], start=str(day), end=str(day), evidence_ref="evidence/calendar-revision.json",
    )
    (store.root / rule["evidence_ref"]).write_text(json.dumps(rule))
    published = publish_library(store, asset_snapshots=(
        dict(root=str(store.root), snapshot_id=old.snapshot_id, tables=["calendar"]),
        dict(root=str(store.root), snapshot_id=corrected.snapshot_id, tables=["calendar"], revision_selections=[rule]),
    ))
    independent = _library(store, (store, old), (store, corrected))
    native = _read_library_range(
        store, load_library(store, published), "calendar", codes=(), start=day, end=day,
        columns=("date", "is_trading_day"),
    )
    assert native["is_trading_day"].tolist() == ["False"]
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
    for source, expected in ((old, ["True"]), (independent, ["False", "True"]), (published, ["False"])):
        _browse(page, source, "calendar", code="", day=day)
        page.selectbox(key="factor_browser_product").set_value("sessions")
        page.selectbox(key="factor_browser_order_calendar").set_value("is_trading_day")
        _query(page)
        flags = page.dataframe[0].value["is_trading_day"].tolist()
        labels = page.selectbox(key="factor_browser_row").options
        print("calendar", source, "official", native.to_dict("records"), "page", flags, "choices", labels)
        assert flags == expected
        assert len(labels) == len(expected)
        assert any(f"{len(expected)} 条获准记录" in item.value for item in page.caption)
        assert any(f"产品获准数据范围 {day} 至 {day}" in item.value for item in page.caption)


def test_name_search_and_browser_table_share_field_grants(tmp_path, monkeypatch):
    store, ref = _names(tmp_path / "names", "SYNTH.SH")
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
    for allow_name in (False, True):
        fields = ["code", "ipo_date", "out_date", *(["name"] if allow_name else [])]
        _product_config(store, "local_names", fields)
        _browse(page, ref, "securities", code="SYNTH.SH")
        page.multiselect(key="factor_browser_fields_securities").set_value(["code", "name"])
        _query(page)
        table = page.dataframe[0].value
        assert table["code"].tolist() == ["SYNTH.SH"]
        assert table["name"].notna().tolist() == [allow_name]
        for browser in (True, False):
            if browser:
                search, launch, pick = "factor_browser_search", "factor_browser_find", "factor_browser_match"
            else:
                page.sidebar.radio(key="page_nav").set_value("因子库").run()
                _factor(page, "traded_amount")
                page.selectbox(key="factor_source_traded_amount").set_value(ref.snapshot_id).run()
                search, launch, pick = (
                    "factor_object_search_traded_amount", "factor_find_object_traded_amount",
                    "factor_pick_traded_amount",
                )
            for needle in ("样本", "SYNTH.SH"):
                page.text_input(key=search).set_value(needle).run()
                page.button(key=launch).click().run()
                assert not page.exception
                matches = next((widget.options for widget in page.selectbox if widget.key == pick), [])
                print("name search", allow_name, "browser", browser, needle, matches)
                if allow_name or needle == "SYNTH.SH":
                    label, = matches
                    assert "SYNTH.SH" in label and ("样本名称" in label) == allow_name
                else:
                    assert matches == []


def test_daily_internal_code_does_not_disclose_which_query_subject_matched(tmp_path, monkeypatch):
    day = date(2024, 4, 2)
    store, ref = _browse_quote_asset(tmp_path / "quotes", "quotes", [(day, 101, 830)], ["date", "close_raw"])
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
    for allow_code in (False, True):
        if allow_code:
            _product_config(store, "quotes", ["code", "date", "close_raw"])
        _browse(page, ref, "daily_bars", code="A.SH OTHER.SH", day=day)
        page.multiselect(key="factor_browser_fields_daily_bars").set_value(["code", "date", "close_raw"])
        page.selectbox(key="factor_browser_order_daily_bars").set_value("close_raw")
        _query(page)
        frame = page.dataframe[0].value
        labels = page.selectbox(key="factor_browser_row").options
        transfer = any(button.key == "factor_browser_to_trial" for button in page.button)
        print("code grant", allow_code, frame.to_dict("records"), labels, "transfer", transfer)
        assert frame["close_raw"].tolist() == [101] and frame["date"].tolist() == [str(day)]
        assert ("A.SH" in "".join(labels)) == allow_code and transfer == allow_code
        if not allow_code:
            assert frame["code"].isna().all()
            latest = [item.value for item in page.caption if "最近有据日期" in item.value]
            assert all(str(day) not in item for item in latest)
            assert all("A.SH" not in item.value and "OTHER.SH" not in item.value for item in page.json)
    page.button(key="factor_browser_to_trial").click().run()
    assert page.text_input(key="factor_subjects_up_amount_share").value == "A.SH"


def test_paid_native_market_cap_separates_publication_day_and_grant(tmp_path, monkeypatch):
    business, published = date(2024, 1, 31), date(2024, 2, 1)
    store, ref, grant_dates = _paid_native_asset(
        tmp_path / "cap", "market_cap", "cap",
        "code,date,market_cap_cny,available_at\nA.SH,2024-01-31,12345,2024-02-01T14:00:00+08:00\n",
        business, published,
    )
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
    tests = (
        ("2024-01-01", "2024-12-31", "date", business, [12345]),
        ("2024-01-01", "2024-12-31", "available_at", published, [12345]),
        (business, business, "available_at", published, [12345]),
        (published, published, "available_at", published, []),
    )
    actual = []
    for start, end, axis, day, expected in tests:
        grant_dates(start, end)
        _browse(page, ref, "market_cap", day=day)
        page.selectbox(key="factor_browser_axis_market_cap").set_value(axis)
        _query(page)
        records = page.dataframe[0].value.to_dict("records") if page.dataframe else []
        print("paid cap", start, end, axis, day, records)
        assert all(row["date"] == str(business) and row["available_at"] == "2024-02-01T14:00:00+08:00"
                   for row in records)
        actual.append(([int(row["market_cap_cny"]) for row in records], expected))
    assert all(result == expected for result, expected in actual)


def _paid_native_asset(path, table, product, csv, business, published):
    store, _ = _native_asset(path, table, product, csv, "catalog")
    policy = store.root / "data-products.json"
    config = json.loads(policy.read_text())
    config["products"][product]["fee_class"] = "paid"
    policy.write_text(json.dumps(config))
    grant_path = store.root / "data-entitlements.json"
    grant = dict(
        product_id=product, codes=["A.SH"], markets=["SH"], approved_scope_ref="合成范围", license_ref="合成许可",
        allowed_operations=["read", "import"], allowed_uses=["factor_calculation"], acquire_until="2099-12-31",
        retained_use=True,
    )

    def grant_dates(start, end):
        grant_path.write_text(json.dumps(dict(grants=[dict(grant, start=str(start), end=str(end))])))

    grant_dates(business, published)
    manifest_path = path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["mode"] = "paid"
    manifest["items"][0]["source"]["fee_class"] = "paid"
    manifest_path.write_text(json.dumps(manifest))
    ref = DataStore(store.root).import_files(manifest_path, (path / f"{table}.csv",))
    return store, ref, grant_dates


@pytest.mark.parametrize("period", ["2023-12-31", "2023Q4"])
@pytest.mark.parametrize("axis", ["available_at", "ann_date"])
def test_paid_report_uses_report_period_for_grant(tmp_path, monkeypatch, period, axis):
    business, published = date(2023, 12, 31), date(2024, 2, 1)
    store, ref, grant_dates = _paid_native_asset(
        tmp_path / "reports", "financial_reports", "reports",
        f"code,report_period,ann_date,available_at\nA.SH,{period},{published},2024-02-01T14:00:00+08:00\n",
        business, published,
    )
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
    observed = []
    for start, end, count in ((business, published, 1), (business, business, 1), (published, published, 0)):
        grant_dates(start, end)
        _browse(page, ref, "financial_reports", day=published)
        page.selectbox(key="factor_browser_axis_financial_reports").set_value(axis)
        _query(page)
        records = page.dataframe[0].value.to_dict("records") if page.dataframe else []
        print("paid report", period, axis, start, end, records)
        received = [str(item.value) for item in page.caption]
        observed.append((len(records), any(f"{count} 条获准记录" in item for item in received)))
        assert all(row["code"] == "A.SH" and row["report_period"] == period and row["ann_date"] == str(published)
                   and row["available_at"] == "2024-02-01T14:00:00+08:00"
                   and row["source_ref"] == "raw/reports/financial_reports.csv" for row in records)
    assert observed == [(1, True), (1, True), (0, True)]


def test_next_browse_page_reuses_range_only_while_effective_permission_matches(tmp_path, monkeypatch):
    first = date(2024, 3, 1)
    last = date(2024, 3, 31)
    past = [
        _browse_quote_asset(tmp_path / str(year), "quotes", [(date(year, 1, 1), 10, 20)],
                            ["code", "date", "close_raw"])
        for year in (2021, 2022, 2023)
    ]
    store, current = _browse_quote_asset(
        tmp_path / "2024", "quotes", [(first + timedelta(days=i), 100 + i, 700) for i in range(31)],
        ["code", "date", "close_raw"],
    )
    published = _library(store, *past, (store, current))
    calls = []
    read = DataStore._range_files

    def watched(owner, ref, table, **kwargs):
        paths = read(owner, ref, table, **kwargs)
        if table == "daily_bars" and kwargs.get("start") == date.min and kwargs.get("end") == date.max:
            calls.extend(paths)
        return paths

    monkeypatch.setattr(DataStore, "_range_files", watched)
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
    _browse(page, published, "daily_bars", day=first)
    page.date_input(key="factor_browser_dates").set_value((first, last))
    page.selectbox(key="factor_browser_product").set_value("quotes")
    page.selectbox(key="factor_browser_order_daily_bars").set_value("date")
    page.multiselect(key="factor_browser_fields_daily_bars").set_value(["date", "close_raw"])
    _query(page)
    assert page.dataframe[0].value["close_raw"].tolist() == list(range(100, 125))
    assert any(f"产品获准数据范围 2021-01-01 至 {last}" in x.value for x in page.caption)
    assert len(calls) == 4
    print("page one", "rows", len(page.dataframe[0].value), "historical range files", calls)
    calls.clear()
    page.button(key="factor_browser_next").click().run()
    assert not page.exception
    assert page.dataframe[0].value["close_raw"].tolist() == list(range(125, 131))
    assert any("31 条获准记录" in x.value for x in page.caption)
    assert any(f"产品获准数据范围 2021-01-01 至 {last}" in x.value for x in page.caption)
    print("page two", "rows", len(page.dataframe[0].value), "historical range files", calls)
    assert calls == []
    _product_config(past[0][0], "quotes", ["code", "close_raw"])
    page.run()
    assert any(f"产品获准数据范围 2022-01-01 至 {last}" in x.value for x in page.caption)
    assert all("2021-01-01" not in x.value for x in page.caption if "产品获准数据范围" in x.value)
    spans = [item.value for item in page.caption if "产品获准数据范围" in item.value]
    assert len(calls) == 3 and all(str(past[0][0].root.resolve()) not in path for path in calls)
    print("after grant reduction", spans, "historical range files", calls)


@pytest.mark.parametrize("private_first", [True, False])
def test_equal_quotes_keep_authorized_code_in_either_order(tmp_path, monkeypatch, private_first):
    day = date(2024, 4, 2)
    public = _browse_quote_asset(tmp_path / "public", "quotes", [(day, 101, 830)],
                                 ["code", "date", "close_raw"])
    private = _browse_quote_asset(tmp_path / "private", "quotes", [(day, 101, 830)], ["date", "close_raw"])
    store = public[0]
    ordered = (private, public) if private_first else (public, private)
    published = _library(store, *ordered)
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
    observed = []
    for allow_code in (True, False):
        if not allow_code:
            _product_config(store, "quotes", ["date", "close_raw"])
        _browse(page, published, "daily_bars", code="A.SH OTHER.SH", day=day)
        page.multiselect(key="factor_browser_fields_daily_bars").set_value(["code", "date", "close_raw"])
        page.selectbox(key="factor_browser_order_daily_bars").set_value("close_raw")
        _query(page)
        row, = page.dataframe[0].value.to_dict("records")
        assert (row["date"], row["close_raw"]) == (str(day), 101)
        label, = page.selectbox(key="factor_browser_row").options
        transfer = any(button.key == "factor_browser_to_trial" for button in page.button)
        latest, = [item.value for item in page.caption if item.value.startswith("A.SH · 最近有据日期")]
        context = None
        if transfer:
            page.button(key="factor_browser_to_trial").click().run()
            context = page.session_state["factor_browse_context_up_amount_share"]
        print("equivalent quotes", private_first, allow_code, row, label, latest, transfer, context)
        observed.append((row["code"], "A.SH" in label, latest.endswith(str(day)), transfer, context))
    verified_origin = dict(
        source_root=str(store.root.resolve()), snapshot_id=public[1].snapshot_id, product_id="quotes",
    )
    assert observed[0][:4] == ("A.SH", True, True, True)
    assert {field: observed[0][4][field] for field in verified_origin} == verified_origin
    assert observed[1] == (None, False, False, False, None)


def test_calendar_pagination_reuses_authorized_product_range(tmp_path, monkeypatch):
    first, last = date(2024, 3, 1), date(2024, 3, 31)
    days = [first + timedelta(days=i) for i in range(31)]
    assets = []
    for year in (2021, 2022, 2023, 2024):
        sessions = days if year == 2024 else (date(year, 1, 1),)
        csv = "date,is_trading_day\n" + "".join(f"{session},True\n" for session in sessions)
        assets.append(_native_asset(tmp_path / str(year), "calendar", "sessions", csv, "calendar"))
    store = assets[-1][0]
    published = _library(store, *assets)
    calls = []
    read = DataStore._range_files

    def watched(owner, ref, table, **kwargs):
        paths = read(owner, ref, table, **kwargs)
        if table == "calendar" and (kwargs.get("start"), kwargs.get("end")) == (date.min, date.max):
            calls.extend(paths)
        return paths

    monkeypatch.setattr(DataStore, "_range_files", watched)
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
    _browse(page, published, "calendar", code="", day=first)
    page.date_input(key="factor_browser_dates").set_value((first, last))
    page.selectbox(key="factor_browser_product").set_value("sessions")
    _query(page)
    first_page = page.dataframe[0].value["date"].tolist()
    print("calendar page 1", first_page, "full scope files", calls)
    assert first_page == [str(day) for day in days[:25]]
    assert any(f"产品获准数据范围 2021-01-01 至 {last}" in item.value for item in page.caption)
    assert len(calls) == 4
    calls.clear()
    page.button(key="factor_browser_next").click().run()
    second_page = page.dataframe[0].value["date"].tolist()
    print("calendar page 2", second_page, "full scope files", calls)
    assert second_page == [str(day) for day in days[25:]]
    assert any("31 条获准记录" in item.value for item in page.caption)
    assert any(f"产品获准数据范围 2021-01-01 至 {last}" in item.value for item in page.caption)
    assert calls == []

    _product_config(assets[0][0], "sessions", ["is_trading_day"])
    page.run()
    assert any(f"产品获准数据范围 2022-01-01 至 {last}" in item.value for item in page.caption)
    assert page.dataframe[0].value["date"].tolist() == second_page
    assert len(calls) == 3 and all(str(assets[0][0].root.resolve()) not in path for path in calls)
    print("calendar narrowed", second_page, "full scope files", calls)


@pytest.mark.parametrize("complementary", [False, True], ids=["same-coordinate-grant", "complementary-grants"])
def test_equal_quotes_keep_requested_provenance_in_either_order(tmp_path, monkeypatch, complementary):
    day = date(2024, 4, 2)
    fields = ["code", "date", "close_raw"]
    public_fields = fields[1:] if complementary else fields
    public = _browse_quote_asset(tmp_path / "public", "quotes", [(day, 101, 830)], [*public_fields, "source_ref"])
    private = _browse_quote_asset(tmp_path / "private", "quotes", [(day, 101, 830)], fields)
    store = public[0]
    libraries = [(name, _library(store, *assets)) for name, assets in (
        ("public-only", (public,)), ("private-first", (private, public)), ("public-first", (public, private)),
    )]
    rendered = []
    browse_page = browse.browse_page

    def capture(*args, **kwargs):
        result = browse_page(*args, **kwargs)
        rendered[:] = [result.rows]
        return result

    monkeypatch.setattr(browse, "browse_page", capture)
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
    def readback(label, allow_ref):
        assert not page.exception
        row, = page.dataframe[0].value.to_dict("records")
        visible_row, = rendered[0].to_dict("records")
        picked_label, = page.selectbox(key="factor_browser_row").options
        assert (row["date"], row["close_raw"]) == (str(day), 101)
        if complementary:
            assert "code" not in row
        else:
            assert row["code"] == "A.SH"
        origin, carried_source = None, None
        transfer = next((button for button in page.button if button.key == "factor_browser_to_trial"), None)
        if complementary and label == "public-only":
            assert "A.SH" not in picked_label and visible_row["code"] is None and transfer is None
            assert all("A.SH" not in block.value for block in page.json)
        else:
            assert "A.SH" in picked_label and transfer is not None
            transfer.click().run()
            context = page.session_state["factor_browse_context_up_amount_share"]
            origin = context["source_root"], context["snapshot_id"]
            carried_source = context.get("source_ref_origin")
            assert page.text_input(key="factor_subjects_up_amount_share").value == "A.SH"
            assert page.date_input(key="factor_date_up_amount_share").value == day
        evidence = visible_row.get("source_ref_origin")
        print("quote provenance", complementary, label, allow_ref, row, picked_label, evidence, origin)
        return row["source_ref"], origin, evidence, carried_source

    allowed, revoked = [], []
    for label, published in libraries:
        _product_config(store, "quotes", [*public_fields, "source_ref"])
        _browse(page, published, "daily_bars", day=day)
        page.multiselect(key="factor_browser_fields_daily_bars").set_value([*public_fields, "source_ref"])
        page.selectbox(key="factor_browser_order_daily_bars").set_value("close_raw")
        _query(page)
        allowed.append(readback(label, True))
        if label != "public-only":
            _product_config(store, "quotes", public_fields)
            page.sidebar.radio(key="page_nav").set_value("数据")
            page.session_state["factor_data_mode"] = "因子输入"
            page.session_state["factor_browser_source"] = f"库 · {published.library_id}"
            page.session_state["factor_browser_table"] = "daily_bars"
            page.run()
            revoked.append(readback(label, False))
    public_origin = (str(store.root.resolve()), public[1].snapshot_id)
    coordinate_origin = (str(private[0].root.resolve()), private[1].snapshot_id) if complementary else public_origin
    original_ref = "raw/quotes/daily_bars.csv"
    assert [(ref, origin) for ref, origin, _, _ in allowed] == [
        (original_ref, None if complementary else public_origin),
        (original_ref, coordinate_origin),
        (original_ref, coordinate_origin),
    ]
    evidence = dict(source_root=public_origin[0], snapshot_id=public_origin[1],
                    product_id="quotes", source_ref=original_ref)
    assert [item[2] for item in allowed] == [evidence] * 3
    assert [item[3] for item in allowed[1:]] == [evidence] * 2
    assert [(ref, evidence, carried) for ref, _, evidence, carried in revoked] == [(None, None, None)] * 2
    if complementary:
        assert [item[1] for item in revoked] == [coordinate_origin] * 2


def test_archive_writeback_keeps_equivalent_quote_provenance(tmp_path, monkeypatch):
    code, day = "A.SH", date(2024, 4, 2)
    fields = ["date", "open_raw", "high_raw", "low_raw", "close_raw", "volume_shares", "amount_cny"]
    public = _browse_quote_asset(tmp_path / "ref-A", "quotes", [(day, 101, 830)], [*fields, "source_ref"])
    private = _browse_quote_asset(tmp_path / "code-B", "quotes", [(day, 101, 830)], [*fields, "code"])
    comparison = _browse_quote_asset(
        tmp_path / "vendor-C", "comparison", [(day, 101, 831)], [*fields, "code", "source_ref"],
    )
    store, public_ref = public
    private_store, private_ref = private
    assets = [dict(root=str(owner.root), snapshot_id=ref.snapshot_id, tables=["daily_bars"])
              for owner, ref in (public, private, comparison)]
    ordinary = publish_library(store, asset_snapshots=assets)
    policy_ref = "evidence/synthetic-source-policy.json"
    (store.root / policy_ref).write_text(json.dumps(dict(
        purpose="daily_archive", primary_product_id="quotes", compared_product_ids=["comparison"],
        fields=["amount_cny"], basis="合成日线对照", codes=[code], start=str(day), end=str(day),
        object_days={f"{code}/{day}": {}},
    )))
    assets[0]["source_policy"] = dict(primary_product_id="quotes", basis_ref=policy_ref)
    adjudicated = publish_library(store, asset_snapshots=assets)
    adjudications = []
    query_library = browse.query_library

    def capture(*args, **kwargs):
        result = query_library(*args, **kwargs)
        columns = ["code", "date", "product_id", "snapshot_id", "close_raw", "amount_cny"]
        adjudications.append((result["bars"][columns].to_dict("records"), result["source_differences"]))
        return result

    monkeypatch.setattr(browse, "query_library", capture)
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
    source_ref = "raw/quotes/daily_bars.csv"
    evidence = dict(source_root=str(store.root.resolve()), snapshot_id=public_ref.snapshot_id,
                    product_id="quotes", source_ref=source_ref)
    winner = dict(code=code, date=str(day), product_id="quotes", snapshot_id=private_ref.snapshot_id,
                  close_raw=101, amount_cny=830)

    def readback(label, published):
        assert not page.exception
        row, = page.dataframe[0].value.to_dict("records")
        decisions = adjudications.copy()
        page.button(key="factor_browser_to_trial").click().run()
        context = page.session_state["factor_browse_context_up_amount_share"]
        assert page.text_input(key="factor_subjects_up_amount_share").value == code
        assert page.date_input(key="factor_date_up_amount_share").value == day
        assert (context["source_root"], context["snapshot_id"], context["product_id"]) == (
            str(private_store.root.resolve()), private_ref.snapshot_id, "quotes",
        )
        origin = context.get("source_ref_origin")
        print(label, published.library_id, "page", row, "carry", context, "archive", decisions)
        assert (row["date"], row["close_raw"]) == (str(day), 101)
        if origin is None:
            assert source_ref not in repr(context)
        return row["source_ref"], origin, decisions

    readbacks = []
    for label, published in (("ordinary", ordinary), ("adjudicated", adjudicated)):
        _product_config(store, "quotes", [*fields, "source_ref"])
        _browse(page, published, "daily_bars", code=code, day=day)
        page.selectbox(key="factor_browser_product").set_value("quotes")
        page.multiselect(key="factor_browser_fields_daily_bars").set_value(["date", "close_raw", "source_ref"])
        page.selectbox(key="factor_browser_order_daily_bars").set_value("close_raw")
        adjudications.clear()
        _query(page)
        readbacks.append(readback(f"{label} allowed", published))

        _product_config(store, "quotes", fields)
        page.sidebar.radio(key="page_nav").set_value("数据")
        page.session_state["factor_data_mode"] = "因子输入"
        page.session_state["factor_browser_source"] = f"库 · {published.library_id}"
        page.session_state["factor_browser_table"] = "daily_bars"
        adjudications.clear()
        page.run()
        readbacks.append(readback(f"{label} revoked", published))

    # A different quote on the same date remains unable to supply a reference to the chosen quote.
    _browse(page, adjudicated, "daily_bars", code=code, day=day)
    page.selectbox(key="factor_browser_product").set_value("全部")
    adjudications.clear()
    _query(page)
    other_ref, other_origin, comparison_decisions = readback("adjudicated competing quote", adjudicated)
    assert any(rows == [winner] and any(item["fields"] == ["amount_cny"] for item in differences)
               for rows, differences in comparison_decisions)
    assert (other_ref, other_origin) == (None, None)

    assert all(not decisions for _, _, decisions in readbacks[:2])
    assert all(any(rows == [winner] for rows, _ in decisions) for _, _, decisions in readbacks[2:])
    assert [(ref, origin) for ref, origin, _ in readbacks] == [
        (source_ref, evidence), (None, None), (source_ref, evidence), (None, None),
    ]


def test_free_registration_pagination_reuses_authorized_product_range(tmp_path, monkeypatch):
    first, last = date(2024, 3, 1), date(2024, 3, 31)
    days = [first + timedelta(days=offset) for offset in range(31)]
    assets = []
    for year in (2021, 2022, 2023, 2024):
        publications = days if year == 2024 else [date(year, 1, 1)]
        csv = "table_name,record_key,revision_id,supersedes,published_at,evidence_ref,sha256,verified\n" + "".join(
            f'daily_bars,A.SH|{day}|close_raw,original,,{day}T14:00:00+08:00,quote-{day}.json,{"a" * 64},true\n'
            for day in publications
        )
        assets.append(_native_asset(tmp_path / str(year), "knowledge_evidence", "proofs", csv, "catalog"))
    store = assets[-1][0]
    published = _library(store, *assets)
    range_only, files = [], []
    rows_query = browse._registered_rows
    read = DataStore._range_files

    def tracked_range(*args, **kwargs):
        range_only.append(kwargs.get("range_only", False))
        try:
            return rows_query(*args, **kwargs)
        finally:
            range_only.pop()

    def tracked_files(owner, ref, table, **kwargs):
        paths = read(owner, ref, table, **kwargs)
        if table == "knowledge_evidence" and range_only and range_only[-1]:
            files.extend(paths)
        return paths

    monkeypatch.setattr(browse, "_registered_rows", tracked_range)
    monkeypatch.setattr(DataStore, "_range_files", tracked_files)
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
    _browse(page, published, "knowledge_evidence", day=first)
    page.selectbox(key="factor_browser_product").set_value("proofs")
    page.selectbox(key="factor_browser_order_knowledge_evidence").set_value("published_at")
    page.date_input(key="factor_browser_dates").set_value((first, last))
    _query(page)

    def readback(label, expected_days, start):
        published_days = page.dataframe[0].value["published_at"].str[:10].tolist()
        span, = [item.value for item in page.caption if "产品获准数据范围" in item.value]
        print("registration range", label, published_days, span, "history files", files)
        assert published_days == [str(day) for day in expected_days]
        assert f"{start} 至 {last}" in span
        assert any("31 条获准记录" in item.value for item in page.caption)
        obtained = files.copy()
        files.clear()
        return obtained

    page1 = readback("page one", days[:25], "2021-01-01")
    page.button(key="factor_browser_next").click().run()
    page2 = readback("page two", days[25:], "2021-01-01")
    _product_config(assets[0][0], "proofs", ["table_name", "record_key"])
    page.run()
    narrowed = readback("narrowed", days[25:], "2022-01-01")
    assert len(page1) == 4 and page2 == []
    assert len(narrowed) == 3 and all(str(assets[0][0].root.resolve()) not in path for path in narrowed)


@pytest.mark.parametrize("mixed", [False, True], ids=["paid-only", "mixed"])
def test_paid_registration_range_still_checks_originals(tmp_path, monkeypatch, mixed):
    store, ref, _, _, _ = _paid_supplementary_ep(tmp_path, "2023Q4")
    source = ref
    if mixed:
        free = _native_asset(
            tmp_path / "free", "knowledge_evidence", "factor_input",
            "table_name,record_key,revision_id,supersedes,published_at,evidence_ref,sha256,verified\n"
            f'daily_bars,A.SH|2021-01-01|close_raw,original,,2021-01-01T14:00:00+08:00,quote.json,{"a" * 64},true\n',
            "catalog",
        )
        source = _library(store, free, (store, ref))
    verified_counts = []
    registered_rows = browse._registered_rows

    def tracked_range(owner, selection, scopes, page, page_size, registration_scope, *, range_only=False):
        def verify(owner, ref, product, candidates):
            if range_only:
                verified_counts.append(len(candidates))
            return registration_scope(owner, ref, product, candidates)

        return registered_rows(owner, selection, scopes, page, page_size, verify, range_only=range_only)

    monkeypatch.setattr(browse, "_registered_rows", tracked_range)
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
    _browse(page, source, "knowledge_evidence", day=date(2024, 2, 1))
    page.selectbox(key="factor_browser_product").set_value("factor_input")
    _query(page)
    for step in ("query", "rerender"):
        span, = [item.value for item in page.caption if "产品获准数据范围" in item.value]
        assert span.endswith("2024-02-01")
        if mixed:
            assert "2021-01-01" in span
        assert len(page.dataframe[0].value) == 1
        print("live registration range", mixed, step, span, "originals checked", verified_counts)
        assert verified_counts and all(count > 0 for count in verified_counts)
        verified_counts.clear()
        if step == "query":
            page.run()
