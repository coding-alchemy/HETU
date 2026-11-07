"""05.1 真实链路衔接修复的定向验证（合成材料，答案在断言侧）。

覆盖三处集成行为：
1. DataRequest.codes_by_product 按产品划分取数代码域，组合快照全部分区
   已覆盖时零取数发布，且仅复制的产品在清单中继承来源与谱系；
2. 标准化层未填连续价格收益列时，排名按 01 的 build_price_series 以
   原始收盘+公司行动构建（送转不冒充收益）；快照已取齐行动覆盖时
   空表=已核验无事件（既有合成世界不变），未取齐时按资格排除（B1）；
   覆盖核验与冻结协议见 ranking-defaults.json corporate_actions；
3. 分类依据缺失（board_kind=unclassified）的板块不入行业/主题榜，
   排除依据留存，结果如实标 limited。
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
from hetu_quant.experiments import ExperimentStore
from hetu_quant.models import DataRequest, RankingDefinition
from hetu_quant.ranking import run_ranking

CN = ZoneInfo("Asia/Shanghai")
AS_OF = datetime(2024, 7, 1, 9, 0, tzinfo=CN)
CUTOFF = date(2024, 6, 28)
START = date(2024, 1, 1)
END = date(2024, 3, 29)
OBS = "2024-06-01T09:00:00+08:00"


class DualProvider:
    """市场日线与成员两类产品的确定替身；记录调用隔离网络。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def fetch(self, product_id, codes, start, end):
        self.calls.append((product_id, tuple(codes)))
        if product_id == "test_daily":
            rows = [
                {
                    "date": day.date().isoformat(),
                    "code": code,
                    "open_raw": 10.0,
                    "high_raw": 10.0,
                    "low_raw": 10.0,
                    "close_raw": 10.0,
                    "volume_shares": 1000.0,
                    "amount_cny": 10000.0,
                }
                for code in codes
                for day in pd.bdate_range(start, end)
            ]
            return pd.DataFrame(rows)
        if product_id == "test_members":
            return pd.DataFrame(
                [
                    {
                        "board_id": "BD1",
                        "code": code,
                        "observed_at": OBS,
                        "available_at": OBS,
                        "effective_from": None,
                        "effective_to": None,
                        "membership_kind": "current_snapshot",
                    }
                    for code in ("600011.SH", "600012.SH")
                ]
            )
        raise KeyError(product_id)

    def standardize(self, product_id, raw, product):
        if product_id == "test_daily":
            return {"daily_bars": raw}
        return {"memberships": raw}


def _composition_store(tmp_path: Path, provider: DualProvider) -> DataStore:
    config = json.loads(json.dumps(make_case("product_config_full", "base")))
    for product_id in ("test_daily", "test_members"):
        config["products"][product_id]["provider"] = "dual"
    config_path = tmp_path / "products.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return DataStore(tmp_path / "store", config_path=config_path, providers={"dual": provider})


def _request(**override) -> DataRequest:
    payload = {
        "product_ids": ("test_daily", "test_members"),
        "codes": ("600011.SH", "600012.SH", "600013.SH"),
        "start": START,
        "end": END,
        "as_of": AS_OF,
        "purpose": "ranking_current",
        "codes_by_product": {"test_members": ("600011.SH", "600012.SH")},
    }
    payload.update(override)
    return DataRequest(**payload)


def test_codes_by_product_composes_domains_and_inherits_items(tmp_path) -> None:
    """按产品代码域取数；全覆盖组合零取数发布，仅复制产品继承来源谱系。"""
    provider = DualProvider()
    store = _composition_store(tmp_path, provider)

    members_first = DataRequest(
        product_ids=("test_members",),
        codes=("BD1", "BD2"),
        start=START,
        end=END,
        as_of=AS_OF,
        purpose="ranking_current",
    )
    ref_members = store.prepare(members_first)
    # 无日期表按代码逐个调用
    assert provider.calls == [
        ("test_members", ("BD1",)),
        ("test_members", ("BD2",)),
    ]

    request = _request()
    ref = store.prepare(request)
    fetched = {str(item["product_id"]) for item in store.load_manifest(ref)["items"]}
    assert fetched == {"test_daily", "test_members"}
    items = {str(i["product_id"]): i for i in store.load_manifest(ref)["items"]}
    assert items["test_members"]["inherited_from"] == ref_members.snapshot_id
    assert items["test_members"]["raw_files"] == [], "仅复制产品不得伪造原始响应"

    calls_before = len(provider.calls)
    ref2 = store.prepare(request)
    assert len(provider.calls) == calls_before, "全覆盖组合不得重新取数"
    assert ref2.snapshot_id == ref.snapshot_id, "单一快照已覆盖全部请求分区时直接复用"

    definitions = ExperimentStore(tmp_path / "defs")
    definition_id = definitions.save_definition("data", request)
    restored = definitions.read_definition(definition_id)
    assert isinstance(restored, DataRequest)
    assert dict(restored.codes_by_product) == {
        "test_members": ("600011.SH", "600012.SH")
    }
    assert restored.codes == ("600011.SH", "600012.SH", "600013.SH")


def _import_universe(root: Path, files: dict[str, str], config: dict) -> object:
    root.mkdir(parents=True, exist_ok=True)
    config_path = root / "products.json"
    config_text = json.dumps(config, ensure_ascii=False)
    config_path.write_text(config_text, encoding="utf-8")
    store = DataStore(root / "store", config_path=config_path)
    # run_ranking 内部按 data.root 重建 DataStore，需要默认配置可解析
    (root / "store" / "data-products.json").write_text(config_text, encoding="utf-8")
    manifest_path = root / "manifest.json"
    manifest_path.write_text(
        json.dumps(universe_manifest(config, files), ensure_ascii=False), encoding="utf-8"
    )
    paths = []
    for name, content in files.items():
        path = root / name
        path.write_text(content, encoding="utf-8")
        paths.append(path)
    ref = store.import_files(manifest_path, tuple(paths))
    # 已核验公司行动覆盖（空表=已核验无事件；拆股用例表内为窗口内事件）：
    # 按代码分区补齐 coverage 元数据（R1）
    annotate_actions_coverage(store, ref, "2023-12-28", "2024-06-28")
    return store, ref


def universe_manifest(config: dict, files: dict[str, str]) -> dict:
    """把文件表名映射到产品项；复用 ranking_universe 清单形状。"""
    table_of = {
        "calendar.csv": "test_calendar",
        "daily_bars.csv": "test_daily",
        "security_status.csv": "test_status",
        "boards.csv": "test_boards",
        "memberships.csv": "test_members",
        "index_bars.csv": "test_index",
        "corporate_actions.csv": "test_actions",
    }
    manifest = {"mode": "free", "items": []}
    for file_name, product_id in table_of.items():
        if file_name not in files:
            continue
        manifest["items"].append(
            {
                "product_id": product_id,
                "table": config["products"][product_id]["tables"][0],
                "files": [file_name],
                "source": {
                    "name": "合成两榜材料",
                    "fee_class": "free",
                    "permission_basis": "合成测试材料，无第三方权利",
                    "source_url": "",
                    "obtained_at": "2024-06-01T09:00:00+08:00",
                },
            }
        )
    return manifest


def _universe_config(with_actions: bool) -> dict:
    config = json.loads(json.dumps(make_case("product_config_full", "base")))
    if with_actions:
        config["products"]["test_actions"] = {
            "provider": "local",
            "fee_class": "free",
            "permission": {"status": "verified_free", "basis": "合成测试材料"},
            "kind": "event",
            "requires_codes": True,
            "tables": ["corporate_actions"],
        }
    return config


def _read_result(result) -> dict[str, pd.DataFrame]:
    result_dir = result.root / "results" / result.result_id
    return {p.stem: pd.read_parquet(p) for p in sorted(result_dir.glob("*.parquet"))}


def _split_universe_files() -> dict[str, str]:
    """600011.SH 最后交易日 1 送 1：原始收盘减半 + 公司行动 m=2。"""
    files = dict(make_case("ranking_universe", "base")["files"])
    lines = files["daily_bars.csv"].splitlines()
    last_index = max(i for i, line in enumerate(lines) if ",600011.SH," in line)
    parts = lines[last_index].split(",")
    assert parts[1] == "600011.SH" and parts[2] == "104.500000"
    last_date = parts[0]
    parts[2] = "52.250000"
    parts[3] = "52.250000"
    parts[4] = "52.250000"
    lines[last_index] = ",".join(parts)
    files["daily_bars.csv"] = "\n".join(lines) + "\n"
    files["corporate_actions.csv"] = (
        "code,ex_date,action_type,m,d,terms_raw,available_at\n"
        f"600011.SH,{last_date},bonus_split,2,0,10送10,2024-06-20\n"
    )
    return files


def test_ranking_builds_price_return_from_actions_when_column_missing(tmp_path) -> None:
    """拆股当日在价格收益口径下不产生假跳变：核心指标与未拆股基线一致。

    两个快照都带已核验公司行动覆盖（基线为空表=已核验无事件）。
    """
    store, ref = _import_universe(
        tmp_path / "base", dict(make_case("ranking_universe", "base")["files"]),
        _universe_config(with_actions=True),
    )
    base = run_ranking(_definition(ref), ref, store.root)
    base_tables = _read_result(base)

    store2, ref2 = _import_universe(
        tmp_path / "split", _split_universe_files(), _universe_config(with_actions=True)
    )
    split = run_ranking(_definition(ref2), ref2, store2.root)
    split_tables = _read_result(split)

    def stock_row(tables, run):
        hit = tables["metrics"][
            (tables["metrics"]["entity_id"] == "600011.SH")
            & (tables["metrics"]["entity_kind"] == "stock")
        ]
        assert len(hit), f"{run}: 600011.SH 必须有指标行"
        return hit.iloc[0]

    base_row = stock_row(base_tables, "base")
    split_row = stock_row(split_tables, "split")
    for key in (
        "excess_3m",
        "excess_6m",
        "max_drawdown_6m",
        "up_amount_60d",
        "volatility_60d",
        "return_6m",
    ):
        assert split_row[key] == pytest.approx(base_row[key], rel=1e-9, abs=1e-12), key
    # 若退回未复权原始价，最后一日 −50% 跳变必然破坏半年收益
    assert abs(float(split_row["return_6m"])) < 0.05


def _definition(ref) -> RankingDefinition:
    return RankingDefinition(
        as_of=AS_OF,
        cutoff=CUTOFF,
        snapshot_id=ref.snapshot_id,
        benchmark_id="csi300_price",
        parameters={"purpose": "ranking_current"},
    )


def test_dividend_duplicate_event_deduped_but_conflicts_pass_gate() -> None:
    """同一除息事件送转要素一致时去重保留首见（002043.SZ 实测形态）；
    送转要素不同的同键记录不去重，交由标准化唯一性门拒绝。"""
    from hetu_quant.providers.baostock import dedupe_dividend_frame

    columns = [
        "code", "dividPreNoticeDate", "dividAgmPumDate", "dividPlanAnnounceDate",
        "dividPlanDate", "dividRegistDate", "dividOperateDate", "dividPayDate",
        "dividStockMarketDate", "dividCashPsBeforeTax", "dividCashPsAfterTax",
        "dividStocksPs", "dividCashStock", "dividReserveToStockPs",
    ]

    def row(cash: str, stocks: str) -> dict:
        values = ["sz.002043", "", "2026-05-22", "2026-04-29", "2026-05-26",
                  "2026-05-29", "2026-06-01", "2026-06-01", "", cash,
                  "x", stocks, "10派1.32元", ""]
        return dict(zip(columns, values, strict=True))

    frame = pd.DataFrame(
        [row("0.132", "0.000000"), row("0.18", "0.000000")], columns=columns
    )
    deduped = dedupe_dividend_frame(frame)
    assert len(deduped) == 1
    assert deduped.iloc[0]["dividCashPsBeforeTax"] == "0.132", "保留首见"

    conflict = pd.DataFrame(
        [row("0.132", "0.000000"), row("0.132", "0.300000")], columns=columns
    )
    assert len(dedupe_dividend_frame(conflict)) == 2, "送转要素冲突不去重"


def test_theme_without_versioned_basis_stays_out(tmp_path) -> None:
    """快照无 theme_library 分类依据表（旧快照）的主题不入榜：全部主题按无
    依据排除（unknown），排除依据留存；classification_version 后缀不参与判断。"""
    files = dict(make_case("ranking_universe", "base")["files"])
    lines = files["boards.csv"].splitlines()
    edited = []
    for line in lines:
        parts = line.split(",")
        if parts[0].startswith("THM") and parts[1] == "theme":
            parts[4] = "em_concept_current_observed"
        edited.append(",".join(parts))
    files["boards.csv"] = "\n".join(edited)
    # 旧快照形状：清单不含 theme_library 表（universe_manifest 不登记该文件）
    assert "theme_library.csv" in files

    store, ref = _import_universe(tmp_path, files, _universe_config(with_actions=True))
    result = run_ranking(_definition(ref), ref, store.root)
    assert result.status == "limited"
    tables = _read_result(result)
    assert len(tables["boards"][tables["boards"]["board_kind"] == "theme"]) == 0
    assert len(tables["slots"]) == 25
    excluded = tables["exclusions"][tables["exclusions"]["scope"] == "classification"]
    thm = excluded[excluded["entity_id"].str.startswith("THM")]
    assert len(thm) == 6
    assert thm["reason"].str.contains("theme_basis").all()
    assert thm["reason"].str.contains("theme_library").all()


def test_unclassified_boards_stay_out_of_theme_board(tmp_path) -> None:
    """分类依据缺失的板块不入主题榜：排除记录留存、25 席、如实 limited。"""
    files = dict(make_case("ranking_universe", "base")["files"])
    lines = files["boards.csv"].splitlines()
    edited = []
    for line in lines:
        parts = line.split(",")
        if parts[0].startswith("THM") and parts[1] == "theme":
            parts[1] = "unclassified"
        edited.append(",".join(parts))
    files["boards.csv"] = "\n".join(edited)

    store, ref = _import_universe(tmp_path, files, _universe_config(with_actions=True))
    result = run_ranking(_definition(ref), ref, store.root)
    assert result.status == "limited", "主题榜缺失必须如实受限"
    tables = _read_result(result)
    boards = tables["boards"]
    assert len(boards[boards["board_kind"] == "theme"]) == 0
    assert boards[boards["board_kind"] == "industry"]["selected"].sum() == 5
    assert len(tables["slots"]) == 25
    excluded = tables["exclusions"][tables["exclusions"]["scope"] == "classification"]
    assert {"THM1", "THM2", "THM3", "THM4", "THM5", "THM6", "STY1"} <= set(
        excluded["entity_id"]
    )
    manifest = json.loads(
        (store.root / "results" / result.result_id / "result.json").read_text(
            encoding="utf-8"
        )
    )
    assert any("theme boards eligible 0" in item for item in manifest["limitations"])


class _FakeResult:
    """离线替身：模拟 baostock rs.next()/get_row_data() 逐行收集协议。"""

    def __init__(self, fields: list[str], rows: list[list[str]]) -> None:
        self.error_code = "0"
        self.error_msg = ""
        self.fields = fields
        self._rows = rows
        self._pos = 0

    def next(self) -> bool:
        self._pos += 1
        return self._pos <= len(self._rows)

    def get_row_data(self) -> list[str]:
        return list(self._rows[self._pos - 1])


class _FakeBaoStock:
    def __init__(self, fields: list[str], rows: list[list[str]]) -> None:
        self._fields = fields
        self._rows = rows
        self.calls: list[dict] = []

    def query_dividend_data(self, code: str, year: str, yearType: str = "report") -> _FakeResult:
        self.calls.append({"code": code, "year": year, "yearType": yearType})
        return _FakeResult(self._fields, self._rows)


def test_baostock_actions_raw_keeps_conflicts_standardize_dedupes() -> None:
    """评审 D1：同一除息事件现金双版本（002043.SZ 实测形态）在 raw 层逐行保留，
    去重只发生在 standardize（保留首见），冲突原文不丢。"""
    from hetu_quant.providers.baostock import BaoStockProvider

    fixture = pd.read_parquet(Path(__file__).parent / "fixtures" / "providers" / "bs_dividend.parquet")
    fields = list(fixture.columns)
    template = {
        key: ("" if pd.isna(value) else str(value))
        for key, value in fixture.iloc[0].items()
    }
    template.update(
        {
            "code": "sz.002043",
            "dividOperateDate": "2026-06-01",
            "dividStocksPs": "0.000000",
            "dividReserveToStockPs": "",
        }
    )
    version_a = dict(template, dividCashPsBeforeTax="0.132")
    version_b = dict(template, dividCashPsBeforeTax="0.18")
    rows = [[version_a[field] for field in fields], [version_b[field] for field in fields]]

    provider = BaoStockProvider()
    provider._bs = _FakeBaoStock(fields, rows)  # 离线：绕过 baostock 登录
    raw = provider.fetch("cn_a_actions", ("002043.SZ",), date(2026, 1, 1), date(2026, 10, 13))
    assert len(raw) == 2, "raw 层必须逐行保留冲突多版本，不得提前去重"
    # yearType=operate 实测语义（2026-10-16）：按除权除息实施年取数，避免公告年
    # 在窗口前一年的实施事件漏采；见 capability-gap-20261016 判定证据。
    assert provider._bs.calls == [
        {"code": "002043.sz", "year": "2026", "yearType": "operate"}
    ], "行动查询必须固定 operate（实施年）口径"

    tables = provider.standardize("cn_a_actions", raw, {"tables": ["corporate_actions"]})
    actions = tables["corporate_actions"]
    assert len(actions) == 1, "standardize 按既有同日同行规则去重"
    assert actions.iloc[0]["d"] == pytest.approx(0.132), "同事件现金双版本保留首见，不合并计"


def test_baostock_dividend_duplicate_ex_date_merged() -> None:
    """同一实施日多条不同事件（中期+年度分红，送转要素不同故不被去重键命中）
    按主键合并；现金合并计、m 连乘、可得时点取最早、条款并集保留。"""
    from hetu_quant.providers.baostock import BaoStockProvider

    raw = pd.DataFrame(
        [
            {
                "code": "sh.600000",
                "dividCashPsBeforeTax": "0.3",
                "dividStocksPs": "",
                "dividReserveToStockPs": "",
                "dividPreNoticeDate": "2024-03-01",
                "dividPlanAnnounceDate": "2024-03-05",
                "dividAgmPumDate": "",
                "dividOperateDate": "2024-06-20",
                "dividCashStock": "10派3",
            },
            {
                "code": "sh.600000",
                "dividCashPsBeforeTax": "0.2",
                "dividStocksPs": "0.200000",
                "dividReserveToStockPs": "",
                "dividPreNoticeDate": "2024-04-01",
                "dividPlanAnnounceDate": "2024-04-05",
                "dividAgmPumDate": "",
                "dividOperateDate": "2024-06-20",
                "dividCashStock": "10派2",
            },
        ]
    )
    tables = BaoStockProvider().standardize(
        "cn_a_actions", raw, {"tables": ["corporate_actions"]}
    )
    actions = tables["corporate_actions"]
    assert len(actions) == 2, "同日不同事件：现金行合并、送转行独立"
    cash = actions[actions["action_type"] == "cash_dividend"].iloc[0]
    assert cash["code"] == "600000.SH"
    assert cash["d"] == pytest.approx(0.5), "同日现金分红合并计"
    assert cash["available_at"] == "2024-03-01", "可得时点取最早公告"
    assert "10派2" in cash["terms_raw"] and "10派3" in cash["terms_raw"]
    bonus = actions[actions["action_type"] == "bonus_split"].iloc[0]
    assert bonus["m"] == pytest.approx(1.2), "送转因子独立成行"


class _CsvFetchProvider:
    """ranking_universe 合成 CSV 为后端的真实取数形态替身（含成功空响应）。

    fetch 按代码与期间过滤对应 CSV；corporate_actions.csv 只有表头，
    对该产品的每次取数都是"成功空响应"。
    """

    TABLE_OF_PRODUCT = {
        "test_calendar": "calendar.csv",
        "test_daily": "daily_bars.csv",
        "test_status": "security_status.csv",
        "test_boards": "boards.csv",
        "test_members": "memberships.csv",
        "test_index": "index_bars.csv",
        "test_actions": "corporate_actions.csv",
    }

    def __init__(self, files: dict[str, str]) -> None:
        import io

        self._frames = {
            name: pd.read_csv(io.StringIO(text), dtype=str)
            for name, text in files.items()
        }

    def fetch(self, product_id, codes, start, end):
        frame = self._frames[self.TABLE_OF_PRODUCT[product_id]].copy()
        if codes and "code" in frame.columns:
            frame = frame[frame["code"].isin(list(codes))]
        if "date" in frame.columns:
            frame = frame[
                (frame["date"] >= start.isoformat())
                & (frame["date"] <= end.isoformat())
            ]
        return frame.reset_index(drop=True)


_FETCH_START = date(2023, 11, 30)
_FETCH_END = date(2024, 6, 28)


def _split_close_form(files: dict[str, str]) -> dict[str, str]:
    """600011.SH 最后交易日收盘减半（拆股假回撤形态），公司行动保持空。"""
    files = dict(files)
    lines = files["daily_bars.csv"].splitlines()
    target = max(i for i, line in enumerate(lines) if ",600011.SH," in line)
    parts = lines[target].split(",")
    parts[2] = f"{float(parts[2]) / 2:.6f}"
    lines[target] = ",".join(parts)
    files["daily_bars.csv"] = "\n".join(lines) + "\n"
    return files


def test_verified_empty_actions_partition_from_fetch_keeps_stock_eligible(
    tmp_path,
) -> None:
    """R3 写侧×R1 消费端到端：prepare 真实取数形态下，成功空响应为目标股
    写出空 code= 分区 + coverage（不手工标注、不入 gaps），拆股形态股票按
    "已核验无事件"参与排名；移除该分区后按 corporate_actions_coverage 排除。"""
    files = _split_close_form(make_case("ranking_universe", "base")["files"])
    provider = _CsvFetchProvider(files)
    config = json.loads(json.dumps(make_case("product_config_full", "base")))
    for product_id in provider.TABLE_OF_PRODUCT:
        config["products"][product_id]["provider"] = "csv"

    root = tmp_path / "store_root"
    root.mkdir(parents=True, exist_ok=True)
    config_text = json.dumps(config, ensure_ascii=False)
    config_path = root / "products.json"
    config_path.write_text(config_text, encoding="utf-8")
    store = DataStore(root / "store", config_path=config_path, providers={"csv": provider})
    # run_ranking 内部按 data.root 重建 DataStore，需要默认配置可解析
    (root / "store" / "data-products.json").write_text(config_text, encoding="utf-8")

    codes = tuple(
        sorted({str(code) for code in provider._frames["daily_bars.csv"]["code"]})
    )
    index_codes = tuple(
        sorted({str(code) for code in provider._frames["index_bars.csv"]["code"]})
    )
    ref = store.prepare(
        DataRequest(
            product_ids=tuple(sorted(provider.TABLE_OF_PRODUCT)),
            codes=codes,
            start=_FETCH_START,
            end=_FETCH_END,
            as_of=AS_OF,
            purpose="ranking_current",
            codes_by_product={"test_index": index_codes},
        )
    )
    manifest = store.load_manifest(ref)
    assert "test_actions" not in manifest.get("gaps", {}), (
        "成功空响应的公司行动产品不得进入 gaps"
    )
    entries = [
        entry
        for entry in manifest["table_files"]
        if entry["table"] == "corporate_actions"
        and entry["partition"] == "code=600011.SH"
    ]
    assert len(entries) == 1, "空响应目标股必须有对象级分区条目"
    assert entries[0]["coverage"] == ["2023-11-30", "2024-06-28"]
    assert len(store.read_table(ref, "corporate_actions")) == 0, "空分区=表内无该代码行"

    result = run_ranking(_definition(ref), ref, root / "out")
    tables = _read_result(result)
    assert "600011.SH" in set(tables["metrics"]["entity_id"]), (
        "空分区已核验：拆股形态股票不得按行动覆盖排除"
    )

    # 移除空分区 = 覆盖未核验：同一快照须按 corporate_actions_coverage 排除
    manifest_path = root / "store" / "snapshots" / ref.snapshot_id / "manifest.json"
    doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    doc["table_files"] = [
        entry
        for entry in doc["table_files"]
        if not (
            entry.get("table") == "corporate_actions"
            and str(entry.get("partition", "")).startswith("code=")
        )
    ]
    manifest_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    result2 = run_ranking(_definition(ref), ref, root / "out2")
    tables2 = _read_result(result2)
    hit = tables2["exclusions"][
        (tables2["exclusions"]["entity_id"] == "600011.SH")
        & (tables2["exclusions"]["scope"] == "eligibility")
    ]
    assert len(hit) and "corporate_actions_coverage" in str(hit.iloc[0]["reason"]), (
        "缺对象级覆盖证据时必须按行动资格排除"
    )
    assert "600011.SH" not in set(tables2["metrics"]["entity_id"])
