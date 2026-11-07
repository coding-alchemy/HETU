"""Independent queue filtering uses real SQLite; opaque legacy records stay untouched."""

import pytest

from hetu_data.catalog import DataTaskStore
from hetu_data.jobs import JobService


def test_data_worker_only_handles_frozen_update_jobs(tmp_path, source_facts):
    from hetu_data.store import DataStore
    from hetu_data.worker import queued_job_ids, recover_stale_jobs, run_maintenance_job

    tasks = DataTaskStore(tmp_path)
    jobs = JobService(tasks)
    old = jobs.submit(tasks._save_payload("data", {}), "old")
    running = jobs.submit(tasks._save_payload("backtest", {}), "research")
    jobs.claim(running.job_id)
    import json
    from datetime import date, datetime
    from zoneinfo import ZoneInfo

    from hetu_data.models import DataRequest
    from hetu_data.providers.tushare import TushareProvider
    from hetu_data.updates import plan_update

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
    (tmp_path / "data-entitlements.json").write_text(json.dumps({"grants": [grant]}))
    adapter = TushareProvider(client=source_facts.SourceClient())
    data = DataStore(tmp_path, providers={"tushare": adapter})
    request = DataRequest(
        ("ts_a_daily",),
        ("SYNTH.SH",),
        date(2026, 1, 5),
        date(2026, 1, 6),
        datetime(2026, 10, 25, 9, tzinfo=ZoneInfo("Asia/Shanghai")),
        "backtest_low_volatility",
        mode="paid",
    )
    contracts = dict(
        products={"ts_a_daily": dict(max_days=6000, upstream_per_fetch=1, sdk_calls=0)},
        limits=dict(provider_fetch=1, upstream_product_requests=1, network_attempts=1, approved_scope_ref="synthetic"),
    )
    plan = plan_update(request, data, contracts)
    maintenance = jobs.submit(tasks.save_definition("maintenance", plan), "maintenance")
    before = [jobs.get(old.job_id), jobs.get(running.job_id)]
    assert queued_job_ids(tasks, kinds=("maintenance",)) == [maintenance.job_id]
    assert recover_stale_jobs(tasks, kinds=("maintenance",)) == []
    with pytest.raises(ValueError):
        run_maintenance_job(old.job_id, tasks, DataStore(tmp_path))
    assert run_maintenance_job(maintenance.job_id, tasks, data).state == "succeeded"
    assert len(adapter.client.calls) == 1
    assert [jobs.get(old.job_id), jobs.get(running.job_id)] == before
