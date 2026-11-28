"""Paired log-price models and residual diagnostics."""

from datetime import timedelta
from statistics import NormalDist

import numpy as np

from .core import output, require, window
from .series import (
    _calendar,
    calendar_dependencies,
    daily,
    daily_observations,
    pair_calendar_dependencies,
    pair_sample_days,
    prices,
)
from .sources import calendar_market


def least_squares(design, target):
    coefficients, _, rank, _ = np.linalg.lstsq(design, target, rcond=None)
    require(rank == design.shape[1], "regression_rank", "解释变量秩不足")
    return coefficients, target - design @ coefficients


def line(x, y):
    coefficient, residuals = least_squares(np.column_stack([np.ones(len(x)), x]), y)
    return float(coefficient[0]), float(coefficient[1]), residuals


def _sample(facts, request, count, *, previous=False):
    require(len(request.subjects) == 2 and len(set(request.subjects)) == 2, "subjects", "按顺序指定两条不同序列")
    observation = str(request.trade_date)
    calendars = [_calendar(facts, request, calendar_market(code)) for code in request.subjects]
    observed = set().union(*(daily_observations(facts, request, code) for code in request.subjects))
    selected = pair_sample_days(calendars, observation, count, previous=previous, observed_days=observed)
    require(len(selected) == count, "calendar", f"共同交易日不足，需要 {count} 日，已有 {len(selected)} 日",
            period=observation)
    a, b = (
        daily(facts, request, code, continuous=True, sample_days=selected)
        for code in request.subjects
    )
    if facts.access:
        cutoff_day = request.trade_date - timedelta(days=1) if previous else request.trade_date
        groups = (group for calendar in calendars for group in calendar_dependencies(
            calendar, selected[0], str(cutoff_day)
        ))
        a[0] = dict(a[0], _workbench_calendar=pair_calendar_dependencies(groups))
    return np.log(prices(a)), np.log(prices(b)), a, b


def _result(facts, request, value, unit, detail, rows):
    return facts.result(request, [output(request.subjects, value, unit, detail, rows)])


def _spread(a, b, end, count):
    start = end - count
    alpha, beta, residual = line(b[start:end], a[start:end])
    spread = float(a[end] - alpha - beta * b[end])
    return alpha, beta, residual, spread


def _spread_result(facts, request, field):
    count = window(request, minimum=3)
    a, b, rows, reference = _sample(facts, request, count + 1)
    alpha, beta, residual, spread = _spread(a, b, count, count)
    detail = dict(alpha=alpha, beta=beta, spread=spread, fit_start=rows[0]["date"], fit_end=rows[-2]["date"])
    value, unit = (_z(spread, residual), "标准分") if field == "z" else (spread, "对数价差")
    return _result(facts, request, value, unit, detail, rows + reference)


def _z(spread, residual):
    deviation = float(residual.std(ddof=1))
    require(deviation > 1e-12, "spread_deviation", "训练残差方差为零")
    return float((spread - residual.mean()) / deviation)


def pair_hedge_beta(facts, request):
    count = window(request, minimum=3)
    a, b, rows, reference = _sample(facts, request, count, previous=True)
    alpha, beta, _ = line(b, a)
    detail = dict(alpha=alpha, beta=beta, fit_start=rows[0]["date"], fit_end=rows[-1]["date"])
    return _result(facts, request, beta, "系数", detail, rows + reference)


def pair_spread(facts, request):
    return _spread_result(facts, request, "spread")


def pair_spread_z(facts, request):
    return _spread_result(facts, request, "z")


def pair_cointegration(facts, request):
    count = window(request, minimum=5)
    lag = request.parameters["lag"]
    require(type(lag) is int and 0 <= lag and 2 * lag + 3 <= count, "lag", "协整样本无法支持该滞后阶数")
    a, b, rows, reference = _sample(facts, request, count)
    alpha, beta, residual = line(b, a)
    require(residual.std(ddof=1) > 1e-12, "residual", "两腿严格共线，残差单位根统计量未定义")
    difference = np.diff(residual)
    x = np.column_stack([residual[lag:-1], *(difference[lag - i : -i] for i in range(1, lag + 1))])
    fit, error = least_squares(x, difference[lag:])
    dof = len(error) - x.shape[1]
    scale = float(np.sqrt(error @ error / dof * np.linalg.inv(x.T @ x)[0, 0]))
    require(scale > 0, "residual", "单位根估计方差为零")
    statistic = float(fit[0] / scale)
    if statistic < -18.86:
        probability = 0.0
    elif statistic > 0.92:
        probability = 1.0
    else:
        coefficients = (2.92, 1.5012, 0.039796) if statistic <= -2.62 else (2.1945, 0.64695, -0.29198, -0.042377)
        probability = NormalDist().cdf(float(np.polynomial.polynomial.polyval(statistic, coefficients)))
    return _result(
        facts,
        request,
        probability,
        "p值",
        dict(statistic=statistic, alpha=alpha, beta=beta, lag=lag, fit_end=rows[-1]["date"]),
        rows + reference,
    )


def pair_half_life(facts, request):
    a, b, rows, reference = _sample(facts, request, window(request, minimum=4))
    _, _, residual = line(b, a)
    _, phi, _ = line(residual[:-1], residual[1:])
    require(
        0 < phi < 1,
        "phi",
        "只有 0<φ<1 的残差过程有该半衰期",
        evidence=[{"phi": phi, "dates": [row["date"] for row in rows]}],
    )
    return _result(facts, request, float(-np.log(2) / np.log(phi)), "交易日", dict(phi=phi), rows + reference)


def pair_beta_drift(facts, request):
    count = window(request, minimum=3)
    a, b, rows, reference = _sample(facts, request, 2 * count)
    _, before, _ = line(b[:count], a[:count])
    _, after, _ = line(b[count:], a[count:])
    return _result(facts, request, after - before, "系数", dict(before=before, after=after), rows + reference)


def pair_extreme_spread(facts, request):
    count = window(request, minimum=3)
    lookback = window(request, "lookback")
    a, b, rows, reference = _sample(facts, request, count + lookback)
    scores = []
    for i in range(count, len(a)):
        _, _, residual, spread = _spread(a, b, i, count)
        scores.append(dict(date=rows[i]["date"], z=_z(spread, residual)))
    return _result(
        facts, request, max(abs(row["z"]) for row in scores), "标准分", dict(scores=scores), rows + reference
    )
