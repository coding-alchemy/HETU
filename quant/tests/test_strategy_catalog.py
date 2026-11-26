import json
import runpy
import zipfile
from pathlib import Path

import pandas as pd
import pytest
from hetu_quant import backtest_reporting
from hetu_quant.strategy_catalog import import_strategy_evidence, list_strategy_assets, read_strategy_asset


def test_strategy_import_preserves_trials_without_execution(tmp_path):
    source = tmp_path / "evidence"
    source.mkdir()
    root = tmp_path / "store"
    strategy_id = "synthetic_case"
    attachment = source / "strategy.py"
    marker = tmp_path / "unexpected.txt"
    attachment.write_text(f"open({str(marker)!r}, 'w').write('executed')")
    trials = [{"trial_id": "one", "status": state} for state in ("complete", "failed", "blocked", "refused")]
    (source / "manifest.json").write_text(json.dumps({
        "strategy_id": strategy_id, "name": "Synthetic", "markets": ["CN_A"], "hypothesis": "test",
        "frequency": "monthly", "code_refs": [attachment.name], "trials": trials,
        "statuses": {
            "research_status": "complete", "research_conclusion": "limited", "execution_adapter": "registered",
        },
    }))
    import_strategy_evidence(root, source / "manifest.json")
    assert not marker.exists()
    matches = list_strategy_assets(
        root, market="CN_A", hypothesis="test", frequency="monthly", research_status="complete",
    )
    assert [row["strategy_id"] for row in matches] == [strategy_id]
    item = read_strategy_asset(root, strategy_id)
    assert item["trials"] == trials
    assert item["statuses"]["research_conclusion"] == "limited"
    assert item["statuses"]["execution_adapter"] == "collected"
    assert item["statuses"]["economic_status"] == "unknown"
    assert "license" in item["missing"]
    assert (root / "strategy-assets" / strategy_id / attachment.name).read_bytes() == attachment.read_bytes()
    assert not (root / "catalog.sqlite").exists()


def _write_phase2_trials(root, trials):
    ledger = root / "research" / "phase2-lowvol" / "trials.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text("".join(f"{json.dumps(row)}\n" for row in trials), encoding="utf-8")


def _phase2_cli_definition(trial_id):
    research = Path(__file__).parents[1] / "research"
    protocol = json.loads((research / "phase2-protocol.json").read_bytes())
    definitions = runpy.run_path(str(research / "run_validation.py"))["validation_definitions"](protocol)
    definition = next(row["definition"] for row in definitions if row["trial_id"] == trial_id)
    definition.update(snapshot_id="synthetic-admitted-input", as_of="2026-10-18T15:00:00+08:00")
    definition["protocol"]["rules_ref"] = "synthetic-snapshot-rule"
    definition["protocol"]["costs"]["statutory_rules_ref"] = "synthetic-snapshot-rule"
    return definition


def test_strategy_filter_uses_blocked_ledger_without_inferring_success(tmp_path):
    # 合成台账沿用调度器字段，失败及额度拒绝均无可读的发布评价。
    trials = [
        {"trial_id": "dev_main", "phase": "development_validation", "run_id": None,
         "kind": "candidate", "status": "failed", "economic_status": "not_evaluated",
         "recorded_at": "2026-10-18T15:01:00+08:00", "detail": "RuntimeError: synthetic"},
        {"trial_id": "dev_main", "phase": "development_validation", "run_id": "bt-candidate",
         "kind": "candidate", "status": "blocked", "economic_status": "not_evaluated",
         "recorded_at": "2026-10-18T15:02:00+08:00"},
        {"trial_id": "dev_benchmark", "phase": "development_validation", "run_id": "bt-benchmark",
         "kind": "benchmark", "status": "complete", "economic_status": "not_evaluated",
         "recorded_at": "2026-10-18T15:03:00+08:00"},
        {"run_id": "refused-4", "status": "refused_budget",
         "recorded_at": "2026-10-18T15:04:00+08:00", "needed_runs": 2},
    ]
    _write_phase2_trials(tmp_path, trials)

    matches = list_strategy_assets(tmp_path, research_status="blocked")
    assert [row["strategy_id"] for row in matches] == ["low_volatility"]
    assert matches[0]["trials"] == trials
    assert matches[0]["statuses"]["economic_status"] == "not_evaluated"
    assert matches[0]["statuses"]["research_conclusion"] == "unknown"
    benchmark = read_strategy_asset(tmp_path, "universe_vw")["statuses"]
    assert benchmark["execution_status"] == benchmark["research_status"] == "unknown"
    assert benchmark["research_conclusion"] == "unknown"


def test_strategy_reads_published_evaluation_only_for_its_own_trial(tmp_path):
    # 两个共享研究的合成发布清单，分别保留自己的原始评价。
    trials = []
    for kind, trial_id, status, evaluation in (
        ("candidate", "dev_main", "limited", {
            "execution_status": "complete", "research_status": "blocked",
            "research_conclusion": "not_evaluated", "economic_status": "not_evaluated",
            "reasons": ["synthetic qualification gap"],
        }),
        ("benchmark", "dev_benchmark", "complete", {
            "execution_status": "complete", "research_status": "evaluated",
            "research_conclusion": "diagnostic_only", "economic_status": "not_evaluated",
        }),
    ):
        run_id = f"bt-{kind}"
        published = tmp_path / "backtests" / run_id
        published.mkdir(parents=True)
        (published / "backtest.json").write_text(json.dumps({
            "run_id": run_id, "status": status, "definition": _phase2_cli_definition(trial_id),
            "research": {"study_id": "phase2-lowvol", "trial_id": trial_id, "phase": "development_validation"},
        }), encoding="utf-8")
        (published / "evaluation.json").write_text(json.dumps(evaluation), encoding="utf-8")
        trials.append({
            "trial_id": trial_id, "phase": "development_validation", "run_id": run_id, "kind": kind,
            "status": "blocked" if status == "limited" else status,
            "economic_status": "not_evaluated", "recorded_at": "2026-10-18T15:01:00+08:00",
        })
    _write_phase2_trials(tmp_path, trials)

    candidate = read_strategy_asset(tmp_path, "low_volatility")
    assert candidate["statuses"] == {
        "evidence_import": "catalogued", "execution_adapter": "registered",
        "execution_status": "complete", "research_status": "blocked",
        "research_conclusion": "not_evaluated", "economic_status": "not_evaluated",
    }
    assert candidate["trials"][0]["evaluation"]["reasons"] == ["synthetic qualification gap"]
    assert "evaluation" not in candidate["trials"][1]
    benchmark = read_strategy_asset(tmp_path, "universe_vw")
    assert benchmark["statuses"]["research_status"] == "evaluated"
    assert benchmark["statuses"]["research_conclusion"] == "diagnostic_only"
    assert "evaluation" not in benchmark["trials"][0]


def test_ep_preparation_is_discoverable_without_account_evidence(tmp_path):
    from hetu_quant.strategy_catalog import read_strategy_asset, strategy_text_attachments

    asset = read_strategy_asset(tmp_path, "ep_value")
    assert "ep_value.py" in strategy_text_attachments(tmp_path, asset)
    assert asset["statuses"]["execution_adapter"] == "collected"
    assert asset["trials"] == []


@pytest.mark.parametrize("reader", ["web", "notebook"])
def test_study_release_joins_cli_trials_without_recalculation(tmp_path, monkeypatch, reader):
    study = tmp_path / "research/phase2-lowvol"
    study.mkdir(parents=True)
    rows = [{"trial_id": "dev_main", "run_id": "cli", "status": "complete"},
            {"trial_id": "final_main", "run_id": None, "status": "failed", "detail": "timeout"}]
    ledger = study / "trials.jsonl"
    ledger.write_text("".join(json.dumps(row) + "\n" for row in rows))
    (study / "study-report.md").write_text("研究原结论")
    run = tmp_path / "backtests/cli"
    (run / "bundle").mkdir(parents=True)
    (run / "backtest.json").write_text(json.dumps({
        "run_id": "cli", "status": "complete",
        "definition": _phase2_cli_definition("dev_main"),
    }))
    original = pd.DataFrame({"signal_value": [0.17], "target_weight": [0.6]})
    original.to_parquet(run / "bundle/signals.parquet")
    nav = [{"date": "2020-01-02", "net_nav": 995.0}]
    costs = [{"date": "2020-01-02", "cost_type": "commission", "amount": 5.0}]
    pd.DataFrame(nav).to_parquet(run / "nav.parquet")
    pd.DataFrame(costs).to_parquet(run / "costs.parquet")
    (run / "evaluation.json").write_text(json.dumps({"economic_status": "not_met"}))

    publish = getattr(backtest_reporting, "publish_research_handoff", None)
    assert publish is not None
    path = publish(tmp_path, "phase2-lowvol", "first")
    first = backtest_reporting.read_research_handoff(path)
    assert first["trials"] == rows
    assert first["report"] == "研究原结论"
    assert first["runs"]["cli"]["evaluation"]["economic_status"] == "not_met"
    with zipfile.ZipFile(path) as archive:
        assert archive.read("runs/cli/bundle/signals.parquet") == (run / "bundle/signals.parquet").read_bytes()
        assert "attachments/low_volatility/low_volatility.py" in archive.namelist()
    with ledger.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"trial_id": "dev_neighbor_30", "status": "failed"}) + "\n")
    assert backtest_reporting.read_research_handoff(path)["trials"] == rows

    if reader == "web":
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_string(
            "from pathlib import Path\nfrom hetu_quant.ui.strategy_page import render_strategy_page\n"
            f"render_strategy_page(Path({str(tmp_path)!r}), None)"
        ).run()
        app.selectbox(key="strategy_asset").set_value("low_volatility").run()
        release = next(box for box in app.selectbox if box.label == "研究发布物")
        release.set_value(path).run()
        assert not app.exception
        displayed = [json.loads(shown.value) for shown in app.json]
        assert first in displayed
        assert rows in displayed
        assert "dev_neighbor_30" not in json.dumps(displayed)
        return

    notebook = Path(__file__).parents[1] / "notebooks/read_result.ipynb"
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    monkeypatch.setenv("HETU_QUANT_RESEARCH_RELEASE", str(path))
    monkeypatch.setenv("HETU_QUANT_ROOT", str(empty_root))
    monkeypatch.chdir(empty_root)
    namespace = {}
    for cell in json.loads(notebook.read_text())["cells"]:
        if cell["cell_type"] == "code":
            exec(compile("".join(cell["source"]), str(notebook), "exec"), namespace)
    assert namespace["research_payload"] == first
    assert namespace["research_payload"]["runs"]["cli"]["nav"] == nav
    assert namespace["research_payload"]["runs"]["cli"]["costs"] == costs
    assert not list(empty_root.iterdir())


@pytest.mark.parametrize("identity", [{}, {"kind": "candidate"}, {"strategy_id": "universe_vw"}])
def test_study_release_rejects_candidate_pointing_at_legacy_benchmark(tmp_path, identity):
    rows = [{"trial_id": "dev_main", "run_id": "wrong-account", "status": "complete", **identity}]
    _write_phase2_trials(tmp_path, rows)
    run = tmp_path / "backtests/wrong-account"
    run.mkdir(parents=True)
    (run / "backtest.json").write_text(json.dumps({
        "run_id": "wrong-account", "status": "complete",
        "definition": {"strategy_id": "universe_vw"},
    }))
    with pytest.raises(ValueError, match="dev_main.*wrong-account"):
        backtest_reporting.publish_research_handoff(tmp_path, "phase2-lowvol", "wrong")
    assert not list((tmp_path / "research/phase2-lowvol/releases").glob("*.zip"))


def test_study_release_includes_failed_candidate_attachments(tmp_path):
    rows = [{"trial_id": "dev_benchmark", "run_id": "benchmark", "status": "complete"},
            {"trial_id": "dev_main", "run_id": None, "status": "failed", "detail": "timeout"}]
    _write_phase2_trials(tmp_path, rows)
    run = tmp_path / "backtests/benchmark"
    run.mkdir(parents=True)
    (run / "backtest.json").write_text(json.dumps({
        "run_id": "benchmark", "status": "complete",
        "definition": _phase2_cli_definition("dev_benchmark"),
    }))
    path = backtest_reporting.publish_research_handoff(tmp_path, "phase2-lowvol", "one")
    with zipfile.ZipFile(path) as archive:
        assert "attachments/low_volatility/low-volatility.md" in archive.namelist()
        assert "attachments/low_volatility/low_volatility.py" in archive.namelist()
        assert "attachments/universe_vw/phase2-protocol.json" in archive.namelist()
    assert backtest_reporting.read_research_handoff(path)["trials"] == rows


@pytest.mark.parametrize("claimed,actual,field", [
    ("dev_main", "dev_neighbor_30", "warmup_months"),
    ("dev_main", "final_main", "start"),
    ("final_main", "final_double_costs", "commission_rate"),
    ("final_main", "final_capacity", "participation_rate"),
])
def test_study_release_requires_exact_frozen_behavior_for_legacy_cli(tmp_path, claimed, actual, field):
    run_id = "legacy-cli"
    _write_phase2_trials(tmp_path, [{"trial_id": claimed, "run_id": run_id, "status": "complete"}])
    run = tmp_path / "backtests" / run_id
    run.mkdir(parents=True)
    manifest = run / "backtest.json"
    manifest.write_text(json.dumps({
        "run_id": run_id, "status": "complete", "definition": _phase2_cli_definition(actual),
    }))
    with pytest.raises(ValueError, match=f"{claimed}.*{run_id}.*{field}"):
        backtest_reporting.publish_research_handoff(tmp_path, "phase2-lowvol", "wrong")
    releases = tmp_path / "research/phase2-lowvol/releases"
    assert not list(releases.glob("*.zip"))

    ledger = releases.parent / "trials.jsonl"
    ledger.write_text(json.dumps({"trial_id": actual, "run_id": run_id, "status": "complete"}) + "\n")
    path = backtest_reporting.publish_research_handoff(tmp_path, "phase2-lowvol", "matching")
    assert backtest_reporting.read_research_handoff(path)["manifest"]["runs"][run_id]["trial_id"] == actual


def test_study_release_rejects_final_cli_account_used_for_tuning(tmp_path):
    study = tmp_path / "research/phase2-lowvol"
    _write_phase2_trials(tmp_path, [{"trial_id": "final_main", "run_id": "tuned", "status": "complete"}])
    evaluation = {"execution_status": "complete", "research_status": "evaluated", "economic_status": "met"}
    definition = _phase2_cli_definition("final_main")
    definition["protocol"]["viewing_record"]["final_seen"] = True
    for run_id, tuned in (("tuned", True), ("untuned", False)):
        run = tmp_path / "backtests" / run_id
        run.mkdir(parents=True)
        definition["protocol"]["viewing_record"]["final_used_for_tuning"] = tuned
        (run / "backtest.json").write_text(json.dumps({
            "run_id": run_id, "status": "complete", "definition": definition,
        }))
        (run / "evaluation.json").write_text(json.dumps(evaluation))

    with pytest.raises(ValueError, match=r"final_main.*tuned.*viewing_record\.final_used_for_tuning"):
        backtest_reporting.publish_research_handoff(tmp_path, "phase2-lowvol", "original")
    assert not list((study / "releases").glob("*.zip"))
    tuned_manifest = tmp_path / "backtests/tuned/backtest.json"
    assert json.loads(tuned_manifest.read_bytes())["definition"]["protocol"]["viewing_record"]["final_used_for_tuning"]
    assert json.loads((tuned_manifest.parent / "evaluation.json").read_bytes()) == evaluation

    (study / "trials.jsonl").write_text(json.dumps({
        "trial_id": "final_main", "run_id": "untuned", "status": "complete",
    }) + "\n")
    path = backtest_reporting.publish_research_handoff(tmp_path, "phase2-lowvol", "original")
    original = backtest_reporting.read_research_handoff(path)
    assert original["runs"]["untuned"]["evaluation"] == evaluation
    stored = json.loads(backtest_reporting.read_research_handoff_artifact(path, "runs/untuned/backtest.json"))
    assert stored["definition"]["protocol"]["viewing_record"] == {
        **definition["protocol"]["viewing_record"], "final_used_for_tuning": False,
    }


def test_selected_release_header_uses_original_evaluation_and_failed_trials(tmp_path):
    from streamlit.testing.v1 import AppTest

    root = tmp_path
    rows = [{"trial_id": "final_main", "run_id": "cli", "status": "complete"}]
    _write_phase2_trials(root, rows)
    run = root / "backtests/cli"
    run.mkdir(parents=True)
    (run / "backtest.json").write_text(json.dumps({
        "run_id": "cli", "status": "complete", "definition": _phase2_cli_definition("final_main"),
    }))
    evaluation = {"execution_status": "complete", "research_status": "evaluated",
                  "research_conclusion": "fail", "economic_status": "not_met"}
    (run / "evaluation.json").write_text(json.dumps(evaluation))
    original_release = backtest_reporting.publish_research_handoff(root, "phase2-lowvol", "original")
    ledger = root / "research/phase2-lowvol/trials.jsonl"
    failure = {"trial_id": "final_main", "run_id": None, "status": "failed",
               "economic_status": "not_evaluated", "detail": "archive fixture failure"}
    ledger.write_text(json.dumps(failure) + "\n")
    failed_release = backtest_reporting.publish_research_handoff(root, "phase2-lowvol", "failed")

    later_run = root / "backtests/later"
    later_run.mkdir()
    (later_run / "backtest.json").write_text(json.dumps({
        "run_id": "later", "status": "complete", "definition": _phase2_cli_definition("final_capacity"),
    }))
    (later_run / "evaluation.json").write_text(json.dumps({
        "execution_status": "complete", "research_status": "evaluated",
        "research_conclusion": "pass", "economic_status": "met",
    }))
    with ledger.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"trial_id": "final_capacity", "run_id": "later", "status": "complete"}) + "\n")

    app = AppTest.from_string(
        "from pathlib import Path\nfrom hetu_quant.ui.strategy_page import render_strategy_page\n"
        f"render_strategy_page(Path({str(root)!r}), None)"
    ).run()
    app.selectbox(key="strategy_asset").set_value("low_volatility").run()

    def header_status():
        assert not app.exception
        return json.loads(app.json[0].value)["statuses"]

    assert header_status()["economic_status"] == "met"
    next(box for box in app.selectbox if box.label == "研究发布物").set_value(original_release).run()
    assert header_status().items() >= evaluation.items()
    assert header_status()["trial_id"] == "final_main"
    next(box for box in app.selectbox if box.label == "研究发布物").set_value(failed_release).run()
    states = header_status()
    assert states["execution_status"] == "failed"
    assert states["economic_status"] == "not_evaluated"
    assert failure in backtest_reporting.read_research_handoff(failed_release)["trials"]


def test_selected_release_separates_final_main_diagnostics_and_latest_failure(tmp_path):
    from streamlit.testing.v1 import AppTest

    def publish_cli_account(trial_id, evaluation):
        run = tmp_path / "backtests" / trial_id
        run.mkdir(parents=True)
        (run / "backtest.json").write_text(json.dumps({
            "run_id": trial_id, "status": "complete", "definition": _phase2_cli_definition(trial_id),
        }))
        (run / "evaluation.json").write_text(json.dumps(evaluation))
        return {"trial_id": trial_id, "run_id": trial_id, "status": "complete"}

    diagnostic = {"execution_status": "complete", "research_status": "evaluated",
                  "research_conclusion": "diagnostic_only", "economic_status": "not_evaluated"}
    main_evaluation = {"execution_status": "complete", "research_status": "evaluated",
                       "research_conclusion": "pass", "economic_status": "met"}
    rows = [publish_cli_account("dev_benchmark", diagnostic), publish_cli_account("dev_main", diagnostic)]
    _write_phase2_trials(tmp_path, rows)
    dev_release = backtest_reporting.publish_research_handoff(tmp_path, "phase2-lowvol", "development")
    additional = [
        publish_cli_account("final_main", main_evaluation),
        publish_cli_account("final_double_costs", diagnostic),
        publish_cli_account("final_capacity", diagnostic),
        {"trial_id": "dev_neighbor_42", "run_id": None, "status": "failed", "detail": "source fixture failure"},
    ]
    ledger = tmp_path / "research/phase2-lowvol/trials.jsonl"
    with ledger.open("a", encoding="utf-8") as stream:
        for row in additional:
            stream.write(json.dumps(row) + "\n")
    final_release = backtest_reporting.publish_research_handoff(tmp_path, "phase2-lowvol", "final")

    app = AppTest.from_string(
        "from pathlib import Path\nfrom hetu_quant.ui.strategy_page import render_strategy_page\n"
        f"render_strategy_page(Path({str(tmp_path)!r}), None)"
    ).run()
    app.selectbox(key="strategy_asset").set_value("low_volatility").run()
    next(box for box in app.selectbox if box.label == "研究发布物").set_value(final_release).run()
    assert not app.exception
    header = json.loads(app.json[0].value)
    assert header["statuses"].items() >= main_evaluation.items()
    assert header["statuses"]["trial_id"] == "final_main"
    assert {entry["trial_id"] for entry in header["diagnostics"]} == {"final_double_costs", "final_capacity"}
    assert all(entry.items() >= diagnostic.items() for entry in header["diagnostics"])
    assert header["latest_failure"]["trial_id"] == "dev_neighbor_42"
    assert header["latest_failure"]["execution_status"] == "failed"
    assert header["latest_failure"]["detail"] == "source fixture failure"

    next(box for box in app.selectbox if box.label == "研究发布物").set_value(dev_release).run()
    assert not app.exception
    header = json.loads(app.json[0].value)
    assert header["statuses"]["trial_id"] == "final_main"
    assert header["statuses"]["status"] == "missing"
    assert header["statuses"]["economic_status"] == "not_evaluated"
    assert [entry["trial_id"] for entry in header["development_validation"]] == ["dev_benchmark", "dev_main"]
    assert all(entry.items() >= diagnostic.items() for entry in header["development_validation"])
    assert header["diagnostics"] == []
    assert header["latest_failure"] is None


@pytest.mark.parametrize("variant", ["complete", "missing_row", "nan", "missing_exposure", "missing_months"])
def test_published_pressure_and_market_use_separate_accounts(tmp_path, monkeypatch, variant):
    import numpy as np
    from hetu_quant import backtest
    from hetu_quant.backtest_evaluation import evaluate_backtest
    from hetu_quant.backtest_models import BacktestRef, backtest_definition_from_dict
    from streamlit.testing.v1 import AppTest

    start, end = (_phase2_cli_definition("final_main")[key] for key in ("start", "end"))
    sessions = [day.date().isoformat() for day in pd.bdate_range(start, end)]
    jan = sum(day.startswith("2024-01") for day in sessions)
    initial = _phase2_cli_definition("final_main")["protocol"]["initial_cash_cny"]
    trial_rows = []
    defect_day = sessions[0] if variant == "missing_exposure" else "2024-01-15"

    def publish(trial_id, january_close, *, benchmark=None, market=None, cash_proved=True):
        facts = _phase2_cli_definition(trial_id)
        definition = backtest_definition_from_dict(facts)
        run_id = f"synthetic-{trial_id}"
        kind = "benchmark" if trial_id == "final_benchmark" else "candidate"
        direction = tmp_path / "backtests" / run_id
        bundle = direction / "bundle"
        (bundle / "tables").mkdir(parents=True)
        pd.DataFrame({"code": ["A"], "signal_date": ["2023-12-29"]}).to_parquet(bundle / "universe.parquet")
        calendar = pd.DataFrame({"date": sessions, "is_trading_day": True})
        calendar.to_parquet(bundle / "tables/calendar.parquet")
        for name in ("daily_bars", "security_status", "corporate_actions", "market_cap", "trading_rules"):
            pd.DataFrame().to_parquet(bundle / "tables" / f"{name}.parquet")
        values = np.r_[np.linspace(initial, january_close, jan), np.full(len(sessions) - jan, january_close)]
        tables = {name: pd.DataFrame(columns=columns) for name, columns in backtest.TABLE_COLUMNS.items()}
        tables["nav"] = pd.DataFrame(dict(date=sessions, net_nav=values, gross_nav=values, benchmark_nav=np.nan))
        if variant == "missing_months":
            later = tables["nav"]["date"].gt("2024-01-31")
            tables["nav"] = tables["nav"].loc[later].copy()
            for day, value in (("2024-02-01", 1_100_000), ("2024-02-29", 1_080_000), ("2024-03-29", 1_090_000)):
                tables["nav"].loc[tables["nav"]["date"].eq(day), ["net_nav", "gross_nav"]] = value
        if trial_id == "final_double_costs":
            if variant in {"missing_row", "missing_exposure"}:
                tables["nav"] = tables["nav"].loc[~tables["nav"]["date"].eq(defect_day)].copy()
            elif variant == "nan":
                tables["nav"].loc[tables["nav"]["date"].eq(defect_day), "net_nav"] = np.nan
        tables["calendar"] = calendar
        if trial_id.startswith("final_") and trial_id not in {"final_main", "final_benchmark"}:
            tables["targets"] = pd.DataFrame([
                dict(code="A", signal_at="2023-12-29", executable_from=day, target_weight=1.0)
                for day in ("2023-12-29", sessions[0])
            ])
            tables["exposure_facts"] = pd.DataFrame([dict(
                code="A", signal_at="2023-12-29", market_cap_cny=80e6,
                market_cap_source="cap-A", industry="I", industry_source="I-A",
            )])
            if cash_proved:
                tables["cash"] = pd.DataFrame([dict(date=sessions[0], cash_available=initial, receivable=0,
                                                    dividend_tax_payable=0)])
        resolved = backtest._attach_formal_references(definition, tables, bundle, run_id, benchmark, market)
        evaluation = evaluate_backtest(tables, definition.protocol)
        manifest = dict(run_id=run_id, status="complete", kind=kind, definition=facts, resolved_inputs=resolved,
                        sources={"snapshot_id": definition.snapshot_id})
        backtest._write_publish(direction, manifest, tables, evaluation)
        trial_rows.append(dict(trial_id=trial_id, run_id=run_id, kind=kind, status="complete"))
        return BacktestRef(run_id, tmp_path, "complete")

    market = publish("final_benchmark", initial * 0.95)
    main = publish("final_main", initial * 1.10, benchmark=market)
    stress = publish("final_double_costs", initial * 1.07, benchmark=main, market=market)
    capacity = publish("final_capacity", initial * 1.07, benchmark=main, cash_proved=False)
    _write_phase2_trials(tmp_path, trial_rows)
    supplied = backtest_reporting.publish_research_handoff(tmp_path, "phase2-lowvol", "pressure-evidence")
    released = backtest_reporting.read_research_handoff(supplied)
    for account, known in ((stress, True), (capacity, False)):
        run = released["runs"][account.run_id]
        metrics = run["evaluation"]["metrics"]
        assert metrics == backtest_reporting.read_backtest(account)["evaluation"]["metrics"]
        if variant == "missing_months":
            states = metrics["market_states"]
            assert [item["reason"] for item in states["gaps"][:2]] == [
                "整个月段无配对净值", "缺月段开始前的配对净值",
            ]
            assert states["gaps"][1]["anchor_date"] == "2024-01-31"
            march = next(item for item in states["states"]["up"]["periods"] if item["window"][1] == "2024-03-29")
            assert march["anchor_date"] == "2024-02-29"
            assert march["benchmark_return"] == pytest.approx(10_000 / 1_080_000)
            continue
        if account == stress and variant != "complete":
            gap, = metrics["daily_nav_gaps"]
            assert (gap["date"], gap["field"], gap["side"], gap["reason"]) == (
                defect_day, "net_nav", "candidate", "缺有限非负净值" if variant == "nan" else "缺净值记录",
            )
            assert metrics["market_states"]["gaps"][0]["daily"] == [gap]
            if defect_day == "2024-01-15":
                known_day, = metrics["daily_nav_evidence"]
                assert known_day == dict(date=defect_day, candidate_nav=None,
                                         comparison_nav=pytest.approx(1_045_454.55, abs=.01),
                                         market_nav=pytest.approx(977_272.73, abs=.01))
            else:
                exposure = metrics["exposures"]
                snapshot, = exposure["snapshots"]
                assert snapshot["date"] == defect_day
                assert snapshot["target"]["industry"]["weights"] == {"I": 1.0}
                assert snapshot["target"]["size"]["weighted_mean_market_cap_cny"] == 80e6
                assert snapshot["actual"]["evidence"] == {
                    "status": "unknown", "reason": "缺生效日有效账户净值", "date": defect_day, "field": "net_nav",
                }
            continue
        assert metrics["comparison"]["reference"]["run_id"] == main.run_id
        assert metrics["comparison"]["candidate_return"] == pytest.approx(0.07)
        assert metrics["comparison"]["reference_return"] == pytest.approx(0.10)
        assert metrics["market_states"]["reference"]["run_id"] == market.run_id
        assert metrics["market_states"]["states"]["down"]["benchmark_return"] == pytest.approx(-0.05)
        exposure = metrics["exposures"]
        snapshot = exposure["snapshots"][0]
        assert snapshot["target"]["industry"]["weights"] == {"I": 1.0}
        assert snapshot["actual"]["holdings_weight"] == (0.0 if known else None)
        assert snapshot["actual"]["evidence"]["status"] == ("all_cash" if known else "unknown")
        assert exposure["status"] == ("available" if known else "limited")
        assert metrics == backtest_reporting.read_backtest(account)["evaluation"]["metrics"]

    app = AppTest.from_string(
        "from pathlib import Path\nfrom hetu_quant.ui.strategy_page import render_strategy_page\n"
        f"render_strategy_page(Path({str(tmp_path)!r}), None)"
    ).run()
    app.selectbox(key="strategy_asset").set_value("low_volatility").run()
    next(box for box in app.selectbox if box.label == "研究发布物").set_value(supplied).run()
    assert not app.exception
    assert released in [json.loads(shown.value) for shown in app.json]
    text = [entry.value for entry in app.markdown]
    if variant == "complete":
        assert any("final_double_costs" in entry and "压力账户 +7.00%，主策略 +10.00%" in entry for entry in text)
        assert any("市场下跌月段合计 -5.00%" in entry and market.run_id in entry for entry in text)
    elif variant in {"missing_row", "nan"}:
        assert any("final_double_costs" in entry and "逐日净值缺口" in entry for entry in text)
        visible = [table.value for table in app.dataframe]
        values = next(table for table in visible if "market_nav" in table)
        assert values.iloc[0]["market_nav"] == pytest.approx(977_272.73, abs=.01)
        assert values.iloc[0]["comparison_nav"] == pytest.approx(1_045_454.55, abs=.01)

    notebook = Path(__file__).parents[1] / "notebooks/read_result.ipynb"
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    monkeypatch.setenv("HETU_QUANT_RESEARCH_RELEASE", str(supplied))
    monkeypatch.setenv("HETU_QUANT_ROOT", str(empty_root))
    monkeypatch.chdir(empty_root)
    namespace = {}
    for cell in json.loads(notebook.read_text())["cells"]:
        if cell["cell_type"] == "code":
            exec(compile("".join(cell["source"]), str(notebook), "exec"), namespace)
    assert namespace["research_payload"] == released


@pytest.mark.parametrize("restore,through,calendar_status,stale_mark", [
    (False, "2024-03-29", "closed", False), (True, "2024-03-29", "closed", False),
    (False, "2024-04-30", "closed", False),
    (True, "2024-03-31", "closed", True), (True, "2024-03-31", "unknown", True),
    (True, "2024-03-31", "conflict", True),
    (True, "2024-03-31", "open", False), (True, "2024-03-31", "conflict", False),
    (True, "2024-03-31", "closed", False),
])
def test_published_evaluation_uses_expected_boundary_for_every_metric(
    tmp_path, monkeypatch, restore, through, calendar_status, stale_mark,
):
    from hetu_quant.backtest import TABLE_COLUMNS, _write_publish
    from hetu_quant.backtest_evaluation import evaluate_backtest
    from hetu_quant.backtest_models import BacktestRef
    from streamlit.testing.v1 import AppTest

    strategy, trial, run_id, study = "boundary-candidate", "slice-trial", "slice-run", "boundary-study"
    asset = tmp_path / "asset.json"
    asset.write_text(json.dumps(dict(strategy_id=strategy, name="Boundary test", study_ids=[study])))
    import_strategy_evidence(tmp_path, asset)
    ledger = tmp_path / "research" / study / "trials.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(json.dumps(dict(trial_id=trial, run_id=run_id, kind="candidate", status="complete",
                                     strategy_id=strategy)) + "\n")
    facts = _phase2_cli_definition("final_main")
    facts.update(strategy_id=strategy, start="2024-01-31", end="2024-04-30")
    protocol = facts["protocol"]
    protocol["time_split"] = {
        "development": ["2024-01-31", "2024-01-31"], "final": ["2024-03-01", min(through, "2024-03-31")],
        "next": ["2024-04-01", "2024-04-30"],
    }
    protocol["evaluation_window"] = ["2024-03-01", through]
    sessions = ["2024-01-31", "2024-02-29", "2024-03-01", "2024-03-15", "2024-03-29", "2024-04-01", "2024-04-30"]
    values = [(1e6, 1e6), (1e6, 1e6), (1.02e6, 1.01e6), (1.04e6, 1.025e6), (1.09e6, 1.06e6),
              (1.095e6, 1.065e6), (1.10e6, 1.07e6)]
    observations = [(day, own, own, base) for day, (own, base) in zip(sessions, values, strict=True)
                    if restore or day != "2024-02-29"]
    tables = {name: pd.DataFrame(columns=columns) for name, columns in TABLE_COLUMNS.items()}
    if stale_mark:
        observations.append(("2024-03-31", 0.99e6, 0.99e6, 1.01e6))
    tables["nav"] = pd.DataFrame(observations, columns=TABLE_COLUMNS["nav"])
    tables["calendar"] = pd.DataFrame(dict(date=sessions, is_trading_day=True))
    for flag in {"closed": [False], "unknown": [], "conflict": [False, True], "open": [True]}[calendar_status]:
        tables["calendar"].loc[len(tables["calendar"])] = ["2024-03-31", flag]
    tables["run"] = pd.DataFrame([dict(status="complete", detail="", account_start=facts["start"],
                                       account_end=facts["end"])])
    tables["benchmark"] = pd.DataFrame([dict(benchmark_id="universe_vw", kind="account", run_id="known-baseline")])
    result = evaluate_backtest(tables, protocol)
    directory = tmp_path / "backtests" / run_id
    directory.mkdir(parents=True)
    manifest = dict(run_id=run_id, kind="candidate", status="complete", definition=facts,
                    sources={"snapshot_id": facts["snapshot_id"]})
    _write_publish(directory, manifest, tables, result)
    release = backtest_reporting.publish_research_handoff(tmp_path, study, "boundary-evidence")
    reopened = backtest_reporting.read_research_handoff(release)
    result = reopened["runs"][run_id]["evaluation"]
    assert result == backtest_reporting.read_backtest(BacktestRef(run_id, tmp_path, "complete"))["evaluation"]
    metrics = result["metrics"]
    if calendar_status in {"open", "unknown", "conflict"}:
        assert result["economic_status"] == "insufficient_evidence"
        assert result["income_target_met"] is result["risk_target_met"] is None
        assert all(metrics.get(key) is None for key in ("net_return_window", "benchmark_return_window"))
        assert metrics["subperiods"]["final"]["net_return"] is None
        market_gap = metrics["market_states"]["gaps"][0]
        if calendar_status == "open":
            assert "calendar_nav_gaps" not in metrics
            assert {(gap["date"], gap["side"], gap["reason"]) for gap in metrics["daily_nav_gaps"]} == {
                ("2024-03-31", "candidate", "缺净值记录"), ("2024-03-31", "benchmark", "缺净值记录"),
            }
            assert market_gap["daily"] == metrics["daily_nav_gaps"]
        else:
            assert metrics["calendar_nav_gaps"] == ["2024-03-31"]
            assert "2024-03-31" in result["reasons"][0]
            assert metrics["subperiods"]["final"]["calendar_nav_gaps"] == ["2024-03-31"]
            assert market_gap["calendar_nav_gaps"] == ["2024-03-31"]
        assert result["statistics"]["n_months"] == 0 and result["statistics"]["end_date"] is None
    elif restore:
        assert metrics["anchor_date"] == "2024-02-29"
        assert (metrics["net_return_window"], metrics["benchmark_return_window"]) == pytest.approx((0.09, 0.06))
        assert metrics["subperiods"]["final"]["net_return"] == pytest.approx(.09)
        assert result["statistics"]["n_months"] == 1
        march, = metrics["market_states"]["states"]["up"]["periods"]
        assert march["window"][-1] == result["statistics"]["end_date"] == "2024-03-29"
        assert (march["net_return"], march["benchmark_return"]) == pytest.approx((.09, .06))
        assert result["economic_status"] == "not_met" and result["income_target_met"] is True
    else:
        assert result["economic_status"] == "insufficient_evidence"
        assert result["income_target_met"] is result["risk_target_met"] is None
        assert all(metrics.get(key) is None for key in ("net_return_window", "benchmark_return_window"))
        assert "2024-02-29" in result["reasons"][0]
        final_slice = metrics["subperiods"]["final"]
        assert final_slice["net_return"] is final_slice["benchmark_return"] is None
        assert result["statistics"]["n_months"] == (1 if through == "2024-04-30" else 0)
        assert metrics["market_states"]["gaps"][0]["anchor_date"] == "2024-02-29"
    if calendar_status == "closed":
        assert metrics["subperiods"]["next"]["net_return"] == pytest.approx(1.10 / 1.09 - 1)
    assert metrics["subperiods"]["development"]["net_return"] == pytest.approx(0)

    app = AppTest.from_string(
        "from pathlib import Path\nfrom hetu_quant.ui.strategy_page import render_strategy_page\n"
        f"render_strategy_page(Path({str(tmp_path)!r}), None)"
    ).run()
    app.selectbox(key="strategy_asset").set_value(strategy).run()
    next(box for box in app.selectbox if box.label == "研究发布物").set_value(release).run()
    assert not app.exception and reopened in [json.loads(shown.value) for shown in app.json]

    notebook = Path(__file__).parents[1] / "notebooks/read_result.ipynb"
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("HETU_QUANT_RESEARCH_RELEASE", str(release))
    monkeypatch.setenv("HETU_QUANT_ROOT", str(empty))
    monkeypatch.chdir(empty)
    namespace = {}
    for cell in json.loads(notebook.read_text())["cells"]:
        if cell["cell_type"] == "code":
            exec(compile("".join(cell["source"]), str(notebook), "exec"), namespace)
    assert namespace["research_payload"] == reopened


def test_study_release_preserves_attachment_permission(tmp_path):
    root = tmp_path / "store"
    study = root / "research/synthetic"
    study.mkdir(parents=True)
    rows = []
    for strategy, license_text in (("shareable", "CC-BY-4.0"), ("local", "仅限本地查阅，禁止导出附件")):
        source = tmp_path / strategy
        source.mkdir()
        (source / "rules.md").write_text(f"{strategy} synthetic original")
        (source / "notes.md").write_text(f"{strategy} local notes")
        (source / "manifest.json").write_text(json.dumps({
            "strategy_id": strategy, "rules_ref": "rules.md", "study_ids": ["synthetic"],
            "license": license_text, "source_refs": [f"https://example.test/{strategy}"],
            "evidence_refs": ["notes.md"],
            "attachment_licenses": {"notes.md": "仅限本地查阅，禁止导出附件"},
        }))
        import_strategy_evidence(root, source / "manifest.json")
        run = root / "backtests" / strategy
        run.mkdir(parents=True)
        (run / "backtest.json").write_text(json.dumps({
            "run_id": strategy, "status": "complete", "definition": {"strategy_id": strategy},
        }))
        rows.append(dict(trial_id=strategy, run_id=strategy, status="complete", strategy_id=strategy))
    (study / "trials.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    path = backtest_reporting.publish_research_handoff(root, "synthetic", "one")
    with zipfile.ZipFile(path) as archive:
        assert archive.read("attachments/shareable/rules.md") == b"shareable synthetic original"
        assert "attachments/local/rules.md" not in archive.namelist()
        assert "attachments/shareable/notes.md" not in archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
    restricted = next(item for item in manifest["attachments"] if item["strategy_id"] == "local")
    assert restricted["source_refs"] == ["https://example.test/local"]
    assert restricted["scope"] == "rules.md"
    assert restricted["license"] == "仅限本地查阅，禁止导出附件"
    assert restricted["acquisition"]
    mixed = next(item for item in manifest["attachments"]
                 if item["strategy_id"] == "shareable" and item["scope"] == "notes.md")
    assert mixed["restriction"] == "仅限本地查阅，禁止导出附件"
