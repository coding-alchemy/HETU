import json

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
    for strategy_id, kind, trial_id, status, evaluation in (
        ("low_volatility", "candidate", "dev_main", "limited", {
            "execution_status": "complete", "research_status": "blocked",
            "research_conclusion": "not_evaluated", "economic_status": "not_evaluated",
            "reasons": ["synthetic qualification gap"],
        }),
        ("universe_vw", "benchmark", "dev_benchmark", "complete", {
            "execution_status": "complete", "research_status": "evaluated",
            "research_conclusion": "diagnostic_only", "economic_status": "not_evaluated",
        }),
    ):
        run_id = f"bt-{kind}"
        published = tmp_path / "backtests" / run_id
        published.mkdir(parents=True)
        (published / "backtest.json").write_text(json.dumps({
            "run_id": run_id, "status": status, "definition": {"strategy_id": strategy_id},
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
