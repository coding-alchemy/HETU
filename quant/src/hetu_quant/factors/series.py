"""Window selection shared by formulas that use daily prices."""

import numpy as np

from .core import MissingFact, check_conflict, event_in_window, money_in_yuan, number, positive, quotation, require
from .sources import calendar_market, calendar_records, calendar_witnesses, known_records, open_calendar_days


def _calendar(facts, request, market, calendar_id=None):
    rows = facts.versions.get("calendar")
    rows = (calendar_records(rows, request.cutoff, facts.calendar_revision_selections)
            if rows is not None else facts.records("calendar"))
    return calendar_witnesses(
        (row for row in rows if row["date"] <= str(request.trade_date)), market, calendar_id=calendar_id,
    )


def sessions(facts, request, market=None, *, calendar_id=None):
    return sorted(open_calendar_days(_calendar(facts, request, market, calendar_id)))


def calendar_dependencies(calendar, start, end):
    return [
        [dict(row, _workbench_fields=("date", "is_trading_day")) for row in rows]
        for day, rows in sorted(calendar.items())
        if start <= day <= end and len({row.get("is_trading_day") for row in rows}) == 1
    ]


def pair_calendar_dependencies(groups):
    """A proven closure on either leg suffices to rule out a common session."""
    days = {}
    for witnesses in groups:
        days.setdefault(witnesses[0]["date"], []).append(witnesses)
    required = []
    for alternatives in days.values():
        closed = [row for witnesses in alternatives for row in witnesses if row["is_trading_day"] is False]
        required.extend([closed] if closed else alternatives)
    return required


def require_consistent_calendar(calendar, start, end, subject):
    for day, witnesses in sorted(calendar.items()):
        if start <= day <= end and len({row.get("is_trading_day") for row in witnesses}) > 1:
            raise MissingFact("calendar", "窗口内交易日历冲突", subject=subject, period=day)


def pair_sample_days(calendars, observation, count, *, previous=False, observed_days=()):
    """Walk backward until the most recent common sessions are proved or history is exhausted."""
    result = []
    candidates = set(observed_days).union(*(calendar.keys() for calendar in calendars))
    for day in sorted(candidates, reverse=True):
        if day > observation or (previous and day == observation):
            continue
        flags = [{row.get("is_trading_day") for row in calendar.get(day, ())} for calendar in calendars]
        if {False} in flags:
            continue
        if all(state == {True} for state in flags):
            result.append(day)
            if len(result) == count:
                break
        else:
            raise MissingFact("calendar", "可能影响共同取样日的交易日历未证", period=day)
    if not previous:
        require(result and result[0] == observation, "calendar", "观察日缺两腿共同交易日历", period=observation)
    return list(reversed(result))


def is_price_index_quote(row):
    return row.get("close") is not None and row.get("return_basis") in ("price", "price_return")


def daily_observations(facts, request, code, *, table="daily_bars"):
    observed = {row["date"]: row for row in known_records(facts, table, request, code)}
    if table == "daily_bars":
        observed.update(
            {
                row["date"]: row
                for row in known_records(facts, "index_bars", request, code)
                if row["date"] not in observed and is_price_index_quote(row)
            }
        )
    return observed


def require_calendar_for_observations(calendar, observed, start, end, code):
    undocumented = sorted(
        day for day in observed if start <= day <= end
        and {row.get("is_trading_day") for row in calendar.get(day, ())} not in ({True}, {False})
    )
    if undocumented:
        raise MissingFact("calendar", "窗口内有行情的日期缺交易日历凭据", subject=code, period=undocumented[0])


def daily(
    facts, request, code, count=None, *, previous=False, table="daily_bars",
    continuous=False, market=None, calendar_id=None, sample_days=None,
):
    market = market or calendar_market(code)
    calendar = _calendar(facts, request, market, calendar_id)
    open_days = open_calendar_days(calendar)
    days = sorted(open_days)
    if sample_days is None:
        require(days and days[-1] == str(request.trade_date), "calendar", "缺少当日交易日或窗口日历", subject=code)
    if previous:
        days = days[:-1]
    if count is not None:
        require(len(days) >= count, "calendar", f"至少需要 {count} 个交易日", subject=code)
        days = days[-count:]
    if sample_days is not None:
        require(set(sample_days) <= open_days, "calendar", "配对取样日缺交易日历凭据", subject=code)
        days = sample_days
    observed = daily_observations(facts, request, code, table=table)
    if days and sample_days is None:
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
            if field in facts.daily_proof_denials.get((code, day), ()):
                raise MissingFact(field, f"{field} 补证字段未获准", subject=code, period=day)
            reason = "所需交易日超出产品授权期间" if restricted else ("已证停牌" if suspended else "交易日行情缺失")
            raise MissingFact(field, reason, subject=code, period=day, evidence=authorization if restricted else None)
    rows = []
    for day in days:
        row = observed[day]
        if denied := facts.daily_proof_denials.get((code, day)):
            row = dict(row, _workbench_denied_proofs=tuple(sorted(denied)))
        rows.append(row)
    if rows and sample_days is None:
        require_consistent_calendar(calendar, days[0], str(request.trade_date), code)
        if facts.access:
            rows[0] = dict(
                rows[0], _workbench_calendar=calendar_dependencies(calendar, days[0], str(request.trade_date))
            )
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
    splits, actions = {}, {}
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
            actions.setdefault(day, []).append(event)
    multiplier, adjusted, applied = 1.0, [], []
    action_days = iter(sorted(actions))
    next_action = next(action_days, None)
    for row in rows:
        while next_action is not None and next_action <= row["date"]:
            applied.extend(actions[next_action])
            multiplier *= splits[next_action]
            next_action = next(action_days, None)
        values = {
            field: number(row, raw) * multiplier
            for field, raw in (("close", "close_raw"), ("high", "high_raw"), ("low", "low_raw"))
            if row.get(raw) is not None
        }
        adjusted.append(dict(row, **values, action_coverage=coverage,
                             applied_actions=list(applied)))
    return adjusted


def prices(records, field="close"):
    return np.array([positive(number(row, field), field) for row in records], dtype=float)


def returns(values):
    return values[1:] / values[:-1] - 1


def require_aligned_return_intervals(code, benchmark, intervals, reference):
    """Require both legs' original windows to supply the same return intervals."""
    if intervals == reference:
        return
    own, base = set(intervals), set(reference)
    left, right = max(own - base), max(base - own)
    raise MissingFact(
        "return_interval",
        f"同起讫日收益区间不足，需要 {len(intervals)} 个，已有 {len(own & base)} 个。"
        f"{code} {left[0]} → {left[1]}，基准 {benchmark} {right[0]} → {right[1]}",
        subject=f"{code}, {benchmark}",
        period=max(left[1], right[1]),
    )
