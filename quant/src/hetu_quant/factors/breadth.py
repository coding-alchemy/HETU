"""Point-in-time market breadth, liquidity aggregates, and price limits."""

from dataclasses import replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from .core import MissingFact, by_subject, check_conflict, close_cutoff, number, output, positive, require, window
from .cross_section import percentile, scope
from .market_activity import _scale_shares
from .series import daily, prices, returns, sessions, yuan_amount
from .sources import known_records


def _scope_data(facts, request, group, measure):
    members, membership, complete = scope(facts, request, group)
    measured, gaps, sources = {}, [], list(membership)
    for code in members:
        try:
            value, evidence = measure(code)
            measured[code] = value
            sources.extend(evidence)
        except MissingFact as error:
            error.subject = error.subject or code
            gaps.append(error)
    require(measured, "eligible_members", "没有可比成员", subject=group)
    if not complete:
        gaps.append(MissingFact("memberships", "原池分母未证，仅展示已证子集", subject=group))
    details = dict(
        members=list(members),
        eligible=list(measured),
        denominator=len(measured),
        full_scope=complete and len(measured) == len(members),
    )
    return measured, details, sources, gaps


def _groups(facts, request, measure, aggregate, unit):
    rows, issues = [], []
    for group in request.subjects:
        try:
            observed, details, sources, gaps = _scope_data(facts, request, group, measure)
            value, extra = aggregate(observed)
            rows.append(output(group, value, unit, dict(details, **extra), sources))
            issues.extend(gaps)
        except MissingFact as error:
            error.subject = error.subject or group
            issues.append(error)
    return facts.result(request, rows, issues)


def _breadth(facts, request, kind):
    count = window(request) if kind in ("high", "low", "ma") else 1

    def measure(code):
        rows = daily(facts, request, code, count if kind == "ma" else count + 1, continuous=True)
        p = prices(rows)
        reference = {
            "up": lambda: p[-2],
            "down": lambda: p[-2],
            "high": lambda: max(p[:-1]),
            "low": lambda: min(p[:-1]),
            "ma": lambda: p.mean(),
        }[kind]()
        return bool(p[-1] > reference if kind in ("up", "high", "ma") else p[-1] < reference), rows

    ratio = kind in ("up", "down", "ma")

    def aggregate(observed):
        yes = [k for k, value in observed.items() if value]
        return (len(yes) / len(observed) if ratio else len(yes)), dict(matches=yes, count=len(yes))

    return _groups(facts, request, measure, aggregate, "比例" if ratio else "家")


def breadth_advance_share(facts, request):
    return _breadth(facts, request, "up")


def breadth_decline_share(facts, request):
    return _breadth(facts, request, "down")


def breadth_new_high_count(facts, request):
    return _breadth(facts, request, "high")


def breadth_new_low_count(facts, request):
    return _breadth(facts, request, "low")


def breadth_above_ma_share(facts, request):
    return _breadth(facts, request, "ma")


def _bands(facts, code, day, side):
    limits = [item for item in facts.records("price_limits", code) if item.get("date") == day]
    if len(limits) == 1:
        rule = limits[0]
        check_conflict(rule, "unlimited")
        require(rule.get("unlimited") is not True, "price_limits", "当日已证不设涨跌幅限制", subject=code, period=day)
        check_conflict(rule, side)
        if rule.get(side) is not None:
            return number(rule, side), rule
    else:
        rules = [
            item
            for item in facts.records("trading_rules", code)
            if item.get("valid_from", "9999") <= day <= (item.get("valid_to") or "9999")
            and item.get("date", day) == day
        ]
        require(len(rules) == 1, "price_limits", "缺当日可知限价或唯一适用规则", subject=code, period=day)
        rule = rules[0]
        require(rule.get("unlimited") is not True, "price_limits", "当日已证不设涨跌幅限制", subject=code, period=day)
    # The authoritative reference is per session and may differ from the raw previous close on an ex-date.
    reference = Decimal(str(positive(number(rule, "limit_reference"), "limit_reference")))
    tick = Decimal(str(positive(number(rule, "tick_size"), "tick_size")))
    pct = Decimal(str(number(rule, "limit_pct")))
    require(0 < pct < 1, "limit_pct", "适用涨跌幅无有效值", subject=code, period=day)
    sign = 1 if side == "upper" else -1
    price = (reference * (1 + sign * pct) / tick).quantize(Decimal(1), rounding=ROUND_HALF_UP) * tick
    return float(price), rule


def _limit(facts, code, row, kind):
    day = row["date"]
    side = "lower" if kind == "down" else "upper"
    band, proof = _bands(facts, code, day, side)
    close = number(row, "close_raw")
    high = number(row, "high_raw") if kind == "failed" else None
    sealed = abs(close - band) < 1e-8
    return (
        sealed if side == "upper" else False,
        sealed if side == "lower" else False,
        high is not None and high >= band - 1e-8,
    ), proof


def _limit_aggregate(facts, request, kind):
    def measure(code):
        count = window(request) if kind == "streak" else 1
        bars = daily(facts, request, code, count)
        flags = [_limit(facts, code, row, kind) for row in bars]
        if kind == "streak":
            streak = 0
            for (up, _, _), _ in reversed(flags):
                if not up:
                    break
                streak += 1
            require(streak < count, "window", "连板起点在已证窗口之外", subject=code)
            return streak, bars + [source for _, source in flags]
        return flags[0][0], bars + [flags[0][1]]

    def aggregate(observed):
        if kind == "streak":
            distribution = {}
            for streak in observed.values():
                if streak:
                    distribution[str(streak)] = distribution.get(str(streak), 0) + 1
            return distribution, dict(streaks=observed)
        if kind == "failed":
            touched = [code for code, (_, _, hit) in observed.items() if hit]
            failed = [code for code in touched if not observed[code][0]]
            return len(failed) / positive(len(touched), "touched_count"), dict(touched=touched, failed=failed)
        codes = [code for code, value in observed.items() if value[0 if kind == "up" else 1]]
        return len(codes), dict(matches=codes)

    return _groups(facts, request, measure, aggregate, "比例" if kind == "failed" else "家")


def limit_up_count(facts, request):
    return _limit_aggregate(facts, request, "up")


def limit_down_count(facts, request):
    return _limit_aggregate(facts, request, "down")


def limit_streak_distribution(facts, request):
    return _limit_aggregate(facts, request, "streak")


def failed_limit_up_share(facts, request):
    return _limit_aggregate(facts, request, "failed")


def _liquidity(facts, request, group, turnover):
    def measure(code):
        row = daily(facts, request, code, 1)[0]
        if not turnover:
            return (yuan_amount(row), 1), [row]
        shares = [r for r in known_records(facts, "shares", request, code) if r["date"] == str(request.trade_date)]
        require(len(shares) == 1, "float_shares", "缺该日可交易流通股本", subject=code)
        return (
            _scale_shares(row, "volume_shares"),
            positive(_scale_shares(shares[0], "float_shares"), "float_shares"),
        ), [row, shares[0]]

    observed, details, sources, issues = _scope_data(facts, request, group, measure)
    numerator = sum(value[0] for value in observed.values())
    denominator = sum(value[1] for value in observed.values()) if turnover else 1
    return numerator / denominator, dict(details, numerator=numerator, base=denominator), sources, issues


def _market_liquidity(facts, request, turnover=False, ranked=False):
    rows, issues = [], []
    for group in request.subjects:
        try:
            dates = [str(request.trade_date)]
            if ranked:
                count = window(request)
                dates = sessions(facts, request)[-count:]
                require(len(dates) == count, "calendar", "历史分位窗口交易日不足")
            values, details, origins = [], [], []
            for day in dates:
                current = date.fromisoformat(day)
                historical = (
                    replace(request, trade_date=current, cutoff=min(request.cutoff, close_cutoff(current)))
                    if ranked
                    else request
                )
                value, parts, inputs, gaps = _liquidity(facts, historical, group, turnover)
                values.append(value)
                details.append(dict(parts, date=day))
                origins.extend(inputs)
                issues.extend(gaps)
            if ranked:
                require(all(part["full_scope"] for part in details), "memberships", "历史分位需要各期完整范围")
            output_value = float(percentile(values)[-1]) if ranked else values[-1]
            rows.append(
                output(
                    group,
                    output_value,
                    "百分点" if ranked else "比例" if turnover else "人民币元",
                    dict(history=values, coverage=details),
                    origins,
                )
            )
        except MissingFact as error:
            issues.append(error)
    return facts.result(request, rows, issues)


def market_amount(facts, request):
    return _market_liquidity(facts, request)


def market_turnover(facts, request):
    return _market_liquidity(facts, request, turnover=True)


def market_amount_percentile(facts, request):
    return _market_liquidity(facts, request, ranked=True)


def market_turnover_percentile(facts, request):
    return _market_liquidity(facts, request, turnover=True, ranked=True)


def index_contribution_gap(facts, request):
    count = window(request, "top")
    days = sessions(facts, request)
    require(len(days) >= 2, "calendar", "缺 T−1 指数权重适用交易日")

    def calculate(group):
        codes, members, complete = scope(facts, request, group, day=days[-2])
        require(complete, "memberships", "贡献计算需要 T−1 的独立原池覆盖")
        weights = {code: number(next(row for row in members if row["member"] == code), "weight") for code in codes}
        require(0 < count < len(codes) and abs(sum(weights.values()) - 1) < 1e-8, "weight", "权重或分组数量不完整")
        ordered = sorted(codes, key=lambda code: (-weights[code], code))
        inputs = list(members)
        changes = {}
        for code in ordered:
            bars = daily(facts, request, code, 2, continuous=True)
            changes[code] = float(returns(prices(bars))[0])
            inputs.extend(bars)
        top = sum(weights[code] * changes[code] for code in ordered[:count])
        rest = sum(weights[code] * changes[code] for code in ordered[count:])
        return output(
            group,
            top - rest,
            "比例",
            dict(
                top=top,
                rest=rest,
                weights=weights,
                returns=changes,
                advance_share=sum(change > 0 for change in changes.values()) / len(ordered),
                top_codes=ordered[:count],
            ),
            inputs,
        )

    return by_subject(facts, request, calculate)
