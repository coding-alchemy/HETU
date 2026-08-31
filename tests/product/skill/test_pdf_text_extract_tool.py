"""Deterministic and refusal-boundary tests for the canonical PDF text tool."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from types import ModuleType

import pytest

from tests.product.skill.deterministic_tool_loader import load_script

SCRIPT_PATH = Path("skills/hetu-stock-analysis/scripts/pdf_text_extract.py")


def _load_pdf_tool() -> ModuleType:
    assert SCRIPT_PATH.is_file(), "canonical pdf_text_extract.py is missing"
    return load_script(SCRIPT_PATH.name)


class _Page:
    def __init__(self, text: str | None = None, error: Exception | None = None) -> None:
        self._text = text
        self._error = error

    def extract_text(self) -> str | None:
        if self._error is not None:
            raise self._error
        return self._text


def _reader_with(*pages: _Page) -> type[object]:
    class Reader:
        def __init__(self, stream: object) -> None:
            assert hasattr(stream, "read")
            self.pages = list(pages)

    return Reader


def test_extract_pdf_text_has_stable_header_pages_and_empty_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = _load_pdf_tool()
    input_path = tmp_path / "input.pdf"
    input_bytes = b"%PDF-synthetic-input"
    input_path.write_bytes(input_bytes)
    monkeypatch.setattr(pdf, "PdfReader", _reader_with(_Page("第一页文本"), _Page(None)))

    extracted = pdf.extract_pdf_text(input_path)

    expected = (
        "HETU_PDF_TEXT_V1\n"
        f"input_sha256={hashlib.sha256(input_bytes).hexdigest()}\n"
        "page_count=2\n"
        "empty_text_pages=2\n"
        "\n"
        "<<<PAGE 1>>>\n"
        "第一页文本\n"
        "\n"
        "<<<PAGE 2>>>\n"
        "[NO_EXTRACTABLE_TEXT]\n"
    )
    assert extracted == expected
    assert pdf.extract_pdf_text(input_path).encode("utf-8") == expected.encode("utf-8")


def test_extract_pdf_text_reads_input_bytes_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = _load_pdf_tool()
    input_path = tmp_path / "input.pdf"
    input_path.write_bytes(b"%PDF-one-read")
    monkeypatch.setattr(pdf, "PdfReader", _reader_with(_Page("text")))
    original_read_bytes = Path.read_bytes
    reads = 0

    def counted_read_bytes(path: Path) -> bytes:
        nonlocal reads
        if path == input_path:
            reads += 1
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", counted_read_bytes)

    pdf.extract_pdf_text(input_path)

    assert reads == 1


def test_page_extraction_error_fails_without_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = _load_pdf_tool()
    input_path = tmp_path / "input.pdf"
    input_path.write_bytes(b"%PDF-page-error")
    output_path = tmp_path / "output.txt"
    monkeypatch.setattr(
        pdf,
        "PdfReader",
        _reader_with(_Page(error=RuntimeError("page extraction failed"))),
    )

    code = pdf.main(["--input", str(input_path), "--output", str(output_path)])

    assert code == 1
    assert not output_path.exists()


def test_missing_input_exits_two_without_output(tmp_path: Path) -> None:
    pdf = _load_pdf_tool()
    output_path = tmp_path / "output.txt"

    code = pdf.main(
        ["--input", str(tmp_path / "missing.pdf"), "--output", str(output_path)]
    )

    assert code == 2
    assert not output_path.exists()


def test_same_input_and_output_path_is_rejected_without_overwrite(tmp_path: Path) -> None:
    pdf = _load_pdf_tool()
    input_path = tmp_path / "input.pdf"
    input_path.write_bytes(b"%PDF-preserve")
    original = input_path.read_bytes()

    code = pdf.main(["--input", str(input_path), "--output", str(input_path)])

    assert code == 2
    assert input_path.read_bytes() == original


def test_existing_output_is_rejected_without_overwrite(tmp_path: Path) -> None:
    pdf = _load_pdf_tool()
    input_path = tmp_path / "input.pdf"
    input_path.write_bytes(b"%PDF-existing-output")
    output_path = tmp_path / "output.txt"
    output_path.write_text("existing evidence\n", encoding="utf-8")

    code = pdf.main(["--input", str(input_path), "--output", str(output_path)])

    assert code == 2
    assert output_path.read_text(encoding="utf-8") == "existing evidence\n"


def test_concurrent_output_creation_is_rejected_and_temp_is_cleaned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = _load_pdf_tool()
    input_path = tmp_path / "input.pdf"
    input_path.write_bytes(b"%PDF-race")
    output_path = tmp_path / "output.txt"
    monkeypatch.setattr(pdf, "PdfReader", _reader_with(_Page("text")))
    original_link = os.link

    def racing_link(source: Path | str, destination: Path | str) -> None:
        Path(destination).write_text("raced evidence\n", encoding="utf-8")
        try:
            original_link(source, destination)
        except FileExistsError:
            raise

    monkeypatch.setattr(pdf.os, "link", racing_link)

    code = pdf.main(["--input", str(input_path), "--output", str(output_path)])

    assert code == 2
    assert output_path.read_text(encoding="utf-8") == "raced evidence\n"
    assert list(tmp_path.glob("*.tmp")) == []
