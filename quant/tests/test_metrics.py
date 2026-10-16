"""03.1 原始指标手算：收益、回撤、上涨成交额占比、60 日波动与自然月锚点。

公式与窗口以 03 计划 §2 为准；预期答案全部在本文件手算侧。
"""

from __future__ import annotations

import math
import statistics
from datetime import date

import pandas as pd
import pytest

from hetu_quant.metrics import compute_metrics

CUTOFF = date(2024, 6, 28)
PARAMS: dict = {}


def _bdays(periods: int) -> list[str]:
    days = pd.bdate_range(end="2024-06-28", periods=periods)
    return [d.strftime("%Y-%m-%d") for d in days]


def _prices(
    days: list[str], closes: list[float], amounts: list[float] | None = None
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": days,
            "close": closes,
            "amount_cny": amounts if amounts is not None else [1e7] * len(days),
        }
    )


def _benchmark(
    days: list[str], close: float = 4000.0, basis: str = "price"
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": days,
            "close": [close] * len(days),
            "return_basis": [basis] * len(days),
        }
    )


def test_handcalc_return_and_drawdown() -> None:
    """价格 [100,120,60,90]：半年收益 −10%，半年最大回撤 50%。

    复用 01 已核对的原始序列；只含锚点与截止日，60 日窗口不足须报缺口而非补零。
    """
    days = ["2023-12-28", "2024-03-28", "2024-06-27", "2024-06-28"]
    result = compute_metrics(
        _prices(days, [100.0, 120.0, 60.0, 90.0]),
        _benchmark(days),
        CUTOFF,
        PARAMS,
    )
    assert result["return_6m"] == pytest.approx(-0.10)
    assert result["max_drawdown_6m"] == pytest.approx(0.50)
    assert result["return_3m"] == pytest.approx(90.0 / 120.0 - 1)
    assert result["excess_6m"] == pytest.approx(-0.10), "基准常数时相对收益等于绝对收益"
    assert result["volatility_60d"] is None
    assert result["up_amount_60d"] is None
    assert any("60d" in gap or "60 日" in gap for gap in result["gaps"])


def test_up_amount_numerator_denominator_and_flat_days() -> None:
    """上涨成交额占比：P_t > P_{t-1} 的成交额进分子，平盘只进分母。

    61 个市场日：第 1 日上涨（成交额 100），第 2 日下跌（200），第 3 日平盘（400），
    其余 57 日平盘各 50。分子 = 100，分母 = 100+200+400+57×50 = 3550。
    """
    days = _bdays(61)
    closes = [100.0, 110.0, 90.0] + [90.0] * 58
    amounts = [100.0, 100.0, 200.0, 400.0] + [50.0] * 57
    result = compute_metrics(
        _prices(days, closes, amounts), _benchmark(days), CUTOFF, PARAMS
    )
    assert result["up_amount_60d"] == pytest.approx(100.0 / 3550.0)
    assert not any("60d" in gap or "60 日" in gap for gap in result["gaps"]), (
        f"60 日窗口齐全时不应有 60 日缺口: {result['gaps']}"
    )


def test_volatility_uses_sample_std_of_60_daily_returns() -> None:
    """60 日波动 = 60 个日简单收益的样本标准差 × sqrt(252)。"""
    days = _bdays(61)
    base = [0.01, -0.02, 0.015, 0.0, -0.01, 0.005, 0.02, -0.015, 0.01, 0.0]
    returns = [base[i % len(base)] * (1 + (i // len(base)) * 0.1) for i in range(60)]
    closes = [100.0]
    for ret in returns:
        closes.append(closes[-1] * (1 + ret))
    result = compute_metrics(
        _prices(days, closes), _benchmark(days), CUTOFF, PARAMS
    )
    expected = statistics.stdev(returns) * math.sqrt(252)
    assert result["volatility_60d"] == pytest.approx(expected, rel=1e-9)

    flat = compute_metrics(
        _prices(days, [100.0] * 61), _benchmark(days), CUTOFF, PARAMS
    )
    assert flat["volatility_60d"] == pytest.approx(0.0), "常数序列波动为 0"
    assert flat["up_amount_60d"] == pytest.approx(0.0), "全平盘只进分母，占比为 0"


def test_natural_month_anchor_across_holiday_gap() -> None:
    """半年／三月锚点按自然月边界取边界当日或之前最近市场交易日，跨长假不平移。"""
    days = [
        d.strftime("%Y-%m-%d")
        for d in pd.bdate_range("2023-08-01", "2024-02-29")
        if not ("2024-02-05" <= d.strftime("%Y-%m-%d") <= "2024-02-16")
    ]
    assert "2024-02-19" in days and "2024-02-16" not in days
    closes = [100.0 + i for i in range(len(days))]
    cutoff = date(2024, 2, 29)
    result = compute_metrics(
        _prices(days, closes), _benchmark(days), cutoff, PARAMS
    )
    anchor_6m = max(d for d in days if d <= "2023-08-29")
    anchor_3m = max(d for d in days if d <= "2023-11-29")
    assert anchor_6m == "2023-08-29"
    assert anchor_3m == "2023-11-29"
    end = closes[-1]
    assert result["return_6m"] == pytest.approx(end / closes[days.index(anchor_6m)] - 1)
    assert result["return_3m"] == pytest.approx(end / closes[days.index(anchor_3m)] - 1)
    assert result["gaps"] == ()


def test_missing_sessions_return_none_not_zero() -> None:
    """缺半年内任一交易日：相关指标返回 None 与缺口说明，绝不补零。"""
    days = _bdays(140)
    assert days[0] <= "2023-12-28", "测试日历须覆盖半年锚点"
    broken = [d for d in days if d != days[len(days) // 2]]
    closes = [100.0 + i for i in range(len(broken))]
    result = compute_metrics(
        _prices(broken, closes), _benchmark(days), CUTOFF, PARAMS
    )
    assert result["return_6m"] is None
    assert result["max_drawdown_6m"] is None
    assert result["excess_6m"] is None
    assert any("half_year" in gap or "半年" in gap for gap in result["gaps"])
    assert all(result[key] is None for key in ("return_6m", "excess_6m"))


def test_zero_amount_60d_up_amount_not_computable() -> None:
    """60 日成交额全零：上涨成交额占比不可计算，不补零。"""
    days = _bdays(61)
    closes = [100.0 * (1.01 ** i) for i in range(61)]
    result = compute_metrics(
        _prices(days, closes, [0.0] * 61), _benchmark(days), CUTOFF, PARAMS
    )
    assert result["up_amount_60d"] is None
    assert any("amount" in gap or "成交额" in gap for gap in result["gaps"])


def test_benchmark_windows_missing_excess_none() -> None:
    """基准缺半年锚点：相对收益 None；不同收益口径直接拒绝。"""
    days = _bdays(70)
    closes = [100.0 + i for i in range(len(days))]
    bench_days = [d for d in days if d < "2023-12-28"] + days[-3:]
    bench = _benchmark(bench_days)
    result = compute_metrics(_prices(days, closes), bench, CUTOFF, PARAMS)
    assert result["excess_6m"] is None
    assert any("benchmark" in gap for gap in result["gaps"])

    with pytest.raises(ValueError, match="return_basis|口径"):
        compute_metrics(
            _prices(days, closes),
            _benchmark(days, basis="total_return"),
            CUTOFF,
            PARAMS,
        )
