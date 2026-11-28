"""Trend and momentum from the same comparable daily price series."""

from bisect import bisect_right
from dataclasses import replace
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .core import by_subject, number, output, positive, require, window
from .series import (
    _calendar,
    calendar_dependencies,
    daily,
    daily_observations,
    prices,
    require_aligned_return_intervals,
    require_calendar_for_observations,
    require_consistent_calendar,
    returns,
    sessions,
)
from .sources import applicable_sector_proxies, calendar_market, known_records


def _sector_proxy(facts, request, code):
    declared = applicable_sector_proxies(known_records(facts, "sector_proxies", request, code), request, code)
    require(len(declared) < 2, "sector_proxies", "同日板块代理来源冲突", subject=code)
    return declared[0] if declared else None


def _comparable_bars(facts, request, code, count, *, previous=False):
    return _bars_for_proxy(facts, request, code, count, previous, _sector_proxy(facts, request, code))


def _bars_for_proxy(facts, request, code, count, previous, proxy):
    if proxy is None:
        return daily(facts, request, code, count, previous=previous, continuous=True)
    mode = proxy.get("kind")
    if mode in ("index", "etf"):
        symbol = proxy.get("symbol")
        require(symbol, "symbol", "缺已声明的板块指数或 ETF")
        return [
            dict(row, sector_proxy=proxy)
            for row in daily(facts, request, symbol, count, previous=previous, continuous=True)
        ]
    require(mode == "weighted", "sector_proxies", "未知板块序列构造口径")
    return _weighted_sector_bars(facts, request, code, count, previous, proxy)


def _weighted_sector_bars(facts, request, code, count, previous, proxy):
    from .core import close_cutoff
    from .cross_section import scope

    days = sessions(facts, request)
    if previous:
        days = days[:-1]
    require(len(days) >= count, "calendar", "板块价格序列历史不足", subject=code)
    days = days[-count:]
    level = 100.0
    result = [dict(code=code, date=days[0], close=level, sector_proxy=proxy)]
    for left, right in zip(days[:-1], days[1:], strict=True):
        day = date.fromisoformat(left)
        historical = replace(request, trade_date=day, cutoff=min(request.cutoff, close_cutoff(day)))
        codes, membership, complete = scope(facts, historical, code)
        require(complete, "memberships", "板块自建序列缺当期完整成员", subject=code, period=left)
        weights = {row["member"]: number(row, "weight") for row in membership}
        require(
            abs(sum(weights.values()) - 1) <= 1e-8 and all(weight >= 0 for weight in weights.values()),
            "weight",
            "板块自建序列需要同日完整非负权重",
            subject=code,
            period=left,
        )
        changes, inputs = {}, []
        for symbol in codes:
            lookup = {row["date"]: row for row in facts.records("daily_bars", symbol)}
            require(left in lookup and right in lookup, "close", "缺成员两日可比收盘", subject=symbol, period=right)
            selected = [lookup[left], lookup[right]]
            values = prices(selected)
            changes[symbol] = float(values[1] / values[0] - 1)
            inputs.extend(selected)
        level *= sum(weights[symbol] * changes[symbol] for symbol in codes) + 1
        result.append(
            dict(
                code=code,
                date=right,
                close=level,
                sector_proxy=proxy,
                weights=weights,
                original_members=membership,
                member_bars=inputs,
            )
        )
    return result


def _comparable(facts, request, code, count, *, previous=False):
    rows = _comparable_bars(facts, request, code, count, previous=previous)
    return rows, prices(rows, "close")


def _mean(facts, request, code):
    rows, values = _comparable(facts, request, code, window(request))
    return rows, values, float(values.mean())


def sma(facts, request):
    def calculate(code):
        rows, values, mean = _mean(facts, request, code)
        return output(code, mean, "元/股", dict(close=float(values[-1]), window=len(values)), rows)

    return by_subject(facts, request, calculate)


def sma_filter(facts, request):
    def calculate(code):
        rows, values, mean = _mean(facts, request, code)
        return output(code, int(values[-1] > mean), "信号", dict(mean=mean, close=float(values[-1])), rows)

    return by_subject(facts, request, calculate)


def sma_cross(facts, request):
    short = window(request, "short")
    long = window(request, "long", 2)
    require(short < long, "short", "短窗必须小于长窗")

    def calculate(code):
        rows, values = _comparable(facts, request, code, long + 1)
        previous = values[-short - 1 : -1].mean() - values[:-1].mean()
        current = values[-short:].mean() - values[1:].mean()
        direction = int(previous <= 0 < current) - int(previous >= 0 > current)
        return output(code, direction, "信号", dict(previous_gap=float(previous), current_gap=float(current)), rows)

    return by_subject(facts, request, calculate)


def donchian_breakout(facts, request):
    upper_window, lower_window = window(request, "upper_window"), window(request, "lower_window")

    def calculate(code):
        rows = _comparable_bars(facts, request, code, max(upper_window, lower_window) + 1)
        upper = max(number(row, "high") for row in rows[-upper_window - 1 : -1])
        lower = min(number(row, "low") for row in rows[-lower_window - 1 : -1])
        close = number(rows[-1], "close")
        return output(
            code,
            int(close > upper) - int(close < lower),
            "信号",
            dict(upper=upper, lower=lower, close=close, channel_end=rows[-2]["date"]),
            rows,
        )

    return by_subject(facts, request, calculate)


def momentum(facts, request, code):
    proxy = _sector_proxy(facts, request, code)
    price_code = proxy.get("symbol") if proxy and proxy.get("kind") in ("etf", "index") else code
    require(price_code, "symbol", "缺已声明的板块指数或 ETF", subject=code)
    market = calendar_market(price_code)
    skip = request.parameters.get("skip", 0)
    require(type(skip) is int and skip >= 0, "skip", "跳期需要非负整数")
    months = request.parameters.get("months", 0)
    require(type(months) is int and months >= 0, "months", "自然月数需要非负整数")
    if months:
        require(skip == 0, "skip", "自然月预设不跳期")
        boundary = (pd.Timestamp(request.trade_date) - pd.DateOffset(months=months)).date()
        days = sessions(facts, request, market)
        position = bisect_right(days, str(boundary)) - 1
        require(position >= 0, "calendar", "自然月锚点无证", period=str(boundary), subject=code)
        require(
            (boundary - pd.Timestamp(days[position]).date()) <= timedelta(days=15),
            "calendar",
            "自然月锚点附近缺日历",
            period=str(boundary),
            subject=code,
        )
        count = len(days) - position
    else:
        count = window(request) + skip + 1
    if skip:
        observation = str(request.trade_date)
        calendar = _calendar(facts, request, market)
        require_consistent_calendar(calendar, observation, observation, code)
        require({row.get("is_trading_day") for row in calendar.get(observation, ())} == {True},
                "calendar", "观察日缺已证交易日历", subject=code, period=observation)
        dates = sorted(day for day, rows in calendar.items() if {row.get("is_trading_day") for row in rows} == {True})
        left = dates[-min(count, len(dates))]
        require_consistent_calendar(calendar, left, observation, code)
        require_calendar_for_observations(
            calendar, daily_observations(facts, request, price_code), left, observation, price_code
        )
        require(len(dates) >= count, "calendar", "跳期窗口不齐", subject=code, period=observation)
        end = date.fromisoformat(dates[-skip - 1])
        rows = _bars_for_proxy(facts, replace(request, trade_date=end), code, count - skip, False, proxy)
        if facts.access and "_workbench_calendar" in rows[0]:
            skipped = calendar_dependencies(calendar, end.isoformat(), observation)
            rows[0] = dict(rows[0], _workbench_calendar=[*rows[0]["_workbench_calendar"], *skipped])
    else:
        rows = _bars_for_proxy(facts, request, code, count, False, proxy)
    values = prices(rows, "close")
    return float(values[-1] / values[0] - 1), rows


def price_momentum(facts, request):
    def calculate(code):
        value, rows = momentum(facts, request, code)
        detail = dict(anchor=rows[0]["date"], end=rows[-1]["date"])
        if rows[0].get("code") and rows[0]["code"] != code:
            detail["price_subject"] = rows[0]["code"]
        return output(code, value, "比例", detail, rows)

    return by_subject(facts, request, calculate)


def absolute_momentum(facts, request):
    threshold = number(request.parameters, "threshold")

    def calculate(code):
        value, rows = momentum(facts, request, code)
        return output(
            code,
            value - threshold,
            "比例",
            dict(own_return=value, threshold=threshold, passed=value > 0 and value > threshold),
            rows,
        )

    return by_subject(facts, request, calculate)


def risk_adjusted_momentum(facts, request):
    count = window(request, "vol_window", 2)
    epsilon = positive(number(request.parameters, "epsilon"), "epsilon")

    def calculate(code):
        value, motion = momentum(facts, request, code)
        rows, values = _comparable(facts, request, code, count + 1)
        vol = float(returns(values).std(ddof=1) * np.sqrt(252))
        return output(code, value / (vol + epsilon), "分数", dict(momentum=value, volatility=vol), motion + rows)

    return by_subject(facts, request, calculate)


def relative_strength(facts, request):
    benchmark = request.parameters.get("benchmark")
    require(bool(benchmark), "benchmark", "需要指定同口径基准")

    def calculate(code):
        value, rows = momentum(facts, request, code)
        base, reference = momentum(facts, request, benchmark)
        anchor, end = rows[0]["date"], rows[-1]["date"]
        require_aligned_return_intervals(code, benchmark, [(anchor, end)],
                                         [(reference[0]["date"], reference[-1]["date"])])
        return output(
            code,
            value - base,
            "比例",
            dict(momentum=value, benchmark=benchmark, benchmark_return=base, anchor=anchor, end=end),
            rows + reference,
        )

    return by_subject(facts, request, calculate)


def short_reversal(facts, request):
    def calculate(code):
        value, rows = momentum(facts, request, code)
        return output(code, -value, "比例", dict(price_return=value), rows)

    return by_subject(facts, request, calculate)


def price_deviation_z(facts, request):
    def calculate(code):
        rows, values, mean = _mean(facts, request, code)
        require(len(values) >= 2, "window", "样本标准差至少需要两个收盘")
        deviation = positive(float(values.std(ddof=1)), "standard_deviation")
        return output(
            code, float((values[-1] - mean) / deviation), "标准分", dict(mean=mean, standard_deviation=deviation), rows
        )

    return by_subject(facts, request, calculate)
