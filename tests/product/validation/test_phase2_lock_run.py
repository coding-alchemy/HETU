"""Behavior tests for the repo-level phase-2 run locking helper.

The helper copies the final delivery message into a fresh lock directory
and records request/research-tree/report/message/environment/visible-file
hashes. It never judges PASS/INVALID/FAIL and never adopts sources; its
output must be directly consumable by the skill artifact checker.
"""

from __future__ import annotations

import json
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from tests.product.skill.phase2_run_fixture import (
    build_valid_phase2_run,
    research_tree_sha256,
    sha256_file,
)

SCRIPT_PATH = (
    Path(__file__).resolve().parents[3] / "scripts" / "phase2_lock_run.py"
)


def load_lock_module() -> object:
    spec = spec_from_file_location("phase2_lock_run", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    sys.modules["phase2_lock_run"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def lock_module() -> object:
    return load_lock_module()


def _lock_arguments(
    tmp_path: Path, run_id: str = "synthetic-run-0001"
) -> dict[str, object]:
    research, delivery, _ = build_valid_phase2_run(tmp_path / "run")
    return {
        "run_id": run_id,
        "request_path": tmp_path / "run" / "request.md",
        "research_root": research,
        "delivery_message_path": delivery,
        "environment_path": tmp_path / "run" / "environment.json",
        "visible_before_path": tmp_path / "run" / "visible-before.txt",
        "visible_after_path": tmp_path / "run" / "visible-after.txt",
        "batch_root": tmp_path / "batch",
        "runtime_skill_id": "hetu-stock-analysis",
        "runtime_skill_sha256": "a" * 64,
        "model_id": "合成模型标识（fixture）",
    }


def test_lock_run_copies_message_and_records_hashes(
    lock_module: object, tmp_path: Path
) -> None:
    arguments = _lock_arguments(tmp_path)

    lock_record_path = lock_module.lock_run(**arguments)

    lock_dir = tmp_path / "batch" / "locks" / "synthetic-run-0001"
    assert lock_record_path == lock_dir / "lock-record.json"
    copied = lock_dir / "delivery-message.md"
    delivery = arguments["delivery_message_path"]
    assert isinstance(delivery, Path)
    assert copied.read_text(encoding="utf-8") == delivery.read_text(
        encoding="utf-8"
    )

    record = json.loads(lock_record_path.read_text(encoding="utf-8"))
    assert record["schema_version"] == "1.0"
    assert record["run_id"] == "synthetic-run-0001"
    request = arguments["request_path"]
    research = arguments["research_root"]
    environment = arguments["environment_path"]
    visible_before = arguments["visible_before_path"]
    visible_after = arguments["visible_after_path"]
    for value in (request, research, environment, visible_before, visible_after):
        assert isinstance(value, Path)
    assert record["request"]["sha256"] == sha256_file(request)
    assert record["research_root"]["tree_sha256"] == research_tree_sha256(
        research
    )
    assert record["report"]["sha256"] == sha256_file(research / "report.md")
    assert record["delivery_message"]["sha256"] == sha256_file(copied)
    assert record["environment"]["sha256"] == sha256_file(environment)
    assert record["visible_before"]["sha256"] == sha256_file(visible_before)
    assert record["visible_after"]["sha256"] == sha256_file(visible_after)
    assert record["runtime_skill"] == {
        "id": "hetu-stock-analysis",
        "sha256": "a" * 64,
    }
    assert record["model_id"] == "合成模型标识（fixture）"
    assert record["locked_at"]
    assert record["locked_by"]


def test_tree_hash_is_stable_across_writes_and_orders(
    lock_module: object, tmp_path: Path
) -> None:
    first_root = tmp_path / "a"
    second_root = tmp_path / "b"
    for root in (first_root, second_root):
        root.mkdir()
        (root / "z.md").write_text("z", encoding="utf-8")
        (root / "m.md").write_text("m", encoding="utf-8")
        (root / "nested").mkdir()
        (root / "nested" / "y.md").write_text("y", encoding="utf-8")

    assert lock_module.research_tree_sha256(
        first_root
    ) == lock_module.research_tree_sha256(second_root)


@pytest.mark.parametrize(
    "bad_id", ("../escape", "a/b", "a\\b", "run\nid", "run\x00id", "", ".")
)
def test_unsafe_run_ids_are_rejected(
    lock_module: object, tmp_path: Path, bad_id: str
) -> None:
    arguments = _lock_arguments(tmp_path, run_id=bad_id)

    with pytest.raises(ValueError):
        lock_module.lock_run(**arguments)


def test_existing_lock_refuses_overwrite(
    lock_module: object, tmp_path: Path
) -> None:
    arguments = _lock_arguments(tmp_path)
    lock_module.lock_run(**arguments)
    lock_dir = tmp_path / "batch" / "locks" / "synthetic-run-0001"
    sentinel = lock_dir / "delivery-message.md"
    sentinel_before = sentinel.read_bytes()

    with pytest.raises(OSError):
        lock_module.lock_run(**arguments)
    assert sentinel.read_bytes() == sentinel_before


def test_symlink_in_research_tree_is_rejected(
    lock_module: object, tmp_path: Path
) -> None:
    arguments = _lock_arguments(tmp_path)
    research = arguments["research_root"]
    real = tmp_path / "real-file.md"
    real.write_text("x", encoding="utf-8")
    (research / "linked.md").symlink_to(real)

    with pytest.raises(OSError):
        lock_module.lock_run(**arguments)
    assert not (tmp_path / "batch/locks/synthetic-run-0001").exists()


def test_symlinked_locks_directory_cannot_redirect_lock_output(
    lock_module: object, tmp_path: Path
) -> None:
    arguments = _lock_arguments(tmp_path)
    batch_root = arguments["batch_root"]
    assert isinstance(batch_root, Path)
    batch_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (batch_root / "locks").symlink_to(outside, target_is_directory=True)

    with pytest.raises(OSError):
        lock_module.lock_run(**arguments)
    assert list(outside.iterdir()) == []


def test_symlinked_batch_root_parent_cannot_redirect_lock_output(
    lock_module: object, tmp_path: Path
) -> None:
    arguments = _lock_arguments(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    redirected_parent = tmp_path / "redirected-parent"
    redirected_parent.symlink_to(outside, target_is_directory=True)
    arguments["batch_root"] = redirected_parent / "batch"

    with pytest.raises(OSError):
        lock_module.lock_run(**arguments)
    assert list(outside.iterdir()) == []


def test_batch_root_parent_traversal_cannot_bypass_symlink_check(
    lock_module: object, tmp_path: Path
) -> None:
    arguments = _lock_arguments(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    redirected_parent = tmp_path / "redirected-parent"
    redirected_parent.symlink_to(outside, target_is_directory=True)
    arguments["batch_root"] = (
        tmp_path / "missing" / ".." / "redirected-parent" / "batch"
    )

    with pytest.raises((OSError, ValueError)):
        lock_module.lock_run(**arguments)
    assert list(outside.iterdir()) == []


def test_runtime_skill_hash_is_validated_before_lock_directory_creation(
    lock_module: object, tmp_path: Path
) -> None:
    arguments = _lock_arguments(tmp_path)
    arguments["runtime_skill_sha256"] = "not-a-sha256"

    with pytest.raises(ValueError):
        lock_module.lock_run(**arguments)
    assert not (tmp_path / "batch/locks/synthetic-run-0001").exists()


def test_source_change_after_lock_is_detectable_by_checker(
    lock_module: object, tmp_path: Path
) -> None:
    arguments = _lock_arguments(tmp_path)
    lock_record_path = lock_module.lock_run(**arguments)

    (arguments["research_root"] / "checkpoint.md").write_text(
        "锁定后被修改\n", encoding="utf-8"
    )
    from tests.product.skill.deterministic_tool_loader import load_script

    checker = load_script("check-run-artifacts.py")
    result = checker.check_run(
        arguments["research_root"],
        lock_record_path.parent / "delivery-message.md",
        lock_record_path,
    )
    codes = {issue["code"] for issue in result["issues"]}
    assert "lock.research_hash_mismatch" in codes
    assert result["mechanical_status"] == "FAIL"


def test_message_change_during_lock_is_rejected_without_publishing(
    lock_module: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments = _lock_arguments(tmp_path)
    delivery = arguments["delivery_message_path"]
    assert isinstance(delivery, Path)
    original_copyfile = lock_module.shutil.copyfile

    def mutate_then_copy(source: Path, destination: Path) -> Path:
        delivery.write_text("锁定期间变化的消息\n", encoding="utf-8")
        return original_copyfile(source, destination)

    monkeypatch.setattr(lock_module.shutil, "copyfile", mutate_then_copy)

    with pytest.raises(OSError):
        lock_module.lock_run(**arguments)

    lock_dir = tmp_path / "batch/locks/synthetic-run-0001"
    assert not lock_dir.exists()


def test_record_write_failure_leaves_no_final_lock_directory(
    lock_module: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments = _lock_arguments(tmp_path)
    original_write_text = lock_module.Path.write_text

    def fail_lock_record(path: Path, data: str, **kwargs: object) -> int:
        if path.name == "lock-record.json":
            raise OSError("synthetic record write failure")
        return original_write_text(path, data, **kwargs)

    monkeypatch.setattr(lock_module.Path, "write_text", fail_lock_record)

    with pytest.raises(OSError):
        lock_module.lock_run(**arguments)

    lock_dir = tmp_path / "batch/locks/synthetic-run-0001"
    assert not lock_dir.exists()
    assert not list((tmp_path / "batch/locks").glob(".lock-staging-*"))
    monkeypatch.undo()
    assert lock_module.lock_run(**arguments) == lock_dir / "lock-record.json"


def test_publish_race_with_empty_target_never_replaces_target(
    lock_module: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments = _lock_arguments(tmp_path)
    lock_dir = tmp_path / "batch/locks/synthetic-run-0001"
    original_mkdir = lock_module.Path.mkdir
    original_rename = lock_module.Path.rename

    def race_mkdir(path: Path, *args: object, **kwargs: object) -> None:
        if path == lock_dir and not path.exists():
            original_mkdir(path)
        original_mkdir(path, *args, **kwargs)

    def race_rename(source: Path, target: Path) -> Path:
        if target == lock_dir and not target.exists():
            original_mkdir(target)
        return original_rename(source, target)

    monkeypatch.setattr(lock_module.Path, "mkdir", race_mkdir)
    monkeypatch.setattr(lock_module.Path, "rename", race_rename)

    with pytest.raises(OSError):
        lock_module.lock_run(**arguments)

    assert lock_dir.is_dir()
    assert list(lock_dir.iterdir()) == []


def test_publish_failure_does_not_delete_a_racing_record(
    lock_module: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments = _lock_arguments(tmp_path)
    lock_dir = tmp_path / "batch/locks/synthetic-run-0001"
    original_link = lock_module.os.link

    def race_record(source: Path, target: Path) -> None:
        if target.name == "lock-record.json":
            target.write_text("racing sentinel\n", encoding="utf-8")
        original_link(source, target)

    monkeypatch.setattr(lock_module.os, "link", race_record)

    with pytest.raises(OSError):
        lock_module.lock_run(**arguments)

    assert (lock_dir / "lock-record.json").read_text(encoding="utf-8") == "racing sentinel\n"


def test_lock_output_carries_no_verdict_or_adoption_fields(
    lock_module: object, tmp_path: Path
) -> None:
    arguments = _lock_arguments(tmp_path)
    lock_record_path = lock_module.lock_run(**arguments)

    text = lock_record_path.read_text(encoding="utf-8")
    for forbidden in ("PASS", "INVALID", "FAIL", "采纳", "采用状态"):
        assert forbidden not in text
