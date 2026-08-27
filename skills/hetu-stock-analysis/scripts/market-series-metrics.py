"""Deterministic Decimal metrics over a saved market close-price series.

The calculator consumes a ``MarketSeriesInput`` JSON object: ``subject``,
``adjustment``, ``timezone``, a timezone-aware ``as_of``, the required
``input_hash`` (lowercase hex, 8–64 characters, identifying the upstream
normalized artifact, echoed verbatim), a positive integer
``window`` and ``bars[{timestamp, close}]`` whose timezone-aware timestamps are
strictly ascending and no later than ``as_of`` (a bar exactly at ``as_of`` is
included). The declared ``timezone`` must resolve as an IANA zone and every
timestamp offset (``as_of`` included) must equal that zone's offset at the
instant, so a declared zone that contradicts the saved offsets is rejected.
Metrics cover the trailing ``window`` bars: window return, simple
moving average, window high/low and max drawdown from the per-point running
peak, serialized as Decimal strings. The adjustment caliber and timezone are
passed through unchanged; the tool never selects a source, never swaps or
rewrites saved values, and never interprets any metric as a trading signal.

CLI: ``market-series-metrics.py --input IN --output OUT``. Exit 0 writes a
success envelope; exit 1 writes a failure envelope for unparseable input or a
rejected series; exit 2 covers argument errors, unreadable input, an output
path equal to the input path, or an existing output file.
"""

from __future__ import annotations

import argparse
import decimal
import re
import sys
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import _artifact_io

# Every computation runs inside this pinned context so results never depend on
# the ambient decimal context (module default: prec=28, ROUND_HALF_EVEN,
# default traps).
_DECIMAL_CONTEXT = Context(prec=28, rounding=ROUND_HALF_EVEN)

# The upstream normalized artifact hash: lowercase hex, 8 to 64 characters.
_INPUT_HASH_PATTERN = re.compile(r"[0-9a-f]{8,64}")

ALLOWED_FIELDS = frozenset(
    {"subject", "adjustment", "timezone", "as_of", "input_hash", "window", "bars",
     "source"}
)
REQUIRED_FIELDS = (
    "subject",
    "adjustment",
    "timezone",
    "as_of",
    "input_hash",
    "window",
    "bars",
)


def _required_input_hash(payload: dict[str, object]) -> str:
    value = payload["input_hash"]
    if not isinstance(value, str) or not _INPUT_HASH_PATTERN.fullmatch(value):
        raise ValueError(
            "input_hash must be a lowercase hex string of 8 to 64 characters "
            "identifying the upstream normalized artifact"
        )
    return value


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _required_text(payload: dict[str, object], key: str) -> str:
    value = payload[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _aware_datetime(value: object, *, name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be an ISO-8601 datetime string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return parsed


def _close(value: object, *, where: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{where}: close must be a decimal number") from exc
    if not result.is_finite():
        raise ValueError(f"{where}: close must be finite")
    if result <= 0:
        raise ValueError(f"{where}: close must be positive")
    return result


def _resolve_timezone(timezone: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise ValueError(
            f"timezone must be a resolvable IANA zone: {timezone!r}"
        ) from exc


def _require_zone_offset(moment: datetime, zone: ZoneInfo, *, name: str) -> None:
    expected = moment.astimezone(zone).utcoffset()
    if moment.utcoffset() != expected:
        raise ValueError(
            f"{name} offset does not match the declared timezone {zone.key!r}"
        )


def _bars(raw_bars: object) -> list[dict[str, Any]]:
    if not isinstance(raw_bars, list) or not raw_bars:
        raise ValueError("bars must be a non-empty list")
    records: list[dict[str, Any]] = []
    for index, entry in enumerate(raw_bars):
        where = f"bars[{index}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{where} must be an object")
        unexpected = sorted(set(entry) - {"timestamp", "close"})
        if unexpected:
            raise ValueError(f"{where}: unexpected field(s): {', '.join(unexpected)}")
        for key in ("timestamp", "close"):
            if key not in entry:
                raise ValueError(f"{where}: missing field(s): {key}")
        records.append(
            {
                "timestamp": entry["timestamp"],
                "moment": _aware_datetime(entry["timestamp"], name=f"{where}.timestamp"),
                "close": _close(entry["close"], where=where),
            }
        )
    for earlier, later in zip(records, records[1:], strict=False):
        if later["moment"] == earlier["moment"]:
            raise ValueError(f"duplicate bar timestamp: {later['timestamp']}")
        if later["moment"] < earlier["moment"]:
            raise ValueError(
                "bars must be in strictly ascending order: "
                f"{earlier['timestamp']} then {later['timestamp']}"
            )
    return records


def calculate_market_series_metrics(payload: dict[str, object]) -> dict[str, object]:
    """Compute the fixed metric set over the trailing ``window`` bars."""
    with decimal.localcontext(_DECIMAL_CONTEXT):
        return _calculate_market_series_metrics(payload)


def _calculate_market_series_metrics(
    payload: dict[str, object],
) -> dict[str, object]:
    for key in REQUIRED_FIELDS:
        if key not in payload:
            raise ValueError(f"missing required field: {key}")
    unexpected = sorted(set(payload) - ALLOWED_FIELDS)
    if unexpected:
        raise ValueError(f"unexpected field(s): {', '.join(unexpected)}")

    # The subject is validated but intentionally not echoed: the formal output
    # field list carries only the eleven contract fields.
    _required_text(payload, "subject")
    adjustment = _required_text(payload, "adjustment")
    timezone = _required_text(payload, "timezone")
    input_hash = _required_input_hash(payload)
    zone = _resolve_timezone(timezone)
    as_of = _aware_datetime(payload["as_of"], name="as_of")
    _require_zone_offset(as_of, zone, name="as_of")

    raw_window = payload["window"]
    if not isinstance(raw_window, int) or isinstance(raw_window, bool) or raw_window < 1:
        raise ValueError("window must be a positive integer")

    records = _bars(payload["bars"])
    for index, record in enumerate(records):
        _require_zone_offset(
            record["moment"], zone, name=f"bars[{index}].timestamp"
        )
        if record["moment"] > as_of:
            raise ValueError(f"bars[{index}].timestamp must not be later than as_of")
    if raw_window > len(records):
        raise ValueError(
            f"window {raw_window} exceeds available bar count {len(records)}"
        )

    effective = records[-raw_window:]
    closes = [record["close"] for record in effective]

    running_peak = closes[0]
    max_drawdown = Decimal(0)
    for close in closes:
        if close > running_peak:
            running_peak = close
        drawdown = (running_peak - close) * Decimal(100) / running_peak
        if drawdown > max_drawdown:
            max_drawdown = drawdown

    first, last = closes[0], closes[-1]
    return {
        "return_percent": _decimal_text((last - first) * Decimal(100) / first),
        "simple_moving_average": _decimal_text(
            sum(closes, Decimal(0)) / Decimal(raw_window)
        ),
        "window_high": _decimal_text(max(closes)),
        "window_low": _decimal_text(min(closes)),
        "max_drawdown_percent": _decimal_text(max_drawdown),
        "effective_start": effective[0]["timestamp"],
        "effective_end": effective[-1]["timestamp"],
        "adjustment": adjustment,
        "timezone": timezone,
        "window": raw_window,
        "input_hash": input_hash,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compute the deterministic market series metrics."
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
            tool_name="market_series_metrics",
            input_path=input_path,
            output_path=output_path,
            transform=calculate_market_series_metrics,
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
