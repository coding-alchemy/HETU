"""独立数据内核与研究消费者的合成事实集成验证。"""

import io

import pandas as pd
from hetu_data import DataStore
from hetu_data.archive import open_archive_view

from backtest_cases import build_prepared_case_store, case_definition, make_backtest_case
from hetu_quant.backtest_data import prepare_backtest


def test_shared_assets_keep_research_qualification_separate(tmp_path, monkeypatch):
    case = make_backtest_case("formal", "workflow")
    caps = pd.read_csv(io.StringIO(case["csvs"]["market_cap"]), dtype=str)
    caps = caps[~((caps["code"] == "VOL_01") & (caps["date"] == "2024-01-31"))]
    case["csvs"]["market_cap"] = caps.to_csv(index=False)
    store, ref = build_prepared_case_store(tmp_path / "facts", case, monkeypatch)
    independent = DataStore(store.root)
    bars = independent.read_table(ref, "daily_bars")
    pd.testing.assert_frame_equal(bars, store.read_table(ref, "daily_bars"))
    assert not bars[bars["code"] == "VOL_01"].empty
    assert {"product_id", "source_ref"} <= set(bars)
    with open_archive_view(independent, ref) as con:
        queried = con.execute(
            "SELECT code,date,close_raw,product_id,source_ref FROM daily_bars WHERE code = ? AND date = ?",
            ["VOL_01", "2024-01-31"],
        ).df()
    assert not queried.empty
    assert queried.source_ref.notna().all()
    prepare_backtest(case_definition(case, ref.snapshot_id), store, tmp_path / "bundle")
    exclusions = pd.read_parquet(tmp_path / "bundle" / "exclusions.parquet")
    selected = exclusions[(exclusions["code"] == "VOL_01") & (exclusions["date"] == "2024-01-31")]
    assert any("market_cap_unknown_at_signal" in str(reason) for reason in selected["reason"])
    pd.testing.assert_frame_equal(bars, independent.read_table(ref, "daily_bars"))


def test_legacy_and_annual_snapshots_share_read_and_permission_contract(tmp_path):
    import json

    import pytest
    from hetu_data.archive import scan_local_coverage
    from hetu_data.models import data_request_to_dict
    from hetu_data.store import AccessDenied

    from backtest_cases import build_case_store
    from hetu_quant.data import DataStore as ResearchStore
    from source_cases import response
    from test_data_sources import request, store

    owner_dir = tmp_path / "owner"
    owner_dir.mkdir()
    owner, _ = store(owner_dir, allowed_operations=["read", "import"])
    owner.config["products"]["ts_a_daily"]["storage_layout"] = "year"
    raw = owner_dir / "facts.json"
    raw.write_bytes(response())
    manifest = owner_dir / "import.json"
    manifest.write_text(
        json.dumps(
            {
                "mode": "paid",
                "items": [
                    {
                        "product_id": "ts_a_daily",
                        "table": "daily_bars",
                        "files": [raw.name],
                        "format": "provider_json",
                        "request": data_request_to_dict(request()),
                        "query": {
                            "api": "daily",
                            "params": {"ts_code": "SYNTH.SH", "start_date": "20260105", "end_date": "20260106"},
                        },
                        "source": {
                            "name": "tushare",
                            "fee_class": "paid",
                            "permission_basis": "synthetic licensed fixture",
                            "obtained_at": "2026-10-25T09:00:00+08:00",
                        },
                    }
                ],
            }
        )
    )
    annual = owner.import_files(manifest, (raw,))
    before = owner.load_manifest(annual)
    assert all("year=" in e["partition"] for e in before["table_files"] if e["table"] == "daily_bars")
    owner.config["products"]["ts_a_daily"].pop("storage_layout", None)
    legacy = owner.import_files(manifest, (raw,))
    assert all(
        "month=" in e["partition"] for e in owner.load_manifest(legacy)["table_files"] if e["table"] == "daily_bars"
    )
    expected = owner.read_table(annual, "daily_bars")
    assert {
        "code",
        "date",
        "open_raw",
        "high_raw",
        "low_raw",
        "close_raw",
        "volume_shares",
        "amount_cny",
        "product_id",
        "source_ref",
    } <= set(expected)
    for entry in (DataStore(owner.root), ResearchStore(owner.root)):
        for ref in (annual, legacy):
            pd.testing.assert_frame_equal(entry.read_table(ref, "daily_bars"), expected)
    assert owner.load_manifest(annual) == before
    consumer, seed = build_case_store(tmp_path / "consumer", make_backtest_case("coverage"))
    registered = [dict(root=str(owner.root), snapshot_id=annual.snapshot_id, tables=["daily_bars"])]
    inventory = scan_local_coverage(consumer, seed, asset_snapshots=registered)
    assert inventory.masks["SYNTH.SH"]["bars"].bit_count() == 1
    # A distinct source revision must remain observable as a conflict.
    raw.write_bytes(response(price=11))
    conflicting = owner.import_files(manifest, (raw,))
    conflicts = scan_local_coverage(
        consumer,
        seed,
        asset_snapshots=registered
        + [dict(root=str(owner.root), snapshot_id=conflicting.snapshot_id, tables=["daily_bars"])],
    )
    assert conflicts.masks["SYNTH.SH"]["conflicting"].bit_count() == 1
    assert conflicts.masks["SYNTH.SH"]["bars"] == 0

    from datetime import date

    from hetu_data.archive import archive_requirements

    from test_data_archive import targets

    case = make_backtest_case("coverage")
    history = targets(case)
    history.loc[0, "lifecycle_status"] = "mapping_unknown"
    another = history.iloc[[0]].copy()
    another["identity_id"] = "different-entity-same-code"
    history = pd.concat([history, another], ignore_index=True)
    history.attrs["target_scope"] = {"complete": True, "source_ref": "synthetic-independent-roster"}
    requirements = archive_requirements(
        history, pd.read_csv(io.StringIO(case["csvs"]["calendar"])), date(2024, 1, 4), years=1
    )
    assert len([obj for obj in requirements["objects"] if obj["code"] == "SYN_A.SH"]) == 2

    # Reuse the existing empty/nonempty action fact constructor (Arrow null + typed schema).
    from test_data_assets import FakeDatelessProvider, _actions_only_store, _two_code_request

    (tmp_path / "actions").mkdir()
    actions = _actions_only_store(tmp_path / "actions", FakeDatelessProvider(empty_codes=("AAA.SZ",)))
    action_ref = actions.prepare(_two_code_request(date(2024, 1, 1), date(2024, 12, 31)))
    for cls in (DataStore, ResearchStore):
        reader = cls(actions.root, config_path=tmp_path / "actions/products.json")
        assert reader.read_table(action_ref, "corporate_actions").code.tolist() == ["BBB.SZ"]

    (owner.root / "data-entitlements.json").write_text(json.dumps({"grants": []}))
    for entry in (DataStore(owner.root), ResearchStore(owner.root)):
        for ref in (annual, legacy):
            with pytest.raises(AccessDenied):
                entry.read_table(ref, "daily_bars")
    denied = scan_local_coverage(consumer, seed, asset_snapshots=registered)
    assert denied.failures
    assert "SYNTH.SH" not in denied.masks
