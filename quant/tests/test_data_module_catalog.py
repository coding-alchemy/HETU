"""合成 SQLite 定义验证共享持久化，不执行数据请求。"""

import pytest

from hetu_quant.experiments import ExperimentStore
from hetu_quant.jobs import JobService


def test_shared_catalog_state_and_legacy_bytes(tmp_path):
    old = ExperimentStore(tmp_path)
    # 模拟已存在的定义；共享持久化不应要求解码研究类型。
    did = "def-legacy"
    path = old.definitions_dir / f"{did}.json"
    raw = b'{"definition_id":"def-legacy","kind":"data","definition":{}}\n'
    path.write_bytes(raw)
    with old._connect() as conn:
        conn.execute("INSERT INTO definitions VALUES (?, ?, ?, ?)", (did, "data", str(path), "old"))
    from hetu_data.catalog import DataTaskStore
    from hetu_data.jobs import JobService as SharedJobs

    shared = DataTaskStore(tmp_path)
    assert isinstance(old, __import__("hetu_data.catalog", fromlist=["CatalogStore"]).CatalogStore)
    assert shared.definition_kind(did) == "data"
    assert path.read_bytes() == raw
    with pytest.raises(ValueError):
        shared.read_definition(did)
    with pytest.raises(ValueError):
        shared.save_definition("data", {})
    jobs = SharedJobs(shared)
    first = jobs.submit(did, "click")
    assert JobService(old).submit(did, "click") == first
    assert jobs.claim(first.job_id).state == "running"
    assert jobs.claim(first.job_id) is None
    assert JobService(old).cancel(first.job_id).state == "cancel_requested"
    assert jobs.finish(first.job_id, progress=1, output=None).state == "cancelled"
    retry = JobService(old).retry(first.job_id, "retry")
    assert jobs.get(retry.job_id).attempt == 2
    assert jobs.get(first.job_id).state == "cancelled"


def test_maintenance_frozen_and_submission_conflict(tmp_path):
    from hetu_data.catalog import DataTaskStore
    from hetu_data.models import data_request_to_dict

    from test_data_sources import request

    tasks = DataTaskStore(tmp_path)
    plan = {"request": data_request_to_dict(request()), "segments": [], "limits": {}}
    did = tasks.save_definition("maintenance", plan)
    plan["segments"].append({"bad": "mutation"})
    assert tasks.read_definition(did)["segments"] == []
    assert ExperimentStore(tmp_path).read_definition(did) == tasks.read_definition(did)
    other = tasks.save_definition("maintenance", tasks.read_definition(did))
    jobs = JobService(tasks)
    first = jobs.submit(did, "same")
    assert jobs.submit(did, "same") == first
    with pytest.raises(ValueError, match="already bound"):
        jobs.submit(other, "same")
    with pytest.raises((ValueError, KeyError)):
        tasks.save_definition("maintenance", {"request": {}, "segments": []})


def test_legacy_job_records_remain_readable_without_proven_retry_origin(tmp_path):
    store = ExperimentStore(tmp_path)
    definition_id = store._save_payload("data", {})
    jobs = JobService(store)
    first = jobs.submit(definition_id, "legacy-click")
    jobs.cancel(first.job_id)
    old_retry = jobs.retry(first.job_id, "legacy-retry")
    jobs.cancel(old_retry.job_id)
    # The old catalog held the same jobs and submissions without any lineage table.
    with store._connect() as connection:
        connection.execute("DROP TABLE IF EXISTS job_lineage")
    reopened = JobService(ExperimentStore(tmp_path))
    assert reopened.original_submission_id(first.job_id) == first.submission_id
    assert reopened.get(old_retry.job_id).submission_id == "legacy-retry"
    assert reopened.get(old_retry.job_id).attempt == 2
    assert reopened.original_submission_id(old_retry.job_id) is None
    retry = reopened.retry(old_retry.job_id, "reopened-retry")
    assert retry.retry_of_job_id == old_retry.job_id
    assert reopened.original_submission_id(retry.job_id) is None
    with store._connect() as connection:
        connection.execute("UPDATE submissions SET definition_id = ? WHERE submission_id = ?",
                           ("another-definition", first.submission_id))
    assert reopened.original_submission_id(first.job_id) is None

    fresh = reopened.submit(definition_id, "fresh-click")
    reopened.cancel(fresh.job_id)
    next_job = reopened.retry(fresh.job_id, "fresh-retry")
    reopened.cancel(next_job.job_id)
    latest = reopened.retry(next_job.job_id, "fresh-second-retry")
    assert latest.retry_of_job_id == next_job.job_id
    assert reopened.original_submission_id(latest.job_id) == fresh.submission_id
