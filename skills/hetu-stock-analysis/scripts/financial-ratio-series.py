"""Deterministic Decimal ratio series over standardized financial data.

The calculator consumes a ``FinancialSeriesInput`` JSON object: ``subject``,
``scope``, ``unit``, the required ``input_hash`` (lowercase hex, 8–64
characters, identifying the upstream normalized artifact, echoed verbatim) and
ascending ``periods``; every period explicitly provides
``revenue``, ``cost``, ``attributable_profit``, ``operating_cash_flow``,
``current_assets``, ``current_liabilities``, ``accounts_receivable``,
``inventory`` and ``total_assets``. It emits the fixed metric set per period
plus endpoint CAGR for revenue and attributable profit, serialized as Decimal
strings. The tool never selects a reporting caliber, never swaps or rewrites
source values, and never interprets results as good or bad; CAGR conditions
that do not hold produce a formal status and reason instead of a guess.

Period labels must parse as calendar dates (strict ``YYYY-MM-DD`` ISO or
``YYYYMMDD``) and the series must ascend by parsed date. CAGR annualization
spans are the true year distance between the endpoint dates, computed on the
ACT/365.25 convention as ``(end_date − start_date).days / 365.25`` — not the
number of period intervals.

CLI: ``financial-ratio-series.py --input IN --output OUT``. Exit 0 writes a
success envelope; exit 1 writes a failure envelope for unparseable input or
rejected series; exit 2 covers argument errors, unreadable input, an output
path equal to the input path, or an existing output file.
"""

from __future__ import annotations

import argparse
import decimal
import re
import sys
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import _artifact_io

# Every computation runs inside this pinned context so results never depend on
# the ambient decimal context (module default: prec=28, ROUND_HALF_EVEN,
# default traps).
_DECIMAL_CONTEXT = Context(prec=28, rounding=ROUND_HALF_EVEN)

# CAGR annualization counts true years on the ACT/365.25 convention.
_DAYS_PER_YEAR = Decimal("365.25")

_ISO_PERIOD_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
_COMPACT_PERIOD_PATTERN = re.compile(r"\d{8}")

# The upstream normalized artifact hash: lowercase hex, 8 to 64 characters.
_INPUT_HASH_PATTERN = re.compile(r"[0-9a-f]{8,64}")

PERIOD_FIELDS: tuple[str, ...] = (
    "revenue",
    "cost",
    "attributable_profit",
    "operating_cash_flow",
    "current_assets",
    "current_liabilities",
    "accounts_receivable",
    "inventory",
    "total_assets",
)

CAGR_QUANTUM = Decimal("0.1")


def _decimal(value: object, *, name: str, where: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{where}: {name} must be a decimal number") from exc
    if not result.is_finite():
        raise ValueError(f"{where}: {name} must be finite")
    return result


def _required_text(payload: dict[str, object], key: str) -> str:
    if key not in payload:
        raise ValueError(f"missing required field: {key}")
    value = payload[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _required_input_hash(payload: dict[str, object]) -> str:
    if "input_hash" not in payload:
        raise ValueError("missing required field: input_hash")
    value = payload["input_hash"]
    if not isinstance(value, str) or not _INPUT_HASH_PATTERN.fullmatch(value):
        raise ValueError(
            "input_hash must be a lowercase hex string of 8 to 64 characters "
            "identifying the upstream normalized artifact"
        )
    return value


def _period_date(label: object, *, where: str) -> date:
    if not isinstance(label, str):
        raise ValueError(f"{where}: period must be a YYYY-MM-DD or YYYYMMDD date")
    if _ISO_PERIOD_PATTERN.fullmatch(label):
        pattern = "%Y-%m-%d"
    elif _COMPACT_PERIOD_PATTERN.fullmatch(label):
        pattern = "%Y%m%d"
    else:
        raise ValueError(f"{where}: period must be a YYYY-MM-DD or YYYYMMDD date")
    try:
        return datetime.strptime(label, pattern).date()
    except ValueError as exc:
        raise ValueError(f"{where}: period must be a valid calendar date") from exc


def _period_records(raw_periods: object) -> list[dict[str, Any]]:
    if not isinstance(raw_periods, list) or not raw_periods:
        raise ValueError("periods must be a non-empty list")
    labels: list[str] = []
    records: list[dict[str, Any]] = []
    allowed = {"period", *PERIOD_FIELDS}
    for index, entry in enumerate(raw_periods):
        where = f"period[{index}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{where} must be an object")
        label = entry.get("period")
        period_date = _period_date(label, where=where)
        unexpected = sorted(set(entry) - allowed)
        if unexpected:
            raise ValueError(f"{where}: unexpected field(s): {', '.join(unexpected)}")
        missing = [name for name in PERIOD_FIELDS if name not in entry]
        if missing:
            raise ValueError(f"{where}: missing field(s): {', '.join(missing)}")
        labels.append(label)
        records.append(
            {
                "label": label,
                "date": period_date,
                "values": {
                    name: _decimal(entry[name], name=name, where=where)
                    for name in PERIOD_FIELDS
                },
            }
        )
    seen: set[str] = set()
    for label in labels:
        if label in seen:
            raise ValueError(f"duplicate period: {label}")
        seen.add(label)
    for earlier, later in zip(records, records[1:], strict=False):
        if later["date"] <= earlier["date"]:
            raise ValueError(
                "periods must be in ascending order: "
                f"{earlier['label']} then {later['label']}"
            )
    return records


def _percent(
    numerator: Decimal, denominator: Decimal, *, name: str, where: str
) -> Decimal:
    if denominator == 0:
        raise ValueError(f"{where}: {name} must not be zero")
    return numerator * Decimal(100) / denominator


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _period_metrics(record: dict[str, Any]) -> dict[str, str]:
    label = record["label"]
    values: dict[str, Decimal] = record["values"]
    net_working_capital = values["current_assets"] - values["current_liabilities"]
    return {
        "period": label,
        "gross_margin_percent": _decimal_text(
            _percent(
                values["revenue"] - values["cost"],
                values["revenue"],
                name="revenue",
                where=label,
            )
        ),
        "attributable_net_margin_percent": _decimal_text(
            _percent(
                values["attributable_profit"],
                values["revenue"],
                name="revenue",
                where=label,
            )
        ),
        "operating_cash_conversion_percent": _decimal_text(
            _percent(
                values["operating_cash_flow"],
                values["attributable_profit"],
                name="attributable_profit",
                where=label,
            )
        ),
        "net_working_capital": _decimal_text(net_working_capital),
        "net_working_capital_to_revenue_percent": _decimal_text(
            _percent(
                net_working_capital,
                values["revenue"],
                name="revenue",
                where=label,
            )
        ),
        "receivables_to_total_assets_percent": _decimal_text(
            _percent(
                values["accounts_receivable"],
                values["total_assets"],
                name="total_assets",
                where=label,
            )
        ),
        "inventory_to_total_assets_percent": _decimal_text(
            _percent(
                values["inventory"],
                values["total_assets"],
                name="total_assets",
                where=label,
            )
        ),
    }


def _cagr_entry(records: list[dict[str, Any]], *, name: str) -> dict[str, str]:
    span_days = (records[-1]["date"] - records[0]["date"]).days
    span_years = Decimal(span_days) / _DAYS_PER_YEAR
    if span_years <= 0:
        return {
            "status": "not_computable",
            "reason": f"{name} CAGR requires a positive span between endpoints",
        }
    values = [record["values"][name] for record in records]
    start, end = values[0], values[-1]
    if start <= 0 or end <= 0:
        return {
            "status": "not_computable",
            "reason": f"{name} endpoints must both be greater than zero",
        }
    growth = (end / start) ** (Decimal(1) / span_years) - Decimal(1)
    percent = (growth * Decimal(100)).quantize(CAGR_QUANTUM)
    return {"percent": _decimal_text(percent)}


def calculate_financial_ratio_series(payload: dict[str, object]) -> dict[str, object]:
    """Compute the fixed per-period metric set and endpoint CAGR values."""
    with decimal.localcontext(_DECIMAL_CONTEXT):
        return _calculate_financial_ratio_series(payload)


def _calculate_financial_ratio_series(
    payload: dict[str, object],
) -> dict[str, object]:
    subject = _required_text(payload, "subject")
    scope = _required_text(payload, "scope")
    unit = _required_text(payload, "unit")
    input_hash = _required_input_hash(payload)
    if "periods" not in payload:
        raise ValueError("missing required field: periods")
    unexpected = sorted(
        set(payload) - {"subject", "scope", "unit", "input_hash", "periods", "source"}
    )
    if unexpected:
        raise ValueError(f"unexpected field(s): {', '.join(unexpected)}")
    records = _period_records(payload["periods"])

    cagr: dict[str, str] = {}
    for name in ("revenue", "attributable_profit"):
        entry = _cagr_entry(records, name=name)
        if "percent" in entry:
            cagr[f"{name}_percent"] = entry["percent"]
        else:
            cagr[f"{name}_status"] = entry["status"]
            cagr[f"{name}_reason"] = entry["reason"]

    return {
        "subject": subject,
        "scope": scope,
        "unit": unit,
        "input_hash": input_hash,
        "periods": [_period_metrics(record) for record in records],
        "cagr": cagr,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compute the deterministic financial ratio series."
    )
    parser.add_argument("--input", required=True, help="series input JSON path")
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
            tool_name="financial_ratio_series",
            input_path=input_path,
            output_path=output_path,
            transform=calculate_financial_ratio_series,
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
