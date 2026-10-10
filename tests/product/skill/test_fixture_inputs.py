import json
import shutil
from pathlib import Path

import pytest

from tests.product.skill.material_fixtures import ROOT, materialize_input


def _text(paths: tuple[Path, ...]) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in paths if p.suffix == ".md")


def _stage_two_boundary(paths: tuple[Path, ...]) -> None:
    text = _text(paths)
    assert "营业收入 100" in text or "营业收入 100／" in text
    assert "分部收入 A=60" not in text
    assert "一次性收益 3" not in text


def test_partial_material_never_contains_later_notes(tmp_path: Path) -> None:
    _stage_two_boundary(materialize_input("B16", "main", "02", tmp_path / "s2"))
    text = _text(materialize_input("B16", "main", "03", tmp_path / "s3"))
    assert "分部收入 A=60" in text
    assert "一次性收益 3" in text


@pytest.mark.parametrize("variant", ["closed", "industry-closed"])
def test_isolated_reuse_off_has_no_old_values(tmp_path: Path, variant: str) -> None:
    text = _text(materialize_input("B17", variant, "01", tmp_path / variant))
    assert "reuse_previous_task_data=false" in text
    for forbidden in ("T1-root", "P1", "W-A-root", "100", "10.2"):
        assert forbidden not in text


def test_deliberate_old_clue_remains_input_not_a_file_to_read(tmp_path: Path) -> None:
    paths = materialize_input("B17", "integration-off", "01", tmp_path / "old-clue")
    text = _text(paths)
    assert "10.2" in text and ".hetu/research/jia-20260331/" in text
    assert {p.name for p in paths} == {"task.md", "material.md", "selection.json"}


def test_copy_variant_keeps_the_permitted_original_facts(tmp_path: Path) -> None:
    text = _text(materialize_input("B17", "copy", "01", tmp_path / "copy"))
    assert "营业收入 100、净利润 10、经营活动现金流量净额 12" in text
    assert "2026-04-20 19:00+08:00" in text
    assert "T1 的旧目录 T1-root 不在 T2 的允许输入集" in text
    assert "Q3-R01" not in text and "复用开关 false" not in text


def test_new_time_and_dual_sections_are_preserved(tmp_path: Path) -> None:
    paths = materialize_input("B11", "main", "02", tmp_path / "b11")
    selection = json.loads((tmp_path / "b11/selection.json").read_text())
    assert selection["context"] == "independent"
    assert selection["as_of"] == ["2026-07-02T12:00:00+08:00"]
    assert "第 407 条" in _text(paths)
    text = _text(materialize_input("B14", "main", "01", tmp_path / "b14"))
    assert "2026-03-31T12:00:00+08:00" in text
    assert "2026-06-30T12:00:00+08:00" in text


def test_only_current_assets_are_copied(tmp_path: Path) -> None:
    early = materialize_input("B13", "main", "01", tmp_path / "early")
    assert "market-cap.json" not in {p.name for p in early}
    late = materialize_input("B13", "main", "03", tmp_path / "late")
    assert "market-cap.json" in {p.name for p in late}
    pdfs = materialize_input("B15", "main", "01", tmp_path / "pdf")
    for name in ("corrupt.pdf", "encrypted.pdf", "no-text.pdf"):
        target = next(p for p in pdfs if p.name == name)
        assert target.read_bytes() == (ROOT / "assets/B15" / name).read_bytes()
        assert not target.is_symlink()


@pytest.mark.parametrize(
    "case,variant,stage", [("B99", "main", "01"), ("B16", "missing", "01"), ("B16", "main", "99")]
)
def test_unknown_selection_creates_nothing(
    tmp_path: Path, case: str, variant: str, stage: str
) -> None:
    output = tmp_path / "unknown"
    with pytest.raises(KeyError):
        materialize_input(case, variant, stage, output)
    assert not output.exists()


def test_contaminated_route_fails_the_boundary_check(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    shutil.copytree(ROOT, root)
    path = root / "catalog.json"
    data = json.loads(path.read_text())
    data["B16/main/02"]["material"] += data["B16/main/03"]["material"]
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(AssertionError):
        _stage_two_boundary(
            materialize_input("B16", "main", "02", tmp_path / "bad", root=root)
        )


def test_review_file_cannot_be_routed_into_an_input(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    shutil.copytree(ROOT, root)
    path = root / "catalog.json"
    data = json.loads(path.read_text())
    data["B16/main/02"]["task"] = [["review-expectations/B16.md", "B16-001"]]
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "review-leak"
    with pytest.raises(KeyError):
        materialize_input("B16", "main", "02", output, root=root)
    assert not output.exists()


def test_reused_task_and_originals_have_one_maintained_copy() -> None:
    data = json.loads((ROOT / "catalog.json").read_text())
    first = data["B16/main/01"]["task"]
    assert first == data["B16/main/02"]["task"] == data["B16/main/03"]["task"]
    bodies = []
    for path in [ROOT / "shared.md", *sorted((ROOT / "inputs").rglob("*.md"))]:
        for block in path.read_text(encoding="utf-8").split("<!-- fixture:")[1:]:
            bodies.append(block.split("-->\n", 1)[1].strip())
    assert len(bodies) == len(set(bodies))


def test_every_registered_selection_is_individually_materializable(tmp_path: Path) -> None:
    data = json.loads((ROOT / "catalog.json").read_text())
    assert {key.split("/")[0] for key in data} == {f"B{i:02d}" for i in range(1, 19)}
    assert len(data) == 48
    for key in data:
        case, variant, stage = key.split("/")
        paths = materialize_input(case, variant, stage, tmp_path / key.replace("/", "-"))
        assert len(paths) == len(set(paths))
        assert all(p.parent == paths[0].parent for p in paths)
        assert not any("review" in p.name or p.name == "catalog.json" for p in paths)
        assert "独立判定依据" not in _text(paths)
