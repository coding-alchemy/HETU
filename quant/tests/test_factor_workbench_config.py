import json
import math
from dataclasses import replace
from datetime import date, time

import pytest

from backtest_cases import build_case_store
from factor_cases import example_query
from hetu_quant import factors
from hetu_quant.data import DataStore
from hetu_quant.factors import compute_factor, list_factors
from hetu_quant.factors.core import MissingFact, parameter_identity
from hetu_quant.factors.selftest import replay_request
from hetu_quant.factors.workbench import build_trial
from hetu_quant.ui import factor_page
from test_factor_workbench import open_factors, run_trial


def test_trial_owns_parameters_and_source(tmp_path):
    from hetu_data.models import LibraryRef, SnapshotRef

    source, _, query = example_query("price_momentum")
    trial = build_trial(source, query)
    query.parameters["skip"] = 0
    trial.request.parameters["skip"] = 0
    assert trial.request.parameters == {"window": 1, "skip": 1}
    assert trial.parameter_key == parameter_identity(trial.request.parameters)
    assert trial.purpose == "factor_calculation"
    first = build_trial(SnapshotRef("same", tmp_path), query)
    other_root = build_trial(SnapshotRef("same", tmp_path / "other"), query)
    library = build_trial(LibraryRef("same", tmp_path), query)
    assert len({first, other_root, library}) == 3


@pytest.mark.parametrize(
    ("parameters", "field"),
    [({"window": True}, "window"), ({"window": 1.0}, "window"), ({"window": 0}, "window"), ({"widnow": 1}, "widnow")],
)
def test_configuration_errors_keep_field_path(parameters, field):
    source, _, query = example_query("price_momentum")
    with pytest.raises(MissingFact) as error:
        build_trial(source, replace(query, parameters=parameters))
    assert error.value.field == field


def test_registered_presets_can_be_frozen():
    for definition in list_factors():
        source, _, query = example_query(definition.factor_id)
        for preset in definition.presets.values():
            trial = build_trial(source, replace(query, parameters=preset))
            assert trial.request.parameters == preset, definition.factor_id


def test_traded_amount_mode_updates_the_frozen_component(monkeypatch, tmp_path):
    factor = "robust_cross_section_z"
    page = open_factors(monkeypatch, tmp_path)
    page.selectbox(key="factor_id").set_value(factor).run()
    raw_key = f"factor_parameters_{factor}_默认"
    mode_key = f"factor_param_{factor}_mode"
    daily = [-1 / 1.4836, 0, 98 / 1.4836]

    for index, mode in enumerate(("daily", "mean", "daily", "mean")):
        if index in (1, 2):
            page.selectbox(key=mode_key).set_value(mode).run()
            raw = json.loads(page.text_area(key=raw_key).value)
            assert raw["component_parameters"] == {"mode": mode, "window": 20}
        elif index == 3:
            page.text_area(key=raw_key).set_value(
                '{"component_parameters": {"mode": "mean", "window": 20}}'
            ).run()
            assert page.selectbox(key=mode_key).value == "mean"
        assert len(page.session_state["factor_attempts"]) == index
        run_trial(page)
        assert not page.exception
        attempt = list(page.session_state["factor_attempts"].values())[-1]
        result = attempt.result
        assert result is not None
        assert attempt.trial.request.parameters["mode"] == mode
        if index:
            assert attempt.trial.request.parameters["component_parameters"] == {"mode": mode, "window": 20}
        if mode == "daily":
            assert page.dataframe[0].value["读数"].tolist() == pytest.approx(daily)
            assert not result.gaps
        else:
            assert not result.rows
            assert any(gap["field"] == "components.traded_amount.calendar" and "20 个交易日" in gap["reason"]
                       for gap in result.gaps)
        print(mode, attempt.trial.request.parameters, [row["value"] for row in result.rows], result.gaps)

    source, document, request = example_query(factor, params={"mode": "mean"})
    result = compute_factor(None, source, build_trial(source, request).request)
    assert not result.rows and result.gaps[0]["field"] == "components.traded_amount.calendar"
    archived_request = replay_request(dict(
        factor_id=factor,
        execution_parameters=dict(factors.get_factor(factor).defaults, mode="mean"),
        replay=dict(document, cutoff=request.cutoff.isoformat()),
    ))
    archived_result = compute_factor(None, source, archived_request)
    assert "component_parameters" not in archived_result.parameters
    assert [row["value"] for row in archived_result.rows] == pytest.approx(daily)
    print("legacy replay", archived_result.parameters, [row["value"] for row in archived_result.rows])


@pytest.mark.parametrize("use_library", [False, True], ids=["snapshot", "library"])
def test_purpose_granted_source_can_run_to_discover_absent_tables(monkeypatch, tmp_path, use_library):
    from hetu_data.library import publish_library

    store, snapshot = build_case_store(
        tmp_path / "quotes",
        dict(
            csvs={"daily_bars": "code,date,open_raw,high_raw,low_raw,close_raw,volume_shares,amount_cny\n"
                               "A.SH,2024-04-01,10,10,10,10,1,230\n"},
            table_products={"daily_bars": ("allowed_daily", "daily")},
            root_config=True,
            permissions={"daily_bars": {"allowed_uses": ["factor_calculation"]}},
            obtained_at="2024-04-03T15:00:00+08:00",
        ),
    )
    source_id = snapshot.snapshot_id
    if use_library:
        source_id = publish_library(
            store, asset_snapshots=(dict(root=str(store.root), snapshot_id=source_id, tables=["daily_bars"]),)
        ).library_id
    compute = factor_page._compute_trial
    read_range = DataStore.read_range
    calls = []
    reads = []

    def record(trial, root):
        calls.append(trial)
        return compute(trial, root)

    def record_read(*args, **kwargs):
        reads.append((args, kwargs))
        return read_range(*args, **kwargs)

    monkeypatch.setattr(factor_page, "_compute_trial", record)
    monkeypatch.setattr(DataStore, "read_range", record_read)
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
    factor = "earnings_yield"
    page.selectbox(key="factor_id").set_value(factor).run()
    page.selectbox(key=f"factor_source_{factor}").set_value(source_id)
    page.date_input(key=f"factor_date_{factor}").set_value(date(2024, 4, 1))
    page.text_input(key=f"factor_subjects_{factor}").set_value("A.SH").run()
    page.run()
    assert not calls and not reads and not page.session_state["factor_attempts"]
    visible_checks = [item.value for item in page.markdown if "缺输入" in item.value]
    assert any("net_profit_ex" in check for check in visible_checks)
    assert any("market_cap_cny" in check for check in visible_checks)
    assert not page.button(key="factor_compute").disabled
    run_trial(page)
    attempt = next(iter(page.session_state["factor_attempts"].values()))
    assert calls == [attempt.trial] and attempt.result is not None
    assert not attempt.result.rows
    assert any(gap["field"] == "net_profit_ex" for gap in attempt.result.gaps)
    assert any("net_profit_ex" in repr(table.value) for table in page.dataframe)
    print(source_id, visible_checks, attempt.result.gaps, "calls", len(calls))

    policy_path = store.root / "data-products.json"
    policy = json.loads(policy_path.read_text())
    policy["products"]["allowed_daily"]["permission"]["allowed_uses"] = ["daily_archive"]
    policy_path.write_text(json.dumps(policy))
    page.run()
    assert page.button(key="factor_compute").disabled
    assert len(calls) == len(page.session_state["factor_attempts"]) == 1
    assert not reads
    print(
        "revoked", [warning.value for warning in page.warning], "formula calls", len(calls), "table reads", len(reads)
    )


@pytest.mark.parametrize("entry", ["ordinary", "json"])
@pytest.mark.parametrize(("factor", "field", "invalid"), [
    ("atr_position_size", "direction", 0),
    ("atr_position_size", "risk_amount", 0),
    ("atr_position_size", "multiple", 0),
    ("residual_volatility", "window", 2),
    ("vol_target_scale", "target_vol", 0.0),
    ("vol_target_scale", "epsilon", 0.0),
    ("risk_adjusted_momentum", "epsilon", 0.0),
    ("market_beta", "window", 1),
    ("max_drawdown", "window", 1),
    ("risk_adjusted_momentum", "vol_window", 1),
    ("etf_dual_momentum", "vol_window", 1),
    ("etf_dual_momentum", "epsilon", 0.0),
    ("robust_cross_section_z", "epsilon", 0.0),
    ("weighted_factor_score", "epsilon", 0.0),
    ("vol_target_scale", "vol_window", 1),
    ("positive_score_vol_weight", "vol_window", 1),
    ("positive_score_vol_weight", "epsilon", 0.0),
    ("risk_budget_vol_weight", "vol_window", 1),
    ("risk_budget_vol_weight", "risk_budget", 0.0),
    ("trend_vol_weight", "vol_window", 1),
    ("trend_vol_weight", "target_vol", 0.0),
    ("trend_vol_weight", "cap", 0.0),
])
def test_formula_parameter_rejected_before_preview_or_compute(monkeypatch, tmp_path, factor, field, invalid, entry):
    preview = factor_page.preview_inputs
    previews = []
    runs = []

    def record(store, trial):
        previews.append(trial)
        return preview(store, trial)

    compute = factor_page._compute_trial

    def record_run(trial, root):
        runs.append(trial)
        return compute(trial, root)

    monkeypatch.setattr(factor_page, "preview_inputs", record)
    monkeypatch.setattr(factor_page, "_compute_trial", record_run)
    page = open_factors(monkeypatch, tmp_path)
    page.selectbox(key="factor_id").set_value(factor).run()
    raw_key = f"factor_parameters_{factor}_默认"
    widget_key = f"factor_param_{factor}_{field}"
    parameters = json.loads(page.text_area(key=raw_key).value)
    recovery = {
        ("atr_position_size", "risk_amount"): ("direction", -1, -250),
        ("atr_position_size", "multiple"): ("direction", -1, -250),
        ("vol_target_scale", "epsilon"): ("target_vol", 0.05, 0.25),
    }.get((factor, field))
    if recovery:
        other_field, other_value, _ = recovery
        parameters[other_field] = other_value
        page.text_area(key=raw_key).set_value(json.dumps(parameters)).run()
    previews.clear()
    if entry == "ordinary":
        page.number_input(key=widget_key).set_value(invalid).run()
    else:
        page.text_area(key=raw_key).set_value(json.dumps(dict(parameters, **{field: invalid}))).run()
    assert not page.exception
    assert any(f"参数错误 · {field}" in error.value for error in page.error)
    assert all(button.disabled for button in page.button if button.key == "factor_compute")
    assert not previews and not runs and not page.session_state["factor_attempts"]
    print(factor, entry, [error.value for error in page.error], "preview/compute/attempt counts", 0, 0, 0)
    if entry == "json":
        with monkeypatch.context() as safe:
            def unexpected_fact_read(*args, **kwargs):
                pytest.fail("invalid request reached the production fact loader")

            safe.setattr(factors, "read_facts", unexpected_fact_read)
            source, _, request = example_query(factor, params={field: invalid})
            assert compute_factor(None, source, request).gaps[0]["field"] == field
    if recovery:
        other_field, other_value, expected = recovery
        assert json.loads(page.text_area(key=raw_key).value)[other_field] == other_value
        if entry == "ordinary":
            page.number_input(key=widget_key).set_value(parameters[field]).run()
        else:
            page.text_area(key=raw_key).set_value(json.dumps(parameters)).run()
        if field == "epsilon":
            epsilon = page.number_input(key=widget_key)
            assert float(epsilon.proto.format % epsilon.value) == parameters[field]
            print("displayed epsilon", epsilon.proto.format % epsilon.value)
        assert not runs and not page.session_state["factor_attempts"]
        assert not page.button(key="factor_compute").disabled
        run_trial(page)
        attempt, = page.session_state["factor_attempts"].values()
        assert runs == [attempt.trial] and attempt.result is not None
        assert attempt.trial.request.parameters == parameters
        assert not attempt.result.gaps
        assert page.dataframe[0].value["读数"].tolist() == pytest.approx([expected])
        print(factor, field, entry, "restored", parameters, page.dataframe[0].value["读数"].tolist())
    if (factor, field) == ("risk_adjusted_momentum", "epsilon"):
        page.number_input(key=widget_key).set_value(0.001).run()
        epsilon = page.number_input(key=widget_key)
        assert float(epsilon.proto.format % epsilon.value) == 0.001
        assert not page.session_state["factor_attempts"]
        run_trial(page)
        assert page.dataframe[0].value["读数"].tolist() == pytest.approx([0.08900778619920988])
        print("epsilon", page.dataframe[0].value["读数"].tolist())


def test_invalid_component_parameter_is_reported_before_loading_facts(monkeypatch, tmp_path):
    factor = "weighted_factor_score"
    configuration = {"components": {"atr_position_size": {
        "weight": 1.0, "direction": 1, "parameters": {"risk_amount": 0},
    }}}
    field = "components.atr_position_size.parameters.risk_amount"
    page = open_factors(monkeypatch, tmp_path)
    page.selectbox(key="factor_id").set_value(factor).run()
    raw_key = f"factor_parameters_{factor}_默认"
    page.text_area(key=raw_key).set_value(json.dumps(configuration)).run()
    assert any(f"参数错误 · {field}" in error.value for error in page.error)
    assert all(button.disabled for button in page.button if button.key == "factor_compute")
    assert not page.session_state["factor_attempts"]

    def unexpected_fact_read(*args, **kwargs):
        pytest.fail("invalid component request reached the production fact loader")

    monkeypatch.setattr(factors, "read_facts", unexpected_fact_read)
    source, _, request = example_query(factor, params=configuration)
    gap, = compute_factor(None, source, request).gaps
    assert gap["field"] == field
    print("nested configuration rejected before facts", gap)


@pytest.mark.parametrize(("factor", "field", "invalid", "entry"), [
    ("rolling_return_correlation", "window", 1, "ordinary"),
    ("rolling_return_correlation", "window", 1, "json"),
    ("etf_tracking_error", "window", 1, "ordinary"),
    ("etf_tracking_error", "window", 1, "json"),
    ("sector_rotation_score", "epsilon", 0.0, "ordinary"),
    ("sector_rotation_score", "epsilon", 0.0, "json"),
    ("sector_rotation_score", "weights", [1, 1, 1], "json"),
])
def test_followup_constraints_stop_trials_before_data_reads(monkeypatch, tmp_path, factor, field, invalid, entry):
    previews, runs, reads = [], [], []
    preview, compute, read = factor_page.preview_inputs, factor_page._compute_trial, factors.read_facts

    def record_preview(store, trial):
        previews.append(trial)
        return preview(store, trial)

    def record_run(trial, root):
        runs.append(trial)
        return compute(trial, root)

    def record_read(store, source, definition, request):
        reads.append(request)
        return read(store, source, definition, request)

    monkeypatch.setattr(factor_page, "preview_inputs", record_preview)
    monkeypatch.setattr(factor_page, "_compute_trial", record_run)
    monkeypatch.setattr(factors, "read_facts", record_read)
    page = open_factors(monkeypatch, tmp_path)
    page.selectbox(key="factor_id").set_value(factor).run()
    page.time_input(key=f"factor_cutoff_{factor}").set_value(time(15, 30)).run()
    raw_key = f"factor_parameters_{factor}_默认"
    widget_key = f"factor_param_{factor}_{field}"
    valid = json.loads(page.text_area(key=raw_key).value)
    if factor == "sector_rotation_score":
        valid.update(weights=[1, 0, 0, 0], epsilon=0.002)
        page.text_area(key=raw_key).set_value(json.dumps(valid)).run()
        # Only momentum is weighted. Its three returns are 0.10, 0.20, and 0.30.
        edge = 0.1 / (1.4826 * 0.1 + valid["epsilon"])
        expected = [-edge, 0, edge]
    elif factor == "rolling_return_correlation":
        valid["window"] = 2
        # The final two daily returns are (0.01, 0.02) and (0.02, 0.04).
        expected = [1]
    else:
        # ETF total returns exceed the flat benchmark by 0.01 and 0.03.
        expected = [0.02 * math.sqrt(126)]
    invalid_parameters = dict(valid, **{field: invalid})
    previews.clear()
    if entry == "ordinary":
        page.number_input(key=widget_key).set_value(invalid).run()
    else:
        page.text_area(key=raw_key).set_value(json.dumps(invalid_parameters)).run()

    assert not page.exception
    errors = [error.value for error in page.error]
    enabled = any(not button.disabled for button in page.button if button.key == "factor_compute")
    attempts = page.session_state["factor_attempts"]
    print(factor, field, entry, errors, "submit enabled", enabled,
          "preview/read/compute/attempt", len(previews), len(reads), len(runs), len(attempts))
    assert any(f"参数错误 · {field}" in error for error in errors)
    assert not enabled and not previews and not reads and not runs and not attempts
    assert json.loads(page.text_area(key=raw_key).value) == invalid_parameters
    assert page.time_input(key=f"factor_cutoff_{factor}").value == time(15, 30)

    source, _, request = example_query(factor, params=invalid_parameters)
    result = compute_factor(None, source, request)
    assert not result.rows and [gap["field"] for gap in result.gaps] == [field]
    if field == "weights":
        negative = replace(request, parameters=dict(invalid_parameters, weights=[-1, 1, 1, 1]))
        result = compute_factor(None, source, negative)
        assert not result.rows and [gap["field"] for gap in result.gaps] == ["weights"]
    assert not reads

    if entry == "ordinary":
        page.number_input(key=widget_key).set_value(valid[field]).run()
    else:
        page.text_area(key=raw_key).set_value(json.dumps(valid)).run()
    assert json.loads(page.text_area(key=raw_key).value) == valid
    assert not reads and not runs and not page.session_state["factor_attempts"]
    assert not page.button(key="factor_compute").disabled
    run_trial(page)
    attempt, = page.session_state["factor_attempts"].values()
    assert runs == [attempt.trial] and reads == [attempt.trial.request]
    assert attempt.trial.cutoff.time() == time(15, 30)
    assert attempt.trial.request.parameters == valid and not attempt.result.gaps
    actual = page.dataframe[0].value["读数"].tolist()
    assert actual == pytest.approx(expected)
    print(factor, field, entry, "restored", valid, "value", actual, "calls", len(reads), len(runs))


def test_valid_tracking_window_with_unavailable_samples_is_still_a_data_gap(monkeypatch):
    reads = []
    read = factors.read_facts

    def record_read(store, source, definition, request):
        reads.append(request)
        return read(store, source, definition, request)

    monkeypatch.setattr(factors, "read_facts", record_read)
    source, _, request = example_query("etf_tracking_error", params={"window": 4})
    result = compute_factor(None, source, request)
    assert len(reads) == 1
    assert not result.rows and [gap["field"] for gap in result.gaps] == ["calendar"]
    print("legal tracking window, insufficient samples", result.gaps)
