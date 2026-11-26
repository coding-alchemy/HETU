"""Sector aggregates and ETF valuations using their native observation objects."""

from dataclasses import replace
from datetime import date

import numpy as np
import pandas as pd

from .breadth import _liquidity, breadth_above_ma_share
from .core import (
    MissingFact,
    by_subject,
    check_conflict,
    money_in_yuan,
    net_assets_unit,
    number,
    output,
    positive,
    price_per_share,
    quotation,
    require,
    window,
)
from .cross_section import percentile, robust_z, scope
from .disclosures import forecast_pairs
from .market_activity import _current
from .prices import momentum, risk_adjusted_momentum, sma_filter
from .series import daily, returns, sessions
from .statements import _growth, _indexed, _sum, amount, financial_rows, quarter


def _sector_members(facts, request, kind):
    rows, issues = [], []
    for sector in request.subjects:
        try:
            members, roster, complete = scope(facts, request, sector)
            inputs = list(roster)
            observations = []
            for code in members:
                try:
                    if kind == "profit":
                        reports = financial_rows(facts, request, code)
                        require(reports, "financial_reports", "缺可比归母净利", subject=code)
                        indexed = _indexed(reports)
                        period = pd.Period(request.parameters["period"], freq="Q")
                        before = quarter(indexed, period - 4, "parent_net_profit")
                        now = quarter(indexed, period, "parent_net_profit")
                        inputs.extend(reports)
                    else:
                        pairs = forecast_pairs(facts, request, code)
                        before = sum(amount(pair[0], "forecast_net_profit") for pair in pairs.values())
                        now = sum(amount(pair[1], "forecast_net_profit") for pair in pairs.values())
                        inputs.extend(row for pair in pairs.values() for row in pair)
                    observations.append(dict(code=code, before=before, now=now))
                except MissingFact as error:
                    error.subject = error.subject or code
                    issues.append(error)
            require(observations, "eligible_members", "无可比成员", subject=sector)
            if kind == "profit":
                value = _growth(sum(row["now"] for row in observations), sum(row["before"] for row in observations))
            else:
                value = sum(row["now"] > row["before"] for row in observations) / len(observations)
            if not complete:
                issues.append(MissingFact("memberships", "只覆盖可证子集", subject=sector))
            rows.append(
                output(
                    sector,
                    value,
                    "比例",
                    dict(
                        observed=observations,
                        eligible=len(observations),
                        members=len(members) if complete else None,
                        period=request.parameters["period"],
                    ),
                    inputs,
                )
            )
        except MissingFact as error:
            issues.append(error)
    return facts.result(request, rows, issues)


def sector_profit_growth(facts, request):
    return _sector_members(facts, request, "profit")


def sector_revision_up_share(facts, request):
    return _sector_members(facts, request, "forecast")


def sector_activity_yoy(facts, request):
    def calculate(sector):
        series = sorted(facts.records("industry_activity", sector), key=lambda row: row["period"])
        require(series, "industry_activity", "所选行业缺已公开的实物景气序列")
        now = series[-1]
        period = str(pd.Period(now["period"], freq="M") - 12)
        earlier = [
            row
            for row in series
            if row["period"] == period and row.get("unit") == now.get("unit") and row.get("series") == now.get("series")
        ]
        require(
            len(earlier) == 1 and now.get("unit") and now.get("series"),
            "industry_activity",
            "缺上年同月同单位景气读数",
            period=period,
        )
        return output(
            sector,
            _growth(number(now, "quantity"), number(earlier[0], "quantity")),
            "比例",
            dict(series=now["series"], unit=now["unit"], period=now["period"]),
            [earlier[0], now],
        )

    return by_subject(facts, request, calculate)


def _valuation(facts, request, sector):
    members, roster, complete = scope(facts, request, sector)
    require(complete, "memberships", "缺该期行业完整成员")
    cap, profit = 0, 0
    inputs = list(roster)
    for member in members:
        capitalization = _current(facts, request, "market_cap", member)
        cap += positive(number(capitalization, "market_cap_cny"), "market_cap_cny")
        reports = [
            row
            for row in financial_rows(facts, request, member)
            if pd.Timestamp(row["available_at"]) <= pd.Timestamp(request.cutoff)
        ]
        require(reports, "financial_reports", "缺该历史时点已知财报", subject=member)
        indexed = _indexed(reports)
        profit += _sum(indexed, max(indexed), "parent_net_profit")
        inputs.extend([capitalization, *reports])
    return cap / positive(profit, "parent_net_profit"), inputs


def _historical(facts, request, sector, mode):
    from .core import close_cutoff

    dates = sessions(facts, request)
    count = window(request)
    require(len(dates) >= count, "calendar", "缺历史交易日")
    values, sources, coverage = [], [], []
    for day in dates[-count:]:
        current = date.fromisoformat(day)
        history = replace(request, trade_date=current, cutoff=min(request.cutoff, close_cutoff(current)))
        if mode == "valuation":
            value, evidence = _valuation(facts, history, sector)
        else:
            value, detail, evidence, _ = _liquidity(facts, history, sector, True)
            require(detail["full_scope"], "memberships", "行业换手历史需完整成员及股本")
            coverage.append(dict(detail, date=day))
        values.append(value)
        sources.extend(evidence)
    return values, dates[-count:], sources, coverage


def _rank(facts, request, kind):
    def calculate(sector):
        values, dates, evidence, coverage = _historical(facts, request, sector, kind)
        return output(
            sector,
            float(percentile(values)[-1]),
            "百分点",
            dict(values=values, dates=dates, coverage=coverage),
            evidence,
        )

    return by_subject(facts, request, calculate)


def sector_valuation_percentile(facts, request):
    return _rank(facts, request, "valuation")


def sector_turnover_percentile(facts, request):
    return _rank(facts, request, "turnover")


def sector_amount_share_change(facts, request):
    count = window(request)
    dates = sessions(facts, request)
    require(len(dates) > count, "calendar", "成交额占比对照日缺证")
    market = request.parameters["market"]

    def calculate(sector):
        ratios, evidence = [], []
        for day in (dates[-count - 1], dates[-1]):
            current = replace(request, trade_date=date.fromisoformat(day))
            amount_sector, part, origins, _ = _liquidity(facts, current, sector, False)
            amount_market, total, base, _ = _liquidity(facts, current, market, False)
            require(part["full_scope"] and total["full_scope"], "memberships", "缺板块与市场的同日完整范围")
            ratios.append(amount_sector / positive(amount_market, "market_amount"))
            evidence.extend([*origins, *base])
        return output(
            sector,
            ratios[-1] - ratios[0],
            "比例",
            dict(previous=ratios[0], current=ratios[1], market=market, dates=[dates[-count - 1], dates[-1]]),
            evidence,
        )

    return by_subject(facts, request, calculate)


def sector_flow_proxy(facts, request):
    def calculate(sector):
        row = _current(facts, request, "sector_flows", sector)
        require(row.get("provider_definition"), "provider_definition", "缺可比净流入的供应商口径")
        value = number(row, "net_flow")
        total = positive(number(row, "amount_cny"), "amount_cny")
        return output(
            sector, value / total, "比例", dict(net_flow=value, amount=total, basis=row["provider_definition"]), [row]
        )

    return by_subject(facts, request, calculate)


def sector_rotation_score(facts, request):
    weights = request.parameters["weights"]
    require(
        isinstance(weights, list)
        and len(weights) == 4
        and all(isinstance(w, (int, float)) and w >= 0 for w in weights),
        "weights",
        "需要四个事前非负权重",
    )
    forecast = sector_revision_up_share(facts, request)
    ma_request = replace(request, parameters={**request.parameters, "window": window(request, "ma_window")})
    breadth = breadth_above_ma_share(facts, ma_request)
    values = sector_valuation_percentile(facts, request)
    parts = []
    for sector in request.subjects:
        motion, history = momentum(facts, request, sector)
        components = [
            next((row for row in r.rows if row["subject"] == sector), None) for r in (forecast, breadth, values)
        ]
        require(
            all(components) and not any(r.gaps for r in (forecast, breadth, values)),
            "components",
            "至少一项板块分量没有完整证据",
            subject=sector,
        )
        parts.append(
            (
                [motion, *(row["value"] for row in components)],
                [*history, *(item for row in components for item in row["inputs"])],
            )
        )
    matrix = np.array([values for values, _ in parts])
    epsilon = number(request.parameters, "epsilon")
    standardized = np.column_stack([robust_z(column, epsilon) for column in matrix.T])
    signs = np.array([1, 1, 1, -1])
    return facts.result(
        request,
        [
            output(
                sector,
                float(z @ (signs * weights)),
                "分数",
                dict(raw=raw, z=z.tolist(), weights=weights, direction=signs.tolist()),
                inputs,
            )
            for sector, (raw, inputs), z in zip(request.subjects, parts, standardized, strict=True)
        ],
    )


def etf_dual_momentum(facts, request):
    group = request.parameters.get("group")
    symbols, roster, complete = scope(facts, request, group)
    require(complete, "memberships", "ETF 排名所需池及生效日期未证", subject=group)
    current = replace(request, subjects=symbols)
    motion = risk_adjusted_momentum(facts, current)
    require(len(motion.rows) == len(symbols), "daily_bars", "缺部分 ETF 风险调整动量")
    mode = request.parameters["filter"]
    require(mode in ("sma", "positive_return"), "filter", "未知绝对趋势过滤规则")
    rank = percentile([row["value"] for row in motion.rows])
    output_rows = []
    for row, score in zip(motion.rows, rank, strict=True):
        code = row["subject"]
        if mode == "sma":
            period = window(request, "filter_window")
            sub = replace(request, subjects=(code,), parameters={**request.parameters, "window": period})
            filtered = sma_filter(facts, sub)
            require(filtered.rows, "filter", "均线过滤证据不足", subject=code)
            flag = bool(filtered.rows[0]["value"])
        else:
            flag = row["components"]["momentum"] > 0
        output_rows.append(
            output(
                code, float(score), "百分点", dict(score=row["value"], filter=mode, passed=flag), row["inputs"] + roster
            )
        )
    return facts.result(request, output_rows)


def _etf_per_share(row, field, currency):
    return price_per_share(
        positive(number(row, field), field), currency, quotation(row, field, "unit"),
        subject=row.get("code"), period=row.get("date"), evidence=[row],
    )


def _tracking_nav(row):
    check_conflict(row, "nav")
    where = dict(subject=row["code"], period=row["date"], evidence=[row])
    require(quotation(row, "nav", "currency") == "CNY", "currency", "净值缺人民币换算凭据", **where)
    require(quotation(row, "nav", "return_basis") == "total", "return_basis", "净值缺同总收益口径", **where)
    return _etf_per_share(row, "nav", "CNY")


def _etf(facts, request, kind):
    def calculate(code):
        if kind == "tracking":
            benchmark = request.parameters["benchmark"]
            count = window(request, minimum=2)
            nav = daily(facts, request, code, count + 1, table="etf_nav")
            benchmark_rows = daily(facts, request, benchmark, count + 1, table="etf_nav")
            levels = [np.array([_tracking_nav(row) for row in leg]) for leg in (nav, benchmark_rows)]
            changes = returns(levels[0]) - returns(levels[1])
            return output(
                code,
                float(changes.std(ddof=1) * np.sqrt(252)),
                "年化比例",
                dict(differences=changes.tolist(), benchmark=benchmark),
                nav + benchmark_rows,
            )
        nav = _current(facts, request, "etf_nav", code)
        where = dict(subject=code, period=nav["date"], evidence=[nav])
        if kind == "assets":
            check_conflict(nav, "net_assets")
            if nav.get("net_assets") is not None:
                currency = quotation(nav, "net_assets", "currency")
                require(currency == "CNY", "currency", "净资产缺人民币换算依据", **where)
                assets = positive(number(nav, "net_assets"), "net_assets")
                capital = money_in_yuan(assets, currency, net_assets_unit(nav), **where)
                return output(code, capital, "人民币元", dict(basis="公布净资产"), [nav])

        check_conflict(nav, "nav")
        nav_currency = quotation(nav, "nav", "currency")
        require(nav_currency, "currency", "净值币种未证明", **where)
        if kind == "assets":
            require(nav_currency == "CNY", "currency", "净资产缺人民币换算依据", **where)
            outstanding = _current(facts, request, "etf_shares", code)
            require(quotation(outstanding, "shares", "unit") == "份", "unit", "份额和对应净值单位不可比")
            share_count = positive(number(outstanding, "shares"), "shares")
            value = _etf_per_share(nav, "nav", nav_currency)
            return output(
                code, share_count * value, "人民币元", dict(shares=share_count, nav=value), [outstanding, nav]
            )
        close = daily(facts, request, code, 1)[0]
        currency = quotation(close, "close_raw", "currency")
        require(
            currency == nav_currency or (currency is None and nav_currency == "CNY"),
            "currency",
            "ETF 场价与净值币种不同且无换算凭据",
            subject=code,
            evidence=[close, nav],
        )

        price = _etf_per_share(close, "close_raw", nav_currency)
        nav_value = _etf_per_share(nav, "nav", nav_currency)
        return output(
            code,
            price / nav_value - 1,
            "比例",
            dict(price=price, nav=nav_value, currency=nav_currency, unit="每份"),
            [nav, close],
        )

    return by_subject(facts, request, calculate)


def etf_assets(facts, request):
    return _etf(facts, request, "assets")


def etf_premium(facts, request):
    return _etf(facts, request, "premium")


def etf_tracking_error(facts, request):
    return _etf(facts, request, "tracking")
