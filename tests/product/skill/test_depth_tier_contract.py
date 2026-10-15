"""Depth-tier report-contract tests: quick-data / research profiles and the
legacy (absent-profile) contract for ``check-run-artifacts.py``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.product.skill.deterministic_tool_loader import load_script
from tests.product.skill.phase2_run_fixture import (
    RAW_REL,
    build_valid_phase2_run,
)


@pytest.mark.parametrize(
    ("depth", "profile"),
    [
        ("quick", "quick-data"),
        ("standard", "research"),
        ("standard", None),
    ],
)
def test_profile_contract(tmp_path: Path, depth: str, profile: str | None) -> None:
    paths = build_valid_phase2_run(
        tmp_path,
        requested_depth=depth,
        report_profile=profile,
    )
    result = load_script("check-run-artifacts.py").check_run(*paths)
    assert result["mechanical_status"] == "PASS", result
    if profile == "quick-data":
        assert not (paths[0] / "work-packages/W9-thesis-counterevidence.md").exists()


@pytest.mark.parametrize(
    "profile, depth",
    [
        ("quick-data", "standard"),
        ("research", "quick"),
        ("unexpected", "quick"),
        (None, "quick"),
    ],
)
def test_explicit_invalid_profile_is_rejected(profile: object, depth: str) -> None:
    checker = load_script("check-run-artifacts.py")
    with pytest.raises(ValueError):
        checker._report_profile(
            {
                "run": {
                    "requested_depth": depth,
                    "report_profile": profile,
                }
            }
        )


def test_absent_profile_keeps_legacy_contract() -> None:
    checker = load_script("check-run-artifacts.py")
    assert checker._report_profile({"run": {"requested_depth": "quick"}}) == "legacy"


def _quick_run(tmp_path: Path) -> tuple[Path, Path, Path]:
    return build_valid_phase2_run(tmp_path, requested_depth="quick", report_profile="quick-data")


def _research_run(tmp_path: Path) -> tuple[Path, Path, Path]:
    return build_valid_phase2_run(tmp_path, requested_depth="standard", report_profile="research")


def test_quick_missing_fact_package_fails(tmp_path: Path) -> None:
    research, delivery, lock = _quick_run(tmp_path)
    (research / "work-packages/W5-financial-validation.md").unlink()
    result = load_script("check-run-artifacts.py").check_run(research, delivery, lock)
    codes = {issue["code"] for issue in result["issues"]}
    assert "missing.required_file" in codes
    assert any(
        issue["code"] == "missing.required_file"
        and issue["path"] == "work-packages/W5-financial-validation.md"
        for issue in result["issues"]
    )
    assert result["mechanical_status"] == "FAIL"


def test_research_missing_w9_fails(tmp_path: Path) -> None:
    research, delivery, lock = _research_run(tmp_path)
    (research / "work-packages/W9-thesis-counterevidence.md").unlink()
    result = load_script("check-run-artifacts.py").check_run(research, delivery, lock)
    assert any(
        issue["code"] == "missing.required_file"
        and issue["path"] == "work-packages/W9-thesis-counterevidence.md"
        for issue in result["issues"]
    )
    assert result["mechanical_status"] == "FAIL"


@pytest.mark.parametrize(
    "mutation",
    ["invalid-profile", "standard-claims-quick-data"],
)
def test_invalid_profile_is_a_manifest_issue_not_legacy(
    tmp_path: Path, mutation: str
) -> None:
    profile = "unexpected" if mutation == "invalid-profile" else "quick-data"
    research, delivery, lock = build_valid_phase2_run(
        tmp_path, requested_depth="standard", report_profile=profile
    )
    result = load_script("check-run-artifacts.py").check_run(research, delivery, lock)
    codes = {issue["code"] for issue in result["issues"]}
    assert "manifest.invalid_report_profile" in codes
    assert result["mechanical_status"] == "FAIL"


@pytest.mark.parametrize(
    "mutation",
    ["swap-chapter-titles", "drop-chapter-heading"],
)
def test_quick_chapter_mutations_fail(tmp_path: Path, mutation: str) -> None:
    research, delivery, lock = _quick_run(tmp_path)
    report = research / "report.md"
    text = report.read_text(encoding="utf-8")
    if mutation == "swap-chapter-titles":
        text = text.replace("## 3. 行情与技术指标", "## 3. 财务报表与指标", 1)
        text = text.replace("## 4. 财务报表与指标", "## 4. 行情与技术指标", 1)
    else:
        text = text.replace("## 5. 业务与经营构成\n", "", 1)
    report.write_text(text, encoding="utf-8")
    result = load_script("check-run-artifacts.py").check_run(research, delivery, lock)
    codes = {issue["code"] for issue in result["issues"]}
    assert "report.chapter_order" in codes
    assert result["mechanical_status"] == "FAIL"


def test_quick_quality_summary_mismatch_fails(tmp_path: Path) -> None:
    research, delivery, lock = _quick_run(tmp_path)
    w10 = research / "work-packages/W10-report-review.md"
    w10.write_text(
        w10.read_text(encoding="utf-8").replace(
            "| 信息完整性 | 完整 |", "| 信息完整性 | 不完整 |", 1
        ),
        encoding="utf-8",
    )
    result = load_script("check-run-artifacts.py").check_run(research, delivery, lock)
    codes = {issue["code"] for issue in result["issues"]}
    assert "quality.summary_mismatch" in codes
    assert result["mechanical_status"] == "FAIL"


@pytest.mark.parametrize(
    "reference",
    [
        "artifacts/normalized/W5/nonexistent.json",
        "artifacts/../outside.json",
    ],
)
def test_quick_report_cannot_cite_unregistered_or_escaping_detail_paths(
    tmp_path: Path, reference: str
) -> None:
    research, delivery, lock = _quick_run(tmp_path)
    report = research / "report.md"
    report.write_text(
        report.read_text(encoding="utf-8").replace(
            "## 4. 财务报表与指标",
            f"## 4. 财务报表与指标\n\n明细见 `{reference}`。",
            1,
        ),
        encoding="utf-8",
    )
    result = load_script("check-run-artifacts.py").check_run(research, delivery, lock)
    codes = {issue["code"] for issue in result["issues"]}
    assert "trace.report_reference_not_adopted" in codes
    assert result["mechanical_status"] == "FAIL"


def test_quick_report_cannot_use_failed_source_as_data(tmp_path: Path) -> None:
    research, delivery, lock = _quick_run(tmp_path)
    manifest_path = research / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest["artifacts"]:
        if entry["path"] == RAW_REL:
            entry["status"] = "failed"
            entry["failure"] = "合成失败（fixture 变异）"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report = research / "report.md"
    report.write_text(
        report.read_text(encoding="utf-8").replace(
            "## 3. 行情与技术指标",
            f"## 3. 行情与技术指标\n\n原始输入 `{RAW_REL}`。",
            1,
        ),
        encoding="utf-8",
    )
    result = load_script("check-run-artifacts.py").check_run(research, delivery, lock)
    codes = {issue["code"] for issue in result["issues"]}
    assert "trace.report_reference_not_adopted" in codes
    assert result["mechanical_status"] == "FAIL"


def test_sequential_checks_of_different_profiles_do_not_pollute_global_state(
    tmp_path: Path,
) -> None:
    checker: Any = load_script("check-run-artifacts.py")
    for run in (
        _quick_run(tmp_path / "quick"),
        _research_run(tmp_path / "research"),
        build_valid_phase2_run(tmp_path / "legacy"),
    ):
        assert checker.check_run(*run)["mechanical_status"] == "PASS"
    assert checker.CHAPTERS[0] == "任务与时点"
    assert checker.QUICK_CHAPTERS[0] == "任务与时点"
    assert len(checker.CHAPTERS) == 12
    assert len(checker.QUICK_CHAPTERS) == 9
