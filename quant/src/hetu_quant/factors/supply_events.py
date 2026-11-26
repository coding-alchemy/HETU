"""Index changes, share unlocks, and visible outcomes of corporate events."""

from datetime import timedelta

from .core import MissingFact, by_subject, iso_day, number, output, positive, require
from .market_activity import _scale_shares
from .shareholder_events import _event_outputs, event_records
from .sources import window_event_versions


def index_rebalance_change(facts, request):
    def calculate(row):
        if row.get("old_weight") is not None and row.get("new_weight") is not None:
            before, after = number(row, "old_weight"), number(row, "new_weight")
            return after - before, dict(old_weight=before, new_weight=after)
        direction = row.get("direction")
        require(direction in ("纳入", "剔除"), "direction", "缺权重及可核对的调整方向")
        return direction, dict(weight_known=False)

    return _event_outputs(facts, request, "index_rebalance", calculate, "比例或类别")


def unlock_supply_pressure(facts, request):
    start = str(request.trade_date)
    end = str(request.trade_date + timedelta(days=request.parameters["days"]))

    def measure(code, selected):
        scoped = event_records(
            facts, code, "unlock", effective_window=(start, end), selected=selected,
            conflict_fields=("revision_cycle", "event_id", "effective_date", "unlock_shares", "unlock_shares_unit",
                             "prior_float", "prior_float_unit"),
        )
        for row in scoped:
            require(iso_day(row.get("effective_date")), "effective_date", "解禁实施日缺证，可能落在查询窗口",
                    subject=code, period=[start, end], evidence=[row])
        if not scoped:
            coverage = [
                row
                for row in facts.records("event_coverage", code)
                if row.get("kind") == "unlock" and row.get("start", "9999") <= start <= end <= row.get("end", "")
            ]
            require(coverage, "event_coverage", "窗内无事件且解禁查询范围未证")
            return output(code, 0, "比例", dict(start=start, end=end), coverage)
        amounts = [
            (_scale_shares(row, "unlock_shares"), positive(_scale_shares(row, "prior_float"), "prior_float"))
            for row in scoped
        ]
        require(len({row[1] for row in amounts}) == 1, "prior_float", "各事项事件前股本基数不同，请分别查询")
        return output(
            code,
            sum(item[0] for item in amounts) / amounts[0][1],
            "比例",
            dict(start=start, end=end, changes=amounts),
            scoped,
        )

    def calculate(code):
        if "events" not in facts.versions:
            return measure(code, None)
        variants, field, evidence = window_event_versions(
            [row for row in facts.versions["events"] if row.get("code") == code],
            request.cutoff, "unlock", (start, end),
        )
        result, amounts = None, set()
        for variant in variants:
            try:
                calculated = measure(code, variant)
            except MissingFact as gap:
                if evidence:
                    originals = gap.evidence if isinstance(gap.evidence, list) else []
                    gap.evidence = [*originals, *(row for row in evidence if row not in originals)]
                raise
            amounts.add(calculated["value"])
            if result is None:
                result = calculated
        require(len(amounts) == 1, field, "有效公告分支改变窗内解禁供给", subject=code,
                period=[start, end], evidence=evidence)
        if evidence:
            result["components"]["revision_choices"] = evidence
        return result

    return by_subject(facts, request, calculate)


def unlock_holder_mix(facts, request):
    rows, gaps = [], []
    for code in request.subjects:
        for event in event_records(facts, code, "unlock"):
            try:
                total = positive(_scale_shares(event, "unlock_shares"), "unlock_shares")
                breakdown = [
                    r
                    for r in facts.records("event_holders", code)
                    if r.get("event_id") == event.get("event_id") and r.get("holder_type")
                ]
                require(breakdown, "holder_type", "缺已披露解禁股东类别")
                counts = {}
                for item in breakdown:
                    key = item["holder_type"]
                    counts[key] = counts.get(key, 0) + _scale_shares(item, "shares")
                unexplained = total - sum(counts.values())
                require(unexplained >= 0, "unlock_shares", "股东分类合计大于当批总数")
                if unexplained:
                    gaps.append(MissingFact("holder_type", f"{unexplained:g} 股类别未披露", subject=code))
                rows.append(
                    output(
                        code,
                        {k: value / total for k, value in counts.items()},
                        "比例",
                        dict(total_shares=total, explained=counts, unknown_shares=unexplained),
                        [event, *breakdown],
                    )
                )
            except MissingFact as error:
                gaps.append(error)
    if not rows and not gaps:
        gaps.append(MissingFact("unlock", "无已知解禁事项及原件"))
    return facts.result(request, rows, gaps)


def corporate_event_type(facts, request):
    def calculate(row):
        kind, stage = row.get("event_type"), row.get("stage")
        require(kind and stage, "event_type/stage", "原件未确认事项类型或阶段")
        return f"{kind}／{stage}", dict(type=kind, stage=stage)

    return _event_outputs(facts, request, "corporate", calculate, "类别")


def event_prior_success_rate(facts, request):
    def calculate(code):
        events = event_records(facts, code, "corporate")
        definition = request.parameters["event_type"]
        start = str(request.trade_date - timedelta(days=request.parameters["days"]))
        cohort = [
            row
            for row in events
            if row.get("event_type") == definition and start <= row.get("origin_date", "") <= str(request.trade_date)
        ]
        success = sum(row.get("outcome") == "成功" for row in cohort)
        failed = sum(row.get("outcome") in ("失败", "终止") for row in cohort)
        denominator = positive(success + failed, "known_final_outcomes")
        return output(
            code,
            success / denominator,
            "比例",
            dict(
                known=int(denominator), pending=len(cohort) - denominator, success=success, type=definition, start=start
            ),
            cohort,
        )

    return by_subject(facts, request, calculate)


def event_value_impact_proxy(facts, request):
    def calculate(row):
        require(
            row.get("currency") == "CNY" and row.get("cap_currency") == "CNY",
            "currency",
            "公告金额与披露前市值缺同币种凭据",
        )
        cap = positive(number(row, "pre_announcement_cap"), "pre_announcement_cap")
        amount = number(row, "announced_amount")
        return amount / cap, dict(announced_amount=amount, pre_announcement_cap=cap, event_type=row.get("event_type"))

    return _event_outputs(facts, request, "corporate", calculate, "比例")
