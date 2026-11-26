"""二期 03.1 策略登记与共同信号计算。

只登记 `low_volatility`（36 个完整月收益波动率、ddof=1、最低十分位、
市值归一）与 `universe_vw`（同一历史合格池、市值权重的可交易账户基准）；
E/P 在规则与数据合格后以独立定义接入，此处不擅自补权重组合。
宇宙与版本时点一律消费 01 的 `_rebuild_universe`，不从排名 Top N 生成组合。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from hetu_quant.backtest_data import (
    SIGNAL_LOOKBACK_MONTHS,
    SignalInputs,
    _proven_month_ends,
    _rebuild_universe,
    _segment_rows,
    _signal_action_inputs,
    _signal_days,
    _truthy,
    select_signal_market_cap,
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



def _low_volatility_ranks(monthly_returns, lookback_months):
    vols: dict[str, float] = {}
    if len(monthly_returns):
        for code, group in monthly_returns.groupby("code"):
            if len(group) != lookback_months:
                continue
            vols[str(code)] = float(group["month_return"].std(ddof=1))
    return sorted(vols.items(), key=lambda item: (item[1], item[0]))

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
    ordered = _low_volatility_ranks(monthly_returns, lookback_months)
    if len(ordered) < MIN_POOL:
        return pd.DataFrame(columns=columns)
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
    market_cap: pd.DataFrame, code: str, signal_day: str,
    knowledge: pd.DataFrame | None = None, evidence_root: Path = Path("."),
) -> float | None:
    """使用共同资格判定选中的收盘市值。"""
    fact = select_signal_market_cap(
        market_cap, knowledge if knowledge is not None else pd.DataFrame(), code, signal_day, evidence_root
    )
    return None if fact is None else fact["market_cap_cny"]


def _return_window(bars: pd.DataFrame, actions: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    window = bars[bars["date"].astype(str).between(start, end)]
    ex_dates = actions["ex_date"].astype(str)
    events = actions[ex_dates.between(start, end) | ex_dates.eq("") | actions["ex_date"].isna()]
    return build_total_return_series(window, events)


def _factor_labels_for_signal(
    code: str, signal_day: str, factor: float, bars: pd.DataFrame, actions: pd.DataFrame,
    month_ends: dict[str, str], boundary: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    labels, exclusions = [], []
    for horizon in (1, 3, 6):
        coordinate = dict(code=code, signal_at=signal_day, horizon_months=horizon)
        month = str(pd.Period(signal_day, freq="M") + horizon)
        mature = month_ends.get(month)
        if mature is None or mature > boundary:
            exclusions.append({**coordinate, "reason": "immature_or_cross_split"})
            continue
        try:
            forward = _return_window(bars, actions, signal_day, mature)
        except UnsupportedActionError:
            exclusions.append({**coordinate, "reason": "unsupported_label_action"})
            continue
        if forward.empty or str(forward.iloc[-1]["date"]) != mature:
            exclusions.append({**coordinate, "reason": "missing_label_price"})
            continue
        values = forward["close_total_return"]
        labels.append(dict(**coordinate, factor_value=factor, label_return=float(values.iloc[-1] / values.iloc[0] - 1),
                           mature_at=mature, split_end=boundary))
    return labels, exclusions


def build_signals(
    definition: BacktestDefinition, store: DataStore, *, qualification_delivery: Path | None = None,
) -> pd.DataFrame:
    """共同信号只消费截至信号日的输入，未来标签用于单独的研究评价。

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
    universe_rows, _excluded, exclusion_rows = _rebuild_universe(
        definition, store, ref, manifest, calendar, signal_days, warmup_months,
        qualification_delivery=qualification_delivery,
    )
    signal_exclusions = [row for row in exclusion_rows if row["field"] in ("corporate_action_terms", "code_continuity")]
    blocked = {row["date"] for row in signal_exclusions}
    by_signal: dict[str, dict] = {}
    for row in universe_rows:
        by_signal.setdefault(row["signal_date"], {})[row["code"]] = row["segments"]

    bars = store.read_table(ref, "daily_bars")
    actions = store.read_table(ref, "corporate_actions")
    market_cap = store.read_table(ref, "market_cap")
    knowledge = store.read_table(ref, "knowledge_evidence")
    bars_by_code = {str(code): frame for code, frame in bars.groupby("code")}
    actions_by_code = {str(code): frame for code, frame in actions.groupby("code")}
    no_actions = actions.iloc[:0]

    month_ends = {day[:7]: day for day in _proven_month_ends(calendar)}

    rows: list[dict[str, Any]] = []
    labels: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    for signal_day in signal_days:
        segments_by_code = by_signal.get(signal_day, {})
        executable = next((day for day in trading_days if day > signal_day), None)
        if signal_day in blocked or not segments_by_code or executable is None:
            continue
        points = ([signal_day] if warmup_months == 0 else
                  [day for month, day in sorted(month_ends.items()) if day <= signal_day])
        points = points[-(warmup_months + 1):]
        if len(points) < warmup_months + 1:
            continue
        return_rows: list[dict[str, object]] = []
        caps: dict[str, float] = {}
        label_inputs = []
        for code, segments in segments_by_code.items():
            cap = _known_market_cap(market_cap, code, signal_day, knowledge, store.root)
            if cap is not None:
                caps[code] = cap
            own = bars_by_code[code]
            own_actions = actions_by_code.get(code, no_actions)
            history = own
            history_actions = own_actions
            if len(segments) > 1:
                history = _segment_rows(bars_by_code, segments, "date", bars.iloc[:0]).assign(
                    source_code=lambda frame: frame.code, code=code
                )
                history_actions = _segment_rows(actions_by_code, segments, "ex_date", no_actions)
            signal_actions, _ = _signal_action_inputs(history_actions, points[0], signal_day)
            series = _return_window(history, signal_actions.assign(code=code), points[0], signal_day)
            returns = _monthly_returns(series, points)
            if len(returns) != len(points) - 1:
                continue
            if strategy_id == "low_volatility":
                label_inputs.append((code, -float(returns.std(ddof=1)), own, own_actions))
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
            weights = _universe_vw_weights(list(segments_by_code), caps)
        if label_inputs:
            split_day = max(str(definition.start), str((pd.Timestamp(signal_day) + pd.Timedelta(days=1)).date()))
            segment = next((bounds for bounds in definition.protocol["time_split"].values()
                            if str(bounds[0]) <= split_day <= str(bounds[1])), None)
            boundary = min(str(definition.end), str(segment[1])) if segment else str(definition.end)
            for code, factor, own, own_actions in label_inputs:
                known_labels, missing_labels = _factor_labels_for_signal(
                    code, signal_day, factor, own, own_actions, month_ends, boundary,
                )
                labels.extend(known_labels)
                exclusions.extend(missing_labels)
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
    result.attrs["signal_exclusions"] = signal_exclusions
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


def _monthly_returns(series, points):
    dated = series.assign(date=series["date"].astype(str))
    closes = dated.drop_duplicates("date", keep="last").set_index("date")["close_total_return"]
    values = pd.to_numeric(closes.reindex(points), errors="coerce")
    if values.isna().any():
        return pd.Series(dtype="float64")
    return values.pct_change(fill_method=None).dropna()


@dataclass
class SignalTarget:
    signal_date: date
    status: str
    executable_from: date | None
    pool_size: int | None
    candidates: pd.DataFrame
    targets: pd.DataFrame
    gaps: list[dict]


def build_signal_targets(inputs: SignalInputs, signal_date: date) -> SignalTarget:
    """Build an independent close target without depending on execution or maturity."""
    if inputs.signal_date != signal_date:
        raise ValueError("signal input date does not match target date")
    columns = ["identity_id", "code", "signal_value", "rank", "reason", "source_ref"]
    candidates = pd.DataFrame(columns=columns)
    targets = pd.DataFrame(columns=[*columns, "target_weight"])
    gaps = list(inputs.gaps)
    result = SignalTarget(signal_date, "blocked", inputs.executable_from, inputs.pool_size, candidates, targets, gaps)
    if inputs.day_kind != "month_end":
        result.status = "ordinary" if inputs.day_kind == "ordinary" else "calendar_unknown"
        return result
    if inputs.executable_from is None:
        gaps.append(dict(code=None, date=str(signal_date), field="executable_from",
                         reason="最早适用日待定，后继交易日日历未证", required=[str(signal_date)], source_refs=[],
                         blocking=False))
    if len(inputs.month_ends) != SIGNAL_LOOKBACK_MONTHS + 1 or inputs.closes.empty:
        return result
    excluded = {fact.get("target_code", fact.get("code")) for fact in gaps if fact.get("code")
                and fact.get("field") != "executable_from"}
    active = {r.code: r for r in inputs.identities.itertuples() if r.active}
    return_rows = []
    for code, own in inputs.closes.groupby("target_code"):
        if code not in active or code in excluded:
            continue
        events = inputs.actions[inputs.actions.target_code.eq(code)].assign(code=code)
        series = _return_window(own.assign(code=code), events, inputs.month_ends[0], str(signal_date))
        returns = _monthly_returns(series, inputs.month_ends)
        return_rows.extend(dict(code=code, month_end=day, month_return=value) for day, value in returns.items())
    ranked = _low_volatility_ranks(pd.DataFrame(return_rows), SIGNAL_LOOKBACK_MONTHS)
    number_selected = max(1, len(ranked) // 10)
    rows = [
        dict(identity_id=active[code].identity_id, code=code, signal_value=vol, rank=index,
             reason="36月总收益样本波动最低十分位" if index <= number_selected else "波动排名未进入最低十分位",
             source_ref=active[code].source_ref)
        for index, (code, vol) in enumerate(ranked, start=1)
    ]
    rows.extend(dict(identity_id=active[code].identity_id if code in active else "", code=code,
                     signal_value=None, rank=None, reason=fact["reason"],
                     source_ref=", ".join(fact.get("source_refs", [])))
                for code in sorted(excluded) for fact in gaps if fact.get("target_code", fact.get("code")) == code)
    result.candidates = pd.DataFrame(rows, columns=columns)
    window = [inputs.month_ends[0], str(signal_date)]
    if len(ranked) < MIN_POOL:
        gaps.append(dict(code=None, date=str(signal_date), field="eligible_pool", reason="合格池不足 10 只",
                         required=window, source_refs=[], blocking=True))
    if inputs.pool_size is None or any(row.get("blocking", True) for row in gaps):
        return result
    selected = result.candidates.iloc[:number_selected].copy()
    caps = {r.code: r.market_cap_cny for r in inputs.market_cap.itertuples()}
    for code in selected.code:
        if code not in caps:
            gaps.append(dict(code=code, date=str(signal_date), field="market_cap_cny",
                             reason="入选证券在收盘时点的市值未证，无法计算完整权重", required=window,
                             source_refs=[active[code].source_ref], blocking=True))
    if any(fact.get("blocking", True) for fact in gaps):
        return result
    weights = selected.code.map(caps)
    selected["target_weight"] = weights / weights.sum()
    result.targets = selected
    result.status = "ready"
    return result
