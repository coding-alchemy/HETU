"""Deterministic tool tests for the canonical market series SVG chart renderer."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from tests.product.skill.deterministic_tool_loader import load_script
from tests.product.skill.test_market_series_metrics_tool import _ohlcv_payload

SCRIPT_PATH = (
    Path(__file__).resolve().parents[3]
    / "skills/hetu-stock-analysis/scripts/market-series-chart.py"
)


def test_svg_geometry_and_escaped_title() -> None:
    metrics = load_script("market-series-metrics.py").calculate_ohlcv_metrics(_ohlcv_payload())
    metrics["subject"] = '<script>alert("x")</script>'
    chart = load_script("market-series-chart.py")
    svg = chart.render_market_svg(metrics)
    root = ET.fromstring(svg)
    assert all(node.tag != "{http://www.w3.org/2000/svg}script" for node in root.iter())
    candles = [node for node in root.iter() if node.get("class") == "candle"]
    volumes = [node for node in root.iter() if node.get("class") == "volume"]
    assert len(candles) == len(volumes) == 6
    assert float(volumes[-1].get("height")) == pytest.approx(140)
    assert float(volumes[0].get("height")) == pytest.approx(70)
    assert float(candles[-1].get("y")) == pytest.approx(186)
    wicks = [node for node in root.iter() if node.get("class") == "wick"]
    assert float(wicks[-1].get("y1")) == pytest.approx(70)
    assert float(wicks[-1].get("y2")) == pytest.approx(360)
    assert chart.render_market_svg(metrics) == svg


def test_invalid_ohlc_is_not_drawn() -> None:
    metrics = load_script("market-series-metrics.py").calculate_ohlcv_metrics(_ohlcv_payload())
    metrics["daily"][0]["low"] = "99"
    with pytest.raises(ValueError):
        load_script("market-series-chart.py").render_market_svg(metrics)


def test_svg_title_text_is_escaped_not_executed() -> None:
    metrics = load_script("market-series-metrics.py").calculate_ohlcv_metrics(_ohlcv_payload())
    metrics["subject"] = '<script>alert("x")</script>'
    svg = load_script("market-series-chart.py").render_market_svg(metrics)
    assert "<script>" not in svg
    root = ET.fromstring(svg)
    title = next(node for node in root.iter() if node.tag.endswith("}text"))
    assert title.text is not None and "alert" in title.text


def test_svg_ma_lines_break_at_insufficient_history_and_gaps() -> None:
    metrics = load_script("market-series-metrics.py").calculate_ohlcv_metrics(_ohlcv_payload())
    svg = load_script("market-series-chart.py").render_market_svg(metrics)
    root = ET.fromstring(svg)
    ma5 = [node for node in root.iter() if node.get("class") == "ma-5"]
    points = ma5[0].get("points").split()
    # MA5 exists only from the fifth bar onward, so at most two points can be
    # connected on this six-bar synthetic series.
    assert len(points) <= 2


@pytest.mark.parametrize(
    "mutation",
    ["empty-daily", "bad-units", "missing-metadata", "future-data", "inverted-rows"],
)
def test_chart_rejects_invalid_payloads(mutation: str) -> None:
    metrics = load_script("market-series-metrics.py").calculate_ohlcv_metrics(_ohlcv_payload())
    if mutation == "empty-daily":
        metrics["daily"] = []
    elif mutation == "bad-units":
        metrics["units"] = {"price": "CNY"}
    elif mutation == "missing-metadata":
        del metrics["adjustment_basis"]
    elif mutation == "future-data":
        metrics["as_of"] = "2026-06-01T00:00:00+08:00"
    elif mutation == "inverted-rows":
        metrics["daily"] = list(reversed(metrics["daily"]))
    with pytest.raises(ValueError):
        load_script("market-series-chart.py").render_market_svg(metrics)


def _write_json(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_chart_writes_envelope_and_svg(tmp_path: Path) -> None:
    metrics = load_script("market-series-metrics.py").calculate_ohlcv_metrics(_ohlcv_payload())
    input_path = _write_json(tmp_path / "metrics.json", metrics)
    output_path = tmp_path / "chart-envelope.json"
    svg_path = tmp_path / "chart.svg"

    completed = _run_cli(
        "--input", str(input_path),
        "--output", str(output_path),
        "--svg-output", str(svg_path),
    )

    assert completed.returncode == 0, completed.stderr
    envelope = json.loads(output_path.read_text(encoding="utf-8"))
    assert envelope["schema_version"] == "1.0"
    assert envelope["tool"] == "market_series_chart"
    assert envelope["status"] == "success"
    assert envelope["result"]["svg_file"] == svg_path.name
    assert envelope["result"]["width"] == 1200
    assert envelope["result"]["height"] == 640
    svg_text = svg_path.read_text(encoding="utf-8")
    root = ET.fromstring(svg_text)
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    assert len([node for node in root.iter() if node.get("class") == "candle"]) == 6


def test_cli_chart_failure_writes_envelope_and_no_svg(tmp_path: Path) -> None:
    metrics = load_script("market-series-metrics.py").calculate_ohlcv_metrics(_ohlcv_payload())
    metrics["daily"][0]["low"] = "99"
    input_path = _write_json(tmp_path / "metrics.json", metrics)
    output_path = tmp_path / "chart-failure.json"
    svg_path = tmp_path / "chart-failure.svg"

    completed = _run_cli(
        "--input", str(input_path),
        "--output", str(output_path),
        "--svg-output", str(svg_path),
    )

    assert completed.returncode == 1
    envelope = json.loads(output_path.read_text(encoding="utf-8"))
    assert envelope["status"] == "failed"
    assert "result" not in envelope
    assert not svg_path.exists()


def test_cli_chart_rejects_same_and_existing_paths(tmp_path: Path) -> None:
    metrics = load_script("market-series-metrics.py").calculate_ohlcv_metrics(_ohlcv_payload())
    input_path = _write_json(tmp_path / "metrics.json", metrics)
    svg_path = tmp_path / "chart.svg"
    svg_path.write_text("existing evidence\n", encoding="utf-8")

    same = _run_cli(
        "--input", str(input_path),
        "--output", str(input_path),
        "--svg-output", str(tmp_path / "other.svg"),
    )
    existing = _run_cli(
        "--input", str(input_path),
        "--output", str(tmp_path / "envelope.json"),
        "--svg-output", str(svg_path),
    )

    assert same.returncode == 2
    assert existing.returncode == 2
    assert svg_path.read_text(encoding="utf-8") == "existing evidence\n"
    assert not (tmp_path / "envelope.json").exists()


def test_cli_chart_same_output_paths_are_rejected(tmp_path: Path) -> None:
    metrics = load_script("market-series-metrics.py").calculate_ohlcv_metrics(_ohlcv_payload())
    input_path = _write_json(tmp_path / "metrics.json", metrics)
    shared = tmp_path / "shared.svg"

    completed = _run_cli(
        "--input", str(input_path),
        "--output", str(shared),
        "--svg-output", str(shared),
    )

    assert completed.returncode == 2
    assert not shared.exists()


def test_cli_chart_replays_byte_identically(tmp_path: Path) -> None:
    metrics = load_script("market-series-metrics.py").calculate_ohlcv_metrics(_ohlcv_payload())
    input_path = _write_json(tmp_path / "metrics.json", metrics)

    outputs = []
    for run in ("one", "two"):
        output_path = tmp_path / f"run-{run}" / "envelope.json"
        svg_path = tmp_path / f"run-{run}" / "chart.svg"
        completed = _run_cli(
            "--input", str(input_path),
            "--output", str(output_path),
            "--svg-output", str(svg_path),
        )
        assert completed.returncode == 0, completed.stderr
        outputs.append((output_path.read_bytes(), svg_path.read_bytes()))

    assert outputs[0] == outputs[1]
