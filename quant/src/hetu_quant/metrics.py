"""03.1 原始指标：收益、相对收益、半年回撤、60 日波动、上涨成交额占比。

公式以 03 计划 §2 为准：收益 P_end/P_anchor-1；相对收益减基准；
半年回撤 max(1 - P_t/running_max)；60 日波动为 60 个日简单收益的
样本标准差 × sqrt(252)；上涨成交额占比为同 60 日中 P_t > P_{t-1} 的
成交额之和 / 全部成交额之和，平盘只进分母。

缺必需值时对应指标返回 None，并在 ``gaps`` 中给出明确缺口说明，绝不补零；
调用方依据资格规则把对象列入观察或排除。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date

import pandas as pd

HALF_YEAR_MONTHS = 6
QUARTER_MONTHS = 3
BACKGROUND_YEARS = 3
RECENT_TRADING_DAYS = 60
PRICE_RETURN_BASIS = "price"

METRIC_KEYS: tuple[str, ...] = (
    "return_3m",
    "return_6m",
    "benchmark_return_3m",
    "benchmark_return_6m",
    "excess_3m",
    "excess_6m",
    "max_drawdown_6m",
    "up_amount_60d",
    "volatility_60d",
)


def _anchor(market_days: list[str], boundary: date) -> str | None:
    """边界当日或之前最近市场交易日。"""
    candidates = [day for day in market_days if day <= boundary.isoformat()]
    return candidates[-1] if candidates else None


def compute_metrics(
    prices: pd.DataFrame,
    benchmark: pd.DataFrame,
    cutoff: date,
    parameters: Mapping[str, object],
) -> dict[str, float | None]:
    """对已对齐输入计算原始指标；市场交易日集合由基准行情日期确定。

    ``prices`` 需含 ``date/close/amount_cny``（单一对象的已对齐序列）；
    ``benchmark`` 需含 ``date/close/return_basis``。返回值含全部指标键，
    缺口说明在 ``gaps`` 键下（tuple[str, ...]）；该键是唯一非 float 值。
    """
    if "return_basis" not in benchmark.columns:
        raise ValueError("return_basis missing: 收益口径未知，拒绝计算")
    bases = {str(value) for value in benchmark["return_basis"].dropna().unique()}
    if len(bases) > 1:
        raise ValueError(f"return_basis mixed: {sorted(bases)}，不同收益口径拒绝混排")
    if not bases:
        raise ValueError("return_basis missing: 收益口径未知，拒绝计算")
    if bases.pop() != PRICE_RETURN_BASIS:
        raise ValueError("return_basis 非价格收益口径：与冻结的排名口径冲突，拒绝混排")

    half_year_months = int(parameters.get("half_year_months", HALF_YEAR_MONTHS))
    quarter_months = int(parameters.get("quarter_months", QUARTER_MONTHS))
    background_years = int(parameters.get("background_years", BACKGROUND_YEARS))
    recent_days = int(parameters.get("recent_trading_days", RECENT_TRADING_DAYS))

    market_days = sorted(
        {str(day) for day in benchmark["date"] if str(day) <= cutoff.isoformat()}
    )
    price_map: dict[str, tuple[float, float | None]] = {}
    for row in prices.itertuples(index=False):
        day = str(row.date)
        if day > cutoff.isoformat():
            continue
        amount = float(row.amount_cny) if pd.notna(row.amount_cny) else None
        price_map[day] = (float(row.close), amount)

    gaps: list[str] = []
    result: dict[str, float | None] = {key: None for key in METRIC_KEYS}
    result["history_days"] = float(len(price_map))
    result["short_history"] = 0.0
    result["gaps"] = ()

    if not market_days or cutoff.isoformat() not in market_days:
        gaps.append("benchmark_missing_cutoff: 基准无截止日行情，共同截止日不能平移")
        result["gaps"] = tuple(gaps)
        return result

    anchor_6m = _anchor(market_days, cutoff - pd.DateOffset(months=half_year_months))
    anchor_3m = _anchor(market_days, cutoff - pd.DateOffset(months=quarter_months))
    anchor_3y = _anchor(market_days, cutoff - pd.DateOffset(years=background_years))
    for label, anchor in (("6m", anchor_6m), ("3m", anchor_3m)):
        if anchor is None:
            gaps.append(f"missing_anchor:{label}: 市场日不足，锚点不存在")
    if anchor_3y is None or min(price_map) > anchor_3y:
        result["short_history"] = 1.0

    bench_map = {
        str(row.date): float(row.close)
        for row in benchmark.itertuples(index=False)
        if str(row.date) <= cutoff.isoformat()
    }

    def close_of(day: str | None) -> float | None:
        if day is None:
            return None
        value = price_map.get(day)
        return value[0] if value else None

    p_cut = close_of(cutoff.isoformat())
    p_6 = close_of(anchor_6m)
    p_3 = close_of(anchor_3m)
    if p_cut is None:
        gaps.append(f"missing_price:cutoff:{cutoff.isoformat()}")
    if p_6 is None:
        gaps.append(f"missing_anchor_price:6m:{anchor_6m}")
    if p_3 is None:
        gaps.append(f"missing_anchor_price:3m:{anchor_3m}")

    half_sessions = [
        d
        for d in market_days
        if anchor_6m is not None and anchor_6m <= d <= cutoff.isoformat()
    ]
    missing_half = [d for d in half_sessions if d not in price_map]
    if missing_half:
        gaps.append(f"missing_half_year_sessions:{len(missing_half)}")
    quarter_sessions = [
        d
        for d in market_days
        if anchor_3m is not None and anchor_3m <= d <= cutoff.isoformat()
    ]
    missing_quarter = [d for d in quarter_sessions if d not in price_map]
    if missing_quarter:
        gaps.append(f"missing_quarter_sessions:{len(missing_quarter)}")

    tail = market_days[-(recent_days + 1):]
    if len(tail) < recent_days + 1:
        gaps.append(f"incomplete_60d_window:only:{len(tail)}:market_days")
    missing_tail = [d for d in tail if d not in price_map]
    if missing_tail:
        gaps.append(f"incomplete_60d_window:missing:{len(missing_tail)}")
    tail_amounts = [price_map[d][1] for d in tail if d in price_map]
    if len(tail_amounts) == len(tail) and any(value is None for value in tail_amounts):
        gaps.append("missing_amount_60d: 60 日窗口存在未知成交额")

    # 基准锚点与窗口
    for label, anchor in (("6m", anchor_6m), ("3m", anchor_3m)):
        if anchor is None or anchor not in bench_map:
            gaps.append(f"benchmark_missing_anchor:{label}")
    bench_sessions_missing = [d for d in half_sessions if d not in bench_map]
    if bench_sessions_missing:
        gaps.append(
            f"benchmark_missing_half_year_sessions:{len(bench_sessions_missing)}"
        )

    # 收益与相对收益
    if p_cut is not None and p_6 is not None and not missing_half and anchor_6m:
        result["return_6m"] = p_cut / p_6 - 1
    if p_cut is not None and p_3 is not None and not missing_quarter and anchor_3m:
        result["return_3m"] = p_cut / p_3 - 1
    b_cut = bench_map.get(cutoff.isoformat())
    if b_cut is None:
        gaps.append("benchmark_missing_cutoff")
    b_6 = bench_map.get(anchor_6m) if anchor_6m else None
    b_3 = bench_map.get(anchor_3m) if anchor_3m else None
    bench_ready = b_cut is not None and not bench_sessions_missing
    if b_6 is not None and bench_ready and anchor_6m:
        result["benchmark_return_6m"] = b_cut / b_6 - 1
    if b_3 is not None and b_cut is not None and anchor_3m and not [
        d for d in quarter_sessions if d not in bench_map
    ]:
        result["benchmark_return_3m"] = b_cut / b_3 - 1
    for label in ("3m", "6m"):
        own = result[f"return_{label}"]
        bench = result[f"benchmark_return_{label}"]
        if own is not None and bench is not None:
            result[f"excess_{label}"] = own - bench

    # 半年最大回撤
    if not missing_half and half_sessions:
        closes = [price_map[d][0] for d in half_sessions if d in price_map]
        if len(closes) == len(half_sessions):
            running = closes[0]
            worst = 0.0
            for value in closes:
                running = max(running, value)
                worst = max(worst, 1 - value / running)
            result["max_drawdown_6m"] = worst

    # 60 日波动与上涨成交额占比
    if not missing_tail and len(tail) == recent_days + 1:
        closes = [price_map[d][0] for d in tail]
        amounts = [price_map[d][1] for d in tail]
        returns = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
        stdev = float(pd.Series(returns).std(ddof=1))
        result["volatility_60d"] = stdev * math.sqrt(252)
        if any(value is None for value in amounts):
            pass  # 缺口已记录
        else:
            up = sum(
                amounts[i] for i in range(1, len(closes)) if closes[i] > closes[i - 1]
            )
            total = sum(amounts[i] for i in range(1, len(closes)))
            if total > 0:
                result["up_amount_60d"] = up / total
            else:
                gaps.append("zero_amount_60d: 60 日成交额合计为 0，占比不可计算")

    result["gaps"] = tuple(gaps)
    return result
