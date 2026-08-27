"""Deterministic tool tests for the canonical market series metrics calculator."""

from __future__ import annotations

import decimal
import hashlib
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from tests.product.skill.deterministic_tool_loader import load_script

market = load_script("market-series-metrics.py")

SCRIPT_PATH = (
    Path(__file__).resolve().parents[3]
    / "skills/hetu-stock-analysis/scripts/market-series-metrics.py"
)

OUTPUT_FIELDS = frozenset(
    {
        "return_percent",
        "simple_moving_average",
        "window_high",
        "window_low",
        "max_drawdown_percent",
        "effective_start",
        "effective_end",
        "adjustment",
        "timezone",
        "window",
        "input_hash",
    }
)

FORBIDDEN_SIGNAL_WORDS = ("buy", "sell", "signal", "买入", "卖出")


def _bar(timestamp: str, close: str) -> dict[str, str]:
    return {"timestamp": timestamp, "close": close}


def _series_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "subject": "600519.SH",
        "adjustment": "qfq",
        "timezone": "Asia/Shanghai",
        "as_of": "2024-06-07T15:00:00+08:00",
        "input_hash": "a1b2c3d4e5f6a7b8",
        "window": 4,
        "bars": [
            _bar("2024-06-03T15:00:00+08:00", "100"),
            _bar("2024-06-04T15:00:00+08:00", "120"),
            _bar("2024-06-05T15:00:00+08:00", "60"),
            _bar("2024-06-06T15:00:00+08:00", "90"),
        ],
    }
    payload.update(overrides)
    return payload


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


def _read_envelope(output_path: Path) -> dict[str, Any]:
    return json.loads(output_path.read_text(encoding="utf-8"))


def test_full_window_metrics_use_explicit_decimals() -> None:
    result = market.calculate_market_series_metrics(_series_payload())

    assert result["return_percent"] == "-10"
    assert result["simple_moving_average"] == "92.5"
    assert result["window_high"] == "120"
    assert result["window_low"] == "60"
    assert result["max_drawdown_percent"] == "50"
    assert result["effective_start"] == "2024-06-03T15:00:00+08:00"
    assert result["effective_end"] == "2024-06-06T15:00:00+08:00"
    assert result["adjustment"] == "qfq"
    assert result["timezone"] == "Asia/Shanghai"
    assert result["window"] == 4
    assert set(result) == set(OUTPUT_FIELDS)


def test_trailing_window_selects_last_bars_only() -> None:
    payload = _series_payload(window=2)

    result = market.calculate_market_series_metrics(payload)

    assert result["return_percent"] == "50"
    assert result["simple_moving_average"] == "75"
    assert result["window_high"] == "90"
    assert result["window_low"] == "60"
    assert result["max_drawdown_percent"] == "0"
    assert result["effective_start"] == "2024-06-05T15:00:00+08:00"
    assert result["effective_end"] == "2024-06-06T15:00:00+08:00"
    assert result["window"] == 2


def test_max_drawdown_uses_per_point_running_peak() -> None:
    payload = _series_payload(
        window=3,
        bars=[
            _bar("2024-06-03T15:00:00+08:00", "50"),
            _bar("2024-06-04T15:00:00+08:00", "100"),
            _bar("2024-06-05T15:00:00+08:00", "40"),
            _bar("2024-06-06T15:00:00+08:00", "80"),
        ],
    )

    result = market.calculate_market_series_metrics(payload)

    # Trailing three closes 100, 40, 80: peak 100, worst point 40 -> 60%.
    assert result["max_drawdown_percent"] == "60"
    assert result["return_percent"] == "-20"


def test_inexact_metrics_keep_full_decimal_context() -> None:
    payload = _series_payload(
        window=2,
        bars=[
            _bar("2024-06-05T15:00:00+08:00", "3"),
            _bar("2024-06-06T15:00:00+08:00", "10"),
        ],
    )

    result = market.calculate_market_series_metrics(payload)

    assert Decimal(result["return_percent"]) == Decimal(7) * Decimal(100) / Decimal(3)
    assert Decimal(result["simple_moving_average"]) == Decimal(13) / Decimal(2)
    assert isinstance(result["return_percent"], str)


def test_metrics_are_independent_of_ambient_decimal_context() -> None:
    payload = _series_payload(
        window=2,
        bars=[
            _bar("2024-06-05T15:00:00+08:00", "3"),
            _bar("2024-06-06T15:00:00+08:00", "10"),
        ],
    )

    default_context = decimal.getcontext().copy()
    try:
        baseline = market.calculate_market_series_metrics(payload)
        decimal.getcontext().prec = 10
        constrained = market.calculate_market_series_metrics(payload)
    finally:
        decimal.setcontext(default_context)

    assert constrained == baseline
    assert Decimal(baseline["return_percent"]) == (
        Decimal(7) * Decimal(100) / Decimal(3)
    )
    assert len(Decimal(baseline["return_percent"]).as_tuple().digits) > 10


def test_exact_as_of_boundary_bar_is_included() -> None:
    payload = _series_payload(
        window=5,
        bars=[
            *_series_payload()["bars"],
            _bar("2024-06-07T15:00:00+08:00", "80"),
        ],
    )

    result = market.calculate_market_series_metrics(payload)

    assert result["effective_end"] == "2024-06-07T15:00:00+08:00"
    assert result["return_percent"] == "-20"


def test_bar_after_as_of_is_rejected() -> None:
    payload = _series_payload(
        bars=[
            *_series_payload()["bars"],
            _bar("2024-06-08T15:00:00+08:00", "95"),
        ],
    )

    with pytest.raises(ValueError, match="as_of"):
        market.calculate_market_series_metrics(payload)


def test_naive_as_of_is_rejected() -> None:
    payload = _series_payload(as_of="2024-06-07T15:00:00")

    with pytest.raises(ValueError, match="timezone-aware"):
        market.calculate_market_series_metrics(payload)


def test_naive_bar_timestamp_is_rejected() -> None:
    payload = _series_payload(
        bars=[
            _bar("2024-06-05T15:00:00", "100"),
            _bar("2024-06-06T15:00:00+08:00", "110"),
        ],
    )

    with pytest.raises(ValueError, match="timezone-aware"):
        market.calculate_market_series_metrics(payload)


def test_duplicate_timestamp_is_rejected() -> None:
    payload = _series_payload(
        bars=[
            _bar("2024-06-05T15:00:00+08:00", "100"),
            _bar("2024-06-05T15:00:00+08:00", "110"),
        ],
    )

    with pytest.raises(ValueError, match="duplicate"):
        market.calculate_market_series_metrics(payload)


def test_inverted_timestamps_are_rejected() -> None:
    payload = _series_payload(
        bars=[
            _bar("2024-06-06T15:00:00+08:00", "100"),
            _bar("2024-06-05T15:00:00+08:00", "110"),
        ],
    )

    with pytest.raises(ValueError, match="ascending"):
        market.calculate_market_series_metrics(payload)


def test_window_larger_than_sample_is_rejected() -> None:
    payload = _series_payload(window=5)

    with pytest.raises(ValueError, match="exceeds"):
        market.calculate_market_series_metrics(payload)


def test_non_positive_window_is_rejected() -> None:
    with pytest.raises(ValueError, match="window must be a positive integer"):
        market.calculate_market_series_metrics(_series_payload(window=0))
    with pytest.raises(ValueError, match="window must be a positive integer"):
        market.calculate_market_series_metrics(_series_payload(window="4"))


def test_non_positive_close_is_rejected() -> None:
    with pytest.raises(ValueError, match="close must be positive"):
        market.calculate_market_series_metrics(
            _series_payload(
                bars=[
                    _bar("2024-06-05T15:00:00+08:00", "0"),
                    _bar("2024-06-06T15:00:00+08:00", "110"),
                ],
            )
        )
    with pytest.raises(ValueError, match="close must be positive"):
        market.calculate_market_series_metrics(
            _series_payload(
                bars=[
                    _bar("2024-06-05T15:00:00+08:00", "-5"),
                    _bar("2024-06-06T15:00:00+08:00", "110"),
                ],
            )
        )


def test_non_finite_close_is_rejected() -> None:
    payload = _series_payload(
        bars=[
            _bar("2024-06-05T15:00:00+08:00", "NaN"),
            _bar("2024-06-06T15:00:00+08:00", "110"),
        ],
    )

    with pytest.raises(ValueError, match="close must be finite"):
        market.calculate_market_series_metrics(payload)


def test_adjustment_and_timezone_pass_through_unchanged() -> None:
    payload = _series_payload(
        adjustment="hfq",
        timezone="UTC",
        as_of="2024-06-07T07:00:00+00:00",
        window=2,
        bars=[
            _bar("2024-06-06T07:00:00+00:00", "100"),
            _bar("2024-06-07T07:00:00+00:00", "110"),
        ],
    )

    result = market.calculate_market_series_metrics(payload)

    assert result["adjustment"] == "hfq"
    assert result["timezone"] == "UTC"


def test_declared_timezone_contradicting_bar_offsets_is_rejected() -> None:
    payload = _series_payload(timezone="UTC")

    with pytest.raises(ValueError, match="timezone"):
        market.calculate_market_series_metrics(payload)


def test_declared_timezone_contradicting_as_of_offset_is_rejected() -> None:
    payload = _series_payload(as_of="2024-06-07T15:00:00+09:00")

    with pytest.raises(ValueError, match="timezone"):
        market.calculate_market_series_metrics(payload)


def test_unresolvable_timezone_is_rejected() -> None:
    payload = _series_payload(timezone="Not/A_Zone")

    with pytest.raises(ValueError, match="timezone"):
        market.calculate_market_series_metrics(payload)


def test_missing_required_field_is_rejected() -> None:
    for key in (
        "subject",
        "adjustment",
        "timezone",
        "as_of",
        "input_hash",
        "window",
        "bars",
    ):
        payload = _series_payload()
        del payload[key]
        with pytest.raises(ValueError, match=f"missing required field: {key}"):
            market.calculate_market_series_metrics(payload)


@pytest.mark.parametrize(
    "bad_hash", ("", "xyz12345", "abc", "0" * 7, "0" * 65, 12345678)
)
def test_invalid_input_hash_is_rejected(bad_hash: object) -> None:
    with pytest.raises(ValueError, match="input_hash"):
        market.calculate_market_series_metrics(_series_payload(input_hash=bad_hash))


def test_input_hash_is_echoed_verbatim_in_output() -> None:
    result = market.calculate_market_series_metrics(
        _series_payload(input_hash="c0ffee1234567890")
    )

    assert result["input_hash"] == "c0ffee1234567890"


def test_unexpected_input_field_is_rejected() -> None:
    payload = _series_payload(extra=1)

    with pytest.raises(ValueError, match="unexpected field"):
        market.calculate_market_series_metrics(payload)


def test_output_carries_no_signal_words() -> None:
    result = market.calculate_market_series_metrics(_series_payload())

    serialized = json.dumps(result, ensure_ascii=False).lower()
    for word in FORBIDDEN_SIGNAL_WORDS:
        assert word not in serialized


def test_cli_success_writes_decimal_envelope(tmp_path: Path) -> None:
    input_path = _write_json(tmp_path / "input.json", _series_payload())
    output_path = tmp_path / "out" / "envelope.json"

    completed = _run_cli("--input", str(input_path), "--output", str(output_path))

    assert completed.returncode == 0, completed.stderr
    envelope = _read_envelope(output_path)
    assert envelope["schema_version"] == "1.0"
    assert envelope["tool"] == "market_series_metrics"
    assert envelope["status"] == "success"
    assert (
        envelope["input_sha256"]
        == hashlib.sha256(input_path.read_bytes()).hexdigest()
    )
    assert envelope["result"]["return_percent"] == "-10"
    assert envelope["result"]["max_drawdown_percent"] == "50"
    envelope_text = output_path.read_text(encoding="utf-8").lower()
    for word in FORBIDDEN_SIGNAL_WORDS:
        assert word not in envelope_text


def test_cli_input_change_changes_hash(
    tmp_path: Path,
) -> None:
    input_path = _write_json(tmp_path / "input.json", _series_payload())
    first = tmp_path / "run1" / "envelope.json"

    first_run = _run_cli("--input", str(input_path), "--output", str(first))

    assert first_run.returncode == 0, first_run.stderr

    changed = _series_payload()
    changed["bars"][0]["close"] = "101"
    changed_path = _write_json(tmp_path / "changed.json", changed)
    changed_output = tmp_path / "changed-envelope.json"
    changed_run = _run_cli(
        "--input", str(changed_path), "--output", str(changed_output)
    )

    assert changed_run.returncode == 0, changed_run.stderr
    first_envelope = _read_envelope(first)
    changed_envelope = _read_envelope(changed_output)
    assert first_envelope["input_sha256"] != changed_envelope["input_sha256"]


def test_cli_transform_failure_exits_1_with_failure_envelope(
    tmp_path: Path,
) -> None:
    payload = _series_payload(
        bars=[
            *_series_payload()["bars"],
            _bar("2024-06-08T15:00:00+08:00", "95"),
        ],
    )
    input_path = _write_json(tmp_path / "input.json", payload)
    output_path = tmp_path / "envelope.json"

    completed = _run_cli("--input", str(input_path), "--output", str(output_path))

    assert completed.returncode == 1
    envelope = _read_envelope(output_path)
    assert envelope["status"] == "failed"
    assert "as_of" in envelope["error_message"]
    assert "result" not in envelope
