"""Window selection shared by formulas that use daily prices."""

import numpy as np

from .core import MissingFact, check_conflict, event_in_window, money_in_yuan, number, positive, quotation, require
from .sources import known_records


def sessions(facts, request, market=None, *, calendar_id=None):
    end = str(request.trade_date)

    def belongs(row):
        if calendar_id and row.get("calendar_id"):
            return row["calendar_id"] == calendar_id
        scope = market or calendar_id
        return not scope or row.get("market") in (None, "", scope)

    return sorted(
        {
            row["date"]
            for row in facts.records("calendar")
            if row["date"] <= end
            and row.get("is_trading_day") is True
            and belongs(row)
        }
    )


def daily(
    facts, request, code, count=None, *, previous=False, table="daily_bars",
    continuous=False, market=None, calendar_id=None,
):
    days = sessions(facts, request, market, calendar_id=calendar_id)
    require(days and days[-1] == str(request.trade_date), "calendar", "缺少当日交易日或窗口日历", subject=code)
    if previous:
        days = days[:-1]
    if count is not None:
        require(len(days) >= count, "calendar", f"至少需要 {count} 个交易日", subject=code)
        days = days[-count:]
    observed = {row["date"]: row for row in known_records(facts, table, request, code)}
    if table == "daily_bars":
        observed.update(
            {
                row["date"]: row
                for row in known_records(facts, "index_bars", request, code)
                if row["date"] not in observed and row.get("return_basis") in ("price", "price_return")
            }
        )
    if days:
        undocumented = sorted(day for day in observed if days[0] <= day <= days[-1] and day not in days)
        if undocumented:
            raise MissingFact("calendar", "窗口内有行情的日期缺交易日历凭据", subject=code, period=undocumented[0])
    for day in days:
        if day not in observed:
            suspended = any(
                row["date"] == day and row.get("is_suspended") is True for row in facts.records("security_status", code)
            )
            authorization = [entry for entry in facts.daily_authorizations if code in entry["codes"]]
            restricted = authorization and not any(entry["start"] <= day <= entry["end"] for entry in authorization)
            field = (
                "amount_cny"
                if request.factor_id in ("traded_amount", "market_amount", "market_amount_percentile")
                else "close_raw"
            )
            reason = "所需交易日超出产品授权期间" if restricted else ("已证停牌" if suspended else "交易日行情缺失")
            raise MissingFact(field, reason, subject=code, period=day, evidence=authorization if restricted else None)
    rows = [observed[day] for day in days]
    return continuous_prices(facts, request, code, rows) if continuous else rows


def yuan_amount(row, field="amount_cny"):
    value = number(row, field)
    currency = quotation(row, field, "currency")
    unit = quotation(row, field, "unit")
    return money_in_yuan(value, currency, unit, subject=row.get("code"), period=row.get("date"), evidence=[row])


def continuous_prices(facts, request, code, rows):
    if not rows or all(row.get("close") is not None for row in rows):
        return rows
    left, right = rows[0]["date"], rows[-1]["date"]
    coverage = [
        row
        for row in known_records(facts, "action_coverage", request, code)
        if (row.get("start") or "9999") <= left <= right <= (row.get("end") or "")
    ]
    require(coverage, "close", "实际窗口缺连续可比价格或行动覆盖凭据", subject=code, period=[left, right])
    splits = {}
    for event in known_records(facts, "corporate_actions", request, code):
        day = event_in_window(event, left, right)
        if day:
            check_conflict(event, "action_type")
            require(
                event.get("action_type") in (None, "cash_dividend", "bonus_split", "split"),
                "action_type",
                "窗口内行动缺连续价格处理依据",
                subject=code,
                period=day,
                evidence=[event],
            )
            splits[day] = splits.get(day, 1) * positive(number(event, "m"), "m")
    multiplier, adjusted = 1.0, []
    for row in rows:
        multiplier *= splits.get(row["date"], 1)
        values = {
            field: number(row, raw) * multiplier
            for field, raw in (("close", "close_raw"), ("high", "high_raw"), ("low", "low_raw"))
            if row.get(raw) is not None
        }
        adjusted.append(dict(row, **values, action_coverage=coverage))
    return adjusted


def prices(records, field="close"):
    return np.array([positive(number(row, field), field) for row in records], dtype=float)


def returns(values):
    return values[1:] / values[:-1] - 1
