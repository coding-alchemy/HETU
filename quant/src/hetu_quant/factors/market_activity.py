"""Trading activity calculations."""

from .core import MissingFact, by_subject, check_revision_cycle, number, output, positive, require, window
from .core import share_count as _scale_shares
from .series import daily, yuan_amount
from .sources import known_records


def up_amount_share(facts, request):
    count = window(request)

    def calculate(code):
        records = daily(facts, request, code, count + 1, continuous=True)
        observations = []
        field = "close"
        for previous, current in zip(records[:-1], records[1:], strict=True):
            close = positive(number(current, field), field)
            prior = positive(number(previous, field), field)
            amount = yuan_amount(current)
            require(amount >= 0, "amount_cny", "成交额不得为负", period=current["date"])
            observations.append(
                dict(date=current["date"], previous_close=prior, close=close, up=close > prior, amount=amount)
            )
        numerator = sum(row["amount"] for row in observations if row["up"])
        denominator = positive(sum(row["amount"] for row in observations), "amount_cny")
        return output(
            code,
            numerator / denominator,
            "比例",
            dict(up_amount=numerator, total_amount=denominator, observations=observations, close_basis=field),
            records,
        )

    return by_subject(facts, request, calculate)


def _current(facts, request, table, code):
    rows = [row for row in known_records(facts, table, request, code) if row.get("date") == str(request.trade_date)]
    require(len(rows) == 1, table, "缺所选交易日的对应记录", subject=code)
    return rows[0]


def _market_cap(facts, request, free_float):
    def calculate(code):
        facts_rows = [r for r in facts.records("market_cap", code) if r.get("date") == str(request.trade_date)]
        cap_field = "free_float_market_cap_cny" if free_float else "market_cap_cny"
        cycle = None
        if len(facts_rows) == 1:
            cap = facts_rows[0]
            try:
                check_revision_cycle(cap, cap_field)
            except MissingFact as error:
                cycle = error
            else:
                if cap.get(cap_field) is not None:
                    return output(code, positive(number(cap, cap_field), cap_field), "人民币元",
                                  dict(basis=cap_field), facts_rows)
        try:
            shares = _current(facts, request, "shares", code)
            field = "free_float_shares" if free_float else "total_shares"
            if free_float:
                require(shares.get("free_float_definition"), "free_float_definition", "缺自由流通股本定义")
            share_count = positive(_scale_shares(shares, field), field)
            row = _current(facts, request, "daily_bars", code)
            price = positive(number(row, "close_raw"), "close_raw")
        except MissingFact as error:
            if cycle is not None:
                raise cycle from error
            raise
        return output(code, share_count * price, "人民币元", dict(shares=share_count, close_raw=price), [shares, row])

    return by_subject(facts, request, calculate)


def total_market_cap(facts, request):
    return _market_cap(facts, request, False)


def free_float_market_cap(facts, request):
    return _market_cap(facts, request, True)


def turnover_rate(facts, request):
    def calculate(code):
        bar = _current(facts, request, "daily_bars", code)
        shareholders = _current(facts, request, "shares", code)
        traded = _scale_shares(bar, "volume_shares")
        shares = positive(_scale_shares(shareholders, "float_shares"), "float_shares")
        return output(
            code, traded / shares, "比例", dict(volume_shares=traded, float_shares=shares), [bar, shareholders]
        )

    return by_subject(facts, request, calculate)


def amihud_illiquidity(facts, request):
    from .series import prices, returns

    count = window(request)
    multiplier = positive(number(request.parameters, "multiplier"), "multiplier")

    def calculate(code):
        rows = daily(facts, request, code, count + 1, continuous=True)
        changes = returns(prices(rows))
        amounts = [positive(yuan_amount(row), "amount_cny") for row in rows[1:]]
        values = [float(abs(change) / amount * multiplier) for change, amount in zip(changes, amounts, strict=True)]
        return output(code, sum(values) / len(values), "每人民币元", dict(daily_values=values), rows)

    return by_subject(facts, request, calculate)


def traded_amount(facts, request):
    from statistics import mean, median

    mode = request.parameters["mode"]
    require(mode in ("daily", "mean", "median", "sum"), "mode", "未知成交额统计口径")
    count = 1 if mode == "daily" else window(request)

    def calculate(code):
        rows = daily(facts, request, code, count)
        values = [yuan_amount(row) for row in rows]
        require(all(value >= 0 for value in values), "amount_cny", "成交额不能为负")
        statistic = {"daily": sum, "sum": sum, "mean": mean, "median": median}[mode]
        return output(code, statistic(values), "人民币元", dict(mode=mode, count=count), rows)

    return by_subject(facts, request, calculate)
