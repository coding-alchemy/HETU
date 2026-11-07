"""04.1 冻结定义与可找回任务、04.2 单机 worker 的持久化与状态行为。

合成定义直接使用 models 的冻结类型；worker 计算步骤以模块级替身隔离，
状态与存储全部走真实实现。
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from hetu_quant import worker
from hetu_quant.data import DataStore
from hetu_quant.experiments import ExperimentStore
from hetu_quant.jobs import JobService
from hetu_quant.models import RankingDefinition, ResultRef

CN = ZoneInfo("Asia/Shanghai")


@pytest.fixture
def ranking_definition() -> RankingDefinition:
    return RankingDefinition(
        as_of=datetime(2024, 7, 1, 9, 0, tzinfo=CN),
        cutoff=date(2024, 6, 28),
        snapshot_id="snap-test-1",
        benchmark_id="csi300_price",
        parameters={"purpose": "ranking_current"},
    )


def _service(root: Path) -> JobService:
    return JobService(ExperimentStore(root))


# ---- 04.1 提交幂等、定义冻结、重试、重开 ----


def test_submission_is_idempotent(tmp_path, ranking_definition):
    store = ExperimentStore(tmp_path)
    definition_id = store.save_definition("ranking", ranking_definition)
    first = JobService(store).submit(definition_id, "click-1")
    reopened = JobService(ExperimentStore(tmp_path))
    assert reopened.submit(definition_id, "click-1").job_id == first.job_id
    assert len(reopened.list()) == 1


def test_same_submission_with_different_definition_rejected(
    tmp_path, ranking_definition
):
    store = ExperimentStore(tmp_path)
    first_id = store.save_definition("ranking", ranking_definition)
    changed = RankingDefinition(
        as_of=ranking_definition.as_of,
        cutoff=date(2024, 6, 27),
        snapshot_id=ranking_definition.snapshot_id,
        benchmark_id=ranking_definition.benchmark_id,
        parameters={"purpose": "ranking_current"},
    )
    second_id = store.save_definition("ranking", changed)
    service = _service(tmp_path)
    service.submit(first_id, "click-1")
    with pytest.raises(ValueError, match="submission"):
        service.submit(second_id, "click-1")
    assert len(service.list()) == 1


def test_definition_is_frozen(tmp_path, ranking_definition):
    store = ExperimentStore(tmp_path)
    parameters = {"purpose": "ranking_current", "thresholds": {"x": 1}}
    original = RankingDefinition(
        as_of=ranking_definition.as_of,
        cutoff=ranking_definition.cutoff,
        snapshot_id=ranking_definition.snapshot_id,
        benchmark_id=ranking_definition.benchmark_id,
        parameters=parameters,
    )
    definition_id = store.save_definition("ranking", original)
    parameters["thresholds"]["x"] = 999
    parameters["injected"] = True
    reread = store.read_definition(definition_id)
    assert dict(reread.parameters) == {
        "purpose": "ranking_current",
        "thresholds": {"x": 1},
    }
    with pytest.raises(TypeError):
        reread.parameters["injected"] = True  # type: ignore[index]
    resaved = store.save_definition("ranking", reread)
    assert resaved != definition_id, "显式另存修改产生新定义"
    assert store.read_definition(definition_id).cutoff == date(2024, 6, 28)


def test_retry_keeps_previous_attempt(tmp_path, ranking_definition):
    store = ExperimentStore(tmp_path)
    definition_id = store.save_definition("ranking", ranking_definition)
    service = JobService(store)
    first = service.submit(definition_id, "click-1")
    failed = service._update(first.job_id, state="failed", reason="boom")
    assert failed.state == "failed"
    second = service.retry(first.job_id, "click-2")
    assert second.job_id != first.job_id
    assert second.attempt == first.attempt + 1
    assert second.definition_id == definition_id
    assert second.state == "queued"
    records = {job.job_id: job for job in service.list()}
    assert records[first.job_id].state == "failed"
    assert records[first.job_id].reason == "boom"
    assert records[second.job_id].state == "queued"
    for state in ("succeeded", "queued", "running", "cancel_requested"):
        job = records[first.job_id]
        service._update(job.job_id, state=state)
        with pytest.raises(ValueError, match="retry"):
            service.retry(first.job_id, f"click-{state}")
        service._update(first.job_id, state="failed", reason="boom")


def test_jobs_survive_reopen(tmp_path, ranking_definition):
    store = ExperimentStore(tmp_path)
    definition_id = store.save_definition("ranking", ranking_definition)
    first = _service(tmp_path).submit(definition_id, "click-1")
    reopened = _service(tmp_path)
    found = reopened.get(first.job_id)
    assert found.submission_id == "click-1"
    assert found.state == "queued"
    listed = reopened.list()
    assert [job.job_id for job in listed] == [first.job_id]
    assert reopened.submit(definition_id, "click-1").job_id == first.job_id


# ---- 04.1 结果索引不伪造、旧时点不隐藏既有结果 ----


def test_get_result_requires_published_files(tmp_path):
    store = ExperimentStore(tmp_path)
    with pytest.raises(FileNotFoundError):
        store.get_result("res-not-there")
    orphan = store.root / "results" / "res-orphan"
    orphan.mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        store.get_result("res-orphan")


def test_old_as_of_does_not_hide_existing_result(tmp_path, ranking_definition):
    store = ExperimentStore(tmp_path)
    result_dir = store.root / "results" / "res-kept-1"
    result_dir.mkdir(parents=True)
    (result_dir / "result.json").write_text(
        json.dumps({"result_id": "res-kept-1", "status": "limited"}),
        encoding="utf-8",
    )
    assert store.get_result("res-kept-1").status == "limited"
    newer = RankingDefinition(
        as_of=datetime(2024, 8, 1, 9, 0, tzinfo=CN),
        cutoff=date(2024, 7, 31),
        snapshot_id=ranking_definition.snapshot_id,
        benchmark_id=ranking_definition.benchmark_id,
        parameters={"purpose": "ranking_current"},
    )
    store.save_definition("ranking", newer)
    assert store.get_result("res-kept-1").status == "limited"


# ---- 04.2 worker：领取、取消、恢复、免费边界 ----


def _queued_ranking_job(tmp_path, definition: RankingDefinition) -> tuple:
    store = ExperimentStore(tmp_path)
    definition_id = store.save_definition("ranking", definition)
    service = JobService(store)
    job = service.submit(definition_id, "click-1")
    data = DataStore(tmp_path / "data")
    snapshot_dir = data.root / "snapshots" / definition.snapshot_id
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    (snapshot_dir / "manifest.json").write_text("{}", encoding="utf-8")
    return store, service, job, data


def _fake_ranking(root: Path, calls: list, blocker: threading.Event | None = None):
    def compute(definition, ref, output_root):
        calls.append(definition.snapshot_id)
        if blocker is not None:
            blocker.wait(10)
        result_id = "res-fake-1"
        result_dir = Path(output_root) / "results" / result_id
        result_dir.mkdir(parents=True, exist_ok=True)
        (result_dir / "result.json").write_text(
            json.dumps({"result_id": result_id, "status": "complete", "tables": []}),
            encoding="utf-8",
        )
        return ResultRef(result_id=result_id, root=output_root, status="complete")

    return compute


def test_only_one_claim_executes(tmp_path, ranking_definition, monkeypatch):
    store, service, job, data = _queued_ranking_job(tmp_path, ranking_definition)
    calls: list[str] = []
    fake = _fake_ranking(tmp_path, calls)
    started = threading.Event()
    gate = threading.Event()

    def compute(definition, ref, output_root):
        started.set()
        gate.wait(5)
        return fake(definition, ref, output_root)

    monkeypatch.setattr(worker, "_execute_ranking", compute)
    barrier = threading.Barrier(3)
    outcomes: dict[str, object] = {}

    def contend(name: str):
        barrier.wait(5)
        outcomes[name] = worker.run_job(job.job_id, store, data)

    threads = [
        threading.Thread(target=contend, args=(f"t{i}",)) for i in range(2)
    ]
    for thread in threads:
        thread.start()
    barrier.wait(5)
    assert started.wait(5), "至少一个领取必须进入计算"
    gate.set()
    for thread in threads:
        thread.join(10)
    assert calls == ["snap-test-1"], "两个竞争领取只执行一次计算"
    final = service.get(job.job_id)
    assert final.state == "succeeded"
    assert all(item.job_id == job.job_id for item in outcomes.values())


def test_cancel_before_publish(tmp_path, ranking_definition, monkeypatch):
    store, service, job, data = _queued_ranking_job(tmp_path, ranking_definition)
    entered = threading.Event()
    release = threading.Event()
    calls: list[str] = []
    fake = _fake_ranking(tmp_path, calls, blocker=release)
    blocker = threading.Event()

    def compute(definition, ref, output_root):
        entered.set()
        blocker.wait(5)
        return fake(definition, ref, output_root)

    monkeypatch.setattr(worker, "_execute_ranking", compute)
    outcome: dict[str, object] = {}

    def run():
        outcome["job"] = worker.run_job(job.job_id, store, data)

    thread = threading.Thread(target=run)
    thread.start()
    assert entered.wait(5), "计算替身必须真正进入"
    blocker.set()
    requested = service.cancel(job.job_id)
    assert requested.state == "cancel_requested"
    release.set()
    thread.join(10)
    final = service.get(job.job_id)
    assert final.state == "cancelled"
    assert final.output is None, "取消后不得发布成功产物"
    assert len(calls) == 1
    assert outcome["job"].state == "cancelled"


def test_cancel_queued_goes_terminal_directly(tmp_path, ranking_definition):
    store = ExperimentStore(tmp_path)
    definition_id = store.save_definition("ranking", ranking_definition)
    service = JobService(store)
    job = service.submit(definition_id, "click-1")
    cancelled = service.cancel(job.job_id)
    assert cancelled.state == "cancelled"
    assert service.cancel(job.job_id).state == "cancelled"
    again = service.retry(job.job_id, "click-2")
    assert again.attempt == 2


def test_restart_marks_interrupted(tmp_path, ranking_definition):
    store, service, job, _ = _queued_ranking_job(tmp_path, ranking_definition)
    service._update(job.job_id, state="running", progress=0.5)
    service._update(job.job_id, output=tmp_path / "artifacts" / "partial")
    recovered = worker.recover_stale_jobs(store)
    assert [item.job_id for item in recovered] == [job.job_id]
    record = service.get(job.job_id)
    assert record.state == "interrupted"
    assert "restart" in (record.reason or "")
    assert record.output == tmp_path / "artifacts" / "partial", "保留产物位置"


def test_free_mode_rechecked_by_worker(tmp_path, ranking_definition, monkeypatch):
    paid = RankingDefinition(
        as_of=ranking_definition.as_of,
        cutoff=ranking_definition.cutoff,
        snapshot_id=ranking_definition.snapshot_id,
        benchmark_id=ranking_definition.benchmark_id,
        parameters={"purpose": "ranking_current"},
        mode="paid",
    )
    store, service, job, data = _queued_ranking_job(tmp_path, paid)
    calls: list[str] = []
    monkeypatch.setattr(worker, "_execute_ranking", _fake_ranking(tmp_path, calls))
    final = worker.run_job(job.job_id, store, data)
    assert final.state == "failed"
    assert "paid" in (final.reason or "")
    assert calls == [], "付费模式不得触发排名计算"


def test_terminal_job_cannot_be_reclaimed(tmp_path, ranking_definition, monkeypatch):
    store, service, job, data = _queued_ranking_job(tmp_path, ranking_definition)
    calls: list[str] = []
    monkeypatch.setattr(worker, "_execute_ranking", _fake_ranking(tmp_path, calls))
    first = worker.run_job(job.job_id, store, data)
    assert first.state == "succeeded"
    again = worker.run_job(job.job_id, store, data)
    assert again.state == "succeeded"
    assert again.output == first.output
    assert calls == ["snap-test-1"], "终态不能被重复领取覆盖"


def test_worker_lock_is_exclusive(tmp_path):
    with worker.worker_lock(tmp_path):
        with pytest.raises(RuntimeError, match="worker"):
            with worker.worker_lock(tmp_path):
                pass


def test_worker_cli_exit_and_recovery(tmp_path, ranking_definition):
    store, service, job, _ = _queued_ranking_job(tmp_path, ranking_definition)
    service._update(job.job_id, state="running", progress=0.3)
    env = {"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}

    def run_cli() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "hetu_quant.worker",
                "--root",
                str(tmp_path),
                "--once",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )

    with worker.worker_lock(tmp_path):
        blocked = run_cli()
    assert blocked.returncode != 0
    assert service.get(job.job_id).state == "running", (
        "旧 worker 仍存活（锁被持有）时不得把 running 标 interrupted"
    )
    done = run_cli()
    assert done.returncode == 0, done.stderr
    record = service.get(job.job_id)
    assert record.state == "interrupted"
    again = run_cli()
    assert again.returncode == 0, again.stderr
