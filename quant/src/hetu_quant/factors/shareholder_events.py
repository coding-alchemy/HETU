"""Observable shareholder events and distribution terms."""

from collections import defaultdict
from datetime import datetime

import pandas as pd

from .core import (
    CN,
    MissingFact,
    by_subject,
    check_conflict,
    check_revision_cycle,
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
from .sources import _BUSINESS_DATES, _event_publication_identities, _merge_live, select_distributions, select_versions


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


def _event_outputs(facts, request, kind, rate, unit, *, conflict_fields=None, selected=None, project=None):
    rows, gaps = [], []
    for code in request.subjects:
        originals = None if selected is None else [row for row in selected if row.get("code") == code]
        events = event_records(facts, code, kind, conflict_fields=conflict_fields, selected=originals)
        if event_id := request.parameters.get("event_id"):
            events = [row for row in events if row.get("event_id") == event_id]
        if not events:
            gaps.append(MissingFact(kind, "没有具备原公告和公开时点的对应事项", subject=code))
        for event in events:
            try:
                value, detail = rate(event)
                row = output(
                    code, value, unit,
                    dict(detail, event_id=event.get("event_id"),
                         published_at=event["available_at"], effective_date=event.get("effective_date")),
                    [event],
                )
                rows.append(project(row) if project else row)
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


_PROGRESS_DENOMINATORS = {"plan_amount_upper", "plan_amount"}
_PROGRESS_ORIGIN = {
    *_BUSINESS_DATES, "code", "available_at", "published_at",
    "announcement_ref", "source_ref", "evidence_ref", "coordinate", "fact_key", "record_key",
    "registered_table", "revision_id", "supersedes", "snapshot_id", "source_root",
    "product_id", "registration_product_id",
}


def _progress_event_evidence(row, fields, *, requires_plan_type=False):
    proof = {key: row[key] for key in _PROGRESS_ORIGIN | fields if key in row}
    if "field_evidence" in row:
        proof["field_evidence"] = {
            field: item for field, item in (row["field_evidence"] or {}).items() if field in fields
        }
    if "corroborating_sources" in row:
        proof["corroborating_sources"] = [
            _progress_event_evidence(origin, fields) for origin in row["corroborating_sources"] or ()
        ]
    if "_workbench_fields" in row:
        used = fields & set(row["_workbench_fields"] or ())
        if requires_plan_type:
            used.add("plan_amount_basis")
        proof["_workbench_fields"] = tuple(sorted(used))
    return proof


def _progress_fields(basis):
    return {"event_id", "kind", "paid", "currency", "plan_amount_basis", basis}


def buyback_progress_evidence(result_row):
    """Project a completed row using its recorded calculation rather than a new read."""
    basis = result_row["components"].get("basis")
    if basis not in _PROGRESS_DENOMINATORS:
        return result_row
    evidence = []
    for row in result_row["inputs"]:
        original = row.get("_workbench_buyback_input") or row
        if parts := original.get("_workbench_buyback_parts"):
            evidence.extend(parts)
            continue
        evidence.append(_progress_event_evidence(original, _progress_fields(basis), requires_plan_type=True))
    return dict(result_row, inputs=evidence)


def buyback_progress_inputs(row):
    """Select the authorized terms needed for this event's particular denominator."""
    coordinate = dict(event=row.get("event_id"), subject=row.get("code"),
                      period=row.get("effective_date") or row.get("available_at"))
    for field in ("kind", "event_identity", "plan_amount_basis"):
        check_conflict(row, field)
    original = row.get("_workbench_buyback_input") or row

    def authorized(field, *, suffix=""):
        allowed = original.get("_workbench_allowed_fields")
        require(
            allowed is None or field in allowed,
            field, f"{field} 补证字段未获准{suffix}",
            **coordinate, evidence=[_progress_event_evidence(original, {"kind", "event_id"})],
        )

    authorized("plan_amount_basis", suffix="，分母待证")
    authorized("paid")
    check_revision_cycle(row, "paid")
    check_conflict(row, "paid")
    paid = number(original, "paid")
    fixed = original.get("plan_amount_basis") == "fixed"
    basis = "plan_amount" if fixed else "plan_amount_upper"
    authorized(basis)
    check_revision_cycle(row, basis)
    check_conflict(row, basis)
    require(
        original.get(basis) is not None, basis,
        "固定金额方案缺计划金额原件" if fixed else "分母待证：缺区间上限或可证的固定金额方案",
        **coordinate, evidence=[_progress_event_evidence(original, {"kind", "event_id", "plan_amount_basis"})],
    )
    denominator = positive(number(original, basis), basis)
    authorized("currency")
    check_conflict(row, "currency")
    require(original.get("currency") == "CNY", "currency", "计划及付款需同人民币金额口径", **coordinate)
    require(paid >= 0, "paid", "实付金额不能为负数", **coordinate)
    return dict(paid=paid, plan_amount=denominator, basis=basis, currency=original["currency"])


def merge_buyback_versions(versions):
    """An independent, complete original can supply progress without a cropped copy."""
    def complete_candidates(originals):
        for original in originals:
            try:
                yield original, buyback_progress_inputs(original)["basis"]
            except MissingFact:
                continue

    merged = _merge_live(versions)
    complete = list(complete_candidates(versions))
    type_restricted = None
    if not complete:
        by_announcement = defaultdict(list)
        for row in versions:
            for identity in _event_publication_identities(row):
                by_announcement[identity].append(row)
        for linked in by_announcement.values():
            if len(linked) < 2:
                continue
            scopes = [row.get("_workbench_allowed_fields") for row in linked]
            allowed = None if any(scope is None for scope in scopes) else set().union(*scopes)
            candidate = dict(_merge_live(linked), _workbench_allowed_fields=allowed)
            implicit_terms = None
            if candidate.get("plan_amount_basis") is None:
                # Only a terms record can establish that its plan type is absent.
                field = "plan_amount_upper" if candidate.get("plan_amount_upper") is not None else "plan_amount"
                terms = [i for i, row in enumerate(linked) if row.get(field) is not None]
                if terms:
                    terms_with_type_access = next(
                        (i for i in terms if scopes[i] is None or "plan_amount_basis" in scopes[i]), None,
                    )
                    if terms_with_type_access is None:
                        type_restricted = linked[terms[0]]
                        continue
                    if field == "plan_amount_upper":
                        implicit_terms = terms_with_type_access
            for original, basis in complete_candidates([candidate]):
                assigned = defaultdict(set)
                for field in _progress_fields(basis):
                    if implicit_terms is not None and field in ("plan_amount_basis", "plan_amount_upper"):
                        assigned[implicit_terms].add(field)
                    elif (index := next((i for i, source in enumerate(linked) if source.get(field) is not None),
                                        None)) is not None:
                        assigned[index].add(field)
                parts = [
                    _progress_event_evidence(
                        linked[i], fields | {"kind", "event_id"}, requires_plan_type="plan_amount_basis" in fields,
                    )
                    for i, fields in assigned.items()
                ]
                stamp = max((origin["available_at"] for origin in parts), key=datetime.fromisoformat)
                complete.append((dict(original, available_at=stamp, _workbench_buyback_parts=parts), basis))

    if not complete:
        if type_restricted is not None:
            return dict(merged, _workbench_buyback_input=type_restricted)
        # A bookkeeping ID identifies the group but is not evidence that numbers from
        # unrelated partial records can be combined. A single original still diagnoses a missing term.
        try:
            buyback_progress_inputs(dict(merged, _workbench_allowed_fields=None))
        except MissingFact:
            return dict(merged, _workbench_buyback_input=versions[0])
        conflicts = [_progress_event_evidence(row, {"kind", "event_id"}) for row in versions]
        return dict(merged, _conflicts={**(merged.get("_conflicts") or {}), "event_identity": conflicts})

    original = complete[0][0]
    if len({basis for _, basis in complete}) > 1:
        conflicts = [
            dict(
                _progress_event_evidence(row, {"event_id", "kind", "plan_amount_basis", basis}),
                value="fixed" if basis == "plan_amount" else "range",
            )
            for row, basis in complete
        ]
        merged["_conflicts"] = {**(merged.get("_conflicts") or {}), "plan_amount_basis": conflicts}
    return dict(merged, available_at=original["available_at"], _workbench_buyback_input=original)


def buyback_progress(facts, request):
    def calculate(row):
        detail = buyback_progress_inputs(row)
        return detail["paid"] / detail["plan_amount"], detail

    originals = facts.versions.get("events")
    selected = (None if originals is None else
                select_versions(originals, request.cutoff, table="events", buyback_progress=True))
    return _event_outputs(
        facts, request, "buyback", calculate, "比例",
        conflict_fields=(), selected=selected, project=buyback_progress_evidence,
    )


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
