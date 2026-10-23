import json
import re
import shutil
from pathlib import Path

import pytest

from tests.product.skill.material_fixtures import materialize_input

ROOT = Path(__file__).resolve().parents[1] / "fixtures/china_macro"
BLOCKS = {
    "main": {"F01", "F03", "F04", "F10", "F11", "F12", "F16"},
    "limited": {"F01", "F03", "F04", "F10", "F16"},
    "scope": {"F03", "F04", "F10"},
}


def assert_boundary(paths: tuple[Path, ...], variant: str) -> None:
    files = {p.name: p.read_text() for p in paths}
    assert set(files) == {
        "task.md", "material.md", "selection.json", "library.md",
        "m310.md", "m320.md", "r212.html",
    }
    assert set(re.findall(r"^## (F\d+) ", files["material.md"], re.M)) == BLOCKS[variant]
    selection = json.loads(files["selection.json"])
    assert selection == {
        "case": "CM11", "variant": variant, "stage": "01", "context": "new",
        "as_of": ["2026-06-30T12:00:00+08:00"],
    }
    assert "M310-P" not in files["material.md"] + files["task.md"]
    assert "M310-P" in files["m310.md"]
    assert "M320-A" in files["m320.md"]
    assert "M320-D" in files["m320.md"]
    assert "净偿还30亿元" in files["r212.html"]
    for target in re.findall(r"\]\(([^)]+)\)", files["library.md"]):
        assert target in files
    for name in ("task.md", "material.md", "library.md"):
        assert "review-expectations" not in files[name]
        assert "验收答案" not in files[name]
    if variant == "limited":
        assert "M310-L" in files["m310.md"]
        assert "M310-F" not in files["m310.md"]
        assert "2026-07-02首次公开" in files["m310.md"]
    else:
        assert "M310-F" in files["m310.md"]
        assert "M310-L" not in files["m310.md"]


@pytest.mark.parametrize("variant", BLOCKS)
def test_selected_input_boundary(tmp_path: Path, variant: str) -> None:
    paths = materialize_input("CM11", variant, "01", tmp_path / "input", root=ROOT)
    assert_boundary(paths, variant)


def test_positive_attachment_pollution_is_detected(tmp_path: Path) -> None:
    paths = materialize_input("CM11", "limited", "01", tmp_path / "input", root=ROOT)
    target = next(p for p in paths if p.name == "m310.md")
    target.write_text((ROOT / "assets/transmission-release.md").read_text())
    with pytest.raises(AssertionError):
        assert_boundary(paths, "limited")


def test_review_route_is_rejected_before_output(tmp_path: Path) -> None:
    root = tmp_path / "source"
    shutil.copytree(ROOT, root)
    path = root / "catalog.json"
    catalog = json.loads(path.read_text())
    catalog["CM11/main/01"]["task"] = [["review-expectations.md", "CM11"]]
    path.write_text(json.dumps(catalog))
    output = tmp_path / "input"
    with pytest.raises(KeyError):
        materialize_input("CM11", "main", "01", output, root=root)
    assert not output.exists()


def test_unknown_selection_does_not_write(tmp_path: Path) -> None:
    output = tmp_path / "input"
    with pytest.raises(KeyError):
        materialize_input("CM11", "unknown", "01", output, root=ROOT)
    assert not output.exists()
