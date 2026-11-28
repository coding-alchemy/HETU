"""Volatility, return regression, and downside risk."""

from bisect import bisect_right

import numpy as np
import pandas as pd

from .core import (
    by_subject,
    check_conflict,
    event_in_window,
    number,
    output,
    positive,
    require,
    stock_share_amount,
    window,
)
from .pairs import line
from .series import daily, prices, require_aligned_return_intervals, returns, sessions


def _basis(request):
    basis = request.parameters.get("basis", "continuous")
    require(basis in ("continuous", "raw"), "basis", "请选择连续价格或原价口径")
    return "close" if basis == "continuous" else "close_raw"


def realized_volatility(facts, request):
    count = window(request, minimum=2)
    field = _basis(request)

    def calculate(code):
        rows = daily(facts, request, code, count + 1, continuous=_basis(request) == "close")
        changes = returns(prices(rows, field))
        value = float(changes.std(ddof=1) * np.sqrt(252))
        return output(code, value, "年化比例", dict(basis=field, returns=changes.tolist()), rows)

    return by_subject(facts, request, calculate)


def _market_fit(facts, request, risk):
    count = window(request, minimum=3 if risk else 2)
    benchmark = request.parameters.get("benchmark")
    require(bool(benchmark), "benchmark", "缺基准标识")
    field = _basis(request)
    reference = daily(facts, request, benchmark, count + 1, continuous=field == "close")
    reference_days = [row["date"] for row in reference]
    reference_intervals = list(zip(reference_days[:-1], reference_days[1:], strict=True))
    base = returns(prices(reference, field))

    def calculate(code):
        rows = daily(facts, request, code, count + 1, continuous=field == "close")
        days = [row["date"] for row in rows]
        intervals = list(zip(days[:-1], days[1:], strict=True))
        require_aligned_return_intervals(code, benchmark, intervals, reference_intervals)
        fitted = returns(prices(rows, field))
        alpha, beta, errors = line(base, fitted)
        residual_sum = float(errors @ errors)
        value = float(np.sqrt(residual_sum / (count - 2) * 252)) if risk else beta
        return output(
            code,
            value,
            "年化比例" if risk else "系数",
            dict(
                alpha=alpha,
                beta=beta,
                benchmark=benchmark,
                observations=count,
                residual_sum=residual_sum,
                residual_degrees=count - 2,
                return_intervals=[dict(start=start, end=end) for start, end in intervals],
            ),
            rows + reference,
        )

    return by_subject(facts, request, calculate)


def market_beta(facts, request):
    return _market_fit(facts, request, False)


def residual_volatility(facts, request):
    return _market_fit(facts, request, True)


def max_drawdown(facts, request):
    months = request.parameters.get("months", 0)
    if months:
        days = sessions(facts, request)
        start = str((pd.Timestamp(request.trade_date) - pd.DateOffset(months=months)).date())
        pos = bisect_right(days, start) - 1
        require(pos >= 0, "calendar", "回撤区间的自然月锚点缺证")
        count = len(days) - pos
    else:
        count = window(request, minimum=2)

    def calculate(code):
        rows = daily(facts, request, code, count, continuous=_basis(request) == "close")
        values = prices(rows, _basis(request))
        drawdown = 1 - values / np.maximum.accumulate(values)
        trough = int(np.argmax(drawdown))
        peak = int(np.argmax(values[: trough + 1]))
        return output(
            code, float(drawdown[trough]), "比例", dict(peak=rows[peak]["date"], trough=rows[trough]["date"]), rows
        )

    return by_subject(facts, request, calculate)


def low_volatility_36m(facts, request):
    from hetu_quant.backtest_data import _complete_month_ends

    frame = facts.tables.get("calendar", pd.DataFrame())
    require(not frame.empty, "calendar", "缺完整月末日历")
    ends = [day for day in _complete_month_ends(frame) if day <= str(request.trade_date)][-37:]
    require(len(ends) == 37, "calendar", "需要 37 个完整月末")
    periods = pd.PeriodIndex(ends, freq="M")
    require(
        all(right - left == 1 for left, right in zip(periods.asi8[:-1], periods.asi8[1:], strict=True)),
        "calendar",
        "月末必须属于连续完整自然月",
    )

    def calculate(code):
        coverage = [
            row
            for row in facts.records("action_coverage", code)
            if row.get("start", "9999") <= ends[0] and row.get("end", "") >= ends[-1]
        ]
        require(coverage, "corporate_actions", "总收益需要覆盖整个预热窗口的公司行动凭据", period=ends)
        rows = {row["date"]: row for row in facts.records("daily_bars", code)}
        for end in ends:
            require(end in rows, "close_raw", "缺完整月末的收盘", subject=code, period=end)
        events, original_events = [], []
        for row in facts.records("corporate_actions", code):
            effective = event_in_window(row, ends[0], ends[-1])
            if not effective:
                continue
            check_conflict(row, "action_type")
            kind = row.get("action_type") or "bonus_split"
            require(
                kind in ("split", "bonus_split", "cash_dividend"),
                "action_type",
                "窗口内行动类型未获支持",
                subject=code,
                period=effective,
                evidence=[row],
                event=row.get("event_id"),
            )
            check_conflict(row, "same_share_basis")
            require(
                row.get("same_share_basis") is True or row.get("same_share_basis") is None,
                "same_share_basis",
                "现金未按每旧股归并",
                subject=code,
                period=effective,
                evidence=[row],
                event=row.get("event_id"),
            )
            check_conflict(row, "m")
            check_conflict(row, "d")
            multiple = 1.0 if kind == "cash_dividend" and row.get("m") is None else positive(number(row, "m"), "m")
            no_cash = row.get("action_type") in ("split", "bonus_split") and row.get("d") is None
            cash = 0.0 if no_cash else stock_share_amount(row, "d")
            original_events.append(row)
            events.append(
                dict(
                    code=code,
                    ex_date=effective,
                    action_type="bonus_split" if kind == "split" else kind,
                    m=multiple,
                    d=cash,
                )
            )
        dates = sorted(set(ends) | {event["ex_date"] for event in events})
        for day in dates:
            require(day in rows, "close_raw", "缺除权实施日收盘", subject=code, period=day)
        from hetu_quant.data import build_total_return_series

        bars = pd.DataFrame(
            [
                dict(code=code, date=day, close_raw=positive(stock_share_amount(rows[day], "close_raw"), "close_raw"))
                for day in dates
            ]
        )
        series = build_total_return_series(bars, pd.DataFrame(events))
        month_values = series.set_index("date")["close_total_return"].reindex(ends)
        values = month_values.pct_change(fill_method=None).iloc[1:].tolist()
        vol = float(np.std(values, ddof=1))
        return output(
            code,
            vol,
            "月度比例",
            dict(score=-vol, returns=values, month_ends=ends),
            [rows[end] for end in ends] + original_events + coverage,
        )

    return by_subject(facts, request, calculate)
