"""Deterministic tool tests for the canonical financial ratio series calculator."""

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

financial = load_script("financial-ratio-series.py")

SCRIPT_PATH = (
    Path(__file__).resolve().parents[3]
    / "skills/hetu-stock-analysis/scripts/financial-ratio-series.py"
)


def _period(
    label: str,
    *,
    revenue: str = "100",
    cost: str = "60",
    attributable_profit: str = "15",
    operating_cash_flow: str = "18",
    current_assets: str = "50",
    current_liabilities: str = "20",
    accounts_receivable: str = "10",
    inventory: str = "12",
    total_assets: str = "100",
) -> dict[str, str]:
    return {
        "period": label,
        "revenue": revenue,
        "cost": cost,
        "attributable_profit": attributable_profit,
        "operating_cash_flow": operating_cash_flow,
        "current_assets": current_assets,
        "current_liabilities": current_liabilities,
        "accounts_receivable": accounts_receivable,
        "inventory": inventory,
        "total_assets": total_assets,
    }


def _series_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "subject": "002371.SZ",
        "scope": "consolidated",
        "unit": "CNY",
        "input_hash": "a1b2c3d4e5f6a7b8",
        "periods": [
            _period("2022-12-31"),
            _period(
                "2023-12-31",
                revenue="110",
                cost="66",
                attributable_profit="16.5",
                operating_cash_flow="19.8",
                current_assets="55",
                current_liabilities="22",
                accounts_receivable="11",
                inventory="13.2",
                total_assets="110",
            ),
            _period(
                "2024-12-31",
                revenue="121",
                cost="72.6",
                attributable_profit="18.15",
                operating_cash_flow="21.78",
                current_assets="60.5",
                current_liabilities="24.2",
                accounts_receivable="12.1",
                inventory="14.52",
                total_assets="121",
            ),
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


def test_two_period_margins_and_asset_ratios_use_explicit_decimals() -> None:
    result = financial.calculate_financial_ratio_series(_series_payload())

    assert result["periods"][0]["gross_margin_percent"] == "40"
    assert result["periods"][0]["attributable_net_margin_percent"] == "15"
    assert result["periods"][0]["operating_cash_conversion_percent"] == "120"
    assert result["periods"][0]["net_working_capital"] == "30"
    assert result["periods"][0]["net_working_capital_to_revenue_percent"] == "30"
    assert result["periods"][0]["receivables_to_total_assets_percent"] == "10"
    assert result["periods"][0]["inventory_to_total_assets_percent"] == "12"

    assert result["periods"][1]["gross_margin_percent"] == "40"
    assert result["periods"][1]["attributable_net_margin_percent"] == "15.0"
    assert result["periods"][1]["operating_cash_conversion_percent"] == "120"
    assert result["periods"][1]["net_working_capital"] == "33"
    assert result["periods"][1]["receivables_to_total_assets_percent"] == "10"
    assert result["periods"][2]["gross_margin_percent"] == "40.0"
    assert result["periods"][2]["inventory_to_total_assets_percent"] == "12.00"


def test_negative_working_capital_keeps_sign_without_interpretation() -> None:
    payload = _series_payload()
    payload["periods"] = [
        _period("2022-12-31", current_assets="20", current_liabilities="50"),
        _period("2023-12-31", current_assets="22", current_liabilities="55"),
    ]

    result = financial.calculate_financial_ratio_series(payload)

    assert result["periods"][0]["net_working_capital"] == "-30"
    assert result["periods"][0]["net_working_capital_to_revenue_percent"] == "-30"


def test_inexact_ratios_keep_full_decimal_context() -> None:
    payload = _series_payload()
    payload["periods"] = [
        _period("2022-12-31", revenue="3", cost="1"),
        _period("2023-12-31", revenue="6", cost="2"),
    ]

    result = financial.calculate_financial_ratio_series(payload)

    assert result["periods"][0]["gross_margin_percent"] == (
        "66.66666666666666666666666667"
    )
    assert Decimal(result["periods"][0]["gross_margin_percent"]) == (
        Decimal(2) * Decimal(100) / Decimal(3)
    )


def test_series_outputs_are_independent_of_ambient_decimal_context() -> None:
    payload = _series_payload()
    payload["periods"] = [
        _period("2022-12-31", revenue="3", cost="1"),
        _period("2023-12-31", revenue="6", cost="2"),
    ]

    default_context = decimal.getcontext().copy()
    try:
        baseline = financial.calculate_financial_ratio_series(payload)
        decimal.getcontext().prec = 10
        constrained = financial.calculate_financial_ratio_series(payload)
    finally:
        decimal.setcontext(default_context)

    assert constrained == baseline
    assert baseline["periods"][0]["gross_margin_percent"] == (
        "66.66666666666666666666666667"
    )
    assert len(
        Decimal(baseline["periods"][0]["gross_margin_percent"]).as_tuple().digits
    ) > 10


def test_endpoint_positive_revenue_and_profit_cagr_are_annualized() -> None:
    result = financial.calculate_financial_ratio_series(_series_payload())

    assert result["cagr"]["revenue_percent"] == "10.0"
    assert result["cagr"]["attributable_profit_percent"] == "10.0"


def test_cagr_uses_true_year_distance_between_endpoint_dates() -> None:
    # Two endpoints two years apart: +21% over the true ACT/365.25 span is
    # about 10% per year, never the 21% a one-interval span would report.
    payload = _series_payload()
    payload["periods"] = [
        _period("2022-12-31", revenue="100", attributable_profit="15"),
        _period(
            "2024-12-31",
            revenue="121",
            cost="72.6",
            attributable_profit="18.15",
        ),
    ]

    result = financial.calculate_financial_ratio_series(payload)

    assert result["cagr"]["revenue_percent"] == "10.0"
    assert result["cagr"]["attributable_profit_percent"] == "10.0"


def test_compact_period_label_is_accepted() -> None:
    payload = _series_payload()
    payload["periods"] = [
        _period("20221231"),
        _period("20231231", revenue="110"),
    ]

    result = financial.calculate_financial_ratio_series(payload)

    assert [entry["period"] for entry in result["periods"]] == [
        "20221231",
        "20231231",
    ]
    assert result["cagr"]["revenue_percent"] == "10.0"


def test_bare_year_period_label_is_rejected() -> None:
    payload = _series_payload()
    payload["periods"] = [_period("2022-12-31"), _period("2023")]

    with pytest.raises(ValueError, match="period must be a YYYY-MM-DD"):
        financial.calculate_financial_ratio_series(payload)


def test_impossible_calendar_period_is_rejected() -> None:
    payload = _series_payload()
    payload["periods"] = [_period("2022-12-31"), _period("2023-02-30")]

    with pytest.raises(ValueError, match="period"):
        financial.calculate_financial_ratio_series(payload)


def test_equivalent_duplicate_period_dates_are_rejected() -> None:
    payload = _series_payload()
    payload["periods"] = [_period("2022-12-31"), _period("20221231")]

    with pytest.raises(ValueError, match="ascending"):
        financial.calculate_financial_ratio_series(payload)


def test_cagr_is_not_computed_when_endpoint_is_not_positive() -> None:
    payload = _series_payload()
    payload["periods"] = [
        _period("2022-12-31", attributable_profit="-5"),
        _period("2023-12-31", revenue="121", attributable_profit="10"),
    ]

    result = financial.calculate_financial_ratio_series(payload)

    assert result["cagr"]["revenue_percent"] == "21.0"
    assert result["cagr"]["attributable_profit_status"] == "not_computable"
    assert result["cagr"]["attributable_profit_reason"]
    assert "attributable_profit_percent" not in result["cagr"]


def test_cagr_is_not_computed_without_positive_span() -> None:
    payload = _series_payload()
    payload["periods"] = [_period("2022-12-31")]

    result = financial.calculate_financial_ratio_series(payload)

    assert result["cagr"]["revenue_status"] == "not_computable"
    assert result["cagr"]["revenue_reason"]
    assert result["cagr"]["attributable_profit_status"] == "not_computable"
    assert result["cagr"]["attributable_profit_reason"]
    assert "revenue_percent" not in result["cagr"]
    assert "attributable_profit_percent" not in result["cagr"]


def test_tool_echoes_subject_scope_unit_and_period_labels() -> None:
    result = financial.calculate_financial_ratio_series(_series_payload())

    assert result["subject"] == "002371.SZ"
    assert result["scope"] == "consolidated"
    assert result["unit"] == "CNY"
    assert [entry["period"] for entry in result["periods"]] == [
        "2022-12-31",
        "2023-12-31",
        "2024-12-31",
    ]


def test_duplicate_period_is_rejected() -> None:
    payload = _series_payload()
    payload["periods"] = [_period("2022-12-31"), _period("2022-12-31")]

    with pytest.raises(ValueError, match="duplicate period"):
        financial.calculate_financial_ratio_series(payload)


def test_non_ascending_periods_are_rejected() -> None:
    payload = _series_payload()
    payload["periods"] = [_period("2023-12-31"), _period("2022-12-31")]

    with pytest.raises(ValueError, match="ascending"):
        financial.calculate_financial_ratio_series(payload)


def test_missing_unit_is_rejected() -> None:
    payload = _series_payload()
    del payload["unit"]

    with pytest.raises(ValueError, match="unit"):
        financial.calculate_financial_ratio_series(payload)


def test_missing_input_hash_is_rejected() -> None:
    payload = _series_payload()
    del payload["input_hash"]

    with pytest.raises(ValueError, match="input_hash"):
        financial.calculate_financial_ratio_series(payload)


@pytest.mark.parametrize(
    "bad_hash", ("", "xyz12345", "abc", "0" * 7, "0" * 65, 12345678)
)
def test_invalid_input_hash_is_rejected(bad_hash: object) -> None:
    with pytest.raises(ValueError, match="input_hash"):
        financial.calculate_financial_ratio_series(_series_payload(input_hash=bad_hash))


def test_input_hash_is_echoed_verbatim_in_output() -> None:
    result = financial.calculate_financial_ratio_series(
        _series_payload(input_hash="c0ffee1234567890")
    )

    assert result["input_hash"] == "c0ffee1234567890"


def test_missing_scope_is_rejected() -> None:
    payload = _series_payload()
    del payload["scope"]

    with pytest.raises(ValueError, match="scope"):
        financial.calculate_financial_ratio_series(payload)


def test_missing_period_field_is_rejected() -> None:
    payload = _series_payload()
    del payload["periods"][1]["operating_cash_flow"]

    with pytest.raises(ValueError, match="operating_cash_flow"):
        financial.calculate_financial_ratio_series(payload)


def test_zero_denominators_are_rejected() -> None:
    with pytest.raises(ValueError, match="revenue must not be zero"):
        financial.calculate_financial_ratio_series(
            _series_payload(periods=[_period("2022-12-31"), _period("2023-12-31", revenue="0")])
        )
    with pytest.raises(ValueError, match="attributable_profit must not be zero"):
        financial.calculate_financial_ratio_series(
            _series_payload(
                periods=[_period("2022-12-31", attributable_profit="0"), _period("2023-12-31")]
            )
        )
    with pytest.raises(ValueError, match="total_assets must not be zero"):
        financial.calculate_financial_ratio_series(
            _series_payload(periods=[_period("2022-12-31", total_assets="0")])
        )


def test_non_finite_numbers_are_rejected() -> None:
    payload = _series_payload()
    payload["periods"][0]["cost"] = "NaN"

    with pytest.raises(ValueError, match="cost must be finite"):
        financial.calculate_financial_ratio_series(payload)


def test_cli_success_writes_decimal_envelope(tmp_path: Path) -> None:
    input_path = _write_json(tmp_path / "input.json", _series_payload())
    output_path = tmp_path / "out" / "envelope.json"

    completed = _run_cli("--input", str(input_path), "--output", str(output_path))

    assert completed.returncode == 0, completed.stderr
    envelope = _read_envelope(output_path)
    assert envelope["schema_version"] == "1.0"
    assert envelope["tool"] == "financial_ratio_series"
    assert envelope["status"] == "success"
    assert (
        envelope["input_sha256"]
        == hashlib.sha256(input_path.read_bytes()).hexdigest()
    )
    assert envelope["result"]["periods"][0]["gross_margin_percent"] == "40"
    assert envelope["result"]["cagr"]["revenue_percent"] == "10.0"


def test_cli_input_change_changes_hash(
    tmp_path: Path,
) -> None:
    input_path = _write_json(tmp_path / "input.json", _series_payload())
    first = tmp_path / "run1" / "envelope.json"

    first_run = _run_cli("--input", str(input_path), "--output", str(first))

    assert first_run.returncode == 0, first_run.stderr

    changed = _series_payload()
    changed["periods"][0]["revenue"] = "101"
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
    payload = _series_payload()
    payload["periods"][1]["revenue"] = "0"
    input_path = _write_json(tmp_path / "input.json", payload)
    output_path = tmp_path / "envelope.json"

    completed = _run_cli("--input", str(input_path), "--output", str(output_path))

    assert completed.returncode == 1
    envelope = _read_envelope(output_path)
    assert envelope["status"] == "failed"
    assert "must not be zero" in envelope["error_message"]
    assert "result" not in envelope
