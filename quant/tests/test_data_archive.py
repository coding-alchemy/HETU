"""合成事实经真实导入核对档案；断言留测试侧，禁止用行情反推目标。"""

import io
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from backtest_cases import build_case_store, make_backtest_case
from hetu_quant.data_archive import archive_requirements, audit_archive, plan_coverage_gaps
from hetu_quant.models import DataRequest, data_request_from_dict, data_request_to_dict


def test_archive_query_and_export_are_offline(tmp_path, monkeypatch):
    from hetu_quant.data_archive import open_archive_view, query_archive

    store, ref = build_case_store(tmp_path, make_backtest_case("coverage"))
    before = list(store.invocations)

    def forbidden(*args, **kwargs):
        pytest.fail("local query must not acquire data or read the full table")

    monkeypatch.setattr(store, "prepare", forbidden)
    monkeypatch.setattr(store, "read_table", forbidden)
    query = dict(codes=("SYN_EXIT.SZ",), start=date(2023, 12, 1), end=date(2024, 1, 4))
    result = query_archive(store, ref, **query)
    bars = result["bars"]
    assert bars.code.tolist() == ["SYN_EXIT.SZ"]
    assert bars.date.astype(str).tolist() == ["2023-12-29"]
    assert bars[["open_raw", "high_raw", "low_raw", "close_raw", "volume_shares", "amount_cny"]].iloc[0].tolist() == [
        10, 11, 9, 10, 1000, 10000
    ]
    assert bars.source_ref.notna().all()
    exported = pd.read_csv(io.StringIO(bars.to_csv(index=False)))
    pd.testing.assert_frame_equal(exported, bars, check_dtype=False)
    with open_archive_view(store, ref) as con:
        sql = con.execute(
            'SELECT ' + ','.join(bars.columns) + ' FROM daily_bars '
            'WHERE code = ? AND date BETWEEN ? AND ? ORDER BY code, date',
            ["SYN_EXIT.SZ", "2023-12-01", "2024-01-04"],
        ).df()
        pd.testing.assert_frame_equal(sql, bars)
        with pytest.raises(Exception, match="view|table"):
            con.execute("DELETE FROM daily_bars")
    empty = query_archive(store, ref, codes=("SYN_NEW.BJ",), start=date(2023, 1, 4), end=date(2023, 12, 29))
    assert empty["bars"].empty
    assert "not_listed" in set(empty["gaps"].reason)
    assert store.invocations == before


def test_range_filters_registered_files_columns_and_bound_codes(tmp_path):
    from hetu_quant.data import DataQualityError, SnapshotTamperedError

    store, ref = build_case_store(tmp_path, make_backtest_case("coverage"))
    query = dict(codes=("SYN_EXIT.SZ",), start=date(2023, 12, 1), end=date(2023, 12, 31),
                 columns=("code", "date", "close_raw"))
    manifest = store.load_manifest(ref)
    snapshot = store.root / "snapshots" / ref.snapshot_id
    # 非登记文件不被glob带入；不在查询范围的其他分区也不读取。
    (snapshot / "tables/daily_bars/unregistered.parquet").write_bytes(b"invalid")
    outside = next(e for e in manifest["table_files"] if e["table"] == "security_status")
    (snapshot / outside["path"]).write_bytes(b"unrelated damaged asset")
    frame = store.read_range(ref, "daily_bars", **query)
    assert frame.to_dict("records") == [{"code": "SYN_EXIT.SZ", "date": "2023-12-29", "close_raw": 10.0}]
    assert store.read_range(ref, "daily_bars", **{**query, "codes": ("' OR 1=1 --",)}).empty
    with pytest.raises((ValueError, DataQualityError), match="column"):
        store.read_range(ref, "daily_bars", **{**query, "columns": ("close_raw; DROP TABLE x",)})
    own = next(e for e in manifest["table_files"] if e["table"] == "daily_bars")
    (snapshot / own["path"]).unlink()
    with pytest.raises(SnapshotTamperedError, match="locked asset mismatch"):
        store.read_range(ref, "daily_bars", **query)


def test_archive_restore_preserves_readable_snapshot(tmp_path):
    from hetu_quant.data import DataStore, SnapshotTamperedError
    from hetu_quant.data_archive import backup_archive, query_archive, verify_archive_backup

    store, ref = build_case_store(tmp_path / "source", make_backtest_case("coverage"))
    query = dict(codes=("SYN_EXIT.SZ",), start=date(2023, 12, 1), end=date(2024, 1, 4))
    expected = query_archive(store, ref, **query)["bars"]
    destination = tmp_path / "backup"
    restored_ref = backup_archive(store, ref, destination)
    assert restored_ref.snapshot_id == ref.snapshot_id
    verified = verify_archive_backup(destination)
    restored = DataStore(destination)
    pd.testing.assert_frame_equal(query_archive(restored, verified, **query)["bars"], expected)
    with pytest.raises(FileExistsError):
        backup_archive(store, ref, destination)
    raw = store.load_manifest(ref)["items"][0]["raw_files"][0]["path"]
    (destination / "snapshots" / ref.snapshot_id / raw).unlink()
    with pytest.raises(SnapshotTamperedError, match="raw"):
        verify_archive_backup(destination)


def test_range_and_sql_keep_paid_scope_and_permission_checks(tmp_path):
    import json

    from hetu_quant.data import AccessDenied
    from hetu_quant.data_archive import open_archive_view
    from test_data_sources import grant, request, store

    ds, adapter = store(tmp_path)
    ref = ds.prepare(request())
    before = len(adapter.client.calls)
    query = dict(codes=("SYNTH.SH",), start=date(2026, 1, 5), end=date(2026, 1, 6), columns=("close_raw",))
    assert not ds.read_range(ref, "daily_bars", **query).empty
    (ds.root / "data-entitlements.json").write_text(json.dumps({"grants": [grant(codes=["OTHER.SH"])]}))
    with pytest.raises(AccessDenied):
        ds.read_range(ref, "daily_bars", **query)
    with pytest.raises(AccessDenied):
        open_archive_view(ds, ref)
    (ds.root / "data-entitlements.json").write_text(json.dumps({"grants": [grant(allowed_operations=["acquire"])]}))
    with pytest.raises(AccessDenied):
        ds.read_range(ref, "daily_bars", **query)
    assert len(adapter.client.calls) == before


@pytest.mark.parametrize("layout", ["year", "month"])
def test_archive_production_layout_range_and_evidence_restore(tmp_path, layout):
    from hetu_quant.data import DataStore, SnapshotTamperedError
    from hetu_quant.data_archive import ARCHIVE_COLUMNS, backup_archive, open_archive_view, verify_archive_backup
    from test_data_sources import request, store

    ds, adapter = store(tmp_path)
    ds.config["products"]["ts_a_daily"]["storage_layout"] = layout
    ref = ds.prepare(request())
    params = dict(codes=("SYNTH.SH",), start=date(2025, 1, 1), end=date(2025, 1, 2), columns=ARCHIVE_COLUMNS)
    empty = ds.read_range(ref, "daily_bars", **params)
    with open_archive_view(ds, ref) as con:
        sql = con.execute("SELECT " + ",".join(ARCHIVE_COLUMNS) + " FROM daily_bars WHERE date < '2026-01-01'").df()
    pd.testing.assert_frame_equal(empty, sql)
    before = len(adapter.client.calls)
    restored_ref = backup_archive(ds, ref, tmp_path / "restored")
    restored = DataStore(restored_ref.root)
    params.update(start=date(2026, 1, 5), end=date(2026, 1, 6))
    frame = restored.read_range(restored_ref, "daily_bars", **params)
    assert frame.volume_shares.tolist() == [1234.0]
    assert frame.amount_cny.tolist() == [56780.0]
    evidence_file = next((restored.root / "evidence").iterdir())
    evidence_file.unlink()
    with pytest.raises(SnapshotTamperedError, match="evidence"):
        verify_archive_backup(restored.root)
    assert len(adapter.client.calls) == before


@pytest.mark.parametrize("calendar_value,reason", [
    ("unknown", "calendar_unknown"), ("False", None), ("True", "missing_bar"),
])
def test_query_unknown_calendar_does_not_hide_missing_bar(tmp_path, calendar_value, reason):
    from hetu_quant.data_archive import query_archive

    case = make_backtest_case("coverage")
    bars = pd.read_csv(io.StringIO(case["csvs"]["daily_bars"]))
    case["csvs"]["daily_bars"] = bars[
        ~(bars.code.eq("SYN_A.SH") & bars.date.eq("2024-01-04"))
    ].to_csv(index=False)
    calendar = pd.read_csv(io.StringIO(case["csvs"]["calendar"]), dtype=str)
    calendar.loc[calendar.date.eq("2024-01-04"), "is_trading_day"] = calendar_value
    case["csvs"]["calendar"] = calendar.to_csv(index=False)
    store, ref = build_case_store(tmp_path, case)
    result = query_archive(store, ref, codes=("SYN_A.SH",), start=date(2024, 1, 4), end=date(2024, 1, 4))
    assert result["bars"].empty
    assert result["gaps"].reason.tolist() == ([reason] if reason else [])


def test_archive_restore_includes_standalone_disposal_evidence(tmp_path):
    import hashlib

    from hetu_quant.data import SnapshotTamperedError
    from hetu_quant.data_archive import backup_archive, verify_archive_backup

    case = make_backtest_case("coverage")
    original = b"synthetic published terminal cash compensation"
    name = "synthetic-disposal.txt"
    case["table_products"]["terminal_disposals"] = ("synthetic_disposals", "event")
    case["csvs"]["terminal_disposals"] = pd.DataFrame([dict(
        code="SYN_EXIT.SZ", date="2023-12-29", cash_per_share="1.25",
        available_at="2023-12-28T18:00:00+08:00", evidence_ref=name,
        sha256=hashlib.sha256(original).hexdigest(),
    )]).to_csv(index=False)
    store, ref = build_case_store(tmp_path / "source", case)
    source = store.root / "evidence" / name
    source.write_bytes(original)
    restored = backup_archive(store, ref, tmp_path / "restored")
    copied = restored.root / "evidence" / name
    assert copied.is_file(), "独立退出处置原件必须随样本备份"
    assert copied.read_bytes() == original
    copied.unlink()
    with pytest.raises(SnapshotTamperedError, match=name):
        verify_archive_backup(restored.root)
    source.unlink()
    with pytest.raises(SnapshotTamperedError, match=name):
        backup_archive(store, ref, tmp_path / "missing-original")


def targets(case):
    frame = pd.DataFrame(case["targets"])
    frame.attrs["target_scope"] = {"complete": True, "source_ref": "synthetic-independent-roster"}
    return frame


def requirements(case):
    return archive_requirements(
        targets(case), pd.read_csv(io.StringIO(case["csvs"]["calendar"])), date(2024, 1, 4), years=1
    )


def test_archive_purpose_is_independent_of_financials_and_research_warmup(tmp_path):
    case = make_backtest_case("coverage")
    store, ref = build_case_store(tmp_path, case)
    report = audit_archive(store, ref, requirements(case))
    assert report["status"] == "complete"
    assert report["core_complete"]
    req = DataRequest(
        tuple(x[0] for x in case["table_products"].values()),
        case["codes"],
        date(2023, 1, 4),
        date(2024, 1, 4),
        datetime(2024, 1, 5, 21, tzinfo=ZoneInfo("Asia/Shanghai")),
        "daily_archive",
    )
    assert data_request_from_dict(data_request_to_dict(req)).purpose == "daily_archive"
    assert store.qualify(req, ref).can_run


@pytest.mark.parametrize("removed", ["year", "market", "delisted"])
def test_archive_missing_year_market_or_exit_object_cannot_be_complete(tmp_path, removed):
    case = make_backtest_case("coverage")
    bars = pd.read_csv(io.StringIO(case["csvs"]["daily_bars"]))
    keep = (
        ~bars.date.str.startswith("2023")
        if removed == "year"
        else (~bars.code.eq("SYN_NEW.BJ") if removed == "market" else ~bars.code.eq("SYN_EXIT.SZ"))
    )
    case["csvs"]["daily_bars"] = bars[keep].to_csv(index=False)
    store, ref = build_case_store(tmp_path, case)
    report = audit_archive(store, ref, requirements(case))
    assert report["status"] == "limited"
    assert any(row["domain"] == "daily_bars" and row["data_gaps"] for row in report["coverage"])


def test_archive_keeps_suspension_not_listed_missing_and_unknown_separate(tmp_path):
    case = make_backtest_case("coverage")
    bars = pd.read_csv(io.StringIO(case["csvs"]["daily_bars"]))
    case["csvs"]["daily_bars"] = bars[
        ~(bars.code.eq("SYN_A.SH") & bars.date.isin(["2024-01-02", "2024-01-04"]))
    ].to_csv(index=False)
    state = pd.read_csv(io.StringIO(case["csvs"]["security_status"]))
    state.loc[state.code.eq("SYN_A.SH") & state.date.eq("2024-01-02"), "is_suspended"] = True
    case["csvs"]["security_status"] = state.to_csv(index=False)
    store, ref = build_case_store(tmp_path, case)
    req = requirements(case)
    report = audit_archive(store, ref, req)
    row = next(r for r in report["coverage"] if r["code"] == "SYN_A.SH" and r["domain"] == "daily_bars")
    assert row["suspended"] == [["2024-01-02", "2024-01-02"]]
    assert row["data_gaps"] == [["2024-01-04", "2024-01-04"]]
    assert any(r["code"] == "SYN_NEW.BJ" and r["reason"] == "not_listed" for r in req["exclusions"])
    frame = targets(case)
    frame.loc[frame.code.eq("SYN_A.SH"), "lifecycle_status"] = "conflict"
    unknown = audit_archive(
        store,
        ref,
        archive_requirements(frame, pd.read_csv(io.StringIO(case["csvs"]["calendar"])), date(2024, 1, 4), years=1),
    )
    assert any(r["code"] == "SYN_A.SH" for r in unknown["unknown_ranges"])


def test_archive_keeps_unproved_inheritance_and_separate_same_code_identities(tmp_path):
    case = make_backtest_case("coverage")
    history = targets(case)
    history.loc[0, "lifecycle_status"] = "mapping_unknown"
    history.loc[0, "valid_to"] = "2023-01-04"
    extra = history.iloc[[0]].copy()
    extra["identity_id"] = "different-entity-same-code"
    history = pd.concat([history, extra], ignore_index=True)
    history.attrs["target_scope"] = {"complete": True, "source_ref": "synthetic-independent-roster"}
    req = archive_requirements(history, pd.read_csv(io.StringIO(case["csvs"]["calendar"])), date(2024, 1, 4), years=1)
    own = [r for r in req["objects"] if r["code"] == "SYN_A.SH"]
    assert len(own) == 2
    assert all(r["required"] == ["2023-01-04", "2024-01-04"] for r in own)
    store, ref = build_case_store(tmp_path, case)
    assert audit_archive(store, ref, req)["status"] == "limited"


def test_complete_coverage_has_zero_shared_proposed_requests(tmp_path, monkeypatch):
    case = make_backtest_case("coverage")
    store, ref = build_case_store(tmp_path, case)
    monkeypatch.setattr(store, "prepare", lambda *_: pytest.fail("offline audit must not acquire"))
    report = audit_archive(store, ref, requirements(case))
    proposed = plan_coverage_gaps(
        [report, report],
        store,
        as_of=datetime(2024, 1, 5, 21, tzinfo=ZoneInfo("Asia/Shanghai")),
        contracts={"products": {}},
        domain_products={"daily_bars": "synthetic_daily_bars"},
    )
    assert proposed["counts"]["provider_fetch"] == 0
    assert proposed["requests"] == []


def test_unknown_status_field_does_not_become_known_normal_state(tmp_path):
    from hetu_quant.data_archive import scan_local_coverage

    case = make_backtest_case("coverage")
    states = pd.read_csv(io.StringIO(case["csvs"]["security_status"]), dtype=str)
    states.loc[states.code.eq("SYN_A.SH") & states.date.eq("2024-01-02"), "is_st"] = ""
    case["csvs"]["security_status"] = states.to_csv(index=False)
    store, ref = build_case_store(tmp_path, case)
    facts = scan_local_coverage(store, ref)
    # ST未知仍须报告，不能仅因is_suspended有值就称完整状态已覆盖。
    rows = facts.masks["SYN_A.SH"]
    assert rows["status"].bit_count() == 3


def test_archive_qualification_preserves_free_paid_boundary(tmp_path):
    from dataclasses import replace

    from hetu_quant.data import AccessDenied
    from test_data_sources import request, store

    ds, _ = store(tmp_path)
    paid = request()
    ref = ds.prepare(paid)
    with pytest.raises(AccessDenied):
        ds.qualify(replace(paid, purpose="daily_archive", mode="free"), ref)


def test_conflicting_prices_are_unknown_not_an_automatic_new_fetch_gap(tmp_path):
    from hetu_quant.data_archive import scan_local_coverage

    case = make_backtest_case("coverage")
    store, ref = build_case_store(tmp_path / "one", case)
    changed = {**case, "csvs": dict(case["csvs"])}
    frame = pd.read_csv(io.StringIO(case["csvs"]["daily_bars"]))
    frame = frame[frame.code.eq("SYN_A.SH") & frame.date.eq("2024-01-04")].copy()
    frame["close_raw"] = 10.5
    changed["csvs"]["daily_bars"] = frame.to_csv(index=False)
    other, other_ref = build_case_store(tmp_path / "two", changed, tables=("daily_bars",))
    facts = scan_local_coverage(
        store,
        ref,
        asset_snapshots=[
            dict(
                root=str(other.root),
                config_path=str(tmp_path / "two/products.json"),
                snapshot_id=other_ref.snapshot_id,
                tables=["daily_bars"],
            )
        ],
    )
    report = audit_archive(store, ref, requirements(case), inventory=facts)
    own = next(r for r in report["coverage"] if r["code"] == "SYN_A.SH" and r["domain"] == "daily_bars")
    assert own["unknown_ranges"] == [["2024-01-04", "2024-01-04"]]
    assert own["data_gaps"] == []
    assert report["status"] == "limited"


def test_audited_missing_rows_can_be_proposed_without_reading_unlicensed_new_objects(tmp_path):
    from dataclasses import replace

    from hetu_quant.data_updates import execute_update, plan_update
    from test_data_sources import request, store

    ds, adapter = store(tmp_path, allowed_operations=["read"])
    req = replace(request(end="2026-01-05"), codes=("OTHER.SH",), purpose="daily_archive")
    missing = [dict(product_id="ts_a_daily", codes=["OTHER.SH"], start="2026-01-05", end="2026-01-05", refresh=False)]
    proposal = plan_update(
        req, ds, {"products": {"ts_a_daily": {"max_days": 365, "sdk_calls": 0}}}, audited_segments=missing
    )
    assert proposal["counts"]["provider_fetch"] == proposal["counts"]["upstream_product_requests"] == 1
    assert proposal["segments"][0]["upstream"][0]["params"] == {
        "ts_code": "OTHER.SH",
        "start_date": "20260105",
        "end_date": "20260105",
    }
    with pytest.raises(ValueError, match="scope approval"):
        execute_update(proposal, ds, tmp_path / "ledger.jsonl", cancelled=lambda: False, progress=lambda _: None)
    assert adapter.client.calls == []


@pytest.mark.parametrize("conflicting", [False, True])
def test_conflicting_suspension_cannot_hide_missing_bar(tmp_path, conflicting):
    from hetu_quant.data_archive import scan_local_coverage

    case = make_backtest_case("coverage")
    bars = pd.read_csv(io.StringIO(case["csvs"]["daily_bars"]))
    case["csvs"]["daily_bars"] = bars[~(bars.code.eq("SYN_A.SH") & bars.date.eq("2024-01-02"))].to_csv(index=False)
    store, ref = build_case_store(tmp_path / "one", case)
    extra = {**case, "csvs": dict(case["csvs"])}
    state = pd.read_csv(io.StringIO(case["csvs"]["security_status"]))
    state = state[state.code.eq("SYN_A.SH") & state.date.eq("2024-01-02")].copy()
    state["is_suspended"] = conflicting
    extra["csvs"]["security_status"] = state.to_csv(index=False)
    other, other_ref = build_case_store(tmp_path / "two", extra, tables=("security_status",))
    facts = scan_local_coverage(
        store,
        ref,
        asset_snapshots=[
            dict(
                root=str(other.root),
                config_path=str(tmp_path / "two/products.json"),
                snapshot_id=other_ref.snapshot_id,
                tables=["security_status"],
            )
        ],
    )
    report = audit_archive(store, ref, requirements(case), inventory=facts)
    row = next(r for r in report["coverage"] if r["code"] == "SYN_A.SH" and r["domain"] == "daily_bars")
    assert report["status"] == "limited"
    assert row["suspended"] == []
    if conflicting:
        assert row["unknown_ranges"] == [["2024-01-02", "2024-01-02"]]
        assert row["data_gaps"] == []
    else:
        assert row["unknown_ranges"] == []
        assert row["data_gaps"] == [["2024-01-02", "2024-01-02"]]


@pytest.mark.parametrize("field,value", [("security_type", "unknown"), ("market", "unknown"), ("market", "")])
def test_unknown_security_attributes_are_pending_not_scope_exclusions(tmp_path, field, value):
    case = make_backtest_case("coverage")
    history = targets(case)
    history.loc[history.code.eq("SYN_A.SH"), field] = value
    securities = pd.read_csv(io.StringIO(case["csvs"]["securities"]), dtype=str).fillna("")
    securities.loc[securities.code.eq("SYN_A.SH"), field] = value
    case["csvs"]["securities"] = securities.to_csv(index=False)
    store, ref = build_case_store(tmp_path, case)
    req = archive_requirements(history, pd.read_csv(io.StringIO(case["csvs"]["calendar"])), date(2024, 1, 4), years=1)
    report = audit_archive(store, ref, req)
    assert report["status"] == "limited"
    assert not next(o for o in req["objects"] if o["code"] == "SYN_A.SH").get("excluded")
    assert any(r["code"] == "SYN_A.SH" and r["reason"] == "security_attributes_unproved" for r in req["unknown_ranges"])
    assert not any(r["code"] == "SYN_A.SH" and r["reason"] == "not_common_stock_or_market" for r in req["exclusions"])

    request = DataRequest(
        tuple(x[0] for x in case["table_products"].values()),
        case["codes"],
        date(2023, 1, 4),
        date(2024, 1, 4),
        datetime(2024, 1, 5, 21, tzinfo=ZoneInfo("Asia/Shanghai")),
        "daily_archive",
    )
    assert not store.qualify(request, ref).can_run


@pytest.mark.parametrize("field,value", [("security_type", "cdr"), ("market", "HK")])
def test_proven_outside_security_attributes_still_allow_legal_exclusion(tmp_path, field, value):
    case = make_backtest_case("coverage")
    history = targets(case)
    history.loc[history.code.eq("SYN_A.SH"), field] = value
    store, ref = build_case_store(tmp_path, case)
    req = archive_requirements(history, pd.read_csv(io.StringIO(case["csvs"]["calendar"])), date(2024, 1, 4), years=1)
    assert audit_archive(store, ref, req)["status"] == "complete"
    assert any(r["code"] == "SYN_A.SH" and r["reason"] == "not_common_stock_or_market" for r in req["exclusions"])


@pytest.mark.parametrize("factor", [1, 2])
def test_conflicting_factors_are_unknown_but_consistent_duplicates_reuse(tmp_path, factor):
    from hetu_quant.data_archive import scan_local_coverage

    case = make_backtest_case("coverage")
    store, ref = build_case_store(tmp_path / "one", case)
    extra = {**case, "csvs": dict(case["csvs"])}
    factors = pd.read_csv(io.StringIO(case["csvs"]["adjustment_factors"]))
    factors = factors[factors.code.eq("SYN_A.SH") & factors.date.eq("2024-01-02")].copy()
    factors["adj_factor"] = factor
    extra["csvs"]["adjustment_factors"] = factors.to_csv(index=False)
    other, other_ref = build_case_store(tmp_path / "two", extra, tables=("adjustment_factors",))
    facts = scan_local_coverage(
        store,
        ref,
        asset_snapshots=[
            dict(
                root=str(other.root),
                config_path=str(tmp_path / "two/products.json"),
                snapshot_id=other_ref.snapshot_id,
                tables=["adjustment_factors"],
            )
        ],
    )
    report = audit_archive(store, ref, requirements(case), inventory=facts)
    row = next(r for r in report["coverage"] if r["code"] == "SYN_A.SH" and r["domain"] == "adjustment_factors")
    if factor == 2:
        assert report["status"] == "limited"
        assert row["unknown_ranges"] == [["2024-01-02", "2024-01-02"]]
        assert row["conflicting_sessions"] == 1
        assert row["observed_sessions"] == 3
        assert row["data_gaps"] == []
    else:
        assert report["status"] == "complete"
        assert row["unknown_ranges"] == []
        assert row["conflicting_sessions"] == 0
        assert row["observed_sessions"] == 4


def test_archive_daily_suspension_evidence_does_not_fill_full_status(tmp_path):
    import json

    from test_data_sources import request
    from test_data_sources import store as source_store

    case = make_backtest_case("coverage")
    bars = pd.read_csv(io.StringIO(case["csvs"]["daily_bars"]))
    gap_days = ["2023-12-29", "2024-01-02", "2024-01-04"]
    case["csvs"]["daily_bars"] = bars[~(bars.code.eq("SYN_A.SH") & bars.date.isin(gap_days))].to_csv(index=False)
    status = pd.read_csv(io.StringIO(case["csvs"]["security_status"]))
    case["csvs"]["security_status"] = status[~(status.code.eq("SYN_A.SH") & status.date.isin(gap_days))].to_csv(
        index=False
    )
    local, ref = build_case_store(tmp_path / "base", case)

    class Events:
        def query(self, api, params, fields):
            day = params["trade_date"]
            facts = {"20231229": [(None, "S")], "20240102": [("09:30-10:30", "S")],
                     "20240104": [(None, "S"), (None, "R")]}
            return json.dumps({"code": 0, "data": {"fields": fields.split(","),
                "items": [["SYN_A.SH", day, timing, event] for timing, event in facts[day]]}}).encode()

    source_root = tmp_path / "source"
    source_root.mkdir()
    pid = "ts_suspend_day"
    source, _ = source_store(source_root, Events(), product=pid, codes=["SYN_A.SH"], acquire_cross_section=True)
    req = requirements(case)
    req["asset_snapshots"] = []
    from dataclasses import replace

    for day in gap_days:
        events = source.prepare(replace(request(pid, start=day, end=day), codes=()))
        req["asset_snapshots"].append(
            {"root": str(events.root), "snapshot_id": events.snapshot_id, "tables": ["security_events"]}
        )
    row = next(r for r in audit_archive(local, ref, req)["coverage"]
               if r["code"] == "SYN_A.SH" and r["domain"] == "daily_bars")
    assert row["suspended"] == [["2023-12-29", "2023-12-29"]]
    assert row["data_gaps"] == [["2024-01-02", "2024-01-02"]]
    assert row["unknown_ranges"] == [["2024-01-04", "2024-01-04"]]
    from hetu_data.archive import _coverage_rows, scan_local_coverage

    inventory = scan_local_coverage(local, ref, asset_snapshots=req["asset_snapshots"])
    full_status = next(r for r in _coverage_rows(req, inventory, {"security_status": "status"})
                       if r["code"] == "SYN_A.SH")
    assert full_status["data_gaps"] == [["2023-12-29", "2024-01-04"]]


@pytest.mark.parametrize("confirmed_suspension", [True, False])
def test_archive_factor_gap_on_missing_bar_respects_proven_no_trade(tmp_path, confirmed_suspension):
    case = make_backtest_case("coverage")
    day = "2024-01-02"
    code = "SYN_A.SH"
    for domain in ("daily_bars", "adjustment_factors"):
        frame = pd.read_csv(io.StringIO(case["csvs"][domain]))
        case["csvs"][domain] = frame[~(frame.code.eq(code) & frame.date.eq(day))].to_csv(index=False)
    status = pd.read_csv(io.StringIO(case["csvs"]["security_status"]))
    status.loc[status.code.eq(code) & status.date.eq(day), "is_suspended"] = confirmed_suspension
    case["csvs"]["security_status"] = status.to_csv(index=False)
    store, ref = build_case_store(tmp_path, case)
    result = audit_archive(store, ref, requirements(case))
    row = next(r for r in result["coverage"] if r["code"] == code and r["domain"] == "adjustment_factors")
    assert row["data_gaps"] == ([] if confirmed_suspension else [[day, day]])
    assert result["status"] == ("complete" if confirmed_suspension else "limited")


@pytest.mark.parametrize("quote", ["placeholder", "partial_trade", "complete_trade"])
def test_official_no_trade_import_explains_only_invalid_bar_after_reopen(tmp_path, quote):
    import json

    from hetu_data.archive import query_archive
    from hetu_data.store import DataStore

    case = make_backtest_case("coverage")
    code, day = "SYN_A.SH", "2024-01-02"
    actual_trade = quote != "placeholder"
    if quote != "complete_trade":
        bars = pd.read_csv(io.StringIO(case["csvs"]["daily_bars"]))
        fields = ["amount_cny"] if actual_trade else ["volume_shares", "amount_cny"]
        bars.loc[bars.code.eq(code) & bars.date.eq(day), fields] = None
        case["csvs"]["daily_bars"] = bars.to_csv(index=False)
    base, parent = build_case_store(tmp_path, case)
    product = "cn_a_archive_no_trade"
    config = tmp_path / "products.json"
    config_doc = json.loads(config.read_text())
    config_doc["products"][product] = {
        "provider": "local_file", "fee_class": "free", "permission": {"status": "verified_free"},
        "tables": ["security_events"], "kind": "event",
    }
    config.write_text(json.dumps(config_doc))
    store = DataStore(base.root, config_path=config, providers={})
    event = tmp_path / "event.csv"
    event.write_text(f"code,date,event_type\n{code},{day},confirmed_no_trade\n")
    doc = tmp_path / "official.json"
    doc.write_text(json.dumps({"base_snapshot_ids": [parent.snapshot_id], "items": [{
        "product_id": product, "table": "security_events", "files": [event.name],
        "source": {"name": "synthetic_exchange", "fee_class": "free", "permission_basis": "synthetic official fact",
                   "obtained_at": "2024-01-02T09:00:00+08:00"},
    }]}))
    asset = store.import_files(doc, (event,))
    reopened = DataStore(base.root, config_path=config, providers={})
    query = query_archive(reopened, asset, codes=(code,), start=date.fromisoformat(day), end=date.fromisoformat(day))
    assert len(query["bars"]) == 1
    reason = "conflicting_trade_evidence" if actual_trade else "confirmed_no_trade"
    assert reason in query["gaps"].reason.tolist()
    report = audit_archive(reopened, asset, requirements(case))
    row = next(r for r in report["coverage"] if r["code"] == code and r["domain"] == "daily_bars")
    assert row["no_trade"] == ([] if actual_trade else [[day, day]])
    assert row["conflicting_sessions"] == int(actual_trade)
    assert row["invalid_sessions"] == int(quote != "complete_trade")
    assert row["data_gaps"] == []
    assert reopened.read_table(asset, "security_status").equals(reopened.read_table(parent, "security_status"))


@pytest.mark.parametrize("amount_delta", [0, 1])
def test_archive_query_matches_audit_for_exact_values_from_two_sources(tmp_path, amount_delta):
    import json

    from hetu_data.archive import query_archive
    from hetu_data.store import DataStore

    case = make_backtest_case("coverage")
    code, day = "SYN_A.SH", "2024-01-02"
    bars = pd.read_csv(io.StringIO(case["csvs"]["daily_bars"]))
    other = bars[bars.code.eq(code) & bars.date.eq(day)].copy()
    other["amount_cny"] += amount_delta
    base, parent = build_case_store(tmp_path, case)
    config = tmp_path / "products.json"
    conf = json.loads(config.read_text())
    pid = case["table_products"]["daily_bars"][0]
    conf["products"]["second_source"] = conf["products"][pid]
    config.write_text(json.dumps(conf))
    store = DataStore(base.root, config_path=config, providers={})
    items, files = [], []
    for source, frame in ((pid, bars), ("second_source", other)):
        file = tmp_path / f"{source}.csv"
        frame.to_csv(file, index=False)
        files.append(file)
        items.append({"product_id": source, "table": "daily_bars", "files": [file.name], "source": {
            "name": source, "fee_class": "free", "permission_basis": "synthetic source",
            "obtained_at": "2024-01-02T09:00:00+08:00",
        }})
    doc = tmp_path / "overlap.json"
    doc.write_text(json.dumps({"base_snapshot_ids": [parent.snapshot_id], "items": items}))
    ref = store.import_files(doc, tuple(files))
    query = query_archive(store, ref, codes=(code,), start=date.fromisoformat(day), end=date.fromisoformat(day))
    assert len(query["bars"]) == 2
    assert ("conflicting_bars" in query["gaps"].reason.tolist()) is bool(amount_delta)
    report = audit_archive(store, ref, requirements(case))
    row = next(r for r in report["coverage"] if r["code"] == code and r["domain"] == "daily_bars")
    assert row["conflicting_sessions"] == int(bool(amount_delta))
