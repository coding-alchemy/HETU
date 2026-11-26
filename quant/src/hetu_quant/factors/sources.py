"""Cutoff and use checks before factor formulas receive input."""

import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from functools import cache
from importlib.resources import files
from itertools import product
from pathlib import Path
from urllib.parse import urlsplit

import pandas as pd
from hetu_data.library import _read_close_attestations, load_library, selected_snapshot_assets

from hetu_quant.backtest_data import _permission_sources, _purpose_allowed
from hetu_quant.data import TABLE_SCHEMAS, AccessDenied
from hetu_quant.models import SnapshotRef

from .core import (
    FactorFacts,
    MissingFact,
    TeachingSource,
    field_attribute,
    financial_amount_in_yuan,
    iso_day,
    money_in_yuan,
    net_assets_unit,
    number,
    price_per_share,
    quotation,
    share_count,
    yuan_per_stock_share,
)


def _public_instant(value):
    try:
        stamp = datetime.fromisoformat(str(value))
        return stamp if stamp.utcoffset() is not None else None
    except ValueError:
        return None


def _known(value, cutoff):
    stamp = _public_instant(value)
    return stamp is not None and stamp <= cutoff


def _same_public_instant(left, right):
    stamp = _public_instant(left)
    return stamp is not None and stamp == _public_instant(right)


def teaching_document(source):
    if Path(source.case_id).name != source.case_id or source.case_id.startswith("."):
        raise ValueError("无效的教学样例标识")
    folder = source.root if source.root is not None else files(__package__).joinpath("examples")
    return json.loads(folder.joinpath(f"{source.case_id}.json").read_text(encoding="utf-8"))


_TEACHING_REVISION_LINKS = "examples/_revision_links.json"


@cache
def _teaching_revision_links():
    return json.loads(files(__package__).joinpath(_TEACHING_REVISION_LINKS).read_text(encoding="utf-8"))


def _link_legacy_teaching_revisions(versions, cutoff):
    for link in _teaching_revision_links():
        table = link["table"]
        rows = versions.get(table, ())
        if not rows or not _known(link["replacement"]["available_at"], cutoff):
            continue
        canonical = [dict(row, available_at=_public_instant(row.get("available_at"))) for row in rows]
        originals = [dict(row, available_at=_public_instant(row["available_at"]))
                     for row in (link["previous"], link["replacement"])]
        matches = [[i for i, row in enumerate(canonical)
                    if all(row.get(key) == value for key, value in original.items())] for original in originals]
        if any(not indices or any(not _same_teaching_record(canonical[i], canonical[indices[0]]) for i in indices)
               for indices in matches):
            continue
        endpoints = [i for indices in matches for i in indices]
        if any(rows[i].get("coordinate") or rows[i].get("fact_key") for i in endpoints):
            continue
        linked = list(rows)
        evidence = f"{_TEACHING_REVISION_LINKS}#{link['id']}"
        for i in endpoints:
            linked[i] = dict(rows[i], coordinate=link["coordinate"],
                             _teaching_revision_scope=(evidence,), revision_evidence_ref=evidence)
        versions[table] = linked


def _same_teaching_record(left, right):
    return left == right and all(type(value) is type(right[key]) for key, value in left.items())


def _fact_key(row):
    return row.get("fact_key") or (
        row.get("code"),
        row.get("date"),
        row.get("market"),
        row.get("period"),
        row.get("event_id"),
        row.get("member"),
        row.get("institution"),
        row.get("field"),
        row.get("kind"),
        row.get("ex_date"),
        row.get("action_type"),
        row.get("valid_from"),
    )


_PROVENANCE_KEYS = {
    "announcement_ref",
    "available_at",
    "observed_at",
    "observed_value",
    "observed_source_ref",
    "observation_status",
    "revision_id",
    "supersedes",
    "evidence_ref",
    "revision_evidence_ref",
    "disclosure_revisions",
    "registered_table",
    "source_ref",
    "product_id",
    "snapshot_id",
    "source_root",
    "field_evidence",
    "fact_key",
    "coordinate",
    "corroborating_sources",
    "_conflicts",
    "_revision_cycles",
    "_distribution_branch",
    "_distribution_copy",
    "_event_copy_key",
    "_event_forks",
    "_teaching_revision_scope",
    "matched",
}

_QUOTE_ATTRIBUTES = (
    "currency", "unit", "market", "calendar_id", "close_timestamp", "close_at", "close_time", "return_basis",
)
_FINANCIAL_CONTEXT = ("flow_basis", "accounting_scope", "first_disclosure")


def _origin(row):
    return {
        key: row[key]
        for key in sorted(_PROVENANCE_KEYS)
        if key in row and key not in (
            "field_evidence", "corroborating_sources", "_distribution_copy", "_event_copy_key", "_event_forks",
        )
    }


def _same_facts(left, right):
    left_facts = {field: value for field, value in left.items() if field not in _PROVENANCE_KEYS}
    right_facts = {field: value for field, value in right.items() if field not in _PROVENANCE_KEYS}
    return left_facts == right_facts and all(
        isinstance(value, bool) == isinstance(right_facts[field], bool) for field, value in left_facts.items()
    )


def _revision_record(row):
    scope = row.get("source_root"), row.get("product_id"), row.get("code")
    if isinstance(link := row.get("_teaching_revision_scope"), tuple):
        return (*scope, link)
    cash = (row.get("field_evidence") or {}).get("d") or {}
    registration = row.get("fact_key")
    coordinate = row.get("coordinate") or cash.get("coordinate")
    if registration or coordinate:
        # Registered field keys qualify attachment-local coordinates.
        if cash.get("fact_key"):
            return (*scope, registration, cash.get("announcement_ref") or coordinate)
        return (*scope, registration, coordinate, row.get("announcement_ref"))
    if source := row.get("source_ref"):
        # Older records can identify a fact within a named original without a separate coordinate.
        return (*scope, source, _fact_key(row))
    return None


def _cycle_revisions(links):
    cyclic = set()
    for revision, prior in links.items():
        pending, seen = list(prior), set()
        while pending:
            target = pending.pop()
            if target == revision:
                cyclic.add(revision)
                break
            if target not in seen:
                seen.add(target)
                pending.extend(links.get(target, ()))
    return cyclic


def _revision_origin(row):
    origin = _quote_origin(_row_quote(row, "d")) if row.get("d") is not None else _origin(row)
    return {**origin, **{field: row[field] for field in ("code", "period", "date", "ex_date", "effective_date")
                         if field in row}}


def _flag_disclosure_branches(versions, revisions, links, replaced, cyclic, *, collect_forks=False):
    branches_by_previous = defaultdict(set)
    for revision in revisions.keys() - replaced - cyclic:
        pending, seen = list(links.get(revision, ())), set()
        while pending:
            previous = pending.pop()
            if previous in seen:
                continue
            seen.add(previous)
            branches_by_previous[previous].add(revision)
            pending.extend(links.get(previous, ()))
    for previous, branches in branches_by_previous.items():
        if len(branches) < 2:
            continue
        indexes = sorted(index for branch in branches for index in revisions[branch])
        kinds = {versions[index]["kind"] for index in indexes if versions[index].get("kind")}
        events = {versions[index]["event_id"] for index in indexes if versions[index].get("event_id")}
        dates = {iso_day(versions[index].get("ex_date")) for index in indexes}
        field = "kind" if len(kinds) > 1 else "event_identity" if len(events) > 1 else None
        if field is None and len(dates) > 1 and all(
            versions[index].get("kind") == "distribution" or versions[index].get("action_type") == "cash_dividend"
            for index in indexes
        ):
            field = "ex_date"
        if not collect_forks and field is None:
            continue
        evidence = [
            {**_revision_origin(versions[index]), "kind": versions[index].get("kind"),
             "event_id": versions[index].get("event_id"), "revision_role": role}
            for role, matching in (("branch", indexes), ("common_previous", revisions[previous]))
            for index in matching
        ]
        if collect_forks:
            for original, index in zip(evidence, [*indexes, *revisions[previous]], strict=True):
                original.update({field: versions[index][field] for field in (
                    "unlock_shares", "unlock_shares_unit", "prior_float", "prior_float_unit",
                ) if field in versions[index]})
        for index in indexes:
            row = versions[index]
            if collect_forks:
                token = _revision_record(row), previous
                versions[index] = dict(row, _event_forks={**(row.get("_event_forks") or {}), token: (field, evidence)})
                continue
            conflicts = {**(row.get("_conflicts") or {})}
            alternatives = conflicts.get(field, [])
            conflicts[field] = [*alternatives, *(item for item in evidence if item not in alternatives)]
            versions[index] = dict(row, _conflicts=conflicts)
            if field in ("event_identity", "ex_date", "kind"):
                versions[index]["_distribution_branch"] = (
                    _revision_record(row), previous, tuple(sorted(branches)),
                )


def _disclosure_revision_history(versions, revisions, links, cyclic):
    successors = defaultdict(set)
    for revision in links.keys() - cyclic:
        for previous in links[revision]:
            successors[previous].add(revision)
    for original in revisions.keys() - links.keys():
        pending, seen = list(successors[original]), set()
        while pending:
            revision = pending.pop()
            if revision not in seen:
                seen.add(revision)
                pending.extend(successors[revision])
        if seen:
            indexes = sorted(index for revision in seen for index in revisions[revision])
            changes = sorted(
                (versions[index] for index in indexes), key=lambda row: _public_instant(row["available_at"]),
            )
            for index in revisions[original]:
                versions[index] = dict(versions[index], disclosure_revisions=changes)


def _live_versions(versions, *, disclosure_branches=False, collect_forks=False, first_releases=False):
    versions = list(versions)
    records = defaultdict(lambda: defaultdict(list))
    for index, row in enumerate(versions):
        record = _first_release_record(row) if first_releases else _revision_record(row)
        if (revision := row.get("revision_id")) and record is not None:
            records[record][revision].append(index)
    retired = set()
    for revisions in records.values():
        links = {}
        for revision, indexes in revisions.items():
            targets = {versions[index].get("supersedes") for index in indexes}
            if prior := revisions.keys() & targets:
                links[revision] = prior
        if not links:
            continue
        cyclic = _cycle_revisions(links)
        replaced = set().union(*links.values()) - cyclic
        hidden = links.keys() - cyclic if first_releases else replaced
        if first_releases:
            _disclosure_revision_history(versions, revisions, links, cyclic)
        retired.update(index for revision in hidden for index in revisions[revision])
        if disclosure_branches or collect_forks:
            _flag_disclosure_branches(versions, revisions, links, replaced, cyclic, collect_forks=collect_forks)
        cycle_indexes = sorted(index for revision in cyclic for index in revisions[revision])
        cycle_evidence = [_revision_origin(versions[index]) for index in cycle_indexes]
        affected = {
            field for index in cycle_indexes for field, value in versions[index].items()
            if field not in _PROVENANCE_KEYS and isinstance(value, (int, float)) and not isinstance(value, bool)
        }
        for index in cycle_indexes:
            row = versions[index]
            conflicts = {**(row.get("_conflicts") or {}), "revision_cycle": cycle_evidence}
            cycles = {**(row.get("_revision_cycles") or {}), **dict.fromkeys(affected, cycle_evidence)}
            versions[index] = dict(row, _conflicts=conflicts, _revision_cycles=cycles)
    return [row for index, row in enumerate(versions) if index not in retired]


def _quote_origin(row):
    keys = ("value", "currency", "unit", *_FINANCIAL_CONTEXT)
    return {**_origin(row), **{key: row[key] for key in keys if key in row}}


def _normalized_quote(proof, field):
    if field not in ("amount_cny", "net_assets", "nav", "d", "financial_amount"):
        return proof
    currency = quotation(proof, "value", "currency")
    if field in ("net_assets", "nav") and not currency:
        return proof
    unit = proof.get("unit")
    try:
        value = number(proof, "value")
        if field == "nav":
            return dict(proof, value=price_per_share(value, currency, unit), currency=currency, unit=f"{currency}/份")
        if field == "d":
            return dict(proof, value=yuan_per_stock_share(value, currency, unit), currency="CNY", unit="元/股")
        if field == "financial_amount":
            return dict(proof, value=financial_amount_in_yuan(value, currency, unit), currency="CNY", unit="元")
        return dict(proof, value=money_in_yuan(value, currency, unit), currency="CNY", unit="元")
    except MissingFact:
        if field == "financial_amount":
            return dict(proof, _conflicts={"value": [_quote_origin(proof)]})
        return proof


def _merge_live(live, *, quote_field=None):
    if not live:
        return None
    if len(live) == 1:
        return live[0]
    originals = live
    live = [_normalized_quote(row, quote_field) for row in live] if quote_field else live
    origins = [_quote_origin(row) if quote_field else _origin(row) for row in originals]
    conflicts = {}
    field_cycles = defaultdict(list)
    for row in live:
        for field, entries in (row.get("_conflicts") or {}).items():
            evidence = conflicts.setdefault(field, [])
            if field != "revision_cycle":
                evidence.extend(entries)
                continue
            for entry in entries:
                if entry not in evidence:
                    evidence.append(entry)
        for field, entries in (row.get("_revision_cycles") or {}).items():
            for entry in entries:
                if entry not in field_cycles[field]:
                    field_cycles[field].append(entry)
    if quote_field == "financial_amount" and "value" in conflicts:
        conflicts["value"] = origins
    if not conflicts and all(_same_facts(row, live[0]) for row in live):
        return dict(live[0], corroborating_sources=origins)
    combined = dict(live[0])
    for field in set().union(*(row.keys() for row in live)) - _PROVENANCE_KEYS:
        if field in conflicts:
            combined.pop(field, None)
            continue
        present = [i for i, row in enumerate(live) if row.get(field) is not None]
        if not present:
            continue
        value = live[present[0]][field]
        if any(live[i][field] != value or isinstance(live[i][field], bool) != isinstance(value, bool) for i in present):
            conflicts[field] = [
                origins[i] if quote_field else {
                    **origins[i], **{key: live[i][key] for key in ("currency", "unit") if key in live[i]},
                    "value": live[i][field],
                }
                for i in present
            ]
            combined.pop(field, None)
        else:
            combined[field] = value
    if conflicts:
        combined["_conflicts"] = conflicts
    elif quote_field:
        combined["corroborating_sources"] = origins
    if field_cycles:
        combined["_revision_cycles"] = dict(field_cycles)
    return combined


def _resolve_group(versions, *, quote_field=None):
    return _merge_live(_live_versions(versions), quote_field=quote_field)


def _row_quote(row, field):
    attributes = ("currency", "unit") if field == "d" else _QUOTE_ATTRIBUTES
    proof = {key: value for key in attributes if (value := quotation(row, field, key)) is not None}
    if field == "net_assets":
        proof["unit"] = net_assets_unit(row)
    evidence = (row.get("field_evidence") or {}).get(field, {})
    match = {} if evidence.get("observation_status") == "unavailable_at_cutoff" else {
        "matched": evidence.get("matched", True),
    }
    return {**_origin(row), **_origin(evidence), **proof,
            "available_at": quotation(row, field, "available_at"), "value": row[field], **match}


def _apply_quote(row, field, proof, *, context=()):
    disputed = proof.get("_conflicts") or {}
    for attribute, entries in disputed.items():
        key = f"{field}_{attribute}" if attribute in context else field
        row.setdefault("_conflicts", {}).setdefault(key, []).extend(dict(item, attribute=attribute) for item in entries)
    if "value" in proof and not (disputed.keys() - set(context)) and proof.get("matched", True):
        row[field] = proof["value"]
        row.setdefault("field_evidence", {})[field] = proof


def _resolve_quoted_fields(live, fields, *, quote_as=None, context=()):
    if len(live) < 2:
        return live[0] if live else None
    excluded = (*fields, "field_evidence")
    row = _merge_live([{key: value for key, value in item.items() if key not in excluded} for item in live])
    for field in fields:
        proofs = [
            dict(_row_quote(item, field), **{
                attr: quotation(item, field, attr)
                for attr in (("currency", "unit", *context) if quote_as == "financial_amount" else context)
            })
            for item in live if item.get(field) is not None
        ]
        if (proof := _merge_live(proofs, quote_field=quote_as or field)) is not None:
            _apply_quote(row, field, proof, context=context)
    return row


def _distribution_coordinate(row):
    action = row.get("action_type") or ("cash_dividend" if row.get("kind") == "distribution" else None)
    day = iso_day(row.get("ex_date"))
    if action == "cash_dividend" and day and row.get("code"):
        return row["code"], day, action
    return None


def _distribution_terms(row, cash_window):
    coordinate = _distribution_coordinate(row)
    if not coordinate or row.get("d") is None or row.get("same_share_basis") is not True:
        return None
    proof = (row.get("field_evidence") or {}).get("d", {})
    if proof.get("matched") is False or proof.get("observation_status") == "unavailable_at_cutoff":
        return None
    try:
        cash = yuan_per_stock_share(number(row, "d"), quotation(row, "d", "currency"), quotation(row, "d", "unit"))
    except (MissingFact, TypeError, ValueError):
        return None
    if cash_window:
        start, end = cash_window
        return coordinate[0], start < coordinate[1] <= end, cash
    return coordinate, cash, row.get("m")


def _is_corroborated_distribution(row, witnesses, cash_window):
    coordinate = _distribution_coordinate(row)
    terms = _distribution_terms(row, cash_window)
    return terms is not None and bool(witnesses) and all(
        not item.get("_conflicts")
        and _distribution_coordinate(item) == coordinate
        and _distribution_terms(item, cash_window) == terms
        for item in witnesses
    )


def _resolve_distribution_aliases(rows, *, cash_window=None):
    groups, by_event, unlabelled = defaultdict(list), defaultdict(list), set()
    for index, row in enumerate(rows):
        if token := row.get("_distribution_branch"):
            groups[token].append(index)
        by_event[row.get("code"), row.get("event_id")].append(index)
        if not row.get("event_id") and (coordinate := _distribution_coordinate(row)):
            unlabelled.add(coordinate)
    resolved, removed = {}, set()
    for indexes in groups.values():
        members = set(indexes)
        first = rows[indexes[0]]
        field = next(key for key in ("kind", "event_identity", "ex_date") if key in first["_conflicts"])
        if field in ("kind", "ex_date") and not cash_window:
            continue
        evidence = first["_conflicts"][field]
        branches = [entry for entry in evidence if entry.get("revision_role") == "branch"]
        if field == "kind":
            noncash = [entry for entry in branches if entry.get("kind") != "distribution"]
            if not noncash or any(
                not entry.get("event_id") or bool(set(by_event[entry.get("code"), entry["event_id"]]) - members)
                for entry in noncash
            ):
                continue
            branches = [entry for entry in branches if entry.get("kind") == "distribution"]
        if len(branches) != len(indexes) or (field != "kind" and len(indexes) < 2):
            continue
        if any(set(rows[index]["_conflicts"]) != {field} for index in indexes):
            continue
        witnesses = {
            index: set(by_event[rows[index].get("code"), rows[index].get("event_id")]) - members
            for index in indexes
        }
        if field == "kind" or any(witnesses.values()):
            if cash_window and all(
                _distribution_coordinate(rows[index]) not in unlabelled
                and _is_corroborated_distribution(rows[index], [rows[i] for i in matches], cash_window)
                for index, matches in witnesses.items()
            ):
                # Every branch is already counted by its independent, matching evidence.
                for witness in set().union(*witnesses.values()):
                    resolved[witness] = dict(rows[witness], identity_evidence=evidence)
                removed.update(indexes)
            continue
        terms = _distribution_terms(first, cash_window)
        if terms is None or any(
            _distribution_terms(rows[index], cash_window) != terms
            or (cash_window and _distribution_coordinate(rows[index]) in unlabelled)
            for index in indexes
        ):
            continue
        event = min((rows[index]["event_id"] for index in indexes if rows[index].get("event_id")), default=None)
        aliases = [dict(rows[index], event_id=event, _conflicts={}) for index in indexes]
        dates = sorted({row["ex_date"] for row in aliases})
        when = {}
        if len(dates) > 1:
            if not terms[1]:
                continue
            for row in aliases:
                del row["ex_date"]
            when = dict(cash_window=cash_window, ex_date_options=dates)
        merged = _resolve_quoted_fields(aliases, ("d",))
        resolved[indexes[0]] = dict(merged, identity_evidence=evidence, **when)
        removed.update(indexes[1:])
    return [resolved.get(index, row) for index, row in enumerate(rows) if index not in removed]


def _event_publication_identities(row):
    proof = (row.get("field_evidence") or {}).get("d", {})
    declared = proof.get("announcement_ref") or row.get("announcement_ref")
    source = proof.get("source_ref") or row.get("source_ref")
    if public := _public_document_url(declared):
        return (("public", public),)
    identities = []
    if declared and (locator := _public_document_url(source)):
        identities.append(("declared", declared, locator))
    if proof.get("announcement_ref") and proof.get("fact_key") and row.get("product_id"):
        # The registered field's original and stable coordinate can also survive an asset split.
        identities.append(("register", row["product_id"], proof["fact_key"], proof["announcement_ref"],
                           _public_instant(proof.get("available_at"))))
    if declared:
        identities.append(("asset", row.get("source_root"), row.get("snapshot_id"), declared))
    return tuple(identities)


def _action_identity_evidence(versions):
    return [_quote_origin(_row_quote(row, "d")) if row.get("d") is not None else _origin(row) for row in versions]


def _match_distribution_copies(rows):
    labelled = defaultdict(set)
    for row in rows:
        event, coordinate = row.get("event_id"), _distribution_coordinate(row)
        if event and coordinate:
            for identity in _event_publication_identities(row):
                labelled[coordinate, identity].add(event)
    matched = []
    for row in rows:
        coordinate = _distribution_coordinate(row)
        identities = _event_publication_identities(row) if not row.get("event_id") else ()
        events = set().union(*(labelled.get((coordinate, identity), ()) for identity in identities))
        if len(events) == 1:
            event, = events
            row = dict(row, event_id=event, _distribution_copy=True)
        matched.append(row)
    return matched


def _resolve_unlabelled_actions(groups, unlabelled):
    if not unlabelled:
        return []
    labelled = {}
    for rows in groups.values():
        for row in rows:
            if row.get("event_id") and (coordinate := _distribution_coordinate(row)):
                labelled.setdefault(coordinate, []).append(row)
    by_coordinate = {}
    for row in unlabelled:
        by_coordinate.setdefault(_distribution_coordinate(row), []).append(row)
    ambiguities = []
    for coordinate, versions in by_coordinate.items():
        related = labelled.get(coordinate, [])
        notices = set.intersection(*(set(_event_publication_identities(row)) for row in versions))
        if not related and (len(versions) == 1 or notices):
            groups[coordinate] = versions
            continue
        identified = {(row["event_id"], identity) for row in related if not row.get("_distribution_copy")
                      for identity in _event_publication_identities(row)}
        competing = [row for row in related if not row.get("_distribution_copy")
                     or not any((row["event_id"], identity) in identified
                                for identity in _event_publication_identities(row))]
        code, day, action = coordinate
        ambiguities.append(dict(
            code=code, ex_date=day, action_type=action,
            _conflicts={"event_identity": _action_identity_evidence([*versions, *competing])},
        ))
    return ambiguities


def select_versions(rows, cutoff, *, table=None):
    actions = table == "corporate_actions"
    current = _current_records(rows, cutoff, disclosure_branches=actions or table == "events")
    if actions:
        current = _resolve_distribution_aliases(_match_distribution_copies(current))
    return _group_current(current, table, cutoff)


def _current_records(rows, cutoff, *, disclosure_branches=False, collect_forks=False):
    return _live_versions(
        (_field_evidence_at(row, cutoff) for row in rows if _known(row.get("available_at"), cutoff)),
        disclosure_branches=disclosure_branches,
        collect_forks=collect_forks,
    )


_UNLOCK_COUNTS = ("unlock_shares", "prior_float")


def _unlock_quantities_in_shares(row):
    data = dict(row)
    for field in _UNLOCK_COUNTS:
        try:
            count = share_count(row, field)
        except MissingFact:
            continue
        data.update({field: count, f"{field}_unit": "股"})
    return data


def _unlock_original(row):
    quantities = [key for field in _UNLOCK_COUNTS for key in (field, f"{field}_unit")]
    return dict(_revision_origin(row), **{key: row[key] for key in quantities if key in row})


def _merge_unlock_versions(versions):
    if len(versions) == 1:
        return versions[0]
    merged = _merge_live([_unlock_quantities_in_shares(row) for row in versions])
    if conflicts := merged.get("_conflicts"):
        originals = [_unlock_original(row) for row in versions]
        for field in _UNLOCK_COUNTS:
            if field in conflicts:
                conflicts[field] = [dict(original, field=field, value=original[field])
                                    for original in originals if field in original]
    merged["corroborating_sources"] = versions
    return merged


def _match_unnumbered_event_copies(rows):
    originals = defaultdict(list)
    same_counts = defaultdict(list)
    counts_at = {}
    for index, row in enumerate(rows):
        if row.get("kind") != "unlock":
            continue
        day = iso_day(row.get("effective_date"))
        identities = _event_publication_identities(row)
        identity = identities[0] if identities and day else index
        original = row.get("code"), identity, day, _public_instant(row.get("available_at"))
        originals[original].append(index)
        try:
            counts = tuple(share_count(row, field) for field in _UNLOCK_COUNTS)
        except MissingFact:
            continue
        counts_at[index] = (row.get("code"), day, counts)
        same_counts[counts_at[index]].append(index)
    matched = list(rows)
    for indexes in originals.values():
        ids = {event for index in indexes if (event := rows[index].get("event_id"))}
        unidentified = [index for index in indexes if not rows[index].get("event_id")]
        if not unidentified:
            continue
        if len(ids) == 1:
            event, = ids
            for index in unidentified:
                matched[index] = dict(rows[index], event_id=event)
            continue
        related = sorted({other for index in indexes for other in same_counts.get(counts_at.get(index), ())}
                         - set(indexes))
        for index in unidentified:
            row = dict(rows[index], _event_copy_key=("original_copy", indexes[0]))
            if len(ids) > 1 or related:
                evidence = [dict(_unlock_original(rows[i]), event_id=rows[i].get("event_id"))
                            for i in [*indexes, *related]]
                row["_conflicts"] = {**(row.get("_conflicts") or {}), "event_identity": evidence}
            matched[index] = row
    return matched


def window_event_versions(rows, cutoff, kind, window):
    start, end = window
    current = _current_records(rows, cutoff, collect_forks=True)
    relevant = {
        row.get("event_id") or _fact_key(row) for row in current
        if row.get("kind") == kind and (not (day := iso_day(row.get("effective_date"))) or start <= day <= end)
    }
    direct = {index for index, row in enumerate(current) if (row.get("event_id") or _fact_key(row)) in relevant}
    tracked, evidence, fields = set(), [], set()
    for index in sorted(direct):
        for token, (field, originals) in current[index].get("_event_forks", {}).items():
            tracked.add(token)
            fields.add(field)
            evidence.extend(item for item in originals if item not in evidence)
    forks = defaultdict(lambda: defaultdict(set))
    for index, row in enumerate(current):
        for token in row.get("_event_forks", {}).keys() & tracked:
            forks[token][row["revision_id"]].add(index)

    # A descendant fork is already represented by the full set of alternatives at its ancestor.
    families = {frozenset(frozenset(members) for members in versions.values()) for versions in forks.values()}
    largest = [family for family in families if not any(family < other for other in families)]
    branched = set().union(*(members for family in largest for members in family))
    independent = direct - branched

    def grouped_choices():
        for choices in product(*largest):
            selected = independent.union(*choices)
            if any(len(family.intersection(choices)) != 1 for family in largest):
                continue
            active = [row for i, row in enumerate(current) if i in selected]
            yield _group_current(_match_unnumbered_event_copies(active), "events", cutoff)

    field = "kind" if "kind" in fields else "event_identity" if "event_identity" in fields else kind
    return grouped_choices(), field, evidence


def _field_evidence_at(row, cutoff):
    fields = row.get("field_evidence") or {}
    suffix = "_available_at"
    clocks = {key[:-len(suffix)] for key in row if key.endswith(suffix)}
    hidden = {
        field for field in clocks | fields.keys()
        if (field in clocks or "available_at" in fields[field])
        and not _known(field_attribute(row, field, "available_at"), cutoff)
    }
    if hidden:
        row = _without_fields(row, hidden)
    return _observations_at(row, cutoff)


def _without_fields(row, excluded):
    filtered = {
        key: value for key, value in row.items()
        if key not in excluded and not any(key.startswith(f"{field}_") for field in excluded)
    }
    if "field_evidence" in row:
        filtered["field_evidence"] = {
            key: value for key, value in row["field_evidence"].items() if key not in excluded
        }
    return filtered


def _observations_at(row, cutoff):
    unavailable = {
        field: dict(
            {key: value for key, value in proof.items()
             if key not in ("observed_at", "observed_value", "observed_source_ref", "matched")},
            observation_status="unavailable_at_cutoff",
        )
        for field, proof in (row.get("field_evidence") or {}).items()
        if "observed_at" in proof and not _known(proof["observed_at"], cutoff)
    }
    return dict(row, field_evidence={**row["field_evidence"], **unavailable}) if unavailable else row


def select_distributions(actions, disclosures, cutoff, trade_date, *, cash_window=None):
    # The two tables have independent revision coordinates, even when they attest to one event.
    actions = [
        row for row in _current_records(actions, cutoff, disclosure_branches=True)
        if not row.get("date") or row["date"] <= trade_date
    ]
    disclosures = _current_records(disclosures, cutoff, disclosure_branches=True)
    candidates = _match_distribution_copies([*actions, *disclosures])
    claims = defaultdict(list)
    admitted = []
    for index, row in enumerate(candidates):
        action = index < len(actions)
        kind = "distribution" if row.get("action_type") == "cash_dividend" else row.get("kind")
        event = row.get("event_id")
        key = (row.get("code"), event) if event else (False, _revision_record(row) or index)
        if kind is not None and (not action or kind == "distribution"):
            proof = (row.get("field_evidence") or {}).get("d", {})
            claims[key].append(dict(_origin(row), **_origin(proof), kind=kind, event_id=event))
        if action or kind == "distribution":
            admitted.append((row, key, kind))
    active = []
    for row, key, kind in admitted:
        if kind == "distribution" and any(item["kind"] != kind for item in claims[key]):
            conflicts = dict(row.get("_conflicts") or {})
            existing = conflicts.get("kind", [])
            provenance = ("source_root", "snapshot_id", "source_ref", "revision_id", "fact_key", "coordinate")
            seen = {tuple(item.get(field) for field in provenance) for item in existing}
            conflicts["kind"] = [*existing, *(item for item in claims[key]
                                             if tuple(item.get(field) for field in provenance) not in seen)]
            row = dict(row, _conflicts=conflicts)
        active.append(row)
    current = _resolve_distribution_aliases(active, cash_window=cash_window)
    return _group_current(current, "corporate_actions", cutoff)


def _verified_cash_row(row, cutoff):
    row = _observations_at(row, cutoff)
    proof = (row.get("field_evidence") or {}).get("d") or {}
    if proof.get("matched") is not False and proof.get("observation_status") != "unavailable_at_cutoff":
        return row
    evidence = {**_origin(row), **proof}
    conflicts = {**(row.get("_conflicts") or {}), "d": [dict(evidence, value=row["d"])]}
    return {**{key: value for key, value in row.items() if key != "d"}, "_conflicts": conflicts}


def _verified_financial_row(row):
    mismatches = {
        field: [{**_origin(row), **proof}]
        for field, proof in (row.get("field_evidence") or {}).items()
        if proof.get("matched") is False or proof.get("observation_status") == "unavailable_at_cutoff"
    }
    if not mismatches:
        return row
    return dict(_without_fields(row, mismatches), _conflicts={**(row.get("_conflicts") or {}), **mismatches})


_FINANCIAL_AMOUNTS = (
    "assets", "capex_cash", "cash", "consolidated_profit", "cost_of_sales", "ebit", "equity", "forecast_lower",
    "forecast_upper", "interest_debt", "liabilities", "net_profit_ex", "operating_cashflow", "parent_equity",
    "parent_net_profit", "rd_investment", "revenue",
)


def _merge_financial_reports(versions):
    observed = defaultdict(list)
    corroborations = defaultdict(list)
    for row in versions:
        for field, proof in (row.get("field_evidence") or {}).items():
            if proof.get("matched") and proof.get("fact_key") and proof.get("evidence_ref"):
                observed[field].append({**_origin(row), **proof})
    if observed:
        # An identical registration may be present in several selected assets. One missing
        # observation does not contradict another asset's verified table value.
        reconciled = []
        for row in versions:
            conflicts = defaultdict(list)
            for field, entries in (row.get("_conflicts") or {}).items():
                for entry in entries:
                    candidates = [match for match in observed[field] if _observes_same_registration(entry, match)]
                    if not candidates:
                        conflicts[field].append(entry)
                        continue
                    original = _registered_amount(entry)
                    disagree = [match for match in candidates if _registered_amount(match) != original]
                    if original is not None and not disagree:
                        if entry not in corroborations[field]:
                            corroborations[field].append(entry)
                    else:
                        conflicts[field].extend([
                            {key: value for key, value in entry.items() if key != "observation_status"},
                            *(disagree or candidates),
                        ])
            reconciled.append(dict(row, _conflicts=dict(conflicts)) if "_conflicts" in row else row)
        versions = reconciled
    merged = _resolve_quoted_fields(
        versions, _FINANCIAL_AMOUNTS, quote_as="financial_amount", context=_FINANCIAL_CONTEXT,
    )
    if corroborations and merged is not None:
        fields = dict(merged.get("field_evidence") or {})
        for field, entries in corroborations.items():
            if proof := fields.get(field):
                fields[field] = dict(proof, corroborating_sources=[*proof.get("corroborating_sources", ()), *entries])
        merged = dict(merged, field_evidence=fields)
    return merged


def _observes_same_registration(entry, match):
    announcement = entry.get("announcement_ref")
    coordinate = entry.get("coordinate")
    observed_coordinate = match.get("coordinate")
    source_root = entry.get("source_root")
    public_notice = _public_document_url(announcement)
    # A business field key and publication time recur in independent registrations.
    # A local coordinate needs its source scope or its declared original.
    anchored = bool(
        public_notice
        or (coordinate and (announcement or (source_root and source_root == match.get("source_root"))))
    )
    return (
        entry.get("observation_status") == "unavailable_at_cutoff"
        and anchored
        and entry.get("fact_key") is not None
        # The same declared publication can corroborate a missing local coordinate.
        and (coordinate == observed_coordinate or (public_notice and not (coordinate and observed_coordinate)))
        and (not entry.get("source_ref") or entry["source_ref"] == match.get("source_ref"))
        and _same_public_instant(entry.get("available_at"), match.get("available_at"))
        and all(entry.get(key) == match.get(key) for key in (
            "fact_key", "announcement_ref", "product_id", "revision_id", "currency", *_FINANCIAL_CONTEXT,
        ))
    )


def _registered_amount(proof):
    normalized = _normalized_quote(proof, "financial_amount")
    if "value" not in (normalized.get("_conflicts") or {}):
        return normalized.get("value")


def _group_current(current, table, cutoff):
    groups, unlabelled = {}, []
    for row in current:
        event_copy = row.get("_event_copy_key")
        hidden = {"_teaching_revision_scope", "_distribution_branch", "_event_copy_key", "_event_forks"}
        if hidden.intersection(row):
            row = {key: value for key, value in row.items() if key not in hidden}
        if table == "corporate_actions":
            row = _verified_cash_row(row, cutoff)
        if table == "financial_reports":
            row = _verified_financial_row(row)
        coordinate = _distribution_coordinate(row) if table == "corporate_actions" else None
        if table in ("corporate_actions", "events") and row.get("event_id"):
            key = (row.get("code"), row["event_id"])
        elif event_copy is not None:
            key = event_copy
        elif coordinate:
            unlabelled.append(row)
            continue
        elif table == "financial_reports" and (period := row.get("period") or row.get("report_period")):
            key = (row.get("code"), _period_from_raw(period))
        elif table == "calendar" and row.get("calendar_id"):
            key = (row.get("date"), row["calendar_id"])
        elif table in ("daily_bars", "market_cap", "shares", "price_limits", "etf_nav", "etf_shares"):
            key = (row.get("code"), row.get("date"))
        else:
            key = _fact_key(row)
        groups.setdefault(key, []).append(row)
    result = []
    if table == "corporate_actions":
        result = _resolve_unlabelled_actions(groups, unlabelled)
    for versions in groups.values():
        if table == "etf_nav":
            selected = _resolve_quoted_fields(versions, ("nav", "net_assets"))
        elif table == "corporate_actions":
            selected = _resolve_quoted_fields(
                [{field: value for field, value in row.items() if field != "_distribution_copy"} for row in versions],
                ("d",),
            )
        elif table == "financial_reports":
            selected = _merge_financial_reports(versions)
        elif table == "events" and versions[0].get("kind") == "distribution":
            selected = _resolve_quoted_fields(versions, ("d",))
        elif table == "events" and all(row.get("kind") == "unlock" for row in versions):
            selected = _merge_unlock_versions(versions)
        else:
            selected = _merge_live(versions)
        if selected is not None:
            result.append(selected)
    return result


def _join_daily(rows, cutoff):
    fields = {}
    plain = {}
    for row in rows:
        # Native rows carry timestamps on their attestations. Supplemental originals carry a row timestamp.
        if "available_at" in row or "field_evidence" not in row:
            if _known(row.get("available_at"), cutoff):
                plain.setdefault((row.get("code"), row.get("date")), []).append(row)
            continue
        key = row["code"], row["date"]
        group = fields.setdefault(key, {})
        for field, proofs in row["field_evidence"].items():
            for proof in proofs if isinstance(proofs, list) else [proofs]:
                if _known(proof["available_at"], cutoff):
                    group.setdefault(field, []).append(proof)
    # Supplemental rows and native attestations use different storage formats but attest to the same field.
    residual = []
    for key, versions in plain.items():
        live = _live_versions(versions)
        if sum(row.get("amount_cny") is not None for row in live) > 1:
            fields.setdefault(key, {}).setdefault("amount_cny", [])
        group = fields.get(key, {})
        for row in live:
            for field, proofs in group.items():
                if row.get(field) is not None:
                    proofs.append(_row_quote(row, field))
            residual.append({key: value for key, value in row.items() if key not in group} if group else row)
    joined = []
    for (code, day), group in fields.items():
        row = dict(code=code, date=day, available_at=cutoff.isoformat())
        for field, records in group.items():
            records = sorted(records, key=lambda proof: not proof.get("matched", True))
            if (proof := _resolve_group(records, quote_field=field)) is not None:
                _apply_quote(row, field, proof)
        if row.get("field_evidence") or row.get("_conflicts"):
            joined.append(row)
    return joined + residual


def _shape(rows, kind, request):
    result = []
    if kind == "daily_bars":
        rows = _join_daily(rows, request.cutoff)
    for row in select_versions(rows, request.cutoff, table=kind):
        day = row.get("date")
        if day and day > str(request.trade_date) and kind not in ("memberships", "events", "calendar"):
            continue
        result.append(row)
    return pd.DataFrame(result)


def _proof_fields(owner, ref, manifest, table, fields):
    if not any(entry["table"] == "knowledge_evidence" for entry in manifest["table_files"]):
        return {}
    proof = owner.read_table(ref, "knowledge_evidence")
    proof = proof[proof.table_name.eq(table)]
    originals, result = {}, {}
    for row in proof.itertuples(index=False):
        if str(row.verified).lower() not in ("true", "1") or (
            fields and str(row.record_key).split("|")[-1] not in fields
        ):
            continue
        ref_key = row.evidence_ref, row.sha256
        if ref_key not in originals:
            originals[ref_key] = _read_close_attestations(owner, *ref_key)
        for original in originals[ref_key].get((row.record_key, row.revision_id), ()):
            if _same_public_instant(original.get("published_at"), row.published_at):
                result.setdefault(row.record_key, []).append((row, original))
    return result


def _attested(rows, columns, proofs, request):
    observed = {}
    for row in rows:
        for field in columns:
            if not pd.isna(row.get(field)):
                observed.setdefault((row["code"], row["date"], field), []).append(row)
    selected = {}
    for coordinate, rows_at_key in observed.items():
        key = "|".join(coordinate)
        field = coordinate[-1]
        candidates = []
        for proof, original in proofs.get(key, ()):
            products = [
                row
                for row in rows_at_key
                if original.get("product_id") == row["product_id"]
                and original.get("source_ref", row["source_ref"]) == row["source_ref"]
            ]
            if not products:
                continue
            origin = next((row["source_ref"] for row in products if original.get("value") == row[field]), None)
            candidates.append(
                dict(
                    revision_id=proof.revision_id,
                    supersedes=proof.supersedes if isinstance(proof.supersedes, str) else "",
                    available_at=proof.published_at,
                    fact_key=key,
                    evidence_ref=proof.evidence_ref,
                    value=original.get("value"),
                    matched=origin is not None,
                    **{key: original[key] for key in _QUOTE_ATTRIBUTES if key in original},
                    product_id=original.get("product_id"),
                    source_ref=origin,
                    **({"coordinate": original["coordinate"]} if original.get("coordinate") else {}),
                )
            )
        if candidates:
            selected[coordinate] = candidates
    return selected


def _authorized(owner, manifest, table, products, purpose):
    try:
        return all(_purpose_allowed(owner, manifest, table, product, purpose) for product in products)
    except AccessDenied:
        return False


def _daily_records(owner, ref, manifest, request, fields, codes, start, product):
    fields = tuple(field for field in fields if field in TABLE_SCHEMAS["daily_bars"]["columns"])
    if not fields or not codes or start > request.trade_date:
        return []
    end = request.trade_date
    if owner.product(product).get("fee_class") == "paid":
        grant = owner._grant(product) or {}
        start = max(start, date.fromisoformat(grant["start"]))
        end = min(end, date.fromisoformat(grant["end"]))
        if start > end:
            return []
    raw = owner.read_range(
        ref,
        "daily_bars",
        codes=codes,
        start=start,
        end=end,
        product_id=product,
        columns=("code", "date", "product_id", "source_ref", *fields),
    )
    proofs = _proof_fields(owner, ref, manifest, "daily_bars", fields)
    selected = _attested(raw.to_dict("records"), fields, proofs, request)
    output = {}
    for (code, day, column), candidates in selected.items():
        item = output.setdefault((code, day), dict(code=code, date=day, field_evidence={}))
        item["field_evidence"][column] = [
            dict(proof, snapshot_id=ref.snapshot_id, source_root=str(owner.root.resolve())) for proof in candidates
        ]
    return list(output.values())


def _daily_start(request, calendar):
    params = request.parameters
    if request.factor_id == "traded_amount" and params["mode"] == "daily":
        return request.trade_date
    if request.factor_id == "low_volatility_36m":
        return (pd.Timestamp(request.trade_date) - pd.DateOffset(months=37)).date()
    if request.factor_id in ("post_announcement_drift", "sector_rotation_score"):
        return date(1990, 1, 1)
    if params.get("months"):
        return (pd.Timestamp(request.trade_date) - pd.DateOffset(months=params["months"])).date() - timedelta(days=15)
    size = max(
        (
            value
            for key, value in params.items()
            if type(value) is int and ("window" in key or key in ("skip", "lookback", "short", "long"))
        ),
        default=1,
    )
    if params.get("exposure_benchmark"):
        size = max(size, params.get("beta_window", 60))
    if request.factor_id in (
        "price_momentum",
        "absolute_momentum",
        "risk_adjusted_momentum",
        "relative_strength",
        "short_reversal",
        "etf_dual_momentum",
    ):
        size = max(size, params["window"] + params.get("skip", 0))
    count = size + 1
    if request.factor_id == "pair_beta_drift":
        count = 2 * params["window"]
    elif request.factor_id == "pair_extreme_spread":
        count = params["window"] + params["lookback"]
    days = sorted(
        {
            row["date"]
            for row in calendar
            if row.get("is_trading_day") is True and row["date"] <= str(request.trade_date)
        }
    )
    return date.fromisoformat(days[-min(len(days), count)]) if days else request.trade_date - timedelta(days=count * 3)


def _period_from_raw(value):
    try:
        period = pd.Period(value, freq="Q")
        return str(period) if pd.notna(period) else None
    except (ValueError, TypeError):
        return str(value) if value else None


def _native_cash_quote(proof, original, raw, observed_at, matched):
    return dict(
        d=original["value"], fact_key=proof.record_key, evidence_ref=proof.evidence_ref,
        field_evidence={"d": {
            **{key: original[key] for key in ("currency", "unit", "source_ref", "coordinate", "announcement_ref")
               if key in original},
            "evidence_ref": proof.evidence_ref,
            "fact_key": proof.record_key,
            "available_at": original["published_at"],
            "observed_at": observed_at,
            **({} if matched else {"matched": False, "observed_value": raw}),
        }},
        **({"announcement_ref": original["announcement_ref"]} if original.get("announcement_ref") else {}),
    )


def _unmatched_cash_records(proofs, observed, owner, products):
    for record_key, candidates in proofs.items():
        parts = record_key.rsplit("|", 2)
        if len(parts) != 3 or parts[-1] != "d" or not iso_day(parts[1]):
            continue
        code, day, _ = parts
        for proof, original in candidates:
            product = original.get("product_id")
            if product not in products or id(original) in observed:
                continue
            if not _supplementary_in_scope(owner, product, {"code": code, "ex_date": day}):
                continue
            yield dict(
                code=code, ex_date=day, action_type="cash_dividend", product_id=product,
                source_ref=original.get("source_ref"), revision_id=proof.revision_id, available_at=proof.published_at,
                supersedes=proof.supersedes, **_native_cash_quote(proof, original, None, None, False),
            )


def _native_financial_quote(proof, original, **observation):
    return dict(
        _origin(original), fact_key=proof.record_key, evidence_ref=proof.evidence_ref,
        available_at=original["published_at"], value=original["value"],
        **{key: original[key] for key in ("currency", "unit", *_FINANCIAL_CONTEXT) if key in original},
        **observation,
    )


def _unmatched_financial_records(proofs, observed, fields, owner, products):
    for record_key, candidates in proofs.items():
        parts = record_key.rsplit("|", 2)
        if len(parts) != 3:
            continue
        code, period, field = parts
        if field not in fields or field not in _FINANCIAL_AMOUNTS:
            continue
        try:
            quarter = pd.Period(period, freq="Q")
        except ValueError:
            continue
        if str(quarter) != period:
            continue
        period_end = quarter.end_time.date().isoformat()
        for proof, original in candidates:
            product = original.get("product_id")
            if product not in products or id(original) in observed:
                continue
            if not _supplementary_in_scope(owner, product, {"code": code, "period": period_end}):
                continue
            quote = _native_financial_quote(proof, original, observation_status="unavailable_at_cutoff")
            yield dict(
                code=code, period=period, product_id=product, source_ref=original.get("source_ref"),
                revision_id=proof.revision_id, available_at=proof.published_at, fact_key=proof.record_key,
                field_evidence={field: quote},
                **({"supersedes": proof.supersedes} if pd.notna(proof.supersedes) else {}),
            )


def _native_records(table, frame, fields, proofs, *, owner, products):
    records = frame.astype(object).where(frame.notna(), None).to_dict("records")
    result = []
    observed_cash = set()
    observed_financial = set()
    financial_clocks = defaultdict(set)
    if table == "financial_reports":
        for row in records:
            row["period"] = _period_from_raw(row.get("report_period"))
            origin = row["code"], row["period"], row["product_id"], row["source_ref"]
            financial_clocks[origin].add(_public_instant(row.get("available_at")))
    for row in records:
        if table == "memberships":
            row.update(
                member=row.get("code"),
                code=row.get("board_id"),
                valid_from=row.get("effective_from"),
                valid_to=row.get("effective_to"),
            )
        if table in (
            "memberships",
            "corporate_actions",
            "financial_reports",
            "market_cap",
            "index_bars",
            "security_status",
        ):
            coordinate = row.get("period") or row.get("date") or row.get("ex_date") or row.get("valid_from")
            scope = row.get("member", row.get("code"))
            accepted = {}
            cash_quotes = defaultdict(list)
            financial_quotes = defaultdict(lambda: defaultdict(list))
            for field in fields:
                key = f"{scope}|{coordinate}|{field}"
                for proof, original in proofs.get(key, ()):
                    raw = row.get(field)
                    supplied = original.get("value")
                    if original.get("product_id") != row.get("product_id"):
                        continue
                    if original.get("source_ref", row["source_ref"]) != row["source_ref"]:
                        continue
                    cash = table == "corporate_actions" and field == "d"
                    observed_at = row.get("available_at")
                    time_matches = _same_public_instant(proof.published_at, observed_at)
                    matched = (raw is not None and (supplied == raw or str(supplied) == str(raw))
                               and (time_matches or (not cash and not observed_at)))
                    financial = table == "financial_reports" and field in _FINANCIAL_AMOUNTS
                    if financial and not time_matches:
                        origin = scope, coordinate, row["product_id"], row["source_ref"]
                        if _public_instant(proof.published_at) in financial_clocks[origin]:
                            continue
                    if not (matched or cash or financial):
                        continue
                    version = proof.revision_id, proof.published_at, str(proof.supersedes)
                    verified = accepted.setdefault(version, {})
                    if cash:
                        observed_cash.add(id(original))
                        cash_quotes[version].append(_native_cash_quote(proof, original, raw, observed_at, matched))
                    else:
                        verified.update({field: supplied, "evidence_ref": proof.evidence_ref})
                        if financial:
                            observed_financial.add(id(original))
                            observation = {} if matched else dict(
                                matched=False, observed_at=observed_at, observed_value=raw,
                                observed_source_ref=row["source_ref"],
                            )
                            financial_quotes[version][field].append(_native_financial_quote(
                                proof, original, **observation,
                            ))
            for (revision, stamp, earlier), verified in accepted.items():
                metadata = {
                    key: row.get(key)
                    for key in (
                        "code",
                        "period",
                        "date",
                        "ex_date",
                        "source_ref",
                        "product_id",
                        "member",
                        "valid_from",
                        "valid_to",
                        "action_type",
                        "event_id",
                    )
                    if row.get(key) is not None
                }
                for quote in cash_quotes.get((revision, stamp, earlier), ({},)):
                    facts = {**verified, **quote}
                    record = dict(metadata, **facts, revision_id=revision, available_at=stamp, supersedes=earlier)
                    for field, originals in financial_quotes.get((revision, stamp, earlier), {}).items():
                        field_candidates = []
                        for original in originals:
                            attested = dict(record, **{field: original["value"]}, field_evidence={field: original})
                            context = {attr: quotation(attested, field, attr) for attr in _FINANCIAL_CONTEXT}
                            field_candidates.append(dict(_row_quote(attested, field), **context))
                        resolved = _merge_live(field_candidates, quote_field="financial_amount")
                        _apply_quote(record, field, resolved, context=_FINANCIAL_CONTEXT)
                        if resolved.get("matched") is False:
                            record.setdefault("field_evidence", {})[field] = resolved
                    result.append(record)
        else:
            result.append(row)
    if table == "corporate_actions" and "d" in fields:
        result.extend(_unmatched_cash_records(proofs, observed_cash, owner, products))
    if table == "financial_reports":
        result.extend(_unmatched_financial_records(proofs, observed_financial, fields, owner, products))
    return result


def _supplementary_in_scope(owner, product_id, raw):
    if owner.product(product_id).get("fee_class") != "paid":
        return True
    grant = owner._grant(product_id) or {}
    codes = [raw["code"], *([raw["member"]] if raw.get("member") else [])]
    if any(
        code not in grant.get("codes", ()) or code.rsplit(".", 1)[-1] not in grant.get("markets", ()) for code in codes
    ):
        return False
    coordinates = [
        raw[field]
        for field in (
            "date",
            "ex_date",
            "effective_date",
            "period",
            "start",
            "end",
            "valid_from",
            "valid_to",
            "origin_date",
        )
        if raw.get(field)
    ]
    if not coordinates:
        return False
    try:
        dates = [date.fromisoformat(str(value)).isoformat() for value in coordinates]
    except ValueError:
        return False
    return all(grant.get("start", "~") <= day <= grant.get("end", "") for day in dates)


_SECTOR_FACTORS = {
    "sma",
    "sma_filter",
    "sma_cross",
    "donchian_breakout",
    "absolute_momentum",
    "price_momentum",
    "risk_adjusted_momentum",
    "relative_strength",
    "short_reversal",
    "price_deviation_z",
    "sector_rotation_score",
}


def _fields(definition, request=None):
    table_fields = dict(definition.fields)
    if request is not None:
        from .catalog import get_factor
        from .cross_section import component_requests

        children = component_requests(request)
        if children:
            table_fields = {
                table: fields
                for table, fields in table_fields.items()
                if table in ("memberships", "membership_coverage")
            }
            dependencies = [(child, child_request) for child, child_request in children.values()]
            modes = [request.parameters.get("neutralize")]
            if definition.factor_id == "weighted_factor_score":
                modes.extend(settings.get("neutralize") for settings in request.parameters["components"].values())
            if request.parameters.get("exposure_benchmark"):
                modes.append("both")
                dependencies.extend((get_factor(name), None) for name in ("market_beta", "price_momentum"))
            if "both" in modes or "size" in modes:
                dependencies.append((get_factor("total_market_cap"), None))
            if "both" in modes or "industry" in modes:
                table_fields["classifications"] = ()
            for child, child_request in dependencies:
                for table, fields in _fields(child, child_request).items():
                    table_fields[table] = tuple(dict.fromkeys([*table_fields.get(table, ()), *fields]))
    if "daily_bars" in table_fields:
        table_fields.setdefault("corporate_actions", ("m", "d", "same_share_basis"))
        table_fields.setdefault("action_coverage", ())
    if definition.factor_id in ("dividend_yield", "ex_distribution_return"):
        table_fields.setdefault("events", ())
    if definition.factor_id in _SECTOR_FACTORS:
        for table in ("sector_proxies", "memberships", "membership_coverage"):
            table_fields.setdefault(table, ())
    return table_fields


def _supplementary(store, source, definition, fields_by_table):
    """Read additional fields only through verified evidence registered in the selected asset."""
    tables = {}
    for owner, ref, document in selected_snapshot_assets(store, source, "knowledge_evidence"):
        products = _table_products(document, "knowledge_evidence")
        if not products or not _authorized(owner, document, "knowledge_evidence", products, definition.purpose):
            continue
        for table in dict.fromkeys([*fields_by_table, "action_coverage"]):
            if table == "calendar":
                continue
            for group in _proof_fields(owner, ref, document, f"factor_input.{table}", ()).values():
                for proof, original in group:
                    raw = original.get("value")
                    if not isinstance(raw, dict) or not raw.get("code") or not raw.get("source_ref"):
                        continue
                    if not _same_public_instant(raw.get("available_at", proof.published_at), proof.published_at):
                        continue
                    if original.get("product_id") != proof.product_id:
                        continue
                    if not _supplementary_in_scope(owner, proof.product_id, raw):
                        continue
                    if original.get("record_key") != f"{table}|{raw['code']}|{raw.get('coordinate', '')}":
                        continue
                    tables.setdefault(table, []).append(
                        dict(
                            raw,
                            fact_key=proof.record_key,
                            available_at=raw.get("available_at", proof.published_at),
                            revision_id=proof.revision_id,
                            supersedes=str(proof.supersedes),
                            evidence_ref=proof.evidence_ref,
                            snapshot_id=ref.snapshot_id,
                            source_root=str(owner.root.resolve()),
                            product_id=proof.product_id,
                        )
                    )
    return tables


def _table_products(document, table):
    return {entry["product_id"] for entry in document["table_files"] if entry["table"] == table}


def _local(store, source, definition, request, label):
    gaps, daily_authorizations = [], []
    manifest = None if isinstance(source, SnapshotRef) else load_library(store, source)
    registered = {
        (entry["root"], entry["snapshot_id"])
        for entry in (manifest or {}).get("asset_snapshots", [])
        if "knowledge_evidence" in entry["tables"]
    }
    documents = (
        (store.load_manifest(source),)
        if manifest is None
        else tuple(item[2] for item in selected_snapshot_assets(store, source, "knowledge_evidence"))
    )
    kinds = {item.get("source", {}).get("data_nature") for document in documents for item in document.get("items", ())}
    if kinds and all(value and "合成" in value for value in kinds):
        label["nature"] = "本地已发布 · 离线合成资料"
    fields_by_table = _fields(definition, request)
    versions = _supplementary(store, source, definition, fields_by_table)
    if "daily_bars" in fields_by_table:
        fields_by_table = {**fields_by_table, "corporate_actions": ("m", "d", "same_share_basis")}
        if set(definition.subject_kind) & {"行业", "主题", "ETF"} or request.parameters.get("benchmark"):
            fields_by_table["index_bars"] = ("close", "amount_cny", "return_basis")
    # Resolve native members first. A group request uses member codes in its window, never today's universe.
    order = sorted(fields_by_table, key=lambda name: {"calendar": 0, "memberships": 1}.get(name, 2))
    for table in order:
        fields = fields_by_table[table]
        rows = versions.setdefault(table, [])
        for owner, ref, document in selected_snapshot_assets(store, source, table):
            products = _table_products(document, table)
            if not products:
                continue
            if not _authorized(owner, document, table, products, definition.purpose):
                if table not in definition.fields:
                    continue
                gaps.append(
                    dict(
                        subject=", ".join(request.subjects),
                        field=table,
                        period=str(request.trade_date),
                        source=label,
                        reason=f"所选资产 {ref.snapshot_id} 缺少该用途许可",
                    )
                )
                continue
            proof_products = _table_products(document, "knowledge_evidence")
            proof_registered = manifest is None or (str(owner.root.resolve()), ref.snapshot_id) in registered
            proof_registered = (
                proof_registered
                and bool(proof_products)
                and _authorized(owner, document, "knowledge_evidence", proof_products, definition.purpose)
            )
            if table == "daily_bars":
                codes = list(request.subjects)
                for item in ("benchmark", "market", "group", "exposure_benchmark"):
                    if value := request.parameters.get(item):
                        codes.append(str(value))
                proxies = select_versions(versions.get("sector_proxies", []), request.cutoff)
                codes.extend(row["symbol"] for row in proxies if row.get("symbol") and row.get("code") in codes)
                effective = select_versions(versions.get("memberships", []), request.cutoff)
                codes.extend(
                    row["member"]
                    for row in effective
                    if row.get("member")
                    and row.get("code") in codes
                    and (row.get("valid_from") or "9999") <= str(request.trade_date)
                )
                if proof_registered:
                    from .cross_section import component_requests

                    children = [child for _, child in component_requests(request).values()]
                    codes.extend(
                        str(child.parameters["benchmark"]) for child in children if child.parameters.get("benchmark")
                    )
                    calendar = select_versions(versions.get("calendar", []), request.cutoff)
                    start = min(_daily_start(query, calendar) for query in [request, *children])
                    for product in sorted(products):
                        requested = tuple(dict.fromkeys(codes))
                        if owner.product(product).get("fee_class") == "paid":
                            grant = owner._grant(product) or {}
                            requested = tuple(
                                code
                                for code in requested
                                if code in grant.get("codes", [])
                                and code.rsplit(".", 1)[-1] in grant.get("markets", [])
                            )
                            daily_authorizations.append(
                                dict(
                                    product_id=product,
                                    snapshot_id=ref.snapshot_id,
                                    codes=list(requested),
                                    start=grant.get("start"),
                                    end=grant.get("end"),
                                )
                            )
                        rows.extend(_daily_records(owner, ref, document, request, fields, requested, start, product))
                continue
            frame = owner.read_table(ref, table)
            if table == "calendar":
                public = {}
                for product in products:
                    sources, _ = _permission_sources(owner, document, table, product)
                    public[product] = next(
                        (item["published_at"] for item in sources if _known(item.get("published_at"), request.cutoff)),
                        None,
                    )
                frame["available_at"] = frame["product_id"].map(public)
                frame["is_trading_day"] = (
                    frame["is_trading_day"]
                    .astype(str)
                    .str.lower()
                    .map({"true": True, "1": True, "false": False, "0": False})
                )
            proofs = _proof_fields(owner, ref, document, table, ()) if proof_registered and table != "calendar" else {}
            if table in ("memberships", "security_status"):
                fields = tuple(
                    field
                    for field in TABLE_SCHEMAS[table]["columns"]
                    if field
                    not in ("code", "date", "product_id", "source_ref", "available_at", "observed_at", "board_id")
                )
            rows.extend(
                dict(row, source_root=str(owner.root.resolve()), snapshot_id=ref.snapshot_id)
                for row in _native_records(table, frame, fields, proofs, owner=owner, products=products)
            )
    tables = _selected_tables(versions, request)
    if "daily_bars" in tables and "index_bars" in tables and not tables["index_bars"].empty:
        indexes = tables["index_bars"]
        indexes = (
            indexes[indexes["return_basis"].isin(("price", "price_return"))] if "return_basis" in indexes else None
        )
        if indexes is not None:
            tables["daily_bars"] = pd.concat([tables["daily_bars"], indexes], ignore_index=True)
    return FactorFacts(tables, label, gaps, versions, daily_authorizations)


def _selected_tables(versions, request):
    return {table: _shape(rows, table, request) for table, rows in versions.items()}


_DISCLOSURE_FIELDS = ("parent_net_profit", "forecast_lower", "forecast_upper")


def _first_release_record(row):
    originals = {proof["announcement_ref"] for field, proof in (row.get("field_evidence") or {}).items()
                 if field in _DISCLOSURE_FIELDS and proof.get("announcement_ref")}
    if notice := row.get("announcement_ref"):
        originals.add(notice)
    if len(originals) == 1 and row.get("period"):
        original, = originals
        scope = ("publication", original) if _public_document_url(original) else ("asset", row.get("source_root"))
        return scope, row.get("product_id"), row["code"], row["period"], original
    if (record := _revision_record(row)) is not None:
        return row["registered_table"], record


def _public_document_url(declared):
    """Check the address syntax of a reference already declared as a publication."""
    if isinstance(declared, str):
        try:
            address = urlsplit(declared)
            if address.scheme in ("https", "http") and address.hostname and address.path not in ("", "/"):
                return declared
        except ValueError:
            return None


def _disclosure_notice(row, evidence):
    if notice := evidence.get("announcement_ref"):
        return notice
    locator = evidence.get("source_ref")
    if not locator or locator == row.get("source_ref"):
        return row.get("announcement_ref")


def _disclosure_original(row, evidence, registration):
    notice = _disclosure_notice(row, evidence)
    # Source locations remain provenance even when reachable via HTTPS.
    if url := _public_document_url(notice):
        return "publication", url
    if notice:
        return "notice", row.get("source_root"), row.get("snapshot_id"), row.get("product_id"), notice
    # A field locator can distinguish its source inside an import row. Shared
    # locator names do not supply publication identity across registrations.
    return "registration", registration, evidence.get("source_ref")


def _legacy_cross_table_registration(row, index, registrations):
    # Some older inputs copy the complete first-release record into both tables.
    # Only those copies share a registration. Partial rows in one import stay separate.
    if not row.get("source_ref"):
        return index
    scope = tuple(row.get(field) for field in (
        "code", "period", "source_root", "snapshot_id", "product_id", "source_ref", "coordinate",
        "revision_id", "supersedes",
    )) + (_public_instant(row["available_at"]),)
    previous = registrations[scope]
    for registered, other in previous:
        if (row["registered_table"] != other["registered_table"] and _same_facts(row, other)
                and all(row.get(field) == other.get(field) for field in (
                    "field_evidence", "_conflicts", "_revision_cycles", "disclosure_revisions",
                ))):
            return registered
    previous.append((index, row))
    return index


def _corroborated_disclosure_groups(originals, witnesses):
    aliases = {}

    def original_of(key):
        while key in aliases:
            key = aliases[key]
        return key

    def complete_registrations(records):
        registrations = []
        for stamp, row in records:
            if not (coordinate := row.get("coordinate")):
                continue
            comparison = {field: value for field, value in row.items()
                          if field not in ("source_root", "snapshot_id", "evidence_ref")}
            # The checked table prefix locates the registration within the asset.
            # Compare the same original coordinate across both first-disclosure tables.
            if row.get("fact_key") == f"{row['registered_table']}|{row['code']}|{coordinate}":
                del comparison["registered_table"]
                comparison["fact_key"] = (row["code"], coordinate)
            registrations.append((stamp, comparison))
        return registrations

    # Attach a copy only when all its registrations already exist in a larger
    # group (or an identical group). A shared row cannot join distinct claims.
    priority = {key: index for index, key in enumerate(sorted(originals, key=lambda key: -len(originals[key])))}
    for possible in witnesses.values():
        complete = []
        for registration in sorted(possible, key=priority.__getitem__):
            current = complete_registrations(originals[registration])
            if len(current) != len(originals[registration]):
                continue
            for prior, registered in complete:
                if all(current.count(record) <= registered.count(record) for record in current):
                    aliases.setdefault(registration, prior)
                    break
            else:
                complete.append((registration, current))
    groups = defaultdict(list)
    for key, records in originals.items():
        groups[original_of(key)].extend(records)
    return groups.values()


def _is_first_disclosure_field(row, field):
    designation = quotation(row, field, "first_disclosure")
    return designation is True or (designation is None and row["registered_table"] == "initial_reports")


def _with_first_disclosed_bounds(rows, corrections):
    def has_field(row, field):
        return row.get(field) is not None or any(field in (row.get(key) or {}) for key in (
            "field_evidence", "_conflicts", "_revision_cycles",
        ))

    if not corrections or any(has_field(row, "parent_net_profit") for row in rows):
        return rows
    supplements = []
    for field in ("forecast_lower", "forecast_upper"):
        candidates = []
        for amendment in corrections:
            if has_field(amendment, field) and _is_first_disclosure_field(amendment, field):
                stamp = _public_instant(quotation(amendment, field, "available_at"))
                if stamp is not None:
                    candidates.append((stamp, amendment))
        if not candidates:
            continue
        first = min(stamp for stamp, _ in candidates)
        original_stamps = [_public_instant(quotation(row, field, "available_at"))
                           for row in rows if has_field(row, field)]
        if any(stamp is None or stamp <= first for stamp in original_stamps):
            continue
        if original_stamps:
            rows = [_without_fields(row, {field}) for row in rows]
        supplements.extend(
            _verified_financial_row(_without_fields(row, set(_FINANCIAL_AMOUNTS) - {field}))
            for stamp, row in candidates if stamp == first
        )
    return [*rows, *supplements]


def first_disclosures(facts, request, code):
    visible = []
    for table in ("financial_reports", "initial_reports"):
        versions = facts.versions[table] if table in facts.versions else facts.records(table, code)
        for row in versions:
            if row.get("code") != code or not _known(row.get("available_at"), request.cutoff):
                continue
            if period := _period_from_raw(row.get("period") or row.get("report_period")):
                visible.append(dict(_field_evidence_at(row, request.cutoff), period=period, registered_table=table))
    originals = defaultdict(list)
    witnesses = defaultdict(dict)
    legacy_registrations = defaultdict(list)
    for index, row in enumerate(_live_versions(visible, first_releases=True)):
        registration = _legacy_cross_table_registration(row, index, legacy_registrations)
        scoped_fields = defaultdict(dict)
        for field in _DISCLOSURE_FIELDS:
            evidence = (row.get("field_evidence") or {}).get(field, {})
            if row.get(field) is None and not evidence and field not in (row.get("_conflicts") or {}):
                continue
            if not _is_first_disclosure_field(row, field):
                continue
            stamp = _public_instant(quotation(row, field, "available_at"))
            if stamp is None:
                continue
            original = _disclosure_original(row, evidence, registration)
            scoped_fields[original][field] = stamp
            locator = evidence.get("source_ref") or row.get("source_ref")
            if original[0] == "notice" and locator:
                witnesses[row["period"], original[-1], locator][row["period"], original] = None
        for original, eligible in scoped_fields.items():
            key = row["period"], original
            release = _verified_financial_row(_without_fields(row, set(_DISCLOSURE_FIELDS) - eligible.keys()))
            conflicts = release.get("_conflicts") or {}
            if row.get("supersedes") not in (None, "", "None", "<NA>") and "revision_cycle" not in conflicts:
                # A surviving correction did not have a proven predecessor in the visible release chain.
                release = dict(release, _conflicts={**conflicts, "initial_reports": [release]})
            originals[key].append((min(eligible.values()), release))
    selected = []
    for records in _corroborated_disclosure_groups(originals, witnesses):
        ordered = sorted(records, key=lambda item: item[0])
        stamp = ordered[0][0]
        rows = [row for _, row in ordered]
        corrections = []
        for row in rows:
            for amendment in row.get("disclosure_revisions", ()):
                if amendment not in corrections:
                    corrections.append(amendment)
        merged = _merge_financial_reports(_with_first_disclosed_bounds(rows, corrections))
        if corrections:
            merged = dict(merged, disclosure_revisions=corrections)
        selected.append(dict(merged, first_known_at=stamp.isoformat()))
    return selected


def known_records(facts, table, request, code):
    if table not in facts.versions:
        return facts.records(table, code)
    versions = facts.versions[table]
    if table == "daily_bars":
        versions = _join_daily(versions, request.cutoff)
    return [
        row
        for row in select_versions(versions, request.cutoff, table=table)
        if row.get("code") == code and (not row.get("date") or row["date"] <= str(request.trade_date))
    ]


def read_facts(store, source, definition, request):
    if isinstance(source, TeachingSource):
        document = teaching_document(source)
        label = dict(nature="离线合成教学", id=source.case_id)
        versions = {key: value for key, value in document["tables"].items() if key in _fields(definition, request)}
        _link_legacy_teaching_revisions(versions, request.cutoff)
        tables = _selected_tables(versions, request)
        return FactorFacts(tables, label, versions=versions)
    label = dict(nature="本地已发布", id=getattr(source, "snapshot_id", getattr(source, "library_id", "")))
    return _local(store, source, definition, request, label)
