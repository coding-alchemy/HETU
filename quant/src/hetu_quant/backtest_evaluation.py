"""二期 02/03 回测评价：同一轨迹的毛净／成本分解与经济判定。

`evaluate_backtest(tables, protocol)` 消费冻结协议与固定轨迹表，输出分立状态：
execution_status（引擎执行）、research_status（研究评价）、economic_status
（经济目标）互不替代；机制阶段与未正式评价一律显式 not_evaluated，有效负结果
仍是 evaluated/fail，不允许靠风险改善宣称盈利。

收益锚点：评价区间的收益、波动与回撤一律从"区间交易前资产"起算——区间前
最后一条净值记录为锚；协议 initial_cash_cny 只在评价区间即已证明的账户
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
from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

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


def _anchored_segment_return(
    nav: pd.DataFrame, start: str, end: str, column: str
) -> float | None:
    """子区间收益：锚 = 区间前最后一条净值，末 = 区间内最后一条；缺任一侧为 None。"""
    days = nav["date"].astype(str)
    before = nav[days < start].sort_values("date")
    window = nav[(days >= start) & (days <= end)].sort_values("date")
    if not len(before) or not len(window):
        return None
    anchor_value = pd.to_numeric(before.iloc[-1][column], errors="coerce")
    end_value = pd.to_numeric(window.iloc[-1][column], errors="coerce")
    if pd.isna(anchor_value) or pd.isna(end_value) or float(anchor_value) <= 0:
        return None
    return float(end_value) / float(anchor_value) - 1.0


def subperiod_metrics(nav: pd.DataFrame, protocol: Mapping[str, Any]) -> dict[str, Any]:
    """按协议 time_split 端点切分子区间（开发／验证／最终样本外）。"""
    split = protocol.get("time_split")
    result: dict[str, Any] = {}
    if not isinstance(split, Mapping):
        return result
    for name, segment in split.items():
        if not isinstance(segment, (list, tuple)) or len(segment) != 2:
            continue
        start, end = str(segment[0]), str(segment[1])
        result[str(name)] = {
            "window": [start, end],
            "net_return": _anchored_segment_return(nav, start, end, "net_nav"),
            "benchmark_return": _anchored_segment_return(nav, start, end, "benchmark_nav"),
        }
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
    monthly["net_return"] = pd.to_numeric(monthly["net_nav"], errors="coerce").pct_change()
    monthly["benchmark_return"] = pd.to_numeric(monthly["benchmark_nav"], errors="coerce").pct_change()
    if initial_values is None:
        monthly = monthly.iloc[1:].reset_index(drop=True)
    elif len(monthly):
        monthly.loc[0, "net_return"] = float(monthly.iloc[0]["net_nav"]) / initial_values[0] - 1
        monthly.loc[0, "benchmark_return"] = float(monthly.iloc[0]["benchmark_nav"]) / initial_values[1] - 1
    return monthly[["month", "date", "net_return", "benchmark_return"]]


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
    base = {
        "block_months": block_months,
        "reps": reps,
        "seed": seed,
        "n_months": n,
        "insufficient": False,
    }
    if n < block_months * 2:
        return {
            **base,
            "insufficient": True,
            "excess_return_ci95": None,
            "vol_diff_ci95": None,
            "statistical_support": {"excess": None, "risk": None},
        }
    net = pd.to_numeric(monthly["net_return"], errors="coerce").to_numpy(dtype=float)
    bench = pd.to_numeric(monthly["benchmark_return"], errors="coerce").to_numpy(dtype=float)
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
        horizons[str(int(horizon))] = {
            "coverage": n,
            "n_signal_dates": len(by_date),
            "effective_nonoverlapping_windows": len(by_date) // int(horizon),
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
        "note": "缺具证期间的历史行业分类输入：不用当前标签回填，不阻塞未用行业域的合格量价；"
        "接入后按信号日时点版本计算暴露",
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


def _anchor(
    nav: pd.DataFrame,
    final_start: str,
    initial_cash: float | None,
    account_start: str | None,
) -> tuple[str, Any] | None:
    """区间交易前资产锚点：(锚定日, 锚点行)。

    区间前有净值记录时锚定最后一条；协议初资只在评价区间即已证明的
    账户起始段（区间起点不晚于账户起始日）时才是合法锚点——后续时间
    切片缺边界资产不得回退全局初资，应报证据不足。
    """
    before = nav[nav["date"].astype(str) < final_start].sort_values("date")
    if len(before):
        row = before.iloc[-1]
        return str(row["date"]), row
    if (
        initial_cash is not None
        and account_start is not None
        and final_start <= account_start
    ):
        return final_start, None
    return None


def _anchored_series(
    window: pd.DataFrame, anchor_date: str, anchor_row: Any, initial_cash: float | None,
    column: str,
) -> tuple[list[str], list[float]] | None:
    """锚点值 + 区间净值组成完整序列；锚点值缺失或非有限正数返回 None。"""
    if anchor_row is not None:
        raw = pd.to_numeric(pd.Series([anchor_row[column]]), errors="coerce").iloc[0]
        value = None if pd.isna(raw) else float(raw)
    else:
        value = initial_cash
    if value is None or not math.isfinite(value) or value <= 0:
        return None
    values = [value] + [float(v) for v in window[column]]
    dates = [anchor_date] + [str(day) for day in window["date"]]
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

    nav = tables.get("nav")
    if nav is None or not len(nav):
        return _not_evaluated(
            "complete", "blocked", "缺关键数据：nav 为空，经济评价受阻"
        )
    window = _window_nav(nav, final)
    if not len(window):
        return _not_evaluated(
            "complete", "blocked",
            f"缺关键数据：评价区间 {final[0]}..{final[1]} 内无净值记录",
        )

    initial_raw = protocol.get("initial_cash_cny")
    initial_cash = float(initial_raw) if isinstance(initial_raw, (int, float)) else None
    anchor = _anchor(nav, final[0], initial_cash, account_start)
    if anchor is None:
        return _insufficient(
            "缺评价区间交易前资产锚点：区间前无净值记录；协议初资仅可用于已证明的"
            "账户起始段（本区间起点晚于账户起点或协议无时间切片证明），"
            "后续切片缺边界资产不得以初资或窗口首条净值冒充锚点"
        )
    anchor_date, anchor_row = anchor

    net = _anchored_series(window, anchor_date, anchor_row, initial_cash, "net_nav")
    bench = _anchored_series(window, anchor_date, anchor_row, initial_cash, "benchmark_nav")
    gross = _anchored_series(window, anchor_date, anchor_row, initial_cash, "gross_nav")
    if net is None or bench is None:
        return _insufficient("锚点或区间内净值／基准值缺失或非有限正数，证据不足")

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

    monthly = monthly_aligned_returns(window, initial_values=(net[1][0], bench[1][0]))
    statistics = block_bootstrap_intervals(monthly)
    statistics.update(window=list(final), anchor_date=anchor_date, end_date=str(window.iloc[-1]["date"]))

    if (protocol.get("research_phase") == "development_validation"
            or protocol.get("comparison", {}).get("purpose") == "sensitivity_vs_main"):
        economic_status, conclusion, next_decision = "not_evaluated", "diagnostic_only", "review_frozen_stage"
        income_met = risk_met = None
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
            "subperiods": subperiod_metrics(nav, protocol),
        },
    }
