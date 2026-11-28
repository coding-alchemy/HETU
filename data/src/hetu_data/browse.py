"""Bounded read-only browsing of one published snapshot or library."""

from dataclasses import dataclass, replace
from datetime import date, timedelta

import duckdb
import pandas as pd

from .archive import _BAR_FIELDS, ARCHIVE_COLUMNS, _archive_calendar_flags, _archive_query_details, _uses_sz_calendar
from .library import (
    _product_read_scope,
    _select_revisions,
    archive_source_policy,
    load_library,
    query_library,
    read_scoped_product,
    revision_replacements,
    selected_snapshot_assets,
)
from .models import LibraryRef, SnapshotRef
from .store import TABLE_SCHEMAS, query_column


@dataclass(frozen=True)
class BrowseSelection:
    source: SnapshotRef | LibraryRef
    purpose: str
    table: str
    columns: tuple[str, ...]
    subjects: tuple[str, ...]
    start: date
    end: date
    market: str = ""
    axis: str = ""
    sort: str = ""
    descending: bool = False
    product_id: str = ""


@dataclass(frozen=True)
class BrowsePage:
    rows: pd.DataFrame
    total: int
    gaps: tuple[dict, ...]
    coverage: dict


def _axes(table):
    columns = TABLE_SCHEMAS[table]["columns"]
    return tuple(
        key
        for key in (
            "date",
            "ex_date",
            "effective_date",
            "report_period",
            "period_end",
            "report_date",
            "period",
            "ann_date",
            "published_at",
            "available_at",
            "effective_from",
            "valid_from",
            "ipo_date",
        )
        if key in columns
    )


def _can_display(selection, fields):
    return bool(set(selection.columns).intersection(fields)) and (not selection.sort or selection.sort in fields)


def separate_grant_day(table, axis):
    if table == "corporate_actions" and axis not in ("", "ex_date"):
        return "ex_date"
    if axis in ("available_at", "published_at", "ann_date"):
        if table == "financial_reports":
            return "report_period"
        if "date" in TABLE_SCHEMAS[table]["columns"]:
            return "date"
    return None


def _authorized_scopes(store, selection, scopes, table, *, revision_assets=()):
    available = {
        (scope["root"], scope["snapshot_id"], scope["product_id"]): scope
        for scope in scopes
        if scope["allowed"] and scope["table"] == table
    }
    for owner, ref, manifest in selected_snapshot_assets(store, selection.source, table):
        products = {entry["product_id"] for entry in manifest["table_files"] if entry["table"] == table}
        for product in sorted(products):
            if selection.product_id and table == selection.table and product != selection.product_id:
                continue
            key = str(owner.root.resolve()), ref.snapshot_id, product
            if (scope := available.get(key)) is None:
                continue
            subjects = tuple(
                code
                for code in selection.subjects
                if not selection.market or code.rsplit(".", 1)[-1] == selection.market
            )
            if scope["subjects"]:
                subjects = tuple(code for code in subjects if code in scope["subjects"])
            if selection.subjects and not subjects and "code" in TABLE_SCHEMAS[table]["columns"]:
                continue
            if table == selection.table and not _can_display(selection, scope["fields"]):
                # A registered replacement still controls version selection when it cannot be displayed.
                if key not in revision_assets:
                    continue
            if table == "knowledge_evidence" or separate_grant_day(table, selection.axis):
                yield owner, ref, product, subjects, selection.start, selection.end
            else:
                yield (
                    owner,
                    ref,
                    product,
                    subjects,
                    max(selection.start, scope["start"]),
                    min(selection.end, scope["end"]),
                )


def _coverage(codes, start, end, observed, calendar, gaps):
    """Denominator of expected bars for explicit codes and a proven calendar."""
    actual = len(observed)
    if not codes:
        return dict(actual=actual, denominator=None, reason="对象范围未证")
    general, shenzhen = _archive_calendar_flags(calendar, sourced_only=True)
    days = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    def applicable(code, day):
        exchange = shenzhen.get(day, set()) if code.endswith(".SZ") else set()
        return exchange if _uses_sz_calendar(code.rsplit(".", 1)[-1], exchange) else general.get(day)

    flags = {(code, day): applicable(code, day) for code in codes for day in days}
    if any(value not in ({True}, {False}) for value in flags.values()):
        return dict(actual=actual, denominator=None, reason="交易日历缺失或冲突")
    if any(gap["reason"] in ("conflicting_trade_evidence", "calendar_unknown") for gap in gaps):
        return dict(actual=actual, denominator=None, reason="交易状态未证")
    excluded = {
        (gap["code"], day)
        for gap in gaps
        if gap["reason"] in ("not_listed", "after_exit", "suspended", "market_closed", "confirmed_no_trade")
        for day in days
        if gap["start"] <= day <= gap["end"]
    }
    expected = {key for key, value in flags.items() if value == {True}} - excluded
    if any(
        gap["reason"] == "exit_boundary_unknown"
        and any(code == gap["code"] and gap["start"] <= day <= gap["end"] for code, day in expected)
        for gap in gaps
    ):
        return dict(actual=actual, denominator=None, reason="退市日期边界待证")
    if any(
        gap["reason"] == "lifecycle_unknown"
        and any(code == gap["code"] and gap["start"] <= day <= gap["end"] for code, day in expected - observed)
        for gap in gaps
    ):
        return dict(actual=actual, denominator=None, reason="缺行日证券存续期间待证")
    return dict(actual=len(observed & expected), denominator=len(expected), reason="")


def _selected_bar_value(fields):
    """Date evidence requires both a readable date and a readable selected value."""
    present = []
    for field in sorted(fields):
        value = f'isfinite("{field}")' if field in _BAR_FIELDS else f"""trim(CAST("{field}" AS VARCHAR)) <> ''"""
        present.append(f"(list_contains(visible_fields, '{field}') AND {value})")
    return f"list_contains(visible_fields, 'date') AND ({' OR '.join(present)})"


def _revision_assets(revisions):
    return {
        (rule["root"], rule[key], rule["product_id"])
        for rule in revisions
        for key in ("superseded_snapshot_id", "selected_snapshot_id")
    }


def _discard_superseded_rows(db, relation, revisions, *, day, product, code=None):
    db.register("scope_revisions", pd.DataFrame(revisions))
    code_scope = f"AND list_contains(rule.codes, old.{code})" if code else ""
    same_code = f"AND chosen.{code} = old.{code}" if code else ""
    # Match all revisions against the same pre-replacement coordinates.
    db.execute(f"""DELETE FROM {relation} WHERE rowid IN (
        SELECT old.rowid FROM {relation} old
        JOIN scope_revisions rule
          ON old.source_root = rule.root AND old.{product} = rule.product_id
          AND old.snapshot_id = rule.superseded_snapshot_id
          AND old.{day} BETWEEN rule.start AND rule.end {code_scope}
        JOIN {relation} chosen
          ON chosen.snapshot_id = rule.selected_snapshot_id AND chosen.source_root = old.source_root
          AND chosen.{product} = old.{product} AND chosen.{day} = old.{day} {same_code}
    )""")


def _bar_page_sql(
    store, selection, authorized, auxiliary, manifest, fields_by_product, page, page_size,
    *, revisions=(), latest_only=False, product_boundaries=False, describe_amounts=None,
):
    codes = tuple(
        code for code in selection.subjects if not selection.market or code.rsplit(".", 1)[-1] == selection.market
    )
    archive_fields = tuple(TABLE_SCHEMAS["daily_bars"]["columns"]) if manifest is not None else ARCHIVE_COLUMNS
    policy = archive_source_policy(manifest) if manifest else None
    needs_full_row = not latest_only or policy or "source_ref" in selection.columns
    statements, args = [], []
    restricted = False
    for order, (owner, ref, product, subjects, left, right) in enumerate(authorized):
        allowed = fields_by_product[(str(owner.root.resolve()), ref.snapshot_id, "daily_bars", product)]
        displayable = _can_display(selection, allowed)
        checked = set(archive_fields) & allowed if displayable else set()
        if not needs_full_row:
            checked.intersection_update((*selection.columns, "date", "code"))
        restricted |= displayable and not set(_BAR_FIELDS).issubset(allowed)
        coordinates = {"code", "date", "product_id"}
        if displayable:
            coordinates.add("source_ref")
        projection = ",".join(
            query_column("daily_bars", field) if field in checked or field in coordinates else f'NULL AS "{field}"'
            for field in archive_fields
        )
        paths = owner._range_files(ref, "daily_bars", codes=subjects, start=left, end=right, product_id=product)
        if paths and subjects and left <= right:
            statements.append(
                f"SELECT {projection}, ? AS source_root, ? AS snapshot_id, ? AS asset_order, ? AS visible_fields "
                "FROM read_parquet(?, union_by_name=true) WHERE product_id = ? "
                "AND code IN (SELECT unnest(?)) AND date BETWEEN ? AND ?"
            )
            args.extend(
                [
                    str(owner.root.resolve()),
                    ref.snapshot_id,
                    order,
                    sorted(checked),
                    paths,
                    product,
                    list(subjects),
                    left.isoformat(),
                    right.isoformat(),
                ]
            )
    columns = tuple(
        dict.fromkeys((*selection.columns, "code", "date", "product_id", "source_ref", "source_root", "snapshot_id"))
    )
    calendar_rules = (manifest or {}).get("revision_selections", ())
    calendar_replacements = revision_replacements(calendar_rules, "calendar")

    def read(table, fields, product_id=None, *, subjects=None):
        frames = []
        for owner, ref, pid, bounded, left, right in auxiliary[table]:
            if left > right or (product_id is not None and product_id != pid):
                continue
            root = str(owner.root.resolve())
            allowed = fields_by_product[root, ref.snapshot_id, table, pid]
            permitted = set(fields) - {"product_id", "source_ref"} <= allowed
            if not permitted and not (table == "calendar" and (root, ref.snapshot_id, pid) in calendar_replacements):
                continue
            codes = bounded if subjects is None else tuple(code for code in bounded if code in subjects)
            frames.append(read_scoped_product(
                owner, ref, table, codes=codes, start=left, end=right,
                columns=fields if permitted else ("date", "product_id"), product_id=pid,
            ).assign(source_root=root, snapshot_id=ref.snapshot_id, _permitted=permitted))
        if not frames:
            return pd.DataFrame(columns=fields)
        merged = pd.concat(frames, ignore_index=True)
        if table == "calendar" and manifest is not None:
            merged = _select_revisions(merged, table, calendar_rules)
        return merged.loc[merged["_permitted"]].reindex(columns=fields)

    source_exit_rule = bool(policy and policy.get("tushare_delist_date_exclusive") is True)
    if not statements:
        if latest_only:
            return pd.DataFrame(columns=columns), 0, (), {"latest": {}}
        if not authorized:
            coverage = dict(actual=0, denominator=None, reason="无获准日线")
            return pd.DataFrame(columns=columns), 0, (), coverage
        summary = pd.DataFrame(columns=["code", "date", "_trade", "_incomplete", "_conflict"])
        gaps, _ = _archive_query_details(
            read, summary, codes, selection.start, selection.end, tushare_delist_date_exclusive=source_exit_rule,
        )
        records = tuple(gaps.to_dict("records"))
        calendar = read("calendar", ("date", "is_trading_day", "source_ref", "product_id"))
        return (
            pd.DataFrame(columns=columns),
            0,
            records,
            _coverage(codes, selection.start, selection.end, set(), calendar, records),
        )

    missing = " OR ".join(f"{field} IS NULL OR isnan({field})" for field in _BAR_FIELDS)
    valid_trade = f"""(NOT ({missing}) AND least(open_raw, high_raw, low_raw, close_raw) > 0
              AND high_raw >= greatest(open_raw, low_raw, close_raw)
              AND low_raw <= least(open_raw, high_raw, close_raw)
              AND volume_shares >= 0 AND amount_cny >= 0) OR volume_shares > 0 OR amount_cny > 0"""
    with duckdb.connect() as db:
        db.execute("CREATE TEMP TABLE bars AS " + " UNION ALL ".join(statements), args)
        if revisions:
            _discard_superseded_rows(db, "bars", revisions, day="date", product="product_id", code="code")
            db.execute("DELETE FROM bars WHERE len(visible_fields) = 0")
        if manifest is not None:
            for asset in manifest["asset_snapshots"]:
                if asset.get("original_root"):
                    db.execute(
                        "UPDATE bars SET source_root = ? WHERE source_root = ? AND snapshot_id = ?",
                        [asset["original_root"], asset["root"], asset["snapshot_id"]],
                    )
        relation = "bars"
        show_reference = "source_ref" in selection.columns
        visible_reference = "list_contains(visible_fields, 'source_ref') AND nullif(trim(source_ref), '') IS NOT NULL"
        reference_origin = f"""CASE WHEN {visible_reference}
            THEN struct_pack(source_ref := source_ref, source_root := source_root,
                snapshot_id := snapshot_id, product_id := product_id) END"""
        reference_projection = reference_origin
        if manifest is not None and needs_full_row:
            fact_keys = ",".join(f'"{field}"' for field in archive_fields if field != "source_ref")
            quote_order = f"""list_contains(visible_fields, 'date') DESC,
                list_contains(visible_fields, 'code') DESC,
                ({visible_reference if show_reference else 'false'}) DESC, asset_order, source_ref"""
            projected = "*"
            if show_reference:
                # The chosen row still provides the navigation coordinates. An equivalent
                # row can provide a separately authorized reference, with its own origin.
                projected += f""", first_value(({reference_origin}) IGNORE NULLS) OVER
                    (PARTITION BY {fact_keys} ORDER BY {quote_order}
                    ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS _quote_source"""
                reference_projection = f"coalesce(_quote_source, {reference_origin})"
            db.execute(f"""CREATE TEMP TABLE distinct_bars AS SELECT {projected} FROM bars
                QUALIFY row_number() OVER (PARTITION BY {fact_keys} ORDER BY {quote_order})=1""")
            relation = "distinct_bars"
        special = set()
        adjudicated_gaps = ()
        if policy:
            status_rule = policy.get("archive_status_rule", {})
            exact_keys = (
                set(policy.get("object_days", ()))
                | set(policy.get("observation_days", ()))
                | set(status_rule.get("object_days", ()))
                | set(status_rule.get("accepted_exceptions", ()))
            )
            special = {
                (code, day)
                for key in exact_keys
                for code, day in [key.split("/", 1)]
                if code in codes and selection.start.isoformat() <= day <= selection.end.isoformat()
            }
            if special:
                db.register("requested_policy_days", pd.DataFrame(sorted(special), columns=["code", "date"]))
                # The archive policy needs the complete quote for each participating source on that date.
                db.execute("""CREATE TEMP TABLE policy_days AS
                    SELECT requested.code, requested.date
                    FROM requested_policy_days requested LEFT JOIN bars USING (code, date)
                    GROUP BY requested.code, requested.date
                    HAVING coalesce(bool_and(list_has_all(bars.visible_fields, ?)), true)""", [_BAR_FIELDS])
                special = set(db.execute("SELECT code, date FROM policy_days").fetchall())
            if special:
                archive_projection = (*ARCHIVE_COLUMNS, "source_root", "snapshot_id")
                if show_reference:
                    # Keep the authorized reference attached to the exact quote through adjudication.
                    archive_projection += ("_quote_source",)
                original = ",".join(f'bars."{field}"' for field in archive_projection)
                needed = db.execute(f"""SELECT {original} FROM {relation} bars
                    JOIN policy_days USING (code, date)
                    ORDER BY bars.code,bars.date,bars.product_id,bars.source_root,bars.snapshot_id,bars.source_ref
                    """).df()
                by_asset = {}
                limits = {}
                auxiliary_products = {table: {} for table in auxiliary}
                for table, entries, target in (
                    ("daily_bars", authorized, by_asset),
                    *((table, entries, auxiliary_products[table]) for table, entries in auxiliary.items()),
                ):
                    for owner, ref, product, subjects, left, right in entries:
                        key = (str(owner.root.resolve()), ref.snapshot_id)
                        target.setdefault(key, []).append(product)
                        limits[(*key, table, product)] = subjects, left, right
                start, end = selection.start, selection.end
                if latest_only:
                    days = [day for _, day in special]
                    start, end = date.fromisoformat(min(days)), date.fromisoformat(max(days))
                result = query_library(
                    store,
                    selection.source,
                    codes=codes,
                    start=start,
                    end=end,
                    daily_products=by_asset,
                    auxiliary_products=auxiliary_products,
                    browse_limits=limits,
                    browse_fields=fields_by_product,
                    _scoped_bars=needed,
                )
                adjudicated_gaps = tuple(result["gaps"].to_dict("records"))
                db.execute("""CREATE TEMP TABLE bar_permissions AS
                    SELECT DISTINCT source_root, snapshot_id, product_id, visible_fields FROM bars""")
                db.execute(f"""DELETE FROM {relation} bars USING policy_days
                    WHERE bars.code=policy_days.code AND bars.date=policy_days.date""")
                db.register("adjudicated_bars", result["bars"])
                fields = ",".join(f'"{field}"' for field in archive_projection)
                db.execute(f"""INSERT INTO {relation} ({fields}, asset_order, visible_fields)
                    SELECT {fields}, 0, coalesce(visible_fields, []::VARCHAR[])
                    FROM adjudicated_bars LEFT JOIN bar_permissions USING (source_root, snapshot_id, product_id)""")
        selected_value = _selected_bar_value(selection.columns)
        if product_boundaries:
            bounds = db.execute(
                f"SELECT min(date), max(date) FROM {relation} WHERE ({selected_value})"
            ).fetchone()
            return pd.DataFrame(columns=columns), 0, (), {"product_range": bounds}
        has_code = "list_contains(visible_fields, 'code')"
        dated_subject = f"{has_code} AND ({selected_value})"
        if latest_only:
            dates = db.execute(
                f"SELECT code, max(date) FROM {relation} WHERE ({dated_subject}) GROUP BY code"
            ).fetchall()
            return pd.DataFrame(columns=columns), 0, (), {"latest": dict(dates)}
        count = db.execute(f"SELECT count(*) FROM {relation}").fetchone()[0]
        # Only per-day flags cross the query boundary for calendar and suspension checks.
        missing_fields = {
            field: f"(list_contains(visible_fields, '{field}') AND ({field} IS NULL OR isnan({field})))"
            for field in _BAR_FIELDS
        }
        incomplete = " OR ".join(missing_fields.values())
        has_date = "list_contains(visible_fields, 'date')"
        undated_missing = ", ".join(
            f"CASE WHEN bool_or(NOT {has_date} AND {check}) THEN '{field}' END"
            for field, check in missing_fields.items()
        )
        conflict = f'count(DISTINCT ({",".join(_BAR_FIELDS)})) > 1'
        if restricted:
            conflict = " OR ".join(
                f"(count(DISTINCT struct_pack(value := {field})) "
                f"FILTER (WHERE list_contains(visible_fields, '{field}')) > 1)"
                for field in _BAR_FIELDS
            )
        summary = db.execute(f"""SELECT code, date, coalesce(bool_or({valid_trade}), false) AS _trade,
            bool_or({incomplete}) AS _incomplete, {conflict} AS _conflict,
            bool_or(NOT {has_date}) AS _has_undated_bar,
            bool_or(NOT {has_code}) AS _has_unidentified_bar,
            bool_or({has_date} AND ({incomplete})) AS _dated_incomplete,
            list_filter(list_value({undated_missing}), field -> field IS NOT NULL) AS _undated_missing,
            coalesce(bool_or({dated_subject}), false) AS _has_selected_value
            FROM {relation} GROUP BY code,date""").df()
        sort = selection.sort or "date"
        tie = ", ".join(
            f'"{field}" ASC'
            for field in (
                "date",
                "code",
                "product_id",
                "source_root",
                "snapshot_id",
                "source_ref",
                "asset_order",
                *_BAR_FIELDS,
            )
            if field != sort
        )
        direction = "DESC" if selection.descending else "ASC"
        # Keep coordinates for matching internally, and apply field grants to the returned page.
        selected_fields = ",".join(
            f"""CASE WHEN list_contains(visible_fields, '{field}') THEN "{field}" END AS "{field}" """
            if field in ("source_ref", "date", "code") else f'"{field}"'
            for field in columns
        )
        annotate_amounts = describe_amounts is not None and "amount_cny" in selection.columns
        if annotate_amounts:
            selected_fields += ", source_ref AS _amount_source_ref, date AS _amount_date, code AS _amount_code"
        if show_reference:
            selected_fields += f", {reference_projection} AS source_ref_origin"
        rows = db.execute(
            f"""SELECT {selected_fields} FROM {relation}
            ORDER BY "{sort}" {direction}, {tie} LIMIT ? OFFSET ?""",
            [page_size, (page - 1) * page_size],
        ).df()
    if annotate_amounts:
        # Match the independent amount evidence before handing the permitted projection to the UI.
        source_refs = rows.pop("_amount_source_ref")
        amount_dates = rows.pop("_amount_date")
        amount_codes = rows.pop("_amount_code")
        if rows["amount_cny"].notna().any():
            observations = rows.assign(date=amount_dates, code=amount_codes)
            rows = rows.join(describe_amounts(selection, observations, source_refs))
    if show_reference:
        # Amount evidence must be matched against the selected quote, before displaying a copy's reference.
        origins = rows["source_ref_origin"]
        rows["source_ref"] = origins.map(lambda origin: origin["source_ref"], na_action="ignore")
    gaps, _ = _archive_query_details(
        read,
        summary,
        codes,
        selection.start,
        selection.end,
        tushare_delist_date_exclusive=source_exit_rule,
    )
    records = tuple(gaps.to_dict("records"))
    if special:
        # Use the existing archive decisions only on their identified object-days.
        records = (*_gap_days(records, special, keep=False), *_gap_days(adjudicated_gaps, special, keep=True))
    calendar = read("calendar", ("date", "is_trading_day", "source_ref", "product_id"))
    observed = set(summary[["code", "date"]].itertuples(index=False, name=None))
    coverage = _coverage(codes, selection.start, selection.end, observed, calendar, records)
    coverage["latest"] = summary.loc[summary["_has_selected_value"]].groupby("code")["date"].max().to_dict()
    public_gaps = _visible_bar_gaps(records, summary)
    if summary["_has_unidentified_bar"].any():
        anonymous = []
        for gap in public_gaps:
            view = {key: value for key, value in gap.items() if key != "code"}
            if view not in anonymous:
                anonymous.append(view)
        public_gaps = tuple(anonymous)
    return rows, count, public_gaps, coverage


def _visible_bar_gaps(gaps, summary):
    undated = summary.loc[summary["_has_undated_bar"]]
    if undated.empty:
        return gaps
    hidden_days = set(undated[["code", "date"]].itertuples(index=False, name=None))
    hidden_codes = set(undated["code"])
    dated_incomplete = set(summary.loc[summary["_dated_incomplete"], ["code", "date"]]
                           .itertuples(index=False, name=None))
    missing_by_code = {}
    for code, fields in undated[["code", "_undated_missing"]].itertuples(index=False, name=None):
        missing_by_code.setdefault(code, set()).update(fields)
    visible, restricted = [], {}
    # The presence of a restricted row can also change the intervals reported for *absent* rows.
    absence_reasons = {"calendar_unknown", "missing_bar", "not_listed", "after_exit", "exit_boundary_unknown"}
    for gap in gaps:
        code, reason = gap["code"], gap["reason"]
        if code not in hidden_codes or reason in {"lifecycle_unknown", "confirmed_intraday_halt"}:
            visible.append(gap)
        elif reason == "fields_missing":
            visible.extend(_gap_days((gap,), dated_incomplete, keep=True))
            if fields := missing_by_code[code]:
                restricted[code, reason] = dict(fields=sorted(fields))
        elif reason in absence_reasons:
            restricted[code, reason] = {}
        else:
            visible.extend(_gap_days((gap,), hidden_days, keep=False))
            if any(_gap_days((gap,), hidden_days, keep=True)):
                restricted[code, reason] = {}
    visible.extend(
        dict(code=code, reason=reason, date_restricted=True, **details)
        for (code, reason), details in restricted.items()
    )
    return tuple(visible)


def _gap_days(gaps, special, *, keep):
    affected = {code for code, _ in special}
    for gap in gaps:
        if gap["code"] not in affected:
            if not keep:
                yield gap
            continue
        start, end = date.fromisoformat(gap["start"]), date.fromisoformat(gap["end"])
        run = None
        for offset in range((end - start).days + 2):
            day = start + timedelta(days=offset)
            match = day <= end and (((gap["code"], day.isoformat()) in special) == keep)
            if match and run is None:
                run = day
            elif not match and run is not None:
                yield dict(gap, start=run.isoformat(), end=(day - timedelta(days=1)).isoformat())
                run = None



def _latest_permitted_bars(store, selection, scopes, manifest):
    """Find the most recent permitted evidence before the requested interval."""
    if selection.start == date.min:
        return {}
    earliest = replace(selection, start=date.min, end=selection.start - timedelta(days=1))
    history = [dict(scope, **scope.get("history", {})) for scope in scopes]
    if not any(
        scope["table"] == "daily_bars" and scope["allowed"] and "date" in scope["fields"]
        and "code" in scope["fields"]
        and (not selection.product_id or scope["product_id"] == selection.product_id)
        for scope in history
    ):
        return {}
    _, _, _, coverage = _scoped_bar_page(store, earliest, history, manifest, 1, 1, latest_only=True)
    return coverage["latest"]


def _scoped_bar_page(store, selection, scopes, manifest, page, page_size, **options):
    auxiliary = {
        table: list(_authorized_scopes(store, selection, scopes, table))
        for table in ("calendar", "security_status", "securities", "security_events")
    }
    revisions = [rule for rule in (manifest or {}).get("revision_selections", ()) if rule["table"] == "daily_bars"]
    authorized = list(_authorized_scopes(
        store, selection, scopes, "daily_bars", revision_assets=_revision_assets(revisions),
    ))
    fields_by_product = {
        (scope["root"], scope["snapshot_id"], scope["table"], scope["product_id"]): set(scope["fields"])
        for scope in scopes
    }
    return _bar_page_sql(
        store, selection, authorized, auxiliary, manifest, fields_by_product, page, page_size,
        revisions=revisions, **options,
    )


def _bars(store, selection, scopes, page, page_size, *, describe_amounts=None, range_cache=None):
    manifest = load_library(store, selection.source) if isinstance(selection.source, LibraryRef) else None
    rows, count, gaps, coverage = _scoped_bar_page(
        store, selection, scopes, manifest, page, page_size, describe_amounts=describe_amounts,
    )
    date_permissions = {
        "date" in scope["fields"] for scope in scopes
        if scope["table"] == "daily_bars" and scope["allowed"]
        and (not selection.product_id or scope["product_id"] == selection.product_id)
    }
    coverage["date_field_restricted"] = False in date_permissions
    coverage["code_field_restricted"] = any(
        scope["table"] == "daily_bars" and scope["allowed"] and "code" not in scope["fields"]
        and (not selection.product_id or scope["product_id"] == selection.product_id)
        for scope in scopes
    )
    if date_permissions == {False}:
        gaps = tuple({key: value for key, value in gap.items() if key not in ("start", "end")} for gap in gaps)
    latest = coverage.setdefault("latest", {})
    missing = tuple(code for code in selection.subjects
                    if code not in latest and (not selection.market or code.rsplit(".", 1)[-1] == selection.market))
    if missing:
        latest.update(_latest_permitted_bars(store, replace(selection, subjects=missing), scopes, manifest))
    if selection.product_id:
        coverage["product_range"] = _product_range(
            store, selection, scopes, "date", manifest, range_cache,
        )
    return rows, count, gaps, coverage


def _axis_day(axis):
    if axis in ("period", "report_period"):
        return f"""COALESCE(TRY_CAST("{axis}" AS DATE),
        CASE WHEN regexp_full_match("{axis}", '[0-9]{{4}}Q[1-4]') THEN LAST_DAY(TRY_CAST(CONCAT(
            SUBSTR("{axis}", 1, 4), '-', LPAD(CAST(3 * TRY_CAST(RIGHT("{axis}", 1) AS INTEGER)
            AS VARCHAR), 2, '0'), '-01') AS DATE)) END)"""
    return f'''TRY_CAST(SUBSTR(CAST("{axis}" AS VARCHAR), 1, 10) AS DATE)'''


def _registered_rows(store, selection, scopes, page, page_size, registration_scope, *, range_only=False):
    table = selection.table
    columns = TABLE_SCHEMAS[table]["columns"]
    has_codes = "code" in columns
    axis = selection.axis or next(iter(_axes(table)), "")
    sort = selection.sort or axis or next(iter(selection.columns))
    clock = next((key for key in ("date", "ex_date") if key in columns), None)
    manifest = load_library(store, selection.source) if clock and isinstance(selection.source, LibraryRef) else {}
    revisions = [rule for rule in manifest.get("revision_selections", ()) if rule["table"] == table]
    statements, args = [], []
    eligible_registrations, gaps = {}, []
    projection = tuple(
        dict.fromkeys(
            (
                *selection.columns,
                sort,
                *(field for field in ("code", axis, "product_id", "source_ref") if field in columns),
            )
        )
    )
    registration_subject = """CASE
        WHEN starts_with(table_name, 'factor_input.')
            AND table_name = 'factor_input.' || split_part(record_key, '|', 1)
            AND split_part(record_key, '|', 3) <> ''
        THEN NULLIF(split_part(record_key, '|', 2), '')
        WHEN NOT starts_with(table_name, 'factor_input.')
            AND split_part(record_key, '|', 3) <> ''
            AND (TRY_CAST(split_part(record_key, '|', 2) AS DATE) IS NOT NULL
                OR regexp_full_match(split_part(record_key, '|', 2), '[0-9]{4}Q[1-4]'))
        THEN NULLIF(split_part(record_key, '|', 1), '') END"""
    if table == "knowledge_evidence":
        projection = tuple(dict.fromkeys((*projection, "table_name", "record_key")))
    fields_by_product = {
        (scope["root"], scope["snapshot_id"], scope["product_id"]): scope["fields"]
        for scope in scopes if scope["table"] == table
    }
    authorized = _authorized_scopes(store, selection, scopes, table, revision_assets=_revision_assets(revisions))
    for owner, ref, product, subjects, left, right in authorized:
        permitted = fields_by_product[(str(owner.root.resolve()), ref.snapshot_id, product)]
        paid = owner.product(product).get("fee_class") == "paid"
        registration_readable = {"table_name", "record_key"}.issubset(permitted)
        if (
            table == "knowledge_evidence" and (paid or selection.subjects or selection.market)
            and not registration_readable
        ):
            if paid:
                gaps.append(dict(product=product, reason="凭据登记对象与业务期间所需字段未获准"))
            continue
        separate_clock = separate_grant_day(table, axis)
        business_scope = separate_clock or table == "knowledge_evidence"
        bounded = _product_read_scope(
            owner,
            product,
            codes=subjects if has_codes else None,
            start=date.min if business_scope else left,
            end=date.max if business_scope else right,
        )
        if bounded is None:
            continue
        subjects, grant_left, grant_right = bounded
        if not business_scope:
            left, right = grant_left, grant_right
        paths = owner._range_files(
            ref,
            table,
            codes=subjects if has_codes and subjects else None,
            start=left if axis in ("date", "ex_date") else None,
            end=right if axis in ("date", "ex_date") else None,
            product_id=product,
        )
        if not paths:
            continue
        filters = ["product_id = ?"]
        params = [str(owner.root.resolve()), ref.snapshot_id, paths, product]
        if has_codes and subjects:
            filters.append('"code" IN (SELECT unnest(?))')
            params.append(list(subjects))
        if table == "knowledge_evidence":
            if selection.subjects:
                filters.append(f"({registration_subject}) IN (SELECT unnest(?))")
                params.append(list(selection.subjects))
            if selection.market:
                filters.append(f"split_part(({registration_subject}), '.', -1) = ?")
                params.append(selection.market)
        time_filters, time_args = [], []
        interval_end = {"valid_from": "valid_to", "effective_from": "effective_to", "ipo_date": "out_date"}.get(axis)
        if interval_end and interval_end in columns:
            time_filters += [f'"{axis}" <= ?', f"""(NULLIF("{interval_end}", '') IS NULL OR "{interval_end}" >= ?)"""]
            time_args += [right.isoformat(), left.isoformat()]
        elif axis in ("period", "report_period"):
            time_filters.append(f"{_axis_day(axis)} BETWEEN ? AND ?")
            time_args += [left, right]
        elif axis:
            time_filters.append(f'SUBSTR(CAST("{axis}" AS VARCHAR), 1, 10) BETWEEN ? AND ?')
            time_args += [left.isoformat(), right.isoformat()]
        display_period = "TRUE"
        if revisions and axis != clock and time_filters:
            # Keep replacements on their registered business coordinates even when they fall outside the UI axis.
            display_period = " AND ".join(time_filters)
        else:
            filters.extend(time_filters)
            params.extend(time_args)
            time_args = []
        if paid:
            if separate_clock:
                filters.append(f"{_axis_day(separate_clock)} BETWEEN ? AND ?")
                params += [grant_left, grant_right]
            elif "date" in columns and axis != "date":
                filters.append('"date" BETWEEN ? AND ?')
                params += [left.isoformat(), right.isoformat()]
            if table == "share_exchanges":
                grant = owner._grant(product) or {}
                approved = [
                    code for code in grant.get("codes", ()) if code.rsplit(".", 1)[-1] in grant.get("markets", ())
                ]
                filters += [
                    "from_code IN (SELECT unnest(?))",
                    "to_code IN (SELECT unnest(?))",
                    "effective_date BETWEEN ? AND ?",
                ]
                params += [approved, approved, left.isoformat(), right.isoformat()]
            if table == "knowledge_evidence":
                if registration_scope is None:
                    continue
                grant = owner._grant(product) or {}
                approved = [
                    code for code in grant.get("codes", ()) if code.rsplit(".", 1)[-1] in grant.get("markets", ())
                ]
                filters.append(f"({registration_subject}) IN (SELECT unnest(?))")
                params.append(approved)
                keys = ("table_name", "record_key", "revision_id", "published_at", "evidence_ref", "sha256")
                filters.append("lower(CAST(verified AS VARCHAR)) IN ('true', '1')")
                with duckdb.connect() as verification:
                    candidate_fields = ", ".join(keys)
                    candidates = verification.execute(
                        f"SELECT {candidate_fields}, product_id FROM read_parquet(?, union_by_name=true) "
                        f"WHERE {' AND '.join(filters)}", params[2:],
                    ).df()
                covered, reasons = registration_scope(owner, ref, product, candidates)
                gaps.extend(dict(product=product, reason=reason) for reason in sorted(reasons))
                if covered.empty:
                    continue
                binding = f"eligible_registrations_{len(eligible_registrations)}"
                eligible_registrations[binding] = covered.loc[:, list(keys)].drop_duplicates()
                same_registration = " AND ".join(f"permitted.{key} = entry.{key}" for key in keys)
                filters.append(f"EXISTS (SELECT 1 FROM {binding} permitted WHERE {same_registration})")
        displayable = _can_display(selection, permitted)
        if range_only:
            day = _axis_day(axis) if displayable else "NULL::DATE"
            # A validity interval can overlap the grant while its original start lies outside it.
            if interval_end and interval_end in columns:
                filters.append(f"{day} BETWEEN ? AND ?")
                params.extend([left, right])
            checked = f"{day} AS product_day"
        else:
            checked = ",".join(
                query_column(table, field) if displayable and field in permitted else f'NULL AS "{field}"'
                for field in projection
            )
            if table == "knowledge_evidence":
                checked += f", ({registration_subject if registration_readable else 'NULL'}) AS code"
        revision_projection = ""
        if revisions:
            code_key = '"code"' if has_codes else "NULL"
            revision_projection = (
                f', "{clock}" AS _revision_day, product_id AS _revision_product, {code_key} AS _revision_code, '
                f'{str(displayable).upper()} AND ({display_period}) AS _revision_visible'
            )
            params[2:2] = time_args
        statements.append(
            f"SELECT {checked}, ? AS source_root, ? AS snapshot_id {revision_projection} "
            f"FROM read_parquet(?, union_by_name=true) entry WHERE {' AND '.join(filters)}"
        )
        args.extend(params)
    if not statements:
        if range_only:
            return None, None
        return pd.DataFrame(columns=projection), 0, tuple(gaps)
    query = " UNION ALL ".join(statements)
    direction = "DESC" if selection.descending else "ASC"
    tie_break = ", ".join(
        f'"{field}" ASC' for field in dict.fromkeys((*projection, "source_root", "snapshot_id")) if field != sort
    )
    with duckdb.connect() as db:
        for name, registrations in eligible_registrations.items():
            db.register(name, registrations)
        db.execute(f"CREATE TEMP TABLE matches AS {query}", args)
        if revisions:
            _discard_superseded_rows(
                db, "matches", revisions, day="_revision_day", product="_revision_product",
                code="_revision_code" if has_codes else None,
            )
            db.execute("DELETE FROM matches WHERE NOT coalesce(_revision_visible, false)")
        if range_only:
            return db.execute(
                "SELECT CAST(min(product_day) AS VARCHAR), CAST(max(product_day) AS VARCHAR) FROM matches",
            ).fetchone()
        count = db.execute("SELECT count(*) FROM matches").fetchone()[0]
        exposed = "*"
        if revisions:
            exposed += " EXCLUDE (_revision_day, _revision_product, _revision_code, _revision_visible)"
        frame = db.execute(
            f'SELECT {exposed} FROM matches ORDER BY "{sort}" {direction}, {tie_break} LIMIT ? OFFSET ?',
            [page_size, (page - 1) * page_size],
        ).df()
    return frame, count, tuple(gaps)


def _product_range(store, selection, scopes, axis, manifest, range_cache, registration_scope=None):
    if not axis:
        return None, None
    expanded = [dict(scope, **scope["product_range"]) for scope in scopes if "product_range" in scope]
    entire_product = replace(
        selection, start=date.min, end=date.max, axis=axis, columns=(axis,), sort="", descending=False,
    )
    key = entire_product, expanded, manifest
    # Free registration ranges depend only on the fixed publication and effective scopes.
    # Paid ranges additionally depend on live verification of each original.
    if selection.table == "knowledge_evidence" and registration_scope is not None and any(
        owner.product(product).get("fee_class") == "paid"
        for owner, _, product, _, _, _ in _authorized_scopes(store, entire_product, expanded, selection.table)
    ):
        range_cache = None
    if range_cache is not None and range_cache.get("key") == key:
        return range_cache["bounds"]
    if selection.table == "daily_bars":
        _, _, _, available = _scoped_bar_page(
            store, entire_product, expanded, manifest, 1, 1, latest_only=True, product_boundaries=True,
        )
        bounds = available.get("product_range", (None, None))
    else:
        bounds = _registered_rows(store, entire_product, expanded, 1, 1, registration_scope, range_only=True)
    if range_cache is not None:
        range_cache.update(key=key, bounds=bounds)
    return bounds


def browse_page(
    store, selection: BrowseSelection, *, scopes: tuple[dict, ...], page: int, page_size: int,
    describe_amounts=None, registration_scope=None, range_cache=None,
) -> BrowsePage:
    columns = (
        ARCHIVE_COLUMNS
        if selection.table == "daily_bars"
        else (TABLE_SCHEMAS.get(selection.table, {}).get("columns", ()))
    )
    if (
        not selection.columns
        or not set(selection.columns).issubset(columns)
        or (selection.axis and selection.axis not in _axes(selection.table))
        or (selection.sort and selection.sort not in columns)
    ):
        raise ValueError("未登记的表、字段或日期口径")
    if not (
        page >= 1
        and 1 <= page_size <= 200
        and 0 <= (selection.end - selection.start).days <= 366
        and len(selection.subjects) <= 30
    ):
        raise ValueError("请选择最多 30 个对象、366 天及不超过 200 行的分页范围")
    if "code" in columns and not selection.subjects:
        raise ValueError("请选择至少一个观察对象")
    if selection.table == "daily_bars":
        rows, count, gaps, coverage = _bars(store, selection, scopes, page, page_size,
                                          describe_amounts=describe_amounts, range_cache=range_cache)
    else:
        rows, count, gaps = _registered_rows(store, selection, scopes, page, page_size, registration_scope)
        coverage = dict(actual=count, denominator=None, reason="此资料没有独立分母")
        if selection.product_id:
            axis = selection.axis or next(iter(_axes(selection.table)), "")
            manifest = load_library(store, selection.source) if isinstance(selection.source, LibraryRef) else None
            coverage["product_range"] = _product_range(
                store, selection, scopes, axis, manifest, range_cache, registration_scope,
            )
    denied = tuple(
        dict(
            reason=entry["reason"]
            if not entry["allowed"]
            else "排序字段未获准"
            if selection.sort and selection.sort not in entry["fields"]
            else "展示字段未获准",
            product=entry["product_id"],
            **({"fields": sorted(set(selection.columns) - set(entry["fields"]))}
               if entry["allowed"] and set(selection.columns) - set(entry["fields"]) else {}),
        )
        for entry in scopes
        if entry["table"] == selection.table
        and (not selection.product_id or entry["product_id"] == selection.product_id)
        and (
            not entry["allowed"]
            or not set(selection.columns).issubset(entry["fields"])
            or (selection.sort and selection.sort not in entry["fields"])
        )
    )
    if not count and not denied:
        reason = (
            "没有匹配的凭据登记，或登记对象归属未证"
            if selection.table == "knowledge_evidence"
            else "已登记输入在所选对象或日期范围无行"
        )
        gaps = (*gaps, {"reason": reason})
    return BrowsePage(
        rows,
        count,
        (*gaps, *denied),
        dict(coverage, start=selection.start, end=selection.end, count=count),
    )


def lookup_objects(store, selection, *, scopes, needle):
    """Find a bounded set of names/codes from permitted identity registrations."""
    needle = needle.strip().casefold()
    if not needle:
        return []
    queries, args = [], []
    permitted_fields = {
        (scope["root"], scope["snapshot_id"], scope["table"], scope["product_id"]): set(scope["fields"])
        for scope in scopes
    }
    for table in ("securities", "historical_securities", "memberships", "boards"):
        columns = TABLE_SCHEMAS[table]["columns"]
        code = "code" if "code" in columns else "board_id"
        for owner, ref, product, _, left, right in _authorized_scopes(store, selection, scopes, table):
            permitted = permitted_fields[(str(owner.root.resolve()), ref.snapshot_id, table, product)]
            if code not in permitted:
                continue
            name_allowed = "name" in columns and "name" in permitted
            label = '"name"' if name_allowed else "'名称受限'" if "name" in columns else f'"{code}"'
            subjects, left, right = _product_read_scope(owner, product, codes=(), start=left, end=right) or (
                (),
                date.max,
                date.min,
            )
            if left > right:
                continue
            paths = owner._range_files(ref, table, codes=subjects or None, product_id=product)
            if not paths:
                continue
            match = f'contains(lower("{code}"), ?)'
            needles = [needle]
            if name_allowed:
                match += ' OR contains(lower("name"), ?)'
                needles.append(needle)
            filters = [f"({match})", "product_id = ?"]
            values = [paths, *needles, product]
            if subjects:
                filters.append(f'"{code}" IN (SELECT unnest(?))')
                values.append(list(subjects))
            span = {
                "securities": ("ipo_date", "out_date"),
                "historical_securities": ("valid_from", "valid_to"),
                "memberships": ("effective_from", "effective_to"),
            }.get(table)
            if span and set(span).issubset(columns):
                begin, end = span
                filters += [
                    f"""(NULLIF("{begin}", '') IS NULL OR "{begin}" <= ?)""",
                    f"""(NULLIF("{end}", '') IS NULL OR "{end}" >= ?)""",
                ]
                values += [right.isoformat(), left.isoformat()]
            queries.append(
                f'SELECT "{code}" AS code, {label} AS name, ? AS product FROM '
                f"read_parquet(?, union_by_name=true) WHERE {' AND '.join(filters)}"
            )
            # The constant in SELECT precedes the path parameter.
            args.extend([product, *values])
    if not queries:
        return []
    with duckdb.connect() as conn:
        result = conn.execute(
            f"SELECT DISTINCT * FROM ({' UNION ALL '.join(queries)}) ORDER BY name, code, product LIMIT 30", args
        ).fetchall()
    return result
