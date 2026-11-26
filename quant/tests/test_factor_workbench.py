import json
from dataclasses import replace
from datetime import date, time
from pathlib import Path

from hetu_quant.ui import app
from streamlit.testing.v1 import AppTest


def open_factors(monkeypatch, tmp_path, evidence=None):
    monkeypatch.setenv("HETU_QUANT_ROOT", str(evidence or tmp_path / "research"))
    monkeypatch.setenv("HETU_DATA_ROOT", str(tmp_path / "data"))
    page = AppTest.from_file(str(Path(app.__file__)), default_timeout=15).run()
    page.sidebar.radio(key="page_nav").set_value("因子库").run()
    assert not page.exception
    return page


def test_catalog_navigation(monkeypatch, tmp_path):
    page = open_factors(monkeypatch, tmp_path)
    assert page.header[0].value == "因子库"
    assert any("def up_amount_share" in code.value for code in page.code)
    page.text_input(key="factor_search").set_value("ETF 成交额").run()
    assert "traded_amount" in page.selectbox(key="factor_id").value
    page.text_input(key="factor_search").set_value("未来收益标签").run()
    assert any("未来收益标签" in element.value for element in page.markdown)


def test_daily_financial_and_breadth_examples(monkeypatch, tmp_path):
    page = open_factors(monkeypatch, tmp_path)
    outputs = {}
    for factor_id, expected in (
        ("up_amount_share", "40%"),
        ("earnings_yield", "6%"),
        ("breadth_above_ma_share", "60%"),
    ):
        page.selectbox(key="factor_id").set_value(factor_id).run()
        page.button(key="factor_compute").click().run()
        assert not page.exception
        outputs[factor_id] = page.dataframe[0].value.to_dict("records")
        assert outputs[factor_id][0]["读数"] == expected
    page.selectbox(key="factor_id").set_value("earnings_yield").run()
    page.date_input(key="factor_date_earnings_yield").set_value(date(2024, 2, 1)).run()
    page.button(key="factor_compute").click().run()
    assert page.dataframe[0].value.iloc[0]["读数"] == "8%"
    outputs["earnings_yield_revised"] = page.dataframe[0].value.to_dict("records")
    print(outputs)


def test_financial_cutoff_can_change_within_observation_day(monkeypatch, tmp_path):
    page = open_factors(monkeypatch, tmp_path)
    page.selectbox(key="factor_id").set_value("earnings_yield").run()
    day = page.date_input(key="factor_date_earnings_yield")
    assert day.value == date(2024, 1, 31)
    page.button(key="factor_compute").click().run()
    assert page.dataframe[0].value.iloc[0]["读数"] == "6%"
    assert any("2024-01-31 15:00:00+08:00" in item.value for item in page.caption)

    cutoff = page.time_input(key="factor_cutoff_earnings_yield")
    assert cutoff.value == time(15)
    cutoff.set_value(time(19)).run()
    assert not page.dataframe
    page.button(key="factor_compute").click().run()
    assert not page.exception
    assert page.date_input(key="factor_date_earnings_yield").value == date(2024, 1, 31)
    assert page.dataframe[0].value.iloc[0]["读数"] == "8%"
    assert any("2024-01-31 19:00:00+08:00" in item.value for item in page.caption)
    assert any("2024-01-31T18:00:00+08:00" in item.value for item in page.json)


def test_evidence_states(monkeypatch, tmp_path):
    import pytest
    from factor_assertions import checked

    root = tmp_path / "evidence"
    monkeypatch.setenv("HETU_FACTOR_EVIDENCE_ROOT", str(root))
    page = open_factors(monkeypatch, tmp_path)
    assert "逻辑自测 · 未测" in [item.value for item in page.subheader]
    for case in ("positive", "boundary", "local"):
        with checked(root, case, "up_amount_share", case, 0.4) as actual:
            actual["value"] = 0.4
    page.run()
    assert "逻辑自测 · 通过" in [item.value for item in page.subheader]
    with pytest.raises(AssertionError), checked(root, "boundary", "up_amount_share", "boundary", 0.4) as actual:
        actual["value"] = 0.415
        assert actual["value"] == 0.4
    page.run()
    assert "逻辑自测 · 失败" in [item.value for item in page.subheader]
    page.text_area(key="factor_parameters_up_amount_share_默认").set_value('{"window":5}').run()
    assert "逻辑自测 · 未测" in [item.value for item in page.subheader]
    assert "效果未验证" in [item.value for item in page.subheader]


def test_local_input_and_no_side_effects(monkeypatch, tmp_path):
    from factor_cases import example_query, local_fact_source
    from hetu_data.library import publish_library
    from hetu_quant import backtest, worker

    _, document, _ = example_query("earnings_yield")
    source_store, ref = local_fact_source(tmp_path / "source", {"market_cap": document["tables"]["market_cap"]})
    library = publish_library(
        source_store,
        asset_snapshots=(
            dict(root=str(source_store.root), snapshot_id=ref.snapshot_id, tables=["knowledge_evidence"]),
        ),
    )
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(source_store.root))
    calls = {"backtests": 0, "jobs": 0}

    def fail(key):
        def record(*args, **kwargs):
            calls[key] += 1
            raise AssertionError(f"unexpected {key}")

        return record

    monkeypatch.setattr(backtest, "run_backtest", fail("backtests"))
    monkeypatch.setattr(worker, "process_job", fail("jobs"), raising=False)
    research = tmp_path / "research"

    def inventory():
        return {str(path.relative_to(research)): path.read_bytes() for path in research.rglob("*") if path.is_file()}

    before = inventory()
    page.selectbox(key="factor_id").set_value("earnings_yield").run()
    page.button(key="factor_compute").click().run()
    assert page.dataframe[0].value.iloc[0]["读数"] == "6%"
    for source in (ref.snapshot_id, library.library_id):
        page.selectbox(key="factor_source_earnings_yield").set_value(source).run()
        assert not page.dataframe
        page.button(key="factor_compute").click().run()
        assert not page.exception
        row = page.dataframe[0].value.iloc[0]
        assert row["subject"] == "A" and row["field"] == "net_profit_ex"
        assert "原公告" in row["reason"]
        print(source, row.to_dict())
    assert inventory() == before
    assert calls == {"backtests": 0, "jobs": 0}


def test_workbench_dividend_gap_hides_future_table_observation(monkeypatch, tmp_path):
    from factor_cases import native_dividend_correction

    store, ref, _ = native_dividend_correction(tmp_path / "source", observed_at="2024-01-31T10:00:00+00:00")
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
    page.selectbox(key="factor_id").set_value("dividend_yield").run()
    page.selectbox(key="factor_source_dividend_yield").set_value(ref.snapshot_id).run()
    page.button(key="factor_compute").click().run()
    expander = next(item for item in page.expander if item.label == "缺口与冲突原件")
    payload = expander.json[0].value
    gap, = json.loads(payload)
    assert "缺少" in gap["reason"]
    proof, = gap["evidence"]
    assert proof["value"] == .3 and proof["announcement_ref"] == "october.pdf"
    assert "2024-01-31T10:00:00+00:00" not in payload and "18:00:00" not in payload and "0.4" not in payload
    print("15:00 visible gap", payload)
    page.time_input(key="factor_cutoff_dividend_yield").set_value(time(19)).run()
    page.button(key="factor_compute").click().run()
    assert not page.exception and page.dataframe[0].value.iloc[0]["读数"] == "6%"
    print("19:00 visible readout", page.dataframe[0].value.to_dict("records"))


def test_workbench_cycle_evidence_respects_cutoff(monkeypatch, tmp_path):
    from factor_cases import native_dividend_correction
    from hetu_quant.factors import compute_factor

    for value in (.40, .90):
        root = tmp_path / str(value)
        store, ref, query = native_dividend_correction(
            root, observed_at="2024-01-31T18:00:00+08:00", cycle_value=value,
        )
        page = open_factors(monkeypatch, root)
        monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
        page.selectbox(key="factor_id").set_value("dividend_yield").run()
        page.selectbox(key="factor_source_dividend_yield").set_value(ref.snapshot_id).run()
        for hour in (15, 19):
            page.time_input(key="factor_cutoff_dividend_yield").set_value(time(hour)).run()
            page.button(key="factor_compute").click().run()
            assert not page.exception
            expander = next(item for item in page.expander if item.label == "缺口与冲突原件")
            gap, = json.loads(expander.json[0].value)
            api = compute_factor(store, ref, replace(query, cutoff=query.cutoff.replace(hour=hour)))
            assert gap == api.gaps[0] and gap["field"] == "revision_cycle"
            assert {(p["revision_id"], p["supersedes"], p["value"]) for p in gap["evidence"]} == {
                ("v1", "v2", .30), ("v2", "v1", .31),
            }
            if hour == 15:
                assert all(not {"observed_at", "observed_value", "matched"}.intersection(p) for p in gap["evidence"])
            print(f"{hour}:00", json.dumps(gap, ensure_ascii=False))


def test_window_type_cannot_inherit_saved_receipt(monkeypatch, tmp_path):
    from hetu_quant.factors.selftest import record_selftest

    for case in ("positive", "boundary", "local"):
        record_selftest(
            tmp_path,
            "up_amount_share",
            "1",
            {"window": 1},
            case,
            status="passed",
            node="historical",
            expected=1,
            actual={"value": 1},
        )
    monkeypatch.setenv("HETU_FACTOR_EVIDENCE_ROOT", str(tmp_path))
    page = open_factors(monkeypatch, tmp_path)
    widget = "factor_parameters_up_amount_share_默认"
    page.text_area(key=widget).set_value('{"window":1}').run()
    assert any("通过" in item.value for item in page.subheader)
    for invalid in ("true", "0", "1.0"):
        page.text_area(key=widget).set_value('{"window":' + invalid + "}").run()
        assert not page.exception
        assert any("window" in item.value for item in page.error)
        assert not any("通过" in item.value for item in page.subheader)
    page.text_area(key=widget).set_value('{"window":1}').run()
    assert any("通过" in item.value for item in page.subheader)


def test_nested_factor_parameter_has_coordinate_and_no_exception(monkeypatch, tmp_path):
    page = open_factors(monkeypatch, tmp_path)
    page.selectbox(key="factor_id").set_value("weighted_factor_score").run()
    page.text_area(key="factor_parameters_weighted_factor_score_默认").set_value(
        '{"components":{"traded_amount":{"weight":1,"direction":1,"parameters":{"mode":[]}}}}'
    ).run()
    assert not page.exception
    assert any("components.traded_amount.parameters.mode" in item.value for item in page.error)
    assert "效果未验证" in [item.value for item in page.subheader]


def test_workbench_reads_actual_formula_receipt(monkeypatch, tmp_path):
    from hetu_quant.factors.selftest import record_selftest

    record_selftest(
        tmp_path,
        "pair_half_life",
        "1",
        {"window": 4},
        "formula:discriminator",
        status="passed",
        node="test_formula_discriminator[pair_half_life]",
        expected={"field": "phi"},
        actual={"gaps": [{"field": "phi", "phi": -0.5}]},
        inputs={"case": "pair_half_life", "variant": "negative_phi"},
    )
    monkeypatch.setenv("HETU_FACTOR_EVIDENCE_ROOT", str(tmp_path))
    page = open_factors(monkeypatch, tmp_path)
    page.selectbox(key="factor_id").set_value("pair_half_life").run()
    assert not page.exception
    assert any("test_formula_discriminator[pair_half_life]" in item.value for item in page.json)
    assert any(str(tmp_path) in item.value for item in page.caption)
