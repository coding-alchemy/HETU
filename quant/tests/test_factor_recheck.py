"""Targeted regressions for evidence merging, local reads, and the workbench."""

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest
from hetu_data.library import publish_library

from factor_cases import example_query, local_fact_source, local_volume_source, native_dividend_correction
from hetu_quant.factors import compute_factor
from test_factor_review import rerun
from test_factor_workbench import open_factors, run_trial


def library(store, *sources):
    assets = []
    for owner, ref in sources:
        tables = {row["table"] for row in owner.load_manifest(ref)["table_files"]}
        assets.append(dict(root=str(owner.root), snapshot_id=ref.snapshot_id, tables=sorted(tables)))
    return publish_library(store, asset_snapshots=tuple(assets))


@pytest.mark.parametrize(
    ("variant", "expected"), [("conflict", None), ("same", 0.05), ("independent", 0.14), ("outside", 0.05)]
)
def test_event_identity_across_original_coordinates(tmp_path, variant, expected):
    source, doc, query = example_query("dividend_yield")
    tables = doc["tables"]
    first = tables["corporate_actions"][0]
    if variant == "outside":
        first = dict(first, event_id="OLD", ex_date="2022-11-01")
        other = dict(first, ex_date="2022-12-01")
        tables["corporate_actions"].append(first)
    else:
        other = dict(first, d=0.2 if variant == "same" else 0.9)
        if variant == "independent":
            other["event_id"] = "OTHER"
    first.update(coordinate="ref-a", source_ref="公告甲")
    other.update(coordinate="ref-b", source_ref="公告乙")
    tables["corporate_actions"].append(other)
    teaching = rerun(tmp_path, source, doc, query)
    store, ref = local_fact_source(tmp_path / "combined", tables)
    left_tables = dict(tables, corporate_actions=tables["corporate_actions"][:-1])
    left_store, left_ref = local_fact_source(tmp_path / "left", left_tables)
    other_store, other_ref = local_fact_source(tmp_path / "other", {"corporate_actions": [other]})
    for result in (
        teaching,
        compute_factor(store, ref, query),
        compute_factor(store, library(store, (left_store, left_ref), (other_store, other_ref)), query),
    ):
        if expected is None:
            assert not result.rows
            gap = result.gaps[0]
            assert gap["field"] == "d"
            assert {item["coordinate"] for item in gap["evidence"]} == {"ref-a", "ref-b"}
        else:
            assert result.rows[0]["value"] == pytest.approx(expected)
            assert not result.gaps


@pytest.mark.parametrize("variant", ["equivalent", "conflict", "independent", "ambiguous"])
def test_dividend_without_event_id_uses_announcement_identity(tmp_path, variant):
    source, doc, query = example_query("dividend_yield")
    tables = doc["tables"]
    first, *other_events = tables.pop("corporate_actions")
    first.pop("event_id")
    first.pop("kind")
    first.update(action_type="cash_dividend", coordinate="import-a", source_ref="公告/执行分红121.pdf",
                 announcement_ref="公告/执行分红121.pdf",
                 field_evidence={"d": {"unit": "元/股", "currency": "CNY"}})
    second = dict(first, coordinate="import-b", d=0.2 if variant == "conflict" else 20,
                  field_evidence={"d": {"unit": "分/股", "currency": "CNY"}})
    if variant in ("independent", "ambiguous"):
        second.update(source_ref="公告/执行分红122.pdf", announcement_ref="公告/执行分红122.pdf")
    if variant == "independent":
        second["ex_date"] = "2023-08-02"
    for index, events in enumerate(((first, second), (second, first))):
        fixture = dict(tables, corporate_actions=[*events, *other_events])
        store, ref = local_fact_source(tmp_path / f"attested-{index}", fixture)
        native_main = local_fact_source(tmp_path / f"left-{index}", dict(tables, corporate_actions=other_events),
                                       native_actions=[events[0]])
        native_peer = local_fact_source(tmp_path / f"right-{index}", {}, native_actions=[events[1]])
        local_group = library(store, native_main, native_peer)
        for name, result in (
            ("teaching", rerun(tmp_path, source, dict(doc, tables=fixture), query)),
            ("local", compute_factor(store, ref, query)),
            ("native", compute_factor(store, local_group, query)),
        ):
            print(variant, index, name, [row["value"] for row in result.rows], result.gaps)
            if variant in ("equivalent", "independent"):
                assert not result.gaps
                assert result.rows[0]["value"] == pytest.approx(0.05 if variant == "equivalent" else 0.07)
                if variant == "equivalent":
                    dividend = next(row for row in result.rows[0]["inputs"] if row.get("ex_date") == "2023-08-01")
                    origins = dividend["field_evidence"]["d"]["corroborating_sources"]
                    assert {row["coordinate"] for row in origins} == {"import-a", "import-b"}
            else:
                assert not result.rows
                gap, = result.gaps
                assert (gap["subject"], gap["period"], gap["field"]) == (
                    "A", "2023-08-01", "d" if variant == "conflict" else "event_identity",
                )
                origin = "announcement_ref" if name == "native" else "source_ref"
                assert {row[origin] for row in gap["evidence"]} == {first["source_ref"], second["source_ref"]}
                assert {row["coordinate"] for row in gap["evidence"]} == {"import-a", "import-b"}
                if variant == "conflict":
                    assert {row["unit"] for row in gap["evidence"]} == {"元/股", "分/股"}


@pytest.mark.parametrize(
    ("variant", "amount", "unit", "expected"),
    [("conflict", 0.2, "分/股", None), ("equivalent", 20, "分/股", 0.05),
     ("superseded", 40, "分/股", 0.07), ("distinct_revision", 40, "分/股", None)],
)
def test_dividend_cash_attestations_use_business_event_and_cutoff(tmp_path, variant, amount, unit, expected):
    source, doc, query = example_query("dividend_yield")
    tables = doc["tables"]
    first, *independent = tables["corporate_actions"]
    first.update(
        source_ref="原公告甲",
        coordinate="announcement-a",
        revision_id="version-a",
        field_evidence={"d": {"unit": "元/股", "currency": "CNY"}},
    )
    other = dict(
        first,
        d=amount,
        source_ref="原公告乙",
        coordinate="announcement-b",
        revision_id="version-b",
        field_evidence={"d": {"unit": unit, "currency": "CNY"}},
    )
    revised_case = variant in ("superseded", "distinct_revision")
    if revised_case:
        other.update(available_at="2024-01-31T18:00:00+08:00", supersedes="version-a")
    if variant == "superseded":
        other["coordinate"] = first["coordinate"]
    observed = [(query, expected)]
    if revised_case:
        revised = replace(query, cutoff=datetime.fromisoformat(other["available_at"]))
        observed = [(query, 0.05), (revised, expected)]
    for reverse in (False, True):
        ordered = [other, first] if reverse else [first, other]
        tables["corporate_actions"] = [*ordered, *independent]
        store, ref = local_fact_source(tmp_path / f"local-{reverse}", tables)
        group_sources = (
            local_fact_source(tmp_path / f"original-{reverse}", dict(tables, corporate_actions=[first, *independent])),
            local_fact_source(tmp_path / f"alternate-{reverse}", {"corporate_actions": [other]}),
        )
        if reverse:
            group_sources = tuple(reversed(group_sources))
        for request, answer in observed:
            results = {
                "teaching": rerun(tmp_path, source, doc, request),
                "local": compute_factor(store, ref, request),
            }
            if variant != "superseded":
                results["library"] = compute_factor(store, library(store, *group_sources), request)
            for kind, result in results.items():
                print(variant, reverse, kind, request.cutoff, [row["value"] for row in result.rows], result.gaps)
                if answer is None:
                    assert not result.rows
                    gap = result.gaps[0]
                    assert (gap["subject"], gap["period"], gap["event"], gap["field"]) == ("A", "2023-08-01", "D1", "d")
                    assert {(item["source_ref"], item["value"], item["unit"]) for item in gap["evidence"]} == {
                        ("原公告甲", 0.2, "元/股"),
                        ("原公告乙", amount, "分/股"),
                    }
                else:
                    assert not result.gaps
                    row = result.rows[0]
                    assert row["value"] == pytest.approx(answer)
                    if revised_case:
                        dividend = next(item for item in row["inputs"] if item.get("event_id") == "D1")
                        assert dividend["source_ref"] == ("原公告甲" if request == query else "原公告乙")
                    if variant == "equivalent":
                        dividend = next(item for item in row["inputs"] if item.get("event_id") == "D1")
                        origins = dividend["field_evidence"]["d"]["corroborating_sources"]
                        assert {item["source_ref"] for item in origins} == {"原公告甲", "原公告乙"}


@pytest.mark.parametrize("variant", ["first_only", "second_only", "both", "ambiguous", "revised_notice"])
def test_same_day_dividend_copies_follow_each_announcement(tmp_path, variant):
    source, doc, request = example_query("dividend_yield")
    tables = doc["tables"]
    first, second, *_ = tables.pop("corporate_actions")
    first.update(event_id="E1", ex_date="2023-08-01", d=0.2)
    second.update(event_id="E2", ex_date="2023-08-01", d=0.7)
    copies = []
    for action, cents in ((first, 20), (second, 70)):
        event = action["event_id"]
        action.update(source_ref=f"公告/{event}.pdf", coordinate=f"{event}-original", action_type="cash_dividend",
                      field_evidence={"d": {"unit": "元/股", "currency": "CNY",
                                            "announcement_ref": f"公告/{event}.pdf"}})
        copy = {field: value for field, value in action.items() if field not in {"event_id", "kind"}}
        copy.update(d=cents, coordinate=f"{event}-copy",
                    field_evidence={"d": dict(action["field_evidence"]["d"], unit="分/股")})
        copies.append(copy)
    if variant == "first_only":
        copies = copies[:1]
    elif variant == "second_only":
        copies = copies[1:]
    elif variant == "ambiguous":
        copies[1].update(source_ref="导入附件/无公告编号.pdf")
        copies[1]["field_evidence"]["d"].pop("announcement_ref")
    prior_notices = []
    if variant == "revised_notice":
        prior_notices.append(dict(first, revision_id="notice-v1", source_ref="公告/E2.pdf",
                                  field_evidence={"d": dict(first["field_evidence"]["d"],
                                                            announcement_ref="公告/E2.pdf")}))
        first.update(revision_id="notice-v2", supersedes="notice-v1", available_at="2024-01-31T15:20:00+08:00")

    for reverse in (False, True):
        actions = [*prior_notices, first, second, *copies]
        fixture = dict(tables, corporate_actions=actions[::-1] if reverse else actions)
        store, ref = local_fact_source(tmp_path / f"local-{reverse}", fixture)
        if variant == "revised_notice":
            for result in (rerun(tmp_path, source, dict(doc, tables=fixture), request),
                           compute_factor(store, ref, request)):
                assert not result.rows
                gap, = result.gaps
                assert (gap["subject"], gap["period"], gap["field"]) == ("A", "2023-08-01", "event_identity")
            query = replace(request, cutoff=datetime.fromisoformat(first["available_at"]))
        else:
            query = request
        for name, result in (("teaching", rerun(tmp_path, source, dict(doc, tables=fixture), query)),
                             ("local", compute_factor(store, ref, query))):
            print(variant, reverse, name, [row["value"] for row in result.rows], result.gaps)
            if variant == "ambiguous":
                assert not result.rows
                gap, = result.gaps
                assert (gap["subject"], gap["period"], gap["field"]) == ("A", "2023-08-01", "event_identity")
                assert {row["source_ref"] for row in gap["evidence"]} == {
                    "导入附件/无公告编号.pdf", "公告/E1.pdf", "公告/E2.pdf",
                }
                continue
            assert not result.gaps
            row, = result.rows
            assert row["value"] == pytest.approx(0.09)
            events = {item["event_id"]: item for item in row["inputs"] if "event_id" in item}
            assert set(events) == {"E1", "E2"}
            for copy in copies:
                event = copy["coordinate"].removesuffix("-copy")
                origins = events[event]["field_evidence"]["d"]["corroborating_sources"]
                assert {part["coordinate"] for part in origins} == {f"{event}-original", f"{event}-copy"}
                assert {part["source_ref"] for part in origins} == {f"公告/{event}.pdf"}
                assert all(part.get("revision_id") != "notice-v1" for part in origins)


@pytest.mark.parametrize("variant", ["current", "history", "foreign"])
def test_idless_dividend_selects_record_revision_before_matching_notice(tmp_path, variant):
    source, doc, request = example_query("dividend_yield")
    tables = doc["tables"]
    first, second, *_ = tables["corporate_actions"]
    first.update(event_id="E1", ex_date="2023-08-01", d=0.2, source_ref="E1.pdf",
                 coordinate="E1-original", revision_id="v1", action_type="cash_dividend",
                 field_evidence={"d": {"announcement_ref": "E1.pdf"}})
    second.update(event_id="E2", ex_date="2023-08-01", d=0.7, source_ref="E2.pdf",
                  coordinate="E2-original", action_type="cash_dividend")
    previous = {key: value for key, value in first.items() if key not in {"event_id", "kind"}}
    previous.update(source_ref="raw/not-yet-linked.csv", coordinate="E1-copy", field_evidence={},
                    available_at="2024-01-31T13:00:00+08:00")
    current = dict(previous, revision_id="v2", supersedes="v1", source_ref="E1.pdf",
                   available_at="2024-01-31T15:30:00+08:00", d=20,
                   field_evidence={"d": {"unit": "分/股", "currency": "CNY", "announcement_ref": "E1.pdf"}})
    latest = replace(request, cutoff=datetime.fromisoformat(current["available_at"]))
    foreign = dict(previous, source_ref="raw/other/actions.csv", coordinate="unrelated-row")
    actions = [first, second, current]
    if variant != "current":
        actions.append(previous)
    if variant == "foreign":
        actions.append(foreign)

    for reverse in (False, True):
        fixture = dict(tables, corporate_actions=actions[::-1] if reverse else actions)
        store, ref = local_fact_source(tmp_path / f"local-{reverse}", fixture)
        # Distinct suppliers may reuse v1, independently of the local archive's product identity.
        reads = [("teaching", latest), ("local", latest)]
        if variant == "history":
            reads.extend([("teaching", request), ("local", request)])
        for name, query in reads:
            result = (rerun(tmp_path, source, dict(doc, tables=fixture), query) if name == "teaching"
                      else compute_factor(store, ref, query))
            if variant == "foreign" or query is request:
                assert not result.rows
                gap, = result.gaps
                assert (gap["subject"], gap["period"], gap["field"]) == ("A", "2023-08-01", "event_identity")
                active = {(item.get("coordinate"), item.get("source_ref")) for item in gap["evidence"]}
                if variant == "foreign":
                    assert ("unrelated-row", "raw/other/actions.csv") in active
                    assert all(coordinate != "E1-copy" for coordinate, _ in active)
                else:
                    assert ("E1-copy", "raw/not-yet-linked.csv") in active
                    assert ("E1-copy", "E1.pdf") not in active
                observed = gap["field"]
            else:
                assert not result.gaps
                row, = result.rows
                assert row["value"] == pytest.approx(0.09)
                events = {item["event_id"]: item for item in row["inputs"] if "event_id" in item}
                assert set(events) == {"E1", "E2"}
                evidence = events["E1"]["field_evidence"]["d"]["corroborating_sources"]
                assert {(item["coordinate"], item["revision_id"], item["source_ref"]) for item in evidence} == {
                    ("E1-original", "v1", "E1.pdf"), ("E1-copy", "v2", "E1.pdf"),
                }
                observed = f"{row['value']:.0%}; events {sorted(events)}"
            print(f"{variant} reverse={reverse} {name} cutoff={query.cutoff}: {result.status} ({observed})")


@pytest.mark.parametrize("old_event", [None, "", "provisional-E1"])
@pytest.mark.parametrize("independent", [False, True])
def test_dividend_retires_only_revisions_of_the_same_record(tmp_path, old_event, independent):
    source, doc, request = example_query("dividend_yield")
    tables = doc["tables"]
    first, second, *_ = tables["corporate_actions"]
    first.update(event_id="E1", ex_date="2023-08-01", action_type="cash_dividend", d=0.2,
                 coordinate="E1-original", source_ref="E1.pdf", revision_id="notice-v2", supersedes="notice-v1",
                 available_at="2024-01-31T15:20:00+08:00", field_evidence={"d": {"unit": "元/股", "currency": "CNY"}})
    second.update(event_id="E2", ex_date=first["ex_date"], action_type="cash_dividend", d=0.7,
                  coordinate="E2-original", source_ref="E2.pdf")
    old = dict(first, event_id=old_event, revision_id="notice-v1", supersedes=None, source_ref="raw/pending.csv",
               available_at="2024-01-30T15:20:00+08:00")
    witness = dict(old, event_id="E1", coordinate="independent-amount", d=0.8, source_ref="independent.pdf")
    actions = [first, second]
    if old_event is not None:
        actions.append(old)
    if independent:
        actions.append(witness)
    query = replace(request, cutoff=datetime.fromisoformat(first["available_at"]))

    for reverse in (False, True):
        fixture = dict(tables, corporate_actions=actions[::-1] if reverse else actions)
        store, ref = local_fact_source(tmp_path / f"local-{reverse}", fixture)
        for result in (rerun(tmp_path, source, dict(doc, tables=fixture), query), compute_factor(store, ref, query)):
            if independent:
                assert not result.rows
                gap, = result.gaps
                assert (gap["event"], gap["field"]) == ("E1", "d")
                assert {(item["source_ref"], item["value"], item["unit"]) for item in gap["evidence"]} == {
                    ("E1.pdf", 0.2, "元/股"), ("independent.pdf", 0.8, "元/股"),
                }
            else:
                assert not result.gaps
                row, = result.rows
                assert row["value"] == pytest.approx(0.09)
                assert sorted(item["event_id"] for item in row["inputs"] if item.get("event_id")) == ["E1", "E2"]
        if old_event == "provisional-E1" and not independent:
            for result in (rerun(tmp_path, source, dict(doc, tables=fixture), request),
                           compute_factor(store, ref, request)):
                assert not result.gaps
                row, = result.rows
                assert row["value"] == pytest.approx(0.09)
                assert sorted(item["event_id"] for item in row["inputs"] if item.get("event_id")) == [
                    "E2", "provisional-E1",
                ]


@pytest.mark.parametrize("variant", ["same", "distinct", "equivalent", "kind_revision", "independent_revision"])
def test_composite_compares_active_dividend_proofs_across_tables(tmp_path, variant):
    names = ("dividend_yield", "insider_net_purchase")
    source, doc, query = example_query(
        "weighted_factor_score", params={"components": {name: {"weight": 1, "direction": 1} for name in names}},
    )
    _, dividend, _ = example_query("dividend_yield")
    _, holding, _ = example_query("insider_net_purchase")
    tables = doc["tables"]
    for bar in tables["daily_bars"]:
        bar["close_raw"] = 10
    tables["corporate_actions"] = [
        dict(row, code=code) for code in query.subjects for row in dividend["tables"]["corporate_actions"]
    ]
    tables["action_coverage"] = [
        dict(row, code=code) for code in query.subjects for row in dividend["tables"]["action_coverage"]
    ]
    tables["events"] = [dict(row, code=code) for code in query.subjects for row in holding["tables"]["events"]]
    action = tables["corporate_actions"][0]
    action.update(coordinate="row-01", source_ref="action-A.pdf", revision_id="v2", supersedes="v1",
                  field_evidence={"d": {"unit": "元/股", "currency": "CNY"}})
    quote_unit = "分/股" if variant == "equivalent" else "元/股"
    disclosure = dict(action, coordinate="row-88" if variant != "same" else "row-01",
                      source_ref="disclosure-B.pdf", revision_id="v1", supersedes="",
                      d=20 if variant == "equivalent" else 0.8,
                      field_evidence={"d": {"unit": quote_unit, "currency": "CNY"}})
    tables["events"].append(disclosure)
    checks = [(query, None if variant == "equivalent" else "d")]
    if variant in ("kind_revision", "independent_revision"):
        tables["events"].append(dict(disclosure, kind="buyback_plan", revision_id="v2", supersedes="v1",
                                     available_at="2024-01-31T18:00:00+08:00",
                                     event_id="D1" if variant == "kind_revision" else "REBUY"))
        checks.append((replace(query, cutoff=query.cutoff.replace(hour=18)),
                       "kind" if variant == "kind_revision" else None))
    store, ref = local_fact_source(tmp_path / "local", tables)
    for composite, expected_gap in checks:
        single = replace(composite, factor_id="dividend_yield", subjects=("A",), parameters={})
        for request in (composite, single):
            _assert_dividend_entry(
                (rerun(tmp_path, source, doc, request), compute_factor(store, ref, request)),
                variant, expected_gap, request.factor_id,
            )


def _assert_dividend_entry(results, variant, expected_gap, factor_id):
    composite = factor_id == "weighted_factor_score"
    for result in results:
        if expected_gap is None:
            assert not result.gaps
            row = next(row for row in result.rows if row["subject"] == "A")
            value = row["components"]["components"]["dividend_yield"]["raw"] if composite else row["value"]
            assert value == pytest.approx(0.05)
            if variant == "equivalent":
                action = next(item for item in row["inputs"] if item.get("event_id") == "D1")
                origins = action["field_evidence"]["d"]["corroborating_sources"]
                assert {(fact["source_ref"], fact["value"], fact["unit"]) for fact in origins} == {
                    ("action-A.pdf", .2, "元/股"), ("disclosure-B.pdf", 20, "分/股"),
                }
        else:
            assert not result.rows
            gap, = result.gaps
            field = f"components.dividend_yield.{expected_gap}" if composite else expected_gap
            assert (gap["subject"], gap["event"], gap["field"]) == ("A", "D1", field)
            if expected_gap == "kind":
                assert {(fact["source_ref"], fact["kind"]) for fact in gap["evidence"]} == {
                    ("action-A.pdf", "distribution"), ("disclosure-B.pdf", "buyback_plan"),
                }
            else:
                assert {(fact["source_ref"], fact["value"], fact["unit"]) for fact in gap["evidence"]} == {
                    ("action-A.pdf", .2, "元/股"), ("disclosure-B.pdf", .8, "元/股"),
                }


def _disclosure_branches(form, *, peer_id="D1", reverse=False, buyback_kind="buyback_plan"):
    names = ("dividend_yield", "insider_net_purchase")
    _, doc, combo = example_query(
        "weighted_factor_score", params={"components": {name: {"weight": 1, "direction": 1} for name in names}},
    )
    _, holding, _ = example_query("insider_net_purchase")
    _, dividend, query = example_query("dividend_yield")
    tables = doc["tables"]
    for bar in tables["daily_bars"]:
        bar["close_raw"] = 10
    first, *others = dividend["tables"]["corporate_actions"]
    tables["corporate_actions"] = [
        dict(row, code=code) for code in combo.subjects for row in (others if code == "A" else [first, *others])
    ]
    tables["action_coverage"] = [
        dict(row, code=code) for code in combo.subjects for row in dividend["tables"]["action_coverage"]
    ]
    prior = dict(first, coordinate="filing-01", revision_id="v1", source_ref="previous.pdf")
    if form == "outside":
        prior["ex_date"] = "2022-12-31"
    revision = dict(prior, revision_id="v2", supersedes="v1", source_ref="dividend-revision.pdf",
                    available_at="2024-01-31T18:00:00+08:00")
    buyback = {key: value for key, value in revision.items() if key not in ("ex_date", "d")}
    buyback.update(revision_id="v3", event_id=peer_id, kind=buyback_kind, source_ref="buyback-revision.pdf")
    identity = dict(others[0], coordinate="filing-01", revision_id="v3", supersedes="v1",
                    available_at=revision["available_at"], source_ref="identity-revision.pdf")
    active = {"distribution": [revision], "buyback": [buyback], "both": [revision, buyback],
              "outside": [revision, buyback], "identity": [revision, identity]}[form]
    if reverse:
        active.reverse()
    tables["events"] = [prior, *active, *(dict(row, code=code) for code in combo.subjects
                                         for row in holding["tables"]["events"])]
    return doc, query, combo


@pytest.mark.parametrize("form", ["distribution", "buyback", "both", "outside"])
def test_dividend_resolves_active_disclosure_kinds_before_filtering(tmp_path, form):
    doc, query, combo = _disclosure_branches(form)
    tables = doc["tables"]
    store, ref = local_fact_source(tmp_path / "local", tables)
    for request in (query, combo):
        for hour in (15, 19):
            as_of = replace(request, cutoff=request.cutoff.replace(hour=hour))
            source = example_query(request.factor_id)[0]
            for result in (rerun(tmp_path, source, doc, as_of), compute_factor(store, ref, as_of)):
                if form == "both" and hour == 19:
                    assert not result.rows
                    gap, = result.gaps
                    assert (gap["subject"], gap["event"], gap["period"]) == ("A", "D1", "2023-08-01")
                    assert gap["field"] == ("kind" if request == query else "components.dividend_yield.kind")
                    notices = {(p["source_ref"], p["kind"], p["revision_id"], p["supersedes"])
                               for p in gap["evidence"] if p.get("revision_role") != "common_previous"}
                    assert notices == {
                        ("dividend-revision.pdf", "distribution", "v2", "v1"),
                        ("buyback-revision.pdf", "buyback_plan", "v3", "v1"),
                    }
                else:
                    assert not result.gaps
                    row = next(item for item in result.rows if item["subject"] == "A")
                    value = row["value"] if request == query else (
                        row["components"]["components"]["dividend_yield"]["raw"]
                    )
                    assert value == pytest.approx(.03 if form == "outside" or form == "buyback" and hour == 19 else .05)


@pytest.mark.parametrize(("form", "peer_id", "reverse", "expected"), [
    ("both", "B1", False, None), ("both", "B1", True, None), ("both", "D1", False, None),
    ("distribution", "B1", False, .05), ("buyback", "B1", False, .03),
    ("outside", "B1", True, .03), ("identity", "D2", False, None),
])
def test_dividend_branches_follow_registration_before_event_id(tmp_path, form, peer_id, reverse, expected):
    doc, query, combo = _disclosure_branches(form, peer_id=peer_id, reverse=reverse, buyback_kind="buyback")
    independent = next(row for row in doc["tables"]["corporate_actions"]
                       if row["code"] == "A" and row["event_id"] == "D2")
    independent.update(coordinate="filing-01", revision_id="v1", source_ref="independent.pdf")
    store, ref = local_fact_source(tmp_path / "local", doc["tables"])
    group = library(store, (store, ref))
    if form == "both":
        source, _, buyback_request = example_query("buyback_plan")
        buyback_request = replace(buyback_request, cutoff=query.cutoff.replace(hour=19))
        for result in (rerun(tmp_path, source, doc, buyback_request), compute_factor(store, ref, buyback_request)):
            assert not result.rows and result.gaps[0]["field"] == "kind"
    for request in (query, combo):
        source, _, _ = example_query(request.factor_id)
        for hour in (15, 19):
            args = replace(request, cutoff=request.cutoff.replace(hour=hour))
            for result in (rerun(tmp_path, source, doc, args), compute_factor(store, ref, args),
                           compute_factor(store, group, args)):
                if expected is not None or hour == 15:
                    assert not result.gaps
                    row = next(row for row in result.rows if row["subject"] == "A")
                    components = row["components"].get("components")
                    actual = row["value"] if request == query else components["dividend_yield"]["raw"]
                    assert actual == pytest.approx((.03 if form == "outside" else .05) if hour == 15 else expected)
                    print(form, peer_id, reverse, hour, result.factor_id, result.source["nature"], actual)
                    continue
                assert not result.rows
                gap, = result.gaps
                field = "event_identity" if form == "identity" else "kind"
                assert gap["field"] == (field if request == query else f"components.dividend_yield.{field}")
                assert gap["subject"] == "A" and gap["period"] in ("2023-08-01", "2023-10-01")
                evidence = {row["revision_id"]: row for row in gap["evidence"]}
                assert set(evidence) == {"v1", "v2", "v3"}
                assert evidence["v1"]["source_ref"] == "previous.pdf"
                assert evidence["v2"]["event_id"] == "D1" and evidence["v2"]["kind"] == "distribution"
                assert evidence["v3"]["event_id"] == peer_id
                assert evidence["v3"]["kind"] == ("distribution" if form == "identity" else "buyback")
                assert all(evidence[key]["supersedes"] == "v1" for key in ("v2", "v3"))
                print(form, peer_id, reverse, hour, result.factor_id, result.source["nature"], gap)


@pytest.mark.parametrize("table", ["events", "corporate_actions"])
@pytest.mark.parametrize("same_total", [False, True])
def test_dividend_forks_require_material_change_to_total(tmp_path, table, same_total):
    doc, query, _ = _disclosure_branches("identity")
    tables = doc["tables"]
    prior, first, second, *other = tables["events"]
    if same_total:
        second.update(event_id="NEW", ex_date=first["ex_date"], d=first["d"])
    source = example_query(query.factor_id)[0]
    query = replace(query, cutoff=query.cutoff.replace(hour=19))
    actions = tables["corporate_actions"][:]
    for index, order in enumerate(([first, second], [second, first])):
        tables["events"] = other
        tables["corporate_actions"] = actions
        tables[table] = [*tables[table], prior, *order]
        store, ref = local_fact_source(tmp_path / str(index), tables)
        for result in (rerun(tmp_path, source, doc, query), compute_factor(store, ref, query)):
            if same_total:
                assert not result.gaps and result.rows[0]["value"] == pytest.approx(.05)
                alternatives = next(row["identity_evidence"] for row in result.rows[0]["inputs"]
                                    if row.get("identity_evidence"))
                assert {p["event_id"] for p in alternatives if p["revision_role"] == "branch"} == {"D1", "NEW"}
            else:
                assert not result.rows and result.gaps[0]["field"] == "event_identity"
                assert {p["revision_id"] for p in result.gaps[0]["evidence"]} == {"v1", "v2", "v3"}


@pytest.mark.parametrize("other_id", ["NEW", "D1"])
@pytest.mark.parametrize("other_day", ["2023-08-02", "2023-01-15"])
def test_dividend_forks_compare_cash_in_window_without_selecting_date(tmp_path, other_day, other_id):
    from datetime import date

    doc, query, _ = _disclosure_branches("identity")
    prior, first, second, *other = doc["tables"]["events"]
    second.update(event_id=other_id, ex_date=other_day, d=first["d"])
    conflict_field = "ex_date" if other_id == "D1" else "event_identity"
    source = example_query(query.factor_id)[0]
    query = replace(query, cutoff=query.cutoff.replace(hour=19))
    for label, branches in (("first", [first]), ("second", [second]), ("both", [first, second])):
        doc["tables"]["events"] = [*other, prior, *branches]
        store, ref = local_fact_source(tmp_path / label, doc["tables"])
        for result in (rerun(tmp_path, source, doc, query), compute_factor(store, ref, query)):
            if label == "both" and other_day == "2023-01-15":
                assert not result.rows and result.gaps[0]["field"] == conflict_field

                assert {item["ex_date"] for item in result.gaps[0]["evidence"]} == {first["ex_date"], other_day}
                continue
            assert not result.gaps
            row, = result.rows
            assert row["value"] == pytest.approx(.03 if label == "second" and other_day == "2023-01-15" else .05)
            if label == "both":
                assert row["components"]["cash"] == pytest.approx(.5)
                matched = next(item for item in row["inputs"] if item.get("identity_evidence"))
                assert "ex_date" not in matched
                assert matched["ex_date_options"] == [first["ex_date"], other_day]
                assert {item["revision_id"] for item in matched["identity_evidence"]} == {"v1", "v2", "v3"}
        if label == "both" and other_day == "2023-08-02":
            ex_source, _, ex_query = example_query("ex_distribution_return")
            exact = replace(ex_query, trade_date=date.fromisoformat(first["ex_date"]), cutoff=query.cutoff)
            for result in (rerun(tmp_path, ex_source, doc, exact), compute_factor(store, ref, exact)):
                assert not result.rows and result.gaps[0]["field"] == conflict_field


@pytest.mark.parametrize("corroborate_both", [True, False])
def test_cash_branch_total_compares_duplicates_across_originals(tmp_path, corroborate_both):
    doc, query, _ = _disclosure_branches("identity")
    tables = doc["tables"]
    prior, first, second, *other = tables["events"]
    second.update(event_id="NEW", ex_date="2023-08-02", d=first["d"])
    tables["events"] = [prior, first, second, *other]
    for row in (first, second) if corroborate_both else (first,):
        witness = {key: row[key] for key in ("code", "event_id", "kind", "ex_date", "d", "same_share_basis")}
        witness.update(source_ref=f"witness-{row['event_id']}.pdf", available_at=prior["available_at"])
        tables["corporate_actions"].append(witness)
    store, ref = local_fact_source(tmp_path / "local", tables)
    query = replace(query, cutoff=query.cutoff.replace(hour=19))
    for result in (rerun(tmp_path, example_query("dividend_yield")[0], doc, query), compute_factor(store, ref, query)):
        if corroborate_both:
            row, = result.rows
            assert not result.gaps and row["value"] == pytest.approx(.07)
            assert any(item.get("identity_evidence") for item in row["inputs"])
        else:
            assert not result.rows and result.gaps[0]["field"] == "event_identity"


@pytest.mark.parametrize("shared_publication", [False, True], ids=["attachment-only", "issuer-announcement"])
@pytest.mark.parametrize("branch", ["first", "second", "both"])
def test_cash_branches_match_unnumbered_copy_by_announcement(tmp_path, shared_publication, branch):
    doc, query, _ = _disclosure_branches("identity")
    tables = doc["tables"]
    prior, first, second, *other = tables["events"]
    second.update(event_id="NEW", ex_date="2023-08-02", d=first["d"])
    witnesses = [
        {**{key: row[key] for key in ("code", "event_id", "kind", "ex_date", "d", "same_share_basis")},
         "source_ref": f"witness-{row['event_id']}.pdf", "available_at": prior["available_at"]}
        for row in (first, second)
    ]
    announcement = "https://issuer.example/distributions/d1"
    if shared_publication:
        witnesses[0]["announcement_ref"] = announcement
    copy = {key: value for key, value in witnesses[0].items() if key != "event_id"}
    tables["corporate_actions"].extend([*witnesses, copy])
    source = example_query(query.factor_id)[0]
    query = replace(query, cutoff=query.cutoff.replace(hour=19))
    branches = {"first": [first], "second": [second], "both": [first, second]}
    tables["events"] = [prior, *branches[branch], *other]
    store, ref = local_fact_source(tmp_path / "local", tables)
    for result in (rerun(tmp_path, source, doc, query), compute_factor(store, ref, query)):
        if not shared_publication:
            assert not result.rows
            gap, = result.gaps
            assert (gap["subject"], gap["field"], gap["period"]) == ("A", "event_identity", "2023-08-01")
            disputed = [proof for proof in gap["evidence"] if proof.get("source_ref") == "witness-D1.pdf"]
            assert len(disputed) == 2
            assert all(proof["value"] == .20 and not proof.get("announcement_ref") for proof in disputed)
            if result.source["id"] == ref.snapshot_id:
                assert len({proof["fact_key"] for proof in disputed}) == 2
            print(branch, result.source["id"], "event_identity", disputed)
            continue
        assert not result.gaps
        row, = result.rows
        assert row["value"] == pytest.approx(.07)
        assert {item["event_id"]: item["d"] for item in row["inputs"] if item.get("event_id")} == {
            "D1": .20, "NEW": .20, "D2": .30,
        }
        assert sum(item.get("d") is not None for item in row["inputs"]) == 3
        assert {item["source_ref"] for item in row["inputs"] if item.get("d") == .2} == {
            "witness-D1.pdf", "witness-NEW.pdf",
        }
        action = next(item for item in row["inputs"] if item.get("event_id") == "D1")
        attestations = [proof for proof in action["field_evidence"]["d"]["corroborating_sources"]
                        if proof.get("source_ref") == "witness-D1.pdf"]
        assert len(attestations) == 2 and {proof["announcement_ref"] for proof in attestations} == {announcement}
        if result.source["id"] == ref.snapshot_id:
            assert len({proof["fact_key"] for proof in attestations}) == 2
        print(branch, result.source["id"], row["value"],
              [(item["event_id"], item["d"], item["source_ref"]) for item in row["inputs"] if "event_id" in item])


@pytest.mark.parametrize("same_identity", [False, True])
@pytest.mark.parametrize("single_revision", [False, True], ids=["fork", "single-revision"])
def test_cash_branches_compare_types_against_independent_dividend(tmp_path, same_identity, single_revision):
    doc, query, _ = _disclosure_branches("both", peer_id="D1" if same_identity else "REBUY")
    tables = doc["tables"]
    first = tables["events"][1]
    witness = {key: first[key] for key in ("code", "event_id", "kind", "ex_date", "d", "same_share_basis")}
    witness.update(source_ref="witness-D1.pdf", available_at="2024-01-01T08:00:00+08:00")
    tables["corporate_actions"].append(witness)
    if single_revision:
        tables["events"].pop(1)
    query = replace(query, cutoff=query.cutoff.replace(hour=19))
    store, ref = local_fact_source(tmp_path / "local", tables)
    for result in (rerun(tmp_path, example_query(query.factor_id)[0], doc, query), compute_factor(store, ref, query)):
        if same_identity:
            assert not result.rows
            gap, = result.gaps
            assert (gap["event"], gap["field"], gap["period"]) == ("D1", "kind", "2023-08-01")
            origins = {item["source_ref"]: item["kind"] for item in gap["evidence"]}
            assert origins["witness-D1.pdf"] == "distribution"
            assert origins["buyback-revision.pdf"] == "buyback_plan"
            continue
        assert not result.gaps
        row, = result.rows
        assert row["value"] == pytest.approx(.05)
        d1 = next(item for item in row["inputs"] if item.get("event_id") == "D1")
        assert d1["source_ref"] == "witness-D1.pdf"
        if not single_revision:
            assert {p["kind"] for p in d1["identity_evidence"]} == {"distribution", "buyback_plan"}


@pytest.mark.parametrize(("factor", "table", "value"), [
    ("dividend_yield", "corporate_actions", .05),
    ("unlock_supply_pressure", "events", .10),
])
def test_event_import_address_is_not_publication_evidence(tmp_path, factor, table, value):
    source, doc, query = example_query(factor)
    first = doc["tables"][table][0]
    address = "vendor.example/data/events.csv"
    publication = "https://issuer.example/release/17"
    for name, locator, declared in (
        ("relative", address, False),
        ("absolute", f"https://{address}", False),
        ("download", "https://vendor.example/download?id=17", False),
        ("html-rendering", "input/aggregate-events.html", False),
        ("pdf-rendering", "input/aggregate-events.pdf", False),
        ("field-locator", f"https://{address}", False),
        ("original", f"https://{address}", True),
    ):
        original = dict(first, coordinate="event-row-1", source_ref=locator)
        if name == "field-locator":
            field = "d" if factor == "dividend_yield" else "unlock_shares"
            original["field_evidence"] = {field: {"source_ref": locator}}
        if declared:
            original["announcement_ref"] = publication
        duplicate = {key: val for key, val in original.items() if key != "event_id"}
        duplicate["coordinate"] = "event-row-2"
        tables = dict(doc["tables"], **{table: [original, duplicate, *doc["tables"][table][1:]]})
        store, ref = local_fact_source(tmp_path / name, tables)
        for result in (rerun(tmp_path, source, dict(doc, tables=tables), query), compute_factor(store, ref, query)):
            if declared:
                assert not result.gaps and result.rows[0]["value"] == pytest.approx(value)
                item = next(entry for entry in result.rows[0]["inputs"] if entry.get("event_id") == first["event_id"])
                proof = item["field_evidence"]["d"] if factor == "dividend_yield" else item
                origins = proof["corroborating_sources"]
                assert {row["announcement_ref"] for row in origins} == {publication}
            else:
                assert not result.rows and result.gaps[0]["field"] == "event_identity"
                origins = result.gaps[0]["evidence"]
            assert {row["source_ref"] for row in origins} == {locator}
            assert {row["coordinate"] for row in origins} == {"event-row-1", "event-row-2"}


@pytest.mark.parametrize("shared_publication", [False, True])
def test_dividend_copy_across_assets_needs_evidence_beyond_attachment_name(tmp_path, shared_publication):
    _, doc, query = example_query("dividend_yield")
    original = doc["tables"]["corporate_actions"][0]
    original.update(coordinate="registered", source_ref="attached.pdf")
    if shared_publication:
        original["announcement_ref"] = "https://issuer.example/distributions/attached.pdf"
    duplicate = {key: value for key, value in original.items() if key != "event_id"}
    duplicate["coordinate"] = "copy"
    asset_a = local_fact_source(tmp_path / "asset-a", doc["tables"])
    asset_b = local_fact_source(tmp_path / "asset-b", {"corporate_actions": [duplicate]})
    store = asset_a[0]
    result = compute_factor(store, library(store, asset_a, asset_b), query)
    if shared_publication:
        assert not result.gaps
        row, = result.rows
        assert row["value"] == pytest.approx(.05)
        action = next(item for item in row["inputs"] if item.get("event_id") == "D1")
        evidence = action["field_evidence"]["d"]["corroborating_sources"]
    else:
        assert not result.rows
        gap, = result.gaps
        assert gap["field"] == "event_identity"
        evidence = gap["evidence"]
    assert {item["coordinate"] for item in evidence} == {"registered", "copy"}
    assert {item["source_root"] for item in evidence} == {
        str(owner.root.resolve()) for owner, _ in (asset_a, asset_b)
    }


def test_revision_does_not_retire_independent_announcements_sharing_row(tmp_path):
    doc, query, _ = _disclosure_branches("distribution")
    prior, revised, *other = doc["tables"]["events"]
    witness = next(row for row in doc["tables"]["corporate_actions"]
                   if row["code"] == "A" and row["event_id"] == "D2")
    doc["tables"]["corporate_actions"].remove(witness)
    for row in (prior, revised):
        row.update(coordinate="row1", announcement_ref="book-A")
    witness.update(coordinate="row1", announcement_ref="book-B", revision_id="v1", source_ref="book-B.pdf")
    doc["tables"]["events"] = [prior, revised, witness, *other]
    store, ref = local_fact_source(tmp_path / "local", doc["tables"])
    query = replace(query, cutoff=query.cutoff.replace(hour=19))
    for result in (rerun(tmp_path, example_query(query.factor_id)[0], doc, query), compute_factor(store, ref, query)):
        assert not result.gaps and result.rows[0]["value"] == pytest.approx(.05)
        events = {row["event_id"]: row["source_ref"] for row in result.rows[0]["inputs"] if "event_id" in row}
        assert events == {"D1": "dividend-revision.pdf", "D2": "book-B.pdf"}


@pytest.mark.parametrize("inside", [False, True])
def test_unlock_filters_disputed_announcements_by_effective_window(tmp_path, inside):
    source, doc, query = example_query("unlock_supply_pressure")
    current, = doc["tables"]["events"]
    old = dict(current, event_id="OLD1", effective_date="2023-01-15", coordinate="old", revision_id="v1",
               source_ref="old.pdf")
    branch = dict(old, revision_id="v2", supersedes="v1", source_ref="old-2.pdf",
                  effective_date=current["effective_date"] if inside else old["effective_date"])
    other = dict(old, kind="corporate", event_id="OLD2", revision_id="v3", supersedes="v1", source_ref="old-3.pdf")
    doc["tables"]["events"].extend([old, branch, other])
    store, ref = local_fact_source(tmp_path / "local", doc["tables"])
    for result in (rerun(tmp_path, source, doc, query), compute_factor(store, ref, query)):
        if inside:
            assert not result.rows and result.gaps[0]["field"] == "kind"
            assert {p["effective_date"] for p in result.gaps[0]["evidence"]} == {"2023-01-15", "2024-02-05"}
        else:
            assert not result.gaps and result.rows[0]["value"] == pytest.approx(.1)
            assert [row["event_id"] for row in result.rows[0]["inputs"]] == [current["event_id"]]


@pytest.mark.parametrize("unlock_inside", [False, True])
def test_unlock_window_checks_each_branches_kind_and_date_together(tmp_path, unlock_inside):
    source, doc, query = example_query("unlock_supply_pressure")
    current, = doc["tables"]["events"]
    old = dict(current, event_id="U", effective_date="2023-01-15", coordinate="old", revision_id="v1",
               source_ref="old.pdf")
    unlock = dict(old, revision_id="v2", supersedes="v1", source_ref="unlock.pdf",
                  effective_date=current["effective_date"] if unlock_inside else old["effective_date"])
    other = dict(old, kind="corporate", event_id="C", revision_id="v3", supersedes="v1", source_ref="corporate.pdf",
                 effective_date=current["effective_date"])
    for label, branches in (("unlock", [unlock]), ("other", [other]), ("both", [unlock, other])):
        doc["tables"]["events"] = [current, old, *branches]
        store, ref = local_fact_source(tmp_path / label, doc["tables"])
        for result in (rerun(tmp_path, source, doc, query), compute_factor(store, ref, query)):
            if unlock_inside and label == "both":
                assert not result.rows and result.gaps[0]["field"] == "kind"
                originals = {p["source_ref"] for p in result.gaps[0]["evidence"]}
                assert originals == {"old.pdf", "unlock.pdf", "corporate.pdf"}
            else:
                assert not result.gaps and result.rows[0]["value"] == pytest.approx(
                    .20 if unlock_inside and label == "unlock" else .10
                )


@pytest.mark.parametrize("equivalent", [False, True], ids=["date-conflict", "equal-window-contribution"])
def test_unlock_deduplicates_independent_originals_before_scoping_forks(tmp_path, equivalent):
    source, doc, query = example_query("unlock_supply_pressure")
    current, = doc["tables"]["events"]
    old = dict(current, event_id="OLD1", effective_date="2023-01-15", coordinate="old", revision_id="v1",
               source_ref="old.pdf")
    unlock = dict(old, revision_id="v2", supersedes="v1", source_ref="unlock.pdf")
    other = dict(old, kind="corporate", event_id="OLD2", revision_id="v3", supersedes="v1",
                 source_ref="corporate.pdf", effective_date=current["effective_date"])
    independent = dict(current, event_id="OLD1", coordinate="second-filing", source_ref="other-unlock.pdf",
                       unlock_shares=200)
    if equivalent:
        unlock.update(effective_date=independent["effective_date"], unlock_shares=200)
    for label, branches in (("unlock", [unlock]), ("other", [other]), ("both", [unlock, other])):
        doc["tables"]["events"] = [current, independent, old, *branches]
        store, ref = local_fact_source(tmp_path / label, doc["tables"])
        for result in (rerun(tmp_path, source, doc, query), compute_factor(store, ref, query)):
            if label == "other" or equivalent:
                assert not result.gaps and result.rows[0]["value"] == pytest.approx(.3)
                components = result.rows[0]["components"]
                assert components["changes"] == [(100, 1000), (200, 1000)]
                if equivalent and label == "both":
                    assert {item["source_ref"] for item in components["revision_choices"]} == {
                        "old.pdf", "unlock.pdf", "corporate.pdf",
                    }
                continue
            assert not result.rows
            gap, = result.gaps
            assert gap["subject"] == "A" and gap["event"] == "OLD1"
            expected = {"other-unlock.pdf", "unlock.pdf"}
            if label == "both":
                expected.update({"old.pdf", "corporate.pdf"})
            assert {item["source_ref"] for item in gap["evidence"]} == expected


@pytest.mark.parametrize("labelled", [True, False])
def test_unlock_does_not_count_unnumbered_copy_of_the_same_original_twice(tmp_path, labelled):
    source, doc, query = example_query("unlock_supply_pressure")
    original, = doc["tables"]["events"]
    original.update(coordinate="registered", source_ref="unlock.pdf", announcement_ref="unlock.pdf")
    duplicate = {key: value for key, value in original.items() if key != "event_id"}
    duplicate["coordinate"] = "copy"
    if not labelled:
        del original["event_id"]
    independent = dict(duplicate, event_id="OTHER", coordinate="separate", source_ref="independent.pdf",
                       announcement_ref="independent.pdf")
    for name, events, expected in (("one", [original], .1), ("copy", [original, duplicate], .1),
                                   ("both", [original, duplicate, independent], .2 if labelled else None),
                                   ("unknown", [duplicate, {key: value for key, value in independent.items()
                                                            if key != "event_id"}], None)):
        doc["tables"]["events"] = events
        store, ref = local_fact_source(tmp_path / name, doc["tables"])
        for result in (rerun(tmp_path, source, doc, query), compute_factor(store, ref, query)):
            if expected is None:
                assert not result.rows
                gap, = result.gaps
                assert gap["field"] == "event_identity"
                assert {item["source_ref"] for item in gap["evidence"]} == {"independent.pdf", "unlock.pdf"}
                continue
            assert not result.gaps and result.rows[0]["value"] == pytest.approx(expected)
            if name == "copy":
                item, = result.rows[0]["inputs"]
                assert {row["coordinate"] for row in item["corroborating_sources"]} == {"copy", "registered"}


def test_unlock_original_matches_economic_quantity_across_units_and_assets(tmp_path):
    source, doc, query = example_query("unlock_supply_pressure")
    original, = doc["tables"]["events"]
    original.update(coordinate="registered", source_ref="unlock.pdf", unlock_shares_unit="股",
                    announcement_ref="https://issuer.example/2024/A-unlock.pdf")
    duplicate = {key: value for key, value in original.items() if key != "event_id"}
    duplicate.update(coordinate="copied", unlock_shares=1, unlock_shares_unit="手")
    shares_copy = dict(duplicate, unlock_shares=100, unlock_shares_unit="股")
    separate = dict(shares_copy, announcement_ref="https://other.example/2024/A-unlock.pdf",
                    raw_note="另一个来源的导入备注")
    known_separate = dict(separate, event_id="OTHER")
    incompatible = dict(duplicate, unlock_shares=2)
    for name, events, expected in (
        ("shares", [original], .1), ("lots", [duplicate], .1), ("equivalent", [original, duplicate], .1),
        ("conflicting", [original, incompatible], None), ("known-separate", [original, known_separate], .2),
        ("unknown", [original, separate], None),
    ):
        doc["tables"]["events"] = events
        store, ref = local_fact_source(tmp_path / name, doc["tables"])
        for result in (rerun(tmp_path, source, doc, query), compute_factor(store, ref, query)):
            if expected is not None:
                assert not result.gaps
                row, = result.rows
                assert row["value"] == pytest.approx(expected)
                if name == "equivalent":
                    item, = row["inputs"]
                    assert item["effective_date"] == "2024-02-05"
                    assert {(entry["unlock_shares"], entry["unlock_shares_unit"])
                            for entry in item["corroborating_sources"]} == {(100, "股"), (1, "手")}
                continue
            assert not result.rows
            gap, = result.gaps
            if name == "unknown":
                assert gap["field"] == "event_identity"
                assert {item["announcement_ref"] for item in gap["evidence"]} == {
                    original["announcement_ref"], separate["announcement_ref"],
                }
            else:
                assert gap["field"] == "unlock"
                assert {(item["value"], item["unlock_shares_unit"]) for item in gap["evidence"]} == {
                    (100, "股"), (2, "手"),
                }
    publication = original["announcement_ref"]
    for locator in ("announcement_ref", "source_ref"):
        facts = [dict(original), dict(shares_copy)]
        if locator == "source_ref":
            for fact in facts:
                fact.update(source_ref=publication, announcement_ref="A-unlock")
        asset_a = local_fact_source(tmp_path / locator / "asset-a", {"events": [facts[0]]})
        asset_b = local_fact_source(tmp_path / locator / "asset-b", {"events": [facts[1]]})
        store = asset_a[0]
        for assets in ((asset_a, asset_b), (asset_b, asset_a)):
            result = compute_factor(store, library(store, *assets), query)
            assert not result.gaps
            row, = result.rows
            assert row["value"] == pytest.approx(.1)
            item, = row["inputs"]
            assert {entry["source_root"] for entry in item["corroborating_sources"]} == {
                str(owner.root.resolve()) for owner, _ in assets
            }
            assert {entry[locator] for entry in item["corroborating_sources"]} == {publication}


@pytest.mark.parametrize("difference", ["raw_note", "holder_type", "unlock_shares", "prior_float"])
def test_unlock_pressure_checks_only_relevant_conflicts(tmp_path, difference):
    source, doc, query = example_query("unlock_supply_pressure")
    original, = doc["tables"]["events"]
    original.update(coordinate="registered", source_ref="input/events.csv",
                    announcement_ref="https://issuer.example/2024/A-unlock.pdf", raw_note="主资产导入")
    duplicate = {key: value for key, value in original.items() if key != "event_id"}
    duplicate["coordinate"] = "copied"
    original["holder_type"] = "董事"
    duplicate["holder_type"] = "董事"
    duplicate[difference] = {"raw_note": "备份资产导入", "holder_type": "监事",
                             "unlock_shares": 200, "prior_float": 2_000}[difference]

    def verify(result, events):
        combined = len(events) == 2
        quantity_conflict = difference in ("unlock_shares", "prior_float")
        if quantity_conflict and combined:
            assert not result.rows
            gap, = result.gaps
            assert gap["field"] == "unlock" and gap["event"] == original["event_id"]
            assert {item["coordinate"]: item["value"] for item in gap["evidence"]} == {
                row["coordinate"]: row[difference] for row in (original, duplicate)
            }
            assert all(item["field"] == difference for item in gap["evidence"])
            return
        assert not result.gaps
        row, = result.rows
        chosen = events[0]
        assert row["value"] == pytest.approx(chosen["unlock_shares"] / chosen["prior_float"])
        used, = row["inputs"]
        assert used["effective_date"] == "2024-02-05"
        if combined:
            assert {item["coordinate"]: item[difference] for item in used["corroborating_sources"]} == {
                row["coordinate"]: row[difference] for row in (original, duplicate)
            }

    for name, events in (("registered", [original]), ("copied", [duplicate]), ("both", [original, duplicate])):
        doc["tables"]["events"] = events
        store, ref = local_fact_source(tmp_path / name, doc["tables"])
        for result in (rerun(tmp_path, source, doc, query), compute_factor(store, ref, query)):
            verify(result, events)

    if difference == "raw_note":
        asset_a = local_fact_source(tmp_path / "asset-a", {"events": [original]})
        asset_b = local_fact_source(tmp_path / "asset-b", {"events": [duplicate]})
        result = compute_factor(asset_a[0], library(asset_a[0], asset_a, asset_b), query)
        verify(result, [original, duplicate])
        assert {item["source_root"] for item in result.rows[0]["inputs"][0]["corroborating_sources"]} == {
            str(asset_a[0].root.resolve()), str(asset_b[0].root.resolve()),
        }
    elif difference == "holder_type":
        holder_source, holder_doc, holder_query = example_query("holder_type")
        holder_doc["tables"]["events"] = [original, dict(duplicate, event_id=original["event_id"])]
        holder_query = replace(holder_query, parameters={"event_id": original["event_id"], "kind": "unlock"})
        store, ref = local_fact_source(tmp_path / "holders", holder_doc["tables"])
        for result in (
            rerun(tmp_path, holder_source, holder_doc, holder_query), compute_factor(store, ref, holder_query),
        ):
            assert not result.rows
            gap, = result.gaps
            assert {item["value"] for item in gap["evidence"]} == {"董事", "监事"}


@pytest.mark.parametrize("equivalent", [False, True], ids=["conflict", "equivalent"])
def test_financial_amount_compares_each_original_unit(tmp_path, equivalent):
    source, doc, query = example_query("earnings_yield")
    base = doc["tables"]["financial_reports"][0]
    first = dict(base, coordinate="A-report", source_ref="A-report.pdf", net_profit_ex=7,
                 field_evidence={"net_profit_ex": {"unit": "亿元", "currency": "CNY"}})
    peer = dict(base, coordinate="B-report", source_ref="B-report.pdf", net_profit_ex=70_000 if equivalent else 7,
                field_evidence={"net_profit_ex": {"unit": "万元", "currency": "CNY"}})
    for index, records in enumerate(([first, peer], [peer, first])):
        doc["tables"]["financial_reports"] = records
        store, ref = local_fact_source(tmp_path / f"local-{index}", doc["tables"])
        for result in (rerun(tmp_path, source, doc, query), compute_factor(store, ref, query)):
            if equivalent:
                assert not result.gaps and result.rows[0]["value"] == pytest.approx(.07)
                proof = result.rows[0]["inputs"][0]["field_evidence"]["net_profit_ex"]
                evidence = proof["corroborating_sources"]
            else:
                assert not result.rows
                gap, = result.gaps
                assert (gap["subject"], gap["period"], gap["field"]) == ("A", "2023Q4", "net_profit_ex")
                evidence = gap["evidence"]
            assert {(p["source_ref"], p["value"], p["unit"], p["currency"]) for p in evidence} == {
                ("A-report.pdf", 7, "亿元", "CNY"), ("B-report.pdf", peer["net_profit_ex"], "万元", "CNY"),
            }


@pytest.mark.parametrize("clock_format", ["nested", "scoped"])
@pytest.mark.parametrize("upper", [13, 17])
def test_guidance_uses_only_public_bounds(tmp_path, upper, clock_format):
    source, doc, query = example_query("earnings_surprise")
    doc["tables"].pop("initial_reports")
    report, = doc["tables"]["financial_reports"]
    report.pop("parent_net_profit")
    report.update(coordinate="guidance", source_ref="notice.pdf", announcement_ref="notice.pdf",
                  available_at="2024-01-31T14:00:00+08:00", forecast_lower=11, forecast_upper=upper)
    stamp = "2024-01-31T18:00:00+08:00"
    if clock_format == "nested":
        report["field_evidence"] = {"forecast_upper": {
            "available_at": stamp, "source_ref": "upper.pdf", "announcement_ref": "notice.pdf",
        }}
    else:
        report["forecast_upper_available_at"] = stamp
    store, ref = local_fact_source(tmp_path / "local", doc["tables"])
    for hour in (15, 19):
        request = replace(query, cutoff=query.cutoff.replace(hour=hour))
        for result in (rerun(tmp_path, source, doc, request), compute_factor(store, ref, request)):
            if hour == 15:
                assert not result.rows and result.gaps[0]["field"] == "forecast_upper"
                readable = json.dumps(result.gaps)
                assert '"forecast_upper":' not in readable and "upper.pdf" not in readable and "T18:00" not in readable
            else:
                assert not result.gaps and result.rows[0]["value"] == pytest.approx(.2 if upper == 13 else .4)
                assert result.rows[0]["components"]["released_at"] == "2024-01-31T18:00:00+08:00"


@pytest.mark.parametrize("originals", ["same", "different", "same-storage"])
def test_split_first_guidance_uses_same_original_and_each_registration_clock(tmp_path, originals):
    source, doc, query = example_query("earnings_surprise")
    doc["tables"].pop("initial_reports")
    row = doc["tables"]["financial_reports"][0]
    row.pop("parent_net_profit")
    lower = dict(row, source_ref="guidance.pdf", coordinate="guidance-lower",
                 forecast_lower=11, available_at="2024-01-31T14:00:00+08:00")
    upper = dict(row, source_ref="guidance.pdf" if originals == "same" else "other.pdf", coordinate="guidance-upper",
                 forecast_upper=13, available_at="2024-01-31T18:00:00+08:00")
    for item in (lower, upper):
        item["announcement_ref"] = item["source_ref"]
    if originals == "same-storage":
        for item, field in ((lower, "forecast_lower"), (upper, "forecast_upper")):
            item.update(field_evidence={field: {"source_ref": item["source_ref"],
                                               "announcement_ref": item.pop("announcement_ref")}},
                        source_ref="raw/factor_financial_reports/financial_reports.csv")
    doc["tables"]["financial_reports"] = [upper, lower]
    forecast = doc["tables"]["forecasts"][0]
    doc["tables"]["forecasts"].append(dict(
        forecast, coordinate="after-lower", source_ref="after-first.pdf",
        fact_key="A|X|2023Q4|2024-01-31T16:00:00+08:00",
        available_at="2024-01-31T16:00:00+08:00", forecast_net_profit=20,
    ))
    store, ref = local_fact_source(tmp_path / "local", doc["tables"])
    for hour in (15, 19):
        request = replace(query, cutoff=query.cutoff.replace(hour=hour))
        for result in (rerun(tmp_path, source, doc, request), compute_factor(store, ref, request)):
            if hour == 15 or originals != "same":
                assert not result.rows and result.gaps[0]["field"] == "forecast_upper"
                assert "guidance-upper" not in json.dumps(result.gaps)
                continue
            row, = result.rows
            assert not result.gaps and row["value"] == pytest.approx(.20)
            detail = row["components"]
            assert (detail["disclosed"], detail["expected"], detail["released_at"]) == (
                1_200_000_000, 1_000_000_000, "2024-01-31T18:00:00+08:00",
            )
            assert detail["first_known_at"] == "2024-01-31T14:00:00+08:00"
            first = row["inputs"][0]["field_evidence"]
            for field, registered in (("forecast_lower", lower), ("forecast_upper", upper)):
                assert {key: first[field][key] for key in ("coordinate", "source_ref", "available_at")} == {
                    key: registered[key] for key in ("coordinate", "source_ref", "available_at")
                }


def test_initial_reports_rejects_nonfirst_actual_field(tmp_path):
    source, doc, query = example_query("earnings_surprise")
    doc["tables"]["financial_reports"][0].update(first_disclosure=False)
    row = doc["tables"]["initial_reports"][0]
    row.update(coordinate="notice", source_ref="first-guidance.pdf", first_disclosure=True,
               forecast_lower=11, forecast_upper=13, parent_net_profit=15, available_at="2024-01-31T14:00:00+08:00",
               field_evidence={"parent_net_profit": {"available_at": "2024-01-31T18:00:00+08:00",
                                                    "first_disclosure": False, "source_ref": "later-actual.pdf"}})
    doc["tables"]["forecasts"][0]["source_ref"] = "consensus.pdf"
    store, ref = local_fact_source(tmp_path / "local", doc["tables"])
    for hour in (15, 19):
        request = replace(query, cutoff=query.cutoff.replace(hour=hour))
        for result in (rerun(tmp_path, source, doc, request), compute_factor(store, ref, request)):
            first, = result.rows
            assert not result.gaps and first["value"] == pytest.approx(.20)
            assert first["components"]["released_at"] == row["available_at"]
            assert [item["source_ref"] for item in first["inputs"]] == ["first-guidance.pdf", "consensus.pdf"]
            assert "parent_net_profit" not in first["inputs"][0]


@pytest.mark.parametrize("unit", [None, "亿元"])
def test_unrelated_financial_original_cannot_supply_profit_unit(tmp_path, unit):
    source, doc, query = example_query("earnings_yield")
    profit = dict(doc["tables"]["financial_reports"][0], net_profit_ex=7, coordinate="profit",
                  source_ref="profit.pdf")
    profit.pop("unit")
    if unit:
        profit["unit"] = unit
    revenue = dict(profit, unit="亿元", coordinate="revenue", source_ref="revenue.pdf", revenue=1)
    revenue.pop("net_profit_ex")
    for index, reports in enumerate(([profit], [profit, revenue], [revenue, profit])):
        doc["tables"]["financial_reports"] = reports
        store, ref = local_fact_source(tmp_path / str(index), doc["tables"])
        for result in (rerun(tmp_path, source, doc, query), compute_factor(store, ref, query)):
            if unit:
                assert not result.gaps and result.rows[0]["value"] == pytest.approx(.07)
            else:
                assert not result.rows and result.gaps[0]["field"] == "currency/unit"
                assert result.gaps[0]["evidence"][0]["source_ref"] == "profit.pdf"


def test_growth_compares_scope_across_quarters(tmp_path):
    source, doc, query = example_query("revenue_growth_yoy")
    for comparable in (False, True):
        for row in doc["tables"]["financial_reports"]:
            period = row["period"]
            row.update(coordinate=period, source_ref=f"report-{period}.pdf",
                       accounting_scope="parent" if not comparable and period.startswith("2023") else "consolidated")
        store, ref = local_fact_source(tmp_path / str(comparable), doc["tables"])
        for result in (rerun(tmp_path, source, doc, query), compute_factor(store, ref, query)):
            if comparable:
                assert not result.gaps and result.rows[0]["value"] == pytest.approx(.5)
            else:
                assert not result.rows and result.gaps[0]["field"] == "accounting_scope"
                assert {(p["period"], p["accounting_scope"]) for p in result.gaps[0]["evidence"]} == {
                    ("2022Q4", "consolidated"), ("2023Q4", "parent"),
                }


@pytest.mark.parametrize("stamp", ["2024-01-01T08:00:00+08:00", "2024-01-01T00:00:00+00:00"])
def test_registered_supplementary_clock_compares_instants(tmp_path, stamp):
    _, doc, query = example_query("dividend_yield")
    for row in doc["tables"]["corporate_actions"]:
        row["coordinate"] = row["event_id"]
    store, ref = local_fact_source(tmp_path, doc["tables"], registration_times={("corporate_actions", "D2"): stamp})
    result = compute_factor(store, ref, query)
    assert not result.gaps and result.rows[0]["value"] == pytest.approx(.05)


@pytest.mark.parametrize("variant", ["missing", "cycle", "matched"])
def test_native_cash_keeps_register_when_price_table_row_is_missing(tmp_path, variant):
    _, doc, query = example_query("dividend_yield")
    august, october, _ = doc["tables"].pop("corporate_actions")
    for event, coordinate in ((august, "aug"), (october, "oct")):
        event.update(action_type="cash_dividend", coordinate=coordinate, source_ref=f"{coordinate}.pdf",
                     revision_id="v1", field_evidence={"d": {"currency": "CNY", "unit": "元/股"}})
    proofs = [august, october]
    if variant == "cycle":
        october["supersedes"] = "v2"
        proofs.append(dict(october, revision_id="v2", supersedes="v1", d=.31,
                           available_at="2024-01-31T14:00:00+08:00"))
    observed = [august, october] if variant == "matched" else [august]
    store, ref = local_fact_source(tmp_path, doc["tables"], native_actions=proofs, native_action_rows=observed)
    result = compute_factor(store, ref, query)
    if variant == "matched":
        assert not result.gaps and result.rows[0]["value"] == pytest.approx(.05)
    else:
        assert not result.rows
        gap, = result.gaps
        assert gap["period"] == "2023-10-01"
        assert gap["field"] == ("revision_cycle" if variant == "cycle" else "d")
        assert {p["announcement_ref"] for p in gap["evidence"]} == {"oct.pdf"}
        assert {p["value"] for p in gap["evidence"]} == ({.3, .31} if variant == "cycle" else {.3})


def test_guidance_keeps_bound_units_across_reports(tmp_path):
    source, doc, query = example_query("earnings_surprise")
    tables = doc["tables"]
    tables.pop("initial_reports")
    guidance, = tables["financial_reports"]
    guidance.pop("parent_net_profit")
    guidance.update(source_ref="guidance.pdf", coordinate="guidance", forecast_lower=110_000, forecast_upper=130_000,
                    field_evidence={field: {"unit": "万元"} for field in ("forecast_lower", "forecast_upper")})
    revenue = dict(code="A", period="2023Q4", revenue=1, unit="亿元", currency="CNY", flow_basis="cumulative",
                   available_at="2024-01-29T11:00:00+08:00", coordinate="revenue", source_ref="revenue.pdf",
                   first_disclosure=False)
    later_profit = dict(revenue, available_at="2024-01-30T13:00:00+08:00",
                        coordinate="later-profit", source_ref="later-profit.pdf", parent_net_profit=15)
    later_guidance = dict(revenue, available_at="2024-01-30T13:00:00+08:00",
                          coordinate="later-guidance", source_ref="later-guidance.pdf",
                          forecast_lower=13, forecast_upper=15)
    first, = tables["forecasts"]
    first.update(coordinate="consensus-early", source_ref="before-consensus.pdf")
    later = dict(first, available_at="2024-01-30T15:00:00+08:00", forecast_net_profit=20,
                 coordinate="consensus-late", source_ref="after-consensus.pdf")
    tables["forecasts"].append(later)
    variants = ([guidance], [guidance, revenue], [revenue, guidance], [later_profit, guidance],
                [guidance, later_guidance], [later_guidance, guidance])
    for case, reports in enumerate(variants):
        tables["financial_reports"] = reports
        store, ref = local_fact_source(tmp_path / str(case), tables)
        for result in (rerun(tmp_path, source, doc, query), compute_factor(store, ref, query)):
            assert not result.gaps
            row, = result.rows
            assert row["components"]["disclosed"] == 1_200_000_000
            assert row["components"]["expected"] == 1_000_000_000
            assert row["components"]["released_at"] == guidance["available_at"]
            assert row["value"] == pytest.approx(.20)
            report, forecast = row["inputs"]
            assert forecast["source_ref"] == "before-consensus.pdf"
            for field in ("forecast_lower", "forecast_upper"):
                proof = report["field_evidence"][field]
                assert proof.get("source_ref", report["source_ref"]) == "guidance.pdf"
                assert proof["unit"] == "万元"
            print(case, result.source["nature"], row["value"], row["components"], report["field_evidence"])


@pytest.mark.parametrize(("basis", "expected"), [(None, None), ("single", 2.5), ("cumulative", .5)])
def test_revenue_basis_belongs_to_revenue_original(tmp_path, basis, expected):
    source, doc, query = example_query("revenue_growth_yoy")
    common = dict(code="A", unit="亿元", currency="CNY", available_at="2024-01-31T11:00:00+08:00")
    documents = [
        dict(common, period=period, revenue=value, flow_basis="cumulative",
             coordinate=period, source_ref=f"revenue-{period}.pdf")
        for period, value in (("2022Q3", 12), ("2022Q4", 20), ("2023Q3", 16))
    ]
    fourth = dict(common, period="2023Q4", coordinate="2023Q4-revenue", source_ref="revenue-2023Q4.pdf", revenue=28)
    if basis:
        fourth["flow_basis"] = basis
    for other_basis in ("single", "cumulative"):
        profit = dict(common, period="2023Q4", coordinate="profit", source_ref="profit.pdf",
                      net_profit_ex=7, flow_basis=other_basis)
        for missing_previous_basis in (False, True):
            previous = dict(documents[-1])
            if missing_previous_basis:
                previous.pop("flow_basis")
            doc["tables"]["financial_reports"] = [*documents[:2], previous, profit, fourth]
            store, ref = local_fact_source(tmp_path / f"{other_basis}-{missing_previous_basis}", doc["tables"])
            for result in (rerun(tmp_path, source, doc, query), compute_factor(store, ref, query)):
                if expected is None or (basis == "cumulative" and missing_previous_basis):
                    assert not result.rows
                    gap, = result.gaps
                    assert (gap["subject"], gap["period"]) == ("A", "2023Q3" if basis else "2023Q4")
                    assert gap["field"] == "flow_basis"
                    assert gap["evidence"][0]["source_ref"] == f"revenue-{gap['period']}.pdf"
                    print(other_basis, missing_previous_basis, result.source["nature"], gap)
                else:
                    row, = result.rows
                    assert not result.gaps and row["value"] == pytest.approx(expected)
                    assert row["inputs"][-1]["field_evidence"]["revenue"]["source_ref"] == "revenue-2023Q4.pdf"
                    print(other_basis, missing_previous_basis, result.source["nature"], row["value"], row["components"])


@pytest.mark.parametrize(("factor", "table", "field", "witness"), [
    ("earnings_yield", "financial_reports", "net_profit_ex", 8),
    ("etf_assets", "etf_nav", "net_assets", 6_000_000),
    ("traded_amount", "daily_bars", "amount_cny", 84),
])
def test_revision_name_cannot_retire_independent_financial_quote(tmp_path, factor, table, field, witness):
    source, doc, query = example_query(factor)
    tables = doc["tables"]
    old_rows = tables[table]
    first = next(row for row in old_rows if row.get("date", str(query.trade_date)) == str(query.trade_date))
    first.update(coordinate="main", source_ref="main.pdf", revision_id="v2", supersedes="v1")
    if factor == "etf_assets":
        first.update(net_assets=5_000_000, net_assets_currency="CNY", net_assets_unit="元")
    peer = dict(first, coordinate="independent", source_ref="independent.pdf", revision_id="v1",
                supersedes="", **{field: witness})
    tables[table] = [first, peer]
    store, ref = local_fact_source(tmp_path / "local", tables)
    for result in (rerun(tmp_path, source, doc, query), compute_factor(store, ref, query)):
        assert not result.rows
        gap, = result.gaps
        assert (gap["subject"], gap["field"]) == ("A", field)
        assert {(row["source_ref"], row["value"]) for row in gap["evidence"]} == {
            ("main.pdf", first[field]), ("independent.pdf", witness),
        }


@pytest.mark.parametrize("cycle", ["clear", "inside", "outside"])
def test_dividend_revision_cycle_remains_visible(tmp_path, cycle):
    source, doc, query = example_query("dividend_yield")
    tables = doc["tables"]
    first, *others = tables["corporate_actions"]
    first.update(coordinate="main", revision_id="v1", supersedes="v2" if cycle != "clear" else "")
    if cycle == "outside":
        first["ex_date"] = "2022-12-31"
    revision = dict(first, revision_id="v2", supersedes="v1", d=.4, available_at="2024-01-30T14:00:00+08:00")
    tables["corporate_actions"] = [first, revision, *others]
    store, ref = local_fact_source(tmp_path / "local", tables)
    for result in (rerun(tmp_path, source, doc, query), compute_factor(store, ref, query)):
        if cycle == "inside":
            assert not result.rows
            gap, = result.gaps
            assert (gap["subject"], gap["field"], gap["period"], gap["event"]) == (
                "A", "revision_cycle", "2023-08-01", "D1",
            )
            assert {(item["revision_id"], item["supersedes"]) for item in gap["evidence"]} == {
                ("v1", "v2"), ("v2", "v1"),
            }
        else:
            assert not result.gaps
            assert result.rows[0]["value"] == pytest.approx(.03 if cycle == "outside" else .07)
            events = {item["event_id"] for item in result.rows[0]["inputs"] if "event_id" in item}
            assert events == ({"D2"} if cycle == "outside" else {"D1", "D2"})


@pytest.mark.parametrize("unit", ["元/股", "分/股"])
def test_native_dividend_retains_all_attestations_for_one_revision(tmp_path, unit):
    _, doc, query = example_query("dividend_yield")
    tables = doc["tables"]
    first, *other_events = tables["corporate_actions"]
    tables["corporate_actions"] = other_events
    first.update(action_type="cash_dividend", coordinate="import-a", revision_id="v1", source_ref="cash.pdf",
                 field_evidence={"d": {"unit": "元/股", "currency": "CNY"}})
    peer = dict(first, coordinate="adjusted-citation", field_evidence={"d": {"unit": unit, "currency": "CNY"}})
    for index, actions in enumerate(((first, peer), (peer, first))):
        store, ref = local_fact_source(tmp_path / str(index), tables, native_actions=actions)
        result = compute_factor(store, ref, query)
        if unit == "元/股":
            assert not result.gaps
            row, = result.rows
            assert row["value"] == pytest.approx(.05)
            event = next(item for item in row["inputs"] if item.get("ex_date") == "2023-08-01")
            evidence = event["field_evidence"]["d"]["corroborating_sources"]
        else:
            assert not result.rows
            gap, = result.gaps
            assert (gap["subject"], gap["field"], gap["period"]) == ("A", "d", "2023-08-01")
            evidence = gap["evidence"]
        assert {(item["coordinate"], item["unit"], item["value"], item["announcement_ref"])
                for item in evidence} == {
                    ("import-a", "元/股", .2, "cash.pdf"), ("adjusted-citation", unit, .2, "cash.pdf"),
                }


@pytest.mark.parametrize("october_coordinate", ["row2", "row1"], ids=["distinct", "reused"])
def test_native_dividend_revision_is_scoped_to_registered_record(tmp_path, october_coordinate):
    _, doc, query = example_query("dividend_yield")
    tables = doc["tables"]
    august, october, *remaining = tables["corporate_actions"]
    tables["corporate_actions"] = remaining
    august.update(action_type="cash_dividend", source_ref="one.pdf", coordinate="row1", revision_id="v1",
                  field_evidence={"d": {"unit": "元/股", "currency": "CNY"}})
    october.update(action_type="cash_dividend", source_ref="two.pdf", coordinate=october_coordinate, revision_id="v1",
                   field_evidence={"d": {"unit": "元/股", "currency": "CNY"}})
    corrected = dict(october, revision_id="v2", supersedes="v1")
    store, ref = local_fact_source(tmp_path, tables, native_actions=[august, october, corrected])
    result = compute_factor(store, ref, query)
    assert not result.gaps
    row, = result.rows
    assert row["value"] == pytest.approx(.05)
    used = [action for action in row["inputs"] if action.get("ex_date")]
    assert len(used) == 2
    assert {(a["ex_date"], a["d"], a["revision_id"], a["announcement_ref"], a["fact_key"]) for a in used} == {
        ("2023-08-01", .2, "v1", "one.pdf", "A|2023-08-01|d"),
        ("2023-10-01", .3, "v2", "two.pdf", "A|2023-10-01|d"),
    }
    assert {a["field_evidence"]["d"]["unit"] for a in used} == {"元/股"}


@pytest.mark.parametrize("variant", ["one_way", "cycle", "older_cycle"])
def test_report_period_correction_preserves_entire_revision_cycle(tmp_path, variant):
    source, doc, query = example_query("earnings_yield")
    tables = doc["tables"]
    old, current = tables["financial_reports"]
    old.update(coordinate="report-18", period="2023Q3", revision_id="v1",
               supersedes="v2" if variant != "one_way" else "")
    current.update(coordinate="report-18", period="2023Q4", revision_id="v2", supersedes="v1",
                   available_at="2024-01-30T14:00:00+08:00")
    if variant == "older_cycle":
        old["period"], current["period"] = "2023Q2", "2023Q3"
        tables["financial_reports"].append(
            dict(current, coordinate="separate-Q4", source_ref="另行披露的Q4", period="2023Q4",
                 revision_id="initial", supersedes="", net_profit_ex=7),
        )
    store, ref = local_fact_source(tmp_path / "local", tables)
    for result in (rerun(tmp_path, source, doc, query), compute_factor(store, ref, query)):
        if variant != "cycle":
            assert not result.gaps
            row, = result.rows
            assert row["value"] == pytest.approx(.08 if variant == "one_way" else .07)
            continue
        assert not result.rows
        gap, = result.gaps
        assert (gap["subject"], gap["field"], gap["period"]) == ("A", "revision_cycle", "2023Q4")
        assert len(gap["evidence"]) == 2
        assert {(r["source_ref"], r["period"], r["revision_id"], r["supersedes"]) for r in gap["evidence"]} == {
            ("合成财报原件", "2023Q3", "v1", "v2"), ("合成晚披露更正", "2023Q4", "v2", "v1"),
        }


@pytest.mark.parametrize("citation", ["row1", "row2"])
@pytest.mark.parametrize("same_event", [False, True], ids=["moved-correction", "independent-announcement"])
def test_native_dividend_versions_follow_registered_announcement(tmp_path, citation, same_event):
    _, doc, query = example_query("dividend_yield")
    tables = doc["tables"]
    first, second, *remaining = tables["corporate_actions"]
    original = first if same_event else second
    tables["corporate_actions"] = [second if same_event else first, *remaining]
    original.update(action_type="cash_dividend", coordinate="row1", revision_id="v1",
                    source_ref="one.pdf" if same_event else "october.pdf",
                    field_evidence={"d": {"unit": "分/股", "currency": "CNY"}})
    corrected = dict(original, coordinate=citation, revision_id="v2", supersedes="v1",
                     source_ref="two.pdf" if same_event else "october.pdf",
                     field_evidence={"d": {"unit": "元/股", "currency": "CNY"}})
    history = [original, corrected]
    if same_event:
        history.append(dict(corrected, revision_id="v1", supersedes=""))
        for row in (original, corrected):
            label = {k: v for k, v in row.items() if k not in ("d", "field_evidence", "revision_id", "supersedes")}
            tables["corporate_actions"].append(dict(label, coordinate=f"label-{row['source_ref']}",
                                                    announcement_ref=row["source_ref"]))
    store, ref = local_fact_source(tmp_path, tables, native_actions=history)
    result = compute_factor(store, ref, query)
    if same_event:
        assert not result.rows
        gap, = result.gaps
        assert (gap["field"], gap["period"]) == ("d", "2023-08-01")
        assert {(p["announcement_ref"], p["value"], p["unit"], p["revision_id"]) for p in gap["evidence"]} == {
            ("one.pdf", .2, "分/股", "v1"), ("two.pdf", .2, "元/股", "v2"),
        }
    else:
        assert not result.gaps
        row, = result.rows
        assert row["value"] == pytest.approx(.05)
        actions = [a for a in row["inputs"] if a.get("ex_date")]
        assert len(actions) == 2
        assert {(a["ex_date"], a["d"]) for a in actions} == {("2023-08-01", .2), ("2023-10-01", .3)}
        assert next(a for a in actions if a["ex_date"] == "2023-10-01")["revision_id"] == "v2"


@pytest.mark.parametrize("stale", [True, False], ids=["stale-import", "updated-import"])
def test_native_dividend_matches_only_the_active_announced_value(tmp_path, stale):
    _, doc, query = example_query("dividend_yield")
    tables = doc["tables"]
    august, october, *remaining = tables["corporate_actions"]
    tables["corporate_actions"] = [august, *remaining]
    october.update(action_type="cash_dividend", coordinate="row1", source_ref="october.pdf", revision_id="v1",
                   field_evidence={"d": {"unit": "元/股", "currency": "CNY"}})
    corrected = dict(october, d=.4, revision_id="v2", supersedes="v1", available_at="2024-01-31T18:00:00+08:00")
    store, ref = local_fact_source(tmp_path, tables, native_actions=[october, corrected],
                                   native_action_rows=[october if stale else corrected])
    late = compute_factor(store, ref, replace(query, cutoff=query.cutoff.replace(hour=19)))
    if stale:
        early = compute_factor(store, ref, query)
        assert not early.gaps and early.rows[0]["value"] == pytest.approx(.05)
        assert not late.rows
        gap, = late.gaps
        assert (gap["field"], gap["period"]) == ("d", "2023-10-01")
        assert "不匹配" in gap["reason"]
        fact, = gap["evidence"]
        assert (float(fact["observed_value"]), fact["value"], fact["available_at"], fact["announcement_ref"]) == (
            .3, .4, "2024-01-31T18:00:00+08:00", "october.pdf",
        )
    else:
        assert not late.gaps and late.rows[0]["value"] == pytest.approx(.06)
        action = next(a for a in late.rows[0]["inputs"] if a.get("ex_date") == "2023-10-01")
        assert (action["d"], action["revision_id"]) == (.4, "v2")


@pytest.mark.parametrize(("observed_at", "registered_at", "matched"), [
    ("2024-01-31T18:00:00+08:00", None, True),
    ("2024-01-31T10:00:00+00:00", None, True),
    ("2024-01-31T18:00:00+08:00", "2024-01-31T10:00:00+00:00", True),
    ("2024-01-31T10:01:00+00:00", None, False),
    ("2024-01-31T18:00:00", None, False),
    ("2024-01-31", None, False),
])
def test_native_dividend_timestamps_compare_public_instants(tmp_path, observed_at, registered_at, matched):
    store, ref, query = native_dividend_correction(tmp_path, observed_at=observed_at, registered_at=registered_at)
    early = compute_factor(store, ref, query)
    assert not early.rows
    gap, = early.gaps
    assert (gap["subject"], gap["field"], gap["period"]) == ("A", "d", "2023-10-01")
    proof, = gap["evidence"]
    assert proof["value"] == .3 and proof["revision_id"] == "v1"
    assert "缺少" in gap["reason"] and "observed_at" not in proof and "observed_value" not in proof
    assert observed_at not in json.dumps(early.gaps) and "0.4" not in json.dumps(early.gaps)
    late = compute_factor(store, ref, replace(query, cutoff=query.cutoff.replace(hour=19)))
    if matched:
        assert not late.gaps and late.rows[0]["value"] == pytest.approx(.06)
        action = next(item for item in late.rows[0]["inputs"] if item.get("ex_date") == "2023-10-01")
        assert action["revision_id"] == "v2" and action["announcement_ref"] == "october.pdf"
        proof = action["field_evidence"]["d"]
        assert proof["observed_at"] == observed_at and proof["available_at"] == "2024-01-31T18:00:00+08:00"
    else:
        assert not late.rows and late.gaps[0]["field"] == "d"


def test_native_cycle_evidence_respects_cutoff(tmp_path):
    early_evidence = []
    for value in (.40, .90):
        store, ref, query = native_dividend_correction(
            tmp_path / str(value), observed_at="2024-01-31T18:00:00+08:00", cycle_value=value,
        )
        for hour in (15, 19):
            result = compute_factor(store, ref, replace(query, cutoff=query.cutoff.replace(hour=hour)))
            gap, = result.gaps
            assert not result.rows and (gap["subject"], gap["field"], gap["period"]) == (
                "A", "revision_cycle", "2023-10-01",
            )
            assert {(p["revision_id"], p["supersedes"], p["value"]) for p in gap["evidence"]} == {
                ("v1", "v2", .30), ("v2", "v1", .31),
            }
            assert all(p["announcement_ref"] == "october.pdf" for p in gap["evidence"])
            if hour == 15:
                forbidden = {"observed_at", "observed_value", "matched"}
                assert all(not forbidden.intersection(p) for p in gap["evidence"])
                early_evidence.append([
                    {k: v for k, v in p.items() if k not in {"snapshot_id", "source_root"}} for p in gap["evidence"]
                ])
            else:
                assert all(float(p["observed_value"]) == value for p in gap["evidence"])
            print(hour, gap)
    assert early_evidence[0] == early_evidence[1]
    store, ref, query = native_dividend_correction(tmp_path / "linear", observed_at="2024-01-31T18:00:00+08:00")
    for stamp in ("2024-01-31T18:00:00+08:00", "2024-01-31T10:00:00+00:00"):
        result = compute_factor(store, ref, replace(query, cutoff=datetime.fromisoformat(stamp)))
        assert not result.gaps and result.rows[0]["value"] == pytest.approx(.06)


@pytest.mark.parametrize("cycle", [False, True])
def test_earnings_yield_reads_profit_outside_revenue_cycle(tmp_path, cycle):
    source, doc, query = example_query("earnings_yield")
    report = doc["tables"]["financial_reports"][0]
    profit = dict(report, coordinate="net-profit", source_ref="profit.pdf", net_profit_ex=7)
    first = dict(report, coordinate="revenue", source_ref="revenue-v1.pdf", revenue=11, revision_id="v1",
                 supersedes="v2" if cycle else "")
    first.pop("net_profit_ex")
    second = dict(first, source_ref="revenue-v2.pdf", revenue=12, revision_id="v2", supersedes="v1")
    # Put the unrelated disclosures first to verify which original provided the field.
    doc["tables"]["financial_reports"] = [first, second, profit]
    store, ref = local_fact_source(tmp_path / "local", doc["tables"])
    for result in (rerun(tmp_path, source, doc, query), compute_factor(store, ref, query)):
        assert not result.gaps
        row, = result.rows
        assert row["value"] == pytest.approx(.07)
        assert row["inputs"][0]["field_evidence"]["net_profit_ex"]["source_ref"] == "profit.pdf"


@pytest.mark.parametrize("variant", ["one-way", "cycle", "cycle-with-independent-cap"])
def test_market_cap_cycle_follows_the_consumed_field(tmp_path, variant):
    ep_source, doc, ep = example_query("earnings_yield")
    cap_source, cap_facts, cap = example_query("total_market_cap")
    tables = doc["tables"]
    tables["market_cap"] = [
        dict(code="A", date="2024-01-30", market_cap_cny=50e8, available_at="2024-01-30T14:50:00+08:00",
             coordinate="provider", source_ref="cap-v1.csv", revision_id="v1",
             supersedes="v2" if variant != "one-way" else ""),
        dict(code="A", date="2024-01-31", market_cap_cny=100e8, available_at="2024-01-31T14:50:00+08:00",
             coordinate="provider", source_ref="cap-v2.csv", revision_id="v2", supersedes="v1"),
    ]
    if variant == "cycle-with-independent-cap":
        tables.update({name: rows for name, rows in cap_facts["tables"].items() if name != "market_cap"})
    store, ref = local_fact_source(tmp_path / "local", tables)
    expected_cap = {"one-way": 100e8, "cycle-with-independent-cap": 1e7}.get(variant)
    for source, query, expected in ((ep_source, ep, .06 if variant == "one-way" else None),
                                    (cap_source, cap, expected_cap)):
        for result in (rerun(tmp_path, source, doc, query), compute_factor(store, ref, query)):
            if expected is None:
                assert not result.rows
                gap, = result.gaps
                assert gap["field"] == "revision_cycle"
                assert {(p["date"], p["source_ref"], p["supersedes"]) for p in gap["evidence"]} == {
                    ("2024-01-30", "cap-v1.csv", "v2"), ("2024-01-31", "cap-v2.csv", "v1"),
                }
            else:
                assert not result.gaps and result.rows[0]["value"] == pytest.approx(expected)
                if variant == "cycle-with-independent-cap":
                    assert result.rows[0]["components"] == {"shares": 1e6, "close_raw": 10.0}


@pytest.mark.parametrize("independent", [False, True], ids=["attested", "independent"])
def test_legacy_teaching_revision_uses_only_attested_originals(tmp_path, independent):
    source, doc, query = example_query("earnings_yield")
    source = type(source)("archived-teaching-copy", root=tmp_path)
    original, corrected = doc["tables"]["financial_reports"]
    for row in (original, corrected):
        row.pop("coordinate")
    if independent:
        original["source_ref"] = "未获更正关系的独立财报"
    raw = json.dumps(doc, ensure_ascii=False).encode()
    path = tmp_path / f"{source.case_id}.json"
    path.write_bytes(raw)
    early = compute_factor(None, source, query)
    assert not early.gaps and early.rows[0]["value"] == pytest.approx(.06)
    assert early.rows[0]["inputs"][0]["source_ref"] == original["source_ref"]
    late = compute_factor(None, source, replace(query, cutoff=query.cutoff.replace(hour=19)))
    if independent:
        assert not late.rows
        gap, = late.gaps
        assert gap["field"] == "net_profit_ex"
        assert {item["source_ref"] for item in gap["evidence"]} == {original["source_ref"], corrected["source_ref"]}
    else:
        assert not late.gaps and late.rows[0]["value"] == pytest.approx(.08)
        report = late.rows[0]["inputs"][0]
        assert report["source_ref"] == corrected["source_ref"]
        assert report["revision_evidence_ref"] == "examples/_revision_links.json#legacy-quarterly-report"
    assert path.read_bytes() == raw


@pytest.mark.parametrize("variant", ["original", "copy_original", "copy_correction", "distinct", "reused",
                                     "same_instant", "different_instant", "date_only", "naive"])
def test_legacy_revision_links_are_scoped_to_attested_versions(tmp_path, variant):
    source, doc, query = example_query("earnings_yield")
    rows = doc["tables"]["financial_reports"]
    old, new = rows
    coordinate = old.pop("coordinate")
    new.pop("coordinate")
    if variant in ("copy_original", "copy_correction"):
        rows.append(dict(old if variant == "copy_original" else new))
    if variant in ("distinct", "reused"):
        rows.append(dict(old, source_ref="独立公告乙.pdf", net_profit_ex=7,
                         coordinate=coordinate if variant == "reused" else "independent"))
    if variant in ("same_instant", "different_instant", "date_only", "naive"):
        new["available_at"] = {
            "same_instant": "2024-01-31T10:00:00+00:00",
            "different_instant": "2024-01-31T10:30:00+00:00",
            "date_only": "2024-01-31",
            "naive": "2024-01-31T18:00:00",
        }[variant]
    for hour in (15, 19):
        request = replace(query, cutoff=query.cutoff.replace(hour=hour))
        result = rerun(tmp_path, source, doc, request)
        if variant in ("distinct", "reused") or variant == "different_instant" and hour == 19:
            assert not result.rows
            gap, = result.gaps
            assert gap["field"] == "net_profit_ex"
            expected = {(old["source_ref"], 6), (new["source_ref"], 8)} if variant == "different_instant" else {
                ("独立公告乙.pdf", 7), (old["source_ref"], 6) if hour == 15 else (new["source_ref"], 8),
            }
            assert {(proof["source_ref"], proof["value"]) for proof in gap["evidence"]} == expected
        else:
            keep_old = hour == 15 or variant in ("date_only", "naive")
            assert not result.gaps
            row, = result.rows
            assert row["value"] == pytest.approx(.06 if keep_old else .08)
            report = row["inputs"][0]
            selected = old if keep_old else new
            assert (report["source_ref"], report["available_at"]) == (selected["source_ref"], selected["available_at"])
            assert bool(report.get("revision_evidence_ref")) is not keep_old


@pytest.mark.parametrize("factor", ["dividend_yield", "ex_distribution_return", "low_volatility_36m"])
@pytest.mark.parametrize(
    ("cash", "unit", "currency"), [(0.2, "元/股", "CNY"), (20, "分/股", "CNY"), (0.2, "USD/股", "USD")]
)
def test_cash_per_old_share_is_consistent_in_all_consuming_factors(tmp_path, factor, cash, unit, currency):
    source, doc, query = example_query(factor)
    tables = doc["tables"]
    if factor == "low_volatility_36m":
        first, *following = tables["daily_bars"]
        first["close_raw"] = 10
        for bar in following:
            bar["close_raw"] = 9.8
        tables["corporate_actions"].append(
            dict(
                code="A",
                event_id="D36",
                action_type="cash_dividend",
                same_share_basis=True,
                ex_date=following[0]["date"],
                available_at=following[0]["available_at"],
            )
        )
    event = tables["corporate_actions"][0]
    event.update(
        d=cash, source_ref="每旧股现金公告", currency="CNY", field_evidence={"d": {"unit": unit, "currency": currency}}
    )
    store, ref = local_fact_source(tmp_path / "local", tables)
    for kind, result in (
        ("teaching", rerun(tmp_path, source, doc, query)),
        ("local", compute_factor(store, ref, query)),
    ):
        print(factor, kind, unit, [row["value"] for row in result.rows], result.gaps)
        if currency == "USD":
            assert not result.rows
            gap = result.gaps[0]
            assert (gap["subject"], gap["period"], gap["event"], gap["field"]) == (
                "A",
                event["ex_date"],
                event["event_id"],
                "currency",
            )
            assert [(item["source_ref"], item["field_evidence"]["d"]["currency"]) for item in gap["evidence"]] == [
                ("每旧股现金公告", "USD")
            ]
        else:
            assert not result.gaps
            row = result.rows[0]
            original = next(item for item in row["inputs"] if item.get("event_id") == event["event_id"])
            assert (original["d"], original["field_evidence"]["d"]["unit"]) == (cash, unit)
            if factor == "dividend_yield":
                assert row["value"] == pytest.approx(0.05)
                assert row["components"]["cash"] == pytest.approx(0.5)
            else:
                assert row["value"] == pytest.approx(0)
                if factor == "ex_distribution_return":
                    assert row["components"]["cash"] == pytest.approx(0.2)
                    assert row["components"]["ratio"] == 2
                else:
                    assert row["components"]["returns"] == pytest.approx([0] * 36)


@pytest.mark.parametrize("duplicate_cash", [20, 0.2])
def test_composite_deduplicates_distribution_across_fact_tables(tmp_path, duplicate_cash):
    source, doc, query = example_query(
        "weighted_factor_score",
        params={
            "components": {name: {"weight": 1, "direction": 1} for name in ("dividend_yield", "insider_net_purchase")}
        },
    )
    _, dividends, _ = example_query("dividend_yield")
    _, holdings, _ = example_query("insider_net_purchase")
    tables = doc["tables"]
    for name in ("corporate_actions", "action_coverage"):
        tables[name] = [dict(row, code=code) for code in query.subjects for row in dividends["tables"][name]]
    for bar in tables["daily_bars"]:
        bar["close_raw"] = 10
    first = tables["corporate_actions"][0]
    first.update(source_ref="除息行动原件", field_evidence={"d": {"unit": "元/股", "currency": "CNY"}})
    duplicate = dict(
        first, d=duplicate_cash, source_ref="分红事件原件", field_evidence={"d": {"unit": "分/股", "currency": "CNY"}}
    )
    tables["events"] = [
        duplicate,
        *(dict(row, code=code) for code in query.subjects for row in holdings["tables"]["events"]),
    ]
    store, ref = local_fact_source(tmp_path / "local", tables)
    for kind, result in (
        ("teaching", rerun(tmp_path, source, doc, query)),
        ("local", compute_factor(store, ref, query)),
    ):
        values = [row["components"]["components"]["dividend_yield"]["raw"] for row in result.rows]
        print("composite two tables", kind, duplicate_cash, values, result.gaps)
        if duplicate_cash == 20:
            assert not result.gaps
            assert [row["subject"] for row in result.rows] == list(query.subjects)
            assert values == pytest.approx([0.05] * 3)
        else:
            assert not result.rows
            gap = result.gaps[0]
            assert (gap["subject"], gap["period"], gap["event"], gap["field"]) == (
                "A",
                "2023-08-01",
                "D1",
                "components.dividend_yield.d",
            )
            assert {item["source_ref"] for item in gap["evidence"]} == {"除息行动原件", "分红事件原件"}


def test_group_amount_keeps_conflicting_currency_attestations(tmp_path):
    cny, cny_ref = local_volume_source(tmp_path / "cny")
    usd, usd_ref = local_volume_source(tmp_path / "usd", amount_unit="USD")
    _, doc, request = example_query("up_amount_share")
    query = replace(request, factor_id="traded_amount")
    assert compute_factor(cny, cny_ref, query).rows[0]["value"] == 15_000_000
    assert compute_factor(usd, usd_ref, query).gaps[0]["field"] == "currency"
    final = next(row for row in doc["tables"]["daily_bars"] if row["date"] == str(query.trade_date))
    extra = local_fact_source(
        tmp_path / "supplement", {"daily_bars": [dict(final, source_ref="补充成交额原件", currency="CNY", unit="CNY")]}
    )
    original = [(cny, cny_ref), (usd, usd_ref)]
    mixed = [*original, extra]
    for assets in (original, mixed, list(reversed(mixed))):
        result = compute_factor(cny, library(cny, *assets), query)
        print("成交额数据组", len(assets), result.status, [(row["value"], row["unit"]) for row in result.rows])
        assert not result.rows
        gap = result.gaps[0]
        assert (gap["subject"], gap["field"], gap["period"]) == ("SYN_A", "amount_cny", "2024-04-01")
        expected = {("CNY", str(cny.root.resolve())), ("USD", str(usd.root.resolve()))}
        if len(assets) == 3:
            expected.add(("CNY", str(extra[0].root.resolve())))
        assert {(item["unit"], item["source_root"]) for item in gap["evidence"]} == expected


@pytest.mark.parametrize(
    ("native_unit", "extra_unit", "extra_amount", "conflict"),
    [
        ("USD", "元", 15_000_000, True),
        ("CNY", "元", 15_000_000, False),
        ("CNY", "万元", 1_500, False),
        ("CNY", "万元", 1_600, True),
        ("CNY", "万元", 15_000_000, True),
        ("CNY", "元", 16_000_000, True),
    ],
)
def test_group_compares_one_native_and_one_supplementary_quote(
    tmp_path, native_unit, extra_unit, extra_amount, conflict
):
    native = local_volume_source(tmp_path / "native", amount_unit=native_unit)
    _, doc, request = example_query("up_amount_share")
    query = replace(request, factor_id="traded_amount")
    final = next(row for row in doc["tables"]["daily_bars"] if row["date"] == str(query.trade_date))
    supplemental = dict(final, source_ref="补充成交额原件", currency="CNY", unit=extra_unit, amount_cny=extra_amount)
    if extra_unit == "万元":
        supplemental.update(unit="元", field_evidence={"amount_cny": {"unit": extra_unit, "currency": "CNY"}})
    extra = local_fact_source(
        tmp_path / "supplement", {"daily_bars": [supplemental], "calendar": doc["tables"]["calendar"]}
    )
    if native_unit == "CNY":
        supplemental_yuan = extra_amount * (10_000 if extra_unit == "万元" else 1)
        for (store, ref), expected in ((native, 15_000_000), (extra, supplemental_yuan)):
            standalone = compute_factor(store, ref, query)
            assert not standalone.gaps and standalone.rows[0]["value"] == expected
    for assets in ((native, extra), (extra, native)):
        result = compute_factor(native[0], library(native[0], *assets), query)
        print("原生与补充成交额", native_unit, extra_unit, result.status, [row["value"] for row in result.rows])
        if not conflict:
            assert not result.gaps and result.rows[0]["value"] == 15_000_000
            if extra_unit == "万元":
                evidence = result.rows[0]["inputs"][0]["field_evidence"]["amount_cny"]["corroborating_sources"]
                assert {(item["value"], item["unit"], item["source_root"]) for item in evidence} == {
                    (15_000_000, "CNY", str(native[0].root.resolve())),
                    (1_500, "万元", str(extra[0].root.resolve())),
                }
            continue
        assert not result.rows
        gap = result.gaps[0]
        assert (gap["subject"], gap["field"], gap["period"]) == ("SYN_A", "amount_cny", "2024-04-01")
        assert {(item["unit"], item["source_root"]) for item in gap["evidence"]} == {
            (native_unit, str(native[0].root.resolve())), (extra_unit, str(extra[0].root.resolve()))
        }


def test_etf_premium_normalizes_fen_per_share(tmp_path):
    source, doc, query = example_query("etf_premium")
    doc["tables"]["daily_bars"][0].update(close_raw=204, currency="CNY", unit="分/份")
    doc["tables"]["etf_nav"][0]["unit"] = "元/份"
    store, ref = local_fact_source(tmp_path / "local", doc["tables"])
    for result in (rerun(tmp_path, source, doc, query), compute_factor(store, ref, query)):
        assert result.rows[0]["value"] == pytest.approx(0.02)
        assert result.rows[0]["components"]["price"] == pytest.approx(2.04)
        assert result.rows[0]["inputs"][-1]["unit"] == "分/份"


def test_group_corroborates_two_supplementary_amounts_in_different_units(tmp_path):
    _, doc, request = example_query("up_amount_share")
    query = replace(request, factor_id="traded_amount")
    final = next(row for row in doc["tables"]["daily_bars"] if row["date"] == str(query.trade_date))
    assets = [
        local_fact_source(
            tmp_path / unit,
            {
                "daily_bars": [dict(final, amount_cny=value, unit=unit, source_ref=unit)],
                "calendar": doc["tables"]["calendar"],
            },
        )
        for value, unit in ((7_000_000, "元"), (0.07, "亿元"))
    ]
    for order in (assets, assets[::-1]):
        result = compute_factor(assets[0][0], library(assets[0][0], *order), query)
        assert not result.gaps and result.rows[0]["value"] == 7_000_000


@pytest.mark.parametrize(
    ("nav", "unit", "published", "expected"),
    [(204, "分/份", None, 2040), (2.04, "元/份", None, 2040), (204, "分/份", 5_000_000, 5_000_000)],
)
def test_etf_assets_normalizes_nav_before_multiplying_shares(tmp_path, nav, unit, published, expected):
    source, doc, query = example_query("etf_assets")
    tables = doc["tables"]
    original = tables["etf_nav"][0]
    original.update(nav=nav, unit=unit, source_ref="ETF 净值原件")
    if published is None:
        original.pop("net_assets")
    else:
        original["net_assets"] = published
    tables["etf_shares"] = [
        dict(code="A", date=str(query.trade_date), shares=1000, unit="份", available_at=original["available_at"])
    ]
    store, ref = local_fact_source(tmp_path / "local", tables)
    for result in (rerun(tmp_path, source, doc, query), compute_factor(store, ref, query)):
        row = result.rows[0]
        print(result.source["nature"], "ETF 规模", row["value"], row["unit"], row["components"])
        assert not result.gaps
        assert (row["subject"], row["value"], row["unit"]) == ("A", expected, "人民币元")
        assert row["inputs"][-1]["unit"] == unit
        if published is None:
            assert row["components"] == {"shares": 1000, "nav": 2.04}
        else:
            assert row["components"]["basis"] == "公布净资产"


@pytest.mark.parametrize(
    ("value", "unit", "explicit_field"), [(500, "万元", True), (5_000_000, "元", True), (500, "万元", False)]
)
def test_etf_normalizes_published_net_assets_in_their_own_unit(tmp_path, value, unit, explicit_field):
    source, doc, query = example_query("etf_assets")
    original = doc["tables"]["etf_nav"][0]
    original.update(net_assets=value, unit="分/份" if explicit_field else unit, source_ref="ETF 净资产原件")
    if explicit_field:
        original["net_assets_unit"] = unit
    store, ref = local_fact_source(tmp_path / "local", doc["tables"])
    for result in (rerun(tmp_path, source, doc, query), compute_factor(store, ref, query)):
        row = result.rows[0]
        print(result.source["nature"], "公告净资产", value, unit, row["value"], row["unit"])
        assert not result.gaps and (row["value"], row["unit"]) == (5_000_000, "人民币元")
        assert row["components"]["basis"] == "公布净资产"
        assert row["inputs"][0]["net_assets"] == value
        assert row["inputs"][0]["net_assets_unit" if explicit_field else "unit"] == unit


@pytest.mark.parametrize("fen", [False, True])
def test_etf_tracking_error_normalizes_each_nav_observation(tmp_path, fen):
    source, doc, query = example_query("etf_tracking_error")
    for row in doc["tables"]["etf_nav"]:
        changed = fen and row["code"] == "A" and row["date"] == "2024-01-30"
        row.update(nav=200 if changed else 2, unit="分/份" if changed else "元/份")
    store, ref = local_fact_source(tmp_path / "local", doc["tables"])
    for result in (rerun(tmp_path, source, doc, query), compute_factor(store, ref, query)):
        row = result.rows[0]
        print(result.source["nature"], "跟踪误差", fen, row["components"]["differences"], row["value"])
        assert not result.gaps and row["value"] == 0
        assert row["components"]["differences"] == [0, 0]
        if fen:
            assert row["inputs"][1]["unit"] == "分/份"


@pytest.mark.parametrize("yuan_value", [500, 5_000_000])
def test_etf_compares_net_assets_using_each_original_field_unit(tmp_path, yuan_value):
    source, doc, query = example_query("etf_assets")
    base = doc["tables"]["etf_nav"][0]
    announcements = [
        dict(base, net_assets=value, source_ref=f"公告-{unit}",
             field_evidence={"net_assets": {"unit": unit, "currency": "CNY"}})
        for value, unit in ((yuan_value, "元"), (500, "万元"))
    ]
    assets = [local_fact_source(tmp_path / str(i), {"etf_nav": [row]}) for i, row in enumerate(announcements)]
    for order in ((0, 1), (1, 0)):
        doc["tables"]["etf_nav"] = [announcements[i] for i in order]
        for result in (
            rerun(tmp_path, source, doc, query),
            compute_factor(assets[0][0], library(assets[0][0], *(assets[i] for i in order)), query),
        ):
            print("净资产双公告", yuan_value, order, result.status, [row["value"] for row in result.rows])
            expected = {(yuan_value, "元", "公告-元"), (500, "万元", "公告-万元")}
            if yuan_value == 500:
                assert not result.rows
                gap = result.gaps[0]
                assert (gap["subject"], gap["period"], gap["field"]) == ("A", "2024-01-31", "net_assets")
                evidence = gap["evidence"]
            else:
                assert not result.gaps and result.rows[0]["value"] == 5_000_000
                proof = result.rows[0]["inputs"][0]["field_evidence"]["net_assets"]
                evidence = proof["corroborating_sources"]
            assert {(item["value"], item["unit"], item["source_ref"]) for item in evidence} == expected


@pytest.mark.parametrize(("code", "currency"), [("A", "USD"), ("B", "USD"), ("A", "CNY")])
def test_etf_tracking_uses_field_currency_at_each_valuation(tmp_path, code, currency):
    source, doc, query = example_query("etf_tracking_error")
    for row in doc["tables"]["etf_nav"]:
        row["nav"] = 2
        if (row["code"], row["date"]) == (code, "2024-01-30"):
            row["field_evidence"] = {"nav": {"currency": currency}}
    store, ref = local_fact_source(tmp_path / "local", doc["tables"])
    for result in (rerun(tmp_path, source, doc, query), compute_factor(store, ref, query)):
        print("逐日净值币种", code, currency, result.status, [row["value"] for row in result.rows])
        if currency == "CNY":
            assert not result.gaps and result.rows[0]["value"] == 0
            assert result.rows[0]["components"]["differences"] == [0, 0]
        else:
            assert not result.rows
            gap = result.gaps[0]
            assert (gap["subject"], gap["period"], gap["field"]) == (code, "2024-01-30", "currency")
            assert gap["evidence"][0]["field_evidence"]["nav"]["currency"] == "USD"


def test_etf_published_net_assets_needs_no_nav_or_row_currency(tmp_path):
    source, doc, query = example_query("etf_assets")
    original = doc["tables"]["etf_nav"][0]
    original.pop("nav")
    original.pop("currency")
    original.update(net_assets=500, net_assets_unit="万元", net_assets_currency="CNY", source_ref="独立净资产公告")
    store, ref = local_fact_source(tmp_path / "local", doc["tables"])
    for result in (rerun(tmp_path, source, doc, query), compute_factor(store, ref, query)):
        print("仅公告净资产", result.status, [row["value"] for row in result.rows], result.gaps)
        assert not result.gaps and result.rows[0]["value"] == 5_000_000
        assert result.rows[0]["inputs"][0]["source_ref"] == "独立净资产公告"


def test_etf_field_currency_also_controls_premium_and_share_based_assets(tmp_path):
    source, doc, query = example_query("etf_premium")
    nav = doc["tables"]["etf_nav"][0]
    nav.pop("net_assets")
    nav["field_evidence"] = {"nav": {"currency": "USD"}}
    doc["tables"]["etf_shares"] = [
        dict(code="A", date=str(query.trade_date), shares=1000, unit="份", available_at=nav["available_at"])
    ]
    store, ref = local_fact_source(tmp_path / "local", doc["tables"])
    for identity in ("etf_premium", "etf_assets"):
        selected = replace(query, factor_id=identity)
        for result in (rerun(tmp_path, source, doc, selected), compute_factor(store, ref, selected)):
            assert not result.rows and result.gaps[0]["field"] == "currency"


def test_etf_selects_cutoff_versions_before_comparing_asset_field_units(tmp_path):
    source, doc, query = example_query("etf_assets")
    original = doc["tables"]["etf_nav"][0]
    original.update(
        net_assets=500, revision_id="first", coordinate="announcement",
        field_evidence={"net_assets": {"currency": "CNY", "unit": "万元"}},
    )
    correction = dict(
        original, net_assets=600, available_at="2024-01-31T17:00:00+08:00", revision_id="second", supersedes="first",
    )
    doc["tables"]["etf_nav"].append(correction)
    store, ref = local_fact_source(tmp_path / "local", doc["tables"])
    for cutoff, expected in ((query.cutoff, 5_000_000), (query.cutoff.replace(hour=18), 6_000_000)):
        selected = replace(query, cutoff=cutoff)
        for result in (rerun(tmp_path, source, doc, selected), compute_factor(store, ref, selected)):
            assert not result.gaps and result.rows[0]["value"] == expected


@pytest.mark.parametrize("factor_id", ["earnings_surprise", "expected_growth"])
def test_individual_forecasts_cannot_impersonate_consensus(tmp_path, factor_id):
    source, doc, query = example_query(factor_id)
    rows = doc["tables"]["forecasts"]
    for index, row in enumerate(rows):
        row["forecast_kind"] = "individual"
        if factor_id == "earnings_surprise":
            row.pop("institution")
            row["forecast_net_profit"] = 14
        else:
            row["institution"] = "XY"[index]
    store, ref = local_fact_source(tmp_path / "local", doc["tables"])
    for result in (rerun(tmp_path, source, doc, query), compute_factor(store, ref, query)):
        assert not result.rows and result.gaps[0]["field"] == "forecast_aggregation"


@pytest.mark.parametrize("factor_id", ["price_momentum", "absolute_momentum", "risk_adjusted_momentum"])
def test_native_momentum_reads_same_lookback_as_teaching(tmp_path, factor_id):
    source, doc, query = example_query("up_amount_share")
    params = {"window": 19, "skip": 12}
    if factor_id == "risk_adjusted_momentum":
        params["vol_window"] = 8
        params["epsilon"] = 0.01
    query = replace(query, factor_id=factor_id, parameters=params)
    store, ref = local_volume_source(tmp_path / "local")
    teaching = rerun(tmp_path, source, doc, query).rows[0]
    for selected in (ref, library(store, (store, ref))):
        result = compute_factor(store, selected, query)
        assert result.rows, result.gaps
        assert result.rows[0]["value"] == pytest.approx(teaching["value"])
        assert result.rows[0]["inputs"][0]["date"] == teaching["inputs"][0]["date"] == "2024-02-16"


def test_correlation_accepts_proven_synchronized_exchanges(tmp_path):
    source, doc, query = example_query("rolling_return_correlation")
    for row in doc["tables"]["daily_bars"]:
        row.update(
            market="SH" if row["code"] == "A" else "SZ",
            calendar_id="CNA",
            close_timestamp=f"{row['date']}T15:00:00+08:00",
            currency="CNY",
            return_basis="price",
        )
    store, ref = local_fact_source(tmp_path / "local", doc["tables"])
    for result in (rerun(tmp_path, source, doc, query), compute_factor(store, ref, query)):
        assert result.rows and result.rows[0]["value"] == pytest.approx(1)
        assert result.rows[0]["components"]["markets"] == ["SH", "SZ"]
    for row in doc["tables"]["daily_bars"]:
        if row["code"] == "B":
            row["close_timestamp"] = f"{row['date']}T07:00:00Z"
    assert rerun(tmp_path, source, doc, query).rows[0]["value"] == pytest.approx(1)
    doc["tables"]["daily_bars"][-1]["close_timestamp"] = "2024-01-31T08:00:00Z"
    assert rerun(tmp_path, source, doc, query).gaps[0]["field"] == "calendar"
    doc["tables"]["daily_bars"][-1]["close_timestamp"] = "2024-01-31T07:00:00Z"
    for row in doc["tables"]["daily_bars"]:
        row.pop("calendar_id")
    assert rerun(tmp_path, source, doc, query).gaps[0]["field"] == "calendar"


@pytest.mark.parametrize(
    "variant", ["naive", "different", "synchronized", "synchronized_time", "old_quote", "old_quote_misaligned"]
)
def test_cross_market_correlation_requires_comparable_close_instants(tmp_path, variant):
    source, doc, query = example_query("rolling_return_correlation")
    synchronized = variant.startswith("synchronized") or variant == "old_quote"
    for row in doc["tables"]["daily_bars"]:
        first = row["code"] == "A"
        row.update(
            market="SH" if first else ("SZ" if synchronized or variant == "old_quote_misaligned" else "NYSE"),
            calendar_id="COMMON-ROWS",
            currency="CNY",
            return_basis="price",
        )
        if variant == "naive":
            row["close_time"] = "15:00"
        elif variant == "synchronized_time":
            row["close_time"] = "15:00+08:00" if first else "07:00Z"
        else:
            instant = "15:00:00+08:00" if first else ("07:00:00Z" if synchronized else "15:00:00-05:00")
            row["close_timestamp"] = f"{row['date']}T{instant}"
    if variant.startswith("old_quote"):
        bars = doc["tables"]["daily_bars"]
        old_close = "2018-01-02T16:00:00+08:00"
        bars.insert(0, dict(bars[0], date="2018-01-02", market="HK", close_timestamp=old_close, available_at=old_close))
    store, ref = local_fact_source(tmp_path / "local", doc["tables"])
    for result in (rerun(tmp_path, source, doc, query), compute_factor(store, ref, query)):
        print(result.source["nature"], variant, [row["value"] for row in result.rows],
              [(gap["field"], gap["reason"]) for gap in result.gaps])
        if synchronized:
            assert not result.gaps and result.rows[0]["value"] == pytest.approx(1)
            assert result.rows[0]["components"]["observations"] == 3
            assert min(item["date"] for item in result.rows[0]["inputs"]) == "2024-01-26"
        else:
            assert not result.rows and result.gaps[0]["field"] == "calendar"
            assert result.gaps[0]["subject"] == ["A", "B"]
            if variant == "naive":
                assert "收盘时点" in result.gaps[0]["reason"]


@pytest.mark.parametrize("foreign_session", [None, "market", "calendar_id"])
def test_correlation_selects_window_from_proven_calendar(tmp_path, foreign_session):
    source, doc, query = example_query("rolling_return_correlation")
    for row in doc["tables"]["daily_bars"]:
        row.update(calendar_id="SH", market="SH", close_timestamp=f"{row['date']}T15:00:00+08:00")
        if (row["code"], row["date"]) == ("A", str(query.trade_date)):
            row.pop("market")
    for day in doc["tables"]["calendar"]:
        day[foreign_session or "market"] = "SH"
    if foreign_session:
        doc["tables"]["calendar"].append(
            {"date": "2024-01-28", foreign_session: "OTHER", "is_trading_day": True,
             "available_at": "2024-01-01T08:00:00+08:00"}
        )
    if foreign_session == "calendar_id":
        doc["tables"]["calendar"].append(
            dict(date="2024-01-31", calendar_id="OTHER", is_trading_day=False, available_at="2024-01-01T08:00:00+08:00")
        )
    result = rerun(tmp_path, source, doc, query)
    print("所属日历", foreign_session, result.status, [row["value"] for row in result.rows], result.gaps)
    assert not result.gaps and result.rows[0]["value"] == pytest.approx(1)
    assert result.rows[0]["components"]["observations"] == 3
    assert {row["date"] for row in result.rows[0]["inputs"]} == {
        "2024-01-26", "2024-01-29", "2024-01-30", "2024-01-31"
    }
    if not foreign_session:
        store, ref = local_fact_source(tmp_path / "local", doc["tables"])
        local = compute_factor(store, ref, query)
        assert not local.gaps and local.rows[0]["value"] == pytest.approx(1)


def test_workbench_rejects_unknown_component_configuration_key(monkeypatch, tmp_path):
    page = open_factors(monkeypatch, tmp_path)
    page.selectbox(key="factor_id").set_value("weighted_factor_score").run()
    widget = page.text_area(key="factor_parameters_weighted_factor_score_默认")
    widget.set_value('{"components":{"price_momentum":{"weight":1,"direction":1,"paramters":{"window":60}}}}').run()
    assert any("components.price_momentum.paramters" in item.value for item in page.error)
    assert not page.exception


def test_cointegration_receipt_indexes_tested_and_execution_parameters(factor_evidence_root):
    from hetu_quant.factors.selftest import read_selftest, replay_selftest

    if factor_evidence_root is None:
        pytest.skip("需先执行专项收据写入")
    paths = []
    for lag in (0, 1):
        evidence = read_selftest(factor_evidence_root, "pair_cointegration", "1", {"window": 30, "lag": lag})
        row = next(case for case in evidence["cases"] if case["case_id"] == "formula:discriminator")
        assert row["tested_parameters"] == {"window": 30, "lag": 0}
        assert row["execution_parameters"] == {"window": 30, "lag": 1}
        assert row["matches_tested_parameters"] is (lag == 0)
        paths.append(row["receipt"])
        value = replay_selftest(row).rows[0]["value"]
        print("协整收据", "查询 lag", lag, "执行 lag", row["execution_parameters"]["lag"], "p", value)
        assert value == pytest.approx(0.20060210296)
    assert paths[0] == paths[1]


def test_workbench_reports_incomplete_replay_before_download(monkeypatch, tmp_path, factor_evidence_root):
    from hetu_quant.factors.selftest import read_selftest

    if factor_evidence_root is None:
        pytest.skip("需先执行专项收据写入")
    original = read_selftest(factor_evidence_root, "pair_cointegration", "1", {"window": 30, "lag": 0})
    root = tmp_path / "evidence"
    for row in original["cases"]:
        path = Path(row["receipt"])
        copy = root / path.relative_to(factor_evidence_root)
        copy.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(path.read_bytes())
        if row["case_id"] == "formula:discriminator":
            del data["replay"]["cutoff"]
        copy.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("HETU_FACTOR_EVIDENCE_ROOT", str(root))
    page = open_factors(monkeypatch, tmp_path)
    page.selectbox(key="factor_id").set_value("pair_cointegration").run()
    print("缺 cutoff 页面", [item.value for item in page.warning])
    assert any("formula:discriminator" in item.value and "replay.cutoff" in item.value for item in page.warning)
    assert not any(item.label == "下载此收据的精确输入" for item in page.get("download_button"))
    assert not any(item.label == "复算所选收据" for item in page.button)
    assert "逻辑自测 · 通过" in [item.value for item in page.subheader]
    assert "效果未验证" in [item.value for item in page.subheader]
    assert not page.exception


def test_workbench_can_recompute_both_200_day_inputs(monkeypatch, tmp_path, factor_evidence_root):
    from hetu_quant.factors.selftest import read_selftest

    if factor_evidence_root is None:
        pytest.skip("需先执行具名预设收据写入")
    monkeypatch.setenv("HETU_FACTOR_EVIDENCE_ROOT", str(factor_evidence_root))
    page = open_factors(monkeypatch, tmp_path)
    page.selectbox(key="factor_id").set_value("breadth_above_ma_share").run()
    page.selectbox(key="factor_preset_breadth_above_ma_share").set_value("200日").run()
    run_trial(page)
    assert page.dataframe[0].value.iloc[0]["读数"] == 0.4
    rows = read_selftest(factor_evidence_root, "breadth_above_ma_share", "1", {"window": 200})["cases"]
    positive = next(row for row in rows if row["case_id"] == "positive")
    assert positive["replay"]["trade_date"] == "2024-01-31"
    page.selectbox(key="factor_replay_breadth_above_ma_share").set_value(positive["receipt"]).run()
    page.button(key="factor_replay_compute").click().run()
    assert not page.exception
    assert [table.value.iloc[0]["读数"] for table in page.dataframe if "读数" in table.value] == [0.4, 0.6]
    assert any(item.label == "下载此收据的精确输入" for item in page.get("download_button"))
    print("2024-01-31 / 200日 / 随包 40% / 正例收据复算 60%")


def test_expected_growth_uses_same_named_equal_weight_cohort(tmp_path):
    source, doc, query = example_query("expected_growth", params={"forecast_aggregation": "equal_weight"})
    rows = doc["tables"]["forecasts"]
    for row, x in zip(rows[:], (9, 12), strict=True):
        row.update(forecast_kind="individual", institution="X", forecast_net_profit=x)
        rows.append(dict(row, institution="Y", forecast_net_profit=x + 2))
    store, ref = local_fact_source(tmp_path / "local", doc["tables"])
    for result in (rerun(tmp_path, source, doc, query), compute_factor(store, library(store, (store, ref)), query)):
        assert result.rows[0]["value"] == pytest.approx(0.3)
        assert result.rows[0]["components"]["forecast_aggregation"] == "equal_weight"
    rows[-1]["institution"] = "Z"
    assert rerun(tmp_path, source, doc, query).gaps[0]["field"] == "forecast_aggregation"


@pytest.mark.parametrize("variant", ["original", "unrelated", "fiscal_conflict", "aggregation_conflict"])
def test_expected_growth_only_checks_applicable_fiscal_pair(tmp_path, variant):
    params = {"forecast_aggregation": "equal_weight"} if variant == "aggregation_conflict" else None
    source, doc, query = example_query("expected_growth", params=params)
    rows = doc["tables"]["forecasts"]
    rows[0].update(fiscal_year_end="12-31", source_ref="2024 年一致预期")
    rows[1].update(fiscal_year_end="12-31", source_ref="2025 年一致预期")
    if variant == "unrelated":
        rows.append(dict(rows[-1], period="2026", fiscal_year_end="03-31", source_ref="2026 年预期"))
    elif variant == "fiscal_conflict":
        rows[1]["fiscal_year_end"] = "03-31"
    elif variant == "aggregation_conflict":
        rows[1].update(forecast_kind="individual", institution="X")
    store, ref = local_fact_source(tmp_path / "local", doc["tables"])
    for result in (rerun(tmp_path, source, doc, query), compute_factor(store, ref, query)):
        print(result.source["nature"], variant, [row["value"] for row in result.rows],
              [(gap["field"], gap["reason"]) for gap in result.gaps])
        if variant in ("original", "unrelated"):
            row = result.rows[0]
            assert not result.gaps and row["value"] == pytest.approx(0.3)
            assert row["components"]["fiscal_years"] == ["2024", "2025"]
            assert [item["source_ref"] for item in row["inputs"]] == ["2024 年一致预期", "2025 年一致预期"]
        else:
            expected = "fiscal_year_end" if variant == "fiscal_conflict" else "forecast_aggregation"
            assert not result.rows and result.gaps[0]["field"] == expected
