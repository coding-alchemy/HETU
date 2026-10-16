"""03.2 独立两榜、席位与重复：合成宇宙的结构断言与手算核算。

宇宙事实由 fixtures.ranking_universe / mini_boards 提供；选中板块、席位、
并列处理与排除的全部预期在本文件断言侧。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from fixtures import SYNTHETIC_THEME_AUDIT, annotate_actions_coverage, make_case
from hetu_quant.data import DataStore
from hetu_quant.models import RankingDefinition, ResultRef
from hetu_quant.ranking import run_ranking

CN = ZoneInfo("Asia/Shanghai")
AS_OF = datetime(2024, 7, 1, 9, 0, tzinfo=CN)
CUTOFF = date(2024, 6, 28)
# 覆盖起点取 2023-11-30：本文件过去任务（cutoff 2024-05-31）的半年锚点，
# 已核验空表须覆盖所有可能任务窗口
WINDOW_START = "2023-11-30"
WINDOW_END = "2024-06-28"


def _build_store(tmp_path: Path, case: str, variant: str) -> tuple[DataStore, object]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    config_path = tmp_path / "products.json"
    config_text = json.dumps(
        make_case("product_config_full", "base"), ensure_ascii=False
    )
    config_path.write_text(config_text, encoding="utf-8")
    store = DataStore(tmp_path / "store", config_path=config_path)
    (tmp_path / "store" / "data-products.json").write_text(
        config_text, encoding="utf-8"
    )
    data = make_case(case, variant)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(data["manifest"], ensure_ascii=False), encoding="utf-8"
    )
    files = []
    for name, content in data["files"].items():
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        files.append(path)
    ref = store.import_files(manifest_path, tuple(files))
    # 已核验空 corporate_actions 表：按代码分区补齐 coverage 元数据（R1），
    # "覆盖未取齐"反例测试自行构造清单，不走本 helper
    annotate_actions_coverage(store, ref, WINDOW_START, WINDOW_END)
    return store, ref


def _import_case(tmp_path: Path, data: dict) -> tuple[DataStore, object]:
    """导入已被测试改写过的 case 文件（反例测试用）；不带 coverage 标注。"""
    tmp_path.mkdir(parents=True, exist_ok=True)
    config_text = json.dumps(
        make_case("product_config_full", "base"), ensure_ascii=False
    )
    config_path = tmp_path / "products.json"
    config_path.write_text(config_text, encoding="utf-8")
    store = DataStore(tmp_path / "store", config_path=config_path)
    (tmp_path / "store" / "data-products.json").write_text(
        config_text, encoding="utf-8"
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(data["manifest"], ensure_ascii=False), encoding="utf-8"
    )
    paths = []
    for name, content in data["files"].items():
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        paths.append(path)
    ref = store.import_files(manifest_path, tuple(paths))
    return store, ref


def _definition(
    ref,
    cutoff: date = CUTOFF,
    as_of: datetime = AS_OF,
) -> RankingDefinition:
    return RankingDefinition(
        as_of=as_of,
        cutoff=cutoff,
        snapshot_id=ref.snapshot_id,
        benchmark_id="csi300_price",
        parameters={
            "purpose": "ranking_current",
            # v2 分类门：合成主题核验条目（THM1–THM6 = industrial_theme）
            "category": {
                "theme_basis": {"audited_boards": SYNTHETIC_THEME_AUDIT}
            },
        },
    )


def _read_tables(result: ResultRef) -> dict[str, pd.DataFrame]:
    result_dir = result.root / "results" / result.result_id
    return {
        p.stem: pd.read_parquet(p)
        for p in sorted(result_dir.glob("*.parquet"))
    }


def _run(
    tmp_path: Path, case: str, variant: str, **kwargs
) -> tuple[ResultRef, dict[str, pd.DataFrame]]:
    store, ref = _build_store(tmp_path, case, variant)
    result = run_ranking(_definition(ref, **kwargs), ref, tmp_path / "out")
    result_dir = result.root / "results" / result.result_id
    tables = {
        path.stem: pd.read_parquet(path)
        for path in sorted(result_dir.glob("*.parquet"))
    }
    return result, tables


def test_two_lists_select_five_boards_each_and_fifty_seats(tmp_path: Path) -> None:
    result, tables = _run(tmp_path, "ranking_universe", "base")
    boards = tables["boards"]
    slots = tables["slots"]
    industries = boards[boards["board_kind"] == "industry"]
    themes = boards[boards["board_kind"] == "theme"]
    assert len(industries) == 6, "两榜分别保留全部合格比较对象"
    assert len(themes) == 6
    assert industries["selected"].sum() == 5
    assert themes["selected"].sum() == 5
    assert set(slots[slots["board_id"].str.startswith("IND")]["board_id"]) == set(
        industries[industries["selected"]]["board_id"]
    )
    assert len(slots) == 50, "席位最多 50"
    assert slots["board_id"].str.startswith("THM").sum() == 25
    assert slots["board_id"].str.startswith("IND").sum() == 25
    assert result.status == "complete"


def test_duplicate_stock_keeps_seats_without_backfill(tmp_path: Path) -> None:
    """600901.SH 同时为 THM1、THM2 最强成员：两席保留、duplicate_count=1、不补位。"""
    _, tables = _run(tmp_path, "ranking_universe", "base")
    slots = tables["slots"]
    dup = slots[slots["code"] == "600901.SH"]
    assert len(dup) == 2, "重复股在两榜各保留一个席位"
    assert (dup["duplicate_count"] == 1).all()
    assert len(slots) == 50, "重复股不占额外席位、不去重补位"
    assert slots["code"].nunique() < 50
    assert (slots.groupby("code")["duplicate_count"].first() >= 1).sum() == 1, (
        "只有 600901.SH 一只重复股"
    )
    overlap = tables["overlap"]
    pair = overlap[
        (overlap["board_a"] == "THM1") & (overlap["board_b"] == "THM2")
    ]
    assert len(pair) == 1 and pair.iloc[0]["intersection_count"] >= 1


def test_boundary_ties_cut_by_code_shown_without_backfill(tmp_path: Path) -> None:
    """THM5 与 THM6 完全并列第 5 席：按代码截取 THM5，THM6 保留展示且不补位。"""
    _, tables = _run(tmp_path, "ranking_universe", "base")
    boards = tables["boards"]
    thm5 = boards[boards["board_id"] == "THM5"].iloc[0]
    thm6 = boards[boards["board_id"] == "THM6"].iloc[0]
    assert thm5["score"] == pytest.approx(thm6["score"])
    assert thm5["rank"] == thm6["rank"], "总分并列保持同名次"
    assert bool(thm5["selected"]) and not bool(thm6["selected"])
    assert "截取" in str(thm6["limitations"]) and "并列" in str(thm6["limitations"]), (
        "边界并列对象须展示并列与截取说明"
    )
    slots = tables["slots"]
    assert "THM6" not in set(slots["board_id"]), "不补位：THM6 不得进入席位"
    assert len(slots) == 50
    # THM1 内部第 5 席两只股票并列：取 5 只、并列说明入排除记录
    thm1_slots = slots[slots["board_id"] == "THM1"]
    assert len(thm1_slots) == 5
    exclusions = tables["exclusions"]
    tie_notes = exclusions[
        (exclusions["scope"] == "slot_boundary_tie")
        & (exclusions["entity_id"] == "THM1")
    ]
    assert len(tie_notes) == 1
    note = str(tie_notes.iloc[0]["reason"])
    assert "600905.SH" in note or "600906.SH" in note


def test_excluded_members_recorded_with_reasons(tmp_path: Path) -> None:
    """ST、低流动性、缺窗口日、停牌列观察；未知类别不入主题榜。"""
    _, tables = _run(tmp_path, "ranking_universe", "base")
    exclusions = tables["exclusions"]
    for code, reason in (
        ("600907.SH", "risk_alert"),
        ("600917.SH", "liquidity"),
        ("600937.SH", "missing_sessions"),
        ("600047.SH", "suspended"),
    ):
        hit = exclusions[exclusions["entity_id"] == code]
        assert len(hit), f"{code} 必须有排除记录"
        joined = " ".join(str(item) for item in hit["reason"])
        assert reason in joined, f"{code}: 缺 {reason}"
    excluded = exclusions[exclusions["scope"] == "classification"]
    assert "STY1" in set(excluded["entity_id"])
    boards = tables["boards"]
    assert "STY1" not in set(boards["board_id"])
    # 观察对象不参与排序
    metrics = tables["metrics"]
    for code in ("600907.SH", "600917.SH", "600937.SH", "600047.SH"):
        assert code not in set(metrics["entity_id"])
    # 短历史成员合格但带标记
    short_row = metrics[metrics["entity_id"] == "600927.SH"].iloc[0]
    assert bool(short_row["short_history"]) is True


def test_theme_uses_member_median_not_index_and_stock_scores_not_in_board(
    tmp_path: Path,
) -> None:
    """主题指标 = 合格当前成员各原始特征中位数；股票分数不计入板块分数。"""
    _, tables = _run(tmp_path, "ranking_universe", "base")
    metrics = tables["metrics"]
    is_board = (metrics["entity_kind"] == "board") & (metrics["group_id"] == "")
    board_rows = metrics[is_board]
    stock_rows = metrics[metrics["entity_kind"] == "stock"]
    for theme in ("THM1", "THM2", "THM5"):
        board_row = board_rows[board_rows["entity_id"] == theme].iloc[0]
        members = stock_rows[stock_rows["group_id"] == theme]
        assert len(members) == 6, f"{theme} 应只聚合 6 只合格成员"
        median_excess = members["excess_6m"].median()
        assert board_row["excess_6m"] == pytest.approx(median_excess), (
            "主题必须用成员中位数聚合，不能用主题指数替代"
        )
        assert board_row["score"] != pytest.approx(members["score"].sum()), (
            "股票分数不得计入板块分数"
        )


def test_theme_coverage_boundary_79_fails_80_passes(tmp_path: Path) -> None:
    low_result, low_tables = _run(tmp_path / "low", "ranking_universe", "coverage79")
    ok_result, ok_tables = _run(tmp_path / "ok", "ranking_universe", "coverage80")
    low_boards = low_tables["boards"]
    assert "THM6" not in set(low_boards["board_id"]), "覆盖率不足的板块不入榜"
    low_excl = low_tables["exclusions"]
    hit = low_excl[low_excl["entity_id"] == "THM6"]
    assert len(hit) and any("coverage" in str(item) for item in hit["reason"])
    assert any("0.79" in str(item) for item in hit["reason"]), "覆盖率如实展示"
    ok_boards = ok_tables["boards"]
    thm6_ok = ok_boards[ok_boards["board_id"] == "THM6"].iloc[0]
    assert thm6_ok["coverage"] == pytest.approx(0.80)
    ok_themes = ok_boards[ok_boards["board_kind"] == "theme"]
    assert len(ok_themes) == 6, "80% 合格仍保留全部合格主题"


def test_future_member_not_in_past_task(tmp_path: Path) -> None:
    """600936.SH 成员生效日 2024-06-10：可服务 6-28 任务，不得进入 5-31 任务。"""
    store, ref = _build_store(tmp_path, "ranking_universe", "future_member")
    current = run_ranking(_definition(ref), ref, tmp_path / "out1")
    past = run_ranking(
        _definition(
            ref,
            cutoff=date(2024, 5, 31),
            as_of=datetime(2024, 6, 1, 12, 0, tzinfo=CN),
        ),
        ref,
        tmp_path / "out2",
    )
    current_tables = _read_tables(current)
    past_tables = _read_tables(past)
    current_members = current_tables["metrics"]
    past_members = past_tables["metrics"]
    assert "600936.SH" in set(
        current_members[current_members["group_id"] == "THM4"]["entity_id"]
    )
    thm4_past = past_members[past_members["group_id"] == "THM4"]
    assert "600936.SH" not in set(thm4_past["entity_id"]), "未来成员不得进入过去任务"
    assert len(thm4_past) == 5
    thm4_board = past_tables["boards"]
    row = thm4_board[thm4_board["board_id"] == "THM4"].iloc[0]
    assert row["eligible_members"] == 5


def test_handcalc_average_rank_and_four_dimension_contributions(tmp_path: Path) -> None:
    """MINIA/B/B2/C 四对象：平均名次、并列同名次与四维贡献逐一手算。"""
    _, tables = _run(tmp_path, "mini_boards", "base")
    metrics = tables["metrics"]
    boards = metrics[(metrics["entity_kind"] == "board") & (metrics["group_id"] == "")]
    by_id = {row["entity_id"]: row for _, row in boards.iterrows()}
    assert by_id["MINIA"]["return_6m"] == pytest.approx(0.20)
    assert by_id["MINIA"]["return_3m"] == pytest.approx(1.20 / 1.10 - 1)
    assert by_id["MINIB"]["return_6m"] == pytest.approx(0.10)
    assert by_id["MINIB"]["return_3m"] == pytest.approx(1.10 / 1.05 - 1)
    assert by_id["MINIC"]["return_6m"] == pytest.approx(0.0)
    for entity in ("MINIA", "MINIB", "MINIB2", "MINIC"):
        assert by_id[entity]["excess_6m"] == pytest.approx(by_id[entity]["return_6m"])

    def pct(values: list[float], own: float) -> float:
        ranks = pd.Series(values).rank(method="average")
        return float(ranks[values.index(own)] - 1) / (len(values) - 1) * 100

    ids = ("MINIA", "MINIB", "MINIB2", "MINIC")
    excess_6m = [float(by_id[e]["excess_6m"]) for e in ids]
    excess_3m = [float(by_id[e]["excess_3m"]) for e in ids]
    dd = [float(by_id[e]["max_drawdown_6m"]) for e in ids]
    up = [float(by_id[e]["up_amount_60d"]) for e in ids]
    vol = [float(by_id[e]["volatility_60d"]) for e in ids]
    order = ids
    for index, entity in enumerate(order):
        trend_3m = pct(excess_3m, excess_3m[index])
        trend_6m = pct(excess_6m, excess_6m[index])
        trend = (trend_3m + trend_6m) / 2
        drawdown_pct = 100 - pct(dd, dd[index])
        volume_pct = pct(up, up[index])
        risk_pct = 100 - pct(vol, vol[index])
        expected = (trend + drawdown_pct + volume_pct + risk_pct) / 4
        assert by_id[entity]["trend_pct"] == pytest.approx(trend)
        assert by_id[entity]["drawdown_pct"] == pytest.approx(drawdown_pct)
        assert by_id[entity]["volume_pct"] == pytest.approx(volume_pct)
        assert by_id[entity]["risk_pct"] == pytest.approx(risk_pct)
        assert by_id[entity]["score"] == pytest.approx(expected)
    # B 与 B2 完全并列：同名次、同分，展示按代码稳定顺序
    rank_b = tables["boards"]
    row_b = rank_b[rank_b["board_id"] == "MINIB"].iloc[0]
    row_b2 = rank_b[rank_b["board_id"] == "MINIB2"].iloc[0]
    assert row_b["rank"] == row_b2["rank"]
    assert row_b["score"] == pytest.approx(row_b2["score"])


def test_single_dimension_change_only_moves_own_contribution(tmp_path: Path) -> None:
    """只给 600101.SH 加中段回撤：只有它的回撤原始指标变化，
    派生百分位变化限于所属组。
    """
    _, base_tables = _run(tmp_path / "a", "mini_boards", "base")
    _, shock_tables = _run(tmp_path / "b", "mini_boards", "drawdown_shock")
    base = base_tables["metrics"]
    shock = shock_tables["metrics"]
    raw_keys = (
        "return_3m", "return_6m", "excess_3m", "excess_6m",
        "max_drawdown_6m", "up_amount_60d", "volatility_60d",
    )
    pct_keys = ("trend_pct", "drawdown_pct", "volume_pct", "risk_pct", "score")

    def diff(
        base_row: pd.Series, shock_row: pd.Series, keys: tuple[str, ...]
    ) -> list[str]:
        changed = []
        for key in keys:
            left, right = base_row[key], shock_row[key]
            if pd.isna(left) and pd.isna(right):
                continue
            if not left == pytest.approx(right):
                changed.append(key)
        return changed

    seen_shock = False
    for _, base_row in base.iterrows():
        shock_row = shock[shock.index == base_row.name].iloc[0]
        changed_raw = diff(base_row, shock_row, raw_keys)
        changed_pct = diff(base_row, shock_row, pct_keys)
        if base_row["entity_id"] == "600101.SH" and base_row["entity_kind"] == "stock":
            seen_shock = True
            assert changed_raw == ["max_drawdown_6m"], changed_raw
            assert {"drawdown_pct", "score"} <= set(changed_pct), changed_pct
        else:
            assert changed_raw == [], (
                f"{base_row['entity_id']}: 原始指标不应变化 {changed_raw}"
            )
    assert seen_shock, "必须存在受冲击股票行"
    # 百分位派生变化只出现在 MINIA 组（板块行 + 组内股票），其他组不受影响
    minia_entities = {
        "MINIA", "600101.SH", "600102.SH", "600103.SH", "600104.SH", "600105.SH",
    }
    for _, base_row in base.iterrows():
        shock_row = shock[shock.index == base_row.name].iloc[0]
        changed_pct = diff(base_row, shock_row, pct_keys)
        if changed_pct:
            inside = (
                base_row["entity_id"] in minia_entities
                or base_row["group_id"] == "MINIA"
            )
            assert inside, (
                f"{base_row['entity_id']}/{base_row['group_id']}: "
                f"组外不应出现百分位变化 {changed_pct}"
            )


def test_limited_status_when_inputs_insufficient(tmp_path: Path) -> None:
    """小宇宙只有 4 个合格行业、无主题：允许保存 limited，不伪装 complete。"""
    result, tables = _run(tmp_path, "mini_boards", "base")
    assert result.status == "limited"
    boards = tables["boards"]
    assert len(boards[boards["board_kind"] == "theme"]) == 0
    assert len(boards[boards["board_kind"] == "industry"]) == 4, "合格行业全部保留"
    assert len(tables["slots"]) == 20
    result_dir = result.root / "results" / result.result_id
    manifest = json.loads((result_dir / "result.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "limited"
    assert manifest["limitations"], "受限必须带缺口说明"


def test_calendar_beyond_cutoff_does_not_clear_lists(tmp_path: Path) -> None:
    """日历含 cutoff 之后的交易日（多年日历或盘中取数）：

    窗口与流动性检查一律以 cutoff 为锚，不得把未来日历日当作应有行情，
    结果须与 base 完全一致（complete、50 席、无误排除）。
    """
    data = make_case("ranking_universe", "base")
    calendar = data["files"]["calendar.csv"]
    extra = "\n".join(
        f"{day},True"
        for day in ("2024-07-01", "2024-07-02", "2024-07-03", "2024-07-04",
                    "2024-07-05")
    )
    data["files"]["calendar.csv"] = calendar + "\n" + extra

    config_text = json.dumps(
        make_case("product_config_full", "base"), ensure_ascii=False
    )
    config_path = tmp_path / "products.json"
    config_path.write_text(config_text, encoding="utf-8")
    store = DataStore(tmp_path / "store", config_path=config_path)
    (tmp_path / "store" / "data-products.json").write_text(
        config_text, encoding="utf-8"
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(data["manifest"], ensure_ascii=False), encoding="utf-8"
    )
    files = []
    for name, content in data["files"].items():
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        files.append(path)
    ref = store.import_files(manifest_path, tuple(files))
    # 已核验空 corporate_actions 表：按代码分区补齐 coverage 元数据（R1）
    annotate_actions_coverage(store, ref, WINDOW_START, WINDOW_END)

    result = run_ranking(_definition(ref), ref, tmp_path / "out")
    tables = _read_tables(result)
    assert result.status == "complete"
    assert len(tables["slots"]) == 50
    exclusions = tables["exclusions"]
    wrongly_excluded = exclusions[
        exclusions["reason"].str.contains("成交额不齐", na=False)
    ]
    assert len(wrongly_excluded) == 0, "cutoff 之后的日历日不得触发误排除"


def test_saved_definition_freezes_full_protocol(tmp_path, monkeypatch):
    """B2 反例：保存定义后修改 ranking-defaults.json 权重，同一已保存定义再跑
    结果逐表一致；不同冻结参数的两个定义产出可区分结果。"""
    import hetu_quant.ranking as ranking_module
    from hetu_quant.experiments import ExperimentStore

    _, ref = _build_store(tmp_path / "s", "mini_boards", "base")
    experiments = ExperimentStore(tmp_path / "s" / "exp")
    definition_id = experiments.save_definition("ranking", _definition(ref))
    frozen = experiments.read_definition(definition_id)
    assert frozen.frozen_protocol is not None, "保存定义必须固化当时完整有效协议"
    assert frozen.frozen_protocol["ranking"]["dimension_weights"]["risk"] == 0.25

    before = run_ranking(frozen, ref, tmp_path / "out1")
    before_tables = _read_tables(before)

    patched = json.loads(json.dumps(ranking_module._default_parameters()))
    patched["ranking"]["dimension_weights"] = {
        "trend": 0.70,
        "drawdown": 0.10,
        "volume": 0.10,
        "risk": 0.10,
    }
    monkeypatch.setattr(ranking_module, "_default_parameters", lambda: patched)

    after = run_ranking(frozen, ref, tmp_path / "out2")
    after_tables = _read_tables(after)
    for name in ranking_module.TABLE_NAMES:
        pd.testing.assert_frame_equal(
            before_tables[name], after_tables[name], obj=name
        ), f"已保存定义不得因默认配置修改而变化：{name}"
    manifest_after = json.loads(
        (after.root / "results" / after.result_id / "result.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest_after["parameters"]["ranking"]["dimension_weights"]["risk"] == (
        0.25
    ), "结果清单记录的是冻结参数"

    other_id = experiments.save_definition("ranking", _definition(ref))
    other = experiments.read_definition(other_id)
    assert other.frozen_protocol["ranking"]["dimension_weights"]["trend"] == 0.70
    different = run_ranking(other, ref, tmp_path / "out3")
    different_tables = _read_tables(different)
    assert not before_tables["metrics"].equals(different_tables["metrics"]), (
        "两个冻结参数不同的定义必须产出可区分结果"
    )


def test_split_stock_without_actions_coverage_is_excluded_not_complete(
    tmp_path,
):
    """B1 反例：拆股（末日 −50% 假回撤）且公司行动未取齐：按资格排除并记录
    原因，结果如实 limited，不得伪装 complete。"""
    data = make_case("ranking_universe", "base")
    files = data["files"]
    lines = files["daily_bars.csv"].splitlines()
    target = max(i for i, line in enumerate(lines) if ",600011.SH," in line)
    parts = lines[target].split(",")
    assert float(parts[2]) > 50, "基线末日收盘应为未拆股价格"
    parts[2] = f"{float(parts[2]) / 2:.6f}"
    lines[target] = ",".join(parts)
    files["daily_bars.csv"] = "\n".join(lines) + "\n"
    # 移除已核验空表 = 公司行动覆盖未取齐
    del files["corporate_actions.csv"]
    data["manifest"]["items"] = [
        item
        for item in data["manifest"]["items"]
        if item["product_id"] != "test_actions"
    ]

    _, ref = _import_case(tmp_path, data)
    result = run_ranking(_definition(ref), ref, tmp_path / "out")
    tables = _read_tables(result)
    assert result.status == "limited", "行动未取齐不得伪装 complete"
    hit = tables["exclusions"][
        (tables["exclusions"]["entity_id"] == "600011.SH")
        & (tables["exclusions"]["scope"] == "eligibility")
    ]
    assert len(hit) and "corporate_actions_coverage" in str(hit.iloc[0]["reason"]), (
        "拆股对象必须按公司行动覆盖资格排除并记录原因"
    )
    assert "600011.SH" not in set(tables["metrics"]["entity_id"]), (
        "未复权假回撤不得进入指标表"
    )
    assert len(tables["slots"]) == 0, "全部需复权对象排除后零席位"
    assert len(tables["boards"]) == 0
    assert "board_kind" in tables["boards"].columns, "空表保留固定列"
    manifest = json.loads(
        (result.root / "results" / result.result_id / "result.json").read_text(
            encoding="utf-8"
        )
    )
    assert any("corporate_actions 覆盖未核验" in item for item in manifest["limitations"])


def test_split_stock_with_only_other_code_actions_coverage_is_excluded(
    tmp_path,
):
    """R1 反例 1：行动表只有另一只股票的分区已核验时，目标股模拟拆股仍须
    按未核验排除（整表"已取齐"不能推导目标代码已覆盖），结果不得 complete。"""
    data = make_case("ranking_universe", "base")
    files = data["files"]
    lines = files["daily_bars.csv"].splitlines()
    target = max(i for i, line in enumerate(lines) if ",600011.SH," in line)
    parts = lines[target].split(",")
    assert float(parts[2]) > 50, "基线末日收盘应为未拆股价格"
    parts[2] = f"{float(parts[2]) / 2:.6f}"
    lines[target] = ",".join(parts)
    files["daily_bars.csv"] = "\n".join(lines) + "\n"
    # 快照只提供另一只股票（600012.SH）的行动记录
    files["corporate_actions.csv"] = (
        "code,ex_date,action_type,m,d,terms_raw,available_at\n"
        "600012.SH,2024-03-15,cash_dividend,1,0.5,10派5,2024-03-01\n"
    )
    store, ref = _import_case(tmp_path, data)
    annotate_actions_coverage(
        store, ref, WINDOW_START, WINDOW_END, codes=["600012.SH"]
    )

    result = run_ranking(_definition(ref), ref, tmp_path / "out")
    tables = _read_tables(result)
    assert result.status == "limited", "目标代码覆盖未核验不得 complete"
    hit = tables["exclusions"][
        (tables["exclusions"]["entity_id"] == "600011.SH")
        & (tables["exclusions"]["scope"] == "eligibility")
    ]
    assert len(hit) and "corporate_actions_coverage" in str(hit.iloc[0]["reason"]), (
        "目标股必须按对象级覆盖资格排除并记录原因"
    )
    assert "600011.SH" in str(hit.iloc[0]["reason"]), "排除原因须记录到代码粒度"
    assert "600011.SH" not in set(tables["metrics"]["entity_id"]), (
        "未复权假回撤不得进入指标表"
    )
    manifest = json.loads(
        (result.root / "results" / result.result_id / "result.json").read_text(
            encoding="utf-8"
        )
    )
    assert any("corporate_actions 覆盖未核验" in item for item in manifest["limitations"])


def test_actions_coverage_short_span_excludes_with_period_reason(
    tmp_path,
):
    """R1 反例 2：目标代码分区 coverage 只到窗口中段，后段发生拆股 → 按
    未核验排除，排除原因记录到代码与期间粒度。"""
    data = make_case("ranking_universe", "base")
    files = data["files"]
    lines = files["daily_bars.csv"].splitlines()
    target = max(i for i, line in enumerate(lines) if ",600011.SH," in line)
    parts = lines[target].split(",")
    assert float(parts[2]) > 50, "基线末日收盘应为未拆股价格"
    parts[2] = f"{float(parts[2]) / 2:.6f}"
    lines[target] = ",".join(parts)
    files["daily_bars.csv"] = "\n".join(lines) + "\n"
    store, ref = _import_case(tmp_path, data)
    annotate_actions_coverage(
        store,
        ref,
        WINDOW_START,
        WINDOW_END,
        only={"600011.SH": [WINDOW_START, "2024-03-28"]},
    )

    result = run_ranking(_definition(ref), ref, tmp_path / "out")
    tables = _read_tables(result)
    hit = tables["exclusions"][
        (tables["exclusions"]["entity_id"] == "600011.SH")
        & (tables["exclusions"]["scope"] == "eligibility")
    ]
    assert len(hit) and "corporate_actions_coverage" in str(hit.iloc[0]["reason"]), (
        "覆盖期间不足必须按资格排除并记录原因"
    )
    reason = str(hit.iloc[0]["reason"])
    assert "600011.SH" in reason and "2024-03-28" in reason, (
        "排除原因须记录到代码与期间粒度"
    )
    assert "600011.SH" not in set(tables["metrics"]["entity_id"]), (
        "未复权假回撤不得进入指标表"
    )


def test_industry_width_and_concentration_use_member_basis(tmp_path):
    """B3：行业宽度=合格当前成员站上 60 日均线比例；集中度代理=正收益成员
    前 5 占正收益总和比例（与主题同口径），独立手算核对。"""
    _, tables = _run(tmp_path, "board_width", "base")
    aux = tables["aux"]
    row = aux[
        (aux["entity_kind"] == "board") & (aux["entity_id"] == "WIND")
    ].iloc[0]
    # 手算：10 个成员中 6 个正收益成员末日收于 60 日均线上方 → width = 0.6
    assert row["width_60d_ma"] == pytest.approx(0.6)
    # 手算：正收益 (0.30,0.24,0.18,0.12,0.06,0.03)，前 5 和 0.90 / 总和 0.93
    positives = [0.30, 0.24, 0.18, 0.12, 0.06, 0.03]
    assert row["concentration_proxy"] == pytest.approx(
        sum(positives[:5]) / sum(positives)
    )
    metrics = tables["metrics"]
    stock_rows = metrics[metrics["entity_kind"] == "stock"]
    assert len(stock_rows) == 10
    rising = stock_rows[stock_rows["return_6m"] > 0]
    falling = stock_rows[stock_rows["return_6m"] < 0]
    assert len(rising) == 6 and len(falling) == 4, "6 涨 4 跌的混合构造"
    expected_returns = {
        "600110.SH": 0.30,
        "600111.SH": 0.24,
        "600112.SH": 0.18,
        "600113.SH": 0.12,
        "600114.SH": 0.06,
        "600115.SH": 0.03,
    }
    for _, item in rising.iterrows():
        assert item["return_6m"] == pytest.approx(
            expected_returns[item["entity_id"]]
        )


def test_ma60_width_uses_last_60_sessions_not_61(tmp_path):
    """R2 反例：尾窗首日（第 61 日，为算 60 个日收益多取）只进旧口径均值，
    "翻转"成员末日收盘介于两种口径均值之间——61 日窗口宽度 0.5，正确
    60 日口径宽度 0.6。手算：翻转成员尾窗 [150, 100×59, 100.5]，
    61 日均值 (150+5900+100.5)/61 ≈ 100.828 > 100.5（旧口径判下方），
    60 日均值 (5900+100.5)/60 ≈ 100.008 < 100.5（正确口径判上方）。"""
    _, tables = _run(tmp_path, "ma60_window", "base")
    aux = tables["aux"]
    row = aux[
        (aux["entity_kind"] == "board") & (aux["entity_id"] == "WIND")
    ].iloc[0]
    assert row["width_60d_ma"] == pytest.approx(0.6)
    # 翻转成员是合格成员（其 60 日口径 flag 计入宽度）；入选股票的 aux 行
    # 与成员口径同源（同一 ma60_above）
    assert "600129.SH" in set(tables["metrics"]["entity_id"]), (
        "翻转成员必须作为合格成员参与宽度计算"
    )
    aux_stocks = aux[(aux["entity_kind"] == "stock")].set_index("entity_id")
    for _, item in tables["slots"].iterrows():
        assert item["code"] in aux_stocks.index
        assert aux_stocks.loc[item["code"], "width_60d_ma"] in (0.0, 1.0)


def test_zero_seat_result_keeps_fixed_schema_and_exports(tmp_path):
    """B4：全排除（零席位）结果九张表列齐全、发布与导出可读。"""
    import io
    import zipfile

    from hetu_quant.ranking import TABLE_SCHEMAS
    from hetu_quant.reporting import export_result

    result, tables = _run(tmp_path, "mini_boards", "zero_seats")
    assert result.status == "limited"
    assert len(tables["slots"]) == 0, "零席位"
    for name, schema in TABLE_SCHEMAS.items():
        frame = tables[name]
        assert list(frame.columns) == list(schema), f"{name} 必须保留固定列集合"
    for name in ("boards", "slots", "stocks", "metrics", "cards", "overlap"):
        assert len(tables[name]) == 0, f"{name} 应为空壳"
    assert len(tables["exclusions"]) > 0, "排除依据留存"
    assert "board_kind" in tables["boards"].columns, "页面读取不再 KeyError"

    zip_path = export_result(result, tmp_path / "export" / f"{result.result_id}.zip")
    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
        for name in TABLE_SCHEMAS:
            assert f"{name}.csv" in names
        boards_csv = pd.read_csv(io.BytesIO(archive.read("boards.csv")))
        assert list(boards_csv.columns) == list(TABLE_SCHEMAS["boards"])
        assert len(boards_csv) == 0
        assert "cards.md" in names
    # Notebook 读取路径：parquet 直接可读
    for name in TABLE_SCHEMAS:
        again = pd.read_parquet(
            result.root / "results" / result.result_id / f"{name}.parquet"
        )
        assert list(again.columns) == list(TABLE_SCHEMAS[name])
