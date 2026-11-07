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
