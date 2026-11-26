"""冻结研究输入资格及合成数据的离线账户验证。"""

import io
import json
import runpy
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from backtest_cases import build_case_store, make_backtest_case
from hetu_quant.data_archive import archive_requirements, audit_archive
from hetu_quant.data_research import audit_research_coverage, prepare_research_delivery, research_data_requirements
from test_backtest_account import ADMITTED, _engine_python
from test_data_archive import targets


def protocol():
    return json.loads((Path(__file__).parents[1] / "research/phase2-protocol.json").read_text())


def calendar():
    days = pd.date_range("2016-04-01", "2026-10-13")
    return pd.DataFrame({"date": days.date.astype(str), "is_trading_day": days.weekday < 5})


def test_research_requirements_preserve_all_windows():
    req = research_data_requirements(protocol(), calendar(), targets(make_backtest_case("coverage")))
    assert len(req["definitions"]) == 8
    assert {d["definition"]["protocol"]["warmup_months"] for d in req["definitions"]} == {30, 36, 42}
    assert req["start"] == "2016-04-01"
    assert req["end"] == "2026-10-13"
    assert len(req["signal_dates"]) == 84
    assert next(d for d in req["definitions"] if d["trial_id"] == "final_capacity")["dependencies"] == ["final_main"]


@pytest.fixture
def qualification_case(tmp_path, monkeypatch, request):
    from backtest_cases import build_prepared_case_store

    inputs = protocol()
    variant = getattr(request, "param", None)
    rename = variant == "code_change"
    action = variant if isinstance(variant, tuple) else None
    listed_from = variant if isinstance(variant, str) and not rename else "2000-01-01"
    roster = targets(make_backtest_case("coverage")).iloc[[0]].copy()
    roster.loc[:, "valid_from"] = listed_from
    roster.loc[:, "valid_to"] = ""
    roster["board"] = "main"
    code = roster.iloc[0]["code"]
    if rename:
        last, effective, successor, published = (
            "2020-09-30", "2020-10-01", "SYN_RENAMED.SH", "2020-09-25T12:00:00+08:00"
        )
        proof = dict(kind="administrative_rename", from_code=code, effective_date=effective,
                     ratio=1, published_at=published)
        next_period = roster.assign(code=successor, valid_from=effective, continuation=[proof])
        roster = pd.concat([roster.assign(valid_to=last), next_period], ignore_index=True)
    if action:
        # 其余合成证券收益为零，让正式低波动选择受分红调整的首个代码。
        peers = [
            roster.assign(code=f"SYN_{letter}.SH", identity_id=f"entity-{letter}") for letter in "BCDEFGHIJ"
        ]
        roster = pd.concat([roster, *peers], ignore_index=True)
    roster.attrs["target_scope"] = {
        "complete": True, "source_ref": "evidence/test-roster.json" if rename else "test-roster",
    }
    cal = calendar()
    if action:
        cal.loc[cal.date.eq("2024-01-01"), "is_trading_day"] = False
    requirements = research_data_requirements(inputs, cal, roster)
    case = make_backtest_case("history")
    case["codes"] = tuple(roster["code"])
    case["csvs"] = {table: csv.replace("KEEP_A", code) for table, csv in case["csvs"].items()}
    case["csvs"]["historical_securities"] = case["csvs"]["historical_securities"].replace("2019-01-02", listed_from)
    case["csvs"]["knowledge_evidence"] = case["csvs"]["knowledge_evidence"].replace(
        "2019-01-02T18", f"{listed_from}T18"
    )
    if rename:
        for table in ("historical_securities", "knowledge_evidence"):
            header, *lines = case["csvs"][table].splitlines()
            row = next(line for line in lines if code in line)
            new = row.replace(code, successor).replace(f"{listed_from}T18:00:00+08:00", published)
            if table == "historical_securities":
                row = row.replace(f",{listed_from},,", f",{listed_from},{last},")
                new = new.replace(f",{listed_from},,", f",{effective},,")
            case["csvs"][table] = f"{header}\n{row}\n{new}\n"
    if action:
        for table in ("historical_securities", "knowledge_evidence"):
            header, *lines = case["csvs"][table].splitlines()
            base_row = next(line for line in lines if code in line)
            case["csvs"][table] = "\n".join([header, *(base_row.replace(code, c) for c in case["codes"])]) + "\n"
    case["csvs"]["trading_rules"] = case["csvs"]["trading_rules"].replace("2019-01-01", "2016-04-01")
    case["csvs"]["calendar"] = cal.to_csv(index=False)
    sessions = requirements["expected_sessions"]
    volume = 100_000 if action else 1000
    bars = []
    for c in case["codes"]:
        for day in sessions:
            price = 9.5 if action and c == code and day >= action[0] else 10
            bars.append(f"{day},{c},{price},11,9,{price},{volume},{price * volume}\n")
    case["csvs"]["daily_bars"] = (
        "date,code,open_raw,high_raw,low_raw,close_raw,volume_shares,amount_cny\n" + "".join(bars)
    )
    case["csvs"]["security_status"] = "date,code,is_st,is_suspended,is_listed\n" + "".join(
        f"{day},{c},False,{'True' if day == '2019-11-04' else 'False'},True\n"
        for c in case["codes"] for day in sessions
    )
    case["csvs"]["market_cap"] = "code,date,market_cap_cny,available_at\n" + "".join(
        f"{c},{day},1000000,{day}T15:00:00+08:00\n"
        for c in case["codes"] for day in requirements["signal_dates"]
    )
    if rename:
        for table in ("daily_bars", "security_status", "market_cap"):
            frame = pd.read_csv(io.StringIO(case["csvs"][table]), dtype=str)
            frame = frame[(frame.code.eq(code) & frame.date.lt(effective))
                          | (frame.code.eq(successor) & frame.date.ge(effective))]
            case["csvs"][table] = frame.to_csv(index=False)
    case["actions_coverage"] = {"start": "2016-04-01", "end": "2026-10-13"}
    case["obtained_at"] = "2026-10-16T12:00:00+08:00"
    store, ref = build_prepared_case_store(tmp_path / "facts", case, monkeypatch)
    if rename:
        (store.root / "evidence" / "test-roster.json").write_text(json.dumps(roster.fillna("").to_dict("records")))
    products_path = store.root / "data-products.json"
    products = json.loads(products_path.read_text())
    for product in products["products"].values():
        product["permission"]["allowed_uses"] = ["backtest_low_volatility"]
    products_path.write_text(json.dumps(products))
    if action:
        from hetu_quant.data import DataStore

        ex_date, record_date, amount, *publication = action
        first_known = publication[0] if publication else f"{record_date}T10:00:00+08:00"
        action_type = publication[1] if len(publication) > 1 else "cash_dividend"
        raw = tmp_path / f"{action_type}.csv"
        raw.write_text(
            "code,ex_date,action_type,m,d,record_date,pay_date,available_at\n"
            f"{code},{ex_date},{action_type},1,{amount},{record_date},,{first_known}\n"
        )
        import_request = tmp_path / "event-supplement.json"
        import_request.write_text(json.dumps(dict(mode="free", base_snapshot_ids=[ref.snapshot_id], items=[dict(
            product_id="cn_a_actions", table="corporate_actions", supplement=True, files=[raw.name],
            source=dict(name="synthetic dividend announcement", fee_class="free",
                        permission_basis="synthetic test data", obtained_at=case["obtained_at"]),
        )])))
        ref = DataStore(store.root).import_files(import_request, (raw,))
    inputs["definition"]["snapshot_id"] = ref.snapshot_id
    scope = {
        "coverage": [
            {
                "code": c,
                "domain": domain,
                "covered": [["2019-11-01" if domain == "trading_rules" else "2016-04-01", "2026-10-13"]],
                "data_gaps": [["2016-04-29", "2016-04-29"]] if domain == "daily_bars" and not action else [],
                "source_refs": [f"test:{domain}"],
            }
            for c in case["codes"] for domain in ("daily_bars", "security_status", "trading_rules", "corporate_actions")
        ],
        "signal_evidence": [
            {"code": c, "date": day, "history_revision": ["v1"],
             "market_cap_known": True, "cutoff": day + "T15:00:00+08:00"}
            for c in case["codes"] for day in requirements["signal_dates"]
        ],
    }
    (tmp_path / "requirements.json").write_text(json.dumps(requirements))
    (tmp_path / "coverage.json").write_text(json.dumps(scope))
    (tmp_path / "handoff.json").write_text(json.dumps({
        "study_id": "phase2-lowvol", "snapshot_id": ref.snapshot_id, "mode": "free"
    }))
    return inputs, store.root, code, requirements, scope


def test_monthly_trial_evidence(tmp_path, qualification_case):
    from hetu_quant.data_research import qualify_frozen_trials

    inputs, root, code, requirements, _ = qualification_case
    result = qualify_frozen_trials(inputs, root, tmp_path, [])
    by_trial = {row["trial_id"]: row for row in result["trials"]}
    assert len(by_trial) == 8
    assert by_trial["dev_neighbor_42"]["input_status"] == "blocked"
    assert by_trial["dev_neighbor_30"]["input_status"] == "qualified"
    assert by_trial["dev_main"]["input_status"] == "qualified"
    assert "dev_neighbor_42" in by_trial["final_benchmark"]["pending_dependencies"]
    assert any(r["signal_date"] == "2019-10-31" for r in result["months"])
    assert all(r["signal_month"] != "2026-10" for r in result["months"])
    assert any(r["code"] == code and r["field"] == "daily_bars" for r in result["field_evidence"])


@pytest.mark.parametrize("qualification_case", ["code_change"], indirect=True)
def test_proven_rename_qualifies_holding_and_warmup_segments(tmp_path, qualification_case):
    from hetu_quant.data_research import qualify_frozen_trials

    inputs, root, code, requirements, _ = qualification_case
    qualified = qualify_frozen_trials(inputs, root, tmp_path, [])
    trial = next(row for row in qualified["trials"] if row["trial_id"] == "dev_main")
    assert trial["input_status"] == "qualified", [
        qualified["field_evidence"][key] for key in trial["evidence_ids"]
    ]
    by_month = {row["signal_month"]: row for row in qualified["months"] if "dev_main" in row["trial_ids"]}
    assert by_month["2020-09"]["qualified_codes"] == [code]
    assert by_month["2020-10"]["qualified_codes"] == ["SYN_RENAMED.SH"]
    requirements["objects"][1].pop("continuation")
    (tmp_path / "requirements.json").write_text(json.dumps(requirements))
    blocked = qualify_frozen_trials(inputs, root, tmp_path, [])
    trial = next(row for row in blocked["trials"] if row["trial_id"] == "dev_main")
    assert trial["input_status"] == "blocked"
    assert any(blocked["field_evidence"][i]["reason"] == "identity_transition_unproved"
               for i in trial["evidence_ids"])

    requirements["calendar_days"].remove("2019-10-31")
    (tmp_path / "requirements.json").write_text(json.dumps(requirements))
    missing_calendar = qualify_frozen_trials(inputs, root, tmp_path, [])
    october = [r for r in missing_calendar["months"] if r["signal_month"] == "2019-10"]
    assert october and all(r["signal_date"] is None for r in october)
    assert all(r["input_status"] == "blocked" for r in missing_calendar["trials"] if r["trial_id"].startswith("dev"))

    requirements["target_scope"]["complete"] = False
    (tmp_path / "requirements.json").write_text(json.dumps(requirements))
    result = qualify_frozen_trials(inputs, root, tmp_path, [])
    assert result["months"][0]["universe_count"] is None


@pytest.mark.parametrize(
    ("peer_period", "active_end", "clear_month", "affected_month", "clear_trial"),
    [
        (("2024-07-01", ""), "2024-06-30", "2019-10", "2024-06", "dev_main"),
        (("2016-04-29", "2020-10-30"), "", "2023-12", "2021-10", "final_main"),
    ],
)
def test_code_transition_only_blocks_windows_that_use_both_codes(
    tmp_path, qualification_case, peer_period, active_end, clear_month, affected_month, clear_trial,
):
    from hetu_quant.data_research import qualify_frozen_trials

    inputs, root, code, requirements, _ = qualification_case
    active = requirements["objects"][0]
    active["valid_to"] = active_end
    if active_end:
        active["required"][1] = active_end
    peer_start, peer_end = peer_period
    requirements["objects"].append(dict(
        active, code="SYN_PEER.SH", valid_from=peer_start, valid_to=peer_end,
        required=[peer_start, peer_end or requirements["end"]], source_ref="test-code-period",
    ))
    (tmp_path / "requirements.json").write_text(json.dumps(requirements))
    result = qualify_frozen_trials(inputs, root, tmp_path, [])
    windows = {row["signal_month"]: row for row in result["months"] if row["warmup_months"] == 36
               and (clear_trial in row["trial_ids"] or row["signal_month"] == affected_month)}
    trial = next(row for row in result["trials"] if row["trial_id"] == clear_trial)
    gap = next(row for row in result["field_evidence"]
               if row["code"] == code and row["reason"] == "identity_transition_unproved")
    assert (affected_month, 36, windows[affected_month]["account_start"]) in gap["windows"]
    assert (clear_month, 36, windows[clear_month]["account_start"]) not in gap["windows"]
    assert code not in windows[affected_month]["qualified_codes"]
    assert code in windows[clear_month]["qualified_codes"]
    assert trial["input_status"] == "qualified"


@pytest.mark.parametrize("qualification_case", ["2018-01-01"], indirect=True)
def test_pre_study_predecessor_is_retained_for_successor_warmup(tmp_path, qualification_case):
    from hetu_quant.data_research import qualify_frozen_trials

    inputs, root, code, baseline, _ = qualification_case
    current = baseline["objects"][0]
    old = dict(current, code="SYN_OLD.SH", valid_from="2014-01-01", valid_to="2017-12-31",
               source_ref="test-predecessor")
    outside = dict(old, code="SYN_OFF.SH", identity_id="independent-old", source_ref="test-independent-exit")
    stale = dict(old, code="SYN_STALE.SH", valid_from="2010-01-01", valid_to="2016-04-28", source_ref="test-stale-code")
    roster = pd.DataFrame([current, old, outside, stale])
    roster.attrs["target_scope"] = baseline["target_scope"]
    requirements = research_data_requirements(inputs, calendar(), roster)
    (tmp_path / "requirements.json").write_text(json.dumps(requirements))
    result = qualify_frozen_trials(inputs, root, tmp_path, [])
    dev = next(row for row in result["trials"] if row["trial_id"] == "dev_main")
    assert dev["input_status"] == "blocked"
    assert [(row["code"], row["reason"]) for row in requirements["legal_exclusions"]] == [
        ("SYN_OFF.SH", "pre_study_exit"), ("SYN_STALE.SH", "pre_study_exit")
    ]
    gaps = [result["field_evidence"][i] for i in dev["evidence_ids"]]
    first = next(row for row in result["months"] if row["signal_month"] == "2019-10" and "dev_main" in row["trial_ids"])
    assert first["required_month_ends"][0] == "2016-10-31"
    assert any(row["code"] == code and row["reason"] == "identity_transition_unproved"
               and ("2019-10", 36, "2019-11-01") in row["windows"] for row in gaps)
    assert current["identity_id"] not in first["warmup_excluded_ids"]
    _assert_same_roster_in_legacy_delivery(tmp_path, root, inputs, requirements, "SYN_OLD.SH", result)


def _assert_same_roster_in_legacy_delivery(tmp_path, root, inputs, requirements, excluded_code, expected):
    from copy import deepcopy

    from hetu_quant.data_research import qualify_frozen_trials

    old_requirements = deepcopy(requirements)
    old = next(row for row in old_requirements["objects"] if row["code"] == excluded_code)
    old["excluded"] = True
    old_requirements["legal_exclusions"].append(dict(
        code=old["code"], identity_id=old["identity_id"], reason="pre_study_exit", source_ref=old["source_ref"],
    ))
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    summary = json.loads((tmp_path / "coverage.json").read_text())
    # 旧版先排除该代码，逐行派生覆盖和信号表没有对应行。
    coverage = [row for row in summary.pop("coverage") if row["code"] != excluded_code]
    summary["signal_evidence"] = [row for row in summary["signal_evidence"] if row["code"] != excluded_code]
    originals = {
        "research-requirements.json": json.dumps(old_requirements).encode(),
        "research-audit-summary.json": json.dumps(summary).encode(),
        "research-coverage.jsonl": "".join(json.dumps(row) + "\n" for row in coverage).encode(),
        "handoff.json": (tmp_path / "handoff.json").read_bytes(),
    }
    for filename, content in originals.items():
        (legacy / filename).write_bytes(content)
    actual = qualify_frozen_trials(inputs, root, legacy, [])
    assert {name: (legacy / name).read_bytes() for name in originals} == originals
    assert actual["months"] == expected["months"]
    assert actual["field_evidence"] == expected["field_evidence"]
    assert [trial["input_status"] for trial in actual["trials"]] == [
        trial["input_status"] for trial in expected["trials"]
    ]


def test_last_valid_day_at_first_signal_is_in_historical_pool(tmp_path, qualification_case):
    from hetu_quant.data_research import qualify_frozen_trials

    inputs, root, code, old, scope = qualification_case
    next(row for row in scope["coverage"] if row["domain"] == "daily_bars")["data_gaps"] = []
    (tmp_path / "coverage.json").write_text(json.dumps(scope))
    roster = pd.DataFrame([dict(old["objects"][0], valid_to="2019-10-31")])
    roster.attrs["target_scope"] = old["target_scope"]
    requirements = research_data_requirements(inputs, calendar(), roster)
    (tmp_path / "requirements.json").write_text(json.dumps(requirements))
    result = qualify_frozen_trials(inputs, root, tmp_path, [])
    first = next(row for row in result["months"] if row["signal_month"] == "2019-10" and "dev_main" in row["trial_ids"])
    assert first["signal_date"] == "2019-10-31" and first["account_start"] == "2019-11-01"
    assert first["universe_count"] == 1
    assert first["qualified_codes"] == [code]
    assert not requirements["legal_exclusions"]
    following = next(
        row for row in result["months"] if row["signal_month"] == "2019-11" and "dev_main" in row["trial_ids"]
    )
    assert following["universe_count"] == 0
    _assert_same_roster_in_legacy_delivery(tmp_path, root, inputs, requirements, code, result)


@pytest.mark.parametrize("known_period", [True, False])
def test_unknown_attributes_are_scoped_by_proved_lifecycle(tmp_path, qualification_case, known_period):
    from hetu_quant.data_research import qualify_frozen_trials

    inputs, root, _, baseline, _ = qualification_case
    active = baseline["objects"][0]
    future = dict(active, code="SYN_FUTURE.SH", valid_from="2025-01-01", market="",
                  lifecycle_status="known" if known_period else "conflict", source_ref="test-future-period")
    roster = pd.DataFrame([active, future])
    roster.attrs["target_scope"] = baseline["target_scope"]
    requirements = research_data_requirements(inputs, calendar(), roster)
    (tmp_path / "requirements.json").write_text(json.dumps(requirements))
    result = qualify_frozen_trials(inputs, root, tmp_path, [])
    trials = {row["trial_id"]: row for row in result["trials"]}
    assert trials["dev_main"]["input_status"] == ("qualified" if known_period else "blocked")
    assert trials["final_main"]["input_status"] == "blocked"
    gap = next(row for row in result["field_evidence"]
               if row["code"] == "SYN_FUTURE.SH" and row["field"] == "historical_universe")
    assert gap["reason"] == ("security_attributes_unproved" if known_period else "identity_or_lifecycle_unproved")
    assert gap["source_refs"] == ["test-future-period"]
    if known_period:
        assert gap["intervals"]["required"] == ["2025-01-01", "2026-10-13"]
        assert gap["intervals"]["missing_fields"] == ["market"]
    assert ("2025-01", 36, "2024-01-01") in gap["windows"]
    assert (("2019-10", 36, "2019-11-01") in gap["windows"]) == (not known_period)


def test_dividend_gaps_link_each_event_to_its_own_windows(tmp_path, qualification_case):
    from hetu_quant.data_research import qualify_frozen_trials

    inputs, root, code, _, scope = qualification_case
    dates = ("2023-06-14", "2024-06-14")
    scope["event_limitations"] = [
        dict(code=code, domain="corporate_actions", reason="unsupported_or_missing_event_fields",
             date=day, action_type="cash_dividend", missing_fields=["record_date", missing],
             source_refs=[f"original:{day}"])
        for day, missing in zip(dates, ("pay_date", "d"), strict=True)
    ]
    (tmp_path / "coverage.json").write_text(json.dumps(scope))
    result = qualify_frozen_trials(inputs, root, tmp_path, [])
    rows = {r["intervals"]["date"]: r for r in result["field_evidence"]
            if r["reason"] == "unsupported_or_missing_event_fields"}
    assert set(rows) == set(dates)
    early, late = (rows[day] for day in dates)
    assert early["source_refs"] == ["original:2023-06-14"]
    assert late["source_refs"] == ["original:2024-06-14"]
    assert early["intervals"]["missing_fields"] == ["record_date", "pay_date"]
    assert late["intervals"]["missing_fields"] == ["record_date", "d"]
    assert ("2023-05", 36, "2019-11-01") in early["windows"]
    assert ("2023-05", 36, "2019-11-01") not in late["windows"]
    for trial in result["trials"]:
        assert (late["evidence_id"] in trial["evidence_ids"]) == trial["trial_id"].startswith("final_")


@pytest.mark.parametrize(("day", "field", "final_status"), [
    ("2023-06-14", "pay_date", "qualified"),
    ("2023-09-14", "d", "blocked"),
])
def test_pre_entry_dividend_checks_fields_needed_for_warmup(tmp_path, qualification_case, day, field, final_status):
    from hetu_quant.data_research import qualify_frozen_trials

    inputs, root, code, _, scope = qualification_case
    scope["event_limitations"] = [
        dict(code=code, domain="corporate_actions", reason="unsupported_or_missing_event_fields",
             date=day, action_type="cash_dividend", missing_fields=[field], source_refs=[f"original:{day}"])
    ]
    (tmp_path / "coverage.json").write_text(json.dumps(scope))
    result = qualify_frozen_trials(inputs, root, tmp_path, [])
    trials = {row["trial_id"]: row for row in result["trials"]}
    evidence = next(row for row in result["field_evidence"] if row["intervals"].get("date") == day)
    assert evidence["intervals"]["missing_fields"] == [field]
    assert evidence["evidence_id"] in trials["dev_main"]["evidence_ids"]
    assert trials["dev_main"]["input_status"] == "blocked"
    for trial_id, trial in trials.items():
        if trial_id.startswith("final_"):
            assert trial["input_status"] == final_status
            assert (evidence["evidence_id"] in trial["evidence_ids"]) == (field == "d")
    assert "dev_main" in trials["final_benchmark"]["pending_dependencies"]


@pytest.mark.parametrize(("qualification_case", "expected_status"), [
    (("2023-12-29", "2023-12-28", 0.5, "2023-12-29T14:55:00+08:00"), "qualified"),
    (("2023-12-29", "2023-12-28", 0.5, "2023-12-29T18:00:00+08:00"), "blocked"),
], indirect=["qualification_case"])
def test_action_terms_close_cutoff_matches_formal_preparation(tmp_path, qualification_case, expected_status):
    from hetu_quant.backtest_data import prepare_backtest
    from hetu_quant.backtest_models import backtest_definition_from_dict
    from hetu_quant.data import DataStore, SnapshotRef
    from hetu_quant.data_research import qualify_frozen_trials

    inputs, root, code, requirements, _ = qualification_case
    snapshot_id = inputs["definition"]["snapshot_id"]
    store = DataStore(root)
    action = store.read_table(SnapshotRef(snapshot_id, root), "corporate_actions").iloc[0]
    result = qualify_frozen_trials(inputs, root, tmp_path, [])
    trial = next(row for row in result["trials"] if row["trial_id"] == "final_main")
    month = next(row for row in result["months"]
                 if row["signal_date"] == "2023-12-29" and "final_main" in row["trial_ids"])
    assert trial["input_status"] == expected_status
    assert trial["pending_dependencies"] == ["final_benchmark"]
    allowed = expected_status == "qualified"
    assert month["qualified_count"] == (10 if allowed else 9)
    assert (code in month["qualified_codes"]) is allowed

    raw = next(item["definition"] for item in requirements["definitions"] if item["trial_id"] == "final_main")
    definition = backtest_definition_from_dict(dict(raw, snapshot_id=snapshot_id))
    bundle = tmp_path / "prepared-cutoff"
    assert prepare_backtest(definition, store, bundle).can_run is allowed
    universe = pd.read_parquet(bundle / "universe.parquet")
    assert (code in set(universe.loc[universe.signal_date.eq("2023-12-29"), "code"])) is allowed
    if not allowed:
        evidence = next(result["field_evidence"][key] for key in trial["evidence_ids"]
                        if result["field_evidence"][key]["field"] == "corporate_action_terms")
        assert evidence["code"] == code
        assert evidence["intervals"]["signal_date"] == evidence["intervals"]["date"] == "2023-12-29"
        assert evidence["intervals"]["action_type"] == "cash_dividend"
        assert evidence["intervals"]["missing_fields"] == ["d"]
        assert evidence["source_refs"] == [action["source_ref"]]
        assert ("2023-12", 36, "2024-01-01") in evidence["windows"]
        assert {window[0] for window in evidence["windows"]} == {"2023-12"}
        exclusions = pd.read_parquet(bundle / "exclusions.parquet")
        matched = exclusions[(exclusions.date == "2023-12-29") & (exclusions.code == code)]
        terms = matched[matched.field.eq("corporate_action_terms")].iloc[0]
        assert terms["reason"] == evidence["reason"]
        assert terms["source_ref"] in evidence["source_refs"]


@pytest.mark.parametrize(("qualification_case", "handoff_format", "expected_counts"), [
    pytest.param(("2023-12-29", "2023-12-28", "", "2023-12-29T18:00:00+08:00", "rights_issue"),
                 "current", (10, 9), id="current"),
    pytest.param(("2023-12-29", "2023-12-28", "", "2023-12-29T18:00:00+08:00", "rights_issue"),
                 "legacy", (10, 9), id="legacy"),
    pytest.param(("2023-12-29", "2023-12-28", "", "2023-12-29T18:00:00+08:00", "rights_issue"),
                 "legacy-unmatched", (9, 9), id="legacy-unmatched"),
    pytest.param(("2020-12-31", "2020-12-30", "", "2020-12-30T14:00:00+08:00", "rights_issue"),
                 "current", (10, 10), id="first-return-close"),
], indirect=["qualification_case"])
def test_persisted_action_audit_preserves_disclosure_clock(
    tmp_path, qualification_case, handoff_format, expected_counts,
):
    from hetu_quant.backtest_data import prepare_backtest
    from hetu_quant.backtest_models import backtest_definition_from_dict
    from hetu_quant.data import DataStore, SnapshotRef
    from hetu_quant.data_research import qualify_frozen_trials
    from hetu_quant.strategies import build_signals

    inputs, root, code, requirements, _ = qualification_case
    snapshot_id = inputs["definition"]["snapshot_id"]
    store = DataStore(root)
    audit = audit_research_coverage(requirements, store, SnapshotRef(snapshot_id, root))
    event = next(row for row in audit["event_limitations"] if row.get("action_type") == "rights_issue")
    at_first_close = event["date"] == "2020-12-31"
    assert event["available_at"] == (
        "2020-12-30T14:00:00+08:00" if at_first_close else "2023-12-29T18:00:00+08:00"
    )
    source_refs = event["source_refs"]
    if handoff_format.startswith("legacy"):
        event.pop("available_at")
        if handoff_format == "legacy-unmatched":
            event["source_refs"] = ["unmatched-announcement"]
    coverage_path = tmp_path / "coverage.json"
    handoff_bytes = json.dumps(audit).encode()
    coverage_path.write_bytes(handoff_bytes)
    result = qualify_frozen_trials(inputs, root, tmp_path, [])
    assert coverage_path.read_bytes() == handoff_bytes
    months = {row["signal_date"]: row for row in result["months"] if "final_main" in row["trial_ids"]}
    first_signal, later_signal = "2023-12-29", "2024-01-31"

    raw = next(item["definition"] for item in requirements["definitions"] if item["trial_id"] == "final_main")
    two_signals = backtest_definition_from_dict(dict(raw, snapshot_id=snapshot_id, end=later_signal))
    bundle = tmp_path / "prepared-round-trip"
    assert prepare_backtest(two_signals, store, bundle).can_run is at_first_close
    universe = pd.read_parquet(bundle / "universe.parquet")
    formal_counts = tuple(int(universe.signal_date.eq(day).sum()) for day in (first_signal, later_signal))
    audit_counts = tuple(months[day]["qualified_count"] for day in (first_signal, later_signal))
    assert formal_counts == (10, 10 if at_first_close else 9)
    assert audit_counts == expected_counts
    for day in (first_signal, later_signal):
        formal_codes = set(universe.loc[universe.signal_date.eq(day), "code"])
        if day == first_signal and handoff_format == "legacy-unmatched":
            formal_codes.remove(code)
        assert formal_codes == set(months[day]["qualified_codes"])
    assert build_signals(two_signals, store)["signal_at"].tolist() == (
        [first_signal, later_signal] if at_first_close else [first_signal]
    )

    final_main = next(row for row in result["trials"] if row["trial_id"] == "final_main")
    terms = [result["field_evidence"][e] for e in final_main["evidence_ids"]
             if result["field_evidence"][e]["intervals"].get("action_type") == "rights_issue"]
    if at_first_close:
        assert months[first_signal]["required_month_ends"][0] == event["date"]
        assert final_main["input_status"] == "qualified" and not terms
        dev_main = next(row for row in result["trials"] if row["trial_id"] == "dev_main")
        assert dev_main["input_status"] == "blocked"
        assert any(result["field_evidence"][e]["reason"] == "unsupported_or_missing_event_fields"
                   and result["field_evidence"][e]["intervals"].get("date") == event["date"]
                   for e in dev_main["evidence_ids"])
        return
    assert final_main["input_status"] == "blocked"
    december = [row for row in terms if ("2023-12", 36, "2024-01-01") in row["windows"]]
    assert [row["source_refs"] for row in december] == (
        [event["source_refs"]] if handoff_format == "legacy-unmatched" else []
    )
    later = next(row for row in terms if row["intervals"].get("signal_date") == later_signal)
    assert later["code"] == code
    assert later["intervals"]["missing_fields"] == ["action_type"]
    assert later["source_refs"] == source_refs


@ADMITTED
@pytest.mark.parametrize("qualification_case", [("2023-06-14", "2023-06-13", 0.5)], indirect=True)
def test_pre_entry_dividend_stays_in_warmup_but_never_enters_final_account(tmp_path, qualification_case):
    from hetu_quant.backtest import read_backtest, run_backtest
    from hetu_quant.backtest_data import prepare_backtest
    from hetu_quant.backtest_models import backtest_definition_from_dict
    from hetu_quant.data import DataStore, build_total_return_series
    from hetu_quant.data_research import qualify_frozen_trials, require_frozen_trial_inputs

    inputs, root, code, requirements, _ = qualification_case
    inputs["qualification_delivery"] = str(tmp_path)
    trials = qualify_frozen_trials(inputs, root, tmp_path, [])["trials"]
    by_trial = {trial["trial_id"]: trial for trial in trials}
    assert by_trial["dev_main"]["input_status"] == "blocked"
    assert by_trial["final_main"]["input_status"] == "qualified"
    assert "dev_main" in by_trial["final_benchmark"]["pending_dependencies"]
    raw = next(item["definition"] for item in requirements["definitions"] if item["trial_id"] == "final_main")
    definition = backtest_definition_from_dict(dict(raw, snapshot_id=inputs["definition"]["snapshot_id"]))
    store = DataStore(root)
    bundle = tmp_path / "prepared"
    assert prepare_backtest(definition, store, bundle).can_run
    actions = pd.read_parquet(bundle / "tables" / "corporate_actions.parquet")
    assert len(actions) == 1
    action = actions.iloc[0]
    assert action["ex_date"] == "2023-06-14" and action["record_date"] == "2023-06-13"
    assert action["d"] == pytest.approx(0.5)
    assert pd.isna(action["pay_date"]) or action["pay_date"] == ""
    bars = pd.read_parquet(bundle / "tables" / "daily_bars.parquet")
    prices = build_total_return_series(bars[bars.code.eq(code)], actions).set_index("date")
    raw_closes = prices["close_raw"].astype(float)
    assert raw_closes.at["2023-06-14"] / raw_closes.at["2023-06-13"] - 1 == pytest.approx(-0.05)
    assert (
        prices.at["2023-06-14", "close_total_return"] / prices.at["2023-06-13", "close_total_return"] - 1
    ) == pytest.approx(0)

    # 仅合成账户机制验证。原研究计划仍等待开发阶段的前序结果。
    admission = tmp_path / "engine-admission.json"
    admission.write_text(json.dumps({"decision": "candidate_ready"}))
    expected = require_frozen_trial_inputs(inputs, root, {"final_main"}, [])["final_main"]
    ref = run_backtest(definition, store, tmp_path / "synthetic-account", admission_path=admission,
                       engine_python=_engine_python(), expected_universe=expected)
    result = read_backtest(ref)
    assert ref.status == "complete"
    trades, cash = (result["tables"][key] for key in ("trades", "cash"))
    signals = pd.read_parquet(ref.root / "backtests" / ref.run_id / "bundle" / "signals.parquet")
    assert not trades.empty
    assert trades.iloc[0]["executed_at"] == "2024-01-02"
    first_signal = signals[signals["signal_at"].eq("2023-12-29")].iloc[0]
    assert first_signal["code"] == code
    assert first_signal["signal_value"] == pytest.approx(0)
    assert cash.receivable.eq(0).all()
    assert not result["manifest"].get("limitations")
    paid = trades["qty"] * trades["execution_price"]
    cash_used = paid.where(trades.side.eq("BUY"), -paid).sum()
    fees = trades[["commission", "stamp_tax", "transfer_fee", "dividend_tax"]].sum().sum()
    assert cash.iloc[-1]["cash_available"] == pytest.approx(1_000_000 - cash_used - fees, abs=0.01)


@pytest.mark.parametrize("qualification_case", [
    ("2023-06-14", "2023-06-13", ""),
    ("2024-06-14", "2024-06-13", 0.5),
], indirect=True)
def test_raw_dividend_missing_required_value_blocks_before_signals(tmp_path, qualification_case, monkeypatch):
    from hetu_quant import backtest, strategies
    from hetu_quant.data_research import qualify_frozen_trials

    inputs, root, code, _, _ = qualification_case
    inputs["qualification_delivery"] = str(tmp_path)

    def unexpected(*_args, **_kwargs):
        pytest.fail("资格不完整不应进入信号或引擎")

    monkeypatch.setattr(backtest, "run_engine_subprocess", unexpected)
    monkeypatch.setattr(strategies, "build_signals", unexpected)
    result = qualify_frozen_trials(inputs, root, tmp_path, [])
    settlement_reason = "unsupported_or_missing_event_fields"
    signal_reason = "total_return_action_terms_unproved"
    gaps = [row for row in result["field_evidence"] if row["code"] == code
            and row["reason"].startswith((settlement_reason, signal_reason))]
    gap = next(row for row in gaps if "final_main" in row["trial_ids"])
    pre_entry = gap["intervals"]["date"] == "2023-06-14"
    assert gap["intervals"]["missing_fields"] == (["d"] if pre_entry else ["pay_date"])
    assert {trial for row in gaps for trial in row["trial_ids"]} == {
        row["trial_id"] for row in result["trials"] if pre_entry or row["trial_id"].startswith("final_")
    }
    if pre_entry:
        assert gap["reason"].startswith(signal_reason)
        dev_gap = next(row for row in gaps if "dev_main" in row["trial_ids"] and row["reason"] == settlement_reason)
        assert dev_gap["intervals"]["missing_fields"] == ["d", "pay_date"]
    script = runpy.run_path(str(Path(__file__).parents[1] / "research/run_validation.py"))
    with pytest.raises(ValueError, match=signal_reason if pre_entry else settlement_reason):
        script["run_validation"](tmp_path / "protocol.json", root, tmp_path / "execution",
                                 phase="final", trial_id="final_main", payload=inputs)
    assert not (root / "research").exists()
    assert not (root / "backtests").exists()


@pytest.mark.parametrize("with_hole", [False, True])
def test_account_action_query_preserves_proved_intervals(tmp_path, qualification_case, with_hole):
    from hetu_quant.data import DataStore, SnapshotRef
    from hetu_quant.data_research import qualify_frozen_trials

    inputs, root, code, requirements, scope = qualification_case
    next(row for row in scope["coverage"] if row["domain"] == "daily_bars")["data_gaps"] = []
    (tmp_path / "coverage.json").write_text(json.dumps(scope))
    ref = SnapshotRef(inputs["definition"]["snapshot_id"], root)
    store = DataStore(root)
    manifest = store.load_manifest(ref)
    first, last = "2016-10-01", "2026-10-13"
    spans = (
        [dict(basis="ex_date", start=first, end="2024-07-02"),
         dict(basis="pay_date", start="2024-07-03", end="2024-07-03"),
         dict(basis="ex_date", start="2024-07-04", end=last)]
        if with_hole else [dict(basis="ex_date", start=first, end=last)]
    )
    action = next(row for row in manifest["table_files"]
                  if row["table"] == "corporate_actions" and row["partition"] == f"code={code}")
    action["query_coverage"] = spans
    (root / "snapshots" / ref.snapshot_id / "manifest.json").write_text(json.dumps(manifest))

    audit = audit_research_coverage(requirements, store, ref)
    actions = next(row for row in audit["coverage"] if row["domain"] == "corporate_actions")
    expected = [[first, "2024-07-02"], ["2024-07-04", last]] if with_hole else [[first, last]]
    assert actions["covered"] == expected
    expected_gaps = [["2016-04-29", "2016-09-30"]]
    if with_hole:
        expected_gaps.append(["2024-07-03", "2024-07-03"])
    assert actions["unknown_ranges"] == expected_gaps

    result = qualify_frozen_trials(inputs, root, tmp_path, [])
    trials = {row["trial_id"]: row for row in result["trials"]}
    expected_blocked = {"dev_neighbor_42"}
    if with_hole:
        expected_blocked |= {trial_id for trial_id in trials if trial_id.startswith("final_")}
    assert {trial_id for trial_id, trial in trials.items() if trial["input_status"] == "blocked"} == expected_blocked
    assert "dev_neighbor_42" in trials["final_benchmark"]["pending_dependencies"]
    if not with_hole:
        assert trials["final_benchmark"]["next_action"] == "await_dependencies"


@pytest.mark.parametrize(("domain", "key", "day"), [
    ("daily_bars", "data_gaps", "2019-11-01"),
    ("corporate_actions", "unknown_ranges", "2019-11-06"),
])
def test_each_account_interval_gap_blocks_trial(tmp_path, qualification_case, domain, key, day):
    from hetu_quant.data_research import qualify_frozen_trials

    inputs, root, code, _, scope = qualification_case
    next(row for row in scope["coverage"] if row["domain"] == domain)[key] = [[day, day]]
    (tmp_path / "coverage.json").write_text(json.dumps(scope))
    result = qualify_frozen_trials(inputs, root, tmp_path, [])
    main = next(row for row in result["trials"] if row["trial_id"] == "dev_main")
    assert main["input_status"] == "blocked"
    assert any(r["code"] == code and r["field"] == domain and ("2019-10", 36, "2019-11-01") in r["windows"]
               for r in (result["field_evidence"][idx] for idx in main["evidence_ids"]))


def test_confirmed_suspension_in_account_is_not_missing_price(tmp_path, qualification_case):
    from hetu_quant.data_research import qualify_frozen_trials

    inputs, root, _, _, scope = qualification_case
    bars = next(row for row in scope["coverage"] if row["domain"] == "daily_bars")
    bars["covered"] = [["2016-04-01", "2019-11-01"], ["2019-11-05", "2026-10-13"]]
    bars["suspended"] = [["2019-11-04", "2019-11-04"]]
    (tmp_path / "coverage.json").write_text(json.dumps(scope))
    result = qualify_frozen_trials(inputs, root, tmp_path, [])
    assert next(row for row in result["trials"] if row["trial_id"] == "dev_main")["input_status"] == "qualified"


@pytest.mark.parametrize("mismatch", ["empty_snapshot", "wrong_use"])
def test_snapshot_permission_must_cover_account_use(tmp_path, qualification_case, mismatch):
    from hetu_quant.data_research import qualify_frozen_trials

    inputs, root, _, _, _ = qualification_case
    snapshot_id = inputs["definition"]["snapshot_id"]
    if mismatch == "empty_snapshot":
        (root / "snapshots" / snapshot_id / "manifest.json").write_text(json.dumps({"snapshot_id": snapshot_id}))
    else:
        path = root / "data-products.json"
        products = json.loads(path.read_text())
        bars = next(p for p in products["products"].values() if "daily_bars" in p["tables"])
        bars["permission"]["allowed_uses"] = ["daily_archive"]
        path.write_text(json.dumps(products))
    result = qualify_frozen_trials(inputs, root, tmp_path, [])
    main = next(row for row in result["trials"] if row["trial_id"] == "dev_main")
    assert main["input_status"] == "blocked"
    assert any(result["field_evidence"][idx]["field"] == "permission" for idx in main["evidence_ids"])


def test_old_qualification_only_reuses_row_facts_proven_at_close(tmp_path, qualification_case):
    from hetu_quant.data_research import qualify_frozen_trials

    inputs, root, _, requirements, _ = qualification_case
    case = make_backtest_case("history")
    caps = pd.read_csv(io.StringIO(case["csvs"]["market_cap"]), dtype=str)
    old_days = requirements["signal_dates"][:2]
    caps = caps.iloc[:2].copy()
    caps["date"] = old_days
    caps["available_at"] = [old_days[0] + "T14:55:00+08:00", old_days[1] + "T18:00:00+08:00"]
    case["csvs"]["market_cap"] = caps.to_csv(index=False)
    store, ref = build_case_store(root / "old", case)
    (store.root / "data-products.json").write_bytes((root / "old" / "products.json").read_bytes())
    # The old 21:00 qualification lists both dates. Their raw publications differ.
    bundle = root / "old" / "bundle"
    bundle.mkdir()
    pd.DataFrame({"code": ["KEEP_A", "KEEP_A"], "signal_date": old_days}).to_parquet(bundle / "universe.parquet")
    definition = dict(inputs["definition"], snapshot_id=ref.snapshot_id)
    (bundle / "definition.json").write_text(json.dumps(definition))
    (bundle / "qualification.json").write_text(json.dumps({
        "can_run": True, "purpose": "backtest_low_volatility", "snapshot_id": ref.snapshot_id,
        "allowed_uses": ["backtest_low_volatility"]
    }))
    report = root / "old" / "production-qualification.json"
    report.write_text(json.dumps({
        "root": str(store.root), "snapshot_id": ref.snapshot_id,
        "results": [{"bundle": str(bundle), "definition": definition}]
    }))
    (tmp_path / "qualification-reuse.json").write_text(json.dumps({"refs": [str(report)]}))
    result = qualify_frozen_trials(inputs, root, tmp_path, [])
    rows = {r["date"]: r for r in result["reused_signal_evidence"] if r["code"] == "KEEP_A"}
    assert rows[old_days[0]]["market_cap_cny"] == 1000000
    assert rows[old_days[1]]["market_cap_cny"] is None
    assert rows[old_days[0]]["source_ref"] == str(report)


def test_plan_only_delivers_evidence_without_running(tmp_path, capsys, monkeypatch):
    from hetu_quant import backtest, backtest_data, strategies

    def unexpected(*args, **kwargs):
        pytest.fail("read-only preview executed production work")

    monkeypatch.setattr(backtest, "run_backtest", unexpected)
    monkeypatch.setattr(backtest_data, "prepare_backtest", unexpected)
    monkeypatch.setattr(strategies, "build_signals", unexpected)
    entry = runpy.run_path(str(Path(__file__).parents[1] / "research/run_validation.py"))
    path = tmp_path / "protocol.json"
    path.write_text(json.dumps(protocol()))
    assert entry["main"](["--plan-only", "--protocol", str(path),
                          "--data-root", str(tmp_path), "--out", str(tmp_path)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["remaining_budget"] == 8
    assert "signal_qualification" in output
    assert len(output["signal_qualification"]["trials"]) == 8
    assert all(row["input_status"] == "blocked" for row in output["signal_qualification"]["trials"])
    assert not (tmp_path / "research").exists()


def test_missing_independent_historical_target_cannot_be_full_scope_ready(tmp_path):
    case = make_backtest_case("coverage")
    hist = targets(case)
    extra = hist.iloc[[0]].copy()
    extra["identity_id"], extra["code"] = "missing-history-object", "MISSING.SH"
    hist = pd.concat([hist, extra], ignore_index=True)
    hist.attrs["target_scope"] = {"complete": True, "source_ref": "synthetic-independent-roster"}
    store, ref = build_case_store(tmp_path, case)
    req = research_data_requirements(protocol(), calendar(), hist)
    result = audit_research_coverage(req, store, ref)
    assert not result["full_scope_ready"]
    assert any(r["code"] == "MISSING.SH" and r["data_gaps"] for r in result["coverage"])
    assert "MISSING.SH" in [o["code"] for o in req["objects"]]


def test_research_lifecycle_exclusion_does_not_hide_unknown_scope():
    hist = targets(make_backtest_case("coverage"))
    hist.loc[0, "valid_to"] = "2017-02-14"
    hist.loc[1, "lifecycle_status"] = "conflict"
    hist.loc[1, "valid_to"] = "2017-02-14"
    req = research_data_requirements(protocol(), calendar(), hist)
    assert any(r["code"] == "SYN_A.SH" and r["reason"] == "pre_study_exit" for r in req["legal_exclusions"])
    assert not any(r["code"] == "SYN_EXIT.SZ" for r in req["legal_exclusions"])
    assert any(r["code"] == "SYN_EXIT.SZ" for r in req["unknown_ranges"])


def test_archive_has_own_gates_and_lowvol_financials_remain_optional(tmp_path):
    case = make_backtest_case("coverage")
    store, ref = build_case_store(tmp_path, case)
    cal = pd.read_csv(io.StringIO(case["csvs"]["calendar"]))
    assert (
        audit_archive(store, ref, archive_requirements(targets(case), cal, date(2024, 1, 4), years=1))["status"]
        == "complete"
    )
    req = research_data_requirements(protocol(), calendar(), targets(case))
    report = audit_research_coverage(req, store, ref)
    assert not report["full_scope_ready"]
    assert "financial_reports" not in [r["domain"] for r in report["coverage"]]
    assert "industry_exposure" in report["optional_limitations"]
    ep = protocol()
    ep["definition"]["strategy_id"] = "ep_value"
    conditional = audit_research_coverage(research_data_requirements(ep, calendar(), targets(case)), store, ref)
    assert any(r["domain"] == "financial_reports" for r in conditional["event_limitations"])


def test_reuse_delivery_never_runs_qualification_or_signal(tmp_path, monkeypatch):
    case = make_backtest_case("coverage")
    store, ref = build_case_store(tmp_path, case)
    history = tmp_path / "targets.csv"
    targets(case).to_csv(history, index=False)
    payload = protocol()
    payload.update(
        target_history_ref=str(history),
        target_scope={"complete": False, "source_ref": "synthetic-independent-roster"},
        qualification_mode="reuse",
    )
    monkeypatch.setattr(store, "prepare", lambda *_: (_ for _ in ()).throw(AssertionError("network")))
    import hetu_quant.backtest as backtest
    import hetu_quant.backtest_data as consumer
    import hetu_quant.strategies as strategies

    monkeypatch.setattr(strategies, "build_signals", lambda *_: (_ for _ in ()).throw(AssertionError("signals")))
    monkeypatch.setattr(backtest, "run_backtest", lambda *_: (_ for _ in ()).throw(AssertionError("engine")))
    monkeypatch.setattr(
        consumer, "prepare_backtest", lambda *_: (_ for _ in ()).throw(AssertionError("old qualification rerun"))
    )
    result = prepare_research_delivery(payload, store, ref, tmp_path / "delivery")
    assert result["handoff"]["new_qualifications"] == 0
    assert result["handoff"]["research_runs"] == 0
    assert not result["coverage"]["full_scope_ready"]
    assert (tmp_path / "delivery/coverage.json").is_file()


def test_future_revision_does_not_rewrite_earlier_audited_evidence(tmp_path):
    case = make_backtest_case("history")
    original = {**case, "csvs": dict(case["csvs"])}
    for table in ("historical_securities", "knowledge_evidence"):
        frame = pd.read_csv(io.StringIO(original["csvs"][table]), dtype=str).fillna("")
        original["csvs"][table] = frame[~frame.evidence_ref.eq("ev-hist-del-rev2")].to_csv(index=False)
    store1, ref1 = build_case_store(tmp_path / "before", original)
    store2, ref2 = build_case_store(tmp_path / "after", case)
    history = pd.DataFrame(
        [
            dict(
                code="DELISTED_LATER",
                identity_id="delisted-entity",
                market="SZ",
                security_type="stock",
                valid_from="2019-06-03",
                valid_to="",
                lifecycle_status="known",
                source_ref="synthetic-roster",
            )
        ]
    )
    req = research_data_requirements(protocol(), calendar(), history)
    before = audit_research_coverage(req, store1, ref1)
    after = audit_research_coverage(req, store2, ref2)

    def selected(report):
        return next(r for r in report["signal_evidence"] if r["date"] == "2021-06-30")

    assert selected(before)["history_revision"] == selected(after)["history_revision"] == ["orig"]


def test_prepare_delivery_calls_production_qualification_once_per_distinct_need(tmp_path, monkeypatch):
    case = make_backtest_case("history")
    store, ref = build_case_store(tmp_path / "facts", case)
    import hetu_quant.backtest_data as consumer

    actual = consumer.prepare_backtest
    calls = []

    def inspect(definition, actual_store, bundle):
        calls.append((definition.start, definition.end, definition.protocol["warmup_months"]))
        return actual(definition, actual_store, bundle)

    monkeypatch.setattr(consumer, "prepare_backtest", inspect)
    monkeypatch.setattr(store, "prepare", lambda *_: (_ for _ in ()).throw(AssertionError("network")))
    payload = protocol()
    payload["qualification_mode"] = "prepare"
    result = prepare_research_delivery(payload, store, ref, tmp_path / "delivery")
    assert len(calls) == len(set(calls)) == result["handoff"]["new_qualifications"] == 4
    assert len(result["qualifications"]) == 8
    assert result["handoff"]["research_runs"] == 0


def test_unproven_later_observation_does_not_hide_legacy_known_capital(tmp_path):
    from hetu_quant.data_archive import scan_local_coverage

    case = make_backtest_case("history")
    store, ref = build_case_store(tmp_path / "original", case)
    late = {**case, "csvs": dict(case["csvs"])}
    caps = pd.read_csv(io.StringIO(case["csvs"]["market_cap"]), dtype=str)
    caps = caps[caps.code.eq("DELISTED_LATER") & caps.date.eq("2021-06-30")].copy()
    caps["available_at"], caps["revision_id"] = "2026-10-25T09:00:00+08:00", "unproven-observation"
    late["csvs"]["market_cap"] = caps.to_csv(index=False)
    late_store, late_ref = build_case_store(tmp_path / "late", late, tables=("market_cap",))
    facts = scan_local_coverage(
        store,
        ref,
        asset_snapshots=[
            dict(
                root=str(late_store.root),
                config_path=str(tmp_path / "late/products.json"),
                snapshot_id=late_ref.snapshot_id,
                tables=["market_cap"],
            )
        ],
    )
    hist = pd.DataFrame(
        [
            dict(
                code="DELISTED_LATER",
                identity_id="synthetic-entity",
                market="SZ",
                security_type="stock",
                valid_from="2019-06-03",
                valid_to="",
                lifecycle_status="known",
                source_ref="synthetic-roster",
            )
        ]
    )
    result = audit_research_coverage(
        research_data_requirements(protocol(), calendar(), hist), store, ref, inventory=facts
    )
    row = next(r for r in result["signal_evidence"] if r["date"] == "2021-06-30")
    assert row["market_cap_known"]


def test_action_requirement_starts_at_first_return_anchor_not_earlier_month_days(tmp_path, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from backtest_cases import build_prepared_case_store

    case = make_backtest_case("coverage")
    case["codes"] = ("SYN_A.SH",)
    case["obtained_at"] = "2026-10-25T09:00:00+08:00"
    case["definition"] = {"as_of": datetime(2026, 10, 25, 21, tzinfo=ZoneInfo("Asia/Shanghai"))}
    case["actions_coverage"] = {"start": "2016-04-29", "end": "2026-10-13"}
    case["csvs"]["calendar"] = calendar().to_csv(index=False)
    store, ref = build_prepared_case_store(tmp_path, case, monkeypatch)
    history = targets(case).iloc[[0]].copy()
    req = research_data_requirements(protocol(), calendar(), history)
    result = audit_research_coverage(req, store, ref)
    assert not any(
        r.get("code") == "SYN_A.SH" and r["reason"] == "operate_ex_date_coverage_unproved"
        for r in result["event_limitations"]
    )
    assert req["start"] == "2016-04-01"  # 原日线窗口完整保留。


def test_warmup_event_does_not_require_account_payment_dates_before_execution(tmp_path):
    case = make_backtest_case("coverage")
    case["table_products"]["corporate_actions"] = ("synthetic_actions", "event")
    case["csvs"]["corporate_actions"] = (
        "code,ex_date,action_type,m,d,record_date,pay_date,stock_listing_date,terms_raw,available_at\n"
        "SYN_A.SH,2018-05-15,cash_dividend,1,0.5,,,,synthetic-warmup,2018-05-01\n"
    )
    store, ref = build_case_store(tmp_path, case)
    req = research_data_requirements(protocol(), calendar(), targets(case))
    result = audit_research_coverage(req, store, ref)
    assert not any(
        r.get("date") == "2018-05-15" and r["reason"] == "unsupported_or_missing_event_fields"
        for r in result["event_limitations"]
    )


def test_rule_period_gap_is_not_hidden_by_table_presence(tmp_path):
    case = make_backtest_case("coverage")
    case["table_products"]["trading_rules"] = ("synthetic_rules", "catalog")
    case["csvs"]["trading_rules"] = (
        "rule_id,market,board,security_type,valid_from,valid_to,min_buy_qty,qty_step,tick_size,settlement,limit_rule,fee_rule\n"
        "synthetic-rule,SH,main,stock,2019-11-01,2023-12-29,100,100,0.01,T+1,pct10,synthetic-fee\n"
    )
    store, ref = build_case_store(tmp_path, case)
    history = targets(case)
    history["board"] = "main"
    result = audit_research_coverage(research_data_requirements(protocol(), calendar(), history), store, ref)
    rule = next(r for r in result["coverage"] if r["domain"] == "trading_rules" and r["code"] == "SYN_A.SH")
    assert rule["unknown_ranges"] == [["2024-01-01", "2026-10-13"]]
    assert rule["observed_sessions"] > 0
    assert rule["source_refs"]
    assert not result["full_scope_ready"]


@pytest.mark.parametrize("field,value", [("security_type", "unknown"), ("market", "unknown"), ("market", "")])
def test_research_unknown_security_attributes_are_not_legal_exclusions(tmp_path, field, value):
    case = make_backtest_case("coverage")
    securities = pd.read_csv(io.StringIO(case["csvs"]["securities"]), dtype=str).fillna("")
    securities.loc[securities.code.eq("SYN_A.SH"), field] = value
    case["csvs"]["securities"] = securities.to_csv(index=False)
    store, ref = build_case_store(tmp_path, case)
    history = targets(case)
    history.loc[history.code.eq("SYN_A.SH"), field] = value
    req = research_data_requirements(protocol(), calendar(), history)
    report = audit_research_coverage(req, store, ref)
    assert not report["full_scope_ready"]
    assert not next(o for o in req["objects"] if o["code"] == "SYN_A.SH").get("excluded")
    assert not any(r["code"] == "SYN_A.SH" for r in req["legal_exclusions"])
    assert any(r["code"] == "SYN_A.SH" and r["reason"] == "security_attributes_unproved" for r in req["unknown_ranges"])


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("security_type", "cdr", "not_common_stock"),
        ("market", "BJ", "outside_research_market"),
    ],
)
def test_research_proven_outside_attributes_remain_excluded(field, value, reason):
    history = targets(make_backtest_case("coverage"))
    history.loc[history.code.eq("SYN_A.SH"), field] = value
    req = research_data_requirements(protocol(), calendar(), history)
    assert any(r["code"] == "SYN_A.SH" and r["reason"] == reason for r in req["legal_exclusions"])


@pytest.mark.parametrize("unproved_identity", [False, True])
def test_factor_conflict_limits_archive_without_adding_research_gate(tmp_path, unproved_identity):
    from hetu_quant.data_archive import scan_local_coverage

    case = make_backtest_case("coverage")
    store, ref = build_case_store(tmp_path / "original", case)
    history = targets(case)
    if unproved_identity:
        history.loc[history.code.eq("SYN_A.SH"), "lifecycle_status"] = "conflict"
    req = research_data_requirements(protocol(), calendar(), history)
    before = audit_research_coverage(req, store, ref)
    extra = {**case, "csvs": dict(case["csvs"])}
    factors = pd.read_csv(io.StringIO(case["csvs"]["adjustment_factors"]))
    factors = factors[factors.code.eq("SYN_A.SH") & factors.date.eq("2024-01-02")].copy()
    factors["adj_factor"] = 2
    extra["csvs"]["adjustment_factors"] = factors.to_csv(index=False)
    other, other_ref = build_case_store(tmp_path / "conflict", extra, tables=("adjustment_factors",))
    facts = scan_local_coverage(
        store,
        ref,
        asset_snapshots=[
            dict(
                root=str(other.root),
                config_path=str(tmp_path / "conflict/products.json"),
                snapshot_id=other_ref.snapshot_id,
                tables=["adjustment_factors"],
            )
        ],
    )
    archive = audit_archive(
        store,
        ref,
        archive_requirements(
            history,
            pd.read_csv(io.StringIO(case["csvs"]["calendar"])),
            date(2024, 1, 4),
            years=1,
        ),
        inventory=facts,
    )
    after = audit_research_coverage(req, store, ref, inventory=facts)
    assert archive["status"] == "limited"
    assert any(r["domain"] == "adjustment_factors" and r["conflicting_sessions"] == 1 for r in archive["coverage"])
    assert not any(r.get("domain") == "adjustment_factors" for r in after["unknown_ranges"])
    assert before["full_scope_ready"] == after["full_scope_ready"]
    assert before["unknown_ranges"] == after["unknown_ranges"]
    assert before["event_limitations"] == after["event_limitations"]
    assert any(r.get("domain") == "calendar" for r in after["unknown_ranges"])
    if unproved_identity:
        assert any(r.get("reason") == "identity_or_lifecycle_unproved" for r in after["unknown_ranges"])

    def required_coverage(report):
        return [{k: v for k, v in r.items() if k != "source_refs"} for r in report["coverage"]]

    assert required_coverage(before) == required_coverage(after)
