"""04.2 单机 worker：排他锁、事务领取、执行与状态发布。

计算步骤拆为模块级函数便于测试以可控替身隔离长计算；
状态与存储始终走真实 JobService / ExperimentStore。
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from zoneinfo import ZoneInfo

from hetu_quant.backtest import BacktestCancelled
from hetu_quant.backtest_models import BacktestDefinition, BacktestRef
from hetu_quant.data import DataStore
from hetu_quant.experiments import ExperimentStore
from hetu_quant.jobs import JobService
from hetu_quant.models import (
    DataRequest,
    RankingDefinition,
    ResultRef,
    SnapshotRef,
)

CN = ZoneInfo("Asia/Shanghai")

POLL_SECONDS = 1.0


class _JobCancelled(Exception):
    """运行中收到取消请求，在边界退出。"""


@contextlib.contextmanager
def worker_lock(root: Path) -> Iterator[Path]:
    """本机排他 worker 锁；另一活动 worker 持锁时立即失败。"""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / "worker.lock"
    handle = open(lock_path, "a+b")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError(
            f"another worker holds the lock at {lock_path}"
        ) from None
    try:
        yield lock_path
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def recover_stale_jobs(store: ExperimentStore) -> list:
    """启动恢复：仅在已确认取得排他锁后调用。

    遗留 running/cancel_requested 标 interrupted，保留原因与产物，不自动重排。
    """
    service = JobService(store)
    recovered = []
    for job in service.list():
        if job.state in ("running", "cancel_requested"):
            reason = job.reason or ""
            suffix = "worker_restart: 旧 worker 已退出，任务未完整发布"
            merged = f"{reason}; {suffix}" if reason else suffix
            recovered.append(
                service._update(job.job_id, state="interrupted", reason=merged)
            )
    return recovered


def _execute_backtest(
    definition: BacktestDefinition,
    data: DataStore,
    *,
    progress: Callable[[float], None],
    cancelled: Callable[[], bool],
) -> BacktestRef:
    """回测执行接缝：默认走 02 的 run_backtest，测试以替身隔离引擎。"""
    from hetu_quant.backtest import run_backtest, run_formal_job

    if definition.protocol["stage"] == "formal":
        return run_formal_job(definition, data, progress=progress, cancelled=cancelled)
    return run_backtest(
        definition, data, data.root, progress=progress, cancelled=cancelled
    )


def _execute_ranking(
    definition: RankingDefinition, ref: SnapshotRef, output_root: Path
) -> ResultRef:
    from hetu_quant.ranking import run_ranking

    return run_ranking(definition, ref, output_root)


def _execute_data(request: DataRequest, data: DataStore):
    ref = data.prepare(request)
    report = data.qualify(request, ref)
    return ref, report


def _raise_if_cancelled(service: JobService, job_id: str) -> None:
    if service.get(job_id).state == "cancel_requested":
        raise _JobCancelled


def _verify_result_published(result_dir: Path) -> None:
    manifest_path = result_dir / "result.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"result manifest missing: {result_dir.name}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for name in manifest.get("tables", []):
        if not (result_dir / f"{name}.parquet").exists():
            raise FileNotFoundError(f"result table missing: {name}")


def _verify_backtest_published(run_dir: Path) -> None:
    manifest_path = run_dir / "backtest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"backtest manifest missing: {run_dir.name}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for name in manifest.get("tables", {}):
        if not (run_dir / f"{name}.parquet").exists():
            raise FileNotFoundError(f"backtest table missing: {name}")


def run_job(job_id: str, store: ExperimentStore, data: DataStore):
    """领取并执行一个任务；取消在数据／计算／发布边界生效。"""
    service = JobService(store)
    claimed = service.claim(job_id)
    if claimed is None:
        return service.get(job_id)
    stage = "claim"
    try:
        definition = store.read_definition(claimed.definition_id)
        kind = store.definition_kind(claimed.definition_id)
        if kind == "data":
            assert isinstance(definition, DataRequest)
            stage = "data"
            ref, report = _execute_data(definition, data)
            _raise_if_cancelled(service, job_id)
            output = data.root / "snapshots" / ref.snapshot_id
            reason = None
            if not report.can_run:
                reason = "qualify: " + "; ".join(report.limitations[:5])
            service.finish(job_id, progress=1.0, output=output, reason=reason)
        elif kind == "backtest":
            assert isinstance(definition, BacktestDefinition)
            stage = "access"
            if definition.mode != "free":
                raise PermissionError("paid mode is not enabled (paid)")
            # 重读冻结定义（store.read_definition），不使用页面残留默认值
            ref = SnapshotRef(definition.snapshot_id, data.root)
            data.load_manifest(ref)
            _raise_if_cancelled(service, job_id)
            stage = "compute"

            def progress(value: float) -> None:
                bounded = min(max(float(value), 0.0), 1.0)
                service._update(job_id, progress=round(0.1 + 0.8 * bounded, 3))

            def cancelled() -> bool:
                return service.get(job_id).state == "cancel_requested"

            try:
                result = _execute_backtest(
                    definition, data, progress=progress, cancelled=cancelled
                )
            except BacktestCancelled:
                raise _JobCancelled from None
            _raise_if_cancelled(service, job_id)
            stage = "publish"
            run_dir = data.root / "backtests" / result.run_id
            _verify_backtest_published(run_dir)
            reason = None
            if result.status == "limited":
                manifest = json.loads(
                    (run_dir / "backtest.json").read_text(encoding="utf-8")
                )
                reason = "qualify: " + "; ".join(manifest.get("limitations", [])[:5])
            service.finish(job_id, progress=1.0, output=run_dir, reason=reason)
        elif kind == "ranking":
            assert isinstance(definition, RankingDefinition)
            stage = "access"
            if definition.mode != "free":
                raise PermissionError("paid mode is not enabled (paid)")
            ref = SnapshotRef(definition.snapshot_id, data.root)
            data.load_manifest(ref)
            _raise_if_cancelled(service, job_id)
            stage = "compute"
            result = _execute_ranking(definition, ref, data.root)
            _raise_if_cancelled(service, job_id)
            stage = "publish"
            result_dir = data.root / "results" / result.result_id
            _verify_result_published(result_dir)
            service.finish(job_id, progress=1.0, output=result_dir)
        else:
            raise ValueError(f"unknown definition kind: {kind!r}")
    except _JobCancelled:
        service.finalize_cancel(job_id)
    except Exception as error:  # noqa: BLE001 - 失败原因持久化到任务记录
        service.fail(job_id, f"{stage}: {type(error).__name__}: {error}")
    return service.get(job_id)


def _queued_job_ids(store: ExperimentStore) -> list[str]:
    service = JobService(store)
    return [
        job.job_id for job in reversed(service.list()) if job.state == "queued"
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HETU 量化单机 worker")
    parser.add_argument("--root", required=True, help=".hetu/quant 或测试临时根目录")
    parser.add_argument(
        "--once",
        action="store_true",
        help="排空当前队列后退出（默认常驻轮询）",
    )
    args = parser.parse_args(argv)
    root = Path(args.root)
    store = ExperimentStore(root)
    data = DataStore(root)
    try:
        with worker_lock(root):
            recover_stale_jobs(store)
            while True:
                pending = _queued_job_ids(store)
                if not pending:
                    if args.once:
                        return 0
                    time.sleep(POLL_SECONDS)
                    continue
                for job_id in pending:
                    run_job(job_id, store, data)
    except RuntimeError as error:
        print(f"worker 未启动：{error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
