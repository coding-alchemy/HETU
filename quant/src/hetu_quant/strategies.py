"""二期 03.1 策略登记与共同信号计算。

只登记 `low_volatility`（36 个完整月收益波动率、ddof=1、最低十分位、
市值归一）与 `universe_vw`（同一历史合格池、市值权重的可交易账户基准）；
E/P 在规则与数据合格后以独立定义接入，此处不擅自补权重组合。
宇宙与版本时点一律消费 01 的 `_rebuild_universe`，不从排名 Top N 生成组合。
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from hetu_quant.backtest_data import (
    _proven_month_ends,
    _rebuild_universe,
    _signal_cutoff,
    _signal_days,
    _truthy,
)
from hetu_quant.backtest_models import BacktestDefinition
from hetu_quant.data import DataStore, UnsupportedActionError, build_total_return_series
from hetu_quant.models import SnapshotRef

# 已登记策略 → 信号构建方式；未登记策略（含未接入的 E/P）拒绝
SIGNAL_STRATEGIES = ("low_volatility", "universe_vw")

MIN_POOL = 10
SIGNAL_COLUMNS = [
    "code",
    "signal_at",
    "executable_from",
    "signal_value",
    "rank",
    "target_weight",
    "source_ref",
]

# formal 阶段协议必须冻结的取值（键存在性由 BacktestDefinition 保证，
# 此处核验取值非空且成型）
_FORMAL_COST_FIELDS = ("commission_rate", "min_commission_cny", "slippage_rate")


def low_volatility_weights(
    monthly_returns: pd.DataFrame, market_cap: pd.DataFrame, *, lookback_months: int = 36
) -> pd.DataFrame:
    """36 个月月收益波动率最低十分位 + 市值归一权重。

    输入须已按时点过滤：monthly_returns 列为 code, month_end, month_return；
    market_cap 列为 code, market_cap_cny（当时可知值）。窗口不足
    lookback_months 个收益的对象不入选；合格池不足 10 或入选者市值未知
    时不产信号。同波动按代码稳定截取。
    """
    columns = ["code", "signal_value", "target_weight"]
    vols: dict[str, float] = {}
    if len(monthly_returns):
        for code, group in monthly_returns.groupby("code"):
            if len(group) != lookback_months:
                continue
            vols[str(code)] = float(group["month_return"].std(ddof=1))
    if len(vols) < MIN_POOL:
        return pd.DataFrame(columns=columns)
    ordered = sorted(vols.items(), key=lambda item: (item[1], item[0]))
    count = max(1, len(ordered) // 10)
    caps = (
        {str(row.code): float(row.market_cap_cny) for row in market_cap.itertuples()}
        if len(market_cap)
        else {}
    )
    selected = [(code, vol) for code, vol in ordered[:count] if caps.get(code, 0.0) > 0]
    total = sum(caps[code] for code, _ in selected)
    if not selected or total <= 0:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(
        {
            "code": [code for code, _ in selected],
            "signal_value": [vol for _, vol in selected],
            "target_weight": [caps[code] / total for code, _ in selected],
        }
    )


def _universe_vw_weights(codes: list[str], caps: dict[str, float]) -> pd.DataFrame:
    """可交易账户基准：历史合格池全员的市值归一权重；信号原值为市值。"""
    columns = ["code", "signal_value", "target_weight"]
    chosen = [(code, caps[code]) for code in sorted(codes) if caps.get(code, 0.0) > 0]
    total = sum(cap for _, cap in chosen)
    if not chosen or total <= 0:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(
        {
            "code": [code for code, _ in chosen],
            "signal_value": [cap for _, cap in chosen],
            "target_weight": [cap / total for _, cap in chosen],
        }
    )


def _known_market_cap(
    market_cap: pd.DataFrame, code: str, signal_day: str
) -> float | None:
    """信号日原价形成、且信号时点（当日 21:00 CN）前已公开的市值。

    不得按研究 as_of 筛选：信号时点后披露的版本（即使研究时点已可得）
    属于未来数据，使用即泄露。
    """
    if not len(market_cap):
        return None
    cutoff = _signal_cutoff(signal_day)
    own = market_cap[
        (market_cap["code"].astype(str) == code)
        & (market_cap["date"].astype(str) == signal_day)
        & (market_cap["available_at"].astype(str) <= cutoff)
    ]
    if not len(own):
        return None
    latest = own.sort_values("date").iloc[-1]
    value = pd.to_numeric(pd.Series([latest["market_cap_cny"]]), errors="coerce").iloc[0]
    return None if pd.isna(value) else float(value)


def build_signals(definition: BacktestDefinition, store: DataStore) -> pd.DataFrame:
    """按定义逐信号日计算共同信号：宇宙来自 01 历史资格，不读未来数据。

    输出列为 code, signal_at, executable_from, signal_value, rank,
    target_weight, source_ref；executable_from 是信号日后首个交易日
    （收盘信号最早次日执行），缺失交易日后继的信号日不产信号。
    """
    strategy_id = definition.strategy_id
    if strategy_id not in SIGNAL_STRATEGIES:
        raise ValueError(
            f"unknown strategy_id for signals: {strategy_id!r} "
            f"(registered: {', '.join(SIGNAL_STRATEGIES)}; E/P 规则与数据合格后另行接入)"
        )
    warmup_months = int(definition.protocol["warmup_months"])
    ref = SnapshotRef(snapshot_id=definition.snapshot_id, root=store.root)
    manifest = store.load_manifest(ref)
    calendar = store.read_table(ref, "calendar")
    trading_days = sorted(
        str(day)
        for day, flag in zip(calendar["date"], calendar["is_trading_day"], strict=False)
        if _truthy(flag)
    )
    frequency = str(definition.protocol['rebalance']['frequency'])
    if strategy_id == 'low_volatility' and frequency != 'monthly':
        raise ValueError('registered low_volatility requires monthly rebalance; weekly method is not registered')
    signal_days = _signal_days(calendar, definition.start, definition.end, frequency)
    universe_rows, _excluded, _exclusion_rows = _rebuild_universe(
        definition, store, ref, manifest, calendar, signal_days, warmup_months
    )
    by_signal: dict[str, list[str]] = {}
    for row in universe_rows:
        by_signal.setdefault(row["signal_date"], []).append(row["code"])

    bars = store.read_table(ref, "daily_bars")
    actions = store.read_table(ref, "corporate_actions")
    market_cap = store.read_table(ref, "market_cap")

    month_ends = {day[:7]: day for day in _proven_month_ends(calendar)}

    rows: list[dict[str, Any]] = []
    labels: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    for signal_day in signal_days:
        codes = by_signal.get(signal_day, [])
        executable = next((day for day in trading_days if day > signal_day), None)
        if not codes or executable is None:
            continue
        points = ([signal_day] if warmup_months == 0 else
                  [day for month, day in sorted(month_ends.items()) if day <= signal_day])
        points = points[-(warmup_months + 1):]
        if len(points) < warmup_months + 1:
            continue
        return_rows: list[dict[str, object]] = []
        caps: dict[str, float] = {}
        for code in codes:
            cap = _known_market_cap(market_cap, code, signal_day)
            if cap is not None:
                caps[code] = cap
            own = bars[(bars["code"].astype(str) == code) & (bars["date"].astype(str) <= str(definition.end))]
            try:
                series = build_total_return_series(own, actions)
            except UnsupportedActionError:
                continue  # 未核验行动类型的对象不产信号（资格侧已记录）
            values: list[float] = []
            for day in points:
                row = series[series["date"].astype(str) == day]
                if not len(row):
                    values = []
                    break
                value = pd.to_numeric(row["close_total_return"], errors="coerce").iloc[-1]
                if pd.isna(value):
                    values = []
                    break
                values.append(float(value))
            if len(values) != len(points):
                continue
            returns = pd.Series(values).pct_change().dropna()
            if strategy_id == "low_volatility":
                factor = -float(returns.std(ddof=1))
                split_day = max(str(definition.start), str((pd.Timestamp(signal_day) + pd.Timedelta(days=1)).date()))
                segment = next((bounds for bounds in definition.protocol["time_split"].values()
                                if str(bounds[0]) <= split_day <= str(bounds[1])), None)
                boundary = min(str(definition.end), str(segment[1])) if segment else str(definition.end)
                for horizon in (1, 3, 6):
                    month = str(pd.Period(signal_day, freq="M") + horizon)
                    mature = month_ends.get(month)
                    if mature is None or mature > boundary:
                        exclusions.append(dict(code=code, signal_at=signal_day, horizon_months=horizon,
                                               reason="immature_or_cross_split"))
                        continue
                    endpoint = series[series["date"].astype(str) == mature]
                    if endpoint.empty:
                        exclusions.append(dict(code=code, signal_at=signal_day, horizon_months=horizon,
                                               reason="missing_label_price"))
                        continue
                    end_value = float(endpoint.iloc[-1]["close_total_return"])
                    labels.append(dict(code=code, signal_at=signal_day, horizon_months=horizon,
                        factor_value=factor, label_return=end_value / values[-1] - 1,
                        mature_at=mature, split_end=boundary))
            for month_end, month_return in zip(points[1:], returns, strict=False):
                return_rows.append(
                    {"code": code, "month_end": month_end, "month_return": month_return}
                )
        return_frame = pd.DataFrame(return_rows, columns=["code", "month_end", "month_return"])
        cap_frame = pd.DataFrame(
            {"code": list(caps), "market_cap_cny": list(caps.values())}
        )
        if strategy_id == "low_volatility":
            weights = low_volatility_weights(
                return_frame, cap_frame, lookback_months=warmup_months
            )
        else:
            weights = _universe_vw_weights(codes, caps)
        for rank, row in enumerate(weights.itertuples(), start=1):
            rows.append(
                {
                    "code": row.code,
                    "signal_at": signal_day,
                    "executable_from": executable,
                    "signal_value": float(row.signal_value),
                    "rank": rank,
                    "target_weight": float(row.target_weight),
                    "source_ref": ref.snapshot_id,
                }
            )
    result = pd.DataFrame(rows, columns=SIGNAL_COLUMNS)
    result.attrs["factor_labels"] = pd.DataFrame(labels, columns=[
        "code", "signal_at", "horizon_months", "factor_value", "label_return", "mature_at", "split_end"])
    result.attrs["factor_exclusions"] = exclusions
    return result


def require_formal_protocol(definition: BacktestDefinition) -> None:
    """formal 阶段协议取值必须完整冻结；缺区间/成本/阈值/依据/预算/查看记录即拒绝。"""
    protocol = definition.protocol
    if protocol["stage"] != "formal":
        return
    problems: list[str] = []
    execution = protocol['execution']
    if execution.get('signal_time') != 'close' or execution.get('fill_time') != 'next_open':
        problems.append('execution requires close signal and next_open fill')

    time_split = protocol["time_split"]
    final = (protocol.get("evaluation_window") if protocol.get("research_phase") == "development_validation"
             else time_split.get("final") if hasattr(time_split, "get") else None)
    valid_final = False
    if isinstance(final, (list, tuple)) and len(final) == 2:
        try:
            from datetime import date as _date

            left, right = (_date.fromisoformat(str(final[0])), _date.fromisoformat(str(final[1])))
            valid_final = left <= right
        except ValueError:
            valid_final = False
    if not valid_final:
        problems.append("time_split.final（最终区间）")

    costs = protocol["costs"]
    cost_ok = hasattr(costs, "get") and all(
        isinstance(costs.get(field), (int, float)) and costs.get(field) >= 0
        for field in _FORMAL_COST_FIELDS
    ) and bool(costs.get("statutory_rules_ref"))
    if not cost_ok:
        problems.append("costs（成本取值）")

    objectives = protocol["objectives"]
    if not (hasattr(objectives, "items") and list(objectives.items())) or any(
        not str(value) for _key, value in objectives.items()
    ):
        problems.append("objectives（经济目标阈值）")

    if not str(protocol["rules_ref"]):
        problems.append("rules_ref（历史规则依据）")

    trial_budget = protocol["trial_budget"]
    max_runs = trial_budget.get("max_runs") if hasattr(trial_budget, "get") else None
    if not isinstance(max_runs, int) or max_runs <= 0:
        problems.append("trial_budget.max_runs（试验预算）")

    viewing = protocol["viewing_record"]
    if not (hasattr(viewing, "get") and isinstance(viewing.get("final_seen"), bool)):
        problems.append("viewing_record.final_seen（已查看记录）")

    if not str(protocol["benchmark_id"]):
        problems.append("benchmark_id（可比基准）")

    if problems:
        raise ValueError(f"formal protocol incomplete: {', '.join(problems)}")


def strategy_method_label(definition: BacktestDefinition) -> str:
    """原方法标签仅在低波动保留全部原规则时成立；任何改写（含周频）即失去。"""
    if definition.strategy_id != "low_volatility":
        return "not_applicable"
    protocol = definition.protocol
    rebalance = protocol["rebalance"]
    frequency = rebalance.get("frequency") if hasattr(rebalance, "get") else None
    if (
        int(protocol["warmup_months"]) == 36
        and int(definition.parameters.get("lookback_months", 36)) == 36
        and frequency == "monthly"
    ):
        return "original_method"
    return "adapted_method"
