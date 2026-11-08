import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from hetu_data import DataRequest, DataStore
from hetu_data.access import AccessDenied
from hetu_data.library import load_library, publish_library, query_library, query_library_sql
from hetu_data.providers.tushare import TushareProvider

START, END = date(2026, 1, 5), date(2026, 1, 6)
CODES = ("SYNTH.SH",)


def _shard(root, facts, day, price=10, *, refresh=False):
    root.mkdir(parents=True, exist_ok=True)
    (root / "data-entitlements.json").write_text(json.dumps({"grants": [dict(
        product_id="ts_a_daily", approved_scope_ref="synthetic", license_ref="synthetic",
        allowed_operations=["read", "acquire"], markets=["SH"], codes=list(CODES),
        start="2000-01-01", end="2099-12-31", acquire_until="2099-12-31", retained_use=False,
    )]}))

    class Client:
        def query(self, api, params, fields):
            return facts.response(day=day.strftime("%Y%m%d"), price=price)

    store = DataStore(root, providers={"tushare": TushareProvider(client=Client())})
    ref = store.prepare(DataRequest(
        ("ts_a_daily",), CODES, day, day, datetime(2026, 1, 7, tzinfo=ZoneInfo("Asia/Shanghai")),
        "daily_archive", mode="paid", refresh_from=day if refresh else None,
    ))
    return dict(root=str(root), snapshot_id=ref.snapshot_id, tables=["daily_bars"])


def test_publish_accumulates_registered_shards(tmp_path, source_facts):
    history = _shard(tmp_path / "history", source_facts, START)
    increment = _shard(tmp_path / "increment", source_facts, END, price=11)
    _shard(tmp_path / "unregistered", source_facts, START, price=30)
    store = DataStore(tmp_path / "library")
    first = publish_library(store, asset_snapshots=(history,))
    assert first is not None
    fixed = store.root / "libraries" / first.library_id / "manifest.json"
    prior = fixed.read_bytes()
    both = publish_library(store, asset_snapshots=(increment, history), previous=first)
    manifest = load_library(store, both)
    assert fixed.read_bytes() == prior
    assert len(manifest["table_files"]) == 2
    assert not list(store.root.rglob("*.parquet"))
    assert {s["root"] for s in manifest["sources"]} == {history["root"], increment["root"]}
    assert query_library(store, both, codes=CODES, start=START, end=END)["bars"].date.tolist() == [
        START.isoformat(), END.isoformat(),
    ]
    assert query_library(store, first, codes=CODES, start=START, end=END)["bars"].date.tolist() == [START.isoformat()]


def test_library_revision_and_source_conflicts(tmp_path, source_facts):
    left = _shard(tmp_path / "original", source_facts, START)
    duplicate = _shard(tmp_path / "duplicate", source_facts, START)
    right = _shard(tmp_path / "conflict", source_facts, START, price=11)
    store = DataStore(tmp_path / "library")
    ref = publish_library(store, asset_snapshots=(left, duplicate, right))
    assert ref is not None
    result = query_library(store, ref, codes=CODES, start=START, end=END, columns=("close_raw",))
    assert result["status"] == "limited"
    assert result["bars"].close_raw.tolist() == [11, 10] or result["bars"].close_raw.tolist() == [10, 11]
    assert len(result["sources"]) == 3
    assert "conflicting_bars" in set(result["gaps"].reason)


def test_library_queries_pin_selection_and_permissions(tmp_path, source_facts):
    left = _shard(tmp_path / "history", source_facts, START)
    right = _shard(tmp_path / "increment", source_facts, END, price=11)
    store = DataStore(tmp_path / "library")
    first = publish_library(store, asset_snapshots=(left,))
    assert first is not None
    predicate = {"field": "close_raw", "op": "ge", "value": 10}
    before = query_library(store, first, codes=CODES, start=START, end=END, predicates=(predicate,))
    publish_library(store, asset_snapshots=(right,), previous=first)
    predicate["value"] = 90
    frozen = before["selection"]
    assert frozen["predicates"][0]["value"] == 10
    sql = "SELECT * FROM daily_bars"
    pd.testing.assert_frame_equal(before["bars"], query_library_sql(store, frozen, sql), check_dtype=False)
    for unsafe in ("SELECT * FROM read_parquet('/absent')", "DELETE FROM daily_bars", "SELECT 1; SELECT 2",
                   "SELECT * FROM duckdb_settings()"):
        with pytest.raises((ValueError, AccessDenied)):
            query_library_sql(store, frozen, unsafe)
    grant = Path(left["root"]) / "data-entitlements.json"
    doc = json.loads(grant.read_text())
    doc["grants"][0]["codes"] = []
    grant.write_text(json.dumps(doc))
    with pytest.raises(AccessDenied):
        query_library_sql(store, frozen, sql)


def test_listing_reads_small_published_summaries(tmp_path, source_facts, monkeypatch):
    from hetu_data.library import list_libraries

    shard = _shard(tmp_path / "history", source_facts, START)
    store = DataStore(tmp_path / "library")
    ref = publish_library(store, asset_snapshots=(shard,))
    original = Path.read_text

    def bounded_read(path, *args, **kwargs):
        if path.parent.name == ref.library_id and path.name == "manifest.json":
            raise AssertionError("listing must not load the full file index")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", bounded_read)
    assert list_libraries(store)[0]["library_id"] == ref.library_id


def test_restored_library_can_republish_verified_revision(tmp_path, source_facts):
    from hetu_data.archive import backup_library

    root = tmp_path / "source"
    old = _shard(root, source_facts, START)
    new = _shard(root, source_facts, START, price=11, refresh=True)
    rule = dict(root=str(root), product_id="ts_a_daily", table="daily_bars",
                superseded_snapshot_id=old["snapshot_id"], selected_snapshot_id=new["snapshot_id"],
                codes=list(CODES), start=START.isoformat(), end=START.isoformat(),
                evidence_ref="evidence/revision.json")
    (root / rule["evidence_ref"]).write_text(json.dumps(rule))
    new["revision_selections"] = [rule]
    grant = root / "data-entitlements.json"
    doc = json.loads(grant.read_text())
    doc["grants"][0]["allowed_operations"].append("backup")
    grant.write_text(json.dumps(doc))
    store = DataStore(root)
    ref = publish_library(store, asset_snapshots=(old, new))
    assert query_library(store, ref, codes=CODES, start=START, end=START)["bars"].close_raw.tolist() == [11]
    restored = backup_library(store, ref, tmp_path / "backup")
    backup = DataStore(restored.root)
    assert query_library(backup, restored, codes=CODES, start=START, end=START)["bars"].close_raw.tolist() == [11]
    increment = publish_library(backup, asset_snapshots=(), previous=restored)
    assert query_library(backup, increment, codes=CODES, start=START, end=START)["bars"].close_raw.tolist() == [11]


@pytest.mark.parametrize("revoke_identity", [False, True])
def test_listed_tushare_identity_and_auxiliary_permission(tmp_path, source_facts, revoke_identity):
    root = tmp_path / "history"
    _shard(root, source_facts, START)
    grants = root / "data-entitlements.json"
    document = json.loads(grants.read_text())
    document["grants"].append(dict(
        document["grants"][0], product_id="ts_a_basic", allowed_operations=["read", "acquire"],
    ))
    grants.write_text(json.dumps(document))

    class Client:
        def query(self, api, params, fields):
            return source_facts.response(api, empty=api == "stock_basic" and params["list_status"] != "L")

    owner = DataStore(root, providers={"tushare": TushareProvider(client=Client())})
    snapshot = owner.prepare(DataRequest(
        ("ts_a_daily", "ts_a_basic"), CODES, START, START, datetime(2026, 1, 7, tzinfo=ZoneInfo("Asia/Shanghai")),
        "daily_archive", mode="paid",
    ))
    store = DataStore(tmp_path / "library")
    ref = publish_library(store, asset_snapshots=(dict(
        root=str(root), snapshot_id=snapshot.snapshot_id, tables=["daily_bars", "securities"],
    ),))
    if revoke_identity:
        document["grants"][1]["allowed_operations"].remove("read")
        grants.write_text(json.dumps(document))
    result = query_library(store, ref, codes=CODES, start=START, end=START)
    assert result["bars"].close_raw.tolist() == [10]
    if revoke_identity:
        assert result["status"] == "limited"
        assert [row["table"] for row in result["auxiliary_limits"]] == ["securities"]
        assert "lifecycle_unknown" in result["gaps"].reason.tolist()
    else:
        assert result["gaps"].empty
        assert result["auxiliary_limits"] == []
    pd.testing.assert_frame_equal(
        result["bars"], query_library_sql(store, result["selection"], "SELECT * FROM daily_bars"), check_dtype=False,
    )

    from hetu_data.archive import query_archive

    legacy = query_archive(owner, snapshot, codes=CODES, start=START, end=START)
    assert legacy["bars"].close_raw.tolist() == [10]
    pd.testing.assert_frame_equal(legacy["gaps"], result["gaps"])
    assert legacy["auxiliary_limits"] == result["auxiliary_limits"]

    document["grants"][0]["allowed_operations"].remove("read")
    grants.write_text(json.dumps(document))
    with pytest.raises(AccessDenied):
        query_library(store, ref, codes=CODES, start=START, end=START)
