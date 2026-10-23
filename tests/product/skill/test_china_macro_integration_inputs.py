import json
import re
import shutil
from pathlib import Path

import pytest

from tests.product.skill.material_fixtures import materialize_input

ROOT = Path(__file__).resolve().parents[1] / "fixtures/china_macro"
FILES = {
    "task.md", "material.md", "selection.json", "library.md", "m410.md",
    "m420.md", "m430.md", "m431.md", "m320.md", "r212.html",
}
BLOCKS = {"F17", "F03", "F04", "F10", "F11", "F12", "F16"}


def assert_boundary(paths: tuple[Path, ...], variant: str) -> None:
    files = {p.name: p.read_text() for p in paths}
    assert set(files) == FILES
    assert set(re.findall(r"^## (F\d+) ", files["material.md"], re.M)) == BLOCKS
    assert json.loads(files["selection.json"]) == {
        "case": "CM12", "variant": variant, "stage": "01", "context": "new",
        "as_of": ["2026-06-30T12:00:00+08:00"],
    }
    for name in ("library.md", "m410.md", "m420.md", "m430.md"):
        for target in re.findall(r"\]\(([^)]+)\)", files[name]):
            assert target.split("#")[0] in files
    assert "m430.md" in files["m410.md"]
    assert "m431.md" in files["m430.md"]
    assert "M431" in files["m431.md"]
    for name in ("task.md", "material.md", "library.md"):
        assert "review-expectations" not in files[name]
        assert "验收答案" not in files[name]
        assert "2050/2000" not in files[name]
        assert "M410-S" not in files[name]
    if variant == "limited":
        assert "M410-LATE" in files["m410.md"]
        assert "M410-S" not in files["m410.md"]
        assert "M420-L" in files["m420.md"]
        assert "M420-H" not in files["m420.md"]
    else:
        assert "M410-S" in files["m410.md"]
        assert "M420-H" in files["m420.md"]
        assert "M410-LATE" not in files["m410.md"]


@pytest.mark.parametrize("variant", ["main", "limited", "scope"])
def test_selected_boundary(tmp_path: Path, variant: str) -> None:
    paths = materialize_input("CM12", variant, "01", tmp_path / "input", root=ROOT)
    assert_boundary(paths, variant)


@pytest.mark.parametrize("source,target", [
    ("integration-release.md", "m410.md"),
    ("integration-policy.md", "m420.md"),
])
def test_full_attachment_pollution_is_detected(tmp_path: Path, source: str, target: str) -> None:
    paths = materialize_input("CM12", "limited", "01", tmp_path / "input", root=ROOT)
    next(p for p in paths if p.name == target).write_bytes((ROOT / "assets" / source).read_bytes())
    with pytest.raises(AssertionError):
        assert_boundary(paths, "limited")


def test_review_route_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "source"
    shutil.copytree(ROOT, root)
    path = root / "catalog.json"
    catalog = json.loads(path.read_text())
    catalog["CM12/main/01"]["task"] = [["review-expectations.md", "CM12"]]
    path.write_text(json.dumps(catalog))
    output = tmp_path / "input"
    with pytest.raises(KeyError):
        materialize_input("CM12", "main", "01", output, root=root)
    assert not output.exists()


def test_unknown_selection_does_not_write(tmp_path: Path) -> None:
    output = tmp_path / "input"
    with pytest.raises(KeyError):
        materialize_input("CM12", "unknown", "01", output, root=ROOT)
    assert not output.exists()
