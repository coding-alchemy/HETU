"""二期正式网页→免费合成事实→真实引擎→同产物读取。无市场请求或真实研究。"""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import zipfile
from pathlib import Path

import pandas as pd
import pytest
from backtest_cases import build_prepared_case_store, case_definition, make_backtest_case
from hetu_quant import worker
from hetu_quant.backtest import TABLE_COLUMNS
from hetu_quant.backtest_data import prepare_backtest
from hetu_quant.backtest_reporting import compare_backtests, export_backtest, read_backtest
from hetu_quant.data import DataStore
from hetu_quant.experiments import ExperimentStore
from hetu_quant.jobs import JobService
from hetu_quant.ui import app as app_module
from hetu_quant.ui.backtest_page import _build_formal_definition
from streamlit.testing.v1 import AppTest

REPO = Path(__file__).resolve().parents[2]
APP = Path(app_module.__file__).resolve()


def _install_engine(root):
    """沿用已批准的真实引擎环境和准入证据；不伪造资格。"""
    records = sorted((REPO / '.hetu/quant/qualification/phase2').glob('engine-admission-*/engine-admission.json'))
    assert records, '正式真实引擎测试必须有既有准入记录'
    latest = records[-1]
    assert json.loads(latest.read_text())['decision'] == 'candidate_ready'
    admission = root / 'qualification/phase2' / latest.parent.name
    admission.mkdir(parents=True)
    shutil.copyfile(latest, admission / latest.name)
    interpreter = REPO / '.hetu/quant/runtime/rqalpha/.venv/bin/python'
    assert interpreter.exists(), '正式真实引擎测试不能skip环境缺失'
    wrapper = root / 'runtime/rqalpha/.venv/bin/python'
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text(f'#!/bin/sh\nexec "{interpreter}" "$@"\n')
    wrapper.chmod(0o755)


def _workflow(tmp_path, monkeypatch):
    case = make_backtest_case('formal', variant='workflow')
    data, snapshot = build_prepared_case_store(tmp_path, case, monkeypatch)
    _install_engine(data.root)
    from hetu_quant.backtest_models import backtest_definition_to_dict
    facts = backtest_definition_to_dict(case_definition(case, snapshot.snapshot_id))
    facts['start'] = facts['protocol']['time_split']['development'][0]
    facts['end'] = facts['protocol']['time_split']['final'][1]
    payload = dict(study_id='synthetic-web-workflow', status='frozen_pre_result',
                   frozen_at='2023-12-01', note='合成免费机制事实，非真实股票研究', definition=facts)
    return data, snapshot, payload


def _bytes(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file()}


def test_phase2_formal_web_real_engine_same_artifact_flow(tmp_path, monkeypatch):
    data, snapshot, payload = _workflow(tmp_path / 'facts', monkeypatch)
    definition = _build_formal_definition(payload, 'development_validation', 'dev_main')
    quality = prepare_backtest(definition, data, tmp_path / 'qualification-bundle')
    assert quality.can_run, quality.limitations
    manifest = data.load_manifest(snapshot)
    actions = [item for item in manifest['items'] if item['product_id'] == 'cn_a_actions']
    assert actions, '行动覆盖应由真实prepare记录'
    # 原始合成事实无账户答案；信号只能由正式共同build_signals产生。
    assert not list(data.root.rglob('signals.parquet'))
    monkeypatch.setenv('HETU_QUANT_ROOT', str(data.root))
    monkeypatch.setenv('TUSHARE_TOKEN', 'configured-paid-credential-must-stay-unused')
    paid_calls = []
    def forbidden(*args, **kwargs):
        paid_calls.append(1)
        raise AssertionError('formal flow must remain offline/free')
    monkeypatch.setattr('hetu_quant.providers.akshare.AKShareProvider.fetch', forbidden)
    monkeypatch.setattr('hetu_quant.providers.baostock.BaoStockProvider.fetch', forbidden)
    from hetu_quant.ui import backtest_page

    monkeypatch.setattr(backtest_page, "_formal_payload", lambda _path: payload)
    app = AppTest.from_file(str(APP)).run()
    app.sidebar.radio[0].set_value('回测').run()
    assert not app.text_area
    app.date_input(key='bt_start').set_value(definition.start).run()
    app.date_input(key='bt_end').set_value(pd.Timestamp(payload['definition']['end']).date()).run()
    app.text_input(key='bt_as_of').set_value(payload['definition']['as_of']).run()
    app.selectbox(key='bt_strategy').set_value('low_volatility').run()
    app.number_input(key='bt_slippage').set_value(0.0).run()
    app.button(key='bt_check_qualification').click().run(timeout=20)
    assert not app.exception
    previews = [json.loads(item.value) for item in app.json]
    assert any(item.get('can_run') for item in previews)
    app.button(key='bt_submit_job').click().run()
    app.button(key='bt_submit_job').click().run()
    experiments = ExperimentStore(data.root)
    jobs = JobService(experiments)
    assert len(jobs.list()) == 1
    job = jobs.list()[0]
    saved = experiments.read_definition(job.definition_id)
    assert saved.protocol['stage'] == 'formal'
    assert saved.protocol['research_execution']['trial_id'] == 'dev_main'
    assert saved.end.isoformat() == '2024-07-31', '网页实际执行区间必须隔离最终阶段'
    final = worker.run_job(job.job_id, experiments, DataStore(data.root))
    assert final.state == 'succeeded', final.reason
    assert final.progress == 1.0
    assert final.output and final.output.name in experiments.list_backtests()
    assert not list((data.root / 'backtests').glob('*.staging'))
    ref = experiments.get_backtest(final.output.name)
    published = read_backtest(ref)
    assert published['manifest']['definition']['protocol']['stage'] == 'formal'
    assert len(published['tables']['signals']) > 0
    assert len(published['tables']['trades']) > 0
    assert published['evaluation']['execution_status'] == 'complete'
    assert published['evaluation']['research_status'] != 'blocked'
    metrics = published['evaluation']['metrics']
    exposure = metrics['exposures']['snapshots'][0]
    assert exposure['target']['size']['weighted_mean_market_cap_cny'] > 0
    assert any(row.get('market_cap_source') for row in exposure['target']['components'])
    assert exposure['target']['industry']['status'] == 'unknown'
    assert metrics['market_states']['status'] == 'available'
    ledger = data.root / 'research/synthetic-web-workflow/trials.jsonl'
    trials = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert [row['trial_id'] for row in trials] == ['dev_benchmark', 'dev_main']
    assert all(row['status'] == 'complete' for row in trials)
    assert not any(row['phase'] == 'final' for row in trials)
    trades = published['tables']['trades'].merge(
        published['tables']['orders'][['order_id', 'signal_at']], on='order_id', validate='many_to_one')
    assert (pd.to_datetime(trades['executed_at']) > pd.to_datetime(trades['signal_at'])).all()
    cash = published['tables']['cash'].set_index('date')
    positions = published['tables']['positions'].groupby('date')['market_value'].sum()
    nav = published['tables']['nav'].set_index('date')['net_nav']
    assets = (cash['cash_available'] + cash['receivable'] - cash['dividend_tax_payable']
              + positions.reindex(cash.index, fill_value=0))
    pd.testing.assert_series_equal(nav, assets, check_names=False)
    before = _bytes(data.root / 'backtests')
    reopened = AppTest.from_file(str(APP)).run()
    assert job.job_id in set(reopened.dataframe[0].value['任务号'])
    reopened.sidebar.radio[0].set_value('回测').run()
    reopened.selectbox(key='bt_result_select').set_value(ref.run_id).run()
    reopened.button(key='bt_export_zip').click().run()
    assert not reopened.exception
    assert len(jobs.list()) == 1
    zip_path = export_backtest(ref, tmp_path / 'exports')
    with zipfile.ZipFile(zip_path) as archive:
        for name in TABLE_COLUMNS:
            expected = published['tables'][name]
            text_columns = {column: str for column in expected.select_dtypes(include='object').columns}
            exported = pd.read_csv(io.BytesIO(archive.read(f'{name}.csv')),
                                   dtype=text_columns, keep_default_na=False)
            pd.testing.assert_frame_equal(exported, expected, check_dtype=False)
        assert json.loads(archive.read('evaluation.json')) == published['evaluation']
        report = archive.read('report.md').decode()
        assert '规模和行业暴露' in report and '市场状态' in report
        assert json.loads(archive.read('definition.json')) == published['manifest']['definition']
        assert json.loads(archive.read('sources.json')) == published['manifest']['sources']
        assert json.loads(archive.read('backtest.json')) == published['manifest']
        assert json.loads(archive.read('bundle/qualification.json'))['can_run']
        assert 'bundle/tables/trading_rules.parquet' in archive.namelist()
        assert 'factor-labels.parquet' in archive.namelist()
        research = json.loads(archive.read('research.json'))
        assert research == published['manifest']['research']
        assert research['trial_id'] == 'dev_main'
        assert research['payload']['study_id'] == 'synthetic-web-workflow'
        assert [json.loads(line)['trial_id']
                for line in archive.read('research/trials.jsonl').decode().splitlines()] == [
                    'dev_benchmark', 'dev_main']
        for name in TABLE_COLUMNS:
            pd.testing.assert_frame_equal(pd.read_parquet(io.BytesIO(archive.read(f'{name}.parquet'))),
                                          published['tables'][name])
    # 执行仓库既有Notebook的BacktestRef只读单元；没有复制数值公式。
    notebook = json.loads((REPO / 'quant/notebooks/read_result.ipynb').read_text())
    cell = next(cell for cell in notebook['cells'] if cell['cell_type'] == 'code'
                and 'backtest_payload' in ''.join(cell['source']))
    monkeypatch.setenv('HETU_QUANT_BACKTEST_RUN_ID', ref.run_id)
    namespace = {'store': experiments, 'os': __import__('os')}
    exec(''.join(cell['source']), namespace)
    nb = namespace['backtest_payload']
    # Notebook默认选同根的最后一项；显式确认该引用后比较同产物值。
    nb_ref = namespace['backtest']
    assert nb_ref.run_id == ref.run_id
    source = published
    assert nb['evaluation'] == source['evaluation']
    for name in TABLE_COLUMNS:
        pd.testing.assert_frame_equal(nb['tables'][name], source['tables'][name])
    comparison = compare_backtests(ref, experiments.get_backtest(trials[0]['run_id']))
    assert comparison['mixed_ranking_allowed']
    assert _bytes(data.root / 'backtests') == before
    assert paid_calls == []


def test_formal_worker_cancel_reaps_actual_engine_and_reopens(tmp_path, monkeypatch):
    """回调观测真实Popen后取消；真实引擎进程退出，任务重开仍为cancelled。"""
    import os
    import threading

    import hetu_quant.backtest as backtest_module

    data, _, payload = _workflow(tmp_path / 'facts', monkeypatch)
    definition = _build_formal_definition(payload, 'development_validation', 'dev_main')
    experiments = ExperimentStore(data.root)
    jobs = JobService(experiments)
    job = jobs.submit(experiments.save_definition('backtest', definition), 'actual-engine-cancel')
    started = threading.Event()
    children = []
    real_popen = backtest_module.subprocess.Popen

    def observe_popen(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        started.set()
        return child

    monkeypatch.setattr(backtest_module.subprocess, 'Popen', observe_popen)
    results = []
    thread = threading.Thread(target=lambda: results.append(
        worker.run_job(job.job_id, ExperimentStore(data.root), DataStore(data.root))))
    thread.start()
    assert started.wait(20), '必须实际启动引擎子进程'
    assert jobs.get(job.job_id).state == 'running'
    assert 0 < jobs.get(job.job_id).progress < 1
    assert jobs.cancel(job.job_id).state == 'cancel_requested'
    thread.join(20)
    assert not thread.is_alive()
    assert results[0].state == 'cancelled'
    assert JobService(ExperimentStore(data.root)).get(job.job_id).state == 'cancelled'
    assert not ExperimentStore(data.root).list_backtests()
    assert children and all(child.poll() is not None for child in children)
    for child in children:
        try:
            os.kill(child.pid, 0)
        except ProcessLookupError:
            pass
        else:
            raise AssertionError(f'取消后引擎仍存活：{child.pid}')
    partials = list((data.root / 'backtests/_partial').glob('*'))
    assert partials, '取消保留部分证据'
    path = data.root / "research" / payload["study_id"] / "trials.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row["status"] for row in rows] == ["failed"]


def test_formal_unknown_history_cannot_receive_economic_pass(tmp_path, monkeypatch):
    data, _, payload = _workflow(tmp_path / 'facts', monkeypatch)
    for evidence in (data.root / 'evidence').iterdir():
        evidence.unlink()  # 具体历史来源原件未知，不能凭登记表verified绕过。
    definition = _build_formal_definition(payload, 'development_validation', 'dev_main')
    quality = prepare_backtest(definition, data, tmp_path / 'unknown-history-bundle')
    assert not quality.can_run
    experiments = ExperimentStore(data.root)
    jobs = JobService(experiments)
    job = jobs.submit(experiments.save_definition('backtest', definition), 'unknown-history')
    final = worker.run_job(job.job_id, experiments, DataStore(data.root))
    assert final.state == 'failed' and 'trial blocked' in final.reason
    assert not (data.root / "research" / payload["study_id"] / "trials.jsonl").exists()
    for run_id in experiments.list_backtests():
        published = read_backtest(experiments.get_backtest(run_id))
        assert published['manifest']['status'] == 'limited'
        assert published['evaluation']['economic_status'] == 'not_evaluated'
        assert published['tables']['trades'].empty


def test_formal_same_day_execution_is_rejected_before_publish(tmp_path, monkeypatch):
    data, _, payload = _workflow(tmp_path / 'facts', monkeypatch)
    payload['definition']['protocol']['execution']['fill_time'] = 'same_close'
    # 构造冻结母定义的拒绝本身是正式入口行为，不给引擎注入同日成交答案。
    import pytest
    with pytest.raises(ValueError, match='fill_time|execution'):
        _build_formal_definition(payload, 'development_validation', 'dev_main')
    assert not ExperimentStore(data.root).list_backtests()


def test_free_source_failure_cannot_publish_or_use_paid_fallback(tmp_path, monkeypatch):
    """免费源失败在真实prepare/JobService中保留失败，无收费回退或经济结论。"""
    from hetu_quant.models import DataRequest

    data, _, payload = _workflow(tmp_path / 'facts', monkeypatch)
    root = tmp_path / 'failure-root'
    root.mkdir()
    shutil.copyfile(data.root / 'data-products.json', root / 'data-products.json')
    free_calls = []
    paid_calls = []

    class FailedFreeSource:
        def fetch(self, *args, **kwargs):
            free_calls.append(1)
            raise RuntimeError('synthetic free source unavailable')

    def forbidden_paid(*args, **kwargs):
        paid_calls.append(1)
        raise AssertionError('paid fallback forbidden')

    monkeypatch.setenv('TUSHARE_TOKEN', 'configured-paid-credential-must-stay-unused')
    monkeypatch.setattr('hetu_quant.providers.akshare.AKShareProvider.fetch', forbidden_paid)
    failing = DataStore(root, providers={'synthetic_facts': FailedFreeSource(),
                                        'baostock': FailedFreeSource()})
    request = DataRequest(product_ids=tuple(failing.config['products']),
                          codes=('VOL_01',), start=pd.Timestamp('2020-01-01').date(),
                          end=pd.Timestamp('2024-12-31').date(),
                          as_of=pd.Timestamp(payload['definition']['as_of']).tz_convert('Asia/Shanghai').to_pydatetime(),
                          purpose='backtest_low_volatility')
    experiments = ExperimentStore(root)
    jobs = JobService(experiments)
    job = jobs.submit(experiments.save_definition('data', request), 'free-failure')
    result = worker.run_job(job.job_id, experiments, failing)
    assert result.state == 'failed' and 'synthetic free source unavailable' in result.reason
    assert free_calls and paid_calls == []
    assert experiments.list_backtests() == []
    assert not failing._published_manifests()
    assert not list(root.rglob('evaluation.json'))


def test_approved_platform_representative_keeps_frozen_trials(tmp_path, monkeypatch):
    from copy import deepcopy

    from hetu_quant import backtest
    from hetu_quant.backtest import run_formal_job, run_representative_job
    from hetu_quant.backtest_models import backtest_definition_to_dict
    from hetu_quant.backtest_reporting import compare_backtests

    data, _, payload = _workflow(tmp_path, monkeypatch)
    monkeypatch.setenv("HETU_QUANT_ROOT", str(data.root))
    benchmark = run_formal_job(_build_formal_definition(payload, "development_validation", "dev_benchmark"), data)
    ledger = data.root / "research" / payload["study_id"] / "trials.jsonl"
    with ledger.open("a") as stream:
        for number in range(7):
            stream.write(json.dumps({"status": "failed", "detail": f"controlled old attempt {number}"}) + "\n")
    original = ledger.read_bytes()
    definition = backtest_definition_to_dict(_build_formal_definition(payload, "development_validation", "dev_main"))
    definition["protocol"].pop("research_execution")
    definition["protocol"]["representative_execution"] = {"study_id": payload["study_id"], "submission_id": "rep-free"}
    approval = data.root / "research/representative-runs/rep-free/scope.json"
    approval.parent.mkdir(parents=True)
    approval.write_text(json.dumps({
        "definition": definition, "study_id": payload["study_id"], "approval_ref": "controlled test",
        "submission_id": "rep-free", "run_count": 1, "benchmark_run_id": benchmark.run_id,
    }))
    app = AppTest.from_file(str(APP)).run()
    app.sidebar.radio[0].set_value("回测").run()
    next(box for box in app.selectbox if box.label == "代表任务定义").set_value(approval).run()
    next(button for button in app.button if button.label == "提交已授权代表任务").click().run()
    assert not app.exception
    experiments = ExperimentStore(data.root)
    jobs = JobService(experiments)
    assert len(jobs.list()) == 1
    original_job = jobs.list()[0]
    final = worker.run_job(original_job.job_id, experiments, data)
    assert final.state == "succeeded", final.reason
    original_ref = experiments.get_backtest(final.output.name)
    published = read_backtest(original_ref)
    assert published["manifest"]["mode"] == "free"
    assert not published["tables"]["trades"].empty
    assert ledger.read_bytes() == original
    next(button for button in app.button if button.label == "提交已授权代表任务").click().run()
    assert not app.exception
    assert [job.job_id for job in jobs.list()] == [original_job.job_id]

    rate = 0.001
    new_baseline = backtest_definition_to_dict(_build_formal_definition(payload, "development_validation",
                                                                        "dev_benchmark"))
    new_baseline["protocol"].pop("research_execution")
    new_baseline["protocol"]["costs"]["commission_rate"] = rate
    new_baseline_approval = data.root / "research/representative-runs/copied-benchmark/scope.json"
    new_baseline_approval.parent.mkdir(parents=True)
    new_baseline_approval.write_text(json.dumps({
        "definition": new_baseline, "study_id": payload["study_id"], "approval_ref": "controlled benchmark",
        "submission_id": "copied-benchmark", "run_count": 1, "role": "benchmark", "approved_data_fee_cny": 0,
    }))
    copied = deepcopy(definition)
    copied["protocol"]["costs"]["commission_rate"] = rate
    copied["protocol"]["representative_execution"]["submission_id"] = "copy-free"
    new_approval = data.root / "research/representative-runs/copy-free/scope.json"
    new_approval.parent.mkdir(parents=True)
    new_approval.write_text(json.dumps({
        "definition": copied, "study_id": payload["study_id"], "approval_ref": "controlled changed fee",
        "submission_id": "copy-free", "run_count": 1, "benchmark_submission_id": "copied-benchmark",
        "approved_data_fee_cny": 0,
    }))
    copy_page = AppTest.from_file(str(APP)).run()
    copy_page.sidebar.radio[0].set_value("回测").run()
    fee_key = f"bt_copy::{original_job.definition_id}::protocol.costs.commission_rate"
    copy_page.selectbox(key="bt_copy_source").set_value(original_job.definition_id).run()
    copy_page.number_input(key=fee_key).set_value(rate).run()
    next(box for box in copy_page.selectbox if box.label == "与复制条件相符的代表授权").set_value(new_approval).run()
    next(button for button in copy_page.button if button.label == "提交已授权代表任务").click().run()
    changed = next(job for job in jobs.list() if job.submission_id == "copy-free")
    interpreter = tmp_path / "non-executable-python"
    interpreter.write_text("#!/bin/sh\nexit 0\n")
    interpreter.chmod(0o644)
    real_engine = backtest.run_engine_subprocess
    launches = []
    actual_starts = []

    def track_launch(engine_python, runner, bundle, output, **kwargs):
        prepared = json.loads((bundle / "definition.json").read_text())
        strategy = prepared["strategy_id"]
        launches.append(strategy)
        if launches == ["universe_vw", "low_volatility"]:
            engine_python = interpreter
        on_start = kwargs["on_engine_start"]

        def track_start():
            actual_starts.append(strategy)
            on_start()

        kwargs["on_engine_start"] = track_start
        return real_engine(engine_python, runner, bundle, output, **kwargs)

    monkeypatch.setattr(backtest, "run_engine_subprocess", track_launch)
    failed = worker.run_job(changed.job_id, experiments, data)
    assert failed.state == "failed" and "PermissionError" in failed.reason
    candidate_log = new_approval.with_name("attempt.jsonl")
    failed_record = json.loads(candidate_log.read_text().splitlines()[-1])
    assert failed_record["status"] == "failed" and failed_record["actual_run_count"] == 0
    benchmark_log = new_baseline_approval.with_name("attempt.jsonl")
    benchmark_before_retry = benchmark_log.read_bytes()
    benchmark_record = json.loads(benchmark_before_retry.splitlines()[-1])
    assert benchmark_record["actual_run_count"] == 1
    assert actual_starts == ["universe_vw"]

    independent = jobs.submit(changed.definition_id, "unapproved-independent-submission")
    other_failed = worker.run_job(independent.job_id, experiments, data)
    assert other_failed.state == "failed" and "submission differs" in other_failed.reason
    unauthorized_retry = jobs.retry(other_failed.job_id, "unapproved-independent-retry")
    audit_before_retry = candidate_log.read_bytes()
    refused = worker.run_job(unauthorized_retry.job_id, experiments, data)
    assert refused.state == "failed" and "submission differs" in refused.reason
    assert actual_starts == ["universe_vw"]
    assert candidate_log.read_bytes() == audit_before_retry
    assert benchmark_log.read_bytes() == benchmark_before_retry

    retried = jobs.retry(failed.job_id, "copy-free-queue-retry")
    reopened_jobs = JobService(ExperimentStore(data.root))
    assert reopened_jobs.get(retried.job_id).retry_of_job_id == failed.job_id
    assert reopened_jobs.original_submission_id(retried.job_id) == changed.submission_id
    assert reopened_jobs.get(unauthorized_retry.job_id).retry_of_job_id == independent.job_id
    assert reopened_jobs.original_submission_id(unauthorized_retry.job_id) == independent.submission_id
    finished = worker.run_job(retried.job_id, experiments, data)
    assert finished.state == "succeeded" and finished.reason is None, finished.reason
    assert launches == ["universe_vw", "low_volatility", "low_volatility"]
    assert actual_starts == ["universe_vw", "low_volatility"]
    assert benchmark_log.read_bytes() == benchmark_before_retry
    assert failed_record in [json.loads(line) for line in candidate_log.read_text().splitlines()]
    changed_ref = experiments.get_backtest(finished.output.name)
    changed_published = read_backtest(changed_ref)
    assert changed_published["manifest"]["resolved_inputs"]["benchmark"]["run_id"] == benchmark_record["run_id"]
    tables = changed_published["tables"]
    trades = tables["trades"]
    old = published["tables"]["trades"]
    buy = trades[trades["side"] == "BUY"].iloc[0]
    prior_buy = old[old["side"] == "BUY"].iloc[0]
    assert buy["commission"] == pytest.approx(max(5, buy["qty"] * buy["execution_price"] * rate))
    assert buy["commission"] > prior_buy["commission"]
    last_nav = tables["nav"].iloc[-1]
    explicit_fees = tables["costs"].loc[tables["costs"]["cost_type"] != "slippage_cost", "amount"].sum()
    assert last_nav["gross_nav"] - last_nav["net_nav"] == pytest.approx(
        explicit_fees + tables["cash"].iloc[-1]["dividend_tax_payable"]
    )
    targets = tables["targets"]
    orders = tables["orders"]
    signal = orders.loc[orders["order_id"] == buy["order_id"], "signal_at"].item()
    target = targets[(targets["code"] == buy["code"]) & (targets["signal_at"] == signal)].iloc[0]
    execution_day = target["executable_from"]
    positions = tables["positions"]
    held = positions[(positions["date"] == execution_day) & (positions["code"] == target["code"])]
    net = tables["nav"].loc[tables["nav"]["date"] == execution_day, "net_nav"].item()
    evaluation = json.loads((finished.output / "evaluation.json").read_text())
    account = evaluation["attribution"]["by_security"][target["code"]]
    trace = next(row for row in account["position_deviations"] if row["signal_at"] == target["signal_at"])
    assert trace["actual_qty"] == held["qty"].sum() > 0
    assert trace["target_weight"] == target["target_weight"]
    assert trace["weight_deviation"] == pytest.approx(held["market_value"].sum() / net - target["target_weight"])
    comparison = compare_backtests(original_ref, changed_ref)
    for side, result in [("left", published), ("right", changed_published)]:
        costs = result["tables"]["costs"]
        assert comparison[side]["costs"]["actual_by_type"]["commission"] == pytest.approx(
            costs.loc[costs["cost_type"] == "commission", "amount"].sum()
        )
        assert comparison[side]["trials"]["actual_run_count"] == 1
    assert comparison["common_end_net_nav_difference"] == pytest.approx(
        last_nav["net_nav"] - published["tables"]["nav"].iloc[-1]["net_nav"]
    )
    assert ledger.read_bytes() == original
    for approval_path, role in [(new_baseline_approval, "benchmark"), (new_approval, "representative")]:
        attempts = [json.loads(line) for line in approval_path.with_name("attempt.jsonl").read_text().splitlines()]
        assert attempts[0]["role"] == role and attempts[0]["approved_run_count"] == 1
        assert attempts[-1]["actual_run_count"] == 1 and attempts[-1]["actual_data_fee_cny"] == 0
        assert attempts[-1]["actual_account_costs_cny"]["commission"] > 0
    with pytest.raises(FileExistsError):
        run_representative_job(experiments.read_definition(changed.definition_id), data, submission_id="copy-free")
    candidate_before_consumed_retry = candidate_log.read_bytes()
    consumed_retry = jobs.retry(failed.job_id, "copy-free-consumed-retry")
    rejected = worker.run_job(consumed_retry.job_id, experiments, data)
    assert rejected.state == "failed" and "already consumed" in rejected.reason
    assert actual_starts == ["universe_vw", "low_volatility"]
    assert candidate_log.read_bytes() == candidate_before_consumed_retry
    assert ledger.read_bytes() == original


@pytest.mark.parametrize("retry", [False, True], ids=["legacy-original", "retry-of-legacy-original"])
def test_legacy_representative_submission_keeps_approval_after_upgrade(tmp_path, monkeypatch, retry):
    from hetu_quant.backtest import run_formal_job
    from hetu_quant.backtest_models import backtest_definition_from_dict, backtest_definition_to_dict

    data, _, payload = _workflow(tmp_path, monkeypatch)
    baseline = _build_formal_definition(payload, "development_validation", "dev_benchmark")
    benchmark = run_formal_job(baseline, data)
    ledger = data.root / "research" / payload["study_id"] / "trials.jsonl"
    ledger_before = ledger.read_bytes()
    submission = "legacy-approved"
    candidate = _build_formal_definition(payload, "development_validation", "dev_main")
    definition = backtest_definition_to_dict(candidate)
    definition["protocol"].pop("research_execution")
    definition["protocol"]["representative_execution"] = {"study_id": payload["study_id"], "submission_id": submission}
    scope = data.root / "research" / "representative-runs" / submission / "scope.json"
    scope.parent.mkdir(parents=True)
    scope.write_text(json.dumps({
        "definition": definition, "study_id": payload["study_id"], "approval_ref": "controlled legacy approval",
        "submission_id": submission, "run_count": 1, "benchmark_run_id": benchmark.run_id,
    }))

    experiments = ExperimentStore(data.root)
    jobs = JobService(experiments)
    saved = experiments.save_definition("backtest", backtest_definition_from_dict(definition))
    original = jobs.submit(saved, submission)
    if retry:
        assert jobs.cancel(original.job_id).state == "cancelled"
        old_retry = jobs.retry(original.job_id, "pre-upgrade-retry")
        assert jobs.cancel(old_retry.job_id).state == "cancelled"
    # The old submit stored these same jobs and submissions, without the lineage table.
    with experiments._connect() as connection:
        connection.execute("DROP TABLE job_lineage")
    experiments = ExperimentStore(data.root)
    upgraded = JobService(experiments)
    assert upgraded.get(original.job_id).retry_of_job_id is None
    attempt_log = scope.with_name("attempt.jsonl")
    if retry:
        unverified = upgraded.retry(old_retry.job_id, "retry-with-unknown-origin")
        refused = worker.run_job(unverified.job_id, experiments, data)
        assert refused.state == "failed" and "no verified original submission" in refused.reason
        assert not attempt_log.exists()
    job = upgraded.retry(original.job_id, "post-upgrade-retry") if retry else upgraded.get(original.job_id)
    if retry:
        assert job.retry_of_job_id == original.job_id
    completed = worker.run_job(job.job_id, experiments, data)
    assert completed.state == "succeeded", completed.reason
    assert upgraded.original_submission_id(job.job_id) == submission
    audit = [json.loads(line) for line in attempt_log.read_text().splitlines()]
    assert audit[0]["benchmark_run_id"] == benchmark.run_id
    assert [row["status"] for row in audit] == ["reserved", "started", "complete"]
    assert audit[-1]["actual_run_count"] == 1 and audit[-1]["run_id"] == completed.output.name
    assert ledger.read_bytes() == ledger_before


@pytest.mark.parametrize("bad_trial,reuse_benchmark,admission_issue", [
    ("dev_neighbor_30", False, None), ("dev_benchmark", False, None), ("dev_benchmark", True, None),
    (None, False, "missing"), (None, False, "blocked"),
], ids=["neighbor-mismatch", "benchmark-mismatch", "reused-benchmark", "missing-admission", "blocked-admission"])
def test_preview_and_worker_check_pending_trial_universes(
    tmp_path, monkeypatch, bad_trial, reuse_benchmark, admission_issue,
):
    from hetu_quant import backtest, data_research, strategies

    data, _, payload = _workflow(tmp_path / "facts", monkeypatch)
    expected = {}
    for trial_id in ("dev_benchmark", "dev_neighbor_30"):
        definition = _build_formal_definition(payload, "development_validation", trial_id)
        bundle = tmp_path / trial_id
        assert prepare_backtest(definition, data, bundle).can_run
        universe = pd.read_parquet(bundle / "universe.parquet")
        expected[trial_id] = universe.groupby("signal_date")["code"].agg(list).to_dict()
    if bad_trial is not None:
        wrong = expected[bad_trial]
        wrong[min(wrong)].append("B_MISSING")
    admission = data.root / "qualification" / "phase2"
    saved_admission = tmp_path / "saved-admission"
    if admission_issue == "missing":
        admission.rename(saved_admission)
        reasons = ("引擎／数据准入证据目录缺失",)
    elif admission_issue == "blocked":
        record, = admission.glob("engine-admission-*/engine-admission.json")
        ready_record = record.read_bytes()
        record.write_text(json.dumps({**json.loads(ready_record), "decision": "blocked"}))
        reasons = ("engine admission decision is 'blocked'", "engine-admission.json")
    else:
        reasons = (bad_trial, "B_MISSING")
    benchmark = None
    if reuse_benchmark:
        baseline = _build_formal_definition(payload, "development_validation", "dev_benchmark")
        benchmark = backtest.run_formal_job(baseline, data)
    tracked_trials = []

    def qualified_universes(payload, root, ids, trials):
        tracked_trials.append(set(ids))
        return {key: expected[key] for key in ids}

    monkeypatch.setattr(data_research, "require_frozen_trial_inputs", qualified_universes)
    signal_calls = []
    launches = []
    build_signals = strategies.build_signals
    run_engine = backtest.run_engine_subprocess

    def track_signals(definition, store):
        signal_calls.append(definition.strategy_id)
        return build_signals(definition, store)

    def track_engine(*args, **kwargs):
        launches.append(1)
        return run_engine(*args, **kwargs)

    monkeypatch.setattr(strategies, "build_signals", track_signals)
    monkeypatch.setattr(backtest, "run_engine_subprocess", track_engine)
    monkeypatch.setenv("HETU_QUANT_ROOT", str(data.root))
    monkeypatch.setattr("hetu_quant.ui.backtest_page._formal_payload", lambda _path: payload)
    app = AppTest.from_file(str(APP)).run()
    app.sidebar.radio[0].set_value("回测").run()
    app.selectbox(key="bt_trial").set_value("dev_neighbor_30").run()
    app.date_input(key="bt_start").set_value(definition.start).run()
    app.date_input(key="bt_end").set_value(pd.Timestamp(payload["definition"]["end"]).date()).run()
    app.text_input(key="bt_as_of").set_value(payload["definition"]["as_of"]).run()
    app.number_input(key="bt_slippage").set_value(payload["definition"]["protocol"]["costs"]["slippage_rate"]).run()
    ledger = data.root / "research" / payload["study_id"] / "trials.jsonl"
    before = ledger.read_bytes() if ledger.exists() else b""
    app.button(key="bt_check_qualification").click().run(timeout=20)
    assert (ledger.read_bytes() if ledger.exists() else b"") == before
    experiments = ExperimentStore(data.root)
    jobs = JobService(experiments)
    if reuse_benchmark:
        assert not app.error and not app.button(key="bt_submit_job").disabled
        app.button(key="bt_submit_job").click().run()
        job, = jobs.list()
    else:
        assert app.button(key="bt_submit_job").disabled
        assert any(all(reason in error.value for reason in reasons) for error in app.error)
        job = jobs.submit(experiments.save_definition("backtest", definition), "mismatch")
    result = worker.run_job(job.job_id, experiments, data)
    checked = {"dev_neighbor_30"} if reuse_benchmark else {"dev_benchmark", "dev_neighbor_30"}
    assert tracked_trials == [checked, checked]
    after = ledger.read_bytes() if ledger.exists() else b""
    if reuse_benchmark:
        assert result.state == "succeeded", result.reason
        assert (signal_calls, launches) == (["low_volatility"], [1])
        published = read_backtest(experiments.get_backtest(result.output.name))
        assert published["manifest"]["resolved_inputs"]["benchmark"]["run_id"] == benchmark.run_id
        assert after.startswith(before)
        added, = after[len(before):].splitlines()
        assert json.loads(added)["trial_id"] == "dev_neighbor_30"
    else:
        assert result.state == "failed" and all(reason in result.reason for reason in reasons)
        assert (signal_calls, launches, after) == ([], [], before)
    if admission_issue == "missing":
        saved_admission.rename(admission)
    elif admission_issue == "blocked":
        record.write_bytes(ready_record)
    if admission_issue is not None:
        app.button(key="bt_check_qualification").click().run(timeout=20)
        assert not app.error and not app.button(key="bt_submit_job").disabled
        app.button(key="bt_submit_job").click().run()
        assert any(queued.state == "queued" for queued in jobs.list())
        assert not ledger.exists() and not signal_calls and not launches


@pytest.mark.parametrize("cash_variant", [None, "conflict", "mismatch"])
def test_mutually_exclusive_disposition_is_visible_at_admission_and_worker(tmp_path, monkeypatch, cash_variant):
    from backtest_cases import build_disposition_case_store
    from hetu_data.archive import archive_requirements
    from hetu_quant import backtest, strategies
    from hetu_quant.backtest_models import backtest_definition_to_dict
    from hetu_quant.data_research import audit_research_coverage

    case, data, snapshot = build_disposition_case_store(tmp_path / "facts", cash_exit=True, cash_variant=cash_variant)
    _install_engine(data.root)
    facts = backtest_definition_to_dict(case_definition(case, snapshot.snapshot_id))
    payload = dict(study_id="synthetic-disposition-conflict", status="frozen_pre_result", definition=facts)
    definition = _build_formal_definition(payload, "development_validation", "dev_main")
    cash_terms = "cash_exit 2 or 8 CNY/share" if cash_variant == "conflict" else "cash_exit 2 CNY/share"
    expected = ("VOL_01", "2024-07-31", cash_terms, "share_exchange 2/3", "VOL_SUCCESSOR",
                "cash-exit.json", "share-exchange.json")
    if cash_variant == "conflict":
        expected += ("cash-other.json",)

    calendar = data.read_table(snapshot, "calendar")
    history = data.read_table(snapshot, "historical_securities").query("code == 'VOL_01'").assign(security_type="stock")
    requirements = archive_requirements(history, calendar, definition.end, start=definition.start)
    requirements.update(study_start=definition.start.isoformat(), signal_dates=[], definitions=[], legal_exclusions=[])
    audit = audit_research_coverage(requirements, data, snapshot)
    conflict, = [gap for gap in audit["event_limitations"] if gap["reason"] == "incompatible_account_dispositions"]
    assert all(value in json.dumps(conflict, ensure_ascii=False) for value in expected)
    if cash_variant == "conflict":
        gap, = [gap for gap in audit["event_limitations"] if gap["reason"] == "exit_disposal_conflicting_terms"]
        assert {(claim["source_ref"], claim["cash_per_share"]) for claim in gap["cash_claims"]} == {
            ("cash-exit.json", 2.0), ("cash-other.json", 8.0),
        }
    if cash_variant == "mismatch":
        gap, = [gap for gap in audit["event_limitations"] if gap["reason"] == "exit_disposal_terms_unproved"]
        assert [(claim["source_ref"], claim["cash_per_share"], claim["prepared_cash_per_share"])
                for claim in gap["cash_claims"]] == [("cash-exit.json", 2.0, 8.0)]
        expected += ("prepared 8 CNY/share",)
    prepared = prepare_backtest(definition, data, tmp_path / "preparation")
    assert not prepared.can_run and any(all(value in line for value in expected) for line in prepared.limitations)

    signals, launches = [], []
    original_build, original_launch = strategies.build_signals, backtest.run_engine_subprocess

    def count_signals(*args, **kwargs):
        signals.append(1)
        return original_build(*args, **kwargs)

    def count_engine(*args, **kwargs):
        launches.append(1)
        return original_launch(*args, **kwargs)

    monkeypatch.setattr(strategies, "build_signals", count_signals)
    monkeypatch.setattr(backtest, "run_engine_subprocess", count_engine)
    monkeypatch.setenv("HETU_QUANT_ROOT", str(data.root))
    monkeypatch.setattr("hetu_quant.ui.backtest_page._formal_payload", lambda _: payload)
    app = AppTest.from_file(str(APP)).run()
    app.sidebar.radio[0].set_value("回测").run()
    app.selectbox(key="bt_trial").set_value("dev_main").run()
    app.date_input(key="bt_start").set_value(definition.start).run()
    app.date_input(key="bt_end").set_value(pd.Timestamp(facts["end"]).date()).run()
    app.text_input(key="bt_as_of").set_value(facts["as_of"]).run()
    app.number_input(key="bt_slippage").set_value(facts["protocol"]["costs"]["slippage_rate"]).run()
    app.button(key="bt_check_qualification").click().run(timeout=20)
    assert not app.exception
    assert app.button(key="bt_submit_job").disabled and any(
        all(value in error.value for value in expected) for error in app.error
    )
    experiments = ExperimentStore(data.root)
    jobs = JobService(experiments)
    assert not jobs.list()
    direct = jobs.submit(experiments.save_definition("backtest", definition), "direct-conflict")
    finished = worker.run_job(direct.job_id, experiments, data)
    assert finished.state == "failed" and all(value in finished.reason for value in expected), finished.reason
    assert signals == launches == []
    assert not (data.root / "research" / payload["study_id"] / "trials.jsonl").exists()
    published = [json.loads(path.read_text()) for path in (data.root / "backtests").glob("*/backtest.json")]
    assert all(item["status"] == "limited" for item in published)
    print(f"admission conflict: {finished.reason}; signals=0, engine starts=0, trial rows=0")
