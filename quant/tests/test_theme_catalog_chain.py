"""新采集路径接通分类门：em_theme_catalog 原始响应 → 真实 standardize →
prepare 入临时 DataStore → theme_basis 门（run_ranking 真实入口）。

正例：名称命中合成题材库且逐条核验为产业主题的概念板块进入主题榜
（limitations 含规则版本与核验分类）；
反例：未命中板块被分类门排除并留存记录；目录自身不带行业/风格判别，
style 行不进概念目录响应（与生产一致）。
"""

from __future__ import annotations

import io
import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from fixtures import annotate_actions_coverage, make_case
from hetu_quant.data import DataStore
from hetu_quant.models import DataRequest, RankingDefinition
from hetu_quant.providers.akshare import AKShareProvider
from hetu_quant.ranking import resolve_ranking_parameters, run_ranking

CN = ZoneInfo("Asia/Shanghai")
AS_OF = datetime(2024, 7, 1, 9, 0, tzinfo=CN)
CUTOFF = date(2024, 6, 28)
FETCH_START = date(2023, 11, 30)
FETCH_END = date(2024, 6, 28)
RULE_VERSION = "em-themelib-20261015-v2"

TABLE_OF_PRODUCT = {
    "test_calendar": "calendar.csv",
    "test_daily": "daily_bars.csv",
    "test_status": "security_status.csv",
    "test_members": "memberships.csv",
    "test_index": "index_bars.csv",
    "test_actions": "corporate_actions.csv",
}

OBS = "2024-05-01T00:00:00+08:00"
FIELD_NAME = (
    "CategoryCode,CategoryName,ParentCode,ParentName,CategoryPchg,"
    "SecuCode,SecuName,IsImportant,Market"
)


class _EMChainProvider:
    """ranking_universe 合成 CSV + 东财响应形状的替身（只隔离网络）。

    test_* 产品回吐 CSV 行（列与表 schema 对齐）；em_theme_catalog 返回 clist
    diff 形状（f12/f14）；em_theme_library 返回 hottheme 形状（FieldName +
    SplitSymbol=| 行）。em_* 的 standardize 委托真实 AKShareProvider，走与
    生产完全相同的标准化分支。
    """

    def __init__(
        self,
        files: dict[str, str],
        extra_theme: tuple[str, str] | None = None,
        extra_members: tuple[str, ...] = (),
    ) -> None:
        self._frames = {
            name: pd.read_csv(io.StringIO(text), dtype=str)
            for name, text in files.items()
        }
        self._extra_theme = extra_theme
        self._extra_members = extra_members

    def fetch(
        self, product_id: str, codes: tuple[str, ...], start: date, end: date
    ) -> pd.DataFrame:
        if product_id == "em_theme_catalog":
            boards = self._frames["boards.csv"]
            themes = boards[boards["board_kind"] == "theme"]
            rows = [
                {"f12": row.board_id, "f14": row.name, "f3": "1.0"}
                for row in themes.itertuples(index=False)
            ]
            if self._extra_theme is not None:
                rows.append(
                    {"f12": self._extra_theme[0], "f14": self._extra_theme[1],
                     "f3": "1.0"}
                )
            return pd.DataFrame(rows)
        if product_id == "em_theme_library":
            library = self._frames["theme_library.csv"]
            data = [
                f"{row.theme_code}|{row.theme_name}|||1.0|300106|样例证券|0|02"
                for row in library.itertuples(index=False)
            ]
            return pd.DataFrame({"raw_row": data, "field_name": FIELD_NAME})
        frame = self._frames[TABLE_OF_PRODUCT[product_id]].copy()
        if codes and "code" in frame.columns:
            frame = frame[frame["code"].isin(list(codes))]
        if "date" in frame.columns:
            frame = frame[
                (frame["date"] >= start.isoformat())
                & (frame["date"] <= end.isoformat())
            ]
        if product_id == "test_members" and self._extra_theme and self._extra_members:
            extra = pd.DataFrame(
                {
                    "board_id": [self._extra_theme[0]] * len(self._extra_members),
                    "code": list(self._extra_members),
                    "observed_at": [OBS] * len(self._extra_members),
                    "available_at": [OBS] * len(self._extra_members),
                    "effective_from": ["2023-01-01"] * len(self._extra_members),
                    "effective_to": [""] * len(self._extra_members),
                    "membership_kind": ["current_snapshot"] * len(self._extra_members),
                }
            )
            frame = pd.concat([frame, extra], ignore_index=True)
        return frame.reset_index(drop=True)

    def standardize(self, product_id, raw, product):
        if product_id in ("em_theme_catalog", "em_theme_library"):
            return AKShareProvider().standardize(product_id, raw, product)
        return {product["tables"][0]: raw}


def _build_root(
    tmp_path: Path,
    extra_theme: tuple[str, str] | None = None,
    extra_members: tuple[str, ...] = (),
):
    root = tmp_path / "qroot"
    root.mkdir(parents=True)
    config = json.loads(json.dumps(make_case("product_config_full", "base")))
    em_product = {
        "provider": "akshare",
        "fee_class": "free",
        "permission": {
            "status": "verified_free",
            "basis": "合成测试材料，无第三方权利",
            "source_url": "",
        },
        "kind": "catalog",
        "requires_codes": False,
        "tables": [],
        "provider_timezone": "Asia/Shanghai",
        "notes": "",
    }
    config["products"]["em_theme_catalog"] = dict(em_product, tables=["boards"])
    config["products"]["em_theme_library"] = dict(em_product, tables=["theme_library"])
    for product in config["products"].values():
        product["provider"] = "akshare"
    config_text = json.dumps(config, ensure_ascii=False)
    (root / "data-products.json").write_text(config_text, encoding="utf-8")
    store = DataStore(root, providers={"akshare": None})
    return root, store, config


def _prepare_chain(tmp_path: Path, provider: _EMChainProvider, config: dict):
    root = tmp_path / "qroot"
    store = DataStore(
        root,
        providers={"akshare": provider},
    )
    codes = tuple(
        sorted({str(code) for code in provider._frames["daily_bars.csv"]["code"]})
    )
    index_codes = tuple(
        sorted({str(code) for code in provider._frames["index_bars.csv"]["code"]})
    )
    request = DataRequest(
        product_ids=(
            "test_calendar",
            "test_daily",
            "test_status",
            "test_members",
            "test_index",
            "test_actions",
            "em_theme_catalog",
            "em_theme_library",
        ),
        codes=codes,
        start=FETCH_START,
        end=FETCH_END,
        as_of=AS_OF,
        purpose="ranking_current",
        codes_by_product={"test_index": index_codes},
    )
    ref = store.prepare(request)
    annotate_actions_coverage(store, ref, FETCH_START.isoformat(), FETCH_END.isoformat())
    return store, ref


def _run_chain(tmp_path: Path, *, gate_variant="base", **provider_kwargs):
    root, _, config = _build_root(tmp_path, **provider_kwargs)
    case = make_case("ranking_universe", "base")
    if gate_variant == "empty_library":
        case["files"]["theme_library.csv"] = case["files"]["theme_library.csv"].splitlines()[0] + "\n"
    provider = _EMChainProvider(case["files"], **provider_kwargs)
    store, ref = _prepare_chain(tmp_path, provider, config)
    protocol = resolve_ranking_parameters({"purpose": "ranking_current"})
    basis = protocol["category"]["theme_basis"]
    basis["audited_boards"] = json.loads(json.dumps(case["synthetic_theme_audit"]))
    if gate_variant in ("missing_audit", "v1"):
        basis.pop("audited_boards")
        if gate_variant == "v1":
            basis["rule_version"] = "em-themelib-20261015-v1"
    elif gate_variant == "empty_audit":
        basis["audited_boards"] = {}
    elif gate_variant in ("unverified", "style", "regional", "listing_board"):
        basis["audited_boards"]["THM1"]["category"] = gate_variant
    elif gate_variant == "unaudited":
        basis["audited_boards"].pop("THM1")
    definition = RankingDefinition(
        as_of=AS_OF,
        cutoff=CUTOFF,
        snapshot_id=ref.snapshot_id,
        benchmark_id="csi300_price",
        frozen_protocol=protocol,
        parameters={
            "purpose": "ranking_current",
            # v2 分类门：注入合成主题核验条目（THM1–THM6 = industrial_theme）
            "category": {
                "theme_basis": {
                    "audited_boards": case["synthetic_theme_audit"],
                }
            },
        },
    )
    result = run_ranking(definition, ref, root)
    result_dir = root / "results" / result.result_id
    tables = {
        path.stem: pd.read_parquet(path)
        for path in sorted(result_dir.glob("*.parquet"))
    }
    return result, tables


def test_new_catalog_path_theme_with_basis_enters_theme_board(tmp_path: Path) -> None:
    """正例：新采集路径（clist diff → standardize 待核验候选）的主题板块
    名称命中题材库 → 进入主题榜，limitations 可追查规则版本与命中代码。"""
    result, tables = _run_chain(tmp_path)
    boards = tables["boards"]
    themes = boards[boards["board_kind"] == "theme"]
    assert {str(b) for b in themes["board_id"]} == {
        "THM1", "THM2", "THM3", "THM4", "THM5", "THM6"
    }, "概念目录全部命中题材库的主题进入主题榜"
    assert themes["selected"].sum() == 5
    for board_id, note in zip(
        sorted(themes["board_id"]),
        themes.sort_values("board_id")["limitations"],
        strict=False,
    ):
        assert RULE_VERSION in str(note), f"{board_id}: 依据须含规则版本"
        assert "SYN" in str(note), f"{board_id}: 依据须含命中题材代码"
        assert "industrial_theme" in str(note), f"{board_id}: 依据须含逐条核验结论"
        assert "synthetic" in str(note), f"{board_id}: 依据须含核验证据引用"
    exclusions = tables["exclusions"]
    assert not (
        (exclusions["scope"] == "classification")
        & exclusions["entity_id"].astype(str).str.startswith("THM")
    ).any(), "命中主题不得有分类排除记录"


def test_new_catalog_path_theme_without_basis_excluded(tmp_path: Path) -> None:
    """反例：新采集路径上名称未命中题材库的板块被分类门排除并留存记录，
    命中主题不受影响。"""
    _, tables = _run_chain(
        tmp_path,
        extra_theme=("XTRA1", "无依据概念X"),
        extra_members=("600951.SH", "600952.SH", "600953.SH",
                       "600954.SH", "600955.SH", "600956.SH"),
    )
    boards = tables["boards"]
    assert "XTRA1" not in set(boards["board_id"]), "未命中板块不入主题榜"
    themes = boards[boards["board_kind"] == "theme"]
    assert {str(b) for b in themes["board_id"]} == {
        "THM1", "THM2", "THM3", "THM4", "THM5", "THM6"
    }, "命中主题不受影响"
    hit = tables["exclusions"][tables["exclusions"]["entity_id"] == "XTRA1"]
    assert len(hit), "未命中板块必须有排除记录"
    reason = str(hit.iloc[0]["reason"])
    assert hit.iloc[0]["scope"] == "classification"
    assert "theme_basis" in reason and "unknown" in reason, reason


@pytest.mark.parametrize("variant", [
    "empty_library", "missing_audit", "empty_audit", "v1",
    "unaudited", "unverified", "style", "regional", "listing_board",
])
def test_new_catalog_candidates_still_require_both_classification_conditions(tmp_path, variant):
    _, tables = _run_chain(tmp_path, gate_variant=variant)
    themes = tables["boards"][tables["boards"]["board_kind"] == "theme"]
    excluded = tables["exclusions"]
    excluded = excluded[excluded["scope"] == "classification"]
    assert "THM1" not in set(themes["board_id"])
    reason = str(excluded[excluded["entity_id"] == "THM1"].iloc[0]["reason"])
    assert "theme_basis" in reason
    if variant in ("empty_library", "missing_audit", "empty_audit", "v1"):
        assert themes.empty
        assert set(excluded["entity_id"]) == {"THM1", "THM2", "THM3", "THM4", "THM5", "THM6"}
    else:
        assert set(themes["board_id"]) == {"THM2", "THM3", "THM4", "THM5", "THM6"}
        if variant != "unaudited":
            assert variant in reason and "synthetic" in reason
