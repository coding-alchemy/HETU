"""二期 03.1 共同信号计算测试：可手算月收益核对原低波动规则。

事实来自 backtest_cases 的 history/signal_pool 变体（±振幅交替月收益、
边界并列、35 月、缺月、未来市值）；期望值按规则手算留在本测试侧。
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import pytest

from backtest_cases import build_case_store, case_definition, make_backtest_case
from fixtures import annotate_actions_coverage
from hetu_quant.strategies import build_signals, low_volatility_weights

# 36 个 ±1% 交替月收益的样本标准差（ddof=1）：mean=0，36a²/35 开方
EXPECTED_VOL_1PCT = 0.01 * math.sqrt(36 / 35)


def _pure_returns(amplitudes: dict[str, float], months: int = 36) -> pd.DataFrame:
    rows = []
    for code, amplitude in amplitudes.items():
        for t in range(months):
            rows.append(
                {
                    "code": code,
                    "month_end": f"m{t:02d}",
                    "month_return": amplitude if t % 2 == 0 else -amplitude,
                }
            )
    return pd.DataFrame(rows)


def _caps(codes: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {"code": codes, "market_cap_cny": [1e9 * (i + 1) for i in range(len(codes))]}
    )


def test_low_volatility_keeps_original_signal_rules() -> None:
    amplitudes = {
        "VOL_01": 0.01,
        "VOL_02": 0.01,  # 边界并列
        **{f"VOL_{k:02d}": 0.01 * (k - 1) for k in range(3, 13)},
    }
    codes = sorted(amplitudes)
    out = low_volatility_weights(_pure_returns(amplitudes), _caps(codes), lookback_months=36)
    # N=12 → max(1, floor(12/10))=1；并列按代码稳定截取
    assert out["code"].tolist() == ["VOL_01"]
    assert abs(out["signal_value"].iloc[0] - EXPECTED_VOL_1PCT) <= 1e-12
    assert abs(out["target_weight"].sum() - 1.0) <= 1e-10


def test_low_volatility_weights_rejects_short_window_and_unknown_cap() -> None:
    amplitudes = {f"VOL_{k:02d}": 0.01 * k for k in range(1, 11)}  # 10 个全窗口
    frame = pd.concat([_pure_returns(amplitudes), _pure_returns({"SHORT_35": 0.005}, months=35)])
    codes = sorted(amplitudes)
    out = low_volatility_weights(frame, _caps(codes), lookback_months=36)
    assert "SHORT_35" not in set(out["code"])  # 35 个月收益不得产合格信号
    assert out["code"].tolist() == ["VOL_01"]  # max(1, floor(10/10))

    # 入选但市值未知：不产生权重
    no_cap = low_volatility_weights(
        _pure_returns(amplitudes), _caps(codes[1:]), lookback_months=36
    )
    assert "VOL_01" not in set(no_cap["code"])

    # 合格池不足 10：不产合格信号
    small = {f"C{k}": 0.01 * k for k in range(1, 6)}
    small_out = low_volatility_weights(_pure_returns(small), _caps(sorted(small)))
    assert len(small_out) == 0


def test_build_signals_uses_historical_universe(tmp_path: Path) -> None:
    case = make_backtest_case("history", variant="signal_pool")
    store, ref = build_case_store(tmp_path, case)
    coverage = case["actions_coverage"]  # type: ignore[index]
    annotate_actions_coverage(
        store, ref, coverage["start"], coverage["end"],
        codes=list(case["codes"]), query_basis="ex_date",
    )
    signals = build_signals(case_definition(case, ref.snapshot_id), store)

    last = signals[signals["signal_at"] == "2024-01-31"]
    assert last["code"].tolist() == ["VOL_01"]
    row = last.iloc[0]
    assert abs(float(row["signal_value"]) - EXPECTED_VOL_1PCT) <= 1e-9
    assert abs(float(last["target_weight"].sum()) - 1.0) <= 1e-10
    assert row["executable_from"] == "2024-02-01"  # 收盘信号次日才可执行
    assert int(row["rank"]) == 1
    assert row["source_ref"] == ref.snapshot_id

    # 35 月、缺月、未来市值均不得产合格信号
    for code in ("SHORT_35", "GAP_MONTH", "FUTURE_MCAP"):
        assert code not in set(signals["code"])

    # 可交易账户基准：同一历史合格池、市值权重、同月调仓
    vw = build_signals(
        case_definition(case, ref.snapshot_id, strategy_id="universe_vw"), store
    )
    vw_last = vw[vw["signal_at"] == "2024-01-31"]
    assert "VOL_01" in set(vw_last["code"])
    assert "FUTURE_MCAP" not in set(vw_last["code"])  # 市值当时不可知
    assert abs(float(vw_last["target_weight"].sum()) - 1.0) <= 1e-10

    # 未登记策略（E/P 未接入）拒绝
    with pytest.raises(ValueError, match="strategy_id"):
        build_signals(case_definition(case, ref.snapshot_id, strategy_id="ep_value"), store)


def test_market_cap_uses_signal_time_known_version() -> None:
    """1 月 31 日信号只能用当时已披露市值：2 月 1 日才公开的 999 不得取代已知的 100。"""
    from hetu_quant.strategies import _known_market_cap

    caps = pd.DataFrame(
        [
            {"code": "X", "date": "2024-01-31", "market_cap_cny": 100.0,
             "available_at": "2024-01-30T18:00:00+08:00"},
            {"code": "X", "date": "2024-01-31", "market_cap_cny": 999.0,
             "available_at": "2024-02-01T09:00:00+08:00"},
        ]
    )
    assert _known_market_cap(caps, "X", "2024-01-31") == 100.0
