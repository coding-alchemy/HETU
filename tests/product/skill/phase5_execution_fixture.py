"""Phase-5 stage-03 execution fixture: task relations, attempts and reuse.

Builds on the valid phase-2 synthetic run and injects the stage-03 scenario
shape the checker must accept (and its mutations must reject):

- two fetch attempts of one dataset (first ``superseded``, later ``adopted``);
- a genuinely failed script kept as ``failed`` with a failure reason and a
  non-zero exit status;
- a cross-task copied raw artifact carrying full five-key ``provenance``
  (identity comes from ``tests/product/fixtures/phase5_execution/``);
- multiple supporting originals from different sources, all adopted;
- ``manifest.run`` conditional fields (``task_id``, ``parent_task_id``,
  ``reuse_previous_task_data``) plus their disclosure in checkpoint/report.

All content is fictional; the fixture proves structure only.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from tests.product.skill.phase2_run_fixture import (
    DERIVED_REL,
    research_tree_sha256,
    sha256_file,
)

FIXTURE_DIR = (
    Path(__file__).resolve().parents[1] / "fixtures" / "phase5_execution"
)

TASK_ID = "task-synth-2026-0002"
PARENT_TASK_ID = "task-synth-2025-0001"
SOURCE_RUN_ID = "合成公司-000001.SZ-standard-20250601T100000+0800"
CREATED = "2026-06-30T19:05:00+08:00"
ACQUIRED = "2025-06-01T10:05:00+08:00"


def _copy_fixture(name: str, target: Path, research: Path) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURE_DIR / name, target)
    return target.relative_to(research).as_posix()


def _hash8(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:8]


def build_phase5_execution_run(
    root: Path,
    *,
    record_run_fields: bool = True,
    reuse_previous_task_data: bool = True,
    with_provenance: bool = True,
) -> tuple[Path, Path, Path]:
    """Return (research_root, delivery_message, lock_record).

    Built on the valid phase-2 run (imported lazily to keep this module
    importable when the phase-2 fixture evolves).
    """
    from tests.product.skill.phase2_run_fixture import build_valid_phase2_run

    research, delivery, lock_record = build_valid_phase2_run(root)
    raw_dir = research / "artifacts" / "raw"
    scripts_dir = research / "artifacts" / "scripts" / "W5"

    attempt1_hash = _hash8(FIXTURE_DIR / "quotes-attempt1.json")
    attempt1 = raw_dir / "source-a" / (
        f"quote--source-a--20260630T185500+0800--{attempt1_hash}.json"
    )
    attempt1_rel = _copy_fixture("quotes-attempt1.json", attempt1, research)
    supporting_b_hash = _hash8(FIXTURE_DIR / "supporting-original-b.json")
    supporting_b = raw_dir / "source-b" / (
        f"quote--source-b--20260630T190500+0800--{supporting_b_hash}.json"
    )
    supporting_b_rel = _copy_fixture("supporting-original-b.json", supporting_b, research)
    supporting_c_hash = _hash8(FIXTURE_DIR / "supporting-original-c.json")
    supporting_c = raw_dir / "source-c" / (
        f"volume--source-c--20260630T190500+0800--{supporting_c_hash}.json"
    )
    supporting_c_rel = _copy_fixture("supporting-original-c.json", supporting_c, research)
    copied_hash = _hash8(FIXTURE_DIR / "annual-report-copy.json")
    copied = raw_dir / "cninfo" / (
        f"annual-report-copy--cninfo--2025-1231--{copied_hash}.json"
    )
    copied_rel = _copy_fixture("annual-report-copy.json", copied, research)
    failed_hash = _hash8(FIXTURE_DIR / "failed-recompute.py")
    failed_script = scripts_dir / (
        f"recompute--W5--20260630T190600+0800--{failed_hash}.py"
    )
    failed_script_rel = _copy_fixture("failed-recompute.py", failed_script, research)

    fragment = json.loads(
        (FIXTURE_DIR / "source-task-fragment.json").read_text(encoding="utf-8")
    )

    copied_entry: dict[str, object] = {
        "path": copied_rel,
        "type": "raw",
        "media_format": "json",
        "work_package": "W5",
        "sha256": sha256_file(copied),
        "source_id": "cninfo",
        "period_or_asof": "2025-12-31",
        "created_at": CREATED,
        "schema_version": None,
        "inputs": [],
        "status": "adopted",
    }
    if with_provenance:
        copied_entry["provenance"] = {
            "source_task_id": fragment["source_task_id"],
            "source_artifact": fragment["source_artifact"],
            "original_source": fragment["original_source"],
            "original_acquired_at": ACQUIRED,
            "copied_at": CREATED,
        }

    extra_entries = [
        {
            "path": attempt1_rel,
            "type": "raw",
            "media_format": "json",
            "work_package": "W5",
            "sha256": sha256_file(attempt1),
            "source_id": "source-a",
            "period_or_asof": "2026-06-30",
            "created_at": "2026-06-30T18:55:00+08:00",
            "schema_version": None,
            "inputs": [],
            "status": "superseded",
        },
        {
            "path": supporting_b_rel,
            "type": "raw",
            "media_format": "json",
            "work_package": "W5",
            "sha256": sha256_file(supporting_b),
            "source_id": "source-b",
            "period_or_asof": "2026-06-30",
            "created_at": CREATED,
            "schema_version": None,
            "inputs": [],
            "status": "adopted",
        },
        {
            "path": supporting_c_rel,
            "type": "raw",
            "media_format": "json",
            "work_package": "W8",
            "sha256": sha256_file(supporting_c),
            "source_id": "source-c",
            "period_or_asof": "2026-06-30",
            "created_at": CREATED,
            "schema_version": None,
            "inputs": [],
            "status": "adopted",
        },
        copied_entry,
        {
            "path": failed_script_rel,
            "type": "script",
            "media_format": "py",
            "work_package": "W5",
            "sha256": sha256_file(failed_script),
            "source_id": None,
            "period_or_asof": "not_applicable",
            "created_at": "2026-06-30T19:06:00+08:00",
            "schema_version": "1.0",
            "inputs": [],
            "status": "failed",
            "failure": "合成执行失败：依赖输入不可解析（fixture）",
            "script": {
                "purpose": "合成重算第一次尝试（fixture，未成功）",
                "safe_call": f"python {failed_script_rel}",
                "dependencies": "标准库",
                "environment": "合成环境",
                "input": [],
                "output": DERIVED_REL,
                "exit_status": 1,
                "executed_at": "2026-06-30T19:06:00+08:00",
            },
        },
    ]

    manifest_path = research / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"].extend(extra_entries)
    if record_run_fields:
        manifest["run"]["task_id"] = TASK_ID
        manifest["run"]["parent_task_id"] = PARENT_TASK_ID
        manifest["run"]["reuse_previous_task_data"] = reuse_previous_task_data
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    if record_run_fields:
        disclosure = (
            f"任务标识：{TASK_ID}（父任务：{PARENT_TASK_ID}）；"
            f"reuse_previous_task_data={str(reuse_previous_task_data).lower()}。\n"
        )
        report_path = research / "report.md"
        report_path.write_text(
            report_path.read_text(encoding="utf-8") + "\n" + disclosure,
            encoding="utf-8",
        )
        checkpoint_path = research / "checkpoint.md"
        checkpoint_path.write_text(
            checkpoint_path.read_text(encoding="utf-8")
            + "\n任务卡补充：task_id="
            + TASK_ID
            + "；parent_task_id="
            + PARENT_TASK_ID
            + "；reuse_previous_task_data="
            + str(reuse_previous_task_data).lower()
            + "。\n",
            encoding="utf-8",
        )

    # the lock must bind the tree as it now stands (injections included)
    lock = json.loads(lock_record.read_text(encoding="utf-8"))
    lock["research_root"]["tree_sha256"] = research_tree_sha256(research)
    lock["report"]["sha256"] = sha256_file(research / "report.md")
    lock_record.write_text(
        json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return research, delivery, lock_record
