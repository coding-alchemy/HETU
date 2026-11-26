"""二期 02.2/03 回测共同运行入口：资格门、协议门、准入门、取消、原子发布。

`run_backtest` 是唯一生产入口：先 `prepare_backtest` 到发布暂存目录，
资格失败发布 limited（九张空固定列表 + not_evaluated，不调用引擎）；
`stage='formal'` 由 03 接通 build_signals 产正式信号（协议取值不完整由
require_formal_protocol 拒绝），mechanism 注入 synthetic_signals；formal
可传 benchmark=BacktestRef 填 nav.benchmark_nav 后评价。引擎子进程仅在
`engine-admission.json` 的 decision 为 candidate_ready 时启动。取消在
引擎启动前与各阶段边界检查，取消/失败的部分产物保留在
`backtests/_partial/<run_id>/` 供排查，发布目录 `backtests/<run_id>/`
不出现该 run_id。发布原子进行：先写 `backtests/.tmp-<run_id>` 再 rename，
目标已存在拒绝覆盖——轨迹与评价不可事后覆盖。

准入文件位置：默认从 output_root 推导，取
`output_root/qualification/phase2/engine-admission-*/engine-admission.json`
中目录名最新的一份；也可经关键字参数 `admission_path` 显式传入。
引擎解释器默认取 `output_root/runtime/rqalpha/.venv/bin/python`。
"""

from __future__ import annotations

import json
import re
import runpy
import shutil
import subprocess
import uuid
from collections.abc import Callable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from hetu_data.locking import worker_lock
from hetu_data.models import SnapshotRef

from hetu_quant.account_evidence import account_dispositions
from hetu_quant.backtest_data import STRATEGY_PURPOSES, prepare_backtest
from hetu_quant.backtest_evaluation import evaluate_backtest, research_exposure_inputs
from hetu_quant.backtest_models import (
    BacktestDefinition,
    BacktestRef,
    backtest_definition_from_dict,
    backtest_definition_to_dict,
)

if TYPE_CHECKING:
    from hetu_quant.data import DataStore

CN = ZoneInfo("Asia/Shanghai")

ADMISSION_READY = "candidate_ready"

# 九张固定轨迹表的最小列；空表也必须有列。
TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "signals": ("signal_at", "executable_from", "code", "signal_value", "rank", "source_ref"),
    "targets": ("signal_at", "executable_from", "code", "target_weight"),
    "orders": (
        "order_id", "signal_at", "submitted_at", "code", "side",
        "requested_qty", "filled_qty", "status", "reason",
    ),
    "trades": (
        "trade_id", "order_id", "executed_at", "code", "side", "qty",
        "price_raw", "execution_price", "commission", "stamp_tax",
        "transfer_fee", "dividend_tax", "slippage_cost",
    ),
    "positions": ("date", "code", "qty", "sellable_qty", "price_raw", "market_value"),
    "cash": ("date", "cash_available", "receivable", "dividend_tax_payable"),
    "nav": ("date", "gross_nav", "net_nav", "benchmark_nav"),
    "costs": ("date", "order_id", "code", "cost_type", "amount"),
    "exclusions": ("date", "code", "scope", "field", "reason", "source_ref"),
}

RUNNER_PATH = Path(__file__).resolve().parents[2] / "engine" / "runner.py"

_WEIGHT_SUM_TOLERANCE = 1e-10
_NAV_IDENTITY_TOLERANCE_CNY = 0.01


class BacktestCancelled(RuntimeError):
    """阶段边界收到取消：不发布 complete，部分产物转 _partial。"""


class FormalBacktestNotReadyError(RuntimeError):
    """历史兼容：formal 分支已由 03 接通（build_signals），本异常不再抛出。

    formal 协议取值不完整时改由 strategies.require_formal_protocol 抛
    ValueError；保留此类以免既有导入失效。
    """


class EngineNotAdmittedError(RuntimeError):
    """引擎准入记录缺失或 decision 非 candidate_ready：拒绝启动子进程。"""


class TraceIntegrityError(RuntimeError):
    """发布前完整性校验失败：现金／持仓为负、权重超限或资产恒等式不成立。"""


def _new_run_id() -> str:
    stamp = datetime.now(CN).strftime("%Y%m%dT%H%M%S")
    return f"bt-{stamp}-{uuid.uuid4().hex[:8]}"


def _raise_if_cancelled(cancelled: Callable[[], bool] | None) -> None:
    if cancelled is not None and cancelled():
        raise BacktestCancelled("cancelled at stage boundary")


def _write_json(path: Path, doc: Mapping[str, object]) -> None:
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def _inject_mechanism_signals(staging: Path, definition: BacktestDefinition) -> None:
    """mechanism 阶段的合成机制信号：来自定义 parameters.synthetic_signals
    （调用方标明 synthetic 的输入事实），在 prepare_backtest 之后、引擎之前
    写入暂存 bundle 根级 signals.parquet；bundle 已有 signals 时不动。
    """
    bundle = staging / "bundle"
    if (bundle / "signals.parquet").exists():
        return
    raw = definition.parameters.get("synthetic_signals") or ()
    rows = [
        {
            "signal_at": str(item["signal_at"]),
            "executable_from": str(item["executable_from"]),
            "code": str(item["code"]),
            "target_weight": float(item["target_weight"]),
        }
        for item in raw
    ]
    pd.DataFrame(
        rows, columns=["signal_at", "executable_from", "code", "target_weight"]
    ).to_parquet(bundle / "signals.parquet", index=False)


# ---- 完整性校验（独立可测） ----


def validate_trace_tables(tables: Mapping[str, pd.DataFrame]) -> list[str]:
    """对固定轨迹表做发布前完整性校验，返回违例列表（空 = 通过）。

    检查：现金各科目非负；持仓数量与非负市值；目标权重按 signal_at
    求和 ≤ 1（容差 1e-10）；逐日资产恒等式 net_nav = 现金可用 + 应收
    − 应付股息税 + 持仓市值（容差 0.01 元）。
    """
    violations: list[str] = []
    cash = tables.get("cash")
    if cash is not None and len(cash):
        for column in ("cash_available", "receivable", "dividend_tax_payable"):
            values = pd.to_numeric(cash[column], errors="coerce")
            bad = cash[~np.isfinite(values) | (values < -1e-9)]
            for row in bad.itertuples():
                violations.append(
                    f"cash_negative: {column}={getattr(row, column)} on {row.date}"
                )

    positions = tables.get("positions")
    if positions is not None and len(positions):
        for column in ("qty", "sellable_qty", "market_value"):
            values = pd.to_numeric(positions[column], errors="coerce")
            bad = positions[~np.isfinite(values) | (values < -1e-9)]
            for row in bad.itertuples():
                violations.append(
                    f"position_negative: {column}={getattr(row, column)} "
                    f"on {row.date} {row.code}"
                )

    targets = tables.get("targets")
    if targets is not None and len(targets):
        grouped = targets.groupby(["signal_at", "executable_from"], sort=False)
        for (signal_at, executable_from), group in grouped:
            total = float(pd.to_numeric(group["target_weight"], errors="coerce").sum())
            if total > 1.0 + _WEIGHT_SUM_TOLERANCE:
                violations.append(
                    f"target_weight_sum: {signal_at}/{executable_from} 目标权重和 "
                    f"{total:.12f} 超过 1（容差 1e-10）"
                )

    nav = tables.get("nav")
    if nav is not None and len(nav):
        cash_totals: dict[str, float] = {}
        if cash is not None and len(cash):
            for row in cash.itertuples():
                cash_totals[str(row.date)] = (
                    float(row.cash_available) + float(row.receivable)
                    - float(row.dividend_tax_payable)
                )
        market_values: dict[str, float] = {}
        if positions is not None and len(positions):
            for row in positions.itertuples():
                day = str(row.date)
                market_values[day] = market_values.get(day, 0.0) + float(row.market_value)
        net_nav_values = pd.to_numeric(nav["net_nav"], errors="coerce")
        for index, row in enumerate(nav.itertuples()):
            day = str(row.date)
            net_nav = net_nav_values.iloc[index]
            if not np.isfinite(net_nav):
                violations.append(
                    f"nav_identity: {day} net_nav 非有限数值（{net_nav}）"
                )
                continue
            if day not in cash_totals:
                violations.append(f"nav_identity: {day} 缺 cash 记录，无法核对资产恒等式")
                continue
            expected = cash_totals[day] + market_values.get(day, 0.0)
            if not np.isfinite(expected):
                violations.append(
                    f"nav_identity: {day} 现金+持仓市值非有限数值（{expected}）"
                )
                continue
            diff = abs(float(net_nav) - expected)
            if diff > _NAV_IDENTITY_TOLERANCE_CNY:
                violations.append(
                    f"nav_identity: {day} net_nav={float(net_nav):.4f} ≠ "
                    f"现金+持仓市值={expected:.4f}（差 {diff:.4f} 元，容差 0.01）"
                )
    return violations


# ---- 准入与引擎子进程 ----


def _default_admission_path(output_root: Path) -> Path:
    phase2 = Path(output_root) / "qualification" / "phase2"
    records = sorted(phase2.glob("engine-admission-*/engine-admission.json"))
    if records:
        return records[-1]
    return phase2 / "engine-admission" / "engine-admission.json"


def _require_admission(path: Path) -> Mapping[str, Any]:
    if not path.exists():
        raise EngineNotAdmittedError(f"engine admission record missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    decision = payload.get("decision")
    if decision != ADMISSION_READY:
        raise EngineNotAdmittedError(
            f"engine admission decision is {decision!r}, required {ADMISSION_READY!r} "
            f"({path}); refusing to start engine"
        )
    return payload


def run_engine_subprocess(
    engine_python: Path,
    runner: Path,
    bundle_dir: Path,
    out_dir: Path,
    cancelled: Callable[[], bool] | None = None,
    on_engine_start: Callable[[], None] | None = None,
) -> None:
    """受控引擎子进程入口（模块级 seam，测试以替身隔离长计算）。

    只读已核验本地 bundle，不在线补数；非零退出即失败。运行中收到取消：
    terminate 回收子进程（超时后 kill），抛 BacktestCancelled，由 run_backtest
    把阶段产物转入 _partial。
    """
    engine_python = Path(engine_python)
    if not engine_python.exists():
        raise FileNotFoundError(f"engine python missing: {engine_python}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [
            str(engine_python), str(runner),
            "--bundle", str(bundle_dir), "--out", str(out_dir),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        if on_engine_start is not None:
            on_engine_start()
        while process.poll() is None:
            if cancelled is not None and cancelled():
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
                raise BacktestCancelled("cancelled during engine execution")
            try:
                process.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                continue
        stdout, stderr = process.communicate()
        if process.returncode != 0:
            raise RuntimeError(
                f"engine subprocess failed (rc={process.returncode}): "
                f"{(stderr or '').strip() or 'no stderr'}"
            )
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


# ---- 发布 ----


def _atomic_publish(staging: Path, target: Path) -> None:
    """临时目录 rename 为正式目录；目标已存在拒绝（轨迹不可事后覆盖）。"""
    staging = Path(staging)
    target = Path(target)
    if target.exists():
        raise FileExistsError(f"backtest run already published: {target}")
    staging.rename(target)


def _load_trace_tables(engine_out: Path) -> dict[str, pd.DataFrame]:
    tables: dict[str, pd.DataFrame] = {}
    for name, columns in TABLE_COLUMNS.items():
        path = Path(engine_out) / f"{name}.parquet"
        if path.exists():
            tables[name] = pd.read_parquet(path)
        else:
            tables[name] = pd.DataFrame(columns=list(columns))
            if name == "positions":
                tables["positions_record_missing"] = pd.DataFrame()
    return tables


def _write_report(staging: Path, manifest: Mapping[str, Any], evaluation: Mapping[str, Any]) -> None:
    definition = manifest["definition"]
    protocol = definition["protocol"]  # type: ignore[index]
    lines = [
        f"# 回测 {manifest['run_id']}",
        "",
        f"status: {manifest['status']}",
        f"stage: {protocol['stage']}",
        f"snapshot: {manifest['sources']['snapshot_id']}",
        "",
        "## 限制与缺口",
    ]
    limitations = manifest.get("limitations") or []
    if limitations:
        lines.extend(f"- {item}" for item in limitations)
    else:
        lines.append("- 无")
    lines.extend(["", "## 评价", f"economic_status: {evaluation['economic_status']}"])
    lines.extend(f"- {reason}" for reason in evaluation.get("reasons", []))
    if "attribution" in evaluation:
        attribution = evaluation["attribution"]
        lines.extend([
            "", "## 同批账户归因",
            "逐证券的原信号、公司行动、费用及目标与成交日收盘持仓的偏差见 `evaluation.json`。",
        ])
        lines.extend(f"- {reason}" for reason in attribution["limitations"])
        lines.append(json.dumps(attribution["by_period"], ensure_ascii=False, indent=2))
    metrics = evaluation.get("metrics", {})
    if "daily_nav_gaps" in metrics:
        lines.extend(["", "## 逐日净值缺口",
                      json.dumps(metrics["daily_nav_gaps"], ensure_ascii=False, indent=2)])
        if "daily_nav_evidence" in metrics:
            lines.extend(["", "缺口日期的已知净值（人民币元）",
                          json.dumps(metrics["daily_nav_evidence"], ensure_ascii=False, indent=2)])
    if "exposures" in metrics:
        exposure = metrics["exposures"]
        lines.extend(["", "## 规模和行业暴露", exposure["basis"],
                      f"状态：{exposure['status']}。快照数：{len(exposure['snapshots'])}。",
                      "完整逐股依据见 evaluation.json。"])
        for snapshot in exposure["snapshots"]:
            for label, kind in (("目标", "target"), ("实际", "actual")):
                item = snapshot[kind]
                evidence = (item or {}).get("evidence", {})
                if evidence.get("status") == "all_cash":
                    lines.append(f"- {snapshot['date']} {label}：全现金，股票暴露 0%。")
                elif evidence.get("status") == "unknown":
                    lines.append(f"- {snapshot['date']} {label}：{evidence['reason']}。")
                elif item is not None:
                    lines.append(
                        f"- {snapshot['date']} {label}：加权市值（人民币元）"
                        f" {item['size']['weighted_mean_market_cap_cny']}，"
                        f"行业权重 {json.dumps(item['industry']['weights'], ensure_ascii=False)}，"
                        f"未知行业权重 {item['industry']['unknown_weight']:.4%}"
                    )
    if "comparison" in metrics:
        lines.extend(["", "## 压力试验与主策略",
                      json.dumps(metrics["comparison"], ensure_ascii=False, indent=2)])
    if "market_states" in metrics:
        lines.extend(["", "## 市场状态",
                      json.dumps(metrics["market_states"], ensure_ascii=False, indent=2)])
    if evaluation.get("factor_diagnostics"):
        lines.extend(["", "## 因子诊断（不调整正式权重）",
                      json.dumps(evaluation["factor_diagnostics"], ensure_ascii=False, indent=2)])
    (staging / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_publish(
    staging: Path,
    manifest: Mapping[str, Any],
    tables: Mapping[str, pd.DataFrame],
    evaluation: Mapping[str, Any],
) -> None:
    _write_json(staging / "backtest.json", manifest)
    for name in TABLE_COLUMNS:
        tables[name].to_parquet(staging / f"{name}.parquet", index=False)
    _write_json(staging / "evaluation.json", evaluation)
    _write_report(staging, manifest, evaluation)


def read_backtest(ref: BacktestRef) -> dict[str, Any]:
    """只读重开已发布回测：backtest.json 清单 + 九张轨迹表，不计算、不写盘。"""
    run_dir = Path(ref.root) / "backtests" / ref.run_id
    manifest_path = run_dir / "backtest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"backtest manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    tables = {
        name: pd.read_parquet(run_dir / f"{name}.parquet") for name in TABLE_COLUMNS
    }
    return {"manifest": manifest, "tables": tables}


def run_formal_job(
    definition: BacktestDefinition, store: DataStore, *, progress=None, cancelled=None
) -> BacktestRef:
    """队列正式入口复用独立脚本的八次调度；提交值不能绕过冻结母定义。"""
    document = backtest_definition_to_dict(definition)
    selection = document['protocol'].pop('research_execution', None)
    if not isinstance(selection, dict) or definition.protocol['stage'] != 'formal':
        raise ValueError('formal job requires research_execution selection')
    payload = selection.get('payload')
    if not isinstance(payload, dict):
        raise ValueError('research_execution requires frozen payload')
    study_id = selection.get('study_id')
    if (not isinstance(study_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', study_id)
            or payload.get('study_id') != study_id):
        raise ValueError('research_execution study_id mismatch or invalid')
    scheduler = runpy.run_path(str(Path(__file__).resolve().parents[2] / 'research' / 'run_validation.py'))
    selected = next((row for row in scheduler['validation_definitions'](payload)
                     if row['trial_id'] == selection.get('trial_id')), None)
    if (selected is None or selected['phase'] != selection.get('phase')
            or selected['definition'] != document):
        raise ValueError('saved definition differs from selected frozen research definition')
    result = scheduler['run_validation'](
        Path('queued-frozen-protocol'), store.root, store.root / 'research' / study_id,
        phase=selection['phase'], trial_id=selection['trial_id'], payload=payload,
        progress=progress, cancelled=cancelled)
    return BacktestRef(run_id=result['candidate']['run_id'], root=store.root, status='complete')


def _read_account_approval(root: Path, study_id: str, submission_id: str, payload: dict, role: str) -> dict:
    if not all(isinstance(key, str) and re.fullmatch(r"[A-Za-z0-9_-]+", key) for key in (study_id, submission_id)):
        raise ValueError("invalid representative approval reference")
    path = root / "research" / "representative-runs" / submission_id / "scope.json"
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("representative approval is missing")
    approval = json.loads(path.read_text(encoding="utf-8"))
    if approval.get("definition") != payload:
        raise ValueError(f"saved definition differs from {role} approval")
    if (approval.get("study_id") != study_id or approval.get("submission_id") != submission_id
            or approval.get("role", "representative") != role
            or not approval.get("approval_ref") or approval.get("run_count") != 1):
        raise ValueError(f"{role} approval requires a matching role, study, approval reference and one scoped run")
    approved_fee = approval.get("approved_data_fee_cny", 0)
    if type(approved_fee) not in (int, float) or approved_fee != 0:
        raise ValueError(f"{role} approval requires zero data fee in free mode")
    return approval


def _representative_benchmark_approval(root: Path, approval: dict) -> dict:
    payload = json.loads(json.dumps(approval["definition"]))
    protocol = payload["protocol"]
    payload["strategy_id"] = protocol["benchmark_id"]
    protocol.pop("representative_execution")
    protocol.pop("comparison", None)
    return _read_account_approval(
        root, approval["study_id"], approval["benchmark_submission_id"], payload, "benchmark"
    )


def read_representative_approval(definition: BacktestDefinition, root: Path) -> dict:
    """Read the operator's approval for the exact frozen free definition."""
    payload = backtest_definition_to_dict(definition)
    protocol = payload["protocol"]
    selection = protocol.get("representative_execution")
    if (protocol["stage"] != "formal" or definition.mode != "free"
            or not isinstance(selection, dict) or protocol.get("research_execution")
            or definition.strategy_id == protocol["benchmark_id"]):
        raise ValueError("representative execution requires a free formal candidate")
    approval = _read_account_approval(root, selection.get("study_id"), selection.get("submission_id"),
                                     payload, "representative")
    references = [approval[key] for key in ("benchmark_run_id", "benchmark_submission_id") if key in approval]
    if (len(references) != 1 or not isinstance(references[0], str)
            or not re.fullmatch(r"[A-Za-z0-9_-]+", references[0])):
        raise ValueError("representative approval requires one published or independently approved benchmark")
    if "benchmark_submission_id" in approval:
        if protocol.get("comparison", {}).get("purpose") == "sensitivity_vs_main":
            raise ValueError("sensitivity comparison requires its published main account")
        _representative_benchmark_approval(root, approval)
    return approval


def run_representative_job(
    definition: BacktestDefinition, store: DataStore, *, submission_id: str, progress=None, cancelled=None
) -> BacktestRef:
    """Execute an approved platform task outside the frozen research trial ledger."""
    from hetu_quant.data_research import require_representative_inputs

    approval = read_representative_approval(definition, store.root)
    if submission_id != approval["submission_id"]:
        raise ValueError("submission differs from representative approval")
    attempt = _account_attempt_path(store.root, submission_id)
    _account_attempt_result(attempt)
    expected_universe = require_representative_inputs(approval, store.root)
    if "benchmark_run_id" in approval:
        benchmark = BacktestRef(run_id=approval["benchmark_run_id"], root=store.root, status="complete")
    else:
        benchmark_approval = _representative_benchmark_approval(store.root, approval)
        benchmark = _run_independently_approved_account(
            backtest_definition_from_dict(benchmark_approval["definition"]), store, benchmark_approval,
            expected_universe=require_representative_inputs(benchmark_approval, store.root),
            cancelled=cancelled,
        )
        if benchmark.status != "complete":
            raise ValueError("the approved benchmark did not publish a complete account")
    return _run_independently_approved_account(
        definition, store, approval, expected_universe=expected_universe, benchmark=benchmark,
        progress=progress, cancelled=cancelled,
    )


def _account_attempt_path(root: Path, submission_id: str) -> Path:
    return root / "research" / "representative-runs" / submission_id / "attempt.jsonl"


def _account_attempt_result(attempt: Path, *, reuse_benchmark: bool = False) -> str | None:
    if not attempt.exists():
        return None
    rows = [json.loads(line) for line in attempt.read_text(encoding="utf-8").splitlines() if line.strip()]
    if rows:
        last = rows[-1]
        if last.get("status") in ("failed", "cancelled") and last.get("actual_run_count") == 0:
            return None
        if (reuse_benchmark and rows[0].get("role") == "benchmark" and last.get("status") == "complete"
                and last.get("actual_run_count") == 1 and isinstance(last.get("run_id"), str)
                and re.fullmatch(r"[A-Za-z0-9_-]+", last["run_id"])):
            return last["run_id"]
    raise FileExistsError(f"account authorization is in flight or already consumed: {attempt}")


def _run_independently_approved_account(
    definition, store, approval, *, expected_universe, benchmark=None, progress=None, cancelled=None
) -> BacktestRef:
    """Serialize access to the approval and retain every launch attempt."""
    attempt = _account_attempt_path(store.root, approval["submission_id"])
    with worker_lock(attempt.parent):
        published = _account_attempt_result(attempt, reuse_benchmark=approval.get("role") == "benchmark")
        if published is not None:
            return BacktestRef(run_id=published, root=store.root, status="complete")
        return _run_account_attempt(definition, store, approval, attempt, expected_universe=expected_universe,
                                    benchmark=benchmark, progress=progress, cancelled=cancelled)


def _run_account_attempt(
    definition, store, approval, attempt, *, expected_universe, benchmark, progress, cancelled
) -> BacktestRef:
    reserved = False
    started = False

    def record_attempt(record: dict) -> None:
        with attempt.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    def reserve() -> None:
        nonlocal reserved
        record = {
            "submission_id": approval["submission_id"], "study_id": approval["study_id"],
            "role": approval.get("role", "representative"), "status": "reserved",
            "approval_ref": approval["approval_ref"], "approved_run_count": approval["run_count"],
            "approved_data_fee_cny": approval.get("approved_data_fee_cny", 0),
            "approved_account_costs": approval["definition"]["protocol"]["costs"],
            "benchmark_run_id": benchmark.run_id if benchmark is not None else None,
        }
        record_attempt(record)
        reserved = True

    def mark_started() -> None:
        nonlocal started
        started = True
        record_attempt({"status": "started", "actual_run_count": 1})

    result = None
    try:
        delivery = approval.get("qualification_delivery")
        input_options = {"qualification_delivery": Path(delivery)} if delivery is not None else {}
        ref = run_backtest(
            definition, store, store.root, benchmark=benchmark, progress=progress, cancelled=cancelled,
            before_engine=reserve, on_engine_start=mark_started, expected_universe=expected_universe,
            **input_options,
        )
    except Exception as error:
        result = {"status": "cancelled" if isinstance(error, BacktestCancelled) else "failed", "detail": str(error)}
        raise
    else:
        result = {"status": ref.status, "run_id": ref.run_id}
        costs_path = ref.root / "backtests" / ref.run_id / "costs.parquet"
        if costs_path.is_file():
            costs = pd.read_parquet(costs_path)
            result["actual_account_costs_cny"] = costs.groupby("cost_type")["amount"].sum().to_dict()
        return ref
    finally:
        if reserved and result is not None:
            result.update(actual_run_count=int(started), actual_data_fee_cny=0)
            record_attempt(result)


def _compare_position_events(bundles, codes, warmup, start, end, as_of):
    """Read the account inputs that can change positions or cash in the common window."""
    names = ("identity_transitions", "terminal_disposals", "share_exchanges")
    frames = [{name: pd.read_parquet(path) if path.is_file() else pd.DataFrame()
               for name in names for path in (bundle / "tables" / f"{name}.parquet",)}
              for bundle in bundles]
    decisions = [
        account_dispositions(side["terminal_disposals"].to_dict("records"), side["share_exchanges"].to_dict("records"),
                             bundle / "evidence", start, end,
                             as_of=datetime.fromisoformat(cutoff) if cutoff else None)
        for bundle, side, cutoff in zip(bundles, frames, as_of, strict=True)
    ]
    renames = [{
        (str(row.from_code), str(row.effective_date)):
        (row.code, row.from_valid_to, row.identity_id, row.ratio, row.kind)
        for row in side["identity_transitions"].itertuples() if warmup <= row.effective_date <= end
    } for side in frames]
    edges = [(code, facts[0]) for side in renames for (code, _), facts in side.items()]
    edges.extend((code, event["code"]) for _, swaps, *_ in decisions for (code, _), event in swaps.items()
                 if event.get("ratio") and event.get("code"))
    # The reference can hold a predecessor even if no later signal named it.
    while True:
        expanded = codes | {code for edge in edges if codes.intersection(edge) for code in edge}
        if expanded == codes:
            break
        codes = expanded
    left, right = renames
    differences = [f"data_values:identity_transitions:{code}@{day}" for code, day in sorted(left.keys() | right.keys())
                   if code in codes and left.get((code, day)) != right.get((code, day))]
    facts = []
    for cash, swaps, cash_gaps, swap_gaps, conflicts in decisions:
        events = {key: ("cash_exit", event["cash_per_share"], event["currency"], event["unit"])
                  for key, event in cash.items()}
        events.update({key: ("share_exchange", *(event.get(field) for field in (
            "code", "ratio", "credit_date", "sellable_date", "share_rule", "fraction_rule", "fraction_cash_cny",
            "fraction_pay_date",
        ))) for key, event in swaps.items()})
        for gap in [*cash_gaps, *swap_gaps, *conflicts]:
            key = gap["code"], gap["date"]
            events[key] = (*events.get(key, ()), gap["reason"], *gap.get("fields", ()), gap.get("terms"))
        facts.append(events)
    left, right = facts
    for code, day in sorted(left.keys() | right.keys()):
        candidate, reference = left.get((code, day)), right.get((code, day))
        if code in codes and candidate != reference:
            events = (candidate or ()) + (reference or ())
            field = "terminal_disposals" if any(str(value).startswith(("cash_exit", "exit_disposal"))
                                                for value in events) else "share_exchanges"
            differences.append(f"data_values:{field}:{code}@{day}: candidate={candidate}; reference={reference}")
    return codes, differences


def validate_benchmark_reference(
    definition: BacktestDefinition, reference: BacktestRef, candidate_bundle: Path | None = None
) -> dict:
    """只读按比较用途核验实际定义/输入；不使用批次、ID、哈希或新旧日期判等。"""
    content = read_backtest(reference)
    manifest = content["manifest"]
    previous = manifest.get("definition") or {}
    current = backtest_definition_to_dict(definition)
    protocol, prior_protocol = current["protocol"], previous.get("protocol", {})
    purpose = protocol.get("comparison", {}).get("purpose", "matched_account")
    sensitivity = purpose == "sensitivity_vs_main"
    required_strategy = "low_volatility" if sensitivity else protocol["benchmark_id"]
    required_kind = "candidate" if sensitivity else "benchmark"
    differences = []
    if (manifest.get("status") != "complete" or manifest.get("kind") != required_kind
            or previous.get("strategy_id") != required_strategy):
        differences.append("reference_kind_or_strategy")
    if (previous.get("start") != current["start"]
            or str(previous.get("end", "")) < current["end"]):
        differences.append("execution_interval")
    for key in ("universe", "initial_cash_cny", "currency", "account_type", "leverage",
                "cash_interest_rate", "investor_tax_profile", "rebalance"):
        if prior_protocol.get(key) != protocol.get(key):
            differences.append(key)
    changed = set(protocol.get("comparison", {}).get("changed_assumptions", [])) if sensitivity else set()
    costs = {k: v for k, v in protocol.get("costs", {}).items() if k != "statutory_rules_ref"}
    old_costs = {k: v for k, v in prior_protocol.get("costs", {}).items() if k != "statutory_rules_ref"}
    if old_costs != costs and "costs" not in changed:
        differences.append("costs")
    execution, old_execution = dict(protocol.get("execution", {})), dict(prior_protocol.get("execution", {}))
    if "participation_rate" in changed:
        execution.pop("participation_rate", None)
        old_execution.pop("participation_rate", None)
    if execution != old_execution:
        differences.append("execution")
    root = reference.root / "backtests" / reference.run_id / "bundle"
    if candidate_bundle is not None and not differences:
        # 比较实际使用域的事实值；来源定位/版本标识不同不构成事实变化。
        universe_path = Path(candidate_bundle) / "universe.parquet"
        old_universe = root / "universe.parquet"
        if not universe_path.exists() or not old_universe.exists():
            differences.append("universe_evidence_missing")
        else:
            universe = pd.read_parquet(universe_path)
            prior_universe = pd.read_parquet(old_universe)
            left = universe[universe["signal_date"].astype(str) <= current["end"]]
            right = prior_universe[prior_universe["signal_date"].astype(str) <= current["end"]]
            if set(map(tuple, left[["signal_date", "code"]].to_numpy())) != set(
                    map(tuple, right[["signal_date", "code"]].to_numpy())):
                differences.append("actual_historical_universe")
            codes = set(left["code"].astype(str))
            positions = content["tables"]["positions"]
            if not positions.empty:
                positions = positions[positions.date.astype(str).between(current["start"], current["end"])]
                codes.update(positions.loc[positions.qty.gt(0), "code"].astype(str))
            warmup = (pd.Timestamp(current["start"]) - pd.DateOffset(months=int(protocol["warmup_months"]))).date()
            codes, event_differences = _compare_position_events(
                (Path(candidate_bundle), root), codes, str(warmup), current["start"], current["end"],
                (current.get("as_of"), previous.get("as_of")),
            )
            differences.extend(event_differences)
            for name in ("calendar", "daily_bars", "security_status", "corporate_actions",
                         "market_cap", "trading_rules", "historical_securities"):
                a, b = Path(candidate_bundle) / "tables" / f"{name}.parquet", root / "tables" / f"{name}.parquet"
                if name == "historical_securities" and not a.exists() and not b.exists():
                    continue
                if not a.exists() or not b.exists():
                    differences.append(f"data_missing:{name}")
                    continue
                frames = []
                for path in (a, b):
                    frame = pd.read_parquet(path)
                    if "code" in frame:
                        frame = frame[frame["code"].astype(str).isin(codes)]
                    day = "date" if "date" in frame else "ex_date" if "ex_date" in frame else None
                    if day:
                        frame = frame[frame[day].astype(str).between(str(warmup), current["end"])]
                    if name == "historical_securities":
                        last = frame["valid_to"].astype("string").fillna("")
                        first = frame["valid_from"].astype("string").fillna("")
                        frame = frame.loc[first.le(current["end"]) & (last.ge(str(warmup)) | last.eq(""))].copy()
                        frame["valid_to"] = last.where(last.ne("") & last.le(current["end"]), current["end"])
                        frame["valid_from"] = first.where(first.ge(str(warmup)), str(warmup))
                        frame = frame[[col for col in (
                            "code", "valid_from", "valid_to", "listed", "market", "board", "security_type",
                            "identity_id",
                        ) if col in frame]]
                    frame = frame.drop(
                        columns=["source_ref", "product_id", "evidence_ref", "rule_id", "sha256"], errors="ignore")
                    frames.append(frame.astype("string").fillna("").drop_duplicates().sort_index(axis=1))
                if all(frame.empty for frame in frames):
                    continue
                columns = list(frames[0].columns)
                if (set(columns) != set(frames[1].columns)
                        or sorted(map(tuple, frames[0][columns].to_numpy())) != sorted(
                            map(tuple, frames[1][columns].to_numpy()))):
                    differences.append(f"data_values:{name}")
    if differences:
        raise ValueError("incompatible_benchmark: " + ", ".join(differences))
    return manifest


def validate_prepared_universe(bundle: Path, expected_universe: Mapping[str, list[str]] | None) -> None:
    """Apply the same per-date universe check in preview and execution."""
    if expected_universe is None:
        return
    universe = pd.read_parquet(bundle / "universe.parquet")
    used = set(zip(universe["signal_date"].astype(str), universe["code"].astype(str), strict=True))
    expected = {(day, code) for day, codes in expected_universe.items() for code in codes}
    if used != expected:
        differences = sorted(used.symmetric_difference(expected))
        raise ValueError(f"prepared account universe differs from qualification: {differences[:5]}")



def _link_published_after_close_simulation(output_root: Path, run_id: str, origin: Mapping) -> None:
    from logging import getLogger

    from hetu_quant.after_close import link_after_close_evidence

    try:
        link_after_close_evidence(output_root, str(origin["publication_id"]),
                                  signal_date=date.fromisoformat(str(origin["signal_date"])),
                                  strategy_id=str(origin["strategy_id"]), kind="simulation", source_id=run_id)
    except (OSError, ValueError, KeyError):
        # The completed account is already immutable. The same source ID can be registered again.
        getLogger(__name__).exception("after-close link pending for published run %s", run_id)


def _reference_nav(tables):
    return tables["nav"][["date", "net_nav"]].astype({"date": str}).rename(columns={"net_nav": "benchmark_nav"})


def _attach_formal_references(definition, tables, bundle, run_id, benchmark, market_benchmark=None) -> dict:
    benchmark_id = definition.protocol["benchmark_id"]
    sensitivity = definition.protocol.get("comparison", {}).get("purpose") == "sensitivity_vs_main"
    if benchmark is None:
        if definition.strategy_id != benchmark_id:
            return {"benchmark": None}
        tables["nav"]["benchmark_nav"] = tables["nav"]["net_nav"]
        provenance = {"benchmark_id": benchmark_id, "strategy_id": benchmark_id, "kind": "account", "run_id": run_id}
    else:
        main = validate_benchmark_reference(definition, benchmark, bundle)
        nav = _reference_nav(read_backtest(benchmark)["tables"])
        tables["comparison_nav"] = nav
        tables["nav"] = tables["nav"].drop(columns="benchmark_nav").astype({"date": str}).merge(
            nav, on="date", how="left", validate="one_to_one",
        )
        provenance = {
            "benchmark_id": benchmark_id, "strategy_id": main["definition"]["strategy_id"],
            "kind": "account", "run_id": benchmark.run_id,
            "resolved_from": str(benchmark.root / "backtests" / benchmark.run_id),
        }
    tables["benchmark"] = pd.DataFrame([provenance])
    resolved = {"benchmark": provenance}
    if sensitivity and benchmark is not None:
        evidence = {"benchmark_id": benchmark_id, "kind": "account"}
        prior = (main.get("resolved_inputs", {}).get("benchmark") or {}).get("run_id")
        if market_benchmark is None and isinstance(prior, str) and re.fullmatch(r"[A-Za-z0-9_-]+", prior):
            market_benchmark = BacktestRef(run_id=prior, root=benchmark.root, status="complete")
        try:
            if market_benchmark is None:
                raise ValueError("缺同池 universe_vw 账户净值")
            if prior and market_benchmark.run_id != prior:
                raise ValueError("同池账户与 final_main 发布记录所引用的账户不一致")
            main_definition = backtest_definition_from_dict(main["definition"])
            market = validate_benchmark_reference(main_definition, market_benchmark, bundle)
            if (market["definition"]["start"], market["definition"]["end"]) != (
                definition.start.isoformat(), definition.end.isoformat(),
            ):
                raise ValueError("同池账户起止日与冻结压力试验区间不同")
            evidence.update(run_id=market_benchmark.run_id, strategy_id=market["definition"]["strategy_id"],
                            resolved_from=str(market_benchmark.root / "backtests" / market_benchmark.run_id))
            tables["market_nav"] = _reference_nav(read_backtest(market_benchmark)["tables"])
        except (OSError, ValueError) as error:
            evidence["gap"] = str(error)
        tables["market_benchmark"] = pd.DataFrame([evidence])
        resolved["market_benchmark"] = evidence
    return resolved


def run_backtest(
    definition: BacktestDefinition,
    store: DataStore,
    output_root: Path,
    *,
    progress: Callable[[float], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    admission_path: Path | None = None,
    engine_python: Path | None = None,
    benchmark: BacktestRef | None = None,
    market_benchmark: BacktestRef | None = None,
    research_context: Mapping[str, object] | None = None,
    before_engine: Callable[[], None] | None = None,
    on_engine_start: Callable[[], None] | None = None,
    expected_universe: Mapping[str, list[str]] | None = None,
    qualification_delivery: Path | None = None,
) -> BacktestRef:
    """资格→输入包→准入→引擎→完整性校验→评价基座→原子发布。

    返回 BacktestRef（root 为 output_root，发布目录 root/backtests/<run_id>）。
    资格失败发布 limited（九张空固定列表、not_evaluated）并直接返回；
    stage='formal' 且资格与准入成立时经 build_signals 产正式信号（协议取值
    不完整由 require_formal_protocol 拒绝）；mechanism 仍注入 synthetic_signals；
    引擎未准入抛 EngineNotAdmittedError，不启动子进程；取消抛 BacktestCancelled，
    部分产物保留在 backtests/_partial/<run_id>/；引擎运行中取消时回收子进程。
    engine_python 默认取 output_root/runtime/rqalpha/.venv/bin/python。
    formal 可传 benchmark=BacktestRef：先执行/读取已获批且适用的基准轨迹
    （同池同成本账户基准），将其净值填入 nav.benchmark_nav 后评价；基准自身
    （strategy_id == 协议 benchmark_id）以本账户净值为参照并标记 kind=benchmark。
    """
    output_root = Path(output_root)
    origin = (research_context or {}).get("after_close_origin")
    publication = None
    if origin is not None:
        from hetu_quant.after_close import after_close_origin

        if not isinstance(origin, Mapping):
            raise ValueError("invalid after_close_origin")
        publication = after_close_origin(output_root, str(origin["publication_id"]),
                                         date.fromisoformat(str(origin["signal_date"])), str(origin["strategy_id"]))
        if origin["strategy_id"] != definition.strategy_id:
            raise ValueError("simulation strategy differs from after-close origin")
    is_formal = str(definition.protocol["stage"]) == "formal"
    if is_formal:
        from hetu_quant.strategies import require_formal_protocol

        require_formal_protocol(definition)
        if (definition.protocol["universe"]["rule"] == "historical_sh_sz_common_stock"
                and expected_universe is None):
            raise ValueError("formal account requires per-signal historical universe qualification")

    backtests_dir = output_root / "backtests"
    backtests_dir.mkdir(parents=True, exist_ok=True)
    run_id = _new_run_id()
    staging = backtests_dir / f".tmp-{run_id}"
    staging.mkdir()

    try:
        input_options = {"qualification_delivery": qualification_delivery} if qualification_delivery is not None else {}
        report = prepare_backtest(definition, store, staging / "bundle", **input_options)
        if progress is not None:
            progress(0.2)
        _raise_if_cancelled(cancelled)

        if not report.can_run:
            sources = json.loads((staging / "bundle" / "sources.json").read_text(encoding="utf-8"))
            manifest: dict[str, Any] = {
                "run_id": run_id,
                "status": "limited",
                "research": research_context,
                "definition": backtest_definition_to_dict(definition),
                "sources": sources,
                "engine": {"decision": None, "note": "资格未通过：未启动引擎"},
                "mode": definition.mode,
                "limitations": list(report.limitations),
                "excluded": {code: list(v) for code, v in report.excluded.items()},
                "tables": {name: f"{name}.parquet" for name in TABLE_COLUMNS},
                "created_at": datetime.now(CN).isoformat(),
            }
            tables = {
                name: pd.DataFrame(columns=list(columns))
                for name, columns in TABLE_COLUMNS.items()
            }
            try:
                evaluation = evaluate_backtest({}, definition.protocol)
            except ValueError as exc:
                # formal limited 也按 blocked/not_evaluated 发布，不因评价输入缺失崩发布
                evaluation = {
                    "execution_status": "complete",
                    "research_status": "blocked",
                    "economic_status": "not_evaluated",
                    "income_target_met": None,
                    "risk_target_met": None,
                    "research_conclusion": "not_evaluated",
                    "next_decision": "resolve_blocker_first",
                    "reasons": [str(exc)],
                    "limitations": [],
                    "metrics": {},
                }
            _write_publish(staging, manifest, tables, evaluation)
            _atomic_publish(staging, backtests_dir / run_id)
            if progress is not None:
                progress(1.0)
            return BacktestRef(run_id=run_id, root=output_root, status="limited")

        validate_prepared_universe(staging / "bundle", expected_universe)

        admission_file = (
            Path(admission_path)
            if admission_path is not None
            else _default_admission_path(output_root)
        )
        admission = _require_admission(admission_file)
        _raise_if_cancelled(cancelled)
        if progress is not None:
            progress(0.4)

        # 引擎门禁输入与信号在暂存 bundle 内落地：admission.json 供 runner
        # 进程内门禁核验；formal 由 build_signals 产正式信号（不注入合成事实），
        # mechanism 注入 parameters.synthetic_signals。
        _write_json(
            staging / "bundle" / "admission.json",
            {"decision": ADMISSION_READY, "admission_ref": str(admission_file)},
        )
        if is_formal or (definition.strategy_id == 'universe_vw'
                         and not definition.parameters.get('synthetic_signals')):
            from hetu_quant.strategies import build_signals

            signals = build_signals(definition, store, **input_options)
            factor_labels = signals.attrs.pop("factor_labels", pd.DataFrame())
            factor_exclusions = signals.attrs.pop("factor_exclusions", [])
            factor_labels.to_parquet(staging / "factor-labels.parquet", index=False)
            signals.to_parquet(staging / "bundle" / "signals.parquet", index=False)
        elif publication is None:
            _inject_mechanism_signals(staging, definition)
        else:
            # The explicit signal version is an input of a mechanism run. Its executable
            # day still needs to be independently confirmed from the account calendar.
            from hetu_quant.backtest_data import split_signal_calendar

            signal_day = publication["signal_date"]
            calendar = store.read_table(SnapshotRef(definition.snapshot_id, store.root), "calendar")
            _, executable = split_signal_calendar(calendar, signal_day)
            if not executable or not str(definition.start) <= executable <= str(definition.end):
                raise ValueError("simulation lacks a proven executable day for the selected close signal")
            pd.DataFrame([
                {**row, "signal_at": signal_day, "executable_from": executable}
                for row in publication["target"]["targets"]
            ]).to_parquet(staging / "bundle" / "signals.parquet", index=False)
        if publication is not None and is_formal:
            from hetu_quant.after_close import _same_signal

            own = signals[signals.signal_at.astype(str).str[:10].eq(publication["signal_date"])]
            if not _same_signal(own, own, publication["target"]["targets"]):
                raise ValueError("formal simulation input differs from the selected close signal")
        engine_out = staging / "engine-out"
        resolved_engine_python = (
            Path(engine_python)
            if engine_python is not None
            else output_root / "runtime" / "rqalpha" / ".venv" / "bin" / "python"
        )
        if before_engine is not None:
            if not resolved_engine_python.exists():
                raise FileNotFoundError(f"engine python missing: {resolved_engine_python}")
            if benchmark is None and definition.strategy_id != definition.protocol["benchmark_id"]:
                raise ValueError("representative candidate requires an account benchmark")
            if benchmark is not None:
                validate_benchmark_reference(definition, benchmark, staging / "bundle")
            _raise_if_cancelled(cancelled)
            before_engine()
        run_engine_subprocess(
            resolved_engine_python, RUNNER_PATH, staging / "bundle", engine_out,
            cancelled=cancelled, **({"on_engine_start": on_engine_start} if on_engine_start is not None else {}),
        )
        if progress is not None:
            progress(0.7)
        _raise_if_cancelled(cancelled)

        engine_manifest = json.loads((engine_out / "backtest.json").read_text())
        account_limitations = list(engine_manifest.get("limitations", []))
        tables = _load_trace_tables(engine_out)
        violations = validate_trace_tables(tables)
        if violations:
            raise TraceIntegrityError(
                "发布前完整性校验失败：\n- " + "\n- ".join(violations)
            )

        # 基准引用（formal）：先执行/读取已获批且适用的基准轨迹，把基准净值按
        # 日期填入 nav.benchmark_nav；基准自身以本账户净值为参照并标记 benchmark。
        # 可比性按实际定义/用途判断，不因 run_id/日期不同拒绝可读旧结果。
        benchmark_id = str(definition.protocol.get("benchmark_id") or "")
        is_benchmark_run = bool(benchmark_id) and definition.strategy_id == benchmark_id
        resolved_inputs: dict[str, Any] = {"benchmark": None}
        blocked_reason: str | None = None
        if is_formal:
            try:
                resolved_inputs = _attach_formal_references(
                    definition, tables, staging / "bundle", run_id, benchmark, market_benchmark,
                )
            except ValueError as exc:
                blocked_reason = str(exc)

        try:
            if blocked_reason:
                raise ValueError(blocked_reason)
            if account_limitations:
                raise ValueError("account_semantics_limited: " + "; ".join(account_limitations))
            tables["run"] = pd.DataFrame([{
                "status": "complete", "detail": "", "account_start": definition.start,
                "account_end": definition.end,
            }])
            if is_formal:
                tables["calendar"] = pd.read_parquet(staging / "bundle" / "tables" / "calendar.parquet")
                tables["exposure_facts"] = research_exposure_inputs(
                    store, SnapshotRef(definition.snapshot_id, store.root),
                    STRATEGY_PURPOSES[definition.strategy_id], tables, staging / "bundle" / "tables",
                )
            evaluation = evaluate_backtest(tables, definition.protocol)
        except ValueError as exc:
            # 错口径/未成熟标签等输入违例：评价 blocked，不静默放行也不吞错
            blocked_reason = blocked_reason or str(exc)
            evaluation = {
                "execution_status": "complete",
                "research_status": "blocked",
                "economic_status": "not_evaluated",
                "income_target_met": None,
                "risk_target_met": None,
                "research_conclusion": "not_evaluated",
                "next_decision": "resolve_blocker_first",
                "reasons": [blocked_reason],
                "limitations": [],
                "metrics": {},
            }
        if is_formal:
            from hetu_quant.backtest_evaluation import account_attribution

            evaluation["attribution"] = account_attribution(
                tables, signals, definition.protocol,
                cash_action_flows=engine_manifest.get("cash_action_flows", ()),
                identity_events=engine_manifest.get("identity_transition_events", ()),
            )
        if is_formal and definition.strategy_id == "low_volatility":
            from hetu_quant.backtest_evaluation import factor_diagnostics

            diagnostic = factor_diagnostics(factor_labels)
            for horizon in ("1", "3", "6"):
                diagnostic["horizons"].setdefault(horizon, dict(coverage=0, ic=None, rank_ic=None, deciles=[]))
            diagnostic["excluded"] = factor_exclusions
            evaluation["factor_diagnostics"] = diagnostic
        limited = bool(account_limitations or (before_engine is not None and blocked_reason))
        manifest = {
            "run_id": run_id,
            "status": "limited" if limited else "complete",
            "research": research_context,
            "kind": "benchmark" if is_benchmark_run else "candidate",
            "resolved_inputs": resolved_inputs,
            "definition": backtest_definition_to_dict(definition),
            "sources": json.loads(
                (staging / "bundle" / "sources.json").read_text(encoding="utf-8")
            ),
            "engine": {
                "candidate": admission.get("candidate"),
                "version": admission.get("version"),
                "admission_ref": str(admission_file),
                "decision": ADMISSION_READY,
            },
            "mode": definition.mode,
            "limitations": account_limitations or ([blocked_reason] if limited else []),
            "disposition_gaps": engine_manifest.get("disposition_gaps", []),
            "dividend_tax_withholdings": engine_manifest.get("dividend_tax_withholdings", []),
            "tables": {name: f"{name}.parquet" for name in TABLE_COLUMNS},
            "created_at": datetime.now(CN).isoformat(),
        }
        _write_publish(staging, manifest, tables, evaluation)
        _raise_if_cancelled(cancelled)
        _atomic_publish(staging, backtests_dir / run_id)
        if origin is not None:
            _link_published_after_close_simulation(output_root, run_id, origin)
        if progress is not None:
            progress(1.0)
        return BacktestRef(run_id=run_id, root=output_root, status=manifest["status"])
    except BacktestCancelled:
        partial = backtests_dir / "_partial" / run_id
        partial.parent.mkdir(parents=True, exist_ok=True)
        if staging.exists():
            staging.rename(partial)
        raise
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
