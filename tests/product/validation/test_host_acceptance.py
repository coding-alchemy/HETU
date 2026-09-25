"""Behavior tests for the stage-01 host acceptance observation helpers.

Expected values below are asserted literally; they are never computed by
calling the production summarizer on itself. Counterexamples marked
S1–S6/R2–R3 come from the stage-01 review findings (2026-09-08).
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "host_acceptance.py"
REPO_ROOT = SCRIPT_PATH.parents[1]
FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "host_acceptance"
# 07.6：最小合成夹具（synthetic-verifier-usage.jsonl）替代真实 B04 历史回放。
# 预期值人工可复算：4 行、2 个 message_id（各重复 2 行→去重计 1 次），
# output=200+300=500；tool unique=tool_synth_01/02=2；cache_read=500+700=1200；
# input（exclusive，不含缓存）=1000+2000=3000；cache_write=0。
SYN_EXPECTED_OUTPUT_TOKENS = 500
SYN_EXPECTED_TOOL_CALLS = 2
SYN_EXPECTED_CACHE_READ = 1_200


def load_module() -> dict:
    import runpy

    return runpy.run_path(str(SCRIPT_PATH))


def message_event(**overrides: object) -> dict:
    event = {
        "source": "zcode", "session_id": "s1", "segment_id": "g1",
        "message_id": "m1", "scope": "research", "kind": "message_final",
        "input_tokens": 100, "output_tokens": 7, "cached_input_read": 80,
        "cached_input_write": 0, "input_includes_cache": True,
    }
    event.update(overrides)
    return event


# ---------------------------------------------------------------------------
# plan-given example (updated to read/write cache fields per review S3)


def test_message_blocks_count_once_and_missing_scope_is_visible():
    summarize = load_module()["summarize_usage"]
    event = {
        "source": "claude", "session_id": "s1", "segment_id": "g1",
        "message_id": "m1", "scope": "review", "kind": "message_final",
        "input_tokens": 100, "output_tokens": 7, "cached_input_read": 80,
        "cached_input_write": 0, "input_includes_cache": True,
    }
    result = summarize([event, dict(event)], expected_scopes={"review", "research"})
    assert result["input_tokens"] == 100
    assert result["output_tokens"] == 7
    assert result["cached_input_read_tokens"] == 80
    assert result["cached_input_write_tokens"] == 0
    assert result["complete"] is False
    assert result["gaps"]


# ---------------------------------------------------------------------------
# S1: missing usage, real zero, and parent-child coverage


def test_s1_missing_required_usage_is_unknown_not_zero():
    summarize = load_module()["summarize_usage"]
    event = message_event(input_tokens=None, output_tokens=None)
    result = summarize([event], expected_scopes={"research"})
    assert result["input_tokens"] is None
    assert result["output_tokens"] is None
    assert result["complete"] is False
    assert any(g["type"] == "missing_required_usage" for g in result["gaps"])


def test_s1_partial_missing_usage_reports_provable_subtotal_only():
    summarize = load_module()["summarize_usage"]
    good = message_event(message_id="ok", input_tokens=50, output_tokens=5)
    bad = message_event(message_id="bad", input_tokens=None, output_tokens=None)
    result = summarize([good, bad], expected_scopes={"research"})
    assert result["input_tokens"] == 50
    assert result["output_tokens"] == 5
    assert result["complete"] is False
    assert any(g["type"] == "missing_required_usage" for g in result["gaps"])


def test_s1_real_zero_is_a_value_not_a_gap():
    summarize = load_module()["summarize_usage"]
    result = summarize(
        [message_event(input_tokens=0, output_tokens=0)],
        expected_scopes={"research"},
    )
    assert result["input_tokens"] == 0
    assert result["output_tokens"] == 0
    assert result["complete"] is True
    assert result["gaps"] == []


def test_s1_child_without_existing_parent_is_counted_and_flagged():
    summarize = load_module()["summarize_usage"]
    child = message_event(
        session_id="child", message_id="c1",
        included_in_parent="ghost-parent",
    )
    result = summarize([child], expected_scopes={"research"})
    assert result["input_tokens"] == 100
    assert result["complete"] is False
    assert any(g["type"] == "unverified_parent_inclusion" for g in result["gaps"])


def test_s1_bare_boolean_inclusion_claim_is_unverified():
    summarize = load_module()["summarize_usage"]
    child = message_event(message_id="c1", included_in_parent=True)
    result = summarize([child], expected_scopes={"research"})
    assert result["input_tokens"] == 100
    assert result["complete"] is False
    assert any(g["type"] == "unverified_parent_inclusion" for g in result["gaps"])


def test_s1_parent_outside_task_scope_cannot_absorb_child():
    summarize = load_module()["summarize_usage"]
    parent = message_event(
        session_id="obs-parent", message_id="p1", scope="observation",
        parent_inclusion="includes_children",
    )
    child = message_event(
        session_id="child", message_id="c1", included_in_parent="obs-parent"
    )
    result = summarize([parent, child], expected_scopes={"research"})
    assert result["input_tokens"] == 100
    assert result["complete"] is False
    assert any(g["type"] == "unverified_parent_inclusion" for g in result["gaps"])


def test_s1_parent_child_contradiction_is_flagged_and_child_counted():
    summarize = load_module()["summarize_usage"]
    parent = message_event(
        session_id="parent", message_id="p1",
        parent_inclusion="excludes_children",
    )
    child = message_event(
        session_id="child", message_id="c1", included_in_parent="parent"
    )
    result = summarize([parent, child], expected_scopes={"research", "review"})
    assert result["input_tokens"] == 200
    assert result["complete"] is False
    assert any(g["type"] == "parent_child_contradiction" for g in result["gaps"])


def test_verified_parent_inclusion_skips_child_tokens_but_counts_child_tools():
    summarize = load_module()["summarize_usage"]
    parent = message_event(
        session_id="parent", message_id="p1",
        parent_inclusion="includes_children",
        input_tokens=500, output_tokens=50,
    )
    child = message_event(
        session_id="child", message_id="c1", scope="review",
        included_in_parent="parent",
        input_tokens=200, output_tokens=20,
        tool_calls=[{"id": "t-child", "name": "Bash"}],
    )
    result = summarize([parent, child], expected_scopes={"research", "review"})
    assert result["input_tokens"] == 500
    assert result["output_tokens"] == 50
    assert result["complete"] is True
    assert result["tool_calls"]["unique_ids"] == 1
    assert result["scopes"]["review"]["included_in_parent"] is True


def test_s1_missing_segment_of_a_multi_session_role_is_visible():
    summarize = load_module()["summarize_usage"]
    result = summarize(
        [message_event(session_id="s1", segment_id="seg-a")],
        expected_scopes={"research", "research#seg-b"},
    )
    assert result["complete"] is False
    missing = {g["detail"] for g in result["gaps"] if g["type"] == "missing_scope"}
    assert "research#seg-b" in missing


# ---------------------------------------------------------------------------
# S2: native-identity dedup and metering semantics


def test_s2_incremental_dedup_ignores_capture_timestamp():
    summarize = load_module()["summarize_usage"]
    first = message_event(kind="incremental", message_id="inc-1",
                          input_tokens=100, output_tokens=10,
                          captured_at="2026-09-08T00:00:00+00:00")
    again = dict(first, captured_at="2026-09-08T00:00:05+00:00")
    result = summarize([first, again], expected_scopes={"research"})
    assert result["input_tokens"] == 100
    assert result["output_tokens"] == 10
    assert result["complete"] is True


def test_s2_incremental_with_conflicting_values_same_identity_is_a_gap():
    summarize = load_module()["summarize_usage"]
    first = message_event(kind="incremental", message_id="inc-1",
                          input_tokens=100, output_tokens=10)
    conflicting = dict(first, input_tokens=120, output_tokens=12,
                       captured_at="later")
    result = summarize([first, conflicting], expected_scopes={"research"})
    assert result["input_tokens"] is None
    assert any(g["type"] == "usage_conflict" for g in result["gaps"])


def test_s2_message_final_and_cumulative_in_one_range_never_sum():
    summarize = load_module()["summarize_usage"]
    final = message_event(kind="message_final", input_tokens=100, output_tokens=7)
    cumulative = message_event(kind="cumulative", message_id=None,
                               sequence=1, input_tokens=100, output_tokens=7)
    result = summarize([final, cumulative], expected_scopes={"research"})
    assert result["input_tokens"] is None
    assert result["complete"] is False
    assert any(g["type"] == "mixed_metering_kinds" for g in result["gaps"])


def test_s2_cumulative_reset_on_output_counter_is_detected():
    summarize = load_module()["summarize_usage"]
    first = message_event(kind="cumulative", message_id=None, sequence=1,
                          input_tokens=10, output_tokens=10)
    second = message_event(kind="cumulative", message_id=None, sequence=2,
                           input_tokens=20, output_tokens=2)
    result = summarize([first, second], expected_scopes={"research"})
    assert result["input_tokens"] is None
    assert result["complete"] is False
    assert any(
        g["type"] == "cumulative_reset_unbounded" and "output_tokens" in g["detail"]
        for g in result["gaps"]
    )


def test_s2_cumulative_reset_on_cache_fields_is_detected():
    summarize = load_module()["summarize_usage"]
    first = message_event(kind="cumulative", message_id=None, sequence=1,
                          input_tokens=10, output_tokens=5,
                          cached_input_read=500)
    second = message_event(kind="cumulative", message_id=None, sequence=2,
                           input_tokens=20, output_tokens=8,
                           cached_input_read=400)
    result = summarize([first, second], expected_scopes={"research"})
    assert result["complete"] is False
    assert any(
        g["type"] == "cumulative_reset_unbounded" and "cached_input_read" in g["detail"]
        for g in result["gaps"]
    )


def test_s2_conflicting_message_values_are_not_silently_last_won():
    summarize = load_module()["summarize_usage"]
    base = message_event(message_id="m1")
    conflict = message_event(message_id="m1", output_tokens=9,
                             captured_at="later")
    result = summarize([base, conflict], expected_scopes={"research"})
    assert result["output_tokens"] is None
    assert result["complete"] is False
    assert any(g["type"] == "usage_conflict" for g in result["gaps"])


def test_incremental_distinct_identities_sum():
    summarize = load_module()["summarize_usage"]
    first = message_event(kind="incremental", message_id="inc-1",
                          input_tokens=10, output_tokens=2)
    second = message_event(kind="incremental", message_id="inc-2",
                           input_tokens=5, output_tokens=1)
    result = summarize([first, second], expected_scopes={"research"})
    assert result["input_tokens"] == 15
    assert result["output_tokens"] == 3


# ---------------------------------------------------------------------------
# S3: cache semantics


def test_s3_mixed_cache_semantics_normalize_when_definition_allows():
    summarize = load_module()["summarize_usage"]
    research = message_event(scope="research", message_id="r1",
                             input_tokens=100, output_tokens=5,
                             cached_input_read=0, cached_input_write=0,
                             input_includes_cache=True)
    review = message_event(scope="review", message_id="v1",
                           input_tokens=40, output_tokens=4,
                           cached_input_read=900, cached_input_write=0,
                           input_includes_cache=False)
    result = summarize([research, review],
                       expected_scopes={"research", "review"})
    # Both cache details are known, so the only same-basis total is the
    # cache-inclusive 100 + (40 + 900) = 1040.
    assert result["input_tokens"] == 1040
    assert result["input_tokens_basis"] == "inclusive-normalized"
    assert result["input_tokens_as_reported"]["research"]["input_tokens"] == 100
    assert result["input_tokens_as_reported"]["review"]["input_tokens"] == 40
    assert result["complete"] is True


def test_s3_missing_cache_does_not_block_input_output_acceptance():
    summarize = load_module()["summarize_usage"]
    event = message_event(cached_input_read=None, cached_input_write=None)
    result = summarize([event], expected_scopes={"research"})
    assert result["input_tokens"] == 100
    assert result["output_tokens"] == 7
    assert result["complete"] is True
    assert result["cached_input_read_tokens"] is None
    assert any(g["type"] == "cache_unknown" for g in result["cache_gaps"])


def test_s3_mixed_semantics_with_unknown_cache_never_publish_blended_total():
    summarize = load_module()["summarize_usage"]
    inclusive = message_event(scope="research", message_id="r1",
                              input_tokens=100, output_tokens=5,
                              cached_input_read=None, cached_input_write=None,
                              input_includes_cache=True)
    exclusive = message_event(scope="review", message_id="v1",
                              input_tokens=40, output_tokens=4,
                              cached_input_read=None, cached_input_write=None,
                              input_includes_cache=False)
    result = summarize([inclusive, exclusive],
                       expected_scopes={"research", "review"})
    assert result["input_tokens"] is None
    assert result["input_tokens_basis"] is None
    # per-scope as-reported subtotals and the output total stay comparable,
    # but a null input total must never be reported as a complete input usage
    assert result["output_tokens"] == 9
    assert result["complete"] is False
    assert any(g["type"] == "cache_cannot_normalize" for g in result["cache_gaps"])


def test_s3_cache_write_is_reported_separately_from_cache_read():
    summarize = load_module()["summarize_usage"]
    event = message_event(cached_input_read=300, cached_input_write=120,
                          input_includes_cache=False)
    result = summarize([event], expected_scopes={"research"})
    assert result["cached_input_read_tokens"] == 300
    assert result["cached_input_write_tokens"] == 120


def test_s3_replay_adapter_keeps_read_and_write_separate(tmp_path):
    module = load_module()
    adapter = module["events_from_closeout_verifier_usage"]
    raw = {
        "case": "X", "client": "claude CLI", "source": "s.jsonl",
        "message_id": "m", "model": "glm-5.3",
        "usage": {"input_tokens": 10, "output_tokens": 2,
                  "cache_read_input_tokens": 7, "cache_creation_input_tokens": 3},
        "tool_calls": [],
    }
    source = tmp_path / "events.jsonl"
    source.write_text(json.dumps(raw), encoding="utf-8")
    events = adapter(source, case="X")
    assert events[0]["cached_input_read"] == 7
    assert events[0]["cached_input_write"] == 3
    result = module["summarize_usage"](events, expected_scopes={"review"})
    # claude semantics: reported input excludes both cache fields; the write
    # stays its own number instead of folding into a single "hit".
    assert result["input_tokens"] == 10
    assert result["input_tokens_basis"] == "exclusive"
    assert result["cached_input_read_tokens"] == 7
    assert result["cached_input_write_tokens"] == 3


# ---------------------------------------------------------------------------
# S4: tool totals share the token task boundary


def test_s4_observation_tools_never_enter_task_tool_totals():
    summarize = load_module()["summarize_usage"]
    research = message_event(message_id="r1",
                             tool_calls=[{"id": "t1", "name": "Bash"}])
    observation = message_event(scope="observation", message_id="o1",
                                tool_calls=[{"id": "t2", "name": "Bash"}])
    result = summarize([research, observation], expected_scopes={"research"})
    assert result["tool_calls"]["task_unique_ids"] == 1
    assert result["tool_calls"]["unique_ids"] == 1


def test_s4_tool_identity_includes_source_host():
    summarize = load_module()["summarize_usage"]
    zcode_event = message_event(source="zcode", message_id="r1",
                                tool_calls=[{"id": "t1", "name": "Bash"}])
    claude_event = message_event(source="claude", session_id="s2",
                                 message_id="c1",
                                 tool_calls=[{"id": "t1", "name": "Bash"}])
    result = summarize(
        [zcode_event, claude_event], expected_scopes={"research", "review"}
    )
    assert result["tool_calls"]["unique_ids"] == 2


def test_s4_wrapper_tools_are_counted_separately_from_task_tools():
    summarize = load_module()["summarize_usage"]
    event = message_event(
        tool_calls=[
            {"id": "t1", "name": "Bash", "external": True},
            {"id": "t3", "name": "Agent", "wrapper": True},
        ],
    )
    result = summarize([event], expected_scopes={"research"})
    assert result["tool_calls"]["task_unique_ids"] == 1
    assert result["tool_calls"]["external_unique_ids"] == 1
    assert result["tool_calls"]["wrapper_unique_ids"] == 1


def test_s4_child_tokens_included_but_child_tools_still_visible():
    summarize = load_module()["summarize_usage"]
    parent = message_event(
        session_id="parent", message_id="p1",
        parent_inclusion="includes_children",
        input_tokens=500, output_tokens=50,
    )
    child = message_event(
        session_id="child", message_id="c1",
        included_in_parent="parent",
        input_tokens=200, output_tokens=20,
        tool_calls=[{"id": "t-child", "name": "Bash"}],
    )
    result = summarize([parent, child], expected_scopes={"research"})
    assert result["input_tokens"] == 500
    assert result["tool_calls"]["unique_ids"] == 1


# ---------------------------------------------------------------------------
# S5: evidence protection


def test_s5_append_event_rejects_non_whitelisted_fields(tmp_path):
    persist = load_module()
    with pytest.raises(ValueError, match="non-whitelisted"):
        persist["append_event"](
            tmp_path / "events.jsonl",
            message_event(api_key="secret", raw_stdout="PROMPT TEXT"),
        )


def test_s5_flush_segment_fsyncs_for_segment_end_durability(
    tmp_path, monkeypatch
):
    persist = load_module()
    path = tmp_path / "events.jsonl"
    calls: list[str] = []
    real_fsync = persist["os"].fsync

    def tracking_fsync(fd: int) -> None:
        calls.append("fsync")
        real_fsync(fd)

    monkeypatch.setattr(persist["os"], "fsync", tracking_fsync)
    persist["append_event"](path, message_event(message_id="m1"))
    assert calls == []  # per-event write flushes; fsync belongs to segment end
    persist["flush_segment"](path)
    assert calls == ["fsync"]


def test_s5_write_json_refuses_to_overwrite_existing_file(tmp_path):
    module = load_module()
    target = tmp_path / "result.json"
    target.write_text('{"keep": true}', encoding="utf-8")
    with pytest.raises(FileExistsError):
        module["_write_json"](target, {"evil": True})
    assert json.loads(target.read_text(encoding="utf-8")) == {"keep": True}


def test_s5_case_whitelist_rejects_unknown_and_answer_fields():
    module = load_module()
    base = {
        "case_id": "X", "security": "600036.SH",
        "neutral_request": "分析", "as_of": "2026-09-06T00:00:00+08:00",
        "data_mode": "public", "depth": "standard",
        "reuse_previous_task_data": False,
    }
    with pytest.raises(ValueError, match="expected_scores"):
        module["_validate_case"]({**base, "expected_scores": {"pass": True}})
    with pytest.raises(ValueError, match="api_key"):
        module["_validate_case"]({**base, "api_key": "sk-..."})
    with pytest.raises(ValueError, match="run_shell"):
        module["_validate_case"]({**base, "run_shell": "rm -rf /"})


def test_s5_truncated_tail_is_diagnosed_not_counted(tmp_path):
    persist = load_module()
    path = tmp_path / "events.jsonl"
    persist["append_event"](path, message_event(message_id="m1"))
    with open(path, "a", encoding="utf-8") as handle:
        handle.write('{"source": "zcode", "message_id": "m2", "out')
    loaded, diagnostics = persist["load_events"](path)
    assert [event["message_id"] for event in loaded] == ["m1"]
    assert diagnostics["truncated_lines"] == 1
    assert diagnostics["truncated_raw"].startswith('{"source"')


# ---------------------------------------------------------------------------
# S6: timing validation


def test_timing_union_overlaps_and_ratios():
    summarize_timing = load_module()["summarize_timing"]
    result = summarize_timing(
        [
            {"category": "research", "start": 0, "end": 10},
            {"category": "research", "start": 5, "end": 15},
            {"category": "review", "start": 12, "end": 18},
        ],
        request_at=0,
        delivered_at=20,
    )
    assert result["categories"]["research"]["union"] == 15
    assert result["categories"]["review"]["union"] == 6
    assert result["covered_union"] == 18
    assert result["total_wait"] == 20
    assert result["categories"]["research"]["ratio"] == pytest.approx(0.75)
    assert result["complete"] is True


def test_s6_interval_exceeding_window_fails_without_negative_uncovered():
    summarize_timing = load_module()["summarize_timing"]
    result = summarize_timing(
        [{"category": "research", "start": 0, "end": 20}],
        request_at=0,
        delivered_at=10,
    )
    assert result["complete"] is False
    assert any(g["type"] == "interval_outside_window" for g in result["gaps"])
    assert result["uncovered"] is None


def test_s6_inverted_window_is_rejected():
    summarize_timing = load_module()["summarize_timing"]
    result = summarize_timing(
        [{"category": "research", "start": 0, "end": 5}],
        request_at=10,
        delivered_at=0,
    )
    assert result["total_wait"] is None
    assert result["complete"] is False
    assert any(g["type"] == "inverted_window" for g in result["gaps"])


def test_s6_zero_total_wait_with_intervals_never_passes():
    summarize_timing = load_module()["summarize_timing"]
    result = summarize_timing(
        [{"category": "research", "start": 0, "end": 0}],
        request_at=0,
        delivered_at=0,
    )
    assert result["complete"] is False
    assert any(g["type"] == "zero_total_wait" for g in result["gaps"])
    assert result["categories"]["research"]["ratio"] is None


def test_s6_non_finite_and_non_numeric_timestamps_are_rejected():
    summarize_timing = load_module()["summarize_timing"]
    for bad in ("10", float("inf"), float("nan"), True):
        result = summarize_timing([], request_at=0, delivered_at=bad)
        assert result["complete"] is False
        assert any(g["type"] == "invalid_timestamp" for g in result["gaps"])


def test_s6_mixed_clock_bases_stay_uncomputed():
    summarize_timing = load_module()["summarize_timing"]
    result = summarize_timing(
        [
            {"category": "research", "start": 0, "end": 5, "clock": "wall"},
            {"category": "review", "start": 1, "end": 6, "clock": "monotonic"},
        ],
        request_at=0,
        delivered_at=10,
    )
    assert result["complete"] is False
    assert any(g["type"] == "clock_basis_mismatch" for g in result["gaps"])


# ---------------------------------------------------------------------------
# 07.6: closeout-verifier replay against a minimal synthetic fixture
# （真实 B04 历史回放已提炼为合成夹具；预期值人工可复算，不再绑定本机会话）


def test_replay_synthetic_verifier_copy_matches_hand_computed_totals():
    summarize = load_module()["summarize_usage"]
    to_events = load_module()["events_from_closeout_verifier_usage"]
    source = FIXTURE_DIR / "synthetic-verifier-usage.jsonl"
    events = to_events(source, case="SYN")
    assert len(events) == 4
    result = summarize(events, expected_scopes={"review"})
    assert result["output_tokens"] == SYN_EXPECTED_OUTPUT_TOKENS
    assert result["tool_calls"]["unique_ids"] == SYN_EXPECTED_TOOL_CALLS
    assert result["input_includes_cache"] is False
    assert result["cached_input_read_tokens"] == SYN_EXPECTED_CACHE_READ
    assert result["cached_input_write_tokens"] == 0
    assert result["input_tokens_basis"] == "exclusive"


# ---------------------------------------------------------------------------
# S-C: live collection never loses complete lines, tails or rotation gaps


def test_sc_half_line_is_not_consumed_until_complete(tmp_path):
    module = load_module()
    poll = module["_poll_complete_lines"]
    path = tmp_path / "t.jsonl"
    path.write_bytes(b'{"a": 1}\n{"b": ')
    state = {"identity": None, "offset": 0, "generation": 0}
    lines, state, lost = poll(path, state)
    assert lines == ['{"a": 1}']
    assert lost is None
    # the half-written tail stays pending; the cursor must not pass it
    lines, state, lost = poll(path, state)
    assert lines == [] and lost is None
    with open(path, "a", encoding="utf-8") as handle:
        handle.write('2}\n{"c": 3}\n')
    lines, state, lost = poll(path, state)
    assert lines == ['{"b": 2}', '{"c": 3}']
    assert lost is None


def test_sc_rotation_reports_lost_tail_and_starts_new_generation(tmp_path):
    module = load_module()
    poll = module["_poll_complete_lines"]
    path = tmp_path / "t.jsonl"
    path.write_bytes(b'{"a": 1}\n{"b": ')
    _, state, _ = poll(path, {"identity": None, "offset": 0, "generation": 0})
    assert state["offset"] == len(b'{"a": 1}\n')
    replacement = tmp_path / "t.next"
    replacement.write_bytes(b'{"z": 9}\n')
    replacement.replace(path)
    lines, state, lost = poll(path, state)
    assert lost is not None
    assert lost["reason"] == "rotated"
    assert lost["generation"] == 0
    assert lost["lost_bytes_from"] == len(b'{"a": 1}\n')
    assert lines == ['{"z": 9}']
    assert state["generation"] == 1


def test_sc_in_place_truncation_reports_gap(tmp_path):
    module = load_module()
    poll = module["_poll_complete_lines"]
    path = tmp_path / "t.jsonl"
    path.write_bytes(b'{"a": 1}\n{"b": 2}\n')
    _, state, _ = poll(path, {"identity": None, "offset": 0, "generation": 0})
    path.write_bytes(b'{"x": 1}\n')  # same inode, content shrunken
    lines, state, lost = poll(path, state)
    assert lost is not None and lost["reason"] == "truncated"
    assert lines == ['{"x": 1}']
    assert state["generation"] == 1


class _FakeProcess:
    def __init__(self, codes):
        self._codes = list(codes)

    def poll(self):
        return self._codes.pop(0) if self._codes else 1


def _claude_transcript_line(message_id="m1", model="glm-5.3"):
    return json.dumps({
        "message": {"id": message_id, "model": model,
                    "usage": {"input_tokens": 10, "output_tokens": 2}},
    })


def test_sc_observer_captures_events_when_process_already_exited(tmp_path):
    module = load_module()
    sessions = tmp_path / "proj"
    sessions.mkdir()
    (sessions / "t1.jsonl").write_text(
        _claude_transcript_line("m1") + "\n", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    module["_observe_claude_process"](out, _FakeProcess([1]), sessions)
    events, _ = module["load_events"](out / "usage-events.jsonl")
    assert [event["message_id"] for event in events] == ["m1"]
    assert module["load_collection_gaps"](out) == []


def test_sc_observer_drains_last_line_written_before_exit(tmp_path, monkeypatch):
    module = load_module()
    sessions = tmp_path / "proj"
    sessions.mkdir()
    transcript = sessions / "t1.jsonl"
    transcript.write_text("", encoding="utf-8")
    wrote = {"done": False}

    def writing_sleep(seconds):
        if not wrote["done"]:
            with open(transcript, "a", encoding="utf-8") as handle:
                handle.write(_claude_transcript_line("late") + "\n")
            wrote["done"] = True

    monkeypatch.setattr(module["time"], "sleep", writing_sleep)
    out = tmp_path / "out"
    out.mkdir()
    module["_observe_claude_process"](out, _FakeProcess([None, 1]), sessions)
    events, _ = module["load_events"](out / "usage-events.jsonl")
    assert [event["message_id"] for event in events] == ["late"]


def test_sc_observer_stays_locked_to_its_session(tmp_path, monkeypatch):
    module = load_module()
    sessions = tmp_path / "proj"
    sessions.mkdir()
    mine = sessions / "mine.jsonl"
    mine.write_text(_claude_transcript_line("m1") + "\n", encoding="utf-8")
    other = sessions / "other.jsonl"

    def competing_sleep(seconds):
        if not other.exists():
            other.write_text(
                _claude_transcript_line("m2") + "\n", encoding="utf-8")
            os_utime = module["os"].utime
            os_utime(other, (module["time"].time() + 60,) * 2)

    monkeypatch.setattr(module["time"], "sleep", competing_sleep)
    out = tmp_path / "out"
    out.mkdir()
    module["_observe_claude_process"](out, _FakeProcess([None, None, 1]), sessions)
    events, _ = module["load_events"](out / "usage-events.jsonl")
    # a newer concurrent transcript in the same directory must not steal
    # attribution: only the locked session's events are collected
    assert [event["message_id"] for event in events] == ["m1"]


def test_sc_observer_records_rotation_gap_and_new_segment(tmp_path, monkeypatch):
    module = load_module()
    sessions = tmp_path / "proj"
    sessions.mkdir()
    transcript = sessions / "t1.jsonl"
    transcript.write_text(_claude_transcript_line("m1") + "\n", encoding="utf-8")
    rotated = {"done": False}

    def rotating_sleep(seconds):
        if not rotated["done"]:
            replacement = tmp_path / "t1.next"
            replacement.write_text(
                _claude_transcript_line("m2") + "\n", encoding="utf-8")
            replacement.replace(transcript)
            rotated["done"] = True

    monkeypatch.setattr(module["time"], "sleep", rotating_sleep)
    out = tmp_path / "out"
    out.mkdir()
    module["_observe_claude_process"](out, _FakeProcess([None, 1]), sessions)
    events, _ = module["load_events"](out / "usage-events.jsonl")
    assert [event["message_id"] for event in events] == ["m1", "m2"]
    assert events[0]["segment_id"] != events[1]["segment_id"]
    gaps = module["load_collection_gaps"](out)
    assert any(gap["type"] == "transcript_rotated" for gap in gaps)


def test_sc_corrupt_complete_line_is_recorded_as_collection_gap(tmp_path):
    module = load_module()
    sessions = tmp_path / "proj"
    sessions.mkdir()
    (sessions / "t1.jsonl").write_text(
        _claude_transcript_line("m1") + "\n{broken json}\n", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    module["_observe_claude_process"](out, _FakeProcess([1]), sessions)
    gaps = module["load_collection_gaps"](out)
    assert any(gap["type"] == "corrupt_line" for gap in gaps)


def _claude_tool_line(message_id, tool_ids, usage, model="glm-5.3"):
    return json.dumps({
        "message": {
            "id": message_id,
            "model": model,
            "usage": usage,
            "content": [
                {"type": "tool_use", "id": tid, "name": "Bash", "input": {}}
                for tid in tool_ids
            ],
        },
    })


def test_sc_message_split_across_lines_with_same_usage_counts_once(tmp_path):
    """One message duplicated into several transcript lines (content-block
    copies) must stay single-counted, and tool calls count by actual
    tool_use id — independent of the message/line count (SP1 deviation
    review, 2026-09-17)."""
    module = load_module()
    out = tmp_path / "out"
    out.mkdir()
    usage = {
        "input_tokens": 1000,
        "output_tokens": 200,
        "cache_read_input_tokens": 800,
        "cache_creation_input_tokens": 0,
    }
    state: dict = {"identity": None, "offset": 0, "generation": 0}
    transcript = Path("sess-x.jsonl")
    # message m1 split across 3 lines, each carrying the same usage and the
    # same two tool_use blocks; m2 is a single-line follow-up, no tools
    for raw in (
        _claude_tool_line("m1", ["tool_a", "tool_b"], usage),
        _claude_tool_line("m1", ["tool_a", "tool_b"], usage),
        _claude_tool_line("m1", ["tool_a", "tool_b"], usage),
        _claude_tool_line("m2", [], {
            "input_tokens": 10, "output_tokens": 2,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
        }),
    ):
        module["_consume_claude_line"](
            out, raw, transcript, state, scope="review")
    events, _ = module["load_events"](out / "usage-events.jsonl")
    assert len(events) == 4  # one event per line; dedup happens at summary
    result = module["summarize_usage"](events, expected_scopes={"review"})
    assert result["scopes"]["review"]["events"] == 2
    assert result["input_tokens"] == 1000 + 10
    assert result["output_tokens"] == 200 + 2
    assert result["cached_input_read_tokens"] == 800
    # tools count by tool_use id: m1's two blocks appear on three lines each
    assert result["tool_calls"]["unique_ids"] == 2
    assert result["tool_calls"]["task_unique_ids"] == 2
    assert not result["gaps"]


def test_sc_conflicting_usage_for_same_message_id_is_a_gap(tmp_path):
    module = load_module()
    out = tmp_path / "out"
    out.mkdir()
    state: dict = {"identity": None, "offset": 0, "generation": 0}
    transcript = Path("sess-y.jsonl")
    module["_consume_claude_line"](
        out, _claude_transcript_line("m1"), transcript, state, scope="review")
    conflicting = json.dumps({
        "message": {
            "id": "m1",
            "model": "glm-5.3",
            "usage": {"input_tokens": 999, "output_tokens": 1},
        },
    })
    module["_consume_claude_line"](
        out, conflicting, transcript, state, scope="review")
    events, _ = module["load_events"](out / "usage-events.jsonl")
    result = module["summarize_usage"](events, expected_scopes={"review"})
    assert any(
        gap["type"] == "usage_conflict" for gap in result["gaps"]
    )
    # the conflicting identity is excluded from totals, never silently kept
    assert result["input_tokens"] is None


def test_sc_account_claude_ingests_finished_transcript_once(tmp_path):
    module = load_module()
    transcript = tmp_path / "sess-z.jsonl"
    transcript.write_text(
        _claude_tool_line(
            "m1", ["tool_a"],
            {"input_tokens": 5, "output_tokens": 1,
             "cache_read_input_tokens": 3, "cache_creation_input_tokens": 0},
        ) + "\n"
        + _claude_transcript_line("m2") + "\n",
        encoding="utf-8",
    )
    # an unfinished trailing line is an explicit collection gap, not a loss
    transcript.write_text(transcript.read_text(encoding="utf-8") + '{"trun', encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()

    class Args:
        evidence = str(out)
        session = None
        scope = "review"

    Args.transcript = str(transcript)
    assert module["_cmd_account_claude"](Args()) == 0
    events, _ = module["load_events"](out / "usage-events.jsonl")
    result = module["summarize_usage"](events, expected_scopes={"review"})
    assert result["scopes"]["review"]["events"] == 2
    assert result["tool_calls"]["unique_ids"] == 1
    gaps = module["load_collection_gaps"](out)
    assert any(gap["type"] == "unconsumed_tail" for gap in gaps)
    marker = json.loads((out / "claude-ingest.json").read_text(encoding="utf-8"))
    assert marker["ingests"][0]["scope"] == "review"
    # re-ingesting the same transcript is refused (no double-append)
    assert module["_cmd_account_claude"](Args()) == 2


# ---------------------------------------------------------------------------
# S-D: nested evidence whitelist and create-only atomic publication


def test_sd_tool_call_entries_are_field_whitelisted(tmp_path):
    module = load_module()
    path = tmp_path / "events.jsonl"
    with pytest.raises(ValueError, match="tool_calls"):
        module["append_event"](
            path,
            message_event(
                tool_calls=[{"id": "t1", "name": "Read",
                             "input": {"api_key": "sk-x"},
                             "output": "SECRET BODY"}],
            ),
        )
    assert not path.exists() or "sk-x" not in path.read_text(encoding="utf-8")


def test_sd_write_json_succeeds_when_target_absent(tmp_path):
    module = load_module()
    target = tmp_path / "result.json"
    module["_write_json"](target, {"ok": True})
    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}
    assert list(tmp_path.glob("*.tmp-*")) == []


def test_sd_write_json_race_publishes_nothing_and_keeps_competitor(tmp_path, monkeypatch):
    module = load_module()
    target = tmp_path / "result.json"
    real_link = module["os"].link

    def racing_link(src, dst, *args, **kwargs):
        # a competitor publishes between the existence check and the publish
        module["Path"](dst).write_text('{"racer": 2}', encoding="utf-8")
        return real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(module["os"], "link", racing_link)
    with pytest.raises(FileExistsError):
        module["_write_json"](target, {"evil": True})
    # the racer wins; the new payload never replaces it
    assert json.loads(target.read_text(encoding="utf-8")) == {"racer": 2}
    assert list(tmp_path.glob("*.tmp-*")) == []


def test_sd_write_json_interrupted_write_publishes_nothing(tmp_path, monkeypatch):
    module = load_module()
    target = tmp_path / "result.json"

    def failing_dump(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(module["json"], "dump", failing_dump)
    with pytest.raises(KeyboardInterrupt):
        module["_write_json"](target, {"x": 1})
    assert not target.exists()
    assert list(tmp_path.glob("*.tmp-*")) == []


# ---------------------------------------------------------------------------
# R-A: check judges evidence end to end (CLI level)


def _good_research_event(**overrides):
    event = {
        "source": "zcode", "session_id": "s1", "segment_id": "g1",
        "message_id": "m1", "scope": "research", "kind": "incremental",
        "input_tokens": 100, "output_tokens": 7,
        "cached_input_read": 80, "cached_input_write": 0,
        "input_includes_cache": True, "model": "glm-5.3-flash",
    }
    event.update(overrides)
    return event


def _write_evidence(directory, events, meta, extra_raw=""):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "usage-events.jsonl").write_text(
        "".join(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
                for event in events) + extra_raw,
        encoding="utf-8",
    )
    (directory / "check-input.json").write_text(json.dumps(meta), encoding="utf-8")


def _isolation_evidence(directory, task_identity="task-1", verdict="pass",
                        host_ok=True, covered_sessions=None):
    if covered_sessions is None:
        covered_sessions = ["s1"]
    payload = {
        "host": "zcode",
        "task_identity": task_identity,
        "verdict": verdict,
        "host_isolation_available_for_02": host_ok,
        "verified_at": "2026-09-08T00:00:00+00:00",
        "cases": {
            "restricted": {"verdict": "hold"},
            "permissive": {"verdict": "violation_detected"},
        },
        "controls": [
            {"aspect": "initial_load", "result": "verified", "basis": "clean"},
            {"aspect": "history_injection", "result": "verified", "basis": "clean"},
            {"aspect": "material_access", "result": "verified", "basis": "clean"},
        ],
        "covered_sessions": covered_sessions,
    }
    path = directory / "isolation-evidence.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _base_meta(**overrides):
    meta = {
        "case_id": "CASE",
        "task_identity": "task-1",
        "expected_scopes": ["research"],
        "probe_mode": True,
        "request_at": 0,
        "delivered_at": 10,
        "timing_intervals": [
            {"category": "research", "start": 0, "end": 10, "clock": "wall"},
        ],
        "isolation": {
            "status": "verified",
            "method": "controlled probe",
            "evidence": "isolation-evidence.json",
        },
    }
    meta.update(overrides)
    return meta


def _run_check(tmp_path, events, meta, extra_raw="", evidence_name="ev"):
    module = load_module()
    evidence = tmp_path / evidence_name
    _write_evidence(evidence, events, meta, extra_raw)
    _isolation_evidence(evidence)
    snapshot = {
        path.name: path.read_bytes()
        for path in evidence.iterdir()
    }
    result_path = tmp_path / f"result-{evidence_name}.json"
    code = module["main"](
        ["check", "--evidence", str(evidence), "--output", str(result_path)]
    )
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    after = {path.name: path.read_bytes() for path in evidence.iterdir()}
    assert after == snapshot, "check must never modify the evidence directory"
    return code, payload


def test_ra_check_passes_a_complete_evidence_set(tmp_path):
    code, payload = _run_check(tmp_path, [_good_research_event()], _base_meta())
    assert code == 0
    assert payload["passed"] is True
    assert payload["failures"] == []
    # a probe passing never claims baseline qualification
    assert payload["baseline_qualification"] == "not_applicable_probe"
    assert "probe" in payload["conclusion"]
    assert "complete baseline-collection" not in payload["conclusion"]


def test_ra_truncated_tail_fails_the_check(tmp_path):
    code, payload = _run_check(
        tmp_path, [_good_research_event()], _base_meta(),
        extra_raw='{"source": "zcode", "message_id": "m2", "out',
    )
    assert code == 1
    assert payload["passed"] is False
    assert any("truncated" in failure for failure in payload["failures"])


def test_ra_missing_delivered_at_fails_timing_incomplete(tmp_path):
    """评审阻断项：缺交付时点时 complete=False 本身就足以阻止通过，
    不得依赖 gaps 非空；未知不为零，失败原因可定位到缺失字段。"""
    meta = _base_meta(delivered_at=None)
    code, payload = _run_check(
        tmp_path, [_good_research_event()], meta, evidence_name="ev-no-delivery",
    )
    assert code == 1
    assert payload["passed"] is False
    assert payload["baseline_qualification"] == "not_established"
    assert payload["timing"]["complete"] is False
    assert payload["timing"]["total_wait"] is None
    assert payload["timing"]["gaps"] == []
    assert any("delivered_at" in failure for failure in payload["failures"])


def test_ra_non_probe_missing_delivered_at_not_established(tmp_path):
    """正式基线路径同样 fail-closed：缺 delivered_at 不得 established。"""
    events = [
        _good_research_event(),
        _good_research_event(scope="review", session_id="s2", message_id="v1",
                             input_tokens=50, output_tokens=5,
                             cached_input_read=10, cached_input_write=0),
    ]
    meta = _base_meta(
        probe_mode=False,
        expected_scopes=["research", "review"],
        delivery_source="independently-observed",
        delivered_at=None,
        timing_intervals=[
            {"category": "research", "start": 0, "end": 8, "clock": "wall"},
            {"category": "review", "start": 8, "end": 10, "clock": "wall"},
        ],
    )
    evidence = tmp_path / "ev-np"
    _write_evidence(evidence, events, meta)
    _isolation_evidence(evidence, covered_sessions=["s1", "s2"])
    result_path = tmp_path / "result-np.json"
    code = load_module()["main"](
        ["check", "--evidence", str(evidence), "--output", str(result_path)]
    )
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert code == 1
    assert payload["passed"] is False
    assert payload["baseline_qualification"] == "not_established"
    assert any("delivered_at" in failure for failure in payload["failures"])


def test_ra_verified_status_without_evidence_file_fails(tmp_path):
    meta = _base_meta(isolation={
        "status": "verified", "method": "claimed", "evidence": "nope.json",
    })
    code, payload = _run_check(tmp_path, [_good_research_event()], meta)
    assert code == 1
    assert payload["passed"] is False
    assert any("nope.json" in failure for failure in payload["failures"])


def test_ra_evidence_from_another_task_fails(tmp_path):
    module = load_module()
    evidence = tmp_path / "ev"
    _write_evidence(evidence, [_good_research_event()], _base_meta())
    _isolation_evidence(evidence, task_identity="some-other-task")
    result_path = tmp_path / "result.json"
    code = module["main"](
        ["check", "--evidence", str(evidence), "--output", str(result_path)]
    )
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert code == 1
    assert any("task_identity" in failure for failure in payload["failures"])


def test_ra_hold_verdict_evidence_does_not_satisfy_check(tmp_path):
    module = load_module()
    evidence = tmp_path / "ev"
    _write_evidence(evidence, [_good_research_event()], _base_meta())
    _isolation_evidence(evidence, verdict="hold", host_ok=False)
    result_path = tmp_path / "result.json"
    code = module["main"](
        ["check", "--evidence", str(evidence), "--output", str(result_path)]
    )
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert code == 1
    assert any("verdict" in failure for failure in payload["failures"])


def test_ra_unnormalizable_input_total_fails_the_check(tmp_path):
    events = [_good_research_event(input_includes_cache=None)]
    code, payload = _run_check(
        tmp_path, events, _base_meta(), evidence_name="ev-unorm",
    )
    assert code == 1
    assert payload["passed"] is False
    assert any("input usage" in failure for failure in payload["failures"])


def test_ra_missing_required_scope_in_non_probe_run_fails(tmp_path):
    meta = _base_meta(probe_mode=False, expected_scopes=["research"])
    code, payload = _run_check(
        tmp_path, [_good_research_event()], meta, evidence_name="ev-scope",
    )
    assert code == 1
    assert any("'review'" in failure for failure in payload["failures"])


def test_ra_empty_expected_scopes_fails(tmp_path):
    meta = _base_meta(expected_scopes=[])
    code, payload = _run_check(
        tmp_path, [_good_research_event()], meta, evidence_name="ev-empty",
    )
    assert code == 1
    assert any("expected_scopes" in failure for failure in payload["failures"])


def test_ra_non_probe_positive_control_passes_with_two_scopes(tmp_path):
    module = load_module()
    events = [
        _good_research_event(),
        _good_research_event(scope="review", session_id="s2", message_id="v1",
                             input_tokens=50, output_tokens=5,
                             cached_input_read=10, cached_input_write=0),
    ]
    meta = _base_meta(
        probe_mode=False,
        expected_scopes=["research", "review"],
        delivery_source="independently-observed",
        timing_intervals=[
            {"category": "research", "start": 0, "end": 8, "clock": "wall"},
            {"category": "review", "start": 8, "end": 10, "clock": "wall"},
        ],
    )
    evidence = tmp_path / "ev"
    _write_evidence(evidence, events, meta)
    _isolation_evidence(evidence, covered_sessions=["s1", "s2"])
    result_path = tmp_path / "result.json"
    code = module["main"](
        ["check", "--evidence", str(evidence), "--output", str(result_path)]
    )
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert code == 0
    assert payload["passed"] is True
    assert payload["baseline_qualification"] == "established"


# ---------------------------------------------------------------------------
# R-B / R-C: native chain parameterisation and honest live verdicts


def test_rb_watch_specs_require_explicit_scope():
    module = load_module()
    assert module["_parse_watch_specs"](["a1=research", "a2=review"]) == [
        ("a1", "research"), ("a2", "review"),
    ]
    with pytest.raises(ValueError):
        module["_parse_watch_specs"](["a1"])
    with pytest.raises(ValueError):
        module["_parse_watch_specs"](["a1=Research#seg"])


def test_rb_zcode_event_exposes_tool_id_and_name_only():
    module = load_module()
    payload = {
        "requestId": "req-1",
        "startedAt": "2026-09-08T00:00:01Z",
        "model": {"modelId": "glm-5.3-flash"},
        "response": {
            "usage": {"inputTokens": 100, "outputTokens": 5,
                      "cacheReadTokens": 90, "cacheWriteTokens": 0},
            "toolCalls": [
                {"id": "t1", "name": "Read", "input": {"file_path": "/x"}},
                {"id": "t2", "name": "Agent", "input": {"prompt": "p"}},
            ],
        },
    }
    event = module["_zcode_line_event"](
        payload, session_id="sess", scope="research", segment_id="g1",
        request_at=0.0,
    )
    assert event["input_tokens"] == 100
    assert event["input_includes_cache"] is True
    assert event["model"] == "glm-5.3-flash"
    assert event["tool_calls"] == [
        {"id": "t1", "name": "Read"},
        {"id": "t2", "name": "Agent", "wrapper": True},
    ]
    assert "input" not in json.dumps(event["tool_calls"])
    # records outside the task window are not part of this task
    assert module["_zcode_line_event"](
        payload, session_id="sess", scope="research", segment_id="g1",
        request_at=10**12,
    ) is None


def test_rc_claude_live_requires_success_model_and_usage():
    module = load_module()
    verified, gaps = module["_claude_live_verdict"](0, {"glm"}, {"output_tokens": 3})
    assert verified and gaps == []
    verified, gaps = module["_claude_live_verdict"](42, set(), None)
    assert not verified and len(gaps) == 3


# ---------------------------------------------------------------------------
# S-E: an unknown cache basis never inherits a neighbour's known basis


def test_se_unknown_semantics_beside_known_is_not_treated_as_known():
    summarize = load_module()["summarize_usage"]
    known = message_event(message_id="m1", input_tokens=100, output_tokens=7,
                          cached_input_read=80, cached_input_write=0,
                          input_includes_cache=True)
    unknown = message_event(message_id="m2", input_tokens=100, output_tokens=7,
                            cached_input_read=80, cached_input_write=0,
                            input_includes_cache=None)
    for events in ([known, unknown], [unknown, known]):  # order must not matter
        result = summarize(events, expected_scopes={"research"})
        assert result["input_tokens"] is None
        assert result["input_tokens_basis"] is None
        assert result["complete"] is False
        # the provable per-scope subtotal stays published
        assert result["input_tokens_as_reported"]["research"]["input_tokens"] == 200
        assert result["output_tokens"] == 14
        assert any(g["type"] == "cache_cannot_normalize" for g in result["cache_gaps"])


def test_se_cumulative_range_with_unknown_semantics_stays_unknown():
    summarize = load_module()["summarize_usage"]
    first = message_event(kind="cumulative", message_id=None, sequence=1,
                          input_tokens=10, output_tokens=1,
                          input_includes_cache=None)
    second = message_event(kind="cumulative", message_id=None, sequence=2,
                           input_tokens=30, output_tokens=3,
                           input_includes_cache=True)
    result = summarize([first, second], expected_scopes={"research"})
    assert result["input_tokens"] is None
    assert result["input_tokens_basis"] is None
    assert result["complete"] is False
    assert any(g["type"] == "cache_cannot_normalize" for g in result["cache_gaps"])


# ---------------------------------------------------------------------------
# S-F: an unconsumed partial tail at observer exit is a recorded data gap


def test_sf_unconsumed_tail_at_observer_exit_is_recorded_as_gap(tmp_path):
    module = load_module()
    sessions = tmp_path / "proj"
    sessions.mkdir()
    (sessions / "t1.jsonl").write_text(
        _claude_transcript_line("m1") + "\n" + '{"trunc', encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    module["_observe_claude_process"](out, _FakeProcess([1]), sessions)
    events, _ = module["load_events"](out / "usage-events.jsonl")
    assert [event["message_id"] for event in events] == ["m1"]
    gaps = module["load_collection_gaps"](out)
    assert any(gap["type"] == "unconsumed_tail" for gap in gaps)


# ---------------------------------------------------------------------------
# R-D: isolation evidence must actually cover what it claims


def _stage_zcode_transcript(directory, agent_id, records, corrupt_tail=False):
    directory.mkdir(parents=True, exist_ok=True)
    name = f"model-io-sess_subagent_agent_{agent_id}.jsonl"
    path = directory / name
    text = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)
    if corrupt_tail:
        text += '{"requestId": "trunc", "request' + "\n"
    path.write_text(text, encoding="utf-8")
    return path


def _zcode_record(*, message_count=2, marker=None, tool_path=None,
                  started="2026-09-08T00:00:10Z"):
    request_messages = [{"role": "user", "content": "probe task"}]
    if marker:
        request_messages.append({"role": "tool", "content": f"result {marker}"})
    tool_calls = []
    if tool_path:
        tool_calls.append({"id": "t1", "name": "Read",
                           "input": {"file_path": tool_path}})
    return {
        "requestId": "req-1",
        "startedAt": started,
        "completedAt": "2026-09-08T00:00:20Z",
        "model": {"modelId": "glm-5.3-flash"},
        "request": {"messageCount": message_count, "messages": request_messages},
        "response": {
            "usage": {"inputTokens": 10, "outputTokens": 2,
                      "cacheReadTokens": 0, "cacheWriteTokens": 0},
            "text": "",
            "toolCalls": tool_calls,
        },
    }


def _run_isolation_report(tmp_path, restricted_records, sanity_records,
                          restricted_agent="aaaa1111", sanity_agent="bbbb2222",
                          restricted_corrupt_tail=False):
    module = load_module()
    stage = tmp_path / "stage"
    out = tmp_path / "iso-out"
    out.mkdir()
    _stage_zcode_transcript(stage, restricted_agent, restricted_records,
                            corrupt_tail=restricted_corrupt_tail)
    _stage_zcode_transcript(stage, sanity_agent, sanity_records)
    report = module["_zcode_isolation_report"](
        out,
        task_identity="task-1",
        restricted_agent=[restricted_agent],
        sanity_agent=sanity_agent,
        decoy_marker="HETU_DECOY_TEST",
        decoy_path="/probe/old-research/decoy.txt",
        allowed_path="/probe/allowed",
        staging_dir=str(stage),
    )
    return report, json.loads((out / "isolation-evidence-zcode.json").read_text(
        encoding="utf-8"))


def test_rd_empty_restricted_transcript_cannot_pass_isolation(tmp_path):
    report, payload = _run_isolation_report(tmp_path, restricted_records=[],
                                            sanity_records=[
                                                _zcode_record(
                                                    marker="HETU_DECOY_TEST",
                                                    tool_path="/probe/old-research/decoy.txt"),
                                            ])
    assert payload["verdict"] != "pass"
    assert payload["host_isolation_available_for_02"] is False
    assert payload["cases"]["restricted"]["verdict"] != "hold"
    assert all(control["result"] != "verified" for control in payload["controls"])


def test_rd_out_of_range_access_without_decoy_is_boundary_gap_not_violation(tmp_path):
    report, payload = _run_isolation_report(
        tmp_path,
        restricted_records=[
            # touches a path outside the declared allowed root but never the
            # seeded decoy: the probe cannot classify this read, so it must
            # NOT be asserted as an old-data violation — and must not pass
            _zcode_record(tool_path="/elsewhere/secret.txt"),
        ],
        sanity_records=[
            _zcode_record(marker="HETU_DECOY_TEST",
                          tool_path="/probe/old-research/decoy.txt"),
        ],
    )
    assert payload["verdict"] != "pass"
    assert payload["host_isolation_available_for_02"] is False
    restricted = payload["cases"]["restricted"]
    assert restricted["verdict"] == "boundary_unproven"
    assert restricted["agents"][0]["tool_targets_outside_allowed"] == 1
    material = [c for c in payload["controls"] if c["aspect"] == "material_access"]
    assert material and material[0]["result"] != "verified"
    # the detector-sanity case must still be satisfied by the sanity agent
    assert payload["cases"]["permissive"]["verdict"] == "violation_detected"


def test_rd_official_skill_read_is_not_old_data_violation(tmp_path):
    report, payload = _run_isolation_report(
        tmp_path,
        restricted_records=[
            # an approved official skill read outside the single declared
            # material root: not forbidden old data, but the single-root
            # probe cannot prove the boundary, so no pass either
            _zcode_record(tool_path="/opt/hetu-skills/hetu-stock-analysis/SKILL.md"),
        ],
        sanity_records=[
            _zcode_record(marker="HETU_DECOY_TEST",
                          tool_path="/probe/old-research/decoy.txt"),
        ],
    )
    restricted = payload["cases"]["restricted"]
    assert restricted["verdict"] == "boundary_unproven"
    assert payload["verdict"] != "pass"
    assert payload["host_isolation_available_for_02"] is False


def test_rd_restricted_decoy_access_still_violation(tmp_path):
    report, payload = _run_isolation_report(
        tmp_path,
        restricted_records=[
            # the seeded forbidden old-task material itself: still a violation
            _zcode_record(marker="HETU_DECOY_TEST",
                          tool_path="/probe/old-research/decoy.txt"),
        ],
        sanity_records=[
            _zcode_record(marker="HETU_DECOY_TEST",
                          tool_path="/probe/old-research/decoy.txt"),
        ],
    )
    restricted = payload["cases"]["restricted"]
    assert restricted["verdict"] == "violation"
    assert payload["verdict"] != "pass"
    assert payload["host_isolation_available_for_02"] is False


def test_rd_corrupt_line_blocks_isolation_pass(tmp_path):
    report, payload = _run_isolation_report(
        tmp_path,
        restricted_records=[
            _zcode_record(tool_path="/probe/allowed/material.txt"),
        ],
        sanity_records=[
            _zcode_record(marker="HETU_DECOY_TEST",
                          tool_path="/probe/old-research/decoy.txt"),
        ],
        restricted_corrupt_tail=True,
    )
    assert payload["verdict"] != "pass"
    assert payload["host_isolation_available_for_02"] is False
    restricted = payload["cases"]["restricted"]
    assert restricted["verdict"] == "evidence_gap"
    agent = restricted["agents"][0]
    assert agent["unparseable_records"] == 1
    # the legal record stays a usable observation, not voided by the gap
    assert agent["valid_coverage"] is True
    assert agent["request_count"] == 1
    assert payload["covered_sessions"]
    assert all(control["result"] != "verified" for control in payload["controls"])


def test_rd_violation_observed_in_legal_records_survives_corrupt_tail(tmp_path):
    report, payload = _run_isolation_report(
        tmp_path,
        restricted_records=[
            _zcode_record(marker="HETU_DECOY_TEST",
                          tool_path="/probe/old-research/decoy.txt"),
        ],
        sanity_records=[
            _zcode_record(marker="HETU_DECOY_TEST",
                          tool_path="/probe/old-research/decoy.txt"),
        ],
        restricted_corrupt_tail=True,
    )
    restricted = payload["cases"]["restricted"]
    # the partial gap must not void the violation seen in the legal records
    assert restricted["verdict"] == "violation"
    assert restricted["agents"][0]["unparseable_records"] == 1
    assert restricted["agents"][0]["decoy_path_in_tool_inputs"] == 1
    assert payload["verdict"] != "pass"


def test_rd_clean_restricted_context_with_all_controls_passes(tmp_path):
    report, payload = _run_isolation_report(
        tmp_path,
        restricted_records=[
            _zcode_record(tool_path="/probe/allowed/material.txt"),
        ],
        sanity_records=[
            _zcode_record(marker="HETU_DECOY_TEST",
                          tool_path="/probe/old-research/decoy.txt"),
        ],
    )
    assert payload["verdict"] == "pass"
    assert payload["covered_sessions"], "evidence must list covered contexts"


def test_rd_isolation_evidence_must_cover_participating_contexts(tmp_path):
    module = load_module()
    events = [
        _good_research_event(),
        _good_research_event(scope="review", session_id="s2", message_id="v1",
                             input_tokens=50, output_tokens=5,
                             cached_input_read=10, cached_input_write=0),
    ]
    meta = _base_meta(
        probe_mode=False,
        expected_scopes=["research", "review"],
        delivery_source="independently-observed",
    )
    evidence = tmp_path / "ev"
    _write_evidence(evidence, events, meta)
    _isolation_evidence(evidence, covered_sessions=["s1"])  # review context missing
    result_path = tmp_path / "result.json"
    code = module["main"](
        ["check", "--evidence", str(evidence), "--output", str(result_path)]
    )
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert code == 1
    assert any("s2" in failure for failure in payload["failures"])


# ---------------------------------------------------------------------------
# R-E: an unobserved delivery endpoint can never yield a complete claim


def test_re_coordinator_reported_delivery_fails_complete_qualification(tmp_path):
    code, payload = _run_check(
        tmp_path, [_good_research_event()],
        _base_meta(delivery_source="coordinator-reported"),
        evidence_name="ev-dl",
    )
    assert code == 1
    assert payload["passed"] is False
    assert any("delivery" in failure for failure in payload["failures"])


def _write_probe_case(tmp_path, *, probe_mode):
    case = {
        "case_id": "CASE", "security": "NONE",
        "neutral_request": "controlled probe",
        "as_of": "2026-09-08T00:00:00+08:00",
        "data_mode": "public", "depth": "probe",
        "reuse_previous_task_data": False,
    }
    if probe_mode is not None:
        case["probe_mode"] = probe_mode
    path = tmp_path / "case.json"
    path.write_text(json.dumps(case), encoding="utf-8")
    return path


def _run_zcode_stage(tmp_path, *, probe_mode, truncated_tail=False):
    module = load_module()
    stage = tmp_path / "stage"
    records = [_zcode_record()]
    transcript = _stage_zcode_transcript(stage, "cccc3333", records)
    if truncated_tail:
        with open(transcript, "a", encoding="utf-8") as handle:
            handle.write('{"startedAt": "2026-09-08T00:00:3')
    out = tmp_path / "out"
    case = _write_probe_case(tmp_path, probe_mode=probe_mode)
    code = module["main"]([
        "run", "--host", "zcode", "--case", str(case), "--output", str(out),
        "--watch-agent", f"file:{transcript}=research",
        "--request-at", "2026-09-08T00:00:00Z",
        "--delivered-at", "2026-09-08T00:01:00Z",
    ])
    return module, code, out


def test_re_run_persists_delivery_gap_and_check_blocks(tmp_path):
    module, code, out = _run_zcode_stage(tmp_path, probe_mode=True)
    assert code == 0
    meta = json.loads((out / "check-input.json").read_text(encoding="utf-8"))
    assert meta["probe_mode"] is True
    gaps = module["load_collection_gaps"](out)
    assert any(gap["type"] == "delivery_tail_unobserved" for gap in gaps)
    result_path = tmp_path / "result.json"
    check_code = module["main"](
        ["check", "--evidence", str(out), "--output", str(result_path)]
    )
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert check_code == 1
    assert payload["passed"] is False
    assert any("delivery" in failure for failure in payload["failures"])


# ---------------------------------------------------------------------------
# R-F: mode comes from the case; missing mode cannot bypass the scope gate


def test_rf_missing_case_mode_defaults_to_real_run_gate(tmp_path):
    module, code, out = _run_zcode_stage(tmp_path, probe_mode=None)
    assert code == 0
    meta = json.loads((out / "check-input.json").read_text(encoding="utf-8"))
    assert meta["probe_mode"] is False
    result_path = tmp_path / "result.json"
    check_code = module["main"](
        ["check", "--evidence", str(out), "--output", str(result_path)]
    )
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert check_code == 1
    assert any("'review'" in failure for failure in payload["failures"])


def test_rf_leaked_scope_observed_but_undeclared_fails(tmp_path):
    events = [
        _good_research_event(),
        _good_research_event(scope="correction", session_id="s3",
                             message_id="c1", input_tokens=30, output_tokens=3,
                             cached_input_read=5, cached_input_write=0),
    ]
    meta = _base_meta(
        probe_mode=False,
        expected_scopes=["research", "review"],
        delivery_source="independently-observed",
    )
    code, payload = _run_check(tmp_path, events, meta, evidence_name="ev-leak")
    assert code == 1
    assert any("correction" in failure for failure in payload["failures"])


# ---------------------------------------------------------------------------
# DB-E: zcode durable metering export (db.sqlite model_usage/tool_usage)

# 合成表只保留导出器实际读取的列；列集合与本机真实库 2026-09-24 只读核实的
# 已支持结构一致（新增 assistant_message_id/attempt_index/status/
# raw_usage_json；query_source 为可选列，由 with_query_source 控制）。
MODEL_USAGE_COLUMNS = (
    "logical_request_id", "session_id", "turn_id", "model_id",
    "input_tokens", "output_tokens",
    "cache_read_input_tokens", "cache_creation_input_tokens",
    "tool_call_count", "started_at", "completed_at",
    "assistant_message_id", "attempt_index", "status", "raw_usage_json",
)
MODEL_USAGE_DDL = """
CREATE TABLE model_usage(
    logical_request_id TEXT, session_id TEXT, turn_id TEXT, model_id TEXT,
    input_tokens INTEGER, output_tokens INTEGER,
    cache_read_input_tokens INTEGER, cache_creation_input_tokens INTEGER,
    tool_call_count INTEGER, started_at INTEGER, completed_at INTEGER,
    assistant_message_id TEXT, attempt_index INTEGER, status TEXT,
    raw_usage_json TEXT, query_source TEXT)
"""
MODEL_USAGE_DDL_NO_QUERY_SOURCE = """
CREATE TABLE model_usage(
    logical_request_id TEXT, session_id TEXT, turn_id TEXT, model_id TEXT,
    input_tokens INTEGER, output_tokens INTEGER,
    cache_read_input_tokens INTEGER, cache_creation_input_tokens INTEGER,
    tool_call_count INTEGER, started_at INTEGER, completed_at INTEGER,
    assistant_message_id TEXT, attempt_index INTEGER, status TEXT,
    raw_usage_json TEXT)
"""
TOOL_USAGE_DDL = """
CREATE TABLE tool_usage(
    session_id TEXT, turn_id TEXT, tool_call_id TEXT, tool_name TEXT,
    started_at INTEGER)
"""
MESSAGE_DDL = """
CREATE TABLE message(
    id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER,
    time_updated INTEGER, data TEXT)
"""
PART_DDL = """
CREATE TABLE part(
    id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT,
    time_created INTEGER, time_updated INTEGER, data TEXT)
"""


def _make_zcode_db(tmp_path, *, rows, tool_rows=(), drop_tool_table=False,
                   message_rows=(), part_rows=(), with_query_source=True):
    db = tmp_path / "db.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        MODEL_USAGE_DDL if with_query_source else MODEL_USAGE_DDL_NO_QUERY_SOURCE
    )
    if not drop_tool_table:
        conn.execute(TOOL_USAGE_DDL)
        conn.executemany("INSERT INTO tool_usage VALUES (?,?,?,?,?)", tool_rows)
    width = 16 if with_query_source else 15
    placeholders = ",".join("?" * width)
    conn.executemany(
        f"INSERT INTO model_usage VALUES ({placeholders})",
        [row[:width] for row in rows],
    )
    conn.execute(MESSAGE_DDL)
    conn.execute(PART_DDL)
    conn.executemany(
        "INSERT INTO message VALUES (?,?,?,?,?)", message_rows
    )
    conn.executemany(
        "INSERT INTO part VALUES (?,?,?,?,?,?)", part_rows
    )
    conn.commit()
    conn.close()
    return db


def _db_row(session="sess_x", turn="turn_1", request="req_1", model="GLM-Test",
            inp=100, out=7, cache_read=40, cache_write=0, tool_count=0,
            started=1_789_359_700_000, completed=1_789_359_701_000,
            amid=None, attempt=0, status="completed", raw="auto",
            query_source="main_turn"):
    """合成一条 model_usage 行；raw="auto" 生成与行值一致的结算证据。"""
    if raw == "auto":
        raw = json.dumps({
            "inputTokens": inp,
            "outputTokens": out,
            "cacheReadTokens": cache_read,
            "cacheWriteTokens": cache_write,
            "totalTokens": inp + out,
        })
    return (request, session, turn, model, inp, out, cache_read, cache_write,
            tool_count, started, completed, amid, attempt, status, raw,
            query_source)


def _tool_part(pid, mid, session, call_id, tool_name, created_ms):
    payload = {"type": "tool", "callID": call_id, "tool": tool_name}
    return (pid, mid, session, created_ms, created_ms,
            json.dumps(payload, ensure_ascii=False))


def _run_db_export(module, db, evidence, pairs, *,
                   request_at="2026-09-14T00:00:00Z", window_end=None):
    args = ["db-export", "--evidence", str(evidence), "--db", str(db),
            "--request-at", request_at]
    if window_end:
        args += ["--window-end", window_end]
    for scope, session in pairs:
        args += ["--session", session, "--scope", scope]
    return module["main"](args)


def _read_events(evidence):
    return [
        json.loads(line)
        for line in (evidence / "usage-events.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]


def test_db_export_maps_rows_to_whitelisted_events(tmp_path):
    module = load_module()
    request_at_ms = int(module["_iso_to_epoch"]("2026-09-14T00:00:00Z") * 1000)
    rows = [
        _db_row(request="req_old", started=request_at_ms - 60_000),
        _db_row(request="req_1", tool_count=1,
                started=request_at_ms + 1_000, amid="msg_1"),
        _db_row(request="req_2", turn="turn_2", tool_count=2, inp=200, out=14,
                started=request_at_ms + 2_000, amid="msg_2"),
    ]
    tool_rows = [
        ("sess_x", "turn_1", "call_a1", "Skill", request_at_ms + 1_900),
        ("sess_x", "turn_2", "call_b1", "Read", request_at_ms + 2_900),
        ("sess_x", "turn_2", "call_b2", "Agent", request_at_ms + 2_950),
    ]
    message_rows = [
        _msg_row("msg_1", created_ms=request_at_ms + 1_000),
        _msg_row("msg_2", created_ms=request_at_ms + 2_000),
    ]
    part_rows = [
        _tool_part("p_a1", "msg_1", "sess_x", "call_a1", "Skill",
                   request_at_ms + 1_900),
        _tool_part("p_b1", "msg_2", "sess_x", "call_b1", "Read",
                   request_at_ms + 2_900),
        _tool_part("p_b2", "msg_2", "sess_x", "call_b2", "Agent",
                   request_at_ms + 2_950),
    ]
    db = _make_zcode_db(tmp_path, rows=rows, tool_rows=tool_rows,
                        message_rows=message_rows, part_rows=part_rows)
    evidence = tmp_path / "ev"
    evidence.mkdir()
    code = module["main"]([
        "db-export", "--evidence", str(evidence), "--db", str(db),
        "--session", "sess_x", "--scope", "research",
        "--request-at", "2026-09-14T00:00:00Z",
    ])
    assert code == 0
    events = _read_events(evidence)
    assert [event["message_id"] for event in events] == [
        '["req_1",0]', '["req_2",0]',
    ]
    first = events[0]
    assert first["source"] == "zcode-db"
    assert first["session_id"] == "sess_x"
    assert first["segment_id"] == "db"
    assert first["scope"] == "research"
    assert first["kind"] == "incremental"
    assert first["input_tokens"] == 100
    assert first["output_tokens"] == 7
    assert first["cached_input_read"] == 40
    assert first["cached_input_write"] == 0
    assert first["input_includes_cache"] is True
    assert first["model"] == "GLM-Test"
    assert first["tool_calls"] == [{"id": "call_a1", "name": "Skill"}]
    second = events[1]
    assert second["tool_calls"] == [
        {"id": "call_b1", "name": "Read"},
        {"id": "call_b2", "name": "Agent", "wrapper": True},
    ]
    assert first["started_at"].endswith("+00:00")
    # provenance for auditability
    provenance = json.loads(
        (evidence / "db-export.json").read_text(encoding="utf-8")
    )
    entry = provenance["sessions"]["research"]["sess_x"]
    assert entry["rows"] == 2
    assert entry["status_distribution"] == {"completed": 2}
    assert entry["query_source_distribution"] == {"main_turn": 2}
    assert provenance["db"] == str(db)


def test_db_export_schema_drift_fails_closed(tmp_path, capsys):
    module = load_module()
    db = _make_zcode_db(tmp_path, rows=[_db_row()], drop_tool_table=True)
    evidence = tmp_path / "ev"
    evidence.mkdir()
    code = module["main"]([
        "db-export", "--evidence", str(evidence), "--db", str(db),
        "--session", "sess_x", "--scope", "research",
        "--request-at", "2026-09-14T00:00:00Z",
    ])
    assert code == 1
    assert "unsupported" in capsys.readouterr().err
    assert not (evidence / "usage-events.jsonl").exists()


def test_db_export_missing_column_fails_closed(tmp_path, capsys):
    module = load_module()
    db = tmp_path / "db.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE model_usage(logical_request_id TEXT, session_id TEXT)"
    )
    conn.commit()
    conn.close()
    evidence = tmp_path / "ev"
    evidence.mkdir()
    code = module["main"]([
        "db-export", "--evidence", str(evidence), "--db", str(db),
        "--session", "sess_x", "--scope", "research",
        "--request-at", "2026-09-14T00:00:00Z",
    ])
    assert code == 1
    assert "unsupported" in capsys.readouterr().err
    assert not (evidence / "usage-events.jsonl").exists()


def test_db_export_zero_rows_is_a_gap_not_silence(tmp_path, capsys):
    module = load_module()
    db = _make_zcode_db(tmp_path, rows=[_db_row(session="other")])
    evidence = tmp_path / "ev"
    evidence.mkdir()
    code = module["main"]([
        "db-export", "--evidence", str(evidence), "--db", str(db),
        "--session", "sess_x", "--scope", "research",
        "--request-at", "2026-09-14T00:00:00Z",
    ])
    assert code == 1
    assert "no model_usage rows" in capsys.readouterr().err
    assert not (evidence / "usage-events.jsonl").exists()


def test_db_export_window_end_excludes_late_rows(tmp_path, capsys):
    module = load_module()
    request_at_ms = int(module["_iso_to_epoch"]("2026-09-14T00:00:00Z") * 1000)
    rows = [
        _db_row(request="req_in", started=request_at_ms + 1_000),
        _db_row(request="req_late", started=request_at_ms + 3_600_000),
    ]
    db = _make_zcode_db(tmp_path, rows=rows)
    evidence = tmp_path / "ev"
    evidence.mkdir()
    code = module["main"]([
        "db-export", "--evidence", str(evidence), "--db", str(db),
        "--session", "sess_x", "--scope", "research",
        "--request-at", "2026-09-14T00:00:00Z",
        "--window-end", "2026-09-14T00:30:00Z",
    ])
    assert code == 0
    events = [
        json.loads(line)
        for line in (evidence / "usage-events.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert [event["message_id"] for event in events] == ['["req_in",0]']


def test_db_export_events_pass_usage_summarizer(tmp_path):
    module = load_module()
    request_at_ms = int(module["_iso_to_epoch"]("2026-09-14T00:00:00Z") * 1000)
    db = _make_zcode_db(
        tmp_path,
        rows=[_db_row(started=request_at_ms + 1_000, amid="msg_1")],
        tool_rows=[("sess_x", "turn_1", "call_a1", "Skill",
                    request_at_ms + 1_900)],
        message_rows=[_msg_row("msg_1", created_ms=request_at_ms + 1_000)],
        part_rows=[_tool_part("p_a1", "msg_1", "sess_x", "call_a1", "Skill",
                              request_at_ms + 1_900)],
    )
    evidence = tmp_path / "ev"
    evidence.mkdir()
    assert module["main"]([
        "db-export", "--evidence", str(evidence), "--db", str(db),
        "--session", "sess_x", "--scope", "research",
        "--request-at", "2026-09-14T00:00:00Z",
    ]) == 0
    events, diagnostics = module["load_events"](
        evidence / "usage-events.jsonl"
    )
    result = module["summarize_usage"](
        events, expected_scopes={"research"}
    )
    assert diagnostics["truncated_lines"] == 0
    assert result["complete"] is True
    assert result["input_tokens"] == 100
    assert result["output_tokens"] == 7


# --- 6-A1/6-B2: 工具归属必须来自可核验身份链，不来自顺序、计数或 turn -------

T0 = 1_789_344_000_000  # 2026-09-14T00:00:00Z in ms


def test_db_export_attributes_tools_by_identity_not_count_order(tmp_path):
    """同 turn 两请求、工具数相等但开始顺序与真实归属交错：顺序切片会把
    call_b1 错挂给 req_a；part.callID 身份链给出真实归属。"""
    module = load_module()
    rows = [
        _db_row(request="req_a", tool_count=1, started=T0 + 1_000,
                amid="msg_a"),
        _db_row(request="req_b", tool_count=1, started=T0 + 2_000,
                amid="msg_b"),
    ]
    message_rows = [
        _msg_row("msg_a", created_ms=T0 + 1_000),
        _msg_row("msg_b", created_ms=T0 + 2_000),
    ]
    part_rows = [
        _tool_part("p_a1", "msg_a", "sess_x", "call_a1", "Read", T0 + 1_500),
        _tool_part("p_b1", "msg_b", "sess_x", "call_b1", "Skill", T0 + 1_200),
    ]
    # call_b1 开始更早但属于 req_b；按 turn 内顺序切片必错挂
    tool_rows = [
        ("sess_x", "turn_1", "call_b1", "Skill", T0 + 1_200),
        ("sess_x", "turn_1", "call_a1", "Read", T0 + 1_500),
    ]
    db = _make_zcode_db(tmp_path, rows=rows, tool_rows=tool_rows,
                        message_rows=message_rows, part_rows=part_rows)
    evidence = tmp_path / "ev"
    evidence.mkdir()
    assert _run_db_export(module, db, evidence, [("research", "sess_x")]) == 0
    by_id = {event["message_id"]: event for event in _read_events(evidence)}
    assert by_id['["req_a",0]']["tool_calls"] == [
        {"id": "call_a1", "name": "Read"},
    ]
    assert by_id['["req_b",0]']["tool_calls"] == [
        {"id": "call_b1", "name": "Skill"},
    ]


def test_db_export_recovers_tools_when_tool_call_count_under_reports(tmp_path):
    """tool_call_count 少报（记 0）但身份链唯一时按链还原；计数不是归属
    依据，也不是丢弃依据。"""
    module = load_module()
    rows = [_db_row(request="req_1", tool_count=0, started=T0 + 1_000,
                    amid="msg_1")]
    db = _make_zcode_db(
        tmp_path, rows=rows,
        tool_rows=[("sess_x", "turn_1", "call_a1", "Skill", T0 + 1_900)],
        message_rows=[_msg_row("msg_1", created_ms=T0 + 1_000)],
        part_rows=[_tool_part("p_a1", "msg_1", "sess_x", "call_a1", "Skill",
                              T0 + 1_900)],
    )
    evidence = tmp_path / "ev"
    evidence.mkdir()
    assert _run_db_export(module, db, evidence, [("research", "sess_x")]) == 0
    (event,) = _read_events(evidence)
    assert event["tool_calls"] == [{"id": "call_a1", "name": "Skill"}]


def test_db_export_orphan_tool_fails_closed(tmp_path, capsys):
    """找不到归属部件的工具不得静默丢失：受控拒绝且不发布任何文件。"""
    module = load_module()
    rows = [_db_row(request="req_1", started=T0 + 1_000, amid="msg_1")]
    db = _make_zcode_db(
        tmp_path, rows=rows,
        tool_rows=[("sess_x", "turn_1", "call_x", "Write", T0 + 1_900)],
        message_rows=[_msg_row("msg_1", created_ms=T0 + 1_000)],
    )
    evidence = tmp_path / "ev"
    evidence.mkdir()
    code = _run_db_export(module, db, evidence, [("research", "sess_x")])
    assert code == 1
    assert "call_x" in capsys.readouterr().err
    assert not (evidence / "usage-events.jsonl").exists()
    assert not (evidence / "db-export.json").exists()


def test_db_export_duplicate_tool_call_id_fails_closed(tmp_path, capsys):
    """窗内重复的工具身份无法区分是一次还是两次调用：受控拒绝。"""
    module = load_module()
    rows = [_db_row(request="req_1", started=T0 + 1_000, amid="msg_1")]
    db = _make_zcode_db(
        tmp_path, rows=rows,
        tool_rows=[
            ("sess_x", "turn_1", "call_a1", "Skill", T0 + 1_900),
            ("sess_x", "turn_1", "call_a1", "Skill", T0 + 1_950),
        ],
        message_rows=[_msg_row("msg_1", created_ms=T0 + 1_000)],
        part_rows=[_tool_part("p_a1", "msg_1", "sess_x", "call_a1", "Skill",
                              T0 + 1_900)],
    )
    evidence = tmp_path / "ev"
    evidence.mkdir()
    code = _run_db_export(module, db, evidence, [("research", "sess_x")])
    assert code == 1
    assert "ambiguous" in capsys.readouterr().err
    assert not (evidence / "usage-events.jsonl").exists()


def test_db_export_tool_claimed_by_two_messages_fails_closed(tmp_path, capsys):
    """同一 callID 出现在两条消息的部件里：归属歧义，受控拒绝。"""
    module = load_module()
    rows = [
        _db_row(request="req_a", started=T0 + 1_000, amid="msg_a"),
        _db_row(request="req_b", started=T0 + 2_000, amid="msg_b"),
    ]
    db = _make_zcode_db(
        tmp_path, rows=rows,
        tool_rows=[("sess_x", "turn_1", "call_x", "Read", T0 + 1_500)],
        message_rows=[
            _msg_row("msg_a", created_ms=T0 + 1_000),
            _msg_row("msg_b", created_ms=T0 + 2_000),
        ],
        part_rows=[
            _tool_part("p_1", "msg_a", "sess_x", "call_x", "Read", T0 + 1_500),
            _tool_part("p_2", "msg_b", "sess_x", "call_x", "Read", T0 + 1_500),
        ],
    )
    evidence = tmp_path / "ev"
    evidence.mkdir()
    code = _run_db_export(module, db, evidence, [("research", "sess_x")])
    assert code == 1
    assert "ambiguous" in capsys.readouterr().err
    assert not (evidence / "usage-events.jsonl").exists()


def test_db_export_tool_of_out_of_window_request_fails_closed(tmp_path, capsys):
    """窗内工具属于窗外请求：窗外身份可只读核对，但工具不能脱离其请求
    计量，也不许猜挂到窗内请求；不能完整解释即拒绝。"""
    module = load_module()
    rows = [
        _db_row(request="req_old", started=T0 - 60_000, amid="msg_old"),
        _db_row(request="req_in", started=T0 + 1_000, amid="msg_in"),
    ]
    db = _make_zcode_db(
        tmp_path, rows=rows,
        tool_rows=[("sess_x", "turn_1", "call_x", "Write", T0 + 500)],
        message_rows=[
            _msg_row("msg_old", created_ms=T0 - 60_000),
            _msg_row("msg_in", created_ms=T0 + 1_000),
        ],
        part_rows=[_tool_part("p_x", "msg_old", "sess_x", "call_x", "Write",
                              T0 + 500)],
    )
    evidence = tmp_path / "ev"
    evidence.mkdir()
    code = _run_db_export(module, db, evidence, [("research", "sess_x")])
    assert code == 1
    assert "outside the export window" in capsys.readouterr().err
    assert not (evidence / "usage-events.jsonl").exists()


def test_db_export_tool_started_after_window_end_is_excluded(tmp_path):
    """窗内请求的工具在窗外开始：窗口规则分别作用于请求与工具开始时间，
    窗外工具不计入也不猜挂，窗内部分正常导出。"""
    module = load_module()
    rows = [_db_row(request="req_1", started=T0 + 1_000, amid="msg_1")]
    db = _make_zcode_db(
        tmp_path, rows=rows,
        tool_rows=[("sess_x", "turn_1", "call_late", "Write",
                    T0 + 3_600_000)],
        message_rows=[_msg_row("msg_1", created_ms=T0 + 1_000)],
        part_rows=[_tool_part("p_late", "msg_1", "sess_x", "call_late",
                              "Write", T0 + 3_600_000)],
    )
    evidence = tmp_path / "ev"
    evidence.mkdir()
    code = _run_db_export(module, db, evidence, [("research", "sess_x")],
                          window_end="2026-09-14T00:30:00Z")
    assert code == 0
    (event,) = _read_events(evidence)
    assert "tool_calls" not in event


# --- 6-A2/6-B2: 执行状态与用量结算分离；未知不是零 -------------------------


def test_db_export_failed_attempt_with_settled_usage_is_counted(tmp_path):
    """status=error 但有可回源结算证据时按合同计量；status 只说明执行
    结果，不独自决定结算。provenance 记录状态分布。"""
    module = load_module()
    rows = [
        _db_row(request="req_ok", started=T0 + 1_000),
        _db_row(request="req_err", status="error", started=T0 + 2_000,
                inp=60, out=3, cache_read=10),
    ]
    db = _make_zcode_db(tmp_path, rows=rows)
    evidence = tmp_path / "ev"
    evidence.mkdir()
    assert _run_db_export(module, db, evidence, [("research", "sess_x")]) == 0
    events = _read_events(evidence)
    result = module["summarize_usage"](events, expected_scopes={"research"})
    assert result["complete"] is True
    assert result["input_tokens"] == 160
    assert result["output_tokens"] == 10
    provenance = json.loads(
        (evidence / "db-export.json").read_text(encoding="utf-8")
    )
    entry = provenance["sessions"]["research"]["sess_x"]
    assert entry["status_distribution"] == {"completed": 1, "error": 1}


def test_db_export_unsettled_cancelled_attempt_refuses_export(tmp_path, capsys):
    """cancelled 且无结算证据：零值只是占位，未知不是零——拒绝完整导出，
    不发布成功文件。"""
    module = load_module()
    rows = [
        _db_row(request="req_ok", started=T0 + 1_000),
        _db_row(request="req_c", status="cancelled", inp=0, out=0,
                cache_read=0, raw=None, started=T0 + 2_000),
    ]
    db = _make_zcode_db(tmp_path, rows=rows)
    evidence = tmp_path / "ev"
    evidence.mkdir()
    code = _run_db_export(module, db, evidence, [("research", "sess_x")])
    assert code == 1
    err = capsys.readouterr().err
    assert "req_c" in err and "cancelled" in err
    assert not (evidence / "usage-events.jsonl").exists()
    assert not (evidence / "db-export.json").exists()


def test_db_export_placeholder_zero_is_not_a_known_zero(tmp_path, capsys):
    """completed 但缺结算证据的零值同样是占位：拒绝而不是按零计入。"""
    module = load_module()
    rows = [_db_row(request="req_z", inp=0, out=0, cache_read=0, raw=None,
                    started=T0 + 1_000)]
    db = _make_zcode_db(tmp_path, rows=rows)
    evidence = tmp_path / "ev"
    evidence.mkdir()
    code = _run_db_export(module, db, evidence, [("research", "sess_x")])
    assert code == 1
    assert "req_z" in capsys.readouterr().err
    assert not (evidence / "usage-events.jsonl").exists()


def test_db_export_settled_zero_usage_is_counted_as_zero(tmp_path):
    """有可回源结算证据的真实零值正常计入（可靠零 ≠ 占位零）。"""
    module = load_module()
    rows = [_db_row(request="req_z", inp=0, out=0, cache_read=0,
                    started=T0 + 1_000)]
    db = _make_zcode_db(tmp_path, rows=rows)
    evidence = tmp_path / "ev"
    evidence.mkdir()
    assert _run_db_export(module, db, evidence, [("research", "sess_x")]) == 0
    events = _read_events(evidence)
    result = module["summarize_usage"](events, expected_scopes={"research"})
    assert result["complete"] is True
    assert result["input_tokens"] == 0
    assert result["output_tokens"] == 0


def test_db_export_unknown_status_fails_closed(tmp_path, capsys):
    """状态词表之外的状态按未知结构拒绝，不静默归类。"""
    module = load_module()
    rows = [_db_row(request="req_u", status="mystery", started=T0 + 1_000)]
    db = _make_zcode_db(tmp_path, rows=rows)
    evidence = tmp_path / "ev"
    evidence.mkdir()
    code = _run_db_export(module, db, evidence, [("research", "sess_x")])
    assert code == 1
    assert "mystery" in capsys.readouterr().err
    assert not (evidence / "usage-events.jsonl").exists()


def test_db_export_raw_usage_contradiction_fails_closed(tmp_path, capsys):
    """raw_usage_json 与行值矛盾时结算不可回源：受控拒绝。"""
    module = load_module()
    rows = [_db_row(request="req_m", started=T0 + 1_000,
                    raw=json.dumps({"inputTokens": 999, "outputTokens": 7,
                                    "cacheReadTokens": 40,
                                    "cacheWriteTokens": 0,
                                    "totalTokens": 1006}))]
    db = _make_zcode_db(tmp_path, rows=rows)
    evidence = tmp_path / "ev"
    evidence.mkdir()
    code = _run_db_export(module, db, evidence, [("research", "sess_x")])
    assert code == 1
    assert "contradicts" in capsys.readouterr().err
    assert not (evidence / "usage-events.jsonl").exists()


def test_db_export_running_status_is_not_settlement_evidence(tmp_path, capsys):
    """running 不是终态：raw 与行字段数值一致只证明两处记录相符，不证明
    最终结算（数值仍可能变化）——受控拒绝，unknown 不为零（6-A2 复评缺口）。"""
    module = load_module()
    rows = [_db_row(request="req_run", status="running", completed=None,
                    started=T0 + 1_000)]
    db = _make_zcode_db(tmp_path, rows=rows)
    evidence = tmp_path / "ev"
    evidence.mkdir()
    code = _run_db_export(module, db, evidence, [("research", "sess_x")])
    assert code == 1
    assert "running" in capsys.readouterr().err
    assert not (evidence / "usage-events.jsonl").exists()
    assert not (evidence / "db-export.json").exists()


# --- 6-A3: 重试身份（logical_request_id + attempt_index）-------------------


def test_db_export_attempts_of_one_request_are_metered_separately(tmp_path):
    """同请求的两个 attempt 分别计量；事件身份编码无歧义。"""
    module = load_module()
    rows = [
        _db_row(request="req_r", attempt=0, started=T0 + 1_000),
        _db_row(request="req_r", attempt=1, inp=120, out=9,
                started=T0 + 2_000),
    ]
    db = _make_zcode_db(tmp_path, rows=rows)
    evidence = tmp_path / "ev"
    evidence.mkdir()
    assert _run_db_export(module, db, evidence, [("research", "sess_x")]) == 0
    events = _read_events(evidence)
    assert [event["message_id"] for event in events] == [
        '["req_r",0]', '["req_r",1]',
    ]
    result = module["summarize_usage"](events, expected_scopes={"research"})
    assert result["complete"] is True
    assert result["input_tokens"] == 220
    assert result["output_tokens"] == 16


def test_db_export_same_attempt_duplicate_snapshot_deduped(tmp_path):
    """同 attempt 的重复快照在发布前去重：只发布一条事件、只计一次。"""
    module = load_module()
    rows = [
        _db_row(request="req_r", attempt=0, started=T0 + 1_000),
        _db_row(request="req_r", attempt=0, started=T0 + 1_500),
    ]
    db = _make_zcode_db(tmp_path, rows=rows)
    evidence = tmp_path / "ev"
    evidence.mkdir()
    assert _run_db_export(module, db, evidence, [("research", "sess_x")]) == 0
    events = _read_events(evidence)
    assert [event["message_id"] for event in events] == ['["req_r",0]']
    result = module["summarize_usage"](events, expected_scopes={"research"})
    assert result["complete"] is True
    assert result["input_tokens"] == 100
    assert result["output_tokens"] == 7


def test_db_export_same_attempt_conflicting_usage_refused_before_publish(
        tmp_path, capsys):
    """同 request 同 attempt 的用量冲突在发布前拒绝：exit 1、零输出，
    不留给下游汇总才报 usage_conflict（6-A3/A4 复评缺口）。"""
    module = load_module()
    rows = [
        _db_row(request="req_r", attempt=0, started=T0 + 1_000),
        _db_row(request="req_r", attempt=0, inp=150, started=T0 + 1_500),
    ]
    db = _make_zcode_db(tmp_path, rows=rows)
    evidence = tmp_path / "ev"
    evidence.mkdir()
    code = _run_db_export(module, db, evidence, [("research", "sess_x")])
    assert code == 1
    err = capsys.readouterr().err
    assert "req_r" in err and "attempt" in err
    assert not (evidence / "usage-events.jsonl").exists()
    assert not (evidence / "db-export.json").exists()


@pytest.mark.parametrize("corrupt_first", [False, True])
def test_db_export_duplicate_snapshot_corrupt_raw_refused_in_any_order(
        tmp_path, capsys, corrupt_first):
    """同 attempt 重复快照中一条 raw 损坏：每条记录先完成结算校验再去重，
    两种顺序都必须拒绝，不得因第一条正常就跳过第二条的校验
    （第 5 项复评缺口 1）。"""
    module = load_module()
    good = _db_row(request="req_r", attempt=0,
                   started=T0 + (1_500 if corrupt_first else 1_000))
    corrupt = _db_row(request="req_r", attempt=0,
                      started=T0 + (1_000 if corrupt_first else 1_500),
                      raw="{}")
    rows = [good, corrupt]
    db = _make_zcode_db(tmp_path, rows=rows)
    evidence = tmp_path / "ev"
    evidence.mkdir()
    code = _run_db_export(module, db, evidence, [("research", "sess_x")])
    assert code == 1
    assert "req_r" in capsys.readouterr().err
    assert not (evidence / "usage-events.jsonl").exists()
    assert not (evidence / "db-export.json").exists()


def test_db_export_duplicate_snapshot_with_tool_attributes_once(tmp_path):
    """两条合法重复快照指向同一 message 且该 message 有一条合法工具记录：
    按请求身份去重后归属无歧义，导出成功且工具只归属一次；真正多个请求
    共享 message 的拒绝行为不受影响（第 5 项复评缺口 2）。"""
    module = load_module()
    rows = [
        _db_row(request="req_r", attempt=0, started=T0 + 1_000,
                amid="msg_1", tool_count=1),
        _db_row(request="req_r", attempt=0, started=T0 + 1_500,
                amid="msg_1", tool_count=1),
    ]
    tool_rows = [("sess_x", "turn_1", "call_a1", "Skill", T0 + 1_900)]
    message_rows = [_msg_row("msg_1", created_ms=T0 + 1_000)]
    part_rows = [
        _tool_part("p_a1", "msg_1", "sess_x", "call_a1", "Skill", T0 + 1_900),
    ]
    db = _make_zcode_db(tmp_path, rows=rows, tool_rows=tool_rows,
                        message_rows=message_rows, part_rows=part_rows)
    evidence = tmp_path / "ev"
    evidence.mkdir()
    assert _run_db_export(module, db, evidence, [("research", "sess_x")]) == 0
    events = _read_events(evidence)
    assert [event["message_id"] for event in events] == ['["req_r",0]']
    assert events[0]["tool_calls"] == [{"id": "call_a1", "name": "Skill"}]


@pytest.mark.parametrize("bad_attempt", [-1, "abc", None, 1.5])
def test_db_export_attempt_index_must_be_a_non_negative_int(
        tmp_path, capsys, bad_attempt):
    """attempt_index 缺失或非负整数以外的值：身份不可核验，拒绝。
    （真实列为 INTEGER NOT NULL，文本 "0" 会被亲和性转换而无法存在，
    故文本负例用无法转换的 "abc"。）"""
    module = load_module()
    rows = [_db_row(request="req_r", attempt=bad_attempt, started=T0 + 1_000)]
    db = _make_zcode_db(tmp_path, rows=rows)
    evidence = tmp_path / "ev"
    evidence.mkdir()
    code = _run_db_export(module, db, evidence, [("research", "sess_x")])
    assert code == 1
    assert "attempt_index" in capsys.readouterr().err
    assert not (evidence / "usage-events.jsonl").exists()


# --- 6-A4: 全部会话验证通过才发布；已有输出写前拒绝；失败清理本次半成品 ----


def test_db_export_multi_session_failure_writes_nothing(tmp_path, capsys):
    """前一会话正常、后一会话无行：非零退出且两个输出都不落盘。"""
    module = load_module()
    db = _make_zcode_db(tmp_path, rows=[_db_row(started=T0 + 1_000)])
    evidence = tmp_path / "ev"
    evidence.mkdir()
    code = _run_db_export(
        module, db, evidence,
        [("research", "sess_x"), ("review", "sess_missing")],
    )
    assert code == 1
    assert "sess_missing" in capsys.readouterr().err
    assert not (evidence / "usage-events.jsonl").exists()
    assert not (evidence / "db-export.json").exists()


def test_db_export_existing_outputs_are_refused_before_any_write(tmp_path,
                                                                 capsys):
    """已有完整输出或只有半成品时都写前拒绝，已有字节不变、无 Traceback。"""
    module = load_module()
    db = _make_zcode_db(tmp_path, rows=[_db_row(started=T0 + 1_000)])
    evidence = tmp_path / "ev"
    evidence.mkdir()
    assert _run_db_export(module, db, evidence, [("research", "sess_x")]) == 0
    events_before = (evidence / "usage-events.jsonl").read_bytes()
    provenance_before = (evidence / "db-export.json").read_bytes()

    code = _run_db_export(module, db, evidence, [("research", "sess_x")])
    assert code == 2
    assert "refusing" in capsys.readouterr().err
    assert (evidence / "usage-events.jsonl").read_bytes() == events_before
    assert (evidence / "db-export.json").read_bytes() == provenance_before

    # 半成品（只有 usage-events.jsonl）同样写前拒绝
    (evidence / "db-export.json").unlink()
    code = _run_db_export(module, db, evidence, [("research", "sess_x")])
    assert code == 2
    assert "refusing" in capsys.readouterr().err
    assert (evidence / "usage-events.jsonl").read_bytes() == events_before
    assert not (evidence / "db-export.json").exists()


def test_db_export_write_failure_cleans_only_this_runs_output(
        tmp_path, monkeypatch, capsys):
    """发布阶段写失败：清除本次新建的半成品，不动既有证据，有诊断。"""
    module = load_module()
    db = _make_zcode_db(tmp_path, rows=[_db_row(started=T0 + 1_000)])
    evidence = tmp_path / "ev"
    evidence.mkdir()
    (evidence / "case-record.json").write_text('{"case_id": "KEPT"}',
                                               encoding="utf-8")

    def failing_write_json(path, payload):
        raise OSError("simulated disk failure")

    # runpy 返回的是副本字典；模块级函数的真实全局表在 __globals__ 上
    monkeypatch.setitem(
        module["main"].__globals__, "_write_json", failing_write_json
    )
    code = _run_db_export(module, db, evidence, [("research", "sess_x")])
    assert code == 1
    assert "write failed" in capsys.readouterr().err
    assert not (evidence / "usage-events.jsonl").exists()
    assert not (evidence / "db-export.json").exists()
    assert json.loads((evidence / "case-record.json").read_text(
        encoding="utf-8")) == {"case_id": "KEPT"}


def test_db_export_event_write_failure_removes_partial_events(
        tmp_path, monkeypatch, capsys):
    """事件写入中途失败：部分写入的 usage-events.jsonl 被清除。"""
    module = load_module()
    rows = [
        _db_row(request="req_1", started=T0 + 1_000),
        _db_row(request="req_2", started=T0 + 2_000),
    ]
    db = _make_zcode_db(tmp_path, rows=rows)
    evidence = tmp_path / "ev"
    evidence.mkdir()
    real_append = module["append_event"]
    calls = {"n": 0}

    def flaky_append(path, event):
        calls["n"] += 1
        if calls["n"] > 1:
            raise OSError("simulated write failure")
        return real_append(path, event)

    monkeypatch.setitem(
        module["main"].__globals__, "append_event", flaky_append
    )
    code = _run_db_export(module, db, evidence, [("research", "sess_x")])
    assert code == 1
    assert "write failed" in capsys.readouterr().err
    assert not (evidence / "usage-events.jsonl").exists()
    assert not (evidence / "db-export.json").exists()


def test_db_export_provenance_publish_failure_is_cleaned_up(
        tmp_path, monkeypatch, capsys):
    """provenance 已发布、写入调用仍抛错（如临时文件清理失败）时，本批两个
    产物都必须被清除，不得残留 db-export.json（6-A4 复评缺口）。"""
    module = load_module()
    db = _make_zcode_db(tmp_path, rows=[_db_row(started=T0 + 1_000)])
    evidence = tmp_path / "ev"
    evidence.mkdir()

    def publishing_then_failing(path, payload):
        Path(path).write_text(json.dumps(payload), encoding="utf-8")
        raise OSError("simulated post-publish cleanup failure")

    monkeypatch.setitem(
        module["main"].__globals__, "_write_json", publishing_then_failing
    )
    code = _run_db_export(module, db, evidence, [("research", "sess_x")])
    assert code == 1
    assert "write failed" in capsys.readouterr().err
    assert not (evidence / "usage-events.jsonl").exists()
    assert not (evidence / "db-export.json").exists()


class _InjectingConnection:
    """包装只读连接：在首个 ``FROM part`` 查询前从另一连接注入并发提交，
    复现「读取未固定在同一快照」的场景。"""

    def __init__(self, real, db_path, fire):
        object.__setattr__(self, "_real", real)
        object.__setattr__(self, "_db_path", db_path)
        object.__setattr__(self, "_fire", fire)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_real"), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_real"), name, value)

    def execute(self, sql, parameters=()):
        if "FROM part" in sql:
            object.__getattribute__(self, "_fire")()
        return object.__getattribute__(self, "_real").execute(sql, parameters)


def test_db_export_reads_one_consistent_snapshot_under_concurrent_writes(
        tmp_path, monkeypatch, capsys):
    """阶段 01「同一只读一致视图」：并发更新在读取中途提交时，导出不得拼出
    不存在于任一时点的组合（100 token 来自旧快照、工具来自新快照）。
    修复前：注入生效，导出混合状态 exit 0；修复后：写方提交撞上读事务，
    导出受控失败且零输出。"""
    module = load_module()
    rows = [_db_row(request="req_1", started=T0 + 1_000, amid="msg_1")]
    message_rows = [_msg_row("msg_1", created_ms=T0 + 1_000)]
    db = _make_zcode_db(tmp_path, rows=rows, message_rows=message_rows)
    evidence = tmp_path / "ev"
    evidence.mkdir()
    fired = {"done": False}

    def fire_concurrent_update():
        if fired["done"]:
            return
        fired["done"] = True
        writer = sqlite3.connect(db)
        try:
            writer.execute(
                "UPDATE model_usage SET input_tokens = 200 "
                "WHERE logical_request_id = 'req_1'"
            )
            writer.execute(
                "INSERT INTO part VALUES (?,?,?,?,?,?)",
                _tool_part("p_inj", "msg_1", "sess_x", "call_inj", "Read",
                           T0 + 1_900),
            )
            writer.execute(
                "INSERT INTO tool_usage VALUES (?,?,?,?,?)",
                ("sess_x", "turn_1", "call_inj", "Read", T0 + 1_900),
            )
            writer.commit()
        finally:
            writer.close()

    real_sqlite3 = sqlite3

    class _SqliteShim:
        Error = real_sqlite3.Error

        @staticmethod
        def connect(*args, **kwargs):
            return _InjectingConnection(
                real_sqlite3.connect(*args, **kwargs), db,
                fire_concurrent_update,
            )

    monkeypatch.setitem(module["main"].__globals__, "sqlite3", _SqliteShim)
    code = _run_db_export(module, db, evidence, [("research", "sess_x")])
    assert fired["done"] is True
    assert code == 1
    assert not (evidence / "usage-events.jsonl").exists()
    assert not (evidence / "db-export.json").exists()


# --- 6-B3: query_source 分布透明化（不替代逐请求计量）----------------------


def test_db_export_query_source_distribution_is_recorded(tmp_path):
    """有 query_source 列时 provenance 记录各会话行分布；总量口径不变。"""
    module = load_module()
    rows = [
        _db_row(request="req_1", started=T0 + 1_000),
        _db_row(request="req_2", started=T0 + 2_000),
        _db_row(request="req_t", query_source="session_title",
                started=T0 + 3_000),
    ]
    db = _make_zcode_db(tmp_path, rows=rows)
    evidence = tmp_path / "ev"
    evidence.mkdir()
    assert _run_db_export(module, db, evidence, [("research", "sess_x")]) == 0
    provenance = json.loads(
        (evidence / "db-export.json").read_text(encoding="utf-8")
    )
    entry = provenance["sessions"]["research"]["sess_x"]
    assert entry["query_source_distribution"] == {
        "main_turn": 2, "session_title": 1,
    }
    events = _read_events(evidence)
    result = module["summarize_usage"](events, expected_scopes={"research"})
    assert result["complete"] is True
    assert result["input_tokens"] == 300


def test_db_export_without_query_source_column_marks_unavailable(tmp_path):
    """无 query_source 列时明确标为不可得（None），不填零分布；导出与
    总量不受影响。"""
    module = load_module()
    db = _make_zcode_db(tmp_path, rows=[_db_row(started=T0 + 1_000)],
                        with_query_source=False)
    evidence = tmp_path / "ev"
    evidence.mkdir()
    assert _run_db_export(module, db, evidence, [("research", "sess_x")]) == 0
    provenance = json.loads(
        (evidence / "db-export.json").read_text(encoding="utf-8")
    )
    entry = provenance["sessions"]["research"]["sess_x"]
    assert entry["query_source_distribution"] is None
    assert entry["rows"] == 1


# ---------------------------------------------------------------------------
# DB-D: durable delivery observation (sweep-equivalent via client db)


def _msg_row(mid, session="sess_x", created_ms=0, updated_ms=None,
             role="assistant", visibility="visible", completed_ms=None):
    data = {
        "role": role,
        "finish": "stop",
        "semantics": {"origin": "agent_runtime",
                      "kind": "assistant_response" if role == "assistant"
                      else "background_notification",
                      "uiVisibility": visibility},
    }
    if completed_ms is not None:
        data["time"] = {"created": created_ms, "completed": completed_ms}
    return (mid, session, created_ms,
            updated_ms if updated_ms is not None else created_ms,
            json.dumps(data, ensure_ascii=False))


def _text_part(pid, mid, session, text, created_ms=0, end_ms=None):
    payload = {"type": "text", "text": text}
    if end_ms is not None:
        payload["time"] = {"start": created_ms, "end": end_ms}
    return (pid, mid, session, created_ms,
            max(created_ms, end_ms if end_ms is not None else created_ms),
            json.dumps(payload, ensure_ascii=False))


def _run_db_delivery(module, db, evidence, *extra,
                     window_end="2026-09-14T01:00:00Z"):
    return module["main"]([
        "db-delivery", "--evidence", str(evidence), "--db", str(db),
        "--session", "sess_x",
        "--request-at", "2026-09-14T00:00:00Z",
        "--window-end", window_end, *extra,
    ])


RUN_ID_THIS = "宝钢股份-600019.SH-standard-20260914T170930+0800"
RUN_ID_OTHER = "长光华芯-688048.SH-standard-20260914T122620+0800"


def test_db_delivery_selects_presentation_not_scoring_dispatch(tmp_path):
    """端点必须是用户可见最终报告呈现，不能误选锁后评分派发回合。

    场景（P1 复现）：窗口内最后完成回合是评分派发（finish=tool-calls、
    Agent 调用），真正的交付是最终报告呈现文本；运行回顾中远处出现的
    "初稿"字样不得误判为草稿。"""
    module = load_module()
    t0 = int(module["_iso_to_epoch"]("2026-09-14T00:00:00Z") * 1000)
    rows = [
        _db_row(request="req_work", started=t0 + 1_000, completed=t0 + 5_000),
        # 评分派发回合在窗口最后完成——旧方法会误选它
        _db_row(request="req_score", started=t0 + 50_000,
                completed=t0 + 60_000),
    ]
    tool_rows = [
        ("sess_x", "turn_1", "call_agent", "Agent", t0 + 55_000),
    ]
    message_rows = [
        _msg_row("msg_draft", created_ms=t0 + 10_000),
        _msg_row("msg_final", created_ms=t0 + 30_000,
                 updated_ms=t0 + 35_100, completed_ms=t0 + 35_100),
    ]
    part_rows = [
        _text_part("p_draft", "msg_draft", "sess_x",
                   "报告路径 report.md（此即最终报告，十二章完整）；W10 置\"待核验\"，"
                   "报告以\"待独立核对\"状态交付，定稿（初稿）时间…", t0 + 10_000),
        _text_part("p_final", "msg_final", "sess_x",
                   "验收完成。最终报告（即本次运行的正式最终报告）："
                   f"[report.md](/research/{RUN_ID_THIS}/report.md)\n\n"
                   + "（运行回顾：" + "过程与核对明细逐段展开。" * 40
                   + "初稿经核验修正后定稿，过程见记录。）",
                   t0 + 30_000, end_ms=t0 + 35_000),
    ]
    db = _make_zcode_db(tmp_path, rows=rows, tool_rows=tool_rows,
                        message_rows=message_rows, part_rows=part_rows)
    evidence = tmp_path / "ev"
    evidence.mkdir()
    (evidence / "delivery-message.md").write_text(
        f"# 交付\n\n- 运行 run_id / 研究目录：`{RUN_ID_THIS}`\n",
        encoding="utf-8",
    )
    assert _run_db_delivery(module, db, evidence) == 0
    observation = json.loads(
        (evidence / "delivery-observation.json").read_text(encoding="utf-8")
    )
    assert observation["source"] == "independently-observed"
    assert observation["session_id"] == "sess_x"
    assert observation["delivery_request_id"] == "msg_final"
    assert observation["delivered_at_epoch"] == (t0 + 35_100) / 1000
    basis = json.loads(
        (evidence / "delivery-observation-basis.json").read_text(
            encoding="utf-8"
        )
    )
    assert basis["method"] == (
        "user-visible assistant final-report presentation (durable db)"
    )
    assert basis["delivery_message_id"] == "msg_final"
    assert basis["excluded_presentations"]["draft"] == 1


def test_db_delivery_excludes_other_run_presentations_in_batch(tmp_path):
    """批内协调会话中他样本的最终呈现（S6 窗口含 S5 交付）不得充当
    本运行的交付端点；marker 无运行 id 时用 --run-id 限定。"""
    module = load_module()
    t0 = int(module["_iso_to_epoch"]("2026-09-14T00:00:00Z") * 1000)
    message_rows = [
        _msg_row("msg_other_final", created_ms=t0 + 10_000),
        _msg_row("msg_this_final", created_ms=t0 + 50_000,
                 updated_ms=t0 + 55_400, completed_ms=t0 + 55_400),
    ]
    part_rows = [
        _text_part("p_other", "msg_other_final", "sess_x",
                   "研究定稿。最终报告："
                   f"/research/{RUN_ID_OTHER}/report.md（已修正定稿）",
                   t0 + 10_000),
        _text_part("p_this", "msg_this_final", "sess_x",
                   "研究定稿。最终报告："
                   f"/research/{RUN_ID_THIS}/report.md（已修正定稿）",
                   t0 + 50_000, end_ms=t0 + 55_000),
    ]
    db = _make_zcode_db(tmp_path, rows=[_db_row(request="req_1")],
                        message_rows=message_rows, part_rows=part_rows)
    evidence = tmp_path / "ev"
    evidence.mkdir()
    # marker 不含运行 id（S6 实况），运行身份由 --run-id 提供
    (evidence / "delivery-message.md").write_text(
        "# 交付确认（协调者）：研究上下文已定稿交付，详见研究目录。\n",
        encoding="utf-8")
    assert _run_db_delivery(module, db, evidence,
                            "--run-id", RUN_ID_THIS) == 0
    observation = json.loads(
        (evidence / "delivery-observation.json").read_text(encoding="utf-8")
    )
    assert observation["delivery_request_id"] == "msg_this_final"
    assert observation["delivered_at_epoch"] == (t0 + 55_400) / 1000
    basis = json.loads(
        (evidence / "delivery-observation-basis.json").read_text(
            encoding="utf-8")
    )
    assert basis["excluded_presentations"]["other_run"] == 1


def test_db_delivery_without_run_identity_fails_closed(tmp_path, capsys):
    """marker 与 --run-id 均无运行 id 时拒绝猜测（fail-closed）。"""
    module = load_module()
    t0 = int(module["_iso_to_epoch"]("2026-09-14T00:00:00Z") * 1000)
    message_rows = [_msg_row("msg_final", created_ms=t0 + 30_000)]
    part_rows = [
        _text_part("p_final", "msg_final", "sess_x",
                   "最终报告：/research/some-run/report.md（已定稿）",
                   t0 + 30_000),
    ]
    db = _make_zcode_db(tmp_path, rows=[_db_row(request="req_1")],
                        message_rows=message_rows, part_rows=part_rows)
    evidence = tmp_path / "ev"
    evidence.mkdir()
    (evidence / "delivery-message.md").write_text("# 交付\n", encoding="utf-8")
    code = _run_db_delivery(module, db, evidence)
    assert code == 1
    assert "cannot be scoped" in capsys.readouterr().err
    assert not (evidence / "delivery-observation.json").exists()


def test_db_delivery_user_request_with_markers_is_not_a_presentation(
        tmp_path, capsys):
    """问题 1 复现：普通用户请求包含"最终报告"与 report.md 时不得被选为
    交付呈现；只有该候选时 fail-closed。"""
    module = load_module()
    t0 = int(module["_iso_to_epoch"]("2026-09-14T00:00:00Z") * 1000)
    message_rows = [
        _msg_row("msg_user_req", created_ms=t0 + 10_000, role="user",
                 visibility="visible"),
    ]
    part_rows = [
        _text_part("p_user_req", "msg_user_req", "sess_x",
                   "请把最终报告发我：/research/" + RUN_ID_THIS
                   + "/report.md",
                   t0 + 10_000),
    ]
    db = _make_zcode_db(tmp_path, rows=[_db_row(request="req_1")],
                        message_rows=message_rows, part_rows=part_rows)
    evidence = tmp_path / "ev"
    evidence.mkdir()
    (evidence / "delivery-message.md").write_text(
        f"# 交付\n\n- 研究目录：`{RUN_ID_THIS}`\n", encoding="utf-8")
    code = _run_db_delivery(module, db, evidence)
    assert code == 1
    err = capsys.readouterr().err
    assert "no user-visible assistant final-report presentation" in err
    assert not (evidence / "delivery-observation.json").exists()


def test_db_delivery_hidden_task_notification_is_not_a_presentation(
        tmp_path, capsys):
    """问题 1 复现（S6 实况）：role=user 且 uiVisibility=hidden 的
    task-notification 子任务通知不是对用户的正式呈现；只有该候选时
    fail-closed 并保留缺口。"""
    module = load_module()
    t0 = int(module["_iso_to_epoch"]("2026-09-14T00:00:00Z") * 1000)
    notification = (
        "<task-notification>\n<task-id>agent_x</task-id>\n"
        "<status>completed</status>\n<result># 研究 — 已定稿交付\n\n"
        "| 报告路径 | 同目录 report.md（此即最终报告，已修正定稿）|\n"
        f"| 研究目录 | /research/{RUN_ID_THIS}/ |\n</result>\n"
        "</task-notification>"
    )
    message_rows = [
        _msg_row("msg_notify", created_ms=t0 + 10_000, role="user",
                 visibility="hidden"),
    ]
    part_rows = [
        _text_part("p_notify", "msg_notify", "sess_x", notification,
                   t0 + 10_000),
    ]
    db = _make_zcode_db(tmp_path, rows=[_db_row(request="req_1")],
                        message_rows=message_rows, part_rows=part_rows)
    evidence = tmp_path / "ev"
    evidence.mkdir()
    (evidence / "delivery-message.md").write_text(
        f"# 交付\n\n- 研究目录：`{RUN_ID_THIS}`\n", encoding="utf-8")
    code = _run_db_delivery(module, db, evidence)
    assert code == 1
    err = capsys.readouterr().err
    assert "no user-visible assistant final-report presentation" in err
    assert "not user-visible" in err
    assert not (evidence / "delivery-observation.json").exists()


def test_db_delivery_uses_completion_time_not_creation(tmp_path):
    """问题 2 复现（P1 实况）：消息创建于 16:05:21.199、文本结束
    16:07:19.250、消息完成 16:07:19.296——端点须取有证据支持的完成
    时刻（取文本结束与消息完成的较大者），不得用创建时间冒充。"""
    module = load_module()
    t0 = int(module["_iso_to_epoch"]("2026-09-14T00:00:00Z") * 1000)
    created = t0 + 30_000
    text_end = t0 + 158_250
    updated = t0 + 158_296
    message_rows = [
        _msg_row("msg_final", created_ms=created, updated_ms=updated,
                 completed_ms=updated),
    ]
    part_rows = [
        _text_part("p_final", "msg_final", "sess_x",
                   "验收完成。最终报告（即本次运行的正式最终报告）："
                   f"[report.md](/research/{RUN_ID_THIS}/report.md)",
                   created, end_ms=text_end),
    ]
    db = _make_zcode_db(tmp_path, rows=[_db_row(request="req_1")],
                        message_rows=message_rows, part_rows=part_rows)
    evidence = tmp_path / "ev"
    evidence.mkdir()
    (evidence / "delivery-message.md").write_text(
        f"# 交付\n\n- 研究目录：`{RUN_ID_THIS}`\n", encoding="utf-8")
    assert _run_db_delivery(module, db, evidence) == 0
    observation = json.loads(
        (evidence / "delivery-observation.json").read_text(encoding="utf-8")
    )
    assert observation["delivered_at_epoch"] == updated / 1000
    basis = json.loads(
        (evidence / "delivery-observation-basis.json").read_text(
            encoding="utf-8")
    )
    assert basis["time_caliber"] == (
        "message completion (max of matched text part end and "
        "client-recorded message time.completed)"
    )


def test_db_delivery_presentation_without_any_time_fails_closed(
        tmp_path, capsys):
    """时间字段缺失（文本无 time 块且 time_created==time_update 无推进）
    时不能证明完成时刻，fail-closed 保留缺口。"""
    module = load_module()
    t0 = int(module["_iso_to_epoch"]("2026-09-14T00:00:00Z") * 1000)
    message_rows = [_msg_row("msg_final", created_ms=t0 + 30_000)]
    part_rows = [
        _text_part("p_final", "msg_final", "sess_x",
                   "最终报告：/research/" + RUN_ID_THIS + "/report.md",
                   t0 + 30_000),
    ]
    db = _make_zcode_db(tmp_path, rows=[_db_row(request="req_1")],
                        message_rows=message_rows, part_rows=part_rows)
    evidence = tmp_path / "ev"
    evidence.mkdir()
    (evidence / "delivery-message.md").write_text(
        f"# 交付\n\n- 研究目录：`{RUN_ID_THIS}`\n", encoding="utf-8")
    code = _run_db_delivery(module, db, evidence)
    assert code == 1
    err = capsys.readouterr().err
    assert "completion time is not provable" in err
    assert not (evidence / "delivery-observation.json").exists()


def test_db_delivery_streaming_update_without_completion_fails_closed(
        tmp_path, capsys):
    """仅流式推进的 time_updated 不是完成证据：消息为用户可见 assistant、
    正文含本次最终报告定位，但文本无 time.end、消息 data 无 time.completed
    时，完成时刻不可证，fail-closed 保留缺口（不得以更新时刻冒充完成
    时刻）。"""
    module = load_module()
    t0 = int(module["_iso_to_epoch"]("2026-09-14T00:00:00Z") * 1000)
    message_rows = [
        _msg_row("msg_stream", created_ms=t0 + 30_000,
                 updated_ms=t0 + 45_000),
    ]
    part_rows = [
        _text_part("p_stream", "msg_stream", "sess_x",
                   "最终报告：/research/" + RUN_ID_THIS
                   + "/report.md（已定稿）",
                   t0 + 30_000),
    ]
    db = _make_zcode_db(tmp_path, rows=[_db_row(request="req_1")],
                        message_rows=message_rows, part_rows=part_rows)
    evidence = tmp_path / "ev"
    evidence.mkdir()
    (evidence / "delivery-message.md").write_text(
        f"# 交付\n\n- 研究目录：`{RUN_ID_THIS}`\n", encoding="utf-8")
    code = _run_db_delivery(module, db, evidence)
    assert code == 1
    err = capsys.readouterr().err
    assert "completion time is not provable" in err
    assert not (evidence / "delivery-observation.json").exists()


def test_db_delivery_draft_markers_before_claim_are_excluded(
        tmp_path, capsys):
    """问题 3 复现：交付声明**前方**的草稿标记（"这是初稿，待独立核对；
    最终报告路径……"）同样判草稿；只有该候选时 fail-closed。"""
    module = load_module()
    t0 = int(module["_iso_to_epoch"]("2026-09-14T00:00:00Z") * 1000)
    message_rows = [_msg_row("msg_draft_before", created_ms=t0 + 10_000)]
    part_rows = [
        _text_part("p_draft_before", "msg_draft_before", "sess_x",
                   "这是初稿，待独立核对；最终报告路径 "
                   f"/research/{RUN_ID_THIS}/report.md（此即最终报告）",
                   t0 + 10_000),
    ]
    db = _make_zcode_db(tmp_path, rows=[_db_row(request="req_1")],
                        message_rows=message_rows, part_rows=part_rows)
    evidence = tmp_path / "ev"
    evidence.mkdir()
    (evidence / "delivery-message.md").write_text(
        f"# 交付\n\n- 研究目录：`{RUN_ID_THIS}`\n", encoding="utf-8")
    code = _run_db_delivery(module, db, evidence)
    assert code == 1
    err = capsys.readouterr().err
    assert "draft" in err
    assert not (evidence / "delivery-observation.json").exists()


def test_db_delivery_endpoint_stays_at_presentation_when_calls_continue(
        tmp_path):
    """交付呈现之后仍有锁定/评分调用时，端点保持在呈现事件（S6 复现：
    定稿呈现后还有 marker/锁定/评分派发与 sleep 轮询回合）。"""
    module = load_module()
    t0 = int(module["_iso_to_epoch"]("2026-09-14T00:00:00Z") * 1000)
    rows = [
        _db_row(request="req_score", started=t0 + 40_000,
                completed=t0 + 90_000),
        _db_row(request="req_sleep", started=t0 + 90_000,
                completed=t0 + 120_000),
    ]
    tool_rows = [
        ("sess_x", "turn_1", "call_lock", "Bash", t0 + 45_000),
        ("sess_x", "turn_1", "call_score", "Agent", t0 + 50_000),
        ("sess_x", "turn_1", "call_poll", "Bash", t0 + 95_000),
    ]
    message_rows = [_msg_row("msg_final", created_ms=t0 + 30_000,
                             updated_ms=t0 + 34_100,
                             completed_ms=t0 + 34_100)]
    part_rows = [
        _text_part("p_final", "msg_final", "sess_x",
                   "研究定稿。报告路径 同目录 report.md（此即最终报告，"
                   f"已按独立核验意见修正定稿）；目录 `{RUN_ID_THIS}`",
                   t0 + 30_000, end_ms=t0 + 34_000),
    ]
    db = _make_zcode_db(tmp_path, rows=rows, tool_rows=tool_rows,
                        message_rows=message_rows, part_rows=part_rows)
    evidence = tmp_path / "ev"
    evidence.mkdir()
    (evidence / "delivery-message.md").write_text(
        f"# 交付\n\n- 研究目录：`{RUN_ID_THIS}`\n", encoding="utf-8")
    assert _run_db_delivery(module, db, evidence) == 0
    observation = json.loads(
        (evidence / "delivery-observation.json").read_text(encoding="utf-8")
    )
    assert observation["delivery_request_id"] == "msg_final"
    assert observation["delivered_at_epoch"] == (t0 + 34_100) / 1000


def test_db_delivery_only_draft_presentation_fails_closed(tmp_path, capsys):
    """只有核验前初稿呈现（claim 附近带待核验/初稿标记）时 fail-closed。"""
    module = load_module()
    t0 = int(module["_iso_to_epoch"]("2026-09-14T00:00:00Z") * 1000)
    rows = [_db_row(request="req_1", started=t0 + 1_000, completed=t0 + 5_000)]
    message_rows = [_msg_row("msg_draft", created_ms=t0 + 10_000)]
    part_rows = [
        _text_part("p_draft", "msg_draft", "sess_x",
                   "研究完成 — 最终交付。报告路径 report.md（此即最终报告）；"
                   "定稿（初稿）时间…W10 置\"待核验\"", t0 + 10_000),
    ]
    db = _make_zcode_db(tmp_path, rows=rows, message_rows=message_rows,
                        part_rows=part_rows)
    evidence = tmp_path / "ev"
    evidence.mkdir()
    (evidence / "delivery-message.md").write_text(
        f"# 交付\n\n- 研究目录：`{RUN_ID_THIS}`\n", encoding="utf-8")
    code = _run_db_delivery(module, db, evidence)
    assert code == 1
    err = capsys.readouterr().err
    assert "final-report presentation" in err
    assert "draft" in err
    assert not (evidence / "delivery-observation.json").exists()


def test_db_delivery_no_presentation_in_window_fails_closed(tmp_path, capsys):
    """窗口内没有任何最终报告呈现文本时 fail-closed。"""
    module = load_module()
    t0 = int(module["_iso_to_epoch"]("2026-09-14T00:00:00Z") * 1000)
    db = _make_zcode_db(tmp_path, rows=[_db_row(started=t0 - 60_000)])
    evidence = tmp_path / "ev"
    evidence.mkdir()
    marker = evidence / "delivery-message.md"
    marker.write_text(
        f"# 交付\n\n- 研究目录：`{RUN_ID_THIS}`\n", encoding="utf-8")
    os.utime(marker, (t0 / 1000 + 1, t0 / 1000 + 1))
    code = _run_db_delivery(module, db, evidence)
    assert code == 1
    assert "final-report presentation" in capsys.readouterr().err
    assert not (evidence / "delivery-observation.json").exists()


# ---------------------------------------------------------------------------
# S-A: cumulative snapshots compress to one consumption per counting range


def test_sa_cumulative_snapshots_compress_to_segment_end():
    summarize = load_module()["summarize_usage"]
    first = message_event(kind="cumulative", message_id=None, sequence=1,
                          input_tokens=10, output_tokens=1)
    second = message_event(kind="cumulative", message_id=None, sequence=2,
                           input_tokens=30, output_tokens=3)
    result = summarize([first, second], expected_scopes={"research"})
    assert result["input_tokens"] == 30
    assert result["output_tokens"] == 3
    assert result["complete"] is True
    assert result["gaps"] == []


def test_sa_cumulative_start_snapshot_is_baseline_not_extra_consumption():
    summarize = load_module()["summarize_usage"]
    baseline = message_event(kind="cumulative", message_id=None, sequence=1,
                             input_tokens=10, output_tokens=1, snapshot="start")
    final = message_event(kind="cumulative", message_id=None, sequence=2,
                          input_tokens=30, output_tokens=3)
    result = summarize([baseline, final], expected_scopes={"research"})
    # the start snapshot was consumed before this range; only the difference
    # belongs to the range, never baseline + final
    assert result["input_tokens"] == 20
    assert result["output_tokens"] == 2
    assert result["complete"] is True


def test_sa_recovery_segments_each_compress_then_sum():
    summarize = load_module()["summarize_usage"]
    events = [
        message_event(kind="cumulative", message_id=None, segment_id="seg-a",
                      sequence=1, input_tokens=10, output_tokens=1),
        message_event(kind="cumulative", message_id=None, segment_id="seg-a",
                      sequence=2, input_tokens=30, output_tokens=3),
        # after recovery the client restarts the counter in a new segment
        message_event(kind="cumulative", message_id=None, segment_id="seg-b",
                      sequence=1, input_tokens=3, output_tokens=0),
        message_event(kind="cumulative", message_id=None, segment_id="seg-b",
                      sequence=2, input_tokens=8, output_tokens=4),
    ]
    result = summarize(events, expected_scopes={"research"})
    assert result["input_tokens"] == 38
    assert result["output_tokens"] == 7
    assert result["complete"] is True


def test_sa_cumulative_missing_sequence_cannot_be_ordered():
    summarize = load_module()["summarize_usage"]
    first = message_event(kind="cumulative", message_id=None, sequence=1,
                          input_tokens=10, output_tokens=1)
    unordered = message_event(kind="cumulative", message_id=None,
                              input_tokens=20, output_tokens=2)
    result = summarize([first, unordered], expected_scopes={"research"})
    assert result["input_tokens"] is None
    assert result["complete"] is False
    assert any(g["type"] == "cumulative_sequence_unknown" for g in result["gaps"])


# ---------------------------------------------------------------------------
# S-B: unknown cache semantics are never normalized; completeness is per metric


def test_sb_unknown_cache_semantics_is_never_treated_as_exclusive():
    summarize = load_module()["summarize_usage"]
    event = message_event(input_tokens=100, output_tokens=7,
                          cached_input_read=80, cached_input_write=0,
                          input_includes_cache=None)
    result = summarize([event], expected_scopes={"research"})
    # 180 would silently assume the input excluded the cached 80
    assert result["input_tokens"] is None
    assert result["input_tokens_basis"] is None
    assert result["input_tokens_as_reported"]["research"]["input_tokens"] == 100
    assert result["output_tokens"] == 7
    assert result["complete"] is False
    assert any(g["type"] == "cache_cannot_normalize" for g in result["cache_gaps"])


def test_sb_mixed_known_and_unknown_semantics_cannot_normalize():
    summarize = load_module()["summarize_usage"]
    known_inclusive = message_event(scope="research", message_id="r1",
                                    input_tokens=100, output_tokens=5,
                                    cached_input_read=10, cached_input_write=0,
                                    input_includes_cache=True)
    unknown_exclusive = message_event(scope="review", message_id="v1",
                                      input_tokens=40, output_tokens=4,
                                      cached_input_read=None,
                                      cached_input_write=None,
                                      input_includes_cache=False)
    result = summarize([known_inclusive, unknown_exclusive],
                       expected_scopes={"research", "review"})
    assert result["input_tokens"] is None
    assert result["input_tokens_basis"] is None
    assert result["output_tokens"] == 9
    assert result["complete"] is False
    assert any(g["type"] == "cache_cannot_normalize" for g in result["cache_gaps"])


def test_sb_contradictory_semantics_on_same_identity_block_the_range():
    summarize = load_module()["summarize_usage"]
    base = message_event(message_id="m1", input_includes_cache=True)
    conflict = message_event(message_id="m1", input_includes_cache=False,
                             captured_at="later")
    result = summarize([base, conflict], expected_scopes={"research"})
    assert result["input_tokens"] is None
    assert result["complete"] is False
    assert any(g["type"] == "cache_semantics_conflict" for g in result["gaps"])


# ---------------------------------------------------------------------------
# SW: the delivery turn is observable only after `run` exits; a follow-up
# `sweep` closes the request->delivery arc and `check` refuses anything less


def _iso(epoch: float) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(epoch, tz=UTC).isoformat()


def _pending_meta(**overrides: object) -> dict:
    meta = _base_meta(
        probe_mode=False,
        expected_scopes=["research", "review", "coordination"],
        request_at=1000.0,
        delivered_at=None,
        delivery_source="pending-sweep",
        delivery_marker="delivery-message.md",
        delivery_session="sess_c",
        delivery_scope="coordination",
        timing_intervals=[
            {"category": "research", "start": 1000.0, "end": 1050.0, "clock": "wall"},
            {"category": "review", "start": 1010.0, "end": 1060.0, "clock": "wall"},
        ],
    )
    meta.update(overrides)
    return meta


def _delivery_observation(**overrides: object) -> dict:
    observation = {
        "source": "independently-observed",
        "session_id": "sess_c",
        "scope": "coordination",
        "marker": "delivery-message.md",
        "marker_mtime_epoch": 1090.0,
        "delivered_at_epoch": 1100.0,
        "delivered_at_iso": _iso(1100.0),
        "delivery_request_id": "rq-delivery",
        "events_appended": 1,
        "swept_at": _iso(1200.0),
    }
    observation.update(overrides)
    return observation


def _write_pending_evidence(directory: Path, delivered_at_epoch: float = 1100.0):
    events = [
        _good_research_event(),
        _good_research_event(scope="review", session_id="s1", message_id="v1"),
        _good_research_event(scope="coordination", session_id="sess_c",
                             message_id="rq-delivery"),
    ]
    _write_evidence(directory, events, _pending_meta())
    marker = directory / "delivery-message.md"
    marker.write_text("delivery message", encoding="utf-8")
    import os

    os.utime(marker, (delivered_at_epoch - 10, delivered_at_epoch - 10))
    return marker


def test_sw_check_pending_sweep_without_observation_fails(tmp_path):
    evidence = tmp_path / "ev"
    events = [_good_research_event(), _good_research_event(scope="review",
                                                           message_id="v1")]
    _write_evidence(evidence, events, _pending_meta())
    (evidence / "delivery-message.md").write_text("delivery", encoding="utf-8")
    result_path = tmp_path / "result.json"
    code = load_module()["main"](
        ["check", "--evidence", str(evidence), "--output", str(result_path)]
    )
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert code == 1
    assert payload["passed"] is False
    assert payload["baseline_qualification"] == "not_established"
    assert any("sweep" in failure for failure in payload["failures"])


def test_sw_check_resolves_observed_delivery_and_establishes_baseline(tmp_path):
    evidence = tmp_path / "ev"
    _write_pending_evidence(evidence)
    _isolation_evidence(evidence)
    (evidence / "delivery-observation.json").write_text(
        json.dumps(_delivery_observation()), encoding="utf-8"
    )
    result_path = tmp_path / "result.json"
    code = load_module()["main"](
        ["check", "--evidence", str(evidence), "--output", str(result_path)]
    )
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert code == 0, payload["failures"]
    assert payload["passed"] is True
    assert payload["baseline_qualification"] == "established"
    assert payload["timing"]["complete"] is True
    # the coordination interval now spans request -> observed delivery
    assert payload["timing"]["total_wait"] == pytest.approx(100.0)
    assert payload["timing"]["uncovered"] == pytest.approx(0.0)


def test_sw_marker_modified_after_endpoint_cannot_observe_delivery(tmp_path):
    evidence = tmp_path / "ev"
    _write_pending_evidence(evidence)
    import os

    marker = evidence / "delivery-message.md"
    os.utime(marker, (1200.0, 1200.0))
    (evidence / "delivery-observation.json").write_text(
        json.dumps(_delivery_observation()), encoding="utf-8"
    )
    result_path = tmp_path / "result.json"
    code = load_module()["main"](
        ["check", "--evidence", str(evidence), "--output", str(result_path)]
    )
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert code == 1
    assert any("marker" in failure for failure in payload["failures"])


def test_sw_observation_from_another_session_fails(tmp_path):
    evidence = tmp_path / "ev"
    _write_pending_evidence(evidence)
    (evidence / "delivery-observation.json").write_text(
        json.dumps(_delivery_observation(session_id="sess_other")), encoding="utf-8"
    )
    result_path = tmp_path / "result.json"
    code = load_module()["main"](
        ["check", "--evidence", str(evidence), "--output", str(result_path)]
    )
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert code == 1
    assert any("session" in failure for failure in payload["failures"])


def _fake_rollout(home: Path, session: str, records: list[dict]) -> Path:
    rollout = home / ".zcode" / "cli" / "rollout"
    rollout.mkdir(parents=True, exist_ok=True)
    transcript = rollout / f"model-io-{session}.jsonl"
    transcript.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    return transcript


def _rollout_record(request_id: str, start: float, end: float) -> dict:
    return {
        "requestId": request_id,
        "startedAt": _iso(start),
        "completedAt": _iso(end),
        "sessionId": "sess_c",
        "type": "model_io",
        "model": {"modelId": "GLM-5.3-Flash"},
        "response": {
            "usage": {"inputTokens": 50, "outputTokens": 5,
                      "cacheReadTokens": 40, "cacheWriteTokens": 0},
            "toolCalls": [],
        },
    }


def test_sw_sweep_captures_delivery_turn_from_rollout(tmp_path, monkeypatch):
    base = 1_700_000_000.0
    home = tmp_path / "home"
    home.mkdir()
    _fake_rollout(home, "sess_c", [
        _rollout_record("rq1", base + 10, base + 30),
        _rollout_record("rq-delivery", base + 140, base + 160),
        _rollout_record("rq-post", base + 200, base + 210),
    ])
    monkeypatch.setattr(Path, "home", lambda: home)
    evidence = tmp_path / "ev"
    evidence.mkdir()
    (evidence / "case-record.json").write_text("{}", encoding="utf-8")
    _write_evidence(evidence, [], _pending_meta(request_at=base))
    marker = evidence / "delivery-message.md"
    marker.write_text("delivery", encoding="utf-8")
    import os

    os.utime(marker, (base + 150, base + 150))
    module = load_module()
    result = module["main"](
        ["sweep", "--evidence", str(evidence), "--session", "sess_c"]
    )
    assert result == 0
    observation = json.loads(
        (evidence / "delivery-observation.json").read_text(encoding="utf-8")
    )
    assert observation["source"] == "independently-observed"
    assert observation["delivered_at_epoch"] == base + 210
    assert observation["delivery_request_id"] == "rq-post"
    assert observation["marker_mtime_epoch"] == base + 150
    events = [json.loads(line)
              for line in (evidence / "usage-events.jsonl").read_text(
                  encoding="utf-8").splitlines() if line]
    # the whole tail is consumed — the conservative direction, so a late
    # sweep can only overcount and never silently drop the delivery turn
    assert len(events) == 3
    assert sorted(event["message_id"] for event in events) == [
        "rq-delivery", "rq-post", "rq1",
    ]
    assert all(event["scope"] == "coordination" for event in events)
    # a stamp is final: a second sweep must never move the endpoint
    assert module["main"](
        ["sweep", "--evidence", str(evidence), "--session", "sess_c"]
    ) == 2


def test_sw_sweep_refuses_missing_marker_or_non_pending_evidence(tmp_path):
    module = load_module()
    evidence = tmp_path / "ev"
    evidence.mkdir()
    (evidence / "case-record.json").write_text("{}", encoding="utf-8")
    _write_evidence(evidence, [], _pending_meta())
    # marker missing
    assert module["main"](
        ["sweep", "--evidence", str(evidence), "--session", "sess_c"]
    ) == 2
    assert not (evidence / "delivery-observation.json").exists()
    # evidence not pending a sweep
    _write_evidence(tmp_path / "ev2", [], _base_meta())
    assert module["main"](
        ["sweep", "--evidence", str(tmp_path / "ev2"), "--session", "sess_c"]
    ) == 2


def test_sw_run_pending_mode_records_no_delivery_gap(tmp_path, monkeypatch):
    import time as time_module

    base = time_module.time()
    home = tmp_path / "home"
    agents_dir = home / ".zcode" / "cli" / "agents" / "sess_x"
    agents_dir.mkdir(parents=True)
    (agents_dir / "agent_a1x").mkdir()
    (agents_dir / "agent_a1x" / "metadata.json").write_text(json.dumps({
        "status": "completed",
        "createdAt": _iso(base + 5),
        "completedAt": _iso(base + 50),
        "childSessionId": "sess_child",
    }), encoding="utf-8")
    _fake_rollout(home, "sess_child", [_rollout_record("rq-r", base + 10, base + 40)])
    _fake_rollout(home, "sess_coord", [
        _rollout_record("rq-c1", base + 12, base + 42),
        _rollout_record("rq-c2", base + 44, base + 48),
    ])
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(time_module, "sleep", lambda *_: None)
    case = tmp_path / "case.json"
    case.write_text(json.dumps({
        "case_id": "CASE-PENDING",
        "security": "NONE",
        "neutral_request": "controlled run-shape probe",
        "as_of": "2026-09-09T00:00:00+08:00",
        "data_mode": "public",
        "depth": "probe",
        "reuse_previous_task_data": False,
        "probe_mode": True,
    }), encoding="utf-8")
    out = tmp_path / "obs"
    module = load_module()
    code = module["main"]([
        "run", "--host", "zcode", "--case", str(case), "--output", str(out),
        "--watch-agent", "agent_a1x=research",
        "--watch-agent", "sess_coord=coordination",
        "--delivery-sweep", "--watch-timeout", "30",
    ])
    assert code == 0
    meta = json.loads((out / "check-input.json").read_text(encoding="utf-8"))
    assert meta["delivery_source"] == "pending-sweep"
    assert meta["delivered_at"] is None
    assert meta["delivery_session"] == "sess_coord"
    assert meta["delivery_marker"] == "delivery-message.md"
    gaps_file = out / "collection-gaps.jsonl"
    recorded = (gaps_file.read_text(encoding="utf-8").splitlines()
                if gaps_file.exists() else [])
    assert not any("delivery_tail" in line for line in recorded)
    sweep_state = json.loads((out / "sweep-state.json").read_text(encoding="utf-8"))
    assert sweep_state["session_id"] == "sess_coord"
    assert sweep_state["state"]["offset"] > 0
    events = [json.loads(line)
              for line in (out / "usage-events.jsonl").read_text(
                  encoding="utf-8").splitlines() if line]
    assert {event["scope"] for event in events} == {"research", "coordination"}


def test_sw_run_sweep_requires_a_coordination_watcher(tmp_path):
    case = tmp_path / "case.json"
    case.write_text(json.dumps({
        "case_id": "CASE-NOCOORD",
        "security": "NONE",
        "neutral_request": "controlled run-shape probe",
        "as_of": "2026-09-09T00:00:00+08:00",
        "data_mode": "public",
        "depth": "probe",
        "reuse_previous_task_data": False,
        "probe_mode": True,
    }), encoding="utf-8")
    module = load_module()
    code = module["main"]([
        "run", "--host", "zcode", "--case", str(case),
        "--output", str(tmp_path / "obs"),
        "--watch-agent", "agent_a1x=research",
        "--delivery-sweep",
    ])
    assert code == 2


def test_sw_sweep_accepts_explicit_transcript_snapshot(tmp_path, monkeypatch):
    """Rotation can replace the live rollout between run and sweep; sweeping
    a pre-rotation snapshot must then work from offset 0 and rely on usage
    dedup, never on the stale sweep-state cursor."""
    import json as json_module

    base = 1_800_000_000.0
    home = tmp_path / "home"
    home.mkdir()
    # the live rollout is gone (rotated away): only the snapshot remains
    snapshot = tmp_path / "staging" / "model-io-sess_c.jsonl"
    snapshot.parent.mkdir()
    records = [
        _rollout_record("rq-old", base + 10, base + 30),
        _rollout_record("rq-delivery", base + 90, base + 110),
    ]
    snapshot.write_text(
        "".join(json_module.dumps(r) + "\n" for r in records), encoding="utf-8"
    )
    monkeypatch.setattr(Path, "home", lambda: home)
    evidence = tmp_path / "ev"
    evidence.mkdir()
    (evidence / "case-record.json").write_text("{}", encoding="utf-8")
    _write_evidence(evidence, [], _pending_meta(request_at=base))
    # a stale cursor from the pre-rotation live file must not gate the snapshot
    (evidence / "sweep-state.json").write_text(json.dumps({
        "session_id": "sess_c", "scope": "coordination",
        "state": {"identity": [123, 456], "offset": 999999, "generation": 0},
    }), encoding="utf-8")
    marker = evidence / "delivery-message.md"
    marker.write_text("delivery", encoding="utf-8")
    import os

    os.utime(marker, (base + 100, base + 100))
    module = load_module()
    code = module["main"]([
        "sweep", "--evidence", str(evidence), "--session", "sess_c",
        "--transcript", str(snapshot),
    ])
    assert code == 0
    observation = json.loads(
        (evidence / "delivery-observation.json").read_text(encoding="utf-8")
    )
    assert observation["delivered_at_epoch"] == base + 110
    events = [json.loads(line)
              for line in (evidence / "usage-events.jsonl").read_text(
                  encoding="utf-8").splitlines() if line]
    assert len(events) == 2


# ---------------------------------------------------------------------------
# --watch-control: runtime watcher registration and finalize lifecycle
# (2026-09-10 minimal fix; real dispatch order registers review/correction
# only after research completes, so their ids cannot be known at run start)


def _fake_agent(home, agent_id, child_session, records, *,
                status="completed", created=5.0, completed=50.0, base=None):
    import time as time_module

    if base is None:
        base = time_module.time() - 60
    agents_dir = home / ".zcode" / "cli" / "agents" / "sess_parent"
    agent_dir = agents_dir / f"agent_{agent_id}"
    agent_dir.mkdir(parents=True)
    (agent_dir / "metadata.json").write_text(json.dumps({
        "status": status,
        "createdAt": _iso(base + created),
        "completedAt": _iso(base + completed),
        "childSessionId": child_session,
    }), encoding="utf-8")
    return _fake_rollout(home, child_session, records)


def _wg_case(tmp_path, case_id, probe=True):
    case = tmp_path / f"case-{case_id}.json"
    case.write_text(json.dumps({
        "case_id": case_id,
        "security": "NONE",
        "neutral_request": "controlled watch-control probe",
        "as_of": "2026-09-10T00:00:00+08:00",
        "data_mode": "public",
        "depth": "probe",
        "reuse_previous_task_data": False,
        "probe_mode": probe,
    }), encoding="utf-8")
    return case


def test_wg_control_registers_late_watchers_into_one_directory(
    tmp_path, monkeypatch
):
    import time as time_module

    base = time_module.time() - 60
    home = tmp_path / "home"
    home.mkdir()
    _fake_agent(home, "a1", "sess_r", [
        _rollout_record("rq-r1", base + 10, base + 20),
        _rollout_record("rq-r2", base + 22, base + 30),
    ], base=base)
    _fake_agent(home, "v1", "sess_v", [
        _rollout_record("rq-v1", base + 35, base + 40),
        _rollout_record("rq-v2", base + 41, base + 45),
    ], base=base)
    _fake_agent(home, "x1", "sess_x", [
        _rollout_record("rq-x1", base + 46, base + 48),
    ], base=base)
    _fake_rollout(home, "sess_coord", [
        _rollout_record("rq-c1", base + 10, base + 30),
    ])
    control = tmp_path / "control.jsonl"
    control.write_text(
        '{"op": "watch", "id": "agent_v1", "scope": "review"}\n'
        '{"op": "watch", "id": "agent_x1", "scope": "correction"}\n'
        '{"op": "finalize"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(time_module, "sleep", lambda *_: None)
    out = tmp_path / "obs"
    module = load_module()
    code = module["main"]([
        "run", "--host", "zcode", "--case", str(_wg_case(tmp_path, "WG-REG")),
        "--output", str(out),
        "--watch-agent", "agent_a1=research",
        "--watch-agent", "sess_coord=coordination",
        "--delivery-sweep", "--watch-timeout", "30",
        "--watch-control", str(control),
        "--request-at", _iso(base),
    ])
    assert code == 0
    meta = json.loads((out / "check-input.json").read_text(encoding="utf-8"))
    assert meta["expected_scopes"] == [
        "coordination", "correction", "research", "review",
    ]
    events = [json.loads(line)
              for line in (out / "usage-events.jsonl").read_text(
                  encoding="utf-8").splitlines() if line]
    by_scope = {}
    for event in events:
        by_scope.setdefault(event["scope"], set()).add(event["message_id"])
    assert by_scope == {
        "research": {"rq-r1", "rq-r2"},
        "review": {"rq-v1", "rq-v2"},
        "correction": {"rq-x1"},
        "coordination": {"rq-c1"},
    }
    gaps = (out / "collection-gaps.jsonl")
    assert not gaps.exists() or not gaps.read_text(encoding="utf-8").strip()


def test_wg_control_rejections_are_recorded_as_gaps(tmp_path, monkeypatch):
    import time as time_module

    base = time_module.time() - 60
    home = tmp_path / "home"
    home.mkdir()
    _fake_agent(home, "a1", "sess_r", [
        _rollout_record("rq-r1", base + 10, base + 20),
    ], base=base)
    _fake_rollout(home, "sess_coord", [
        _rollout_record("rq-c1", base + 10, base + 30),
    ])
    control = tmp_path / "control.jsonl"
    control.write_text(
        # duplicate id under a different scope: misattribution risk
        '{"op": "watch", "id": "agent_a1", "scope": "review"}\n'
        # unknown op
        '{"op": "nope"}\n'
        # malformed scope
        '{"op": "watch", "id": "agent_v1", "scope": "Bad Scope"}\n'
        # second coordination watcher under --delivery-sweep
        '{"op": "watch", "id": "sess_other", "scope": "coordination"}\n'
        '{"op": "finalize"}\n'
        # registration after finalize is an ordering error
        '{"op": "watch", "id": "agent_v1", "scope": "review"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(time_module, "sleep", lambda *_: None)
    out = tmp_path / "obs"
    module = load_module()
    code = module["main"]([
        "run", "--host", "zcode", "--case", str(_wg_case(tmp_path, "WG-REJ")),
        "--output", str(out),
        "--watch-agent", "agent_a1=research",
        "--watch-agent", "sess_coord=coordination",
        "--delivery-sweep", "--watch-timeout", "30",
        "--watch-control", str(control),
    ])
    assert code == 0
    gaps = [json.loads(line) for line
            in (out / "collection-gaps.jsonl").read_text(
                encoding="utf-8").splitlines() if line]
    assert [gap["type"] for gap in gaps] == ["control_line_rejected"] * 5
    meta = json.loads((out / "check-input.json").read_text(encoding="utf-8"))
    assert meta["expected_scopes"] == ["coordination", "research"]


def test_wg_missing_finalize_before_timeout_is_a_gap(tmp_path, monkeypatch):
    import time as time_module

    home = tmp_path / "home"
    home.mkdir()
    _fake_rollout(home, "sess_coord", [])
    control = tmp_path / "control.jsonl"
    control.write_text("", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(time_module, "sleep", lambda *_: None)
    out = tmp_path / "obs"
    module = load_module()
    code = module["main"]([
        "run", "--host", "zcode", "--case", str(_wg_case(tmp_path, "WG-DL")),
        "--output", str(out),
        "--watch-agent", "sess_coord=coordination",
        "--delivery-sweep", "--watch-timeout", "0",
        "--watch-control", str(control),
    ])
    assert code == 0
    gaps = [json.loads(line) for line
            in (out / "collection-gaps.jsonl").read_text(
                encoding="utf-8").splitlines() if line]
    types = [gap["type"] for gap in gaps]
    assert types.count("watch_deadline_exceeded") == 2
    assert any("finalize" in gap["detail"] for gap in gaps)


def test_wg_run_waits_for_finalize_beyond_last_watcher(tmp_path, monkeypatch):
    """The lifecycle must not exit when every current watcher finished:
    late review/correction sessions and coordination records keep arriving
    until the coordinator declares delivery closeout."""
    import threading
    import time as time_module

    base = time_module.time() - 60
    home = tmp_path / "home"
    home.mkdir()
    _fake_agent(home, "a1", "sess_r", [
        _rollout_record("rq-r1", base + 10, base + 20),
    ], base=base)
    _fake_rollout(home, "sess_coord", [
        _rollout_record("rq-c1", base + 10, base + 30),
    ])
    control = tmp_path / "control.jsonl"
    control.write_text("", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: home)
    out = tmp_path / "obs"
    module = load_module()
    outcome: dict = {}

    def drive() -> None:
        outcome["code"] = module["main"]([
            "run", "--host", "zcode",
            "--case", str(_wg_case(tmp_path, "WG-WAIT")),
            "--output", str(out),
            "--watch-agent", "agent_a1=research",
            "--watch-agent", "sess_coord=coordination",
            "--delivery-sweep", "--watch-timeout", "40",
            "--watch-control", str(control),
            "--request-at", _iso(base),
        ])

    thread = threading.Thread(target=drive, daemon=True)
    thread.start()
    # the research agent finished long before this point; an old-lifecycle
    # run would already have exited
    time_module.sleep(8.5)
    assert thread.is_alive(), "run exited before finalize was declared"
    with open(control, "a", encoding="utf-8") as handle:
        handle.write('{"op": "finalize"}\n')
        handle.flush()
    thread.join(timeout=25)
    assert not thread.is_alive()
    assert outcome["code"] == 0
    gaps = out / "collection-gaps.jsonl"
    assert not gaps.exists() or not gaps.read_text(encoding="utf-8").strip()
    events = [json.loads(line)
              for line in (out / "usage-events.jsonl").read_text(
                  encoding="utf-8").splitlines() if line]
    assert {event["scope"] for event in events} == {"research", "coordination"}
    meta = json.loads((out / "check-input.json").read_text(encoding="utf-8"))
    assert meta["delivery_source"] == "pending-sweep"


def test_wg_control_full_chain_establishes_single_directory(
    tmp_path, monkeypatch
):
    """Offline acceptance shape: research done -> review joins -> correction
    joins -> finalize -> delivery turn -> sweep -> check established, all
    scopes in ONE evidence directory."""
    import os
    import time as time_module

    base = time_module.time() - 60
    home = tmp_path / "home"
    home.mkdir()
    _fake_agent(home, "a1", "sess_r", [
        _rollout_record("rq-r1", base + 10, base + 20),
    ], base=base)
    _fake_agent(home, "v1", "sess_v", [
        _rollout_record("rq-v1", base + 30, base + 35),
    ], base=base)
    _fake_agent(home, "x1", "sess_x", [
        _rollout_record("rq-x1", base + 40, base + 45),
    ], base=base)
    _fake_rollout(home, "sess_coord", [
        _rollout_record("rq-c1", base + 10, base + 30),
    ])
    control = tmp_path / "control.jsonl"
    control.write_text(
        '{"op": "watch", "id": "agent_v1", "scope": "review"}\n'
        '{"op": "watch", "id": "agent_x1", "scope": "correction"}\n'
        '{"op": "finalize"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(time_module, "sleep", lambda *_: None)
    out = tmp_path / "obs"
    out.mkdir()
    _isolation_evidence(
        out, task_identity="zcode-task:WG-CHAIN",
        covered_sessions=["sess_r", "sess_v", "sess_x"],
    )
    (out / "isolation-evidence.json").replace(out / "evidence.json")
    module = load_module()
    code = module["main"]([
        "run", "--host", "zcode",
        "--case", str(_wg_case(tmp_path, "WG-CHAIN", probe=False)),
        "--output", str(out),
        "--watch-agent", "agent_a1=research",
        "--watch-agent", "sess_coord=coordination",
        "--delivery-sweep", "--watch-timeout", "30",
        "--watch-control", str(control),
        "--request-at", _iso(base),
        "--isolation-evidence", "evidence.json",
    ])
    assert code == 0

    # the delivery turn happens after the run exited, then sweep stamps it
    coordination = (home / ".zcode" / "cli" / "rollout"
                    / "model-io-sess_coord.jsonl")
    now = time_module.time()
    with open(coordination, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(_rollout_record(
            "rq-delivery", now, now + 5)) + "\n")
    marker = out / "delivery-message.md"
    marker.write_text("delivery", encoding="utf-8")
    os.utime(marker, (now, now))
    assert module["main"]([
        "sweep", "--evidence", str(out), "--session", "sess_coord",
    ]) == 0

    result_path = tmp_path / "result.json"
    code = module["main"]([
        "check", "--evidence", str(out), "--output", str(result_path),
    ])
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert code == 0, payload["failures"]
    assert payload["passed"] is True
    assert payload["baseline_qualification"] == "established"
    assert payload["usage"]["scopes"].keys() == {
        "research", "review", "correction", "coordination",
    }
    assert payload["usage"]["scopes"]["coordination"]["events"] == 2


# ---------------------------------------------------------------------------
# --resume / watch-state persistence / native session ids / status
# (2026-09-10 infrastructure repair: a killed collector must bound its loss
# to the dead window and be resumable; file-scope and agent-scope watchers
# must report the same session-id form as the isolation scan)


def _wh_records(path, count, prefix="rq-r"):
    import time as time_module

    base = time_module.time() - 60
    path.write_text(
        "".join(json.dumps(_rollout_record(f"{prefix}-{n}",
                                           base + n, base + n + 1)) + "\n"
                for n in range(1, count + 1)),
        encoding="utf-8",
    )
    return base


def test_wh_resume_continues_from_saved_cursor(tmp_path, monkeypatch):
    import os
    import time as time_module

    monkeypatch.setattr(time_module, "sleep", lambda *_: None)
    transcript = tmp_path / "model-io-sess_r.jsonl"
    base = _wh_records(transcript, 4)
    head = transcript.read_text(encoding="utf-8").splitlines(keepends=True)
    consumed_bytes = sum(len(line.encode()) for line in head[:2])
    stat = os.stat(transcript)
    obs = tmp_path / "obs"
    obs.mkdir()
    case = tmp_path / "case.json"
    case.write_text(json.dumps({
        "case_id": "WH-RES", "security": "NONE",
        "neutral_request": "resume probe", "as_of": "2026-09-10T00:00:00+08:00",
        "data_mode": "public", "depth": "probe",
        "reuse_previous_task_data": False, "probe_mode": True,
    }), encoding="utf-8")
    (obs / "case-record.json").write_text("{}", encoding="utf-8")
    (obs / "watch-state.json").write_text(json.dumps({
        "watchers": [{
            "native_id": f"file:{transcript}", "scope": "research",
            "kind": "file", "metadata_path": None, "session_id": "sess_r",
            "transcript": str(transcript),
            "state": {"identity": [stat.st_dev, stat.st_ino],
                      "offset": consumed_bytes, "generation": 0},
            "done": False, "started_epoch": None, "ended_epoch": None,
        }],
        "control": None, "request_at": base,
        "saved_at": "2026-09-10T00:00:00+00:00",
    }), encoding="utf-8")
    module = load_module()
    code = module["main"]([
        "run", "--host", "zcode", "--case", str(case), "--output", str(obs),
        "--resume", "--watch-timeout", "5",
    ])
    assert code == 0
    events = [json.loads(line)
              for line in (obs / "usage-events.jsonl").read_text(
                  encoding="utf-8").splitlines() if line]
    # only the two unconsumed records are appended — no duplicates
    assert [event["message_id"] for event in events] == ["rq-r-3", "rq-r-4"]
    assert (obs / "check-input.json").exists()
    # the only expected gap is the by-design unobserved delivery tail
    gaps = [json.loads(line) for line
            in (obs / "collection-gaps.jsonl").read_text(
                encoding="utf-8").splitlines() if line]
    assert [gap["type"] for gap in gaps] == ["delivery_tail_unobserved"]


def test_wh_resume_detects_truncation_and_reconsumes(tmp_path, monkeypatch):
    import os
    import time as time_module

    monkeypatch.setattr(time_module, "sleep", lambda *_: None)
    transcript = tmp_path / "model-io-sess_t.jsonl"
    _wh_records(transcript, 3, prefix="rq-t")
    stat = os.stat(transcript)
    obs = tmp_path / "obs"
    obs.mkdir()
    case = tmp_path / "case.json"
    case.write_text(json.dumps({
        "case_id": "WH-TRUNC", "security": "NONE",
        "neutral_request": "resume probe", "as_of": "2026-09-10T00:00:00+08:00",
        "data_mode": "public", "depth": "probe",
        "reuse_previous_task_data": False, "probe_mode": True,
    }), encoding="utf-8")
    (obs / "case-record.json").write_text("{}", encoding="utf-8")
    (obs / "watch-state.json").write_text(json.dumps({
        "watchers": [{
            "native_id": f"file:{transcript}", "scope": "research",
            "kind": "file", "metadata_path": None, "session_id": "sess_t",
            "transcript": str(transcript),
            "state": {"identity": [stat.st_dev, stat.st_ino],
                      "offset": stat.st_size + 500, "generation": 0},
            "done": False, "started_epoch": None, "ended_epoch": None,
        }],
        "control": None, "request_at": 1000.0,
        "saved_at": "2026-09-10T00:00:00+00:00",
    }), encoding="utf-8")
    module = load_module()
    code = module["main"]([
        "run", "--host", "zcode", "--case", str(case), "--output", str(obs),
        "--resume", "--watch-timeout", "5",
    ])
    assert code == 0
    gaps = [json.loads(line) for line
            in (obs / "collection-gaps.jsonl").read_text(
                encoding="utf-8").splitlines() if line]
    assert [gap["type"] for gap in gaps] == [
        "transcript_truncated", "delivery_tail_unobserved"]
    events = [json.loads(line)
              for line in (obs / "usage-events.jsonl").read_text(
                  encoding="utf-8").splitlines() if line]
    assert len(events) == 3


def test_wh_file_and_agent_watchers_share_native_session_ids(
    tmp_path, monkeypatch
):
    """The isolation scan and every watcher kind must report the same id
    form, or check-side coverage comparisons can never match."""
    import time as time_module

    base = time_module.time() - 60
    home = tmp_path / "home"
    home.mkdir()
    _fake_agent(home, "a1", "sess_r", [
        _rollout_record("rq-r1", base + 10, base + 20),
    ], base=base)
    snapshot_dir = tmp_path / "staging"
    snapshot_dir.mkdir()
    snapshot = snapshot_dir / "model-io-sess_subagent_agent_a1.jsonl"
    snapshot.write_text(
        "".join(json.dumps(r) + "\n" for r in [
            _rollout_record("rq-r1", base + 10, base + 20)]),
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(time_module, "sleep", lambda *_: None)
    module = load_module()
    out = tmp_path / "obs"
    module["main"]([
        "run", "--host", "zcode", "--case", str(_wg_case(tmp_path, "WH-IDS")),
        "--output", str(out),
        "--watch-agent", f"file:{snapshot}=research",
        "--watch-agent", "agent_a1=review",
        "--watch-timeout", "10", "--request-at", _iso(base),
    ])
    events = [json.loads(line)
              for line in (out / "usage-events.jsonl").read_text(
                  encoding="utf-8").splitlines() if line]
    ids = {event["scope"]: event["session_id"] for event in events}
    # the file-scope watcher reports the stripped native id of its snapshot
    # and the agent-scope watcher the metadata childSessionId — both without
    # the model-io- prefix, so coverage comparisons can match either kind
    assert ids == {"research": "sess_subagent_agent_a1", "review": "sess_r"}
    probe_out = tmp_path / "iso"
    module["main"]([
        "probe", "--host", "zcode", "--output", str(probe_out), "--isolation",
        "--agent", "agent_a1", "--sanity-agent", "agent_a1",
        "--task-identity", "zcode-task:WH-IDS",
        "--decoy-marker", "M", "--decoy-path", "/nope/marker.txt",
        "--allowed-path", str(tmp_path), "--staging-dir", str(snapshot_dir),
    ])
    covered = json.loads(
        (probe_out / "isolation-evidence-zcode.json").read_text(
            encoding="utf-8"))["covered_sessions"]
    assert covered == ["sess_subagent_agent_a1"]
    # the isolation scan and the file-scope watcher agree on the id form
    assert covered[0] == ids["research"]


# ---------------------------------------------------------------------------
# 07.1a: 统一验收入口 —— check 永不调用 run；--host-evidence 在目录缺失、
# 记录缺失或缺宿主/授权/必要工具记录时非零退出并打印“未执行／不支持”，
# skip 绝不算通过；check.sh 默认完全离线。


def test_071a_check_never_invokes_run(tmp_path):
    module = load_module()

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("check path must never call run")

    # runpy 每次返回独立命名空间，直接替换即可，无需恢复
    module["_cmd_run"] = _boom
    evidence = tmp_path / "ev"
    _write_evidence(evidence, [_good_research_event()], _base_meta())
    _isolation_evidence(evidence)
    code = module["main"](
        ["check", "--evidence", str(evidence), "--output", str(tmp_path / "r1.json")]
    )
    assert code == 0
    _write_host_support(evidence, _host_support_record())
    code = module["main"](
        ["check", "--host-evidence", str(evidence),
         "--output", str(tmp_path / "r2.json")]
    )
    assert code == 0


def _host_support_record(**overrides: object) -> dict:
    record = {
        "host": "zcode",
        "authorization": {
            "status": "granted",
            "granted_by": "local-acceptance",
            "granted_at": "2026-09-12T00:00:00+08:00",
        },
        "required_tools": ["Read", "Bash"],
        "recorded_at": "2026-09-12T00:00:00+08:00",
    }
    record.update(overrides)
    return record


def _write_host_support(directory: Path, record: dict) -> Path:
    path = directory / "host-support-record.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def test_071a_host_evidence_missing_directory_is_unexecuted(tmp_path, capsys):
    module = load_module()
    code = module["main"](["check", "--host-evidence", str(tmp_path / "absent")])
    assert code != 0
    assert "未执行" in capsys.readouterr().err


def test_071a_host_evidence_without_support_record_is_unsupported(tmp_path, capsys):
    module = load_module()
    evidence = tmp_path / "ev"
    _write_evidence(evidence, [_good_research_event()], _base_meta())
    _isolation_evidence(evidence)
    result_path = tmp_path / "result.json"
    code = module["main"](
        ["check", "--host-evidence", str(evidence), "--output", str(result_path)]
    )
    assert code != 0
    assert "不支持" in capsys.readouterr().err
    # 缺记录同样写入新的检查结果，不把 skip 算通过
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["passed"] is False
    assert any("host-support-record" in failure for failure in payload["failures"])
    assert "未执行" in payload["conclusion"] or "不支持" in payload["conclusion"]


@pytest.mark.parametrize(
    ("remove", "override"),
    [
        ("host", None),
        ("authorization", None),
        ("required_tools", None),
        ("host", "not-a-host"),
        ("authorization", {"status": "denied"}),
        ("authorization", ""),
        ("required_tools", []),
        ("required_tools", ["Read", 7]),
        (None, {"api_key": "sk-should-not-be-here"}),
    ],
    ids=(
        "missing-host",
        "missing-authorization",
        "missing-required-tools",
        "unknown-host",
        "authorization-denied",
        "authorization-empty",
        "required-tools-empty",
        "required-tools-non-string",
        "non-whitelisted-field",
    ),
)
def test_071a_host_evidence_invalid_support_record_is_unsupported(
    tmp_path, capsys, remove, override
):
    module = load_module()
    evidence = tmp_path / "ev"
    _write_evidence(evidence, [_good_research_event()], _base_meta())
    _isolation_evidence(evidence)
    record = _host_support_record()
    if remove:
        record.pop(remove)
    if override is not None:
        record.update(override if isinstance(override, dict) else {remove: override})
    _write_host_support(evidence, record)
    code = module["main"](
        ["check", "--host-evidence", str(evidence),
         "--output", str(tmp_path / "result.json")]
    )
    assert code != 0
    assert "不支持" in capsys.readouterr().err


def test_071a_host_evidence_with_support_record_passes_the_same_check(tmp_path):
    module = load_module()
    evidence = tmp_path / "ev"
    _write_evidence(evidence, [_good_research_event()], _base_meta())
    _isolation_evidence(evidence)
    _write_host_support(evidence, _host_support_record())
    result_path = tmp_path / "result.json"
    code = module["main"](
        ["check", "--host-evidence", str(evidence), "--output", str(result_path)]
    )
    assert code == 0
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["passed"] is True
    assert payload["baseline_qualification"] == "not_applicable_probe"
    assert payload["host_support"]["host"] == "zcode"
    assert payload["host_support"]["required_tools"] == ["Read", "Bash"]
    # 显式证据检查通过也不提升为宿主认证
    assert "not a baseline qualification" in payload["conclusion"]


def test_071a_host_evidence_default_output_is_create_only(tmp_path):
    module = load_module()
    evidence = tmp_path / "ev"
    _write_evidence(evidence, [_good_research_event()], _base_meta())
    _isolation_evidence(evidence)
    _write_host_support(evidence, _host_support_record())
    code = module["main"](["check", "--host-evidence", str(evidence)])
    assert code == 0
    default_output = evidence / "host-support-check.json"
    assert default_output.exists()
    # create-only：重跑必须失败而不是覆盖既有结果
    code = module["main"](["check", "--host-evidence", str(evidence)])
    assert code != 0


def test_071a_check_requires_exactly_one_evidence_selector(tmp_path, capsys):
    module = load_module()
    assert module["main"](["check"]) == 2
    evidence = tmp_path / "ev"
    _write_evidence(evidence, [_good_research_event()], _base_meta())
    _isolation_evidence(evidence)
    code = module["main"]([
        "check", "--evidence", str(evidence), "--host-evidence", str(evidence),
        "--output", str(tmp_path / "result.json"),
    ])
    assert code == 2
    assert capsys.readouterr().err


def _write_logging_stub(tmp_path: Path, log: Path) -> Path:
    import sys

    stub = tmp_path / "stub-python"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'printf \'%s\\n\' "$@" >> "{log}"\n'
        f'case "$*" in *host_acceptance.py*) exec "{sys.executable}" "$@" ;; '
        "*) exit 0 ;; esac\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return stub


def _run_check_sh(env_extra: dict) -> subprocess.CompletedProcess:
    import os

    env = dict(os.environ)
    env.pop("HETU_HOST_EVIDENCE", None)
    env.update(env_extra)
    return subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "check.sh")],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, check=False,
    )


# ---------------------------------------------------------------------------
# 07.1a 修复：run 在授权与能力验证通过后写出 host-support-record.json，
# 使 check --host-evidence 对 run 输出目录的成功路径可达；无授权引用或
# 无已验证能力事实时不写记录（fail-closed），resume 不覆盖既有记录。


def test_071a_run_writes_host_support_record_after_verified_facts(
    tmp_path, monkeypatch
):
    import time as time_module

    base = time_module.time() - 60
    home = tmp_path / "home"
    (home / ".zcode" / "cli" / "rollout").mkdir(parents=True)
    transcript = tmp_path / "model-io-sess_r.jsonl"
    _wh_records(transcript, 2)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(time_module, "sleep", lambda *_: None)
    case = _write_probe_case(tmp_path, probe_mode=True)
    out = tmp_path / "obs"
    module = load_module()
    code = module["main"]([
        "run", "--host", "zcode", "--case", str(case), "--output", str(out),
        "--watch-agent", f"file:{transcript}=research",
        "--request-at", _iso(base), "--delivered-at", _iso(base + 30),
        "--authorization-ref", "grant-ref-with-fake-secret:sk-test-123",
    ])
    assert code == 0
    record_path = out / "host-support-record.json"
    assert record_path.exists()
    # 记录在 case 执行前写出：即使执行失败/中断也应已存在，此处用其先序性
    # 与 case-record 同批 create-only 落盘来证明
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["host"] == "zcode"
    assert record["authorization"] == "granted"
    # required_tools 只来自本次已验证的事实，不凭空缺充
    assert record["required_tools"] == [
        "zcode-rollout-observation", "zcode-transcript-snapshot",
    ]
    assert set(record) == {"host", "authorization", "required_tools", "recorded_at"}
    # 授权引用名与任何凭据内容都不得入库
    assert "sk-test-123" not in json.dumps(record)


def test_071a_run_record_closes_host_evidence_check_end_to_end(
    tmp_path, monkeypatch
):
    import os
    import time as time_module

    base = time_module.time() - 60
    home = tmp_path / "home"
    home.mkdir()
    _fake_agent(home, "a1", "sess_r", [
        _rollout_record("rq-r1", base + 10, base + 20),
    ], base=base)
    _fake_rollout(home, "sess_coord", [
        _rollout_record("rq-c1", base + 10, base + 30),
    ])
    control = tmp_path / "control.jsonl"
    control.write_text('{"op": "finalize"}\n', encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(time_module, "sleep", lambda *_: None)
    case = _wg_case(tmp_path, "HS-E2E", probe=True)
    out = tmp_path / "obs"
    out.mkdir()
    _isolation_evidence(
        out, task_identity="zcode-task:HS-E2E", covered_sessions=["sess_r"]
    )
    (out / "isolation-evidence.json").replace(out / "evidence.json")
    module = load_module()
    code = module["main"]([
        "run", "--host", "zcode", "--case", str(case), "--output", str(out),
        "--watch-agent", "agent_a1=research",
        "--watch-agent", "sess_coord=coordination",
        "--delivery-sweep", "--watch-timeout", "30",
        "--watch-control", str(control),
        "--request-at", _iso(base),
        "--isolation-evidence", "evidence.json",
        "--authorization-ref", "local-grant-ref",
    ])
    assert code == 0
    record = json.loads(
        (out / "host-support-record.json").read_text(encoding="utf-8"))
    assert record["authorization"] == "granted"

    coordination = home / ".zcode" / "cli" / "rollout" / "model-io-sess_coord.jsonl"
    now = time_module.time()
    with open(coordination, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(_rollout_record("rq-delivery", now, now + 5)) + "\n")
    marker = out / "delivery-message.md"
    marker.write_text("delivery", encoding="utf-8")
    os.utime(marker, (now, now))
    assert module["main"]([
        "sweep", "--evidence", str(out), "--session", "sess_coord",
    ]) == 0

    result_path = tmp_path / "result.json"
    code = module["main"]([
        "check", "--host-evidence", str(out), "--output", str(result_path),
    ])
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert code == 0, payload["failures"]
    assert payload["passed"] is True
    assert payload["baseline_qualification"] == "not_applicable_probe"
    assert payload["host_support"]["host"] == "zcode"


def test_071a_run_without_authorization_ref_writes_no_record(tmp_path):
    module, code, out = _run_zcode_stage(tmp_path, probe_mode=True)
    assert code == 0
    # 未验证授权：不伪造记录，check --host-evidence 仍 fail-closed
    assert not (out / "host-support-record.json").exists()
    result_path = tmp_path / "result.json"
    check_code = module["main"]([
        "check", "--host-evidence", str(out), "--output", str(result_path),
    ])
    assert check_code != 0
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["passed"] is False
    assert any("host-support-record" in f for f in payload["failures"])


def test_071a_run_with_ref_but_no_capability_fact_writes_no_record(
    tmp_path, monkeypatch, capsys
):
    import time as time_module

    base = time_module.time() - 60
    home = tmp_path / "home"  # 空 home：rollout 缺失，无任何已验证事实
    home.mkdir()
    missing_snapshot = tmp_path / "absent.jsonl"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(time_module, "sleep", lambda *_: None)
    case = _write_probe_case(tmp_path, probe_mode=True)
    out = tmp_path / "obs"
    module = load_module()
    code = module["main"]([
        "run", "--host", "zcode", "--case", str(case), "--output", str(out),
        "--watch-agent", f"file:{missing_snapshot}=research",
        "--request-at", _iso(base), "--delivered-at", _iso(base + 30),
        "--authorization-ref", "local-grant-ref",
    ])
    assert code == 0
    assert not (out / "host-support-record.json").exists()
    assert "NOT written" in capsys.readouterr().err


def test_071a_run_refuses_preexisting_support_record(tmp_path):
    module = load_module()
    out = tmp_path / "obs"
    out.mkdir()
    record_path = out / "host-support-record.json"
    record_path.write_text('{"keep": true}', encoding="utf-8")
    case = _write_probe_case(tmp_path, probe_mode=True)
    code = module["main"]([
        "run", "--host", "zcode", "--case", str(case), "--output", str(out),
        "--watch-agent", "whatever=research",
        "--authorization-ref", "local-grant-ref",
    ])
    assert code == 2
    assert json.loads(record_path.read_text(encoding="utf-8")) == {"keep": True}


def test_071a_run_resume_keeps_existing_support_record(tmp_path, monkeypatch):
    import time as time_module

    base = time_module.time() - 60
    home = tmp_path / "home"
    (home / ".zcode" / "cli" / "rollout").mkdir(parents=True)
    transcript = tmp_path / "model-io-sess_r.jsonl"
    _wh_records(transcript, 2)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(time_module, "sleep", lambda *_: None)
    case = _write_probe_case(tmp_path, probe_mode=True)
    out = tmp_path / "obs"
    module = load_module()
    args = [
        "run", "--host", "zcode", "--case", str(case), "--output", str(out),
        "--watch-agent", f"file:{transcript}=research",
        "--request-at", _iso(base), "--delivered-at", _iso(base + 30),
        "--authorization-ref", "local-grant-ref",
    ]
    assert module["main"](args) == 0
    record_path = out / "host-support-record.json"
    snapshot = record_path.read_bytes()
    assert module["main"]([
        "run", "--host", "zcode", "--case", str(case), "--output", str(out),
        "--resume", "--watch-timeout", "5",
    ]) == 0
    assert record_path.read_bytes() == snapshot


def test_071a_check_sh_default_never_invokes_host_acceptance(tmp_path):
    log = tmp_path / "calls.log"
    log.touch()
    stub = _write_logging_stub(tmp_path, log)
    completed = _run_check_sh({"HETU_PYTHON": str(stub), "HETU_CLI": str(stub)})
    assert completed.returncode == 0, completed.stderr
    logged = log.read_text(encoding="utf-8")
    assert "host_acceptance" not in logged
    # 未设置环境变量时输出不得暗示真实认证已完成
    assert "HETU_HOST_EVIDENCE" not in completed.stdout
    assert "认证" not in completed.stdout


def test_071a_check_sh_with_env_runs_explicit_check_and_fails_closed(tmp_path):
    log = tmp_path / "calls.log"
    log.touch()
    stub = _write_logging_stub(tmp_path, log)
    missing = tmp_path / "absent-evidence"
    completed = _run_check_sh({
        "HETU_PYTHON": str(stub),
        "HETU_CLI": str(stub),
        "HETU_HOST_EVIDENCE": str(missing),
    })
    assert completed.returncode != 0
    logged = log.read_text(encoding="utf-8")
    assert "--host-evidence" in logged
    assert str(missing) in logged
    assert "未执行" in completed.stderr


def test_071a_check_sh_with_env_passes_on_selected_evidence(tmp_path):
    log = tmp_path / "calls.log"
    log.touch()
    stub = _write_logging_stub(tmp_path, log)
    evidence = tmp_path / "ev"
    _write_evidence(evidence, [_good_research_event()], _base_meta())
    _isolation_evidence(evidence)
    _write_host_support(evidence, _host_support_record())
    completed = _run_check_sh({
        "HETU_PYTHON": str(stub),
        "HETU_CLI": str(stub),
        "HETU_HOST_EVIDENCE": str(evidence),
    })
    assert completed.returncode == 0, completed.stderr
    assert (evidence / "host-support-check.json").exists()


def test_wh_status_reports_state_events_and_gaps(tmp_path, monkeypatch, capsys):
    import os
    import time as time_module

    monkeypatch.setattr(time_module, "sleep", lambda *_: None)
    transcript = tmp_path / "model-io-sess_s.jsonl"
    _wh_records(transcript, 2, prefix="rq-s")
    stat = os.stat(transcript)
    obs = tmp_path / "obs"
    obs.mkdir()
    (obs / "usage-events.jsonl").write_text("", encoding="utf-8")
    (obs / "collection-gaps.jsonl").write_text(json.dumps({
        "type": "transcript_rotated", "detail": "x",
        "generation": 1, "session_id": "sess_s",
        "at": "2026-09-10T00:00:00+00:00"}) + "\n", encoding="utf-8")
    (obs / "watch-state.json").write_text(json.dumps({
        "watchers": [{
            "native_id": f"file:{transcript}", "scope": "research",
            "kind": "file", "metadata_path": None, "session_id": "sess_s",
            "transcript": str(transcript),
            "state": {"identity": [stat.st_dev, stat.st_ino],
                      "offset": 0, "generation": 1},
            "done": False, "started_epoch": None, "ended_epoch": None,
        }],
        "control": None, "request_at": 1000.0,
        "saved_at": "2026-09-10T00:00:00+00:00",
    }), encoding="utf-8")
    module = load_module()
    code = module["main"](["status", "--evidence", str(obs)])
    assert code == 0
    out = capsys.readouterr().out
    assert "watch-state saved_at" in out
    assert "watcher research" in out
    assert "gaps: 1" in out


# ---------------------------------------------------------------------------
# 阶段 07 B1：预期取消（宿主终态 stopped）的显式声明与 gap 抑制


def test_stage03_expect_terminal_parses_declared_native_ids():
    module = load_module()
    parse = module["_parse_expect_terminal"]
    assert parse(["agent_a=stopped", "agent_b=cancelled"]) == {
        "agent_a": "stopped",
        "agent_b": "cancelled",
    }
    assert parse(None) == {}
    assert parse([]) == {}


def test_stage03_expect_terminal_rejects_malformed_specs():
    module = load_module()
    parse = module["_parse_expect_terminal"]
    for bad in ("agent_a", "=stopped", "agent_a="):
        with pytest.raises(ValueError):
            parse([bad])


def test_stage03_declared_stopped_terminal_produces_no_gap():
    module = load_module()
    gap = module["_terminal_status_gap"]
    expected = {"agent_c": "stopped"}
    assert (
        gap(
            native_id="agent_c",
            scope="research",
            session_id="sess_x",
            generation=1,
            status="stopped",
            expected_terminal=expected,
        )
        is None
    )


def test_stage03_undeclared_non_completed_terminal_still_records_gap():
    module = load_module()
    gap = module["_terminal_status_gap"]
    recorded = gap(
        native_id="agent_z",
        scope="research",
        session_id="sess_y",
        generation=2,
        status="stopped",
        expected_terminal={"agent_c": "stopped"},
    )
    assert recorded is not None
    assert recorded["type"] == "agent_terminal_status"
    assert recorded["session_id"] == "sess_y"
    assert "stopped" in recorded["detail"]


def test_stage03_completed_terminal_never_records_gap():
    module = load_module()
    gap = module["_terminal_status_gap"]
    assert (
        gap(
            native_id="agent_c",
            scope="research",
            session_id="sess_x",
            generation=0,
            status="completed",
            expected_terminal={},
        )
        is None
    )
