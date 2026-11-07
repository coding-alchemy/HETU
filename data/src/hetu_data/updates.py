"""同一DataStore差集上的可审阅分片、受控逐尝试计量及断点恢复。"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import time
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from hetu_data.locking import worker_lock
from hetu_data.models import data_request_from_dict, data_request_to_dict

METERS = (
    "provider_fetch",
    "sdk_calls",
    "upstream_product_requests",
    "network_attempts",
    "auth_calls",
    "retries",
    "rows",
    "quota_bytes",
    "data_fee_cny",
)


def plan_update(request, store, contracts, *, audited_segments=None):
    if audited_segments is None:
        base = store.plan(request)
    else:
        # 已由实际行审计扣除共享覆盖；这里只展开待审批的缺口，不读取未授权新对象。
        # 获取权限和预算仍由execute_update/prepare逐次检查，计划不授予许可。
        for segment in audited_segments:
            pid = segment["product_id"]
            if (
                pid not in request.product_ids
                or not set(segment["codes"]) <= set(request.codes_for(pid))
                or not request.start.isoformat() <= segment["start"] <= segment["end"] <= request.end.isoformat()
            ):
                raise ValueError("audited segment outside requested scope")
            if request.mode == "free" and store.product(pid).get("fee_class") == "paid":
                raise PermissionError("free proposal cannot silently select paid product")
        base = dict(segments=list(audited_segments), reused_coverage=[])
    segments, unresolved = [], []
    counts = dict.fromkeys(METERS, 0)
    counts["quota_bytes"] = None
    for segment in base["segments"]:
        pid = segment["product_id"]
        if "local_cross_section_codes" in segment:
            segments.append({**segment, "upstream": []})
            continue
        contract = contracts.get("products", {}).get(pid)
        if not contract:
            unresolved.append(f"{pid}: interface contract missing")
            continue
        maximum = int(contract.get("max_days", 0))
        if maximum <= 0:
            unresolved.append(f"{pid}: supported splitting is unknown")
            continue
        left, end = date.fromisoformat(segment["start"]), date.fromisoformat(segment["end"])
        while left <= end:
            right = min(end, left + timedelta(days=maximum - 1))
            piece = {**segment, "start": left.isoformat(), "end": right.isoformat()}
            if store.product(pid).get("provider") == "tushare":
                from hetu_data.providers.tushare import calls_for

                piece["upstream"] = [
                    {"api": api, "params": params, "pagination": "none; fail on has_more/row limit"}
                    for api, params in calls_for(pid, tuple(piece["codes"]), left, right)
                ]
                upstream = len(piece["upstream"])
            else:
                upstream = contract.get("upstream_per_fetch")
                if upstream is None:
                    unresolved.append(f"{pid}: upstream expansion unknown")
                    break
            segments.append(piece)
            counts["provider_fetch"] += 1
            counts["upstream_product_requests"] += upstream
            counts["network_attempts"] += upstream
            counts["sdk_calls"] += contract.get("sdk_calls", 0)
            left = right + timedelta(days=1)
    return dict(
        request=data_request_to_dict(request),
        segments=segments,
        reused_coverage=base["reused_coverage"],
        counts=counts,
        limits=dict(contracts.get("limits", {})),
        unresolved=unresolved,
        planning_basis="audited_missing_rows" if audited_segments is not None else "store_query_coverage",
        acquisition_grant_required=any("local_cross_section_codes" not in segment for segment in segments),
    )


def _write(ledger, event):
    with ledger.open("a") as stream:
        stream.write(
            json.dumps({"at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(), **event}, ensure_ascii=False) + "\n"
        )
        stream.flush()
        os.fsync(stream.fileno())


def validate_update_plan(plan):
    if not isinstance(plan, dict):
        raise TypeError("maintenance requires a concrete update plan")
    request = data_request_from_dict(plan["request"])
    for segment in plan["segments"]:
        pid = segment["product_id"]
        if (
            pid not in request.product_ids
            or not set(segment["codes"]) <= set(request.codes_for(pid))
            or not request.start.isoformat() <= segment["start"] <= segment["end"] <= request.end.isoformat()
        ):
            raise ValueError("update segment outside frozen request")
    for name, amount in plan.get("limits", {}).items():
        if name in METERS and (not isinstance(amount, (int, float)) or amount < 0):
            raise ValueError("update limit must be a nonnegative known amount")
        if name == "requests_per_minute" and (type(amount) is not int or not 1 <= amount <= 500):
            raise ValueError("approved requests_per_minute must be an integer from 1 to 500")


def execute_update(plan, store, ledger: Path, *, cancelled, progress):
    with worker_lock(store.root, reentrant=True):
        validate_update_plan(plan)
        if plan["unresolved"] or not plan.get("limits", {}).get("approved_scope_ref"):
            raise ValueError("unresolved contract or missing scope approval")
        ledger = Path(ledger)
        ledger.parent.mkdir(parents=True, exist_ok=True)
        events = [json.loads(x) for x in ledger.read_text().splitlines()] if ledger.exists() else []
        recorded_limits = [e["limits"] for e in events if e["type"] == "approval_limits"]
        if recorded_limits and recorded_limits[0] != plan["limits"]:
            raise PermissionError("approved ledger limits changed; a new explicit approval is required")
        if not recorded_limits:
            _write(ledger, dict(type="approval_limits", limits=plan["limits"]))
        usage = dict.fromkeys(METERS, 0)
        usage["quota_bytes"] = None
        for event in events:
            for name, amount in event.get("usage", {}).items():
                if amount is not None and usage.get(name) is not None:
                    usage[name] += amount
        initial_usage = dict(usage)
        completed = [e["segment"] for e in events if e["type"] == "completed"]
        snapshots = [e["snapshot_id"] for e in events if e["type"] == "completed"]
        failures, pending = [], []
        original_request = data_request_from_dict(plan["request"])
        limits = plan["limits"]
        interval = 60 / limits["requests_per_minute"] if "requests_per_minute" in limits else 2.05
        previous_call = None
        prior_wires = [e for e in events if e.get("usage", {}).get("network_attempts", 0) > 0 and e.get("at")]
        if prior_wires:
            elapsed = max(
                0,
                (
                    datetime.now(ZoneInfo("Asia/Shanghai")) - datetime.fromisoformat(prior_wires[-1]["at"])
                ).total_seconds(),
            )
            previous_call = time.monotonic() - elapsed
        for segment in plan["segments"]:
            # 语义描述用于断点定位，不用批次/哈希/as_of判断既有结果过期。
            identity = {k: segment[k] for k in ("product_id", "codes", "start", "end", "refresh")}
            local_scope = segment.get("local_cross_section_codes")
            if local_scope is not None:
                identity["local_cross_section_codes"] = local_scope
            if identity in completed:
                continue
            if failures and limits.get("stop_on_failure"):
                pending.append({**identity, "reason": "earlier segment failed"})
                continue
            if cancelled():
                pending.append(identity)
                continue
            starts = sum(e["type"] == "started" and e["segment"] == identity for e in events)
            ends = sum(e["type"] in ("failed", "completed") and e["segment"] == identity for e in events)
            saved_responses = [
                e["response"] for e in events
                if e["type"] == "response" and e.get("segment") == identity
                and e["response"].get("failed") is False and e["response"].get("raw_ref")
            ]
            # 此接口每段只有一次产品请求。已落盘的原件可完成本地发布，
            # 即使上次退出时还没写结束事件。校验失败也不允许改走网络。
            resume_empty = (
                segment["product_id"] == "cn_a_status" and len(saved_responses) == 1
                and saved_responses[0].get("api") == "query_history_k_data_plus"
                and saved_responses[0].get("rows") == 0
            )
            if starts > ends and not resume_empty:
                pending.append({**identity, "reason": "previous attempt uncertain; no automatic repeat"})
                continue
            failures_before = [e for e in events if e["type"] == "failed" and e["segment"] == identity]
            had_upstream_attempt = any(
                e.get("usage", {}).get("network_attempts", 0) > 0 for e in events if e.get("segment") == identity
            )
            resume_retry = bool(failures_before and had_upstream_attempt and not resume_empty)
            if resume_retry:
                kind = failures_before[-1]["failure"]["error_type"]
                recoverable = kind in {"ConnectionError", "TimeoutError", "OSError"} or (
                    kind == "InterruptedError"
                    and failures_before[-1]["failure"].get("cancelled_before_request") is True
                )
                if not recoverable or usage["retries"] >= limits.get("retries", 0):
                    pending.append({**identity, "reason": "failed segment retry not authorized or not recoverable"})
                    continue
            req = replace(
                original_request,
                product_ids=(segment["product_id"],),
                codes=tuple(segment["codes"]),
                codes_by_product={},
                start=date.fromisoformat(segment["start"]),
                end=date.fromisoformat(segment["end"]),
                refresh_from=date.fromisoformat(segment["start"]) if segment["refresh"] else None,
            )
            _write(ledger, {"type": "started", "segment": identity})
            adapter = store._provider(store.product(segment["product_id"])["provider"])
            metered_products = getattr(adapter, "metered_products", None)
            metered_transport = (
                hasattr(adapter, "before_request")
                and hasattr(adapter, "on_usage")
                and (metered_products is None or segment["product_id"] in metered_products)
            )
            old_before = getattr(adapter, "before_request", None)
            old_usage = getattr(adapter, "on_usage", None)
            old_resume = getattr(adapter, "resume_responses", [])
            cancelled_before_wire = False

            def reserve(increments, identity=identity):
                for name, increment in increments.items():
                    if increment is None or usage.get(name) is None:
                        continue
                    if name in limits and usage[name] + increment > limits[name]:
                        raise PermissionError(f"{name}: approved limit reached")
                for name, increment in increments.items():
                    if increment is not None and usage.get(name) is not None:
                        usage[name] += increment
                _write(ledger, {"type": "usage", "segment": identity, "usage": increments})
                progress(
                    dict(
                        completed=len(completed),
                        segments=len(plan["segments"]),
                        actual_usage=dict(usage),
                        incremental_usage={
                            k: usage[k] - initial_usage[k] if usage[k] is not None else None for k in METERS
                        },
                    )
                )

            def before(event, old_before=old_before, reserve=reserve, resume_empty=resume_empty):
                nonlocal previous_call, cancelled_before_wire
                if resume_empty:
                    raise PermissionError("saved empty response must recover without upstream requests")
                if cancelled():
                    cancelled_before_wire = True
                    raise InterruptedError("cancelled before upstream request")
                increments = {
                    name: event.get(name, 0)
                    for name in ("sdk_calls", "upstream_product_requests", "network_attempts", "auth_calls", "retries")
                }
                if previous_call is not None:
                    time.sleep(max(0, interval - (time.monotonic() - previous_call)))
                if cancelled():
                    cancelled_before_wire = True
                    raise InterruptedError("cancelled during throttle wait")
                reserve(increments)
                previous_call = time.monotonic()
                if old_before:
                    old_before(event)

            def on_usage(event, identity=identity, old_usage=old_usage, reserve=reserve):
                reserve({"rows": event.get("rows") or 0})
                saved = dict(event)
                if "raw_response" in saved:
                    raw = saved.pop("raw_response")
                    # 失败页也能留存原形；不是覆盖或资格证明。
                    target = ledger.parent / "raw-attempts"
                    target.mkdir(exist_ok=True)
                    filename = f"{len(list(target.glob('*.json'))):08d}.json"
                    (target / filename).write_text(raw["body"])
                    saved["raw_ref"] = str(target / filename)
                    saved["sha256"] = hashlib.sha256(raw["body"].encode("utf-8")).hexdigest()
                    saved["obtained_at"] = raw.get("obtained_at")
                _write(ledger, {"type": "response", "segment": identity, "response": saved})
                if old_usage:
                    old_usage(event)

            adapter.before_request, adapter.on_usage = before, on_usage
            segment_failure_type = None
            try:
                product = store.product(segment["product_id"])
                if local_scope is not None and (
                    product.get("provider") != "tushare" or not product.get("cross_section")
                    or segment["refresh"] or store._cross_section_codes(segment["product_id"]) != local_scope
                ):
                    raise PermissionError("local cross-section scope differs from the frozen plan")
                if hasattr(adapter, "resume_responses"):
                    adapter.resume_responses = []
                    for saved in saved_responses:
                        raw_path = Path(saved["raw_ref"])
                        if not saved.get("sha256") or not saved.get("obtained_at") or not raw_path.exists():
                            raise PermissionError("partial response provenance unknown; no automatic resend")
                        body = raw_path.read_bytes()
                        if hashlib.sha256(body).hexdigest() != saved["sha256"]:
                            raise PermissionError("partial response hash mismatch; no automatic resend")
                        adapter.resume_responses.append(
                            dict(
                                api=saved["api"],
                                params=saved["params"],
                                body=body.decode("utf-8"),
                                obtained_at=saved["obtained_at"],
                                sha256=saved["sha256"],
                            )
                        )
                if not metered_transport:
                    raise PermissionError("per-request upstream metering is not established for this provider")
                if "quota_bytes" in limits:
                    raise PermissionError("source byte-quota unit cannot be reserved before this request")
                if not resume_empty and local_scope is None and any(
                    "local_cross_section_codes" not in s for s in store.plan(req)["segments"]
                ):
                    if resume_retry:
                        reserve({"retries": 1})
                    reserve({"provider_fetch": 1})
                ref = store.prepare(req, allow_acquire=False) if local_scope is not None else store.prepare(req)
                manifest = store.load_manifest(ref)
                proved_empty_status = any(
                    raw.get("query_observation") == "success_empty"
                    for item in manifest["items"] if item["product_id"] == segment["product_id"]
                    for raw in item["raw_files"]
                )
                if manifest.get("failures") or (manifest.get("gaps") and not proved_empty_status):
                    own_failures = [
                        f for f in manifest.get("failures", []) if f.get("product_id") == segment["product_id"]
                    ]
                    if own_failures:
                        segment_failure_type = own_failures[0].get("error", "").partition(":")[0]
                    raise RuntimeError("segment remains failed or incomplete; no completed coverage")
                completed.append(identity)
                snapshots.append(ref.snapshot_id)
                done = {"type": "completed", "segment": identity, "snapshot_id": ref.snapshot_id}
                if proved_empty_status:
                    done["query_observation"] = "success_empty"
                _write(ledger, done)
                progress(
                    {"completed": len(completed), "segments": len(plan["segments"]), "snapshot_id": ref.snapshot_id}
                )
            except Exception as error:
                failure = {
                    **identity,
                    "error_type": segment_failure_type or type(error).__name__,
                    "reason": str(error),
                    "cancelled_before_request": cancelled_before_wire,
                }
                failures.append(failure)
                pending.append(identity)
                _write(ledger, {"type": "failed", "segment": identity, "failure": failure})
            finally:
                adapter.before_request, adapter.on_usage = old_before, old_usage
                if hasattr(adapter, "resume_responses"):
                    adapter.resume_responses = old_resume
        incremental = {key: usage[key] - initial_usage[key] if usage[key] is not None else None for key in METERS}
        return dict(
            snapshot_ids=snapshots,
            completed=completed,
            pending=pending,
            failures=failures,
            actual_usage=usage,
            incremental_usage=incremental,
        )


def maintenance_plan(store, target_date, products, *, mode, contracts):
    """独立维护窗口；仅读登记资产/目标事实，不生成研究或滚动旧截止日。"""
    import pandas as pd
    import pyarrow.parquet as pq

    from hetu_data.archive import _history_objects, _ranges
    from hetu_data.models import DataRequest, SnapshotRef
    from hetu_data.store import DataStore

    settings = contracts.get("maintenance", {})
    start = date.fromisoformat(settings["start"])
    if start > target_date:
        raise ValueError("maintenance start exceeds target")
    history_path = settings.get("history_ref")
    history = (
        pd.DataFrame(settings["history_records"])
        if "history_records" in settings
        else pd.read_csv(history_path, dtype=str, keep_default_na=False)
        if history_path
        else pd.DataFrame()
    )
    query_start = min(
        [start]
        + [
            min(
                start,
                target_date
                - timedelta(days=int(contracts.get("products", {}).get(pid, {}).get("revision_lookback_days", 0)) - 1),
            )
            for pid in products
            if int(contracts.get("products", {}).get(pid, {}).get("revision_lookback_days", 0)) > 0
        ]
    )
    published = store._published_manifests()
    selected = settings.get("coverage_snapshot_ids")
    if selected is not None:
        published = [(sid, m) for sid, m in published if sid in selected]
    sources = [(store, sid, manifest) for sid, manifest in published]
    for reference in settings.get("asset_snapshots", contracts.get("asset_snapshots", ())):
        root = Path(reference["root"])
        if not (root / "snapshots" / reference["snapshot_id"] / "manifest.json").is_file():
            raise FileNotFoundError("registered asset snapshot missing")
        owner = (
            store
            if root.resolve() == store.root.resolve()
            else DataStore(root, config_path=Path(reference["config_path"]) if reference.get("config_path") else None)
        )
        ref = SnapshotRef(reference["snapshot_id"], owner.root)
        manifest = owner.load_manifest(ref)
        if reference.get("tables") is not None:
            manifest = {
                **manifest,
                "table_files": [e for e in manifest.get("table_files", []) if e["table"] in reference["tables"]],
            }
        sources.append((owner, ref.snapshot_id, manifest))
    calendar = {}
    superseded = {}
    for owner, _, manifest in sources:
        for pid, revision in manifest.get("calendar_refresh", {}).items():
            for parent in revision["supersedes"]:
                superseded.setdefault((str(owner.root.resolve()), parent, pid), []).append(revision)
    for owner, sid, manifest in sources:
        entries = [e for e in manifest.get("table_files", []) if e["table"] == "calendar"]
        if entries:
            frame = owner.read_table(SnapshotRef(sid, owner.root), "calendar")
            if "product_id" in frame:
                frame = frame[frame.product_id.isin([e["product_id"] for e in entries])]
            for row in frame.to_dict("records"):
                day = str(row["date"])[:10]
                revisions = superseded.get((str(owner.root.resolve()), sid, row.get("product_id")), [])
                if any(r["start"] <= day <= r["end"] for r in revisions):
                    continue
                calendar.setdefault(day, set()).add(str(row["is_trading_day"]).lower())
    natural = [d.date().isoformat() for d in pd.date_range(query_start, target_date)]
    trading = [d for d in natural if calendar.get(d) in ({"true"}, {"1"})]
    unknown_calendar = [d for d in natural if calendar.get(d) not in ({"true"}, {"false"}, {"1"}, {"0"})]
    coverage_sources = [
        (owner, sid, manifest, owner._coverage_index([(sid, manifest)])) for owner, sid, manifest in sources
    ]
    domains, segments, reused, global_unknown = [], [], [], []
    codes_by_product = {}
    limits = dict(contracts.get("limits", {}))
    counts = dict.fromkeys(METERS, 0)
    counts["quota_bytes"] = None
    for pid in products:
        product = store.product(pid)
        contract = contracts.get("products", {}).get(pid, {})
        basis = contract.get("time_basis")
        lookback = int(contract.get("revision_lookback_days", 0))
        product_start = min(start, target_date - timedelta(days=lookback - 1)) if lookback > 0 else start
        objects = (
            _history_objects(history, product_start.isoformat(), target_date.isoformat())[0] if len(history) else []
        )
        calendar_gaps = [d for d in unknown_calendar if d >= product_start.isoformat()]
        domain = dict(
            product_id=pid,
            start=product_start.isoformat(),
            last_success=None,
            target=target_date.isoformat(),
            time_basis=basis,
            covered=[],
            missing=[],
            refresh=[],
            unrecoverable=list(contract.get("unavailable_revisions", [])),
            successful_empty=[],
            failures=[],
            unknown=[],
            target_codes=[],
            counts={},
        )
        domains.append(domain)
        if not contract or basis not in ("trading_days", "natural_days"):
            domain["unknown"].append(dict(reason="interface_time_contract_missing"))
            continue
        if basis == "trading_days" and calendar_gaps:
            domain["unknown"].append(dict(reason="calendar_unknown", ranges=_ranges(calendar_gaps)))
        if not objects and product.get("requires_codes", True):
            domain["unknown"].append(dict(reason="independent_historical_target_missing"))
            continue
        allowed = []
        for obj in objects:
            if not obj["attributes_known"] or not obj["lifecycle_known"]:
                domain["unknown"].append(dict(code=obj["code"], reason="identity_or_lifecycle_unknown"))
            elif (
                obj["market"] in product.get("markets", ("SH", "SZ", "BJ"))
                and obj["security_type"] == "stock"
                and obj["required"][0] <= obj["required"][1]
            ):
                allowed.append(obj)
        if not product.get("requires_codes", True):
            allowed = [dict(code="", required=[product_start.isoformat(), target_date.isoformat()], market="")]
        domain["target_codes"] = [o["code"] for o in allowed if o["code"]]
        codes_by_product[pid] = tuple(domain["target_codes"])
        table = contract.get("table", product["tables"][0])
        query = DataRequest(
            (pid,),
            tuple(domain["target_codes"]),
            product_start,
            target_date,
            datetime.now(ZoneInfo("Asia/Shanghai")),
            "daily_archive",
            mode=mode,
        )
        try:
            store._check_scope(query, pid, "read")
        except PermissionError as error:
            domain["unknown"].append(dict(reason=str(error)))
            continue
        if product.get("provider") != "tushare":
            domain["unknown"].append(dict(reason="per_request_metering_unproved_for_provider"))
            continue
        frames = []
        fields = {
            "daily_bars": ("open_raw", "high_raw", "low_raw", "close_raw", "volume_shares", "amount_cny"),
            "security_status": ("is_st", "is_suspended", "is_listed"),
        }.get(table, ())
        if basis == "trading_days":
            for owner, sid, manifest in sources:
                entries = [e for e in manifest.get("table_files", []) if e["table"] == table]
                source_codes = list(domain["target_codes"])
                source_start, source_end = product_start, target_date
                for source_pid in {e["product_id"] for e in entries}:
                    if owner.product(source_pid).get("fee_class") == "paid":
                        grant = owner._grant(source_pid) or {}
                        source_codes = [c for c in source_codes if c in grant.get("codes", [])]
                        source_start = max(source_start, date.fromisoformat(grant.get("start", "9999-12-31")))
                        source_end = min(source_end, date.fromisoformat(grant.get("end", "0001-01-01")))
                if entries and source_codes and source_start <= source_end:
                    frames.append(
                        owner.read_range(
                            SnapshotRef(sid, owner.root),
                            table,
                            codes=tuple(source_codes),
                            start=source_start,
                            end=source_end,
                            columns=("code", "date", *fields),
                        )
                    )
        observed = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        invalid_rows = pd.DataFrame()
        if len(observed) and fields:
            if table == "daily_bars":
                numbers = observed[list(fields)].apply(pd.to_numeric, errors="coerce")
                valid = numbers.notna().all(axis=1) & numbers.ge(0).all(axis=1)
                valid &= numbers[list(fields[:4])].gt(0).all(axis=1)
                valid &= numbers.high_raw.ge(numbers[list(fields[:4])].max(axis=1))
                valid &= numbers.low_raw.le(numbers[list(fields[:4])].min(axis=1))
            else:
                valid = (
                    observed[list(fields)]
                    .astype(str)
                    .apply(lambda c: c.str.lower().isin(["true", "false", "0", "1"]))
                    .all(axis=1)
                )
            invalid_rows = observed[~valid]
            observed = observed[valid]
            for code, own in invalid_rows.groupby("code"):
                domain["unknown"].append(
                    dict(code=code, reason="invalid_or_unknown_rows", dates=sorted(set(own.date.astype(str))))
                )
        actual_dates = observed.date.astype(str).tolist() if "date" in observed else []
        if actual_dates:
            domain["last_success"] = max(actual_dates)
        pieces = []
        for obj in allowed:
            code = obj["code"]
            if basis == "trading_days" and obj["market"] and obj["market"] not in settings.get("calendar_markets", []):
                domain["unknown"].append(dict(code=code, reason="market_calendar_contract_unknown"))
                continue
            # 发布/修订可能发生在退出后的周末：保留窗口内存续对象的完整自然日查询。
            days = [
                d
                for d in (trading if basis == "trading_days" else natural)
                if d >= product_start.isoformat()
                and (basis == "natural_days" or obj["required"][0] <= d <= obj["required"][1])
            ]
            proven, empty_days = set(), set()
            for owner, source_id, manifest, coverage_index in coverage_sources:
                for key, entries in coverage_index.get(pid, {}).items():
                    if code and not key.startswith(f"code={code}|") and key != f"code={code}":
                        continue
                    for sid, rel, _, _, cov in entries:
                        if not cov:
                            continue
                        span = {d for d in days if cov[0] <= d <= cov[1]}
                        if owner.product(pid).get("fee_class") == "paid":
                            grant = owner._grant(pid) or {}
                            if code and code not in grant.get("codes", []):
                                continue
                            span = {d for d in span if grant.get("start", "~") <= d <= grant.get("end", "")}
                        if not span:
                            continue
                        owner._check_scope(
                            replace(
                                query,
                                codes=(code,) if code else (),
                                start=date.fromisoformat(min(span)),
                                end=date.fromisoformat(max(span)),
                            ),
                            pid,
                            "read",
                        )
                        source = owner._verified_source(sid, rel, {source_id: manifest})
                        if pq.ParquetFile(source).metadata.num_rows == 0:
                            empty_days.update(span)
                        if basis == "natural_days":
                            proven.update(span)
                            if span:
                                domain["last_success"] = max(domain["last_success"] or "", max(span))
            if basis == "trading_days" and len(observed):
                own = observed[observed.code.eq(code)] if "code" in observed else observed
                proven = set(own.date.astype(str)) & set(days)
            if table == "calendar":
                proven = {d for d in days if calendar.get(d) in ({"true"}, {"false"}, {"1"}, {"0"})}
                if proven:
                    domain["last_success"] = max(proven)
            missing = set(days) - proven

            def spans(values, code=code, days=days):
                return [dict(code=code, start=a, end=b) for a, b in _ranges(values, days)]

            domain["covered"].extend(spans(proven))
            domain["missing"].extend(spans(missing))
            domain["successful_empty"].extend(spans(empty_days))
            if basis == "trading_days" and missing & empty_days:
                domain["unknown"].append(
                    dict(
                        code=code,
                        reason="successful_empty_not_content_coverage",
                        ranges=_ranges(missing & empty_days, days),
                    )
                )
            refresh_days = set()
            if lookback > 0:
                refresh_days = {d for d in days if d >= (target_date - timedelta(days=lookback - 1)).isoformat()}
                domain["refresh"].extend(spans(refresh_days))
            for a, b in _ranges((missing - empty_days) | refresh_days, days):
                pieces.append(
                    dict(
                        product_id=pid,
                        codes=[code] if code else [],
                        start=a,
                        end=b,
                        refresh=bool(code) or bool(refresh_days),
                    )
                )
        sub = plan_update(query, store, contracts, audited_segments=pieces)
        domain["unknown"].extend(dict(reason=r) for r in sub["unresolved"])
        if sub["unresolved"]:
            continue
        try:
            if sub["segments"]:
                store._check_scope(query, pid, "acquire")
        except PermissionError as error:
            domain["unknown"].append(dict(reason=str(error)))
            continue
        domain["counts"] = sub["counts"]
        segments.extend(sub["segments"])
        reused.extend(domain["covered"])
        for key, value in sub["counts"].items():
            if value is not None and counts[key] is not None:
                counts[key] += value
        domain["failures"] = [f for _, m in published for f in m.get("failures", []) if f.get("product_id") == pid]
        if contract.get("historical_revisions") is False:
            domain["version_limitation"] = "source_does_not_prove_complete_historical_versions"
    request = DataRequest(
        tuple(products),
        tuple(dict.fromkeys(c for cs in codes_by_product.values() for c in cs)),
        query_start,
        target_date,
        datetime.now(ZoneInfo("Asia/Shanghai")),
        "daily_archive",
        mode=mode,
        codes_by_product=codes_by_product,
    )
    frozen_contracts = copy.deepcopy(contracts)
    frozen_contracts.setdefault("maintenance", {})["history_records"] = history.to_dict("records")
    return dict(
        request=data_request_to_dict(request),
        start=start.isoformat(),
        query_start=query_start.isoformat(),
        target=target_date.isoformat(),
        domains=domains,
        segments=segments,
        reused_coverage=reused,
        counts=counts,
        limits=limits,
        unresolved=global_unknown,
        acquisition_grant_required=True,
        planning_basis="maintenance_actual_rows_and_query_evidence",
        target_scope=settings.get("target_scope", {}),
        maintenance_contracts=frozen_contracts,
    )
