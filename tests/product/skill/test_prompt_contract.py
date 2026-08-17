import re
from pathlib import Path

from hetu_stock.skill.work_packages import load_work_package
from tests.product.skill.contract_fixtures import EXPECTED

ROOT = Path("skills/hetu-stock-analysis")
RUNTIME_REFERENCES = (
    "references/orchestration.md",
    "references/evidence-rules.md",
    "references/checkpoint.md",
    "references/recovery.md",
    "references/report-guidance.md",
    "references/host-tools.md",
    "references/work-packages/catalog.md",
)
FORBIDDEN = (
    "references/controller.md",
    "references/workflow.md",
    "references/pause-resume.md",
    "references/examples/stage-result.example.json",
    "templates/report.md.j2",
)
PACKAGE_FILES = {
    "W0": "W0-task-framing.md",
    "W1": "W1-subject-verification.md",
    "W2": "W2-incremental-events.md",
    "W3": "W3-industry-competition.md",
    "W4": "W4-business-governance.md",
    "W5": "W5-financial-validation.md",
    "W6": "W6-forecast-scenarios.md",
    "W7": "W7-valuation-expectations.md",
    "W8": "W8-market-signals.md",
    "W9": "W9-thesis-counterevidence.md",
    "W10": "W10-report-review.md",
}


def _work_package_path(package_id: str) -> Path:
    return ROOT / "references" / "work-packages" / "core" / PACKAGE_FILES[package_id]


def _section(text: str, heading: str) -> str:
    start = text.index(heading) + len(heading)
    tail = text[start:]
    boundary = tail.find("\n## ")
    return tail if boundary == -1 else tail[:boundary]


def test_skill_has_direct_resolving_links_to_every_runtime_resource() -> None:
    skill = ROOT.joinpath("SKILL.md").read_text(encoding="utf-8")
    for relative in RUNTIME_REFERENCES:
        assert f"]({relative})" in skill
        assert ROOT.joinpath(relative).is_file()
    for package_id, filename in PACKAGE_FILES.items():
        matches = list(ROOT.glob(f"references/work-packages/core/{package_id}-*.md"))
        assert matches == [_work_package_path(package_id)]
        relative = f"references/work-packages/core/{filename}"
        assert f"]({relative})" in skill


def test_phase_one_protocol_is_absent_from_canonical_package() -> None:
    assert all(not ROOT.joinpath(relative).exists() for relative in FORBIDDEN)
    assert not ROOT.joinpath("references/stages").exists()
    assert not ROOT.joinpath("references/schema").exists()
    corpus = "\n".join(path.read_text(encoding="utf-8") for path in ROOT.rglob("*.md"))
    for text in (
        "StageResult",
        "RunState",
        "run submit",
        "hetu-stock report render",
        "publication validator",
        "Jinja",
    ):
        assert text not in corpus
    for pattern in (
        r"S0\s*[-–—]\s*S9",
        r"S0\s*(?:到|至)\s*S9",
    ):
        assert re.search(pattern, corpus, re.IGNORECASE) is None


def test_runtime_resources_forbid_hidden_reasoning_artifacts() -> None:
    corpus = "\n".join(
        ROOT.joinpath(relative).read_text(encoding="utf-8")
        for relative in ("SKILL.md", *RUNTIME_REFERENCES[:-1])
    )
    assert "不得保存或展示隐藏思维链" in corpus


def test_work_package_metadata_matches_frozen_matrix() -> None:
    for package_id, expected in EXPECTED.items():
        spec = load_work_package(_work_package_path(package_id))
        actual = (
            spec.start_requires,
            spec.finalize_requires,
            spec.shared_baseline,
            spec.may_reopen,
        )
        assert actual == expected
        assert spec.id == package_id
        assert spec.kind == "core"
        assert spec.coverage_role == "required"
    assert load_work_package(_work_package_path("W2")).shared_baseline == ("W5",)
    assert load_work_package(_work_package_path("W5")).shared_baseline == ("W2",)


def test_catalog_registers_exactly_the_canonical_core_packages() -> None:
    catalog = ROOT.joinpath("references/work-packages/catalog.md").read_text(
        encoding="utf-8"
    )
    entries = dict(
        re.findall(r"^\|\s*(W\d+)\s*\|.*?\]\((core/[^)]+)\)", catalog, re.MULTILINE)
    )
    expected = {
        package_id: f"core/{filename}" for package_id, filename in PACKAGE_FILES.items()
    }
    assert entries == expected
    assert not list(ROOT.glob("references/work-packages/official/*.md"))


def test_each_reopen_target_has_one_exact_explanation_line() -> None:
    for package_id, metadata in EXPECTED.items():
        may_reopen = metadata[3]
        body = _work_package_path(package_id).read_text(encoding="utf-8")
        revisit = _section(body.split("\n---\n", 1)[1], "## 回访触发")
        if may_reopen:
            for target in may_reopen:
                assert len(re.findall(rf"^- {target}:\s*\S.*$", revisit, re.MULTILINE)) == 1
        else:
            assert revisit.strip() == "不适用"
