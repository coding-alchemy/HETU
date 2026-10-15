"""Deterministic SVG chart renderer over a saved OHLCV metrics result.

``render_market_svg(payload)`` consumes the successful ``ohlcv`` result of
``market-series-metrics.py`` (daily rows, windows, caliber metadata and
``input_hash``) and draws one fixed candlestick/MA/volume chart as SVG using
only the standard library. All geometry and colors are fixed by this file;
the input can only change data values and XML-escaped text. Data gaps break
the MA polylines instead of being filled with zero; insufficient history
leaves an MA un drawn. The renderer never selects a source, never fetches
anything, never embeds scripts or external resources, and never interprets
the chart as a trading signal.

CLI: ``market-series-chart.py --input IN --output RESULT.json --svg-output
CHART.svg``. The input must be the metrics result object (the Agent extracts
the success envelope's ``result`` and registers that step's input relation);
failed envelopes and arbitrary HTML templates are rejected. Exit 0 writes the
envelope plus the SVG; exit 1 writes a failure envelope (the partial SVG, if
any, is cleaned up); exit 2 covers argument errors, unreadable input, path
collisions or existing outputs. Outputs are never overwritten.
"""

from __future__ import annotations

import argparse
import decimal
import sys
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal
from pathlib import Path
from xml.etree import ElementTree as ET

import _artifact_io

_DRAW_CONTEXT = Context(prec=28, rounding=ROUND_HALF_EVEN)
_SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", _SVG_NS)


def render_market_svg(payload: dict[str, object]) -> str:
    with decimal.localcontext(_DRAW_CONTEXT):
        return _render_market_svg(payload)


def _render_market_svg(payload: dict[str, object]) -> str:
    rows = payload.get("daily")
    if not isinstance(rows, list) or not rows:
        raise ValueError("daily must be a non-empty list")
    if payload.get("units") != {
        "price": "CNY/share",
        "volume": "share",
        "amount": "CNY",
        "float_shares": "share",
    }:
        raise ValueError("unsupported chart units")
    for name in ("subject", "as_of", "timezone", "adjustment", "adjustment_basis", "input_hash"):
        if not isinstance(payload.get(name), str) or not payload[name].strip():
            raise ValueError(f"missing chart metadata: {name}")

    def number(raw: object, *, zero: bool = False) -> Decimal:
        if isinstance(raw, bool):
            raise ValueError("invalid chart number")
        value = Decimal(str(raw))
        if not value.is_finite() or (value < 0 if zero else value <= 0):
            raise ValueError("invalid chart number")
        return value

    data = []
    moments = []
    price_values = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid chart row")
        prices = {name: number(row[name]) for name in ("open", "high", "low", "close")}
        if (
            not prices["low"]
            <= min(prices["open"], prices["close"])
            <= max(prices["open"], prices["close"])
            <= prices["high"]
        ):
            raise ValueError("invalid chart OHLC")
        prices["volume"] = number(row["volume"], zero=True)
        ma = row.get("ma")
        if not isinstance(ma, dict) or set(ma) != {"5", "10", "20", "60"}:
            raise ValueError("invalid moving averages")
        prices["ma"] = {key: None if ma[key] is None else number(ma[key]) for key in ma}
        moment = datetime.fromisoformat(row["timestamp"])
        if moment.tzinfo is None or (moments and moment <= moments[-1]):
            raise ValueError("invalid chart timestamp order")
        moments.append(moment)
        price_values.extend(prices[key] for key in ("high", "low"))
        price_values.extend(value for value in prices["ma"].values() if value is not None)
        data.append(prices)
    if moments[-1] > datetime.fromisoformat(payload["as_of"]):
        raise ValueError("future chart data")
    gaps = payload.get("gaps")
    if not isinstance(gaps, list):
        raise ValueError("invalid chart gaps")
    breaks = []
    for gap in gaps:
        if not isinstance(gap, dict) or gap.get("kind") not in (
            "missing",
            "suspension",
            "price_break",
        ):
            raise ValueError("invalid chart gap")
        left = datetime.fromisoformat(gap["start"])
        right = datetime.fromisoformat(gap["end"])
        if left.tzinfo is None or right.tzinfo is None or left > right:
            raise ValueError("invalid chart gap interval")
        if gap["kind"] != "suspension":
            breaks.append((left, right))
    low, high = min(price_values), max(price_values)
    if low == high:
        low, high = low * Decimal("0.99"), high * Decimal("1.01")
    max_volume = max(Decimal(1), max(row["volume"] for row in data))
    span = max(Decimal(1), Decimal(str((moments[-1] - moments[0]).total_seconds())))

    def coordinate(value: Decimal) -> str:
        return format(value.quantize(Decimal("0.01")), "f")

    def x(index: int) -> Decimal:
        elapsed = Decimal(str((moments[index] - moments[0]).total_seconds()))
        return Decimal(70) + elapsed / span * Decimal(1050)

    def y(price: Decimal) -> Decimal:
        return Decimal(70) + (high - price) / (high - low) * Decimal(290)

    body_width = (
        Decimal(4)
        if len(data) == 1
        else min(
            Decimal(4),
            max(Decimal(1), min(x(i + 1) - x(i) for i in range(len(data) - 1)) * Decimal("0.7")),
        )
    )
    root = ET.Element(f"{{{_SVG_NS}}}svg", width="1200", height="640", viewBox="0 0 1200 640")

    def element(tag: str, **attrs: object) -> ET.Element:
        return ET.SubElement(
            root, f"{{{_SVG_NS}}}{tag}", {key: str(value) for key, value in attrs.items()}
        )

    title = element("text", x="70", y="25", fill="#1f1f1f")
    title.text = f"{payload['subject']}｜{payload['adjustment']}｜{payload['as_of']}"
    for index, row in enumerate(data):
        xpos = x(index)
        color = "#d73027" if row["close"] >= row["open"] else "#1a9850"
        element(
            "line",
            x1=coordinate(xpos),
            x2=coordinate(xpos),
            y1=coordinate(y(row["high"])),
            y2=coordinate(y(row["low"])),
            stroke=color,
            **{"class": "wick"},
        )
        top = min(y(row["open"]), y(row["close"]))
        height = max(Decimal(1), abs(y(row["open"]) - y(row["close"])))
        element(
            "rect",
            x=coordinate(xpos - body_width / 2),
            y=coordinate(top),
            width=coordinate(body_width),
            height=coordinate(height),
            fill=color,
            **{"class": "candle"},
        )
        volume_height = row["volume"] / max_volume * Decimal(140)
        element(
            "rect",
            x=coordinate(xpos - body_width / 2),
            y=coordinate(Decimal(540) - volume_height),
            width=coordinate(body_width),
            height=coordinate(volume_height),
            fill=color,
            **{"class": "volume"},
        )
    for key, color in (("5", "#e69f00"), ("10", "#0072b2"), ("20", "#cc79a7"), ("60", "#4d4d4d")):
        points: list[str] = []

        def flush(points: list[str] = points, color: str = color, key: str = key) -> None:
            if len(points) >= 2:
                element(
                    "polyline",
                    points=" ".join(points),
                    fill="none",
                    stroke=color,
                    **{"class": f"ma-{key}"},
                )
            points.clear()

        for index, row in enumerate(data):
            if index and any(
                moments[index - 1] < right and left <= moments[index] for left, right in breaks
            ):
                flush()
            value = row["ma"][key]
            if value is None:
                flush()
            else:
                points.append(f"{coordinate(x(index))},{coordinate(y(value))}")
        flush()
    for label, xpos, ypos in (
        ("价格（元/股）", "5", "60"),
        ("成交量（股）", "5", "395"),
        (coordinate(high), "5", "80"),
        (coordinate(low), "5", "360"),
        (coordinate(max_volume), "5", "420"),
    ):
        element("text", x=xpos, y=ypos, fill="#1f1f1f").text = label
    for index in sorted({0, len(data) // 2, len(data) - 1}):
        element("text", x=coordinate(x(index)), y="575", fill="#1f1f1f").text = (
            moments[index].date().isoformat()
        )
    element("text", x="70", y="605", fill="#1f1f1f").text = str(payload["adjustment_basis"])
    return ET.tostring(root, encoding="unicode") + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render one explicit market SVG")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--svg-output", required=True)
    args = parser.parse_args(argv)
    input_path, output_path, svg_path = map(Path, (args.input, args.output, args.svg_output))
    if len({path.resolve() for path in (input_path, output_path, svg_path)}) != 3:
        return 2
    if not input_path.is_file() or output_path.exists() or svg_path.exists():
        return 2
    created_svg = False

    def transform(payload: dict[str, object]) -> dict[str, object]:
        nonlocal created_svg
        svg = render_market_svg(payload)
        svg_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with svg_path.open("x", encoding="utf-8") as handle:
                created_svg = True
                handle.write(svg)
        except FileExistsError as error:
            raise _artifact_io.OutputConflictError("SVG already exists") from error
        return {
            "svg_file": svg_path.name,
            "width": 1200,
            "height": 640,
            "input_hash": payload["input_hash"],
        }

    try:
        _artifact_io.run_transform(
            tool_name="market_series_chart",
            input_path=input_path,
            output_path=output_path,
            transform=transform,
        )
    except (_artifact_io.OutputConflictError, _artifact_io.InputUnreadableError) as error:
        if created_svg:
            svg_path.unlink(missing_ok=True)
        print(str(error), file=sys.stderr)
        return 2
    except Exception as error:
        if created_svg:
            svg_path.unlink(missing_ok=True)
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
