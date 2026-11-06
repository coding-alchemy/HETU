import json
from pathlib import Path

import pytest

from tests.product.skill.material_fixtures import materialize_input

ROOT = Path(__file__).resolve().parents[1] / "fixtures/global_macro"


@pytest.mark.parametrize("variant", ["main", "window"])
def test_history_input_scope_and_raw_data(tmp_path: Path, variant: str) -> None:
    paths = materialize_input("GM07", variant, "01", tmp_path / "input", root=ROOT)
    by_name = {path.name: path for path in paths}
    assert set(by_name) == {"task.md", "material.md", "selection.json"}
    selection = json.loads(by_name["selection.json"].read_text())
    assert selection["as_of"] == ["2026-10-24T12:00:00+08:00"]
    assert selection["context"] == "new"
    material = by_name["material.md"].read_text()
    task = by_name["task.md"].read_text()
    assert "## G09 " in material and "## G11 " in material
    assert ("## G10 " in material) == (variant == "main")
    assert ("## G12 " in material) == (variant == "main")
    assert "| 2025-02 | 未取得 |" in material
    assert "| 2026-09 | 124.0 |" in material
    assert "| 2020 | 100.0 |" in material
    assert "| 2026-10 |" not in material  # 尚未发布月不作为观察值输入。
    assert "评审专用判定" not in task + material
    assert "review-expectations" not in task + material
    if variant == "window":
        assert "24个月" in task and "3年" in task
        assert "单次" not in material  # 不注入无关政策及累计主题。
    else:
        assert "上年同期" in task
