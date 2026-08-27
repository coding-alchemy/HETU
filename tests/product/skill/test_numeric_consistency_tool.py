"""Deterministic tool tests for the canonical numeric consistency calculator."""

from __future__ import annotations

import decimal
import hashlib
import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from tests.product.skill.deterministic_tool_loader import load_script

numeric = load_script("numeric_consistency.py")

SCRIPT_PATH = (
    Path(__file__).resolve().parents[3]
    / "skills/hetu-stock-analysis/scripts/numeric_consistency.py"
)


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


def test_glm_gross_margin_mapping_is_rejected_by_absolute_value_check() -> None:
    recomputed = numeric.gross_margin_percent(
        revenue="10253641782.94",
        revenue_unit="CNY",
        cost="6090662417.87",
        cost_unit="CNY",
    )
    check = numeric.compare_metric(
        metric="gross_margin_percent",
        unit="percent",
        claimed="50.010921849",
        recomputed=recomputed,
        tolerance="0.01",
    )

    assert recomputed.quantize(Decimal("0.0001")) == Decimal("40.6000")
    assert check.consistent is False
    assert check.absolute_difference > Decimal("9")


def test_glm_claimed_gross_margin_matches_debt_ratio_instead() -> None:
    ratio = numeric.debt_ratio_percent(
        liabilities="45812369470.55",
        liabilities_unit="CNY",
        assets="91604729081.31",
        assets_unit="CNY",
    )

    assert ratio.quantize(Decimal("0.0001")) == Decimal("50.0109")


def test_market_cap_values_expose_swapped_total_and_float_labels() -> None:
    total, floating = numeric.market_cap_values(
        price="649.58",
        price_unit="CNY/share",
        total_shares="631475820",
        total_shares_unit="share",
        float_shares="630958462",
        float_shares_unit="share",
    )
    total_check = numeric.compare_metric(
        metric="total_market_cap",
        unit="CNY",
        claimed="409857997745.96",
        recomputed=total,
        tolerance="1",
    )
    float_check = numeric.compare_metric(
        metric="float_market_cap",
        unit="CNY",
        claimed="410194063155.60",
        recomputed=floating,
        tolerance="1",
    )

    assert total_check.consistent is False
    assert float_check.consistent is False
    assert floating < total


def test_price_earnings_ratio_uses_matching_market_cap_and_profit_units() -> None:
    ratio = numeric.price_earnings_ratio(
        market_cap="482156347901",
        market_cap_unit="CNY",
        attributable_profit="4961475284.52",
        attributable_profit_unit="CNY",
    )

    assert ratio.quantize(Decimal("0.01")) == Decimal("97.18")


def test_numeric_consistency_rejects_non_finite_and_zero_denominators() -> None:
    with pytest.raises(ValueError, match="must be finite"):
        numeric.compare_metric(
            metric="x", unit="percent", claimed="NaN", recomputed="1", tolerance="0"
        )
    with pytest.raises(ValueError, match="must not be zero"):
        numeric.gross_margin_percent(
            revenue="0", revenue_unit="CNY", cost="1", cost_unit="CNY"
        )


def test_outputs_are_independent_of_ambient_decimal_context() -> None:
    default_context = decimal.getcontext().copy()
    try:
        baseline = numeric.gross_margin_percent(
            revenue="3", revenue_unit="CNY", cost="1", cost_unit="CNY"
        )
        decimal.getcontext().prec = 10
        constrained = numeric.gross_margin_percent(
            revenue="3", revenue_unit="CNY", cost="1", cost_unit="CNY"
        )
        check = numeric.compare_metric(
            metric="gross_margin_percent",
            unit="percent",
            claimed=baseline,
            recomputed=constrained,
            tolerance="0",
        )
    finally:
        decimal.setcontext(default_context)

    assert constrained == baseline
    assert check.absolute_difference == Decimal("0")
    assert check.consistent is True
    # A 28-digit division result proves the pinned precision was used.
    assert baseline == Decimal(2) * Decimal(100) / Decimal(3)
    assert len(baseline.as_tuple().digits) > 10


def test_market_cap_check_does_not_swap_source_labels() -> None:
    total, floating = numeric.market_cap_values(
        price="10",
        price_unit="CNY/share",
        total_shares="100",
        total_shares_unit="share",
        float_shares="80",
        float_shares_unit="share",
    )
    total_check = numeric.compare_metric(
        metric="total_market_cap",
        unit="CNY",
        claimed="800",
        recomputed=total,
        tolerance="0",
    )
    assert total == Decimal("1000")
    assert floating == Decimal("800")
    assert total_check.consistent is False


def test_compare_metric_keeps_both_values_and_difference() -> None:
    check = numeric.compare_metric(
        metric="revenue",
        unit="CNY",
        claimed="100",
        recomputed="97.5",
        tolerance="2.5",
    )

    assert check.unit == "CNY"
    assert check.claimed == Decimal("100")
    assert check.recomputed == Decimal("97.5")
    assert check.absolute_difference == Decimal("2.5")
    assert check.consistent is True


def test_compare_metric_rejects_negative_tolerance() -> None:
    with pytest.raises(ValueError, match="tolerance must not be negative"):
        numeric.compare_metric(
            metric="x", unit="percent", claimed="1", recomputed="1", tolerance="-0.1"
        )


def test_compare_metric_requires_an_explicit_unit() -> None:
    with pytest.raises(TypeError):
        numeric.compare_metric(
            metric="revenue", claimed="1", recomputed="1", tolerance="0"
        )
    with pytest.raises(ValueError, match="unit must be a non-empty string"):
        numeric.compare_metric(
            metric="revenue", unit="  ", claimed="1", recomputed="1", tolerance="0"
        )
    with pytest.raises(ValueError, match="unit must be a non-empty string"):
        numeric.compare_metric(
            metric="revenue",
            unit=1,  # type: ignore[arg-type]
            claimed="1",
            recomputed="1",
            tolerance="0",
        )


@pytest.mark.parametrize(
    ("operation", "payload", "fragment"),
    (
        (
            "compare_metric",
            {
                "metric": "total_market_cap",
                "claimed": "800",
                "recomputed": "1000",
                "tolerance": "0",
            },
            "unit",
        ),
        (
            "gross_margin_percent",
            {
                "revenue": "0",
                "revenue_unit": "CNY",
                "cost": "1",
                "cost_unit": "CNY",
            },
            "must not be zero",
        ),
        (
            "market_cap_values",
            {
                "price": "649.58",
                "price_unit": "CNY/share",
                "total_shares": "631475820",
                "total_shares_unit": "share",
                "float_shares": "630958462",
                "float_shares_unit": "thousand shares",
            },
            "must match",
        ),
    ),
    ids=(
        "test_cli_compare_metric_missing_unit_exits_1_with_failure_envelope",
        "test_cli_zero_denominator_exits_1_with_failure_envelope",
        "test_cli_mismatched_units_exit_1_with_failure_envelope",
    ),
)
def test_cli_failures_exit_1_with_failure_envelope(
    tmp_path: Path, operation: str, payload: dict[str, Any], fragment: str
) -> None:
    input_path = _write_json(tmp_path / "input.json", payload)
    output_path = tmp_path / "envelope.json"

    completed = _run_cli(
        "--operation", operation,
        "--input", str(input_path),
        "--output", str(output_path),
    )

    assert completed.returncode == 1
    envelope = _decimal_envelope(output_path)
    assert envelope["status"] == "failed"
    assert fragment in envelope["error_message"]
    assert "result" not in envelope


def _decimal_envelope(output_path: Path) -> dict[str, Any]:
    return json.loads(output_path.read_text(encoding="utf-8"))


def test_cli_compare_metric_outputs_decimal_strings(tmp_path: Path) -> None:
    input_path = _write_json(
        tmp_path / "input.json",
        {
            "metric": "total_market_cap",
            "unit": "CNY",
            "claimed": "800",
            "recomputed": "1000",
            "tolerance": "0",
        },
    )
    output_path = tmp_path / "out" / "envelope.json"

    completed = _run_cli(
        "--operation", "compare_metric",
        "--input", str(input_path),
        "--output", str(output_path),
    )

    assert completed.returncode == 0, completed.stderr
    envelope = _decimal_envelope(output_path)
    assert envelope["schema_version"] == "1.0"
    assert envelope["tool"] == "numeric_consistency"
    assert envelope["status"] == "success"
    assert (
        envelope["input_sha256"]
        == hashlib.sha256(input_path.read_bytes()).hexdigest()
    )
    result = envelope["result"]
    assert result == {
        "metric": "total_market_cap",
        "unit": "CNY",
        "claimed": "800",
        "recomputed": "1000",
        "tolerance": "0",
        "consistent": False,
        "absolute_difference": "200",
    }


def test_cli_arithmetic_operations_output_decimal_strings(
    tmp_path: Path,
) -> None:
    gross_input = _write_json(
        tmp_path / "gross.json",
        {
            "revenue": "10253641782.94",
            "revenue_unit": "CNY",
            "cost": "6090662417.87",
            "cost_unit": "CNY",
        },
    )
    gross_output = tmp_path / "gross-envelope.json"
    cap_input = _write_json(
        tmp_path / "cap.json",
        {
            "price": "649.58",
            "price_unit": "CNY/share",
            "total_shares": "631475820",
            "total_shares_unit": "share",
            "float_shares": "630958462",
            "float_shares_unit": "share",
        },
    )
    cap_output = tmp_path / "cap-envelope.json"

    gross = _run_cli(
        "--operation", "gross_margin_percent",
        "--input", str(gross_input),
        "--output", str(gross_output),
    )
    cap = _run_cli(
        "--operation", "market_cap_values",
        "--input", str(cap_input),
        "--output", str(cap_output),
    )

    assert gross.returncode == 0, gross.stderr
    assert cap.returncode == 0, cap.stderr
    gross_result = _decimal_envelope(gross_output)["result"]
    cap_result = _decimal_envelope(cap_output)["result"]
    assert isinstance(gross_result["value"], str)
    assert Decimal(gross_result["value"]).quantize(Decimal("0.0001")) == Decimal(
        "40.6000"
    )
    assert Decimal(cap_result["total_market_cap"]) == Decimal("649.58") * Decimal(
        "631475820"
    )
    assert Decimal(cap_result["float_market_cap"]) == Decimal("649.58") * Decimal(
        "630958462"
    )
    assert gross_result["input_units"] == {"revenue": "CNY", "cost": "CNY"}
    assert gross_result["result_unit"] == "%"
    assert cap_result["input_units"] == {
        "price": "CNY/share",
        "total_shares": "share",
        "float_shares": "share",
    }
    assert cap_result["result_unit"] == "CNY/share·share"


def test_cli_non_object_source_exits_1_with_failure_envelope(
    tmp_path: Path,
) -> None:
    input_path = _write_json(
        tmp_path / "input.json",
        {
            "source": "bad",
            "revenue": "1",
            "revenue_unit": "CNY",
            "cost": "1",
            "cost_unit": "CNY",
        },
    )
    output_path = tmp_path / "envelope.json"

    completed = _run_cli(
        "--operation", "gross_margin_percent",
        "--input", str(input_path),
        "--output", str(output_path),
    )

    assert completed.returncode == 1
    envelope = _decimal_envelope(output_path)
    assert envelope["status"] == "failed"
    assert "source must be an object" in envelope["error_message"]
    assert envelope["source"] is None
    assert envelope["source_provided"] is False
    assert "result" not in envelope


def test_arithmetic_operations_reject_mismatched_units() -> None:
    with pytest.raises(ValueError, match="must match"):
        numeric.gross_margin_percent(
            revenue="100", revenue_unit="CNY", cost="1", cost_unit="thousand CNY"
        )
    with pytest.raises(ValueError, match="must match"):
        numeric.debt_ratio_percent(
            liabilities="1", liabilities_unit="CNY", assets="1", assets_unit="万元"
        )
    with pytest.raises(ValueError, match="must match"):
        numeric.market_cap_values(
            price="10",
            price_unit="CNY/share",
            total_shares="100",
            total_shares_unit="share",
            float_shares="80",
            float_shares_unit="thousand shares",
        )
    with pytest.raises(ValueError, match="must match"):
        numeric.price_earnings_ratio(
            market_cap="1000",
            market_cap_unit="CNY",
            attributable_profit="10",
            attributable_profit_unit="万元",
        )


def test_arithmetic_operations_require_explicit_units() -> None:
    with pytest.raises(ValueError, match="revenue_unit must be a non-empty unit string"):
        numeric.gross_margin_percent(revenue="100", revenue_unit=" ", cost="1", cost_unit="CNY")
    with pytest.raises(TypeError):
        numeric.market_cap_values(price="10", total_shares="100", float_shares="80")


def test_cli_concurrent_output_creation_returns_2_without_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import _artifact_io as shared_io

    input_path = _write_json(
        tmp_path / "input.json",
        {"revenue": "100", "revenue_unit": "CNY", "cost": "1", "cost_unit": "CNY"},
    )
    output_path = tmp_path / "envelope.json"
    original_write = shared_io._write_json

    def racing_write(path: Path, envelope: dict[str, object]) -> None:
        # Simulates a concurrent writer creating the output between the
        # friendly pre-checks and the exclusive envelope write.
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("raced evidence\n", encoding="utf-8")
        original_write(path, envelope)

    monkeypatch.setattr(shared_io, "_write_json", racing_write)
    code = numeric.main(
        [
            "--operation", "gross_margin_percent",
            "--input", str(input_path),
            "--output", str(output_path),
        ]
    )

    assert code == 2
    assert output_path.read_text(encoding="utf-8") == "raced evidence\n"


def test_cli_unreadable_input_returns_2_without_partial_output(
    tmp_path: Path,
) -> None:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("permission bits are ineffective for root")
    input_path = _write_json(
        tmp_path / "input.json",
        {"revenue": "100", "revenue_unit": "CNY", "cost": "1", "cost_unit": "CNY"},
    )
    input_path.chmod(0o000)
    output_path = tmp_path / "envelope.json"
    try:
        code = numeric.main(
            [
                "--operation", "gross_margin_percent",
                "--input", str(input_path),
                "--output", str(output_path),
            ]
        )
    finally:
        input_path.chmod(0o644)

    assert code == 2
    assert not output_path.exists()


def test_cli_does_not_use_legacy_second_json_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import _artifact_io as shared_io

    input_path = _write_json(
        tmp_path / "input.json",
        {"revenue": "100", "revenue_unit": "CNY", "cost": "1", "cost_unit": "CNY"},
    )
    output_path = tmp_path / "envelope.json"

    def forbidden_second_read(path: Path) -> dict[str, Any]:
        raise AssertionError(f"unexpected second read: {path}")

    monkeypatch.setattr(shared_io, "read_json_object", forbidden_second_read)
    code = numeric.main(
        [
            "--operation", "gross_margin_percent",
            "--input", str(input_path),
            "--output", str(output_path),
        ]
    )

    assert code == 0
    assert json.loads(output_path.read_text(encoding="utf-8"))["status"] == "success"


def test_cli_unknown_operation_exits_2(tmp_path: Path) -> None:
    input_path = _write_json(tmp_path / "input.json", {"value": "1"})
    output_path = tmp_path / "envelope.json"

    completed = _run_cli(
        "--operation", "sum_values",
        "--input", str(input_path),
        "--output", str(output_path),
    )

    assert completed.returncode == 2
    assert not output_path.exists()


def test_cli_missing_parameter_exits_1_with_failure_envelope(tmp_path: Path) -> None:
    input_path = _write_json(
        tmp_path / "input.json",
        {"revenue": "100", "revenue_unit": "CNY", "cost_unit": "CNY"},
    )
    output_path = tmp_path / "envelope.json"

    completed = _run_cli(
        "--operation", "gross_margin_percent",
        "--input", str(input_path),
        "--output", str(output_path),
    )

    assert completed.returncode == 1
    envelope = _decimal_envelope(output_path)
    assert envelope["status"] == "failed"
    assert "cost" in envelope["error_message"]
