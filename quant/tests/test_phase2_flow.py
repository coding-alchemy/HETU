"""二期正式网页→免费合成事实→真实引擎→同产物读取。无市场请求或真实研究。"""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import zipfile
from pathlib import Path

import pandas as pd
from streamlit.testing.v1 import AppTest

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
    app = AppTest.from_file(str(APP)).run()
    app.sidebar.radio[0].set_value('回测').run()
    app.text_area(key='bt_protocol').set_value(json.dumps(payload)).run()
    app.date_input(key='bt_start').set_value(definition.start).run()
    app.date_input(key='bt_end').set_value(pd.Timestamp(payload['definition']['end']).date()).run()
    app.text_input(key='bt_as_of').set_value(payload['definition']['as_of']).run()
    app.selectbox(key='bt_strategy').set_value('low_volatility').run()
    app.number_input(key='bt_slippage').set_value(0.0).run()
    app.button(key='bt_check_qualification').click().run()
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
