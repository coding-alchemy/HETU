import json
import shutil
from pathlib import Path

import pytest

from tests.product.skill.material_fixtures import materialize_input

ROOT = Path(__file__).resolve().parents[1] / "fixtures/global_macro"
CASES = {
    "GM01/main/01": ("G01", "G02", "G03"),
    "GM02/main/01": ("G01", "G03"),
    "GM03/main/01": ("G05",),
    "GM04/main/01": ("G06", "G07", "G08"),
    "GM04/main/02": (),
    "GM04/missing/01": ("G07", "G08"),
    "GM05/readonly/01": ("G01", "G03"),
    "GM05/save-failure/01": ("G01", "G03"),
    "GM06/excluded/01": (),
    "GM06/ambiguous/01": (),
}


def assert_boundary(paths: tuple[Path, ...], key: str) -> None:
    by_name = {p.name: p for p in paths}
    assert set(by_name) == {"task.md", "material.md", "selection.json"}
    material = by_name["material.md"].read_text()
    selected = set(CASES[key])
    for block in {b for blocks in CASES.values() for b in blocks}:
        assert (f"## {block} " in material) == (block in selected), (key, block)
    combined = by_name["task.md"].read_text() + material
    assert "review-expectations" not in combined
    assert "评审专用判定" not in combined
    selection = json.loads(by_name["selection.json"].read_text())
    expected_context = "continue" if key == "GM04/main/02" else "new"
    assert selection["context"] == expected_context
    assert selection["as_of"] == ["2026-06-30T12:00:00+00:00"]
    assert "/".join(selection[k] for k in ("case", "variant", "stage")) == key
    if key == "GM04/missing/01":
        assert "初值0.5%" not in material


@pytest.mark.parametrize("key", CASES)
def test_selected_input(tmp_path: Path, key: str) -> None:
    paths = materialize_input(*key.split("/"), tmp_path / "input", root=ROOT)
    assert_boundary(paths, key)


def test_missing_vintage_pollution_is_detected(tmp_path: Path) -> None:
    root = tmp_path / "source"
    shutil.copytree(ROOT, root)
    path = root / "catalog.json"
    catalog = json.loads(path.read_text())
    catalog["GM04/missing/01"]["material"].append(["inputs/facts.md", "G06"])
    path.write_text(json.dumps(catalog))
    paths = materialize_input("GM04", "missing", "01", tmp_path / "input", root=root)
    with pytest.raises(AssertionError):
        assert_boundary(paths, "GM04/missing/01")


def test_review_route_is_rejected_before_write(tmp_path: Path) -> None:
    root = tmp_path / "source"
    shutil.copytree(ROOT, root)
    path = root / "catalog.json"
    catalog = json.loads(path.read_text())
    catalog["GM01/main/01"]["task"] = [["review-expectations.md", "V1"]]
    path.write_text(json.dumps(catalog))
    output = tmp_path / "input"
    with pytest.raises(KeyError):
        materialize_input("GM01", "main", "01", output, root=root)
    assert not output.exists()


def test_unknown_selection_does_not_write(tmp_path: Path) -> None:
    output = tmp_path / "input"
    with pytest.raises(KeyError):
        materialize_input("GM01", "unknown", "01", output, root=ROOT)
    assert not output.exists()


def test_existing_output_is_preserved(tmp_path: Path) -> None:
    output = tmp_path / "input"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("保留原件")
    with pytest.raises(FileExistsError):
        materialize_input("GM01", "main", "01", output, root=ROOT)
    assert list(output.iterdir()) == [marker]
    assert marker.read_text() == "保留原件"
