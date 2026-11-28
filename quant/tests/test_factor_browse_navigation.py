import pytest

from test_factor_workbench import open_factors, run_trial


def test_browse_roundtrip_preserves_trial(monkeypatch, tmp_path):
    page = open_factors(monkeypatch, tmp_path)
    page.text_input(key="factor_search").set_value("price_momentum").run()
    page.number_input(key="factor_param_price_momentum_skip").set_value(0).run()
    run_trial(page)
    page.button(key="factor_evidence_row_0").click().run()
    assert page.sidebar.radio(key="page_nav").value == "数据"
    assert any("本次试算的输入" in item.value for item in page.subheader)
    assert any("2024-01-30" in item.value and "2024-01-31" in item.value for item in page.json)
    page.button(key="factor_return_to_draft").click().run()
    assert page.sidebar.radio(key="page_nav").value == "因子库"
    assert page.number_input(key="factor_param_price_momentum_skip").value == 0


@pytest.mark.parametrize("choose_before_browse", [True, False], ids=["factor-first", "data-first"])
def test_published_daily_can_be_brought_into_trial(monkeypatch, tmp_path, choose_before_browse):
    from datetime import date

    from factor_cases import local_volume_source

    store, ref = local_volume_source(tmp_path / "volume", code="A.SH")
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
    if choose_before_browse:
        page.selectbox(key="factor_id").set_value("traded_amount").run()
    page.sidebar.radio(key="page_nav").set_value("数据").run()
    page.radio(key="factor_data_mode").set_value("因子输入").run()
    page.selectbox(key="factor_browser_source").set_value("快照 · " + ref.snapshot_id).run()
    page.selectbox(key="factor_browser_table").set_value("daily_bars").run()
    page.text_input(key="factor_browser_codes").set_value("A.SH")
    day = date(2024, 4, 1)
    page.date_input(key="factor_browser_dates").set_value((day, day))
    page.button(key="factor_browser_query_submit").click().run()
    assert not page.exception
    result = next(table.value for table in page.dataframe if "amount_cny" in table.value.columns)
    assert result.amount_cny.tolist() == [15_000_000]
    page.button(key="factor_browser_to_trial").click().run()
    assert page.sidebar.radio(key="page_nav").value == "因子库"
    if not choose_before_browse:
        page.selectbox(key="factor_id").set_value("traded_amount").run()
    observed = (
        page.selectbox(key="factor_source_traded_amount").value,
        page.text_input(key="factor_subjects_traded_amount").value,
        page.date_input(key="factor_date_traded_amount").value,
    )
    print("带入来源、对象及日期", choose_before_browse, observed)
    assert observed == (ref.snapshot_id, "A.SH", day)
    assert page.selectbox(key="factor_source_traded_amount").value == ref.snapshot_id
    assert not page.session_state["factor_attempts"]
    run_trial(page)
    reading = page.dataframe[0].value.iloc[0]["读数"]
    assert reading == 15_000_000.0
    print("带入后的成交额", choose_before_browse, reading)
    if not choose_before_browse:
        page.selectbox(key="factor_id").set_value("pair_hedge_beta").run()
        assert page.selectbox(key="factor_source_pair_hedge_beta").value == ref.snapshot_id
        assert page.date_input(key="factor_date_pair_hedge_beta").value == day
        assert page.text_input(key="factor_role_pair_hedge_beta_0").value == "A.SH"
        assert page.text_input(key="factor_role_pair_hedge_beta_1").value == ""
        assert any("第二腿" in error.value for error in page.error)
        assert not any(button.key == "factor_compute" for button in page.button)
        assert len(page.session_state["factor_attempts"]) == 1
        page.button(key="factor_load_teaching").click().run()
        assert page.selectbox(key="factor_source_pair_hedge_beta").value == "教学样例"


@pytest.mark.parametrize("entry", ["import", "direct", "teach-other"])
def test_switching_factor_restores_edited_import_date(monkeypatch, tmp_path, entry):
    from datetime import date

    from factor_cases import local_volume_source
    from hetu_quant.ui import factor_page
    from test_factor_workbench_review import _browse, _query

    imported, edited = date(2024, 4, 1), date(2024, 1, 10)
    store, ref = local_volume_source(tmp_path / "quotes", code="A.SH", amount_by_date={str(edited): 20_000_000})
    submitted = []
    compute = factor_page._compute_trial

    def trace(trial, root):
        result = compute(trial, root)
        submitted.append((trial.source, trial.subjects, trial.trade_date, result.rows[0]["value"]))
        return result

    monkeypatch.setattr(factor_page, "_compute_trial", trace)
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
    if entry != "direct":
        _browse(page, ref, "daily_bars", day=imported)
        _query(page)
        page.button(key="factor_browser_to_trial").click().run()
    page.selectbox(key="factor_id").set_value("traded_amount").run()
    expected = []
    if entry == "direct":
        page.selectbox(key="factor_source_traded_amount").set_value(ref.snapshot_id).run()
        page.text_input(key="factor_subjects_traded_amount").set_value("A.SH").run()
    else:
        run_trial(page)
        assert page.dataframe[0].value["读数"].tolist() == [15_000_000]
        expected.append((ref, ("A.SH",), imported, 15_000_000))
    page.date_input(key="factor_date_traded_amount").set_value(edited).run()
    run_trial(page)
    assert page.dataframe[0].value["读数"].tolist() == [20_000_000]
    expected.append((ref, ("A.SH",), edited, 20_000_000))
    before_switch = list(submitted)
    previous_attempts = tuple(page.session_state["factor_attempts"])
    page.selectbox(key="factor_id").set_value("up_amount_share").run()
    if entry == "teach-other":
        page.button(key="factor_load_teaching").click().run()
        assert page.selectbox(key="factor_source_up_amount_share").value == "教学样例"
    page.selectbox(key="factor_id").set_value("traded_amount").run()
    draft = (page.selectbox(key="factor_source_traded_amount").value,
             page.text_input(key="factor_subjects_traded_amount").value,
             page.date_input(key="factor_date_traded_amount").value)
    print(entry, "切换后草稿", draft, "生产调用增量", len(submitted) - len(before_switch),
          "尝试增量", len(page.session_state["factor_attempts"]) - len(previous_attempts))
    assert submitted == before_switch
    assert tuple(page.session_state["factor_attempts"]) == previous_attempts
    run_trial(page)
    print(entry, "生产请求及读数", submitted)
    assert draft == (ref.snapshot_id, "A.SH", edited)
    assert submitted == [*expected, (ref, ("A.SH",), edited, 20_000_000)]
    attempts = list(page.session_state["factor_attempts"].values())
    assert [item.id for item in attempts[:-1]] == list(previous_attempts)
    assert [(item.trial.trade_date, item.result.rows[0]["value"]) for item in attempts] == [
        (day, reading) for _, _, day, reading in submitted
    ]
    _browse(page, ref, "daily_bars", day=imported)
    _query(page)
    page.button(key="factor_browser_to_trial").click().run()
    assert page.date_input(key="factor_date_traded_amount").value == imported
    assert len(submitted) == len(expected) + 1
    run_trial(page)
    assert submitted[-1] == (ref, ("A.SH",), imported, 15_000_000)
    print(entry, "再次带入", submitted[-1])
    if entry == "teach-other":
        page.selectbox(key="factor_id").set_value("up_amount_share").run()
        assert page.selectbox(key="factor_source_up_amount_share").value == "教学样例"
