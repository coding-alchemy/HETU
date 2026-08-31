"""Deterministically extract page-delimited text from one local PDF.

CLI: ``pdf_text_extract.py --input INPUT.pdf --output OUTPUT.txt``. Exit 0
publishes a new UTF-8 text file; exit 1 covers PDF parsing or page extraction;
exit 2 covers invalid paths, unreadable input and output conflicts. The tool
does not use network access or OCR and never overwrites an existing output.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import sys
from contextlib import suppress
from pathlib import Path
from tempfile import NamedTemporaryFile

from pypdf import PdfReader


class _InputUnreadableError(OSError):
    pass


def extract_pdf_text(input_path: Path) -> str:
    try:
        input_bytes = input_path.read_bytes()
    except OSError as error:
        raise _InputUnreadableError(f"input file is unreadable: {error}") from error

    input_sha256 = hashlib.sha256(input_bytes).hexdigest()
    reader = PdfReader(io.BytesIO(input_bytes))
    page_texts: list[str] = []
    empty_pages: list[int] = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text()
        if text is None or not text.strip():
            page_texts.append("[NO_EXTRACTABLE_TEXT]")
            empty_pages.append(page_number)
        else:
            page_texts.append(text.rstrip("\r\n"))

    lines = [
        "HETU_PDF_TEXT_V1",
        f"input_sha256={input_sha256}",
        f"page_count={len(page_texts)}",
        "empty_text_pages="
        + (",".join(str(page) for page in empty_pages) if empty_pages else "none"),
    ]
    for page_number, text in enumerate(page_texts, start=1):
        lines.extend(("", f"<<<PAGE {page_number}>>>", text))
    return "\n".join(lines).rstrip("\n") + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract deterministic page-delimited text from one local PDF."
    )
    parser.add_argument("--input", required=True, help="local PDF path")
    parser.add_argument("--output", required=True, help="new UTF-8 text path")
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    output_path = Path(args.output)
    if input_path.resolve(strict=False) == output_path.resolve(strict=False):
        print("output_path must differ from input_path", file=sys.stderr)
        return 2
    if output_path.exists():
        print("output_path already exists", file=sys.stderr)
        return 2
    if not input_path.is_file():
        print(f"input file not found: {input_path}", file=sys.stderr)
        return 2

    try:
        extracted = extract_pdf_text(input_path)
    except _InputUnreadableError as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 2
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1

    temp_path: Path | None = None
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(extracted)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temp_path, output_path)
    except FileExistsError:
        print("output_path already exists", file=sys.stderr)
        return 2
    except OSError as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 2
    finally:
        if temp_path is not None:
            with suppress(FileNotFoundError):
                temp_path.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
