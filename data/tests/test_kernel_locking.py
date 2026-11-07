from concurrent.futures import ThreadPoolExecutor

import pytest


def test_lock_same_thread_reenters_normalized_root(tmp_path):
    from hetu_data.locking import worker_lock

    alias = tmp_path / "alias"
    root = tmp_path / "root"
    root.mkdir()
    alias.symlink_to(root, target_is_directory=True)
    with worker_lock(root) as path:
        assert path == root / "worker.lock"
        with worker_lock(alias, reentrant=True):
            pass
        with pytest.raises(RuntimeError):
            with worker_lock(root):
                pass
        with ThreadPoolExecutor(1) as executor:
            assert executor.submit(_attempt, root).result() is False
    assert _attempt(root)


def _attempt(root):
    from hetu_data.locking import worker_lock

    try:
        with worker_lock(root):
            return True
    except RuntimeError:
        return False


def _process_attempt(root, connection):
    connection.send(_attempt(root))
    connection.close()


@pytest.mark.parametrize("kind", ["thread", "process"])
def test_lock_other_thread_or_process_is_excluded(tmp_path, kind):
    import multiprocessing

    from hetu_data.locking import worker_lock

    with worker_lock(tmp_path):
        if kind == "thread":
            with ThreadPoolExecutor(1) as executor:
                assert executor.submit(_attempt, tmp_path).result(timeout=5) is False
        else:
            ctx = multiprocessing.get_context("spawn")
            receiver, sender = ctx.Pipe(False)
            process = ctx.Process(target=_process_attempt, args=(tmp_path, sender))
            process.start()
            assert receiver.poll(10)
            assert receiver.recv() is False
            process.join(10)
            assert process.exitcode == 0
    assert _attempt(tmp_path)


@pytest.mark.parametrize("operation", ["prepare", "import_files"])
def test_prepare_and_import_share_existing_worker_lock(tmp_path, operation, source_facts):
    from hetu_data.locking import worker_lock

    store, adapter, req, _ = kernel_case(tmp_path, source_facts)
    ref = store.prepare(req)
    calls = len(adapter.client.calls)
    snapshots = list((store.root / "snapshots").iterdir())
    manifest, files = import_case(tmp_path, source_facts, req)

    def invoke():
        if operation == "prepare":
            return store.prepare(req)
        return store.import_files(manifest, files)

    with worker_lock(store.root):
        assert len(store.read_table(ref, "daily_bars")) == 1
        with ThreadPoolExecutor(1) as executor:
            with pytest.raises(RuntimeError, match="another worker"):
                executor.submit(invoke).result(timeout=5)
        assert len(adapter.client.calls) == calls
        assert list((store.root / "snapshots").iterdir()) == snapshots


def kernel_case(tmp_path, source_facts):
    import json
    from datetime import date, datetime
    from zoneinfo import ZoneInfo

    from hetu_data import DataRequest, DataStore
    from hetu_data.providers.tushare import TushareProvider

    root = tmp_path / "store"
    root.mkdir(exist_ok=True)
    grant = dict(
        product_id="ts_a_daily",
        approved_scope_ref="synthetic",
        license_ref="synthetic",
        allowed_operations=["read", "acquire", "import"],
        markets=["SH"],
        codes=["SYNTH.SH"],
        start="2000-01-01",
        end="2099-12-31",
        acquire_until="2099-12-31",
        retained_use=False,
    )
    (root / "data-entitlements.json").write_text(json.dumps({"grants": [grant]}))
    adapter = TushareProvider(client=source_facts.SourceClient())
    store = DataStore(root, providers={"tushare": adapter})
    req = DataRequest(
        ("ts_a_daily",),
        ("SYNTH.SH",),
        date(2026, 1, 5),
        date(2026, 1, 5),
        datetime(2026, 10, 25, 9, tzinfo=ZoneInfo("Asia/Shanghai")),
        "backtest_low_volatility",
        mode="paid",
    )
    contracts = {
        "products": {"ts_a_daily": {"max_days": 6000, "upstream_per_fetch": 1, "sdk_calls": 0}},
        "limits": dict(
            provider_fetch=1, upstream_product_requests=1, network_attempts=1, approved_scope_ref="synthetic-lock"
        ),
    }
    return store, adapter, req, contracts


def test_execute_update_holds_lock_across_budget_and_publish(tmp_path, source_facts):
    import threading

    from hetu_data.updates import execute_update, plan_update

    store, adapter, req, contracts = kernel_case(tmp_path, source_facts)
    entered, release = threading.Event(), threading.Event()
    query = adapter.client.query

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return query(*args, **kwargs)

    adapter.client.query = blocked
    plan = plan_update(req, store, contracts)
    ledger = tmp_path / "usage.jsonl"

    def execute():
        return execute_update(plan, store, ledger, cancelled=lambda: False, progress=lambda _: None)

    with ThreadPoolExecutor(1) as executor:
        first = executor.submit(execute)
        try:
            assert entered.wait(10)
            before = ledger.read_bytes()
            with pytest.raises(RuntimeError, match="another worker"):
                execute()
            assert ledger.read_bytes() == before
        finally:
            release.set()
        result = first.result(timeout=10)
    assert not result["failures"]
    assert len(adapter.client.calls) == 1
    again = execute()
    assert len(adapter.client.calls) == 1
    assert again["actual_usage"]["network_attempts"] == 1


def import_case(tmp_path, facts, req):
    import json

    from hetu_data.models import data_request_to_dict

    raw = tmp_path / "raw.json"
    raw.write_bytes(facts.response())
    manifest = tmp_path / "import.json"
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
                        "request": data_request_to_dict(req),
                        "query": {
                            "api": "daily",
                            "params": {"ts_code": "SYNTH.SH", "start_date": "20260105", "end_date": "20260105"},
                        },
                        "source": {
                            "name": "tushare",
                            "fee_class": "paid",
                            "permission_basis": "synthetic",
                            "obtained_at": "2026-10-25T09:00:00+08:00",
                        },
                    }
                ],
            }
        )
    )
    return manifest, (raw,)
