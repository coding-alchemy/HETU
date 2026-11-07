"""收益口径：价格收益与税前总收益手算断言；口径混用与未核验事件被拒绝。"""

from __future__ import annotations

import pandas as pd
import pytest

from fixtures import make_case
from hetu_quant.data import (
    UnsupportedActionError,
    assert_single_return_basis,
    build_price_series,
    build_total_return_series,
)


def _bars(closes: list[float], dates: list[str] | None = None, code: str = "T001") -> pd.DataFrame:
    dates = dates or [f"2024-06-{day:02d}" for day in range(3, 3 + len(closes))]
    return pd.DataFrame(
        {
            "date": dates,
            "code": [code] * len(closes),
            "close_raw": closes,
            "volume_shares": [1000.0] * len(closes),
            "amount_cny": [10000.0] * len(closes),
        }
    )


def _actions(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(
        rows,
        columns=["code", "ex_date", "action_type", "m", "d", "terms_raw", "available_at"],
    )


def test_no_action_series_equals_raw() -> None:
    case = make_case("price_series", "base")
    bars = _bars(case["closes"], case["dates"], code=case["code"])
    actions = _actions([])
    price = build_price_series(bars, actions).set_index("date")
    # 无事件：连续价格等于原价；区间收益 90/100-1 = -10%，最大回撤 50%（手算事实）
    assert price["close_price_return"].tolist() == case["closes"]
    assert price["close_price_return"].iloc[-1] / price["close_price_return"].iloc[0] - 1 == pytest.approx(-0.1)


def test_split_bonus_return_is_zero() -> None:
    # 每原股送一股（m=2），价格 10 -> 5：连续价格收益 0%
    bars = _bars([10.0, 5.0], ["2024-06-03", "2024-06-04"])
    actions = _actions(
        [
            {
                "code": "T001",
                "ex_date": "2024-06-04",
                "action_type": "bonus_split",
                "m": 2.0,
                "d": 0.0,
                "terms_raw": "10送10股",
                "available_at": "2024-06-01",
            }
        ]
    )
    price = build_price_series(bars, actions).set_index("date")
    split_return = price.loc["2024-06-04", "close_price_return"] / price.loc[
        "2024-06-03", "close_price_return"
    ] - 1
    assert split_return == pytest.approx(0)


def test_cash_dividend_price_return_minus_ten_percent() -> None:
    # 税前现金 1 元，价格 10 -> 9：价格收益 -10%
    bars = _bars([10.0, 9.0], ["2024-06-03", "2024-06-04"])
    actions = _actions(
        [
            {
                "code": "T001",
                "ex_date": "2024-06-04",
                "action_type": "cash_dividend",
                "m": 1.0,
                "d": 1.0,
                "terms_raw": "10派10元(含税)",
                "available_at": "2024-06-01",
            }
        ]
    )
    price = build_price_series(bars, actions).set_index("date")
    cash_ex_return = price.loc["2024-06-04", "close_price_return"] / price.loc[
        "2024-06-03", "close_price_return"
    ] - 1
    assert cash_ex_return == pytest.approx(-0.1)


def test_total_return_cash_dividend_is_zero() -> None:
    # 税前现金 1 元、价格 10 -> 9：税前总收益为 0
    bars = _bars([10.0, 9.0], ["2024-06-03", "2024-06-04"])
    actions = _actions(
        [
            {
                "code": "T001",
                "ex_date": "2024-06-04",
                "action_type": "cash_dividend",
                "m": 1.0,
                "d": 1.0,
                "terms_raw": "10派10元(含税)",
                "available_at": "2024-06-01",
            }
        ]
    )
    total = build_total_return_series(bars, actions).set_index("date")
    total_return = total.loc["2024-06-04", "close_total_return"] / total.loc[
        "2024-06-03", "close_total_return"
    ] - 1
    assert total_return == pytest.approx(0)


def test_unverified_action_type_limits_object() -> None:
    # 配股条款未核验：对象级缺口，不按公式硬套
    bars = _bars([10.0, 8.0], ["2024-06-03", "2024-06-04"])
    actions = _actions(
        [
            {
                "code": "T001",
                "ex_date": "2024-06-04",
                "action_type": "rights_issue",
                "m": None,
                "d": None,
                "terms_raw": "10配3股 配股价6元",
                "available_at": "2024-06-01",
            }
        ]
    )
    with pytest.raises(UnsupportedActionError, match="T001"):
        build_price_series(bars, actions)
    with pytest.raises(UnsupportedActionError, match="T001"):
        build_total_return_series(bars, actions)


def test_mixed_return_basis_rejected() -> None:
    price = pd.DataFrame(
        {"code": ["I1"], "date": ["2024-06-03"], "close": [100.0], "return_basis": ["price"]}
    )
    qfq = pd.DataFrame(
        {"code": ["T1"], "date": ["2024-06-03"], "close": [100.0], "return_basis": ["qfq"]}
    )
    with pytest.raises(ValueError, match="return_basis"):
        assert_single_return_basis([price, qfq])
    assert_single_return_basis([price, price.copy()])
