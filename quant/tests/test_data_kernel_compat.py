import pytest


def test_old_and_new_entries_share_snapshot_and_types(tmp_path):
    import hetu_data
    from hetu_data.access import AccessDenied as NewDenied

    from hetu_quant import models
    from hetu_quant.data import AccessDenied, DataStore

    assert models.DataRequest is hetu_data.DataRequest
    assert AccessDenied is NewDenied
    assert issubclass(DataStore, hetu_data.DataStore)
    import json
    from datetime import date, datetime
    from zoneinfo import ZoneInfo

    import pandas as pd

    config = hetu_data.DataStore(tmp_path / "defaults").config
    product = dict(config["products"]["ts_a_daily"])
    product.update(product_id="synthetic", provider="plain", fee_class="free", permission={"status": "declared_free"})
    product.pop("storage_layout", None)
    config["products"]["synthetic"] = product
    config["synthetic_marker"] = "root"
    root = tmp_path / "store"
    root.mkdir()
    (root / "data-products.json").write_text(json.dumps(config))

    class Plain:
        def fetch(self, *args):
            return pd.DataFrame(
                [
                    dict(
                        date="2026-01-05",
                        code="SYNTH.SH",
                        open_raw=10,
                        high_raw=11,
                        low_raw=9,
                        close_raw=10,
                        volume_shares=1234,
                        amount_cny=56780,
                    )
                ]
            )

    old = DataStore(root, providers={"plain": Plain()})
    req = models.DataRequest(
        ("synthetic",),
        ("SYNTH.SH",),
        date(2026, 1, 5),
        date(2026, 1, 5),
        datetime(2026, 10, 25, tzinfo=ZoneInfo("Asia/Shanghai")),
        "backtest_low_volatility",
    )
    assert models.data_request_from_dict(models.data_request_to_dict(req)) == req
    ref = old.prepare(req)
    new = hetu_data.DataStore(root)
    pd.testing.assert_frame_equal(old.read_table(ref, "daily_bars"), new.read_table(ref, "daily_bars"))
    assert old.config["synthetic_marker"] == new.config["synthetic_marker"] == "root"
    report = old.qualify(req, ref)
    assert not report.can_run
    assert "missing_table_for_backtest_low_volatility: historical_securities" in report.limitations
    assert len(list((root / "snapshots").iterdir())) == 1


@pytest.mark.parametrize("operation", ["prepare", "import_files", "execute_update"])
def test_legacy_worker_lock_reenters_data_writer(tmp_path, operation):
    import importlib.util
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path

    from hetu_data.updates import execute_update, plan_update

    from hetu_quant.worker import worker_lock

    def load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    repo = Path(__file__).resolve().parents[2]
    facts = load("lock_facts", repo / "quant/tests/source_cases.py")
    cases = load("lock_cases", repo / "data/tests/test_kernel_locking.py")
    store, adapter, req, contracts = cases.kernel_case(tmp_path, facts)
    manifest, files = cases.import_case(tmp_path, facts, req)
    with worker_lock(store.root) as path:
        assert path.name == "worker.lock"
        if operation == "prepare":
            ref = store.prepare(req)
        elif operation == "import_files":
            ref = store.import_files(manifest, files)
        else:
            result = execute_update(
                plan_update(req, store, contracts),
                store,
                tmp_path / "usage.jsonl",
                cancelled=lambda: False,
                progress=lambda _: None,
            )
            assert not result["failures"]
            ref = None
        if ref is not None:
            assert len(store.read_table(ref, "daily_bars")) == 1
        with ThreadPoolExecutor(1) as executor:
            assert executor.submit(cases._attempt, store.root).result(timeout=5) is False
