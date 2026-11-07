"""主题分类依据门（冻结协议 category.theme_basis，em-themelib-20261015-v2）。

合成题材库由 fixtures 从主题板块事实派生（虚构题材代码，不用真实东财数据）；
门语义：名称精确命中题材库且逐条核验分类为产业主题 → 入主题榜（依据可追查）；
核验为 style/listing_board/regional/unverified 标签（如 BK1158 微盘股为市值
风格标签）→ 排除并引用证据；命中但不在核验覆盖内 → 保守排除；未命中 / 快照缺
theme_library 表 / 旧协议无 theme_basis → 全部按无依据排除。核验表缺失或
为空（含 v1 协议、被剥离核验表的 v2 协议）→ 主题无产业分类依据，受限不
降级放行；旧结果的保护方式是既有产物留存，不是重放旧行为。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from fixtures import annotate_actions_coverage, make_case
from hetu_quant.data import DataStore
from hetu_quant.models import RankingDefinition
from hetu_quant.ranking import resolve_ranking_parameters, run_ranking

CN = ZoneInfo("Asia/Shanghai")
AS_OF = datetime(2024, 7, 1, 9, 0, tzinfo=CN)
CUTOFF = date(2024, 6, 28)
WINDOW_START = "2023-11-30"
WINDOW_END = "2024-06-28"
RULE_VERSION = "em-themelib-20261015-v2"
V1_RULE_VERSION = "em-themelib-20261015-v1"


def _build_store(
    tmp_path: Path, data: dict
) -> tuple[DataStore, object]:
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
    ref,
    frozen_without_theme_basis: bool = False,
    frozen_theme_basis: dict | None = None,
    audited: dict | None = None,
    strip_audit: str | None = None,
) -> RankingDefinition:
    kwargs: dict = {}
    parameters: dict = {"purpose": "ranking_current"}
    if audited is not None:
        parameters["category"] = {"theme_basis": {"audited_boards": audited}}
    if (
        frozen_theme_basis is not None
        or frozen_without_theme_basis
        or strip_audit is not None
    ):
        protocol = json.loads(
            json.dumps(resolve_ranking_parameters({"purpose": "ranking_current"}))
        )
        if frozen_without_theme_basis:
            protocol["category"].pop("theme_basis", None)
        if frozen_theme_basis is not None:
            protocol["category"]["theme_basis"] = frozen_theme_basis
        if strip_audit == "missing":
            protocol["category"]["theme_basis"].pop("audited_boards", None)
        elif strip_audit == "empty":
            protocol["category"]["theme_basis"]["audited_boards"] = {}
        kwargs["frozen_protocol"] = protocol
    return RankingDefinition(
        as_of=AS_OF,
        cutoff=CUTOFF,
        snapshot_id=ref.snapshot_id,
        benchmark_id="csi300_price",
        parameters=parameters,
        **kwargs,
    )


def _run(tmp_path: Path, data: dict, **kwargs):
    store, ref = _build_store(tmp_path, data)
    # 默认注入合成主题核验条目（THM1–THM6 = industrial_theme）
    kwargs.setdefault("audited", data.get("synthetic_theme_audit"))
    result = run_ranking(_definition(ref, **kwargs), ref, tmp_path / "out")
    result_dir = result.root / "results" / result.result_id
    tables = {
        path.stem: pd.read_parquet(path)
        for path in sorted(result_dir.glob("*.parquet"))
    }
    return result, tables


def _drop_theme_library(data: dict) -> None:
    del data["files"]["theme_library.csv"]
    data["manifest"]["items"] = [
        item
        for item in data["manifest"]["items"]
        if item["product_id"] != "test_themelib"
    ]


def _rename_boards(files: dict, mapping: dict[str, str]) -> None:
    """boards.csv 按 id 改名称（board_id 保持不变）。"""
    lines = files["boards.csv"].splitlines()
    edited = [lines[0]]
    for line in lines[1:]:
        parts = line.split(",")
        if parts[0] in mapping:
            parts[2] = mapping[parts[0]]
        edited.append(",".join(parts))
    files["boards.csv"] = "\n".join(edited)


def _rekey_boards_and_members(files: dict, old: str, new: str) -> None:
    """boards.csv 与 memberships.csv 按 id 前缀整体替换 board_id。"""
    for key in ("boards.csv", "memberships.csv"):
        lines = files[key].splitlines()
        files[key] = "\n".join(
            line.replace(f"{old},", f"{new},", 1)
            if line.startswith(f"{old},")
            else line
            for line in lines
        )


def _rename_theme_in_library(files: dict, old: str, new: str) -> None:
    files["theme_library.csv"] = "\n".join(
        line.replace(old, new) for line in files["theme_library.csv"].splitlines()
    )


def _exclusion_reason(tables: dict, entity: str) -> str:
    hit = tables["exclusions"][tables["exclusions"]["entity_id"] == entity]
    assert len(hit), f"{entity} 必须有排除记录"
    return str(hit.iloc[0]["reason"])


def test_theme_name_in_library_enters_theme_ranking_with_traceable_basis(
    tmp_path: Path,
) -> None:
    data = make_case("ranking_universe", "base")
    result, tables = _run(tmp_path, data)
    assert result.status == "complete"
    boards = tables["boards"]
    themes = boards[boards["board_kind"] == "theme"]
    assert len(themes) == 6, "命中题材库且核验为产业主题的主题进入主题榜（可入选）"
    for board_id, note in zip(
        sorted(themes["board_id"]),
        themes.sort_values("board_id")["limitations"],
        strict=False,
    ):
        assert RULE_VERSION in str(note), f"{board_id}: 依据须含规则版本"
        assert "SYN" in str(note), f"{board_id}: 依据须含命中题材代码"
        assert "industrial_theme" in str(note), f"{board_id}: 依据须含核验分类"


def test_theme_name_not_in_library_excluded_with_record(tmp_path: Path) -> None:
    data = make_case("ranking_universe", "base")
    files = data["files"]
    _rename_boards(files, {f"THM{i}": f"无依据主题{i}" for i in range(1, 7)})
    result, tables = _run(tmp_path, data)
    assert result.status == "limited"
    boards = tables["boards"]
    assert len(boards[boards["board_kind"] == "theme"]) == 0
    excluded = tables["exclusions"][
        tables["exclusions"]["scope"] == "classification"
    ]
    for i in range(1, 7):
        hit = excluded[excluded["entity_id"] == f"THM{i}"]
        assert len(hit), f"THM{i} 必须有排除记录"
        reason = str(hit.iloc[0]["reason"])
        assert "theme_basis" in reason and "unknown" in reason, reason
        assert RULE_VERSION in reason, "排除原因须引用冻结规则版本"


def test_microcap_style_label_excluded_with_evidence(tmp_path: Path) -> None:
    """P1 负例：名称命中题材库但发布者释义核验为市值风格标签（BK1158
    微盘股）→ 不得作为产业主题放行，排除须引用核验分类与证据。"""
    data = make_case("ranking_universe", "base")
    files = data["files"]
    _rename_boards(files, {"THM1": "微盘股"})
    _rekey_boards_and_members(files, "THM1", "BK1158")
    _rename_theme_in_library(files, "合成主题1", "微盘股")
    result, tables = _run(tmp_path, data)
    boards = tables["boards"]
    assert "BK1158" not in set(boards["board_id"]), "风格标签不进入主题榜"
    assert len(boards[boards["board_kind"] == "theme"]) == 5, "其余主题不受影响"
    reason = _exclusion_reason(tables, "BK1158")
    assert "style" in reason, reason
    assert "defs/a324.html" in reason, "排除原因须引用发布者释义证据文件"


def test_regional_theme_excluded_with_evidence(tmp_path: Path) -> None:
    """核验为区域经济维度（BK0643 上海自贸，发布者父级:区域经济）→ 非产业
    主题，排除并引用证据。"""
    data = make_case("ranking_universe", "base")
    files = data["files"]
    _rename_boards(files, {"THM1": "上海自贸"})
    _rekey_boards_and_members(files, "THM1", "BK0643")
    _rename_theme_in_library(files, "合成主题1", "上海自贸")
    _, tables = _run(tmp_path, data)
    boards = tables["boards"]
    assert "BK0643" not in set(boards["board_id"]), "区域维度不进入主题榜"
    reason = _exclusion_reason(tables, "BK0643")
    assert "regional" in reason, reason
    assert "defs/005001.html" in reason, reason


def test_unverified_definition_page_excluded_conservatively(tmp_path: Path) -> None:
    """发布者释义页不可得（BK1177 荣耀概念，详情页 404 证据留存）→
    unverified，保守排除。"""
    data = make_case("ranking_universe", "base")
    files = data["files"]
    _rename_boards(files, {"THM1": "荣耀概念"})
    _rekey_boards_and_members(files, "THM1", "BK1177")
    _rename_theme_in_library(files, "合成主题1", "荣耀概念")
    _, tables = _run(tmp_path, data)
    boards = tables["boards"]
    assert "BK1177" not in set(boards["board_id"]), "释义不可得不进入主题榜"
    reason = _exclusion_reason(tables, "BK1177")
    assert "unverified" in reason, reason


def test_hit_but_not_in_audit_excluded_conservatively(tmp_path: Path) -> None:
    """名称命中题材库但 board_id 不在核验附件覆盖内（如后续新增板块）→
    保守排除，不得按名称命中放行。"""
    data = make_case("ranking_universe", "base")
    files = data["files"]
    _rekey_boards_and_members(files, "THM1", "NEWBK1")
    result, tables = _run(tmp_path, data)
    boards = tables["boards"]
    assert "NEWBK1" not in set(boards["board_id"]), "未核验板块不进入主题榜"
    assert len(boards[boards["board_kind"] == "theme"]) == 5
    reason = _exclusion_reason(tables, "NEWBK1")
    assert "不在逐条核验附件覆盖内" in reason, reason


def test_audited_exception_board_excluded_with_label_and_evidence(
    tmp_path: Path,
) -> None:
    data = make_case("ranking_universe", "base")
    files = data["files"]
    # THM1 → BK1107（科创板做市商）：名称命中题材库且在核验表内，
    # 但核验分类为 listing_board → 仍须排除并引用证据。
    _rename_boards(files, {"THM1": "科创板做市商"})
    _rekey_boards_and_members(files, "THM1", "BK1107")
    _rename_theme_in_library(files, "合成主题1", "科创板做市商")
    result, tables = _run(tmp_path, data)
    boards = tables["boards"]
    assert "BK1107" not in set(boards["board_id"]), "例外标签不进入主题榜"
    assert len(boards[boards["board_kind"] == "theme"]) == 5, "其余主题不受影响"
    reason = _exclusion_reason(tables, "BK1107")
    assert "listing_board" in reason, reason
    assert "defs/a270.html" in reason, reason


def test_v2_protocol_missing_audit_table_restricted_not_degraded(
    tmp_path: Path,
) -> None:
    """P1 负例：保留 v2 版本号但协议缺失 audited_boards → 无逐条核验表，
    受限不降级放行——名称命中（含微盘股风格标签）不得进入主题榜，
    排除原因须标明协议版本。"""
    data = make_case("ranking_universe", "base")
    files = data["files"]
    _rename_boards(files, {"THM1": "微盘股"})
    _rekey_boards_and_members(files, "THM1", "BK1158")
    _rename_theme_in_library(files, "合成主题1", "微盘股")
    result, tables = _run(tmp_path, data, strip_audit="missing", audited=None)
    boards = tables["boards"]
    assert len(boards[boards["board_kind"] == "theme"]) == 0, "无核验表全部主题受限"
    assert "BK1158" not in set(boards["board_id"]), "不得降级放行风格标签"
    assert result.status == "limited"
    reason = _exclusion_reason(tables, "BK1158")
    assert "无逐条核验表" in reason, reason
    assert RULE_VERSION in reason, "受限原因须标明协议版本"
    assert "受限" in reason, reason


def test_v2_protocol_empty_audit_table_restricted_not_degraded(
    tmp_path: Path,
) -> None:
    """P1 负例：v2 协议 audited_boards 为空表 → 同缺失处理，受限不降级。"""
    data = make_case("ranking_universe", "base")
    files = data["files"]
    _rename_boards(files, {"THM1": "微盘股"})
    _rekey_boards_and_members(files, "THM1", "BK1158")
    _rename_theme_in_library(files, "合成主题1", "微盘股")
    result, tables = _run(tmp_path, data, strip_audit="empty", audited=None)
    boards = tables["boards"]
    assert len(boards[boards["board_kind"] == "theme"]) == 0
    assert "BK1158" not in set(boards["board_id"]), "空表同样不得放行"
    assert result.status == "limited"
    reason = _exclusion_reason(tables, "BK1158")
    assert "无逐条核验表" in reason and RULE_VERSION in reason, reason


def test_v1_protocol_reexecution_restricted_not_degraded(tmp_path: Path) -> None:
    """v1 冻结协议（仅 exceptions、无核验表）重新执行 → 主题无产业分类
    依据，受限不降级放行：微盘股等名称命中不得入榜。旧结果的保护方式是
    既有产物留存（验收记录与结果表），不是重放 v1 行为；重跑须升级为
    含核验表的 v2 协议。"""
    v1_theme_basis = {
        "rule_version": V1_RULE_VERSION,
        "positive_source_table": "theme_library",
        "match": "name_exact",
        "exceptions": {
            "BK1107": {
                "label": "listing_board",
                "evidence": (
                    ".hetu/quant/qualification/theme-basis/"
                    "em-themelib-20261015-evidence-BK1107-a270.html"
                ),
            },
            "BK1108": {
                "label": "listing_board",
                "evidence": (
                    ".hetu/quant/qualification/theme-basis/"
                    "em-themelib-20261015-evidence-BK1108-a271.html"
                ),
            },
        },
        "on_unknown": "exclude_with_record",
    }
    data = make_case("ranking_universe", "base")
    files = data["files"]
    # THM1 → BK1107 科创板做市商（v1 例外）；THM2 → BK1158 微盘股（v1 曾放行）
    _rename_boards(files, {"THM1": "科创板做市商", "THM2": "微盘股"})
    _rekey_boards_and_members(files, "THM1", "BK1107")
    _rekey_boards_and_members(files, "THM2", "BK1158")
    _rename_theme_in_library(files, "合成主题1", "科创板做市商")
    _rename_theme_in_library(files, "合成主题2", "微盘股")
    result, tables = _run(
        tmp_path, data, frozen_theme_basis=v1_theme_basis, audited=None
    )
    boards = tables["boards"]
    assert len(boards[boards["board_kind"] == "theme"]) == 0, "v1 重跑全部主题受限"
    assert "BK1158" not in set(boards["board_id"]), "新执行不放行已知风格标签"
    assert "BK1107" not in set(boards["board_id"])
    assert result.status == "limited"
    reason = _exclusion_reason(tables, "BK1158")
    assert "无逐条核验表" in reason, reason
    assert V1_RULE_VERSION in reason, "受限原因须标明 v1 协议版本"


def test_classification_version_suffix_cannot_bypass_gate(tmp_path: Path) -> None:
    """classification_version 改为任意非 current_observed 字符串（名称未命中
    题材库）→ 仍被排除：新门不再依赖该后缀判断。"""
    data = make_case("ranking_universe", "base")
    files = data["files"]
    _rename_boards(files, {f"THM{i}": f"无依据主题{i}" for i in range(1, 7)})
    lines = files["boards.csv"].splitlines()
    files["boards.csv"] = "\n".join(
        line.rsplit(",", 1)[0] + ",em_themerank_v2026" if line.startswith("THM")
        else line
        for line in lines
    )
    _, tables = _run(tmp_path, data)
    boards = tables["boards"]
    assert len(boards[boards["board_kind"] == "theme"]) == 0
    excluded = tables["exclusions"][
        tables["exclusions"]["scope"] == "classification"
    ]
    thm = excluded[excluded["entity_id"].str.startswith("THM")]
    assert len(thm) == 6
    assert thm["reason"].str.contains("theme_basis").all()


def test_snapshot_without_theme_library_excludes_all_themes(tmp_path: Path) -> None:
    data = make_case("ranking_universe", "base")
    _drop_theme_library(data)
    result, tables = _run(tmp_path, data)
    assert result.status == "limited", "旧快照缺 theme_library 表（旧行为语义）"
    boards = tables["boards"]
    assert len(boards[boards["board_kind"] == "theme"]) == 0
    assert boards[boards["board_kind"] == "industry"]["selected"].sum() == 5
    assert len(tables["slots"]) == 25
    excluded = tables["exclusions"][
        tables["exclusions"]["scope"] == "classification"
    ]
    thm = excluded[excluded["entity_id"].str.startswith("THM")]
    assert len(thm) == 6
    assert thm["reason"].str.contains("theme_library").all()


def test_frozen_protocol_without_theme_basis_excludes_all_themes(
    tmp_path: Path,
) -> None:
    data = make_case("ranking_universe", "base")
    result, tables = _run(tmp_path, data, frozen_without_theme_basis=True)
    assert result.status == "limited", "旧定义（协议无 theme_basis）全部主题排除"
    boards = tables["boards"]
    assert len(boards[boards["board_kind"] == "theme"]) == 0
    excluded = tables["exclusions"][
        tables["exclusions"]["scope"] == "classification"
    ]
    thm = excluded[excluded["entity_id"].str.startswith("THM")]
    assert len(thm) == 6
    assert thm["reason"].str.contains("theme_basis").all()
