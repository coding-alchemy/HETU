"""Exercise after-close publications with synthetic local sources."""

import json
from datetime import date

import pytest
from after_close_cases import JAN, make_after_close_source
from hetu_data.library import publish_library
from hetu_data.store import DataStore as KernelStore
from hetu_quant.after_close import generate_after_close, read_after_close
from hetu_quant.data import AccessDenied, DataStore


def _asset(store, ref, *tables):
    return dict(root=str(store.root), snapshot_id=ref.snapshot_id, tables=list(tables))


def test_library_observation_keeps_granted_bars(tmp_path):
    store, granted = make_after_close_source(tmp_path / "granted")
    other, denied = make_after_close_source(
        tmp_path / "denied", daily_allowed_uses=["ranking_historical"], change_close=99,
    )
    allowed = _asset(store, granted, "daily_bars", "calendar", "knowledge_evidence")
    ranking_only = _asset(other, denied, "daily_bars", "knowledge_evidence")
    baseline = publish_library(store, asset_snapshots=[allowed])
    mixed = publish_library(store, asset_snapshots=[ranking_only, allowed])

    first = read_after_close(generate_after_close(store, baseline, trade_date=JAN, output_root=tmp_path / "out"))
    result = read_after_close(generate_after_close(store, mixed, trade_date=JAN, output_root=tmp_path / "out"))

    assert result["actual_cutoff"] == "2024-01-31"
    assert result["coverage"]["count"] == 20
    assert result["observations"] == first["observations"]
    assert next(row for row in result["observations"] if row["code"] == "VOL_01")["close_raw"] == 10
    assert any(gap.get("snapshot_id") == denied.snapshot_id and gap["field"] == "daily_bars"
               and "用途未许可" in gap["reason"] for gap in result["observation_gaps"])


def test_historical_turnover_conflict_blocks_only_turnover_median(tmp_path):
    conflict_date = "2024-01-19"
    store, original = make_after_close_source(tmp_path / "original")
    other, alternate = make_after_close_source(tmp_path / "alternate", amount_change=(conflict_date, 60_000_000))
    source = publish_library(store, asset_snapshots=[
        _asset(store, original, "daily_bars", "calendar"), _asset(other, alternate, "daily_bars"),
    ])

    result = read_after_close(generate_after_close(store, source, trade_date=JAN, output_root=tmp_path / "out"))

    observed = {row["code"]: row for row in result["observations"]}
    assert "amount_median_20d" not in observed["VOL_01"]
    assert observed["VOL_01"]["amount_cny"] == 30_000_000
    assert observed["VOL_01"]["raw_change"] == observed["VOL_01"]["raw_volatility_60d"] == 0.0
    assert observed["VOL_02"]["amount_median_20d"] == 30_000_000
    conflict = next(gap for gap in result["observation_gaps"] if gap.get("code") == "VOL_01"
                    and gap.get("date") == conflict_date and gap.get("field") == "amount_cny")
    assert "冲突" in conflict["reason"]
    assert {item["snapshot_id"] for item in conflict["sources"]} == {original.snapshot_id, alternate.snapshot_id}


@pytest.mark.parametrize("allowed_uses", [["ranking_historical"], None], ids=["ranking-only", "purpose-unproved"])
def test_observation_auxiliary_purpose_does_not_borrow_ranking_permission(tmp_path, monkeypatch, allowed_uses):
    store, daily = make_after_close_source(tmp_path / "daily")
    ancillary, restricted = make_after_close_source(
        tmp_path / "ranking-only",
        auxiliary_uses={"calendar": allowed_uses, "security_status": allowed_uses},
        suspended_code="VOL_20",
    )
    bars = _asset(store, daily, "daily_bars", "knowledge_evidence")
    unlicensed = _asset(ancillary, restricted, "calendar", "security_status")
    allowed = _asset(store, daily, "calendar", "security_status")
    output = tmp_path / "results"
    range_files = KernelStore._range_files

    def assert_readable(self, ref, name, **request):
        assert not (self.root == ancillary.root and name in {"calendar", "security_status"}), (ref, name)
        return range_files(self, ref, name, **request)

    monkeypatch.setattr(KernelStore, "_range_files", assert_readable)

    def publish(*assets):
        group = publish_library(store, asset_snapshots=assets)
        return read_after_close(generate_after_close(store, group, trade_date=JAN, output_root=output))

    free = publish(bars)
    denied = publish(bars, unlicensed)
    restored = publish(bars, unlicensed, allowed)
    assert {result["coverage"]["count"] for result in (free, denied, restored)} == {20}
    assert denied["observations"] == free["observations"]
    assert "VOL_20" not in denied["coverage"]["suspended"]
    assert {(x["field"], x["purpose"]) for x in denied["observation_gaps"]
            if x.get("snapshot_id") == restricted.snapshot_id and x.get("source_root") == str(ancillary.root)} == {
        ("calendar", "daily_archive"), ("security_status", "daily_archive"),
    }
    for field in ("raw_change", "amount_median_20d", "raw_volatility_60d"):
        assert field not in denied["observations"][0]
    assert restored["observations"][0]["close_raw"] == 10
    assert restored["observations"][0]["amount_median_20d"] == 30_000_000
    assert restored["observations"][0]["raw_change"] == 0
    print("calendar-only denial: coverage=20, metrics absent, suspension excluded; allowed: 30000000, 0%")


def test_reopened_close_annotation_requires_the_evidence_products_observation_permission(tmp_path, monkeypatch):
    uses = ["ranking_historical"]
    store, restricted = make_after_close_source(
        tmp_path / "restricted", correction_only=True, rankings=True,
        auxiliary_uses={"knowledge_evidence": uses},
    )
    owner, admitted = make_after_close_source(
        tmp_path / "admitted", correction_only=True, rankings=True,
        auxiliary_uses={"knowledge_evidence": [*uses, "daily_archive"]},
    )
    reads = []
    read_table, range_files = KernelStore.read_table, KernelStore._range_files

    def track_table(self, ref, name):
        if name == "knowledge_evidence":
            reads.append((self.root, ref.snapshot_id, None))
        return read_table(self, ref, name)

    def track_range(self, ref, name, **request):
        if name == "knowledge_evidence":
            reads.append((self.root, ref.snapshot_id, request["product_id"]))
        return range_files(self, ref, name, **request)

    monkeypatch.setattr(KernelStore, "read_table", track_table)
    monkeypatch.setattr(KernelStore, "_range_files", track_range)
    for asset, name in ((
        _asset(store, restricted, "daily_bars", "calendar"), "bare",
    ), (
        _asset(store, restricted, "daily_bars", "calendar", "security_status", "corporate_actions", "boards",
               "memberships", "index_bars", "knowledge_evidence"), "ranking-only",
    ), (
        _asset(owner, admitted, "daily_bars", "calendar", "security_status", "corporate_actions", "boards",
               "memberships", "index_bars", "knowledge_evidence"), "daily_archive",
    )):
        reads.clear()
        source = publish_library(store, asset_snapshots=[asset])
        saved = generate_after_close(store, source, trade_date=JAN, output_root=tmp_path / "publications")
        result = read_after_close(saved)
        row = next(row for row in result["observations"] if row["code"] == "VOL_01")
        print(f"{name}: {saved.result_id}, close={row['close_raw']}, "
              f"published={row.get('revised_published_at')}, version_note={row.get('version_note')}, reads={reads}")
        assert row["close_raw"] == 12
        if name == "daily_archive":
            assert row["revised_published_at"] == "2024-02-02T12:00:00+08:00"
            assert reads == [(owner.root, admitted.snapshot_id, "test_bt_knowledge")]
        else:
            assert row.get("revised_published_at") is None
            assert row["version_note"] == "原价公开时点未知"
            assert not reads
        if name == "ranking-only":
            denied = next(gap for gap in result["observation_gaps"] if gap.get("field") == "knowledge_evidence")
            assert (denied["purpose"], denied["product_id"], denied["snapshot_id"], denied["source_root"]) == (
                "daily_archive", "test_bt_knowledge", restricted.snapshot_id, str(store.root),
            )
            assert "用途未许可" in denied["reason"]
            print(f"permission gap: {denied}")
        if name != "bare":
            assert result["rankings"]["purpose"] == "ranking_historical"
            assert {item["board_kind"] for item in result["rankings"]["boards"]} == {"industry", "theme"}


def _paid_daily(root):
    root.mkdir()
    product = "licensed_daily"
    (root / "data-products.json").write_text(json.dumps({"schema_version": 1, "products": {product: dict(
        fee_class="paid", kind="daily", provider="local_file", requires_codes=True, tables=["daily_bars"],
        provider_timezone="Asia/Shanghai", permission={"status": "verified_free"}, notes="合成日线",
    )}}))
    rights = dict(product_id=product, codes=["A.SH"], start="2024-03-29", end="2024-04-01",
                  markets=["SH"], allowed_operations=["read", "import"], allowed_uses=["daily_archive"],
                  acquire_until="2099-12-31", retained_use=True, approved_scope_ref="synthetic",
                  license_ref="synthetic")
    grant = root / "data-entitlements.json"
    grant.write_text(json.dumps({"grants": [rights]}))
    manifest = root / "original.json"
    manifest.write_text(json.dumps({"mode": "paid", "items": [dict(
        product_id=product, table="daily_bars", files=["bars.csv"], source=dict(
            name="模拟窄范围许可", fee_class="paid", permission_basis="合成测试材料",
            obtained_at="2024-04-01T18:00:00+08:00",
        ),
    )]}))
    path = root / "bars.csv"
    path.write_text("code,date,open_raw,high_raw,low_raw,close_raw,volume_shares,amount_cny\n"
                    "A.SH,2024-03-29,8,8,8,8,100,800\nA.SH,2024-04-01,9,9,9,9,100,900\n")
    owner = DataStore(root)
    ref = owner.import_files(manifest, (path,))
    grant.write_text(json.dumps({"grants": [dict(rights, start="2024-04-01")]}))
    return owner, ref


def test_daily_archive_keeps_free_history_with_single_day_paid_grant(tmp_path, monkeypatch):
    today = date(2024, 4, 1)
    store, free = make_after_close_source(tmp_path / "free", bars_through=today, calendar_through=today)
    owner, paid = _paid_daily(tmp_path / "paid")
    with pytest.raises(AccessDenied, match="period grant"):
        owner.read_range(paid, "daily_bars", codes=("A.SH",), start=date(2024, 3, 29), end=today,
                         columns=("code", "close_raw"))
    assert owner.read_range(
        paid, "daily_bars", codes=("A.SH",), start=today, end=today, columns=("close_raw",),
    ).close_raw.tolist() == [9]

    underlying = KernelStore._range_files
    reads = []

    def observed(self, ref, name, **request):
        if self.root == owner.root and name == "daily_bars":
            reads.append(request)
        return underlying(self, ref, name, **request)

    monkeypatch.setattr(KernelStore, "_range_files", observed)
    group = publish_library(store, asset_snapshots=[
        _asset(store, free, "daily_bars", "calendar"), _asset(owner, paid, "daily_bars"),
    ])
    saved = generate_after_close(store, group, trade_date=today, output_root=tmp_path / "results")
    result = read_after_close(saved)
    assert reads and all(r["start"] == r["end"] == today and tuple(r["codes"]) == ("A.SH",) for r in reads)
    assert result["actual_cutoff"] == str(today)
    assert result["coverage"]["count"] == 21
    rows = {row["code"]: row for row in result["observations"]}
    assert rows["VOL_01"]["amount_median_20d"] == 30_000_000
    assert rows["A.SH"]["close_raw"] == 9
    assert "amount_median_20d" not in rows["A.SH"]
    gap = next(gap for gap in result["observation_gaps"]
               if gap.get("code") == "A.SH" and gap.get("field") == "amount_median_20d")
    assert (gap["start"], gap["end"], gap["input_field"], len(gap["missing_dates"])) == (
        "2024-03-05", "2024-03-29", "amount_cny", 19,
    )
    print(f"mixed source: publication={saved.result_id}, cutoff={today}, coverage=21, "
          "A.SH close=9, 19 missing amount days 2024-03-05 through 2024-03-29")


def test_today_turnover_conflict_reopens_both_originals_in_either_order(tmp_path, monkeypatch):
    from hetu_quant.models import ResultRef
    from streamlit.testing.v1 import AppTest
    from test_web_workflow import APP_PATH, _after_close_app

    store, left = make_after_close_source(tmp_path / "left")
    other, right = make_after_close_source(tmp_path / "right", amount_change=(str(JAN), 60_000_000))
    originals = [_asset(store, left, "daily_bars", "calendar"), _asset(other, right, "daily_bars")]
    conflicts = []
    output = tmp_path / "results"
    for order in (originals, originals[::-1]):
        group = publish_library(store, asset_snapshots=order)
        app = _after_close_app(monkeypatch, store.root, output, trade_date=JAN, snapshot_id=group.library_id)
        saved = ResultRef(app.session_state["after_close_result"], output, "limited")
        result = read_after_close(saved)
        reopened = AppTest.from_file(str(APP_PATH)).run()
        reopened.sidebar.radio[0].set_value("结果与导出").run()
        reopened.selectbox(key="result_select").set_value(saved.result_id).run()
        assert not reopened.exception
        gap_rows = next(table.value for table in reopened.dataframe if "sources" in table.value.columns)
        sources = gap_rows.loc[gap_rows.code.eq("VOL_01") & gap_rows.date.eq(str(JAN))
                               & gap_rows.field.eq("amount_cny"), "sources"].iloc[0]
        assert {(item["value"], item["unit"]) for item in sources} == {
            (30_000_000, "人民币元"), (60_000_000, "人民币元"),
        }
        observed = {row["code"]: row for row in result["observations"]}
        assert (result["coverage"]["count"], observed["VOL_01"]["close_raw"]) == (20, 10)
        assert observed["VOL_01"]["amount_cny"] is None
        assert "amount_median_20d" not in observed["VOL_01"]
        assert observed["VOL_02"]["amount_median_20d"] == 30_000_000
        conflict = next(gap for gap in result["observation_gaps"]
                        if (gap.get("code"), gap.get("date"), gap.get("field")) == ("VOL_01", str(JAN), "amount_cny"))
        assert {(fact["snapshot_id"], fact["value"], fact["unit"]) for fact in conflict["sources"]} == {
            (left.snapshot_id, 30_000_000, "人民币元"), (right.snapshot_id, 60_000_000, "人民币元"),
        }
        assert all(fact["source_root"] and fact["product_id"] and fact["source_ref"] for fact in conflict["sources"])
        print(f"same-day conflict: publication={saved.result_id}, close=10, median withheld, {conflict}")
        conflicts.append(conflict)
    assert conflicts[0] == conflicts[1]
