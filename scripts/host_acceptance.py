"""Stage-01 host acceptance observation helpers (HETU phase 5).

Deterministic, offline-replayable metering for multi-host research runs.

Review-driven semantics (stage-01 fix, 2026-09-08):

- Deduplication uses native event identity (source/session/kind/message or
  segment+sequence). Extra metadata such as capture timestamps never creates
  new consumption, and conflicting values for one identity are an explicit
  gap instead of a silent last-value win.
- Missing required input/output tokens stay unknown (``null``); a real zero,
  a provable subtotal and a complete total are different outcomes.
- ``included_in_parent`` must point at an existing, counted parent session
  that declares ``includes_children``; anything else is counted conservatively
  and reported as an unverified claim.
- Cumulative, incremental and message-final metering are never mixed inside
  one counting range; cumulative resets are checked on every observable
  counter (input, output and both cache fields).
- Cache reads and cache writes are separate fields. Cache detail gaps are
  reported but do not block input/output comparison; mixed cache semantics
  are never published as one blended total.
- Tool totals use the same task boundary as token totals; tool identity
  includes the source host, and wrapper dispatches are counted separately.
- Timing validates types, finiteness, ordering and the shared clock basis;
  inverted, zero-length or out-of-window results fail loudly instead of
  producing negative uncovered time or meaningless ratios that pass.
- ``check`` requires non-empty events, declared coverage, real request and
  delivery times, and verified isolation evidence before it can pass; outputs
  are written atomically and never overwrite existing evidence.

Second review round (stage-01 fix2, 2026-09-08):

- Cumulative snapshots compress to one consumption per counting range: the
  final observed value counts, a ``snapshot: "start"`` baseline observed
  before the range is subtracted, and recovery resets across segments are
  handled by segment boundaries instead of re-adding every snapshot.
- Unknown cache inclusion semantics never normalize a total and never count
  as exclusive; ``complete`` is judged per metric, so a null input total is
  never reported as an established input usage.
- Live collection consumes only complete lines (byte-offset cursor), drains
  the tail after the child exits, locks onto one native session, records
  rotation/truncation as unrecoverable collection gaps, and starts a new
  segment per generation so offline recomputation stays possible.
- Evidence whitelists apply to nested structures too (tool_calls entries keep
  only id/name/classification); JSON publication is create-only even under
  races (``os.link`` publish).
- ``check`` verifies isolation evidence exists, parses, matches the task
  identity and carries the required verdicts and controls; truncated lines
  and collection gaps fail the check; a non-probe run must declare at least
  the mandatory research and review scopes.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SKILL_DIR_NAME = "hetu-stock-analysis"

USAGE_FIELDS = ("input_tokens", "output_tokens")
CACHE_FIELDS = ("cached_input_read", "cached_input_write")

ALLOWED_EVENT_FIELDS = {
    "source",
    "session_id",
    "segment_id",
    "message_id",
    "scope",
    "kind",
    "input_tokens",
    "output_tokens",
    "cached_input_read",
    "cached_input_write",
    "input_includes_cache",
    "parent_inclusion",
    "includes_children_tools",
    "included_in_parent",
    "tool_calls",
    "model",
    "sequence",
    "snapshot",
    "started_at",
    "completed_at",
    "captured_at",
}
ALLOWED_TOOL_CALL_FIELDS = {"id", "name", "external", "wrapper"}
ALLOWED_COLLECTION_GAP_FIELDS = {"type", "detail", "generation", "session_id", "at"}
ALLOWED_CHECK_INPUT_FIELDS = {
    "case_id",
    "task_identity",
    "expected_scopes",
    "declared_absent_scopes",
    "probe_mode",
    "request_at",
    "delivered_at",
    "delivery_source",
    "timing_intervals",
    "isolation",
}
ALLOWED_CASE_FIELDS = {
    "case_id",
    "security",
    "neutral_request",
    "as_of",
    "data_mode",
    "depth",
    "reuse_previous_task_data",
    "allowed_materials",
    "controlled_triggers",
    "probe_mode",
}
ALLOWED_ISOLATION_FIELDS = {
    "status",
    "method",
    "evidence",
    "verified_at",
    "cases",
}

# Delivery endpoint provenance. Only an independently observed delivery
# (a future adapter that captures the user-facing final message itself) may
# claim the delivery arc is fully metered; anything else stays a gap.
DELIVERY_SOURCE_OBSERVED = "independently-observed"
UNOBSERVED_DELIVERY_SOURCES = ("coordinator-reported", "last-scope-completed")


# ---------------------------------------------------------------------------
# usage aggregation


def _usage_values(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "input_tokens": event.get("input_tokens"),
        "output_tokens": event.get("output_tokens"),
        "cached_input_read": event.get("cached_input_read"),
        "cached_input_write": event.get("cached_input_write"),
    }


def _identity_key(event: dict[str, Any]) -> tuple[Any, ...]:
    kind = event.get("kind")
    if kind == "cumulative":
        return (
            event.get("source"),
            event.get("session_id"),
            kind,
            event.get("segment_id"),
            event.get("sequence"),
        )
    return (
        event.get("source"),
        event.get("session_id"),
        kind,
        event.get("message_id"),
    )


def _valid_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def summarize_usage(
    events: list[dict[str, Any]], *, expected_scopes: set[str] | frozenset[str]
) -> dict[str, Any]:
    """Aggregate whitelisted usage events by native identity and semantics.

    Returns input/output totals only when a same-basis number exists;
    unknown values stay ``null``. ``complete`` reflects the input/output
    coverage and semantics required for comparison; cache-detail gaps are
    reported separately in ``cache_gaps`` and never block that comparison.
    """
    blocking: list[dict[str, Any]] = []
    cache_gaps: list[dict[str, Any]] = []

    # 0. schema gate: unknown fields mean the caller is off-contract.
    for event in events:
        unknown = set(event) - ALLOWED_EVENT_FIELDS
        if unknown:
            blocking.append(
                {
                    "type": "unknown_field",
                    "detail": f"event carries non-whitelisted fields: {sorted(unknown)}",
                }
            )

    # 1. dedup by native identity; conflicting usage values are a gap.
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for event in events:
        groups.setdefault(_identity_key(event), []).append(event)
    deduped: list[dict[str, Any]] = []
    for key, group in groups.items():
        signatures = {json.dumps(_usage_values(event), sort_keys=True) for event in group}
        if len(signatures) > 1:
            blocking.append(
                {
                    "type": "usage_conflict",
                    "detail": f"identity {key} carries {len(signatures)} divergent usage values",
                }
            )
            continue
        merged = dict(group[0])
        semantics = {event.get("input_includes_cache") for event in group}
        if len(semantics) > 1:
            # the same native event cannot declare two opposite cache bases;
            # the declaration becomes unknown instead of picking one silently
            blocking.append(
                {
                    "type": "cache_semantics_conflict",
                    "detail": (
                        f"identity {key} carries contradictory input_includes_cache "
                        f"declarations {sorted(str(item) for item in semantics)}"
                    ),
                }
            )
            merged["input_includes_cache"] = None
        tools: dict[Any, dict[str, Any]] = {}
        for event in group:
            for tool in event.get("tool_calls") or []:
                tools.setdefault(tool.get("id"), tool)
        if tools:
            merged["tool_calls"] = list(tools.values())
        deduped.append(merged)

    # 2. required usage fields: unknown stays unknown, never zero.
    valid: list[dict[str, Any]] = []
    for event in deduped:
        problems = [
            field
            for field in USAGE_FIELDS
            if not (_valid_int(event.get(field)) and event[field] >= 0)
        ]
        if problems:
            blocking.append(
                {
                    "type": "missing_required_usage",
                    "detail": (
                        f"identity {_identity_key(event)} lacks valid "
                        f"{problems} (absent, non-integer or negative)"
                    ),
                }
            )
            continue
        valid.append(event)

    # 3. one metering semantics per counting range (scope+session+segment).
    ranges: dict[tuple[Any, ...], set[str]] = {}
    for event in valid:
        key = (event.get("scope"), event.get("session_id"), event.get("segment_id"))
        ranges.setdefault(key, set()).add(event.get("kind"))
    for key, kinds in ranges.items():
        if len(kinds) > 1:
            blocking.append(
                {
                    "type": "mixed_metering_kinds",
                    "detail": (
                        f"counting range {key} mixes metering kinds {sorted(kinds)}; "
                        "the client definition must select one basis"
                    ),
                }
            )

    def range_blocked(event: dict[str, Any]) -> bool:
        key = (event.get("scope"), event.get("session_id"), event.get("segment_id"))
        return len(ranges.get(key, set())) > 1

    # 4. cumulative segments: snapshots compress to one consumption per range.
    #    The final observed value of the range counts; a snapshot marked
    #    ``snapshot="start"`` is the baseline observed BEFORE the range and is
    #    subtracted, never added. Resets on any observable counter keep the
    #    whole range out of the totals (unbounded), and events without a
    #    usable sequence cannot be ordered, so they also block the range.
    cumulative_groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for event in valid:
        if event.get("kind") == "cumulative":
            key = (event.get("scope"), event.get("session_id"), event.get("segment_id"))
            cumulative_groups.setdefault(key, []).append(event)

    reset_segments: set[tuple[Any, ...]] = set()
    cumulative_consumption: dict[tuple[Any, ...], dict[str, int | None]] = {}
    for key, group in cumulative_groups.items():
        if any(not _valid_int(event.get("sequence")) for event in group):
            reset_segments.add(key)
            blocking.append(
                {
                    "type": "cumulative_sequence_unknown",
                    "detail": (
                        f"cumulative range {key} has snapshots without a usable "
                        "sequence; the range order and consumption are unknown"
                    ),
                }
            )
            continue
        ordered = sorted(group, key=lambda event: event.get("sequence"))
        range_reset = False
        for counter in ("input_tokens", "output_tokens", *CACHE_FIELDS):
            known = [
                event.get(counter)
                for event in ordered
                if _valid_int(event.get(counter))
            ]
            for earlier, later in zip(known, known[1:], strict=False):
                if later < earlier:
                    range_reset = True
                    blocking.append(
                        {
                            "type": "cumulative_reset_unbounded",
                            "detail": (
                                f"counter {counter} dropped inside range {key} "
                                f"({earlier} -> {later})"
                            ),
                        }
                    )
                    break
        baselines: dict[str, list[int]] = {}
        for event in ordered:
            if event.get("snapshot") == "start":
                for counter in ("input_tokens", "output_tokens", *CACHE_FIELDS):
                    if _valid_int(event.get(counter)):
                        baselines.setdefault(counter, []).append(event[counter])
        for counter, values in baselines.items():
            if len(set(values)) > 1:
                range_reset = True
                blocking.append(
                    {
                        "type": "cumulative_baseline_conflict",
                        "detail": (
                            f"range {key} carries contradictory start snapshots "
                            f"for {counter}: {sorted(set(values))}"
                        ),
                    }
                )
        if range_reset:
            reset_segments.add(key)
            continue
        consumption: dict[str, int | None] = {}
        for counter in ("input_tokens", "output_tokens", *CACHE_FIELDS):
            known = [
                event.get(counter)
                for event in ordered
                if _valid_int(event.get(counter))
            ]
            if not known:
                consumption[counter] = None
                continue
            baseline = baselines[counter][0] if counter in baselines else 0
            consumption[counter] = known[-1] - baseline
        cumulative_consumption[key] = consumption

    counted = [event for event in valid if not range_blocked(event)]

    # 5. parent-child inclusion: a claim counts as covered only when the
    #    parent session exists, is counted, and declares includes_children.
    parent_declarations: dict[Any, set[str]] = {}
    parent_scope: dict[Any, str] = {}
    for event in counted:
        session = event.get("session_id")
        declaration = event.get("parent_inclusion")
        if declaration:
            parent_declarations.setdefault(session, set()).add(declaration)
            parent_scope.setdefault(session, event.get("scope"))

    def inclusion_verified(child: dict[str, Any]) -> bool:
        claim = child.get("included_in_parent")
        if not isinstance(claim, str):
            return False
        if claim not in parent_declarations:
            return False
        if "includes_children" not in parent_declarations[claim]:
            return False
        return parent_scope.get(claim) in expected_scopes

    # 6. per-scope aggregation with explicit exclusion bookkeeping.
    scopes: dict[str, dict[str, Any]] = {}
    tool_keys: set[tuple[Any, ...]] = set()
    external_tool_keys: set[tuple[Any, ...]] = set()
    wrapper_tool_keys: set[tuple[Any, ...]] = set()

    def blocked(event: dict[str, Any]) -> bool:
        if range_blocked(event):
            return True
        key = (event.get("scope"), event.get("session_id"), event.get("segment_id"))
        return key in reset_segments

    def add_tools(event: dict[str, Any]) -> None:
        for tool in event.get("tool_calls") or []:
            key = (event.get("source"), event.get("session_id"), tool.get("id"))
            tool_keys.add(key)
            if tool.get("external"):
                external_tool_keys.add(key)
            if tool.get("wrapper"):
                wrapper_tool_keys.add(key)

    def merge_flag(entry: dict[str, Any], scope: str, flag: Any) -> None:
        if flag is None:
            # an undeclared basis can never inherit a neighbour's known basis
            entry["semantics_unknown"] = True
            return
        flag = bool(flag)
        if entry["input_includes_cache"] is None:
            entry["input_includes_cache"] = flag
        elif entry["input_includes_cache"] != flag:
            blocking.append(
                {
                    "type": "cache_semantics_mixed",
                    "detail": (
                        f"scope {scope!r} mixes cache-inclusive and "
                        "cache-exclusive inputs"
                    ),
                }
            )

    consumed_ranges: set[tuple[Any, ...]] = set()
    for event in valid:
        scope = event.get("scope")
        entry = scopes.setdefault(scope, _empty_scope_entry())
        range_key = (scope, event.get("session_id"), event.get("segment_id"))

        if event.get("kind") == "cumulative":
            # a whole cumulative range contributes its compressed consumption
            # exactly once; later snapshots of the same range add nothing
            if blocked(event):
                entry["excluded_events"] = entry.get("excluded_events", 0) + 1
                continue
            if range_key in consumed_ranges:
                continue
            consumed_ranges.add(range_key)
            consumption = cumulative_consumption[range_key]
            entry["events"] += 1
            entry["input_tokens"] += consumption["input_tokens"] or 0
            entry["output_tokens"] += consumption["output_tokens"] or 0
            for field in CACHE_FIELDS:
                value = consumption[field]
                if value is None:
                    entry[field] = None
                elif entry[field] is not None:
                    entry[field] += value
            range_flag_values = [
                item.get("input_includes_cache")
                for item in cumulative_groups[range_key]
            ]
            if any(value is None for value in range_flag_values):
                entry["semantics_unknown"] = True
            range_flags = {value for value in range_flag_values if value is not None}
            if len(range_flags) > 1:
                blocking.append(
                    {
                        "type": "cache_semantics_mixed",
                        "detail": (
                            f"cumulative range {range_key} mixes cache-inclusive "
                            "and cache-exclusive snapshots"
                        ),
                    }
                )
            elif range_flags:
                merge_flag(entry, scope, range_flags.pop())
            else:
                merge_flag(entry, scope, None)
            if scope in expected_scopes:
                for item in cumulative_groups[range_key]:
                    add_tools(item)
            continue

        if blocked(event):
            entry["excluded_events"] = entry.get("excluded_events", 0) + 1
            continue

        if scope in expected_scopes:
            claim = event.get("included_in_parent")
            token_included = isinstance(claim, str) and inclusion_verified(event)
            if claim and not token_included:
                if isinstance(claim, str) and claim in parent_declarations and (
                    "excludes_children" in parent_declarations[claim]
                ):
                    blocking.append(
                        {
                            "type": "parent_child_contradiction",
                            "detail": (
                                f"child session claims inclusion in {claim!r} while "
                                "that session declares excludes_children"
                            ),
                        }
                    )
                else:
                    blocking.append(
                        {
                            "type": "unverified_parent_inclusion",
                            "detail": (
                                f"inclusion claim {claim!r} does not match an existing, "
                                "counted parent declaring includes_children; counting "
                                "the child conservatively"
                            ),
                        }
                    )
            # Parent token coverage says nothing about the child's tool list:
            # child tools are always counted separately (S4).
            add_tools(event)
            if token_included:
                # Tokens live in the parent; the child's tools were counted above.
                entry["included_events"] = entry.get("included_events", 0) + 1
                entry["included_in_parent"] = (
                    entry.get("included_events", 0) > 0 and entry["events"] == 0
                )
                entry["parent_session"] = claim
                continue

        entry["events"] += 1
        entry["input_tokens"] += event["input_tokens"]
        entry["output_tokens"] += event["output_tokens"]
        for field in CACHE_FIELDS:
            value = event.get(field)
            if value is None:
                entry[field] = None
            elif entry[field] is not None:
                entry[field] += value
        merge_flag(entry, scope, event.get("input_includes_cache"))

    # 7. coverage: every expected scope (and scope#segment) must be observed.
    observed_plain: set[str] = set()
    observed_segment: set[tuple[str, str]] = set()
    for event in counted:
        scope = event.get("scope")
        if scope in expected_scopes:
            observed_plain.add(scope)
            observed_segment.add((scope, str(event.get("segment_id"))))
    for expected in sorted(expected_scopes):
        if "#" in expected:
            scope, _, segment = expected.partition("#")
            if (scope, segment) not in observed_segment:
                blocking.append({"type": "missing_scope", "detail": expected})
        elif expected not in observed_plain:
            blocking.append({"type": "missing_scope", "detail": expected})

    if not events:
        blocking.append({"type": "empty_events", "detail": "no usage events provided"})

    # 8. totals with an explicit basis; mixed semantics never blend.
    task_scopes = {
        scope: entry
        for scope, entry in scopes.items()
        if scope in expected_scopes and not entry.get("included_in_parent")
    }
    nothing_counted = (
        all(entry["events"] == 0 for entry in task_scopes.values())
        if task_scopes
        else True
    )
    cache_known = bool(task_scopes) and all(
        entry.get(field) is not None
        for entry in task_scopes.values()
        for field in CACHE_FIELDS
    )
    flags: set[Any] = set()
    for entry in task_scopes.values():
        if entry.get("semantics_unknown") or entry["input_includes_cache"] is None:
            flags.add(None)
        else:
            flags.add(entry["input_includes_cache"])
    as_reported = {
        scope: {
            "input_tokens": entry["input_tokens"],
            "input_includes_cache": entry["input_includes_cache"],
        }
        for scope, entry in task_scopes.items()
    }

    input_total: int | None
    basis: str | None
    if not task_scopes or nothing_counted:
        input_total, basis = None, None
    elif flags == {True}:
        input_total = sum(entry["input_tokens"] for entry in task_scopes.values())
        basis = "inclusive"
    elif flags == {False}:
        input_total = sum(entry["input_tokens"] for entry in task_scopes.values())
        basis = "exclusive"
    elif None not in flags and cache_known:
        # mixed but fully declared and fully known cache details: the only
        # same-basis total is the cache-inclusive normalization
        input_total = sum(
            entry["input_tokens"]
            + (
                0
                if entry["input_includes_cache"]
                else (entry["cached_input_read"] or 0)
                + (entry["cached_input_write"] or 0)
            )
            for entry in task_scopes.values()
        )
        basis = "inclusive-normalized"
    else:
        input_total, basis = None, None
        if None in flags:
            reason = (
                "input_includes_cache is undeclared for at least one counted "
                "scope; the input basis is unknown, so no same-basis input "
                "total exists (guessing exclusive or inclusive is refused)"
            )
        else:
            reason = (
                "counted scopes mix cache-inclusive and cache-exclusive inputs "
                "and some cache details are unknown; only per-scope as-reported "
                "subtotals are published"
            )
        cache_gaps.append({"type": "cache_cannot_normalize", "detail": reason})

    def cache_sum(field: str) -> int | None:
        if not task_scopes:
            return None
        values = [entry.get(field) for entry in task_scopes.values()]
        if any(value is None for value in values):
            return None
        return sum(value for value in values if value is not None)

    for scope, entry in task_scopes.items():
        if entry["events"]:
            for field in CACHE_FIELDS:
                if entry.get(field) is None:
                    cache_gaps.append(
                        {
                            "type": "cache_unknown",
                            "detail": f"scope {scope!r} has events without {field}",
                        }
                    )

    output_total: int | None = (
        sum(entry["output_tokens"] for entry in task_scopes.values())
        if task_scopes and not nothing_counted
        else None
    )

    # completeness is judged per metric: a null input or output total means
    # that metric is NOT established, even when no blocking gap exists
    return {
        "input_tokens": input_total,
        "input_tokens_basis": basis,
        "input_tokens_as_reported": as_reported,
        "output_tokens": output_total,
        "cached_input_read_tokens": cache_sum("cached_input_read"),
        "cached_input_write_tokens": cache_sum("cached_input_write"),
        "input_includes_cache": flags.pop() if len(flags) == 1 else None,
        "complete": not blocking and input_total is not None and output_total is not None,
        "gaps": blocking,
        "cache_gaps": cache_gaps,
        "scopes": scopes,
        "tool_calls": {
            "unique_ids": len(tool_keys),
            "external_unique_ids": len(external_tool_keys),
            "wrapper_unique_ids": len(wrapper_tool_keys),
            "task_unique_ids": len(tool_keys - wrapper_tool_keys),
        },
    }


def _empty_scope_entry() -> dict[str, Any]:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_input_read": 0,
        "cached_input_write": 0,
        "events": 0,
        "included_events": 0,
        "excluded_events": 0,
        "input_includes_cache": None,
        "semantics_unknown": False,
        "included_in_parent": False,
    }


# ---------------------------------------------------------------------------
# timing aggregation


def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _merge_interval_union(intervals: list[tuple[float, float]]) -> float:
    if not intervals:
        return 0.0
    ordered = sorted(intervals)
    union = 0.0
    current_start, current_end = ordered[0]
    for start, end in ordered[1:]:
        if start > current_end:
            union += current_end - current_start
            current_start, current_end = start, end
        else:
            current_end = max(current_end, end)
    union += current_end - current_start
    return union


def summarize_timing(
    intervals: list[dict[str, Any]], *, request_at: float | None, delivered_at: float | None
) -> dict[str, Any]:
    """Compute per-category unions against the request->delivery window.

    Timestamps must be finite numbers on one clock basis with
    ``request_at <= delivered_at``; intervals outside the window, inverted or
    zero-length windows fail explicitly instead of producing negative
    uncovered time or meaningless ratios that pass.
    """
    gaps: list[dict[str, Any]] = []
    categories: dict[str, dict[str, Any]] = {}

    for label, value in (("request_at", request_at), ("delivered_at", delivered_at)):
        if value is not None and not _finite_number(value):
            gaps.append(
                {"type": "invalid_timestamp", "detail": f"{label} is not a finite number"}
            )

    valid_bounds = request_at is not None and delivered_at is not None and not any(
        gap["type"] == "invalid_timestamp" for gap in gaps
    )
    total_wait: float | None = None
    if valid_bounds:
        if delivered_at < request_at:  # type: ignore[operator]
            gaps.append(
                {
                    "type": "inverted_window",
                    "detail": (
                        f"delivered_at {delivered_at} precedes request_at {request_at}"
                    ),
                }
            )
        elif delivered_at == request_at:
            gaps.append(
                {
                    "type": "zero_total_wait",
                    "detail": "delivered_at equals request_at; no measurable wait",
                }
            )
        else:
            total_wait = delivered_at - request_at

    clocks = {interval.get("clock", "wall") for interval in intervals}
    if len(clocks) > 1:
        gaps.append(
            {
                "type": "clock_basis_mismatch",
                "detail": f"intervals mix clock bases: {sorted(str(c) for c in clocks)}",
            }
        )

    usable: list[dict[str, Any]] = []
    for interval in intervals:
        category = interval.get("category")
        start, end = interval.get("start"), interval.get("end")
        if not category or not _finite_number(start) or not _finite_number(end):
            gaps.append(
                {"type": "malformed_interval", "detail": f"invalid interval: {interval!r}"}
            )
            continue
        if end < start:
            gaps.append(
                {"type": "malformed_interval", "detail": f"end before start: {interval!r}"}
            )
            continue
        if valid_bounds and total_wait is not None and (
            start < request_at or end > delivered_at  # type: ignore[operator]
        ):
            gaps.append(
                {
                    "type": "interval_outside_window",
                    "detail": (
                        f"interval [{start}, {end}] for {category!r} exceeds the "
                        f"request->delivery window [{request_at}, {delivered_at}]"
                    ),
                }
            )
            continue
        if len(clocks) <= 1:
            usable.append(interval)
            categories.setdefault(category, {"union": 0.0, "ratio": None})

    in_window = total_wait is not None and total_wait > 0
    if usable and len(clocks) <= 1 and in_window:
        by_category: dict[str, list[tuple[float, float]]] = {}
        overall: list[tuple[float, float]] = []
        for interval in usable:
            by_category.setdefault(interval["category"], []).append(
                (interval["start"], interval["end"])
            )
            overall.append((interval["start"], interval["end"]))
        for category, spans in by_category.items():
            categories[category]["union"] = _merge_interval_union(spans)
            categories[category]["ratio"] = categories[category]["union"] / total_wait
        covered = _merge_interval_union(overall)
    else:
        covered = None
        for entry in categories.values():
            entry["union"] = None

    uncovered = None if covered is None or total_wait is None else total_wait - covered

    return {
        "categories": categories,
        "covered_union": covered,
        "uncovered": uncovered,
        "total_wait": total_wait,
        "complete": not gaps and total_wait is not None,
        "gaps": gaps,
    }


# ---------------------------------------------------------------------------
# evidence persistence


def _reject_unknown_fields(record: dict[str, Any], allowed: set[str], kind: str) -> None:
    unknown = set(record) - allowed
    if unknown:
        raise ValueError(
            f"{kind} record carries non-whitelisted fields: {sorted(unknown)}; "
            "evidence records accept only the documented measurement fields"
        )


def _validate_event(event: dict[str, Any]) -> None:
    _reject_unknown_fields(event, ALLOWED_EVENT_FIELDS, "usage event")
    for tool in event.get("tool_calls") or []:
        if not isinstance(tool, dict):
            raise ValueError("tool_calls entries must be objects")
        _reject_unknown_fields(tool, ALLOWED_TOOL_CALL_FIELDS, "tool_calls entry")


def append_event(path: Path, event: dict[str, Any]) -> None:
    """Append one evidence record with an immediate flush.

    The whitelist applies to nested structures too: tool_calls entries keep
    only their identity, name and classification, so tool parameters, tool
    results, prompt text and credentials can never enter the evidence stream.
    Validation happens before the file is opened, so a rejected record
    leaves the file untouched.
    """
    _validate_event(event)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def append_collection_gap(output_dir: Path, gap: dict[str, Any]) -> None:
    """Record an unrecoverable collection problem (rotation tail, corrupt
    line, terminal agent failure). ``check`` fails on any recorded gap."""
    _reject_unknown_fields(gap, ALLOWED_COLLECTION_GAP_FIELDS, "collection gap")
    with open(output_dir / "collection-gaps.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps(gap, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def load_collection_gaps(evidence_dir: Path) -> list[dict[str, Any]]:
    """Load collection gaps; unparsable or off-contract records are
    themselves reported as gaps (fail closed)."""
    path = Path(evidence_dir) / "collection-gaps.jsonl"
    if not path.exists():
        return []
    gaps: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError:
                gaps.append({"type": "unparsable_collection_gap", "detail": stripped[:200]})
                continue
            if not isinstance(record, dict) or set(record) - ALLOWED_COLLECTION_GAP_FIELDS:
                gaps.append(
                    {
                        "type": "unparsable_collection_gap",
                        "detail": "collection gap record is off-contract",
                    }
                )
                continue
            gaps.append(record)
    return gaps


def flush_segment(path: Path) -> None:
    """Segment-end durability: fsync the evidence file."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def load_events(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load JSONL evidence; truncated tails become diagnostics, never data."""
    events: list[dict[str, Any]] = []
    truncated_lines = 0
    truncated_raw = ""
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                events.append(json.loads(stripped))
            except json.JSONDecodeError:
                truncated_lines += 1
                truncated_raw = stripped
    return events, {
        "truncated_lines": truncated_lines,
        "truncated_raw": truncated_raw,
    }


def events_from_closeout_verifier_usage(
    path: Path, *, case: str
) -> list[dict[str, Any]]:
    """Map the desensitized closeout verifier events to whitelisted usage events.

    The closeout copy records claude CLI message-level final usage where the
    reported input excludes the cached subsets. Cache reads and cache writes
    stay separate fields (writes happened to be zero for these sessions, but
    the mapping never merges them into one "hit" number).
    """
    mapped: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            if record.get("case") != case:
                continue
            session = Path(record.get("source", "")).stem
            usage = record.get("usage") or {}
            mapped.append(
                {
                    "source": "claude",
                    "session_id": session,
                    "segment_id": session,
                    "message_id": record.get("message_id"),
                    "scope": "review",
                    "kind": "message_final",
                    "input_tokens": usage.get("input_tokens"),
                    "output_tokens": usage.get("output_tokens"),
                    "cached_input_read": usage.get("cache_read_input_tokens"),
                    "cached_input_write": usage.get("cache_creation_input_tokens"),
                    "input_includes_cache": False,
                    "model": record.get("model"),
                    "tool_calls": [
                        {"id": call.get("id"), "name": call.get("name")}
                        for call in record.get("tool_calls") or []
                    ],
                }
            )
    return mapped


# ---------------------------------------------------------------------------
# atomic JSON output (never overwrites existing evidence)


def _write_json(path: Path, payload: Any) -> None:
    """Publish a JSON report atomically and create-only.

    The existence check is only a fast friendly error; the actual guarantee
    comes from ``os.link``, which fails atomically when the target appeared
    in the meantime, so a racing writer can never be replaced.
    """
    path = Path(path)
    if path.exists():
        raise FileExistsError(
            f"refusing to overwrite existing file: {path}; choose a new output path"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".tmp-{os.getpid()}")
    try:
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp, path)
        except FileExistsError:
            raise
        except OSError:
            # same-directory link is expected to work; if a platform refuses
            # it, fail loudly instead of falling back to a replace race
            raise
    finally:
        with contextlib.suppress(FileNotFoundError):
            temp.unlink()


# ---------------------------------------------------------------------------
# CLI: probe / run / check


def _load_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _claude_project_slug(cwd: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def _claude_sessions_for(cwd: Path) -> Path:
    return Path.home() / ".claude" / "projects" / _claude_project_slug(str(cwd))


def _probe_zcode(output_dir: Path) -> dict[str, Any]:
    """Verify ZCode capabilities checkable without an interactive
    coordinator; dispatch itself stays coordinator-mediated and is reported
    as a boundary, not papered over."""
    import subprocess

    verified: list[str] = []
    gaps: list[str] = []
    version = None
    plist = Path("/Applications/ZCode.app/Contents/Info.plist")
    if plist.exists():
        try:
            result = subprocess.run(
                [
                    "defaults",
                    "read",
                    "/Applications/ZCode.app/Contents/Info.plist",
                    "CFBundleShortVersionString",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            version = result.stdout.strip()
            verified.append(f"client version: {version}")
        except (OSError, subprocess.CalledProcessError) as error:
            gaps.append(f"client version unreadable: {error}")
    else:
        gaps.append("ZCode.app not found at /Applications/ZCode.app")

    rollout = Path.home() / ".zcode" / "cli" / "rollout"
    agents = Path.home() / ".zcode" / "cli" / "agents"
    if rollout.is_dir():
        verified.append(f"model-io observation directory readable: {rollout}")
    else:
        gaps.append(f"model-io observation directory missing: {rollout}")
    if agents.is_dir():
        verified.append(f"agent metadata directory readable: {agents}")
    else:
        gaps.append(f"agent metadata directory missing: {agents}")

    sample_usage = None
    for metadata in sorted(agents.glob("*/agent_*/metadata.json")):
        try:
            payload = _load_json(metadata)
        except (OSError, json.JSONDecodeError):
            continue
        usage = payload.get("usage")
        if usage:
            sample_usage = {"file": str(metadata), "usage": usage}
            verified.append("agent metadata usage schema parsed (sample)")
            break
    if sample_usage is None:
        gaps.append("no parsable agent metadata with usage found")

    gaps.append(
        "scriptable sub-agent dispatch not available: ZCode research contexts are "
        "dispatched by the interactive coordinator; use `run --host zcode "
        "--watch-agent <id>` to observe a coordinator-dispatched session"
    )
    report = {
        "host": "zcode",
        "mode": "probe",
        "client_version": version,
        "verified": verified,
        "gaps": gaps,
        "sample_usage": sample_usage,
        "model_identity": "recorded per event from rollout records",
        "dispatch": "coordinator-mediated",
    }
    _write_json(output_dir / "probe-zcode.json", report)
    return report


def _claude_isolation_probe(output_dir: Path) -> dict[str, Any]:
    """Two-case isolation probe with transcript-based verdicts.

    Positive case: the decoy "old research" file sits outside the probe
    working root; a compliant context must not return its content. Negative
    case: the decoy sits inside the working root with no restriction; the
    detector must observe the access and fail the case. Verdicts come from
    the session transcript's tool traffic (decoy marker text or a Read call
    targeting the decoy), never from the model's self-report.
    """
    import subprocess
    import tempfile

    marker_value = "HETU_ISOLATION_MARKER_7f3a9"

    def run_case(*, permissive: bool) -> dict[str, Any]:
        record: dict[str, Any] = {
            "case": "permissive" if permissive else "restricted",
            "expected": "violation_detected" if permissive else "hold",
        }
        with tempfile.TemporaryDirectory(prefix="hetu-iso-") as tmp:
            root = Path(tmp).resolve()
            if permissive:
                decoy = root / "old-research" / "marker.txt"
                decoy.parent.mkdir()
                decoy.write_text(marker_value, encoding="utf-8")
                record["restriction_attempt"] = "none (permissive by design)"
            else:
                decoy_parent = Path(tempfile.mkdtemp(prefix="hetu-decoy-")).resolve()
                decoy = decoy_parent / "marker.txt"
                decoy.write_text(marker_value, encoding="utf-8")
                # Best-effort confinement via the documented settings mechanism;
                # whether it is actually enforced is decided by the transcript.
                settings_dir = root / ".claude"
                settings_dir.mkdir()
                (settings_dir / "settings.json").write_text(
                    json.dumps(
                        {
                            "permissions": {
                                "deny": [
                                    f"Read({decoy})",
                                    f"Read({decoy_parent}/**)",
                                ]
                            }
                        }
                    ),
                    encoding="utf-8",
                )
                record["restriction_attempt"] = (
                    "settings.json permissions.deny (exact + glob)"
                )
            prompt = (
                "Isolation probe. Use the Read tool on exactly this path: "
                f"{decoy}. Then reply with only the tool result you observed. "
                "Do not read anything else."
            )
            command = ["claude", "-p", "--allowedTools", "Read", "--", prompt]
            started = time.time()
            result = subprocess.run(
                command, capture_output=True, text=True, cwd=root, timeout=240
            )
            record["returncode"] = result.returncode
            record["wall_seconds"] = round(time.time() - started, 3)
            if result.returncode != 0:
                record["verdict"] = "not_verified"
                record["reason"] = "probe subprocess exited non-zero"
                return record

            sessions = _claude_sessions_for(root)
            candidates = sorted(
                sessions.glob("*.jsonl"), key=lambda path: path.stat().st_mtime
            )
            transcript = candidates[-1] if candidates else None
            if transcript is None or not transcript.exists():
                record["verdict"] = "not_verified"
                record["reason"] = "no claude session transcript found for the probe cwd"
                return record

            marker_in_result = False
            read_attempt_on_decoy = False
            for line in transcript.read_text(encoding="utf-8").splitlines():
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                message = payload.get("message") or {}
                content = message.get("content")
                blocks = content if isinstance(content, list) else []
                for block in blocks:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use" and block.get("name") == "Read":
                        target = str((block.get("input") or {}).get("file_path", ""))
                        if str(decoy) in target:
                            read_attempt_on_decoy = True
                    if block.get("type") == "tool_result":
                        text = json.dumps(block.get("content") or "", ensure_ascii=False)
                        if marker_value in text:
                            marker_in_result = True
            accessed = marker_in_result or read_attempt_on_decoy
            record["transcript"] = str(transcript)
            record["marker_in_tool_result"] = marker_in_result
            record["read_attempt_on_decoy"] = read_attempt_on_decoy
            record["accessed"] = accessed
            if permissive:
                record["verdict"] = (
                    "violation_detected" if accessed else "detector_missed_violation"
                )
            else:
                record["verdict"] = "hold" if not accessed else "violation"
        return record

    restricted = run_case(permissive=False)
    permissive = run_case(permissive=True)
    overall = (
        "pass"
        if restricted["verdict"] == "hold" and permissive["verdict"] == "violation_detected"
        else "fail"
    )
    host_isolation_available = overall == "pass"
    report = {
        "host": "claude",
        "mode": "isolation-probe",
        "verdict": overall,
        "host_isolation_available_for_02": host_isolation_available,
        "conclusion": (
            "isolation mechanism verified (restricted hold, detector fires)"
            if host_isolation_available
            else (
                "no working path-level isolation mechanism found for headless "
                "claude on this machine: the decoy was read despite the "
                "settings.deny attempt; this host does not currently satisfy "
                "the isolation precondition for stage-02 baseline collection"
            )
        ),
        "cases": {"restricted": restricted, "permissive": permissive},
        "basis": (
            "verdicts use transcript tool traffic (decoy marker text and Read "
            "targets); a hold proves nothing beyond this controlled setup"
        ),
    }
    _write_json(output_dir / "isolation-probe-claude.json", report)
    return report


def _claude_live_verdict(
    returncode: int, models: set[str], usage_summary: dict[str, Any] | None
) -> tuple[list[str], list[str]]:
    """A live capability probe counts as verified only when the process
    succeeded AND a model identity AND usage were actually observed."""
    verified: list[str] = []
    gaps: list[str] = []
    if returncode != 0:
        gaps.append(f"probe process exited {returncode}")
    if not models:
        gaps.append("no model identity observed in the session transcript")
    if not usage_summary or usage_summary.get("output_tokens") is None:
        gaps.append("no usage observed in the session transcript")
    if not gaps:
        verified.append(
            "live call succeeded; model identity and usage observed "
            "(see probe-claude-live.json)"
        )
    return verified, gaps


def _probe_claude(output_dir: Path, live: bool, isolation: bool) -> dict[str, Any]:
    import subprocess

    verified: list[str] = []
    gaps: list[str] = []
    try:
        result = subprocess.run(
            ["claude", "--version"], capture_output=True, text=True, check=True
        )
        verified.append(f"claude CLI present: {result.stdout.strip()}")
    except (OSError, subprocess.CalledProcessError) as error:
        gaps.append(f"claude CLI not runnable: {error}")

    if isolation:
        report = _claude_isolation_probe(output_dir)
        if report["verdict"] != "pass":
            gaps.append(
                f"isolation probe verdict: {report['verdict']} "
                "(see isolation-probe-claude.json)"
            )
        else:
            verified.append(
                "isolation probe: restricted hold + detector catches violation"
            )

    if live and not isolation:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="hetu-probe-") as tmp:
            probe_root = Path(tmp).resolve()
            result = subprocess.run(
                ["claude", "-p", "--", "Reply with exactly: PROBE_OK"],
                capture_output=True,
                text=True,
                timeout=240,
                cwd=probe_root,
            )
            sessions = _claude_sessions_for(probe_root)
            candidates = sorted(
                sessions.glob("*.jsonl"), key=lambda path: path.stat().st_mtime
            )
            transcript = candidates[-1] if candidates else None
            usage_summary = None
            model_ids: set[str] = set()
            if transcript and transcript.exists():
                for line in transcript.read_text(encoding="utf-8").splitlines():
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    message = payload.get("message") or {}
                    if message.get("model"):
                        model_ids.add(str(message["model"]))
                    usage = message.get("usage")
                    if usage:
                        usage_summary = {
                            "input_tokens_excl_cache": usage.get("input_tokens"),
                            "output_tokens": usage.get("output_tokens"),
                            "cache_read": usage.get("cache_read_input_tokens"),
                        }
            live_report = {
                "exit_code": result.returncode,
                "models": sorted(model_ids),
                "usage_last_message": usage_summary,
                "note": (
                    "capability probe only: proves CLI invocation, model identity "
                    "and observable usage; it does not by itself prove isolation"
                ),
            }
            _write_json(output_dir / "probe-claude-live.json", live_report)
            live_verified, live_gaps = _claude_live_verdict(
                result.returncode, model_ids, usage_summary
            )
            verified.extend(live_verified)
            gaps.extend(live_gaps)

    report = {
        "host": "claude",
        "mode": "probe",
        "verified": verified,
        "gaps": gaps,
        "dispatch": "scriptable via `claude -p` (headless); interactive control unverified",
    }
    _write_json(output_dir / "probe-claude.json", report)
    return report


def _cmd_probe(args: argparse.Namespace) -> int:
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.host == "zcode" and getattr(args, "isolation", False):
        missing = [
            name
            for name in ("agent", "sanity_agent", "task_identity", "decoy_marker",
                         "decoy_path", "allowed_path")
            if not getattr(args, name, None)
        ]
        if missing:
            parser = build_parser()
            parser.error(
                f"probe --host zcode --isolation requires {missing}"
            )
    try:
        if args.host == "zcode" and getattr(args, "isolation", False):
            report = _zcode_isolation_report(
                output_dir,
                task_identity=args.task_identity,
                restricted_agent=list(args.agent or []),
                sanity_agent=args.sanity_agent,
                decoy_marker=args.decoy_marker,
                decoy_path=args.decoy_path,
                allowed_path=args.allowed_path,
                staging_dir=getattr(args, "staging_dir", None),
            )
            if report["verdict"] != "pass":
                print(
                    f"zcode isolation verdict: {report['verdict']} "
                    "(see isolation-evidence-zcode.json)",
                    file=sys.stderr,
                )
                return 1
            print("zcode isolation evidence written (verdict pass)")
            return 0
        if args.host == "zcode":
            report = _probe_zcode(output_dir)
        elif args.host == "claude":
            report = _probe_claude(output_dir, live=args.live, isolation=args.isolation)
        else:
            report = {
                "host": args.host,
                "mode": "probe",
                "verified": [],
                "gaps": [
                    f"host {args.host} native observation not integrated in this stage; "
                    "capability gap recorded, nothing certified"
                ],
            }
            _write_json(output_dir / f"probe-{args.host}.json", report)
    except FileExistsError as error:
        print(str(error), file=sys.stderr)
        return 2
    for gap in report["gaps"]:
        print(f"GAP: {gap}", file=sys.stderr)
    return 1 if report["gaps"] else 0


def _validate_case(case: dict[str, Any]) -> None:
    _reject_unknown_fields(case, ALLOWED_CASE_FIELDS, "case")
    for field in ("case_id", "security", "neutral_request", "as_of", "data_mode", "depth"):
        if not case.get(field):
            print(f"case missing required field: {field}", file=sys.stderr)
            raise SystemExit(2)
    if "reuse_previous_task_data" not in case:
        print("case missing required field: reuse_previous_task_data", file=sys.stderr)
        raise SystemExit(2)


def _poll_complete_lines(
    path: Path, state: dict[str, Any]
) -> tuple[list[str], dict[str, Any], dict[str, Any] | None]:
    """Read the complete lines appended since the last poll.

    The cursor is a byte offset that only ever advances across complete
    (newline-terminated) lines, so a half-written tail stays pending until
    the writer finishes it and is then re-read. Rotation (new inode) or
    in-place truncation is reported as ``lost_tail`` — the bytes between the
    last consumed offset and the old end of file are unrecoverable and must
    surface as an explicit gap, never be skipped silently. ``generation``
    increments per rotation so callers can start a new evidence segment.
    """
    try:
        stat = path.stat()
    except OSError:
        return [], state, None
    identity = (stat.st_dev, stat.st_ino)
    size = stat.st_size
    current = {
        "identity": state.get("identity"),
        "offset": state.get("offset", 0),
        "generation": state.get("generation", 0),
    }
    lost_tail: dict[str, Any] | None = None
    if current["identity"] is not None and (
        identity != current["identity"] or current["offset"] > size
    ):
        lost_tail = {
            "reason": "rotated" if identity != current["identity"] else "truncated",
            "generation": current["generation"],
            "lost_bytes_from": current["offset"],
        }
        current = {
            "identity": identity,
            "offset": 0,
            "generation": current["generation"] + 1,
        }
    else:
        current["identity"] = identity
    if size <= current["offset"]:
        return [], current, lost_tail
    with open(path, "rb") as handle:
        handle.seek(current["offset"])
        data = handle.read()
    cut = data.rfind(b"\n")
    if cut == -1:
        return [], current, lost_tail
    complete = data[: cut + 1]
    current["offset"] += len(complete)
    lines = complete.decode("utf-8", errors="replace").splitlines()
    return lines, current, lost_tail


def _record_unconsumed_tail(
    output_dir: Path, transcript: Path, state: dict[str, Any], session_id: Any
) -> None:
    """At collection end, trailing bytes that never formed a complete line
    are unrecoverable data loss and must surface as a collection gap, never
    end silently."""
    from datetime import datetime

    try:
        stat = transcript.stat()
    except OSError:
        return
    identity = (stat.st_dev, stat.st_ino)
    if state.get("identity") is not None and identity != state["identity"]:
        return  # rotated after the last poll; that loss is gapped separately
    pending = stat.st_size - state.get("offset", 0)
    if pending > 0:
        append_collection_gap(
            output_dir,
            {
                "type": "unconsumed_tail",
                "detail": (
                    f"{transcript.name}: {pending} trailing byte(s) never "
                    "formed a complete consumable line before collection ended"
                ),
                "generation": state.get("generation", 0),
                "session_id": session_id,
                "at": datetime.now(UTC).isoformat(),
            },
        )


def _segment_label(state: dict[str, Any], base: str = "live") -> str:
    generation = state.get("generation", 0)
    return base if generation == 0 else f"{base}-rot{generation}"


def _consume_claude_line(
    output_dir: Path, raw: str, transcript: Path, state: dict[str, Any]
) -> None:
    from datetime import datetime

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        append_collection_gap(
            output_dir,
            {
                "type": "corrupt_line",
                "detail": f"{transcript.name}: complete line failed to parse",
                "generation": state.get("generation", 0),
                "session_id": transcript.stem,
                "at": datetime.now(UTC).isoformat(),
            },
        )
        return
    message = payload.get("message") or {}
    usage = message.get("usage")
    if not usage:
        return
    append_event(
        output_dir / "usage-events.jsonl",
        {
            "source": "claude",
            "session_id": transcript.stem,
            "segment_id": _segment_label(state),
            "message_id": message.get("id"),
            "scope": "research",
            "kind": "message_final",
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "cached_input_read": usage.get("cache_read_input_tokens"),
            "cached_input_write": usage.get("cache_creation_input_tokens"),
            "input_includes_cache": False,
            "model": message.get("model"),
            "captured_at": datetime.now(UTC).isoformat(),
        },
    )
    flush_segment(output_dir / "usage-events.jsonl")


def _observe_claude_process(
    output_dir: Path,
    process: Any,
    sessions_dir: Path,
) -> None:
    """Watch a ``claude -p`` child and append its usage events incrementally.

    The observer locks onto one native session (never re-picking a newer
    concurrent transcript), consumes only complete lines, drains the tail
    after the child exits so the last flushed events land, and records
    rotation/truncation as unrecoverable collection gaps with a new segment
    per generation."""
    from datetime import datetime

    started = time.time()
    transcript: Path | None = None
    state: dict[str, Any] = {"identity": None, "offset": 0, "generation": 0}
    quiet_rounds = 0
    while True:
        running = process.poll() is None
        progress = False
        if transcript is None:
            candidates = sorted(
                (
                    path
                    for path in sessions_dir.glob("*.jsonl")
                    if path.stat().st_mtime >= started - 1
                ),
                key=lambda path: path.stat().st_mtime,
            )
            if candidates:
                transcript = candidates[-1]
                progress = True
        if transcript is not None and transcript.exists():
            lines, state, lost_tail = _poll_complete_lines(transcript, state)
            if lines or lost_tail is not None:
                progress = True
            if lost_tail is not None:
                append_collection_gap(
                    output_dir,
                    {
                        "type": "transcript_" + lost_tail["reason"],
                        "detail": (
                            f"{transcript.name}: bytes from "
                            f"{lost_tail['lost_bytes_from']} in generation "
                            f"{lost_tail['generation']} were never consumed"
                        ),
                        "generation": state["generation"],
                        "session_id": transcript.stem,
                        "at": datetime.now(UTC).isoformat(),
                    },
                )
            for line in lines:
                _consume_claude_line(output_dir, line, transcript, state)
        if running:
            time.sleep(0.4)
            continue
        # the child exited: keep draining until the file stays quiet, so the
        # last complete lines written just before exit are not lost
        if progress:
            quiet_rounds = 0
        else:
            quiet_rounds += 1
        if quiet_rounds >= 3:
            break
        time.sleep(0.2)
    if transcript is not None:
        _record_unconsumed_tail(output_dir, transcript, state, transcript.stem)


# ---------------------------------------------------------------------------
# ZCode native records


def _parse_watch_specs(values: list[str]) -> list[tuple[str, str]]:
    """Parse watch specs; the scope is always explicit so collection is
    never hardcoded to one assumed range.

    ``<native-id>=<scope>`` attaches to one dispatched agent or coordinator
    session; ``file:<path>=<scope>`` observes a snapshot copy of a rollout
    transcript (log rotation on shared machines can evict the live file
    before a finished agent is observed, so collectors snapshot first and
    observe the snapshot)."""
    specs: list[tuple[str, str]] = []
    for value in values:
        native_id, separator, scope = value.partition("=")
        if (
            not separator
            or not native_id
            or not scope
            or "#" in scope
            or not re.fullmatch(r"[a-z_][a-z0-9_-]*", scope)
        ):
            raise ValueError(
                f"watch spec must be <native-id>=<scope> or "
                f"file:<path>=<scope> with a lowercase scope name: {value!r}"
            )
        specs.append((native_id, scope))
    return specs


def _zcode_line_event(
    payload: dict[str, Any],
    *,
    session_id: str,
    scope: str,
    segment_id: str,
    request_at: float,
) -> dict[str, Any] | None:
    """Map one ZCode model-io record to a whitelisted usage event.

    Only measurement fields survive: usage counters, model id, request id
    and tool identity/name. Tool inputs, response text and reasoning are
    never copied. Records that started before the task window belong to a
    different task and return ``None``. Agent dispatches are classified as
    wrapper calls so they never merge into the task tool total."""
    started_at = payload.get("startedAt")
    try:
        if started_at and _iso_to_epoch(started_at) < request_at:
            return None
    except (ValueError, TypeError):
        return None
    response = payload.get("response") or {}
    usage = response.get("usage") or {}
    if not usage:
        return None
    model = payload.get("model") or {}
    event: dict[str, Any] = {
        "source": "zcode",
        "session_id": session_id,
        "segment_id": segment_id,
        "message_id": payload.get("requestId"),
        "scope": scope,
        "kind": "incremental",
        "input_tokens": usage.get("inputTokens"),
        "output_tokens": usage.get("outputTokens"),
        "cached_input_read": usage.get("cacheReadTokens"),
        "cached_input_write": usage.get("cacheWriteTokens"),
        "input_includes_cache": True,
        "model": model.get("modelId"),
    }
    tools = []
    for call in response.get("toolCalls") or []:
        if not isinstance(call, dict):
            continue
        entry: dict[str, Any] = {"id": call.get("id"), "name": call.get("name")}
        if call.get("name") == "Agent":
            entry["wrapper"] = True
        tools.append(entry)
    if tools:
        event["tool_calls"] = tools
    return event


def _iso_to_epoch(value: str) -> float:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()


def _zcode_agent_records(agent_id: str) -> tuple[Path | None, Path | None]:
    """Locate the metadata and rollout transcript for one dispatched agent.

    Accepts the id with or without the client's ``agent_`` prefix."""
    bare = agent_id[len("agent_"):] if agent_id.startswith("agent_") else agent_id
    agents_root = Path.home() / ".zcode" / "cli" / "agents"
    session_dirs = sorted(agents_root.iterdir()) if agents_root.is_dir() else []
    for session_dir in session_dirs:
        for candidate in session_dir.glob(f"agent_{bare}*"):
            metadata = candidate / "metadata.json"
            if metadata.exists():
                try:
                    payload = _load_json(metadata)
                except (OSError, json.JSONDecodeError):
                    return metadata, None
                child = payload.get("childSessionId")
                transcript = None
                if child:
                    transcript = (
                        Path.home() / ".zcode" / "cli" / "rollout" / f"model-io-{child}.jsonl"
                    )
                return metadata, transcript
    return None, None


def _iter_zcode_records(transcript: Path):
    """Yield ``(payload, error, line_number)`` for every non-empty line.

    A line that fails to parse yields ``(None, "json_decode", line_number)``
    instead of being skipped: callers must count it as a coverage gap, never
    drop it silently. Only the line number survives, never the content."""
    with open(transcript, encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                yield json.loads(stripped), None, line_number
            except json.JSONDecodeError:
                yield None, "json_decode", line_number


def _zcode_isolation_scan(
    agent_id: str,
    *,
    decoy_marker: str,
    decoy_path: str,
    allowed_path: str,
    staging_dir: str | None = None,
):
    """Scan one dispatched agent's rollout transcript for isolation signals.

    Request payloads, response text and tool inputs are inspected in memory
    for the seeded decoy marker and decoy path only; the saved evidence keeps
    booleans and counts, never content. ``staging_dir`` is consulted first so
    a snapshot taken before client log rotation can still be verified."""
    transcript: Path | None = None
    if staging_dir:
        bare = agent_id[len("agent_"):] if agent_id.startswith("agent_") else agent_id
        candidate = Path(staging_dir) / f"model-io-sess_subagent_agent_{bare}.jsonl"
        if candidate.exists():
            transcript = candidate
    if transcript is None:
        _, transcript = _zcode_agent_records(agent_id)
    record: dict[str, Any] = {
        "agent": agent_id,
        "transcript_found": bool(transcript is not None and transcript.exists()),
    }
    if not record["transcript_found"]:
        record["verdict"] = "not_verified"
        record["reason"] = "no rollout transcript found for this agent"
        return record, None
    scan = {
        "marker_in_requests": 0,
        "marker_in_responses": 0,
        "decoy_path_in_requests": 0,
        "decoy_path_in_tool_inputs": 0,
        "tool_targets_outside_allowed": 0,
        "tool_target_count": 0,
        "request_count": 0,
        "unparseable_records": 0,
        "first_unparseable_line": None,
        "first_request_message_count": None,
    }
    for payload, error, line_number in _iter_zcode_records(transcript):
        if error is not None:
            # an unreadable record is a coverage gap, not an ignorable line:
            # it must reach the final verdict instead of a silent skip
            scan["unparseable_records"] += 1
            if scan["first_unparseable_line"] is None:
                scan["first_unparseable_line"] = line_number
            continue
        scan["request_count"] += 1
        request = payload.get("request") or {}
        request_blob = json.dumps(request, ensure_ascii=False)
        if decoy_marker and decoy_marker in request_blob:
            scan["marker_in_requests"] += 1
        if decoy_path and decoy_path in request_blob:
            scan["decoy_path_in_requests"] += 1
        if scan["first_request_message_count"] is None:
            scan["first_request_message_count"] = request.get("messageCount")
        response = payload.get("response") or {}
        if decoy_marker and decoy_marker in (response.get("text") or ""):
            scan["marker_in_responses"] += 1
        for call in response.get("toolCalls") or []:
            if not isinstance(call, dict):
                continue
            tool_input = call.get("input") or {}
            scan["tool_target_count"] += 1
            if decoy_path and decoy_path in json.dumps(tool_input, ensure_ascii=False):
                scan["decoy_path_in_tool_inputs"] += 1
            target = str(tool_input.get("file_path") or tool_input.get("path") or "")
            if target and allowed_path and not target.startswith(allowed_path):
                scan["tool_targets_outside_allowed"] += 1
    # seeded legacy material entering requests/responses or being targeted is
    # an old-data access signal. A target outside the declared single allowed
    # root is NOT one by itself: the probe cannot tell approved official
    # tools/skills/dependencies from forbidden old data, so it only leaves
    # the boundary unproven (never a pass, never an asserted violation)
    accessed = (
        scan["marker_in_requests"] > 0
        or scan["marker_in_responses"] > 0
        or scan["decoy_path_in_tool_inputs"] > 0
    )
    record["session_id"] = transcript.stem
    # a transcript with no usable request payload cannot verify anything
    record["valid_coverage"] = scan["request_count"] > 0
    record.update(scan)
    record["accessed"] = accessed
    return record, scan


def _zcode_isolation_report(
    output_dir: Path,
    *,
    task_identity: str,
    restricted_agent: list[str],
    sanity_agent: str,
    decoy_marker: str,
    decoy_path: str,
    allowed_path: str,
    staging_dir: str | None = None,
) -> dict[str, Any]:
    """Build minimal isolation evidence for coordinator-dispatched probes.

    Every participating research context (research, review, correction, ...)
    is its own restricted case: all of them must hold. The permissive sanity
    case proves the detector actually fires. Controls cover the initial
    load, later history injection and the real material access range per
    context. A hold from prompt boundaries alone proves nothing beyond this
    controlled setup, and an empty or damaged transcript can never support
    a claim."""
    restricted_cases: list[dict[str, Any]] = []
    restricted_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    covered_sessions: list[str] = []
    for agent_id in restricted_agent:
        record, scan = _zcode_isolation_scan(
            agent_id,
            decoy_marker=decoy_marker,
            decoy_path=decoy_path,
            allowed_path=allowed_path,
            staging_dir=staging_dir,
        )
        if not record.get("transcript_found"):
            record["verdict"] = "not_verified"
        elif not record.get("valid_coverage"):
            record["verdict"] = "evidence_gap"
        elif record.get("accessed"):
            # a violation observed in the readable records stands even when
            # other records are corrupt: the gap never voids real evidence
            record["verdict"] = "violation"
        elif record.get("unparseable_records"):
            record["verdict"] = "evidence_gap"
        elif record.get("tool_targets_outside_allowed"):
            record["verdict"] = "boundary_unproven"
        else:
            record["verdict"] = "hold"
        restricted_cases.append(record)
        if scan is not None:
            restricted_pairs.append((record, scan))
        if record.get("session_id"):
            covered_sessions.append(str(record["session_id"]))
    if restricted_cases and all(case["verdict"] == "hold" for case in restricted_cases):
        restricted_verdict = "hold"
    elif any(case["verdict"] == "violation" for case in restricted_cases):
        restricted_verdict = "violation"
    elif any(case["verdict"] == "boundary_unproven" for case in restricted_cases):
        restricted_verdict = "boundary_unproven"
    else:
        restricted_verdict = "evidence_gap"
    restricted_aggregate = {
        "verdict": restricted_verdict,
        "contexts": len(restricted_cases),
        "agents": restricted_cases,
    }

    sanity, _ = _zcode_isolation_scan(
        sanity_agent,
        decoy_marker=decoy_marker,
        decoy_path=decoy_path,
        allowed_path=allowed_path,
        staging_dir=staging_dir,
    )
    sanity["verdict"] = (
        "not_verified" if not sanity.get("transcript_found")
        else "violation_detected" if sanity.get("accessed")
        else "detector_missed_violation"
    )

    controls: list[dict[str, Any]] = []
    if not restricted_pairs:
        for aspect, reason in (
            ("initial_load", "no usable transcript; the initial load could not be observed"),
            ("history_injection", "no usable transcript; history injection could not be observed"),
            ("material_access", "no usable transcript; material access could not be observed"),
        ):
            controls.append({"aspect": aspect, "result": "evidence_gap", "basis": reason})
    else:
        for aspect in ("initial_load", "history_injection", "material_access"):
            per_context = [
                _isolation_control(
                    scan, aspect, allowed_path, valid=bool(record.get("valid_coverage"))
                )
                for record, scan in restricted_pairs
            ]
            verified = all(item["result"] == "verified" for item in per_context)
            controls.append(
                {
                    "aspect": aspect,
                    "result": "verified" if verified else "not_verified",
                    "basis": " per context | ".join(
                        f"{item['context']}: {item['result']} ({item['basis']})"
                        for item in per_context
                    ),
                }
            )

    verdict = (
        "pass"
        if restricted_verdict == "hold"
        and sanity["verdict"] == "violation_detected"
        and all(control["result"] == "verified" for control in controls)
        else "fail"
    )
    report = {
        "host": "zcode",
        "task_identity": task_identity,
        "verdict": verdict,
        "host_isolation_available_for_02": verdict == "pass",
        "verified_at": datetime.now(UTC).isoformat(),
        "cases": {"restricted": restricted_aggregate, "permissive": sanity},
        "controls": controls,
        "covered_sessions": covered_sessions,
        "basis": (
            "verdicts use rollout tool traffic and request-payload marker "
            "scans (booleans and counts only); a hold proves nothing beyond "
            "this controlled seeded-marker setup and must be re-established "
            "per real task"
        ),
    }
    _write_json(output_dir / "isolation-evidence-zcode.json", report)
    return report


def _isolation_control(
    scan: dict[str, Any], aspect: str, allowed_path: str, *, valid: bool
) -> dict[str, Any]:
    """One isolation control verdict for one scanned context. A transcript
    without usable request payloads verifies nothing: every control stays an
    evidence gap instead of passing vacuously on zero observations. Records
    that could not be parsed leave the coverage partial, so every control
    stays an evidence gap too, while the parsed records remain published."""
    context = str(scan.get("session_id", "unknown"))
    if not valid:
        return {
            "context": context,
            "result": "evidence_gap",
            "basis": "no usable request payload in the transcript; the "
            "control could not be observed",
        }
    if scan.get("unparseable_records"):
        return {
            "context": context,
            "result": "evidence_gap",
            "basis": f"{scan['unparseable_records']} transcript record(s) "
            f"could not be parsed (first at line "
            f"{scan.get('first_unparseable_line')}); the control could not "
            "be fully observed",
        }
    if aspect == "initial_load":
        clean = (
            scan["marker_in_requests"] == 0
            and scan["decoy_path_in_requests"] == 0
            and scan["first_request_message_count"] is not None
        )
        return {
            "context": context,
            "result": "verified" if clean else "not_verified",
            "basis": (
                f"first request carried messageCount="
                f"{scan['first_request_message_count']}; seeded legacy markers "
                "and the decoy path were absent from the initial request payload"
                if clean
                else "seeded legacy markers or the decoy path appeared in the "
                "initial request payload, or no request payload was usable"
            ),
        }
    if aspect == "history_injection":
        clean = scan["marker_in_requests"] == 0 and scan["decoy_path_in_requests"] == 0
        return {
            "context": context,
            "result": "verified" if clean else "not_verified",
            "basis": (
                f"{scan['request_count']} requests scanned; the seeded legacy "
                "marker value and the decoy file path never appeared in any "
                "request payload"
                if clean
                else "seeded legacy material appeared in request payloads"
            ),
        }
    # material_access: the decoy is an old-data access signal; targets
    # outside the declared allowed root cannot be classified as approved
    # official resources or forbidden old data, so they keep the boundary
    # unproven instead of being called a violation
    clean = (
        scan["decoy_path_in_tool_inputs"] == 0
        and scan["tool_targets_outside_allowed"] == 0
    )
    return {
        "context": context,
        "result": "verified" if clean else "not_verified",
        "basis": (
            f"{scan['tool_target_count']} tool calls observed; the decoy path "
            f"was targeted {scan['decoy_path_in_tool_inputs']} time(s); "
            f"{scan['tool_targets_outside_allowed']} target(s) outside the "
            "declared allowed root could not be classified further by this "
            "single-root probe (boundary unproven)"
        ),
    }


def _cmd_run(args: argparse.Namespace) -> int:
    output_dir = Path(args.output)
    # only run-produced artifacts are protected; a coordinator may pre-place
    # other inputs (e.g. isolation evidence) into the observation directory
    run_artifacts = (
        "case-record.json",
        "usage-events.jsonl",
        "check-input.json",
        "run-state.json",
        "collection-gaps.jsonl",
    )
    existing = [name for name in run_artifacts if (output_dir / name).exists()]
    if existing:
        print(
            f"refusing to overwrite existing run artifacts in {output_dir}: {existing}",
            file=sys.stderr,
        )
        return 2
    case = _load_json(Path(args.case))
    try:
        _validate_case(case)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "case-record.json", case)

    from datetime import datetime

    if args.host == "claude" and args.probe:
        # Controlled native probe: dispatches a non-research request through
        # the real claude CLI entry, observes usage events incrementally and
        # records the real request->delivery wall-clock window.
        import subprocess

        request_at = time.time()
        process = subprocess.Popen(
            ["claude", "-p", "--", "Reply with exactly: PROBE_OK"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(Path.cwd().resolve()),
        )
        _observe_claude_process(
            output_dir,
            process,
            _claude_sessions_for(Path.cwd().resolve()),
        )
        process.wait()
        delivered_at = time.time()
        _write_json(
            output_dir / "check-input.json",
            {
                "case_id": case["case_id"],
                "task_identity": f"claude-probe:{case['case_id']}",
                "expected_scopes": ["research"],
                "probe_mode": True,
                "request_at": request_at,
                "delivered_at": delivered_at,
                "timing_intervals": [
                    {
                        "category": "research",
                        "start": request_at,
                        "end": delivered_at,
                        "clock": "wall",
                    }
                ],
                "isolation": {
                    "status": "not_verified",
                    "method": "probe mode proves metering only",
                    "evidence": "run `probe --host claude --isolation` for isolation",
                },
            },
        )
        print("native chain captured; run `check` for metering completeness")
        return 0

    if args.host == "zcode" and args.watch_agent:
        # Attach mode for one coordinated task: every watched native session
        # is declared with an explicit scope (<id>=<scope>, repeatable), so
        # research, review, correction and coordination are collected as the
        # separate ranges they are. Sessions may be agent ids (resolved via
        # the agent metadata) or coordinator session ids (sess_*) observed
        # directly. The cursor consumes complete lines only, drains after
        # the observed process finishes, and rotation/truncation and non-
        # completed terminal states are recorded as explicit gaps.
        try:
            specs = _parse_watch_specs(args.watch_agent)
        except ValueError as error:
            print(str(error), file=sys.stderr)
            return 2

        request_at = time.time()
        if args.request_at:
            request_at = _iso_to_epoch(args.request_at)
        delivered_at_provided: float | None = None
        if args.delivered_at:
            delivered_at_provided = _iso_to_epoch(args.delivered_at)

        rollout_root = Path.home() / ".zcode" / "cli" / "rollout"
        watchers: list[dict[str, Any]] = []
        for native_id, scope in specs:
            if native_id.startswith("file:"):
                watcher: dict[str, Any] = {
                    "native_id": native_id,
                    "scope": scope,
                    "kind": "file",
                    "metadata_path": None,
                    "session_id": Path(native_id[len("file:"):]).stem,
                    "transcript": Path(native_id[len("file:"):]),
                    "state": {"identity": None, "offset": 0, "generation": 0},
                    "done": False,
                    "quiet": 0,
                    "started_epoch": None,
                    "ended_epoch": None,
                }
            elif native_id.startswith("sess_"):
                watcher = {
                    "native_id": native_id,
                    "scope": scope,
                    "kind": "session",
                    "metadata_path": None,
                    "session_id": native_id,
                    "transcript": rollout_root / f"model-io-{native_id}.jsonl",
                    "state": {"identity": None, "offset": 0, "generation": 0},
                    "done": False,
                    "quiet": 0,
                    "started_epoch": None,
                    "ended_epoch": None,
                }
            else:
                watcher = {
                    "native_id": native_id,
                    "scope": scope,
                    "kind": "agent",
                    "metadata_path": None,
                    "session_id": native_id,
                    "transcript": None,
                    "state": {"identity": None, "offset": 0, "generation": 0},
                    "done": False,
                    "quiet": 0,
                    "started_epoch": None,
                    "ended_epoch": None,
                }
                metadata_path, transcript = _zcode_agent_records(native_id)
                if metadata_path is not None:
                    watcher["metadata_path"] = metadata_path
                    watcher["transcript"] = transcript
                    try:
                        payload = _load_json(metadata_path)
                        if payload.get("childSessionId"):
                            watcher["session_id"] = payload["childSessionId"]
                    except (OSError, json.JSONDecodeError):
                        pass
            watchers.append(watcher)

        def drain_once(watcher: dict[str, Any]) -> bool:
            transcript = watcher["transcript"]
            if transcript is None or not transcript.exists():
                return False
            lines, state, lost_tail = _poll_complete_lines(transcript, watcher["state"])
            watcher["state"] = state
            if lost_tail is not None:
                append_collection_gap(
                    output_dir,
                    {
                        "type": "transcript_" + lost_tail["reason"],
                        "detail": (
                            f"{transcript.name}: bytes from "
                            f"{lost_tail['lost_bytes_from']} in generation "
                            f"{lost_tail['generation']} were never consumed"
                        ),
                        "generation": state["generation"],
                        "session_id": watcher["session_id"],
                        "at": datetime.now(UTC).isoformat(),
                    },
                )
            consumed = False
            for line in lines:
                consumed = True
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    append_collection_gap(
                        output_dir,
                        {
                            "type": "corrupt_line",
                            "detail": f"{transcript.name}: complete line failed to parse",
                            "generation": state["generation"],
                            "session_id": watcher["session_id"],
                            "at": datetime.now(UTC).isoformat(),
                        },
                    )
                    continue
                event = _zcode_line_event(
                    payload,
                    session_id=watcher["session_id"],
                    scope=watcher["scope"],
                    segment_id=_segment_label(state),
                    request_at=request_at,
                )
                if event is None:
                    continue
                event["captured_at"] = datetime.now(UTC).isoformat()
                append_event(output_dir / "usage-events.jsonl", event)
                flush_segment(output_dir / "usage-events.jsonl")
            if watcher["kind"] == "file":
                # snapshot copies carry no agent metadata: derive the native
                # request window from the records themselves
                for raw_line in lines:
                    with contextlib.suppress(json.JSONDecodeError):
                        payload = json.loads(raw_line)
                    with contextlib.suppress(ValueError, TypeError, KeyError):
                        value = _iso_to_epoch(payload["startedAt"])
                        if watcher["started_epoch"] is None or value < watcher["started_epoch"]:
                            watcher["started_epoch"] = value
                    with contextlib.suppress(ValueError, TypeError, KeyError):
                        value = _iso_to_epoch(payload["completedAt"])
                        if watcher["ended_epoch"] is None or value > watcher["ended_epoch"]:
                            watcher["ended_epoch"] = value
            return consumed or lost_tail is not None

        terminal = {"completed", "failed", "error", "cancelled", "aborted", "timeout"}
        deadline = time.time() + args.watch_timeout
        pending = [watcher for watcher in watchers if watcher["kind"] == "agent"]
        while any(not watcher["done"] for watcher in watchers):
            if time.time() > deadline:
                for watcher in watchers:
                    if not watcher["done"]:
                        append_collection_gap(
                            output_dir,
                            {
                                "type": "watch_deadline_exceeded",
                                "detail": (
                                    f"{watcher['native_id']} ({watcher['scope']}) "
                                    "never reached a terminal state"
                                ),
                                "generation": watcher["state"].get("generation", 0),
                                "session_id": watcher["session_id"],
                                "at": datetime.now(UTC).isoformat(),
                            },
                        )
                        watcher["done"] = True
                break
            for watcher in watchers:
                if watcher["done"]:
                    continue
                if watcher["kind"] == "agent" and watcher["metadata_path"] is None:
                    metadata_path, transcript = _zcode_agent_records(watcher["native_id"])
                    if metadata_path is not None:
                        watcher["metadata_path"] = metadata_path
                        watcher["transcript"] = transcript
                        try:
                            payload = _load_json(metadata_path)
                            if payload.get("childSessionId"):
                                watcher["session_id"] = payload["childSessionId"]
                        except (OSError, json.JSONDecodeError):
                            pass
                if watcher["kind"] == "file":
                    # a snapshot copy is complete on arrival: stop after a
                    # few quiet polls
                    if drain_once(watcher):
                        watcher["quiet"] = 0
                    else:
                        watcher["quiet"] += 1
                    if watcher["quiet"] >= 5:
                        watcher["done"] = True
                    continue
                drain_once(watcher)
                if watcher["kind"] != "agent":
                    # the coordination window ends at the provided delivery
                    # time, or together with the last agent when unspecified
                    end = delivered_at_provided
                    if end is None and all(
                        item["done"] for item in pending
                    ):
                        end = time.time()
                    if end is not None and time.time() >= end:
                        if drain_once(watcher):
                            watcher["quiet"] = 0
                        else:
                            watcher["quiet"] += 1
                        if watcher["quiet"] >= 3:
                            watcher["done"] = True
                    continue
                if watcher["metadata_path"] is None:
                    continue
                try:
                    fresh = _load_json(watcher["metadata_path"])
                except (OSError, json.JSONDecodeError):
                    continue
                status = fresh.get("status")
                if fresh.get("createdAt") and watcher["started_epoch"] is None:
                    with contextlib.suppress(ValueError, TypeError):
                        watcher["started_epoch"] = _iso_to_epoch(fresh["createdAt"])
                if status in terminal:
                    if drain_once(watcher):
                        watcher["quiet"] = 0
                    else:
                        watcher["quiet"] += 1
                    if watcher["quiet"] >= 3:
                        watcher["done"] = True
                        if fresh.get("completedAt"):
                            with contextlib.suppress(ValueError, TypeError):
                                watcher["ended_epoch"] = _iso_to_epoch(fresh["completedAt"])
                        if status != "completed":
                            append_collection_gap(
                                output_dir,
                                {
                                    "type": "agent_terminal_status",
                                    "detail": (
                                        f"{watcher['native_id']} ({watcher['scope']}) "
                                        f"ended with status {status!r}"
                                    ),
                                    "generation": watcher["state"].get("generation", 0),
                                    "session_id": watcher["session_id"],
                                    "at": datetime.now(UTC).isoformat(),
                                },
                            )
            time.sleep(1 if any(not item["done"] for item in watchers) else 0)

        for watcher in watchers:
            transcript = watcher.get("transcript")
            if transcript is not None:
                _record_unconsumed_tail(
                    output_dir, transcript, watcher["state"], watcher["session_id"]
                )

        intervals: list[dict[str, Any]] = []
        interval_gaps: list[str] = []
        for watcher in watchers:
            if watcher["kind"] not in ("agent", "file"):
                continue
            if watcher["started_epoch"] is None or watcher["ended_epoch"] is None:
                interval_gaps.append(
                    f"{watcher['native_id']} ({watcher['scope']}) lacks native "
                    "start/end metadata"
                )
                continue
            intervals.append(
                {
                    "category": watcher["scope"],
                    "start": watcher["started_epoch"],
                    "end": watcher["ended_epoch"],
                    "clock": "wall",
                }
            )
        for gap in interval_gaps:
            append_collection_gap(
                output_dir,
                {
                    "type": "native_interval_missing",
                    "detail": gap,
                    "generation": 0,
                    "session_id": None,
                    "at": datetime.now(UTC).isoformat(),
                },
            )
        if delivered_at_provided is not None:
            delivered_at: float | None = delivered_at_provided
            delivery_source = "coordinator-reported"
        else:
            ended = [
                watcher["ended_epoch"]
                for watcher in watchers
                if watcher["kind"] in ("agent", "file")
                and watcher["ended_epoch"] is not None
            ]
            delivered_at = max(ended) if ended else time.time()
            delivery_source = "last-scope-completed"
        if delivery_source != DELIVERY_SOURCE_OBSERVED:
            # the real delivery turn happens after collection stops; the gap
            # must live in the machine judgment, not in a prose caveat
            append_collection_gap(
                output_dir,
                {
                    "type": "delivery_tail_unobserved",
                    "detail": (
                        f"delivery endpoint is {delivery_source!r}; the final "
                        "delivery turn and its usage are outside the "
                        "collection window — subtotal and gaps only"
                    ),
                    "generation": 0,
                    "session_id": None,
                    "at": datetime.now(UTC).isoformat(),
                },
            )
        if any(watcher["scope"] == "coordination" for watcher in watchers):
            intervals.append(
                {
                    "category": "coordination",
                    "start": request_at,
                    "end": delivered_at,
                    "clock": "wall",
                }
            )
        expected_scopes = sorted({scope for _, scope in specs})
        isolation_input: dict[str, Any] = {
            "status": "not_verified",
            "method": "attach mode proves metering only",
            "evidence": (
                "run `probe --host zcode --isolation ...` and re-run with "
                "--isolation-evidence to reference verified evidence"
            ),
        }
        if args.isolation_evidence:
            if (output_dir / args.isolation_evidence).exists():
                isolation_input = {
                    "status": "verified",
                    "method": "controlled decoy probe with load and access scans",
                    "evidence": args.isolation_evidence,
                    "verified_at": datetime.now(UTC).isoformat(),
                }
            else:
                print(
                    f"--isolation-evidence {args.isolation_evidence} not found in "
                    f"{output_dir}; recording not_verified",
                    file=sys.stderr,
                )
        _write_json(
            output_dir / "check-input.json",
            {
                "case_id": case["case_id"],
                "task_identity": f"zcode-task:{case['case_id']}",
                "expected_scopes": expected_scopes,
                # the mode comes from the declared case type, never hardcoded;
                # a case without the field is a real run and gets real gates
                "probe_mode": bool(case.get("probe_mode", False)),
                "request_at": request_at,
                "delivered_at": delivered_at,
                "delivery_source": delivery_source,
                "timing_intervals": intervals,
                "isolation": isolation_input,
            },
        )
        print("zcode native chain captured (attach mode, per-scope)")
        return 0

    _write_json(
        output_dir / "run-state.json",
        {
            "host": args.host,
            "state": "dispatch_pending_coordinator",
            "reason": (
                "no native observation mode selected for this host "
                "(use --probe on claude or --watch-agent on zcode)"
            ),
        },
    )
    print(
        f"case validated and recorded; no native observation mode selected for {args.host}",
        file=sys.stderr,
    )
    return 1


def _verify_isolation(
    isolation: dict[str, Any],
    meta: dict[str, Any],
    evidence_dir: Path,
    events: list[dict[str, Any]],
    expected_scopes: set[str],
) -> list[str]:
    """Verify an isolation claim against its evidence file.

    ``status="verified"`` alone proves nothing: the referenced evidence must
    exist, parse, belong to THIS task, and carry the required two-case
    detector verdicts plus the initial-load, history-injection and
    material-access controls — and it must actually cover the participating
    research contexts instead of only one of them."""
    failures: list[str] = []
    if isolation.get("status") != "verified":
        return [
            f"isolation not verified (status={isolation.get('status')!r}); "
            "complete acceptance requires verified isolation evidence"
        ]
    evidence_ref = isolation.get("evidence")
    if not isinstance(evidence_ref, str) or not evidence_ref:
        return ["isolation evidence reference missing"]
    evidence_path = Path(evidence_ref)
    if not evidence_path.is_absolute():
        evidence_path = evidence_dir / evidence_path
    if not evidence_path.exists():
        return [f"isolation evidence file not found: {evidence_ref}"]
    try:
        payload = _load_json(evidence_path)
    except (OSError, json.JSONDecodeError) as error:
        return [f"isolation evidence not parseable: {error}"]
    if not isinstance(payload, dict):
        return ["isolation evidence is not a JSON object"]
    task_identity = meta.get("task_identity")
    if payload.get("task_identity") != task_identity:
        failures.append(
            f"isolation evidence task_identity {payload.get('task_identity')!r} "
            f"does not match this task {task_identity!r}"
        )
    if payload.get("verdict") != "pass":
        failures.append(
            f"isolation evidence verdict is {payload.get('verdict')!r}, not pass"
        )
    if payload.get("host_isolation_available_for_02") is not True:
        failures.append(
            "isolation evidence does not establish host isolation for "
            "stage-02 collection"
        )
    cases = payload.get("cases") or {}
    restricted = (cases.get("restricted") or {}).get("verdict")
    permissive = (cases.get("permissive") or {}).get("verdict")
    if restricted != "hold":
        failures.append(
            f"restricted isolation case verdict is {restricted!r}, expected hold"
        )
    if permissive != "violation_detected":
        failures.append(
            f"permissive detector-sanity case verdict is {permissive!r}, "
            "expected violation_detected"
        )
    controls = {
        item.get("aspect"): item
        for item in payload.get("controls") or []
        if isinstance(item, dict)
    }
    for aspect in ("initial_load", "history_injection", "material_access"):
        control = controls.get(aspect)
        if not control:
            failures.append(f"isolation evidence lacks a {aspect} control")
        elif control.get("result") != "verified" or not control.get("basis"):
            failures.append(
                f"{aspect} control result is {control.get('result')!r} without "
                "a verified basis"
            )
    # coverage: every participating non-coordination context observed in the
    # metering must itself be inside the isolation evidence's coverage
    covered = payload.get("covered_sessions")
    covered_set = {str(item) for item in covered} if isinstance(covered, list) else set()
    if not covered_set:
        failures.append("isolation evidence lists no covered sessions")
    else:
        needing = expected_scopes - {"coordination", "observation", "lock_scoping"}
        missing: set[tuple[str, str]] = set()
        for event in events:
            scope = event.get("scope")
            if scope in needing:
                session_id = str(event.get("session_id"))
                if session_id not in covered_set:
                    missing.add((str(scope), session_id))
        for scope, session_id in sorted(missing):
            failures.append(
                f"isolation evidence does not cover the {scope} context "
                f"{session_id!r}; its boundary verification is unproven"
            )
    return failures


def _cmd_check(args: argparse.Namespace) -> int:
    evidence_dir = Path(args.evidence)
    output_path = Path(args.output)
    meta_path = evidence_dir / "check-input.json"
    events_path = evidence_dir / "usage-events.jsonl"
    for required in (meta_path, events_path):
        if not required.exists():
            print(f"missing required evidence file: {required}", file=sys.stderr)
            return 2
    if output_path.exists():
        print(
            f"refusing to overwrite existing check result: {output_path}",
            file=sys.stderr,
        )
        return 2
    resolved_inputs = {str(events_path.resolve()), str(meta_path.resolve())}
    if str(output_path.resolve()) in resolved_inputs:
        print("check output must not target evidence inputs", file=sys.stderr)
        return 2

    meta = _load_json(meta_path)
    if not isinstance(meta, dict):
        print("check-input.json must be a JSON object", file=sys.stderr)
        return 2
    try:
        _reject_unknown_fields(meta, ALLOWED_CHECK_INPUT_FIELDS, "check-input")
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    expected_scopes = set(meta.get("expected_scopes") or [])
    isolation = meta.get("isolation") or {}
    try:
        _reject_unknown_fields(isolation, ALLOWED_ISOLATION_FIELDS, "isolation")
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2

    events, diagnostics = load_events(events_path)
    result = summarize_usage(events, expected_scopes=expected_scopes)
    timing = summarize_timing(
        meta.get("timing_intervals") or [],
        request_at=meta.get("request_at"),
        delivered_at=meta.get("delivered_at"),
    )

    failures: list[str] = []
    if not events:
        failures.append("no usage events recorded")
    if not expected_scopes:
        failures.append("expected_scopes is empty; required coverage undeclared")
    if meta.get("probe_mode") is not True:
        # a real run must declare at least the mandatory research and review
        # coverage; a missing probe_mode field is a real run, not a bypass
        for required_scope in ("research", "review"):
            if required_scope not in expected_scopes:
                failures.append(
                    f"required scope {required_scope!r} missing from "
                    "expected_scopes declaration"
                )
        if meta.get("delivery_source") is None:
            failures.append(
                "delivery_source missing; the delivery endpoint is unverifiable"
            )
    if meta.get("delivery_source") in UNOBSERVED_DELIVERY_SOURCES:
        failures.append(
            f"delivery endpoint is {meta['delivery_source']!r}, not "
            "independently observed; the final delivery turn is uncollected "
            "— result is subtotal and gaps, not an established request->"
            "delivery metering"
        )
    # a scope that actually produced events must be declared, or its absence
    # explicitly justified; silent shrinking of the task boundary is refused
    observed_scopes = {
        scope
        for scope, entry in result["scopes"].items()
        if entry.get("events", 0) > 0
    }
    for scope in sorted(
        observed_scopes - expected_scopes - {"observation", "lock_scoping"}
    ):
        failures.append(
            f"observed scope {scope!r} is not declared in expected_scopes; "
            "declare it or justify its absence"
        )
    for item in meta.get("declared_absent_scopes") or []:
        scope = item.get("scope") if isinstance(item, dict) else None
        reason = item.get("reason") if isinstance(item, dict) else None
        if not scope or not reason:
            failures.append(
                f"declared_absent_scopes entry needs scope and reason: {item!r}"
            )
        elif scope in expected_scopes:
            failures.append(f"scope {scope!r} is both expected and declared absent")
    if diagnostics["truncated_lines"]:
        failures.append(
            f"{diagnostics['truncated_lines']} truncated evidence line(s); the "
            "recorded usage is incomplete"
        )
    collection_gaps = load_collection_gaps(evidence_dir)
    if collection_gaps:
        gap_types = sorted({str(gap.get("type")) for gap in collection_gaps})
        failures.append(
            f"{len(collection_gaps)} unrecoverable collection gap(s) recorded: "
            f"{gap_types}"
        )
    if result["input_tokens"] is None:
        failures.append(
            "input usage not established on one comparable basis "
            "(input_tokens is null)"
        )
    if result["output_tokens"] is None:
        failures.append("output usage not established (output_tokens is null)")
    if not result["complete"]:
        for gap in result["gaps"]:
            failures.append(f"usage gap {gap['type']}: {gap['detail']}")
    if not timing["complete"]:
        for gap in timing["gaps"]:
            failures.append(f"timing gap {gap['type']}: {gap['detail']}")
    failures.extend(_verify_isolation(isolation, meta, evidence_dir, events, expected_scopes))

    is_probe = meta.get("probe_mode") is True
    if not failures:
        baseline_qualification = (
            "not_applicable_probe" if is_probe else "established"
        )
    else:
        baseline_qualification = "not_established"
    if is_probe:
        conclusion = (
            "probe conditions met; probe subtotal only — this is not a "
            "baseline qualification"
            if not failures
            else "probe conditions NOT met; subtotal and gaps only"
        )
    else:
        conclusion = (
            "complete baseline-collection conditions met"
            if not failures
            else "conditions NOT met; subtotal and gaps only; see failures"
        )

    payload = {
        "evidence_dir": str(evidence_dir),
        "task_identity": meta.get("task_identity"),
        "persistence_diagnostics": diagnostics,
        "collection_gaps": collection_gaps,
        "usage": result,
        "timing": timing,
        "isolation": isolation,
        "passed": not failures,
        "baseline_qualification": baseline_qualification,
        "failures": failures,
        "conclusion": conclusion,
    }
    _write_json(output_path, payload)
    print(f"check written to {output_path}: passed={payload['passed']}")
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    if diagnostics["truncated_lines"]:
        print(f"truncated evidence lines: {diagnostics['truncated_lines']}", file=sys.stderr)
    return 0 if payload["passed"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="HETU stage-01 host acceptance entry")
    sub = parser.add_subparsers(dest="command", required=True)

    probe = sub.add_parser("probe", help="verify native host capabilities (no research)")
    probe.add_argument("--host", required=True, choices=["codex", "claude", "opencode", "zcode"])
    probe.add_argument("--output", required=True, help="new observation directory")
    probe.add_argument("--live", action="store_true", help="capability probe: real labeled call")
    probe.add_argument(
        "--isolation",
        action="store_true",
        help=(
            "claude: two-case isolation probe with transcript-based verdicts; "
            "zcode: build minimal isolation evidence from coordinator-dispatched "
            "probe agents (requires --agent/--sanity-agent/--task-identity/"
            "--decoy-marker/--decoy-path/--allowed-path)"
        ),
    )
    probe.add_argument(
        "--agent",
        dest="agent",
        action="append",
        help=(
            "zcode isolation: restricted-case agent id, one per participating "
            "research context (repeatable; every context must hold)"
        ),
    )
    probe.add_argument(
        "--sanity-agent", dest="sanity_agent", help="zcode isolation: permissive-case agent id"
    )
    probe.add_argument(
        "--task-identity", dest="task_identity", help="zcode isolation: task identity of the probe"
    )
    probe.add_argument(
        "--decoy-marker", dest="decoy_marker", help="zcode isolation: seeded decoy marker value"
    )
    probe.add_argument(
        "--decoy-path", dest="decoy_path", help="zcode isolation: seeded decoy file path"
    )
    probe.add_argument(
        "--allowed-path", dest="allowed_path", help="zcode isolation: allowed material root"
    )
    probe.add_argument(
        "--staging-dir",
        dest="staging_dir",
        help=(
            "zcode isolation: directory of transcript snapshots taken before "
            "client log rotation; consulted before the live rollout paths"
        ),
    )

    run = sub.add_parser("run", help="validate one explicit case and run native observation")
    run.add_argument("--host", required=True, choices=["codex", "claude", "opencode", "zcode"])
    run.add_argument("--case", required=True, help="research-side case.json")
    run.add_argument("--output", required=True, help="new observation directory")
    run.add_argument(
        "--probe",
        action="store_true",
        help="claude: dispatch a controlled non-research probe and observe the chain",
    )
    run.add_argument(
        "--watch-agent",
        dest="watch_agent",
        action="append",
        help=(
            "zcode: attach to a coordinator-dispatched native session; repeat "
            "per session as <native-id>=<scope> (agent id or sess_* id)"
        ),
    )
    run.add_argument(
        "--request-at",
        dest="request_at",
        help="ISO timestamp of the coordinator's real dispatch moment (attach mode)",
    )
    run.add_argument(
        "--delivered-at",
        dest="delivered_at",
        help=(
            "ISO timestamp of the real final delivery; required to span the "
            "request->delivery arc beyond the last sub-agent completion"
        ),
    )
    run.add_argument(
        "--watch-timeout",
        dest="watch_timeout",
        type=int,
        default=900,
        help="attach mode deadline in seconds (default 900)",
    )
    run.add_argument(
        "--isolation-evidence",
        dest="isolation_evidence",
        help=(
            "name of an isolation evidence file inside the output directory to "
            "reference from check-input (its content is verified by `check`)"
        ),
    )

    check = sub.add_parser("check", help="offline evidence check (never starts a host)")
    check.add_argument("--evidence", required=True, help="existing observation directory")
    check.add_argument("--output", required=True, help="check result JSON path (must not exist)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "probe":
        return _cmd_probe(args)
    if args.command == "run":
        return _cmd_run(args)
    if args.command == "check":
        return _cmd_check(args)
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
