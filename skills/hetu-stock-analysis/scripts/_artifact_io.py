from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

Transform = Callable[[dict[str, Any]], object]


class InputUnreadableError(OSError):
    """The input file cannot be read at all (missing, permission, I/O)."""


class OutputConflictError(FileExistsError):
    """The output path got created concurrently; nothing was overwritten."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json_object(path: Path) -> dict[str, Any]:
    return _decode_json_object(path.read_bytes())


def _decode_json_object(data: bytes) -> dict[str, Any]:
    payload = json.loads(data.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("input JSON must be an object")
    return payload


def _write_json(path: Path, envelope: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (
        json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    # Exclusive creation (O_EXCL): the envelope can only ever be created once,
    # so a racing writer between the friendly pre-check and this call can never
    # be overwritten, and vice versa.
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(data)
    except FileExistsError as error:
        raise OutputConflictError("output_path already exists") from error


def run_transform(
    *, tool_name: str, input_path: Path, output_path: Path, transform: Transform
) -> dict[str, object]:
    if input_path.resolve() == output_path.resolve():
        raise ValueError("output_path must differ from input_path")
    if output_path.exists():
        # Typed so the CLI maps the race between the friendly pre-check and
        # this check onto exit code 2, never onto the failure-envelope path.
        raise OutputConflictError("output_path already exists")
    try:
        input_bytes = input_path.read_bytes()
    except OSError as error:
        raise InputUnreadableError(f"input file is unreadable: {error}") from error
    input_sha256 = hashlib.sha256(input_bytes).hexdigest()
    try:
        payload = _decode_json_object(input_bytes)
    except Exception as error:
        _write_json(
            output_path,
            _failed_envelope(tool_name, input_sha256, error, None),
        )
        raise
    source: dict[str, object] | None = None
    try:
        if "source" in payload:
            if not isinstance(payload["source"], dict):
                raise ValueError("source must be an object")
            source = payload["source"]
        result = transform(payload)
    except Exception as error:
        _write_json(
            output_path,
            _failed_envelope(tool_name, input_sha256, error, source),
        )
        raise
    envelope: dict[str, object] = {
        "schema_version": "1.0",
        "tool": tool_name,
        "input_sha256": input_sha256,
        "status": "success",
        "source": source,
        "source_provided": source is not None,
        "result": result,
    }
    _write_json(output_path, envelope)
    return envelope


def _failed_envelope(
    tool_name: str,
    input_sha256: str,
    error: BaseException,
    source: dict[str, object] | None,
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "tool": tool_name,
        "input_sha256": input_sha256,
        "status": "failed",
        "error_type": type(error).__name__,
        "error_message": str(error),
        "source": source,
        "source_provided": source is not None,
    }
