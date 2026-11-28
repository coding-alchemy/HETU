"""Licensed buyback progress through the production and workbench entry points."""

import json
from dataclasses import replace

import pytest
from hetu_data import DataStore

from factor_cases import example_query, local_fact_source
from hetu_quant.factors import compute_factor
from hetu_quant.factors.catalog import get_factor
from hetu_quant.factors.sources import read_facts
from hetu_quant.factors.workbench import build_trial
from hetu_quant.factors.workbench_inputs import preview_inputs, visible_evidence
from hetu_quant.ui import factor_navigation, factor_page
from test_factor_workbench import open_factors, run_trial
from test_factor_workbench_review import _library, _revoke


def _grant(store, *, omit=()):
    path = store.root / "data-products.json"
    config = json.loads(path.read_text())
    permission = config["products"]["factor_input"]["permission"]
    permission["allow_export"] = True
    if omit:
        permission["allowed_fields"] = sorted({
            "kind", "event_id", "paid", "currency", "plan_amount_upper",
            "plan_amount", "plan_amount_basis", "price_cap", "price_unit",
        } - set(omit))
    else:
        permission.pop("allowed_fields", None)
    path.write_text(json.dumps(config))
    return DataStore(store.root)


def _plan(path, *, fixed=False, missing_upper=False, upper=1_000_000, paid=True,
          announcement=None, group=False, fixed_with_upper=False):
    _, document, request = example_query("buyback_progress", params={"event_id": "EV1"})
    event = document["tables"]["events"][0]
    event.update(code="A.SH", source_ref="回购原公告", plan_amount=500_000, plan_amount_upper=upper)
    if announcement:
        event["announcement_ref"] = announcement
    if (fixed and not fixed_with_upper) or missing_upper:
        del event["plan_amount_upper"]
    if not paid:
        del event["paid"]
    if fixed:
        event["plan_amount_basis"] = "fixed"
    if group:
        document["tables"]["memberships"] = [
            dict(code="POOL", member="A.SH", valid_from="2024-01-01",
                 available_at="2024-01-01T08:00:00+08:00", source_ref="合成成员公告")
        ]
    store, ref = local_fact_source(path, document["tables"])
    return store, ref, replace(request, subjects=("A.SH",))


def _progress(result, denominator, basis):
    assert not result.gaps
    row, = result.rows
    assert row["subject"] == "A.SH"
    assert row["value"] == (0.2 if denominator == 1_000_000 else 0.4)
    parts = row["components"]
    assert (parts["event_id"], parts["published_at"], parts["currency"]) == (
        "EV1", "2024-01-01T08:00:00+08:00", "CNY",
    )
    assert (parts["paid"], parts["plan_amount"], parts["basis"]) == (200_000, denominator, basis)
    assert row["inputs"][0]["source_ref"] == "回购原公告"
    return row


@pytest.mark.parametrize("scenario", ["upper-denied", "fixed", "unused-fixed", "missing-upper", "paid-denied"])
def test_buyback_uses_the_proven_denominator_and_permitted_paid_amount(tmp_path, scenario):
    store, ref, request = _plan(tmp_path, fixed=scenario == "fixed", missing_upper=scenario == "missing-upper")
    omit = {
        "upper-denied": ("plan_amount_upper",), "fixed": ("plan_amount_upper",),
        "unused-fixed": ("plan_amount",), "missing-upper": (), "paid-denied": ("paid",),
    }[scenario]
    store = _grant(store, omit=omit)
    trial = build_trial(ref, request)
    preview = preview_inputs(store, trial)
    result = compute_factor(store, ref, request)
    assert preview.can_submit
    expected_gap = {
        "upper-denied": ("plan_amount_upper", "denied", "补证字段未获准"),
        "missing-upper": ("plan_amount_upper", "missing", "缺"),
        "paid-denied": ("paid", "denied", "补证字段未获准"),
    }.get(scenario)
    if expected_gap:
        field, state, reason = expected_gap
        gap = next(gap for gap in result.gaps if gap["field"] == field)
        assert not result.rows
        assert (gap["subject"], gap["event"], gap["period"]) == ("A.SH", "EV1", "2024-01-01T08:00:00+08:00")
        assert reason in gap["reason"]
        assert field not in gap["evidence"][0]
        check = next(item for item in preview.checks if item["field"] == field and item["state"] == state)
        assert (check["subject"], check["product"]) == ("A.SH", "factor_input")
        assert reason in check["reason"]
        print(scenario, {"preview": check, "production": gap})
    else:
        expected = (500_000, "plan_amount") if scenario == "fixed" else (1_000_000, "plan_amount_upper")
        row = _progress(result, *expected)
        assert not [item for item in preview.checks if item["state"] == "denied"]
        print(scenario, row["value"], row["components"])


def test_buyback_workbench_reopens_and_exports_only_permitted_progress(tmp_path, monkeypatch):
    store, ref, request = _plan(tmp_path / "source")
    _grant(store)
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
    page.selectbox(key="factor_id").set_value("buyback_progress").run()
    page.selectbox(key="factor_source_buyback_progress").set_value(ref.snapshot_id).run()
    page.text_input(key="factor_subjects_buyback_progress").set_value("A.SH")
    page.date_input(key="factor_date_buyback_progress").set_value(request.trade_date).run()
    run_trial(page)
    assert not page.exception
    assert page.dataframe[0].value["读数"].tolist() == [0.2]
    attempt = next(iter(page.session_state["factor_attempts"].values()))
    original = _progress(attempt.result, 1_000_000, "plan_amount_upper")
    assert "price_cap" not in original["inputs"][0]
    legacy_inputs = read_facts(
        DataStore(store.root), ref, get_factor("buyback_progress"), attempt.trial.request
    ).records("events", "A.SH")
    assert legacy_inputs[0]["price_cap"] == 1250

    calls = []
    compute = factor_page.compute_factor

    def track(*args, **kwargs):
        calls.append("compute")
        return compute(*args, **kwargs)

    downloads = []
    download = factor_navigation.st.download_button

    def capture(label, data, *args, **kwargs):
        if label == "下载该项获准证据":
            downloads.append(json.loads(data))
        return download(label, data, *args, **kwargs)

    monkeypatch.setattr(factor_page, "compute_factor", track)
    monkeypatch.setattr(factor_navigation.st, "download_button", capture)
    current = _grant(store, omit=("price_cap",))
    current_row = _progress(compute_factor(current, ref, request), 1_000_000, "plan_amount_upper")
    assert "price_cap" not in current_row["inputs"][0]
    page.run()
    assert page.dataframe[0].value["读数"].tolist() == [0.2]
    page.button(key="factor_evidence_row_0").click().run()
    assert not page.exception
    assert downloads
    assert downloads[-1]["value"] == 0.2
    assert downloads[-1]["inputs"][0]["source_ref"] == "回购原公告"
    assert "price_cap" not in json.dumps(downloads[-1])
    assert all("price_cap" not in item.value for item in page.json)
    assert not calls
    legacy = replace(attempt.result, rows=[dict(
        original, inputs=legacy_inputs,
        components={key: value for key, value in original["components"].items() if key != "currency"},
    )])
    old_download = visible_evidence(current, attempt.trial, legacy, row_index=0, operation="download")
    assert old_download["permitted"] and old_download["item"]["value"] == 0.2
    assert old_download["item"]["inputs"][0]["currency"] == "CNY"
    assert "price_cap" not in json.dumps(old_download)
    assert legacy.rows[0]["inputs"][0]["price_cap"] == 1250
    print("grant narrowed: 20% new + original, no formula restart, exported", downloads[-1])

    page.button(key="factor_return_to_draft").click().run()
    _grant(store, omit=("paid",))
    page.run()
    assert any("paid 补证字段未获准" in item.value for item in page.markdown)
    assert not calls
    run_trial(page)
    assert not page.exception
    assert calls == ["compute"]
    assert any("paid 补证字段未获准" in frame.value.get("reason", []).tolist()
               for frame in page.dataframe if "reason" in frame.value)
    print("paid grant narrowed: preflight and explicit page run denied")


def test_buyback_complete_original_is_not_hidden_by_a_cropped_asset(tmp_path):
    cropped, a, request = _plan(tmp_path / "cropped")
    full, b, _ = _plan(tmp_path / "full", group=True)
    cropped, full = _grant(cropped, omit=("plan_amount_upper",)), _grant(full)
    completed = []
    for assets in (((cropped, a), (full, b)), ((full, b), (cropped, a))):
        library = _library(cropped, *assets)
        trial = build_trial(library, request)
        preview = preview_inputs(cropped, trial)
        assert not [item for item in preview.checks if item["state"] == "denied"]
        result = compute_factor(cropped, library, request)
        report = visible_evidence(cropped, trial, result, row_index=0, operation="download")
        assert report["permitted"]
        row = report["item"]
        assert row["value"] == 0.2
        assert [(proof["snapshot_id"], proof["plan_amount_upper"]) for proof in row["inputs"]] == [
            (b.snapshot_id, 1_000_000),
        ]
        completed.append((trial, result))
    _revoke(cropped, "factor_input")
    for trial, result in completed:
        row, = visible_evidence(DataStore(cropped.root), trial, result)["rows"]
        assert row["value"] == 0.2
    print("two asset orders: 20%, complete proof only", b.snapshot_id, "still 20% when unused asset revoked")


def test_weighted_score_uses_same_authorized_buyback_proof(tmp_path):
    cropped, a, request = _plan(tmp_path / "cropped")
    full, b, _ = _plan(tmp_path / "full", group=True)
    cropped, full = _grant(cropped, omit=("plan_amount_upper",)), _grant(full)
    library = _library(cropped, (cropped, a), (full, b))
    trial = build_trial(library, replace(
        request, factor_id="weighted_factor_score",
        parameters={"components": {"buyback_progress": {"weight": 1, "direction": 1}}},
    ))
    result = compute_factor(cropped, library, trial.request)
    row, = result.rows
    assert row["components"]["components"]["buyback_progress"]["raw"] == 0.2
    event, = [item for item in row["inputs"] if item.get("kind") == "buyback"]
    assert (event["snapshot_id"], event["plan_amount_upper"]) == (b.snapshot_id, 1_000_000)
    assert "price_cap" not in event
    _grant(full, omit=("price_cap",))
    old, = visible_evidence(DataStore(cropped.root), trial, result)["rows"]
    assert old["components"]["components"]["buyback_progress"]["raw"] == 0.2
    print("weighted component: 20% from complete permitted proof; old score independent of price_cap")


def test_buyback_two_permitted_originals_cannot_overwrite_a_real_ceiling_conflict(tmp_path):
    first, a, request = _plan(tmp_path / "one-million")
    second, b, _ = _plan(tmp_path / "two-million", upper=2_000_000)
    for assets in (((first, a), (second, b)), ((second, b), (first, a))):
        library = _library(first, *assets)
        result = compute_factor(first, library, request)
        assert not result.rows
        gap, = result.gaps
        assert (gap["subject"], gap["event"], gap["field"]) == ("A.SH", "EV1", "plan_amount_upper")
        assert {(item["snapshot_id"], item["value"]) for item in gap["evidence"]} == {
            (a.snapshot_id, 1_000_000), (b.snapshot_id, 2_000_000),
        }
    print("two asset orders: genuine 1,000,000 vs 2,000,000 ceiling conflict preserved")


@pytest.mark.parametrize(("same_announcement", "type_revoked"), [(True, False), (False, False), (True, True)],
                         ids=["same-announcement", "same-local-id-only", "same-announcement-type-revoked"])
def test_buyback_split_registration_requires_and_retains_shared_original(tmp_path, same_announcement, type_revoked):
    announcement = "https://disclosures.example/notice/issuer-one-buyback"
    first, a, request = _plan(tmp_path / "upper", paid=False, announcement=announcement)
    second, b, _ = _plan(
        tmp_path / "paid", missing_upper=True,
        announcement=announcement if same_announcement else "https://disclosures.example/notice/other-buyback",
    )
    omitted = ("plan_amount_basis",) if type_revoked else ()
    first, second = _grant(first, omit=omitted), _grant(second, omit=omitted)
    completed = []
    for assets in (((first, a), (second, b)), ((second, b), (first, a))):
        library = _library(first, *assets)
        trial = build_trial(library, request)
        result = compute_factor(first, library, request)
        if type_revoked:
            assert not result.rows and any(gap["field"] == "plan_amount_basis" for gap in result.gaps)
            assert any(check["field"] == "plan_amount_basis" and check["state"] == "denied"
                       for check in preview_inputs(first, trial).checks)
            print("split registration plan type revoked", result.gaps[0]["reason"])
            continue
        if not same_announcement:
            assert not result.rows
            gap, = result.gaps
            assert (gap["subject"], gap["event"], gap["field"]) == ("A.SH", "EV1", "event_identity")
            assert {item["snapshot_id"] for item in gap["evidence"]} == {a.snapshot_id, b.snapshot_id}
            continue
        report = visible_evidence(first, trial, result, row_index=0, operation="download")
        assert report["permitted"] and report["item"]["value"] == 0.2
        inputs = {proof["snapshot_id"]: proof for proof in report["item"]["inputs"]}
        assert set(inputs) == {a.snapshot_id, b.snapshot_id}
        assert (inputs[a.snapshot_id]["plan_amount_upper"], inputs[b.snapshot_id]["paid"]) == (1_000_000, 200_000)
        completed.append((trial, result))
    if same_announcement and not type_revoked:
        _revoke(second, "factor_input")
        for trial, result in completed:
            assert not visible_evidence(DataStore(first.root), trial, result)["rows"]
            assert not compute_factor(DataStore(first.root), trial.source, request).rows
    if not type_revoked:
        print("split registration:", "20%, both proofs required" if same_announcement else "same ID insufficient")


def test_buyback_revoked_plan_type_does_not_change_denominator(tmp_path, monkeypatch):
    store, ref, request = _plan(tmp_path / "source", fixed=True, fixed_with_upper=True)
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
    page.selectbox(key="factor_id").set_value("buyback_progress").run()
    page.selectbox(key="factor_source_buyback_progress").set_value(ref.snapshot_id).run()
    page.text_input(key="factor_subjects_buyback_progress").set_value("A.SH")
    page.date_input(key="factor_date_buyback_progress").set_value(request.trade_date).run()
    run_trial(page)
    assert page.dataframe[0].value["读数"].tolist() == [0.4]
    original = next(iter(page.session_state["factor_attempts"].values()))
    current = _grant(store, omit=("plan_amount_basis",))
    result = compute_factor(current, ref, request)
    print("方案类型撤权后", [row["value"] for row in result.rows], result.gaps)
    assert not result.rows
    assert any(gap["field"] == "plan_amount_basis" and "未获准" in gap["reason"] for gap in result.gaps)
    assert "fixed" not in json.dumps(result.gaps, ensure_ascii=False)
    page.run()
    assert not visible_evidence(current, original.trial, original.result)["rows"]
    assert any("plan_amount_basis 补证字段未获准" in item.value for item in page.markdown)
    run_trial(page)
    assert any("plan_amount_basis" in frame.value.get("field", []).tolist()
               for frame in page.dataframe if "field" in frame.value)
    _grant(store)
    page.run()
    run_trial(page)
    assert page.dataframe[0].value["读数"].tolist() == [0.4]
    print("许可恢复后", page.dataframe[0].value["读数"].tolist())


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("fixed_with_upper", [True, False], ids=["with-upper", "fixed-only"])
def test_buyback_payment_cannot_supply_permission_for_plan_terms(tmp_path, reverse, fixed_with_upper):
    announcement = "https://disclosures.example/notice/issuer-one-buyback"
    terms, terms_ref, request = _plan(
        tmp_path / "terms", fixed=True, fixed_with_upper=fixed_with_upper, paid=False, announcement=announcement,
    )
    _, document, _ = example_query("buyback_progress")
    payment = dict(document["tables"]["events"][0], announcement_ref=announcement, code="A.SH")
    payment = {field: payment[field] for field in (
        "code", "event_id", "kind", "available_at", "paid", "currency", "announcement_ref",
    )}
    payer, payer_ref = local_fact_source(tmp_path / "payment", {"events": [payment]})
    terms, payer = _grant(terms), _grant(payer)
    assets = [(terms, terms_ref), (payer, payer_ref)]
    library = _library(terms, *(reversed(assets) if reverse else assets))
    trial = build_trial(library, request)
    initial = compute_factor(terms, library, request)
    row, = initial.rows
    assert row["value"] == 0.4
    assert next(item for item in row["inputs"] if "plan_amount_basis" in item)["snapshot_id"] == terms_ref.snapshot_id
    current = _grant(terms, omit=("plan_amount_basis",))
    checks = preview_inputs(current, trial).checks
    denied = compute_factor(current, library, request)
    print("条款撤权", reverse, [item["value"] for item in denied.rows], denied.gaps)
    assert not denied.rows
    gap, = denied.gaps
    assert (gap["field"], gap["event"]) == ("plan_amount_basis", "EV1")
    assert gap["evidence"][0]["snapshot_id"] == terms_ref.snapshot_id
    assert "plan_amount_basis" not in gap["evidence"][0]
    assert any(check["field"] == "plan_amount_basis" and check["state"] == "denied"
               and check["source"] == terms_ref.snapshot_id for check in checks)
    assert not visible_evidence(current, trial, initial)["rows"]
    restored = _grant(terms)
    assert compute_factor(restored, library, request).rows[0]["value"] == 0.4
    assert visible_evidence(restored, trial, initial)["rows"][0]["value"] == 0.4
    print("条款许可恢复", reverse, 0.4)
