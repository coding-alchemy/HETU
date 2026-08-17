"""Deterministic Decimal calculations for detecting conflicting claims."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True)
class MetricComparison:
    metric: str
    claimed: Decimal
    recomputed: Decimal
    tolerance: Decimal
    consistent: bool
    absolute_difference: Decimal


def _decimal(value: Decimal | str | int, *, name: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be a decimal number") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


def gross_margin_percent(
    *,
    revenue: Decimal | str | int,
    cost: Decimal | str | int,
) -> Decimal:
    revenue_value = _decimal(revenue, name="revenue")
    if revenue_value == 0:
        raise ValueError("revenue must not be zero")
    cost_value = _decimal(cost, name="cost")
    return (revenue_value - cost_value) / revenue_value * Decimal("100")


def debt_ratio_percent(
    *,
    liabilities: Decimal | str | int,
    assets: Decimal | str | int,
) -> Decimal:
    assets_value = _decimal(assets, name="assets")
    if assets_value == 0:
        raise ValueError("assets must not be zero")
    liabilities_value = _decimal(liabilities, name="liabilities")
    return liabilities_value / assets_value * Decimal("100")


def market_cap_values(
    *,
    price: Decimal | str | int,
    total_shares: Decimal | str | int,
    float_shares: Decimal | str | int,
) -> tuple[Decimal, Decimal]:
    price_value = _decimal(price, name="price")
    total_share_value = _decimal(total_shares, name="total_shares")
    float_share_value = _decimal(float_shares, name="float_shares")
    return price_value * total_share_value, price_value * float_share_value


def price_earnings_ratio(
    *,
    market_cap: Decimal | str | int,
    attributable_profit: Decimal | str | int,
) -> Decimal:
    profit_value = _decimal(attributable_profit, name="attributable_profit")
    if profit_value == 0:
        raise ValueError("attributable_profit must not be zero")
    return _decimal(market_cap, name="market_cap") / profit_value


def compare_metric(
    *,
    metric: str,
    claimed: Decimal | str | int,
    recomputed: Decimal | str | int,
    tolerance: Decimal | str | int,
) -> MetricComparison:
    if not isinstance(metric, str) or not metric.strip():
        raise ValueError("metric must be a non-empty string")
    claimed_value = _decimal(claimed, name="claimed")
    recomputed_value = _decimal(recomputed, name="recomputed")
    tolerance_value = _decimal(tolerance, name="tolerance")
    if tolerance_value < 0:
        raise ValueError("tolerance must not be negative")
    difference = abs(claimed_value - recomputed_value)
    return MetricComparison(
        metric=metric,
        claimed=claimed_value,
        recomputed=recomputed_value,
        tolerance=tolerance_value,
        consistent=difference <= tolerance_value,
        absolute_difference=difference,
    )
