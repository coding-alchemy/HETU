import json
import shutil
from pathlib import Path

import pytest

from tests.product.skill.material_fixtures import materialize_input

ROOT = Path(__file__).resolve().parents[1] / "fixtures/china_macro"
CASES = {
    "CM08/main/01": ("F10", "F11", "F12"),
    "CM08/limited/01": ("F11", "F13", "F14"),
    "CM09/main/01": ("F15",),
}


def assert_boundary(paths: tuple[Path, ...], key: str) -> None:
    by_name = {p.name: p for p in paths}
    material = by_name["material.md"].read_text()
    for block in CASES[key]:
        assert f"## {block} " in material
    for block in {"F10", "F11", "F12", "F13", "F14", "F15"} - set(CASES[key]):
        assert f"## {block} " not in material
    assert "review-expectations" not in material
    selection = json.loads(by_name["selection.json"].read_text())
    assert selection["context"] == "new"
    assert selection["as_of"] == ["2026-06-30T12:00:00+08:00"]
    expected = {"task.md", "material.md", "selection.json"}
    if key == "CM09/main/01":
        expected.add("depth_bank_release.txt")
        assert "实收80亿元" not in material
        assert "实收80亿元" in by_name["depth_bank_release.txt"].read_text()
    assert set(by_name) == expected


@pytest.mark.parametrize("key", CASES)
def test_selected_input_boundary(tmp_path: Path, key: str) -> None:
    paths = materialize_input(*key.split("/"), tmp_path / "input", root=ROOT)
    assert_boundary(paths, key)


def test_positive_evidence_pollution_is_detected(tmp_path: Path) -> None:
    root = tmp_path / "source"
    shutil.copytree(ROOT, root)
    catalog_path = root / "catalog.json"
    catalog = json.loads(catalog_path.read_text())
    catalog["CM08/limited/01"]["material"].append(["inputs/facts.md", "F12"])
    catalog_path.write_text(json.dumps(catalog))
    paths = materialize_input("CM08", "limited", "01", tmp_path / "input", root=root)
    with pytest.raises(AssertionError):
        assert_boundary(paths, "CM08/limited/01")


def test_review_route_is_rejected_before_output(tmp_path: Path) -> None:
    root = tmp_path / "source"
    shutil.copytree(ROOT, root)
    catalog_path = root / "catalog.json"
    catalog = json.loads(catalog_path.read_text())
    catalog["CM08/main/01"]["task"] = [["review-expectations.md", "V1"]]
    catalog_path.write_text(json.dumps(catalog))
    output = tmp_path / "input"
    with pytest.raises(KeyError):
        materialize_input("CM08", "main", "01", output, root=root)
    assert not output.exists()


def test_unknown_selection_does_not_write(tmp_path: Path) -> None:
    output = tmp_path / "input"
    with pytest.raises(KeyError):
        materialize_input("CM09", "unknown", "01", output, root=ROOT)
    assert not output.exists()
