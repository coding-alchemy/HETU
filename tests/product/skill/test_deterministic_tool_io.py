"""Deterministic I/O contract tests for the shared ``_artifact_io`` module."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path, PureWindowsPath
from types import ModuleType
from typing import Any

import pytest

from tests.product.skill.deterministic_tool_loader import load_script

SCRIPTS_DIR = Path("skills/hetu-stock-analysis/scripts")

# Stage 03 closed whitelist: the shared I/O module plus the approved tool
# filenames. The set below must equal the on-disk ``*.py`` filenames exactly
# (strict equality, enforced since Task 5 when the five tools landed). Stage 04
# added ``source_adapter.py`` (mechanical helper) and stage 05 adds
# ``check-run-artifacts.py``; the whitelist grows with those stages only.
APPROVED_SCRIPTS = frozenset(
    {
        "_artifact_io.py",
        "financial_statements.py",
        "announcement_index.py",
        "numeric_consistency.py",
        "pdf_text_extract.py",
        "financial-ratio-series.py",
        "market-series-metrics.py",
        "source_adapter.py",
        "source_fetch.py",
        "check-run-artifacts.py",
    }
)

FORBIDDEN_SECURITY_TOKENS = ("002371", "北方华创", "600519", "贵州茅台")
DATE_LITERAL_PATTERNS = (
    re.compile(r"20\d{2}-\d{2}-\d{2}"),
    re.compile(r"20\d{2}\d{2}\d{2}"),
    re.compile(r"20\d{2}/\d{2}/\d{2}"),
)
SECURITY_CODE_IN_LITERAL = re.compile(
    r"(?<!\d)\d{6}(?:\.(?:SZ|SH|BJ))?(?!\d)", re.IGNORECASE
)


@pytest.fixture()
def artifact_io() -> ModuleType:
    return load_script("_artifact_io.py")


def _echo_transform(payload: dict[str, Any]) -> object:
    return {"records": payload.get("records", [])}


def _failing_transform(payload: dict[str, Any]) -> object:
    raise RuntimeError("transform rejected the payload")


def _write_input(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_envelope(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_same_input_produces_byte_identical_envelopes(
    artifact_io: ModuleType, tmp_path: Path
) -> None:
    input_path = _write_input(tmp_path / "input.json", {"records": [{"value": 1}]})
    first = tmp_path / "run1" / "envelope.json"
    second = tmp_path / "run2" / "envelope.json"

    envelope = artifact_io.run_transform(
        tool_name="sample_tool",
        input_path=input_path,
        output_path=first,
        transform=_echo_transform,
    )
    repeat = artifact_io.run_transform(
        tool_name="sample_tool",
        input_path=input_path,
        output_path=second,
        transform=_echo_transform,
    )

    assert first.read_bytes() == second.read_bytes()
    assert envelope == repeat
    assert envelope["schema_version"] == "1.0"
    assert envelope["tool"] == "sample_tool"
    assert envelope["status"] == "success"
    assert envelope["input_sha256"] == _sha256_file(input_path)
    assert envelope["result"] == {"records": [{"value": 1}]}


def test_input_change_changes_input_sha256(
    artifact_io: ModuleType, tmp_path: Path
) -> None:
    first_input = _write_input(tmp_path / "first.json", {"records": []})
    second_input = _write_input(tmp_path / "second.json", {"records": [{"value": 1}]})
    first = artifact_io.run_transform(
        tool_name="sample_tool",
        input_path=first_input,
        output_path=tmp_path / "first" / "envelope.json",
        transform=_echo_transform,
    )
    second = artifact_io.run_transform(
        tool_name="sample_tool",
        input_path=second_input,
        output_path=tmp_path / "second" / "envelope.json",
        transform=_echo_transform,
    )

    assert first["input_sha256"] == _sha256_file(first_input)
    assert second["input_sha256"] == _sha256_file(second_input)
    assert first["input_sha256"] != second["input_sha256"]


def test_run_transform_reads_input_bytes_once(
    artifact_io: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path = _write_input(tmp_path / "input.json", {"records": [{"value": 1}]})
    output_path = tmp_path / "output.json"
    original_read_bytes = Path.read_bytes
    input_reads = 0

    def counted_read_bytes(path: Path) -> bytes:
        nonlocal input_reads
        if path == input_path:
            input_reads += 1
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", counted_read_bytes)
    envelope = artifact_io.run_transform(
        tool_name="sample_tool",
        input_path=input_path,
        output_path=output_path,
        transform=_echo_transform,
    )

    assert input_reads == 1
    assert envelope["input_sha256"] == hashlib.sha256(
        original_read_bytes(input_path)
    ).hexdigest()


def test_missing_output_parent_directories_are_created(
    artifact_io: ModuleType, tmp_path: Path
) -> None:
    input_path = _write_input(tmp_path / "input.json", {"records": []})
    output_path = tmp_path / "nested" / "deeper" / "envelope.json"

    artifact_io.run_transform(
        tool_name="sample_tool",
        input_path=input_path,
        output_path=output_path,
        transform=_echo_transform,
    )

    assert output_path.is_file()


def test_output_path_must_differ_from_input_path(
    artifact_io: ModuleType, tmp_path: Path
) -> None:
    input_path = _write_input(tmp_path / "input.json", {"records": []})
    original = input_path.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="output_path must differ from input_path"):
        artifact_io.run_transform(
            tool_name="sample_tool",
            input_path=input_path,
            output_path=input_path,
            transform=_echo_transform,
        )

    alias = input_path.parent / f"./{input_path.name}"
    with pytest.raises(ValueError, match="output_path must differ from input_path"):
        artifact_io.run_transform(
            tool_name="sample_tool",
            input_path=input_path,
            output_path=alias,
            transform=_echo_transform,
        )

    assert input_path.read_text(encoding="utf-8") == original


def test_existing_output_file_is_rejected_not_overwritten(
    artifact_io: ModuleType, tmp_path: Path
) -> None:
    input_path = _write_input(tmp_path / "input.json", {"records": []})
    output_path = tmp_path / "envelope.json"
    output_path.write_text("existing evidence\n", encoding="utf-8")

    with pytest.raises(artifact_io.OutputConflictError, match="output_path already exists"):
        artifact_io.run_transform(
            tool_name="sample_tool",
            input_path=input_path,
            output_path=output_path,
            transform=_echo_transform,
        )

    assert output_path.read_text(encoding="utf-8") == "existing evidence\n"


def test_write_json_creates_output_exclusively(
    artifact_io: ModuleType, tmp_path: Path
) -> None:
    existing = tmp_path / "envelope.json"
    existing.write_text("existing evidence\n", encoding="utf-8")

    with pytest.raises(FileExistsError):
        artifact_io._write_json(existing, {"schema_version": "1.0"})

    assert existing.read_text(encoding="utf-8") == "existing evidence\n"


def test_output_created_after_precheck_is_never_overwritten(
    artifact_io: ModuleType, tmp_path: Path
) -> None:
    input_path = _write_input(tmp_path / "input.json", {"records": []})
    output_path = tmp_path / "envelope.json"

    def racing_transform(payload: dict[str, Any]) -> object:
        # Simulates another writer creating the output between the friendly
        # exists() pre-check and the envelope write.
        output_path.write_text("raced evidence\n", encoding="utf-8")
        return {"records": []}

    with pytest.raises(FileExistsError):
        artifact_io.run_transform(
            tool_name="sample_tool",
            input_path=input_path,
            output_path=output_path,
            transform=racing_transform,
        )

    assert output_path.read_text(encoding="utf-8") == "raced evidence\n"


def test_second_run_to_same_output_fails_and_keeps_first_envelope(
    artifact_io: ModuleType, tmp_path: Path
) -> None:
    input_path = _write_input(tmp_path / "input.json", {"records": []})
    output_path = tmp_path / "envelope.json"

    artifact_io.run_transform(
        tool_name="sample_tool",
        input_path=input_path,
        output_path=output_path,
        transform=_echo_transform,
    )
    first_bytes = output_path.read_bytes()

    with pytest.raises(artifact_io.OutputConflictError, match="output_path already exists"):
        artifact_io.run_transform(
            tool_name="sample_tool",
            input_path=input_path,
            output_path=output_path,
            transform=_echo_transform,
        )

    assert output_path.read_bytes() == first_bytes


@pytest.mark.parametrize("text", ["{not json", "[1, 2]", '"a string"'])
def test_unparseable_or_non_object_input_writes_failure_envelope(
    artifact_io: ModuleType, tmp_path: Path, text: str
) -> None:
    input_path = tmp_path / "input.json"
    input_path.write_text(text, encoding="utf-8")
    output_path = tmp_path / "envelope.json"

    with pytest.raises(ValueError):
        artifact_io.run_transform(
            tool_name="sample_tool",
            input_path=input_path,
            output_path=output_path,
            transform=_echo_transform,
        )

    envelope = _read_envelope(output_path)
    assert envelope["schema_version"] == "1.0"
    assert envelope["tool"] == "sample_tool"
    assert envelope["status"] == "failed"
    assert envelope["input_sha256"] == _sha256_file(input_path)
    assert envelope["error_type"]
    assert envelope["error_message"]
    assert envelope["source"] is None
    assert envelope["source_provided"] is False
    assert "result" not in envelope


def test_transform_error_writes_failure_envelope(
    artifact_io: ModuleType, tmp_path: Path
) -> None:
    source = {"provider": "sample", "record_id": "batch-1"}
    input_path = _write_input(
        tmp_path / "input.json", {"source": source, "records": []}
    )
    output_path = tmp_path / "envelope.json"

    with pytest.raises(RuntimeError, match="transform rejected the payload"):
        artifact_io.run_transform(
            tool_name="sample_tool",
            input_path=input_path,
            output_path=output_path,
            transform=_failing_transform,
        )

    envelope = _read_envelope(output_path)
    assert envelope["schema_version"] == "1.0"
    assert envelope["tool"] == "sample_tool"
    assert envelope["status"] == "failed"
    assert envelope["input_sha256"] == _sha256_file(input_path)
    assert envelope["error_type"] == "RuntimeError"
    assert envelope["error_message"] == "transform rejected the payload"
    assert envelope["source"] == source
    assert envelope["source_provided"] is True
    assert "result" not in envelope


def test_success_envelope_passes_source_through_and_reports_source_provided(
    artifact_io: ModuleType, tmp_path: Path
) -> None:
    source = {"provider": "sample", "record_id": "batch-1"}
    input_path = _write_input(
        tmp_path / "input.json", {"source": source, "records": [{"value": 1}]}
    )
    output_path = tmp_path / "envelope.json"

    envelope = artifact_io.run_transform(
        tool_name="sample_tool",
        input_path=input_path,
        output_path=output_path,
        transform=_echo_transform,
    )

    assert envelope["source"] == source
    assert envelope["source_provided"] is True
    assert _read_envelope(output_path)["source_provided"] is True


def test_success_envelope_without_source_reports_source_provided_false(
    artifact_io: ModuleType, tmp_path: Path
) -> None:
    input_path = _write_input(tmp_path / "input.json", {"records": []})
    output_path = tmp_path / "envelope.json"

    envelope = artifact_io.run_transform(
        tool_name="sample_tool",
        input_path=input_path,
        output_path=output_path,
        transform=_echo_transform,
    )

    assert envelope["source"] is None
    assert envelope["source_provided"] is False
    assert _read_envelope(output_path)["source_provided"] is False


def test_non_object_source_is_rejected_with_failure_envelope(
    artifact_io: ModuleType, tmp_path: Path
) -> None:
    input_path = _write_input(
        tmp_path / "input.json", {"source": "bad", "records": []}
    )
    output_path = tmp_path / "envelope.json"

    with pytest.raises(ValueError, match="source must be an object"):
        artifact_io.run_transform(
            tool_name="sample_tool",
            input_path=input_path,
            output_path=output_path,
            transform=_echo_transform,
        )

    envelope = _read_envelope(output_path)
    assert envelope["status"] == "failed"
    assert envelope["error_type"] == "ValueError"
    assert "source must be an object" in envelope["error_message"]
    assert envelope["source"] is None
    assert envelope["source_provided"] is False
    assert "result" not in envelope


def test_scripts_directory_contains_only_approved_files() -> None:
    assert SCRIPTS_DIR.is_dir(), "canonical skill scripts directory is missing"
    present = {
        path.relative_to(SCRIPTS_DIR).as_posix()
        for path in SCRIPTS_DIR.rglob("*")
        if path.is_file()
    }
    assert present == set(APPROVED_SCRIPTS), (
        f"scripts directory must equal the stage whitelist exactly "
        f"(recursively; __pycache__ or stray files are rejected); "
        f"missing={sorted(set(APPROVED_SCRIPTS) - present)} "
        f"unapproved={sorted(present - set(APPROVED_SCRIPTS))}"
    )


def _scan_text_violations(name: str, text: str) -> list[str]:
    violations: list[str] = []
    for token in FORBIDDEN_SECURITY_TOKENS:
        if token in text:
            violations.append(f"{name}: hardcoded security {token!r}")
    for pattern in DATE_LITERAL_PATTERNS:
        for match in pattern.finditer(text):
            violations.append(f"{name}: hardcoded date literal {match.group()!r}")
    return violations


def _scan_ast_violations(name: str, text: str) -> list[str]:
    violations: list[str] = []
    for node in ast.walk(ast.parse(text)):
        if not isinstance(node, ast.Constant):
            continue
        if isinstance(node.value, str):
            if SECURITY_CODE_IN_LITERAL.search(node.value):
                violations.append(
                    f"{name}: hardcoded security code literal {node.value!r}"
                )
            is_posix_path = node.value.startswith("/") and node.value not in {"/", "//"}
            if is_posix_path or PureWindowsPath(node.value).is_absolute():
                violations.append(f"{name}: absolute path literal {node.value!r}")
        elif (
            isinstance(node.value, int)
            and not isinstance(node.value, bool)
            and 100000 <= node.value <= 999999
        ):
            violations.append(f"{name}: hardcoded integer security code {node.value!r}")
    return violations


def test_canonical_scripts_have_no_hardcoded_security_date_or_absolute_path() -> None:
    sources = sorted(SCRIPTS_DIR.glob("*.py")) if SCRIPTS_DIR.is_dir() else []
    violations: list[str] = []
    for source in sources:
        text = source.read_text(encoding="utf-8")
        violations.extend(_scan_text_violations(source.name, text))
        violations.extend(_scan_ast_violations(source.name, text))
    assert not violations, "\n".join(violations)


def test_hardcode_scan_flags_synthetic_violations() -> None:
    # Mutation negative evidence: every scanner branch must fail on a real
    # violation, otherwise the green scan above proves nothing.
    samples = {
        "token.py": 'SUBJECT = "北方华创"\n',
        "string_code.py": 'SECID = "sz300750"\n',
        "query_code.py": 'QUERY = "secid=0.603501"\n',
        "compact_date.py": 'AS_OF = "20260819"\n',
        "dashed_date.py": 'AS_OF = "2026-08-19"\n',
        "slashed_date.py": 'AS_OF = "2026/08/19"\n',
        "posix_path.py": 'ROOT = "/private/data"\n',
        "windows_path.py": 'ROOT = "E:\\\\research"\n',
        "int_code.py": "PEER = 300750\n",
    }
    for name, source in samples.items():
        violations = _scan_text_violations(name, source) + _scan_ast_violations(
            name, source
        )
        assert violations, f"{name} not flagged by the hardcode scan"
    clean = "WINDOW = 30\n"
    assert not _scan_text_violations("clean.py", clean) + _scan_ast_violations(
        "clean.py", clean
    )
    delimiters = 'ROOT = "/"\nDOUBLE_SLASH = "//"\n'
    assert not _scan_text_violations(
        "delimiters.py", delimiters
    ) + _scan_ast_violations("delimiters.py", delimiters)
