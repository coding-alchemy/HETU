"""Read-only coverage and result evidence for the exact submitted factor request."""

from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import date

from hetu_data.library import load_library, revision_replacements, selected_snapshot_assets
from hetu_data.models import SnapshotRef

from hetu_quant.backtest_data import _permission_sources

from .catalog import get_factor
from .core import MissingFact, TeachingSource, iso_day, parameter_identity
from .series import is_price_index_quote, pair_calendar_dependencies, pair_sample_days
from .sources import (
    MOMENTUM_PRICE_FACTORS,
    _business_day,
    _business_days,
    _daily_start,
    _daily_window_size,
    _fields,
    _known,
    _native_records,
    _permission_fields,
    _permitted_products,
    _proof_fields,
    _supplementary,
    _table_products,
    calendar_market,
    calendar_records,
    calendar_witnesses,
    open_calendar_days,
    product_scope,
    quote_subject,
    read_calendar_product,
    select_versions,
    teaching_document,
)


@dataclass(frozen=True)
class CoveragePreview:
    can_submit: bool
    checks: tuple[dict, ...]


def _local_calendar(store, trial, left):
    revisions = (
        () if isinstance(trial.source, SnapshotRef) else load_library(store, trial.source)["revision_selections"]
    )
    replacements = revision_replacements(revisions, "calendar")
    records = [
        row
        for owner, ref, manifest in selected_snapshot_assets(store, trial.source, "calendar")
        for product in sorted(_permitted_products(owner, manifest, "calendar", trial.purpose))
        for row in read_calendar_product(
            owner, ref, manifest, product, trial.purpose, left, trial.trade_date, trial.cutoff,
            replacement=(str(owner.root.resolve()), ref.snapshot_id, product) in replacements,
        )
    ]
    return calendar_records(records, trial.cutoff, revisions)


def _daily_checks(store, trial, dependencies, quote_subjects):
    """Check physical coverage for a proven short daily window, without reading quotes."""
    request = trial.request
    if (
        "daily_bars" not in dependencies
        or request.parameters.get("months")
        or not (
            request.factor_id.startswith("pair_")
            or request.factor_id in ("up_amount_share", "traded_amount", "price_momentum", "absolute_momentum")
        )
    ):
        return []
    end = trial.trade_date
    result = []
    definition = get_factor(trial.factor_id)
    subjects = tuple(dict.fromkeys(
        (*quote_subjects, *[request.parameters[name] for name in ("benchmark",) if request.parameters.get(name)])
    ))
    skip = request.parameters.get("skip", 0) if request.factor_id in ("price_momentum", "absolute_momentum") else 0

    def calendars_from(left):
        records = _local_calendar(store, trial, left)
        return {code: calendar_witnesses(records, calendar_market(code)) for code in subjects}

    left = _daily_start(replace(request, subjects=subjects), [])
    calendars = calendars_from(left)
    size = _daily_window_size(request)
    paired = request.factor_id.startswith("pair_")
    windows, short_windows = {}, {}
    if paired and len(subjects) == 2:
        previous = request.factor_id == "pair_hedge_beta"
        count = size
        try:
            selected = pair_sample_days(list(calendars.values()), str(end), count, previous=previous)
            if len(selected) < count:
                calendars = calendars_from(date.min)
                selected = pair_sample_days(list(calendars.values()), str(end), count, previous=previous)
            if len(selected) < count:
                raise MissingFact(
                    "calendar", f"共同交易日不足，需要 {count} 日，已有 {len(selected)} 日", period=str(end),
                )
        except MissingFact as exc:
            day = date.fromisoformat(exc.period)
            result.append(dict(
                state="pending", subject=", ".join(subjects), field="calendar", start=day, end=day,
                purpose=trial.purpose, reason=str(exc),
            ))
            return result
        windows = {code: [date.fromisoformat(day) for day in selected] for code in subjects}
    elif not paired:
        if skip and any(len(open_calendar_days(calendar)) < size for calendar in calendars.values()):
            calendars = calendars_from(date.min)
        for subject in subjects:
            calendar = {day: {row["is_trading_day"] for row in rows} for day, rows in calendars[subject].items()}
            observation = calendar.get(str(end))
            if observation != {True}:
                closed = observation == {False}
                result.append(dict(
                    state="missing" if closed else "pending", subject=subject, field="calendar",
                    start=end, end=end, purpose=trial.purpose,
                    reason="已证休市，请显式选择观察日" if closed else "当日交易属性待证",
                ))
                continue
            sessions = sorted(day for day, flags in calendar.items() if flags == {True})
            if len(sessions) < size and not skip:
                continue
            window = sessions[-size:]
            conflict = next((day for day, flags in sorted(calendar.items())
                             if window[0] <= day <= str(end) and len(flags) > 1), None)
            if conflict:
                day = date.fromisoformat(conflict)
                result.append(dict(
                    state="pending", subject=subject, field="calendar", start=day, end=day,
                    purpose=trial.purpose, reason="窗口内交易日历冲突",
                ))
                continue
            if len(window) < size:
                short_windows[subject] = len(window)
            sampled = window[:-skip] if skip and subject not in short_windows else window
            windows[subject] = [date.fromisoformat(day) for day in sampled]
    if not windows:
        return result
    start = min(days[0] for days in windows.values())
    finish = end if skip or paired else max(days[-1] for days in windows.values())
    supplementary_rows = _supplementary(
        store, trial.source, definition,
        {"daily_bars": dependencies["daily_bars"], "index_bars": ("close", "return_basis"),
         "security_status": ("is_suspended",)},
    )

    def coordinates(table):
        for row in select_versions(supplementary_rows.get(table, ()), trial.cutoff, table=table):
            if row["code"] in subjects and iso_day(row.get("date")):
                yield (row["code"], date.fromisoformat(row["date"])), row

    supplementary = {key for key, _ in coordinates("daily_bars")}
    registered_proofs = {(owner.root.resolve(), ref.snapshot_id)
                         for owner, ref, _ in selected_snapshot_assets(store, trial.source, "knowledge_evidence")}
    observed, attested = set(), set()
    index_versions = list(supplementary_rows.get("index_bars", ()))
    suspended = {key for key, row in coordinates("security_status") if row.get("is_suspended") is True}
    for table in ("daily_bars", "index_bars", "security_status"):
        for owner, ref, manifest in selected_snapshot_assets(store, trial.source, table):
            for product in _permitted_products(owner, manifest, table, trial.purpose):
                columns = (
                    ("code", "date", "is_suspended", "product_id", "source_ref")
                    if table == "security_status"
                    else ("code", "date")
                )
                scope = product_scope(
                    owner,
                    ref,
                    manifest,
                    table=table,
                    product_id=product,
                    purpose=trial.purpose,
                    subjects=tuple(windows),
                    start=start,
                    end=finish,
                    fields=columns,
                )
                if not scope["allowed"] or set(scope["fields"]) != set(columns):
                    continue
                frame = owner.read_range(
                    ref,
                    table,
                    codes=scope["subjects"],
                    start=scope["start"],
                    end=scope["end"],
                    columns=columns,
                    product_id=product,
                )
                if table == "security_status":
                    keys = tuple(f"{row.code}|{row.date}|is_suspended" for row in frame.itertuples(index=False))
                    proofs = _proof_fields(
                        owner, ref, manifest, table, ("is_suspended",), purpose=trial.purpose, record_keys=keys
                    )
                    verified = _native_records(table, frame, ("is_suspended",), proofs, owner=owner, products={product})
                    for row in verified:
                        if row.get("is_suspended") is True and _known(row.get("available_at"), trial.cutoff):
                            suspended.add((row["code"], date.fromisoformat(row["date"])))
                else:
                    coordinates_in_table = {(row.code, date.fromisoformat(str(row.date)))
                                            for row in frame.itertuples(index=False)}
                    observed.update(coordinates_in_table)
                    if not (paired or skip) or (owner.root.resolve(), ref.snapshot_id) not in registered_proofs:
                        continue
                    fields = ("close_raw",) if table == "daily_bars" else ("close", "return_basis")
                    allowed = _permission_fields(owner, manifest, table, product)
                    if allowed is not None:
                        fields = tuple(field for field in fields if field in allowed)
                    if not fields:
                        continue
                    uncertain = {(code, day) for code, day in coordinates_in_table
                                 if not calendars[code].get(str(day))}
                    if not uncertain:
                        continue
                    proofs = _proof_fields(
                        owner, ref, manifest, table, fields, purpose=trial.purpose,
                        record_keys=tuple(
                            f"{code}|{day}|{field}" for code, day in sorted(uncertain) for field in fields
                        ),
                    )
                    if table == "index_bars" and proofs:
                        indexed = owner.read_range(
                            ref, table, codes=tuple(sorted({code for code, _ in uncertain})),
                            start=min(day for _, day in uncertain), end=max(day for _, day in uncertain),
                            columns=("code", "date", "product_id", "source_ref", *fields), product_id=product,
                        )
                        index_versions.extend(
                            dict(row, source_root=str(owner.root.resolve()), snapshot_id=ref.snapshot_id)
                            for row in _native_records(table, indexed, fields, proofs, owner=owner, products={product})
                        )
                    elif table == "daily_bars":
                        attested.update(
                            (code, day) for code, day in uncertain
                            if any(original.get("product_id") == product and _known(proof.published_at, trial.cutoff)
                                   for proof, original in proofs.get(f"{code}|{day}|close_raw", ()))
                        )
    known_indexes = {
        (row["code"], date.fromisoformat(row["date"]))
        for row in select_versions(index_versions, trial.cutoff, table="index_bars")
        if row["code"] in windows and iso_day(row.get("date")) and is_price_index_quote(row)
    }
    supplementary.update(known_indexes - observed)
    known_quotes = attested | supplementary | known_indexes
    if paired:
        try:
            pair_sample_days(
                list(calendars.values()), str(end), count, previous=previous,
                observed_days={str(day) for _, day in known_quotes},
            )
        except MissingFact as exc:
            day = date.fromisoformat(exc.period)
            result.append(dict(
                state="pending", subject=", ".join(subjects), field="calendar", start=day, end=day,
                purpose=trial.purpose, reason=str(exc),
            ))
            return result
    for subject, window in windows.items():
        if skip:
            undocumented = sorted(
                day for code, day in known_quotes
                if code == subject and window[0] <= day <= end
                and {row["is_trading_day"] for row in calendars[subject].get(str(day), ())}
                not in ({True}, {False})
            )
            if undocumented:
                result.append(dict(
                    state="pending", subject=subject, field="calendar",
                    start=undocumented[0], end=undocumented[0], purpose=trial.purpose,
                    reason="窗口内有行情的日期缺交易日历凭据",
                ))
                continue
            if subject in short_windows:
                result.append(dict(
                    state="missing", subject=subject, field="calendar", start=window[0], end=end,
                    purpose=trial.purpose,
                    reason=f"跳期窗口不足，需要 {size} 个交易日，已有 {short_windows[subject]} 日",
                ))
                continue
        if paired:
            result.append(dict(
                state="pending", subject=subject, field="共同取样日", start=window[0], end=window[-1],
                purpose=trial.purpose, reason="、".join(map(str, window)),
            ))
        for day in window:
            key = subject, day
            if key not in observed:
                result.append(
                    dict(
                        state="pending" if key in supplementary else "missing",
                        subject=subject,
                        field=", ".join(dependencies["daily_bars"]),
                        start=day,
                        end=day,
                        purpose=trial.purpose,
                        reason=(
                            "有获准日线补证，字段待核对"
                            if key in supplementary
                            else "已证停牌"
                            if key in suspended
                            else "应有交易日日线缺行"
                        ),
                    )
                )
        if all((subject, day) in observed for day in window):
            result.append(
                dict(
                    state="pending",
                    subject=subject,
                    field="行情日期",
                    start=window[0],
                    end=window[-1],
                    purpose=trial.purpose,
                    reason=f"库表有 {len(window)} 个交易日；当时原件与字段待核对",
                )
            )
    return result


def _buyback_checks(store, trial):
    from .shareholder_events import buyback_progress_inputs

    definition = get_factor(trial.factor_id)
    evidence = _supplementary(store, trial.source, definition, {"events": ()})
    event_id = trial.request.parameters.get("event_id")
    for row in select_versions(evidence.get("events", ()), trial.cutoff, table="events", buyback_progress=True):
        if row.get("code") not in trial.subjects or row.get("kind") != "buyback":
            continue
        if event_id and event_id != row.get("event_id"):
            continue
        try:
            buyback_progress_inputs(row)
        except MissingFact as exc:
            original = row.get("_workbench_buyback_input") or row
            yield dict(
                table="knowledge_evidence", subject=row["code"], field=exc.field, end=trial.trade_date,
                purpose=trial.purpose, source=original.get("snapshot_id"),
                product=original.get("registration_product_id"),
                state="denied" if "未获准" in str(exc) else "missing", reason=str(exc),
            )


def _has_other_permitted_input(store, trial, checked_tables):
    if isinstance(trial.source, SnapshotRef):
        registered = {entry["table"] for entry in store.load_manifest(trial.source)["table_files"]}
    else:
        registered = {
            table for asset in load_library(store, trial.source)["asset_snapshots"] for table in asset["tables"]
        }
    return any(
        product_scope(
            owner, ref, manifest, table=table, product_id=product, purpose=trial.purpose,
            subjects=trial.subjects, start=date.min, end=trial.trade_date, fields=(),
        )["allowed"]
        for table in sorted(registered - checked_tables)
        for owner, ref, manifest in selected_snapshot_assets(store, trial.source, table)
        for product in sorted(_table_products(manifest, table))
    )


def preview_inputs(store, trial):
    request = trial.request
    dependencies = _fields(get_factor(trial.factor_id), request)
    checks = []
    teaching = isinstance(trial.source, TeachingSource)
    included = teaching_document(trial.source)["tables"] if teaching else {}
    allowed_source = teaching
    quote_subjects = trial.subjects
    declared = ()
    if not teaching and trial.factor_id in MOMENTUM_PRICE_FACTORS:
        definition = get_factor(trial.factor_id)
        proofs = _supplementary(store, trial.source, definition, {"sector_proxies": ()})
        declared = select_versions(proofs.get("sector_proxies", ()), trial.cutoff, table="sector_proxies")
        requested_quotes = trial.subjects
        if benchmark := request.parameters.get("benchmark"):
            requested_quotes = (*requested_quotes, benchmark)
        quote_subjects = tuple(quote_subject(declared, request, code) for code in requested_quotes)
        for subject, code in zip(requested_quotes, quote_subjects, strict=True):
            if subject != code:
                checks.append(dict(
                    table="sector_proxies", state="pending", subject=subject, field="sector_proxies",
                    start=request.trade_date, end=request.trade_date, purpose=trial.purpose,
                    reason=f"实际取价证券 {code}",
                ))
    for table, fields in dependencies.items():
        subjects = quote_subjects if table == "daily_bars" else trial.subjects
        left = _daily_start(replace(request, subjects=subjects), []) if table == "daily_bars" else None
        right = request.trade_date
        base = dict(
            table=table,
            field=", ".join(fields) or table,
            subject=", ".join(dict.fromkeys(subjects)),
            start=left,
            end=right,
            purpose=trial.purpose,
        )
        registered = False
        if teaching:
            checks.append(
                dict(
                    base,
                    state="pending" if included.get(table) else "missing",
                    source=trial.source.case_id,
                    reason="教学事实，由生产计算核对",
                )
            )
            continue
        for owner, ref, manifest in selected_snapshot_assets(store, trial.source, table):
            for product in sorted(_table_products(manifest, table)):
                registered = True
                scope = product_scope(
                    owner,
                    ref,
                    manifest,
                    table=table,
                    product_id=product,
                    purpose=trial.purpose,
                    subjects=subjects,
                    start=left or date.min,
                    end=right,
                    fields=fields,
                )
                permitted = scope["allowed"]
                allowed_source |= permitted
                for denied in sorted(set(fields) - set(scope["fields"])):
                    checks.append(dict(base, source=ref.snapshot_id, product=product, field=denied,
                                       state="denied", reason="字段未获准用于本次因子计算"))
                if not permitted or not fields or scope["fields"]:
                    checks.append(
                        dict(
                            base,
                            field=", ".join(scope["fields"]) or table,
                            source=ref.snapshot_id,
                            product=product,
                            state="pending" if permitted else "denied",
                            reason="已登记，实际窗口与表值待计算核对" if permitted else scope["reason"],
                        )
                    )
        if not registered:
            if table == "sector_proxies" and declared:
                checks.append(dict(base, state="pending", reason="存在已登记的板块代理凭据"))
            else:
                checks.append(dict(base, state="missing", reason="未登记此原生表；补证待核对"))
    if not teaching:
        required_proofs = {
            field for table, fields in dependencies.items()
            if table != "calendar" and not (trial.factor_id == "traded_amount" and table == "corporate_actions")
            for field in fields
        }
        for owner, ref, manifest in selected_snapshot_assets(store, trial.source, "knowledge_evidence"):
            provided = _table_products(manifest, "knowledge_evidence")
            permitted = _permitted_products(owner, manifest, "knowledge_evidence", trial.purpose)
            allowed_source |= bool(permitted)
            for product in sorted(provided):
                if product in permitted:
                    allowed = _permission_fields(owner, manifest, "knowledge_evidence", product)
                    if allowed is not None:
                        for field in sorted(required_proofs - allowed):
                            checks.append(dict(
                                table="knowledge_evidence", field=field, subject=", ".join(trial.subjects),
                                end=request.trade_date, purpose=trial.purpose, source=ref.snapshot_id,
                                product=product, state="denied", reason="补证字段未获准用于本次因子计算",
                            ))
                        if required_proofs and not required_proofs & allowed:
                            continue
                checks.append(
                    dict(
                        table="knowledge_evidence",
                        field="知识凭据",
                        subject=", ".join(trial.subjects),
                        end=request.trade_date,
                        purpose=trial.purpose,
                        source=ref.snapshot_id,
                        product=product,
                        state="pending" if product in permitted else "denied",
                        reason="登记与原件、字段时间待核对" if product in permitted else "产品未获准用于本次因子计算",
                    )
                )
    if not teaching and allowed_source:
        if trial.factor_id == "buyback_progress":
            checks.extend(_buyback_checks(store, trial))
        daily_checks = _daily_checks(store, trial, dependencies, quote_subjects)
        if any(check["reason"] == "有获准日线补证，字段待核对" for check in daily_checks):
            for check in checks:
                if check["table"] == "daily_bars" and check["reason"] == "未登记此原生表；补证待核对":
                    check.update(state="pending", reason="存在当前窗口的获准日线补证")
        checks.extend(daily_checks)
    if not allowed_source:
        # A purpose grant permits requesting a missing-input result without reading unrelated tables.
        allowed_source = _has_other_permitted_input(store, trial, {*dependencies, "knowledge_evidence"})
    return CoveragePreview(allowed_source, tuple(checks))


def _at_cutoff(value, cutoff):
    if isinstance(value, (tuple, list)):
        return type(value)(
            _at_cutoff(item, cutoff) for item in value if not isinstance(item, dict) or _visible_time(item, cutoff)
        )
    if not isinstance(value, dict):
        return value
    observed = value.get("observed_at")
    hidden = (
        {"observed_at", "observed_value", "observed_source_ref", "matched"}
        if observed and not _known(observed, cutoff)
        else set()
    )
    return {
        key: _at_cutoff(item, cutoff)
        for key, item in value.items()
        if key not in hidden
        and not key.startswith("_workbench_")
        and (not isinstance(item, dict) or _visible_time(item, cutoff))
    }


def _visible_time(item, cutoff):
    return all(_known(item[key], cutoff) for key in ("available_at", "published_at", "released_at") if item.get(key))


def _visible_references(value, rights, origin=None):
    if isinstance(value, (tuple, list)):
        return type(value)(_visible_references(item, rights, origin) for item in value)
    if not isinstance(value, dict):
        return value
    root, snapshot, product, registration = origin or (None, None, None, None)
    root = value.get("source_root", root)
    snapshot = value.get("snapshot_id", snapshot)
    if "product_id" in value:
        product = value["product_id"]
        registration = None
    registration = value.get("registration_product_id", registration)
    labels = {
        "source_ref": (product, "source_ref"),
        "observed_source_ref": (product, "source_ref"),
        "evidence_ref": (registration or product, "evidence_ref"),
        "revision_evidence_ref": (registration or product, "evidence_ref"),
    }
    hidden = {
        key for key, (owner, field) in labels.items()
        if (fields := rights.get((root, snapshot, owner))) is not None and field not in fields
    }
    return {
        key: _visible_references(item, rights, (root, snapshot, product, registration))
        for key, item in value.items() if key not in hidden
    }


def _origins(value, asset=None):
    if isinstance(value, dict):
        if value.get("snapshot_id") and value.get("source_root"):
            asset = {"snapshot_id": value["snapshot_id"], "source_root": value["source_root"]}
            yield value
        elif asset and (value.get("product_id") or value.get("registration_product_id")):
            yield dict(value, **asset)
        for key, item in value.items():
            if key != "_workbench_calendar":
                yield from _origins(item, asset)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _origins(item, asset)


def _current_rights(store, trial, access):
    current = {}
    for table in {entry["table"] for entry in access}:
        for owner, ref, manifest in selected_snapshot_assets(store, trial.source, table):
            for product in _permitted_products(owner, manifest, table, trial.purpose):
                sources, permission = _permission_sources(owner, manifest, table, product)
                grant = owner._grant(product) if owner.product(product).get("fee_class") == "paid" else None
                declarations = (owner.product(product).get("permission") or {}, permission, *sources)
                allow_export = any(item.get("allow_export") is True for item in declarations)
                if any(item.get("allow_export") is False for item in declarations):
                    allow_export = False
                fields = _permission_fields(owner, manifest, table, product)
                current[str(owner.root.resolve()), ref.snapshot_id, table, product] = (grant, allow_export, fields)
    return current


def _calendar_groups(value):
    if isinstance(value, dict):
        yield from value.get("_workbench_calendar", ())
        for key, child in value.items():
            if key != "_workbench_calendar":
                yield from _calendar_groups(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _calendar_groups(child)


def _needed_access(item, access, *, has_calendar):
    origins = list(_origins(item))
    if not origins:
        return access
    assets = {}
    for origin in origins:
        key = origin["source_root"], origin["snapshot_id"]
        used = {origin.get("product_id"), origin.get("registration_product_id")} - {None}
        assets.setdefault(key, set()).update(used)
    return [
        entry
        for entry in access
        # Successful daily windows carry the calendars they used and did not need missing-bar status.
        if not (has_calendar and entry["table"] in ("calendar", "security_status"))
        and (
            entry["table"] in ("calendar", "memberships", "security_status")
            or (
                (used := assets.get((entry["root"], entry["id"]))) is not None
                and (not used or entry["product"] in used)
            )
        )
    ]


def _still_allowed(entry, right, item):
    if right is None:
        return False
    previous = entry.get("grant")
    grant, _, permitted_fields = right
    matches = [
        origin
        for origin in _origins(item)
        if (origin["source_root"], origin["snapshot_id"]) == (entry["root"], entry["id"])
        and entry["product"] in (origin.get("product_id"), origin.get("registration_product_id"))
    ]
    codes, dates = set(), set()
    used_fields = set()
    for origin in matches:
        key = origin.get("fact_key") or origin.get("record_key") or ""
        parts = key.split("|", 2)
        supplemental = len(parts) == 3 and entry["table"] == "knowledge_evidence" and not _business_day(
            parts[1], quarter=True
        )
        code = origin.get("code") or (parts[1 if supplemental else 0] if len(parts) == 3 else None)
        if code:
            codes.add(code)
        if origin.get("member"):
            codes.add(origin["member"])
        days = _business_days(origin)
        if len(parts) == 3 and not supplemental:
            if day := _business_day(parts[1], quarter=True):
                days.append(day)
            used_fields.add(parts[2])
        used_fields.update(origin.get("_workbench_fields", ()))
        dates.update(day for day in days if day)
        if grant is not None and None in days:
            return False
    if permitted_fields is not None and not used_fields <= permitted_fields:
        return False
    if previous is None:
        return grant is None
    if grant is None:
        return False
    if not codes and isinstance(item.get("subject"), (str, tuple)):
        subject = item["subject"]
        codes.update((subject,) if isinstance(subject, str) else subject)
    codes = (codes & set(previous.get("codes", ()))) or set(previous.get("codes", ()))
    dates = dates or {previous.get("start", "~"), previous.get("end", "")}
    return (
        codes <= set(grant.get("codes", ()))
        and all(code.rsplit(".", 1)[-1] in grant.get("markets", ()) for code in codes)
        and all(grant.get("start", "~") <= day <= grant.get("end", "") for day in dates)
    )


def visible_evidence(store, trial, result, *, row_index=None, gap_index=None, operation="view"):
    source_id = next(
        getattr(trial.source, name) for name in ("snapshot_id", "library_id", "case_id") if hasattr(trial.source, name)
    )
    if result._source_ref != trial.source or (
        result.factor_id,
        result.version,
        result.subjects,
        result.trade_date,
        result.cutoff,
        parameter_identity(result.parameters),
        result.source.get("id"),
    ) != (
        trial.factor_id,
        trial.definition_version,
        trial.subjects,
        trial.trade_date,
        trial.cutoff,
        trial.parameter_key,
        source_id,
    ):
        raise ValueError("结果不属于所选试算")
    if operation not in ("view", "download") or (row_index is not None and gap_index is not None):
        raise ValueError("只有一个受约束的证据位置可供查看")
    if operation == "download" and row_index is None and gap_index is None:
        raise ValueError("下载必须指定一条获准明细")
    teaching = isinstance(trial.source, TeachingSource)
    access = result._access
    if not teaching and trial.source.root.resolve() != store.root.resolve():
        raise ValueError("所选结果属于另一数据根")
    current = {} if teaching else _current_rights(store, trial, access)
    reference_rights = {}
    for (root, snapshot, _, product), (_, _, fields) in current.items():
        if fields is not None:
            key = root, snapshot, product
            reference_rights[key] = reference_rights.get(key, fields) & fields

    def calendar_allowed(groups, *, export=False):
        # Each date may have several agreeing witnesses from the original calculation.
        def allowed(origin):
            key = (origin["source_root"], origin["snapshot_id"], "calendar", origin["product_id"])
            right = current.get(key)
            previous = access_by_key.get(key)
            return previous and _still_allowed(previous, right, origin) and (not export or right[1])

        return all(any(allowed(origin) for origin in witnesses) for witnesses in groups)

    access_by_key = {(entry["root"], entry["id"], entry["table"], entry["product"]): entry for entry in access}

    def project(item, index, index_key):
        if not teaching and index_key == "row_index" and result.factor_id == "buyback_progress":
            from .shareholder_events import buyback_progress_evidence

            item = buyback_progress_evidence(item)
        groups = list(_calendar_groups(item))
        if result.factor_id.startswith("pair_") and index_key == "row_index":
            groups = pair_calendar_dependencies(groups)
        needed = _needed_access(item, access, has_calendar=bool(groups) and index_key == "row_index")
        rights = [current.get((entry["root"], entry["id"], entry["table"], entry["product"])) for entry in needed]
        if not teaching and (
            not calendar_allowed(groups) or any(
                not _still_allowed(entry, right, item) for entry, right in zip(needed, rights, strict=True)
            )
        ):
            if index_key == "row_index":
                return None, False
            return dict(
                gap_index=index,
                subject=item.get("subject"),
                field=item.get("field"),
                period=item.get("period"),
                reason="当前许可不足，证据已遮蔽",
            ), False
        projected = _at_cutoff(deepcopy(item), trial.cutoff)
        if reference_rights:
            projected = _visible_references(projected, reference_rights)
        return dict(projected, **{index_key: index}), (
            bool(rights) and all(right[1] for right in rights) and calendar_allowed(groups, export=True)
        )

    if row_index is not None or gap_index is not None:
        is_row = row_index is not None
        index, items = (row_index, result.rows) if is_row else (gap_index, result.gaps)
        if type(index) is not int or not 0 <= index < len(items):
            raise ValueError("证据位置超出此次试算")
        item, export = project(items[index], index, "row_index" if is_row else "gap_index")
        if operation == "download" and not export:
            return dict(permitted=False, reason="缺少该明细的下载许可")
        return dict(
            permitted=item is not None, item=item, reason="" if item is not None else "当前许可不足，证据已遮蔽"
        )

    rows = [visible for i, row in enumerate(result.rows) if (visible := project(row, i, "row_index")[0]) is not None]
    gaps = [project(gap, i, "gap_index")[0] for i, gap in enumerate(result.gaps)]
    if len(rows) != len(result.rows):
        gaps.append(
            dict(
                field="许可",
                subject=", ".join(trial.subjects),
                period=str(trial.trade_date),
                reason="当前许可不足，相关读数已遮蔽",
            )
        )
    return dict(
        status=("partial" if gaps else "available") if rows else "unavailable",
        factor_id=result.factor_id,
        rows=rows,
        gaps=gaps,
        trade_date=result.trade_date,
        cutoff=result.cutoff,
        subjects=result.subjects,
        parameters=result.parameters,
        source=_at_cutoff(result.source, trial.cutoff),
    )
