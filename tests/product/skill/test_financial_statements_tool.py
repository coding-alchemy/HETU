"""Deterministic tool tests for the canonical financial statements normalizer."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.product.skill.deterministic_tool_loader import load_script

financial = load_script("financial_statements.py")

SCRIPT_PATH = (
    Path(__file__).resolve().parents[3]
    / "skills/hetu-stock-analysis/scripts/financial_statements.py"
)


def _sina_payload() -> dict[str, Any]:
    return {
        "result": {
            "data": {
                "report_list": {
                    "20251231": {
                        "data": [
                            {
                                "item_title": "营业收入",
                                "item_value": "39247153086.41",
                                "item_tongbi": "0.3162",
                            }
                        ]
                    },
                    "20260331": {
                        "data": [
                            {
                                "item_title": "营业收入",
                                "item_value": "10253641782.94",
                                "item_tongbi": "0.2714",
                            },
                            {
                                "item_title": "营业成本",
                                "item_value": "6090662417.87",
                            },
                        ]
                    },
                }
            }
        }
    }


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


def test_normalize_finance_report_orders_periods_and_preserves_values() -> None:
    rows = financial.normalize_finance_report(_sina_payload(), limit=2)

    assert rows == [
        {
            "报告期": "2026-03-31",
            "营业收入": "10253641782.94",
            "营业收入_同比": "0.2714",
            "营业成本": "6090662417.87",
        },
        {
            "报告期": "2025-12-31",
            "营业收入": "39247153086.41",
            "营业收入_同比": "0.3162",
        },
    ]


def test_normalize_finance_report_rejects_missing_report_list() -> None:
    with pytest.raises(ValueError, match="report_list"):
        financial.normalize_finance_report({"result": {"data": {}}}, limit=1)


def test_normalize_finance_report_rejects_duplicate_item_title() -> None:
    payload = {
        "result": {
            "data": {
                "report_list": {
                    "20260331": {
                        "data": [
                            {"item_title": "营业收入", "item_value": "1"},
                            {"item_title": "营业收入", "item_value": "2"},
                        ]
                    }
                }
            }
        }
    }
    with pytest.raises(ValueError, match="duplicate item"):
        financial.normalize_finance_report(payload, limit=1)


@pytest.mark.parametrize(
    "period_key",
    ("2026Q1", "202681", 20260831),
    ids=(
        "test_normalize_finance_report_rejects_invalid_report_period",
        "test_normalize_finance_report_rejects_non_padded_period_key",
        "test_normalize_finance_report_rejects_non_string_period_key",
    ),
)
def test_normalize_finance_report_rejects_invalid_report_period(
    period_key: object,
) -> None:
    # strptime("%Y%m%d") would happily read "202681" as 2026-08-01; the
    # contract requires a strict 8-digit YYYYMMDD key.
    payload = {
        "result": {"data": {"report_list": {period_key: {"data": []}}}}
    }
    with pytest.raises(ValueError, match="invalid report period"):
        financial.normalize_finance_report(payload, limit=1)


def test_normalize_finance_report_rejects_non_string_item_value() -> None:
    payload = {
        "result": {
            "data": {
                "report_list": {
                    "20260331": {
                        "data": [{"item_title": "营业收入", "item_value": 1}]
                    }
                }
            }
        }
    }
    with pytest.raises(ValueError, match="lacks a string value"):
        financial.normalize_finance_report(payload, limit=1)


@pytest.mark.parametrize("limit", (0, -1, True, "2"))
def test_financial_statements_rejects_invalid_limit(limit: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        financial.normalize_finance_report(
            {"result": {"data": {"report_list": {}}}}, limit=limit
        )


def test_financial_normalizer_rejects_eastmoney_payload_shape() -> None:
    with pytest.raises(ValueError):
        financial.normalize_finance_report(
            {"result": {"data": {"report_list": [{"f183": "-13.5"}]}}}, limit=1
        )


def test_cli_success_writes_success_envelope(tmp_path: Path) -> None:
    input_path = _write_json(tmp_path / "input.json", _sina_payload())
    output_path = tmp_path / "out" / "envelope.json"

    completed = _run_cli(
        "--input", str(input_path), "--output", str(output_path), "--limit", "2"
    )

    assert completed.returncode == 0, completed.stderr
    envelope = json.loads(output_path.read_text(encoding="utf-8"))
    assert envelope["schema_version"] == "1.0"
    assert envelope["tool"] == "financial_statements"
    assert envelope["status"] == "success"
    assert (
        envelope["input_sha256"]
        == hashlib.sha256(input_path.read_bytes()).hexdigest()
    )
    result = envelope["result"]
    assert result["limit"] == 2
    assert result["rows"][0]["报告期"] == "2026-03-31"
    assert result["rows"][1]["报告期"] == "2025-12-31"


def test_cli_transform_failure_exits_1_with_failure_envelope(tmp_path: Path) -> None:
    input_path = _write_json(tmp_path / "input.json", {"result": {"data": {}}})
    output_path = tmp_path / "envelope.json"

    completed = _run_cli("--input", str(input_path), "--output", str(output_path))

    assert completed.returncode == 1
    envelope = json.loads(output_path.read_text(encoding="utf-8"))
    assert envelope["status"] == "failed"
    assert "report_list" in envelope["error_message"]
    assert "result" not in envelope


def test_cli_invalid_limit_value_exits_1_with_failure_envelope(tmp_path: Path) -> None:
    input_path = _write_json(tmp_path / "input.json", _sina_payload())
    output_path = tmp_path / "envelope.json"

    completed = _run_cli(
        "--input", str(input_path), "--output", str(output_path), "--limit", "0"
    )

    assert completed.returncode == 1
    envelope = json.loads(output_path.read_text(encoding="utf-8"))
    assert envelope["status"] == "failed"
    assert "positive integer" in envelope["error_message"]


def test_cli_non_integer_limit_argument_exits_2(tmp_path: Path) -> None:
    input_path = _write_json(tmp_path / "input.json", _sina_payload())
    output_path = tmp_path / "envelope.json"

    completed = _run_cli(
        "--input", str(input_path), "--output", str(output_path), "--limit", "two"
    )

    assert completed.returncode == 2
    assert not output_path.exists()
