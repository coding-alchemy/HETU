"""Phase-5 stage-03 mechanical contract: task relations, attempts, reuse.

Covers the 03.1 acceptance shape: conditional ``manifest.run`` fields and
artifact-level ``provenance`` are structurally checked; old records without
the new fields stay valid; the checker never infers purpose equivalence
(two adopted attempts / multiple supporting originals are not duplicates);
cross-task copying under ``reuse_previous_task_data=false`` is an explicit
contradiction. Real attempts remain adjudicated by 01's timing/access/owner
records and independent review, not by string fields.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.product.skill.deterministic_tool_loader import load_script
from tests.product.skill.phase5_execution_fixture import (
    PARENT_TASK_ID,
    TASK_ID,
    build_phase5_execution_run,
)

CHECKER_FILENAME = "check-run-artifacts.py"


@pytest.fixture
def checker() -> Any:
    return load_script(CHECKER_FILENAME)


def _rewrite_manifest(research: Path, mutate) -> None:
    manifest_path = research / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mutate(manifest)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _schema_codes(result: dict[str, Any]) -> list[str]:
    return [
        issue["code"]
        for issue in result["issues"]
        if issue["code"] == "manifest.schema"
    ]


def test_valid_stage03_run_with_relations_attempts_and_reuse_passes(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_phase5_execution_run(tmp_path)

    result = checker.check_run(research, delivery, lock)

    assert result["mechanical_status"] == "PASS", result["issues"]
    manifest = json.loads(
        (research / "manifest.json").read_text(encoding="utf-8")
    )
    statuses = {
        (entry["source_id"], entry["path"].split("/")[-1]): entry["status"]
        for entry in manifest["artifacts"]
    }
    assert any(status == "superseded" for status in statuses.values())
    assert any(status == "failed" for status in statuses.values())
    provenance_entries = [
        entry for entry in manifest["artifacts"] if "provenance" in entry
    ]
    assert len(provenance_entries) == 1
    assert provenance_entries[0]["provenance"]["source_task_id"] == PARENT_TASK_ID


def test_old_records_without_stage03_fields_stay_valid(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_phase5_execution_run(
        tmp_path, record_run_fields=False, with_provenance=False
    )

    result = checker.check_run(research, delivery, lock)

    assert result["mechanical_status"] == "PASS", result["issues"]


def test_run_conditional_fields_remain_closed_to_unknown_keys(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_phase5_execution_run(tmp_path)
    _rewrite_manifest(
        research, lambda m: m["run"].__setitem__("task_group", "x")
    )

    result = checker.check_run(research, delivery, lock)

    assert result["mechanical_status"] == "FAIL"
    assert "manifest.schema" in _schema_codes(result)


@pytest.mark.parametrize(
    "mutation", ["empty_task_id", "numeric_parent", "string_reuse_flag"]
)
def test_run_conditional_field_types_are_enforced(
    checker: Any, tmp_path: Path, mutation: str
) -> None:
    research, delivery, lock = build_phase5_execution_run(tmp_path)

    def apply(manifest: dict[str, Any]) -> None:
        if mutation == "empty_task_id":
            manifest["run"]["task_id"] = ""
        elif mutation == "numeric_parent":
            manifest["run"]["parent_task_id"] = 7
        elif mutation == "string_reuse_flag":
            manifest["run"]["reuse_previous_task_data"] = "true"

    _rewrite_manifest(research, apply)

    result = checker.check_run(research, delivery, lock)

    assert result["mechanical_status"] == "FAIL"
    assert "manifest.schema" in _schema_codes(result)


def test_provenance_requires_the_full_five_key_object(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_phase5_execution_run(tmp_path)

    def drop_key(manifest: dict[str, Any]) -> None:
        for entry in manifest["artifacts"]:
            if "provenance" in entry:
                del entry["provenance"]["original_source"]

    _rewrite_manifest(research, drop_key)

    result = checker.check_run(research, delivery, lock)

    assert result["mechanical_status"] == "FAIL"
    assert "manifest.schema" in _schema_codes(result)


def test_provenance_timestamps_must_parse(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_phase5_execution_run(tmp_path)

    def break_time(manifest: dict[str, Any]) -> None:
        for entry in manifest["artifacts"]:
            if "provenance" in entry:
                entry["provenance"]["copied_at"] = "not-a-timestamp"

    _rewrite_manifest(research, break_time)

    result = checker.check_run(research, delivery, lock)

    assert result["mechanical_status"] == "FAIL"
    assert "manifest.schema" in _schema_codes(result)


def test_copying_under_reuse_false_is_an_explicit_contradiction(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_phase5_execution_run(
        tmp_path, reuse_previous_task_data=False
    )

    result = checker.check_run(research, delivery, lock)

    codes = [issue["code"] for issue in result["issues"]]
    assert "manifest.reuse_contradiction" in codes
    assert result["mechanical_status"] == "FAIL"


def test_attempts_and_supporting_originals_are_not_duplicate_adoption(
    checker: Any, tmp_path: Path
) -> None:
    """Two attempts of one dataset (superseded + adopted) and several
    supporting originals stay mechanically valid: the checker must not
    infer purpose equivalence or call multiple adoptions duplicates —
    same-purpose multi-adoption is adjudicated by the agent and
    independent review, never by the tool."""
    research, delivery, lock = build_phase5_execution_run(tmp_path)

    result = checker.check_run(research, delivery, lock)

    assert result["mechanical_status"] == "PASS", result["issues"]
    texts = json.dumps(result["issues"] + result["warnings"], ensure_ascii=False)
    assert "重复采用" not in texts
    assert "duplicate" not in texts


def test_failed_attempt_must_keep_its_failure_reason(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_phase5_execution_run(tmp_path)

    def drop_failure(manifest: dict[str, Any]) -> None:
        for entry in manifest["artifacts"]:
            if entry.get("status") == "failed":
                del entry["failure"]

    _rewrite_manifest(research, drop_failure)

    result = checker.check_run(research, delivery, lock)

    codes = [issue["code"] for issue in result["issues"]]
    assert "manifest.missing_failure_reason" in codes
    assert result["mechanical_status"] == "FAIL"


def test_unregistered_input_reference_does_not_close(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_phase5_execution_run(tmp_path)

    def point_nowhere(manifest: dict[str, Any]) -> None:
        for entry in manifest["artifacts"]:
            if entry["type"] == "derived":
                entry["inputs"] = [
                    {"path": "artifacts/raw/never-registered.json", "sha256": "0" * 64}
                ]

    _rewrite_manifest(research, point_nowhere)

    result = checker.check_run(research, delivery, lock)

    assert result["mechanical_status"] == "FAIL"
    assert "manifest.schema" in _schema_codes(result)


def test_report_and_checkpoint_disclose_task_relations_when_recorded(
    tmp_path: Path,
) -> None:
    research, _, _ = build_phase5_execution_run(tmp_path)

    report = (research / "report.md").read_text(encoding="utf-8")
    checkpoint = (research / "checkpoint.md").read_text(encoding="utf-8")
    assert TASK_ID in report and PARENT_TASK_ID in report
    assert "reuse_previous_task_data=true" in report
    assert TASK_ID in checkpoint and "reuse_previous_task_data=true" in checkpoint


def test_old_style_run_has_no_task_disclosure(
    tmp_path: Path,
) -> None:
    research, _, _ = build_phase5_execution_run(
        tmp_path, record_run_fields=False, with_provenance=False
    )

    assert TASK_ID not in (research / "report.md").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "bad_locator",
    [
        "/Users/somewhere/old-task/artifacts/raw/a.json",
        "/artifacts/raw/a.json",
        "../../outside.json",
        "artifacts/raw/../../escape.json",
    ],
)
def test_provenance_source_artifact_rejects_absolute_and_escaping_paths(
    checker: Any, tmp_path: Path, bad_locator: str
) -> None:
    """source_artifact is a locator INSIDE the source task: absolute paths
    and '..' escapes must fail; the check is structural only — the source
    task directory is never accessed."""
    research, delivery, lock = build_phase5_execution_run(tmp_path)

    def apply(manifest: dict[str, Any]) -> None:
        for entry in manifest["artifacts"]:
            if "provenance" in entry:
                entry["provenance"]["source_artifact"] = bad_locator

    _rewrite_manifest(research, apply)

    result = checker.check_run(research, delivery, lock)

    assert result["mechanical_status"] == "FAIL"
    assert "manifest.schema" in _schema_codes(result)


def test_provenance_source_artifact_accepts_deep_relative_locators(
    checker: Any, tmp_path: Path
) -> None:
    """A plain relative locator under the source task's artifact tree keeps
    passing (positive case; the source file need not exist locally)."""
    research, delivery, lock = build_phase5_execution_run(tmp_path)

    def apply(manifest: dict[str, Any]) -> None:
        for entry in manifest["artifacts"]:
            if "provenance" in entry:
                entry["provenance"][
                    "source_artifact"
                ] = "artifacts/raw/cninfo/annual-report--cninfo--2025-1231--0a1b2c3d.pdf"

    _rewrite_manifest(research, apply)

    result = checker.check_run(research, delivery, lock)

    assert result["mechanical_status"] == "PASS", result["issues"]
