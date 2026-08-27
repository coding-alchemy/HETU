"""Unified CLI contract checks across the five deterministic skill scripts.

Each tool keeps the same file-based contract: ``--input``/``--output`` (plus
tool-specific arguments), exit 0 with a success envelope on disk for a legal
input, exit 1 with a failure envelope for unparseable or rejected input, exit 2
for argument errors or a missing input file, and refusal with exit 2 when the
output file already exists. The market series tool is exercised through real
subprocess calls here as well; its dedicated CLI RED lives in its own module.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, NamedTuple

import pytest

SCRIPTS_DIR = (
    Path(__file__).resolve().parents[3] / "skills/hetu-stock-analysis/scripts"
)


class ToolCase(NamedTuple):
    """Minimal legal invocation for one deterministic tool."""

    id: str
    script: str
    tool: str
    extra_args: tuple[str, ...]
    valid_input: dict[str, Any]


FINANCIAL_STATEMENTS_INPUT = {
    "result": {
        "data": {
            "report_list": {
                "20241231": {
                    "data": [
                        {"item_title": "营业收入", "item_value": "100"},
                    ],
                },
            },
        },
    },
}

ANNOUNCEMENT_INDEX_INPUT = {
    "pages": [],
    "as_of": "2025-01-31T18:00:00+08:00",
}

NUMERIC_CONSISTENCY_INPUT = {
    "revenue": "100",
    "revenue_unit": "CNY",
    "cost": "60",
    "cost_unit": "CNY",
}

FINANCIAL_RATIO_SERIES_INPUT = {
    "subject": "600519.SH",
    "scope": "consolidated",
    "unit": "CNY",
    "input_hash": "a1b2c3d4e5f6a7b8",
    "periods": [
        {
            "period": "2024-12-31",
            "revenue": "100",
            "cost": "60",
            "attributable_profit": "15",
            "operating_cash_flow": "18",
            "current_assets": "50",
            "current_liabilities": "20",
            "accounts_receivable": "10",
            "inventory": "12",
            "total_assets": "100",
        },
    ],
}

MARKET_SERIES_METRICS_INPUT = {
    "subject": "600519.SH",
    "adjustment": "qfq",
    "timezone": "Asia/Shanghai",
    "as_of": "2024-06-07T15:00:00+08:00",
    "input_hash": "a1b2c3d4e5f6a7b8",
    "window": 2,
    "bars": [
        {"timestamp": "2024-06-06T15:00:00+08:00", "close": "10"},
        {"timestamp": "2024-06-07T15:00:00+08:00", "close": "11"},
    ],
}

TOOL_CASES = [
    ToolCase(
        id="financial_statements",
        script="financial_statements.py",
        tool="financial_statements",
        extra_args=(),
        valid_input=FINANCIAL_STATEMENTS_INPUT,
    ),
    ToolCase(
        id="announcement_index",
        script="announcement_index.py",
        tool="announcement_index",
        extra_args=(),
        valid_input=ANNOUNCEMENT_INDEX_INPUT,
    ),
    ToolCase(
        id="numeric_consistency",
        script="numeric_consistency.py",
        tool="numeric_consistency",
        extra_args=("--operation", "gross_margin_percent"),
        valid_input=NUMERIC_CONSISTENCY_INPUT,
    ),
    ToolCase(
        id="financial_ratio_series",
        script="financial-ratio-series.py",
        tool="financial_ratio_series",
        extra_args=(),
        valid_input=FINANCIAL_RATIO_SERIES_INPUT,
    ),
    ToolCase(
        id="market_series_metrics",
        script="market-series-metrics.py",
        tool="market_series_metrics",
        extra_args=(),
        valid_input=MARKET_SERIES_METRICS_INPUT,
    ),
]


def _run_script(
    case: ToolCase,
    input_path: Path | None,
    output_path: Path | None,
) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(SCRIPTS_DIR / case.script), *case.extra_args]
    if input_path is not None:
        command.extend(["--input", str(input_path)])
    if output_path is not None:
        command.extend(["--output", str(output_path)])
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )


def _write_json(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _read_envelope(output_path: Path) -> dict[str, Any]:
    return json.loads(output_path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", TOOL_CASES, ids=[case.id for case in TOOL_CASES])
def test_same_saved_input_replays_byte_identically_for_every_tool(
    case: ToolCase, tmp_path: Path
) -> None:
    input_path = _write_json(tmp_path / "input.json", case.valid_input)
    first_output = tmp_path / "run-one" / "envelope.json"
    second_output = tmp_path / "run-two" / "envelope.json"

    first = _run_script(case, input_path, first_output)
    second = _run_script(case, input_path, second_output)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first_output.read_bytes() == second_output.read_bytes()


@pytest.mark.parametrize("case", TOOL_CASES, ids=[case.id for case in TOOL_CASES])
def test_valid_input_exits_0_with_success_envelope(
    case: ToolCase, tmp_path: Path
) -> None:
    input_path = _write_json(tmp_path / "input.json", case.valid_input)
    output_path = tmp_path / "out" / "envelope.json"

    completed = _run_script(case, input_path, output_path)

    assert completed.returncode == 0, completed.stderr
    envelope = _read_envelope(output_path)
    assert envelope["schema_version"] == "1.0"
    assert envelope["tool"] == case.tool
    assert envelope["status"] == "success"
    assert (
        envelope["input_sha256"]
        == hashlib.sha256(input_path.read_bytes()).hexdigest()
    )
    assert "result" in envelope


@pytest.mark.parametrize("case", TOOL_CASES, ids=[case.id for case in TOOL_CASES])
def test_unparseable_input_exits_1_with_failure_envelope(
    case: ToolCase, tmp_path: Path
) -> None:
    input_path = tmp_path / "input.json"
    input_path.write_text("{not json", encoding="utf-8")
    output_path = tmp_path / "envelope.json"

    completed = _run_script(case, input_path, output_path)

    assert completed.returncode == 1
    envelope = _read_envelope(output_path)
    assert envelope["schema_version"] == "1.0"
    assert envelope["tool"] == case.tool
    assert envelope["status"] == "failed"
    assert envelope["error_type"]
    assert envelope["error_message"]
    assert "result" not in envelope


@pytest.mark.parametrize("case", TOOL_CASES, ids=[case.id for case in TOOL_CASES])
def test_invalid_arguments_exit_2(case: ToolCase, tmp_path: Path) -> None:
    input_path = _write_json(tmp_path / "input.json", case.valid_input)

    # Missing the required --output argument is an argument error for every tool.
    completed = _run_script(case, input_path, None)

    assert completed.returncode == 2


@pytest.mark.parametrize("case", TOOL_CASES, ids=[case.id for case in TOOL_CASES])
def test_missing_input_file_exits_2(case: ToolCase, tmp_path: Path) -> None:
    output_path = tmp_path / "envelope.json"

    completed = _run_script(case, tmp_path / "absent.json", output_path)

    assert completed.returncode == 2
    assert not output_path.exists()


@pytest.mark.parametrize("case", TOOL_CASES, ids=[case.id for case in TOOL_CASES])
def test_existing_output_is_refused_with_exit_2(
    case: ToolCase, tmp_path: Path
) -> None:
    input_path = _write_json(tmp_path / "input.json", case.valid_input)
    output_path = tmp_path / "envelope.json"
    output_path.write_text("existing evidence\n", encoding="utf-8")

    completed = _run_script(case, input_path, output_path)

    assert completed.returncode == 2
    assert output_path.read_text(encoding="utf-8") == "existing evidence\n"
