"""Deterministic Decimal calculations for detecting conflicting claims.

The calculators keep both the claimed and the recomputed value plus their
absolute difference, never swap source labels, and never rewrite either side.
All CLI results serialize numbers as Decimal strings, not floats.

CLI: ``numeric_consistency.py --operation OP --input IN --output OUT`` where the
input JSON object holds the explicit parameters for one operation:
``gross_margin_percent`` (revenue, revenue_unit, cost, cost_unit),
``debt_ratio_percent`` (liabilities, liabilities_unit, assets, assets_unit),
``market_cap_values`` (price, price_unit, total_shares, total_shares_unit,
float_shares, float_shares_unit), ``price_earnings_ratio`` (market_cap,
market_cap_unit, attributable_profit, attributable_profit_unit), or
``compare_metric``
(metric, unit, claimed, recomputed, tolerance), where ``unit`` is the required
explicit unit of the compared metric. Exit 0 writes a success envelope;
exit 1 writes a failure envelope for unparseable input or rejected parameters;
exit 2 covers argument errors, unreadable input, an output path equal to the
input path, or an existing output file.
"""

from __future__ import annotations

import argparse
import decimal
import sys
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import _artifact_io

# Every computation runs inside this pinned context so results never depend on
# the ambient decimal context (module default: prec=28, ROUND_HALF_EVEN,
# default traps).
_DECIMAL_CONTEXT = Context(prec=28, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True)
class MetricComparison:
    metric: str
    unit: str
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


def _unit(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty unit string")
    return value.strip()


def _require_matching_units(first: str, second: str, *, first_name: str, second_name: str) -> None:
    if first != second:
        raise ValueError(
            f"{first_name} unit {first!r} and {second_name} unit {second!r} must match; "
            "convert outside the tool or resubmit consistent units"
        )


def gross_margin_percent(
    *,
    revenue: Decimal | str | int,
    revenue_unit: str,
    cost: Decimal | str | int,
    cost_unit: str,
) -> Decimal:
    revenue_unit_value = _unit(revenue_unit, name="revenue_unit")
    cost_unit_value = _unit(cost_unit, name="cost_unit")
    _require_matching_units(
        revenue_unit_value, cost_unit_value, first_name="revenue", second_name="cost"
    )
    revenue_value = _decimal(revenue, name="revenue")
    if revenue_value == 0:
        raise ValueError("revenue must not be zero")
    cost_value = _decimal(cost, name="cost")
    with decimal.localcontext(_DECIMAL_CONTEXT):
        return (revenue_value - cost_value) / revenue_value * Decimal("100")


def debt_ratio_percent(
    *,
    liabilities: Decimal | str | int,
    liabilities_unit: str,
    assets: Decimal | str | int,
    assets_unit: str,
) -> Decimal:
    liabilities_unit_value = _unit(liabilities_unit, name="liabilities_unit")
    assets_unit_value = _unit(assets_unit, name="assets_unit")
    _require_matching_units(
        liabilities_unit_value, assets_unit_value,
        first_name="liabilities", second_name="assets",
    )
    assets_value = _decimal(assets, name="assets")
    if assets_value == 0:
        raise ValueError("assets must not be zero")
    liabilities_value = _decimal(liabilities, name="liabilities")
    with decimal.localcontext(_DECIMAL_CONTEXT):
        return liabilities_value / assets_value * Decimal("100")


def market_cap_values(
    *,
    price: Decimal | str | int,
    price_unit: str,
    total_shares: Decimal | str | int,
    total_shares_unit: str,
    float_shares: Decimal | str | int,
    float_shares_unit: str,
) -> tuple[Decimal, Decimal]:
    _unit(price_unit, name="price_unit")
    total_share_unit_value = _unit(total_shares_unit, name="total_shares_unit")
    float_share_unit_value = _unit(float_shares_unit, name="float_shares_unit")
    _require_matching_units(
        total_share_unit_value, float_share_unit_value,
        first_name="total_shares", second_name="float_shares",
    )
    price_value = _decimal(price, name="price")
    total_share_value = _decimal(total_shares, name="total_shares")
    float_share_value = _decimal(float_shares, name="float_shares")
    with decimal.localcontext(_DECIMAL_CONTEXT):
        return price_value * total_share_value, price_value * float_share_value


def price_earnings_ratio(
    *,
    market_cap: Decimal | str | int,
    market_cap_unit: str,
    attributable_profit: Decimal | str | int,
    attributable_profit_unit: str,
) -> Decimal:
    market_cap_unit_value = _unit(market_cap_unit, name="market_cap_unit")
    profit_unit_value = _unit(attributable_profit_unit, name="attributable_profit_unit")
    _require_matching_units(
        market_cap_unit_value, profit_unit_value,
        first_name="market_cap", second_name="attributable_profit",
    )
    profit_value = _decimal(attributable_profit, name="attributable_profit")
    if profit_value == 0:
        raise ValueError("attributable_profit must not be zero")
    market_cap_value = _decimal(market_cap, name="market_cap")
    with decimal.localcontext(_DECIMAL_CONTEXT):
        return market_cap_value / profit_value


def compare_metric(
    *,
    metric: str,
    unit: str,
    claimed: Decimal | str | int,
    recomputed: Decimal | str | int,
    tolerance: Decimal | str | int,
) -> MetricComparison:
    if not isinstance(metric, str) or not metric.strip():
        raise ValueError("metric must be a non-empty string")
    if not isinstance(unit, str) or not unit.strip():
        raise ValueError("unit must be a non-empty string")
    claimed_value = _decimal(claimed, name="claimed")
    recomputed_value = _decimal(recomputed, name="recomputed")
    tolerance_value = _decimal(tolerance, name="tolerance")
    if tolerance_value < 0:
        raise ValueError("tolerance must not be negative")
    with decimal.localcontext(_DECIMAL_CONTEXT):
        difference = abs(claimed_value - recomputed_value)
        return MetricComparison(
            metric=metric,
            unit=unit,
            claimed=claimed_value,
            recomputed=recomputed_value,
            tolerance=tolerance_value,
            consistent=difference <= tolerance_value,
            absolute_difference=difference,
        )


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


_OPERATION_PARAMS: dict[str, tuple[str, ...]] = {
    "gross_margin_percent": ("revenue", "revenue_unit", "cost", "cost_unit"),
    "debt_ratio_percent": ("liabilities", "liabilities_unit", "assets", "assets_unit"),
    "market_cap_values": (
        "price", "price_unit", "total_shares", "total_shares_unit",
        "float_shares", "float_shares_unit",
    ),
    "price_earnings_ratio": (
        "market_cap", "market_cap_unit", "attributable_profit", "attributable_profit_unit",
    ),
    "compare_metric": ("metric", "unit", "claimed", "recomputed", "tolerance"),
}


def _gross_margin_result(payload: dict[str, Any]) -> dict[str, str]:
    value = gross_margin_percent(
        revenue=payload["revenue"],
        revenue_unit=payload["revenue_unit"],
        cost=payload["cost"],
        cost_unit=payload["cost_unit"],
    )
    return {
        "value": _decimal_text(value),
        "input_units": {"revenue": payload["revenue_unit"], "cost": payload["cost_unit"]},
        "result_unit": "%",
    }


def _debt_ratio_result(payload: dict[str, Any]) -> dict[str, str]:
    value = debt_ratio_percent(
        liabilities=payload["liabilities"],
        liabilities_unit=payload["liabilities_unit"],
        assets=payload["assets"],
        assets_unit=payload["assets_unit"],
    )
    return {
        "value": _decimal_text(value),
        "input_units": {
            "liabilities": payload["liabilities_unit"],
            "assets": payload["assets_unit"],
        },
        "result_unit": "%",
    }


def _market_cap_result(payload: dict[str, Any]) -> dict[str, str]:
    total, floating = market_cap_values(
        price=payload["price"],
        price_unit=payload["price_unit"],
        total_shares=payload["total_shares"],
        total_shares_unit=payload["total_shares_unit"],
        float_shares=payload["float_shares"],
        float_shares_unit=payload["float_shares_unit"],
    )
    return {
        "total_market_cap": _decimal_text(total),
        "float_market_cap": _decimal_text(floating),
        "input_units": {
            "price": payload["price_unit"],
            "total_shares": payload["total_shares_unit"],
            "float_shares": payload["float_shares_unit"],
        },
        "result_unit": f"{payload['price_unit']}·{payload['total_shares_unit']}",
    }


def _price_earnings_result(payload: dict[str, Any]) -> dict[str, str]:
    value = price_earnings_ratio(
        market_cap=payload["market_cap"],
        market_cap_unit=payload["market_cap_unit"],
        attributable_profit=payload["attributable_profit"],
        attributable_profit_unit=payload["attributable_profit_unit"],
    )
    return {
        "value": _decimal_text(value),
        "input_units": {
            "market_cap": payload["market_cap_unit"],
            "attributable_profit": payload["attributable_profit_unit"],
        },
        "result_unit": "ratio",
    }


def _compare_metric_result(payload: dict[str, Any]) -> dict[str, str | bool]:
    comparison = compare_metric(
        metric=payload["metric"],
        unit=payload["unit"],
        claimed=payload["claimed"],
        recomputed=payload["recomputed"],
        tolerance=payload["tolerance"],
    )
    return {
        "metric": comparison.metric,
        "unit": comparison.unit,
        "claimed": _decimal_text(comparison.claimed),
        "recomputed": _decimal_text(comparison.recomputed),
        "tolerance": _decimal_text(comparison.tolerance),
        "consistent": comparison.consistent,
        "absolute_difference": _decimal_text(comparison.absolute_difference),
    }


_OPERATIONS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "gross_margin_percent": _gross_margin_result,
    "debt_ratio_percent": _debt_ratio_result,
    "market_cap_values": _market_cap_result,
    "price_earnings_ratio": _price_earnings_result,
    "compare_metric": _compare_metric_result,
}


def _transform(operation: str) -> _artifact_io.Transform:
    def transform(payload: dict[str, Any]) -> object:
        expected = _OPERATION_PARAMS[operation]
        missing = [name for name in expected if name not in payload]
        if missing:
            raise ValueError(f"missing parameter(s): {', '.join(missing)}")
        unexpected = sorted(set(payload) - set(expected) - {"source"})
        if unexpected:
            raise ValueError(f"unexpected parameter(s): {', '.join(unexpected)}")
        return _OPERATIONS[operation](payload)

    return transform


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one deterministic numeric consistency operation."
    )
    parser.add_argument("--operation", required=True, choices=sorted(_OPERATIONS))
    parser.add_argument("--input", required=True, help="operation parameters JSON path")
    parser.add_argument("--output", required=True, help="envelope JSON path to create")
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    output_path = Path(args.output)
    if input_path.resolve() == output_path.resolve():
        print("output_path must differ from input_path", file=sys.stderr)
        return 2
    if output_path.exists():
        print("output_path already exists", file=sys.stderr)
        return 2
    if not input_path.is_file():
        print(f"input file not found: {input_path}", file=sys.stderr)
        return 2

    try:
        _artifact_io.run_transform(
            tool_name="numeric_consistency",
            input_path=input_path,
            output_path=output_path,
            transform=_transform(args.operation),
        )
    except _artifact_io.OutputConflictError as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 2
    except _artifact_io.InputUnreadableError as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 2
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
