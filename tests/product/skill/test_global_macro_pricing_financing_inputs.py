import csv
import json
import shutil
from pathlib import Path

import pytest

from tests.product.skill.material_fixtures import materialize_input

ROOT = Path(__file__).resolve().parents[1] / "fixtures/global_macro"


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open() as stream:
        return list(csv.DictReader(stream))


def check_limited(output: Path) -> None:
    assert all(r["basis_bp"] == "" for r in read_rows(output / "market.csv"))
    rows = {r["quarter"]: r for r in read_rows(output / "quarters.csv")}
    assert rows["2025Q4"]["it_debt"] == ""
    assert rows["2025Q4"]["fr_debt"]
    text = (output / "material.md").read_text()
    assert "## G27 " in text and "## G26 " not in text


@pytest.mark.parametrize("variant", ["main", "limited", "scope"])
def test_selection(tmp_path: Path, variant: str) -> None:
    output = tmp_path / variant
    paths = materialize_input("GM13", variant, "01", output, root=ROOT)
    assert {p.name for p in paths} == {
        "task.md", "material.md", "selection.json", "market.csv", "quarters.csv"}
    selection = json.loads((output / "selection.json").read_text())
    assert selection["context"] == "new"
    assert selection["as_of"] == ["2026-11-04T12:00:00+08:00"]
    catalog = json.loads((ROOT / "catalog.json").read_text())
    for relative, name in catalog[f"GM13/{variant}/01"]["assets"]:
        assert (output / name).read_bytes() == (ROOT / relative).read_bytes()
    text = (output / "task.md").read_text() + (output / "material.md").read_text()
    for token in ("review-expectations", "评审标准", "预期答案", "必须发现"):
        assert token not in text
    assert "合成" in text
    if variant == "limited":
        check_limited(output)
    if variant == "scope":
        assert "## G28 " not in text and "## G26 " in text
        assert "法国" in (output / "task.md").read_text()


def test_raw_windows_and_comparison_inputs() -> None:
    market = read_rows(ROOT / "inputs/pricing-market.csv")
    assert len(market) == 8 and market[0]["date"] == "2026-09-09"
    assert market[-1]["date"] == "2026-10-28"
    rows = read_rows(ROOT / "inputs/pricing-quarters.csv")
    assert len(rows) == 12 and rows[0]["quarter"] == "2023Q3"
    assert rows[-1]["quarter"] == "2026Q2"
    assert rows[-1]["bill_issue"] == "600" and rows[-1]["bill_redeem"] == "620"
    assert rows[-2]["bill_issue"] == "550" and rows[-2]["bill_redeem"] == "500"
    assert rows[-5]["bill_issue"] == "520" and rows[-5]["bill_redeem"] == "470"
    assert rows[0]["nominal_buyback"] == "0"
    assert "net_supply" not in rows[0] and "spread_bp" not in market[0]


def test_full_attachment_pollution_is_detected(tmp_path: Path) -> None:
    output = tmp_path / "limited"
    materialize_input("GM13", "limited", "01", output, root=ROOT)
    shutil.copyfile(ROOT / "inputs/pricing-market.csv", output / "market.csv")
    with pytest.raises(AssertionError):
        check_limited(output)


def test_unknown_selection_does_not_write(tmp_path: Path) -> None:
    output = tmp_path / "input"
    with pytest.raises(KeyError):
        materialize_input("GM13", "unknown", "01", output, root=ROOT)
    assert not output.exists()


def test_existing_output_is_preserved(tmp_path: Path) -> None:
    output = tmp_path / "input"
    output.mkdir()
    keep = output / "keep.txt"
    keep.write_text("保留")
    with pytest.raises(FileExistsError):
        materialize_input("GM13", "main", "01", output, root=ROOT)
    assert keep.read_text() == "保留" and set(output.iterdir()) == {keep}


def test_scoring_route_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "fixtures"
    shutil.copytree(ROOT, root)
    path = root / "catalog.json"
    catalog = json.loads(path.read_text())
    catalog["GM13/main/01"]["material"] = [["review-expectations.md", "G25"]]
    path.write_text(json.dumps(catalog))
    output = tmp_path / "input"
    with pytest.raises(KeyError):
        materialize_input("GM13", "main", "01", output, root=root)
    assert not output.exists()
