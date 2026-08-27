"""Acceptance-time tamper-evident sealing helper for one finished phase-2 run.

``lock_run(...)`` copies the final delivery message verbatim into a fresh
``<batch_root>/locks/<run_id>/`` directory and writes a ``lock-record.json``
binding the request, research tree (frozen tree hash), report, message,
environment, visible-file snapshots, runtime Skill identity and model id.
All source inputs stay untouched.

The helper never orchestrates research, never selects references, never
reads G1/G2 answers and never judges ``INVALID``/``FAIL``/``PASS`` —
natural-language facts, scope and final-message consistency are adjudicated
by an independent reviewer after the candidate is locked.

The hashes bind this one recorded version so later mutation is detectable.
They do not compare different reports, judge semantic equivalence or score
report quality. A revision uses a new run id and a new seal; this helper does
not archive the whole research tree and therefore is not a recovery store.

Tree hash (stable contract, duplicated verbatim in the Skill checker
``skills/hetu-stock-analysis/scripts/check-run-artifacts.py``): symlinks
rejected; POSIX relative paths sorted by UTF-8 bytes; for each regular
file write relpath bytes + NUL + file SHA-256 ASCII + NUL.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

SCHEMA_VERSION = "1.0"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def research_tree_sha256(research_root: Path) -> str:
    """Frozen tree hash; identical to the Skill-side checker algorithm."""
    digest = hashlib.sha256()
    entries: list[tuple[bytes, str]] = []
    for path in research_root.rglob("*"):
        if path.is_symlink():
            raise OSError(f"symlink inside research tree: {path}")
        if path.is_file():
            relative = path.relative_to(research_root).as_posix()
            entries.append((relative.encode("utf-8"), _sha256_file(path)))
    for name_bytes, file_sha in sorted(entries):
        digest.update(name_bytes)
        digest.update(b"\x00")
        digest.update(file_sha.encode("ascii"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _require_file(path: Path, role: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise OSError(f"{role} must be a regular non-symlink file: {path}")


def _require_directory(path: Path, role: str) -> None:
    if path.is_symlink() or not path.is_dir():
        raise OSError(f"{role} must be a real directory: {path}")


def _reject_symlink_components(path: Path, role: str) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.is_symlink():
            raise OSError(f"{role} must not traverse symlinks: {current}")


def _validate_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id must be a non-empty string")
    forbidden = {"/", "\\", "\n", "\r", "\x00"}
    if any(character in forbidden for character in run_id):
        raise ValueError(f"run_id must not contain path fragments: {run_id!r}")
    if run_id in {".", ".."} or run_id.strip() != run_id:
        raise ValueError(f"unsafe run_id: {run_id!r}")
    return run_id


def _entry(path: Path) -> dict[str, str]:
    _require_file(path, "lock input")
    return {"path": str(path.resolve()), "sha256": _sha256_file(path)}


def lock_run(
    run_id: str,
    request_path: Path,
    research_root: Path,
    delivery_message_path: Path,
    environment_path: Path,
    visible_before_path: Path,
    visible_after_path: Path,
    batch_root: Path,
    runtime_skill_id: str,
    runtime_skill_sha256: str,
    model_id: str,
) -> Path:
    """Copy the final message and write the lock record; never overwrite."""
    _validate_run_id(run_id)
    if ".." in batch_root.parts:
        raise ValueError("batch_root must not contain parent traversal")
    _require_directory(research_root, "research root")
    for role, path in (
        ("request", request_path),
        ("delivery message", delivery_message_path),
        ("environment", environment_path),
        ("visible-before", visible_before_path),
        ("visible-after", visible_after_path),
        ("report", research_root / "report.md"),
    ):
        _require_file(path, role)
    if not isinstance(runtime_skill_id, str) or not runtime_skill_id.strip():
        raise ValueError("runtime_skill_id must be a non-empty string")
    if (
        not isinstance(runtime_skill_sha256, str)
        or SHA256_RE.fullmatch(runtime_skill_sha256) is None
    ):
        raise ValueError("runtime_skill_sha256 must be 64 lowercase hexadecimal characters")
    if not isinstance(model_id, str) or not model_id.strip():
        raise ValueError("model_id must be a non-empty string")

    locks_root = batch_root / "locks"
    lock_dir = locks_root / run_id
    _reject_symlink_components(lock_dir, "lock target")
    lock_record_path = lock_dir / "lock-record.json"
    if lock_dir.exists():
        raise OSError(f"lock target already exists, refusing to overwrite: {lock_dir}")

    request_entry = _entry(request_path)
    research_tree_hash = research_tree_sha256(research_root)
    report_entry = _entry(research_root / "report.md")
    message_sha = _sha256_file(delivery_message_path)
    environment_entry = _entry(environment_path)
    visible_before_entry = _entry(visible_before_path)
    visible_after_entry = _entry(visible_after_path)

    locks_root.mkdir(parents=True, exist_ok=True)
    _reject_symlink_components(lock_dir, "lock target")
    if lock_dir.exists():
        raise OSError(f"lock target already exists, refusing to overwrite: {lock_dir}")

    with TemporaryDirectory(prefix=".lock-staging-", dir=locks_root) as temporary:
        staging_dir = Path(temporary)
        staged_message = staging_dir / "delivery-message.md"
        shutil.copyfile(delivery_message_path, staged_message)
        copied_message_sha = _sha256_file(staged_message)
        if copied_message_sha != message_sha:
            raise OSError("delivery message changed while creating lock snapshot")

        record = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "request": request_entry,
            "research_root": {
                "path": str(research_root.resolve()),
                "tree_sha256": research_tree_hash,
            },
            "report": report_entry,
            "delivery_message": {
                "path": str((lock_dir / "delivery-message.md").absolute()),
                "sha256": copied_message_sha,
            },
            "runtime_skill": {
                "id": runtime_skill_id,
                "sha256": runtime_skill_sha256,
            },
            "model_id": model_id,
            "environment": environment_entry,
            "visible_before": visible_before_entry,
            "visible_after": visible_after_entry,
            "locked_at": datetime.now(UTC).astimezone().isoformat(),
            "locked_by": "scripts/phase2_lock_run.py",
        }
        (staging_dir / "lock-record.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        unchanged = (
            _sha256_file(request_path) == request_entry["sha256"]
            and research_tree_sha256(research_root) == research_tree_hash
            and _sha256_file(research_root / "report.md") == report_entry["sha256"]
            and _sha256_file(delivery_message_path) == message_sha
            and _sha256_file(environment_path) == environment_entry["sha256"]
            and _sha256_file(visible_before_path) == visible_before_entry["sha256"]
            and _sha256_file(visible_after_path) == visible_after_entry["sha256"]
        )
        if not unchanged:
            raise OSError("lock input changed while creating snapshot")
        try:
            lock_dir.mkdir()
        except FileExistsError as error:
            raise OSError(
                f"lock target already exists, refusing to overwrite: {lock_dir}"
            ) from error
        published_message = lock_dir / "delivery-message.md"
        message_published = False
        record_published = False
        try:
            os.link(staged_message, published_message)
            message_published = True
            # The record is the final commit marker. Both hard links are
            # no-replace operations, and the directory itself was reserved
            # atomically above, so a racing target is never overwritten.
            os.link(staging_dir / "lock-record.json", lock_record_path)
            record_published = True
        except OSError as error:
            if record_published:
                lock_record_path.unlink()
            if message_published:
                published_message.unlink()
            try:
                lock_dir.rmdir()
            except OSError as cleanup_error:
                raise OSError(
                    f"lock publish failed and cleanup was incomplete: {lock_dir}"
                ) from cleanup_error
            raise error
    return lock_record_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lock one finished run")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--research-root", required=True, type=Path)
    parser.add_argument("--delivery-message", required=True, type=Path)
    parser.add_argument("--environment", required=True, type=Path)
    parser.add_argument("--visible-before", required=True, type=Path)
    parser.add_argument("--visible-after", required=True, type=Path)
    parser.add_argument("--batch-root", required=True, type=Path)
    parser.add_argument("--runtime-skill-id", required=True)
    parser.add_argument("--runtime-skill-sha256", required=True)
    parser.add_argument("--model-id", required=True)
    arguments = parser.parse_args(argv)
    try:
        lock_record = lock_run(
            arguments.run_id,
            arguments.request,
            arguments.research_root,
            arguments.delivery_message,
            arguments.environment,
            arguments.visible_before,
            arguments.visible_after,
            arguments.batch_root,
            arguments.runtime_skill_id,
            arguments.runtime_skill_sha256,
            arguments.model_id,
        )
    except (OSError, ValueError) as error:
        print(f"lock failed: {error}", file=sys.stderr)
        return 2
    print(lock_record)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
