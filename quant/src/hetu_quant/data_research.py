"""原八定义与独立目标审计；资格复用默认只引用，不生成策略信号。"""

from __future__ import annotations

import importlib.util
import json
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from hetu_quant import backtest_data
from hetu_quant.account_evidence import account_dispositions
from hetu_quant.backtest_data import (
    _evidence_original_ok,
    _proven_month_ends,
    _signal_days,
    _truthy,
    _warmup_start,
    select_known_versions,
)
from hetu_quant.data import PURPOSE_TABLES, DataStore, SnapshotRef, SnapshotTamperedError, _action_query_records
from hetu_quant.data_archive import (
    _coverage_rows,
    _history_objects,
    _ranges,
    _source_rows,
    audit_archive,
    scan_local_coverage,
)

CN = ZoneInfo("Asia/Shanghai")
_ACCOUNT_ACTION_FIELDS = {
    "cash_dividend": ("record_date", "pay_date"),
    "bonus_split": ("record_date", "stock_listing_date"),
}
_ACTION_QUERY_GAP = "operate_ex_date_coverage_unproved"


def _read_delivery_file(directory: Path | None, name: str, legacy: str = ""):
    if directory is None:
        return {}
    for filename in filter(None, (name, legacy)):
        path = directory / filename
        if path.is_file():
            if path.suffix == ".jsonl":
                with path.open(encoding="utf-8") as stream:
                    return [json.loads(line) for line in stream if line.strip()]
            return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _data_ref(reference, data_root, base):
    path = Path(reference)
    if path.parts[:2] == (".hetu", "quant"):
        return data_root.joinpath(*path.parts[2:])
    return base / path


def _delivery_evidence(directory: Path | None, data_root: Path):
    requirements = _read_delivery_file(directory, "requirements.json", "research-requirements.json")
    coverage = _read_delivery_file(directory, "coverage.json", "research-audit-summary.json")
    if "coverage" not in coverage:
        coverage["coverage"] = _read_delivery_file(directory, "research-coverage.jsonl") or []
    if directory is not None and "signal_evidence" not in coverage:
        by_coordinate = {}
        base = coverage.get("old_signal_evidence_reused")
        if base:
            original = _data_ref(base, data_root, directory)
            for item in _read_delivery_file(original.parent, original.name) or []:
                by_coordinate[item["code"], item["date"]] = item
        for key in ("signal_evidence_delta", "B04_signal_evidence_delta"):
            if coverage.get(key):
                for item in _read_delivery_file(directory, coverage[key]) or []:
                    by_coordinate[item["code"], item["date"]] = item
        coverage["signal_evidence"] = list(by_coordinate.values())
    return requirements, coverage


def _licensed_account_audit(payload, data_root, handoff, requirements, definitions):
    definition = payload["definition"]
    snapshot = definition["snapshot_id"]
    mode = definition["mode"]
    path = data_root / "snapshots" / snapshot / "manifest.json"
    if (handoff.get("study_id") != payload["study_id"] or handoff.get("snapshot_id") != snapshot
            or handoff.get("mode") != mode or handoff.get("supplemented_snapshots")
            or snapshot in ("", ".", "..") or Path(snapshot).name != snapshot or not path.is_file()):
        return {}, "research_input_not_bound"
    purposes = {backtest_data.STRATEGY_PURPOSES.get(d["definition"]["strategy_id"]) for d in definitions}
    if None in purposes:
        return {}, "research_input_use_unproved"
    tables = {table for purpose in purposes for table in PURPOSE_TABLES[purpose]}
    try:
        store = DataStore(data_root)
        ref = SnapshotRef(snapshot, data_root)
        manifest = store.load_manifest(ref)
        if manifest.get("snapshot_id") != snapshot or manifest.get("mode") != mode:
            return {}, "research_input_not_bound"
        available = {entry["table"] for entry in manifest.get("table_files", [])}
        if not tables <= available:
            return {}, "account_tables_missing"
        account_dispositions = {"terminal_disposals", "share_exchanges"}
        tables.update(account_dispositions & available)
        entries = [entry for entry in _source_rows(store, ref, ()) if entry[3]["table"] in tables]
        products = {entry[3]["product_id"] for entry in entries}
        as_of = pd.Timestamp(definition["as_of"])
        for product_id in products:
            product = store.product(product_id)
            store.check_access(mode, product, operation="read")
            sources = [item["source"] for item in manifest.get("items", []) if item["product_id"] == product_id]
            if not sources or any(
                source.get("fee_class") != product.get("fee_class")
                or not pd.Timestamp(source["obtained_at"]) <= as_of
                for source in sources
            ):
                return {}, "account_source_unproved"
            product_permission = (store._grant(product_id) if product["fee_class"] == "paid"
                                  else product.get("permission")) or {}
            permitted = [record["allowed_uses"] for record in [product_permission, *sources]
                         if "allowed_uses" in record]
            used_tables = {entry[3]["table"] for entry in entries if entry[3]["product_id"] == product_id}
            required_uses = {purpose for purpose in purposes if used_tables.intersection(PURPOSE_TABLES[purpose])}
            if account_dispositions & used_tables:
                required_uses.update(purposes)
            if not permitted or any(not isinstance(uses, list) or not required_uses.issubset(uses)
                                    for uses in permitted):
                return {}, "research_input_use_unproved"
        snapshot_dir = path.parent.resolve()
        for entry in (*manifest["table_files"],
                      *(raw for item in manifest["items"] for raw in item.get("raw_files", []))):
            if not (snapshot_dir / entry["path"]).resolve().is_relative_to(snapshot_dir):
                return {}, "account_source_unproved"
        store._verify_hashes(ref, manifest)
        inventory = scan_local_coverage(store, ref, _entries=entries)
        if inventory.failures:
            return {}, "account_source_unproved"
        result = audit_research_coverage(requirements, store, ref, inventory=inventory)
        result["identity_renames"] = list(backtest_data._account_renames(
            requirements["objects"], inventory.calendar, inventory.frames["historical_securities"],
            inventory.frames["knowledge_evidence"], store.root, as_of,
        ))
        result["calendar"] = dict(zip(inventory.calendar.date.astype(str),
                                      inventory.calendar.is_trading_day.map(_truthy), strict=True))
        actions = inventory.frames.get("corporate_actions", pd.DataFrame())
        result["actions_by_code"] = (
            {str(code): frame for code, frame in actions.groupby("code")} if len(actions) else {}
        )
        return result, None
    except (OSError, ValueError, TypeError, KeyError, SnapshotTamperedError):
        return {}, "account_source_unproved"


def _observed_points(record, dates, *, allow_suspended=False):
    if not dates or not record:
        return False
    covered = record.get("covered", ())
    gaps = (*record.get("data_gaps", ()), *record.get("unknown_ranges", ()))
    if record.get("domain") == "daily_bars":
        suspended = tuple(record.get("suspended", ()))
        if allow_suspended:
            covered = (*covered, *suspended)
        else:
            gaps += suspended
    return all(
        any(start <= day <= end for start, end in covered)
        and not any(start <= day <= end for start, end in gaps)
        for day in dates
    )


def _observed_span(record, start, end):
    if any(left <= end and start <= right
           for left, right in (*record.get("data_gaps", ()), *record.get("unknown_ranges", ()))):
        return False
    cursor = start
    for left, right in sorted(record.get("covered", ())):
        if right < cursor:
            continue
        if left > cursor:
            return False
        if right >= end:
            return True
        cursor = (date.fromisoformat(right) + timedelta(days=1)).isoformat()
    return False


def _action_query_intervals(entries, start, end):
    """取实施日查询已证区间及其在所需期间的补集，均含首尾日。"""
    begin, stop = date.fromisoformat(start), date.fromisoformat(end)
    day = timedelta(days=1)
    spans = sorted(
        (max(begin, date.fromisoformat(query["start"])), min(stop, date.fromisoformat(query["end"])))
        for entry in entries for query in _action_query_records(entry) if query.get("basis") == "ex_date"
    )
    covered = []
    for left, right in spans:
        if left > right:
            continue
        if covered and left <= covered[-1][1] + day:
            covered[-1][1] = max(covered[-1][1], right)
        else:
            covered.append([left, right])
    unknown = []
    cursor = begin
    for left, right in covered:
        if cursor < left:
            unknown.append([cursor, left - day])
        cursor = right + day
    if cursor <= stop:
        unknown.append([cursor, stop])
    return (
        [[left.isoformat(), right.isoformat()] for left, right in covered],
        [[left.isoformat(), right.isoformat()] for left, right in unknown],
    )


def _window_gap(field, evidence, anchors, active_start, active_end, active_days):
    if field == "corporate_actions":
        return None if _observed_span(evidence, active_start, active_end) else "coverage_gap"
    if field == "daily_bars" and anchors and not _observed_points(evidence, anchors):
        if any(left <= point <= right for left, right in evidence.get("suspended", ()) for point in anchors):
            return "suspended_month_end_without_price"
        return "coverage_gap"
    if active_days and not _observed_points(evidence, active_days, allow_suspended=field == "daily_bars"):
        return "coverage_gap"
    return None


def _account_chain(obj, peers, proved_renames, signal):
    chain = sorted([obj, *peers], key=lambda segment: segment["valid_from"])
    outgoing = {}
    for old, new in zip(chain, chain[1:], strict=False):
        event = proved_renames.get((old["code"], new["code"]))
        if event is None:
            return None, {}
        if new["valid_from"] <= signal and not backtest_data._public_at(
            event["published_at"], datetime.fromisoformat(backtest_data._signal_cutoff(signal))
        ):
            return None, {}
        outgoing[old["code"]] = event
    return chain, outgoing


def _read_prior_qualifications(prior, data_root, directory, requested_days):
    citations, facts = [], []
    if not directory or not isinstance(prior, dict):
        return citations, facts
    sources = {}
    for reference in prior.get("refs", []):
        path = _data_ref(reference, data_root, directory).resolve()
        if not path.is_relative_to(data_root.resolve()) or not path.is_file():
            citations.append(dict(source_ref=reference, status="original_unavailable"))
            continue
        try:
            report = _read_delivery_file(path.parent, path.name)
        except (OSError, ValueError):
            citations.append(dict(source_ref=str(path), status="original_unreadable"))
            continue
        details = dict(source_ref=str(path), snapshot_id=report.get("snapshot_id"), signal_rows=0)
        citations.append(details)
        root = _data_ref(report.get("root") or "qualification-store", data_root, path.parent).resolve()
        for result in report.get("results", report.get("qualifications", [])):
            if not result.get("bundle"):
                continue  # A report summary alone has no security/date coordinates.
            bundle = _data_ref(result["bundle"], data_root, path.parent).resolve()
            if not bundle.is_relative_to(data_root.resolve()):
                continue
            definition = _read_delivery_file(bundle, "definition.json")
            qual = _read_delivery_file(bundle, "qualification.json")
            snapshot = definition.get("snapshot_id")
            purpose = backtest_data.STRATEGY_PURPOSES.get(definition.get("strategy_id"))
            if (not snapshot or Path(snapshot).name != snapshot or snapshot in (".", "..")
                    or qual.get("snapshot_id") != snapshot or not qual.get("can_run")
                    or purpose not in qual.get("allowed_uses", [])
                    or qual.get("purpose") != purpose or not root.is_relative_to(data_root.resolve())):
                continue
            if not (root / "snapshots" / snapshot / "manifest.json").is_file():
                continue
            source_key = (root, snapshot)
            try:
                if source_key not in sources:
                    store = DataStore(root)
                    ref = SnapshotRef(snapshot, root)
                    manifest = store.load_manifest(ref)
                    if manifest.get("mode") != definition.get("mode"):
                        continue
                    sources[source_key] = [store.read_table(ref, name) for name in
                                           ("historical_securities", "knowledge_evidence", "market_cap")]
                history, knowledge, caps = sources[source_key]
                roster = pd.read_parquet(bundle / "universe.parquet", columns=["code", "signal_date"])
            except (OSError, ValueError, TypeError, KeyError, SnapshotTamperedError):
                continue
            for code, day in roster.drop_duplicates().itertuples(index=False, name=None):
                if day not in requested_days:
                    continue
                at = datetime.fromisoformat(backtest_data._signal_cutoff(day))
                own_history = history[history["code"].eq(code)]
                own_evidence = knowledge[knowledge["record_key"].eq(code)].assign(evidence_root=str(root))
                versions = _known_history(own_history, own_evidence, at)
                capital = backtest_data.select_signal_market_cap(caps, knowledge, code, day, root)
                facts.append(dict(
                    code=code, date=day, cutoff=at.isoformat(), snapshot_id=snapshot, source_ref=str(path),
                    universe_ref=str(bundle / "universe.parquet"),
                    history_revision=versions.revision_id.tolist() if len(versions) else [],
                    market_cap_known=capital is not None,
                    market_cap_cny=capital["market_cap_cny"] if capital else None,
                    capital_revision=capital.get("revision_id") if capital else None,
                    market_cap_source_ref=capital.get("evidence_ref") if capital else None,
                ))
                details["signal_rows"] += 1
        details["status"] = "row_facts_checked_at_close" if details["signal_rows"] else "summary_only"
    return citations, facts


def qualify_frozen_trials(payload: dict, data_root: Path, delivery_dir: Path | None, trials: list[dict]) -> dict:
    """Read existing observations using each trial's own signal window and warm-up."""
    return _qualify_trial_inputs(payload, data_root, delivery_dir, trials, _definitions(payload))


def _possibly_active_in(obj, start, end):
    return not obj.get("lifecycle_known") or (
        obj["valid_from"] <= end and (not obj.get("valid_to") or obj["valid_to"] >= start)
    )


def _pre_study_exit_indices(objects, calendar, required_start, definition):
    month_ends = {day[:7]: day for day in _proven_month_ends(calendar)}
    first_month = pd.Period(definition["start"], freq="M") - 1
    first_signal = month_ends.get(str(first_month), first_month.start_time.date().isoformat())
    earliest_anchor = month_ends.get(required_start[:7], required_start)
    study_codes = defaultdict(set)
    for obj in objects:
        could_be_ordinary_stock = not obj["attributes_known"] or (
            obj["market"] in ("SH", "SZ") and obj["security_type"] == "stock"
        )
        if could_be_ordinary_stock and _possibly_active_in(obj, first_signal, definition["end"]):
            study_codes[obj["identity_id"]].add(obj["code"])
    return {
        index for index, obj in enumerate(objects)
        if obj["lifecycle_known"] and obj["valid_to"] and obj["valid_to"] < first_signal
        and (obj["valid_to"] < earliest_anchor or not study_codes[obj["identity_id"]] - {obj["code"]})
    }


def _restore_pre_study_periods(requirements, calendar, definition):
    """按实际信号起点重读旧版排除标记，保持交接原件只读。"""
    legal = requirements.get("legal_exclusions", [])
    pre_study = {(row["identity_id"], row["code"]) for row in legal if row["reason"] == "pre_study_exit"}
    if not pre_study:
        return requirements, set()
    original = requirements["objects"]
    confirmed = _pre_study_exit_indices(original, calendar, requirements["start"], definition)
    objects, restored = [], set()
    for index, obj in enumerate(original):
        key = (obj["identity_id"], obj["code"])
        if obj.get("excluded") and key in pre_study and index not in confirmed:
            obj = dict(obj, excluded=False)
            restored.add(key)
        objects.append(obj)
    return dict(
        requirements,
        objects=objects,
        legal_exclusions=[row for row in legal
                          if row["reason"] != "pre_study_exit" or (row["identity_id"], row["code"]) not in restored],
    ), {code for _, code in restored}


def _legacy_action_available_at(event, actions_by_code) -> str | None:
    actions = actions_by_code.get(event.get("code"))
    sources = event.get("source_refs")
    if actions is None or not sources:
        return None
    timestamps = actions.loc[
        actions["ex_date"].eq(event.get("date"))
        & actions["action_type"].eq(event.get("action_type"))
        & actions["source_ref"].isin(sources),
        "available_at",
    ].drop_duplicates()
    if len(timestamps) == 1 and pd.notna(timestamps.iloc[0]):
        return str(timestamps.iloc[0])
    return None


def _qualify_trial_inputs(payload, data_root, delivery_dir, trials, definitions):
    requirements, audit = _delivery_evidence(delivery_dir, data_root)
    handoff = _read_delivery_file(delivery_dir, "handoff.json")
    prior = _read_delivery_file(delivery_dir, "qualifications.json", "qualification-reuse.json")
    original = payload["definition"]
    prior_checks, prior_facts = _read_prior_qualifications(
        prior, data_root, delivery_dir, set(requirements.get("signal_dates", []))
    )
    prior_by_day = defaultdict(list)
    for fact in prior_facts:
        prior_by_day[fact["code"], fact["date"]].append(fact)
    scope = requirements.get("target_scope", {})
    calendar_days = pd.Index(requirements.get("calendar_days", []))
    calendar = pd.DataFrame({
        "date": calendar_days,
        "is_trading_day": calendar_days.isin(requirements.get("expected_sessions", [])),
    })
    requirements, restored_codes = _restore_pre_study_periods(requirements, calendar, original)
    account_audit, permission_gap = _licensed_account_audit(payload, data_root, handoff, requirements, definitions)
    observed_calendar_days = set(calendar_days)
    calendar_counts = pd.Series(sorted(observed_calendar_days), dtype=str).str[:7].value_counts()
    ends = {
        day[:7]: day for day in _proven_month_ends(calendar)
        if calendar_counts.get(day[:7], 0) == pd.Period(day, freq="M").days_in_month
    }
    sessions = sorted(requirements.get("expected_sessions", []))
    session_set = set(sessions)
    domains = {(row["code"], row["domain"]): row for row in audit.get("coverage", [])}
    signals = {(row["code"], row["date"]): row for row in audit.get("signal_evidence", [])}
    account_domains = {(row["code"], row["domain"]): row for row in account_audit.get("coverage", [])}
    account_signals = {(row["code"], row["date"]): row for row in account_audit.get("signal_evidence", [])}
    account_actions = account_audit.get("actions_by_code", {})
    roster_ref = str(scope.get("source_ref") or "")
    roster = (data_root / roster_ref).resolve()
    proof = {}
    if (scope.get("complete") is True and roster_ref and roster.is_relative_to(data_root.resolve())
            and roster.is_file() and backtest_data._original_roster_matches(roster, requirements["objects"])):
        proof = {(event["from_code"], event["code"]): event
                 for event in account_audit.get("identity_renames", [])}
    outgoing_proof = {event["from_code"]: event for event in proof.values()}
    for key, evidence in account_domains.items():
        if key[0] in restored_codes:
            domains.setdefault(key, evidence)
    objects = [row for row in requirements.get("objects", []) if not row.get("excluded")]
    periods_by_identity = defaultdict(list)
    for obj in objects:
        if obj.get("identity_id"):
            periods_by_identity[obj["identity_id"]].append(obj)
    events = defaultdict(list)
    event_doc = (
        "coverage.json" if delivery_dir and (delivery_dir / "coverage.json").is_file()
        else "research-audit-summary.json"
    )
    for index, event in enumerate(audit.get("event_limitations", [])):
        if (event["reason"] == "unsupported_or_missing_event_fields" and not event.get("available_at")
                and event.get("action_type") not in backtest_data._ACTION_RETURN_FIELDS):
            available = _legacy_action_available_at(event, account_actions)
            if available:
                event = {**event, "available_at": available}
        sources = event.get("source_refs") or [f"{delivery_dir / event_doc}#event_limitations/{index}"]
        events[event.get("code")].append((event, sources, False))
    for event in account_audit.get("event_limitations", []):
        events[event.get("code")].append((event, event.get("source_refs") or [original["snapshot_id"]], True))
    windows = {}
    fields = {}
    def record_gap(obj, field, reason, sources, month, warmup, account_start, *, action="补证", record=None):
        code = obj.get("code")
        key = (obj.get("identity_id"), code, field, reason)
        source_refs = list(dict.fromkeys(x for x in sources if x))
        if record and record.get("date"):
            key += (record["date"], record.get("action_type"), tuple(sorted(record.get("missing_fields", ()))),
                    tuple(sorted(source_refs)), record.get("available_at"))
        if record and record.get("signal_date"):
            key += (record["signal_date"], tuple(record["required"]))
        if key not in fields:
            fields[key] = dict(
                identity_id=key[0], code=code, field=field, reason=reason, disposition=action,
                source_refs=source_refs, windows=set(),
                intervals={k: record[k] for k in ("required", "data_gaps", "unknown_ranges", "suspended", "date",
                                                   "signal_date", "action_type", "missing_fields", "available_at",
                                                   "effective_date", "terms", "event_ids", "expected_originals")
                           if record and k in record},
            )
        fields[key]["windows"].add((month, warmup, account_start))

    for row in definitions:
        definition = row["definition"]
        warmup = definition["protocol"]["warmup_months"]
        first = pd.Period(definition["start"], freq="M") - 1
        last = pd.Period(definition["end"], freq="M")
        if definition["end"] < last.end_time.date().isoformat() and ends.get(str(last), "9999") > definition["end"]:
            last -= 1
        for period in pd.period_range(first, last, freq="M"):
            month = str(period)
            key = (month, warmup, definition["start"])
            if key in windows:
                windows[key]["trial_ids"].append(row["trial_id"])
                continue
            day = ends.get(month)
            anchors = [ends.get(str(at)) for at in pd.period_range(period - warmup, period, freq="M")]
            first_anchor = anchors[0]
            window_start = first_anchor or (period - warmup).start_time.date().isoformat()
            calendar_complete = all(anchors)
            member_ids, excluded_ids, verified_ids, unknown_ids = set(), set(), set(), set()
            verified_codes = set()
            pool_proved = scope.get("complete") is True and day is not None
            if not pool_proved:
                record_gap({}, "historical_universe", "independent_pool_unproved", [scope.get("source_ref")], *key)
            if not calendar_complete:
                record_gap({}, "calendar", "month_end_unproved", [], *key)
            if permission_gap:
                record_gap({}, "permission", permission_gap, [], *key, action="补许可")
            if day:
                next_signal = ends.get(str(period + 1), original["end"])
                end_of_holding = min(next_signal, definition["end"])
                account_begin = max(bisect_right(sessions, day), bisect_left(sessions, definition["start"]))
                account_days = sessions[account_begin:bisect_right(sessions, end_of_holding)]
                holding_calendar = pd.date_range(day, end_of_holding).strftime("%Y-%m-%d")
                holding_complete = observed_calendar_days.issuperset(holding_calendar)
                if not holding_complete:
                    record_gap({}, "calendar", "account_calendar_incomplete", [], *key)
                if account_audit and calendar_complete:
                    locked_calendar = account_audit["calendar"]
                    scoped_calendar = pd.date_range(date.fromisoformat(first_anchor).replace(day=1), end_of_holding)
                    if any(locked_calendar.get(d) != (d in session_set)
                           for d in scoped_calendar.strftime("%Y-%m-%d")):
                        record_gap({}, "calendar", "account_snapshot_calendar_mismatch",
                                   [original["snapshot_id"]], *key)
                        holding_complete = False
                for obj in objects:
                    code = obj["code"]
                    identity = obj["identity_id"]
                    if not _possibly_active_in(obj, window_start, end_of_holding):
                        continue
                    lifecycle_known = obj.get("lifecycle_known")
                    if not lifecycle_known or not obj.get("attributes_known"):
                        unknown_ids.add(identity)
                        reason = "security_attributes_unproved" if lifecycle_known else "identity_or_lifecycle_unproved"
                        evidence = obj
                        if lifecycle_known:
                            missing = [
                                field for field in ("market", "security_type")
                                if str(obj.get(field) or "").strip().lower() in ("", "unknown")
                            ]
                            evidence = dict(obj, missing_fields=missing)
                        record_gap(obj, "historical_universe", reason, [obj.get("source_ref")], *key, record=evidence)
                        continue
                    start, end = obj["valid_from"], obj.get("valid_to")
                    if start > day or (end and end < day):
                        continue
                    member_ids.add(identity)
                    # 接续判断使用持有窗口的终点，原代码自身退市日可能早于后续代码生效日。
                    peers = [peer for peer in periods_by_identity[identity] if peer["code"] != code
                             and _possibly_active_in(peer, window_start, end_of_holding)]
                    chain, outgoing = _account_chain(obj, peers, proof, day)
                    if chain is None:
                        record_gap(obj, "identity_transition", "identity_transition_unproved",
                                   [obj.get("source_ref"), *(peer.get("source_ref") for peer in peers)],
                                   *key)
                    observed = signals.get((code, day), {})
                    account_fact = account_signals.get((code, day), {})
                    reused = prior_by_day.get((code, day), ())
                    if account_audit:
                        history_known = bool(account_fact.get("history_revision")) and (
                            account_fact.get("history_valid_from") == start
                        )
                        cap_known = account_fact.get("market_cap_known") is True
                    else:
                        exact = observed.get("cutoff") == backtest_data._signal_cutoff(day)
                        history_known = (exact and bool(observed.get("history_revision"))) or any(
                            fact["history_revision"] for fact in reused
                        )
                        cap_known = any(fact["market_cap_known"] for fact in reused)
                    if not history_known:
                        record_gap(obj, "historical_public_evidence", "close_version_unproved",
                                   [obj.get("source_ref")], *key)
                    if first_anchor and chain and chain[0]["valid_from"] > first_anchor and history_known:
                        excluded_ids.add(identity)
                        continue
                    qualified = history_known and chain is not None
                    if not cap_known:
                        record_gap(obj, "market_cap", "close_version_unproved", [obj.get("source_ref")], *key)
                        qualified = False
                    if calendar_complete:
                        for segment in chain or [obj]:
                            code = segment["code"]
                            active_start = max(first_anchor, segment["valid_from"])
                            active_end = min(segment["valid_to"] or end_of_holding, end_of_holding)
                            if active_start > active_end:
                                continue
                            active_days = account_days[
                                bisect_left(account_days, active_start):bisect_right(account_days, active_end)
                            ]
                            segment_anchors = [point for point in anchors if active_start <= point <= active_end]
                            incoming = next((event for event in outgoing.values() if event["code"] == code), None)
                            last_session = bisect_right(sessions, active_end)
                            for boundary in (
                                incoming["effective_date"] if incoming else "",
                                sessions[last_session - 1] if code in outgoing and last_session else "",
                            ):
                                if active_start <= boundary <= active_end:
                                    segment_anchors.append(boundary)
                            for field in ("daily_bars", "security_status", "corporate_actions", "trading_rules"):
                                evidence = domains.get((code, field), {})
                                segment_window = (segment_anchors, active_start, active_end, active_days)
                                reason = _window_gap(field, evidence, *segment_window)
                                if reason:
                                    record_gap(segment, field, reason, evidence.get("source_refs", ()), *key,
                                               record=evidence)
                                    qualified = False
                                if account_audit:
                                    locked = account_domains.get((code, field), {})
                                    if _window_gap(field, locked, *segment_window):
                                        record_gap(segment, field, "account_snapshot_coverage_gap",
                                                   locked.get("source_refs", ()), *key, record=locked)
                                        qualified = False
                            if code in account_actions and active_start <= day:
                                window_actions = backtest_data._segment_rows(
                                    account_actions, [(code, active_start, active_end)], "ex_date", pd.DataFrame()
                                )
                                _, action_gaps = backtest_data._signal_action_inputs(
                                    window_actions, first_anchor, day
                                )
                                for gap in action_gaps:
                                    sources = gap.get("source_refs") or [gap["source_ref"] or original["snapshot_id"]]
                                    record_gap(segment, gap["field"], gap["reason"], sources, *key, record={
                                        "date": gap["action_date"], "signal_date": gap["date"],
                                        "action_type": gap["action_type"], "missing_fields": gap["missing_fields"],
                                        "required": gap["required"],
                                    })
                                    qualified = False
                            for event, sources, from_account in events[code]:
                                event_date = event.get("date")
                                if event_date and not active_start <= event_date <= active_end:
                                    continue
                                if event["domain"] == "terminal_disposals" and (
                                    (segment["valid_to"] and segment["valid_to"] > active_end)
                                    or (code in outgoing_proof and event["reason"] == "exit_disposal_unproved"
                                        and event_date == outgoing_proof[code]["from_valid_to"])
                                ):
                                    continue
                                if event["reason"] == _ACTION_QUERY_GAP:
                                    gaps = event.get("unknown_ranges")
                                    if gaps is None:
                                        action_proof = domains.get((code, "corporate_actions"), {})
                                        gaps = action_proof.get("unknown_ranges", [event["required"]])
                                    if not any(left <= active_end and active_start <= right for left, right in gaps):
                                        continue
                                if event_date and event_date < definition["start"]:
                                    action_type = event.get("action_type")
                                    if event["reason"] == "unsupported_or_missing_event_fields":
                                        # 预热收益只使用起始收盘之后、信号收盘之前或当天的行动。
                                        # 账户快照的原行动已由 _signal_action_inputs 核验。
                                        if from_account or not first_anchor < event_date <= day:
                                            continue
                                        available = event.get("available_at")
                                        if available and action_type not in backtest_data._ACTION_RETURN_FIELDS:
                                            _, late = backtest_data._action_availability(pd.Series([available]), day)
                                            if late.iat[0]:
                                                continue
                                    missing = event.get("missing_fields", [])
                                    account_fields = _ACCOUNT_ACTION_FIELDS.get(action_type, ())
                                    warmup_missing = [field for field in missing if field not in account_fields]
                                    if missing and not warmup_missing:
                                        continue
                                    if warmup_missing != missing:
                                        event = {**event, "missing_fields": warmup_missing}
                                record_gap(segment, event["domain"], event["reason"], sources,
                                           *key, record=event)
                                qualified = False
                    if qualified and not permission_gap and calendar_complete and holding_complete:
                        verified_ids.add(identity)
                        verified_codes.add(obj["code"])
                    else:
                        unknown_ids.add(identity)
            windows[key] = dict(
                signal_month=month, signal_date=day, warmup_months=warmup, required_month_ends=anchors,
                account_start=definition["start"],
                trial_ids=[row["trial_id"]], universe_count=len(member_ids) if pool_proved and not
                (unknown_ids - member_ids) else None,
                roster_source=scope.get("source_ref"), known_roster_count=len(member_ids),
                warmup_excluded_ids=sorted(excluded_ids), warmup_excluded_count=len(excluded_ids),
                qualified_count=len(verified_ids), unknown_count=len(unknown_ids),
                qualified_codes=sorted(verified_codes),
            )
    gaps_by_trial = defaultdict(list)
    evidence_rows = []
    for evidence in fields.values():
        coordinates = sorted(evidence.pop("windows"))
        affected = sorted({trial for key in coordinates for trial in windows[key]["trial_ids"]})
        evidence.update(evidence_id=len(evidence_rows), windows=coordinates, trial_ids=affected)
        for trial in affected:
            gaps_by_trial[trial].append(evidence["evidence_id"])
        evidence_rows.append(evidence)
    completed = {row["trial_id"]: row["run_id"] for row in trials
                 if row.get("trial_id") and row.get("run_id") and row.get("status") == "complete"}
    trial_rows = []
    for item in definitions:
        trial_id = item["trial_id"]
        evidence_ids = gaps_by_trial[trial_id]
        pending = [key for key in item["dependencies"] if key not in completed]
        trial_rows.append(dict(
            trial_id=trial_id, input_status="blocked" if evidence_ids else "qualified",
            software_gaps=[key for key in evidence_ids if evidence_rows[key]["disposition"] == "修读取或账户"],
            pending_dependencies=pending, evidence_ids=evidence_ids,
            evidence_refs=[str(delivery_dir)] if delivery_dir else [],
            reusable_run_id=completed.get(trial_id),
            next_action="review_gaps" if evidence_ids else "await_dependencies" if pending else "review_run_scope",
        ))
    return dict(months=list(windows.values()), field_evidence=evidence_rows, trials=trial_rows,
                prior_qualifications=prior, prior_qualification_checks=prior_checks,
                reused_signal_evidence=prior_facts, account_signal_evidence=list(account_signals.values()),
                source_handoff=handoff)


def frozen_trial_delivery(payload: dict, data_root: Path) -> Path:
    configured = payload.get("qualification_delivery")
    return Path(configured) if configured else data_root / "research" / payload["study_id"] / "qualification"


def frozen_trial_qualification(payload: dict, data_root: Path, trials: list[dict]) -> dict:
    directory = frozen_trial_delivery(payload, data_root)
    return qualify_frozen_trials(payload, data_root, directory if directory.is_dir() else None, trials)


def require_frozen_trial_inputs(payload: dict, data_root: Path, trial_ids: set[str], trials: list[dict]) -> dict:
    if payload["definition"]["protocol"]["universe"]["rule"] != "historical_sh_sz_common_stock":
        return {}
    result = frozen_trial_qualification(payload, data_root, trials)
    return _require_input_rows(result, trial_ids)


def require_representative_inputs(approval: dict, data_root: Path) -> dict | None:
    definition = approval["definition"]
    if definition["protocol"]["universe"]["rule"] != "historical_sh_sz_common_stock":
        return None
    submission_id = approval["submission_id"]
    source = approval.get("qualification_delivery")
    result = _qualify_trial_inputs(
        {"definition": definition, "study_id": approval["study_id"]}, data_root,
        Path(source) if source else None, [],
        [{"trial_id": submission_id, "definition": definition, "dependencies": []}],
    )
    return _require_input_rows(result, {submission_id})[submission_id]


def _require_input_rows(result: dict, trial_ids: set[str]) -> dict:
    for row in result["trials"]:
        if row["trial_id"] in trial_ids and row["input_status"] == "blocked":
            reasons = set()
            for key in row["evidence_ids"]:
                evidence = result["field_evidence"][key]
                interval = evidence.get("intervals", {})
                reason = evidence["reason"]
                if reason in {"incompatible_account_dispositions", "share_exchange_original_unavailable"}:
                    day = interval.get("effective_date", interval.get("date"))
                    facts = interval.get("terms") or ", ".join(interval.get("event_ids", ()))
                    reason = f"{reason} {evidence['code']} {day} {facts} ({', '.join(evidence['source_refs'])})"
                reasons.add(reason)
            raise ValueError(f"{row['trial_id']}: " + ", ".join(sorted(reasons)))
    return {
        trial_id: {item["signal_date"]: item["qualified_codes"] for item in result["months"]
                   if trial_id in item["trial_ids"]}
        for trial_id in trial_ids
    }


def _definitions(payload):
    # 复用既有脚本的纯定义展开，避免复制冻结/阶段依赖逻辑。
    path = Path(__file__).resolve().parents[2] / "research/run_validation.py"
    spec = importlib.util.spec_from_file_location("hetu_frozen_validation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validation_definitions(dict(payload))


def research_data_requirements(payload, calendar, history):
    definitions = _definitions(payload)
    signals, starts = set(), []
    for item in definitions:
        definition = item["definition"]
        days = _signal_days(
            calendar,
            date.fromisoformat(definition["start"]),
            date.fromisoformat(definition["end"]),
            definition["protocol"]["rebalance"]["frequency"],
        )
        item["signal_dates"] = days
        item["history_start"] = (
            _warmup_start(days[0], definition["protocol"]["warmup_months"]).isoformat() if days else definition["start"]
        )
        starts.append(item["history_start"])
        signals.update(days)
    start = min(starts)
    study = payload["definition"]
    objects, outside, unknown = _history_objects(history, start, study["end"])
    pre_study = _pre_study_exit_indices(objects, calendar, start, study)
    legal = []
    for index, obj in enumerate(objects):
        if not obj["attributes_known"]:
            continue
        reason = (
            "outside_research_market"
            if obj["market"] not in ("SH", "SZ")
            else "not_common_stock"
            if obj["security_type"] != "stock"
            else "pre_study_exit"
            if index in pre_study
            else "not_listed_during_study"
            if obj["lifecycle_known"] and obj["valid_from"] > study["end"]
            else None
        )
        if reason:
            obj["excluded"] = True
            legal.append(
                dict(
                    identity_id=obj["identity_id"],
                    code=obj["code"],
                    reason=reason,
                    source_ref=obj.get("source_ref", ""),
                )
            )
    legally_excluded = {(row["identity_id"], row["code"]) for row in legal}
    return dict(
        purpose="research",
        definitions=definitions,
        objects=objects,
        start=start,
        end=study["end"],
        study_start=study["start"],
        signal_dates=sorted(signals),
        required_intervals=[
            dict(identity_id=o["identity_id"], code=o["code"], interval=o["required"]) for o in objects
        ],
        expected_sessions=sorted(
            str(d)[:10]
            for d, flag in zip(calendar.date, calendar.is_trading_day, strict=False)
            if _truthy(flag) and start <= str(d)[:10] <= study["end"]
        ),
        calendar_days=sorted(set(calendar.date.astype(str))),
        lifecycle_exclusions=outside,
        legal_exclusions=legal,
        unknown_ranges=[r for r in unknown if (r["identity_id"], r["code"]) not in legally_excluded],
        target_scope=dict(history.attrs.get("target_scope", {})),
    )


def _known_history(hist, knowledge, at):
    if hist.empty or knowledge.empty:
        return pd.DataFrame()
    evidence = knowledge[knowledge.table_name.eq("historical_securities")].copy()
    evidence = evidence.rename(columns={"record_key": "code"})
    cols = ["code", "evidence_ref", "sha256", "revision_id", "supersedes", "published_at", "verified", "evidence_root"]
    merged = hist.drop(columns=["evidence_root"], errors="ignore").merge(evidence[cols], on=["code", "evidence_ref"])
    known = select_known_versions(merged, at, keys=("code",))
    if known.empty:
        return known
    return known[known.apply(lambda r: _evidence_original_ok(Path(r.evidence_root), r.evidence_ref, r.sha256), axis=1)]


def _historical_versions(inventory, at):
    return _known_history(
        inventory.frames.get("historical_securities", pd.DataFrame()),
        inventory.frames.get("knowledge_evidence", pd.DataFrame()),
        at,
    )


def audit_research_coverage(requirements, store, ref, *, inventory=None):
    inventory = inventory or scan_local_coverage(store, ref, asset_snapshots=requirements.get("asset_snapshots", ()))
    rows = _coverage_rows(requirements, inventory, {"daily_bars": "bars", "security_status": "status"})
    # 复用档案日历审计，但不把档案因子门强加研究。
    base = audit_archive(store, ref, requirements, inventory=inventory)
    caps = inventory.frames.get("market_cap", pd.DataFrame())
    caps_by_code = {str(code): group for code, group in caps.groupby("code")} if len(caps) else {}
    empty_caps = pd.DataFrame()
    knowledge = inventory.frames.get("knowledge_evidence", pd.DataFrame())
    actions = inventory.frames.get("corporate_actions", pd.DataFrame())
    signal_evidence, limitations = [], []
    disposals, exchanges, disposal_gaps, exchange_gaps, conflicts = account_dispositions(
        inventory.frames.get("terminal_disposals", pd.DataFrame()).to_dict("records"),
        inventory.frames.get("share_exchanges", pd.DataFrame()).to_dict("records"),
        store.root / "evidence", requirements["study_start"], requirements["end"],
    )
    issues = defaultdict(list)
    for gap in [*disposal_gaps, *exchange_gaps, *conflicts]:
        issues[gap["code"], gap["date"]].append(gap)
    sessions = sorted(requirements.get("expected_sessions", ()))
    for (code, day), event in tuple(disposals.items()):
        prior = next((session for session in reversed(sessions) if session < day), day)
        # A cash payout can take effect at the next execution session after the last listing session.
        disposals.setdefault((code, prior), event)
    for (code, day), event in exchanges.items():
        prior = next((session for session in reversed(sessions) if session < day), day)
        for key in {(code, prior), (code, day)}:
            if gaps := issues.get((code, day)):
                issues[key] = [dict(gap, date=key[1], effective_date=day, missing_fields=gap.get("fields", ()))
                               for gap in gaps]
                disposals.pop(key, None)
            else:
                disposals[key] = event
    month_anchors = _proven_month_ends(inventory.calendar)
    needed = [o for o in requirements["objects"] if not o.get("excluded")]
    history_gaps, capital_gaps = defaultdict(list), defaultdict(list)
    applicable_signals = defaultdict(list)
    rules = inventory.frames.get("trading_rules", pd.DataFrame())
    for day in requirements["signal_dates"]:
        at = datetime.fromisoformat(backtest_data._signal_cutoff(day))
        known = _historical_versions(inventory, at)
        for obj in needed:
            if obj["lifecycle_known"] and (obj["valid_from"] > day or (obj["valid_to"] and obj["valid_to"] < day)):
                continue
            applicable_signals[obj["identity_id"]].append(day)
            own = known[known.code.eq(obj["code"])] if len(known) else known
            active = (own[own.listed.map(_truthy) & own.valid_from.le(day)
                          & (own.valid_to.fillna("").eq("") | own.valid_to.ge(day))]
                      if len(own) else own)
            revisions = active.revision_id.astype(str).tolist() if len(active) else []
            if not revisions:
                history_gaps[obj["identity_id"]].append(day)
            capital = backtest_data.select_signal_market_cap(
                caps_by_code.get(obj["code"], empty_caps), knowledge, obj["code"], day, store.root
            )
            cap_known = capital is not None
            if not cap_known:
                capital_gaps[obj["identity_id"]].append(day)
            signal_evidence.append(
                dict(
                    code=obj["code"],
                    identity_id=obj["identity_id"],
                    date=day,
                    cutoff=at.isoformat(),
                    capital_revision=capital.get("revision_id") if capital else None,
                    market_cap_cny=capital["market_cap_cny"] if capital else None,
                    market_cap_source_ref=capital.get("evidence_ref") if capital else None,
                    history_revision=revisions,
                    history_valid_from=str(active.iloc[0]["valid_from"]) if len(active) == 1 else None,
                    market_cap_known=cap_known,
                )
            )
    for obj in needed:
        for domain, gaps in [("historical_public_evidence", history_gaps), ("market_cap", capital_gaps)]:
            rows.append(
                dict(
                    code=obj["code"],
                    identity_id=obj["identity_id"],
                    domain=domain,
                    required=obj["required"],
                    covered=_ranges(
                        set(applicable_signals[obj["identity_id"]]) - set(gaps[obj["identity_id"]]),
                        requirements["signal_dates"],
                    ),
                    observed_signals=len(applicable_signals[obj["identity_id"]]) - len(gaps[obj["identity_id"]]),
                    data_gaps=[],
                    unknown_ranges=_ranges(gaps[obj["identity_id"]], requirements["signal_dates"]),
                    signal_dates_unknown=gaps[obj["identity_id"]],
                    source_refs=[obj.get("source_ref", "")],
                )
            )
        start, end = obj["required"]
        anchors = [day for day in month_anchors if day[:7] == start[:7]]
        action_start = anchors[0] if anchors and not isinstance(obj.get("continuation"), dict) else start
        entries = [e for e in inventory.action_entries if e.get("partition", "").split("|")[0] == "code=" + obj["code"]]
        action_covered, action_unknown = _action_query_intervals(entries, action_start, end)
        rows.append(
            dict(
                code=obj["code"],
                identity_id=obj["identity_id"],
                domain="corporate_actions",
                required=[action_start, end],
                covered=action_covered,
                data_gaps=[],
                unknown_ranges=action_unknown,
                source_refs=[e.get("path", "") for e in entries],
                coverage_semantics="operate/ex_date查询证明；不等于事件内容及字段齐备",
            )
        )
        if action_unknown:
            limitations.append(
                dict(
                    code=obj["code"],
                    domain="corporate_actions",
                    reason=_ACTION_QUERY_GAP,
                    required=[action_start, end],
                    unknown_ranges=action_unknown,
                )
            )
        own = actions[actions.code.eq(obj["code"])] if len(actions) else actions
        for row in own.to_dict("records"):
            if not action_start <= str(row.get("ex_date", "")) <= end:
                continue
            kind = row.get("action_type")
            required = ["d"] if kind == "cash_dividend" else ["m"]
            if str(row.get("ex_date", "")) >= requirements["study_start"]:
                required += _ACCOUNT_ACTION_FIELDS.get(kind, ())
            missing = [k for k in required if not row.get(k) or pd.isna(row.get(k))]
            if kind not in ("cash_dividend", "bonus_split") or missing:
                limitations.append(
                    dict(
                        code=obj["code"],
                        domain="corporate_actions",
                        date=row.get("ex_date"),
                        reason="unsupported_or_missing_event_fields",
                        action_type=kind,
                        missing_fields=missing,
                        available_at=str(row["available_at"]) if pd.notna(row.get("available_at")) else None,
                        source_refs=(
                            [str(row["source_ref"])] if pd.notna(row.get("source_ref")) and row.get("source_ref")
                            else [e.get("path", "") for e in entries]
                        ),
                    )
                )
        if (
            obj["lifecycle_known"]
            and obj["valid_to"]
            and requirements["study_start"] <= obj["valid_to"] <= requirements["end"]
        ):
            exit_date = obj["valid_to"]
            exit_key = obj["code"], exit_date
            if exit_key not in disposals:
                gaps = issues.get(exit_key) or [dict(code=obj["code"], date=exit_date, reason="exit_disposal_unproved")]
                limitations.extend({**gap, "domain": "terminal_disposals"} for gap in gaps)
        rule_start = max(requirements["study_start"], start)
        sessions = [d for d in requirements["expected_sessions"] if rule_start <= d <= end]
        covered_rules = set()
        refs = []
        if len(rules) and obj.get("board") and obj["lifecycle_known"]:
            own_rules = rules[
                rules.market.eq(obj["market"]) & rules.board.eq(obj["board"]) & rules.security_type.eq("stock")
            ]
            for rule in own_rules.to_dict("records"):
                fields = [
                    "min_buy_qty",
                    "qty_step",
                    "tick_size",
                    "settlement",
                    "limit_rule",
                    "fee_rule",
                    "source_ref",
                ]
                if any(pd.isna(rule.get(k)) or not rule.get(k) for k in fields):
                    continue
                left, right = str(rule.get("valid_from", "")), str(rule.get("valid_to") or end)
                covered_rules.update(d for d in sessions if left <= d <= right)
                refs.append(rule["source_ref"])
        rows.append(
            dict(
                code=obj["code"],
                identity_id=obj["identity_id"],
                domain="trading_rules",
                required=[rule_start, end],
                covered=_ranges(covered_rules, sessions),
                observed_sessions=len(covered_rules),
                data_gaps=[],
                unknown_ranges=_ranges(set(sessions) - covered_rules, sessions),
                source_refs=sorted(set(refs)),
                coverage_semantics="已存普通股规则的有效期；身份/板块/PIT及特殊状态仍分别核验",
            )
        )
    need_financials = any(d["definition"]["strategy_id"] == "ep_value" for d in requirements["definitions"])
    # An effective conflict remains evidence even if the lifecycle endpoint is still unknown.
    represented = {(row["code"], row.get("effective_date", row.get("date")), row["reason"])
                   for row in limitations if row.get("domain") == "terminal_disposals"}
    needed_codes = {obj["code"] for obj in needed}
    for conflict in conflicts:
        code, day = conflict["code"], conflict["date"]
        if code in needed_codes:
            limitations.extend({**gap, "domain": "terminal_disposals"} for gap in issues[code, day]
                               if (code, day, gap["reason"]) not in represented)
    if need_financials and inventory.frames.get("financial_reports", pd.DataFrame()).empty:
        limitations.append(dict(domain="financial_reports", reason="ep_PIT_financials_missing"))
    unknown = [item for item in base["unknown_ranges"] if item.get("domain") != "adjustment_factors"]
    ready = bool(needed) and requirements["target_scope"].get("complete") is True and not unknown
    ready = (
        ready
        and not limitations
        and not inventory.failures
        and not any(r["data_gaps"] or r["unknown_ranges"] for r in rows)
    )
    return dict(
        full_scope_ready=ready,
        coverage=rows,
        legal_exclusions=requirements["legal_exclusions"],
        unknown_ranges=unknown,
        event_limitations=limitations,
        signal_evidence=signal_evidence,
        optional_limitations=["industry_exposure"] + ([] if need_financials else ["financial_reports_for_EP_only"]),
        target_scope_complete=requirements["target_scope"].get("complete") is True,
        read_failures=inventory.failures,
        source_stats=inventory.stats,
    )


def prepare_research_delivery(payload, store, ref, output_dir, *, inventory=None):
    from hetu_quant.backtest_models import backtest_definition_from_dict

    output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=False, parents=True)
    inventory = inventory or scan_local_coverage(store, ref, asset_snapshots=payload.get("asset_snapshots", ()))
    if payload.get("target_history_ref"):
        history = pd.read_csv(payload["target_history_ref"], dtype=str).fillna("")
    else:
        history = inventory.frames.get("historical_securities", pd.DataFrame(columns=["code"]))
    history.attrs["target_scope"] = dict(payload.get("target_scope", {}))
    requirements = research_data_requirements(payload, inventory.calendar, history)
    coverage = audit_research_coverage(requirements, store, ref, inventory=inventory)
    mode = payload.get("qualification_mode", "reuse")
    if mode not in ("reuse", "prepare"):
        raise ValueError("unknown qualification mode")
    qualifications, completed = [], {}
    for item in requirements["definitions"]:
        definition = dict(item["definition"], snapshot_id=ref.snapshot_id)
        key = (
            definition["start"],
            definition["end"],
            definition["protocol"]["warmup_months"],
            "backtest_ep" if definition["strategy_id"] == "ep_value" else "backtest_low_volatility",
            definition["mode"],
        )
        if mode == "prepare":
            if key not in completed:
                report = backtest_data.prepare_backtest(
                    backtest_definition_from_dict(definition), store, output_dir / f"qualification-{len(completed)}"
                )
                completed[key] = dict(can_run=report.can_run, eligible_ids=list(report.eligible_ids))
            qualifications.append(dict(trial_id=item["trial_id"], reused_need=key, report=completed[key]))
        else:
            qualifications.append(
                dict(
                    trial_id=item["trial_id"],
                    status="not_recomputed",
                    evidence_refs=list(payload.get("qualification_references", [])),
                )
            )
    handoff = dict(
        new_qualifications=len(completed),
        research_runs=0,
        qualification_mode=mode,
        qualification_references=list(payload.get("qualification_references", [])),
        full_scope_ready=coverage["full_scope_ready"],
        study_id=payload.get("study_id"),
        snapshot_id=ref.snapshot_id,
        mode=payload["definition"]["mode"],
        supplemented_snapshots=bool(payload.get("asset_snapshots")),
    )
    result = dict(requirements=requirements, coverage=coverage, qualifications=qualifications, handoff=handoff)
    for name, value in result.items():
        (output_dir / f"{name}.json").write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")
    return result
