"""二期 02/03 回测评价：同一轨迹的毛净／成本分解与经济判定。

`evaluate_backtest(tables, protocol)` 消费冻结协议与固定轨迹表，输出分立状态：
execution_status（引擎执行）、research_status（研究评价）、economic_status
（经济目标）互不替代；机制阶段与未正式评价一律显式 not_evaluated，有效负结果
仍是 evaluated/fail，不允许靠风险改善宣称盈利。

收益锚点：评价区间的收益、波动与回撤一律从"区间交易前资产"起算——有交易日历时
使用区间前应有的最近交易日净值；协议 initial_cash_cny 只在评价区间即已证明的账户
起始段（时间切片最早起点）时为合法锚点，后续时间切片缺边界资产不得回退
全局初资。两者都缺时报 insufficient_evidence，不得以窗口首条净值冒充锚点
（首日费用等损失不得被吞掉）。

输入表约定（固定轨迹表之外的两条评价输入事实）：

- ``run``：单行 ``status, detail``；status 非 complete 视为执行失败。缺省视为 complete。
- ``benchmark``：单行 ``benchmark_id, kind``；formal 评价要求与协议
  ``benchmark_id`` 一致且 kind 为 ``account``（同池同成本可交易账户基准），
  价格指数等错口径直接拒绝。缺省视为缺失并拒绝 formal。
- ``labels``（可选）：``code, signal_at, horizon_months, mature_at``；
  任一标签 mature_at 晚于评价区间末即未成熟，拒绝评价，不以未成熟标签出结论。
  因子诊断另需 ``label_return`` 与 ``factor_value``（定向因子 -sigma）。

指标与统计（机制层）：trade_metrics 换手／费用分项／成交率（容量使用需 bar
量与参与率参数，固定轨迹表不可计算，逐项说明 not_available）；
subperiod_metrics 按协议端点切分子区间并沿用锚点语义；monthly_aligned_returns
把净值按月末对齐成月度收益；block_bootstrap_intervals 按冻结合同（块长 3 月、
1,000 次、seed 20261017）给净超额与风险改善的 95% 区间，点估计目标、统计支持
与结论分开，区间跨零不得宣称统计支持；factor_diagnostics 低波动定向诊断
（-sigma 因子 × 未来 1/3/6 完整月总收益标签，分 horizon 覆盖、IC/RankIC、
十分组、衰减）；industry_exposure 缺历史分类时标 unknown，不用当前标签回填。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Iterator, Mapping, Set
from datetime import datetime
from typing import Any, NamedTuple

import numpy as np
import pandas as pd
from hetu_data.utils import _truthy

from hetu_quant.backtest_data import _calendar_flags

ECONOMIC_STATUSES = ("not_evaluated", "met", "not_met", "insufficient_evidence")
RESEARCH_CONCLUSIONS = ("not_evaluated", "pass", "fail", "insufficient_evidence")

_TRADING_DAYS_PER_YEAR = 252
_DAYS_PER_YEAR = 365.25
_RISK_VOL_CAP = 0.9  # 年化波动 ≤ 基准的 90%
_MONTHS_PER_YEAR = 12

# 冻结合同（phase2-protocol.json robustness.bootstrap）
BOOTSTRAP_BLOCK_MONTHS = 3
BOOTSTRAP_REPS = 1000
BOOTSTRAP_SEED = 20261017


def _position_deviations(
    targets: pd.DataFrame, positions: pd.DataFrame | None, nav: pd.DataFrame, orders: pd.DataFrame,
) -> Iterator[tuple[str, dict[str, Any]]]:
    if targets.empty:
        return
    net_by_day = dict(zip(nav["date"].astype(str), nav["net_nav"], strict=True)) if not nav.empty else {}
    holdings = {}
    if positions is not None and not positions.empty:
        grouped = positions.groupby([positions["date"].astype(str), positions["code"].astype(str)])
        holdings = grouped[["qty", "market_value"]].sum().to_dict("index")
    order_records = {}
    if not orders.empty and "signal_at" in orders:
        fields = orders.columns.intersection(
            ["order_id", "submitted_at", "side", "requested_qty", "filled_qty", "status", "reason"]
        )
        for (signal, code), rows in orders.groupby([orders["signal_at"].astype(str), orders["code"].astype(str)]):
            order_records[signal, code] = rows[fields].to_dict("records")
    for row in targets.itertuples(index=False):
        signal, day, code = str(row.signal_at), str(row.executable_from), str(row.code)
        net_nav = net_by_day.get(day)
        holding = holdings.get((day, code), {"qty": 0, "market_value": 0})
        observed = net_nav is not None and positions is not None
        qty = int(holding["qty"]) if observed else None
        value = float(holding["market_value"]) if observed else None
        weight = value / float(net_nav) if value is not None and net_nav > 0 else None
        target = float(row.target_weight)
        yield code, {
            "signal_at": signal, "executable_from": day, "target_weight": target,
            "actual_qty": qty, "actual_market_value_cny": value, "actual_weight": weight,
            "weight_deviation": None if weight is None else weight - target,
            "orders": order_records.get((signal, code), []),
        }


def account_attribution(
    tables: Mapping[str, pd.DataFrame], source_signals: pd.DataFrame, protocol: Mapping[str, Any],
    *, cash_action_flows: Iterable[Mapping[str, Any]] = (), identity_events: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Explain published fills and observed holdings with the source signal values."""
    orders = tables.get("orders", pd.DataFrame())
    targets = tables.get("targets", pd.DataFrame())
    costs = tables.get("costs", pd.DataFrame())
    positions = tables.get("positions", pd.DataFrame())
    trades = tables.get("trades", pd.DataFrame())
    nav = tables.get("nav", pd.DataFrame())
    if not costs.empty:
        explicit = ~costs["cost_type"].astype(str).str.lower().isin({"slippage", "slippage_cost"})
        # Aggregate tax accrual has no security. Only the trace's per-security liability change is attributable.
        costs = costs[explicit & costs["code"].fillna("").ne("")]
    sources = {}
    for frame in (source_signals, targets, orders, costs, positions, trades):
        if not frame.empty:
            for code in frame["code"].astype(str).unique():
                sources.setdefault(code, [])
    if not source_signals.empty:
        for code, rows in source_signals.groupby("code"):
            sources[str(code)] = rows[["signal_at", "signal_value", "rank", "target_weight"]].to_dict("records")
    action_flows = [dict(flow) for flow in cash_action_flows]
    transfers = []
    if not positions.empty:
        position_days = positions["date"].astype(str)
        for event in identity_events:
            old, day, new = str(event["from_code"]), str(event["date"]), str(event["code"])
            before = positions.loc[positions["code"].astype(str).eq(old) & position_days.lt(day)]
            if before.empty:
                continue
            anchor_day = before["date"].astype(str).max()
            previous = before[before["date"].astype(str).eq(anchor_day)]
            market_value = float(previous["market_value"].sum())
            if not math.isfinite(market_value) or previous["qty"].sum() != event.get("old_quantity", event["quantity"]):
                continue
            transfers.append({**event, "valuation_date": anchor_day, "transferred_market_value_cny": market_value})
            sources.setdefault(old, [])
            sources.setdefault(new, [])
    for flow in action_flows:
        sources.setdefault(str(flow["code"]), [])
    by_security = {
        code: dict(signals=rows, signal_values=[row["signal_value"] for row in rows],
                   unfilled_qty=0, unfilled_reasons=[], explicit_fees_cny=0.0,
                   unfilled_pnl_cny=None, observed_pnl_cny={}, cash_action_flows=[], position_deviations=[])
        for code, rows in sources.items()
    }
    for flow in action_flows:
        by_security[str(flow["code"])]["cash_action_flows"].append(flow)
    for code, deviation in _position_deviations(targets, tables.get("positions"), nav, orders):
        by_security[code]["position_deviations"].append(deviation)
    for row in orders.itertuples():
        unfilled_qty = max(0, int(row.requested_qty) - int(row.filled_qty))
        item = by_security[str(row.code)]
        item["unfilled_qty"] += unfilled_qty
        if unfilled_qty and row.reason:
            item["unfilled_reasons"].append(str(row.reason))
    if not costs.empty:
        for code, amount in costs.groupby("code")["amount"].sum().items():
            by_security[str(code)]["explicit_fees_cny"] = float(amount)
    by_period = {}
    if not nav.empty:
        nav = nav.sort_values("date")
        for label, (start, end) in protocol.get("time_split", {}).items():
            days = nav["date"].astype(str)
            previous = nav[days.lt(start)]
            current = nav[days.between(start, end)]
            if current.empty:
                continue
            previous_day = None if previous.empty else str(previous.iloc[-1]["date"])
            last_day = str(current.iloc[-1]["date"])
            start_value = protocol.get("initial_cash_cny") if previous.empty else previous.iloc[-1]["net_nav"]
            if start_value is None:
                continue
            observed = {}

            def add_amounts(observed, amounts, sign):
                for code, value in amounts.items():
                    observed[str(code)] = observed.get(str(code), 0.0) + sign * float(value)

            if not positions.empty:
                for day, sign in ((previous_day, -1), (last_day, 1)):
                    at = positions[positions["date"].astype(str).eq(day)]
                    add_amounts(observed, at.groupby("code")["market_value"].sum(), sign)
            if not trades.empty:
                at = trades[trades["executed_at"].astype(str).str[:10].between(start, last_day)]
                value = at["qty"] * at["execution_price"] * np.where(at["side"].eq("SELL"), 1, -1)
                add_amounts(observed, value.groupby(at["code"]).sum(), 1)
            if not costs.empty and "date" in costs:
                at = costs[costs["date"].astype(str).str[:10].between(start, last_day)]
                add_amounts(observed, at.groupby("code")["amount"].sum(), -1)
            cash_action_pnl = 0.0
            for flow in action_flows:
                if start <= str(flow["date"]) <= last_day:
                    add_amounts(observed, {flow["code"]: flow["amount_cny"]}, 1)
                    cash_action_pnl += float(flow["amount_cny"])
            for event in transfers:
                if start <= str(event["date"]) <= last_day:
                    amount = event["transferred_market_value_cny"]
                    add_amounts(observed, {event["from_code"]: amount, event["code"]: -amount}, 1)
            delta = float(current.iloc[-1]["net_nav"] - start_value)
            total = sum(observed.values())
            by_period[label] = dict(start=start, end=last_day, net_nav_change_cny=delta,
                                    cash_action_pnl_cny=cash_action_pnl,
                                    observed_pnl_cny=total, unallocated_cny=delta - total)
            for code, value in observed.items():
                by_security[code]["observed_pnl_cny"][label] = value
    return dict(by_security=by_security, by_period=by_period, identity_transfers=transfers, limitations=[
        "未成交指令没有反事实成交价",
        "持仓变化、成交现金流、费用及逐股入账行动之外的差额，包括未逐股核验的行动与退出，保留为未归属",
        "持仓偏差按对应可成交日收盘持仓市值除以账户净值求实际权重，缺当日账户证据时不计算",
        "历史行业归因按已证范围展示",
        "有据换码以换码前最后一条已匹配数量的持仓市值中性转移，不视为买卖现金流",
    ])


def trade_metrics(tables: Mapping[str, pd.DataFrame]) -> dict[str, Any]:
    """换手／费用分项／成交率：固定轨迹表可计算口径。

    单边换手 =（买成交额 + 卖成交额）/ 2 / 区间平均净值；费用分项取 costs
    表逐笔合计（与 trades 费用列同一引擎数据源）；成交率按数量
    filled/requested（无申报时为 None，不用 0 冒充）；容量使用需 bar 量与
    参与率参数，固定轨迹表不可计算，逐项说明 not_available。
    """
    trades = tables.get("trades")
    buy_value = sell_value = 0.0
    if trades is not None and len(trades):
        qty = pd.to_numeric(trades["qty"], errors="coerce").fillna(0.0)
        price = pd.to_numeric(trades["price_raw"], errors="coerce").fillna(0.0)
        value = qty * price
        side = trades["side"].astype(str)
        buy_value = float(value[side == "BUY"].sum())
        sell_value = float(value[side == "SELL"].sum())
    traded = buy_value + sell_value

    avg_nav: float | None = None
    nav = tables.get("nav")
    if nav is not None and len(nav):
        values = pd.to_numeric(nav["net_nav"], errors="coerce").dropna()
        if len(values):
            avg_nav = float(values.mean())

    breakdown: dict[str, float] = {}
    costs = tables.get("costs")
    if costs is not None and len(costs):
        amounts = pd.to_numeric(costs["amount"], errors="coerce").fillna(0.0)
        for cost_type, group_amounts in amounts.groupby(costs["cost_type"].astype(str)):
            breakdown[str(cost_type)] = float(group_amounts.sum())
    total_costs = float(sum(breakdown.values()))

    fill_rate: float | None = None
    orders = tables.get("orders")
    if orders is not None and len(orders):
        requested = float(pd.to_numeric(orders["requested_qty"], errors="coerce").fillna(0.0).sum())
        filled = float(pd.to_numeric(orders["filled_qty"], errors="coerce").fillna(0.0).sum())
        if requested > 0:
            fill_rate = filled / requested

    return {
        "traded_value": traded,
        "buy_value": buy_value,
        "sell_value": sell_value,
        "turnover_oneway": ((buy_value + sell_value) / 2 / avg_nav) if avg_nav else None,
        "total_costs": total_costs,
        "cost_breakdown": breakdown,
        "cost_bps": (total_costs / traded * 1e4) if traded > 0 else None,
        "fill_rate_qty": fill_rate,
        "capacity_usage": "not_available",
    }


def subperiod_metrics(
    nav: pd.DataFrame, protocol: Mapping[str, Any], *, calendar: pd.DataFrame | None = None,
    account_start: str | None = None, initial_cash: float | None = None,
) -> dict[str, Any]:
    """按协议 time_split 端点切分子区间（开发／验证／最终样本外）。"""
    split = protocol.get("time_split")
    result: dict[str, Any] = {}
    if not isinstance(split, Mapping):
        return result
    calendar = _resolved_calendar(calendar)
    nav = _exclude_closed_nav(nav, calendar)
    sessions = _sessions(calendar)
    for name, segment in split.items():
        if not isinstance(segment, (list, tuple)) or len(segment) != 2:
            continue
        start, end = str(segment[0]), str(segment[1])
        start = max(start, account_start) if account_start else start
        window = _window_nav(nav, [start, end])
        anchor = _anchor(nav, start, initial_cash, account_start, calendar)
        calendar_gaps = _calendar_nav_gaps(window, calendar, anchor, bounds=(start, end))
        expected = {day for day in sessions if start <= day <= end}
        complete = not calendar_gaps and (calendar is None or expected == set(window["date"].astype(str)))
        own, bench = (None, None) if not len(window) or not complete else (
            _anchored_series(window, anchor, column) for column in ("net_nav", "benchmark_nav")
        )
        result[str(name)] = dict(
            window=[start, end], anchor_date=anchor.day,
            net_return=own[1][-1] / own[1][0] - 1 if own and bench else None,
            benchmark_return=bench[1][-1] / bench[1][0] - 1 if own and bench else None,
        )
        if calendar_gaps:
            result[str(name)]["calendar_nav_gaps"] = calendar_gaps
    return result


def monthly_aligned_returns(
    nav: pd.DataFrame, *, initial_values: tuple[float, float] | None = None
) -> pd.DataFrame:
    """净值按每月最后一条对齐成月度收益序列（首月无前置月不产生收益行）。"""
    frame = nav.copy()
    frame["date"] = frame["date"].astype(str)
    frame = frame.sort_values("date")
    frame["month"] = frame["date"].str[:7]
    monthly = frame.groupby("month", sort=True).tail(1)[["month", "date", "net_nav", "benchmark_nav"]]
    monthly = monthly.reset_index(drop=True)
    adjacent = pd.Series(pd.PeriodIndex(monthly["month"], freq="M").asi8).diff().eq(1)
    for nav_column, return_column in (("net_nav", "net_return"), ("benchmark_nav", "benchmark_return")):
        values = pd.to_numeric(monthly[nav_column], errors="coerce").astype("float64")
        valid = np.isfinite(values) & values.ge(0)
        monthly[return_column] = values.where(valid).pct_change(fill_method=None).where(adjacent & values.shift().gt(0))
    if initial_values is None:
        monthly = monthly.iloc[1:].reset_index(drop=True)
    elif len(monthly):
        monthly.loc[0, "net_return"] = float(monthly.iloc[0]["net_nav"]) / initial_values[0] - 1
        monthly.loc[0, "benchmark_return"] = float(monthly.iloc[0]["benchmark_nav"]) / initial_values[1] - 1
    return monthly[["month", "date", "net_return", "benchmark_return"]]


def _daily_nav_gaps(frame: pd.DataFrame, expected: Set[str] = frozenset()) -> list[dict[str, str]]:
    gaps = []
    for column, side in (("net_nav", "candidate"), ("benchmark_nav", "benchmark")):
        present = frame[f"_{side}_row"].eq(True)
        days = frame["date"].astype(str)
        values = pd.to_numeric(frame[column], errors="coerce").astype("float64")
        invalid = set(days[present & ~(np.isfinite(values) & values.ge(0))])
        missing = expected.union(days) - set(days[present])
        for affected, reason in ((missing, "缺净值记录"), (invalid, "缺有限非负净值")):
            gaps.extend({"date": day, "side": side, "field": column, "reason": reason} for day in affected)
    return sorted(gaps, key=lambda gap: (gap["date"], gap["side"]))


def _paired_nav(nav: pd.DataFrame, reference: pd.DataFrame | None) -> pd.DataFrame:
    nav = nav.astype({"date": str}).assign(_candidate_row=True)
    if reference is None:
        return nav.assign(_benchmark_row=True)
    return nav.drop(columns="benchmark_nav").merge(
        reference.astype({"date": str}).assign(_benchmark_row=True), on="date", how="outer", validate="one_to_one",
    )


def _gap_nav_evidence(gaps, comparison, market) -> list[dict[str, Any]]:
    comparison = comparison.set_index("date")
    market_values = market.set_index(market["date"].astype(str))["benchmark_nav"] if market is not None else {}

    def known(value):
        number = pd.to_numeric(value, errors="coerce")
        return float(number) if pd.notna(number) and math.isfinite(number) and number >= 0 else None

    return [
        dict(date=day, candidate_nav=known(comparison["net_nav"].get(day)),
             comparison_nav=known(comparison["benchmark_nav"].get(day)), market_nav=known(market_values.get(day)))
        for day in sorted({gap["date"] for gap in gaps})
    ]


def _sessions(calendar: pd.DataFrame | None) -> set[str]:
    if calendar is None or calendar.empty:
        return set()
    return set(calendar.loc[calendar["is_trading_day"].map(_truthy), "date"].astype(str))


def _resolved_calendar(calendar: pd.DataFrame | None) -> pd.DataFrame | None:
    if calendar is None or calendar.empty or calendar["date"].is_unique:
        return calendar
    flags = _calendar_flags(calendar)
    dates = calendar["date"].astype(str).drop_duplicates()
    return pd.DataFrame({"date": dates, "is_trading_day": dates.map(flags)})


def _closed_sessions(calendar: pd.DataFrame | None) -> set[str]:
    if calendar is None or calendar.empty:
        return set()
    closed = calendar["is_trading_day"].astype(str).str.strip().str.lower().isin({"false", "0"})
    return set(calendar.loc[closed, "date"].astype(str)) - _sessions(calendar)


def _exclude_closed_nav(nav: pd.DataFrame, calendar: pd.DataFrame | None) -> pd.DataFrame:
    return nav.loc[~nav["date"].astype(str).isin(_closed_sessions(calendar))]


def _unproved_calendar_days(calendar, bounds) -> set[str]:
    if calendar is None:
        return set()
    dates = set(calendar["date"].astype(str)) - _sessions(calendar) - _closed_sessions(calendar)
    return {day for day in dates if bounds[0] <= day <= bounds[1]}


def _calendar_nav_gaps(nav, calendar, anchor=None, *, bounds=None) -> list[str]:
    if calendar is None:
        return []
    dates = set(nav["date"].astype(str)) - _sessions(calendar) - _closed_sessions(calendar)
    if bounds is not None:
        dates.update(_unproved_calendar_days(calendar, bounds))
    if anchor is not None and anchor.calendar_unproved:
        dates.add(anchor.day)
    return sorted(dates)


def _monthly_segments(nav, calendar, bounds, initial_cash, account_start):
    """Use the same scheduled boundary for monthly pairing and the enclosing evaluation."""
    sessions = _sessions(calendar)
    expected_by_month: dict[str, set[str]] = {}
    for day in sessions:
        if bounds[0] <= day <= bounds[1]:
            expected_by_month.setdefault(day[:7], set()).add(day)
    window = _window_nav(nav, bounds)
    observed = dict(tuple(window.groupby(window["date"].str[:7], sort=True)))
    unproved = _unproved_calendar_days(calendar, bounds)
    for month in sorted(expected_by_month.keys() | observed.keys() | {day[:7] for day in unproved}):
        frame = observed.get(month, window.iloc[0:0])
        expected = expected_by_month.get(month, set())
        days = set(frame["date"])
        disputed = {day for day in unproved if day.startswith(month)}
        endpoints = expected | disputed or days
        first, last = min(endpoints), max(endpoints)
        daily = _daily_nav_gaps(frame, expected)
        anchor = _anchor(nav, max(bounds[0], month + "-01"), initial_cash, account_start, calendar)
        calendar_gaps = sorted(set(_calendar_nav_gaps(frame, calendar, anchor)) | disputed)
        reason = ""
        if calendar_gaps:
            reason = "区间日期的交易日属性待证或存在冲突"
        elif frame.empty:
            reason = "整个月段无配对净值"
        elif not expected or expected != days:
            reason = "缺完整交易日历或月段配对净值"
        elif anchor.row is None and anchor.opening_cash is None:
            reason = "缺月段开始前的配对净值"
        elif any(_anchor_value(anchor, column) is None for column in ("net_nav", "benchmark_nav")):
            reason = "缺正数且有限的配对净值锚点"
        elif daily:
            reason = "月段逐日配对净值无效"
        if reason:
            yield month, {"window": [first, last], "reason": reason, "anchor_date": anchor.day,
                          **({"daily": daily} if daily else {}),
                          **({"calendar_nav_gaps": calendar_gaps} if calendar_gaps else {})}
        else:
            net, bench = (_anchored_series(frame, anchor, column)[1] for column in ("net_nav", "benchmark_nav"))
            yield month, {"window": [first, last], "anchor_date": anchor.day,
                          "net_return": net[-1] / net[0] - 1, "benchmark_return": bench[-1] / bench[0] - 1}


def _market_states(nav, calendar, bounds, initial_cash, account_start) -> dict[str, Any]:
    """Classify realised month segments by the sign of the matched account benchmark."""
    states: dict[str, list[dict[str, Any]]] = {}
    missing = []
    for _, segment in _monthly_segments(nav, calendar, bounds, initial_cash, account_start):
        if "reason" in segment:
            missing.append(segment)
        else:
            change = segment["benchmark_return"]
            state = "up" if change > 0 else "down" if change < 0 else "flat"
            states.setdefault(state, []).append(segment)
    return {
        "status": "limited" if missing and states else "unknown" if missing else "available",
        "basis": "同池账户基准在评价区间内各月段的已实现净收益方向；仅用于事后归因",
        "states": {
            state: {
                "periods": periods,
                **{metric: math.prod(1 + part[metric] for part in periods) - 1
                   for metric in ("net_return", "benchmark_return")},
            }
            for state, periods in states.items()
        },
        "gaps": sorted(missing, key=lambda gap: gap["window"]),
    }


def _exposure_targets(tables: Mapping[str, pd.DataFrame]):
    targets = tables.get("targets", pd.DataFrame())
    if targets.empty:
        return
    positions = tables.get("positions", pd.DataFrame())
    for (signal, day), rows in targets.groupby(["signal_at", "executable_from"], sort=True):
        holdings = positions.loc[positions["date"].astype(str).eq(str(day))] if not positions.empty else positions
        yield str(signal)[:10], str(day), rows, holdings


def research_exposure_inputs(
    store, source, purpose: str, tables: Mapping[str, pd.DataFrame], bundle_tables,
) -> pd.DataFrame:
    """Select licensed size/classification facts at the same signal cutoff as the account."""
    from hetu_quant.backtest_data import _public_at, _signal_cutoff, _signal_table, select_signal_market_cap

    points = list(_exposure_targets(tables))
    if not points:
        return pd.DataFrame()
    caps = pd.read_parquet(bundle_tables / "market_cap.parquet")
    knowledge = pd.read_parquet(bundle_tables / "knowledge_evidence.parquet")
    members, _, _ = _signal_table(store, source, "memberships", purpose=purpose)
    boards, _, _ = _signal_table(store, source, "boards", purpose=purpose)
    # Only recognise historical membership in an unambiguous, registered industry.
    kinds = boards.groupby("board_id")["board_kind"].agg(set)
    industries = {key for key, values in kinds.items() if values == {"industry"}}
    members = members[members["board_id"].isin(industries) & members["membership_kind"].eq("historical")]
    caps_by_code = dict(tuple(caps.groupby("code")))
    members_by_code = dict(tuple(members.groupby("code")))
    by_key = {}
    for signal, _, targets, holdings in points:
        cutoff = datetime.fromisoformat(_signal_cutoff(signal))
        for code in set(targets["code"]) | (set(holdings["code"]) if not holdings.empty else set()):
            if (signal, code) in by_key:
                continue
            own = caps_by_code.get(code)
            cap = select_signal_market_cap(own, knowledge, code, signal, store.root) if own is not None else None
            item = {"signal_at": signal, "code": code}
            if cap is not None:
                refs = [cap.get(key) for key in ("evidence_ref", "source_ref")]
                reference = next((value for value in refs if isinstance(value, str) and value), None)
                item.update(market_cap_cny=cap["market_cap_cny"],
                            market_cap_source=reference, market_cap_snapshot=source.snapshot_id)
            own_members = members_by_code.get(code)
            if own_members is not None:
                starts = own_members["effective_from"].astype("string").fillna("")
                ends = own_members["effective_to"].astype("string").fillna("")
                known = own_members[
                    own_members["available_at"].map(lambda ts, cutoff=cutoff: _public_at(ts, cutoff))
                    & starts.str.match(r"^\d{4}-\d{2}-\d{2}$") & starts.le(signal)
                    & (ends.eq("") | (ends.str.match(r"^\d{4}-\d{2}-\d{2}$") & ends.gt(signal)))
                    & own_members["source_ref"].fillna("").ne("")
                ]
                if known["board_id"].nunique() == 1:
                    item.update(
                        industry=str(known.iloc[0]["board_id"]),
                        industry_source=sorted(set(known["source_ref"].astype(str))),
                        industry_snapshot=source.snapshot_id,
                    )
            by_key[signal, code] = item
    return pd.DataFrame(by_key.values())


def _weight_exposures(weights: pd.Series, facts: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    total = float(weights.sum())
    size_weight = size_value = 0.0
    industry_weights: dict[str, float] = {}
    rows = []
    for code, weight in weights.items():
        if weight <= 0:
            continue
        fact = facts.get(code, {})
        cap = fact.get("market_cap_cny")
        if isinstance(cap, (int, float)) and math.isfinite(cap) and cap > 0 and fact.get("market_cap_source"):
            size_weight += weight
            size_value += weight * cap
        industry = fact.get("industry")
        if isinstance(industry, str) and industry and fact.get("industry_source"):
            industry_weights[industry] = industry_weights.get(industry, 0.0) + float(weight)
        rows.append({"code": code, "weight": float(weight), **fact})
    known_industry = sum(industry_weights.values())

    def coverage(known):
        return {
            "status": "unknown" if not known else "limited" if not math.isclose(known, total) else "available",
            "known_weight": known, "unknown_weight": max(0.0, total - known),
        }

    return {
        "size": {
            **coverage(size_weight), "weighted_mean_market_cap_cny": size_value / size_weight if size_weight else None,
        },
        "industry": {
            **coverage(known_industry), "weights": industry_weights,
            **({"note": industry_exposure()["note"]} if not known_industry else {}),
        },
        "holdings_weight": total,
        "components": rows,
    }


def _portfolio_exposures(tables: Mapping[str, pd.DataFrame], window: pd.DataFrame, bounds: list[str]) -> dict[str, Any]:
    facts = tables.get("exposure_facts", pd.DataFrame())
    by_signal = {}
    if not facts.empty:
        for row in facts.astype(object).where(facts.notna(), None).to_dict("records"):
            by_signal.setdefault(row["signal_at"], {})[row["code"]] = row
    nav = window.set_index(window["date"].astype(str))["net_nav"]
    snapshots = []
    for signal, day, targets, holdings in _exposure_targets(tables):
        if not bounds[0] <= day <= bounds[1]:
            continue
        evidence = by_signal.get(signal, {})
        target = _weight_exposures(targets.groupby("code")["target_weight"].sum(), evidence)
        base = pd.to_numeric(nav.get(day), errors="coerce")
        actual = _actual_exposure(tables, holdings, evidence, day, base)
        snapshots.append({"signal_at": signal, "date": day, "target": target, "actual": actual})
    coverage = [
        snapshot[portfolio][dimension]["status"]
        for snapshot in snapshots for portfolio in ("target", "actual") for dimension in ("size", "industry")
    ]
    status = "unknown"
    if any(item != "unknown" for item in coverage):
        status = "available" if all(item == "available" for item in coverage) else "limited"
    return {
        "basis": "信号日收盘可知市值与有效行业；实际权重为生效日收盘持仓市值占账户净值",
        "status": status,
        "snapshots": snapshots,
    }


def _actual_exposure(tables, holdings, facts, day, base) -> dict[str, Any]:
    valid_nav = pd.notna(base) and math.isfinite(base) and base > 0
    if not holdings.empty and valid_nav:
        return _weight_exposures(holdings.groupby("code")["market_value"].sum() / base, facts)

    cash = tables.get("cash", pd.DataFrame())
    cash = cash.loc[cash["date"].astype(str).eq(day)] if not cash.empty else cash
    fields = ["cash_available", "receivable", "dividend_tax_payable"]
    if ("positions" in tables and "positions_record_missing" not in tables and holdings.empty
            and len(cash) == 1 and set(fields).issubset(cash)):
        available, receivable, tax = pd.to_numeric(cash.iloc[0][fields], errors="coerce")
        if (math.isfinite(available) and available > 0 and valid_nav
                and receivable == tax == 0 and math.isclose(available, base, rel_tol=0, abs_tol=0.01)):
            coverage = {"status": "available", "known_weight": 0.0, "unknown_weight": 0.0}
            return {
                "size": {**coverage, "weighted_mean_market_cap_cny": None},
                "industry": {**coverage, "weights": {}}, "holdings_weight": 0.0, "components": [],
                "evidence": {"status": "all_cash", "cash_available_cny": float(available), "net_nav_cny": float(base),
                             "sources": ["positions.parquet", "cash.parquet", "nav.parquet"]},
            }
    coverage = {"status": "unknown", "known_weight": None, "unknown_weight": None}
    evidence = (dict(status="unknown", reason="缺实际持仓或可对平净值的全现金记录") if valid_nav else
                dict(status="unknown", reason="缺生效日有效账户净值", date=day, field="net_nav"))
    return {
        "size": {**coverage, "weighted_mean_market_cap_cny": None},
        "industry": {**coverage, "weights": None}, "holdings_weight": None, "components": [],
        "evidence": evidence,
    }


def block_bootstrap_intervals(
    monthly: pd.DataFrame,
    *,
    block_months: int = BOOTSTRAP_BLOCK_MONTHS,
    reps: int = BOOTSTRAP_REPS,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """月度连续块 bootstrap：净超额与风险改善的 95% 区间。

    对月度 paired（候选/基准）收益序列按块长 block_months 重采样 reps 次
    （seed 固定，结果可复现），每次计算累计超额与年化波动差，取 2.5/97.5
    分位。区间跨零不得宣称统计支持（excess/risk 各自独立判定）；样本不足
    两个块长时报 insufficient，不强行出区间。
    """
    n = len(monthly)
    paired = monthly[["net_return", "benchmark_return"]].apply(pd.to_numeric, errors="coerce").astype("float64")
    valid = int(np.isfinite(paired).all(axis=1).sum())
    base = {
        "block_months": block_months,
        "reps": reps,
        "seed": seed,
        "n_months": valid,
        "insufficient": False,
    }
    complete = valid == n
    if n < block_months * 2 or not complete:
        return {
            **base,
            "insufficient": True,
            "excess_return_ci95": None,
            "vol_diff_ci95": None,
            "statistical_support": {"excess": None, "risk": None},
            **({"reason": "缺有效相邻月末净值，无法构造连续配对月度样本"} if not complete else {}),
        }
    net, bench = paired.to_numpy().T
    rng = np.random.default_rng(seed)
    excess_reps = np.empty(reps)
    vol_diff_reps = np.empty(reps)
    for rep in range(reps):
        indices: list[int] = []
        while len(indices) < n:
            start = int(rng.integers(0, n - block_months + 1))
            indices.extend(range(start, start + block_months))
        sample = np.array(indices[:n])
        sampled_net = net[sample]
        sampled_bench = bench[sample]
        excess_reps[rep] = float(np.prod(1.0 + sampled_net) - np.prod(1.0 + sampled_bench))
        vol_diff_reps[rep] = float(
            np.std(sampled_net, ddof=1) * math.sqrt(_MONTHS_PER_YEAR)
            - np.std(sampled_bench, ddof=1) * math.sqrt(_MONTHS_PER_YEAR)
        )
    excess_lo, excess_hi = (float(v) for v in np.quantile(excess_reps, [0.025, 0.975]))
    vol_lo, vol_hi = (float(v) for v in np.quantile(vol_diff_reps, [0.025, 0.975]))
    # 数值噪声容差：退化（常数序列）波动差的浮点残差不视为效应
    eps = 1e-12
    return {
        **base,
        "excess_return_ci95": [excess_lo, excess_hi],
        "vol_diff_ci95": [vol_lo, vol_hi],
        "statistical_support": {
            # 区间不含零才算有统计支持；跨零不得宣称
            "excess": bool(excess_lo > eps or excess_hi < -eps),
            "risk": bool(vol_lo > eps or vol_hi < -eps),
        },
    }


def _nonoverlapping_windows(labels: pd.DataFrame, horizon: int) -> list[dict[str, str]]:
    intervals = []
    for signal_at, group in labels.groupby("signal_at"):
        start = pd.to_datetime(str(signal_at)[:10], errors="coerce")
        if pd.isna(start):
            continue
        if "mature_at" in group:
            observed = pd.to_datetime(group["mature_at"].astype(str).str[:10], errors="coerce")
            if observed.isna().any():
                continue
            end = observed.max()
        else:
            end = start + pd.DateOffset(months=horizon)
        if start < end:
            intervals.append((end, start))
    selected = []
    last_end = None
    # A common endpoint shares a price anchor but no realised return observations.
    for end, start in sorted(intervals):
        if last_end is None or start >= last_end:
            selected.append({"signal_at": str(start.date()), "mature_at": str(end.date())})
            last_end = end
    return selected


def factor_diagnostics(labels: pd.DataFrame) -> dict[str, Any]:
    """低波动定向诊断：-sigma 因子 × 未来 1/3/6 完整月总收益标签。

    分 horizon 给出覆盖、IC（Pearson）/RankIC（Spearman）、十分组均值
    （样本不足 10 时按实际组数出均值并说明）；多 horizon 只做衰减观察，
    不反向调整正式权重。标签成熟性由评价侧统一拒绝（未成熟不进入本函数）。
    """
    required = {"code", "signal_at", "horizon_months", "label_return", "factor_value"}
    missing = required - set(labels.columns)
    if missing:
        raise ValueError(f"factor_diagnostics 缺列: {', '.join(sorted(missing))}")
    horizons: dict[str, Any] = {}
    for horizon, group in labels.groupby(labels["horizon_months"].astype(int)):
        factor = pd.to_numeric(group["factor_value"], errors="coerce")
        label = pd.to_numeric(group["label_return"], errors="coerce")
        valid = factor.notna() & label.notna()
        factor, label = factor[valid], label[valid]
        n = int(valid.sum())
        ic = float(factor.corr(label)) if n >= 3 else None
        # Spearman 手工实现（rank 后 Pearson），不依赖 scipy
        rank_ic = (
            float(factor.rank().corr(label.rank())) if n >= 3 else None
        )
        k = min(10, n)
        deciles: list[dict[str, Any]] = []
        if n >= 2:
            ordered = group[valid.to_numpy()].assign(_f=factor).sort_values("_f")
            per = n // k
            sizes = [per + (1 if i < n % k else 0) for i in range(k)]
            offset = 0
            for index, size in enumerate(sizes):
                part = ordered.iloc[offset:offset + size]
                offset += size
                deciles.append(
                    {
                        "group": index + 1,
                        "n": int(size),
                        "factor_min": float(part["_f"].iloc[0]),
                        "factor_max": float(part["_f"].iloc[-1]),
                        "mean_label_return": float(pd.to_numeric(part["label_return"], errors="coerce").mean()),
                    }
                )
        by_date = []
        for signal_at, cross_section in group.groupby("signal_at"):
            x = pd.to_numeric(cross_section["factor_value"], errors="coerce")
            y = pd.to_numeric(cross_section["label_return"], errors="coerce")
            valid_cross = x.notna() & y.notna()
            x, y = x[valid_cross], y[valid_cross]
            pearson = float(x.corr(y)) if len(x) >= 3 and x.std() > 0 and y.std() > 0 else None
            spearman = float(x.rank().corr(y.rank())) if pearson is not None else None
            by_date.append(dict(signal_at=str(signal_at), n=len(x), ic=pearson, rank_ic=spearman))
        block_intervals = {"block_months": BOOTSTRAP_BLOCK_MONTHS, "reps": BOOTSTRAP_REPS,
                           "seed": BOOTSTRAP_SEED, "ic_ci95": None, "rank_ic_ci95": None}
        for metric in ("ic", "rank_ic"):
            values = np.array([row[metric] for row in by_date if row[metric] is not None], dtype=float)
            if len(values) >= 2 * BOOTSTRAP_BLOCK_MONTHS:
                rng = np.random.default_rng(BOOTSTRAP_SEED)
                estimates = []
                for _ in range(BOOTSTRAP_REPS):
                    indices = []
                    while len(indices) < len(values):
                        start = int(rng.integers(0, len(values) - BOOTSTRAP_BLOCK_MONTHS + 1))
                        indices.extend(range(start, start + BOOTSTRAP_BLOCK_MONTHS))
                    estimates.append(float(values[indices[:len(values)]].mean()))
                block_intervals[f"{metric}_ci95"] = np.quantile(estimates, [.025, .975]).tolist()
        windows = _nonoverlapping_windows(group.loc[valid], int(horizon))
        horizons[str(int(horizon))] = {
            "coverage": n,
            "n_signal_dates": len(by_date),
            "effective_nonoverlapping_windows": len(windows),
            "nonoverlapping_windows": windows,
            "cross_sections": by_date,
            "mean_ic": float(np.mean([r["ic"] for r in by_date if r["ic"] is not None]))
                       if any(r["ic"] is not None for r in by_date) else None,
            "block_intervals": block_intervals,
            "ic": ic,
            "rank_ic": rank_ic,
            "deciles": deciles,
        }
    return {
        "horizons": horizons,
        "notes": [
            "样本不足 10 时十分组按实际组数出均值，不补空组",
            "多 horizon 的逐期 IC／分组观察衰减与漂移，不反向调整正式权重",
            "effective_nonoverlapping_windows 为不重叠月窗数，非股票行数；相关性下不宣称精确独立样本数",
            "IC 块区间不足六个信号时点时为 null，不伪造置信度",
            "IC 不代替盈利判定；标签须在所属评价区间末已成熟",
        ],
    }


def industry_exposure() -> dict[str, str]:
    """行业暴露：缺历史分类输入时标 unknown，不用当前标签回填。"""
    return {
        "status": "unknown",
        "note": "缺具证期间的历史行业分类输入：不用当前标签回填，不阻塞未用行业域的合格量价。",
    }


def _not_evaluated(execution_status: str, research_status: str, reason: str) -> dict:
    return {
        "execution_status": execution_status,
        "research_status": research_status,
        "economic_status": "not_evaluated",
        "income_target_met": None,
        "risk_target_met": None,
        "research_conclusion": "not_evaluated",
        "next_decision": "resolve_blocker_first",
        "reasons": [reason],
        "limitations": [],
        "metrics": {},
    }


def _insufficient(reason: str) -> dict:
    return {
        "execution_status": "complete",
        "research_status": "evaluated",
        "economic_status": "insufficient_evidence",
        "income_target_met": None,
        "risk_target_met": None,
        "research_conclusion": "insufficient_evidence",
        "next_decision": "report_gap",
        "reasons": [reason],
        "limitations": ["统计区间未计算：锚点证据不足，块 bootstrap 不出无锚区间"],
        "metrics": {},
    }


def _window_nav(nav: pd.DataFrame, final: list[str]) -> pd.DataFrame:
    days = nav["date"].astype(str)
    window = nav[(days >= final[0]) & (days <= final[1])]
    return window.sort_values("date")


def _account_start(protocol: Mapping[str, Any]) -> str | None:
    """协议时间切片证明的账户起始日：全部切片起点中的最早者。

    无切片信息则无法证明账户起点，协议初资不得用作锚点。
    """
    split = protocol.get("time_split")
    if not isinstance(split, Mapping):
        return None
    starts = [
        str(segment[0])
        for segment in split.values()
        if isinstance(segment, (list, tuple)) and len(segment)
    ]
    return min(starts) if starts else None


class _NavAnchor(NamedTuple):
    day: str | None
    row: pd.Series | None = None
    opening_cash: float | None = None
    calendar_unproved: bool = False


def _known_closed(calendar: pd.DataFrame, start: str, end: str) -> bool:
    return set(pd.date_range(start, end, inclusive="left").strftime("%Y-%m-%d")) <= _closed_sessions(calendar)


def _anchor(
    nav: pd.DataFrame,
    final_start: str,
    initial_cash: float | None,
    account_start: str | None,
    calendar: pd.DataFrame | None,
) -> _NavAnchor:
    """Keep the expected date even when its balance record is missing."""
    before = nav[nav["date"].astype(str) < final_start].sort_values("date")
    if before.empty and initial_cash is not None and account_start is not None and (
        final_start <= account_start or (calendar is not None and _known_closed(calendar, account_start, final_start))
    ):
        return _NavAnchor(account_start, opening_cash=initial_cash)
    if calendar is not None:
        day = max((day for day in _sessions(calendar) if day < final_start), default=None)
        unknown = [date for date in _calendar_nav_gaps(before, calendar, bounds=(account_start or "", final_start))
                   if date < final_start]
        if unknown and unknown[-1] > (day or "") and (account_start is None or unknown[-1] >= account_start):
            return _NavAnchor(unknown[-1], calendar_unproved=True)
        if day is not None and day[:7] < str(pd.Period(final_start, freq="M") - 1):
            following_day = (pd.Timestamp(day) + pd.Timedelta(days=1)).date().isoformat()
            if not _known_closed(calendar, following_day, final_start):
                return _NavAnchor(None)
        rows = before[before["date"].astype(str).eq(day)]
        return _NavAnchor(day, rows.iloc[-1] if len(rows) else None)
    if len(before):
        row = before.iloc[-1]
        return _NavAnchor(str(row["date"]), row)
    return _NavAnchor(None)


def _anchor_value(anchor: _NavAnchor, column: str) -> float | None:
    raw = anchor.row[column] if anchor.row is not None else anchor.opening_cash
    value = pd.to_numeric(raw, errors="coerce")
    return float(value) if pd.notna(value) and math.isfinite(value) and value > 0 else None


def _anchored_series(
    window: pd.DataFrame, anchor: _NavAnchor, column: str,
) -> tuple[list[str], list[float]] | None:
    """锚点值 + 区间净值组成完整序列；锚点值缺失或非有限正数返回 None。"""
    value = _anchor_value(anchor, column)
    if value is None or anchor.day is None:
        return None
    daily = pd.to_numeric(window[column], errors="coerce").astype("float64")
    if not (np.isfinite(daily) & daily.ge(0)).all():
        return None
    values = [value] + daily.astype(float).tolist()
    dates = [anchor.day] + [str(day) for day in window["date"]]
    return dates, values


def _cagr(dates: list[str], values: list[float]) -> float | None:
    if len(values) < 2 or values[0] <= 0:
        return None
    days = (pd.Timestamp(dates[-1]) - pd.Timestamp(dates[0])).days
    if days <= 0:
        return None
    return (values[-1] / values[0]) ** (_DAYS_PER_YEAR / days) - 1.0


def _ann_vol(values: list[float]) -> float | None:
    returns = pd.Series(values, dtype="float64").pct_change().dropna()
    if len(returns) < 2:
        return None
    return float(returns.std(ddof=1)) * math.sqrt(_TRADING_DAYS_PER_YEAR)


def _max_drawdown(values: list[float]) -> float:
    running = -math.inf
    worst = 0.0
    for value in values:
        running = max(running, value)
        if running > 0:
            worst = max(worst, 1.0 - value / running)
    return worst


def _check_benchmark(tables: Mapping[str, pd.DataFrame], protocol: Mapping[str, Any]) -> None:
    frame = tables.get("benchmark")
    if frame is None or not len(frame):
        raise ValueError("incompatible_benchmark: 缺少基准口径事实，formal 评价拒绝")
    row = frame.iloc[0]
    if str(row["kind"]) != "account":
        raise ValueError(
            f"incompatible_benchmark: kind={row['kind']}，"
            "价格指数等口径不作总收益超额的正式匹配基准"
        )
    if str(row["benchmark_id"]) != str(protocol["benchmark_id"]):
        raise ValueError(
            f"incompatible_benchmark: {row['benchmark_id']} != 协议 {protocol['benchmark_id']}"
        )


def _check_labels(tables: Mapping[str, pd.DataFrame], final: list[str]) -> None:
    frame = tables.get("labels")
    if frame is None or not len(frame):
        return
    immature = frame[frame["mature_at"].astype(str) > final[1]]
    if len(immature):
        row = immature.iloc[0]
        raise ValueError(
            f"immature_label: {row['code']} {row['signal_at']} "
            f"horizon={row['horizon_months']}m 于 {row['mature_at']} 才成熟，"
            f"晚于评价区间末 {final[1]}，拒绝以未成熟标签出结论"
        )


def _market_evaluation(tables, protocol, nav, final, initial_cash, account_start, calendar) -> dict[str, Any]:
    stress = protocol.get("comparison", {}).get("purpose") == "sensitivity_vs_main"
    reference = tables.get("market_benchmark" if stress else "benchmark", pd.DataFrame())
    provenance = reference.iloc[0].to_dict() if not reference.empty else {"benchmark_id": protocol["benchmark_id"]}
    market = tables.get("market_nav" if stress else "comparison_nav")
    if stress and (market is None or market.empty):
        return {
            "status": "unknown", "reference": provenance, "states": {},
            "gaps": [{"window": final, "reason": provenance.get("gap", "缺同池 universe_vw 账户净值")}],
        }
    paired = _exclude_closed_nav(_paired_nav(nav, market), calendar)
    states = _market_states(paired, calendar, final, initial_cash, account_start)
    return {**states, "reference": provenance}


def _monthly_statistics(nav, window, calendar, final, initial_cash, account_start, anchor):
    if calendar is not None:
        columns = ["net_return", "benchmark_return"]
        monthly = pd.DataFrame([
            {key: segment.get(key) for key in columns}
            for _, segment in _monthly_segments(nav, calendar, final, initial_cash, account_start)
        ], columns=columns)
    else:
        initial_values = tuple(_anchor_value(anchor, col) for col in ("net_nav", "benchmark_nav"))
        if None in initial_values or (not window.empty and str(window.iloc[0]["date"])[:7] != final[0][:7]):
            initial_values = None
        monthly = monthly_aligned_returns(window, initial_values=initial_values)
    statistics = block_bootstrap_intervals(monthly)
    end_date = str(window.iloc[-1]["date"]) if len(window) else None
    if end_date is not None and calendar is not None:
        uncertain = _calendar_nav_gaps(window, calendar, bounds=final)
        expected = {day for day in _sessions(calendar) if final[0] <= day <= final[1]}
        if max(expected | set(uncertain), default=end_date) > end_date or end_date in uncertain:
            end_date = None
    statistics.update(window=list(final), anchor_date=anchor.day, end_date=end_date)
    return statistics


def evaluate_backtest(
    tables: Mapping[str, pd.DataFrame], protocol: Mapping[str, Any]
) -> dict[str, Any]:
    """按冻结协议评价同一轨迹；执行失败、机制阶段、缺数据均不给经济判定。"""
    run = tables.get("run")
    run_status = str(run.iloc[0]["status"]) if run is not None and len(run) else "complete"
    run_detail = str(run.iloc[0]["detail"]) if run is not None and len(run) else ""
    if run_status != "complete":
        return _not_evaluated(
            "execution_failed", "not_evaluated",
            f"引擎执行未成功（{run_status}）：{run_detail or '无详情'}；执行状态不折算为经济结论",
        )
    if str(protocol["stage"]) != "formal":
        return _not_evaluated(
            "complete", "not_evaluated", "stage=mechanism：机制轨迹不产出经济判定"
        )

    final = [str(day) for day in protocol.get("evaluation_window", protocol["time_split"].get("final", []))]
    account_start = _account_start(protocol)
    if run is not None and len(run) and "account_start" in run:
        account_start = str(run.iloc[0]["account_start"])
        final = [max(final[0], account_start), min(final[1], str(run.iloc[0]["account_end"]))]
    _check_benchmark(tables, protocol)
    _check_labels(tables, final)

    calendar = _resolved_calendar(tables.get("calendar"))
    own = tables.get("nav", pd.DataFrame(columns=["date", "net_nav", "gross_nav", "benchmark_nav"]))
    nav = _exclude_closed_nav(_paired_nav(own, tables.get("comparison_nav")), calendar)
    window = _window_nav(nav, final)
    initial_raw = protocol.get("initial_cash_cny")
    initial_cash = float(initial_raw) if isinstance(initial_raw, (int, float)) else None
    attributed = {
        "exposures": _portfolio_exposures(tables, window, final),
        "market_states": _market_evaluation(tables, protocol, own, final, initial_cash, account_start, calendar),
        "subperiods": subperiod_metrics(nav, protocol, calendar=calendar, account_start=account_start,
                                        initial_cash=initial_cash),
    }
    expected = {day for day in _sessions(calendar) if final[0] <= day <= final[1]}
    daily_gaps = _daily_nav_gaps(window, expected)
    anchor = _anchor(nav, final[0], initial_cash, account_start, calendar)
    calendar_gaps = _calendar_nav_gaps(window, calendar, anchor, bounds=final)
    if calendar_gaps:
        attributed["calendar_nav_gaps"] = calendar_gaps
    if daily_gaps:
        attributed["daily_nav_gaps"] = daily_gaps
        market = tables.get("market_nav")
        if "comparison_nav" in tables or market is not None:
            attributed["daily_nav_evidence"] = _gap_nav_evidence(daily_gaps, window, market)
    if own.empty:
        return _not_evaluated(
            "complete", "blocked", "缺关键数据：nav 为空，经济评价受阻"
        ) | {"metrics": attributed}
    if not window["_candidate_row"].eq(True).any():
        return _not_evaluated(
            "complete", "blocked",
            f"缺关键数据：评价区间 {final[0]}..{final[1]} 内无净值记录",
        ) | {"metrics": attributed}
    statistics = _monthly_statistics(nav, window, calendar, final, initial_cash, account_start, anchor)
    if calendar_gaps:
        report = _insufficient("区间日期的交易日属性待证或存在冲突（" + "、".join(calendar_gaps) + "）")
        report["limitations"] = ["受影响月份不计入配对统计，整段经济结论待证"]
        report.update(metrics=attributed, statistics=statistics)
        return report
    net = _anchored_series(window, anchor, "net_nav")
    bench = _anchored_series(window, anchor, "benchmark_nav")
    if any(_anchor_value(anchor, col) is None for col in ("net_nav", "benchmark_nav")):
        return _insufficient(
            f"缺评价区间边界净值（{anchor.day or '应有交易日待证'}）；协议初资仅用于已证明的账户起始段"
        ) | {"metrics": attributed, "statistics": statistics}

    if daily_gaps or net is None or bench is None:
        report = _insufficient("评价区间内候选或比较账户缺逐日有效净值，见 daily_nav_gaps")
        report["limitations"] = ["缺逐日配对净值，整段经济和统计评价证据不足"]
        report["metrics"] = attributed
        report["statistics"] = statistics
        return report
    gross = _anchored_series(window, anchor, "gross_nav")
    anchor_date = anchor.day

    limitations = [
        "块 bootstrap 为月度连续块重采样区间（块长/次数/seed 见 statistics）；"
        "IC 标签由正式信号入口生成并按阶段成熟性过滤，不代替盈利判定"
    ]
    reasons: list[str] = [f"收益锚点：{anchor_date}（区间交易前资产，含首日费用影响）"]

    net_cagr = _cagr(*net)
    bench_cagr = _cagr(*bench)
    if net_cagr is None or bench_cagr is None:
        return _insufficient("锚点至区间末不足一个自然日，年化估计不成立")
    excess = net_cagr - bench_cagr
    gross_cagr = _cagr(*gross) if gross is not None else None
    income_met = net_cagr > 0.0 and excess > 0.0  # 严格大于，零收益不算达到
    reasons.append(
        f"最终区间净年化 {net_cagr:.4%}、基准 {bench_cagr:.4%}、超额 {excess:.4%}"
        + (f"、毛年化 {gross_cagr:.4%}（仅费用分解口径）" if gross_cagr is not None else "")
    )

    net_vol = _ann_vol(net[1])
    bench_vol = _ann_vol(bench[1])
    net_dd = _max_drawdown(net[1])
    bench_dd = _max_drawdown(bench[1])
    if net_vol is None or bench_vol is None:
        risk_met = None
        reasons.append("波动估计点不足，风险目标证据不足")
    else:
        risk_met = (
            net_vol <= _RISK_VOL_CAP * bench_vol + 1e-12
            and net_dd <= bench_dd + 1e-12
        )
        reasons.append(
            f"年化波动 {net_vol:.4%} vs 基准 {bench_vol:.4%}（上限 90%），"
            f"最大回撤 {net_dd:.4%} vs 基准 {bench_dd:.4%}"
        )

    if income_met and risk_met:
        economic_status, conclusion, next_decision = "met", "pass", "complete_report"
    elif risk_met is None:
        economic_status, conclusion, next_decision = (
            "insufficient_evidence", "insufficient_evidence", "report_gap",
        )
    else:
        economic_status, conclusion = "not_met", "fail"
        next_decision = "stop_additional_runs"  # 默认未达标停止本轮追加
        if not income_met:
            reasons.append("收益目标未达到：净年化或超额未严格大于 0；风险改善不构成盈利")
        if risk_met is False:
            reasons.append("风险目标未达到")

    if (protocol.get("research_phase") == "development_validation"
            or protocol.get("comparison", {}).get("purpose") == "sensitivity_vs_main"):
        economic_status, conclusion, next_decision = "not_evaluated", "diagnostic_only", "review_frozen_stage"
        income_met = risk_met = None
    comparison = {}
    if protocol.get("comparison", {}).get("purpose") == "sensitivity_vs_main":
        reference = tables["benchmark"].iloc[0].to_dict()
        comparison["comparison"] = {
            "purpose": "sensitivity_vs_main", "reference_trial": "final_main", "reference": reference,
            "candidate_return": net[1][-1] / net[1][0] - 1,
            "reference_return": bench[1][-1] / bench[1][0] - 1,
        }
    return {
        "execution_status": "complete",
        "research_status": "evaluated",
        "economic_status": economic_status,
        "income_target_met": income_met,
        "risk_target_met": risk_met,
        "research_conclusion": conclusion,
        "next_decision": next_decision,
        "reasons": reasons,
        "limitations": limitations,
        "statistics": statistics,
        "metrics": {
            "anchor_date": anchor_date,
            "net_return_window": net[1][-1] / net[1][0] - 1.0,
            "benchmark_return_window": bench[1][-1] / bench[1][0] - 1.0,
            "net_cagr": net_cagr,
            "benchmark_cagr": bench_cagr,
            "excess_cagr": excess,
            "gross_cagr": gross_cagr,
            "ann_vol": net_vol,
            "benchmark_ann_vol": bench_vol,
            "max_drawdown": net_dd,
            "benchmark_max_drawdown": bench_dd,
            **trade_metrics(tables),
            **attributed,
            **comparison,
        },
    }


def publish_after_close_evaluation(store, source, original, *, through) -> str:
    """Publish an evaluation of the exact frozen close, independently of any account."""
    from datetime import date, datetime, time
    from pathlib import Path
    from tempfile import TemporaryDirectory
    from uuid import uuid4

    from hetu_data.library import read_known_closes

    from hetu_quant.after_close import (
        _frozen_evaluation_split,
        _origin_coordinate,
        _publish_json,
        link_after_close_evidence,
        read_after_close,
    )
    from hetu_quant.backtest_data import (
        CN,
        _action_coverage,
        _complete_month_ends,
        _signal_action_inputs,
        _signal_calendar,
        _signal_table,
    )
    from hetu_quant.strategies import _factor_labels_for_signal

    publication = read_after_close(original)
    signal_day = publication["signal_date"]
    boundary = str(through)
    if not signal_day or boundary < signal_day or datetime.combine(through, time(15), CN) > datetime.now(CN):
        raise ValueError("评价需要已发布信号和已收盘的有效截止日")
    origin = _origin_coordinate(publication)
    at = datetime.combine(through, time(15), CN)
    calendar = _signal_calendar(store, source, at)
    ends = {day[:7]: day for day in _complete_month_ends(calendar)}
    month = pd.Period(signal_day, freq="M")
    expected = {h: str(month + h) for h in (1, 3, 6)}
    source_split = _frozen_evaluation_split(store, source, date.fromisoformat(signal_day))
    split_ends = [value for value in (publication.get("evaluation_split_end"), source_split.get("evaluation_split_end"))
                  if value]
    split = min(split_ends) if split_ends else None
    split_unproved = publication.get("evaluation_split_unproved") or source_split.get("evaluation_split_unproved")
    effective = min(boundary, split) if split else boundary
    targets = publication["target"]["targets"]
    actions, actions_denied, action_entries = _signal_table(store, source, "corporate_actions")
    _, bars_denied, _ = _signal_table(store, source, "daily_bars")
    codes = tuple(r["code"] for r in targets)
    closes, _ = read_known_closes(store, source, codes=codes, start=date.fromisoformat(signal_day),
                                  end=date.fromisoformat(effective), at=at) if not bars_denied else (pd.DataFrame(), [])
    rows = []
    for h, maturity_month in expected.items():
        end = pd.Period(maturity_month, freq="M").end_time.date().isoformat()
        mature = ends.get(maturity_month)
        for target in targets:
            code = target["code"]
            base = dict(code=code, horizon_months=h, split_end=split, mature_at=mature)
            if (mature or end) > boundary:
                rows.append(dict(**base, status="pending", reason="尚未到期"))
                continue
            if mature is None:
                rows.append(dict(**base, status="limited", reason="到期月末日历缺证"))
                continue
            if split_unproved or mature > effective:
                reason = "冻结评价切分未证" if split_unproved else "越过冻结评价切分"
                rows.append(dict(**base, status="limited", reason=reason))
                continue
            initial = [fact for fact in publication["close_evidence"]
                       if fact["code"] == code and fact["date"] == signal_day]
            covered = _action_coverage(action_entries, code, signal_day, mature)
            if bars_denied or not len(initial) or not covered or actions_denied:
                rows.append(dict(**base, status="limited", reason="原收盘、评价用途许可或行动覆盖缺证"))
                continue
            events, gaps = _signal_action_inputs(
                actions[actions.code.eq(code)], signal_day, mature, knowledge_day=boundary,
            )
            own = closes[closes.code.eq(code) & closes.date.gt(signal_day) & closes.date.le(mature)]
            window = pd.concat([pd.DataFrame(initial), own], ignore_index=True)
            if gaps or not set(events.ex_date.astype(str)) <= set(window.date):
                rows.append(dict(**base, status="limited", reason="到期窗口行动条款或实施日收盘缺证", gaps=gaps))
                continue
            labels, missing = _factor_labels_for_signal(code, signal_day, -target["signal_value"], window, events,
                                                        {maturity_month: mature}, effective)
            fact = next((row for row in labels if row["horizon_months"] == h), None)
            reason = next((row["reason"] for row in missing if row["horizon_months"] == h), "收益缺证")
            rows.append(dict(**base, status="mature", label_return=fact["label_return"]) if fact else
                        dict(**base, status="limited", reason=reason))
    identifier = f"eval-{uuid4().hex[:12]}"
    parent = original.root / "after-close-evaluations"
    parent.mkdir(parents=True, exist_ok=True)
    source_id = getattr(source, "library_id", None) or source.snapshot_id
    with TemporaryDirectory(prefix=".tmp-", dir=parent) as scratch:
        staging = Path(scratch)
        _publish_json(staging / "evaluation.json", dict(
            evaluation_id=identifier, **origin, through=boundary, horizons=rows,
            source=dict(root=str(store.root.resolve()), id=source_id), generated_at=datetime.now(CN).isoformat(),
        ))
        staging.rename(parent / identifier)
    link_after_close_evidence(original.root, original.result_id, signal_date=date.fromisoformat(signal_day),
                              strategy_id=origin["strategy_id"], kind="evaluation", source_id=identifier)
    return identifier
