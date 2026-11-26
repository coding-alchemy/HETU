"""Independent formula discriminators for the existing teaching cases."""

import json
from dataclasses import replace
from math import exp, sqrt

import pytest
from factor_assertions import checked
from factor_cases import example_query
from hetu_quant.factors import TeachingSource, compute_factor
from test_factor_sensitivity import CHANGES

# Expected readings are derived from the changed input shown in each row, independently of production.
# Entries with a field assertion require the exact expected failure.
CASES = {
    "absolute_momentum": ("自身回报 .1，阈值 .12", {"threshold": 0.12}, -0.02, {"passed": False}),
    "amihud_illiquidity": ("首个收益日成交额为零", ("daily_bars", 1, "amount_cny", 0), None, "amount_cny"),
    "book_to_price": ("7.5 亿归母权益，市值 100 亿", ("financial_reports", 0, "parent_equity", 7.5), 0.075, {}),
    "cross_section_clip": ("线性截尾时末值 100→60", ("daily_bars", 3, "amount_cny", 60), [7.5, 10, 20, 30], {}),
    "earnings_yield": (
        "扣非净利 6 亿改 7 亿",
        ("financial_reports", 0, "net_profit_ex", 7),
        0.07,
        {"numerator": 7e8, "denominator": 1e10},
    ),
    "expected_growth": ("同一未来财年预期用 USD 报价", ("forecasts", 1, "currency", "USD"), None, "currency/unit"),
    "free_cashflow_yield": (
        "全年资本支出累计由 5 亿改 7 亿，经营现金流仍 14 亿",
        ("financial_reports", 3, "capex_cash", 7),
        0.07,
        {"numerator": 7e8},
    ),
    "free_float_market_cap": (
        "自由流通股本减半仍用当日收盘",
        ("shares", 0, "free_float_shares", 200000),
        2e6,
        {"shares": 200000},
    ),
    "gross_margin_stability": ("只留最近七个单季", ("financial_reports", 0, None, None), None, "financial_reports"),
    "low_volatility_36m": ("最新月末已知配股不可套用送转规则", "rights_issue", None, "action_type"),
    "market_beta": ("基准四个收益为常数", "constant_benchmark", None, "regression_rank"),
    "max_drawdown": ("旧最低收盘 9 改 6", "cut_drawdown_trough", 0.6, {}),
    "momentum_rank": ("令三个标的单期收益同为 10%", ("daily_bars", -1, "close", 110), [50, 50, 50], {"denominator": 3}),
    "neutralized_score": ("仅行业中性化", {"neutralize": "industry"}, [-0.5, 0.5, -2.5, 2.5], {}),
    "operating_cashflow_yield": (
        "全年经营现金流累计从 14 亿增至 16 亿",
        ("financial_reports", 3, "operating_cashflow", 16),
        0.16,
        {"numerator": 16e8},
    ),
    "pair_beta_drift": ("前段 β=3，后段 β=2", "reverse_beta", -1, {"before": 3, "after": 2}),
    "pair_cointegration": (
        "相同 30 对价格改用固定 ADF 滞后一阶",
        {"lag": 1},
        0.20060210296,
        {"lag": 1, "statistic": -2.6971590465},
    ),
    "pair_extreme_spread": ("两日滚动窗口改为仅末日，仍使用之前四日训练", {"lookback": 1}, 0, {}),
    "pair_half_life": ("几何残差系数设为 −0.5，反转过程超出 OU 半衰期口径", "negative_phi", None, "phi"),
    "pair_spread": ("保持训练窗，标的当日价格乘 exp(.01)", "raise_last_pair", 0.03, {"beta": 2, "alpha": 1}),
    "pair_spread_z": ("训练期对数价格严格共线", "collinear_pair", None, "spread_deviation"),
    "price_deviation_z": ("三期收盘都为 10，样本标准差为零", "flat", None, "standard_deviation"),
    "price_momentum": ("只把被跳过的当日价改为 300", ("daily_bars", -1, "close", 300), 0.2, {"anchor": "2024-01-29"}),
    "rd_intensity": ("年度研发累计 6 亿，年度营收改为 60 亿", ("financial_reports", 3, "revenue", 60), 0.1, {}),
    "realized_volatility": ("连续三日收盘一致", "flat", 0, {"returns": [0, 0]}),
    "relative_strength": ("基准两日同价 100，标的收益仍 20%", "equal_benchmark_end", 0.2, {"benchmark_return": 0}),
    "residual_volatility": ("基准常数，市场模型没有可估计斜率", "constant_benchmark", None, "regression_rank"),
    "revenue_growth_yoy": (
        "同期第四季基数为零：去年累计 Q4 等于 Q3",
        ("financial_reports", 1, "revenue", 12),
        None,
        "prior_period_base",
    ),
    "risk_adjusted_momentum": ("标的价格连续三日均为 100", "flat", 0, {"volatility": 0, "momentum": 0}),
    "robust_cross_section_z": ("三只证券成交额都为 2，稳健标准分均为零", "flat_amounts", [0, 0, 0], {}),
    "total_market_cap": ("总股本 200 万股，收盘 10 元", ("shares", 0, "total_shares", 2e6), 2e7, {"shares": 2e6}),
    "traded_amount": ("观察日已证成交额为零", ("daily_bars", -1, "amount_cny", 0), 0, {"count": 1, "mode": "daily"}),
    "weighted_factor_score": ("两个方向相反的原始分量在等权下抵消", "equal_weights", [0, 0, 0], {}),
    "analyst_revision_diffusion": (
        "机构 Y 新预测改为 21，2 上调、0 下调、1 不变",
        ("forecasts", 3, "forecast_net_profit", 21),
        2 / 3,
        {"up": 2, "down": 0, "unchanged": 1, "count": 3},
    ),
    "analyst_up_share": (
        "机构 X 最新预期与旧值 10 相同",
        ("forecasts", 1, "forecast_net_profit", 10),
        0,
        {"up": 0, "down": 1, "unchanged": 2, "count": 3},
    ),
    "atr_position_size": ("方向反转，风险额和 ATR 不变", {"direction": -1}, -250, {"atr": 2, "direction": -1}),
    "buyback_plan": ("原件现已明确撤销", ("events", 0, "state", "已撤销"), "已撤销", {"event_id": "EV1"}),
    "buyback_price_cap": (
        "原件 1250 分／股改为已证 1250 元／股",
        ("events", 0, "price_unit", "元/股"),
        1250,
        {"original": 1250},
    ),
    "dividend_yield": (
        "已知事件临时写成待定，不能发布剩余现金的股息率",
        ("corporate_actions", 0, "ex_date", "待定"),
        None,
        "ex_date",
    ),
    "earnings_growth_change": (
        "前一年同季利润为零，不能核算本期同比",
        ("financial_reports", 1, "parent_net_profit", 0),
        None,
        "prior_period_base",
    ),
    "earnings_revision": (
        "将 X 新预测 12 改 11，三家新旧合计相同",
        ("forecasts", 1, "forecast_net_profit", 11),
        0,
        {"up": 1, "down": 1, "unchanged": 1, "count": 3},
    ),
    "earnings_surprise": (
        "首披 12 亿，已有一致值 10 亿之外新增另一机构 14 亿仍保留既有一致值",
        "ignore_individual_forecast",
        0.2,
        {"expected": 1e9, "forecast_aggregation": "published_consensus"},
    ),
    "etf_assets": ("去掉公布净资产，改用同日 200 万份×2.5 元", "asset_from_shares", 5e6, {"nav": 2.5, "shares": 2e6}),
    "etf_dual_momentum": (
        "A、B 都为正收益，B 因较低波动排名第一，两者正回报过滤均通过",
        "positive_second_etf",
        [0, 100],
        {"passed": True},
    ),
    "etf_tracking_error": (
        "基金第二段收益改为 1%，日跟踪差保持 1%",
        ("etf_nav", 2, "nav", 102.01),
        0,
        {"differences": [0.01, 0.01]},
    ),
    "event_value_impact_proxy": (
        "合同金额用 USD，原市值人民币且无换算",
        ("events", 0, "currency", "USD"),
        None,
        "currency",
    ),
    "grid_geometric_level": ("第负一层按除以 1.1", {"level": -1}, 100 / 1.1, {"level": -1}),
    "grid_linear_level": ("网格步长为零报参数缺口", {"step": 0}, None, "step"),
    "index_contribution_gap": (
        "前日权重改为 .4、.6，前一大权重股变为 B",
        "swap_index_weights",
        -0.07,
        {"top_codes": ["B"], "top": -0.03, "rest": 0.04, "advance_share": 0.5},
    ),
    "insider_net_purchase": (
        "把 30 股增持改为减持，减持合计 40 股／流通股 1000",
        ("events", 0, "direction", "减持"),
        -0.04,
        {"changes": [-30, -10], "float_shares": 1000},
    ),
    "post_announcement_drift": ("仍在首次完整收盘日，观察窗未形成", "early_drift", None, "observation_window"),
    "risk_budget_vol_weight": ("年化波动翻倍至 0.4，风险预算仍 0.1", "double_vol", 0.25, {"volatility": 0.4}),
    "rolling_return_correlation": ("第二腿报价用 USD，第一腿人民币", "usd_second_leg", None, "currency"),
    "sector_activity_yoy": ("同口径上年数量 80，今年 120", ("industry_activity", 1, "quantity", 120), 0.5, {}),
    "sector_amount_share_change": (
        "标的部门现日 30 中减去 5，市场原 120 同减 5",
        ("daily_bars", 1, "amount_cny", 25),
        25 / 115 - 0.2,
        {"current": 25 / 115, "previous": 0.2},
    ),
    "sector_profit_growth": (
        "B 同期利润 8、现期 9 改为 11，A 仍 2→4",
        ("financial_reports", 3, "parent_net_profit", 11),
        0.5,
        {"eligible": 2},
    ),
    "sector_revision_up_share": (
        "B 新预期从持平 10 改为 11",
        ("forecasts", 3, "forecast_net_profit", 11),
        2 / 3,
        {"eligible": 3},
    ),
    "sector_rotation_score": (
        "四分量的估值符号从已审阅权重中单次扣减，估值权重加倍",
        {"weights": [0.25, 0.25, 0.25, 0.5]},
        [-((0.1 / 0.14926 + 0.5 / 0.37165) / 4 - 25 / 74.131), 0, (0.1 / 0.14926 + 0.5 / 0.37165) / 4 - 25 / 74.131],
        {"direction": [1, 1, 1, -1]},
    ),
    "sector_turnover_percentile": (
        "当期股本仍 20，把当前量 1.8 改为 2，同 .10 取中位 50",
        ("daily_bars", 1, "volume_shares", 2),
        50,
        {"values": [0.1, 0.1]},
    ),
    "sector_valuation_percentile": (
        "当期市值从 120 亿改为 70 亿，历史估值 [8,7]",
        ("market_cap", 1, "market_cap_cny", 7e9),
        0,
        {"values": [8, 7]},
    ),
    "trend_vol_weight": ("趋势为真，比例 .5 被上限 .4 截住", {"cap": 0.4}, 0.4, {"trend": 1}),
    "unlock_holder_mix": (
        "员工 30 股类别缺证，控股占全批仍 .7",
        ("event_holders", 1, "holder_type", None),
        {"控股股东": 0.7},
        {"unknown_shares": 30, "total_shares": 100},
    ),
    "unlock_supply_pressure": (
        "解禁数量从 100 股变为 200 股，原流通股 1000",
        ("events", 0, "unlock_shares", 200),
        0.2,
        {},
    ),
    "vol_target_scale": ("目标 .5 超过当前波动 .199 涨至风险比例上限", {"target_vol": 0.5}, 1, {}),
    "up_amount_share": (
        "末日原价高于前收时多计 1500 万元",
        ("daily_bars", -1, "close_raw", 101),
        0.415,
        {"up_amount": 415000000, "total_amount": 1000000000},
    ),
}


def mutate(doc, query, spec):
    if isinstance(spec, dict):
        return replace(query, parameters=spec)
    tables = doc["tables"]
    if isinstance(spec, tuple):
        table, position, field, value = spec
        if field is None:
            del tables[table][position]
        else:
            tables[table][position][field] = value
        return query
    bars = tables.get("daily_bars", [])
    if spec == "cut_drawdown_trough":
        relevant = sorted(bars, key=lambda row: row["date"])[-4:]
        relevant[-2]["close"] = 6
    elif spec == "equal_benchmark_end":
        next(row for row in bars if row["code"] == "B" and row["date"] == str(query.trade_date))["close"] = 100
    elif spec in ("negative_phi", "collinear_pair", "reverse_beta"):
        a = [row for row in bars if row["code"] == "A"]
        b = [row for row in bars if row["code"] == "B"]
        n = len(a)
        x = (2.1, 2.1, 1.8, 2) if spec == "negative_phi" else tuple(1 + i * 0.02 for i in range(n))
        ys = (
            [1 + (3 if i < n // 2 else 2) * v for i, v in enumerate(x)]
            if spec == "reverse_beta"
            else [
                1 + 2 * v + (0.01 * (-0.5) ** i if spec == "negative_phi" else (0 if i < n - 1 else 0.02))
                for i, v in enumerate(x)
            ]
        )
        for left, right, va, vb in zip(a, b, ys, x, strict=True):
            left["close"], right["close"] = exp(va), exp(vb)
    elif spec == "raise_last_pair":
        [row for row in bars if row["code"] == "A"][-1]["close"] *= exp(0.01)
    elif spec in ("flat", "constant_benchmark", "flat_amounts"):
        for row in bars:
            if spec == "flat_amounts":
                row["amount_cny"] = 2
            elif spec == "flat" or row["code"] == "B":
                row["close"] = 100
    elif spec == "rights_issue":
        row = bars[-1]
        tables["corporate_actions"] = [
            dict(
                code=row["code"],
                ex_date=row["date"],
                action_type="rights_issue",
                m=1,
                d=0,
                available_at=row["available_at"],
            )
        ]
    elif spec == "equal_weights":
        return replace(
            query,
            parameters={
                "components": {
                    "price_momentum": {"weight": 0.5, "direction": 1},
                    "turnover_rate": {"weight": 0.5, "direction": -1},
                }
            },
        )
    elif spec == "ignore_individual_forecast":
        row = tables["forecasts"][0]
        tables["forecasts"].append(
            dict(row, institution="Y", fact_key="other", forecast_kind="individual", forecast_net_profit=14)
        )
    elif spec == "asset_from_shares":
        row = tables["etf_nav"][0]
        row.pop("net_assets")
        row["nav"] = 2.5
        tables["etf_shares"] = [
            dict(code=row["code"], date=row["date"], shares=2e6, unit="份", available_at=row["available_at"])
        ]
    elif spec == "positive_second_etf":
        for row, price in zip([row for row in bars if row["code"] == "B"], (100, 101, 102), strict=True):
            row["close"] = price
    elif spec == "swap_index_weights":
        tables["memberships"][0]["weight"], tables["memberships"][1]["weight"] = 0.4, 0.6
    elif spec == "early_drift":
        from datetime import timedelta

        from hetu_quant.factors import close_cutoff

        day = query.trade_date - timedelta(days=1)
        return replace(query, trade_date=day, cutoff=close_cutoff(day))
    elif spec == "double_vol":
        bars[-1]["close"] = bars[-2]["close"] * (1 + 0.4 / sqrt(126))
    elif spec == "usd_second_leg":
        for row in bars:
            row.update(currency="USD" if row["code"] == "B" else "CNY", market="SH")
    else:
        raise AssertionError(spec)
    return query


def assert_reading(value, expected):
    if type(expected) in (float, int) or isinstance(expected, list) and all(type(x) in (int, float) for x in expected):
        assert value == pytest.approx(expected, abs=1e-8)
    else:
        assert value == expected


@pytest.mark.parametrize("factor_id", CASES)
def test_formula_discriminator(tmp_path, request, factor_evidence_root, factor_id):
    description, variant, value, detail = CASES[factor_id]
    source, doc, standard = example_query(factor_id)
    query = mutate(doc, standard, variant)
    expected = {"value": value, "components": detail} if not isinstance(detail, str) else {"field": detail, "rows": 0}
    target = tmp_path / f"{source.case_id}.json"
    target.write_text(json.dumps(doc))
    inputs = dict(
        case=source.case_id,
        variant=variant,
        description=description,
        day=str(query.trade_date),
        cutoff=query.cutoff.isoformat(),
        subjects=query.subjects,
        parameter_overrides=query.parameters,
    )
    with checked(
        factor_evidence_root,
        request.node.nodeid,
        factor_id,
        "formula:discriminator",
        expected,
        inputs=inputs,
        replay=(query, doc),
    ) as actual:
        result = compute_factor(None, TeachingSource(source.case_id, tmp_path), query)
        actual.update(status=result.status, gaps=result.gaps, parameters=result.parameters)
        if isinstance(detail, str):
            assert not result.rows
            assert any(gap["field"] == detail for gap in result.gaps)
        else:
            assert result.rows, result.gaps
            readings = [row["value"] for row in result.rows] if isinstance(value, list) else result.rows[0]["value"]
            assert_reading(readings, value)
            actual["rows"] = [
                {key: row[key] for key in ("subject", "value", "unit", "components")} for row in result.rows
            ]
            for key, expected_value in detail.items():
                assert_reading(result.rows[0]["components"][key], expected_value)


def test_each_definition_has_formula_discriminator():
    from hetu_quant.factors import list_factors

    assert set(CASES) | {item[0] for item in CHANGES} == {item.factor_id for item in list_factors()}
