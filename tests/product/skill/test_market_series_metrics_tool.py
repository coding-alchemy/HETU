"""Deterministic tool tests for the canonical market series metrics calculator."""

from __future__ import annotations

import decimal
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from tests.product.skill.deterministic_tool_loader import load_script

market = load_script("market-series-metrics.py")

SCRIPT_PATH = (
    Path(__file__).resolve().parents[3]
    / "skills/hetu-stock-analysis/scripts/market-series-metrics.py"
)

_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _daily_timestamp(index: int) -> str:
    moment = datetime(2026, 4, 1, 15, 0, 0, tzinfo=_SHANGHAI) + timedelta(days=index)
    return moment.isoformat()

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


def _ohlcv_payload() -> dict[str, Any]:
    dates = ("01", "02", "03", "04", "05", "08")
    closes = ("10", "12", "11", "9", "8", "10")
    bars = []
    for index, (day, close) in enumerate(zip(dates, closes, strict=True)):
        bars.append(
            {
                "timestamp": f"2026-06-{day}T15:00:00+08:00",
                "open": close,
                "high": "12",
                "low": "7",
                "close": close,
                "volume": "200" if index == 5 else "100",
                "amount": "2000",
                "reference_close": "8" if index == 5 else None,
                "float_shares": "10000",
            }
        )
    return {
        "subject": "合成证券",
        "timezone": "Asia/Shanghai",
        "as_of": "2026-06-08T18:00:00+08:00",
        "input_hash": "a1b2c3d4",
        "adjustment": "raw",
        "adjustment_basis": "合成同口径序列，无公司行动",
        "display_start": bars[0]["timestamp"],
        "focus_start": bars[0]["timestamp"],
        "units": {
            "price": "CNY/share",
            "volume": "share",
            "amount": "CNY",
            "float_shares": "share",
        },
        "bars": bars,
        "gaps": [],
    }


def _ohlcv_bar(timestamp: str, close: str) -> dict[str, Any]:
    return {
        "timestamp": timestamp,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": "100",
        "amount": "1000",
        "reference_close": None,
        "float_shares": "10000",
    }


def test_ohlcv_metrics_use_prior_volume_and_explicit_share_count() -> None:
    result = market.calculate_ohlcv_metrics(_ohlcv_payload())
    last = result["daily"][-1]
    assert last["ma"]["5"] == "10"
    assert last["ma"]["10"] is None
    assert last["volume_ratio_5"] == "2"
    assert last["turnover_percent"] == "2.00"
    assert last["change_percent"] == "25.00"
    assert last["amplitude_percent"] == "62.500"
    assert last["volatility_20_annual_percent"] is None
    assert result["windows"]["full"]["return_percent"] == "0"
    assert result["windows"]["full"]["max_drawdown_percent"] is not None
    assert result["windows"]["full"]["reason"] is None
    assert last["unavailable"]["ma_10"] == "insufficient_history"
    assert last["unavailable"]["volatility_20_annual_percent"] == (
        "insufficient_or_zero_input"
    )
    assert result["adjustment_basis"] == "合成同口径序列，无公司行动"
    assert result["gaps"] == []
    assert set(result) == {
        "subject",
        "as_of",
        "timezone",
        "adjustment",
        "adjustment_basis",
        "input_hash",
        "units",
        "daily",
        "windows",
        "gaps",
    }


def test_ohlcv_daily_output_keeps_bar_rows_with_null_inputs_flagged() -> None:
    result = market.calculate_ohlcv_metrics(_ohlcv_payload())
    assert len(result["daily"]) == 6
    first = result["daily"][0]
    assert first["reference_close"] is None
    assert first["change_percent"] is None
    assert first["amplitude_percent"] is None
    assert first["unavailable"]["change_percent"] == "missing_input"
    assert first["unavailable"]["amplitude_percent"] == "missing_input"


def test_ohlcv_volatility_is_zero_for_constant_growth() -> None:
    payload = _ohlcv_payload()
    bars = []
    closes = [Decimal(10) * (Decimal("1.01") ** index) for index in range(22)]
    for index, close in enumerate(closes):
        bars.append(_ohlcv_bar(_daily_timestamp(index), format(close, "f")))
    payload["bars"] = bars
    payload["display_start"] = bars[0]["timestamp"]
    payload["focus_start"] = bars[0]["timestamp"]
    result = market.calculate_ohlcv_metrics(payload)
    volatility = result["daily"][-1]["volatility_20_annual_percent"]
    assert volatility is not None
    # Constant multiplicative growth: log returns are equal, so the sample
    # variance is zero up to the pinned 28-digit Decimal precision.
    assert Decimal(volatility) < Decimal("1E-20")


def test_ohlcv_volatility_matches_independent_high_precision_expectation() -> None:
    closes = [Decimal(10 + (index % 3)) for index in range(21)]
    with decimal.localcontext(
        decimal.Context(prec=60, rounding=decimal.ROUND_HALF_EVEN)
    ):
        changes = [
            (right / left).ln() for left, right in zip(closes, closes[1:], strict=False)
        ]
        mean = sum(changes, Decimal(0)) / Decimal(20)
        variance = sum(
            ((value - mean) ** 2 for value in changes), Decimal(0)
        ) / Decimal(19)
        expected = variance.sqrt() * Decimal(252).sqrt() * Decimal(100)
    payload = _ohlcv_payload()
    bars = [
        _ohlcv_bar(_daily_timestamp(index), format(close, "f"))
        for index, close in enumerate(closes)
    ]
    payload["bars"] = bars
    payload["display_start"] = bars[0]["timestamp"]
    payload["focus_start"] = bars[0]["timestamp"]
    result = market.calculate_ohlcv_metrics(payload)
    got = Decimal(result["daily"][-1]["volatility_20_annual_percent"])
    assert abs(got - expected) < Decimal("1E-20")


def test_ohlcv_ma60_stays_null_with_only_59_available_bars() -> None:
    payload = _ohlcv_payload()
    bars = [
        _ohlcv_bar(_daily_timestamp(index), format(10 + index))
        for index in range(59)
    ]
    payload["bars"] = bars
    payload["display_start"] = bars[0]["timestamp"]
    payload["focus_start"] = bars[0]["timestamp"]
    result = market.calculate_ohlcv_metrics(payload)
    assert result["daily"][-1]["ma"]["60"] is None
    assert result["daily"][-1]["unavailable"]["ma_60"] == "insufficient_history"

    sixty = [
        _ohlcv_bar(_daily_timestamp(index), format(10 + index))
        for index in range(60)
    ]
    payload["bars"] = sixty
    result60 = market.calculate_ohlcv_metrics(payload)
    assert result60["daily"][-1]["ma"]["60"] is not None


def test_ohlcv_warmup_rows_are_excluded_from_displayed_daily() -> None:
    payload = _ohlcv_payload()
    warmup = [
        _ohlcv_bar(_daily_timestamp(index), format(5 + index))
        for index in range(10)
    ]
    payload["bars"] = [*warmup, *payload["bars"]]
    result = market.calculate_ohlcv_metrics(payload)
    assert result["daily"][0]["timestamp"] == payload["display_start"]
    assert len(result["daily"]) == 6
    assert result["windows"]["full"]["effective_start"] == payload["display_start"]


def test_ohlcv_missing_gap_blocks_rolling_metrics_but_suspension_does_not() -> None:
    payload = _ohlcv_payload()
    payload["gaps"] = [
        {
            "start": "2026-06-02T00:00:00+08:00",
            "end": "2026-06-03T00:00:00+08:00",
            "kind": "missing",
            "evidence": "合成缺失日证据",
        }
    ]
    result = market.calculate_ohlcv_metrics(payload)
    blocked = result["daily"][-1]
    assert blocked["ma"]["5"] is None
    assert blocked["unavailable"]["ma_5"] == "price_gap"
    assert blocked["volume_ratio_5"] is None
    assert result["windows"]["full"]["return_percent"] is None
    assert result["windows"]["full"]["reason"] == "price_gap"

    suspended = {
        **payload,
        "gaps": [
            {
                "start": "2026-06-02T00:00:00+08:00",
                "end": "2026-06-03T00:00:00+08:00",
                "kind": "suspension",
                "evidence": "合成停牌证据",
            }
        ],
    }
    suspended_result = market.calculate_ohlcv_metrics(suspended)
    assert suspended_result["daily"][-1]["ma"]["5"] == "10"
    assert suspended_result["windows"]["full"]["return_percent"] == "0"


def test_ohlcv_intraday_as_of_keeps_completed_daily_rows() -> None:
    payload = _ohlcv_payload()
    payload["as_of"] = "2026-06-08T16:30:00+08:00"
    result = market.calculate_ohlcv_metrics(payload)
    assert result["daily"][-1]["timestamp"] == "2026-06-08T15:00:00+08:00"


@pytest.mark.parametrize(
    "mutation",
    [
        "bad-ohlc",
        "duplicate-timestamp",
        "inverted-timestamps",
        "future-bar",
        "wrong-timezone-offset",
        "negative-volume",
        "negative-amount",
        "zero-float-shares",
        "bad-units",
        "extra-top-field",
        "missing-field",
        "focus-before-display",
        "display-after-as-of",
    ],
)
def test_ohlcv_invalid_inputs_are_rejected(mutation: str) -> None:
    payload = _ohlcv_payload()
    if mutation == "bad-ohlc":
        payload["bars"][0]["low"] = "99"
    elif mutation == "duplicate-timestamp":
        payload["bars"][1]["timestamp"] = payload["bars"][0]["timestamp"]
    elif mutation == "inverted-timestamps":
        payload["bars"][0]["timestamp"] = "2026-06-08T15:00:00+08:00"
    elif mutation == "future-bar":
        payload["bars"][-1]["timestamp"] = "2026-06-09T15:00:00+08:00"
    elif mutation == "wrong-timezone-offset":
        payload["as_of"] = "2026-06-08T18:00:00+09:00"
    elif mutation == "negative-volume":
        payload["bars"][0]["volume"] = "-1"
    elif mutation == "negative-amount":
        payload["bars"][0]["amount"] = "-1"
    elif mutation == "zero-float-shares":
        payload["bars"][0]["float_shares"] = "0"
    elif mutation == "bad-units":
        payload["units"] = {
            "price": "CNY",
            "volume": "share",
            "amount": "CNY",
            "float_shares": "share",
        }
    elif mutation == "extra-top-field":
        payload["unexpected"] = True
    elif mutation == "missing-field":
        del payload["adjustment_basis"]
    elif mutation == "focus-before-display":
        payload["focus_start"] = "2026-05-01T15:00:00+08:00"
    elif mutation == "display-after-as-of":
        payload["display_start"] = "2026-06-09T15:00:00+08:00"
    with pytest.raises(ValueError):
        market.calculate_ohlcv_metrics(payload)


def test_ohlcv_zero_prior_average_volume_gives_null_reason() -> None:
    payload = _ohlcv_payload()
    for bar in payload["bars"][:5]:
        bar["volume"] = "0"
    result = market.calculate_ohlcv_metrics(payload)
    last = result["daily"][-1]
    assert last["volume_ratio_5"] is None
    assert last["unavailable"]["volume_ratio_5"] == "insufficient_or_zero_input"
    assert last["ma"]["5"] == "10"


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


def test_cli_ohlcv_operation_writes_daily_and_windows(tmp_path: Path) -> None:
    input_path = _write_json(tmp_path / "input.json", _ohlcv_payload())
    output_path = tmp_path / "ohlcv-envelope.json"

    completed = _run_cli(
        "--input", str(input_path), "--output", str(output_path), "--operation", "ohlcv"
    )

    assert completed.returncode == 0, completed.stderr
    envelope = _read_envelope(output_path)
    assert envelope["tool"] == "market_series_metrics"
    assert envelope["status"] == "success"
    assert envelope["result"]["daily"][-1]["ma"]["5"] == "10"
    assert envelope["result"]["windows"]["full"]["return_percent"] == "0"


def test_cli_ohlcv_failure_writes_failure_envelope(tmp_path: Path) -> None:
    payload = _ohlcv_payload()
    payload["bars"][0]["low"] = "99"
    input_path = _write_json(tmp_path / "input.json", payload)
    output_path = tmp_path / "ohlcv-failure.json"

    completed = _run_cli(
        "--input", str(input_path), "--output", str(output_path), "--operation", "ohlcv"
    )

    assert completed.returncode == 1
    envelope = _read_envelope(output_path)
    assert envelope["status"] == "failed"
    assert "result" not in envelope


def test_cli_default_operation_is_close_series(tmp_path: Path) -> None:
    input_path = _write_json(tmp_path / "input.json", _series_payload())
    output_path = tmp_path / "default-envelope.json"

    completed = _run_cli("--input", str(input_path), "--output", str(output_path))

    assert completed.returncode == 0, completed.stderr
    envelope = _read_envelope(output_path)
    assert set(envelope["result"]) == OUTPUT_FIELDS
