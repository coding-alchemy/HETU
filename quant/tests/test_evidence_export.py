"""03.3 研究卡反例与只读导出：证据边界、市场下跌措辞、导出再读一致性。"""

from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from fixtures import annotate_actions_coverage, make_case
from hetu_quant.data import DataStore
from hetu_quant.models import RankingDefinition, ResultRef
from hetu_quant.ranking import run_ranking
from hetu_quant.reporting import export_result

CN = ZoneInfo("Asia/Shanghai")
AS_OF = datetime(2024, 7, 1, 9, 0, tzinfo=CN)
CUTOFF = date(2024, 6, 28)
WINDOW_START = "2023-12-28"
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
    # 已核验空 corporate_actions 表：按代码分区补齐 coverage 元数据（R1）
    annotate_actions_coverage(store, ref, WINDOW_START, WINDOW_END)
    return store, ref


def _run(tmp_path: Path, variant: str) -> tuple[ResultRef, dict[str, pd.DataFrame]]:
    store, ref = _build_store(tmp_path, "ranking_universe", variant)
    definition = RankingDefinition(
        as_of=AS_OF,
        cutoff=CUTOFF,
        snapshot_id=ref.snapshot_id,
        benchmark_id="csi300_price",
        parameters={"purpose": "ranking_current"},
    )
    result = run_ranking(definition, ref, tmp_path / "out")
    result_dir = result.root / "results" / result.result_id
    tables = {
        path.stem: pd.read_parquet(path)
        for path in sorted(result_dir.glob("*.parquet"))
    }
    return result, tables


def test_cards_gate_state_and_unverified_clues(tmp_path: Path) -> None:
    """缺基本面时五类线索全部未核验，gate_state 为观察／待人工判断。"""
    _, tables = _run(tmp_path, "base")
    cards = tables["cards"]
    assert len(cards) > 0
    assert (cards["gate_state"] == "观察／待人工判断").all()
    for clue in ("景气", "政策", "估值", "资金", "事件"):
        assert cards["clue_types"].str.contains(f"{clue}=未核验").all(), clue
    assert not cards["clue_types"].str.contains("已核验").any()
    for _, row in cards.iterrows():
        rationale = row["rationale"]
        assert "贡献" in rationale and "→" in rationale, "须含原值／贡献引用"
        assert row["counter_evidence"], "须含反方解释"
        assert row["limitations"], "须含缺口与限制"
        assert row["next_check"], "须含下一项核验"
    assert cards["rationale"].str.contains("成交活动代理").all(), (
        "量能只作成交活动代理，不推断资金身份"
    )


def test_cards_market_down_wording_and_counter_evidence(tmp_path: Path) -> None:
    """市场半年下跌：相对优先但整体环境不利；量价高分与反证并存。"""
    _, tables = _run(tmp_path, "market_down")
    cards = tables["cards"]
    assert any(
        "相对优先但整体环境不利" in text for text in cards["counter_evidence"]
    ), "基准半年下跌时正超额对象须提示整体环境不利"
    assert any(
        "短期过热提示" in text and "不改变四维权重" in text
        for text in cards["counter_evidence"]
    ), "过热提示只提示、不改权重"
    high = cards[
        cards["rationale"].str.contains("总分")
        & (cards["counter_evidence"] != "")
    ]
    assert len(high) > 0, "高分卡也必须带反证"


def test_no_historical_members_no_historical_width(tmp_path: Path) -> None:
    """无历史成员：不生成历史宽度或主题组合收益，只保留当前成员口径。"""
    _, tables = _run(tmp_path, "base")
    cards = tables["cards"]
    boards = tables["boards"]
    theme_cards = cards[cards["group_id"] == ""]
    assert len(theme_cards) == int(boards["selected"].sum()), "每个入选板块一张卡"
    assert theme_cards["limitations"].str.contains("不生成历史宽度或主题组合收益").all()
    assert not cards["rationale"].str.contains("历史宽度").any(), "不得编造历史宽度"
    aux = tables["aux"]
    assert "width_60d_ma" in aux.columns
    assert not any("historical" in col or "历史" in col for col in aux.columns), (
        "aux 不得出现历史宽度列"
    )


def test_export_roundtrip_matches_published_values(tmp_path: Path) -> None:
    """导出 ZIP 的 CSV 与已发布 Parquet 数值一致；卡片 Markdown 可读。"""
    result, tables = _run(tmp_path, "base")
    zip_path = export_result(result, tmp_path / "exports")
    assert zip_path.exists() and zip_path.suffix == ".zip"
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        assert {
            "definition.json",
            "sources.json",
            "limitations.json",
            "cards.md",
        } <= names
        table_names = (
            "boards", "slots", "stocks", "metrics",
            "exclusions", "series", "cards", "overlap",
        )
        for table in table_names:
            assert f"{table}.csv" in names, table
        definition = json.loads(zf.read("definition.json").decode("utf-8"))
        assert definition["benchmark_id"] == "csi300_price"
        assert definition["snapshot_id"], "定义须保留输入快照引用"
        limitations = json.loads(zf.read("limitations.json").decode("utf-8"))
        assert isinstance(limitations, list)
        cards_md = zf.read("cards.md").decode("utf-8")
        assert "观察／待人工判断" in cards_md
        for table in ("boards", "slots", "metrics", "cards"):
            from_csv = pd.read_csv(
                zf.open(f"{table}.csv"), dtype=str, keep_default_na=False
            )
            expected = tables[table].fillna("").astype(str)
            pd.testing.assert_frame_equal(
                from_csv.reset_index(drop=True),
                expected.reset_index(drop=True),
            )


def test_export_path_change_is_read_only(tmp_path: Path) -> None:
    """改变导出路径不新增计算、不撤销或改动已发布结果。"""
    result, tables = _run(tmp_path, "base")
    result_dir = result.root / "results" / result.result_id
    hashes_before = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(result_dir.glob("*"))
    }
    first = export_result(result, tmp_path / "a" / "one.zip")
    second = export_result(result, tmp_path / "b")
    assert first != second
    with zipfile.ZipFile(first) as zf1, zipfile.ZipFile(second) as zf2:
        for name in zf1.namelist():
            assert zf1.read(name) == zf2.read(name), f"{name}: 两次导出必须一致"
    hashes_after = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(result_dir.glob("*"))
    }
    assert hashes_before == hashes_after, "导出必须是只读操作"
