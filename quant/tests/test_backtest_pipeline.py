"""二期 02.2 共同运行入口：资格门、阶段门、准入门、取消、原子发布与完整性校验。

合成输入只含事实（pipeline case 与显式写入的准入文件）；引擎调用次数、
limited/complete 判定与完整性期望全部留在本测试侧。引擎 seam 以模块级
替身计数，仅用于隔离长计算，不充当真实引擎证据；真实轨迹测试显式受阻跳过。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd
import pytest

from backtest_cases import build_case_store as _build_store
from backtest_cases import case_definition as _definition
from backtest_cases import make_backtest_case
from fixtures import annotate_actions_coverage
from hetu_quant.backtest import (
    TABLE_COLUMNS,
    BacktestCancelled,
    EngineNotAdmittedError,
    TraceIntegrityError,
    _atomic_publish,
    read_backtest,
    run_backtest,
    run_engine_subprocess,
    validate_trace_tables,
)
from hetu_quant.experiments import ExperimentStore

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = REPO_ROOT / "quant" / "engine" / "runner.py"
PHASE2_QUALIFICATION = REPO_ROOT / ".hetu" / "quant" / "qualification" / "phase2"


def _engine_python() -> Path | None:
    default = REPO_ROOT / ".hetu" / "quant" / "runtime" / "rqalpha" / ".venv" / "bin" / "python"
    return default if default.exists() else None


def _latest_admission() -> Path | None:
    records = sorted(PHASE2_QUALIFICATION.glob("engine-admission-*/engine-admission.json"))
    return records[-1] if records else None


def _admission_decision() -> str | None:
    latest = _latest_admission()
    if latest is None:
        return None
    return str(json.loads(latest.read_text(encoding="utf-8")).get("decision"))


def _admission_cost_semantics() -> str | None:
    """准入记录声明的成本语义；完整税费语义（02.1）完成前为 zero_cost_only。"""
    latest = _latest_admission()
    if latest is None:
        return None
    payload = json.loads(latest.read_text(encoding="utf-8"))
    semantics = payload.get("semantics") or {}
    return str(semantics.get("costs"))


def _write_admission(output_root: Path, decision: str | None, name: str = "engine-admission-20261017t") -> Path:
    directory = Path(output_root) / "qualification" / "phase2" / name
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "engine-admission.json"
    path.write_text(json.dumps({"decision": decision}, ensure_ascii=False), encoding="utf-8")
    return path


def _qualified_inputs(tmp_path: Path):
    case = make_backtest_case("pipeline")
    store, ref = _build_store(tmp_path / "store-root", case)
    coverage = case["actions_coverage"]
    annotate_actions_coverage(
        store, ref, coverage["start"], coverage["end"],
        codes=list(case["codes"]), query_basis="ex_date",  # type: ignore[arg-type]
    )
    return case, store, ref


def _seam(recorder: list, writer) -> object:
    """模块级引擎替身：计数调用并写合成轨迹事实，不充当真实引擎证据。"""

    def run(engine_python, runner, bundle_dir, out_dir, cancelled=None) -> None:
        recorder.append((Path(bundle_dir), Path(out_dir)))
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        writer(out)

    return run


def _valid_trace(out: Path) -> None:
    """两日常数账户轨迹：现金 1e6、无持仓、净值 = 现金（逐日恒等式成立）。"""
    (out / "backtest.json").write_text(json.dumps({"status": "complete", "limitations": []}))
    days = ["2024-06-03", "2024-06-04"]
    pd.DataFrame(
        {
            "date": days,
            "cash_available": [1_000_000.0, 1_000_000.0],
            "receivable": [0.0, 0.0],
            "dividend_tax_payable": [0.0, 0.0],
        }
    ).to_parquet(out / "cash.parquet", index=False)
    pd.DataFrame(
        {
            "date": days,
            "gross_nav": [1_000_000.0, 1_000_000.0],
            "net_nav": [1_000_000.0, 1_000_000.0],
            "benchmark_nav": [1_000_000.0, 1_000_000.0],
        }
    ).to_parquet(out / "nav.parquet", index=False)
    # 其余七张表故意不写：pipeline 必须以空固定列补齐


def _broken_trace(out: Path) -> None:
    _valid_trace(out)
    nav = pd.read_parquet(out / "nav.parquet")
    nav.loc[nav["date"] == "2024-06-03", "net_nav"] = 999_000.0  # 资产恒等式破坏
    nav.to_parquet(out / "nav.parquet", index=False)


# ---- 资格门：不合格输入不启动引擎，发布 limited ----


def test_pipeline_refuses_unqualified_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case = make_backtest_case("pipeline", variant="unqualified")
    store, ref = _build_store(tmp_path / "store-root", case)
    engine_calls: list = []
    monkeypatch.setattr(
        "hetu_quant.backtest.run_engine_subprocess",
        _seam(engine_calls, _valid_trace),
    )
    output_root = tmp_path / "out"

    ref_out = run_backtest(_definition(case, ref.snapshot_id), store, output_root)

    assert ref_out.status == "limited"
    assert engine_calls == []  # 资格失败：引擎调用 0
    run_dir = Path(ref_out.root) / "backtests" / ref_out.run_id
    assert run_dir.is_dir()

    manifest = json.loads((run_dir / "backtest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "limited"
    assert manifest["limitations"]  # limited 只存缺口
    assert manifest["excluded"]
    assert manifest["definition"]["strategy_id"] == "low_volatility"
    assert manifest["sources"]["snapshot_id"] == ref.snapshot_id

    # 九张空表存在且只有固定列
    for name, columns in TABLE_COLUMNS.items():
        table = pd.read_parquet(run_dir / f"{name}.parquet")
        assert list(table.columns) == list(columns)
        assert len(table) == 0

    evaluation = json.loads((run_dir / "evaluation.json").read_text(encoding="utf-8"))
    assert evaluation["economic_status"] == "not_evaluated"
    assert (run_dir / "report.md").is_file()


# ---- 阶段门：formal 协议不完整拒绝；formal 完整时走 build_signals 信号链 ----


def test_pipeline_rejects_formal_stage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """formal 协议不完整（缺目标/预算等）时拒绝且不启动引擎；
    formal 完整时不再抛 FormalBacktestNotReadyError：经 build_signals 产信号。"""
    case, store, ref = _qualified_inputs(tmp_path)
    engine_calls: list = []
    monkeypatch.setattr(
        "hetu_quant.backtest.run_engine_subprocess",
        _seam(engine_calls, _valid_trace),
    )
    protocol = dict(case["definition"]["protocol"])  # type: ignore[arg-type]
    protocol["stage"] = "formal"
    incomplete = dict(protocol)
    incomplete["objectives"] = {}  # 经济目标阈值为空：协议门拒绝（键仍齐全）
    formal = _definition(case, ref.snapshot_id, protocol=incomplete)

    # formal 协议不完整（缺目标等）时拒绝且不启动引擎
    with pytest.raises(ValueError, match="formal protocol incomplete"):
        run_backtest(formal, store, tmp_path / "out")
    assert engine_calls == []

    # 补全协议：formal 走 build_signals（合成 pipeline 无信号则空信号表也发布）
    complete = dict(protocol)
    complete.update(
        {
            "objectives": {"income": "final_net_cagr_gt_0_and_excess_gt_0"},
            "trial_budget": {"max_runs": 8},
            "viewing_record": {"final_seen": False},
            "benchmark_id": "universe_vw",
            "time_split": {
                "development": ["2020-01-31", "2020-12-31"],
                "validation": ["2021-01-29", "2021-12-31"],
                "final": ["2022-01-31", "2022-12-30"],
            },
            "costs": {
                "commission_rate": 0.0003,
                "min_commission_cny": 5,
                "slippage_rate": 0.0005,
                "statutory_rules_ref": "phase2-trading-rules",
            },
            "rules_ref": "phase2-trading-rules",
        }
    )
    signal_calls: list = []
    import hetu_quant.strategies as strategies

    def no_signals(*args, **kwargs):
        signal_calls.append(1)
        frame = pd.DataFrame(
            columns=["code", "signal_at", "executable_from", "signal_value", "rank", "target_weight", "source_ref"]
        )
        frame.attrs["factor_labels"] = pd.DataFrame(columns=[
            "code", "signal_at", "horizon_months", "factor_value", "label_return", "mature_at", "split_end"])
        return frame

    monkeypatch.setattr(strategies, "build_signals", no_signals)
    ready = _definition(case, ref.snapshot_id, protocol=complete)
    _write_admission(tmp_path / "out2", "candidate_ready")
    ref_out = run_backtest(ready, store, tmp_path / "out2", engine_python=Path(sys.executable))
    assert signal_calls == [1]  # formal 调用 build_signals，不注入 synthetic_signals
    assert ref_out.status == "complete"
    assert len(engine_calls) == 1
    run_dir = Path(ref_out.root) / "backtests" / ref_out.run_id
    manifest = json.loads((run_dir / "backtest.json").read_text(encoding="utf-8"))
    assert manifest["kind"] == "candidate"
    signals = pd.read_parquet(run_dir / "bundle" / "signals.parquet")
    assert len(signals) == 0  # spy 返回空信号表也如实落盘


# ---- 准入门：引擎未准入不启动任何子进程 ----


def test_pipeline_requires_engine_admission(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case, store, ref = _qualified_inputs(tmp_path)
    engine_calls: list = []
    monkeypatch.setattr(
        "hetu_quant.backtest.run_engine_subprocess",
        _seam(engine_calls, _valid_trace),
    )
    definition = _definition(case, ref.snapshot_id)
    output_root = tmp_path / "out"

    # 准入记录缺失：拒绝且不启动子进程。
    with pytest.raises(EngineNotAdmittedError, match="missing"):
        run_backtest(definition, store, output_root)
    assert engine_calls == []

    # 准入记录为 blocked：同样拒绝。
    _write_admission(output_root, "blocked")
    with pytest.raises(EngineNotAdmittedError, match="blocked"):
        run_backtest(definition, store, output_root)
    assert engine_calls == []

    # 发布目录不得出现 complete 结果。
    backtests_dir = output_root / "backtests"
    if backtests_dir.exists():
        published = [
            child
            for child in backtests_dir.iterdir()
            if child.is_dir() and not child.name.startswith(".tmp-") and child.name != "_partial"
        ]
        assert published == []


# ---- 准入门通过后的最小发布链路（合成轨迹仅驱动管道行为） ----


def test_pipeline_publishes_complete_atomically(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case, store, ref = _qualified_inputs(tmp_path)
    engine_calls: list = []
    monkeypatch.setattr(
        "hetu_quant.backtest.run_engine_subprocess",
        _seam(engine_calls, _valid_trace),
    )
    _write_admission(tmp_path / "out", "candidate_ready")
    progress_marks: list[float] = []

    ref_out = run_backtest(
        _definition(case, ref.snapshot_id),
        store,
        tmp_path / "out",
        progress=progress_marks.append,
    )

    assert ref_out.status == "complete"
    assert len(engine_calls) == 1
    assert progress_marks and progress_marks[-1] == 1.0

    backtests_dir = Path(ref_out.root) / "backtests"
    run_dir = backtests_dir / ref_out.run_id
    assert run_dir.is_dir()
    # 原子发布：临时目录已 rename，不残留
    assert not (backtests_dir / f".tmp-{ref_out.run_id}").exists()

    manifest = json.loads((run_dir / "backtest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "complete"
    assert manifest["engine"]["decision"] == "candidate_ready"
    assert set(manifest["tables"]) == set(TABLE_COLUMNS)

    # 引擎未写的表补齐为空固定列；已写的表内容一致
    published = read_backtest(ref_out)
    for name, columns in TABLE_COLUMNS.items():
        assert list(published["tables"][name].columns) == list(columns)
    assert published["tables"]["nav"]["net_nav"].tolist() == [1_000_000.0, 1_000_000.0]
    assert published["tables"]["orders"].empty

    evaluation = json.loads((run_dir / "evaluation.json").read_text(encoding="utf-8"))
    assert evaluation["economic_status"] == "not_evaluated"  # mechanism 阶段
    assert (run_dir / "report.md").is_file()


def test_pipeline_trace_integrity_failure_blocks_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case, store, ref = _qualified_inputs(tmp_path)
    engine_calls: list = []
    monkeypatch.setattr(
        "hetu_quant.backtest.run_engine_subprocess",
        _seam(engine_calls, _broken_trace),
    )
    _write_admission(tmp_path / "out", "candidate_ready")

    with pytest.raises(TraceIntegrityError, match="nav_identity"):
        run_backtest(_definition(case, ref.snapshot_id), store, tmp_path / "out")

    assert len(engine_calls) == 1
    backtests_dir = tmp_path / "out" / "backtests"
    published = [
        child.name
        for child in backtests_dir.iterdir()
        if child.is_dir() and not child.name.startswith((".tmp-", "_partial"))
    ]
    assert published == []


# ---- 取消、部分产物保留、重开只读与一期旧结果保护 ----


def test_backtest_publish_cancel_and_reopen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case, store, ref = _qualified_inputs(tmp_path)

    # 一期旧 ranking 结果直接构造结果文件（不重跑一期排名）
    experiment_store = ExperimentStore(tmp_path / "out")
    legacy_dir = experiment_store.results_dir / "result-legacy-1"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "result.json").write_text(
        json.dumps({"status": "complete", "definition_id": "def-legacy"}, ensure_ascii=False),
        encoding="utf-8",
    )
    legacy_bytes_before = (legacy_dir / "result.json").read_bytes()

    engine_calls: list = []
    monkeypatch.setattr(
        "hetu_quant.backtest.run_engine_subprocess",
        _seam(engine_calls, _valid_trace),
    )
    _write_admission(tmp_path / "out", "candidate_ready")

    # 1) 引擎启动前取消：不发布 complete，部分产物保留在 _partial/<run_id>/
    with pytest.raises(BacktestCancelled):
        run_backtest(
            _definition(case, ref.snapshot_id),
            store,
            tmp_path / "out",
            cancelled=lambda: True,
        )
    assert engine_calls == []
    backtests_dir = tmp_path / "out" / "backtests"
    partial_dirs = list((backtests_dir / "_partial").iterdir())
    assert len(partial_dirs) == 1
    partial = partial_dirs[0]
    assert (partial / "bundle").is_dir()  # 阶段证据保留供排查
    complete_runs = [
        child.name
        for child in backtests_dir.iterdir()
        if child.is_dir() and not child.name.startswith((".tmp-", "_partial"))
    ]
    assert complete_runs == []

    # 2) 阶段边界取消（引擎后）：同样不发布、保留部分产物
    fired = {"engine": False}

    def cancel_after_engine():
        return fired["engine"]

    def late_cancel_seam(engine_python, runner, bundle_dir, out_dir, cancelled=None) -> None:
        fired["engine"] = True
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        _valid_trace(out)

    monkeypatch.setattr("hetu_quant.backtest.run_engine_subprocess", late_cancel_seam)
    with pytest.raises(BacktestCancelled):
        run_backtest(
            _definition(case, ref.snapshot_id),
            store,
            tmp_path / "out",
            cancelled=cancel_after_engine,
        )
    assert len(list((backtests_dir / "_partial").iterdir())) == 2
    complete_runs = [
        child.name
        for child in backtests_dir.iterdir()
        if child.is_dir() and not child.name.startswith((".tmp-", "_partial"))
    ]
    assert complete_runs == []

    # 3) limited 发布可重开读取，读取只读：发布目录字节前后不变
    case_limited = make_backtest_case("pipeline", variant="unqualified")
    store_limited, ref_limited = _build_store(tmp_path / "store-limited", case_limited)
    limited_ref = run_backtest(
        _definition(case_limited, ref_limited.snapshot_id), store_limited, tmp_path / "out"
    )
    assert limited_ref.status == "limited"
    limited_dir = Path(limited_ref.root) / "backtests" / limited_ref.run_id

    def _snapshot_bytes(directory: Path) -> dict[str, bytes]:
        return {
            str(path.relative_to(directory)): path.read_bytes()
            for path in sorted(directory.rglob("*"))
            if path.is_file()
        }

    before = _snapshot_bytes(limited_dir)
    reopened = read_backtest(limited_ref)
    assert reopened["manifest"]["status"] == "limited"
    for name, columns in TABLE_COLUMNS.items():  # 空表固定列
        assert list(reopened["tables"][name].columns) == list(columns)
        assert len(reopened["tables"][name]) == 0
    assert _snapshot_bytes(limited_dir) == before  # 重开只读

    # 一期旧 ranking 结果字节全程不变
    assert (legacy_dir / "result.json").read_bytes() == legacy_bytes_before


# ---- 完整性校验：对合成轨迹帧做单元级测试 ----


def _identity_frames(net_nav: float = 9995.0) -> dict[str, pd.DataFrame]:
    return {
        "cash": pd.DataFrame(
            {
                "date": ["2024-01-03"],
                "cash_available": [4995.0],
                "receivable": [0.0],
                "dividend_tax_payable": [0.0],
            }
        ),
        "positions": pd.DataFrame(
            {
                "date": ["2024-01-03"],
                "code": ["SYN_A"],
                "qty": [500],
                "sellable_qty": [0],
                "price_raw": [10.0],
                "market_value": [5000.0],
            }
        ),
        "nav": pd.DataFrame(
            {
                "date": ["2024-01-03"],
                "gross_nav": [9995.0],
                "net_nav": [net_nav],
                "benchmark_nav": [9995.0],
            }
        ),
        "targets": pd.DataFrame(
            {
                "signal_at": ["2024-01-02"],
                "executable_from": ["2024-01-03"],
                "code": ["SYN_A"],
                "target_weight": [1.0],
            }
        ),
    }


def test_trace_identity_holds_for_consistent_frames() -> None:
    assert validate_trace_tables(_identity_frames()) == []


def test_trace_integrity_checks(tmp_path: Path) -> None:
    # 现金为负
    tables = _identity_frames()
    tables["cash"].loc[0, "cash_available"] = -1.0
    violations = validate_trace_tables(tables)
    assert any("cash_negative" in item for item in violations)

    # 持仓数量为负
    tables = _identity_frames()
    tables["positions"].loc[0, "qty"] = -100
    violations = validate_trace_tables(tables)
    assert any("position_negative" in item for item in violations)

    # 目标权重和超过 1（容差 1e-10）
    tables = _identity_frames()
    extra = pd.DataFrame(
        {
            "signal_at": ["2024-01-02"],
            "executable_from": ["2024-01-03"],
            "code": ["SYN_B"],
            "target_weight": [1e-8],
        }
    )
    tables["targets"] = pd.concat([tables["targets"], extra], ignore_index=True)
    violations = validate_trace_tables(tables)
    assert any("target_weight_sum" in item for item in violations)

    # 逐日资产恒等式：nav ≠ cash + positions 市值（容差 0.01）
    tables = _identity_frames(net_nav=9994.0)
    violations = validate_trace_tables(tables)
    assert any("nav_identity" in item for item in violations)

    # 容差内不报错
    tables = _identity_frames(net_nav=9995.005)
    assert validate_trace_tables(tables) == []


def test_trace_integrity_rejects_non_finite_values() -> None:
    # net_nav 为 NaN：diff 比较对 NaN 恒 False，必须显式判违例
    tables = _identity_frames(net_nav=float("nan"))
    violations = validate_trace_tables(tables)
    assert any("nav_identity" in item for item in violations)

    # 现金为 inf 且 net_nav 同为 inf：abs(inf-inf)=NaN 不得放行
    tables = _identity_frames(net_nav=float("inf"))
    tables["cash"].loc[0, "cash_available"] = float("inf")
    violations = validate_trace_tables(tables)
    assert any("cash_negative" in item for item in violations)
    assert any("nav_identity" in item for item in violations)

    # 持仓市值为 inf
    tables = _identity_frames()
    tables["positions"].loc[0, "market_value"] = float("inf")
    violations = validate_trace_tables(tables)
    assert any("position_negative" in item for item in violations)


def test_atomic_publish_refuses_existing(tmp_path: Path) -> None:
    staging = tmp_path / ".tmp-bt-x"
    staging.mkdir()
    (staging / "backtest.json").write_text("{}", encoding="utf-8")
    target = tmp_path / "bt-x"
    target.mkdir()
    with pytest.raises(FileExistsError):
        _atomic_publish(staging, target)
    assert staging.exists()  # 拒绝覆盖：临时目录保留待排查
    _atomic_publish(staging, tmp_path / "bt-y")
    assert (tmp_path / "bt-y" / "backtest.json").is_file()


def test_pipeline_case_rejects_unknown_variant() -> None:
    with pytest.raises(ValueError, match="unknown variant"):
        make_backtest_case("pipeline", variant="cheat")


# ---- 真实引擎轨迹：显式受阻跳过，不用 mock 引擎充当证据 ----


@pytest.mark.skipif(
    _admission_decision() != "candidate_ready" or _admission_cost_semantics() != "full",
    reason=(
        "真实引擎未准入或成本语义未完整（最新 engine-admission.json decision="
        f"{_admission_decision()!r}, costs={_admission_cost_semantics()!r}，见 "
        f"{PHASE2_QUALIFICATION}）：毛净分解需要 02.1 完整税费语义的同一成交轨迹，"
        "禁用 mock 引擎替代；跳过本身不构成准入或机制通过证据"
    ),
)
def test_cost_decomposition_uses_one_execution_trace(tmp_path: Path) -> None:
    """毛净来自同一成交轨迹：gross − net 与显式费用逐项相等（容差 0.01 元）。

    真实引擎经 run_backtest 完整链路（prepare_backtest→子进程→发布）执行
    mechanism 合成信号（定义 parameters.synthetic_signals 标明 synthetic）；
    跳过本身不构成准入或机制通过证据。
    """
    case, store, ref = _qualified_inputs(tmp_path)
    latest = _latest_admission()
    assert latest is not None
    engine_python = _engine_python()
    assert engine_python is not None
    ref_out = run_backtest(
        _definition(case, ref.snapshot_id),
        store,
        tmp_path / "out",
        admission_path=latest,
        engine_python=engine_python,
    )
    assert ref_out.status == "complete"
    published = read_backtest(ref_out)

    nav = published["tables"]["nav"]
    trades = published["tables"]["trades"]
    explicit = (
        trades["commission"].sum()
        + trades["stamp_tax"].sum()
        + trades["transfer_fee"].sum()
        + trades["dividend_tax"].sum()
    )
    initial_cash = float(case["definition"]["protocol"]["initial_cash_cny"])
    gross_pnl = float(nav["gross_nav"].iloc[-1] - initial_cash)
    net_pnl = float(nav["net_nav"].iloc[-1] - initial_cash)
    assert abs(gross_pnl - net_pnl - explicit) <= 0.01
    # 非零成本定义真实执行：显式费用必须为正，不得拿零成本重跑冒充相同毛收益
    assert explicit > 0


def test_unproven_ex_reference_reaches_published_evaluation(tmp_path):
    """导入缺现金金额的合成行动：真实引擎限制必须传到发布及经济评价。"""
    from hetu_quant.backtest_reporting import read_backtest as read_published

    case = make_backtest_case("pipeline")
    case["csvs"]["corporate_actions"] = (
        "code,ex_date,action_type,m,d,terms_raw,available_at,record_date,pay_date,stock_listing_date\n"
        "KEEP_A,2021-06-30,cash_dividend,1,,合成未知现金金额,2021-05-30T18:00:00+08:00,"
        "2021-05-31,2021-07-31,\n"
    )
    store, ref = _build_store(tmp_path / "store", case)
    coverage = case["actions_coverage"]
    annotate_actions_coverage(store, ref, coverage["start"], coverage["end"],
                             codes=list(case["codes"]), query_basis="ex_date")
    latest, interpreter = _latest_admission(), _engine_python()
    assert latest is not None and interpreter is not None
    published_ref = run_backtest(_definition(case, ref.snapshot_id), store, tmp_path / "out",
                                admission_path=latest, engine_python=interpreter)
    published = read_published(published_ref)
    assert published_ref.status == "limited"
    assert any("limit_reference_unproven" in reason for reason in published["manifest"]["limitations"])
    assert published["evaluation"]["research_status"] == "blocked"
    assert published["evaluation"]["economic_status"] == "not_evaluated"
    assert published["tables"]["trades"].empty


# ---- runner CLI 一致性：同一 bundle 经 run_backtest 内部链路与独立 CLI 一致 ----


@pytest.mark.skipif(
    _admission_decision() != "candidate_ready" or _admission_cost_semantics() != "full",
    reason=(
        "真实引擎未准入或成本语义未完整（最新 engine-admission.json decision="
        f"{_admission_decision()!r}, costs={_admission_cost_semantics()!r}，见 "
        f"{PHASE2_QUALIFICATION}）：CLI 一致性需要 02.1 完整语义的真实引擎"
    ),
)
def test_runner_cli_matches_internal_execution(tmp_path: Path) -> None:
    """同一 bundle：run_backtest 内部子进程链路与独立 runner CLI 逐表一致。"""
    case, store, ref = _qualified_inputs(tmp_path)
    latest = _latest_admission()
    assert latest is not None
    engine_python = _engine_python()
    assert engine_python is not None
    ref_out = run_backtest(
        _definition(case, ref.snapshot_id),
        store,
        tmp_path / "out",
        admission_path=latest,
        engine_python=engine_python,
    )
    assert ref_out.status == "complete"
    run_dir = Path(ref_out.root) / "backtests" / ref_out.run_id
    bundle_dir = run_dir / "bundle"
    assert (bundle_dir / "admission.json").exists()
    assert (bundle_dir / "signals.parquet").exists()

    cli_out = tmp_path / "cli-out"
    completed = subprocess.run(
        [
            str(engine_python), str(RUNNER_PATH),
            "--bundle", str(bundle_dir), "--out", str(cli_out),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr
    # order_id/trade_id 是引擎按运行时刻生成的运行内标识，非语义内容：
    # 逐表比对时剔除，其余列逐值一致
    generated_ids = {"orders": ["order_id"], "trades": ["trade_id", "order_id"], "costs": ["order_id"]}
    for name in TABLE_COLUMNS:
        internal = pd.read_parquet(run_dir / "engine-out" / f"{name}.parquet")
        cli = pd.read_parquet(cli_out / f"{name}.parquet")
        drop = generated_ids.get(name, [])
        pd.testing.assert_frame_equal(
            internal.drop(columns=drop), cli.drop(columns=drop), check_dtype=False
        )


# ---- 取消语义：运行中引擎子进程被停止并回收 ----


def test_engine_subprocess_cancel_terminates_and_recovers(tmp_path: Path) -> None:
    """引擎运行中取消：子进程 terminate/kill 回收，抛 BacktestCancelled。"""
    slow_runner = tmp_path / "slow_runner.py"
    slow_runner.write_text("import time\ntime.sleep(120)\n", encoding="utf-8")
    engine_python = Path(sys.executable)
    started = time.time()
    with pytest.raises(BacktestCancelled):
        run_engine_subprocess(
            engine_python,
            slow_runner,
            tmp_path / "bundle-unused",
            tmp_path / "out",
            cancelled=lambda: True,
        )
    elapsed = time.time() - started
    assert elapsed < 30  # 未等待慢进程跑完即回收
    check = subprocess.run(
        ["pgrep", "-f", str(slow_runner)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert check.returncode != 0, "slow runner process still alive after cancel"
