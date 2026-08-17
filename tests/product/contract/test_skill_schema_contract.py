import hashlib
import json
from pathlib import Path

from hetu_stock.skill import validate_skill_package
from hetu_stock.skill.work_packages import load_work_package

ROOT = Path("skills/hetu-stock-analysis")


def test_canonical_skill_schema_contract_is_valid() -> None:
    validate_skill_package(ROOT, require_manifest=True)


def test_canonical_manifest_matches_every_installed_resource() -> None:
    manifest = json.loads(ROOT.joinpath("MANIFEST.json").read_text(encoding="utf-8"))
    actual = {
        path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in ROOT.rglob("*")
        if path.is_file() and path.name != "MANIFEST.json"
    }
    assert manifest == {"files": dict(sorted(actual.items()))}


def test_canonical_work_package_files_parse_as_exact_core_set() -> None:
    core = ROOT / "references" / "work-packages" / "core"
    specs = [load_work_package(path) for path in sorted(core.glob("*.md"))]
    assert {spec.id for spec in specs} == {f"W{index}" for index in range(11)}
    assert len(specs) == 11
    assert all(spec.kind == "core" and spec.coverage_role == "required" for spec in specs)


def test_canonical_package_has_no_phase_one_runtime_resources() -> None:
    for relative in (
        "references/controller.md",
        "references/workflow.md",
        "references/pause-resume.md",
        "references/stages",
        "references/examples/stage-result.example.json",
        "references/schema",
        "templates/report.md.j2",
    ):
        assert not ROOT.joinpath(relative).exists()
