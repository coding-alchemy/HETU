import csv
import json
import shutil
from pathlib import Path

import pytest

from tests.product.skill.material_fixtures import materialize_input

ROOT = Path(__file__).resolve().parents[1] / "fixtures/global_macro"
SELECTED = {
    "main": set(range(9, 21)),
    "limited": set(range(9, 21)) - {17, 18},
    "scope": {15, 17},
}


def check_input(paths: tuple[Path, ...], variant: str, root: Path) -> None:
    files = {p.name: p for p in paths}
    names = {"task.md", "material.md", "selection.json"}
    if variant != "scope":
        names.add("stablecoin-history.csv")
    assert set(files) == names
    selection = json.loads(files["selection.json"].read_text())
    assert selection == {
        "case": "GM09", "variant": variant, "stage": "01",
        "context": "new", "as_of": ["2026-10-24T12:00:00+08:00"],
    }
    task = files["task.md"].read_text()
    material = files["material.md"].read_text()
    for number in range(9, 21):
        assert (f"## G{number:02d} " in material) == (number in SELECTED[variant])
    for forbidden in ("review-expectations", "评审专用判定", "期望结论"):
        assert forbidden not in task + material
    if variant == "main":
        for name in ("比特币", "稳定币", "黄金", "美债", "汇率"):
            assert name not in task  # 默认覆盖须由Skill提供。
    if variant != "scope":
        attachment = files["stablecoin-history.csv"]
        suffix = "-limited" if variant == "limited" else ""
        original = root / f"inputs/stablecoin-history{suffix}.csv"
        assert attachment.read_bytes() == original.read_bytes()
        with attachment.open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        assert len(rows) == 25
        values = {r["month"]: r["circulation_usd_bn"] for r in rows}
        assert len(values) == 25
        assert values["2026-09"] == "320"
        assert values["2024-09"] == "200"
        missing = {month for month, value in values.items() if value == ""}
        assert missing == ({"2025-03", "2026-05"} if variant == "limited" else set())


@pytest.mark.parametrize("variant", SELECTED)
def test_selected_feedback_input(tmp_path: Path, variant: str) -> None:
    paths = materialize_input("GM09", variant, "01", tmp_path / "input", root=ROOT)
    check_input(paths, variant, ROOT)


def test_limited_complete_attachment_pollution_is_detected(tmp_path: Path) -> None:
    root = tmp_path / "source"
    shutil.copytree(ROOT, root)
    catalog_path = root / "catalog.json"
    catalog = json.loads(catalog_path.read_text())
    catalog["GM09/limited/01"]["assets"] = [
        ["inputs/stablecoin-history.csv", "stablecoin-history.csv"]
    ]
    catalog_path.write_text(json.dumps(catalog))
    paths = materialize_input("GM09", "limited", "01", tmp_path / "input", root=root)
    with pytest.raises(AssertionError):
        check_input(paths, "limited", root)


def test_unknown_feedback_selection_does_not_write(tmp_path: Path) -> None:
    output = tmp_path / "input"
    with pytest.raises(KeyError):
        materialize_input("GM09", "unknown", "01", output, root=ROOT)
    assert not output.exists()


def test_existing_feedback_output_is_preserved(tmp_path: Path) -> None:
    output = tmp_path / "input"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("旧产物")
    with pytest.raises(FileExistsError):
        materialize_input("GM09", "main", "01", output, root=ROOT)
    assert list(output.iterdir()) == [marker]
    assert marker.read_text() == "旧产物"
