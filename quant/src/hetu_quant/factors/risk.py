"""Price grid and theoretical volatility or ATR position transformations."""

from datetime import date, datetime, time
from math import floor, log

import numpy as np

from .core import by_subject, number, output, positive, quotation, require, window
from .prices import sma_filter
from .series import daily, prices, returns


def _grid(facts, request, geometric):
    base = positive(number(request.parameters, "base"), "base")
    step = positive(number(request.parameters, "step"), "step")
    level = request.parameters["level"]
    require(isinstance(level, int), "level", "网格层数必须为整数")
    maximum = window(request, "max_levels")

    def price_at(k):
        return base * (1 + step) ** k if geometric else base + k * step

    def calculate(code):
        row = daily(facts, request, code, 1)[0]
        close = positive(number(row, "close_raw"), "close_raw")
        position = floor(log(close / base) / log(1 + step)) if geometric else floor((close - base) / step)
        return output(
            code,
            price_at(level),
            "元/股",
            dict(
                level=level,
                position=position,
                out_of_bounds=abs(level) > maximum or abs(position) > maximum,
                neighbors=[price_at(position), price_at(position + 1)],
            ),
            [row],
        )

    return by_subject(facts, request, calculate)


def grid_linear_level(facts, request):
    return _grid(facts, request, False)


def grid_geometric_level(facts, request):
    return _grid(facts, request, True)


def atr(facts, request, code):
    count = window(request)
    bars = daily(facts, request, code, count + 1, continuous=True)
    previous = prices(bars)[:-1]
    observations = []
    for row, pre in zip(bars[1:], previous, strict=True):
        high, low = number(row, "high"), number(row, "low")
        require(high >= low, "high/low", "最高价不得低于最低价")
        observations.append(float(max(high - low, abs(high - pre), abs(low - pre))))
    return sum(observations) / count, observations, bars


def average_true_range(facts, request):
    def calculate(code):
        value, observations, bars = atr(facts, request, code)
        return output(code, value, "元/股", dict(true_ranges=observations, window=len(observations)), bars)

    return by_subject(facts, request, calculate)


def _volatility_scale(facts, request, kind):
    count = window(request, "vol_window", 2)
    epsilon = positive(number(request.parameters, "epsilon"), "epsilon") if kind in ("target", "score") else 0

    def calculate(code):
        bars = daily(facts, request, code, count + 1, continuous=True)
        changes = returns(prices(bars))
        vol = float(changes.std(ddof=1) * np.sqrt(252))
        details = dict(volatility=vol, epsilon=epsilon, observations=changes.tolist())
        evidence = list(bars)
        if kind == "target":
            value = min(1, positive(number(request.parameters, "target_vol"), "target_vol") / (vol + epsilon))
        elif kind == "score":
            records = [row for row in facts.records("declared_scores", code) if row["date"] == str(request.trade_date)]
            require(len(records) == 1 and records[0].get("source_ref"), "declared_scores", "缺事前得分及定义")
            score = number(records[0], "score")
            details["score"] = score
            evidence.extend(records)
            value = max(0, score) / (vol + epsilon)
        elif kind == "budget":
            value = positive(number(request.parameters, "risk_budget"), "risk_budget") / positive(vol, "volatility")
        else:
            trend = sma_filter(facts, request)
            require(trend.rows, "trend", "当日趋势状态缺证")
            signal = next((row["value"] for row in trend.rows if row["subject"] == code), None)
            require(signal is not None, "trend", "当日趋势状态缺证")
            details["trend"] = signal
            value = signal * min(
                positive(number(request.parameters, "cap"), "cap"),
                positive(number(request.parameters, "target_vol"), "target_vol") / positive(vol, "volatility"),
            )
        return output(code, value, "比例", details, evidence)

    return by_subject(facts, request, calculate)


def vol_target_scale(facts, request):
    return _volatility_scale(facts, request, "target")


def positive_score_vol_weight(facts, request):
    return _volatility_scale(facts, request, "score")


def risk_budget_vol_weight(facts, request):
    return _volatility_scale(facts, request, "budget")


def trend_vol_weight(facts, request):
    return _volatility_scale(facts, request, "trend")


def rolling_return_correlation(facts, request):
    require(len(request.subjects) == 2 and len(set(request.subjects)) == 2, "subjects", "输入有序的两条序列")
    count = window(request, minimum=2)

    def quote(row, attribute):
        return quotation(row, ("close", "close_raw"), attribute)

    series = []
    for code in request.subjects:
        end = str(request.trade_date)
        current = next((row for row in facts.records("daily_bars", code) if row["date"] == end), {})
        market = quote(current, "market") or (code.rsplit(".", 1)[-1] if "." in code else None)
        calendar_id = quote(current, "calendar_id")
        rows = daily(facts, request, code, count + 1, continuous=True, market=market, calendar_id=calendar_id)
        markets = {quote(row, "market") for row in rows}
        markets.discard(None)
        require(
            len(markets) <= 1, "calendar", "窗口内单腿市场不一致",
            subject=code, period=[rows[0]["date"], rows[-1]["date"]], evidence=rows,
        )
        series.append((rows, next(iter(markets), market)))
    (a, market_a), (b, market_b) = series

    def basis(rows, key, default):
        return {quote(row, key) or default for row in rows}

    currency_a, currency_b = basis(a, "currency", "CNY"), basis(b, "currency", "CNY")
    where = dict(subject=list(request.subjects), period=[a[0]["date"], a[-1]["date"]], evidence=[*a, *b])
    require(len(currency_a) == 1 and currency_a == currency_b, "currency", "两腿收益币种不可比", **where)
    calendars_a, calendars_b = basis(a, "calendar_id", market_a), basis(b, "calendar_id", market_b)
    explicit_calendar = all(quote(row, "calendar_id") for row in [*a, *b])

    def clock(row):
        for field in ("close_timestamp", "close_at", "close_time"):
            value = quote(row, field)
            if value:
                require(isinstance(value, str), "calendar", "收盘时钟格式无效", **where)
                if field == "close_time":
                    try:
                        local_clock = time.fromisoformat(value)
                    except ValueError:
                        require(False, "calendar", "收盘时钟格式无效", **where)
                    if local_clock.utcoffset() is None:
                        return local_clock
                    return datetime.combine(date.fromisoformat(row["date"]), local_clock)
                try:
                    stamp = datetime.fromisoformat(value)
                except ValueError:
                    require(False, "calendar", "收盘时点格式无效", **where)
                require(stamp.utcoffset() is not None, "calendar", "收盘时点缺时区", **where)
                return stamp
        return None

    clocks = [(clock(left), clock(right)) for left, right in zip(a, b, strict=True)]
    if market_a != market_b:
        require(
            all(isinstance(stamp, datetime) for pair in clocks for stamp in pair),
            "calendar",
            "跨市场收盘时点缺带时区的证据",
            **where,
        )

    require(
        len(calendars_a) == 1
        and calendars_a == calendars_b
        and (market_a == market_b or explicit_calendar)
        and [row["date"] for row in a] == [row["date"] for row in b]
        and all(left == right for left, right in clocks),
        "calendar",
        "两腿交易日历或收盘区间不可比",
        **where,
    )
    basis_a, basis_b = basis(a, "return_basis", "price"), basis(b, "return_basis", "price")
    require(len(basis_a) == 1 and basis_a == basis_b, "return_basis", "两腿收益口径不可比", **where)
    ra, rb = returns(prices(a)), returns(prices(b))
    require(ra.std() > 1e-12 and rb.std() > 1e-12, "volatility", "常数收益序列无相关系数")
    value = float(np.corrcoef(ra, rb)[0, 1])
    return facts.result(
        request,
        [
            output(
                request.subjects,
                value,
                "相关系数",
                dict(
                    observations=count,
                    dates=[row["date"] for row in a[1:]],
                    currency=next(iter(currency_a)),
                    return_basis=next(iter(basis_a)),
                    markets=[market_a, market_b],
                ),
                a + b,
            )
        ],
    )


def atr_position_size(facts, request):
    direction = number(request.parameters, "direction")
    require(direction in (-1, 1), "direction", "方向必须为 +1 或 −1")
    risk = positive(number(request.parameters, "risk_amount"), "risk_amount")
    multiple = positive(number(request.parameters, "multiple"), "multiple")

    def calculate(code):
        volatility, observations, bars = atr(facts, request, code)
        value = direction * risk / (positive(volatility, "ATR") * multiple)
        return output(
            code,
            value,
            "股",
            dict(atr=volatility, true_ranges=observations, direction=direction, risk_amount=risk, multiple=multiple),
            bars,
        )

    return by_subject(facts, request, calculate)
