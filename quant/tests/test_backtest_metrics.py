"""二期 03.2 机制层指标：换手/费用/成交率、子区间、低波动诊断、块 bootstrap。

期望全部手算（小例可逐笔核对），不读取生产侧答案；bootstrap 合同固定
块长 3 月／1,000 次／seed 20261017，输出净超额与风险改善的 95% 区间，
点估计目标、统计支持与结论分开，区间跨零不得宣称统计支持。
"""

from __future__ import annotations

import pandas as pd
import pytest

from hetu_quant.backtest_evaluation import (
    block_bootstrap_intervals,
    factor_diagnostics,
    industry_exposure,
    monthly_aligned_returns,
    subperiod_metrics,
    trade_metrics,
)


def _nav(days: list[str], net: list[float], bench: list[float] | None = None) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": days,
            "gross_nav": net,
            "net_nav": net,
            "benchmark_nav": bench if bench is not None else [float("nan")] * len(days),
        }
    )


def _trades(rows: list[tuple]) -> pd.DataFrame:
    """(executed_at, code, side, qty, price) → 固定轨迹 trades 表。"""
    frames = {
        "trade_id": [], "order_id": [], "executed_at": [], "code": [], "side": [],
        "qty": [], "price_raw": [], "execution_price": [], "commission": [],
        "stamp_tax": [], "transfer_fee": [], "dividend_tax": [], "slippage_cost": [],
    }
    for index, (day, code, side, qty, price) in enumerate(rows):
        frames["trade_id"].append(f"t{index}")
        frames["order_id"].append(f"o{index}")
        for key, value in (
            ("executed_at", day), ("code", code), ("side", side),
            ("qty", qty), ("price_raw", price), ("execution_price", price),
            ("commission", 0.0), ("stamp_tax", 0.0), ("transfer_fee", 0.0),
            ("dividend_tax", 0.0), ("slippage_cost", 0.0),
        ):
            frames[key].append(value)
    return pd.DataFrame(frames)


def _costs_from_trades(trades: pd.DataFrame, amounts: dict[str, float]) -> pd.DataFrame:
    rows = []
    for trade in trades.itertuples():
        for cost_type, column in (
            ("commission", "commission"), ("stamp_tax", "stamp_tax"),
            ("transfer_fee", "transfer_fee"), ("dividend_tax", "dividend_tax"),
            ("slippage_cost", "slippage_cost"),
        ):
            value = float(getattr(trade, column)) or amounts.get(f"{trade.trade_id}:{cost_type}", 0.0)
            if value:
                rows.append(
                    {
                        "date": trade.executed_at,
                        "order_id": trade.order_id,
                        "code": trade.code,
                        "cost_type": cost_type,
                        "amount": value,
                    }
                )
    return pd.DataFrame(rows, columns=["date", "order_id", "code", "cost_type", "amount"])


def test_trade_metrics_turnover_costs_fill_rate() -> None:
    """手算：单边换手 =（买+卖成交额）/2/区间平均净值；费用分项与成交率。"""
    days = ["2024-01-02", "2024-01-03", "2024-01-04"]
    nav = _nav(days, [10000.0, 9900.0, 10100.0])
    trades = _trades(
        [
            ("2024-01-02", "A", "BUY", 1000, 10.0),   # 买 10,000
            ("2024-01-03", "A", "SELL", 600, 12.0),   # 卖 7,200
        ]
    )
    orders = pd.DataFrame(
        {
            "order_id": ["o0", "o1", "o2"],
            "requested_qty": [1000, 600, 500],
            "filled_qty": [1000, 600, 100],
            "status": ["FILLED", "FILLED", "CANCELLED"],
        }
    )
    costs = _costs_from_trades(trades, {"t0:commission": 5.0, "t1:stamp_tax": 7.2})
    metrics = trade_metrics({"trades": trades, "orders": orders, "nav": nav, "costs": costs})

    avg_nav = (10000.0 + 9900.0 + 10100.0) / 3  # 10,000
    assert metrics["traded_value"] == pytest.approx(17200.0)
    assert metrics["buy_value"] == pytest.approx(10000.0)
    assert metrics["sell_value"] == pytest.approx(7200.0)
    assert metrics["turnover_oneway"] == pytest.approx((10000.0 + 7200.0) / 2 / avg_nav)
    assert metrics["total_costs"] == pytest.approx(12.2)
    assert metrics["cost_breakdown"] == {"commission": 5.0, "stamp_tax": 7.2}
    # 成交率按数量：1,700 / 2,100
    assert metrics["fill_rate_qty"] == pytest.approx(1700 / 2100)
    assert metrics["cost_bps"] == pytest.approx(12.2 / 17200.0 * 1e4)
    # 容量使用需要 bar 量与参与率参数，固定轨迹表不可计算：逐项说明，不静默
    assert metrics["capacity_usage"] == "not_available"


def test_trade_metrics_empty_tables() -> None:
    metrics = trade_metrics(
        {
            "trades": pd.DataFrame(columns=_trades([]).columns),
            "orders": pd.DataFrame(columns=["order_id", "requested_qty", "filled_qty", "status"]),
            "nav": _nav(["2024-01-02"], [1e6]),
            "costs": pd.DataFrame(columns=["date", "order_id", "code", "cost_type", "amount"]),
        }
    )
    assert metrics["traded_value"] == 0.0
    assert metrics["fill_rate_qty"] is None  # 无申报不定义成交率，不用 0 冒充


def test_subperiod_metrics_by_protocol_endpoints() -> None:
    """按协议端点切分子区间；锚点沿用评价语义（区间前最后一条净值）。"""
    days = ["2024-01-02", "2024-06-28", "2025-01-02", "2025-06-30"]
    nav = _nav(days, [1.00e6, 1.10e6, 1.21e6, 1.32e6], [1.0e6, 1.05e6, 1.10e6, 1.15e6])
    protocol = {
        "time_split": {
            "development": ["2024-01-02", "2024-06-28"],
            "validation": ["2024-07-01", "2025-01-02"],
            "final": ["2025-01-03", "2025-06-30"],
        }
    }
    result = subperiod_metrics(nav, protocol)
    dev = result["development"]
    # 锚点 = 区间前最后净值（2024-01-02 之前无记录 → 该段报证据不足）
    assert dev["net_return"] is None
    val = result["validation"]
    # 锚 2024-06-28 的 1.10e6 → 2025-01-02 的 1.21e6 = +10%
    assert val["net_return"] == pytest.approx(0.10)
    assert val["benchmark_return"] == pytest.approx(1.10 / 1.05 - 1.0)
    final = result["final"]
    assert final["net_return"] == pytest.approx(1.32 / 1.21 - 1.0)
    assert final["benchmark_return"] == pytest.approx(1.15 / 1.10 - 1.0)


def test_monthly_aligned_returns() -> None:
    days = ["2024-01-31", "2024-02-29", "2024-03-28", "2024-03-29"]
    nav = _nav(days, [1.00e6, 1.10e6, 1.10e6, 1.21e6], [1.0e6, 1.0e6, 1.05e6, 1.05e6])
    monthly = monthly_aligned_returns(nav)
    # 每月最后一条净值 → 月度收益：1 月无前置月不产生收益行
    assert monthly["month"].tolist() == ["2024-02", "2024-03"]
    assert monthly["net_return"].tolist()[0] == pytest.approx(0.10)
    assert monthly["net_return"].tolist()[1] == pytest.approx(0.10)
    assert monthly["benchmark_return"].tolist()[0] == pytest.approx(0.0)
    assert monthly["benchmark_return"].tolist()[1] == pytest.approx(0.05)


def test_block_bootstrap_contract_and_determinism() -> None:
    """合同：块长 3 月、1,000 次、seed 20261017；同输入同区间（确定性）；
    恒定正超额 → 区间不含零 → 统计支持；常数序列波动差为 0 的退化说明。"""
    months = pd.DataFrame(
        {
            "month": [f"2024-{m:02d}" for m in range(1, 13)],
            "net_return": [0.01] * 12,
            "benchmark_return": [0.0] * 12,
        }
    )
    stats = block_bootstrap_intervals(months)
    assert stats["block_months"] == 3
    assert stats["reps"] == 1000
    assert stats["seed"] == 20261017
    assert stats["n_months"] == 12
    lo, hi = stats["excess_return_ci95"]
    assert lo > 0 and hi > 0  # 恒定 +1%/月超额：区间不含零
    assert stats["statistical_support"]["excess"] is True
    # 基准常数 → 波动差退化，风险改善不成立也不宣称支持
    assert stats["vol_diff_ci95"] == pytest.approx([0.0, 0.0], abs=1e-12)
    assert stats["statistical_support"]["risk"] is False

    again = block_bootstrap_intervals(months)
    assert again["excess_return_ci95"] == stats["excess_return_ci95"]
    assert again["vol_diff_ci95"] == stats["vol_diff_ci95"]


def test_block_bootstrap_zero_crossing_has_no_support() -> None:
    """正负对称的零均值超额：区间跨零，不得宣称统计支持。"""
    months = pd.DataFrame(
        {
            "month": [f"2024-{m:02d}" for m in range(1, 13)],
            "net_return": [0.02, -0.02] * 6,
            "benchmark_return": [0.0] * 12,
        }
    )
    stats = block_bootstrap_intervals(months)
    lo, hi = stats["excess_return_ci95"]
    assert lo < 0 < hi
    assert stats["statistical_support"]["excess"] is False


def test_block_bootstrap_insufficient_months() -> None:
    months = pd.DataFrame(
        {
            "month": ["2024-01", "2024-02"],
            "net_return": [0.01, 0.01],
            "benchmark_return": [0.0, 0.0],
        }
    )
    stats = block_bootstrap_intervals(months)
    assert stats["insufficient"] is True
    assert stats["statistical_support"] == {"excess": None, "risk": None}


def test_factor_diagnostics_ic_rankic_deciles_coverage() -> None:
    """手算：factor 与 label 完全线性 → IC=RankIC=1.0；分 horizon 覆盖；
    样本不足十分组时按实际分组数出十分组均值并说明。"""
    rows = []
    factors = {"A": 4.0, "B": 3.0, "C": 2.0, "D": 1.0}
    labels = {"A": 0.10, "B": 0.20, "C": 0.30, "D": 0.40}  # 低 sigma（factor 高）→ 高标签
    for code in factors:
        for horizon in (1, 3):
            rows.append(
                {
                    "code": code,
                    "signal_at": "2024-01-31",
                    "horizon_months": horizon,
                    "mature_at": "2024-07-31",
                    "label_return": labels[code],
                    "factor_value": -factors[code],  # 定向因子 -sigma
                }
            )
    diag = factor_diagnostics(pd.DataFrame(rows))
    for horizon in ("1", "3"):
        block = diag["horizons"][horizon]
        assert block["coverage"] == 4
        assert block["ic"] == pytest.approx(1.0)
        assert block["rank_ic"] == pytest.approx(1.0)
    # n=4 < 10：十分组按实际 4 组出均值，不足说明存在
    deciles = diag["horizons"]["1"]["deciles"]
    assert len(deciles) == 4
    assert diag["notes"], "十分组不足与样本量说明不得为空"


def test_factor_diagnostics_requires_label_return() -> None:
    with pytest.raises(ValueError, match="label_return"):
        factor_diagnostics(
            pd.DataFrame(
                {
                    "code": ["A"],
                    "signal_at": ["2024-01-31"],
                    "horizon_months": [1],
                    "mature_at": ["2024-02-29"],
                }
            )
        )


def test_industry_exposure_unknown_without_historical_classification() -> None:
    exposure = industry_exposure()
    assert exposure["status"] == "unknown"
    assert "具证期间" in exposure["note"] or "回填" in exposure["note"]
