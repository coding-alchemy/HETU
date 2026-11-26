"""Disclosed surprises, realized post-publication drift, and forecast diffusion."""

from dataclasses import replace
from datetime import datetime, time, timedelta

from .core import CN, by_subject, output, quotation, require, window
from .series import prices, sessions
from .sources import first_disclosures, known_records
from .statements import _growth, _indexed, amount, comparable_quarters, consensus_value, financial_rows


def _disclosure_stamp(row, fields):
    return max((quotation(row, field, "available_at") for field in fields), key=datetime.fromisoformat)


def _disclosed_fields(row):
    actual = "parent_net_profit"
    if row.get(actual) is not None or actual in (row.get("_conflicts") or {}):
        return (actual,)
    return "forecast_lower", "forecast_upper"


def earnings_surprise(facts, request):
    def calculate(code):
        reports = first_disclosures(facts, request, code)
        require(reports, "initial_reports", "缺首次披露及其公开时间", subject=code)
        end = max(row["period"] for row in reports)
        by_time = [(datetime.fromisoformat(row["first_known_at"]), row) for row in reports if row["period"] == end]
        earliest = min(stamp for stamp, _ in by_time)
        firsts = [row for stamp, row in by_time if stamp == earliest]
        require(len(firsts) == 1, "initial_reports", "同一时刻首次披露的原件未能唯一确认",
                subject=code, period=end, evidence=firsts)
        first = firsts[0]
        unverified = (first.get("_conflicts") or {}).get("initial_reports")
        require(not unverified, "initial_reports", "更正声明缺可核对的首版",
                subject=code, period=end, evidence=unverified)
        fields = _disclosed_fields(first)
        value = sum(amount(first, field) for field in fields) / len(fields)
        context = ("accounting_scope", "flow_basis")
        scopes = {tuple(quotation(first, field, attribute) for attribute in context) for field in fields}
        require(len(scopes) == 1, "accounting_scope", "首披区间两端的会计范围或期间口径不同",
                subject=code, period=end, evidence=[first])
        released_at = _disclosure_stamp(first, fields)
        before_first = replace(request, cutoff=earliest - timedelta(microseconds=1))
        eligible = [row for row in known_records(facts, "forecasts", before_first, code) if row["period"] == end]
        require(eligible, "forecast_net_profit", "缺披露前同报告期预期", period=end)
        eligible = [
            row
            for row in eligible
            if tuple(quotation(row, "forecast_net_profit", attribute) for attribute in context) in scopes
        ]
        require(eligible, "forecast_net_profit", "缺与首披同会计范围及期间口径的预期", period=end)
        expected, forecasts, aggregation = consensus_value(
            eligible, request.parameters.get("forecast_aggregation"), subject=code, period=end
        )
        basis = "预告区间中点" if len(fields) == 2 else "首披归母盈利"
        return output(
            code,
            _growth(value, expected),
            "比例",
            dict(
                disclosed=value,
                expected=expected,
                basis=basis,
                released_at=released_at,
                first_known_at=earliest.isoformat(),
                forecast_cutoff=before_first.cutoff.isoformat(),
                forecast_aggregation=aggregation,
                institutions=[row.get("institution") for row in forecasts if row.get("institution")],
                period=end,
            ),
            [first, *forecasts],
        )

    return by_subject(facts, request, calculate)


def earnings_growth_change(facts, request):
    def calculate(code):
        rows = financial_rows(facts, request, code)
        require(rows, "financial_reports", "缺可比报告期")
        indexed = _indexed(rows)
        end = max(indexed)
        values = [
            _growth(*comparable_quarters(indexed, period, period - 4, "parent_net_profit"))
            for period in (end - 1, end)
        ]
        return output(code, 100 * (values[1] - values[0]), "百分点", dict(now=values[1], previous=values[0]), rows)

    return by_subject(facts, request, calculate)


def post_announcement_drift(facts, request):
    benchmark = request.parameters["benchmark"]
    count = window(request)

    def calculate(code):
        reports = facts.records("initial_reports", code)
        require(reports, "initial_reports", "缺首发公告时钟")
        release = max(reports, key=lambda row: datetime.fromisoformat(row["available_at"]))
        clock = datetime.fromisoformat(release["available_at"]).astimezone(CN)
        start = clock.date().isoformat()
        before_open = clock.time() < time(9, 30)
        days = [day for day in sessions(facts, request) if day > start or (day == start and before_open)]
        require(len(days) >= count + 1, "observation_window", "公告后的完整收盘观察窗待形成")
        used_days = days[-1:]
        used_days.insert(0, days[0])
        observed = []
        results = []
        for subject in (code, benchmark):
            available = {row["date"]: row for row in facts.records("daily_bars", subject)}
            require(all(day in available for day in days), "close", "公告后窗口缺对齐收盘", subject=subject)
            values = prices([available[day] for day in used_days])
            results.append(float(values[-1] / values[0] - 1))
            observed.extend(available[day] for day in days)
        return output(
            code,
            results[0] - results[1],
            "比例",
            dict(
                start=days[0],
                end=days[-1],
                benchmark=benchmark,
                stock_return=results[0],
                benchmark_return=results[1],
                released_at=release["available_at"],
            ),
            [release, *observed],
        )

    return by_subject(facts, request, calculate)


def forecast_pairs(facts, request, code):
    period = request.parameters["period"]
    boundary = request.cutoff - timedelta(days=request.parameters["days"])

    def latest_at(query):
        selected = {}
        for row in known_records(facts, "forecasts", query, code):
            institution = row.get("institution")
            if row["period"] != period or not institution:
                continue
            stamp = datetime.fromisoformat(row["available_at"])
            if institution not in selected or stamp > datetime.fromisoformat(selected[institution]["available_at"]):
                selected[institution] = row
        return selected

    history = latest_at(replace(request, cutoff=boundary))
    present = latest_at(request)
    pairs = {
        key: (old, present[key])
        for key, old in history.items()
        if key in present and datetime.fromisoformat(present[key]["available_at"]) > boundary
    }
    require(pairs, "forecast_net_profit", "窗口内缺同机构同期间的旧、新可比预期", subject=code, period=period)
    return pairs


def _revision(facts, request, kind):
    def calculate(code):
        pairs = forecast_pairs(facts, request, code)
        old, new = zip(
            *[(amount(a, "forecast_net_profit"), amount(b, "forecast_net_profit")) for a, b in pairs.values()],
            strict=True,
        )
        up = sum(b > a for a, b in zip(old, new, strict=True))
        down = sum(b < a for a, b in zip(old, new, strict=True))
        count = len(old)
        value = _growth(sum(new), sum(old)) if kind == "change" else (up if kind == "up" else up - down) / count
        return output(
            code,
            value,
            "比例",
            dict(up=up, down=down, unchanged=count - up - down, count=count, institutions=list(pairs)),
            [row for pair in pairs.values() for row in pair],
        )

    return by_subject(facts, request, calculate)


def earnings_revision(facts, request):
    return _revision(facts, request, "change")


def analyst_up_share(facts, request):
    return _revision(facts, request, "up")


def analyst_revision_diffusion(facts, request):
    return _revision(facts, request, "diffusion")
