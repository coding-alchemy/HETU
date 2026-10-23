import csv
import io
import json
import re
import shutil
from pathlib import Path

import pytest

from tests.product.skill.material_fixtures import materialize_input

ROOT = Path(__file__).resolve().parents[1] / "fixtures/china_macro"
FILES = {"task.md", "material.md", "selection.json", "library.md", "m500.md",
         "m510.csv", "m520.csv", "m530.csv", "m540.md", "m550.md"}


def assert_boundary(paths: tuple[Path, ...], variant: str) -> None:
    files = {p.name: p.read_bytes() for p in paths}
    assert set(files) == FILES
    assert files["material.md"] == b""
    assert json.loads(files["selection.json"]) == {
        "case": "CM13", "variant": variant, "stage": "01", "context": "new",
        "as_of": ["2026-11-20T12:00:00+08:00"],
    }
    entry = json.loads((ROOT / "catalog.json").read_text())[f"CM13/{variant}/01"]
    for source, target in entry["assets"]:
        assert files[target] == (ROOT / source).read_bytes()
    for name in ("library.md", "m500.md", "m540.md", "m550.md"):
        for target in re.findall(r"\]\(([^)]+)\)", files[name].decode()):
            assert target.split("#")[0] in files
    for data in files.values():
        assert b"review-expectations" not in data
        assert b"V1-V5" not in data
    rows = list(csv.DictReader(io.StringIO(files["m510.csv"].decode())))
    fiscal = [r for r in rows if r["series"] == "local_spend"]
    fiscal_periods = {
        f"{y}-{m:02}" for y in range(2023, 2027) for m in range(1, 13)
        if "2023-11" <= f"{y}-{m:02}" <= "2026-10" and m != 1
    }
    if variant == "limited":
        fiscal_periods.remove("2025-06")
    assert {r["period"] for r in fiscal} == fiscal_periods
    assert not any(r["period"].endswith("-01") for r in fiscal)
    assert {r["period"] for r in rows if r["series"] == "lgfv_issue"} == {
        f"{y}-{m:02}" for y in range(2023, 2027) for m in range(1, 13)
        if "2023-11" <= f"{y}-{m:02}" <= "2026-10"
    }
    point = [r for r in fiscal if r["period"] == "2025-06"]
    assert bool(point) == (variant == "main")
    changed = next(r for r in fiscal if r["period"] == "2025-04")
    assert changed["version"] == ("B" if variant == "limited" else "A")
    assert next(r for r in fiscal if r["period"] == "2026-10")["value"] == "15930"
    quarters = list(csv.DictReader(io.StringIO(files["m520.csv"].decode())))
    assert any(r["published_at"] > "2026-11-20" for r in quarters)
    assert len([r for r in quarters if r["version"] == "A"]) == 15
    years = list(csv.DictReader(io.StringIO(files["m530.csv"].decode())))
    assert [r["year"] for r in years] == [str(y) for y in range(2020, 2026)]
    policy = files["m540.md"].decode()
    assert "2027" in policy and "220" in policy and "2028" in policy
    assert "合成" in files["m500.md"].decode()


@pytest.mark.parametrize("variant", ["main", "limited"])
def test_selected_boundary(tmp_path: Path, variant: str) -> None:
    paths = materialize_input("CM13", variant, "01", tmp_path / "input", root=ROOT)
    assert_boundary(paths, variant)


@pytest.mark.parametrize("source,target", [
    ("assets/history-monthly.csv", "m510.csv"),
    ("review-expectations.md", "m500.md"),
])
def test_pollution_is_detected(tmp_path: Path, source: str, target: str) -> None:
    paths = materialize_input("CM13", "limited", "01", tmp_path / "input", root=ROOT)
    next(p for p in paths if p.name == target).write_bytes((ROOT / source).read_bytes())
    with pytest.raises(AssertionError):
        assert_boundary(paths, "limited")


def test_review_route_rejected_before_write(tmp_path: Path) -> None:
    root = tmp_path / "source"
    shutil.copytree(ROOT, root)
    p = root / "catalog.json"
    catalog = json.loads(p.read_text())
    catalog["CM13/main/01"]["task"] = [["review-expectations.md", "CM13"]]
    p.write_text(json.dumps(catalog))
    output = tmp_path / "input"
    with pytest.raises(KeyError):
        materialize_input("CM13", "main", "01", output, root=root)
    assert not output.exists()


@pytest.mark.parametrize("choice", [
    ("UNKNOWN", "main", "01"), ("CM13", "unknown", "01"), ("CM13", "main", "99"),
])
def test_unknown_selection_does_not_write(tmp_path: Path, choice: tuple[str, ...]) -> None:
    output = tmp_path / "input"
    with pytest.raises(KeyError):
        materialize_input(*choice, output, root=ROOT)
    assert not output.exists()
