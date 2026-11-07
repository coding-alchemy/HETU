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
    payload['definition']['snapshot_id'] = 'synthetic'
    protocol_path = tmp_path / 'protocol.json'
    protocol_path.write_text(json.dumps(payload))
    (tmp_path / 'snapshots/synthetic').mkdir(parents=True)
    (tmp_path / 'qualification/phase2').mkdir(parents=True)
    calls = []

    def fake_run(definition, store, root, benchmark=None, **callbacks):
        from hetu_quant.backtest_models import backtest_definition_to_dict
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
