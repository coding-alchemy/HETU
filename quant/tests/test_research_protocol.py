"""二期 03.1 事前协议冻结测试：formal 前协议必须完整，改写即失去原方法标签。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backtest_cases import make_backtest_case
from hetu_quant.backtest_models import BacktestDefinition, backtest_definition_from_dict
from hetu_quant.strategies import require_formal_protocol, strategy_method_label

REPO_PROTOCOL = Path(__file__).resolve().parents[1] / "research" / "phase2-protocol.json"


def _definition_with(**changes: object) -> BacktestDefinition:
    case = make_backtest_case("history")
    facts = dict(case["definition"])  # type: ignore[arg-type]
    protocol = dict(facts["protocol"])
    for key, value in changes.items():
        if key in protocol:
            protocol[key] = value
        else:
            facts[key] = value
    facts["protocol"] = protocol
    facts["snapshot_id"] = "snap-protocol-test"
    return BacktestDefinition(**facts)  # type: ignore[arg-type]


def test_mechanism_stage_needs_no_formal_gate() -> None:
    require_formal_protocol(_definition_with())  # stage=mechanism：不拦截


def test_complete_formal_protocol_passes() -> None:
    require_formal_protocol(_definition_with(stage="formal"))


@pytest.mark.parametrize(
    ("override", "missing"),
    [
        ({"stage": "formal", "time_split": {}}, "time_split"),
        ({"stage": "formal", "time_split": {"final": []}}, "time_split"),
        ({"stage": "formal", "costs": {}}, "costs"),
        ({"stage": "formal", "objectives": {}}, "objectives"),
        ({"stage": "formal", "rules_ref": ""}, "rules_ref"),
        ({"stage": "formal", "trial_budget": {}}, "trial_budget"),
        ({"stage": "formal", "viewing_record": {}}, "viewing_record"),
        ({"stage": "formal", "benchmark_id": ""}, "benchmark_id"),
    ],
)
def test_protocol_must_be_frozen_before_final_result(override: dict, missing: str) -> None:
    with pytest.raises(ValueError, match=missing):
        require_formal_protocol(_definition_with(**override))


def test_method_label_drops_original_when_rules_change() -> None:
    original = dict(warmup_months=36)
    assert strategy_method_label(_definition_with(**original)) == "original_method"
    changed_frequency = _definition_with(**original, rebalance={"frequency": "weekly"})
    changed_frequency_label = strategy_method_label(changed_frequency)
    assert changed_frequency_label != "original_method"
    assert strategy_method_label(_definition_with(warmup_months=30)) != "original_method"
    assert (
        strategy_method_label(_definition_with(**original, strategy_id="universe_vw"))
        != "original_method"
    )


def test_repo_phase2_protocol_is_frozen_formal() -> None:
    payload = json.loads(REPO_PROTOCOL.read_text(encoding="utf-8"))
    definition = backtest_definition_from_dict(payload["definition"])
    assert definition.protocol["stage"] == "formal"
    require_formal_protocol(definition)
    assert strategy_method_label(definition) == "original_method"
    assert payload["viewing_record_frozen"]["final_seen"] is False


def test_validation_plan_has_eight_concrete_isolated_definitions(tmp_path):
    import runpy

    ns = runpy.run_path(str(REPO_PROTOCOL.parent / 'run_validation.py'))
    plan = ns['plan_validation'](REPO_PROTOCOL, tmp_path, tmp_path / 'out')
    definitions = plan.get('definitions', [])
    assert len(definitions) == 8, '预算必须落实为八次具体定义，不能只给max_runs'
    by_id = {row['trial_id']: row for row in definitions}
    assert len(by_id) == 8
    assert by_id['dev_neighbor_30']['definition']['protocol']['warmup_months'] == 30
    assert by_id['dev_neighbor_42']['definition']['protocol']['warmup_months'] == 42
    for row in definitions:
        definition = row['definition']
        if row['phase'] == 'development_validation':
            assert definition['end'] == '2023-12-31'
            assert 'final' not in definition['protocol']['time_split']
        else:
            assert definition['start'] == '2024-01-01'
    assert by_id['final_double_costs']['definition']['protocol']['costs']['commission_rate'] == .0006
    assert by_id['final_double_costs']['definition']['protocol']['costs']['slippage_rate'] == .001
    assert by_id['final_capacity']['definition']['protocol']['execution']['participation_rate'] == .0025
    assert by_id['final_main']['dependencies'] == ['final_benchmark']
    assert by_id['final_double_costs']['reference_trial'] == 'final_main'


def _synthetic_validation_execution(tmp_path, monkeypatch):
    import runpy

    import hetu_quant.backtest as backtest
    from hetu_quant.backtest_models import BacktestRef

    ns = runpy.run_path(str(REPO_PROTOCOL.parent / 'run_validation.py'))
    payload = json.loads(REPO_PROTOCOL.read_text())
    payload['study_id'] = 'SYNTHETIC_STAGE_TEST'
    payload['definition']['protocol']['universe'] = {'rule': 'synthetic_history'}
    payload['definition']['snapshot_id'] = 'synthetic'
    protocol_path = tmp_path / 'protocol.json'
    protocol_path.write_text(json.dumps(payload))
    (tmp_path / 'snapshots/synthetic').mkdir(parents=True)
    admission = tmp_path / 'qualification/phase2/engine-admission/engine-admission.json'
    admission.parent.mkdir(parents=True)
    admission.write_text(json.dumps({'decision': 'candidate_ready'}))
    calls = []

    def fake_run(definition, store, root, benchmark=None, **callbacks):
        from hetu_quant.backtest_models import backtest_definition_to_dict
        callbacks["on_engine_start"]()
        calls.append((backtest_definition_to_dict(definition), benchmark))
        run_id = f'synthetic-{len(calls)}'
        directory = root / 'backtests' / run_id
        directory.mkdir(parents=True)
        (directory / 'evaluation.json').write_text(json.dumps(dict(
            research_status='evaluated', economic_status='not_met',
            research_conclusion='fail', next_decision='review')))
        return BacktestRef(run_id=run_id, root=root, status='complete')

    monkeypatch.setattr(backtest, 'run_backtest', fake_run)
    def comparable(definition, reference):
        prior = calls[int(reference.run_id.rsplit('-', 1)[1])-1][0]
        if prior['start'] != str(definition.start):
            raise ValueError('incompatible_benchmark: period')
    monkeypatch.setattr(backtest, 'validate_benchmark_reference', comparable)
    return ns, payload, protocol_path, calls


def test_eight_trials_execute_once_in_stage_order_and_freeze(tmp_path, monkeypatch):
    ns, payload, protocol_path, calls = _synthetic_validation_execution(tmp_path, monkeypatch)
    run = ns['run_validation']
    with pytest.raises(ValueError, match='development/validation incomplete'):
        run(protocol_path, tmp_path, tmp_path/'out', phase='final')
    for _ in range(3):
        run(protocol_path, tmp_path, tmp_path/'out')
    assert len(calls) == 4
    assert [d['protocol']['warmup_months'] for d, _ in calls] == [36, 36, 30, 42]
    assert all('final' not in d['protocol']['time_split'] and d['end'] == '2023-12-31' for d, _ in calls)
    assert run(protocol_path, tmp_path, tmp_path/'out')['execution_status'] == 'phase_complete'
    assert len(calls) == 4
    for _ in range(3):
        run(protocol_path, tmp_path, tmp_path/'out', phase='final')
    assert len(calls) == 8
    assert all(d['start'] == '2024-01-01' for d, _ in calls[4:])
    assert calls[6][0]['protocol']['costs']['commission_rate'] == .0006
    assert calls[7][0]['protocol']['execution']['participation_rate'] == .0025
    assert calls[6][1].run_id == 'synthetic-6'
    assert calls[7][1].run_id == 'synthetic-6'
    payload['definition']['protocol']['costs']['slippage_rate'] = .01
    protocol_path.write_text(json.dumps(payload))
    # 已执行定义不会因再次 execute 而重跑。
    assert run(protocol_path, tmp_path, tmp_path/'out', phase='final')['execution_status'] == 'phase_complete'


@pytest.mark.parametrize("identity", ["as_of", "snapshot_id"])
def test_final_freeze_allows_identity_change_after_six_runs(tmp_path, monkeypatch, identity):
    ns, payload, path, calls = _synthetic_validation_execution(tmp_path, monkeypatch)
    run = ns['run_validation']
    for _ in range(3):
        run(path, tmp_path, tmp_path/'out')
    run(path, tmp_path, tmp_path/'out', phase='final')
    assert len(calls) == 6
    frozen = tmp_path/'research/SYNTHETIC_STAGE_TEST/final-entry.json'
    original = frozen.read_bytes()
    if identity == 'as_of':
        payload['definition']['as_of'] = '2026-10-18T21:00:00+08:00'
    else:
        payload['definition']['snapshot_id'] = 'synthetic-same-facts'
        (tmp_path/'snapshots/synthetic-same-facts').mkdir()
    path.write_text(json.dumps(payload))
    result = run(path, tmp_path, tmp_path/'out', phase='final')
    assert len(calls) == 7
    assert result['trial_id'] == 'final_double_costs'
    assert frozen.read_bytes() == original
    source_uses = [json.loads(line) for line in (frozen.parent/'source-uses.jsonl').read_text().splitlines()]
    assert source_uses[-1]['source'][identity] == payload['definition'][identity]
    assert len([row for row in calls if row[0]['strategy_id'] == 'universe_vw']) == 2


@pytest.mark.parametrize('field', ['costs', 'parameters'])
def test_final_freeze_still_rejects_behavior_change_after_six_runs(tmp_path, monkeypatch, field):
    ns, payload, path, calls = _synthetic_validation_execution(tmp_path, monkeypatch)
    run = ns['run_validation']
    for _ in range(3):
        run(path, tmp_path, tmp_path/'out')
    run(path, tmp_path, tmp_path/'out', phase='final')
    if field == 'costs':
        payload['definition']['protocol']['costs']['commission_rate'] = .0007
    else:
        payload['definition']['parameters']['lookback_months'] = 35
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match='definitions changed'):
        run(path, tmp_path, tmp_path/'out', phase='final')
    assert len(calls) == 6


def test_formal_job_reuses_eight_trial_scheduler_and_checks_saved_definition(tmp_path, monkeypatch):
    from hetu_quant.backtest import run_formal_job
    from hetu_quant.backtest_models import backtest_definition_from_dict
    from hetu_quant.data import DataStore

    ns, payload, path, calls = _synthetic_validation_execution(tmp_path, monkeypatch)
    selected = next(row for row in ns['validation_definitions'](payload) if row['trial_id'] == 'dev_main')
    raw = selected['definition']
    raw['protocol']['research_execution'] = dict(study_id=payload['study_id'],
        trial_id='dev_main', phase='development_validation', payload=payload)
    definition = backtest_definition_from_dict(raw)
    ref = run_formal_job(definition, DataStore(tmp_path))
    assert ref.run_id == 'synthetic-2'
    assert len(calls) == 2
    with pytest.raises(ValueError, match='already attempted'):
        run_formal_job(definition, DataStore(tmp_path))
    assert len(calls) == 2
    raw['protocol']['costs']['commission_rate'] = .123
    with pytest.raises(ValueError, match='saved definition'):
        run_formal_job(backtest_definition_from_dict(raw), DataStore(tmp_path))
    assert len(calls) == 2


def test_formal_job_without_study_selection_cannot_bypass_budget(tmp_path):
    from hetu_quant.backtest import run_formal_job
    from hetu_quant.backtest_models import backtest_definition_from_dict
    from hetu_quant.data import DataStore

    definition = backtest_definition_from_dict(json.loads(REPO_PROTOCOL.read_text())['definition'])
    with pytest.raises(ValueError, match='research_execution'):
        run_formal_job(definition, DataStore(tmp_path))


def _published_benchmark_case(tmp_path, monkeypatch):
    import runpy
    from copy import deepcopy

    import pandas as pd

    from backtest_cases import build_prepared_case_store, case_definition
    from hetu_quant import backtest
    from hetu_quant.backtest_models import backtest_definition_to_dict

    case = make_backtest_case("formal", variant="workflow")
    store, snapshot = build_prepared_case_store(tmp_path / "facts", case, monkeypatch)
    facts = backtest_definition_to_dict(case_definition(case, snapshot.snapshot_id))
    payload = {"study_id": "synthetic-reuse", "definition": facts}
    scheduler = runpy.run_path(str(REPO_PROTOCOL.parent / "run_validation.py"))
    definition = scheduler["validation_definitions"](payload)[0]["definition"]

    def publish_benchmark(run_id, trial_id, *, end="2024-07-31", changed_rules=False):
        benchmark = deepcopy(definition)
        benchmark["end"] = end
        benchmark["protocol"]["evaluation_window"][1] = end
        benchmark["protocol"]["time_split"]["validation"][1] = end
        run_dir = store.root / "backtests" / run_id
        bundle = run_dir / "bundle"
        assert backtest.prepare_backtest(backtest_definition_from_dict(benchmark), store, bundle).can_run
        if changed_rules:
            rule_path = bundle / "tables/trading_rules.parquet"
            rules = pd.read_parquet(rule_path)
            rules.loc[0, "tick_size"] = "0.05"
            rules.to_parquet(rule_path)
        # 合成轨迹手写，不启动引擎。
        manifest = {"run_id": run_id, "status": "complete", "kind": "benchmark", "definition": benchmark,
                    "sources": {"snapshot_id": snapshot.snapshot_id}}
        evaluation = {"research_status": "evaluated", "economic_status": "not_evaluated",
                      "research_conclusion": "diagnostic_only", "next_decision": "review"}
        tables = {name: pd.DataFrame(columns=columns) for name, columns in backtest.TABLE_COLUMNS.items()}
        calendar = pd.read_parquet(bundle / "tables/calendar.parquet")
        days = calendar.loc[calendar["date"].astype(str).between(benchmark["start"], end), "date"]
        assert days.astype(str).max() == end
        tables["nav"] = pd.DataFrame(dict(date=days, gross_nav=1_000_000, net_nav=1_000_000, benchmark_nav=1_000_000))
        tables["cash"] = pd.DataFrame(dict(date=days, cash_available=1_000_000, receivable=0, dividend_tax_payable=0))
        backtest._write_publish(run_dir, manifest, tables, evaluation)
        return dict(trial_id=trial_id, run_id=run_id, kind="benchmark", status="complete")

    return store, payload, scheduler, publish_benchmark


@pytest.mark.parametrize(("prior_trial", "variant"), [
    (trial, variant)
    for trial in ("dev_benchmark", "legacy_benchmark")
    for variant in ("same-span", "changed-rules", "longer-span")
] + [("legacy_benchmark", "newer-aliases"), ("dev_benchmark", "explicit-longer")])
def test_published_benchmark_reuse_with_engine_off(tmp_path, monkeypatch, prior_trial, variant):
    import pandas as pd

    from hetu_quant import backtest, worker
    from hetu_quant.backtest_models import BacktestRef
    from hetu_quant.experiments import ExperimentStore
    from hetu_quant.jobs import JobService
    from hetu_quant.ui.backtest_page import _build_formal_definition

    store, payload, scheduler, publish_benchmark = _published_benchmark_case(tmp_path, monkeypatch)
    facts = payload["definition"]
    run_id = "original-benchmark"
    end = "2024-08-31" if variant in ("longer-span", "explicit-longer") else "2024-07-31"
    records = [publish_benchmark(run_id, prior_trial, end=end, changed_rules=variant == "changed-rules")]
    if variant == "newer-aliases":
        records.append(publish_benchmark("newer-changed-rules", "legacy_changed_rules", changed_rules=True))
        records.append(publish_benchmark("newest-august", "legacy_extended", end="2024-08-31"))
    if variant == "explicit-longer":
        records.append(publish_benchmark("valid-july-alias", "legacy_july"))
    ledger = store.root / "research/synthetic-reuse/trials.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text("".join(json.dumps(record) + "\n" for record in records))
    assert not (store.root / "qualification/phase2").exists()
    original_files = {path.relative_to(store.root): path.read_bytes()
                      for path in (*ledger.parent.iterdir(), *(store.root / "backtests").rglob("*.json"),
                                   *(store.root / "qualification").glob("qual-*.json"))}

    def prohibited(*args, **kwargs):
        pytest.fail("reading a published benchmark must not build signals or run the engine")

    monkeypatch.setattr("hetu_quant.strategies.build_signals", prohibited)
    monkeypatch.setattr(backtest, "run_engine_subprocess", prohibited)
    phase, selected = "development_validation", "dev_benchmark"

    def preview():
        return scheduler["preview_validation"](payload, store, phase=phase, trial_id=selected)

    def execute():
        return scheduler["run_validation"](REPO_PROTOCOL, store.root, tmp_path, phase=phase,
                                           trial_id=selected, payload=payload)

    def queued():
        return backtest.run_formal_job(_build_formal_definition(payload, phase, selected), store)

    if variant == "longer-span":
        candidate = _build_formal_definition(payload, phase, "dev_main")
        assert candidate.end.isoformat() == "2024-07-31"
        candidate_bundle = tmp_path / "candidate"
        assert backtest.prepare_backtest(candidate, store, candidate_bundle, record_qualification=False).can_run
        reference = BacktestRef(run_id, store.root, "complete")
        assert backtest.validate_benchmark_reference(candidate, reference, candidate_bundle)["run_id"] == run_id

    if variant not in ("same-span", "newer-aliases"):
        mismatch = "trading_rules" if variant == "changed-rules" else "execution_interval"
        for entry in (preview, execute, queued):
            with pytest.raises(ValueError, match=f"incompatible_benchmark.*{mismatch}"):
                entry()
    else:
        report = preview()
        assert report.can_run and any(run_id in message for message in report.limitations)
        result = execute()
        assert result["candidate"]["run_id"] == run_id and result["runs"] == []
        assert queued().run_id == run_id
        if variant == "newer-aliases":
            from streamlit.testing.v1 import AppTest

            from hetu_quant.ui import app as app_module

            monkeypatch.setenv("HETU_QUANT_ROOT", str(store.root))
            monkeypatch.setattr("hetu_quant.ui.backtest_page._formal_payload", lambda _path: payload)
            page = AppTest.from_file(str(Path(app_module.__file__).resolve())).run()
            page.sidebar.radio[0].set_value("回测").run()
            page.selectbox(key="bt_trial").set_value(selected).run()
            protocol = facts["protocol"]
            page.date_input(key="bt_start").set_value(pd.Timestamp(protocol["time_split"]["development"][0]).date())
            page.date_input(key="bt_end").set_value(pd.Timestamp(protocol["time_split"]["final"][1]).date())
            page.text_input(key="bt_as_of").set_value(facts["as_of"])
            page.number_input(key="bt_slippage").set_value(protocol["costs"]["slippage_rate"])
            page.button(key="bt_check_qualification").click().run(timeout=20)
            assert not page.error and not page.button(key="bt_submit_job").disabled
            previews = [json.loads(entry.value) for entry in page.json]
            assert any(report.get("can_run") and f"复用已发布账户 {run_id}" in report["limitations"]
                       for report in previews)
            page.button(key="bt_submit_job").click().run()
            experiments = ExperimentStore(store.root)
            service = JobService(experiments)
            job, = service.list()
            completed = worker.run_job(job.job_id, experiments, store)
            assert completed.state == "succeeded", completed.reason
            assert completed.output == store.root / "backtests" / run_id
        with pytest.raises(ValueError, match="引擎／数据准入证据目录缺失"):
            scheduler["run_validation"](REPO_PROTOCOL, store.root, tmp_path, phase=phase,
                                        trial_id="dev_main", payload=payload)
    current_files = {path.relative_to(store.root): path.read_bytes()
                     for path in (*ledger.parent.iterdir(), *(store.root / "backtests").rglob("*.json"),
                                  *(store.root / "qualification").glob("qual-*.json"))}
    assert current_files == original_files


@pytest.mark.parametrize(("bound", "queued"), [(False, False), (False, True), (True, False)],
                         ids=["unbound", "queued", "bound"])
def test_pending_candidate_uses_comparable_published_benchmark(tmp_path, monkeypatch, bound, queued):
    import pandas as pd

    from hetu_quant import backtest, data_research, worker
    from hetu_quant.backtest_models import BacktestRef
    from hetu_quant.experiments import ExperimentStore
    from hetu_quant.jobs import JobService
    from hetu_quant.ui.backtest_page import _build_formal_definition

    store, payload, scheduler, publish_benchmark = _published_benchmark_case(tmp_path, monkeypatch)
    compatible = publish_benchmark("original-august", "legacy_august", end="2024-08-31")
    ledger = store.root / "research/synthetic-reuse/trials.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(json.dumps(compatible) + "\n")
    admission = store.root / "qualification/phase2/engine-admission/engine-admission.json"
    admission.parent.mkdir(parents=True)
    admission.write_text(json.dumps({"decision": "candidate_ready"}))
    phase, selected = "development_validation", "dev_main"
    candidate = _build_formal_definition(payload, phase, selected)
    candidate_bundle = tmp_path / "candidate"
    assert backtest.prepare_backtest(candidate, store, candidate_bundle, record_qualification=False).can_run
    if bound:
        rows = pd.read_parquet(candidate_bundle / "universe.parquet")
        universe = rows.groupby("signal_date")["code"].agg(list).to_dict()
        monkeypatch.setattr(data_research, "require_frozen_trial_inputs", lambda *_args: {selected: universe})

    def preview():
        return scheduler["preview_validation"](payload, store, phase=phase, trial_id=selected)

    assert preview().can_run
    incompatible = publish_benchmark("newer-july", "dev_benchmark" if bound else "legacy_july", changed_rules=True)
    with ledger.open("a") as handle:
        handle.write(json.dumps(incompatible) + "\n")
    original_ledger = ledger.read_bytes()
    reference = BacktestRef("original-august", store.root, "complete")
    assert backtest.validate_benchmark_reference(candidate, reference, candidate_bundle)["run_id"] == "original-august"
    references = []

    def prohibited(*args, **kwargs):
        pytest.fail("the offline candidate test must not calculate signals or start the engine")

    def simulated_account(definition, data, root, *, benchmark, on_engine_start, **kwargs):
        references.append(benchmark.run_id)
        on_engine_start()
        run_dir = root / "backtests/offline-candidate"
        run_dir.mkdir()
        (run_dir / "backtest.json").write_text(json.dumps({"run_id": "offline-candidate", "status": "complete"}))
        (run_dir / "evaluation.json").write_text(json.dumps({"research_status": "evaluated",
            "economic_status": "not_evaluated", "research_conclusion": "diagnostic_only", "next_decision": "review"}))
        return BacktestRef("offline-candidate", root, "complete")

    def execute():
        return scheduler["run_validation"](REPO_PROTOCOL, store.root, tmp_path, phase=phase,
                                           trial_id=selected, payload=payload)

    monkeypatch.setattr("hetu_quant.strategies.build_signals", prohibited)
    monkeypatch.setattr(backtest, "run_engine_subprocess", prohibited)
    monkeypatch.setattr(backtest, "run_backtest", simulated_account)
    if bound:
        for entry in (preview, execute):
            with pytest.raises(ValueError, match="incompatible_benchmark.*trading_rules"):
                entry()
        assert references == [] and ledger.read_bytes() == original_ledger
    else:
        assert preview().can_run and ledger.read_bytes() == original_ledger
        if queued:
            experiments = ExperimentStore(store.root)
            service = JobService(experiments)
            job = service.submit(experiments.save_definition("backtest", candidate), "candidate-reference-fallback")
            completed = worker.run_job(job.job_id, experiments, store)
            assert completed.state == "succeeded", completed.reason
            assert completed.output == store.root / "backtests/offline-candidate"
        else:
            assert execute()["benchmark"] == {"run_id": "original-august", "reused": True}
        assert references == ["original-august"]
        updated = ledger.read_bytes()
        assert updated.startswith(original_ledger)
        added, = updated[len(original_ledger):].splitlines()
        assert json.loads(added)["run_id"] == "offline-candidate"


@pytest.mark.parametrize('universe', [
    {'rule': 'current_survivors'},
    {'rule': 'historical_sh_sz_common_stock', 'boards': ['star']},
])
def test_validation_rejects_unimplemented_universe_instead_of_silently_using_all_sh_sz(universe):
    import runpy

    ns = runpy.run_path(str(REPO_PROTOCOL.parent / 'run_validation.py'))
    payload = json.loads(REPO_PROTOCOL.read_text())
    payload['definition']['protocol']['universe'] = universe
    with pytest.raises(ValueError, match='unsupported universe'):
        ns['validation_definitions'](payload)


def test_frozen_trial_rejects_missing_real_scope_before_attempt(tmp_path, monkeypatch):
    import runpy

    script = Path(__file__).parents[1] / "research/run_validation.py"
    entry = runpy.run_path(str(script))
    payload = json.loads((script.parent / "phase2-protocol.json").read_text())
    payload["definition"]["snapshot_id"] = "qualified-subset"
    (tmp_path / "snapshots" / "qualified-subset").mkdir(parents=True)
    (tmp_path / "qualification" / "phase2").mkdir(parents=True)
    ran = []
    monkeypatch.setitem(entry["run_validation"].__globals__, "_run_engine_once",
                        lambda *a, **k: ran.append((a, k)))
    with pytest.raises(ValueError, match="independent_pool_unproved"):
        entry["run_validation"](script, tmp_path, tmp_path, trial_id="dev_main", payload=payload)
    assert ran == []
    assert not (tmp_path / "research").exists()


def test_only_engine_started_failure_uses_trial_budget(tmp_path, monkeypatch):
    import hetu_quant.backtest as backtest

    ns, _, path, _ = _synthetic_validation_execution(tmp_path, monkeypatch)
    run = ns["run_validation"]
    ledger = tmp_path / "research/SYNTHETIC_STAGE_TEST/trials.jsonl"

    def before_start(*args, **kwargs):
        raise ValueError("input rejected")

    def after_start(*args, **kwargs):
        kwargs["on_engine_start"]()
        raise RuntimeError("engine failed")

    monkeypatch.setattr(backtest, "run_backtest", before_start)
    with pytest.raises(ValueError, match="input rejected"):
        run(path, tmp_path, tmp_path / "out")
    assert not ledger.exists()

    monkeypatch.setattr(backtest, "run_backtest", after_start)
    with pytest.raises(RuntimeError, match="engine failed"):
        run(path, tmp_path, tmp_path / "out")
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert [row["status"] for row in rows] == ["failed"]
    assert rows[0]["trial_id"] == "dev_benchmark"
