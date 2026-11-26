import inspect
import os
import re
import shutil
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

import pytest

from hetu_quant.factors import list_factors
from hetu_quant.factors.selftest import read_selftest, replay_request
from test_factor_formula_evidence import CASES

REPOSITORY = Path(__file__).resolve().parents[2]


def test_factor_catalog_matches_approved_ids():
    path = REPOSITORY / "specs/2026-11-26-quant-research-implementation.md"
    sections = re.split(r"^### 5\.\d .*$", path.read_text(), flags=re.MULTILINE)[1:]
    expected = []
    for section, count in zip(sections, (44, 55, 4), strict=True):
        ids = []
        for row in section.splitlines():
            cells = row.split("|")
            if len(cells) < 4 or not cells[2].strip().startswith("`"):
                continue
            ids.extend(re.findall(r"`([a-z_0-9]+)`", cells[2]))
        assert len(ids) == count
        expected.extend(ids)
    assert len(set(expected)) == 103
    definitions = list_factors()
    assert {definition.factor_id for definition in definitions} == set(expected)
    assert len(definitions) == len(expected)
    folder = files("hetu_quant.factors")
    assert {p.name for p in folder.joinpath("cards").iterdir()} == {f"{name}.md" for name in expected}
    for definition in definitions:
        card = folder.joinpath("cards", f"{definition.factor_id}.md").read_text()
        assert definition.name in card and definition.factor_id in card and definition.chapters in card
        assert inspect.isfunction(definition.compute) and inspect.getsource(definition.compute)
        assert definition.defaults == definition.presets["默认"]
        assert definition.effect_validation["status"] == "not_evaluated"
        assert definition.effect_validation["evidence_refs"] == []
        assert folder.joinpath("examples", f"{definition.example_id}.json").is_file()


def test_installed_factor_resources(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    shutil.copy(REPOSITORY / "quant/pyproject.toml", source)
    shutil.copytree(REPOSITORY / "quant/src", source / "src", ignore=shutil.ignore_patterns("__pycache__"))
    environment = dict(os.environ, PIP_NO_INDEX="1", PYTHONPATH=str(REPOSITORY / "data/src"))
    subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "-w", str(tmp_path), str(source)],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
    )
    wheel = next(tmp_path.glob("*.whl"))
    target = tmp_path / "wheel-install"
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-deps", "--target", str(target), str(wheel)],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
    )
    script = """
import inspect
from importlib.resources import files
from pathlib import Path
from hetu_quant.factors import list_factors
import hetu_quant.factors as module
assert Path(module.__file__).is_relative_to(Path("wheel-install").resolve())
folder = files("hetu_quant.factors")
definitions = list_factors()
assert len(definitions) == 103
for definition in definitions:
    assert folder.joinpath("cards", definition.factor_id + ".md").read_text()
    assert folder.joinpath("examples", definition.example_id + ".json").read_text()
    if definition.factor_id in ("up_amount_share", "earnings_yield", "breadth_above_ma_share"):
        assert "def " + definition.factor_id in inspect.getsource(definition.compute)
"""
    environment["PYTHONPATH"] = str(target) + os.pathsep + environment["PYTHONPATH"]
    subprocess.run([sys.executable, "-c", script], cwd=tmp_path, env=environment, check=True, capture_output=True)


def test_recorded_factor_cases_cover_approved_ids(factor_evidence_root):
    if factor_evidence_root is None:
        pytest.skip("仅显式提供已执行收据目录时核对交付")
    missing = []
    for definition in list_factors():
        for preset, parameters in definition.presets.items():
            evidence = read_selftest(factor_evidence_root, definition.factor_id, definition.version, parameters)
            if evidence["status"] != "passed":
                missing.append((definition.factor_id, preset, evidence["status"]))
            if preset == "默认":
                case = "formula:discriminator" if definition.factor_id in CASES else "formula:changed_fact"
            else:
                case = "formula:preset_boundary"
            required = (case,) if preset == "默认" else (case, "positive")
            for case_id in required:
                receipt = next(
                    (
                        row for row in evidence["cases"]
                        if row["case_id"] == case_id and row["matches_tested_parameters"] and row["status"] == "passed"
                    ),
                    None,
                )
                coordinate = (definition.factor_id, preset, parameters, case_id)
                if receipt is None:
                    missing.append((*coordinate, "缺可复算必需收据"))
                    continue
                try:
                    replay_request(receipt)
                except ValueError as error:
                    missing.append((*coordinate, str(error)))
            for item in evidence["cases"]:
                assert item["node"] and item["expected"] is not None and item["actual"]
    assert not missing, missing
