"""Mutation-matrix tests for the stateless run artifact checker.

Each mutation breaks exactly one structural guarantee of a valid synthetic
run and must surface its stable issue code; the valid fixture passes with
``mechanical_status == "PASS"`` and ``message_input_status == "locked"``.
No test judges natural-language truth — structure only.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.product.skill.deterministic_tool_loader import load_script
from tests.product.skill.phase2_run_fixture import (
    DEFAULT_RUN_ID,
    DERIVED_REL,
    NORMALIZED_REL,
    RAW_REL,
    WORK_PACKAGES,
    _conclusion_boundary_table,
    _method_selection_table,
    _model_profile_table,
    _quality_summary_table,
    _required_items_table,
    build_valid_phase2_run,
    sha256_file,
)

CHECKER_FILENAME = "check-run-artifacts.py"
SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "skills/hetu-stock-analysis/scripts"
CHECKER_PATH = SCRIPTS_DIR / CHECKER_FILENAME

MUTATIONS: dict[str, str] = {
    "missing-work-package": "missing.required_file",
    "invalid-manifest-json": "manifest.invalid_json",
    "manifest-path-traversal": "manifest.unsafe_path",
    "manifest-hash-mismatch": "manifest.hash_mismatch",
    "missing-model-field": "report.missing_model",
    "wrong-report-order": "report.chapter_order",
    "unregistered-script": "script.unregistered",
    "missing-no-script-declaration": "script.missing_declaration",
    "missing-delivery-message": "message.not_checked",
    "delivery-hash-mismatch": "lock.message_hash_mismatch",
    "research-tree-hash-mismatch": "lock.research_hash_mismatch",
    "lock-record-missing-hash-fields": "lock.research_hash_missing",
    "lock-record-missing-message-hash": "lock.message_hash_missing",
    "missing-checkpoint": "missing.required_file",
    "missing-evidence": "missing.required_file",
    "missing-report": "missing.required_file",
    "missing-manifest": "missing.required_file",
    "manifest-invalid-status": "manifest.invalid_status",
    "manifest-failed-without-reason": "manifest.missing_failure_reason",
    "manifest-script-without-metadata": "manifest.missing_script_metadata",
    "missing-home-field": "report.missing_home_field",
    "missing-core-row": "report.missing_core_row",
    "script-outside-owner-dir": "script.owner_missing",
}

MANIFEST_SCHEMA_MUTATIONS: dict[str, Callable[[dict[str, Any]], None]] = {
    "extra-top-level-key": lambda manifest: manifest.__setitem__("unexpected", True),
    "extra-run-key": lambda manifest: manifest["run"].__setitem__("unexpected", True),
    "null-run-id": lambda manifest: manifest["run"].__setitem__("run_id", None),
    "invalid-data-mode": lambda manifest: manifest["run"].__setitem__("data_mode", "private"),
    "invalid-requested-depth": lambda manifest: manifest["run"].__setitem__(
        "requested_depth", "extreme"
    ),
    "null-model": lambda manifest: manifest["run"].__setitem__("model", None),
    "empty-runtime-skill": lambda manifest: manifest["run"].__setitem__("runtime_skill", {}),
    "empty-media-format": lambda manifest: manifest["artifacts"][0].__setitem__("media_format", ""),
    "invalid-source-id": lambda manifest: manifest["artifacts"][0].__setitem__("source_id", {}),
    "invalid-period": lambda manifest: manifest["artifacts"][0].__setitem__("period_or_asof", []),
    "null-created-at": lambda manifest: manifest["artifacts"][0].__setitem__("created_at", None),
    "invalid-artifact-schema-version": lambda manifest: manifest["artifacts"][1].__setitem__(
        "schema_version", {}
    ),
    "inputs-not-array": lambda manifest: manifest["artifacts"][1].__setitem__(
        "inputs", "not-an-array"
    ),
    "input-not-object": lambda manifest: manifest["artifacts"][1].__setitem__(
        "inputs", ["not-an-object"]
    ),
    "empty-script-metadata": lambda manifest: manifest["artifacts"][3].__setitem__("script", {}),
    "invalid-script-output": lambda manifest: manifest["artifacts"][3]["script"].__setitem__(
        "output", 42
    ),
}

LOCK_REQUIRED_FIELDS = (
    "schema_version",
    "run_id",
    "request",
    "report",
    "runtime_skill",
    "model_id",
    "environment",
    "visible_before",
    "visible_after",
    "locked_at",
    "locked_by",
)

W10_INVALID_ROWS = {
    "missing-report-chapter": (
        "不存在章节",
        "不存在主张",
        "W0",
        "E1",
        "adopted",
    ),
    "missing-claim": (
        "1. 任务与时点",
        "不存在主张",
        "W0",
        "E1",
        "adopted",
    ),
    "unknown-owner": (
        "1. 任务与时点",
        "重要声明：合成内容，无研究意义。",
        "WX",
        "E1",
        "adopted",
    ),
    "missing-evidence": (
        "1. 任务与时点",
        "重要声明：合成内容，无研究意义。",
        "W0",
        "missing.md",
        "adopted",
    ),
    "invalid-adoption-status": (
        "1. 任务与时点",
        "重要声明：合成内容，无研究意义。",
        "W0",
        "E1",
        "bogus",
    ),
}


def _edit_json(path: Path, edit: Callable[[dict[str, Any]], None]) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    edit(payload)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _refresh_research_lock(checker: Any, research: Path, lock: Path) -> None:
    _edit_json(
        lock,
        lambda record: (
            record["research_root"].__setitem__(
                "tree_sha256", checker.research_tree_sha256(research)
            ),
            record["report"].__setitem__(
                "sha256", sha256_file(research / "report.md")
            ),
        ),
    )


def _assert_nonblocking_warning(result: dict[str, Any], code: str) -> None:
    assert result["mechanical_status"] == "PASS", result
    assert result["issues"] == []
    assert code in {warning["code"] for warning in result["warnings"]}, result[
        "warnings"
    ]


def _apply_mutation(name: str, root: Path) -> None:
    research = root / "research" / DEFAULT_RUN_ID
    lock = root / "locks" / DEFAULT_RUN_ID / "lock-record.json"
    if name == "missing-work-package":
        (research / "work-packages/W3-industry-competition.md").unlink()
    elif name == "invalid-manifest-json":
        (research / "manifest.json").write_text("{ not json", encoding="utf-8")
    elif name == "manifest-path-traversal":

        def edit(manifest: dict[str, Any]) -> None:
            manifest["artifacts"][0]["path"] = "../outside.json"

        _edit_json(research / "manifest.json", edit)
    elif name == "manifest-hash-mismatch":

        def edit(manifest: dict[str, Any]) -> None:
            manifest["artifacts"][0]["sha256"] = "0" * 64

        _edit_json(research / "manifest.json", edit)
    elif name == "missing-model-field":
        report = research / "report.md"
        model_row = "| 分析模型 | 合成模型标识（fixture） |\n"
        report.write_text(
            report.read_text(encoding="utf-8").replace(model_row, ""),
            encoding="utf-8",
        )
    elif name == "wrong-report-order":
        report = research / "report.md"
        text = report.read_text(encoding="utf-8")
        text = text.replace("## 3. 公司、业务与行业", "## 3. 最终边界", 1)
        text = text.replace("## 12. 最终边界", "## 12. 公司、业务与行业", 1)
        report.write_text(text, encoding="utf-8")
    elif name == "missing-w10-mapping":
        w10 = research / "work-packages/W10-report-review.md"
        w10.write_text("# W10（合成，缺映射表）\n", encoding="utf-8")
    elif name == "unregistered-script":
        stray = research / "artifacts/scripts/W5/stray.py"
        stray.write_text("# 未登记脚本\n", encoding="utf-8")
    elif name == "missing-no-script-declaration":
        w7 = research / "work-packages/W7-valuation-expectations.md"
        w7.write_text(
            w7.read_text(encoding="utf-8").replace("本工作包未创建或修改中间脚本。", ""),
            encoding="utf-8",
        )
    elif name == "missing-delivery-message":
        (root / "delivery-message.md").unlink()
    elif name == "delivery-hash-mismatch":
        (root / "delivery-message.md").write_text("被篡改的最终消息\n", encoding="utf-8")
    elif name == "research-tree-hash-mismatch":
        (research / "checkpoint.md").write_text("锁定后被修改的检查点\n", encoding="utf-8")
    elif name == "lock-record-missing-hash-fields":
        _edit_json(lock, lambda record: record["research_root"].pop("tree_sha256"))
    elif name == "lock-record-missing-message-hash":
        _edit_json(lock, lambda record: record["delivery_message"].pop("sha256"))
    elif name == "missing-checkpoint":
        (research / "checkpoint.md").unlink()
    elif name == "missing-evidence":
        (research / "evidence.md").unlink()
    elif name == "missing-report":
        (research / "report.md").unlink()
    elif name == "missing-manifest":
        (research / "manifest.json").unlink()
    elif name == "manifest-invalid-status":

        def edit(manifest: dict[str, Any]) -> None:
            manifest["artifacts"][0]["status"] = "bogus"

        _edit_json(research / "manifest.json", edit)
    elif name == "manifest-failed-without-reason":

        def edit(manifest: dict[str, Any]) -> None:
            manifest["artifacts"][0]["status"] = "failed"

        _edit_json(research / "manifest.json", edit)
    elif name == "manifest-script-without-metadata":

        def edit(manifest: dict[str, Any]) -> None:
            for entry in manifest["artifacts"]:
                if entry.get("type") == "script":
                    entry.pop("script")

        _edit_json(research / "manifest.json", edit)
    elif name == "missing-home-field":
        report = research / "report.md"
        report.write_text(
            report.read_text(encoding="utf-8").replace(
                "| as_of | 2026-06-30T18:00:00+08:00 |\n", ""
            ),
            encoding="utf-8",
        )
    elif name == "missing-core-row":
        report = research / "report.md"
        report.write_text(
            report.read_text(encoding="utf-8").replace(
                "| 最强反证 | 有 | 合成内容 | 合成证据 | 合成定位 |\n", ""
            ),
            encoding="utf-8",
        )
    elif name == "script-outside-owner-dir":
        loose = research / "artifacts/scripts/loose.py"
        loose.write_text("# 无属主脚本\n", encoding="utf-8")
    else:  # pragma: no cover - exhaustive dispatch
        raise AssertionError(f"unknown mutation {name}")


@pytest.fixture
def checker() -> Any:
    return load_script(CHECKER_FILENAME)


def test_valid_run_passes_mechanically(checker: Any, tmp_path: Path) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)

    result = checker.check_run(research, delivery, lock)

    assert result["schema_version"] == "1.0"
    assert result["mechanical_status"] == "PASS"
    assert result["message_input_status"] == "locked"
    assert result["issues"] == []
    assert result["warnings"] == []
    assert isinstance(result["checks"], list) and result["checks"]


@pytest.mark.parametrize("depth", ("quick", "standard", "deep"))
def test_canonical_run_directory_accepts_all_depths(
    checker: Any, tmp_path: Path, depth: str
) -> None:
    run_id = f"合成公司-000001.SZ-{depth}-20260630T190000+0800"
    research, delivery, lock = build_valid_phase2_run(
        tmp_path, run_id=run_id, requested_depth=depth
    )
    result = checker.check_run(research, delivery, lock)
    assert result["mechanical_status"] == "PASS"
    assert result["warnings"] == []


def test_noncanonical_but_consistent_run_directory_warns(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(
        tmp_path, run_id="000001.SZ-20260630T190000+0800"
    )
    result = checker.check_run(research, delivery, lock)
    _assert_nonblocking_warning(result, "run.directory_name_noncanonical")


def test_research_and_lock_directory_names_must_match_run_id(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    wrong_lock = lock.parent.parent / "wrong-run-id" / "lock-record.json"
    wrong_lock.parent.mkdir(parents=True)
    lock = lock.rename(wrong_lock)
    result = checker.check_run(research, delivery, lock)
    assert result["mechanical_status"] == "FAIL"
    assert "run.directory_run_id_mismatch" in {
        issue["code"] for issue in result["issues"]
    }


def test_verified_identity_metadata_drift_is_a_nonblocking_warning(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    _edit_json(
        research / "manifest.json",
        lambda manifest: manifest["run"].__setitem__("verified_security", "待核验"),
    )
    _refresh_research_lock(checker, research, lock)
    result = checker.check_run(research, delivery, lock)
    _assert_nonblocking_warning(result, "identity.metadata_out_of_sync")


def test_markdown_bullet_w1_identity_is_actually_mirror_checked(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    w1 = research / "work-packages/W1-subject-verification.md"
    text = w1.read_text(encoding="utf-8")
    text = text.replace("证券简称：合成公司", "- 证券简称：**合成公司**")
    text = text.replace(
        "权威身份：000001.SZ / 合成发行人 / 合成交易所（唯一核验）",
        "- 权威身份：**000001.SZ／合成发行人／合成交易所**（唯一核验）",
    )
    w1.write_text(text, encoding="utf-8")
    _edit_json(
        research / "manifest.json",
        lambda manifest: manifest["run"].__setitem__("verified_security", "待核验"),
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    _assert_nonblocking_warning(result, "identity.metadata_out_of_sync")


def test_realistic_bold_bullet_w1_identity_without_unique_suffix_is_parsed(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    w1 = research / "work-packages/W1-subject-verification.md"
    text = w1.read_text(encoding="utf-8")
    text = text.replace(
        "证券简称：合成公司",
        "- 证券简称：**合成公司**（交易所公开简称）[S1，L0]",
    )
    text = text.replace(
        "权威身份：000001.SZ / 合成发行人 / 合成交易所（唯一核验）",
        "- 权威身份：**000001.SZ／合成发行人／合成交易所** [S1，L0]",
    )
    w1.write_text(text, encoding="utf-8")
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    warning_codes = {warning["code"] for warning in result["warnings"]}
    assert "identity.metadata_unreadable" not in warning_codes
    assert "run.directory_name_noncanonical" not in warning_codes


def test_canonical_directory_accepts_security_inside_full_identity_string(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    _edit_json(
        research / "manifest.json",
        lambda manifest: manifest["run"].__setitem__(
            "verified_security", "000001.SZ / 合成发行人 / 合成交易所主板"
        ),
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert "run.directory_name_noncanonical" not in {
        warning["code"] for warning in result["warnings"]
    }


def test_existing_w1_with_unreadable_identity_warns_instead_of_skipping(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    w1 = research / "work-packages/W1-subject-verification.md"
    w1.write_text(
        w1.read_text(encoding="utf-8").replace("权威身份：", "身份说明："),
        encoding="utf-8",
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    _assert_nonblocking_warning(result, "identity.metadata_unreadable")


def _default_w10_quality_summary() -> str:
    return _quality_summary_table(
        "standard", "standard", "完整", "有效", "合成经营模型（fixture）"
    )


def test_missing_w10_mapping_is_a_nonblocking_warning(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    (research / "work-packages/W10-report-review.md").write_text(
        "# W10（合成，缺映射表）\n\n"
        + _default_w10_quality_summary()
        + "\n本工作包未创建或修改中间脚本。\n",
        encoding="utf-8",
    )
    _refresh_research_lock(checker, research, lock)
    result = checker.check_run(research, delivery, lock)
    _assert_nonblocking_warning(result, "trace.missing_w10_mapping")


def test_script_dependencies_accept_nonempty_string_list(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)

    def use_dependency_list(manifest: dict[str, Any]) -> None:
        script_entry = next(
            entry for entry in manifest["artifacts"] if entry["type"] == "script"
        )
        script_entry["script"]["dependencies"] = ["python3", "pypdf"]

    _edit_json(research / "manifest.json", use_dependency_list)
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert result["mechanical_status"] == "PASS", result
    assert result["issues"] == []
    assert result["warnings"] == []


def test_external_collection_script_may_have_no_local_input(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)

    def remove_local_input(manifest: dict[str, Any]) -> None:
        script_entry = next(
            entry for entry in manifest["artifacts"] if entry["type"] == "script"
        )
        script_entry["inputs"] = []
        script_entry["script"]["input"] = []
        script_entry["script"]["output"] = RAW_REL

    _edit_json(research / "manifest.json", remove_local_input)
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert result["mechanical_status"] == "PASS", result
    assert result["issues"] == []
    assert result["warnings"] == []


def test_report_home_table_accepts_content_header(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    report = research / "report.md"
    report.write_text(
        report.read_text(encoding="utf-8").replace(
            "| 字段 | 规则 |", "| 字段 | 内容 |", 1
        ),
        encoding="utf-8",
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert result["mechanical_status"] == "PASS", result
    assert result["issues"] == []


def test_fixed_report_tables_accept_documented_header_aliases(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    report = research / "report.md"
    report.write_text(
        report.read_text(encoding="utf-8")
        .replace("口径与单位", "口径", 1)
        .replace("结果或范围", "结果", 1),
        encoding="utf-8",
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert result["mechanical_status"] == "PASS", result
    assert result["issues"] == []


def test_checkpoint_must_be_utf8_readable(checker: Any, tmp_path: Path) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    (research / "checkpoint.md").write_bytes(b"\xff\xfe")
    _refresh_research_lock(checker, research, lock)

    with pytest.raises(ValueError):
        checker.check_run(research, delivery, lock)


@pytest.mark.parametrize("artifact_type", ("raw", "normalized", "derived"))
def test_unregistered_data_artifacts_are_rejected(
    checker: Any, tmp_path: Path, artifact_type: str
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    extra = research / "artifacts" / artifact_type / "unregistered.bin"
    extra.write_bytes(b"extra")
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert "artifact.unregistered" in {
        issue["code"] for issue in result["issues"]
    }, result["issues"]


@pytest.mark.parametrize(
    ("missing_directory", "removed_types"),
    (
        ("raw", {"raw", "normalized", "derived"}),
        ("normalized", {"normalized", "derived"}),
        ("derived", {"derived"}),
        ("scripts", {"script"}),
    ),
)
def test_four_fixed_artifact_directories_are_required_even_without_entries(
    checker: Any,
    tmp_path: Path,
    missing_directory: str,
    removed_types: set[str],
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    manifest_path = research / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    removed_paths = {
        entry["path"]
        for entry in manifest["artifacts"]
        if entry["type"] in removed_types
    }
    for relative in removed_paths:
        (research / relative).unlink()
    manifest["artifacts"] = [
        entry for entry in manifest["artifacts"] if entry["type"] not in removed_types
    ]
    for entry in manifest["artifacts"]:
        if entry["type"] == "script":
            entry["inputs"] = [
                item for item in entry["inputs"] if item["path"] not in removed_paths
            ]
            if entry["script"]["input"] in removed_paths:
                entry["script"]["input"] = "not_applicable"
            if entry["script"]["output"] in removed_paths:
                entry["script"]["output"] = "not_applicable"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    surviving_reference = manifest["artifacts"][0]["path"]
    evidence = research / "evidence.md"
    evidence.write_text(
        evidence.read_text(encoding="utf-8")
        .replace(RAW_REL, surviving_reference)
        .replace(DERIVED_REL, surviving_reference),
        encoding="utf-8",
    )
    if missing_directory == "scripts":
        w5 = research / "work-packages/W5-financial-validation.md"
        w5.write_text(
            w5.read_text(encoding="utf-8") + "\n本工作包未创建或修改中间脚本。\n",
            encoding="utf-8",
        )
    artifact_directory = research / "artifacts" / missing_directory
    for directory in sorted(
        (path for path in artifact_directory.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        directory.rmdir()
    artifact_directory.rmdir()
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert "missing.required_directory" in {
        issue["code"] for issue in result["issues"]
    }, result["issues"]


def test_every_work_package_is_required(checker: Any, tmp_path: Path) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    for name in WORK_PACKAGES:
        (research / "work-packages" / f"{name}.md").unlink()

    result = checker.check_run(research, delivery, lock)

    # Deleting files after locking also breaks the tree hash — both codes
    # are correct; every missing file must be reported individually.
    codes = {issue["code"] for issue in result["issues"]}
    assert "missing.required_file" in codes
    missing_files = [
        issue["path"] for issue in result["issues"] if issue["code"] == "missing.required_file"
    ]
    assert sorted(missing_files) == sorted(f"work-packages/{name}.md" for name in WORK_PACKAGES)
    assert result["mechanical_status"] == "FAIL"


@pytest.mark.parametrize(("mutation", "expected_code"), sorted(MUTATIONS.items()))
def test_each_mutation_surfaces_its_stable_issue_code(
    checker: Any, tmp_path: Path, mutation: str, expected_code: str
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    _apply_mutation(mutation, tmp_path)

    result = checker.check_run(research, delivery, lock)

    codes = {issue["code"] for issue in result["issues"]}
    assert expected_code in codes, f"{mutation}: issues={result['issues']}"
    assert result["mechanical_status"] == "FAIL"
    for issue in result["issues"]:
        assert set(issue) == {"code", "path", "message"}


def test_missing_message_keeps_mechanical_checks_but_not_pass(checker: Any, tmp_path: Path) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    delivery.unlink()

    result = checker.check_run(research, delivery, lock)

    assert result["message_input_status"] == "not_checked"
    assert {issue["code"] for issue in result["issues"]} == {"message.not_checked"}
    assert result["mechanical_status"] == "FAIL"
    assert isinstance(result["checks"], list) and result["checks"]


def test_symlinked_inputs_are_rejected(checker: Any, tmp_path: Path) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    real = tmp_path / "real-report.md"
    (research / "report.md").rename(real)
    (research / "report.md").symlink_to(real)

    with pytest.raises(OSError):
        checker.check_run(research, delivery, lock)


def _run_cli(
    research: Path, delivery: Path, lock: Path, output: Path
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(CHECKER_PATH),
            "--research-root",
            str(research),
            "--delivery-message",
            str(delivery),
            "--lock-record",
            str(lock),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_scripts_under_full_package_directory_count_as_owned(checker: Any, tmp_path: Path) -> None:
    # The contract canon is W0-W10 short ids, but a run that names the
    # script directory with the full package name still owns its scripts.
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    scripts_root = research / "artifacts" / "scripts"
    long_named = scripts_root / "W5-financial-validation"
    long_named.mkdir()
    script_content = "# extra script\n"
    script_hash = hashlib.sha256(script_content.encode()).hexdigest()
    extra_name = (
        "extra--W5-financial-validation--20260630T190500+0800--"
        f"{script_hash[:8]}.py"
    )
    (long_named / extra_name).write_text(script_content, encoding="utf-8")
    import json as _json

    from tests.product.skill.phase2_run_fixture import sha256_file

    manifest_path = research / "manifest.json"
    manifest = _json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = _json.loads(_json.dumps(manifest["artifacts"][3]))
    entry["path"] = f"artifacts/scripts/W5-financial-validation/{extra_name}"
    entry["sha256"] = sha256_file(long_named / extra_name)
    manifest["artifacts"].append(entry)
    manifest_path.write_text(
        _json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    import importlib.util as _ilu

    spec = _ilu.spec_from_file_location(
        "phase2_lock_run",
        Path(__file__).resolve().parents[3] / "scripts" / "phase2_lock_run.py",
    )
    module = _ilu.module_from_spec(spec)
    spec.loader.exec_module(module)
    record = module.lock_run(
        run_id=DEFAULT_RUN_ID,
        request_path=tmp_path / "request.md",
        research_root=research,
        delivery_message_path=delivery,
        environment_path=tmp_path / "environment.json",
        visible_before_path=tmp_path / "visible-before.txt",
        visible_after_path=tmp_path / "visible-after.txt",
        batch_root=tmp_path / "batch",
        runtime_skill_id="synthetic",
        runtime_skill_sha256="0" * 64,
        model_id="合成模型标识（fixture）",
    )

    result = checker.check_run(research, record.parent / "delivery-message.md", record)

    assert result["issues"] == [], result["issues"]
    assert result["mechanical_status"] == "PASS"


def _strip_table_rows(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith("|"))


def test_home_fields_and_core_rows_as_prose_are_rejected(checker: Any, tmp_path: Path) -> None:
    # Keywords scattered in prose without the fixed tables must fail: the
    # contract requires table rows, not mentions.
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    report = research / "report.md"
    text = report.read_text(encoding="utf-8")
    first_start = text.find("## 1. ")
    first_end = text.find("## 2. ")
    second_end = text.find("## 3. ")
    first = _strip_table_rows(text[first_start:first_end])
    first += "\n正文中提及 分析模型 、as_of 、推理深度 等字段名与 主体与交易状态 。\n"
    second = _strip_table_rows(text[first_end:second_end])
    second += "\n正文提及 核心业务 与 经营规模 。\n"
    report.write_text(text[:first_start] + first + second + text[second_end:], encoding="utf-8")

    result = checker.check_run(research, delivery, lock)

    codes = {issue["code"] for issue in result["issues"]}
    assert "report.missing_model" in codes
    assert "report.missing_home_field" in codes
    assert "report.missing_core_row" in codes


def test_w10_mapping_fields_in_prose_without_table_warn(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    w10 = research / "work-packages/W10-report-review.md"
    prose = _strip_table_rows(w10.read_text(encoding="utf-8"))
    prose += "\n正文提及 报告章节 、关键主张定位 、owner 工作包 、证据定位 、采用状态 。\n"
    w10.write_text(prose, encoding="utf-8")

    result = checker.check_run(research, delivery, lock)

    codes = {warning["code"] for warning in result["warnings"]}
    assert "trace.missing_w10_mapping" in codes


def test_unknown_script_owner_directory_is_rejected(checker: Any, tmp_path: Path) -> None:
    import hashlib
    import json

    research, delivery, lock = build_valid_phase2_run(tmp_path)
    bad_dir = research / "artifacts/scripts/BAD"
    bad_dir.mkdir()
    (bad_dir / "tool.py").write_text("# unknown owner\n", encoding="utf-8")
    manifest_path = research / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = json.loads(json.dumps(manifest["artifacts"][3]))
    entry["path"] = "artifacts/scripts/BAD/tool.py"
    entry["sha256"] = hashlib.sha256((bad_dir / "tool.py").read_bytes()).hexdigest()
    manifest["artifacts"].append(entry)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    result = checker.check_run(research, delivery, lock)

    codes = {issue["code"] for issue in result["issues"]}
    assert "script.unknown_owner" in codes
    assert result["mechanical_status"] == "FAIL"


def test_lock_parent_field_with_wrong_type_is_exit_two_input(checker: Any, tmp_path: Path) -> None:
    import json

    research, delivery, lock = build_valid_phase2_run(tmp_path)
    payload = json.loads(lock.read_text(encoding="utf-8"))
    payload["delivery_message"] = "not-an-object"
    lock.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError):
        checker.check_run(research, delivery, lock)


def test_empty_or_gutted_manifest_fails_schema(checker: Any, tmp_path: Path) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    (research / "manifest.json").write_text('{"artifacts": []}\n', encoding="utf-8")

    result = checker.check_run(research, delivery, lock)

    codes = {issue["code"] for issue in result["issues"]}
    assert "manifest.schema" in codes
    assert result["mechanical_status"] == "FAIL"


def test_manifest_entry_with_extra_or_missing_key_fails_schema(
    checker: Any, tmp_path: Path
) -> None:
    import json

    research, delivery, lock = build_valid_phase2_run(tmp_path)
    manifest_path = research / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"][0]["unexpected_key"] = "x"
    del manifest["artifacts"][1]["media_format"]
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    result = checker.check_run(research, delivery, lock)

    schema_issues = [issue for issue in result["issues"] if issue["code"] == "manifest.schema"]
    assert len(schema_issues) == 2
    assert result["mechanical_status"] == "FAIL"


def test_failed_script_must_record_a_nonzero_exit_status(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)

    def mark_script_failed(manifest: dict[str, Any]) -> None:
        script_entry = next(
            entry for entry in manifest["artifacts"] if entry["type"] == "script"
        )
        script_entry["status"] = "failed"
        script_entry["failure"] = "合成失败"

    _edit_json(research / "manifest.json", mark_script_failed)
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert "manifest.schema" in {
        issue["code"] for issue in result["issues"]
    }, result["issues"]


@pytest.mark.parametrize("status", ("adopted", "superseded", "not_adopted"))
def test_nonfailed_script_must_record_zero_exit_status(
    checker: Any, tmp_path: Path, status: str
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)

    def set_nonfailed_exit(manifest: dict[str, Any]) -> None:
        script_entry = next(
            entry for entry in manifest["artifacts"] if entry["type"] == "script"
        )
        script_entry["status"] = status
        script_entry["script"]["exit_status"] = 1

    _edit_json(research / "manifest.json", set_nonfailed_exit)
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert "manifest.schema" in {
        issue["code"] for issue in result["issues"]
    }, result["issues"]


def test_script_output_must_reference_a_data_artifact(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)

    def point_script_output_to_itself(manifest: dict[str, Any]) -> None:
        script_entry = next(
            entry for entry in manifest["artifacts"] if entry["type"] == "script"
        )
        script_entry["script"]["output"] = script_entry["path"]

    _edit_json(research / "manifest.json", point_script_output_to_itself)
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert "manifest.schema" in {
        issue["code"] for issue in result["issues"]
    }, result["issues"]


@pytest.mark.parametrize(
    "output_value",
    ("not_applicable", "external fetch outputs listed separately in manifest"),
    ids=(
        "test_successful_script_output_not_applicable_is_a_nonblocking_warning",
        "test_free_text_script_output_is_a_nonblocking_warning",
    ),
)
def test_successful_script_output_unresolved_is_a_nonblocking_warning(
    checker: Any, tmp_path: Path, output_value: str
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)

    def set_unresolved_output(manifest: dict[str, Any]) -> None:
        script_entry = next(
            entry for entry in manifest["artifacts"] if entry["type"] == "script"
        )
        script_entry["script"]["output"] = output_value

    _edit_json(research / "manifest.json", set_unresolved_output)
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert result["mechanical_status"] == "PASS", result
    assert result["issues"] == []
    assert "manifest.script_output_unresolved" in {
        warning["code"] for warning in result["warnings"]
    }, result["warnings"]


@pytest.mark.parametrize(
    "invalid_source",
    ("def invalid(:\n", "return 1\n"),
    ids=(
        "test_python_artifact_must_parse_as_python",
        "test_python_artifact_must_compile_as_a_module",
    ),
)
def test_python_artifact_must_parse_and_compile(
    checker: Any, tmp_path: Path, invalid_source: str
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    manifest_path = research / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    script_entry = next(
        entry for entry in manifest["artifacts"] if entry["type"] == "script"
    )
    old_path = research / script_entry["path"]
    new_sha = hashlib.sha256(invalid_source.encode()).hexdigest()
    new_relative = re.sub(
        r"--[0-9a-f]{8}\.py$", f"--{new_sha[:8]}.py", script_entry["path"]
    )
    old_path.rename(research / new_relative)
    (research / new_relative).write_text(invalid_source, encoding="utf-8")
    script_entry["path"] = new_relative
    script_entry["sha256"] = new_sha
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert "artifact.unreadable_format" in {
        issue["code"] for issue in result["issues"]
    }, result["issues"]


@pytest.mark.parametrize("mutation", sorted(MANIFEST_SCHEMA_MUTATIONS))
def test_manifest_value_schema_is_closed(checker: Any, tmp_path: Path, mutation: str) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    manifest_path = research / "manifest.json"
    _edit_json(manifest_path, MANIFEST_SCHEMA_MUTATIONS[mutation])

    result = checker.check_run(research, delivery, lock)

    codes = {issue["code"] for issue in result["issues"]}
    assert "manifest.schema" in codes, f"{mutation}: {result['issues']}"
    assert result["mechanical_status"] == "FAIL"


@pytest.mark.parametrize(
    "mutation",
    (
        "data-mode-list",
        "requested-depth-object",
        "artifact-type-list",
        "work-package-list",
        "media-format-list",
    ),
)
def test_manifest_string_enums_reject_container_values_without_exception(
    checker: Any, tmp_path: Path, mutation: str
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)

    def apply_mutation(manifest: dict[str, Any]) -> None:
        if mutation == "data-mode-list":
            manifest["run"]["data_mode"] = []
        elif mutation == "requested-depth-object":
            manifest["run"]["requested_depth"] = {}
        elif mutation == "artifact-type-list":
            manifest["artifacts"][0]["type"] = []
        elif mutation == "work-package-list":
            manifest["artifacts"][0]["work_package"] = []
        else:
            manifest["artifacts"][1]["media_format"] = []

    _edit_json(research / "manifest.json", apply_mutation)
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert "manifest.schema" in {
        issue["code"] for issue in result["issues"]
    }, result["issues"]


def test_cli_manifest_type_error_writes_failure_without_traceback(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path / "run")
    _edit_json(
        research / "manifest.json",
        lambda manifest: manifest["run"].__setitem__("data_mode", []),
    )
    _refresh_research_lock(checker, research, lock)
    output = tmp_path / "checker-output/result.json"

    completed = _run_cli(research, delivery, lock, output)

    assert completed.returncode == 1
    assert "Traceback" not in completed.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["mechanical_status"] == "FAIL"


def test_report_fixed_rows_require_real_tables_and_label_cells(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    report = research / "report.md"
    text = report.read_text(encoding="utf-8")
    text = text.replace("| --- | --- |", "字段和值如下：", 1)
    text = text.replace(
        "| 最强反证 | 有 | 合成内容 | 合成证据 | 合成定位 |",
        "| 伪装字段 | 最强反证 | 合成内容 | 合成证据 | 合成定位 |",
    )
    report.write_text(text, encoding="utf-8")

    result = checker.check_run(research, delivery, lock)

    codes = {issue["code"] for issue in result["issues"]}
    assert "report.missing_model" in codes
    assert "report.missing_home_field" in codes
    assert "report.missing_core_row" in codes


def test_gfm_tables_without_outer_pipes_are_accepted(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    w10 = research / "work-packages/W10-report-review.md"
    lines = w10.read_text(encoding="utf-8").splitlines()
    w10.write_text(
        "\n".join(
            line[1:-1] if line.startswith("|") and line.endswith("|") else line
            for line in lines
        )
        + "\n",
        encoding="utf-8",
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert result["mechanical_status"] == "PASS", result["issues"]


@pytest.mark.parametrize("chapter_locator", ("1", "第 1 章", "1–2"))
def test_w10_accepts_numbered_chapter_and_chapter_range_locators(
    checker: Any, tmp_path: Path, chapter_locator: str
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    w10 = research / "work-packages/W10-report-review.md"
    w10.write_text(
        w10.read_text(encoding="utf-8").replace(
            "| 1. 任务与时点 |", f"| {chapter_locator} |", 1
        ),
        encoding="utf-8",
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert result["mechanical_status"] == "PASS", result
    assert result["issues"] == []
    assert not any(
        warning["code"] == "trace.invalid_w10_mapping"
        for warning in result["warnings"]
    ), result["warnings"]


def test_w10_accepts_owner_ranges(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    for owner_number in range(2, 10):
        owner = research / "work-packages" / f"{WORK_PACKAGES[owner_number]}.md"
        owner.write_text(
            owner.read_text(encoding="utf-8") + "\n证据定位：E1。\n",
            encoding="utf-8",
        )
    w10 = research / "work-packages/W10-report-review.md"
    w10.write_text(
        w10.read_text(encoding="utf-8").replace("| W0 | E1 |", "| W2–W9 | E1 |", 1),
        encoding="utf-8",
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert result["mechanical_status"] == "PASS", result
    assert result["issues"] == []
    assert not any(
        warning["code"] == "trace.invalid_w10_mapping"
        for warning in result["warnings"]
    ), result["warnings"]


@pytest.mark.parametrize(
    ("old_text", "new_text"),
    (
        (
            "| 期间 | 收入 | 归母利润 | 经营现金流 | 总资产 | 归母权益 | 口径与单位 |\n"
            "| --- | --- | --- | --- | --- | --- | --- |\n"
            "| 2026Q2 | 1 | 1 | 1 | 1 | 1 | 合成单位 |",
            "期间 | 收入 | 归母利润 | 经营现金流 | 总资产 | 口径与单位\n"
            "--- | --- | --- | --- | --- | ---\n"
            "2026Q2 | 1 | 1 | 1 | 1 | 合成单位",
        ),
        (
            "| 期间 | 收入 | 归母利润 | 经营现金流 | 总资产 | 归母权益 | 口径与单位 |",
            "| 年份 | 收入 | 归母利润 | 经营现金流 | 总资产 | 归母权益 | 口径与单位 |",
        ),
    ),
    ids=(
        "test_malformed_gfm_contract_table_without_outer_pipes_is_rejected",
        "test_contract_table_cannot_evade_validation_by_renaming_first_field",
    ),
)
def test_report_contract_table_evasions_are_rejected(
    checker: Any, tmp_path: Path, old_text: str, new_text: str
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    report = research / "report.md"
    report.write_text(
        report.read_text(encoding="utf-8").replace(
            old_text,
            new_text,
            1,
        ),
        encoding="utf-8",
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert "report.missing_fixed_table" in {
        issue["code"] for issue in result["issues"]
    }, result["issues"]


@pytest.mark.parametrize("indent", ("  ", "    "))
def test_nested_list_headings_cannot_satisfy_top_level_report_chapters(
    checker: Any, tmp_path: Path, indent: str
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    report = research / "report.md"
    report.write_text(
        report.read_text(encoding="utf-8").replace(
            "## 3. 公司、业务与行业",
            f"- 列表容器\n{indent}## 3. 公司、业务与行业",
            1,
        ),
        encoding="utf-8",
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert "report.chapter_order" in {
        issue["code"] for issue in result["issues"]
    }, result["issues"]


HIDDEN_TABLE_CASES = (
    # (edits, expected issue codes, expected warning codes). Each edit is
    # (relative path, table marker, prefix, suffix) wrapping the marked table
    # block, or (relative path, None, whole-file literal, None) replacing the
    # entire file.
    (
        (
            ("report.md", "| 字段 | 规则 |", "```markdown\n", "\n```"),
            ("work-packages/W10-report-review.md", "| 报告章节 |", "```markdown\n", "\n```"),
        ),
        ("report.missing_model", "report.missing_home_field"),
        ("trace.missing_w10_mapping",),
    ),
    (
        (
            (
                "work-packages/W10-report-review.md",
                None,
                "# W10\n\n- 嵌套代码示例\n    ```markdown\n"
                "    | 报告章节 | 关键主张定位 | owner 工作包 | 证据定位 | 采用状态 |\n"
                "    | --- | --- | --- | --- | --- |\n"
                "    | 1. 任务与时点 | 重要声明：合成内容，无研究意义。 | W0 | E1 | adopted |\n"
                "    ```\n\n本工作包未创建或修改中间脚本。\n",
                None,
            ),
        ),
        (),
        ("trace.missing_w10_mapping",),
    ),
    (
        (
            ("report.md", "| 字段 | 规则 |", "<!--\n", "\n-->"),
            ("report.md", "| 期间 | 收入 |", "<!--\n", "\n-->"),
            ("work-packages/W10-report-review.md", "| 报告章节 |", "<!--\n", "\n-->"),
        ),
        (
            "report.missing_model",
            "report.missing_home_field",
            "report.missing_fixed_table",
        ),
        ("trace.missing_w10_mapping",),
    ),
)


def _checked_run_with_table_edits(
    checker: Any, tmp_path: Path, edits: tuple[tuple[str, str | None, str, str | None], ...]
) -> dict[str, Any]:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    for relative, marker, prefix, suffix in edits:
        target = research / relative
        if marker is None:
            target.write_text(prefix, encoding="utf-8")
            continue
        text = target.read_text(encoding="utf-8")
        start = text.index(marker)
        table = text[start : text.index("\n\n", start)]
        target.write_text(text.replace(table, f"{prefix}{table}{suffix}"), encoding="utf-8")
    _refresh_research_lock(checker, research, lock)
    return checker.check_run(research, delivery, lock)


@pytest.mark.parametrize(
    ("edits", "expected_issue_codes", "expected_warning_codes"),
    HIDDEN_TABLE_CASES,
    ids=(
        "test_fenced_tables_do_not_satisfy_report_or_w10_contracts",
        "test_list_nested_fenced_w10_table_does_not_satisfy_mapping_contract",
        "test_html_commented_tables_do_not_satisfy_report_or_w10_contracts",
    ),
)
def test_hidden_table_variants_do_not_satisfy_report_or_w10_contracts(
    checker: Any,
    tmp_path: Path,
    edits: tuple[tuple[str, str | None, str, str | None], ...],
    expected_issue_codes: tuple[str, ...],
    expected_warning_codes: tuple[str, ...],
) -> None:
    result = _checked_run_with_table_edits(checker, tmp_path, edits)

    assert set(expected_issue_codes) <= {
        issue["code"] for issue in result["issues"]
    }, result["issues"]
    assert set(expected_warning_codes) <= {
        warning["code"] for warning in result["warnings"]
    }, result["warnings"]


RAW_HTML_BLOCK_CASES = (
    # (relative, marker, prefix, suffix, expected issue codes, expected warning
    # codes, expect nonblocking PASS)
    ("report.md", "| 字段 | 规则 |", "<script>\n", "\n</script>", ("report.raw_html",), (), False),
    ("report.md", "| 字段 | 规则 |", "<pre>\n", "\n</pre>", ("report.raw_html",), (), False),
    (
        "work-packages/W10-report-review.md",
        "| 报告章节 |",
        "<pre>\n",
        "\n</pre>",
        (),
        ("trace.invalid_w10_mapping", "trace.missing_w10_mapping"),
        True,
    ),
    ("report.md", "| 字段 | 规则 |", "<?hidden\n", "\n?>", ("report.raw_html",), (), False),
    (
        "work-packages/W10-report-review.md",
        "| 报告章节 |",
        "<?hidden\n",
        "\n?>",
        (),
        ("trace.invalid_w10_mapping",),
        True,
    ),
)


@pytest.mark.parametrize(
    (
        "relative",
        "marker",
        "prefix",
        "suffix",
        "expected_issues",
        "expected_warnings",
        "expect_pass",
    ),
    RAW_HTML_BLOCK_CASES,
    ids=(
        "test_raw_html_block_cannot_supply_report_tables[script]",
        "test_raw_html_block_cannot_supply_report_tables[pre]",
        "test_raw_html_block_cannot_supply_w10_mapping",
        "test_processing_instruction_cannot_hide_report_table",
        "test_processing_instruction_cannot_hide_w10_mapping",
    ),
)
def test_raw_html_and_processing_instruction_variants_cannot_supply_contract_tables(
    checker: Any,
    tmp_path: Path,
    relative: str,
    marker: str,
    prefix: str,
    suffix: str,
    expected_issues: tuple[str, ...],
    expected_warnings: tuple[str, ...],
    expect_pass: bool,
) -> None:
    result = _checked_run_with_table_edits(
        checker, tmp_path, ((relative, marker, prefix, suffix),)
    )

    assert set(expected_issues) <= {
        issue["code"] for issue in result["issues"]
    }, result["issues"]
    assert set(expected_warnings) <= {
        warning["code"] for warning in result["warnings"]
    }, result["warnings"]
    if expect_pass:
        assert result["mechanical_status"] == "PASS", result
        assert result["issues"] == []
    else:
        assert result["mechanical_status"] == "FAIL", result


def test_fence_with_trailing_text_does_not_close_code_block(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    report = research / "report.md"
    report_text = report.read_text(encoding="utf-8")
    home_start = report_text.index("| 字段 | 规则 |")
    home_table = report_text[home_start : report_text.index("\n\n", home_start)]
    report.write_text(
        report_text.replace(
            home_table,
            f"```markdown\n```not-a-close\n{home_table}\n```",
        ),
        encoding="utf-8",
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    codes = {issue["code"] for issue in result["issues"]}
    assert "report.missing_model" in codes
    assert "report.missing_home_field" in codes


def test_old_home_table_header_is_rejected(checker: Any, tmp_path: Path) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    report = research / "report.md"
    report.write_text(
        report.read_text(encoding="utf-8").replace("| 字段 | 规则 |", "| 字段 | 值 |", 1),
        encoding="utf-8",
    )

    result = checker.check_run(research, delivery, lock)

    codes = {issue["code"] for issue in result["issues"]}
    assert "report.missing_model" in codes
    assert "report.missing_home_field" in codes


def test_fixed_report_sections_allow_single_simple_fact_without_table(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    report = research / "report.md"
    monitor_table = (
        "| 指标 | 口径或分母 | 方向与阈值 | 窗口 | 连续期 | 来源 | 触发动作 | 复查时间 |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| 指标 | 口径 | 阈值 | 窗口 | 一期 | 合成来源 | 复查 | 2026-07-01 |\n"
    )
    report.write_text(
        report.read_text(encoding="utf-8").replace(monitor_table, "单一简单监控事实。\n", 1),
        encoding="utf-8",
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert result["mechanical_status"] == "PASS", result["issues"]


def test_fixed_report_tables_allow_additional_columns(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    report = research / "report.md"
    report.write_text(
        report.read_text(encoding="utf-8")
        .replace(
            "| 期间 | 收入 | 归母利润 | 经营现金流 | 总资产 | 归母权益 | 口径与单位 |",
            "| 期间 | 收入 | 归母利润 | 扣非利润 | 经营现金流 | 总资产 | "
            "归母权益 | 口径与单位 | 备注 |",
            1,
        )
        .replace(
            "| --- | --- | --- | --- | --- | --- | --- |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            1,
        )
        .replace(
            "| 2026Q2 | 1 | 1 | 1 | 1 | 1 | 合成单位 |",
            "| 2026Q2 | 1 | 1 | 1 | 1 | 1 | 1 | 合成单位 | 合法补充 |",
            1,
        ),
        encoding="utf-8",
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert result["mechanical_status"] == "PASS", result["issues"]


def test_present_contract_table_requires_all_minimum_fields(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    report = research / "report.md"
    report.write_text(
        report.read_text(encoding="utf-8")
        .replace(
            "| 期间 | 收入 | 归母利润 | 经营现金流 | 总资产 | 归母权益 | 口径与单位 |",
            "| 期间 | 收入 | 归母利润 | 经营现金流 | 总资产 | 口径与单位 |",
            1,
        )
        .replace(
            "| --- | --- | --- | --- | --- | --- | --- |",
            "| --- | --- | --- | --- | --- | --- |",
            1,
        )
        .replace(
            "| 2026Q2 | 1 | 1 | 1 | 1 | 1 | 合成单位 |",
            "| 2026Q2 | 1 | 1 | 1 | 1 | 合成单位 |",
            1,
        ),
        encoding="utf-8",
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert "report.missing_fixed_table" in {
        issue["code"] for issue in result["issues"]
    }


def test_indented_code_table_does_not_satisfy_optional_table_contract(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    report = research / "report.md"
    financial_table = (
        "| 期间 | 收入 | 归母利润 | 经营现金流 | 总资产 | 归母权益 | 口径与单位 |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
        "| 2026Q2 | 1 | 1 | 1 | 1 | 1 | 合成单位 |\n"
    )
    indented = "".join(f"    {line}" for line in financial_table.splitlines(keepends=True))
    report.write_text(
        report.read_text(encoding="utf-8").replace(financial_table, indented, 1),
        encoding="utf-8",
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert "report.missing_fixed_table" in {
        issue["code"] for issue in result["issues"]
    }


def test_lock_identity_must_match_manifest_and_report(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    _edit_json(lock, lambda record: record.__setitem__("run_id", "forged-run"))
    _edit_json(lock, lambda record: record.__setitem__("model_id", "forged-model"))
    _edit_json(
        lock,
        lambda record: record["runtime_skill"].__setitem__("sha256", "1" * 64),
    )

    result = checker.check_run(research, delivery, lock)

    codes = {issue["code"] for issue in result["issues"]}
    assert "lock.run_id_mismatch" in codes
    assert "lock.model_id_mismatch" in codes
    assert "lock.runtime_skill_mismatch" in codes


@pytest.mark.parametrize(
    "field", ("request", "environment", "visible_before", "visible_after")
)
def test_lock_auxiliary_input_paths_must_identify_real_files(
    checker: Any, tmp_path: Path, field: str
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    _edit_json(
        lock,
        lambda record: record[field].__setitem__(
            "path", str(tmp_path / f"missing-{field}.txt")
        ),
    )

    result = checker.check_run(research, delivery, lock)

    assert "lock.auxiliary_path_invalid" in {
        issue["code"] for issue in result["issues"]
    }, result["issues"]


@pytest.mark.parametrize(
    "field", ("request", "environment", "visible_before", "visible_after")
)
def test_lock_auxiliary_input_hashes_are_recomputed(
    checker: Any, tmp_path: Path, field: str
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    record = json.loads(lock.read_text(encoding="utf-8"))
    Path(record[field]["path"]).write_text("锁定后被修改\n", encoding="utf-8")

    result = checker.check_run(research, delivery, lock)

    assert "lock.auxiliary_hash_mismatch" in {
        issue["code"] for issue in result["issues"]
    }, result["issues"]


def test_missing_lock_is_not_checked_but_other_mechanical_checks_run(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, _ = build_valid_phase2_run(tmp_path)

    result = checker.check_run(research, delivery, tmp_path / "absent-lock.json")

    assert result["message_input_status"] == "not_checked"
    assert result["mechanical_status"] == "FAIL"
    assert "lock.not_checked" in {issue["code"] for issue in result["issues"]}
    assert any(check["check"] == "manifest.entries" and check["ok"] for check in result["checks"])


def test_registered_json_artifact_must_be_parseable(checker: Any, tmp_path: Path) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    artifact = research / RAW_REL
    artifact.write_text("{not json", encoding="utf-8")
    manifest_path = research / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"][0]["sha256"] = sha256_file(artifact)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _edit_json(
        lock,
        lambda record: record["research_root"].__setitem__(
            "tree_sha256", checker.research_tree_sha256(research)
        ),
    )

    result = checker.check_run(research, delivery, lock)

    assert "artifact.unreadable_format" in {
        issue["code"] for issue in result["issues"]
    }


def test_manifest_artifact_names_follow_the_contract(checker: Any, tmp_path: Path) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    manifest_path = research / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    old_relative = manifest["artifacts"][0]["path"]
    new_relative = "artifacts/raw/source-a/readable-name.json"
    (research / old_relative).rename(research / new_relative)
    manifest["artifacts"][0]["path"] = new_relative
    manifest["artifacts"][1]["inputs"][0]["path"] = new_relative
    manifest["artifacts"][3]["inputs"][0]["path"] = new_relative
    manifest["artifacts"][3]["script"]["input"] = new_relative
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    evidence = research / "evidence.md"
    evidence.write_text(
        evidence.read_text(encoding="utf-8").replace(old_relative, new_relative),
        encoding="utf-8",
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert result["mechanical_status"] == "PASS", result
    assert result["issues"] == []
    assert "manifest.invalid_name" in {
        warning["code"] for warning in result["warnings"]
    }


def test_normalized_filename_period_must_match_manifest(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    wrong_rel = NORMALIZED_REL.replace("--2026-06-30--", "--2025-01-01--")
    (research / NORMALIZED_REL).rename(research / wrong_rel)
    manifest_path = research / "manifest.json"

    def edit(manifest: dict[str, Any]) -> None:
        manifest["artifacts"][1]["path"] = wrong_rel
        manifest["artifacts"][2]["inputs"][0]["path"] = wrong_rel

    _edit_json(manifest_path, edit)
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert result["mechanical_status"] == "PASS", result
    assert result["issues"] == []
    assert "manifest.invalid_name" in {
        warning["code"] for warning in result["warnings"]
    }, result["warnings"]


def test_normalized_artifact_rejects_media_format_outside_json_or_csv(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    wrong_rel = NORMALIZED_REL.removesuffix(".json") + ".md"
    (research / NORMALIZED_REL).rename(research / wrong_rel)
    manifest_path = research / "manifest.json"

    def edit(manifest: dict[str, Any]) -> None:
        manifest["artifacts"][1]["path"] = wrong_rel
        manifest["artifacts"][1]["media_format"] = "md"
        manifest["artifacts"][2]["inputs"][0]["path"] = wrong_rel

    _edit_json(manifest_path, edit)
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert "manifest.invalid_name" in {
        issue["code"] for issue in result["issues"]
    }, result["issues"]


def test_derived_multi_input_filename_uses_sorted_aggregate_hash(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    manifest_path = research / "manifest.json"

    def add_second_input(manifest: dict[str, Any]) -> None:
        manifest["artifacts"][2]["inputs"].append(
            {
                "path": RAW_REL,
                "sha256": manifest["artifacts"][0]["sha256"],
            }
        )

    _edit_json(manifest_path, add_second_input)
    _refresh_research_lock(checker, research, lock)

    wrong_result = checker.check_run(research, delivery, lock)

    assert wrong_result["mechanical_status"] == "PASS", wrong_result
    assert "manifest.invalid_name" in {
        warning["code"] for warning in wrong_result["warnings"]
    }, wrong_result["warnings"]

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    derived = manifest["artifacts"][2]
    combined = "".join(
        item["sha256"] for item in sorted(derived["inputs"], key=lambda item: item["path"])
    )
    aggregate_hash8 = hashlib.sha256(combined.encode("ascii")).hexdigest()[:8]
    correct_rel = DERIVED_REL.replace(
        DERIVED_REL.split("--")[1],
        aggregate_hash8,
        1,
    )
    (research / DERIVED_REL).rename(research / correct_rel)
    (research / "evidence.md").write_text(
        (research / "evidence.md").read_text(encoding="utf-8").replace(
            DERIVED_REL, correct_rel
        ),
        encoding="utf-8",
    )

    def use_aggregate_name(updated: dict[str, Any]) -> None:
        updated["artifacts"][2]["path"] = correct_rel
        updated["artifacts"][3]["script"]["output"] = correct_rel

    _edit_json(manifest_path, use_aggregate_name)
    _refresh_research_lock(checker, research, lock)

    correct_result = checker.check_run(research, delivery, lock)

    assert correct_result["mechanical_status"] == "PASS", correct_result["issues"]


def test_manifest_input_graph_rejects_self_references(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)

    def make_normalized_self_reference(manifest: dict[str, Any]) -> None:
        normalized = next(
            entry for entry in manifest["artifacts"] if entry["type"] == "normalized"
        )
        normalized["inputs"] = [
            {"path": normalized["path"], "sha256": normalized["sha256"]}
        ]

    _edit_json(research / "manifest.json", make_normalized_self_reference)
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert "manifest.input_cycle" in {
        issue["code"] for issue in result["issues"]
    }, result["issues"]


def test_manifest_input_graph_rejects_multi_artifact_cycles(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)

    def make_two_artifact_cycle(manifest: dict[str, Any]) -> None:
        normalized = next(
            entry for entry in manifest["artifacts"] if entry["type"] == "normalized"
        )
        derived = next(
            entry for entry in manifest["artifacts"] if entry["type"] == "derived"
        )
        normalized["inputs"] = [
            {"path": derived["path"], "sha256": derived["sha256"]}
        ]

    _edit_json(research / "manifest.json", make_two_artifact_cycle)
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert "manifest.input_cycle" in {
        issue["code"] for issue in result["issues"]
    }, result["issues"]


def test_normalized_input_chain_without_raw_source_is_a_nonblocking_warning(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)

    def terminate_chain_at_script(manifest: dict[str, Any]) -> None:
        normalized = next(
            entry for entry in manifest["artifacts"] if entry["type"] == "normalized"
        )
        script = next(
            entry for entry in manifest["artifacts"] if entry["type"] == "script"
        )
        normalized["inputs"] = [
            {"path": script["path"], "sha256": script["sha256"]}
        ]
        script["inputs"] = []
        script["script"]["input"] = "not_applicable"

    _edit_json(research / "manifest.json", terminate_chain_at_script)
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert result["mechanical_status"] == "PASS", result
    assert result["issues"] == []
    assert "manifest.input_not_source_bound" in {
        warning["code"] for warning in result["warnings"]
    }, result["warnings"]


def test_normalized_artifact_without_inputs_is_a_nonblocking_warning(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)

    def remove_inputs(manifest: dict[str, Any]) -> None:
        normalized = next(
            entry for entry in manifest["artifacts"] if entry["type"] == "normalized"
        )
        normalized["inputs"] = []

    _edit_json(research / "manifest.json", remove_inputs)
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert result["mechanical_status"] == "PASS", result
    assert result["issues"] == []
    assert "manifest.input_not_source_bound" in {
        warning["code"] for warning in result["warnings"]
    }, result["warnings"]


def test_derived_artifact_rejects_non_json_media_format(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    wrong_rel = DERIVED_REL.removesuffix(".json") + ".csv"
    (research / DERIVED_REL).rename(research / wrong_rel)
    (research / "evidence.md").write_text(
        (research / "evidence.md").read_text(encoding="utf-8").replace(
            DERIVED_REL, wrong_rel
        ),
        encoding="utf-8",
    )
    manifest_path = research / "manifest.json"

    def edit(manifest: dict[str, Any]) -> None:
        manifest["artifacts"][2]["path"] = wrong_rel
        manifest["artifacts"][2]["media_format"] = "csv"
        manifest["artifacts"][3]["script"]["output"] = wrong_rel

    _edit_json(manifest_path, edit)
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert "manifest.invalid_name" in {
        issue["code"] for issue in result["issues"]
    }, result["issues"]


def test_report_home_field_accepts_inline_code_label(checker: Any, tmp_path: Path) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    report = research / "report.md"
    report.write_text(
        report.read_text(encoding="utf-8").replace("| as_of |", "| `as_of` |", 1),
        encoding="utf-8",
    )
    _edit_json(
        lock,
        lambda record: (
            record["research_root"].__setitem__(
                "tree_sha256", checker.research_tree_sha256(research)
            ),
            record["report"].__setitem__("sha256", sha256_file(report)),
        ),
    )

    result = checker.check_run(research, delivery, lock)

    assert result["mechanical_status"] == "PASS", result["issues"]


def test_evidence_heading_accepts_fullwidth_colon(checker: Any, tmp_path: Path) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    evidence = research / "evidence.md"
    evidence.write_text(
        evidence.read_text(encoding="utf-8")
        .replace("### E1 时点", "### E1：时点")
        .replace("### C1 财务校验", "### C1：财务校验"),
        encoding="utf-8",
    )
    _edit_json(
        lock,
        lambda record: record["research_root"].__setitem__(
            "tree_sha256", checker.research_tree_sha256(research)
        ),
    )

    result = checker.check_run(research, delivery, lock)

    assert result["mechanical_status"] == "PASS", result["issues"]


def test_adopted_evidence_cannot_bypass_unregistered_artifact_via_good_link(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    evidence = research / "evidence.md"
    evidence.write_text(
        evidence.read_text(encoding="utf-8").replace(
            f"- 合成时点证据：`{RAW_REL}`。",
            "- 无效定位：`artifacts/raw/source-a/unregistered.json`；另见 E2。",
            1,
        )
        + f"\n### E2 补充\n\n- 有效定位：`{RAW_REL}`。\n",
        encoding="utf-8",
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    codes = {issue["code"] for issue in result["issues"]}
    assert "trace.adopted_claim_missing_source" in codes
    assert result["mechanical_status"] == "FAIL"


def test_adopted_w10_evidence_id_without_local_artifact_fails(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    evidence = research / "evidence.md"
    evidence.write_text(
        evidence.read_text(encoding="utf-8")
        + "\n### E17 公告事实\n\n- 原文定位：https://example.test/disclosure。\n",
        encoding="utf-8",
    )
    owner = research / "work-packages/W0-task-framing.md"
    owner.write_text(
        owner.read_text(encoding="utf-8").replace("证据定位：E1。", "证据定位：E17。", 1),
        encoding="utf-8",
    )
    w10 = research / "work-packages/W10-report-review.md"
    w10.write_text(
        w10.read_text(encoding="utf-8").replace(
            "| W0 | E1 | adopted |", "| W0 | E17 | adopted |", 1
        ),
        encoding="utf-8",
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert "trace.adopted_claim_missing_source" in {
        issue["code"] for issue in result["issues"]
    }
    assert result["mechanical_status"] == "FAIL"


def test_adopted_w10_indirect_reference_without_local_artifact_fails(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    evidence = research / "evidence.md"
    evidence.write_text(
        evidence.read_text(encoding="utf-8")
        + "\n### E17 间接事实\n\n- 另见 E18。\n"
        + "\n### E18 外部定位\n\n- 原文定位：https://example.test/disclosure。\n",
        encoding="utf-8",
    )
    owner = research / "work-packages/W0-task-framing.md"
    owner.write_text(
        owner.read_text(encoding="utf-8").replace("证据定位：E1。", "证据定位：E17。", 1),
        encoding="utf-8",
    )
    w10 = research / "work-packages/W10-report-review.md"
    w10.write_text(
        w10.read_text(encoding="utf-8").replace(
            "| W0 | E1 | adopted |", "| W0 | E17 | adopted |", 1
        ),
        encoding="utf-8",
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert "trace.adopted_claim_missing_source" in {
        issue["code"] for issue in result["issues"]
    }
    assert result["mechanical_status"] == "FAIL"


def test_one_valid_reference_does_not_hide_another_dangling_adopted_reference(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    evidence = research / "evidence.md"
    evidence.write_text(
        evidence.read_text(encoding="utf-8")
        + "\n### E17 公告事实\n\n- 原文定位：https://example.test/disclosure。\n",
        encoding="utf-8",
    )
    owner = research / "work-packages/W0-task-framing.md"
    owner.write_text(
        owner.read_text(encoding="utf-8") + "\n证据定位：E17。\n",
        encoding="utf-8",
    )
    w10 = research / "work-packages/W10-report-review.md"
    w10.write_text(
        w10.read_text(encoding="utf-8").replace(
            "| W0 | E1 | adopted |", "| W0 | E1、E17 | adopted |", 1
        ),
        encoding="utf-8",
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert "trace.adopted_claim_missing_source" in {
        issue["code"] for issue in result["issues"]
    }
    assert result["mechanical_status"] == "FAIL"


def test_adopted_w10_indirect_reference_to_adopted_artifact_passes(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    evidence = research / "evidence.md"
    evidence.write_text(
        evidence.read_text(encoding="utf-8") + "\n### E17 别名\n\n- 另见 E1。\n",
        encoding="utf-8",
    )
    owner = research / "work-packages/W0-task-framing.md"
    owner.write_text(
        owner.read_text(encoding="utf-8").replace("证据定位：E1。", "证据定位：E17。", 1),
        encoding="utf-8",
    )
    w10 = research / "work-packages/W10-report-review.md"
    w10.write_text(
        w10.read_text(encoding="utf-8").replace(
            "| W0 | E1 | adopted |", "| W0 | E17 | adopted |", 1
        ),
        encoding="utf-8",
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert "trace.adopted_claim_missing_source" not in {
        issue["code"] for issue in result["issues"]
    }
    assert result["mechanical_status"] == "PASS", result["issues"]


def test_direct_adopted_artifact_remains_a_leaf_when_block_discloses_a_gap(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    evidence = research / "evidence.md"
    evidence.write_text(
        evidence.read_text(encoding="utf-8").replace(
            f"- 合成时点证据：`{RAW_REL}`。",
            f"- 合成时点证据：`{RAW_REL}`；另见 U17（未取得的对照）。",
            1,
        )
        + "\n### U17 未取得的对照\n\n- 未取得，作为缺口保留。\n",
        encoding="utf-8",
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert "trace.adopted_claim_missing_source" not in {
        issue["code"] for issue in result["issues"]
    }
    assert result["mechanical_status"] == "PASS", result["issues"]


def test_filtered_locator_only_supports_adopted_claim_when_artifact_is_adopted(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    evidence = research / "evidence.md"
    evidence.write_text(
        evidence.read_text(encoding="utf-8").replace(
            f"- 合成时点证据：`{RAW_REL}`。", f"[unused]: {RAW_REL}", 1
        ),
        encoding="utf-8",
    )
    _edit_json(
        research / "manifest.json",
        lambda manifest: manifest["artifacts"][0].__setitem__("status", "not_adopted"),
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert "trace.report_claim_not_adopted" in {
        issue["code"] for issue in result["issues"]
    }
    assert result["mechanical_status"] == "FAIL"


def test_chinese_parenthetical_after_artifact_does_not_break_source_resolution(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    evidence = research / "evidence.md"
    evidence.write_text(
        evidence.read_text(encoding="utf-8").replace(
            f"- 合成时点证据：`{RAW_REL}`。",
            f"- 合成时点证据：{RAW_REL}（输入：合成）。",
            1,
        ),
        encoding="utf-8",
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert "trace.adopted_claim_missing_source" not in {
        issue["code"] for issue in result["issues"]
    }
    assert result["mechanical_status"] == "PASS", result["issues"]


def test_exact_artifact_path_does_not_emit_mapping_warning(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)

    result = checker.check_run(research, delivery, lock)

    assert "trace.invalid_w10_mapping" not in {
        warning["code"] for warning in result["warnings"]
    }


def test_exact_artifact_path_is_not_ambiguous_when_basenames_repeat(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    duplicate_rel = f"artifacts/raw/source-b/{Path(RAW_REL).name}"
    duplicate = research / duplicate_rel
    duplicate.parent.mkdir(parents=True)
    duplicate.write_bytes((research / RAW_REL).read_bytes())

    def add_duplicate(manifest: dict[str, Any]) -> None:
        entry = dict(manifest["artifacts"][0])
        entry["path"] = duplicate_rel
        entry["source_id"] = "source-b"
        manifest["artifacts"].append(entry)

    _edit_json(research / "manifest.json", add_duplicate)
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert result["issues"] == []
    assert "trace.invalid_w10_mapping" not in {
        warning["code"] for warning in result["warnings"]
    }


@pytest.mark.parametrize("ellipsis", ("...", "…"))
def test_unique_abbreviated_adopted_artifact_is_a_warning_not_a_missing_source(
    checker: Any, tmp_path: Path, ellipsis: str
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    evidence = research / "evidence.md"
    abbreviated = f"artifacts/raw/source-a/quote--{ellipsis}--de7fa80b.json"
    evidence.write_text(
        evidence.read_text(encoding="utf-8").replace(
            f"- 合成时点证据：`{RAW_REL}`。",
            f"- 合成时点证据：`{abbreviated}`。",
            1,
        ),
        encoding="utf-8",
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    _assert_nonblocking_warning(result, "trace.invalid_w10_mapping")


def test_unique_adopted_artifact_basename_is_a_warning_not_a_missing_source(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    evidence = research / "evidence.md"
    evidence.write_text(
        evidence.read_text(encoding="utf-8").replace(
            f"- 合成时点证据：`{RAW_REL}`。",
            f"- 合成时点证据：`{Path(RAW_REL).name}`。",
            1,
        ),
        encoding="utf-8",
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    _assert_nonblocking_warning(result, "trace.invalid_w10_mapping")


def test_unique_nonadopted_artifact_basename_does_not_support_an_adopted_claim(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    evidence = research / "evidence.md"
    evidence.write_text(
        evidence.read_text(encoding="utf-8").replace(
            f"- 合成时点证据：`{RAW_REL}`。",
            f"- 合成时点证据：`{Path(RAW_REL).name}`。",
            1,
        ),
        encoding="utf-8",
    )
    _edit_json(
        research / "manifest.json",
        lambda manifest: manifest["artifacts"][0].__setitem__("status", "not_adopted"),
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert "trace.report_claim_not_adopted" in {
        issue["code"] for issue in result["issues"]
    }
    assert result["mechanical_status"] == "FAIL"


def _set_w0_mapping_locator(research: Path, old: str, new: str) -> None:
    owner = research / "work-packages/W0-task-framing.md"
    owner.write_text(
        owner.read_text(encoding="utf-8").replace(
            f"证据定位：{old}。", f"证据定位：{new}。", 1
        ),
        encoding="utf-8",
    )
    w10 = research / "work-packages/W10-report-review.md"
    w10.write_text(
        w10.read_text(encoding="utf-8").replace(
            f"| W0 | {old} | adopted |", f"| W0 | {new} | adopted |", 1
        ),
        encoding="utf-8",
    )


def test_indirect_reference_to_unique_adopted_basename_is_nonblocking(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    evidence = research / "evidence.md"
    evidence.write_text(
        evidence.read_text(encoding="utf-8").replace(
            f"- 合成时点证据：`{RAW_REL}`。",
            f"- 合成时点证据：`{Path(RAW_REL).name}`。",
            1,
        )
        + "\n### E17 间接定位\n\n- 另见 E1。\n",
        encoding="utf-8",
    )
    _set_w0_mapping_locator(research, "E1", "E17")
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    _assert_nonblocking_warning(result, "trace.invalid_w10_mapping")


def test_indirect_reference_to_nonadopted_basename_is_rejected(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    evidence = research / "evidence.md"
    evidence.write_text(
        evidence.read_text(encoding="utf-8").replace(
            f"- 合成时点证据：`{RAW_REL}`。",
            f"- 合成时点证据：`{Path(RAW_REL).name}`。",
            1,
        )
        + "\n### E17 间接定位\n\n- 另见 E1。\n",
        encoding="utf-8",
    )
    _set_w0_mapping_locator(research, "E1", "E17")
    _edit_json(
        research / "manifest.json",
        lambda manifest: manifest["artifacts"][0].__setitem__("status", "not_adopted"),
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert "trace.report_claim_not_adopted" in {
        issue["code"] for issue in result["issues"]
    }
    assert result["mechanical_status"] == "FAIL"


def test_fragment_with_unique_adopted_basename_is_nonblocking(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    evidence = research / "evidence.md"
    evidence.write_text(
        evidence.read_text(encoding="utf-8").replace(
            f"- 合成时点证据：`{RAW_REL}`。",
            f"- 合成时点证据：`{Path(RAW_REL).name}`。",
            1,
        ),
        encoding="utf-8",
    )
    _set_w0_mapping_locator(research, "E1", "evidence.md#E1 时点")
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    _assert_nonblocking_warning(result, "trace.invalid_w10_mapping")


def test_fragment_with_nonadopted_basename_is_rejected(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    evidence = research / "evidence.md"
    evidence.write_text(
        evidence.read_text(encoding="utf-8").replace(
            f"- 合成时点证据：`{RAW_REL}`。",
            f"- 合成时点证据：`{Path(RAW_REL).name}`。",
            1,
        ),
        encoding="utf-8",
    )
    _set_w0_mapping_locator(research, "E1", "evidence.md#E1 时点")
    _edit_json(
        research / "manifest.json",
        lambda manifest: manifest["artifacts"][0].__setitem__("status", "not_adopted"),
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert "trace.report_claim_not_adopted" in {
        issue["code"] for issue in result["issues"]
    }
    assert result["mechanical_status"] == "FAIL"


def test_multiple_unique_adopted_basenames_in_one_block_are_nonblocking(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    evidence = research / "evidence.md"
    evidence.write_text(
        evidence.read_text(encoding="utf-8").replace(
            f"- 合成时点证据：`{RAW_REL}`。",
            f"- 合成来源一：`{Path(RAW_REL).name}`；"
            f"来源二：`{Path(NORMALIZED_REL).name}`。",
            1,
        ),
        encoding="utf-8",
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    _assert_nonblocking_warning(result, "trace.invalid_w10_mapping")


def test_one_nonadopted_basename_invalidates_a_multi_basename_leaf(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    evidence = research / "evidence.md"
    evidence.write_text(
        evidence.read_text(encoding="utf-8").replace(
            f"- 合成时点证据：`{RAW_REL}`。",
            f"- 合成来源一：`{Path(RAW_REL).name}`；"
            f"来源二：`{Path(NORMALIZED_REL).name}`。",
            1,
        ),
        encoding="utf-8",
    )
    _edit_json(
        research / "manifest.json",
        lambda manifest: manifest["artifacts"][1].__setitem__("status", "not_adopted"),
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert "trace.report_claim_not_adopted" in {
        issue["code"] for issue in result["issues"]
    }
    assert result["mechanical_status"] == "FAIL"


def test_indirect_evidence_cycle_does_not_resolve(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    evidence = research / "evidence.md"
    evidence.write_text(
        evidence.read_text(encoding="utf-8")
        + "\n### E17 循环一\n\n- 另见 E18。\n"
        + "\n### E18 循环二\n\n- 另见 E17。\n",
        encoding="utf-8",
    )
    _set_w0_mapping_locator(research, "E1", "E17")
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert "trace.adopted_claim_missing_source" in {
        issue["code"] for issue in result["issues"]
    }
    assert result["mechanical_status"] == "FAIL"


@pytest.mark.parametrize("status", ("not_adopted", "failed"))
def test_nonadopted_or_failed_w10_gap_remains_nonblocking(
    checker: Any, tmp_path: Path, status: str
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    evidence = research / "evidence.md"
    evidence.write_text(
        evidence.read_text(encoding="utf-8")
        + "\n### E17 已披露缺口\n\n- 原文定位未保存。\n",
        encoding="utf-8",
    )
    owner = research / "work-packages/W0-task-framing.md"
    owner.write_text(
        owner.read_text(encoding="utf-8").replace("证据定位：E1。", "证据定位：E17。", 1),
        encoding="utf-8",
    )
    w10 = research / "work-packages/W10-report-review.md"
    w10.write_text(
        w10.read_text(encoding="utf-8").replace(
            "| W0 | E1 | adopted |", f"| W0 | E17 | {status} |", 1
        ),
        encoding="utf-8",
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    _assert_nonblocking_warning(result, "trace.invalid_w10_mapping")


@pytest.mark.parametrize("continuation_indent", ("  ", "    "))
def test_evidence_reference_accepts_artifact_in_indented_bullet_continuation(
    checker: Any, tmp_path: Path, continuation_indent: str
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    evidence = research / "evidence.md"
    evidence.write_text(
        evidence.read_text(encoding="utf-8").replace(
            f"### E1 时点\n\n- 合成时点证据：`{RAW_REL}`。",
            f"- E1 证据明细：\n{continuation_indent}- artifact：`{RAW_REL}`。",
            1,
        ),
        encoding="utf-8",
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert result["mechanical_status"] == "PASS", result["issues"]


@pytest.mark.parametrize("case", sorted(W10_INVALID_ROWS))
def test_w10_mapping_rows_must_close_the_trace_chain(
    checker: Any, tmp_path: Path, case: str
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    row = "| " + " | ".join(W10_INVALID_ROWS[case]) + " |"
    w10 = research / "work-packages/W10-report-review.md"
    w10.write_text(
        "# W10\n\n"
        + _default_w10_quality_summary()
        + "| 报告章节 | 关键主张定位 | owner 工作包 | 证据定位 | 采用状态 |\n"
        "| --- | --- | --- | --- | --- |\n"
        f"{row}\n\n"
        "本工作包未创建或修改中间脚本。\n",
        encoding="utf-8",
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    _assert_nonblocking_warning(result, "trace.invalid_w10_mapping")


def test_w10_artifact_locator_must_match_a_registered_file_not_a_directory(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    directory_locator = "artifacts/raw/source-a/"
    owner = research / "work-packages/W0-task-framing.md"
    owner.write_text(
        owner.read_text(encoding="utf-8").replace(
            "证据定位：E1。",
            f"证据定位：{directory_locator}。",
            1,
        ),
        encoding="utf-8",
    )
    w10 = research / "work-packages/W10-report-review.md"
    w10.write_text(
        w10.read_text(encoding="utf-8").replace(
            "| W0 | E1 | adopted |",
            f"| W0 | {directory_locator} | adopted |",
            1,
        ),
        encoding="utf-8",
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    codes = {issue["code"] for issue in result["issues"]}
    assert "trace.report_claim_not_adopted" in codes
    assert result["mechanical_status"] == "FAIL"


@pytest.mark.parametrize(
    ("old_row", "new_row"),
    (
        ("| 1. 任务与时点 |", "| 1. 任务与时点（伪后缀） |"),
        ("| 重要声明：合成内容，无研究意义。 |", "| 任务与时点 |"),
        ("| W0 | E1 | adopted |", "| W0 | evidence.md#时点 | adopted |"),
        ("| W0 | E1 | adopted |", "| W0、W2 | E9 | adopted |"),
        ("| W0 | E1 | adopted |", "| W0、WX | E1 | adopted |"),
    ),
    ids=(
        "test_w10_chapter_title_must_match_fixed_title_exactly",
        "test_w10_claim_locator_cannot_be_only_the_chapter_title",
        "test_w10_fragment_locator_must_also_appear_in_owner_work_package",
        "test_each_w10_owner_must_carry_the_mapping_locator",
        "test_w10_owner_cell_rejects_mixed_unknown_owner",
    ),
)
def test_invalid_w10_mapping_edits_are_nonblocking_warnings(
    checker: Any, tmp_path: Path, old_row: str, new_row: str
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    evidence = research / "evidence.md"
    evidence.write_text(
        evidence.read_text(encoding="utf-8")
        + f"\n### E9 回访定位\n\n- 合成证据：`{RAW_REL}`。\n",
        encoding="utf-8",
    )
    w10 = research / "work-packages/W10-report-review.md"
    w10.write_text(
        w10.read_text(encoding="utf-8").replace(
            old_row,
            new_row,
            1,
        ),
        encoding="utf-8",
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    _assert_nonblocking_warning(result, "trace.invalid_w10_mapping")


@pytest.mark.parametrize("chapter", ("1.任务与时点", "1.   任务与时点"))
def test_w10_chapter_number_and_title_spacing_is_flexible(
    checker: Any, tmp_path: Path, chapter: str
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    w10 = research / "work-packages/W10-report-review.md"
    w10.write_text(
        w10.read_text(encoding="utf-8").replace(
            "| 1. 任务与时点 |",
            f"| {chapter} |",
            1,
        ),
        encoding="utf-8",
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert result["mechanical_status"] == "PASS", result
    assert result["issues"] == []


def test_w10_fragment_locator_must_match_the_complete_evidence_heading(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    owner = research / "work-packages/W0-task-framing.md"
    owner.write_text(
        owner.read_text(encoding="utf-8") + "\n证据定位：evidence.md#时。\n",
        encoding="utf-8",
    )
    w10 = research / "work-packages/W10-report-review.md"
    w10.write_text(
        w10.read_text(encoding="utf-8").replace(
            "| W0 | E1 | adopted |",
            "| W0 | evidence.md#时 | adopted |",
            1,
        ),
        encoding="utf-8",
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    _assert_nonblocking_warning(result, "trace.invalid_w10_mapping")


W10_FRAGMENT_HEADING_CASES = (
    # (evidence E1 heading override, fragment locator, rejected as invalid)
    (None, "E1 时点", False),
    ("E1 财务证据，2026年", "E1 财务证据，2026年", False),
    ("E1 关键-证据", "E1 关键-证据", False),
    ("E1 关键-证据", "e1-关键-证据", False),
    ("E1 财务证据", "E1--财务证据", True),
)


@pytest.mark.parametrize(
    ("heading", "fragment", "rejected"),
    W10_FRAGMENT_HEADING_CASES,
    ids=(
        "test_w10_fragment_locator_accepts_the_complete_heading_with_spaces",
        "test_w10_fragment_preserves_title_punctuation_and_literal_hyphens"
        "[E1 财务证据，2026年-E1 财务证据，2026年]",
        "test_w10_fragment_preserves_title_punctuation_and_literal_hyphens"
        "[E1 关键-证据-E1 关键-证据]",
        "test_w10_fragment_preserves_title_punctuation_and_literal_hyphens"
        "[E1 关键-证据-e1-关键-证据]",
        "test_w10_fragment_rejects_noncanonical_repeated_hyphens",
    ),
)
def test_w10_fragment_heading_match_variants(
    checker: Any, tmp_path: Path, heading: str | None, fragment: str, rejected: bool
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    if heading is not None:
        evidence = research / "evidence.md"
        evidence.write_text(
            evidence.read_text(encoding="utf-8").replace(
                "### E1 时点", f"### {heading}", 1
            ),
            encoding="utf-8",
        )
    owner = research / "work-packages/W0-task-framing.md"
    owner.write_text(
        owner.read_text(encoding="utf-8").replace(
            "证据定位：E1。", f"证据定位：evidence.md#{fragment}。", 1
        ),
        encoding="utf-8",
    )
    w10 = research / "work-packages/W10-report-review.md"
    w10.write_text(
        w10.read_text(encoding="utf-8").replace(
            "| W0 | E1 | adopted |", f"| W0 | evidence.md#{fragment} | adopted |", 1
        ),
        encoding="utf-8",
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    if rejected:
        _assert_nonblocking_warning(result, "trace.invalid_w10_mapping")
    else:
        assert result["mechanical_status"] == "PASS", result["issues"]


def test_w10_adoption_status_must_match_terminal_manifest_artifacts(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)

    def mark_e1_artifact_not_adopted(manifest: dict[str, Any]) -> None:
        raw_entry = next(
            entry for entry in manifest["artifacts"] if entry["path"] == RAW_REL
        )
        raw_entry["status"] = "not_adopted"

    _edit_json(research / "manifest.json", mark_e1_artifact_not_adopted)
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    codes = {issue["code"] for issue in result["issues"]}
    assert "trace.report_claim_not_adopted" in codes
    assert result["mechanical_status"] == "FAIL"


def test_w10_substitute_obtained_means_the_terminal_artifact_is_adopted(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    w10 = research / "work-packages/W10-report-review.md"
    w10.write_text(
        w10.read_text(encoding="utf-8").replace(
            "| W0 | E1 | adopted |",
            "| W0 | E1 | 替代取得 |",
            1,
        ),
        encoding="utf-8",
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert result["mechanical_status"] == "PASS", result["issues"]


def test_w10_fragment_includes_artifacts_from_nested_subsections(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    evidence = research / "evidence.md"
    evidence.write_text(
        evidence.read_text(encoding="utf-8").replace(
            "### E1 时点\n\n",
            "### E1 时点\n\n#### 原始定位\n\n",
            1,
        ),
        encoding="utf-8",
    )
    owner = research / "work-packages/W0-task-framing.md"
    owner.write_text(
        owner.read_text(encoding="utf-8").replace(
            "证据定位：E1。", "证据定位：evidence.md#E1 时点。", 1
        ),
        encoding="utf-8",
    )
    w10 = research / "work-packages/W10-report-review.md"
    w10.write_text(
        w10.read_text(encoding="utf-8").replace(
            "| W0 | E1 | adopted |",
            "| W0 | evidence.md#E1 时点 | adopted |",
            1,
        ),
        encoding="utf-8",
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert result["mechanical_status"] == "PASS", result["issues"]


def test_w10_owner_evidence_id_match_uses_token_boundaries(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    evidence = research / "evidence.md"
    evidence.write_text(
        evidence.read_text(encoding="utf-8")
        + f"\n### E10 词边界\n\n- 合成证据：`{RAW_REL}`。\n",
        encoding="utf-8",
    )
    owner = research / "work-packages/W0-task-framing.md"
    owner.write_text(
        owner.read_text(encoding="utf-8").replace("E1", "E10"),
        encoding="utf-8",
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    _assert_nonblocking_warning(result, "trace.invalid_w10_mapping")


EVIDENCE_TRACE_WARNING_CASES = (
    # (evidence bullet replacement, evidence append, owner append, W10 row)
    (
        None,
        f"\n```markdown\n### 伪标题\n\n`{RAW_REL}`\n```\n",
        "\n证据定位：evidence.md#伪标题。\n",
        "| W0 | evidence.md#伪标题 | adopted |",
    ),
    (
        None,
        f"\n<pre>\n### 伪标题\n\n`{RAW_REL}`\n</pre>\n",
        "\n证据定位：evidence.md#伪标题。\n",
        "| W0 | evidence.md#伪标题 | adopted |",
    ),
    (f"<script>\n- 合成时点证据：`{RAW_REL}`。", None, None, None),
    (f"[unused]: {RAW_REL}", None, None, None),
)


@pytest.mark.parametrize(
    ("replacement", "append_text", "owner_extra", "w10_row"),
    EVIDENCE_TRACE_WARNING_CASES,
    ids=(
        "test_w10_fragment_does_not_resolve_from_non_markdown_evidence"
        "[test_w10_fragment_does_not_resolve_from_fenced_evidence]",
        "test_w10_fragment_does_not_resolve_from_non_markdown_evidence"
        "[test_w10_fragment_does_not_resolve_from_raw_html_evidence]",
        "test_evidence_edits_that_block_trace_resolution_warn"
        "[test_unclosed_raw_html_in_evidence_blocks_trace_resolution]",
        "test_evidence_edits_that_block_trace_resolution_warn"
        "[test_unused_markdown_reference_definition_does_not_close_trace]",
    ),
)
def test_evidence_variants_that_break_w10_trace_resolution_warn(
    checker: Any,
    tmp_path: Path,
    replacement: str | None,
    append_text: str | None,
    owner_extra: str | None,
    w10_row: str | None,
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    evidence = research / "evidence.md"
    text = evidence.read_text(encoding="utf-8")
    if replacement is not None:
        text = text.replace(f"- 合成时点证据：`{RAW_REL}`。", replacement, 1)
    evidence.write_text(text + (append_text or ""), encoding="utf-8")
    if owner_extra is not None:
        owner = research / "work-packages/W0-task-framing.md"
        owner.write_text(
            owner.read_text(encoding="utf-8") + owner_extra, encoding="utf-8"
        )
    if w10_row is not None:
        w10 = research / "work-packages/W10-report-review.md"
        w10.write_text(
            w10.read_text(encoding="utf-8").replace(
                "| W0 | E1 | adopted |", w10_row, 1
            ),
            encoding="utf-8",
        )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    _assert_nonblocking_warning(result, "trace.invalid_w10_mapping")


def test_used_markdown_reference_definition_can_close_trace(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    evidence = research / "evidence.md"
    evidence.write_text(
        evidence.read_text(encoding="utf-8").replace(
            f"- 合成时点证据：`{RAW_REL}`。",
            f"- 合成时点证据见[原始材料][raw-source]。\n\n[raw-source]: {RAW_REL}",
            1,
        ),
        encoding="utf-8",
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert result["mechanical_status"] == "PASS", result["issues"]


def test_inline_code_html_literal_is_not_raw_html(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    report = research / "report.md"
    report.write_text(
        report.read_text(encoding="utf-8") + "\n字面量示例：`<script>`。\n",
        encoding="utf-8",
    )
    _refresh_research_lock(checker, research, lock)

    result = checker.check_run(research, delivery, lock)

    assert result["mechanical_status"] == "PASS", result["issues"]


def test_w10_pipe_rows_without_separator_warn(checker: Any, tmp_path: Path) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    w10 = research / "work-packages/W10-report-review.md"
    w10.write_text(
        "# W10\n\n"
        "| 报告章节 | 关键主张定位 | owner 工作包 | 证据定位 | 采用状态 |\n"
        "| 这不是分隔行 | 但仍是 | pipe | 文本 | adopted |\n"
        "| 1. 任务与时点 | 重要声明：合成内容，无研究意义。 | W0 | E1 | adopted |\n\n"
        "本工作包未创建或修改中间脚本。\n",
        encoding="utf-8",
    )

    result = checker.check_run(research, delivery, lock)

    codes = {warning["code"] for warning in result["warnings"]}
    assert "trace.missing_w10_mapping" in codes


def test_lock_requires_recorded_message_and_research_paths(checker: Any, tmp_path: Path) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    _edit_json(lock, lambda record: record["delivery_message"].pop("path"))
    _edit_json(lock, lambda record: record["research_root"].pop("path"))

    result = checker.check_run(research, delivery, lock)

    codes = {issue["code"] for issue in result["issues"]}
    assert "lock.message_path_missing" in codes
    assert "lock.research_path_missing" in codes
    assert result["mechanical_status"] == "FAIL"


@pytest.mark.parametrize("field", LOCK_REQUIRED_FIELDS)
def test_lock_requires_complete_minimum_schema(checker: Any, tmp_path: Path, field: str) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    _edit_json(lock, lambda record: record.pop(field))

    result = checker.check_run(research, delivery, lock)

    codes = {issue["code"] for issue in result["issues"]}
    assert "lock.schema" in codes, f"{field}: {result['issues']}"
    assert result["mechanical_status"] == "FAIL"


def test_forged_lock_paths_are_rejected(checker: Any, tmp_path: Path) -> None:
    import json

    research, delivery, lock = build_valid_phase2_run(tmp_path)
    payload = json.loads(lock.read_text(encoding="utf-8"))
    payload["delivery_message"]["path"] = "/tmp/forged-message.md"
    payload["research_root"]["path"] = "/tmp/forged-research"
    lock.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    result = checker.check_run(research, delivery, lock)

    codes = {issue["code"] for issue in result["issues"]}
    assert "lock.message_path_mismatch" in codes
    assert "lock.research_path_mismatch" in codes
    assert result["mechanical_status"] == "FAIL"


def test_output_write_failure_leaves_no_partial_result(
    tmp_path: Path,
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path / "run")
    output_dir = tmp_path / "read-only"
    output_dir.mkdir()
    output = output_dir / "result.json"
    output_dir.chmod(0o500)

    try:
        completed = _run_cli(research, delivery, lock, output)
    finally:
        output_dir.chmod(0o700)

    assert completed.returncode == 2
    assert not output.exists()
    assert list(output_dir.glob("*.tmp")) == []


def test_cli_output_parent_creation_failure_exits_two_without_traceback(
    tmp_path: Path,
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path / "run")
    output_parent = tmp_path / "not-a-directory"
    output_parent.write_text("ordinary file\n", encoding="utf-8")

    completed = _run_cli(
        research,
        delivery,
        lock,
        output_parent / "result.json",
    )

    assert completed.returncode == 2
    assert "Traceback" not in completed.stderr


def test_cli_does_not_overwrite_or_delete_a_preexisting_legacy_temp_path(
    tmp_path: Path,
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path / "run")
    output = tmp_path / "checker-output/result.json"
    output.parent.mkdir()
    legacy_temp = output.parent / "result.json.tmp"
    legacy_temp.write_text("unrelated sentinel\n", encoding="utf-8")

    completed = _run_cli(research, delivery, lock, output)

    assert completed.returncode == 0
    assert legacy_temp.read_text(encoding="utf-8") == "unrelated sentinel\n"
    assert json.loads(output.read_text(encoding="utf-8"))["mechanical_status"] == "PASS"


def test_cli_valid_run_exits_0(tmp_path: Path) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path / "run")
    output = tmp_path / "checker-output/result.json"

    completed = _run_cli(research, delivery, lock, output)

    assert completed.returncode == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["mechanical_status"] == "PASS"


def test_cli_issues_still_write_result_and_exit_1(tmp_path: Path) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path / "run")
    (research / "work-packages/W1-subject-verification.md").unlink()
    output = tmp_path / "checker-output/result.json"

    completed = _run_cli(research, delivery, lock, output)

    assert completed.returncode == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["mechanical_status"] == "FAIL"


def test_cli_missing_lock_writes_not_checked_result_and_exits_1(tmp_path: Path) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path / "run")
    output = tmp_path / "checker-output/result.json"

    completed = _run_cli(
        research,
        delivery,
        tmp_path / "run/missing-lock/lock-record.json",
        output,
    )

    assert completed.returncode == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["message_input_status"] == "not_checked"
    assert payload["mechanical_status"] == "FAIL"


def test_cli_refuses_existing_output(tmp_path: Path) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path / "run")
    output = tmp_path / "checker-output/result.json"
    output.parent.mkdir()
    output.write_text("existing\n", encoding="utf-8")

    completed = _run_cli(research, delivery, lock, output)

    assert completed.returncode == 2
    assert output.read_text(encoding="utf-8") == "existing\n"


@pytest.mark.parametrize("protected", ("research", "lock"))
def test_cli_rejects_output_inside_locked_inputs(
    tmp_path: Path, protected: str
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    output_parent = research if protected == "research" else lock.parent
    output = output_parent / "checker-result.json"

    completed = _run_cli(research, delivery, lock, output)

    assert completed.returncode == 2
    assert "Traceback" not in completed.stderr
    assert not output.exists()


def test_cli_rejects_symlinked_output_parent_into_research_root(tmp_path: Path) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path / "run")
    redirected = tmp_path / "redirected-output"
    redirected.symlink_to(research, target_is_directory=True)
    output = redirected / "checker-result.json"

    completed = _run_cli(research, delivery, lock, output)

    assert completed.returncode == 2
    assert "Traceback" not in completed.stderr
    assert not (research / "checker-result.json").exists()


# --- Phase 4 quality contract: mechanical structure only, no business verdicts ---


def _phase4_result(
    checker: Any,
    research: Path,
    delivery: Path,
    lock: Path,
    *,
    refresh_lock: bool = True,
) -> tuple[dict[str, Any], set[str]]:
    if refresh_lock:
        _refresh_research_lock(checker, research, lock)
    result = checker.check_run(research, delivery, lock)
    return result, {issue["code"] for issue in result["issues"]}


@pytest.mark.parametrize(
    ("requested", "actual", "completeness", "validity"),
    (
        ("quick", "quick", "完整", "有效"),
        ("standard", "quick", "不完整", "有效"),
        ("deep", "standard", "不完整", "有效"),
        ("deep", "quick", "完整", "受限"),
        ("deep", "未达到 quick", "不完整", "受限"),
    ),
)
def test_phase4_quality_combinations_pass(
    checker: Any,
    tmp_path: Path,
    requested: str,
    actual: str,
    completeness: str,
    validity: str,
) -> None:
    run_id = f"合成公司-000001.SZ-{requested}-20260630T190000+0800"
    research, delivery, lock = build_valid_phase2_run(
        tmp_path,
        run_id=run_id,
        requested_depth=requested,
        actual_depth=actual,
        information_completeness=completeness,
        analysis_validity=validity,
    )
    result = checker.check_run(research, delivery, lock)
    assert result["mechanical_status"] == "PASS", result["issues"]


@pytest.mark.parametrize(
    ("old_row", "new_row", "expected_code"),
    (
        (
            "| 请求深度 | standard |",
            "| 请求深度 | 超深度 |",
            "quality.invalid_value",
        ),
        (
            "| 实际深度 | standard |",
            "| 实际深度 | fast |",
            "quality.invalid_value",
        ),
        (
            "| 信息完整性 | 完整 |",
            "| 信息完整性 | 部分 |",
            "quality.invalid_value",
        ),
        (
            "| 分析有效性 | 有效 |",
            "| 分析有效性 | 无效 |",
            "quality.invalid_value",
        ),
        (
            "| 实际深度 | standard |",
            "| 实际深度 | deep |",
            "quality.depth_order",
        ),
        (
            "| 请求深度 | standard |",
            "| 请求深度 | deep |",
            "quality.request_depth_mismatch",
        ),
    ),
)
def test_phase4_home_value_and_depth_relations_are_enforced(
    checker: Any,
    tmp_path: Path,
    old_row: str,
    new_row: str,
    expected_code: str,
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    _replace_quality_field_everywhere(research, old_row, new_row)

    result, codes = _phase4_result(checker, research, delivery, lock)

    assert codes == {expected_code}
    assert result["mechanical_status"] == "FAIL"


@pytest.mark.parametrize(
    ("completeness", "validity"),
    (
        ("不完整", "有效"),
        ("完整", "受限"),
    ),
)
def test_phase4_limited_research_must_not_claim_requested_depth(
    checker: Any,
    tmp_path: Path,
    completeness: str,
    validity: str,
) -> None:
    research, delivery, lock = build_valid_phase2_run(
        tmp_path,
        information_completeness=completeness,
        analysis_validity=validity,
    )

    result, codes = _phase4_result(
        checker, research, delivery, lock, refresh_lock=False
    )

    assert "quality.request_depth_claim" in codes
    assert result["mechanical_status"] == "FAIL"


def _rewrite_package(
    research: Path, package_filename: str, edit: Callable[[str], str]
) -> None:
    path = research / "work-packages" / package_filename
    path.write_text(edit(path.read_text(encoding="utf-8")), encoding="utf-8")


def _without_table(text: str, table: str) -> str:
    assert table in text
    return text.replace(table, "", 1)


def _replace_quality_field_everywhere(
    research: Path, old_row: str, new_row: str
) -> None:
    for relative in (
        "report.md",
        "checkpoint.md",
        "work-packages/W10-report-review.md",
    ):
        path = research / relative
        text = path.read_text(encoding="utf-8")
        assert old_row in text
        path.write_text(text.replace(old_row, new_row, 1), encoding="utf-8")


@pytest.mark.parametrize(
    ("package_filename", "expected_code"),
    (
        ("W3-industry-competition.md", "quality.missing_required_items"),
        ("W8-market-signals.md", "quality.missing_required_items"),
        ("W4-business-governance.md", "quality.missing_model_profile"),
        ("W6-forecast-scenarios.md", "quality.missing_method_selection"),
        ("W9-thesis-counterevidence.md", "quality.missing_conclusion_boundary"),
    ),
)
def test_phase4_owner_tables_are_required(
    checker: Any,
    tmp_path: Path,
    package_filename: str,
    expected_code: str,
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    removals = {
        "W3-industry-competition.md": lambda text: _without_table(
            text, _required_items_table("standard")
        ),
        "W8-market-signals.md": lambda text: _without_table(
            text, _required_items_table("standard")
        ),
        "W4-business-governance.md": lambda text: _without_table(
            text, _model_profile_table("合成经营模型（fixture）")
        ),
        "W6-forecast-scenarios.md": lambda text: _without_table(
            text, _method_selection_table()
        ),
        "W9-thesis-counterevidence.md": lambda text: _without_table(
            text, _conclusion_boundary_table()
        ),
    }
    _rewrite_package(research, package_filename, removals[package_filename])

    result, codes = _phase4_result(checker, research, delivery, lock)

    assert expected_code in codes
    assert result["mechanical_status"] == "FAIL"


@pytest.mark.parametrize(
    ("old_row", "new_row"),
    (
        (
            "| 合成必需项 | standard 共通底线 | 有 | E1，合成查询范围 | 满足：无额外限制 |",
            "| 合成必需项 | standard 共通底线 | 遗失 | E1，合成查询范围 | 满足：无额外限制 |",
        ),
        (
            "| 合成必需项 | standard 共通底线 | 有 | E1，合成查询范围 | 满足：无额外限制 |",
            "| 合成必需项 | standard 共通底线 | 未发现 | E1，合成查询范围 | 满足：无额外限制 |",
        ),
    ),
)
def test_phase4_required_item_status_and_prefix_must_agree(
    checker: Any,
    tmp_path: Path,
    old_row: str,
    new_row: str,
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    _rewrite_package(
        research,
        "W3-industry-competition.md",
        lambda text: text.replace(old_row, new_row, 1),
    )

    result, codes = _phase4_result(checker, research, delivery, lock)

    assert "quality.invalid_required_item" in codes
    assert result["mechanical_status"] == "FAIL"


def test_phase4_rejects_an_incomplete_required_item_row(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    valid_row = (
        "| 合成必需项 | standard 共通底线 | 有 | E1，合成查询范围 | "
        "满足：无额外限制 |"
    )
    incomplete_row = (
        "| 合成残缺项 | standard 共通底线 | 有 | | 满足：无额外限制 |"
    )
    _rewrite_package(
        research,
        "W3-industry-competition.md",
        lambda text: text.replace(valid_row, f"{valid_row}\n{incomplete_row}", 1),
    )

    result, codes = _phase4_result(checker, research, delivery, lock)

    assert "quality.invalid_required_item" in codes
    assert result["mechanical_status"] == "FAIL"


@pytest.mark.parametrize(
    ("target", "edit"),
    (
        ("checkpoint", lambda text: _without_table(
            text, _quality_summary_table(
                "standard", "standard", "完整", "有效", "合成经营模型（fixture）"
            )
        )),
        ("W10", lambda text: _without_table(
            text, _quality_summary_table(
                "standard", "standard", "完整", "有效", "合成经营模型（fixture）"
            )
        )),
    ),
)
def test_phase4_quality_summary_is_required(
    checker: Any, tmp_path: Path, target: str, edit: Callable[[str], str]
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    if target == "checkpoint":
        path = research / "checkpoint.md"
    else:
        path = research / "work-packages/W10-report-review.md"
    path.write_text(edit(path.read_text(encoding="utf-8")), encoding="utf-8")

    result, codes = _phase4_result(checker, research, delivery, lock)

    assert "quality.missing_summary" in codes
    assert result["mechanical_status"] == "FAIL"


@pytest.mark.parametrize("target", ("checkpoint", "W10"))
def test_phase4_summary_must_mirror_the_report(
    checker: Any, tmp_path: Path, target: str
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    path = (
        research / "checkpoint.md"
        if target == "checkpoint"
        else research / "work-packages/W10-report-review.md"
    )
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "| 实际深度 | standard |", "| 实际深度 | quick |", 1
        ),
        encoding="utf-8",
    )

    result, codes = _phase4_result(checker, research, delivery, lock)

    assert "quality.summary_mismatch" in codes
    assert result["mechanical_status"] == "FAIL"


@pytest.mark.parametrize(
    ("build_kwargs", "edit"),
    (
        (
            {},
            lambda text: text.replace(
                "| 合成必需项 | standard 共通底线 | 有 | E1，合成查询范围 | 满足：无额外限制 |",
                "| 合成必需项 | standard 共通底线 | 未取得 | E1，合成查询范围 "
                "| 未满足：限制合成结论 |",
                1,
            ),
        ),
        (
            {"information_completeness": "不完整", "actual_depth": "quick"},
            lambda text: text.replace(
                "| 合成必需项 | standard 共通底线 | 未取得 | E1，合成查询范围 "
                "| 未满足：限制合成结论 |",
                "| 合成必需项 | standard 共通底线 | 有 | E1，合成查询范围 | 满足：无额外限制 |",
                1,
            ),
        ),
    ),
)
def test_phase4_completeness_must_match_explicit_required_items(
    checker: Any,
    tmp_path: Path,
    build_kwargs: dict[str, Any],
    edit: Callable[[str], str],
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path, **build_kwargs)
    _rewrite_package(
        research, "W5-financial-validation.md", edit
    )

    result, codes = _phase4_result(checker, research, delivery, lock)

    assert "quality.summary_mismatch" in codes
    assert result["mechanical_status"] == "FAIL"


def test_phase4_restricted_validity_accepts_an_excluded_method_with_missing_input(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(
        tmp_path, analysis_validity="受限", actual_depth="quick"
    )
    w5 = (research / "work-packages/W5-financial-validation.md").read_text(
        encoding="utf-8"
    )
    assert "| 合成方法 | 排除 |" in w5
    assert "| 合成方法 | 受限 |" not in w5

    result = checker.check_run(research, delivery, lock)

    assert result["mechanical_status"] == "PASS", result["issues"]


def test_phase4_restricted_validity_requires_an_observable_method_constraint(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(
        tmp_path, analysis_validity="受限", actual_depth="quick"
    )
    _rewrite_package(
        research,
        "W5-financial-validation.md",
        lambda text: text.replace(
            _method_selection_table(limited=True), _method_selection_table(), 1
        ),
    )

    result, codes = _phase4_result(checker, research, delivery, lock)

    assert codes == {"quality.summary_mismatch"}
    assert result["mechanical_status"] == "FAIL"


@pytest.mark.parametrize("field", ("关键资料缺口", "当前不能得出的结论"))
def test_phase4_formatted_none_does_not_hide_a_limited_summary(
    checker: Any, tmp_path: Path, field: str
) -> None:
    research, delivery, lock = build_valid_phase2_run(
        tmp_path, analysis_validity="受限", actual_depth="quick"
    )
    report = research / "report.md"
    text = report.read_text(encoding="utf-8")
    text, replacements = re.subn(
        rf"^\| {re.escape(field)} \| .* \|$",
        f"| {field} | **无** |",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    assert replacements == 1
    report.write_text(text, encoding="utf-8")

    result, codes = _phase4_result(checker, research, delivery, lock)

    assert "quality.summary_mismatch" in codes
    assert result["mechanical_status"] == "FAIL"


def test_phase4_checker_has_no_business_model_or_method_whitelist(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(
        tmp_path, company_model="未列入六类的证据型自定义模型"
    )

    result = checker.check_run(research, delivery, lock)

    assert result["mechanical_status"] == "PASS", result["issues"]


def test_phase4_limited_conclusion_points_to_the_owner_of_the_gap(
    tmp_path: Path,
) -> None:
    research, _, _ = build_valid_phase2_run(
        tmp_path, information_completeness="不完整", actual_depth="quick"
    )
    w9 = (research / "work-packages/W9-thesis-counterevidence.md").read_text(
        encoding="utf-8"
    )

    assert "| 合成受限结论 | 合成资料或方法限制 | W5 | 取得合成关键输入 |" in w9


def test_decorated_report_model_label_breaks_the_identity_mirror(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    report = research / "report.md"
    report.write_text(
        report.read_text(encoding="utf-8").replace(
            "| 分析模型 | 合成模型标识（fixture） |",
            "| 分析模型 | 合成模型标识（fixture）（宿主实际暴露标识） |",
            1,
        ),
        encoding="utf-8",
    )

    result, codes = _phase4_result(checker, research, delivery, lock)

    assert "lock.model_id_mismatch" in codes
    assert result["mechanical_status"] == "FAIL"


def test_manifest_runtime_skill_hash_must_match_the_lock(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    _edit_json(
        research / "manifest.json",
        lambda manifest: manifest["run"]["runtime_skill"].__setitem__(
            "sha256", "1" * 64
        ),
    )

    result, codes = _phase4_result(checker, research, delivery, lock)

    assert "lock.runtime_skill_mismatch" in codes
    assert result["mechanical_status"] == "FAIL"


def _register_raw_fixture(
    research: Path,
    *,
    stem: str,
    content: str,
    media_format: str,
    work_package: str,
    status: str,
    failure: str | None = None,
) -> str:
    digest = hashlib.sha256(content.encode()).hexdigest()
    relative = (
        f"artifacts/raw/source-a/{stem}--source-a--20260630T190500+0800--"
        f"{digest[:8]}.{media_format}"
    )
    (research / relative).write_text(content, encoding="utf-8")

    def register(manifest: dict[str, Any]) -> None:
        entry: dict[str, Any] = {
            "path": relative,
            "type": "raw",
            "media_format": media_format,
            "work_package": work_package,
            "sha256": digest,
            "source_id": "source-a",
            "period_or_asof": "2026-06-30",
            "created_at": "2026-06-30T19:05:00+08:00",
            "schema_version": None,
            "inputs": [],
            "status": status,
        }
        if failure is not None:
            entry["failure"] = failure
        manifest["artifacts"].append(entry)

    _edit_json(research / "manifest.json", register)
    return relative


@pytest.mark.parametrize(
    ("status", "normalized", "expected_status"),
    (
        pytest.param("transport_error", None, "FAIL", id="failed-cannot-be-adopted"),
        pytest.param(
            "success", {"price": "10.00"}, "PASS", id="success-stays-adoptable"
        ),
    ),
)
def test_source_adapter_envelope_adoption_matches_status(
    checker: Any,
    tmp_path: Path,
    status: str,
    normalized: dict[str, str] | None,
    expected_status: str,
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    envelope = {
        "schema_version": "1.0",
        "adapter": "source_fetch",
        "domain": "quotes",
        "source_id": "source-a",
        "called": True,
        "disabled": False,
        "status": status,
        "source_metadata": {},
        "normalized": normalized,
        "equivalence": None,
        "raw_input_sha256": "0" * 64,
    }
    content = json.dumps(envelope, ensure_ascii=False) + "\n"
    _register_raw_fixture(
        research,
        stem="fetch",
        content=content,
        media_format="json",
        work_package="W2",
        status="adopted",
    )

    result, codes = _phase4_result(checker, research, delivery, lock)

    assert result["mechanical_status"] == expected_status, result["issues"]
    assert ("manifest.envelope_failure_adopted" in codes) == (expected_status == "FAIL")


def test_failed_canonical_tool_envelope_cannot_be_adopted_as_derived(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    envelope = {
        "tool": "market_series_metrics",
        "status": "failed",
        "error_type": "upstream_unavailable",
        "error_message": "fixture failure",
        "input_sha256": "0" * 64,
    }
    content = json.dumps(envelope, ensure_ascii=False) + "\n"
    (research / DERIVED_REL).write_text(content, encoding="utf-8")

    _edit_json(
        research / "manifest.json",
        lambda manifest: next(
            entry for entry in manifest["artifacts"] if entry["path"] == DERIVED_REL
        ).__setitem__("sha256", hashlib.sha256(content.encode()).hexdigest()),
    )

    result, codes = _phase4_result(checker, research, delivery, lock)

    assert "manifest.envelope_failure_adopted" in codes
    assert result["mechanical_status"] == "FAIL"


def _append_report_line(research: Path, line: str) -> None:
    report = research / "report.md"
    report.write_text(
        report.read_text(encoding="utf-8") + "\n" + line + "\n", encoding="utf-8"
    )


def test_report_cannot_reference_superseded_artifacts(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    _edit_json(
        research / "manifest.json",
        lambda manifest: manifest["artifacts"][2].__setitem__("status", "superseded"),
    )
    _append_report_line(research, f"过程产物见 `{DERIVED_REL}`（留档）。")

    result, codes = _phase4_result(checker, research, delivery, lock)

    assert "trace.report_reference_not_adopted" in codes
    assert result["mechanical_status"] == "FAIL"


def test_report_cannot_reference_unregistered_artifacts(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    ghost = "artifacts/raw/source-a/ghost--source-a--20260630T190500+0800--deadbeef.json"
    _append_report_line(research, f"另见 `{ghost}`。")

    result, codes = _phase4_result(checker, research, delivery, lock)

    assert "trace.report_reference_not_adopted" in codes
    assert result["mechanical_status"] == "FAIL"


def test_dangling_evidence_references_are_rejected(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    _append_report_line(research, "补充证据：E9（不存在的证据编号）。")
    w3 = research / "work-packages/W3-industry-competition.md"
    w3.write_text(
        w3.read_text(encoding="utf-8") + "\n另见 F7。\n", encoding="utf-8"
    )

    result, codes = _phase4_result(checker, research, delivery, lock)
    targets = [
        i["path"]
        for i in result["issues"]
        if i["code"] == "trace.dangling_evidence_reference"
    ]

    assert "report.md" in targets
    assert "work-packages/W3-industry-competition.md" in targets
    assert result["mechanical_status"] == "FAIL"


def _move_fixture_derived_compute_to_owner(research: Path, owner: str) -> None:
    relocated = DERIVED_REL.replace("artifacts/derived/W5/", f"artifacts/derived/{owner}/")
    (research / relocated).parent.mkdir(parents=True)
    (research / DERIVED_REL).rename(research / relocated)
    evidence = research / "evidence.md"
    evidence.write_text(
        evidence.read_text(encoding="utf-8").replace(DERIVED_REL, relocated),
        encoding="utf-8",
    )

    def move(manifest: dict[str, Any]) -> None:
        for entry in manifest["artifacts"]:
            if entry["path"] == DERIVED_REL:
                entry["path"] = relocated
                entry["work_package"] = owner
            elif entry["type"] == "script":
                entry["status"] = "not_adopted"

    _edit_json(research / "manifest.json", move)


def test_report_chapter_seven_recomputation_claim_requires_w7_compute_artifact(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    _move_fixture_derived_compute_to_owner(research, "W8")
    report = research / "report.md"
    report.write_text(
        report.read_text(encoding="utf-8").replace(
            "## 7. 估值与隐含预期",
            "## 7. 估值与隐含预期\n\nW7 canonical 复算已执行。",
            1,
        ),
        encoding="utf-8",
    )

    result, codes = _phase4_result(checker, research, delivery, lock)

    assert "trace.unbacked_recomputation_claim" in codes
    assert result["mechanical_status"] == "FAIL"


def test_report_chapter_seven_prefix_execution_claim_requires_w7_compute_artifact(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    _move_fixture_derived_compute_to_owner(research, "W8")
    report = research / "report.md"
    report.write_text(
        report.read_text(encoding="utf-8").replace(
            "## 7. 估值与隐含预期",
            "## 7. 估值与隐含预期\n\n已执行 canonical 复算。",
            1,
        ),
        encoding="utf-8",
    )

    result, codes = _phase4_result(checker, research, delivery, lock)

    assert "trace.unbacked_recomputation_claim" in codes
    assert result["mechanical_status"] == "FAIL"


def test_w7_recomputation_claim_needs_w7_adopted_compute_artifact(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    _move_fixture_derived_compute_to_owner(research, "W8")
    w7 = research / "work-packages/W7-valuation-expectations.md"
    w7.write_text(
        w7.read_text(encoding="utf-8").replace(
            "| 合成必需项 |",
            "| PE/PB canonical 复算 |",
            1,
        ),
        encoding="utf-8",
    )

    result, codes = _phase4_result(checker, research, delivery, lock)

    assert "trace.unbacked_recomputation_claim" in codes
    assert result["mechanical_status"] == "FAIL"


def test_w7_required_item_recomputation_claim_accepts_w7_compute_artifact(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    _move_fixture_derived_compute_to_owner(research, "W7")
    w7 = research / "work-packages/W7-valuation-expectations.md"
    w7.write_text(
        w7.read_text(encoding="utf-8").replace(
            "| 合成必需项 |",
            "| PE/PB canonical 复算 |",
            1,
        ),
        encoding="utf-8",
    )

    result, codes = _phase4_result(checker, research, delivery, lock)

    assert "trace.unbacked_recomputation_claim" not in codes
    assert result["mechanical_status"] == "PASS", result["issues"]


def test_unmet_required_item_does_not_claim_recomputation_was_executed(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    _move_fixture_derived_compute_to_owner(research, "W8")
    w7 = research / "work-packages/W7-valuation-expectations.md"
    w7.write_text(
        w7.read_text(encoding="utf-8").replace(
            "| 合成必需项 | standard 共通底线 | 有 | E1，合成查询范围 | 满足：无额外限制 |",
            "| PE/PB canonical 复算 | quick | 未取得 | 输入缺失 | 未满足：不输出估值 |",
            1,
        ),
        encoding="utf-8",
    )

    _, codes = _phase4_result(checker, research, delivery, lock)

    assert "trace.unbacked_recomputation_claim" not in codes


def test_w10_summary_and_ordinary_canonical_rule_do_not_claim_execution(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    for relative, line in (
        (
            "work-packages/W10-report-review.md",
            "汇总 W5/W7/W8 确定性计算链。",
        ),
        (
            "work-packages/W7-valuation-expectations.md",
            "估值判断按 canonical 规则执行。",
        ),
    ):
        path = research / relative
        path.write_text(path.read_text(encoding="utf-8") + "\n" + line + "\n", encoding="utf-8")

    result, codes = _phase4_result(checker, research, delivery, lock)

    assert "trace.unbacked_recomputation_claim" not in codes
    assert result["mechanical_status"] == "PASS", result["issues"]


def test_report_chapter_seven_recomputation_claim_accepts_w7_compute_artifact(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    _move_fixture_derived_compute_to_owner(research, "W7")
    report = research / "report.md"
    report.write_text(
        report.read_text(encoding="utf-8").replace(
            "## 7. 估值与隐含预期",
            "## 7. 估值与隐含预期\n\nW7 canonical 复算已执行。",
            1,
        ),
        encoding="utf-8",
    )

    result, codes = _phase4_result(checker, research, delivery, lock)

    assert "trace.unbacked_recomputation_claim" not in codes
    assert result["mechanical_status"] == "PASS", result["issues"]


@pytest.mark.parametrize("relative", ("checkpoint.md", "work-packages/W0-task-framing.md"))
def test_data_mode_with_user_qualification_is_still_mirrored(
    checker: Any, tmp_path: Path, relative: str
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    report = research / "report.md"
    report.write_text(
        report.read_text(encoding="utf-8").replace(
            "| 数据模式 | public |", "| 数据模式 | authorized |", 1
        ),
        encoding="utf-8",
    )
    _edit_json(
        research / "manifest.json",
        lambda manifest: manifest["run"].__setitem__("data_mode", "authorized"),
    )
    path = research / relative
    path.write_text(
        path.read_text(encoding="utf-8") + "\n数据模式：public（用户指定）\n",
        encoding="utf-8",
    )

    result, codes = _phase4_result(checker, research, delivery, lock)

    assert "quality.data_mode_mismatch" in codes
    assert result["mechanical_status"] == "FAIL"


def test_data_mode_must_match_report_manifest_checkpoint_and_work_packages(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    report = research / "report.md"
    report.write_text(
        report.read_text(encoding="utf-8").replace(
            "| 数据模式 | public |", "| 数据模式 | authorized |", 1
        ),
        encoding="utf-8",
    )
    _edit_json(
        research / "manifest.json",
        lambda manifest: manifest["run"].__setitem__("data_mode", "authorized"),
    )
    for relative in ("checkpoint.md", "work-packages/W0-task-framing.md"):
        path = research / relative
        path.write_text(
            path.read_text(encoding="utf-8") + "\n数据模式：public\n",
            encoding="utf-8",
        )

    result, codes = _phase4_result(checker, research, delivery, lock)

    assert "quality.data_mode_mismatch" in codes
    assert result["mechanical_status"] == "FAIL"


W5_GAP_ROW = (
    "| 合成必需项 | standard 共通底线 | 未取得 | E1，合成查询范围 | "
    "未满足：限制合成结论 |"
)


def _rewrite_w5_gap_row(research: Path, cell: str) -> None:
    path = research / "work-packages/W5-financial-validation.md"
    text = path.read_text(encoding="utf-8")
    assert W5_GAP_ROW in text
    replaced = text.replace(
        W5_GAP_ROW,
        W5_GAP_ROW.replace("E1，合成查询范围", cell),
        1,
    )
    path.write_text(replaced, encoding="utf-8")


def _claim_candidate_warnings(result: dict[str, Any]) -> list[dict[str, str]]:
    return [
        warning
        for warning in result["warnings"]
        if warning["code"] == "trace.execution_claim_record_candidate"
    ]


def test_probe_failure_claim_without_record_is_surfaced_as_candidate(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(
        tmp_path, actual_depth="quick", information_completeness="不完整"
    )
    _rewrite_w5_gap_row(
        research,
        "上交所 query 接口返回空/被拒（2026-09-05 多次尝试），官方字段无可得路径",
    )

    result, _ = _phase4_result(checker, research, delivery, lock)
    candidates = _claim_candidate_warnings(result)

    assert len(candidates) == 1
    assert candidates[0]["path"] == "work-packages/W5-financial-validation.md"
    assert "接口返回空" in candidates[0]["message"]
    assert result["mechanical_status"] == "PASS"


def test_probe_failure_claim_closed_by_evidence_record_is_not_flagged(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(
        tmp_path, actual_depth="quick", information_completeness="不完整"
    )
    _rewrite_w5_gap_row(research, "合成接口返回空（E1）")

    result, _ = _phase4_result(checker, research, delivery, lock)

    assert _claim_candidate_warnings(result) == []
    assert result["mechanical_status"] == "PASS"


def test_probe_failure_claim_closed_by_saved_error_output_is_not_flagged(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(
        tmp_path, actual_depth="quick", information_completeness="不完整"
    )
    _rewrite_w5_gap_row(research, f"接口返回空，原始响应留档 `{RAW_REL}`")

    result, _ = _phase4_result(checker, research, delivery, lock)

    assert _claim_candidate_warnings(result) == []
    assert result["mechanical_status"] == "PASS"


def test_honest_no_record_wording_is_not_flagged(checker: Any, tmp_path: Path) -> None:
    research, delivery, lock = build_valid_phase2_run(
        tmp_path, actual_depth="quick", information_completeness="不完整"
    )
    _rewrite_w5_gap_row(
        research,
        "本次未取得该字段，也没有可核验的接口探测记录，不能判断接口是否可用。",
    )

    result, _ = _phase4_result(checker, research, delivery, lock)

    assert _claim_candidate_warnings(result) == []
    assert result["mechanical_status"] == "PASS"


def test_report_coverage_probe_claim_is_surfaced_as_candidate(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    report = research / "report.md"
    report.write_text(
        report.read_text(encoding="utf-8").replace(
            "| 合成域 | 有 | 合成来源 | 2026-06-30 | 不适用 | 无 |",
            "| 合成域 | 有 | 合成来源 | 2026-06-30 | 上交所官方接口空返回 | 无 |",
            1,
        ),
        encoding="utf-8",
    )

    result, _ = _phase4_result(checker, research, delivery, lock)
    candidates = _claim_candidate_warnings(result)

    assert len(candidates) == 1
    assert candidates[0]["path"] == "report.md"
    assert result["mechanical_status"] == "PASS"


def _register_failed_probe_record(research: Path) -> str:
    content = "上游 504 响应留档（fixture）\n"
    return _register_raw_fixture(
        research,
        stem="sse-probe",
        content=content,
        media_format="txt",
        work_package="W8",
        status="failed",
        failure="真实执行失败：上交所动态接口返回空（fixture）",
    )


@pytest.mark.parametrize(
    "cell",
    (
        "接口请求结果不能判断（返回空或被拒均未见留档）。",
        "没有可核验的接口探测记录，接口返回空/被拒也未见留档。",
    ),
)
def test_partial_honest_wording_is_still_surfaced(
    checker: Any, tmp_path: Path, cell: str
) -> None:
    research, delivery, lock = build_valid_phase2_run(
        tmp_path, actual_depth="quick", information_completeness="不完整"
    )
    _rewrite_w5_gap_row(research, cell)

    result, _ = _phase4_result(checker, research, delivery, lock)

    assert len(_claim_candidate_warnings(result)) == 1
    assert result["mechanical_status"] == "PASS"


@pytest.mark.parametrize(
    "cell",
    (
        "来源原文提到接口，未见相关内容。",
        "尚未尝试任何接口请求。",
    ),
)
def test_probe_mention_without_outcome_is_not_flagged(
    checker: Any, tmp_path: Path, cell: str
) -> None:
    research, delivery, lock = build_valid_phase2_run(
        tmp_path, actual_depth="quick", information_completeness="不完整"
    )
    _rewrite_w5_gap_row(research, cell)

    result, _ = _phase4_result(checker, research, delivery, lock)

    assert _claim_candidate_warnings(result) == []
    assert result["mechanical_status"] == "PASS"


def test_probe_claim_citing_unresolvable_evidence_is_surfaced(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(
        tmp_path, actual_depth="quick", information_completeness="不完整"
    )
    _rewrite_w5_gap_row(research, "合成接口返回空（E9）")

    result, codes = _phase4_result(checker, research, delivery, lock)

    assert len(_claim_candidate_warnings(result)) == 1
    assert "trace.dangling_evidence_reference" in codes


def test_failed_envelope_record_closes_probe_claim(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(
        tmp_path, actual_depth="quick", information_completeness="不完整"
    )
    record_relative = _register_failed_probe_record(research)
    _rewrite_w5_gap_row(
        research,
        f"上交所动态接口返回空/被拒（多次尝试），原始响应留档 `{record_relative}`",
    )

    result, _ = _phase4_result(checker, research, delivery, lock)

    assert _claim_candidate_warnings(result) == []
    assert result["mechanical_status"] == "PASS"


def test_propagated_probe_claim_copies_surface_per_file(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(
        tmp_path, actual_depth="quick", information_completeness="不完整"
    )
    claim = "上交所 query 接口返回空/被拒（多次尝试）"
    _rewrite_w5_gap_row(research, claim)
    evidence = research / "evidence.md"
    evidence.write_text(
        evidence.read_text(encoding="utf-8") + f"\n- {claim}。\n",
        encoding="utf-8",
    )
    evidence.write_text(
        evidence.read_text(encoding="utf-8") + f"\n- 接口返回空，留档 `{RAW_REL}`。\n",
        encoding="utf-8",
    )
    report = research / "report.md"
    report.write_text(
        report.read_text(encoding="utf-8").replace(
            "| 合成域 | 有 | 合成来源 | 2026-06-30 | 不适用 | 无 |",
            f"| 合成域 | 有 | 合成来源 | 2026-06-30 | {claim} | 无 |",
            1,
        ),
        encoding="utf-8",
    )

    result, _ = _phase4_result(checker, research, delivery, lock)
    candidates = _claim_candidate_warnings(result)

    assert {candidate["path"] for candidate in candidates} == {
        "report.md",
        "evidence.md",
        "work-packages/W5-financial-validation.md",
    }
    assert result["mechanical_status"] == "PASS"


def _register_failed_log_record(research: Path) -> str:
    content = "curl exit 52 upstream empty（fixture 失败日志）\n"
    return _register_raw_fixture(
        research,
        stem="kline-failure",
        content=content,
        media_format="txt",
        work_package="W8",
        status="failed",
        failure="真实执行失败：K 线接口返回空（fixture）",
    )


def test_report_source_table_failed_log_reference_is_a_legitimate_record(
    checker: Any, tmp_path: Path
) -> None:
    """Integration positive: the report's source-coverage table may cite a
    registered failed log as the record behind an 'interface returned empty'
    process disclosure — no candidate warning, no provenance FAIL."""
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    record_relative = _register_failed_log_record(research)
    report = research / "report.md"
    report.write_text(
        report.read_text(encoding="utf-8").replace(
            "| 合成域 | 有 | 合成来源 | 2026-06-30 | 不适用 | 无 |",
            f"| 合成域 | 有 | 合成来源 | 2026-06-30 | 接口返回空，留档 `{record_relative}` | 无 |",
            1,
        ),
        encoding="utf-8",
    )

    result, codes = _phase4_result(checker, research, delivery, lock)

    assert "trace.report_reference_not_adopted" not in codes
    assert _claim_candidate_warnings(result) == []
    assert result["mechanical_status"] == "PASS"


def test_report_reference_to_not_adopted_artifact_still_fails(
    checker: Any, tmp_path: Path
) -> None:
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    _edit_json(
        research / "manifest.json",
        lambda manifest: manifest["artifacts"][2].__setitem__("status", "not_adopted"),
    )
    _append_report_line(research, f"另见 `{DERIVED_REL}`（留档）。")

    result, codes = _phase4_result(checker, research, delivery, lock)

    assert "trace.report_reference_not_adopted" in codes
    assert result["mechanical_status"] == "FAIL"


@pytest.mark.parametrize(
    ("target", "replacement", "allowed"),
    (
        pytest.param(
            "| 合成方法 | 合成输入 | 2026-06-30 | 结果 | 预期 | 限制 |",
            "| 合成方法 | 接口返回空，留档 `{record}` | 2026-06-30 | 结果 | 预期 | 限制 |",
            False,
            id="business-table-blocked",
        ),
        pytest.param(
            "## 5. 财务验证与经营质量",
            "## 5. 财务验证与经营质量\n\n公司营业收入为 100 亿元，依据：`{record}`。",
            False,
            id="business-prose-blocked",
        ),
        pytest.param(
            "## 11. 数据覆盖、缺口、冲突与来源",
            (
                "## 11. 数据覆盖、缺口、冲突与来源\n\n"
                "上交所动态接口返回空，原始响应留档 `{record}`，该来源本期不可用。"
            ),
            True,
            id="chapter-11-gap-prose-allowed",
        ),
    ),
)
def test_failed_record_report_reference_depends_on_chapter(
    checker: Any,
    tmp_path: Path,
    target: str,
    replacement: str,
    allowed: bool,
) -> None:
    """Failed records describe process gaps in chapter 11 only, not facts."""
    research, delivery, lock = build_valid_phase2_run(tmp_path)
    record_relative = _register_failed_log_record(research)
    report = research / "report.md"
    report.write_text(
        report.read_text(encoding="utf-8").replace(
            target,
            replacement.format(record=record_relative),
            1,
        ),
        encoding="utf-8",
    )

    result, codes = _phase4_result(checker, research, delivery, lock)

    assert ("trace.report_reference_not_adopted" not in codes) == allowed
    expected_status = "PASS" if allowed else "FAIL"
    assert result["mechanical_status"] == expected_status, result["issues"]
