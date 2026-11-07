"""数据资产行为：标准化、重复键、空返回、局部失败、增量补缺、篡改与重开。"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from fixtures import make_case
from hetu_quant.data import DataQualityError, DataStore, SnapshotTamperedError
from hetu_quant.models import DataRequest, SnapshotRef

CN = ZoneInfo("Asia/Shanghai")


class FakeQuoteProvider:
    """确定的标准形日线替身；记录每次调用，只隔离网络。"""

    def __init__(
        self,
        empty_for: tuple[str, ...] = (),
        fail_for: tuple[str, ...] = (),
        duplicate_for: tuple[str, ...] = (),
    ) -> None:
        self.empty_for = set(empty_for)
        self.fail_for = set(fail_for)
        self.duplicate_for = set(duplicate_for)
        self.calls: list[tuple[str, tuple[str, ...], date, date]] = []

    def fetch(
        self, product_id: str, codes: tuple[str, ...], start: date, end: date
    ) -> pd.DataFrame:
        self.calls.append((product_id, tuple(codes), start, end))
        columns = [
            "date",
            "code",
            "open_raw",
            "high_raw",
            "low_raw",
            "close_raw",
            "volume_shares",
            "amount_cny",
        ]
        if product_id in self.fail_for:
            raise ConnectionError(f"fake source unavailable: {product_id}")
        if product_id in self.empty_for:
            return pd.DataFrame(columns=columns)
        rows = []
        for code in codes:
            for day in pd.bdate_range(start, end):
                rows.append(
                    {
                        "date": day.date().isoformat(),
                        "code": code,
                        "open_raw": 10.0,
                        "high_raw": 11.0,
                        "low_raw": 9.0,
                        "close_raw": 10.0,
                        "volume_shares": 1000.0,
                        "amount_cny": 10000.0,
                    }
                )
        if product_id in self.duplicate_for and rows:
            rows.append(dict(rows[0]))
        return pd.DataFrame(rows)


def _store(tmp_path: Path, provider: FakeQuoteProvider) -> DataStore:
    config = make_case("product_config", "base")
    config_path = tmp_path / "products.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return DataStore(tmp_path / "store", config_path=config_path, providers={"fake": provider})


def _request(
    start: date = date(2024, 1, 1),
    end: date = date(2024, 3, 31),
    codes: tuple[str, ...] = ("000001.SZ",),
    product_ids: tuple[str, ...] = ("test_prices",),
) -> DataRequest:
    return DataRequest(
        product_ids=product_ids,
        codes=codes,
        start=start,
        end=end,
        as_of=datetime(2024, 7, 1, 9, 0, tzinfo=CN),
        purpose="ranking_current",
    )


def test_standard_columns_and_source_rows(tmp_path: Path) -> None:
    store = _store(tmp_path, FakeQuoteProvider())
    ref = store.prepare(_request())
    table = store.read_table(ref, "daily_bars")
    assert {
        "date",
        "code",
        "open_raw",
        "high_raw",
        "low_raw",
        "close_raw",
        "volume_shares",
        "amount_cny",
        "product_id",
        "source_ref",
    } <= set(table.columns)
    assert set(table["product_id"]) == {"test_prices"}
    assert table["source_ref"].str.startswith("raw/test_prices/").all()


def test_duplicate_key_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path, FakeQuoteProvider(duplicate_for=("test_prices",)))
    with pytest.raises(DataQualityError, match="duplicate"):
        store.prepare(_request())


def test_empty_return_records_gap_not_rows(tmp_path: Path) -> None:
    store = _store(tmp_path, FakeQuoteProvider(empty_for=("test_prices",)))
    ref = store.prepare(_request())
    manifest = store.load_manifest(ref)
    assert manifest["gaps"]["test_prices"]
    table = store.read_table(ref, "daily_bars")
    assert len(table) == 0
    assert "close_raw" in table.columns


def test_product_failure_keeps_gap_and_last_success(tmp_path: Path) -> None:
    provider = FakeQuoteProvider(fail_for=("test_fin",))
    config = make_case("product_config", "base")
    config["products"]["test_fin"]["provider"] = "fake"
    config_path = tmp_path / "products.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    store = DataStore(tmp_path / "store", config_path=config_path, providers={"fake": provider})

    store.prepare(_request(product_ids=("test_prices",)))
    first_success = json.loads((tmp_path / "store" / "last_success.json").read_text())
    assert "test_prices" in first_success

    ref2 = store.prepare(_request(product_ids=("test_prices", "test_fin")))
    manifest = store.load_manifest(ref2)
    assert manifest["failures"][0]["product_id"] == "test_fin"
    assert manifest["failures"][0]["last_success"] is None
    assert manifest["failures"][0]["error"]
    # 失败不写更新成功；既有成功产品的最后成功时点保留
    after = json.loads((tmp_path / "store" / "last_success.json").read_text())
    assert after["test_prices"] == first_success["test_prices"]
    assert "test_fin" not in after


def test_incremental_prepare_fetches_only_missing_partitions(tmp_path: Path) -> None:
    provider = FakeQuoteProvider()
    store = _store(tmp_path, provider)
    ref1 = store.prepare(_request(end=date(2024, 3, 31)))
    calls_after_first = len(provider.calls)

    ref2 = store.prepare(_request(end=date(2024, 6, 30)))
    new_calls = provider.calls[calls_after_first:]
    assert new_calls, "extend 应有新增取数"
    for _, _, start, _ in new_calls:
        assert start >= date(2024, 4, 1), "只补缺失分区，不重取已有期间"
    table = store.read_table(ref2, "daily_bars")
    months = {str(d)[:7] for d in table["date"]}
    assert {"2024-01", "2024-02", "2024-03", "2024-04", "2024-05", "2024-06"} <= months
    # 旧快照不重写
    manifest1 = store.load_manifest(ref1)
    assert manifest1["snapshot_id"] == ref1.snapshot_id
    assert store.load_manifest(ref2)["revises"] == ref1.snapshot_id

    calls_after_second = len(provider.calls)
    ref3 = store.prepare(_request(end=date(2024, 6, 30)))
    assert ref3.snapshot_id == ref2.snapshot_id
    assert len(provider.calls) == calls_after_second, "完整覆盖时不重复取数"


def test_contiguous_missing_months_fetched_as_one_call(tmp_path: Path) -> None:
    """连续缺失月份合并为一次取数调用，不逐月拆请求。"""
    provider = FakeQuoteProvider()
    store = _store(tmp_path, provider)
    store.prepare(_request(end=date(2024, 3, 31)))
    calls_after_first = len(provider.calls)
    store.prepare(_request(end=date(2024, 6, 30)))
    new_calls = provider.calls[calls_after_first:]
    assert len(new_calls) == 1, f"连续缺失月份应合并为一次取数: {new_calls}"
    _, codes, start, end = new_calls[0]
    assert codes == ("000001.SZ",)
    assert start == date(2024, 4, 1) and end == date(2024, 6, 30)


def test_same_month_partial_extension_fetches_only_gap(tmp_path: Path) -> None:
    """A1: 同月部分覆盖必须识别为缺口。先入 03-01..03-15，扩窗到月底只补 03-16..03-31，
    不重取已有段；合并后整月齐全且同窗口幂等。"""
    provider = FakeQuoteProvider()
    store = _store(tmp_path, provider)
    store.prepare(_request(start=date(2024, 3, 1), end=date(2024, 3, 15)))
    calls_after_first = len(provider.calls)

    ref2 = store.prepare(_request(start=date(2024, 3, 1), end=date(2024, 3, 31)))
    new_calls = provider.calls[calls_after_first:]
    assert len(new_calls) == 1, f"同月扩窗应只补一次缺口: {new_calls}"
    _, codes, start, end = new_calls[0]
    assert codes == ("000001.SZ",)
    assert (start, end) == (date(2024, 3, 16), date(2024, 3, 31)), "只补未覆盖段"
    days = sorted(store.read_table(ref2, "daily_bars")["date"].astype(str).unique())
    expected = [d.date().isoformat() for d in pd.bdate_range("2024-03-01", "2024-03-31")]
    assert days == expected, "合并后 3 月整月工作日齐全、无重复"
    march = [
        entry
        for entry in store.load_manifest(ref2)["table_files"]
        if entry["partition"].endswith("month=2024-03")
    ]
    assert len(march) == 1 and march[0]["coverage"] == ["2024-03-01", "2024-03-31"]

    calls_after_second = len(provider.calls)
    ref3 = store.prepare(_request(start=date(2024, 3, 1), end=date(2024, 3, 31)))
    assert ref3.snapshot_id == ref2.snapshot_id
    assert len(provider.calls) == calls_after_second, "补齐后同窗口不得重取"


def test_cross_year_extension_fetches_new_year_gap(tmp_path: Path) -> None:
    """A1: 跨年扩窗同样按区间差集补缺，12 月只覆盖到 15 日时 12-16..01-31 为缺口。"""
    provider = FakeQuoteProvider()
    store = _store(tmp_path, provider)
    store.prepare(_request(start=date(2024, 11, 1), end=date(2024, 12, 15)))
    calls_after_first = len(provider.calls)

    ref2 = store.prepare(_request(start=date(2024, 11, 1), end=date(2025, 1, 31)))
    new_calls = provider.calls[calls_after_first:]
    assert [(codes, start, end) for _, codes, start, end in new_calls] == [
        (("000001.SZ",), date(2024, 12, 16), date(2025, 1, 31))
    ], f"跨年扩窗应合并为一次补取: {new_calls}"
    months = {str(d)[:7] for d in store.read_table(ref2, "daily_bars")["date"]}
    assert {"2024-11", "2024-12", "2025-01"} <= months


def _tamper_daily_bars_partition(tmp_path: Path, ref: SnapshotRef) -> None:
    table_dir = tmp_path / "store" / "snapshots" / ref.snapshot_id / "tables" / "daily_bars"
    target = next(iter(table_dir.glob("*.parquet")))
    frame = pd.read_parquet(target)
    frame["close_raw"] = 999.0
    frame.to_parquet(target, index=False)


def _published_snapshot_ids(store_root: Path) -> set[str]:
    return {
        child.name
        for child in (store_root / "snapshots").iterdir()
        if child.is_dir() and not child.name.startswith(".tmp-")
    }


def test_incremental_reuse_rejects_tampered_source_partition(tmp_path: Path) -> None:
    """A2: 增量复用旧分区前先按来源清单 sha256 校验；篡改必须拒绝而非静默接受，
    且不得发布含篡改值的新快照。"""
    store = _store(tmp_path, FakeQuoteProvider())
    ref1 = store.prepare(_request(end=date(2024, 3, 31)))
    _tamper_daily_bars_partition(tmp_path, ref1)
    with pytest.raises(SnapshotTamperedError, match="locked asset"):
        store.prepare(_request(end=date(2024, 6, 30)))
    assert _published_snapshot_ids(tmp_path / "store") == {ref1.snapshot_id}, (
        "被拒绝的增量不得发布新快照"
    )


def test_incremental_merge_rejects_tampered_source_partition(tmp_path: Path) -> None:
    """A2: 同月部分覆盖的合并复用路径同样先校验来源哈希，篡改必须拒绝。"""
    store = _store(tmp_path, FakeQuoteProvider())
    ref1 = store.prepare(_request(start=date(2024, 3, 1), end=date(2024, 3, 15)))
    _tamper_daily_bars_partition(tmp_path, ref1)
    with pytest.raises(SnapshotTamperedError, match="locked asset"):
        store.prepare(_request(start=date(2024, 3, 1), end=date(2024, 3, 31)))
    assert _published_snapshot_ids(tmp_path / "store") == {ref1.snapshot_id}
    with pytest.raises(SnapshotTamperedError):
        store.read_table(ref1, "daily_bars")


def test_tamper_detected_on_locked_asset(tmp_path: Path) -> None:
    store = _store(tmp_path, FakeQuoteProvider())
    ref = store.prepare(_request())
    table_dir = tmp_path / "store" / "snapshots" / ref.snapshot_id / "tables" / "daily_bars"
    target = next(iter(table_dir.glob("*.parquet")))
    content = bytearray(target.read_bytes())
    content[-10] ^= 0xFF
    target.write_bytes(bytes(content))
    with pytest.raises(SnapshotTamperedError, match="locked asset"):
        store.read_table(ref, "daily_bars")


def test_reopen_old_snapshot_with_new_store(tmp_path: Path) -> None:
    provider = FakeQuoteProvider()
    store = _store(tmp_path, provider)
    ref = store.prepare(_request())
    # 新实例、新 as_of：旧快照资格与内容不因此失效
    reopened = _store(tmp_path, FakeQuoteProvider())
    table = reopened.read_table(SnapshotRef(ref.snapshot_id, ref.root), "daily_bars")
    assert len(table) > 0
    later = _request()
    object.__setattr__(later, "as_of", datetime(2024, 8, 1, 9, 0, tzinfo=CN))
    table2 = reopened.read_table(ref, "daily_bars")
    assert len(table2) == len(table)


class FakeDatelessProvider:
    """无 date 列的表（成员、公司行动）替身；记录每次调用。

    empty_codes 指定的代码返回成功空响应（provider 正常返回、0 行），
    模拟混合批次中部分代码无记录的真实取数形态。
    """

    def __init__(
        self, empty_codes: tuple[str, ...] = (), fail_for: tuple[str, ...] = ()
    ) -> None:
        self.calls: list[tuple[str, tuple[str, ...], date, date]] = []
        self.empty_codes = set(empty_codes)
        self.fail_for = set(fail_for)

    def fetch(
        self, product_id: str, codes: tuple[str, ...], start: date, end: date
    ) -> pd.DataFrame:
        self.calls.append((product_id, tuple(codes), start, end))
        if product_id in self.fail_for:
            raise ConnectionError(f"fake source unavailable: {product_id}")
        codes = tuple(code for code in codes if code not in self.empty_codes)
        if not codes:
            return pd.DataFrame()
        if product_id == "test_members":
            return pd.DataFrame(
                {
                    "board_id": ["IND1"] * len(codes),
                    "code": list(codes),
                    "observed_at": ["2024-06-01T00:00:00+08:00"] * len(codes),
                    "available_at": ["2024-06-01T00:00:00+08:00"] * len(codes),
                    "membership_kind": ["current_snapshot"] * len(codes),
                }
            )
        if product_id == "test_actions":
            # ex_date 取请求窗口末日：扩窗补缺后新旧段键不同，可验证合并去重
            return pd.DataFrame(
                {
                    "code": list(codes),
                    "ex_date": [end.isoformat()] * len(codes),
                    "action_type": ["cash_dividend"] * len(codes),
                    "m": [1.0] * len(codes),
                    "d": [1.0] * len(codes),
                }
            )
        raise KeyError(product_id)


class _BoardBatchProvider(FakeDatelessProvider):
    """成员批次形状替身：按板块标识取数（请求域=板块代码），分区空间为
    派生股票代码——raw 文件按板块命名，与真实 sw/em 成员批次一致。"""

    BOARD_MEMBERS = {"801010": ("AAA.SZ", "BBB.SZ"), "801030": ("CCC.SZ",)}

    def fetch(
        self, product_id: str, codes: tuple[str, ...], start: date, end: date
    ) -> pd.DataFrame:
        self.calls.append((product_id, tuple(codes), start, end))
        if product_id in self.fail_for:
            raise ConnectionError(f"fake source unavailable: {product_id}")
        if product_id != "test_members":
            raise KeyError(product_id)
        rows = {
            "board_id": [],
            "code": [],
            "observed_at": [],
            "available_at": [],
            "membership_kind": [],
        }
        for board in codes:
            for stock in self.BOARD_MEMBERS.get(board, ()):
                rows["board_id"].append(board)
                rows["code"].append(stock)
                rows["observed_at"].append("2024-06-01T00:00:00+08:00")
                rows["available_at"].append("2024-06-01T00:00:00+08:00")
                rows["membership_kind"].append("current_snapshot")
        return pd.DataFrame(rows)


def _dateless_store(tmp_path: Path, provider: FakeDatelessProvider) -> DataStore:
    base = make_case("product_config", "base")["products"]["test_prices"]
    members = dict(
        base, kind="membership", tables=["memberships"], provider="fake"
    )
    actions = dict(
        base, kind="event", tables=["corporate_actions"], provider="fake"
    )
    config = {
        "schema_version": 1,
        "products": {"test_members": members, "test_actions": actions},
    }
    config_path = tmp_path / "products.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return DataStore(tmp_path / "store", config_path=config_path, providers={"fake": provider})


def test_date_less_tables_partition_by_code(tmp_path: Path) -> None:
    """memberships/corporate_actions 无 date 列，按 code 分区并可覆盖复用。"""
    provider = FakeDatelessProvider()
    store = _dateless_store(tmp_path, provider)
    request = DataRequest(
        product_ids=("test_members", "test_actions"),
        codes=("000001.SZ",),
        start=date(2024, 1, 1),
        end=date(2024, 3, 31),
        as_of=datetime(2024, 7, 1, 9, 0, tzinfo=CN),
        purpose="ranking_current",
    )
    ref = store.prepare(request)
    members = store.read_table(ref, "memberships")
    assert len(members) == 1
    assert members.iloc[0]["code"] == "000001.SZ"
    actions = store.read_table(ref, "corporate_actions")
    assert len(actions) == 1
    assert actions.iloc[0]["action_type"] == "cash_dividend"

    calls = len(provider.calls)
    ref2 = store.prepare(request)
    assert ref2.snapshot_id == ref.snapshot_id, "完整覆盖时不重复取数"
    assert len(provider.calls) == calls


def _actions_only_store(tmp_path: Path, provider: FakeDatelessProvider) -> DataStore:
    base = make_case("product_config", "base")["products"]["test_prices"]
    actions = dict(base, kind="event", tables=["corporate_actions"], provider="fake")
    config = {
        "schema_version": 1,
        "products": {"test_actions": actions},
    }
    config_path = tmp_path / "products.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return DataStore(tmp_path / "store", config_path=config_path, providers={"fake": provider})


def _actions_request(start: date, end: date) -> DataRequest:
    return DataRequest(
        product_ids=("test_actions",),
        codes=("000001.SZ",),
        start=start,
        end=end,
        as_of=datetime(2026, 1, 5, 9, 0, tzinfo=CN),
        purpose="low_volatility_preparation",
    )


def test_event_product_fetches_only_gap_period_on_extension(tmp_path: Path) -> None:
    """R3: code 分区事件产品按查询期间补缺。先入 2024 年窗口，扩到 2025 年时
    只对 2025 年缺口取数一次，2024 年不重取，合并后分区覆盖两年。"""
    provider = FakeDatelessProvider()
    store = _actions_only_store(tmp_path, provider)
    store.prepare(_actions_request(date(2024, 1, 1), date(2024, 12, 31)))
    calls_after_first = len(provider.calls)

    ref2 = store.prepare(_actions_request(date(2024, 1, 1), date(2025, 12, 31)))
    new_calls = provider.calls[calls_after_first:]
    assert [(codes, start, end) for _, codes, start, end in new_calls] == [
        (("000001.SZ",), date(2025, 1, 1), date(2025, 12, 31))
    ], f"扩窗应只对 2025 年缺口取数一次: {new_calls}"
    actions = store.read_table(ref2, "corporate_actions")
    assert sorted(actions["ex_date"].astype(str)) == ["2024-12-31", "2025-12-31"], (
        "旧段在前、新段补缺，合并覆盖两年"
    )
    entries = [
        entry
        for entry in store.load_manifest(ref2)["table_files"]
        if entry["partition"] == "code=000001.SZ"
    ]
    assert len(entries) == 1
    assert entries[0]["coverage"] == ["2024-01-01", "2025-12-31"], (
        "code 分区条目持久化 coverage [起始, 结束]"
    )


def test_event_product_same_window_prepare_is_idempotent(tmp_path: Path) -> None:
    """R3: 重复同窗口 prepare 幂等：零取数、返回旧快照。"""
    provider = FakeDatelessProvider()
    store = _actions_only_store(tmp_path, provider)
    ref1 = store.prepare(_actions_request(date(2024, 1, 1), date(2025, 12, 31)))
    calls = len(provider.calls)

    ref2 = store.prepare(_actions_request(date(2024, 1, 1), date(2025, 12, 31)))
    assert ref2.snapshot_id == ref1.snapshot_id
    assert len(provider.calls) == calls, "完整覆盖时不得重复取数"


class _ProvenActions(FakeDatelessProvider):
    """合成查询原事实；沿用原事件替身，不附资格或预期结果。"""

    def fetch(self, product_id, codes, start, end):
        raw = super().fetch(product_id, codes, start, end)
        raw.attrs["query_evidence"] = [
            dict(code=code, basis="ex_date", start=str(start), end=str(end),
                 method="query_dividend_data", yearType="operate") for code in codes
        ]
        return raw


def test_action_increment_uses_proven_query_span_not_generic_coverage(tmp_path: Path) -> None:
    """历史组合资产的 generic coverage 不得遮蔽实际 operate 缺口。"""
    from hetu_quant.data import action_query_covers

    provider = _ProvenActions()
    store = _actions_only_store(tmp_path, provider)
    old = store.prepare(_actions_request(date(2024, 1, 1), date(2024, 12, 31)))
    path = store.root / "snapshots" / old.snapshot_id / "manifest.json"
    manifest = json.loads(path.read_text())
    # 仅模拟已存历史形状：原件/真实查询证明不变，通用跨度比实际证明宽。
    manifest["table_files"][0]["coverage"] = ["2024-01-01", "2025-12-31"]
    manifest["table_files"][0].pop("query_basis", None)
    path.write_text(json.dumps(manifest))
    ref = store.prepare(_actions_request(date(2024, 1, 1), date(2025, 12, 31)))
    assert [(start, end) for _, _, start, end in provider.calls] == [
        (date(2024, 1, 1), date(2024, 12, 31)),
        (date(2025, 1, 1), date(2025, 12, 31)),
    ]
    assert len(store.read_table(ref, "corporate_actions")) == 2
    entry = store.load_manifest(ref)["table_files"][0]
    assert action_query_covers(entry, "2024-01-01", "2025-12-31")
    reopened = DataStore(store.root, config_path=tmp_path / "products.json",
                         providers={"fake": provider})
    assert reopened.prepare(_actions_request(date(2024, 1, 1), date(2025, 12, 31))) == ref
    assert len(provider.calls) == 2


@pytest.mark.parametrize("item_count", [1, 2])
def test_official_action_supplement_preserves_query_proof_and_rejects_overlap(tmp_path, item_count):
    """已存查询证明与新官方事件事实分别继承，不能以补事实伪造查询覆盖。"""
    from hetu_quant.data import action_query_covers

    store = _actions_only_store(tmp_path, _ProvenActions())
    base = store.prepare(_actions_request(date(2024, 1, 1), date(2024, 12, 31)))
    parent_entry = store.load_manifest(base)["table_files"][0]
    parent_file = store.root / "snapshots" / base.snapshot_id / parent_entry["path"]
    parent_bytes = parent_file.read_bytes()
    files, items, extra_days = [], [], ["2024-06-28", "2024-09-27"][:item_count]
    for index, day in enumerate(extra_days):
        csv = tmp_path / f"official-actions-{index}.csv"
        pd.DataFrame([dict(code="000001.SZ", ex_date=day, action_type="cash_dividend",
            m=1, d=2, record_date=("2024-06-27", "2024-09-26")[index],
            pay_date=("2024-07-01", "2024-09-30")[index],
            available_at=("2024-06-21", "2024-09-20")[index])]).to_csv(csv, index=False)
        files.append(csv)
        items.append(dict(product_id="test_actions",table="corporate_actions",supplement=True,
            files=[csv.name], source=dict(name=f"synthetic_official_{index}",fee_class="free",
                permission_basis="合成免费原件",obtained_at="2025-01-01T00:00:00+08:00")))
    doc = dict(mode="free", base_snapshot_ids=[base.snapshot_id], items=items)
    manifest = tmp_path / "official-import.json"
    manifest.write_text(json.dumps(doc))
    ref = store.import_files(manifest, tuple(files))
    frame = store.read_table(ref, "corporate_actions")
    assert sorted(frame["ex_date"].astype(str)) == extra_days + ["2024-12-31"]
    assert parent_file.read_bytes() == parent_bytes
    current = store.load_manifest(ref)
    assert len(list((store.root / "snapshots" / ref.snapshot_id / "tables/corporate_actions").glob("*.parquet"))) == 1
    assert current["table_files"][0]["coverage"] == parent_entry["coverage"]
    assert current["table_files"][0]["query_coverage"] == parent_entry["query_coverage"]
    for item in current["items"]:
        for raw in item["raw_files"]:
            raw_file = store.root / "snapshots" / ref.snapshot_id / raw["path"]
            assert hashlib.sha256(raw_file.read_bytes()).hexdigest() == raw["sha256"]
    assert {Path(x).name for x in frame["source_ref"] if "official-actions-" in x} == {f.name for f in files}
    entry = next(e for e in store.load_manifest(ref)["table_files"] if e["partition"].startswith("code="))
    assert action_query_covers(entry,"2024-01-01","2024-12-31")
    origin = entry["supplemented_from"]
    assert hashlib.sha256((store.root / "snapshots" / origin["snapshot_id"] /
                           origin["path"]).read_bytes()).hexdigest() == origin["sha256"]
    assert next(frame[frame.ex_date.astype(str)=="2024-06-28"].itertuples()).pay_date == "2024-07-01"
    extended = store.prepare(_actions_request(date(2024, 1, 1), date(2025, 12, 31)))
    assert sorted(store.read_table(extended, "corporate_actions")["ex_date"].astype(str)) == [
        *extra_days, "2024-12-31", "2025-12-31"]
    doc['base_snapshot_ids'] = [ref.snapshot_id]
    manifest.write_text(json.dumps(doc))
    with pytest.raises(DataQualityError, match="overlap"):
        store.import_files(manifest, tuple(files))


def _tamper_actions_partition(tmp_path: Path, ref: SnapshotRef) -> None:
    table_dir = tmp_path / "store" / "snapshots" / ref.snapshot_id / "tables" / "corporate_actions"
    target = next(iter(table_dir.glob("*.parquet")))
    frame = pd.read_parquet(target)
    frame["d"] = 999.0
    frame.to_parquet(target, index=False)


def test_event_extension_rejects_tampered_source_partition(tmp_path: Path) -> None:
    """R3: 增量扩窗合并复用前按来源清单校验；篡改必须拒绝且不发布新快照。"""
    store = _actions_only_store(tmp_path, FakeDatelessProvider())
    ref1 = store.prepare(_actions_request(date(2024, 1, 1), date(2024, 12, 31)))
    _tamper_actions_partition(tmp_path, ref1)
    with pytest.raises(SnapshotTamperedError, match="locked asset"):
        store.prepare(_actions_request(date(2024, 1, 1), date(2025, 12, 31)))
    assert _published_snapshot_ids(tmp_path / "store") == {ref1.snapshot_id}


def _two_code_request(start: date, end: date) -> DataRequest:
    return DataRequest(
        product_ids=("test_actions",),
        codes=("AAA.SZ", "BBB.SZ"),
        start=start,
        end=end,
        as_of=datetime(2026, 1, 5, 9, 0, tzinfo=CN),
        purpose="low_volatility_preparation",
    )


def _partition_entries(store: DataStore, ref: SnapshotRef, partition: str) -> list[dict]:
    return [
        entry
        for entry in store.load_manifest(ref)["table_files"]
        if entry["partition"] == partition
    ]


def test_event_empty_response_writes_partition_with_coverage_not_gaps(
    tmp_path: Path,
) -> None:
    """成功空响应的对象级覆盖证据：混合批次中空代码写出 0 行 code= 分区并记
    coverage（该代码实际被查询的期间），不计入 gaps——R1 按分区+coverage
    消费"已核验无事件"，缺条目会被误判为未核验排除。"""
    provider = FakeDatelessProvider(empty_codes=("BBB.SZ",))
    store = _actions_only_store(tmp_path, provider)
    ref = store.prepare(_two_code_request(date(2024, 1, 1), date(2024, 12, 31)))

    manifest = store.load_manifest(ref)
    assert "test_actions" not in manifest["gaps"], "成功空响应不再计入 gaps"
    entries = _partition_entries(store, ref, "code=BBB.SZ")
    assert len(entries) == 1, "空代码必须有对象级分区条目"
    entry = entries[0]
    assert entry["coverage"] == ["2024-01-01", "2024-12-31"]
    target = tmp_path / "store" / "snapshots" / ref.snapshot_id / entry["path"]
    assert target.exists()
    assert hashlib.sha256(target.read_bytes()).hexdigest() == entry["sha256"]
    empty = pd.read_parquet(target)
    assert len(empty) == 0
    assert {"code", "ex_date", "action_type"} <= set(empty.columns)
    actions = store.read_table(ref, "corporate_actions")
    assert sorted(actions["code"].astype(str)) == ["AAA.SZ"]


def test_event_empty_partition_participates_in_incremental_reuse(
    tmp_path: Path,
) -> None:
    """空分区与非空分区行为一致：扩窗按 coverage 差集只补新期间，
    合并取 coverage 并集，同窗口幂等零取数。"""
    provider = FakeDatelessProvider(empty_codes=("BBB.SZ",))
    store = _actions_only_store(tmp_path, provider)
    store.prepare(_two_code_request(date(2024, 1, 1), date(2024, 12, 31)))
    calls_after_first = len(provider.calls)

    ref2 = store.prepare(_two_code_request(date(2024, 1, 1), date(2025, 12, 31)))
    new_calls = provider.calls[calls_after_first:]
    assert [(codes, start, end) for _, codes, start, end in new_calls] == [
        (("AAA.SZ",), date(2025, 1, 1), date(2025, 12, 31)),
        (("BBB.SZ",), date(2025, 1, 1), date(2025, 12, 31)),
    ], f"空分区扩窗同样只补缺口期间: {new_calls}"
    entries = _partition_entries(store, ref2, "code=BBB.SZ")
    assert len(entries) == 1 and entries[0]["coverage"] == ["2024-01-01", "2025-12-31"], (
        "空分区与新段合并取 coverage 并集"
    )
    assert "test_actions" not in store.load_manifest(ref2)["gaps"]
    actions = store.read_table(ref2, "corporate_actions")
    assert sorted(
        zip(actions["code"].astype(str), actions["ex_date"].astype(str), strict=True)
    ) == [
        ("AAA.SZ", "2024-12-31"),
        ("AAA.SZ", "2025-12-31"),
    ]

    calls_after_second = len(provider.calls)
    ref3 = store.prepare(_two_code_request(date(2024, 1, 1), date(2025, 12, 31)))
    assert ref3.snapshot_id == ref2.snapshot_id, "补齐后同窗口幂等返回旧快照"
    assert len(provider.calls) == calls_after_second, "幂等时零取数"


def test_event_all_empty_first_prepare_records_coverage(tmp_path: Path) -> None:
    """复评 1a：首次整窗成功空也写出空分区，重复请求零取数。"""
    provider = FakeDatelessProvider(empty_codes=("AAA.SZ", "BBB.SZ"))
    store = _actions_only_store(tmp_path, provider)
    ref1 = store.prepare(_two_code_request(date(2024, 1, 1), date(2024, 12, 31)))
    manifest = store.load_manifest(ref1)
    assert "test_actions" not in manifest["gaps"]
    for code in ("AAA.SZ", "BBB.SZ"):
        entries = _partition_entries(store, ref1, f"code={code}")
        assert len(entries) == 1, f"{code} 必须有对象级空分区"
        assert entries[0]["coverage"] == ["2024-01-01", "2024-12-31"]

    calls = len(provider.calls)
    ref2 = store.prepare(_two_code_request(date(2024, 1, 1), date(2024, 12, 31)))
    assert ref2.snapshot_id == ref1.snapshot_id, "整窗成功空后同窗口幂等"
    assert len(provider.calls) == calls, "不得把成功空响应当缺口重抓"


def test_event_failed_fetch_not_recorded_as_coverage(tmp_path: Path) -> None:
    """复评 1c：fetch 失败的代码与期间不登记任何覆盖，后续请求必须重取；
    同批成功的产品不受影响（整段复制、零取数）。"""
    provider = FakeDatelessProvider(fail_for=("test_actions",))
    store = _dateless_store(tmp_path, provider)
    request = DataRequest(
        product_ids=("test_members", "test_actions"),
        codes=("000001.SZ",),
        start=date(2024, 1, 1),
        end=date(2024, 3, 31),
        as_of=datetime(2024, 7, 1, 9, 0, tzinfo=CN),
        purpose="ranking_current",
    )
    ref1 = store.prepare(request)
    manifest = store.load_manifest(ref1)
    assert [failure["product_id"] for failure in manifest["failures"]] == ["test_actions"]
    assert "test_actions" not in manifest["gaps"], "失败记 failures，不记 gaps"
    calls_after_first = len(provider.calls)

    ref2 = store.prepare(request)
    new_calls = provider.calls[calls_after_first:]
    assert [(product_id, codes) for product_id, codes, _, _ in new_calls] == [
        ("test_actions", ("000001.SZ",))
    ], f"失败代码必须按原窗口重取，成功产品不得重取: {new_calls}"
    assert ref2.snapshot_id != ref1.snapshot_id


def _strip_coverage_metadata(store_root: Path, ref: SnapshotRef) -> None:
    """模拟 coverage 元数据出现之前的旧格式 prepare 快照（request/raw/哈希保留）。"""
    manifest_path = store_root / "snapshots" / ref.snapshot_id / "manifest.json"
    doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in doc["table_files"]:
        entry.pop("coverage", None)
    manifest_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def test_legacy_snapshot_recovered_from_request_and_raw_evidence(tmp_path: Path) -> None:
    """复评 2a：旧格式快照（无 coverage，保留 request/raw/哈希）同窗再请求
    按证据链恢复覆盖：零取数。"""
    provider = FakeDatelessProvider()
    store = _actions_only_store(tmp_path, provider)
    ref1 = store.prepare(_two_code_request(date(2024, 1, 1), date(2024, 12, 31)))
    _strip_coverage_metadata(tmp_path / "store", ref1)

    calls = len(provider.calls)
    ref2 = store.prepare(_two_code_request(date(2024, 1, 1), date(2024, 12, 31)))
    assert len(provider.calls) == calls, "可核验的旧覆盖不得整窗重取"
    assert ref2.snapshot_id == ref1.snapshot_id, "全部覆盖且同一快照满足：返回旧快照"
    actions = store.read_table(ref2, "corporate_actions")
    assert len(actions) == 2


def test_legacy_snapshot_extension_fetches_only_gap(tmp_path: Path) -> None:
    """复评 2b：旧格式快照扩窗，恢复的覆盖只把 2025 当缺口，2024 不重取。"""
    provider = FakeDatelessProvider()
    store = _actions_only_store(tmp_path, provider)
    ref1 = store.prepare(_two_code_request(date(2024, 1, 1), date(2024, 12, 31)))
    _strip_coverage_metadata(tmp_path / "store", ref1)
    calls_after_first = len(provider.calls)

    ref2 = store.prepare(_two_code_request(date(2024, 1, 1), date(2025, 12, 31)))
    new_calls = provider.calls[calls_after_first:]
    assert [(codes, start, end) for _, codes, start, end in new_calls] == [
        (("AAA.SZ",), date(2025, 1, 1), date(2025, 12, 31)),
        (("BBB.SZ",), date(2025, 1, 1), date(2025, 12, 31)),
    ], f"旧格式扩窗只补缺口期间: {new_calls}"
    for code in ("AAA.SZ", "BBB.SZ"):
        entries = _partition_entries(store, ref2, f"code={code}")
        assert len(entries) == 1
        assert entries[0]["coverage"] == ["2024-01-01", "2025-12-31"], (
            "恢复的覆盖与新段合并取并集"
        )

    calls_after_second = len(provider.calls)
    ref3 = store.prepare(_two_code_request(date(2024, 1, 1), date(2025, 12, 31)))
    assert ref3.snapshot_id == ref2.snapshot_id
    assert len(provider.calls) == calls_after_second, "合并后同窗口幂等零取数"


def _tamper_raw_response(tmp_path: Path, ref: SnapshotRef, product_id: str) -> Path:
    raw_dir = tmp_path / "store" / "snapshots" / ref.snapshot_id / "raw" / product_id
    target = next(iter(raw_dir.glob("*.parquet")))
    content = bytearray(target.read_bytes())
    content[-10] ^= 0xFF
    target.write_bytes(bytes(content))
    return target


def test_legacy_snapshot_recovery_rejects_tampered_evidence(tmp_path: Path) -> None:
    """复评 2c：篡改 raw 或分区后，恢复/复用前哈希检查拒绝（A2 语义）。"""
    provider = FakeDatelessProvider()
    store = _actions_only_store(tmp_path, provider)
    ref1 = store.prepare(_two_code_request(date(2024, 1, 1), date(2024, 12, 31)))

    _strip_coverage_metadata(tmp_path / "store", ref1)
    _tamper_raw_response(tmp_path, ref1, "test_actions")
    with pytest.raises(SnapshotTamperedError, match="locked asset"):
        store.prepare(_two_code_request(date(2024, 1, 1), date(2024, 12, 31)))
    assert _published_snapshot_ids(tmp_path / "store") == {ref1.snapshot_id}

    # 分区篡改：同窗复制与扩窗合并两条复用路径都必须拒绝
    store2_root = tmp_path / "store2"
    provider2 = FakeDatelessProvider()
    config_path = tmp_path / "products2.json"
    config_path.write_text(
        (tmp_path / "products.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    store2 = DataStore(store2_root, config_path=config_path, providers={"fake": provider2})
    ref2 = store2.prepare(_two_code_request(date(2024, 1, 1), date(2024, 12, 31)))
    _strip_coverage_metadata(store2_root, ref2)
    tampered = next(
        (store2_root / "snapshots" / ref2.snapshot_id / "tables" / "corporate_actions").glob(
            "*AAA*.parquet"
        )
    )
    frame = pd.read_parquet(tampered)
    frame["d"] = 999.0
    frame.to_parquet(tampered, index=False)
    with pytest.raises(SnapshotTamperedError, match="locked asset"):
        store2.prepare(_two_code_request(date(2024, 1, 1), date(2024, 12, 31)))
    with pytest.raises(SnapshotTamperedError, match="locked asset"):
        store2.prepare(_two_code_request(date(2024, 1, 1), date(2025, 12, 31)))


def test_legacy_snapshot_recovery_refetches_when_raw_missing(tmp_path: Path) -> None:
    """复评 2d：raw 响应缺失无法证明覆盖的代码如实重取；可证明的代码不重取。"""
    provider = FakeDatelessProvider()
    store = _actions_only_store(tmp_path, provider)
    ref1 = store.prepare(_two_code_request(date(2024, 1, 1), date(2024, 12, 31)))
    _strip_coverage_metadata(tmp_path / "store", ref1)
    raw_bbb = next(
        (tmp_path / "store" / "snapshots" / ref1.snapshot_id / "raw" / "test_actions").glob(
            "*BBB*.parquet"
        )
    )
    raw_bbb.unlink()

    calls_after_first = len(provider.calls)
    store.prepare(_two_code_request(date(2024, 1, 1), date(2024, 12, 31)))
    new_calls = provider.calls[calls_after_first:]
    assert [(codes, start, end) for _, codes, start, end in new_calls] == [
        (("BBB.SZ",), date(2024, 1, 1), date(2024, 12, 31))
    ], f"raw 缺失的代码按原窗口重取，其余代码复用: {new_calls}"


def test_legacy_board_batch_snapshot_recovers_derived_partitions(tmp_path: Path) -> None:
    """P1-2：分区空间由批次查询派生的旧格式快照（成员按板块取数：请求域=
    板块代码，分区=派生股票代码）：分区存在 + 该产品全部 raw 核验通过 +
    无取数失败 → 恢复覆盖，股票代码域再请求零取数。"""
    provider = _BoardBatchProvider()
    store = _dateless_store(tmp_path, provider)
    board_request = DataRequest(
        product_ids=("test_members",),
        codes=("801010", "801030"),
        start=date(2024, 1, 1),
        end=date(2024, 12, 31),
        as_of=datetime(2026, 1, 5, 9, 0, tzinfo=CN),
        purpose="ranking_current",
    )
    ref1 = store.prepare(board_request)
    # 仅剥离 coverage 模拟历史格式；新写侧的对象语义由独立测试直接检查，
    # 此处不再删除板块空分区来掩盖写侧错误。
    for code in ("AAA.SZ", "BBB.SZ", "CCC.SZ"):
        assert _partition_entries(store, ref1, f"code={code}"), code
    _strip_coverage_metadata(tmp_path / "store", ref1)

    calls_after_first = len(provider.calls)
    stock_request = DataRequest(
        product_ids=("test_members",),
        codes=("AAA.SZ", "BBB.SZ"),
        start=date(2024, 1, 1),
        end=date(2024, 12, 31),
        as_of=datetime(2026, 1, 5, 9, 0, tzinfo=CN),
        purpose="ranking_current",
    )
    ref2 = store.prepare(stock_request)
    assert len(provider.calls) == calls_after_first, (
        "派生分区可由批次 raw 证据证明时不得安排重取"
    )
    assert ref2.snapshot_id == ref1.snapshot_id, (
        "全部覆盖且同一快照满足：返回旧快照（与逐码恢复 2a 行为一致）"
    )
    memberships = store.read_table(ref2, "memberships")
    assert sorted(memberships["code"].astype(str)) == ["AAA.SZ", "BBB.SZ", "CCC.SZ"]


def test_legacy_board_batch_missing_raw_refetches(tmp_path: Path) -> None:
    """P1-2 反例：派生分区空间下批次 raw 缺失（证据链不完整）→ 该产品
    分区不可证明，股票代码如实重取。"""
    provider = _BoardBatchProvider()
    store = _dateless_store(tmp_path, provider)
    board_request = DataRequest(
        product_ids=("test_members",),
        codes=("801010", "801030"),
        start=date(2024, 1, 1),
        end=date(2024, 12, 31),
        as_of=datetime(2026, 1, 5, 9, 0, tzinfo=CN),
        purpose="ranking_current",
    )
    ref1 = store.prepare(board_request)
    _strip_coverage_metadata(tmp_path / "store", ref1)
    raw = next(
        (tmp_path / "store" / "snapshots" / ref1.snapshot_id / "raw" / "test_members").glob(
            "*.parquet"
        )
    )
    raw.unlink()

    calls_after_first = len(provider.calls)
    stock_request = DataRequest(
        product_ids=("test_members",),
        codes=("AAA.SZ", "BBB.SZ"),
        start=date(2024, 1, 1),
        end=date(2024, 12, 31),
        as_of=datetime(2026, 1, 5, 9, 0, tzinfo=CN),
        purpose="ranking_current",
    )
    store.prepare(stock_request)
    new_calls = provider.calls[calls_after_first:]
    assert [(codes, start, end) for _, codes, start, end in new_calls] == [
        (("AAA.SZ",), date(2024, 1, 1), date(2024, 12, 31)),
        (("BBB.SZ",), date(2024, 1, 1), date(2024, 12, 31)),
    ], f"批次 raw 缺失时派生分区如实重取: {new_calls}"


def test_legacy_board_batch_tampered_raw_rejected(tmp_path: Path) -> None:
    """P1-2 反例：派生分区空间的批次 raw 被篡改 → 恢复前哈希核验拒绝
    （A2 语义），不静默降级为重取。"""
    provider = _BoardBatchProvider()
    store = _dateless_store(tmp_path, provider)
    board_request = DataRequest(
        product_ids=("test_members",),
        codes=("801010", "801030"),
        start=date(2024, 1, 1),
        end=date(2024, 12, 31),
        as_of=datetime(2026, 1, 5, 9, 0, tzinfo=CN),
        purpose="ranking_current",
    )
    ref1 = store.prepare(board_request)
    _strip_coverage_metadata(tmp_path / "store", ref1)
    _tamper_raw_response(tmp_path, ref1, "test_members")
    stock_request = DataRequest(
        product_ids=("test_members",),
        codes=("AAA.SZ", "BBB.SZ"),
        start=date(2024, 1, 1),
        end=date(2024, 12, 31),
        as_of=datetime(2026, 1, 5, 9, 0, tzinfo=CN),
        purpose="ranking_current",
    )
    with pytest.raises(SnapshotTamperedError, match="locked asset"):
        store.prepare(stock_request)


def test_legacy_recovered_coverage_survives_copy_when_adding_code(tmp_path: Path) -> None:
    """复评：旧格式 A 恢复覆盖后与新增 B 同窗合成，复制路径必须把恢复出的
    coverage 落进新快照条目——再次同窗请求零取数且幂等返回同一快照。"""
    provider = FakeDatelessProvider()
    store = _actions_only_store(tmp_path, provider)
    single = DataRequest(
        product_ids=("test_actions",),
        codes=("AAA.SZ",),
        start=date(2024, 1, 1),
        end=date(2024, 12, 31),
        as_of=datetime(2026, 1, 5, 9, 0, tzinfo=CN),
        purpose="low_volatility_preparation",
    )
    ref1 = store.prepare(single)
    _strip_coverage_metadata(tmp_path / "store", ref1)
    calls_after_first = len(provider.calls)

    ref2 = store.prepare(_two_code_request(date(2024, 1, 1), date(2024, 12, 31)))
    new_calls = provider.calls[calls_after_first:]
    assert [(codes, start, end) for _, codes, start, end in new_calls] == [
        (("BBB.SZ",), date(2024, 1, 1), date(2024, 12, 31))
    ], f"旧格式 A 复用，只取新代码 B: {new_calls}"
    entries = _partition_entries(store, ref2, "code=AAA.SZ")
    assert len(entries) == 1 and entries[0]["coverage"] == ["2024-01-01", "2024-12-31"], (
        "复制路径必须把恢复出的 coverage 落进新快照，回到新格式"
    )

    calls_after_second = len(provider.calls)
    ref3 = store.prepare(_two_code_request(date(2024, 1, 1), date(2024, 12, 31)))
    assert ref3.snapshot_id == ref2.snapshot_id, "再次同窗必须幂等返回同一快照"
    assert len(provider.calls) == calls_after_second, "再次同窗零取数"


def _remove_partition(store_root: Path, ref: SnapshotRef, partition: str) -> None:
    """删除某分区的清单条目与文件（模拟"成功空响应只有 raw、无分区"的旧批次）。"""
    manifest_path = store_root / "snapshots" / ref.snapshot_id / "manifest.json"
    doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    kept = []
    for entry in doc["table_files"]:
        if entry["partition"] == partition:
            target = store_root / "snapshots" / ref.snapshot_id / entry["path"]
            target.unlink(missing_ok=True)
        else:
            kept.append(entry)
    doc["table_files"] = kept
    manifest_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def test_legacy_candidate_fallback_recovers_from_older_provable_snapshot(
    tmp_path: Path,
) -> None:
    """P1：最新快照候选无 coverage 且无 raw 证据（组合快照只复制分区）时，
    回退到较旧候选按该快照的 request+raw 证据恢复：复制较旧分区、零取数，
    新快照条目落恢复出的 coverage，再次请求幂等。"""
    provider = FakeDatelessProvider()
    store = _actions_only_store(tmp_path, provider)
    single = DataRequest(
        product_ids=("test_actions",),
        codes=("AAA.SZ",),
        start=date(2024, 1, 1),
        end=date(2024, 12, 31),
        as_of=datetime(2026, 1, 5, 9, 0, tzinfo=CN),
        purpose="low_volatility_preparation",
    )
    ref1 = store.prepare(single)
    _strip_coverage_metadata(tmp_path / "store", ref1)
    ref2 = store.prepare(_two_code_request(date(2024, 1, 1), date(2024, 12, 31)))
    assert ref2.snapshot_id != ref1.snapshot_id
    # 组合快照形状：最新快照条目无 coverage 且 items raw_files=[]
    _strip_coverage_metadata(tmp_path / "store", ref2)

    calls_after_second = len(provider.calls)
    ref3 = store.prepare(_two_code_request(date(2024, 1, 1), date(2024, 12, 31)))
    assert len(provider.calls) == calls_after_second, (
        "较旧候选可证明时不得安排重取"
    )
    assert ref3.snapshot_id != ref2.snapshot_id
    for code in ("AAA.SZ", "BBB.SZ"):
        entries = _partition_entries(store, ref3, f"code={code}")
        assert len(entries) == 1, f"{code}: 必须从可证明候选复制"
        assert entries[0]["coverage"] == ["2024-01-01", "2024-12-31"], (
            "复制路径把恢复出的 coverage 落进新快照条目"
        )

    calls_after_third = len(provider.calls)
    ref4 = store.prepare(_two_code_request(date(2024, 1, 1), date(2024, 12, 31)))
    assert ref4.snapshot_id == ref3.snapshot_id, "再次同窗幂等返回同一快照"
    assert len(provider.calls) == calls_after_third, "幂等时零取数"


def test_empty_response_without_partition_recovers_empty_partition(
    tmp_path: Path,
) -> None:
    """P1：某代码在任何快照都没有分区，但原始批次有该代码的成功空响应
    raw（sha256 核验通过）→ 写空分区并登记恢复出的 coverage，零取数。"""
    provider = FakeDatelessProvider(empty_codes=("BBB.SZ",))
    store = _actions_only_store(tmp_path, provider)
    ref1 = store.prepare(_two_code_request(date(2024, 1, 1), date(2024, 12, 31)))
    # 旧批次形状：BBB 只有 raw 空响应证据，无任何快照有空分区
    _remove_partition(tmp_path / "store", ref1, "code=BBB.SZ")
    assert not _partition_entries(store, ref1, "code=BBB.SZ")

    calls_after_first = len(provider.calls)
    ref2 = store.prepare(_two_code_request(date(2024, 1, 1), date(2024, 12, 31)))
    assert len(provider.calls) == calls_after_first, (
        "有已核验空响应证据的代码不得安排重取"
    )
    assert ref2.snapshot_id != ref1.snapshot_id
    entries = _partition_entries(store, ref2, "code=BBB.SZ")
    assert len(entries) == 1, "必须写出恢复的空分区"
    assert entries[0]["coverage"] == ["2024-01-01", "2024-12-31"], (
        "coverage = 证据快照 request 的 [start, end]"
    )
    target = tmp_path / "store" / "snapshots" / ref2.snapshot_id / entries[0]["path"]
    assert target.exists() and len(pd.read_parquet(target)) == 0, "空分区=已核验无记录"
    assert "test_actions" not in store.load_manifest(ref2).get("gaps", {})
    actions = store.read_table(ref2, "corporate_actions")
    assert sorted(actions["code"].astype(str)) == ["AAA.SZ"]


def test_recovered_empty_partition_first_reads_and_reopens_idempotent(
    tmp_path: Path,
) -> None:
    """P1-2 复评：空分区（0 行，arrow null 列类型）排在非空分区之前时，
    整表读取必须成功（union_by_name 统一 schema）；重开存储（新实例读
    同一磁盘状态）再次请求零调用、幂等返回同一快照。"""
    provider = FakeDatelessProvider(empty_codes=("AAA.SZ",))
    store = _actions_only_store(tmp_path, provider)
    ref1 = store.prepare(_two_code_request(date(2024, 1, 1), date(2024, 12, 31)))
    # 旧批次形状：AAA 只剩成功空响应 raw 证据（分区删除，排序将居首）
    _remove_partition(tmp_path / "store", ref1, "code=AAA.SZ")
    _strip_coverage_metadata(tmp_path / "store", ref1)

    calls_after_first = len(provider.calls)
    ref2 = store.prepare(_two_code_request(date(2024, 1, 1), date(2024, 12, 31)))
    assert len(provider.calls) == calls_after_first, "纯恢复零取数"
    entries = _partition_entries(store, ref2, "code=AAA.SZ")
    assert len(entries) == 1 and entries[0]["coverage"] == ["2024-01-01", "2024-12-31"]
    # 空分区在首位 + BBB 非空：整表读取不受 NULL 列类型影响
    actions = store.read_table(ref2, "corporate_actions")
    assert sorted(actions["code"].astype(str)) == ["BBB.SZ"]

    # 重开：新 DataStore 实例读同一磁盘状态，再次请求仍零调用且幂等
    reopened = DataStore(
        tmp_path / "store",
        config_path=tmp_path / "products.json",
        providers={"fake": provider},
    )
    calls_after_second = len(provider.calls)
    ref3 = reopened.prepare(_two_code_request(date(2024, 1, 1), date(2024, 12, 31)))
    assert ref3.snapshot_id == ref2.snapshot_id, "重开后同窗幂等返回同一快照"
    assert len(provider.calls) == calls_after_second, "重开后零取数"
    actions3 = reopened.read_table(ref3, "corporate_actions")
    assert sorted(actions3["code"].astype(str)) == ["BBB.SZ"]


def test_empty_response_without_raw_evidence_fetches(tmp_path: Path) -> None:
    """P1 反例：无分区且无 raw 证据的代码如实取数（宁可重取不得静默漏取）。"""
    provider = FakeDatelessProvider(empty_codes=("BBB.SZ",))
    store = _actions_only_store(tmp_path, provider)
    ref1 = store.prepare(_two_code_request(date(2024, 1, 1), date(2024, 12, 31)))
    _remove_partition(tmp_path / "store", ref1, "code=BBB.SZ")
    raw_bbb = next(
        (tmp_path / "store" / "snapshots" / ref1.snapshot_id / "raw" / "test_actions").glob(
            "*BBB*.parquet"
        )
    )
    raw_bbb.unlink()

    calls_after_first = len(provider.calls)
    store.prepare(_two_code_request(date(2024, 1, 1), date(2024, 12, 31)))
    new_calls = provider.calls[calls_after_first:]
    assert [(codes, start, end) for _, codes, start, end in new_calls] == [
        (("BBB.SZ",), date(2024, 1, 1), date(2024, 12, 31))
    ], f"无证据代码如实重取，其余代码复用: {new_calls}"


def test_recovered_empty_coverage_rejects_tampered_raw(tmp_path: Path) -> None:
    """P1：空响应证据的 raw 哈希不一致按 A2 语义拒绝（SnapshotTamperedError），
    不静默降级为重取。"""
    provider = FakeDatelessProvider(empty_codes=("BBB.SZ",))
    store = _actions_only_store(tmp_path, provider)
    ref1 = store.prepare(_two_code_request(date(2024, 1, 1), date(2024, 12, 31)))
    _remove_partition(tmp_path / "store", ref1, "code=BBB.SZ")
    raw_bbb = next(
        (tmp_path / "store" / "snapshots" / ref1.snapshot_id / "raw" / "test_actions").glob(
            "*BBB*.parquet"
        )
    )
    content = bytearray(raw_bbb.read_bytes())
    content[-10] ^= 0xFF
    raw_bbb.write_bytes(bytes(content))

    with pytest.raises(SnapshotTamperedError, match="locked asset"):
        store.prepare(_two_code_request(date(2024, 1, 1), date(2024, 12, 31)))
    assert _published_snapshot_ids(tmp_path / "store") == {ref1.snapshot_id}


def test_failed_fetch_is_not_recovered_empty_coverage(tmp_path: Path) -> None:
    """P1：failures 中的取数失败永远不算覆盖证据，后续请求必须如实重取。"""
    provider = FakeDatelessProvider(fail_for=("test_actions",))
    store = _actions_only_store(tmp_path, provider)
    with pytest.raises(ConnectionError, match="fake source unavailable"):
        store.prepare(_actions_request(date(2024, 1, 1), date(2024, 12, 31)))
    assert _published_snapshot_ids(tmp_path / "store") == set()

    provider.fail_for = ()
    calls_after_first = len(provider.calls)
    store.prepare(_actions_request(date(2024, 1, 1), date(2024, 12, 31)))
    assert len(provider.calls) == calls_after_first + 1, (
        "失败记录不产生恢复覆盖，必须重取"
    )


@pytest.mark.parametrize("boards", [("801010", "BK0001"), ("EMPTY",)])
def test_board_queries_write_only_returned_stocks_and_reopen_without_calls(tmp_path, boards):
    """真实 prepare 输出不得把查询板块当股票；成功空查询仍有可复用 raw 证据。"""
    provider = _BoardBatchProvider()
    provider.BOARD_MEMBERS = {
        "801010": ("AAA.SZ", "BBB.SZ"),
        "BK0001": ("BBB.SZ", "CCC.SZ"),
    }
    store = _dateless_store(tmp_path, provider)
    request = DataRequest(
        product_ids=("test_members",), codes=boards,
        start=date(2024, 1, 1), end=date(2024, 12, 31),
        as_of=datetime(2026, 1, 5, 9, 0, tzinfo=CN), purpose="ranking_current",
    )
    ref = store.prepare(request)
    manifest = store.load_manifest(ref)
    entries = manifest["table_files"]
    expected = {"code=AAA.SZ", "code=BBB.SZ", "code=CCC.SZ"} if boards[0] != "EMPTY" else set()
    assert {entry["partition"] for entry in entries} == expected
    table_dir = tmp_path / "store" / "snapshots" / ref.snapshot_id / "tables" / "memberships"
    assert len(list(table_dir.glob("*.parquet"))) == len(expected)
    members = store.read_table(ref, "memberships")
    assert set(zip(members["board_id"], members["code"], strict=True)) == (
        {("801010", "AAA.SZ"), ("801010", "BBB.SZ"),
         ("BK0001", "BBB.SZ"), ("BK0001", "CCC.SZ")} if expected else set()
    )
    assert len(manifest["items"][0]["raw_files"]) == len(boards)
    assert not manifest["failures"] and not manifest["gaps"]
    calls = len(provider.calls)
    reopened = _dateless_store(tmp_path, provider)
    ref2 = reopened.prepare(request)
    assert len(provider.calls) == calls
    assert ref2.snapshot_id == ref.snapshot_id
    assert {entry["partition"] for entry in reopened.load_manifest(ref2)["table_files"]} == expected


def test_failed_board_query_does_not_register_success_coverage(tmp_path):
    provider = _BoardBatchProvider(fail_for=("test_members",))
    store = _dateless_store(tmp_path, provider)
    request = DataRequest(
        product_ids=("test_members", "test_actions"), codes=("801010",),
        start=date(2024, 1, 1), end=date(2024, 12, 31),
        as_of=datetime(2026, 1, 5, 9, 0, tzinfo=CN), purpose="ranking_current",
    )
    # 使用行动产品成功结果保留部分失败快照。
    provider_fetch = provider.fetch
    actions = FakeDatelessProvider()

    def fetch(product_id, codes, start, end):
        if product_id == "test_actions":
            return actions.fetch(product_id, codes, start, end)
        return provider_fetch(product_id, codes, start, end)

    provider.fetch = fetch
    ref = store.prepare(request)
    manifest = store.load_manifest(ref)
    assert any(failure["product_id"] == "test_members" for failure in manifest["failures"])
    assert not [entry for entry in manifest["table_files"] if entry["product_id"] == "test_members"]
    calls = len(provider.calls)
    _dateless_store(tmp_path, provider).prepare(request)
    assert len(provider.calls) == calls + 1


@pytest.mark.parametrize("damage", ["missing_raw", "tampered_raw", "tampered_partition"])
def test_board_query_reuse_requires_intact_raw_and_stock_partitions(tmp_path, damage):
    provider = _BoardBatchProvider()
    store = _dateless_store(tmp_path, provider)
    request = DataRequest(
        product_ids=("test_members",), codes=("801010",),
        start=date(2024, 1, 1), end=date(2024, 12, 31),
        as_of=datetime(2026, 1, 5, 9, 0, tzinfo=CN), purpose="ranking_current",
    )
    ref = store.prepare(request)
    manifest = store.load_manifest(ref)
    snapshot = tmp_path / "store" / "snapshots" / ref.snapshot_id
    raw = snapshot / manifest["items"][0]["raw_files"][0]["path"]
    if damage == "missing_raw":
        raw.unlink()
        calls = len(provider.calls)
        ref2 = _dateless_store(tmp_path, provider).prepare(request)
        assert len(provider.calls) == calls + 1
        assert {e["partition"] for e in store.load_manifest(ref2)["table_files"]} == {
            "code=AAA.SZ", "code=BBB.SZ",
        }
    else:
        target = raw if damage == "tampered_raw" else snapshot / manifest["table_files"][0]["path"]
        target.write_bytes(target.read_bytes() + b"tampered")
        with pytest.raises(SnapshotTamperedError, match="locked asset"):
            _dateless_store(tmp_path, provider).prepare(request)


def test_board_query_legacy_coverage_survives_copy_with_another_product(tmp_path):
    provider = _BoardBatchProvider()
    store = _dateless_store(tmp_path, provider)
    request = DataRequest(
        product_ids=("test_members",), codes=("801010",),
        start=date(2024, 1, 1), end=date(2024, 12, 31),
        as_of=datetime(2026, 1, 5, 9, 0, tzinfo=CN), purpose="ranking_current",
    )
    ref = store.prepare(request)
    _strip_coverage_metadata(tmp_path / "store", ref)
    # 新产品只隔离网络；成员仍消费原批次证据并走真实复制发布路径。
    member_fetch = provider.fetch
    actions = FakeDatelessProvider()

    def fetch(product_id, codes, start, end):
        if product_id == "test_actions":
            return actions.fetch(product_id, codes, start, end)
        return member_fetch(product_id, codes, start, end)

    provider.fetch = fetch
    mixed = DataRequest(
        product_ids=("test_members", "test_actions"), codes=("801010",),
        start=request.start, end=request.end, as_of=request.as_of, purpose=request.purpose,
    )
    calls = len(provider.calls)
    ref2 = store.prepare(mixed)
    assert len(provider.calls) == calls
    entries = [e for e in store.load_manifest(ref2)["table_files"] if e["product_id"] == "test_members"]
    assert {e["partition"] for e in entries} == {"code=AAA.SZ", "code=BBB.SZ"}
    assert all(e.get("coverage") == ["2024-01-01", "2024-12-31"] for e in entries)
    _dateless_store(tmp_path, provider).prepare(mixed)
    assert len(provider.calls) == calls
    assert len(actions.calls) == 1


def test_repeated_evidence_imports_keep_inherited_partitions_readable(tmp_path):
    """合成连续证据补录不能因把所有祖先ID叠进文件名而达到文件系统长度上限。"""
    provider = _ProvenActions()
    store = _actions_only_store(tmp_path, provider)
    base = store.prepare(_actions_request(date(2024, 1, 1), date(2024, 12, 31)))
    original_entry = store.load_manifest(base)['table_files'][0]
    parent_file = store.root / 'snapshots' / base.snapshot_id / original_entry['path']
    parent_bytes = parent_file.read_bytes()
    current = base
    for step in range(10):
        manifest = tmp_path / f'chain-{step}.json'
        manifest.write_text(json.dumps({'mode': 'free', 'items': [],
                                       'base_snapshot_ids': [current.snapshot_id]}))
        current = store.import_files(manifest, ())
    assert parent_file.read_bytes() == parent_bytes
    assert store.read_table(current, 'corporate_actions').equals(store.read_table(base, 'corporate_actions'))
    store._verify_hashes(current, store.load_manifest(current))
    assert store.load_manifest(current)['table_files'][0]['coverage'] == original_entry['coverage']
