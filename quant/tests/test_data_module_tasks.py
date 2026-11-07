"""Cross-entrypoint maintenance uses synthetic transport and real storage."""

import json

from hetu_data.catalog import DataTaskStore
from hetu_data.jobs import JobService
from hetu_data.updates import plan_update
from hetu_data.worker import run_maintenance_job

from hetu_quant.experiments import ExperimentStore
from hetu_quant.worker import run_job
from test_data_sources import request, store
from test_data_updates import contracts


def test_quant_maintenance_is_claimed_once(tmp_path):
    ds, adapter = store(tmp_path)
    tasks = ExperimentStore(ds.root)
    jobs = JobService(tasks)
    job = jobs.submit(tasks.save_definition("maintenance", plan_update(request(), ds, contracts())), "once")
    result = run_job(job.job_id, tasks, ds)
    assert result.state == "succeeded"
    assert len(adapter.client.calls) == 1
    assert run_job(job.job_id, tasks, ds) == result
    assert len(adapter.client.calls) == 1


def test_legacy_data_without_sidecar_keeps_result_contract(tmp_path):
    ds, adapter = store(tmp_path)
    tasks = ExperimentStore(ds.root)
    jobs = JobService(tasks)
    job = jobs.submit(tasks.save_definition("data", request()), "legacy")
    result = run_job(job.job_id, tasks, ds)
    assert result.state == "succeeded"
    assert result.output.parent == ds.root / "snapshots"
    assert len(list((ds.root / "snapshots").iterdir())) == 1
    assert len(adapter.client.calls) == 1
    from hetu_data.models import SnapshotRef

    report = ds.qualify(request(), SnapshotRef(result.output.name, ds.root))
    assert not report.can_run
    assert result.reason == "qualify: " + "; ".join(report.limitations[:5])


def test_resume_across_entrypoints_preserves_ledger(tmp_path):
    ds, adapter = store(tmp_path)
    tasks = DataTaskStore(ds.root)
    jobs = JobService(tasks)
    spec = contracts()
    spec["products"]["ts_a_daily"]["max_days"] = 1
    plan = plan_update(request(), ds, spec)
    assert len(plan["segments"]) == 2
    job = jobs.submit(tasks.save_definition("maintenance", plan), "resume")
    query = adapter.client.query

    def cancel_after_first(api, params, fields):
        payload = query(api, params, fields)
        jobs.cancel(job.job_id)
        return payload

    adapter.client.query = cancel_after_first
    first = run_maintenance_job(job.job_id, tasks, ds)
    assert first.state == "cancelled"
    adapter.client.query = query
    retry = jobs.retry(job.job_id, "retry")
    second = run_job(retry.job_id, ExperimentStore(ds.root), ds)
    assert second.state == "succeeded"
    result = json.loads((second.output / "update.json").read_text())
    assert result["actual_usage"]["network_attempts"] == 2
    assert result["incremental_usage"]["network_attempts"] == 1
    assert len(adapter.client.calls) == 2
    assert jobs.get(job.job_id) == first
