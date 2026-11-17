import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

from hetu_data.catalog import DataTaskStore
from hetu_data.jobs import JobService
from hetu_data.locking import worker_lock
from hetu_data.store import DataStore


def recover_stale_jobs(store: DataTaskStore, *, kinds=None) -> list:
    """启动恢复：仅在已确认取得排他锁后调用。

    遗留 running/cancel_requested 标 interrupted，保留原因与产物，不自动重排。
    """
    service = JobService(store)
    recovered = []
    for job in service.list():
        if job.state in ("running", "cancel_requested") and (
            kinds is None or store.definition_kind(job.definition_id) in kinds
        ):
            reason = job.reason or ""
            suffix = "worker_restart: 旧 worker 已退出，任务未完整发布"
            merged = f"{reason}; {suffix}" if reason else suffix
            recovered.append(service._update(job.job_id, state="interrupted", reason=merged))
    return recovered


def queued_job_ids(store: DataTaskStore, *, kinds=None) -> list[str]:
    service = JobService(store)
    return [
        job.job_id
        for job in reversed(service.list())
        if job.state == "queued" and (kinds is None or store.definition_kind(job.definition_id) in kinds)
    ]


class _JobCancelled(Exception):
    """运行中收到取消请求，在边界退出。"""


def _raise_if_cancelled(service: JobService, job_id: str) -> None:
    if service.get(job_id).state == "cancel_requested":
        raise _JobCancelled


def approved_usage_ledger(root: Path, approved_scope_ref: str) -> Path:
    if not approved_scope_ref:
        raise ValueError("approved_scope_ref is required")
    scope_id = hashlib.sha256(approved_scope_ref.encode()).hexdigest()[:24]
    return root / "updates" / "approved-usage" / scope_id / "usage.jsonl"


def maintenance_data_store(data, definition):
    """Select the registered acquisition owner's separate product and grants."""
    relative = definition.get("acquisition_owner")
    if relative is None:
        return data
    owner = data.root.resolve() / relative
    if (owner.parent != data.root.resolve() / "owners" or owner.resolve() != owner
            or not (owner / "data-products.json").is_file() or not (owner / "data-entitlements.json").is_file()):
        raise PermissionError("maintenance acquisition owner is not registered under this data root")
    return DataStore(owner)


def _market_day_revision_rules(acquisition, snapshot_id, manifest, registered):
    rules = []
    root = acquisition.root.resolve()
    for pid, span in manifest.get("market_day_refresh", {}).items():
        table = acquisition.product(pid)["tables"][0]
        for parent in span["supersedes"]:
            if parent not in registered:
                continue
            rule = dict(
                root=str(root), product_id=pid, table=table, superseded_snapshot_id=parent,
                selected_snapshot_id=snapshot_id, codes=span["codes"], start=span["start"], end=span["end"],
            )
            relative = f"evidence/market-day-revisions/{snapshot_id}-{parent}-{pid}.json"
            path = root / relative
            body = json.dumps(rule, ensure_ascii=False, sort_keys=True)
            if path.exists():
                if path.read_text(encoding="utf-8") != body:
                    raise ValueError("recorded market-day revision evidence differs")
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(".tmp")
                temporary.write_text(body, encoding="utf-8")
                temporary.replace(path)
            rules.append(dict(**rule, evidence_ref=relative))
    return rules


def _publish_update_library(data, ledger, definition, result, *, acquisition=None):
    from hetu_data.library import load_library, publish_library
    from hetu_data.models import LibraryRef, SnapshotRef
    from hetu_data.updates import _read_events, _write

    previous_ref = definition.get("library_ref")
    for event in reversed(_read_events(ledger)):
        if event["type"] == "library_published":
            previous_ref = dict(library_id=event["library_id"], root=event["root"])
            break
    result["library_ref"] = previous_ref
    previous = LibraryRef(**previous_ref) if previous_ref else None
    prior = load_library(data, previous) if previous else {}
    acquisition = data if acquisition is None else acquisition
    root = str(acquisition.root.resolve())
    known = {asset["snapshot_id"] for asset in prior.get("asset_snapshots", ()) if asset["root"] == root}
    snapshot_ids = dict.fromkeys([*definition.get("registered_local_snapshots", ()), *result["snapshot_ids"]])
    registered = known | snapshot_ids.keys()
    assets = []
    for sid in snapshot_ids:
        if sid in known:
            continue
        manifest = acquisition.load_manifest(SnapshotRef(sid, acquisition.root))
        tables = sorted({entry["table"] for entry in manifest["table_files"]})
        raw_products = sorted({item["product_id"] for item in manifest["items"] if item.get("raw_files")})
        revisions = [
            dict(
                root=root, product_id=pid, table="calendar", superseded_snapshot_id=parent, selected_snapshot_id=sid,
                codes=[], start=span["start"], end=span["end"], evidence_ref=f"snapshots/{sid}/manifest.json",
            )
            for pid, span in manifest.get("calendar_refresh", {}).items()
            for parent in span["supersedes"] if parent in registered
        ]
        revisions.extend(_market_day_revision_rules(acquisition, sid, manifest, registered))
        assets.append(dict(
            root=root, snapshot_id=sid, tables=tables, raw_products=raw_products, revision_selections=revisions,
        ))
    if not assets:
        return previous_ref
    published = publish_library(data, asset_snapshots=tuple(assets), previous=previous)
    ref = dict(library_id=published.library_id, root=str(published.root))
    _write(ledger, dict(type="library_published", **ref, snapshot_ids=[a["snapshot_id"] for a in assets]))
    return ref


def run_maintenance_job(job_id, tasks, data):
    with worker_lock(data.root, reentrant=True):
        service = JobService(tasks)
        job = service.get(job_id)
        if tasks.definition_kind(job.definition_id) != "maintenance":
            raise ValueError("only maintenance jobs can be executed")
        claimed = service.claim(job_id)
        if claimed is None:
            return service.get(job_id)
        stage = "maintenance"
        try:
            definition = tasks.read_definition(claimed.definition_id)
            from hetu_data.updates import execute_update, maintenance_plan

            stage = "maintenance"
            directory = data.root / "updates" / claimed.definition_id
            output = directory / "jobs" / job_id
            output.mkdir(parents=True, exist_ok=True)
            service._update(job_id, output=output)

            def publish(name, payload):
                path = output / name
                temporary = path.with_suffix(".tmp")
                temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                temporary.replace(path)

            def update_progress(value):
                publish("progress.json", value)
                total = max(1, value["segments"])
                service._update(job_id, progress=round(0.1 + 0.8 * min(1, value["completed"] / total), 3))

            # 安全命名同一批准额度的账本；不是结果等价/过期/批次绑定条件。
            ledger = approved_usage_ledger(data.root, definition.get("limits", {}).get("approved_scope_ref", ""))
            acquisition = maintenance_data_store(data, definition)
            phase, added = definition, {}
            contracts = json.loads(json.dumps(definition.get("maintenance_contracts")))
            covered_segments = set()

            def cancelled():
                return service.get(job_id).state == "cancel_requested"

            while True:
                result = execute_update(
                    phase, acquisition, ledger, quota_root=data.root, cancelled=cancelled, progress=update_progress,
                )
                for meter, count in result["incremental_usage"].items():
                    prior_count = added.get(meter, 0)
                    added[meter] = None if count is None or prior_count is None else prior_count + count
                result["incremental_usage"] = added
                result["approved_usage_ledger"] = str(ledger)
                if "library_ref" in definition:
                    try:
                        result["library_ref"] = _publish_update_library(
                            data, ledger, definition, result, acquisition=acquisition,
                        )
                    except Exception:
                        # The next explicit attempt can retry publication using the successful snapshots.
                        publish("update.json", result)
                        raise
                if not contracts or cancelled():
                    publish("update.json", result)
                    break
                settings = contracts["maintenance"]
                if result.get("library_ref"):
                    contracts["library_ref"] = result["library_ref"]
                selected = settings.get("coverage_snapshot_ids")
                if selected is not None:
                    settings["coverage_snapshot_ids"] = list(dict.fromkeys(selected + result["snapshot_ids"]))
                from datetime import date

                post = maintenance_plan(
                    acquisition, date.fromisoformat(definition["target"]), tuple(definition["request"]["product_ids"]),
                    mode=definition["request"]["mode"], contracts=contracts, quota_root=data.root,
                )
                result["maintenance"] = {k: post[k] for k in ("start", "target", "domains", "target_scope")}
                result["maintenance"]["semantics"] = "请求完成不等于内容或PIT齐备，后续周期仍需手动触发"
                publish("update.json", result)
                if (not settings.get("follow_calendar_and_shortfalls") or result["pending"]
                        or result["failures"] or result["stop_reason"] or post["unresolved"]):
                    break
                covered_segments.update(json.dumps(s, sort_keys=True) for s in phase["segments"])
                remaining = [
                    s for s in post["segments"]
                    if acquisition.product(s["product_id"]).get("cross_section") == "trade_day"
                    and json.dumps(s, sort_keys=True) not in covered_segments
                ]
                if not remaining:
                    break
                phase = {**definition, "segments": remaining, "request": post["request"]}
            _raise_if_cancelled(service, job_id)
            if result["pending"] or result["failures"]:
                reason = result["stop_reason"] or "pending/failed segments"
                raise RuntimeError(f"maintenance limited: {reason}; see update.json")
            limited = any(
                d["missing"] or d["unknown"] or d["unrecoverable"]
                for d in result.get("maintenance", {}).get("domains", [])
            )
            service.finish(
                job_id,
                progress=1.0,
                output=output,
                reason="maintenance: requests completed; content/version limitations remain" if limited else None,
            )
        except _JobCancelled:
            service.finalize_cancel(job_id)
        except Exception as error:
            service.fail(job_id, f"{stage}: {type(error).__name__}: {error}")
        return service.get(job_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HETU 数据 worker")
    parser.add_argument("--root", required=True, help=".hetu/quant 或测试临时根目录")
    selected = parser.add_mutually_exclusive_group()
    selected.add_argument(
        "--once",
        action="store_true",
        help="排空当前队列后退出（默认常驻轮询）",
    )
    selected.add_argument("--job-id", help="只执行指定的持久维护任务")
    args = parser.parse_args(argv)
    root = Path(args.root)
    store = DataTaskStore(root)
    data = DataStore(root)
    try:
        with worker_lock(root):
            if args.job_id:
                job = run_maintenance_job(args.job_id, store, data)
                print(json.dumps(dict(job_id=job.job_id, state=job.state, reason=job.reason), ensure_ascii=False))
                return 0 if job.state == "succeeded" else 2
            recover_stale_jobs(store, kinds=("maintenance",))
            while True:
                pending = queued_job_ids(store, kinds=("maintenance",))
                if not pending:
                    if args.once:
                        return 0
                    time.sleep(1.0)
                    continue
                for job_id in pending:
                    run_maintenance_job(job_id, store, data)
    except (KeyError, ValueError, RuntimeError) as error:
        print(f"worker 未启动：{error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
