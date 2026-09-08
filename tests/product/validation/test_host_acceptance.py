"""Behavior tests for the stage-01 host acceptance observation helpers.

Expected values below are asserted literally; they are never computed by
calling the production summarizer on itself. Counterexamples marked
S1–S6/R2–R3 come from the stage-01 review findings (2026-09-08).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "host_acceptance.py"
FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "host_acceptance"
B04_EXPECTED_OUTPUT_TOKENS = 33_319
B04_EXPECTED_TOOL_CALLS = 46
B04_EXPECTED_CACHE_READ = 1_279_616


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
# B04 replay against the audit-recorded totals


def test_replay_b04_verifier_copy_matches_audited_totals():
    summarize = load_module()["summarize_usage"]
    to_events = load_module()["events_from_closeout_verifier_usage"]
    source = FIXTURE_DIR / "b04-verifier-usage-events.jsonl"
    events = to_events(source, case="B04")
    assert len(events) == 83
    result = summarize(events, expected_scopes={"review"})
    assert result["output_tokens"] == B04_EXPECTED_OUTPUT_TOKENS
    assert result["tool_calls"]["unique_ids"] == B04_EXPECTED_TOOL_CALLS
    assert result["input_includes_cache"] is False
    assert result["cached_input_read_tokens"] == B04_EXPECTED_CACHE_READ
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
