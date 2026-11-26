"""Point-in-time income, balance sheet, and forecast calculations."""

from datetime import date, datetime

import numpy as np
import pandas as pd

from .core import by_subject, check_conflict, financial_amount_in_yuan, number, output, positive, quotation, require
from .sources import known_records


def financial_rows(facts, request, code):
    rows = known_records(facts, "financial_reports", request, code)
    return sorted(
        (
            dict(row, period=row.get("period") or row.get("report_period"))
            for row in rows
            if row.get("period") or row.get("report_period")
        ),
        key=lambda row: row["period"],
    )


def amount(row, field):
    proof = (row.get("field_evidence") or {}).get(field) or row
    return financial_amount_in_yuan(
        number(row, field),
        quotation(row, field, "currency"),
        quotation(row, field, "unit"),
        subject=row.get("code"),
        period=row.get("period"),
        evidence=[{**proof, "field": field}],
    )


def _indexed(rows):
    return {pd.Period(row["period"], freq="Q"): row for row in rows}


def _row_at(index, period):
    require(period in index, "financial_reports", "报告期资料不足", period=str(period))
    return index[period]


def quarter(index, period, field):
    row = _row_at(index, period)
    current = amount(row, field)
    basis = _flow_basis(row, field)
    if basis == "cumulative" and period.quarter != 1:
        previous = _row_at(index, period - 1)
        require(_flow_basis(previous, field) == "cumulative", "flow_basis", "缺少同年度可比累计数",
                subject=row.get("code"), period=previous["period"], evidence=[previous])
        require(quotation(previous, field, "accounting_scope") == quotation(row, field, "accounting_scope"),
                "accounting_scope", "同年度累计数的会计范围不一致", subject=row.get("code"), period=str(period),
                evidence=[previous, row])
        current -= amount(previous, field)
    return current


def _flow_basis(row, field):
    basis = quotation(row, field, "flow_basis")
    proof = (row.get("field_evidence") or {}).get(field)
    require(basis in ("single", "cumulative"), "flow_basis", "单季或累计口径未证",
            subject=row.get("code"), period=row["period"], evidence=[proof or row])
    return basis


def comparable_quarters(index, current, previous, field):
    now, old = quarter(index, current, field), quarter(index, previous, field)
    reports = [_row_at(index, period) for period in (current, previous)]
    scopes = [quotation(row, field, "accounting_scope") for row in reports]
    require(
        scopes[0] == scopes[1], "accounting_scope", "两期财报会计范围不一致",
        subject=reports[0].get("code"), period=str(current),
        evidence=[
            {**((row.get("field_evidence") or {}).get(field) or row),
             "period": row["period"], "accounting_scope": scope}
            for row, scope in zip(reports, scopes, strict=True)
        ],
    )
    return now, old


def _flows(index, end, field, count=4):
    return [quarter(index, end - i, field) for i in range(count - 1, -1, -1)]


def _sum(index, end, field):
    return sum(_flows(index, end, field))


def _cap(facts, request, code):
    rows = [row for row in facts.records("market_cap", code) if row.get("date") == str(request.trade_date)]
    require(len(rows) == 1, "market_cap_cny", "缺所选交易日可知的总市值", subject=code)
    return positive(number(rows[0], "market_cap_cny"), "market_cap_cny"), rows[0]


def _growth(current, old):
    return (current - old) / positive(abs(old), "prior_period_base")


def consensus_value(rows, aggregation, *, subject, period):
    require(rows, "forecasts", "缺适用报告期的预期", subject=subject, period=period)
    for row in rows:
        check_conflict(row, "forecast_kind")
    published = [row for row in rows if row.get("forecast_kind") == "consensus"]

    def most_recent(candidates):
        latest = max(datetime.fromisoformat(row["available_at"]) for row in candidates)
        matching = [row for row in candidates if datetime.fromisoformat(row["available_at"]) == latest]
        require(
            len(matching) == 1,
            "forecast_net_profit",
            "同一公开时点的预测有冲突",
            subject=subject,
            period=period,
            evidence=matching,
        )
        return matching[0]

    if published:
        selected = [most_recent(published)]
        method = "published_consensus"
    else:
        require(
            aggregation == "equal_weight",
            "forecast_aggregation",
            "缺已发布的一致标记或事前机构聚合口径",
            subject=subject,
            period=period,
            evidence=rows,
        )
        institutions = {}
        for row in rows:
            check_conflict(row, "institution")
            require(
                row.get("institution") and row.get("forecast_kind") in (None, "individual"),
                "institution",
                "机构聚合需要有据的机构明细及名称",
                subject=subject,
                period=period,
                evidence=[row],
            )
            institutions.setdefault(row["institution"], []).append(row)
        selected = [most_recent(group) for group in institutions.values()]
        method = "equal_weight"
    return sum(amount(row, "forecast_net_profit") for row in selected) / len(selected), selected, method


def _applicable_fiscal_pair(candidates, trade_date, code):
    by_year = {}
    for row in candidates:
        by_year.setdefault(str(row.get("period")), []).append(row)
    current_year = trade_date.year
    possible = []
    for year in (current_year, current_year + 1):
        for row in by_year.get(str(year), ()):
            fiscal_end = row.get("fiscal_year_end") or "12-31"
            try:
                fiscal_close = date.fromisoformat(f"{current_year}-{fiscal_end}")
            except ValueError:
                require(False, "fiscal_year_end", "财年截止要求可识别的月-日", subject=code, evidence=[row])
            first_year = current_year if trade_date < fiscal_close else current_year + 1
            if year == first_year:
                possible.append((year, fiscal_end, row))
    require(possible, "forecasts", "缺适用报告期的预期", subject=code, period=str(trade_date))
    schedules = {(year, end) for year, end, _ in possible}
    require(
        len(schedules) == 1,
        "fiscal_year_end",
        "无法确定唯一的适用财年及其截止口径",
        subject=code,
        evidence=[row for _, _, row in possible],
    )
    year, fiscal_end = next(iter(schedules))
    periods = [str(year), str(year + 1)]
    applicable = [row for period in periods for row in by_year.get(period, ())]
    for row in applicable:
        check_conflict(row, "fiscal_year_end")
    require(
        all((row.get("fiscal_year_end") or "12-31") == fiscal_end for row in applicable),
        "fiscal_year_end",
        "两个适用财年的截止口径不同",
        subject=code,
        period=periods,
        evidence=applicable,
    )
    return periods, fiscal_end, by_year


def _compute(facts, request, kind):
    def calculate(code):
        rows = financial_rows(facts, request, code)
        if kind == "expected_growth":
            candidates = known_records(facts, "forecasts", request, code)
            periods, fiscal_end, by_year = _applicable_fiscal_pair(candidates, request.trade_date, code)
            estimates = [
                consensus_value(
                    by_year.get(period, []),
                    request.parameters.get("forecast_aggregation"),
                    subject=code,
                    period=period,
                )
                for period in periods
            ]
            (old, first, first_method), (now, second, second_method) = estimates
            forecasts = first + second
            require(
                len({tuple(quotation(row, "forecast_net_profit", attribute)
                           for attribute in ("accounting_scope", "flow_basis")) for row in forecasts}) == 1,
                "accounting_scope",
                "两个财年预期范围或期间口径不同",
                evidence=forecasts,
            )
            if (first_method, second_method) == ("equal_weight", "equal_weight"):
                require(
                    {row["institution"] for row in first} == {row["institution"] for row in second},
                    "forecast_aggregation",
                    "两个财年缺可比的同一机构集合",
                    subject=code,
                    evidence=forecasts,
                )
            require(
                first_method == second_method,
                "forecast_aggregation",
                "两个财年预测聚合口径不同",
                subject=code,
                evidence=forecasts,
            )
            return output(
                code,
                _growth(now, old),
                "比例",
                dict(
                    previous=old,
                    current=now,
                    negative_base=old < 0,
                    fiscal_years=periods,
                    fiscal_year_end=fiscal_end,
                    forecast_aggregation=first_method,
                ),
                forecasts,
            )
        require(
            rows,
            "net_profit_ex" if kind == "earnings_yield" else "financial_reports",
            "缺原公告或收盘前可知的报告版本",
            subject=code,
        )
        index = _indexed(rows)
        end = max(index)
        latest = index[end]
        inputs = rows
        detail = dict(period=latest["period"])
        if kind in ("earnings_yield", "book_to_price", "operating_cashflow_yield", "free_cashflow_yield"):
            cap, cap_row = _cap(facts, request, code)
            if kind in ("earnings_yield", "book_to_price"):
                numerator = amount(latest, "net_profit_ex" if kind == "earnings_yield" else "parent_equity")
            else:
                numerator = _sum(index, end, "operating_cashflow")
                if kind == "free_cashflow_yield":
                    numerator -= _sum(index, end, "capex_cash")
            value = numerator / cap
            detail.update(numerator=numerator, denominator=cap)
            inputs = [latest, cap_row] if kind in ("earnings_yield", "book_to_price") else [*rows, cap_row]
        elif kind in ("revenue_growth_yoy", "profit_growth_yoy"):
            field = "revenue" if kind == "revenue_growth_yoy" else "parent_net_profit"
            now, old = comparable_quarters(index, end, end - 4, field)
            value = _growth(now, old)
            detail.update(current=now, previous=old, negative_base=old < 0)
        elif kind == "gross_margin_stability":
            revenue = _flows(index, end, "revenue", 8)
            costs = _flows(index, end, "cost_of_sales", 8)
            margins = [1 - cost / positive(rev, "revenue") for rev, cost in zip(revenue, costs, strict=True)]
            value = -float(np.std(margins, ddof=1))
            detail["quarterly_margins"] = margins
        else:
            if kind in ("roe", "roic", "accruals"):
                first = _row_at(index, end - 4)
            if kind == "roe":
                numerator = _sum(index, end, "parent_net_profit")
                denominator = (amount(first, "parent_equity") + amount(latest, "parent_equity")) / 2
            elif kind == "roic":
                tax = [_row_at(index, end - i) for i in range(3, -1, -1)]
                numerator = sum(
                    ebit * (1 - number(row, "tax_rate"))
                    for ebit, row in zip(_flows(index, end, "ebit"), tax, strict=True)
                )
                capitals = [
                    amount(row, "interest_debt") + amount(row, "equity") - amount(row, "cash")
                    for row in (first, latest)
                ]
                denominator = sum(capitals) / 2
            elif kind == "accruals":
                numerator = _sum(index, end, "consolidated_profit") - _sum(index, end, "operating_cashflow")
                denominator = (amount(first, "assets") + amount(latest, "assets")) / 2
            elif kind == "leverage":
                numerator, denominator = amount(latest, "liabilities"), amount(latest, "assets")
            else:
                require(
                    all(_row_at(index, end - i).get("rd_basis") == "total" for i in range(4)),
                    "rd_basis",
                    "缺含资本化的完整研发投入定义",
                )
                numerator, denominator = _sum(index, end, "rd_investment"), _sum(index, end, "revenue")
            value = numerator / positive(denominator, "denominator")
            detail.update(numerator=numerator, denominator=denominator)
        return output(code, value, "比例", detail, inputs)

    return by_subject(facts, request, calculate)


def earnings_yield(facts, request):
    return _compute(facts, request, "earnings_yield")


def book_to_price(facts, request):
    return _compute(facts, request, "book_to_price")


def operating_cashflow_yield(facts, request):
    return _compute(facts, request, "operating_cashflow_yield")


def free_cashflow_yield(facts, request):
    return _compute(facts, request, "free_cashflow_yield")


def roe(facts, request):
    return _compute(facts, request, "roe")


def roic(facts, request):
    return _compute(facts, request, "roic")


def gross_margin_stability(facts, request):
    return _compute(facts, request, "gross_margin_stability")


def accruals(facts, request):
    return _compute(facts, request, "accruals")


def leverage(facts, request):
    return _compute(facts, request, "leverage")


def revenue_growth_yoy(facts, request):
    return _compute(facts, request, "revenue_growth_yoy")


def profit_growth_yoy(facts, request):
    return _compute(facts, request, "profit_growth_yoy")


def expected_growth(facts, request):
    return _compute(facts, request, "expected_growth")


def rd_intensity(facts, request):
    return _compute(facts, request, "rd_intensity")
