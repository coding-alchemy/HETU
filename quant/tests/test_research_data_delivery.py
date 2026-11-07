"""冻结需求及独立目标缺失反例；纯数据准备不执行信号和研究。"""

import io
import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from backtest_cases import build_case_store, make_backtest_case
from hetu_quant.data_archive import archive_requirements, audit_archive
from hetu_quant.data_research import audit_research_coverage, prepare_research_delivery, research_data_requirements
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
