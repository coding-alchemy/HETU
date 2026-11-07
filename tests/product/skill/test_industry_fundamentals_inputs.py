import csv
import json
import shutil
from pathlib import Path

import pytest

from tests.product.skill.material_fixtures import materialize_input

ROOT = Path(__file__).resolve().parents[1] / "fixtures/industry_fundamentals"
BASE = {"task.md", "material.md", "selection.json"}
EXPECTED = {
    "IF01/main/01": BASE | {"history.csv", "comparison.csv"},
    "IF01/limited/01": BASE | {"history.csv"},
    "IF01/price/01": BASE | {"prices.csv"},
    "IF02/entry/01": BASE,
    "IF02/entry/02": BASE,
    "IF02/entry/03": BASE,
}


def rows(path):
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def check_input(output, key):
    assert {p.name for p in output.iterdir()} == EXPECTED[key]
    case, variant, stage = key.split("/")
    selected = json.loads((output / "selection.json").read_text())
    assert selected == {
        "case": case, "variant": variant, "stage": stage,
        "context": "continue" if case == "IF02" and stage != "01" else "new",
        "as_of": ["2026-11-01T12:00:00+08:00"],
    }
    text = (output / "task.md").read_text() + (output / "material.md").read_text()
    assert "SCORING_ONLY_7CF4" not in text
    assert "review-expectations" not in text
    assert all(not p.is_symlink() for p in output.iterdir())
    for target in ("history.csv", "comparison.csv", "prices.csv"):
        if f"]({target})" in text:
            assert (output / target).is_file()
    if variant in {"main", "limited"}:
        data = rows(output / "history.csv")
        assert len(data) == 21
        assert data[0]["month"] == "2025-01" and data[-1]["month"] == "2026-09"
        assert data[-1]["terminal_t"] == "90.0"
        assert data[-1]["quote_x1_yuan_t"] == "1200"
        assert next(r for r in data if r["month"] == "2026-06")["sample_stock_t"] == ""
        assert "2026-11-10" in text and "92吨" in text
        assert "厂商自行选定客户" in text
        if variant == "main":
            assert len(rows(output / "comparison.csv")) == 3
            assert "用户提供的摘要" not in text
        else:
            assert "2024-10" not in text and "用户提供的摘要" in text
    if variant == "price":
        data = rows(output / "prices.csv")
        assert len(data) == 21
        assert set(data[0]) == {"month", "quote_x1_yuan_t"}
        assert "厂商自行选定客户" not in text
        assert "X1现货报价" in text
    if case == "IF02":
        assert not (output / "material.md").read_text().strip()


@pytest.mark.parametrize("key", EXPECTED)
def test_selection(tmp_path, key):
    output = tmp_path / "input"
    materialize_input(*key.split("/"), output, root=ROOT)
    check_input(output, key)
    catalog = json.loads((ROOT / "catalog.json").read_text())
    for source, target in catalog[key]["assets"]:
        assert (output / target).read_bytes() == (ROOT / source).read_bytes()


@pytest.mark.parametrize("key", ["missing/main/01", "IF01/missing/01", "IF01/main/99"])
def test_unknown_selection_does_not_write(tmp_path, key):
    output = tmp_path / "input"
    with pytest.raises(KeyError):
        materialize_input(*key.split("/"), output, root=ROOT)
    assert not output.exists()


def test_existing_output_is_preserved(tmp_path):
    output = tmp_path / "input"
    output.mkdir()
    (output / "keep").write_text("保留")
    with pytest.raises(FileExistsError):
        materialize_input("IF01", "main", "01", output, root=ROOT)
    assert (output / "keep").read_text() == "保留"
    assert {p.name for p in output.iterdir()} == {"keep"}


def test_complete_base_pollution_is_detected(tmp_path):
    output = tmp_path / "input"
    materialize_input("IF01", "limited", "01", output, root=ROOT)
    shutil.copyfile(ROOT / "assets/comparison.csv", output / "comparison.csv")
    with pytest.raises(AssertionError):
        check_input(output, "IF01/limited/01")


def test_fact_contamination_is_detected(tmp_path):
    output = tmp_path / "input"
    materialize_input("IF01", "main", "01", output, root=ROOT)
    path = output / "history.csv"
    path.write_text(path.read_text().replace("90.0", "92.0"))
    with pytest.raises(AssertionError):
        check_input(output, "IF01/main/01")


def test_scoring_in_task_is_detected(tmp_path):
    output = tmp_path / "input"
    materialize_input("IF01", "main", "01", output, root=ROOT)
    path = output / "task.md"
    path.write_text(path.read_text() + "\nSCORING_ONLY_7CF4\n")
    with pytest.raises(AssertionError):
        check_input(output, "IF01/main/01")


def test_scoring_asset_is_detected(tmp_path):
    output = tmp_path / "input"
    materialize_input("IF01", "limited", "01", output, root=ROOT)
    shutil.copyfile(ROOT / "review-expectations.md", output / "review-expectations.md")
    with pytest.raises(AssertionError):
        check_input(output, "IF01/limited/01")


def test_circulation_selection_and_original_values(tmp_path):
    output = tmp_path / "input"
    materialize_input("IF03", "circulation", "01", output, root=ROOT)
    attachments = {
        "circulation-projects.csv", "circulation-finance.csv",
        "circulation-inventory.csv",
    }
    assert {path.name for path in output.iterdir()} == BASE | attachments
    assert all(not path.is_symlink() for path in output.iterdir())
    selected = json.loads((output / "selection.json").read_text())
    assert selected == {
        "case": "IF03", "variant": "circulation", "stage": "01",
        "context": "new", "as_of": ["2026-11-01T12:00:00+08:00"],
    }
    text = (output / "task.md").read_text() + (output / "material.md").read_text()
    assert "SCORING_ONLY_7CF4" not in text
    assert "review-expectations" not in text
    assert "合成" in text and "2026-10-25" in text
    for name in attachments:
        assert f"]({name})" in text
        assert (output / name).read_bytes() == (ROOT / "assets" / name).read_bytes()
    finance = rows(output / "circulation-finance.csv")
    assert len(finance) == 6
    assert finance[-1]["operating_cash_net"] == "-1"
    assert sum(float(row["operating_cash_net"]) for row in finance[3:]) == 5
    projects = rows(output / "circulation-projects.csv")
    assert [row["annual_capex_guidance"] for row in projects] == ["300", "450"]
    assert [row["actual_capex"] for row in projects] == ["200", "180"]
    inventory = rows(output / "circulation-inventory.csv")
    assert len(inventory) == 6
    assert sum(float(row["gross_million_yuan"]) for row in inventory[3:]) == 104
    assert sum(float(row["allowance_million_yuan"]) for row in inventory[3:]) == 6
    assert float(inventory[-1]["quantity_t"]) - float(inventory[-1]["allocated_t"]) == 30
