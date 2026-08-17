from __future__ import annotations

from decimal import Decimal

from numeric_consistency import (
    compare_metric,
    debt_ratio_percent,
    gross_margin_percent,
    market_cap_values,
    price_earnings_ratio,
)


def test_glm_gross_margin_mapping_is_rejected_by_absolute_value_check() -> None:
    recomputed = gross_margin_percent(
        revenue="10322863908.82",
        cost="6113944336.44",
    )
    check = compare_metric(
        metric="gross_margin_percent",
        claimed="50.009533175",
        recomputed=recomputed,
        tolerance="0.01",
    )

    assert recomputed.quantize(Decimal("0.0001")) == Decimal("40.7728")
    assert check.consistent is False
    assert check.absolute_difference > Decimal("9")


def test_glm_claimed_gross_margin_matches_debt_ratio_instead() -> None:
    ratio = debt_ratio_percent(
        liabilities="45537075214.11",
        assets="91056789222.02",
    )

    assert ratio.quantize(Decimal("0.0001")) == Decimal("50.0095")


def test_market_cap_values_expose_swapped_total_and_float_labels() -> None:
    total, floating = market_cap_values(
        price="738.66",
        total_shares="725689200",
        float_shares="725109848",
    )

    total_check = compare_metric(
        metric="total_market_cap",
        claimed="535609640323.68",
        recomputed=total,
        tolerance="1",
    )
    float_check = compare_metric(
        metric="float_market_cap",
        claimed="536037584472.00",
        recomputed=floating,
        tolerance="1",
    )

    assert total_check.consistent is False
    assert float_check.consistent is False
    assert floating < total


def test_price_earnings_ratio_uses_matching_market_cap_and_profit_units() -> None:
    ratio = price_earnings_ratio(
        market_cap="536037584472",
        attributable_profit="5521993004.86",
    )

    assert ratio.quantize(Decimal("0.01")) == Decimal("97.07")
