"""03.1 共同截止日反例：缺最后下跌日资格失败、删三年前背景只产生短历史标记、
收益口径混用拒绝。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from fixtures import annotate_actions_coverage, make_case
from hetu_quant.data import DataStore
from hetu_quant.models import RankingDefinition, ResultRef
from hetu_quant.ranking import run_ranking

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


def _definition(
    ref, cutoff: date = CUTOFF, as_of: datetime = AS_OF
) -> RankingDefinition:
    return RankingDefinition(
        as_of=as_of,
        cutoff=cutoff,
        snapshot_id=ref.snapshot_id,
        benchmark_id="csi300_price",
        parameters={"purpose": "ranking_current"},
    )


def _tables(result: ResultRef) -> dict[str, pd.DataFrame]:
    result_dir = result.root / "results" / result.result_id
    return {
        name: pd.read_parquet(path)
        for path in sorted(result_dir.glob("*.parquet"))
        for name in [path.stem]
    }


def test_missing_last_market_day_fails_eligibility(tmp_path: Path) -> None:
    """只删除 600011.SH 最后一个市场日：资格必须失败，不能因波动下降入选。"""
    store, ref = _build_store(tmp_path, "ranking_universe", "missing_last_day")
    result = run_ranking(_definition(ref), ref, tmp_path / "out")
    tables = _tables(result)
    metrics = tables["metrics"]
    slots = tables["slots"]
    exclusions = tables["exclusions"]
    assert "600011.SH" not in set(metrics["entity_id"]), "缺末日对象不得进入指标与排序"
    assert "600011.SH" not in set(slots["code"])
    hit = exclusions[exclusions["entity_id"] == "600011.SH"]
    assert len(hit), "排除记录必须保留"
    reasons = " ".join(hit["reason"])
    assert "missing_latest" in reasons or "latest" in reasons


def test_removed_three_year_background_keeps_half_year_metrics(tmp_path: Path) -> None:
    """删三年前背景：半年指标不变，只产生短历史标记。"""
    store_base, ref_base = _build_store(tmp_path / "a", "ranking_universe", "base")
    base = run_ranking(_definition(ref_base), ref_base, tmp_path / "a" / "out")
    store, ref = _build_store(tmp_path / "b", "ranking_universe", "short_history")
    result = run_ranking(_definition(ref), ref, tmp_path / "b" / "out")

    base_metrics = _tables(base)["metrics"]
    metrics = _tables(result)["metrics"]
    for code in ("600012.SH", "600011.SH"):
        row = metrics[metrics["entity_id"] == code].iloc[0]
        base_row = base_metrics[base_metrics["entity_id"] == code].iloc[0]
        assert row["return_6m"] == pytest.approx(base_row["return_6m"])
        assert row["max_drawdown_6m"] == pytest.approx(base_row["max_drawdown_6m"])
    short_row = metrics[metrics["entity_id"] == "600012.SH"].iloc[0]
    assert bool(short_row["short_history"]) is True, "不足三年须打短历史标记"
    assert str(short_row["history_start"]) >= "2023-12-28"
    normal_row = metrics[metrics["entity_id"] == "600011.SH"].iloc[0]
    assert bool(normal_row["short_history"]) is False


def test_mixed_return_basis_rejected(tmp_path: Path) -> None:
    """行情与基准收益口径混用直接拒绝，不产生任何结果。"""
    store, ref = _build_store(tmp_path, "mini_boards", "mixed_basis")
    with pytest.raises(ValueError, match="return_basis|口径"):
        run_ranking(_definition(ref), ref, tmp_path / "out")


def test_cutoff_must_be_closed_trading_day(tmp_path: Path) -> None:
    """共同截止日只能是已收盘交易日，不回退。"""
    store, ref = _build_store(tmp_path, "mini_boards", "base")
    with pytest.raises(ValueError, match="cutoff|交易日"):
        run_ranking(_definition(ref, cutoff=date(2024, 6, 29)), ref, tmp_path / "out")
