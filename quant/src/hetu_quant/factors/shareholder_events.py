"""Observable shareholder events and distribution terms."""

from datetime import datetime

import pandas as pd

from .core import (
    CN,
    MissingFact,
    by_subject,
    check_conflict,
    event_in_window,
    iso_day,
    number,
    output,
    positive,
    require,
    stock_share_amount,
)
from .market_activity import _current, _scale_shares
from .series import daily
from .sources import select_distributions


def event_records(facts, code, kind, *, validate=True, effective_window=None, selected=None, conflict_fields=None):
    originals = facts.records("events", code) if selected is None else selected
    rows = [
        row for row in originals
        if row.get("kind") == kind or (not row.get("kind") and any(
            entry.get("kind", entry.get("value")) == kind and entry.get("revision_role") != "common_previous"
            for entry in (row.get("_conflicts") or {}).get("kind", ())
        ))
    ]
    if effective_window:
        start, end = effective_window
        scoped = []
        for row in rows:
            conflicts = row.get("_conflicts") or {}
            dates = [
                iso_day(item.get("value")) for item in conflicts.get("effective_date", ())
            ] or [iso_day(row.get("effective_date"))]
            if all(day and not start <= day <= end for day in dates):
                continue
            scoped.append(row)
        rows = scoped
    if validate:
        for row in rows:
            check_conflict(row, "kind")
            check_conflict(row, "event_identity")
            checked = row
            if conflict_fields is not None:
                conflicts = row.get("_conflicts") or {}
                checked = dict(row, _conflicts={field: conflicts[field] for field in conflict_fields
                                                if field in conflicts})
            check_conflict(checked, kind, any_field=True)
    return rows


def _event_outputs(facts, request, kind, rate, unit):
    rows, gaps = [], []
    for code in request.subjects:
        events = event_records(facts, code, kind)
        if event_id := request.parameters.get("event_id"):
            events = [row for row in events if row.get("event_id") == event_id]
        if not events:
            gaps.append(MissingFact(kind, "没有具备原公告和公开时点的对应事项", subject=code))
        for event in events:
            try:
                value, detail = rate(event)
                rows.append(
                    output(
                        code,
                        value,
                        unit,
                        dict(
                            detail,
                            event_id=event.get("event_id"),
                            published_at=event["available_at"],
                            effective_date=event.get("effective_date"),
                        ),
                        [event],
                    )
                )
            except MissingFact as error:
                error.subject = error.subject or code
                error.period = error.period or event.get("effective_date") or event["available_at"]
                gaps.append(error)
    return facts.result(request, rows, gaps)


def _category(row, field, options):
    value = row.get(field)
    require(value in options, field, "原件未披露可核对的类别")
    return options[value]


def buyback_plan(facts, request):
    statuses = {value: value for value in ("已披露", "已通过", "实施中", "已完成", "已撤销", "未通过")}
    return _event_outputs(
        facts, request, "buyback", lambda row: (_category(row, "state", statuses), dict(raw=row["state"])), "类别"
    )


def buyback_progress(facts, request):
    def calculate(row):
        basis = "plan_amount_upper" if row.get("plan_amount_upper") is not None else "plan_amount"
        paid = number(row, "paid")
        denominator = positive(number(row, basis), basis)
        require(row.get("currency") == "CNY", "currency", "计划及付款需同人民币金额口径")
        require(paid >= 0, "paid", "实付金额不能为负数")
        return paid / denominator, dict(paid=paid, plan_amount=denominator, basis=basis)

    return _event_outputs(facts, request, "buyback", calculate, "比例")


def buyback_price_cap(facts, request):
    def calculate(row):
        unit = row.get("price_unit")
        require(unit in ("分/股", "元/股") and row.get("currency") == "CNY", "price_unit", "价格币种或口径缺证")
        price = number(row, "price_cap")
        return price / (100 if unit == "分/股" else 1), dict(original=price, original_unit=unit)

    return _event_outputs(facts, request, "buyback", calculate, "元/股")


def buyback_cancellation_purpose(facts, request):
    options = {"注销并减少注册资本": "注销", "注销": "注销", "用于员工持股": "其他目的", "股权激励": "其他目的"}
    return _event_outputs(
        facts, request, "buyback", lambda row: (_category(row, "purpose", options), dict(raw=row["purpose"])), "类别"
    )


def _event_share(facts, request, kind):
    def calculate(code):
        events = event_records(facts, code, kind, validate=kind != "actual_holding")
        if kind == "actual_holding":
            start = str((pd.Timestamp(request.trade_date) - pd.Timedelta(days=request.parameters["days"])).date())
            events = [
                row
                for row in events
                if start
                <= datetime.fromisoformat(row["available_at"]).astimezone(CN).date().isoformat()
                <= str(request.trade_date)
            ]
            amounts = []
            for row in events:
                for attribute in ("kind", "event_identity", "direction"):
                    check_conflict(row, attribute)
                require(row.get("direction") in ("增持", "减持"), "direction", "实际持股变动方向缺证")
                amounts.append(_scale_shares(row, "shares") * (1 if row["direction"] == "增持" else -1))
            field = "actual_holding"
        else:
            day = str(request.trade_date)
            events = [row for row in events if row.get("effective_date", "9999") <= day <= (row.get("end") or "9999")]
            amounts = [
                0
                if row.get("state") == "已撤销"
                else max(0, _scale_shares(row, "plan_shares") - _scale_shares(row, "used_shares"))
                for row in events
            ]
            field = "sale_plan"
        require(events, field, "对应事项或实施状态缺证", subject=code)
        capital = _current(facts, request, "shares", code)
        share_base = positive(_scale_shares(capital, "float_shares"), "float_shares")
        return output(
            code, sum(amounts) / share_base, "比例", dict(changes=amounts, float_shares=share_base), [*events, capital]
        )

    return by_subject(facts, request, calculate)


def insider_net_purchase(facts, request):
    return _event_share(facts, request, "actual_holding")


def planned_sale_pressure(facts, request):
    return _event_share(facts, request, "sale_plan")


def holder_type(facts, request):
    options = {value: value for value in ("控股股东", "实际控制人", "董事", "监事", "高管", "员工", "机构", "其他")}
    return _event_outputs(
        facts,
        request,
        request.parameters.get("kind", "actual_holding"),
        lambda row: (_category(row, "holder_type", options), dict(raw=row["holder_type"])),
        "类别",
    )


def _distributions(facts, request, code, *, cash_window=None):
    # A composite can load trading actions and disclosures for different components.
    disclosures = [row for row in facts.versions.get("events", ()) if row.get("code") == code]
    if "corporate_actions" not in facts.versions and not disclosures:
        return facts.records("corporate_actions", code)
    actions = [row for row in facts.versions.get("corporate_actions", ()) if row.get("code") == code]
    return select_distributions(actions, disclosures, request.cutoff, str(request.trade_date), cash_window=cash_window)


def dividend_yield(facts, request):
    start = str((pd.Timestamp(request.trade_date) - pd.DateOffset(months=12)).date())
    end = str(request.trade_date)

    def calculate(code):
        raw = _distributions(facts, request, code, cash_window=(start, end))
        coverage = [
            row
            for row in facts.records("action_coverage", code)
            if row.get("start", "9999") <= start and row.get("end", "") >= str(request.trade_date)
        ]
        require(coverage, "action_coverage", "缺近十二月行动覆盖凭据", subject=code)
        actions = [row for row in raw if row.get("cash_window") == (start, end) or event_in_window(row, start, end)]
        for row in actions:
            check_conflict(row, "d")
            check_conflict(row, "same_share_basis")
        require(all(row.get("same_share_basis") is True for row in actions), "same_share_basis", "现金额股本口径不齐")
        cash = sum(stock_share_amount(row, "d") for row in actions)
        bar = daily(facts, request, code, 1)[0]
        close = positive(stock_share_amount(bar, "close_raw"), "close_raw")
        return output(code, cash / close, "比例", dict(cash=cash, close=close, start=start), [bar, *actions, *coverage])

    return by_subject(facts, request, calculate)


def dividend_persistence(facts, request):
    def calculate(code):
        years = sorted(facts.records("dividend_years", code), key=lambda row: row["period"], reverse=True)
        require(years and years[0]["period"] < request.trade_date.year, "dividend_years", "缺完整财年披露")
        streak, expected_year, used = 0, years[0]["period"], []
        for row in years:
            require(
                row["period"] == expected_year and row.get("complete") is True,
                "dividend_years",
                "财年披露覆盖不连续",
                period=str(expected_year),
            )
            used.append(row)
            if number(row, "cash_distributions") == 0:
                break
            streak += 1
            expected_year -= 1
        require(streak < len(years), "dividend_years", "连续分红起点尚无完整覆盖", period=str(expected_year))
        return output(code, streak, "财年", dict(years=[row["period"] for row in used]), used)

    return by_subject(facts, request, calculate)


def ex_distribution_return(facts, request):
    def calculate(code):
        start = str((pd.Timestamp(request.trade_date) - pd.Timedelta(days=1)).date())
        actions = [
            row for row in _distributions(facts, request, code) if event_in_window(row, start, str(request.trade_date))
        ]
        require(len(actions) == 1, "corporate_actions", "未证实当日唯一除权实施或合并条款")
        event = actions[0]
        check_conflict(event, "same_share_basis")
        require(
            event.get("same_share_basis") is True or event.get("same_share_basis") is None,
            "same_share_basis",
            "现金未按每旧股归并",
            subject=code,
            period=event["ex_date"],
            event=event.get("event_id"),
            evidence=[event],
        )
        bars = daily(facts, request, code, 2)
        previous = positive(stock_share_amount(bars[0], "close_raw"), "close_raw")
        current = positive(stock_share_amount(bars[1], "close_raw"), "close_raw")
        shares, dividend = positive(number(event, "m"), "m"), stock_share_amount(event, "d")
        raw, adjusted = current / previous - 1, (shares * current + dividend) / previous - 1
        return output(
            code,
            adjusted,
            "比例",
            dict(
                raw_return=raw,
                adjusted=adjusted,
                ratio=shares,
                cash=dividend,
                published_at=datetime.fromisoformat(event["available_at"]).isoformat(),
            ),
            [*bars, event],
        )

    return by_subject(facts, request, calculate)
