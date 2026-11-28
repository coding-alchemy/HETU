"""Point-in-time factor reads from a published daily-bars revision."""

import hashlib
import json
from datetime import date, datetime, time

import pandas as pd
import pytest
from hetu_data import DataStore
from hetu_data.library import load_library, publish_library

from backtest_cases import build_case_store
from hetu_quant.factors import FactorRequest, close_cutoff, compute_factor
from hetu_quant.factors.catalog import get_factor
from hetu_quant.factors.sources import known_records, read_facts
from hetu_quant.factors.workbench import build_trial
from hetu_quant.factors.workbench_inputs import preview_inputs
from test_factor_workbench import open_factors, run_trial
from test_factor_workbench_review import _browse, _query

DAY = date(2024, 4, 1)
CODE = "A.SH"
CLOSE = close_cutoff(DAY)
PRODUCTS = {
    "daily_bars": ("factor_daily", "daily"),
    "calendar": ("factor_calendar", "calendar"),
    "knowledge_evidence": ("factor_proofs", "catalog"),
}


def _import_amount(path, amount, evidence_name, *, published=CLOSE, daily_allowed=None, calendar_open=True,
                   calendar_published="2023-10-01T09:00:00+08:00", calendar_allowed=None,
                   code=CODE, calendar_product="factor_calendar", native_amount=None):
    # Synthetic daily observations and independently issued attestations.
    record = dict(
        product_id="factor_daily", record_key=f"{code}|{DAY}|amount_cny", revision_id="original",
        published_at=published.isoformat(), value=amount, unit="CNY",
    )
    original = json.dumps(record).encode()
    registration = dict(
        table_name="daily_bars", record_key=record["record_key"], revision_id=record["revision_id"], supersedes="",
        published_at=record["published_at"], evidence_ref=evidence_name, sha256=hashlib.sha256(original).hexdigest(),
        verified=True,
    )
    case = dict(
        csvs=dict(
            daily_bars=(
                "date,code,open_raw,high_raw,low_raw,close_raw,volume_shares,amount_cny\n"
                f"{DAY},{code},100,100,100,100,100,{amount if native_amount is None else native_amount}\n"
            ),
            calendar=f"date,is_trading_day\n{DAY},{calendar_open}\n",
            knowledge_evidence=pd.DataFrame([registration]).to_csv(index=False),
        ),
        table_products=dict(PRODUCTS, calendar=(calendar_product, "calendar")),
        root_config=True, obtained_at="2024-04-03T18:00:00+08:00",
        permissions={table: {"allowed_uses": ["factor_calculation"]} for table in PRODUCTS},
        source_metadata={"calendar": {"published_at": calendar_published}},
    )
    if daily_allowed is not None:
        case["source_metadata"]["daily_bars"] = {"allowed_fields": daily_allowed}
    if calendar_allowed is not None:
        case["source_metadata"]["calendar"]["allowed_fields"] = calendar_allowed
    store, source = build_case_store(path, case)
    (store.root / "evidence" / evidence_name).write_bytes(original)
    return store, source


def _published_revision(store, previous, current, *independent):
    rule = dict(
        root=str(store.root), product_id="factor_daily", table="daily_bars",
        superseded_snapshot_id=previous.snapshot_id, selected_snapshot_id=current.snapshot_id,
        codes=[CODE], start=str(DAY), end=str(DAY), evidence_ref="evidence/revision.json",
    )
    (store.root / rule["evidence_ref"]).write_text(json.dumps(rule))
    assets = [dict(root=str(store.root), snapshot_id=ref.snapshot_id, tables=list(PRODUCTS))
              for ref in (previous, current, *independent)]
    assets[1]["revision_selections"] = [rule]
    library = publish_library(store, asset_snapshots=assets)
    assert load_library(store, library)["revision_selections"] == [rule]
    return library


def _compute(store, source, cutoff=CLOSE):
    return compute_factor(store, source, FactorRequest("traded_amount", (CODE,), DAY, cutoff, {}))


def _reading(result, source, value, proof_name):
    assert not result.gaps, result.gaps
    row, = result.rows
    assert row["value"] == value
    observation, = row["inputs"]
    proof = observation["field_evidence"]["amount_cny"]
    assert proof["snapshot_id"] == source.snapshot_id
    assert proof["evidence_ref"] == proof_name
    print("生产读回", result.source["id"], result.cutoff.isoformat(), row["value"], proof["snapshot_id"],
          proof["evidence_ref"])


def test_published_revision_flows_from_browser_to_explicit_production_trial(tmp_path, monkeypatch):
    path = tmp_path / "quotes"
    store, previous = _import_amount(path, 700, "factor-original.json")
    _, current = _import_amount(path, 830, "factor-current.json")
    library = _published_revision(store, previous, current)

    _reading(_compute(store, previous), previous, 700, "factor-original.json")
    _reading(_compute(store, current), current, 830, "factor-current.json")

    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
    page.selectbox(key="factor_id").set_value("traded_amount").run()
    _browse(page, library, "daily_bars", day=DAY)
    page.multiselect(key="factor_browser_fields_daily_bars").set_value(["amount_cny"])
    _query(page)
    browse = page.dataframe[0].value[["amount_cny", "成交额依据"]].to_dict("records")
    print("普通浏览读回", library.library_id, CODE, DAY, browse)
    assert browse == [{"amount_cny": 830, "成交额依据": "factor-current.json"}]
    page.button(key="factor_browser_to_trial").click().run()
    assert not page.session_state["factor_attempts"]
    run_trial(page)
    assert not page.exception
    attempt, = page.session_state["factor_attempts"].values()
    assert (attempt.trial.source, attempt.trial.subjects, attempt.trial.trade_date, attempt.trial.cutoff) == (
        library, (CODE,), DAY, CLOSE,
    )
    _reading(attempt.result, current, 830, "factor-current.json")
    assert page.dataframe[0].value["读数"].tolist() == [830]
    _reading(_compute(store, library), current, 830, "factor-current.json")


def test_library_revision_waits_for_public_attestation_and_preserves_independent_conflict(tmp_path):
    store, previous = _import_amount(tmp_path, 700, "factor-original.json")
    later = datetime.fromisoformat(f"{DAY}T18:00:00+08:00")
    _, current = _import_amount(tmp_path, 830, "factor-current.json", published=later)
    library = _published_revision(store, previous, current)

    early = _compute(store, library)
    _reading(early, previous, 700, "factor-original.json")
    assert "factor-current.json" not in repr((early.rows, early.gaps))
    _reading(_compute(store, library, later), current, 830, "factor-current.json")

    _, independent = _import_amount(tmp_path, 900, "factor-independent.json")
    conflicted = _published_revision(store, previous, current, independent)
    result = _compute(store, conflicted, later)
    assert not result.rows
    gap, = result.gaps
    assert gap["field"] == "amount_cny"
    assert gap["reason"] == "同一坐标存在互不替代的冲突版本"
    evidence = {entry["evidence_ref"] for entry in gap["evidence"]}
    print("独立原件冲突", conflicted.library_id, sorted(evidence))
    assert evidence == {"factor-current.json", "factor-independent.json"}


def _traded_amount_trial(monkeypatch, path, store, source, *, display=("amount_cny",), coverage=None):
    page = open_factors(monkeypatch, path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
    page.selectbox(key="factor_id").set_value("traded_amount").run()
    _browse(page, source, "daily_bars", day=DAY)
    page.multiselect(key="factor_browser_fields_daily_bars").set_value(list(display))
    _query(page)
    if coverage:
        assert any(coverage in item.value for item in page.caption)
    assert len(page.dataframe[0].value) == 1
    if "close_raw" in display:
        assert page.dataframe[0].value["close_raw"].tolist() == [100]
        assert page.dataframe[0].value["amount_cny"].isna().all()
    page.button(key="factor_browser_to_trial").click().run()
    assert not page.session_state["factor_attempts"]
    return page


def _page_evidence(page, index):
    page.button(key=f"factor_evidence_{index}_0").click().run()
    assert not page.exception
    assert page.session_state["factor_evidence_location"]
    json_text = [json.dumps(json.loads(item.value), ensure_ascii=False) for item in page.json]
    return "\n".join([*json_text, *(item.value for item in [*page.caption, *page.markdown])])


def test_effective_amount_revision_survives_field_denial(tmp_path, monkeypatch):
    path = tmp_path / "quotes"
    later = CLOSE.replace(hour=18)
    store, old = _import_amount(path, 700, "old.json")
    _, full = _import_amount(path, 830, "new.json", published=later)
    _, restricted = _import_amount(
        path, 830, "new.json", published=later, daily_allowed=["code", "date", "close_raw"],
    )
    full_library = _published_revision(store, old, full)
    denied_library = _published_revision(store, old, restricted)
    columns_read = []
    read = DataStore.read_range

    def observed(owner, ref, name, **kwargs):
        if ref == restricted and name == "daily_bars":
            columns_read.append(tuple(kwargs["columns"]))
        return read(owner, ref, name, **kwargs)

    monkeypatch.setattr(DataStore, "read_range", observed)
    _reading(_compute(store, full_library, later), full, 830, "new.json")
    page = _traded_amount_trial(monkeypatch, tmp_path, store, full_library)
    page.time_input(key="factor_cutoff_traded_amount").set_value(time(18)).run()
    run_trial(page)
    assert page.dataframe[0].value["读数"].tolist() == [830]
    assert "new.json" in _page_evidence(page, "row")

    early = _compute(store, denied_library)
    assert [row["value"] for row in early.rows] == [700]
    assert "new.json" not in repr((early.rows, early.gaps))
    page = _traded_amount_trial(monkeypatch, tmp_path, store, denied_library, display=("close_raw", "amount_cny"))
    run_trial(page)
    assert page.dataframe[0].value["读数"].tolist() == [700]
    early_detail = _page_evidence(page, "row")
    assert "old.json" in early_detail and "new.json" not in early_detail
    assert "830" not in early_detail and "18:00" not in early_detail

    late_request = FactorRequest("traded_amount", (CODE,), DAY, later, {})
    preview = preview_inputs(store, build_trial(denied_library, late_request))
    assert any(item.get("field") == "amount_cny" and item["state"] == "denied" for item in preview.checks)
    late = _compute(store, denied_library, later)
    print("晚截止字段受限", late.rows, late.gaps, "新版底层日线投影", columns_read)
    assert not late.rows
    assert {gap["field"] for gap in late.gaps} == {"amount_cny"}
    assert any("字段未获准" in gap["reason"] for gap in late.gaps)
    assert "830" not in repr(late.gaps) and "new.json" not in repr(late.gaps)
    late_request = FactorRequest("traded_amount", (CODE,), DAY, later, {"mode": "daily"})
    facts = read_facts(store, denied_library, get_factor("traded_amount"), late_request)
    earlier = FactorRequest("traded_amount", (CODE,), DAY, CLOSE, {"mode": "daily"})
    available, = known_records(facts, "daily_bars", earlier, CODE)
    assert available["amount_cny"] == 700 and "new.json" not in repr(available)

    page.sidebar.radio(key="page_nav").set_value("因子库").run()
    page.time_input(key="factor_cutoff_traded_amount").set_value(time(18)).run()
    run_trial(page)
    assert all("读数" not in frame.value for frame in page.dataframe)
    attempt = list(page.session_state["factor_attempts"].values())[-1]
    assert attempt.result.gaps == late.gaps and not attempt.result.rows
    detail = _page_evidence(page, "gap")
    assert "字段未获准" in detail and "830" not in detail and "new.json" not in detail
    assert columns_read and all("amount_cny" not in columns for columns in columns_read)


@pytest.mark.parametrize(("native_amount", "reason"), [
    ("", "缺少与截止时点生效的日线原件匹配的原生表值"),
    (820, "所选原生表值与截止时点生效的日线原件不匹配"),
], ids=["empty", "mismatch"])
def test_effective_amount_revision_requires_matching_native_value(tmp_path, monkeypatch, native_amount, reason):
    path = tmp_path / "missing-quote"
    later = CLOSE.replace(hour=18)
    store, previous = _import_amount(path, 700, "old.json")
    _, current = _import_amount(path, 830, "new.json", published=later, native_amount=native_amount)
    library = _published_revision(store, previous, current)
    native = store.read_range(
        current, "daily_bars", codes=(CODE,), start=DAY, end=DAY,
        columns=("close_raw", "amount_cny"), product_id="factor_daily",
    )
    assert native["close_raw"].tolist() == [100]
    if native_amount == "":
        assert native["amount_cny"].isna().all()
    else:
        assert native["amount_cny"].tolist() == [native_amount]

    early = _compute(store, library)
    _reading(early, previous, 700, "old.json")
    assert "new.json" not in repr(early.rows) and "830" not in repr(early.rows)
    direct = _compute(store, library, later)

    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
    page.selectbox(key="factor_id").set_value("traded_amount").run()
    _browse(page, library, "daily_bars", day=DAY)
    page.multiselect(key="factor_browser_fields_daily_bars").set_value(["close_raw", "amount_cny"])
    _query(page)
    displayed = page.dataframe[0].value[["close_raw", "amount_cny"]]
    assert displayed.equals(native[["close_raw", "amount_cny"]])
    page.button(key="factor_browser_to_trial").click().run()
    assert not page.session_state["factor_attempts"]
    run_trial(page)
    first, = page.session_state["factor_attempts"].values()
    assert first.trial.cutoff == CLOSE and first.result.rows == early.rows
    earlier_detail = _page_evidence(page, "row")
    assert "old.json" in earlier_detail and "new.json" not in earlier_detail

    page.sidebar.radio(key="page_nav").set_value("因子库").run()
    page.time_input(key="factor_cutoff_traded_amount").set_value(time(18)).run()
    run_trial(page)
    _, latest = page.session_state["factor_attempts"].values()
    assert latest.trial.source == library and latest.trial.cutoff == later
    print("日线原生表", displayed.to_dict("records"), "18:00 直接和带入",
          [(r["value"], r["subject"]) for r in direct.rows],
          [(r["value"], r["subject"]) for r in latest.result.rows], direct.gaps)
    assert not direct.rows and not latest.result.rows
    assert direct.gaps == latest.result.gaps
    gap, = direct.gaps
    assert (gap["subject"], gap["field"], gap["period"], gap["reason"]) == (CODE, "amount_cny", str(DAY), reason)
    proof, = gap["evidence"]
    assert proof["evidence_ref"] == "new.json" and proof["snapshot_id"] == current.snapshot_id
    assert proof["observed_value"] == (None if native_amount == "" else native_amount)
    assert proof["value"] == 830 and not proof["matched"]
    assert reason in _page_evidence(page, "gap")

    request = FactorRequest("traded_amount", (CODE,), DAY, later, {"mode": "daily"})
    facts = read_facts(store, library, get_factor("traded_amount"), request)
    historical, = known_records(facts, "daily_bars", first.trial.request, CODE)
    selected, = known_records(facts, "daily_bars", request, CODE)
    assert historical["amount_cny"] == 700
    assert "new.json" not in repr(historical)
    assert "amount_cny" not in selected
    assert {part["evidence_ref"] for part in selected["_conflicts"]["amount_cny"]} == {"new.json"}
    print("历史截止重建", historical["amount_cny"], "新版字段缺口", selected["_conflicts"]["amount_cny"])


@pytest.mark.parametrize("other_calendar_source", [False, True], ids=["one-calendar-original", "other-early-original"])
def test_calendar_revision_reaches_coverage_preview_and_factor(tmp_path, monkeypatch, other_calendar_source):
    path = tmp_path / "sessions"
    later = CLOSE.replace(hour=18)
    store, old = _import_amount(path, 830, "amount.json", calendar_open=False)
    _, current = _import_amount(path, 830, "amount.json", calendar_published=later.isoformat())
    if other_calendar_source:
        manifest_file = path / "manifest.json"
        manifest = json.loads(manifest_file.read_text())
        calendar = next(item for item in manifest["items"] if item["table"] == "calendar")
        earlier = path / "earlier-calendar.csv"
        earlier.write_text("date,is_trading_day\n2024-03-29,True\n")
        manifest["items"].insert(0, dict(
            calendar, files=[earlier.name],
            source=dict(calendar["source"], name="更早公开的独立日历", published_at="2023-10-01T09:00:00+08:00"),
        ))
        manifest_file.write_text(json.dumps(manifest))
        current = store.import_files(manifest_file, (earlier, *(path / f"{table}.csv" for table in PRODUCTS)))
    rule = dict(
        root=str(store.root), product_id="factor_calendar", table="calendar",
        superseded_snapshot_id=old.snapshot_id, selected_snapshot_id=current.snapshot_id,
        codes=[], start=str(DAY), end=str(DAY), evidence_ref="evidence/calendar-revision.json",
    )
    (store.root / rule["evidence_ref"]).write_text(json.dumps(rule))
    assets = [dict(root=str(store.root), snapshot_id=ref.snapshot_id, tables=list(PRODUCTS))
              for ref in (old, current)]
    independent = publish_library(store, asset_snapshots=assets)
    assets[1]["revision_selections"] = [rule]
    published = publish_library(store, asset_snapshots=assets)

    _reading(_compute(store, current, later), current, 830, "amount.json")
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
    _browse(page, published, "calendar", code="", day=DAY)
    _query(page)
    assert page.dataframe[0].value["is_trading_day"].tolist() == ["True"]

    for source, coverage, expected in (
        (independent, "应有数量未知", []), (published, "实有 1，应有 1", [830]),
    ):
        page = _traded_amount_trial(monkeypatch, tmp_path, store, source, coverage=coverage)
        early = _compute(store, source)
        print("早截止日历", source.library_id, [row["value"] for row in early.rows],
              [gap["field"] for gap in early.gaps])
        assert not early.rows and {gap["field"] for gap in early.gaps} == {"calendar"}
        assert any("已证休市" in item.value for item in page.markdown)
        page.time_input(key="factor_cutoff_traded_amount").set_value(time(18)).run()
        late_preview = [item.value for item in page.markdown if " · calendar · " in item.value]
        assert any("当日交易属性待证" in item for item in late_preview) == (not expected)
        run_trial(page)
        attempt, = page.session_state["factor_attempts"].values()
        direct = _compute(store, source, later)
        print("日历修订", source.library_id, coverage,
              [row["value"] for row in attempt.result.rows], direct.gaps, late_preview)
        assert [row["value"] for row in direct.rows] == [row["value"] for row in attempt.result.rows] == expected
        if expected:
            assert page.dataframe[0].value["读数"].tolist() == [830]
            calendar, = attempt.result.rows[0]["inputs"][0]["_workbench_calendar"]
            assert {row["snapshot_id"] for row in calendar} == {current.snapshot_id}
        else:
            assert {gap["field"] for gap in direct.gaps} == {"calendar"}


def test_calendar_revision_does_not_resurrect_revoked_status(tmp_path, monkeypatch):
    path = tmp_path / "restricted-calendar"
    later = CLOSE.replace(hour=18)
    store, old = _import_amount(path, 830, "amount.json")
    _, current = _import_amount(
        path, 830, "amount.json", calendar_open=False, calendar_published=later.isoformat(),
        calendar_allowed=["date"],
    )
    rule = dict(
        root=str(store.root), product_id="factor_calendar", table="calendar",
        superseded_snapshot_id=old.snapshot_id, selected_snapshot_id=current.snapshot_id,
        codes=[], start=str(DAY), end=str(DAY), evidence_ref="evidence/calendar-revision.json",
    )
    (store.root / rule["evidence_ref"]).write_text(json.dumps(rule))
    assets = [dict(root=str(store.root), snapshot_id=ref.snapshot_id, tables=list(PRODUCTS))
              for ref in (old, current)]
    assets[1]["revision_selections"] = [rule]
    published = publish_library(store, asset_snapshots=assets)
    assert [row["value"] for row in _compute(store, published).rows] == [830]
    late = _compute(store, published, later)
    print("晚截止日历状态受限", [row["value"] for row in late.rows], late.gaps)
    assert not late.rows and {gap["field"] for gap in late.gaps} == {"calendar"}
    page = _traded_amount_trial(monkeypatch, tmp_path, store, published, coverage="应有数量未知")
    page.time_input(key="factor_cutoff_traded_amount").set_value(time(18)).run()
    assert any("当日交易属性待证" in item.value for item in page.markdown)
    run_trial(page)
    attempt, = page.session_state["factor_attempts"].values()
    assert not attempt.result.rows and attempt.result.gaps == late.gaps


def test_archive_policy_uses_the_same_restricted_calendar_revision(tmp_path, monkeypatch):
    path, code, calendar = tmp_path / "archive", "A.SZ", "cn_sz_archive_calendar"
    store, old = _import_amount(path, 830, "amount.json", code=code, calendar_product=calendar, calendar_open=False)
    _, current = _import_amount(
        path, 830, "amount.json", code=code, calendar_product=calendar, calendar_allowed=["date"],
    )
    rev_ref = "evidence/calendar-revision.json"
    rule = dict(
        root=str(store.root), product_id=calendar, table="calendar",
        superseded_snapshot_id=old.snapshot_id, selected_snapshot_id=current.snapshot_id,
        codes=[], start=str(DAY), end=str(DAY), evidence_ref=rev_ref,
    )
    (store.root / rev_ref).write_text(json.dumps(rule))
    policy_ref = "evidence/synthetic-source-policy.json"
    (store.root / policy_ref).write_text(json.dumps(dict(
        purpose="daily_archive", primary_product_id="factor_daily", compared_product_ids=["comparison"],
        codes=[code], start=str(DAY), end=str(DAY), fields=["amount_cny"], basis="合成原价口径",
        object_days={f"{code}/{DAY}": {}},
    )))
    assets = [dict(root=str(store.root), snapshot_id=ref.snapshot_id, tables=list(PRODUCTS))
              for ref in (old, current)]
    assets[1]["revision_selections"] = [rule]
    ordinary = publish_library(store, asset_snapshots=assets)
    assets[0]["source_policy"] = dict(primary_product_id="factor_daily", basis_ref=policy_ref)
    adjudicated = publish_library(store, asset_snapshots=assets)
    page = open_factors(monkeypatch, tmp_path)
    monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
    for library in (ordinary, adjudicated):
        _browse(page, library, "daily_bars", code=code, day=DAY)
        _query(page)
        details = [json.loads(item.value) for item in page.json]
        reasons = {gap["reason"] for group in details if isinstance(group, list) for gap in group}
        print("档案日历撤权", library.library_id, reasons)
        assert page.dataframe[0].value["close_raw"].tolist() == [100]
        assert any("应有数量未知" in item.value for item in page.caption)
        assert reasons == {"lifecycle_unknown"}


def test_calendar_revision_matches_original_after_nested_snapshot_inheritance(tmp_path, monkeypatch):
    path = tmp_path / "calendar-lineage"
    later = CLOSE.replace(hour=18)
    store, old = _import_amount(path, 830, "amount.json", calendar_open=False)
    _import_amount(path, 830, "amount.json", calendar_published=later.isoformat())
    manifest_file = path / "manifest.json"
    manifest = json.loads(manifest_file.read_text())
    calendar = next(item for item in manifest["items"] if item["table"] == "calendar")
    earlier = path / "earlier-calendar.csv"
    earlier.write_text("date,is_trading_day\n2024-03-29,True\n")
    manifest["items"].append(dict(
        calendar, files=[earlier.name],
        source=dict(calendar["source"], name="独立日历原件", published_at="2023-10-01T09:00:00+08:00"),
    ))
    manifest_file.write_text(json.dumps(manifest))
    current = store.import_files(manifest_file, (earlier, *(path / f"{table}.csv" for table in PRODUCTS)))
    noncalendar = tuple(table for table in PRODUCTS if table != "calendar")
    noncalendar_files = tuple(path / f"{table}.csv" for table in noncalendar)
    noncalendar_items = [item for item in manifest["items"] if item["table"] in noncalendar]

    for generation in range(3):
        if generation:
            manifest_file.write_text(json.dumps(dict(
                manifest, base_snapshot_ids=[current.snapshot_id], items=noncalendar_items,
            )))
            current = store.import_files(manifest_file, noncalendar_files)
        rule = dict(
            root=str(store.root), product_id="factor_calendar", table="calendar",
            superseded_snapshot_id=old.snapshot_id, selected_snapshot_id=current.snapshot_id,
            codes=[], start=str(DAY), end=str(DAY), evidence_ref=f"evidence/calendar-generation-{generation}.json",
        )
        (store.root / rule["evidence_ref"]).write_text(json.dumps(rule))
        assets = [dict(root=str(store.root), snapshot_id=ref.snapshot_id, tables=list(PRODUCTS))
                  for ref in (old, current)]
        assets[1]["revision_selections"] = [rule]
        published = publish_library(store, asset_snapshots=assets)

        raw = store.read_table(current, "calendar")
        stored = raw.loc[raw["date"].eq(str(DAY))].iloc[0]
        assert stored["is_trading_day"] == "True"
        assert stored["source_ref"] == "raw/factor_calendar/calendar.csv"
        original, = [
            raw_file["path"]
            for item in store.load_manifest(current)["items"]
            if item["table"] == "calendar" and item["source"]["published_at"] == later.isoformat()
            for raw_file in item["raw_files"]
        ]
        assert original.count("inherited/") == generation
        page = _traded_amount_trial(monkeypatch, tmp_path, store, published, coverage="实有 1，应有 1")
        early_preview = [item.value for item in page.markdown if " · calendar · " in item.value]
        early = _compute(store, published)
        assert not early.rows and {gap["field"] for gap in early.gaps} == {"calendar"}
        assert any("已证休市" in item for item in early_preview)
        page.time_input(key="factor_cutoff_traded_amount").set_value(time(18)).run()
        late_preview = [item.value for item in page.markdown if " · calendar · " in item.value]
        run_trial(page)
        assert not page.exception
        attempt, = page.session_state["factor_attempts"].values()
        direct = _compute(store, published, later)
        direct_values = [row["value"] for row in direct.rows]
        trial_values = [row["value"] for row in attempt.result.rows]
        print("日历传承", generation, original, "普通浏览覆盖 1/1",
              "15:00", early_preview, "18:00", late_preview, "试算", trial_values, "直接", direct_values)
        assert not any("已证休市" in item or "当日交易属性待证" in item for item in late_preview)
        assert attempt.trial.source == published and attempt.trial.cutoff == later
        assert direct_values == trial_values == [830]
        assert not direct.gaps and not attempt.result.gaps
        assert page.dataframe[0].value["读数"].tolist() == [830]
        witness_group, = direct.rows[0]["inputs"][0]["_workbench_calendar"]
        witness, = witness_group
        assert witness["snapshot_id"] == current.snapshot_id
        assert witness["source_ref"] == stored["source_ref"] and witness["available_at"] == later.isoformat()
