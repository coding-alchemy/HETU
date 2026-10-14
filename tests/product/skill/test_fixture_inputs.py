import json
import shutil
from pathlib import Path

import pytest

from tests.product.skill.material_fixtures import ROOT, materialize_input


def _text(paths: tuple[Path, ...]) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in paths if p.suffix == ".md")


def _stage_two_boundary(paths: tuple[Path, ...]) -> None:
    text = _text(paths)
    assert "营业收入 100" in text or "营业收入 100／" in text
    assert "分部收入 A=60" not in text
    assert "一次性收益 3" not in text


def test_partial_material_never_contains_later_notes(tmp_path: Path) -> None:
    _stage_two_boundary(materialize_input("B16", "main", "02", tmp_path / "s2"))
    text = _text(materialize_input("B16", "main", "03", tmp_path / "s3"))
    assert "分部收入 A=60" in text
    assert "一次性收益 3" in text


@pytest.mark.parametrize("variant", ["closed", "industry-closed"])
def test_isolated_reuse_off_has_no_old_values(tmp_path: Path, variant: str) -> None:
    text = _text(materialize_input("B17", variant, "01", tmp_path / variant))
    assert "reuse_previous_task_data=false" in text
    for forbidden in ("T1-root", "P1", "W-A-root", "100", "10.2"):
        assert forbidden not in text


def test_deliberate_old_clue_remains_input_not_a_file_to_read(tmp_path: Path) -> None:
    paths = materialize_input("B17", "integration-off", "01", tmp_path / "old-clue")
    text = _text(paths)
    assert "10.2" in text and ".hetu/research/jia-20260331/" in text
    assert {p.name for p in paths} == {"task.md", "material.md", "selection.json"}


def test_copy_variant_keeps_the_permitted_original_facts(tmp_path: Path) -> None:
    text = _text(materialize_input("B17", "copy", "01", tmp_path / "copy"))
    assert "营业收入 100、净利润 10、经营活动现金流量净额 12" in text
    assert "2026-04-20 19:00+08:00" in text
    assert "T1 的旧目录 T1-root 不在 T2 的允许输入集" in text
    assert "Q3-R01" not in text and "复用开关 false" not in text


def test_new_time_and_dual_sections_are_preserved(tmp_path: Path) -> None:
    paths = materialize_input("B11", "main", "02", tmp_path / "b11")
    selection = json.loads((tmp_path / "b11/selection.json").read_text())
    assert selection["context"] == "independent"
    assert selection["as_of"] == ["2026-07-02T12:00:00+08:00"]
    assert "第 407 条" in _text(paths)
    text = _text(materialize_input("B14", "main", "01", tmp_path / "b14"))
    assert "2026-03-31T12:00:00+08:00" in text
    assert "2026-06-30T12:00:00+08:00" in text


def test_only_current_assets_are_copied(tmp_path: Path) -> None:
    early = materialize_input("B13", "main", "01", tmp_path / "early")
    assert "market-cap.json" not in {p.name for p in early}
    late = materialize_input("B13", "main", "03", tmp_path / "late")
    assert "market-cap.json" in {p.name for p in late}
    pdfs = materialize_input("B15", "main", "01", tmp_path / "pdf")
    for name in ("corrupt.pdf", "encrypted.pdf", "no-text.pdf"):
        target = next(p for p in pdfs if p.name == name)
        assert target.read_bytes() == (ROOT / "assets/B15" / name).read_bytes()
        assert not target.is_symlink()


@pytest.mark.parametrize(
    "case,variant,stage", [("B99", "main", "01"), ("B16", "missing", "01"), ("B16", "main", "99")]
)
def test_unknown_selection_creates_nothing(
    tmp_path: Path, case: str, variant: str, stage: str
) -> None:
    output = tmp_path / "unknown"
    with pytest.raises(KeyError):
        materialize_input(case, variant, stage, output)
    assert not output.exists()


def test_contaminated_route_fails_the_boundary_check(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    shutil.copytree(ROOT, root)
    path = root / "catalog.json"
    data = json.loads(path.read_text())
    data["B16/main/02"]["material"] += data["B16/main/03"]["material"]
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(AssertionError):
        _stage_two_boundary(
            materialize_input("B16", "main", "02", tmp_path / "bad", root=root)
        )


def test_review_file_cannot_be_routed_into_an_input(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    shutil.copytree(ROOT, root)
    path = root / "catalog.json"
    data = json.loads(path.read_text())
    data["B16/main/02"]["task"] = [["review-expectations/B16.md", "B16-001"]]
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "review-leak"
    with pytest.raises(KeyError):
        materialize_input("B16", "main", "02", output, root=root)
    assert not output.exists()


def test_reused_task_and_originals_have_one_maintained_copy() -> None:
    data = json.loads((ROOT / "catalog.json").read_text())
    first = data["B16/main/01"]["task"]
    assert first == data["B16/main/02"]["task"] == data["B16/main/03"]["task"]
    bodies = []
    for path in [ROOT / "shared.md", *sorted((ROOT / "inputs").rglob("*.md"))]:
        for block in path.read_text(encoding="utf-8").split("<!-- fixture:")[1:]:
            bodies.append(block.split("-->\n", 1)[1].strip())
    assert len(bodies) == len(set(bodies))


def test_every_registered_selection_is_individually_materializable(tmp_path: Path) -> None:
    data = json.loads((ROOT / "catalog.json").read_text())
    assert {key.split("/")[0] for key in data} == {
        *{f"B{i:02d}" for i in range(1, 19)}, "DQ01", "DQ02", "DS01", "DS02",
        "QS01", "QS02", "SS01", "SS02", "SS03", "SS04",
    }
    assert len(data) == 66
    for key in data:
        case, variant, stage = key.split("/")
        paths = materialize_input(case, variant, stage, tmp_path / key.replace("/", "-"))
        assert len(paths) == len(set(paths))
        assert all(p.parent == paths[0].parent for p in paths)
        assert not any("review" in p.name or p.name == "catalog.json" for p in paths)
        assert "独立判定依据" not in _text(paths)

def test_depth_task_goals_stay_neutral(tmp_path: Path) -> None:
    for case, variant, stage in (
        ("DQ01", "main", "01"), ("DQ02", "missing", "01"), ("DQ02", "model", "01"),
        ("DS01", "main", "01"), ("DS01", "main", "02"),
        ("DS02", "covered", "01"), ("DS02", "missing", "01"), ("DS02", "downgrade", "01"),
    ):
        paths = materialize_input(case, variant, stage, tmp_path / f"{case}-{variant}-{stage}")
        task = (tmp_path / f"{case}-{variant}-{stage}/task.md").read_text(encoding="utf-8")
        expected_depth = "quick" if case.startswith("DQ") else "standard"
        assert f"请求深度 {expected_depth}" in task
        # 评分答案不得混入执行输入（中性目标，见 03-A 计划）。
        for forbidden in ("不许归因", "别漏担保", "这是缺失", "覆盖完整", "不得降级"):
            assert forbidden not in task
        # 材料与任务分离：诱导与缺失事实只能出现在 material.md。
        material = (tmp_path / f"{case}-{variant}-{stage}/material.md").read_text(encoding="utf-8")
        assert "买入评级" in material or case in ("DS02", "DS01")
        assert {p.name for p in paths} == {"task.md", "material.md", "selection.json"}


def test_depth_variants_are_isolated_from_each_other(tmp_path: Path) -> None:
    base = _text(materialize_input("DQ01", "main", "01", tmp_path / "dq01"))
    assert "2025 年度：营业收入 10,200" in base
    assert "2026-03-02 至 2026-03-31 的交易日数据未取得" not in base
    missing = _text(materialize_input("DQ02", "missing", "01", tmp_path / "dq02-missing"))
    assert "2025 年度：营业收入 10,200" not in missing  # 必要期间三表被移除
    assert "2026-03-02 至 2026-03-31 的交易日数据未取得" in missing
    model = _text(materialize_input("DQ02", "model", "01", tmp_path / "dq02-model"))
    assert "未披露分产品利润" not in model or "分产品利润未在公司层面单独披露" in model
    assert "32.61% 变为 30.00%" in model and "2026-03-02 至 2026-03-31" not in model


def test_ds_stages_and_coverage_variants(tmp_path: Path) -> None:
    stage1 = _text(materialize_input("DS01", "main", "01", tmp_path / "ds01-1"))
    assert "担保金额 1,800" in stage1 and "W6 未定稿" in stage1
    assert "乐观 11,800" not in stage1
    stage2 = _text(materialize_input("DS01", "main", "02", tmp_path / "ds01-2"))
    assert "乐观 11,800" in stage2 and "担保金额 1,800" not in stage2
    selection2 = json.loads((tmp_path / "ds01-2/selection.json").read_text())
    assert selection2["context"] == "independent"
    assert selection2["as_of"] == ["2026-07-02T12:00:00+08:00"]
    covered = _text(materialize_input("DS02", "covered", "01", tmp_path / "ds02-a"))
    assert "事件（2 条，均含正文）" in covered and "12 条" not in covered
    missing = _text(materialize_input("DS02", "missing", "01", tmp_path / "ds02-b"))
    assert "命中 12 条" in missing and "正文与附件未取得" in missing
    downgrade = _text(materialize_input("DS02", "downgrade", "01", tmp_path / "ds02-c"))
    assert "无任何行情数值" in downgrade and "日线" not in downgrade.replace(
        "行情窗口（Q02）输入——日线采集返回 403 失败信封", ""
    )
    assert "244 个交易日" not in downgrade


SCOPE_SELECTIONS = (
    ("QS01", "main"), ("QS02", "base"), ("QS02", "missing"),
    ("SS01", "industrial"), ("SS01", "financial"),
    ("SS02", "income"), ("SS02", "goodwill"),
    ("SS03", "base"), ("SS03", "missing"), ("SS04", "main"),
)


@pytest.mark.parametrize(("case", "variant"), SCOPE_SELECTIONS)
def test_scope_selections_are_neutral_and_isolated(
    tmp_path: Path, case: str, variant: str
) -> None:
    output = tmp_path / f"{case}-{variant}"
    paths = materialize_input(case, variant, "01", output)
    task = (output / "task.md").read_text(encoding="utf-8")
    selection = json.loads((output / "selection.json").read_text(encoding="utf-8"))
    assert selection["context"] == "new"
    assert len(selection["as_of"]) == 1
    assert f"请求深度 {'quick' if case.startswith('Q') else 'standard'}" in task
    assert "局部" in task
    assert "独立判定依据" not in _text(paths)
    for answer in ("必须判通过", "不许归因", "别漏担保", "4.4444", "应当降档"):
        assert answer not in task
    names = {p.name for p in paths}
    expected = {"task.md", "material.md", "selection.json"}
    if (case, variant) == ("QS02", "missing"):
        expected |= {"latest-balance.txt", "missing-response.json"}
    assert names == expected


def _scope_missing_boundary(paths: tuple[Path, ...]) -> None:
    text = _text(paths)
    assert "2025Q1 归母净利润：-15" not in text
    assert "2025Q1 归母净利润：本次未取得" in text


def test_scope_missing_input_and_pollution_counterexample(tmp_path: Path) -> None:
    base = _text(materialize_input("QS02", "base", "01", tmp_path / "base"))
    assert "2025Q1 归母净利润：-15" in base
    _scope_missing_boundary(materialize_input("QS02", "missing", "01", tmp_path / "missing"))
    root = tmp_path / "corpus"
    shutil.copytree(ROOT, root)
    path = root / "catalog.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["QS02/missing/01"]["material"].append(
        ["inputs/depth/scope-quick.md", "QS02-BASE"]
    )
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(AssertionError):
        _scope_missing_boundary(
            materialize_input("QS02", "missing", "01", tmp_path / "polluted", root=root)
        )


def test_scope_forecast_and_goodwill_variants_do_not_leak(tmp_path: Path) -> None:
    forecast = _text(materialize_input("SS03", "missing", "01", tmp_path / "forecast"))
    assert "单位成本报价范围 5.7–6.0" not in forecast
    assert "单位成本资料本次未取得" in forecast
    income = _text(materialize_input("SS02", "income", "01", tmp_path / "income"))
    risk = _text(materialize_input("SS02", "goodwill", "01", tmp_path / "risk"))
    assert "未形成商誉" in income and "商誉 2,700" not in income
    assert "商誉 2,700" in risk and "未形成商誉" not in risk
