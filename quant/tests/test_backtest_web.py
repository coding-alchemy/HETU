"""二期 04.1 回测网页闭环（引擎无关部分）：定义目录、worker 分派、页面、只读导出与比较。

合成闭环只含事实（pipeline case 与显式写入的准入文件）；任务重复、协议冻结、
导出一致性与比较期望留在本测试侧。引擎经 `_execute_backtest` 模块级替身隔离，
真实引擎子进程确认按 test_engine_admission 模式显式受阻跳过。
"""

from __future__ import annotations

import io
import json
import shutil
import threading
import zipfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from backtest_cases import build_case_store as _build_store
from backtest_cases import case_definition as _definition
from backtest_cases import make_backtest_case
from fixtures import annotate_actions_coverage
from hetu_quant import worker
from hetu_quant.backtest import TABLE_COLUMNS, run_backtest
from hetu_quant.backtest_models import BacktestRef
from hetu_quant.backtest_reporting import (
    compare_backtests,
    export_backtest,
    read_backtest,
)
from hetu_quant.data import DataStore
from hetu_quant.experiments import ExperimentStore
from hetu_quant.jobs import JobService
from hetu_quant.models import RankingDefinition
from hetu_quant.ui import app as app_module

CN = ZoneInfo("Asia/Shanghai")
APP_PATH = Path(app_module.__file__).resolve()
REPO_ROOT = APP_PATH.parents[4]
PHASE2_QUALIFICATION = REPO_ROOT / ".hetu" / "quant" / "qualification" / "phase2"


def _admission_decision() -> str | None:
    records = sorted(PHASE2_QUALIFICATION.glob("engine-admission-*/engine-admission.json"))
    if not records:
        return None
    return str(json.loads(records[-1].read_text(encoding="utf-8")).get("decision"))


def _admission_cost_semantics() -> str | None:
    """准入记录声明的成本语义；完整税费语义（02.1）完成前为 zero_cost_only。"""
    records = sorted(PHASE2_QUALIFICATION.glob("engine-admission-*/engine-admission.json"))
    if not records:
        return None
    payload = json.loads(records[-1].read_text(encoding="utf-8"))
    semantics = payload.get("semantics") or {}
    return str(semantics.get("costs"))


def _seed_root(tmp_path: Path) -> tuple[Path, object, dict]:
    """临时量化根：pipeline case 快照 + 已核验行动覆盖 + 根级产品配置。"""
    case = make_backtest_case("pipeline")
    store, ref = _build_store(tmp_path / "seed", case)
    shutil.copy(tmp_path / "seed" / "products.json", store.root / "data-products.json")
    coverage = case["actions_coverage"]
    annotate_actions_coverage(
        store, ref, coverage["start"], coverage["end"],
        codes=list(case["codes"]), query_basis="ex_date",  # type: ignore[arg-type]
    )
    return store.root, ref, case


def _jobs(root: Path) -> list:
    return JobService(ExperimentStore(root)).list()


def _publish_fake_backtest(root: Path, run_id: str = "bt-fake-1") -> BacktestRef:
    """直接构造已发布回测目录（九表 + 清单 + evaluation），不跑引擎。"""
    run_dir = Path(root) / "backtests" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    days = ["2024-01-03"]
    frames = {
        "cash": pd.DataFrame(
            {
                "date": days,
                "cash_available": [4995.0],
                "receivable": [0.0],
                "dividend_tax_payable": [0.0],
            }
        ),
        "positions": pd.DataFrame(
            {
                "date": days,
                "code": ["SYN_A"],
                "qty": [500],
                "sellable_qty": [0],
                "price_raw": [10.0],
                "market_value": [5000.0],
            }
        ),
        "nav": pd.DataFrame(
            {
                "date": days,
                "gross_nav": [9995.0],
                "net_nav": [9995.0],
                "benchmark_nav": [9995.0],
            }
        ),
    }
    for name, columns in TABLE_COLUMNS.items():
        frames.get(name, pd.DataFrame(columns=list(columns))).to_parquet(
            run_dir / f"{name}.parquet", index=False
        )
    (run_dir / "backtest.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "status": "complete",
                "definition": {"strategy_id": "low_volatility"},
                "sources": {"snapshot_id": "snap-x"},
                "limitations": [],
                "tables": {name: f"{name}.parquet" for name in TABLE_COLUMNS},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (run_dir / "evaluation.json").write_text(
        json.dumps({"economic_status": "not_evaluated", "reasons": ["stage=mechanism"]}),
        encoding="utf-8",
    )
    (run_dir / "report.md").write_text("# 回测 bt-fake-1\n", encoding="utf-8")
    return BacktestRef(run_id=run_id, root=Path(root), status="complete")


def _paid_guard(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """收费源调用计数守卫：被调用即失败并计数，期望全程为 0。"""
    calls: list[str] = []

    def forbidden(self, *args, **kwargs):
        calls.append(type(self).__name__)
        raise AssertionError("paid-capable fetch must not be called in free flow")

    monkeypatch.setattr("hetu_quant.providers.akshare.AKShareProvider.fetch", forbidden)
    monkeypatch.setattr("hetu_quant.providers.baostock.BaoStockProvider.fetch", forbidden)
    return calls


# ---- test_backtest_page_lifecycle_and_free_boundary ----


def test_backtest_page_lifecycle_and_free_boundary(tmp_path, monkeypatch) -> None:
    root, ref, case = _seed_root(tmp_path)
    paid_calls = _paid_guard(monkeypatch)
    monkeypatch.setenv("HETU_QUANT_ROOT", str(root))

    app = AppTest.from_file(str(APP_PATH)).run()
    assert not app.exception
    app.sidebar.radio[0].set_value("回测").run()
    app.radio(key="bt_stage").set_value("mechanism").run()
    assert not app.exception

    # 预览：解析后的实际定义 + prepare_backtest 资格（不降低门槛）
    app.button(key="bt_check_qualification").click().run()
    assert not app.exception
    previews = [json.loads(element.value) for element in app.json]
    assert any("can_run" in item and item["can_run"] for item in previews), (
        "合格合成输入的资格预览必须显示 can_run=True"
    )

    # 同次重复触发只一个任务
    app.button(key="bt_submit_job").click().run()
    app.button(key="bt_submit_job").click().run()
    assert not app.exception
    jobs = _jobs(root)
    assert len(jobs) == 1, "同次重复触发同一提交不得新建任务"
    first = jobs[0]

    # 双会话各提交一次 → 两个任务
    second_session = AppTest.from_file(str(APP_PATH)).run()
    second_session.sidebar.radio[0].set_value("回测").run()
    second_session.radio(key="bt_stage").set_value("mechanism").run()
    second_session.button(key="bt_submit_job").click().run()
    assert not second_session.exception
    jobs = _jobs(root)
    assert len(jobs) == 2, "两个会话各提交一次必须产生两个任务"

    # 慢引擎替身：queued → running → cancel_requested → cancelled，取消不发布产物
    entered = threading.Event()
    release = threading.Event()
    seen_definitions: list = []

    def slow_execute(definition, data, *, progress, cancelled):
        seen_definitions.append(definition)
        entered.set()
        release.wait(5)
        return _publish_fake_backtest(data.root)

    monkeypatch.setattr(worker, "_execute_backtest", slow_execute)
    service = JobService(ExperimentStore(root))
    outcome: dict = {}

    def run_worker():
        outcome["job"] = worker.run_job(first.job_id, ExperimentStore(root), DataStore(root))

    thread = threading.Thread(target=run_worker)
    thread.start()
    assert entered.wait(5), "worker 必须真正进入回测计算"
    assert service.get(first.job_id).state == "running"
    requested = service.cancel(first.job_id)
    assert requested.state == "cancel_requested"
    release.set()
    thread.join(10)
    final = service.get(first.job_id)
    assert final.state == "cancelled"
    assert final.output is None, "取消后不得发布 complete 产物"
    assert outcome["job"].state == "cancelled"

    # worker 重读冻结定义（页面残留默认值不影响已存定义）
    assert seen_definitions[0].snapshot_id == ref.snapshot_id
    assert seen_definitions[0].protocol["costs"]["commission_rate"] == 0.0003

    # 终态后的明确新点击产生新任务；刷新（重跑页面）不重跑
    app.button(key="bt_submit_job").click().run()
    assert not app.exception
    jobs = _jobs(root)
    assert len(jobs) == 3, "终态后的新点击必须新建任务"
    newest = jobs[0]
    assert newest.job_id != first.job_id and newest.state == "queued"

    fast_calls: list[str] = []

    def fast_execute(definition, data, *, progress, cancelled):
        fast_calls.append(definition.strategy_id)
        return _publish_fake_backtest(data.root)

    monkeypatch.setattr(worker, "_execute_backtest", fast_execute)
    done = worker.run_job(newest.job_id, ExperimentStore(root), DataStore(root))
    assert done.state == "succeeded"
    assert done.output == Path(root) / "backtests" / "bt-fake-1"

    refreshed = AppTest.from_file(str(APP_PATH)).run()
    refreshed.sidebar.radio[0].set_value("回测").run()
    assert not refreshed.exception
    assert len(_jobs(root)) == 3, "刷新不得产生新任务"
    assert len(ExperimentStore(root).list_backtests()) == 1, "刷新不得重跑已发布回测"
    text = "\n".join(str(item.value) for item in refreshed.markdown)
    assert "not_evaluated" in text or "经济" in text, "结果先显示研究/经济结论"

    # 重开找回：任务与产物在全新会话可见
    reopened = AppTest.from_file(str(APP_PATH)).run()
    table = reopened.dataframe[0].value
    assert first.job_id in set(table["任务号"].astype(str))
    reopened.sidebar.radio[0].set_value("回测").run()
    assert "bt-fake-1" in "\n".join(
        str(item.value) for item in reopened.selectbox
    )

    assert paid_calls == [], "免费源失败/闭环全程不得产生收费调用"


# ---- test_backtest_definition_freeze_and_old_result_read ----


def test_backtest_definition_freeze_and_old_result_read(tmp_path, monkeypatch) -> None:
    root, ref, case = _seed_root(tmp_path)
    experiments = ExperimentStore(root)

    definition = _definition(case, ref.snapshot_id)
    definition_id = experiments.save_definition("backtest", definition)

    # 保存后改变默认成本/参数（新默认值形成新定义），已存值不受影响
    protocol = json.loads(json.dumps(case["definition"]["protocol"]))  # type: ignore[index]
    protocol["costs"]["commission_rate"] = 0.0006
    protocol["warmup_months"] = 24
    changed = _definition(case, ref.snapshot_id, protocol=protocol)
    changed_id = experiments.save_definition("backtest", changed)
    assert changed_id != definition_id

    frozen = experiments.read_definition(definition_id)
    assert frozen.protocol["costs"]["commission_rate"] == 0.0003
    assert frozen.protocol["warmup_months"] == 12
    reread_changed = experiments.read_definition(changed_id)
    assert reread_changed.protocol["costs"]["commission_rate"] == 0.0006

    # 未知 kind 明确拒绝，不落入 ranking 的 else
    with pytest.raises(ValueError, match="unknown definition kind"):
        experiments.save_definition("mystery", definition)
    mystery_path = experiments.definitions_dir / "def-mystery.json"
    mystery_path.write_text(
        json.dumps(
            {"definition_id": "def-mystery", "kind": "mystery", "definition": {}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with experiments._connect() as conn:
        conn.execute(
            "INSERT INTO definitions VALUES (?, ?, ?, ?)",
            ("def-mystery", "mystery", str(mystery_path), "2026-10-17T00:00:00+08:00"),
        )
    with pytest.raises(ValueError, match="unknown definition kind"):
        experiments.read_definition("def-mystery")

    # 旧 ranking 任务/结果继续可读
    ranking_definition = RankingDefinition(
        as_of=datetime(2024, 7, 1, 9, 0, tzinfo=CN),
        cutoff=date(2024, 6, 28),
        snapshot_id="snap-any",
        benchmark_id="csi300_price",
        parameters={"purpose": "ranking_current"},
    )
    ranking_id = experiments.save_definition("ranking", ranking_definition)
    job = JobService(experiments).submit(ranking_id, "click-ranking-1")
    result_dir = experiments.results_dir / "res-legacy-1"
    result_dir.mkdir(parents=True)
    (result_dir / "result.json").write_text(
        json.dumps({"status": "complete", "definition_id": ranking_id}, ensure_ascii=False),
        encoding="utf-8",
    )
    assert experiments.get_result("res-legacy-1").status == "complete"
    assert "res-legacy-1" in experiments.list_results()
    assert job.definition_id == ranking_id

    # 回测目录独立于 ranking 结果：未完成 backtest 不冒充排名结果
    assert experiments.list_results() == ["res-legacy-1"]
    assert experiments.list_backtests() == []
    with pytest.raises(FileNotFoundError):
        experiments.get_backtest("bt-nothing")
    with pytest.raises(FileNotFoundError):
        experiments.get_result("bt-fake-1")

    # 已发布回测可读
    backtest_ref = _publish_fake_backtest(root)
    assert experiments.list_backtests() == ["bt-fake-1"]
    found = experiments.get_backtest("bt-fake-1")
    assert found.status == "complete" and found.run_id == backtest_ref.run_id


# ---- test_backtest_export_and_comparison ----


def _published_run(case: dict, store, ref, *, protocol=None) -> BacktestRef:
    """经 run_backtest 真实发布链（引擎 seam 写合成轨迹、合成 candidate_ready 准入）。"""
    from hetu_quant.backtest import run_backtest

    output_root = store.root
    admission_dir = Path(output_root) / "qualification" / "phase2" / "engine-admission-test"
    admission_dir.mkdir(parents=True, exist_ok=True)
    (admission_dir / "engine-admission.json").write_text(
        json.dumps({"decision": "candidate_ready"}), encoding="utf-8"
    )

    def fake_engine(engine_python, runner, bundle_dir, out_dir, cancelled=None) -> None:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "backtest.json").write_text(json.dumps({"status": "complete", "limitations": []}))
        days = ["2024-06-03"]
        pd.DataFrame(
            {
                "date": days,
                "cash_available": [4995.0],
                "receivable": [0.0],
                "dividend_tax_payable": [0.0],
            }
        ).to_parquet(out / "cash.parquet", index=False)
        pd.DataFrame(
            {
                "date": days,
                "code": ["SYN_A"],
                "qty": [500],
                "sellable_qty": [0],
                "price_raw": [10.0],
                "market_value": [5000.0],
            }
        ).to_parquet(out / "positions.parquet", index=False)
        pd.DataFrame(
            {
                "date": days,
                "gross_nav": [9995.0],
                "net_nav": [9995.0],
                "benchmark_nav": [9995.0],
            }
        ).to_parquet(out / "nav.parquet", index=False)

    import hetu_quant.backtest as backtest_module

    original = backtest_module.run_engine_subprocess
    backtest_module.run_engine_subprocess = fake_engine
    try:
        if protocol:
            definition = _definition(case, ref.snapshot_id, protocol=protocol)
        else:
            definition = _definition(case, ref.snapshot_id)
        return run_backtest(definition, store, output_root)
    finally:
        backtest_module.run_engine_subprocess = original


def test_backtest_export_and_comparison(tmp_path) -> None:
    case = make_backtest_case("pipeline")
    store, ref = _build_store(tmp_path / "seed", case)
    shutil.copy(tmp_path / "seed" / "products.json", store.root / "data-products.json")
    coverage = case["actions_coverage"]
    annotate_actions_coverage(
        store, ref, coverage["start"], coverage["end"],
        codes=list(case["codes"]), query_basis="ex_date",  # type: ignore[arg-type]
    )

    ref_a = _published_run(case, store, ref)
    assert ref_a.status == "complete"

    # 同池故意改成本：仍可并列比较并标明变更
    protocol_b = json.loads(json.dumps(case["definition"]["protocol"]))  # type: ignore[index]
    protocol_b["costs"]["commission_rate"] = 0.0006
    case_b_root = tmp_path / "b"
    case_b_store, ref_b_snap = _build_store(case_b_root, case)
    shutil.copy(case_b_root / "products.json", case_b_store.root / "data-products.json")
    annotate_actions_coverage(
        case_b_store, ref_b_snap, coverage["start"], coverage["end"],
        codes=list(case["codes"]), query_basis="ex_date",  # type: ignore[arg-type]
    )
    ref_b = _published_run(case, case_b_store, ref_b_snap, protocol=protocol_b)

    comparison = compare_backtests(ref_a, ref_b)
    assert comparison["mixed_ranking_allowed"] is True
    assert any("costs" in item for item in comparison["differences"]), (
        "同池成本变更必须作为差异标明"
    )

    # 不同基准：不兼容口径，不混排
    protocol_c = json.loads(json.dumps(protocol_b))
    protocol_c["benchmark_id"] = "csi300_price"
    ref_c = _published_run(case, case_b_store, ref_b_snap, protocol=protocol_c)
    comparison = compare_backtests(ref_a, ref_c)
    assert comparison["mixed_ranking_allowed"] is False
    assert any("benchmark_id" in item for item in comparison["differences"])

    # 导出：九表/evaluation/定义/限制与已发布值逐项一致（空表可导出）
    zip_path = export_backtest(ref_a, tmp_path / "exports")
    assert zip_path.exists() and zip_path.name.endswith(".zip")
    archive = zipfile.ZipFile(zip_path)
    names = set(archive.namelist())
    published = read_backtest(ref_a)
    for name in TABLE_COLUMNS:
        assert f"{name}.csv" in names
        exported = pd.read_csv(io.BytesIO(archive.read(f"{name}.csv")))
        published_table = published["tables"][name]
        assert list(exported.columns) == list(published_table.columns), (
            f"空表/轨迹表 {name} 导出的列必须与已发布一致"
        )
        assert exported.empty == published_table.empty
        if len(published_table):
            pd.testing.assert_frame_equal(
                exported.reset_index(drop=True),
                published_table.reset_index(drop=True),
                check_dtype=False,
            )
    exported_manifest = json.loads(archive.read("definition.json"))
    assert exported_manifest == published["manifest"]["definition"]
    assert json.loads(archive.read("evaluation.json")) == published["evaluation"]
    assert json.loads(archive.read("limitations.json")) == published["manifest"]["limitations"]
    assert "report.md" in names
    assert json.loads(archive.read("backtest.json")) == published["manifest"]
    assert json.loads(archive.read("engine.json")) == published["manifest"].get("engine", {})
    # 导出只读：发布目录字节不变
    before = (Path(ref_a.root) / "backtests" / ref_a.run_id / "backtest.json").read_bytes()
    export_backtest(ref_a, tmp_path / "exports" / "again")
    after = (Path(ref_a.root) / "backtests" / ref_a.run_id / "backtest.json").read_bytes()
    assert before == after

    # limited 结果（空表）同样可查看导出
    case_limited = make_backtest_case("pipeline", variant="unqualified")
    store_limited, ref_limited = _build_store(tmp_path / "c", case_limited)
    limited = run_backtest(
        _definition(case_limited, ref_limited.snapshot_id), store_limited, store_limited.root
    )
    assert limited.status == "limited"
    payload = read_backtest(limited)
    assert payload["manifest"]["limitations"]
    assert all(len(frame) == 0 for frame in payload["tables"].values())
    zip_limited = export_backtest(limited, tmp_path / "exports")
    archive_limited = zipfile.ZipFile(zip_limited)
    assert "signals.csv" in archive_limited.namelist()
    empty_signals = pd.read_csv(io.BytesIO(archive_limited.read("signals.csv")))
    assert list(empty_signals.columns) == list(TABLE_COLUMNS["signals"])
    assert len(empty_signals) == 0


# ---- worker 真实引擎子进程确认：显式受阻跳过 ----


@pytest.mark.skipif(
    _admission_decision() != "candidate_ready" or _admission_cost_semantics() != "full",
    reason=(
        "真实引擎未准入或引擎语义未完整（最新 engine-admission.json decision="
        f"{_admission_decision()!r}, costs={_admission_cost_semantics()!r}，见 "
        f"{PHASE2_QUALIFICATION}）：生产 bundle 含非零成本定义且由 03 信号链接入，"
        "完整引擎语义（02.1）完成前 worker 真实子进程确认显式受阻，禁用替身充当证据"
    ),
)
def test_worker_backtest_runs_real_engine_subprocess(tmp_path, monkeypatch) -> None:
    """准入通过后：worker.run_job 经 run_backtest 启动真实引擎子进程并发布。

    跳过本身不构成准入或机制通过证据（同 test_engine_admission 真实正例模式）。
    """
    root, ref, case = _seed_root(tmp_path)
    # run_backtest 默认从 output_root/runtime/rqalpha/.venv 取引擎解释器：
    # 链接到真实隔离环境，worker 链路不改签名
    engine_bin = Path(root) / "runtime" / "rqalpha" / ".venv" / "bin"
    engine_bin.mkdir(parents=True)
    real_python = (
        REPO_ROOT / ".hetu" / "quant" / "runtime" / "rqalpha" / ".venv" / "bin" / "python"
    ).absolute()
    assert real_python.exists()
    # 经外部符号链接调用 venv python 会丢失 venv 上下文（pyvenv.cfg 按解析后
    # 路径查找）：用包装脚本 exec 真实解释器
    wrapper = engine_bin / "python"
    wrapper.write_text(f'#!/bin/sh\nexec "{real_python}" "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)
    # 准入门从 output_root/qualification/phase2 读最新记录：复制真实准入记录
    records = sorted(PHASE2_QUALIFICATION.glob("engine-admission-*/engine-admission.json"))
    assert records
    latest = records[-1]
    admission_dir = Path(root) / "qualification" / "phase2" / latest.parent.name
    admission_dir.mkdir(parents=True)
    shutil.copyfile(latest, admission_dir / "engine-admission.json")
    experiments = ExperimentStore(root)
    definition_id = experiments.save_definition("backtest", _definition(case, ref.snapshot_id))
    job = JobService(experiments).submit(definition_id, "click-real-1")
    final = worker.run_job(job.job_id, experiments, DataStore(root))
    assert final.state == "succeeded"
    assert final.output is not None and (final.output / "backtest.json").exists()


def test_formal_page_freezes_selected_trial_without_mechanism_downgrade():
    """正式入口保留完整母协议，预览为实际展开试验定义。"""
    from hetu_quant.ui.backtest_page import _build_definition

    payload = json.loads((REPO_ROOT / 'quant/research/phase2-protocol.json').read_text())
    inputs = dict(strategy_id='low_volatility', snapshot_id='synthetic-snapshot',
                  start=date(2019, 11, 1), end=date(2026, 10, 13),
                  as_of=datetime(2026, 10, 17, 21, tzinfo=CN), initial_cash=1_000_000,
                  benchmark_id='universe_vw', commission_rate=.0003,
                  min_commission_cny=5, slippage_rate=.0005, lookback_months=36,
                  stage='formal', research_payload=payload,
                  phase='development_validation', trial_id='dev_main')
    definition = _build_definition(inputs)
    assert definition.protocol['stage'] == 'formal'
    frozen = definition.protocol['research_execution']
    assert definition.start.isoformat() == '2019-11-01'
    assert definition.end.isoformat() == '2023-12-31'
    assert definition.parameters['lookback_months'] == 36
    assert definition.protocol['rebalance']['frequency'] == 'monthly'
    assert frozen['study_id'] == payload['study_id']
    assert frozen['trial_id'] == 'dev_main'
    assert frozen['payload']['definition']['protocol']['time_split']['final'][0] == '2024-01-01'
    payload['definition']['protocol']['costs']['commission_rate'] = .9
    assert definition.protocol['costs']['commission_rate'] == .0003
    with pytest.raises(ValueError, match='phase'):
        _build_definition({**inputs, 'phase': 'final'})


def test_mechanism_page_selects_only_transparent_benchmark(tmp_path, monkeypatch):
    """周/月机制选择必须冻结透明市值基准，不把空信号低波动当能力证据。"""
    from hetu_quant.ui.backtest_page import _build_definition

    root, ref, _ = _seed_root(tmp_path)
    monkeypatch.setenv("HETU_QUANT_ROOT", str(root))
    app = AppTest.from_file(str(APP_PATH)).run()
    app.sidebar.radio[0].set_value("回测").run()
    app.radio(key="bt_stage").set_value("mechanism").run()
    assert app.selectbox(key="bt_strategy").options == ["universe_vw"]
    app.selectbox(key="bt_frequency").set_value("weekly").run()
    previews = [json.loads(element.value) for element in app.json]
    definition = next(item for item in previews if "strategy_id" in item)
    assert definition["strategy_id"] == "universe_vw"
    assert definition["protocol"]["rebalance"]["frequency"] == "weekly"
    assert definition["parameters"] == {}, "页面不得预填信号或交易答案"
    assert definition["protocol"]["warmup_months"] == 0, "市值基准不估计波动率预热"
    assert "not_evaluated" in "\n".join(str(item.value) for item in app.info)
    app.button(key="bt_submit_job").click().run()
    frozen = ExperimentStore(root).read_definition(_jobs(root)[0].definition_id)
    assert frozen.strategy_id == "universe_vw"
    assert frozen.protocol["rebalance"]["frequency"] == "weekly"
    assert frozen.parameters == {}
    with pytest.raises(ValueError, match="universe_vw"):
        _build_definition(dict(stage="mechanism", strategy_id="low_volatility"))
    assert frozen.snapshot_id == ref.snapshot_id
