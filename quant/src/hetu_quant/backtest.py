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
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from hetu_quant.backtest_data import prepare_backtest
from hetu_quant.backtest_evaluation import evaluate_backtest
from hetu_quant.backtest_models import (
    BacktestDefinition,
    BacktestRef,
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
            warmup = (pd.Timestamp(current["start"]) - pd.DateOffset(months=int(protocol["warmup_months"]))).date()
            for name in ("calendar", "daily_bars", "security_status", "corporate_actions",
                         "market_cap", "trading_rules", "terminal_disposals"):
                a, b = Path(candidate_bundle) / "tables" / f"{name}.parquet", root / "tables" / f"{name}.parquet"
                if name == "terminal_disposals" and not a.exists() and not b.exists():
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
                    frame = frame.drop(
                        columns=["source_ref", "product_id", "evidence_ref", "rule_id", "sha256"], errors="ignore")
                    frames.append(frame.astype("string").fillna("").sort_index(axis=1))
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
    research_context: Mapping[str, object] | None = None,
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
    is_formal = str(definition.protocol["stage"]) == "formal"
    if is_formal:
        from hetu_quant.strategies import require_formal_protocol

        require_formal_protocol(definition)

    backtests_dir = output_root / "backtests"
    backtests_dir.mkdir(parents=True, exist_ok=True)
    run_id = _new_run_id()
    staging = backtests_dir / f".tmp-{run_id}"
    staging.mkdir()

    try:
        report = prepare_backtest(definition, store, staging / "bundle")
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

            signals = build_signals(definition, store)
            factor_labels = signals.attrs.pop("factor_labels", pd.DataFrame())
            factor_exclusions = signals.attrs.pop("factor_exclusions", [])
            factor_labels.to_parquet(staging / "factor-labels.parquet", index=False)
            signals.to_parquet(staging / "bundle" / "signals.parquet", index=False)
        else:
            _inject_mechanism_signals(staging, definition)

        engine_out = staging / "engine-out"
        resolved_engine_python = (
            Path(engine_python)
            if engine_python is not None
            else output_root / "runtime" / "rqalpha" / ".venv" / "bin" / "python"
        )
        run_engine_subprocess(
            resolved_engine_python, RUNNER_PATH, staging / "bundle", engine_out,
            cancelled=cancelled,
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
        benchmark_provenance: dict[str, Any] | None = None
        blocked_reason: str | None = None
        if is_formal:
            try:
                if benchmark is not None:
                    bench = read_backtest(benchmark)
                    validate_benchmark_reference(definition, benchmark, staging / "bundle")
                    bench_nav = bench["tables"]["nav"][["date", "net_nav"]].copy()
                    bench_nav["date"] = bench_nav["date"].astype(str)
                    nav = tables["nav"].copy()
                    nav["date"] = nav["date"].astype(str)
                    # 引擎输出的 benchmark_nav 占位列（NaN）先丢弃再按日期填入基准净值
                    nav = nav.drop(columns=["benchmark_nav"]).merge(
                        bench_nav.rename(columns={"net_nav": "benchmark_nav"}),
                        on="date",
                        how="left",
                    )[["date", "gross_nav", "net_nav", "benchmark_nav"]]
                    tables["nav"] = nav
                    benchmark_provenance = {
                        "benchmark_id": benchmark_id,
                        "kind": "account",
                        "run_id": benchmark.run_id,
                        "resolved_from": str(Path(benchmark.root) / "backtests" / benchmark.run_id),
                    }
                elif is_benchmark_run:
                    nav = tables["nav"]
                    nav["benchmark_nav"] = nav["net_nav"]
                    benchmark_provenance = {
                        "benchmark_id": benchmark_id,
                        "kind": "account",
                        "run_id": run_id,
                        "note": "基准自身以本账户净值为参照（超额恒为零，不作经济通过）",
                    }
            except ValueError as exc:
                blocked_reason = str(exc)
            if benchmark_provenance is not None:
                tables["benchmark"] = pd.DataFrame([benchmark_provenance])

        try:
            if blocked_reason:
                raise ValueError(blocked_reason)
            if account_limitations:
                raise ValueError("account_semantics_limited: " + "; ".join(account_limitations))
            tables["run"] = pd.DataFrame([{
                "status": "complete", "detail": "", "account_start": definition.start,
                "account_end": definition.end,
            }])
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
        if is_formal and definition.strategy_id == "low_volatility":
            from hetu_quant.backtest_evaluation import factor_diagnostics

            diagnostic = factor_diagnostics(factor_labels)
            for horizon in ("1", "3", "6"):
                diagnostic["horizons"].setdefault(horizon, dict(coverage=0, ic=None, rank_ic=None, deciles=[]))
            diagnostic["excluded"] = factor_exclusions
            evaluation["factor_diagnostics"] = diagnostic
        manifest = {
            "run_id": run_id,
            "status": "limited" if account_limitations else "complete",
            "research": research_context,
            "kind": "benchmark" if is_benchmark_run else "candidate",
            "resolved_inputs": {"benchmark": benchmark_provenance},
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
            "limitations": account_limitations,
            "tables": {name: f"{name}.parquet" for name in TABLE_COLUMNS},
            "created_at": datetime.now(CN).isoformat(),
        }
        _write_publish(staging, manifest, tables, evaluation)
        _raise_if_cancelled(cancelled)
        _atomic_publish(staging, backtests_dir / run_id)
        if progress is not None:
            progress(1.0)
        return BacktestRef(run_id=run_id, root=output_root,
                           status="limited" if account_limitations else "complete")
    except BacktestCancelled:
        partial = backtests_dir / "_partial" / run_id
        partial.parent.mkdir(parents=True, exist_ok=True)
        if staging.exists():
            staging.rename(partial)
        raise
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
