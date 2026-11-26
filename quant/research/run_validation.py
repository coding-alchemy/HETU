"""二期 03.2 独立验证入口：plan-only 出范围与缺口，execute 跑获批研究。

CLI：
    quant/.venv/bin/python quant/research/run_validation.py \
      --protocol quant/research/phase2-protocol.json --data-root .hetu/quant \
      --out <新目录> --plan-only
    # 另获本次范围授权后：--execute（与 --plan-only 互斥，二者必居其一）

plan-only 只读现有资产：协议、试验台账、快照与准入证据存在性；不计算策略、
不调用引擎、不访问网络。execute 先执行预算门禁（本次所需运行次数超出余量即在
执行前拒绝并登记）；每次运行结束即时登记台账，引擎异常先记 failed 行（计入预算）
再上抛。真实引擎准入未成立时明确受阻，不静默降级。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

CN = ZoneInfo("Asia/Shanghai")

ZERO_COSTS = {
    "data_cost_cny": 0,
    "company_research_calls": 0,
    "llm_business_calls": 0,
}


def _load_protocol(protocol_path: Path) -> dict:
    payload = json.loads(Path(protocol_path).read_text(encoding="utf-8"))
    if "study_id" not in payload or "definition" not in payload:
        raise ValueError("protocol file must contain study_id and definition")
    return payload


def _ledger_path(data_root: Path, study_id: str) -> Path:
    return Path(data_root) / "research" / study_id / "trials.jsonl"


def _read_trials(ledger: Path) -> list[dict]:
    if not ledger.exists():
        return []
    return [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]


def _count_runs(trials: list[dict]) -> int:
    """拒绝登记不算运行次数；失败、受阻、负结果均计入预算。"""
    return sum(1 for row in trials if row.get("status") != "refused_budget")


def _gaps(payload: dict, data_root: Path) -> list[str]:
    from hetu_quant.backtest import EngineNotAdmittedError, _default_admission_path, _require_admission

    gaps: list[str] = []
    definition = payload["definition"]
    snapshot_id = str(definition.get("snapshot_id", ""))
    if snapshot_id == "pending-real-data-admission":
        gaps.append("真实数据资格未成立：snapshot_id 为占位，待 01 真实来源就绪后替换")
    elif not (Path(data_root) / "snapshots" / snapshot_id).exists():
        gaps.append(f"快照不存在：{snapshot_id}")
    admission = Path(data_root) / "qualification" / "phase2"
    if not admission.exists():
        gaps.append("引擎／数据准入证据目录缺失，formal 数值运行受阻")
    else:
        try:
            _require_admission(_default_admission_path(data_root))
        except (EngineNotAdmittedError, OSError, ValueError) as error:
            gaps.append(str(error))
    return gaps


def _check_admission_evidence(payload: dict, data_root: Path) -> None:
    gaps = _gaps(payload, data_root)
    if gaps:
        raise ValueError("run_validation blocked: " + "; ".join(gaps))


def plan_validation(protocol_path: Path, data_root: Path, output_dir: Path) -> dict:
    """只读现有资产，返回范围／缺口／次数与成本；不计算策略、不调引擎。"""
    from hetu_quant.data_research import frozen_trial_qualification

    payload = _load_protocol(Path(protocol_path))
    definition = payload["definition"]
    protocol = definition["protocol"]
    study_id = str(payload["study_id"])
    trials = _read_trials(_ledger_path(Path(data_root), study_id))
    max_runs = int(protocol["trial_budget"]["max_runs"])
    used = _count_runs(trials)
    return {
        "mode": "plan_only",
        "study_id": study_id,
        "protocol_path": str(protocol_path),
        "stage": protocol["stage"],
        "scope": {
            "strategy_id": definition["strategy_id"],
            "start": definition["start"],
            "end": definition["end"],
            "warmup_months": protocol["warmup_months"],
            "benchmark_id": protocol["benchmark_id"],
        },
        "planned_runs": max_runs,
        "definitions": validation_definitions(payload),
        "signal_qualification": frozen_trial_qualification(payload, Path(data_root), trials),
        "completed_trials": used,
        "remaining_budget": max_runs - used,
        "gaps": _gaps(payload, Path(data_root)),
        "costs": dict(ZERO_COSTS),
        "note": "plan-only 不产生任何信号／引擎调用；execute 须另获本次范围授权",
    }


def validation_definitions(payload: dict) -> list[dict]:
    """把获批的八次预算展开为事实定义；开发阶段不携带最终区间。"""
    original = payload["definition"]
    universe = original['protocol']['universe']
    actual_universe = dict(rule='historical_sh_sz_common_stock', includes_delisted=True,
        boards=['main', 'chinext', 'star'], ipo_warmup_rule='exclude_and_record_daily',
        no_future_exclusion=True)
    synthetic_rules = ('synthetic_history', 'synthetic_account', 'synthetic_signal_pool')
    if universe != actual_universe and universe not in [{'rule': rule} for rule in synthetic_rules]:
        raise ValueError('unsupported universe: only approved historical SH/SZ common stocks '
                         'or explicit synthetic fixtures')
    split = original["protocol"]["time_split"]
    if not all(name in split for name in ("development", "validation", "final")):
        raise ValueError("time_split requires development, validation and final")
    rows = []
    for phase, prefix in (("development_validation", "dev"), ("final", "final")):
        variants = ("benchmark", "main", "neighbor_30", "neighbor_42") if prefix == "dev" else (
            "benchmark", "main", "double_costs", "capacity")
        for variant in variants:
            definition = deepcopy(original)
            protocol = definition["protocol"]
            selected = {key: value for key, value in split.items()
                        if (key != "final") == (prefix == "dev")}
            protocol["time_split"] = selected
            definition["start"] = min(value[0] for value in selected.values())
            definition["end"] = max(value[1] for value in selected.values())
            protocol["research_phase"] = phase
            protocol["evaluation_window"] = [definition["start"], definition["end"]]
            trial_id = f"{prefix}_{variant}"
            reference = None if variant == "benchmark" else f"{prefix}_benchmark"
            dependencies = [] if reference is None else [reference]
            if variant == "benchmark":
                definition["strategy_id"] = protocol["benchmark_id"]
                if prefix == "final":
                    dependencies = [f"dev_{v}" for v in ("benchmark", "main", "neighbor_30", "neighbor_42")]
            if variant.startswith("neighbor_"):
                months = int(variant.rsplit("_", 1)[1])
                protocol["warmup_months"] = months
                definition["parameters"]["lookback_months"] = months
            if variant in ("double_costs", "capacity"):
                reference = "final_main"
                dependencies = [reference]
                protocol["comparison"] = {"purpose": "sensitivity_vs_main"}
                if variant == "double_costs":
                    for key in ("commission_rate", "min_commission_cny", "slippage_rate"):
                        protocol["costs"][key] *= 2
                    protocol["comparison"]["changed_assumptions"] = ["costs"]
                else:
                    protocol["execution"]["participation_rate"] = .0025
                    protocol["comparison"]["changed_assumptions"] = ["participation_rate"]
            rows.append(dict(trial_id=trial_id, phase=phase, definition=definition,
                             dependencies=dependencies, reference_trial=reference))
    return rows


def _resolved(trials: list[dict]) -> dict[str, dict]:
    return {row["trial_id"]: row for row in trials
            if row.get("trial_id") and row.get("status") == "complete"}


def _published_benchmark_rows(trials):
    for row in reversed(trials):
        run_id = row.get("run_id")
        if (row.get("kind") == "benchmark" and row.get("status") == "complete"
                and isinstance(run_id, str) and re.fullmatch(r"[A-Za-z0-9_-]+", run_id)):
            yield row


def _resolve_references(definitions: list[dict], trials: list[dict], data_root: Path) -> dict[str, dict]:
    from hetu_quant.backtest import validate_benchmark_reference
    from hetu_quant.backtest_models import BacktestRef, backtest_definition_from_dict

    done = _resolved(trials)
    for row in definitions:
        if row["reference_trial"] is not None or row["trial_id"] in done:
            continue
        for prior in _published_benchmark_rows(trials):
            ref = BacktestRef(run_id=prior["run_id"], root=data_root, status="complete")
            try:
                validate_benchmark_reference(backtest_definition_from_dict(row["definition"]), ref)
            except (ValueError, FileNotFoundError):
                continue
            done[row["trial_id"]] = {**prior, "trial_id": row["trial_id"], "reused": True}
            break
    return done


def _execution_scope(definitions, trials, data_root, phase, trial_id):
    by_id = {row["trial_id"]: row for row in definitions}
    done = _resolve_references(definitions, trials, data_root)
    attempted = {row.get("trial_id") for row in trials if row.get("status") != "refused_budget"}
    candidates = (row for row in definitions if row["phase"] == phase
                  and row["trial_id"] not in attempted and row["reference_trial"] is not None)
    selected = by_id.get(trial_id) if trial_id else next(candidates, None)
    if selected is None:
        return None, [], done
    key = selected["trial_id"]
    completed_benchmark = selected["reference_trial"] is None and any(
        row.get("trial_id") == key and row.get("kind") == "benchmark" and row.get("status") == "complete"
        for row in trials
    )
    if selected["phase"] != phase or (key in attempted and not completed_benchmark):
        raise ValueError("trial already attempted or phase mismatch; no automatic retry")
    needed = {}

    def collect(row):
        key = row["trial_id"]
        if key in done or key in needed:
            return
        for dependency in row["dependencies"]:
            collect(by_id[dependency])
        needed[key] = row

    collect(selected)
    if attempted.intersection(needed):
        raise ValueError("failed dependency retained; explicit review required before further execution")
    return selected, list(needed.values()), done


def _read_reused_benchmark(selected, prior, store, trials, *, cancelled=None, qualification_delivery=None):
    from hetu_quant.backtest import BacktestCancelled, prepare_backtest
    from hetu_quant.backtest_models import backtest_definition_from_dict

    candidates = [prior]
    if selected["trial_id"] not in _resolved(trials):
        # 依赖解析许可候选使用基准前缀。整账户复用须继续检查其余原发布物。
        candidates.extend(row for row in _published_benchmark_rows(trials) if row["run_id"] != prior["run_id"])
    definition = backtest_definition_from_dict(selected["definition"])
    failures = []
    with TemporaryDirectory(prefix="hetu-benchmark-reuse-") as workdir:
        bundle = Path(workdir) / "bundle"
        input_options = {"qualification_delivery": qualification_delivery} if qualification_delivery is not None else {}
        report = prepare_backtest(definition, store, bundle, record_qualification=False, **input_options)
        if not report.can_run:
            raise ValueError(", ".join(report.limitations) or "cannot verify comparable benchmark inputs")
        for row in candidates:
            if cancelled is not None and cancelled():
                raise BacktestCancelled("cancelled during benchmark verification")
            try:
                evaluation = _verify_reused_benchmark(definition, store.root, bundle, row.get("run_id"))
            except (ValueError, OSError, KeyError) as error:
                failures.append(error)
                continue
            if cancelled is not None and cancelled():
                raise BacktestCancelled("cancelled during benchmark verification")
            return row, evaluation
    raise failures[0]


def _verify_reused_benchmark(definition, data_root, bundle, run_id):
    from hetu_quant.backtest import validate_benchmark_reference
    from hetu_quant.backtest_models import BacktestRef

    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
        raise ValueError("completed benchmark lacks a valid published run")
    reference = BacktestRef(run_id=run_id, root=data_root, status="complete")
    manifest = validate_benchmark_reference(definition, reference, bundle)
    published = manifest["definition"]
    requested_interval = (definition.start.isoformat(), definition.end.isoformat())
    published_interval = (published.get("start"), published.get("end"))
    if published_interval != requested_interval:
        raise ValueError(f"incompatible_benchmark: execution_interval {published_interval} != {requested_interval}")
    if (manifest.get("run_id") != run_id or published.get("mode") != definition.mode
            or published.get("market") != definition.market or published["protocol"].get("stage") != "formal"):
        raise ValueError("incompatible_benchmark: publication identity, market, mode or stage")
    evaluation = json.loads((data_root / "backtests" / run_id / "evaluation.json").read_text())
    required = {"research_status", "economic_status", "research_conclusion", "next_decision"}
    if not isinstance(evaluation, dict) or not required.issubset(evaluation):
        raise ValueError("incompatible_benchmark: published evaluation is incomplete")
    if evaluation["research_status"] == "blocked":
        raise ValueError("incompatible_benchmark: published evaluation is blocked")
    return evaluation


def _qualify_candidate_reference(definition, data_root, bundle, prior, alternatives):
    from hetu_quant.backtest import validate_benchmark_reference
    from hetu_quant.backtest_models import BacktestRef

    failures = []
    for row in (prior, *alternatives):
        reference = BacktestRef(run_id=row["run_id"], root=data_root, status="complete")
        try:
            validate_benchmark_reference(definition, reference, bundle)
        except (ValueError, OSError, KeyError) as error:
            failures.append(error)
        else:
            return row
    raise failures[0]


def _qualification_input_options(payload, store):
    from hetu_quant.data_research import frozen_trial_delivery

    if payload["definition"]["protocol"]["universe"]["rule"] == "historical_sh_sz_common_stock":
        return {"qualification_delivery": frozen_trial_delivery(payload, store.root)}
    return {}


def _qualify_pending_trials(payload, store, needed, done, trials, *, preview=False, cancelled=None):
    from hetu_quant.backtest import BacktestCancelled, prepare_backtest, validate_prepared_universe
    from hetu_quant.backtest_models import backtest_definition_from_dict
    from hetu_quant.data_research import require_frozen_trial_inputs

    trial_ids = {row["trial_id"] for row in needed}
    input_universes = require_frozen_trial_inputs(payload, store.root, trial_ids, trials) if trial_ids else {}
    input_options = _qualification_input_options(payload, store)
    record_qualification = preview or bool(input_universes)
    report = None
    explicit = _resolved(trials)
    for row in needed:
        reference_trial = row["reference_trial"]
        prior = done.get(reference_trial)
        unbound = prior is not None and reference_trial not in explicit
        if not (record_qualification or unbound):
            continue
        if cancelled is not None and cancelled():
            raise BacktestCancelled("cancelled during formal input qualification")
        key = row["trial_id"]
        definition = backtest_definition_from_dict(row["definition"])
        with TemporaryDirectory(prefix="hetu-formal-qualification-") as workdir:
            bundle = Path(workdir) / "bundle"
            try:
                report = prepare_backtest(
                    definition, store, bundle, record_qualification=record_qualification, **input_options,
                )
                if not report.can_run:
                    raise ValueError(", ".join(report.limitations) or "account inputs not qualified")
                validate_prepared_universe(bundle, input_universes[key] if input_universes else None)
                if prior is not None:
                    alternatives = (item for item in _published_benchmark_rows(trials)
                                    if item["run_id"] != prior["run_id"]) if unbound else ()
                    done[reference_trial] = _qualify_candidate_reference(
                        definition, store.root, bundle, prior, alternatives,
                    )
            except (ValueError, FileNotFoundError) as error:
                raise ValueError(f"{key}: {error}") from error
    return input_universes, report


def preview_validation(payload, store, *, phase, trial_id):
    """Read-only qualification of the same pending accounts the driver would run."""
    from hetu_data.models import QualityReport

    ledger = _ledger_path(store.root, payload["study_id"])
    trials = _read_trials(ledger)
    definitions = validation_definitions(payload)
    selected, needed, done = _execution_scope(definitions, trials, store.root, phase, trial_id)
    if selected is None:
        raise ValueError(f"unknown trial: {trial_id}")
    if not needed:
        prior, _ = _read_reused_benchmark(
            selected, done[selected["trial_id"]], store, trials, **_qualification_input_options(payload, store),
        )
        return QualityReport(True, (), (), {}, (f"复用已发布账户 {prior['run_id']}",))
    _check_budget(payload, trials, needed)
    _, report = _qualify_pending_trials(payload, store, needed, done, trials, preview=True)
    _check_final_entry(payload, definitions, done, phase, ledger.parent / "final-entry.json")
    _check_admission_evidence(payload, store.root)
    return report


def _run_engine_once(payload, store, selected, needed, done, *, phase, queued_payload, input_universes,
                     progress=None, cancelled=None) -> dict:
    from hetu_quant.backtest import run_backtest, validate_benchmark_reference
    from hetu_quant.backtest_models import BacktestRef, backtest_definition_from_dict

    data_root = store.root
    ledger = _ledger_path(data_root, payload["study_id"])
    scheduled = [(row, backtest_definition_from_dict(row["definition"])) for row in needed]

    runs = []
    for index, (row, definition) in enumerate(scheduled):
        reference = None
        if row["reference_trial"]:
            prior = done[row["reference_trial"]]
            reference = BacktestRef(run_id=prior["run_id"], root=data_root, status="complete")
            validate_benchmark_reference(definition, reference)
        started = False

        def mark_started():
            nonlocal started
            started = True

        try:
            callbacks = {"on_engine_start": mark_started}
            if row["reference_trial"] == "final_main" and "final_benchmark" in done:
                callbacks["market_benchmark"] = BacktestRef(
                    run_id=done["final_benchmark"]["run_id"], root=data_root, status="complete",
                )
            if queued_payload:
                callbacks['research_context'] = dict(study_id=payload['study_id'],
                    trial_id=row['trial_id'], phase=phase, payload=payload)
            if progress is not None:
                callbacks['progress'] = lambda value, i=index: progress((i + value) / len(needed))
            if cancelled is not None:
                callbacks['cancelled'] = cancelled
            if input_universes:
                callbacks["expected_universe"] = input_universes[row["trial_id"]]
                callbacks.update(_qualification_input_options(payload, store))
            ref = run_backtest(definition, store, data_root, benchmark=reference, **callbacks)
            if not started:
                manifest = json.loads((data_root / "backtests" / ref.run_id / "backtest.json").read_text())
                detail = "; ".join(manifest.get("limitations", ()))
                raise ValueError(f"trial blocked before engine start: {row['trial_id']}: {detail}")
            evaluation = json.loads((data_root / "backtests" / ref.run_id / "evaluation.json").read_text())
            status = "blocked" if evaluation["research_status"] == "blocked" else ref.status
            economic_status = evaluation["economic_status"]
        except Exception as exc:
            if started:
                _append_trial(ledger, dict(trial_id=row["trial_id"], phase=phase, run_id=None,
                    kind="benchmark" if row["reference_trial"] is None else "candidate",
                    status="failed", economic_status="not_evaluated",
                    recorded_at=datetime.now(tz=CN).isoformat(), detail=f"{type(exc).__name__}: {exc}"))
            raise
        record = dict(trial_id=row["trial_id"], phase=phase, run_id=ref.run_id,
            kind="benchmark" if row["reference_trial"] is None else "candidate",
            status=status, economic_status=economic_status, recorded_at=datetime.now(tz=CN).isoformat())
        _append_trial(ledger, record)
        if status != "complete":
            raise ValueError(f"trial blocked: {row['trial_id']}; retained against budget")
        done[row["trial_id"]] = record
        runs.append({**record, "reused": False})
    result = done[selected["trial_id"]]
    evaluation = json.loads((data_root / "backtests" / result["run_id"] / "evaluation.json").read_text())
    return _validation_result(payload, selected, done, phase, runs, evaluation)


def _validation_result(payload, selected, done, phase, runs, evaluation):
    result = done[selected["trial_id"]]
    reference_row = done.get(selected["reference_trial"])
    return dict(mode="execute", study_id=payload["study_id"], phase=phase, runs=runs,
        trial_id=selected["trial_id"], definition=selected["definition"],
        benchmark={"run_id": reference_row["run_id"], "reused": len(runs) == 1} if reference_row else None,
        candidate={"run_id": result["run_id"], "economic_status": evaluation["economic_status"],
                   "research_conclusion": evaluation["research_conclusion"]},
        execution_status="complete", research_status=evaluation["research_status"],
        economic_status=evaluation["economic_status"], integration_status="integrated",
        next_decision=evaluation["next_decision"], costs=dict(ZERO_COSTS))


def _append_trial(ledger: Path, row: dict) -> None:
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _behavior_definitions(definitions: list[dict]) -> list[dict]:
    """冻结八次研究行为；仅剥离已知来源定位，实际输入另经生产资格/可比性核验。"""
    behavior = deepcopy(definitions)
    for row in behavior:
        definition = row["definition"]
        definition.pop("as_of", None)
        definition.pop("snapshot_id", None)
        protocol = definition["protocol"]
        protocol.pop("rules_ref", None)
        protocol["costs"].pop("statutory_rules_ref", None)
    return behavior


def _source_identity(definition: dict) -> dict:
    return dict(as_of=definition["as_of"], snapshot_id=definition["snapshot_id"],
                rules_ref=definition["protocol"].get("rules_ref"),
                statutory_rules_ref=definition["protocol"]["costs"].get("statutory_rules_ref"))


def _check_budget(payload, trials, needed, *, ledger=None):
    used = _count_runs(trials)
    maximum = int(payload["definition"]["protocol"]["trial_budget"]["max_runs"])
    if used + len(needed) > maximum:
        if ledger is not None:
            _append_trial(ledger, dict(run_id=f"refused-{used + 1}", status="refused_budget",
                recorded_at=datetime.now(tz=CN).isoformat(), needed_runs=len(needed)))
        raise ValueError(f"trial_budget exhausted: {used}/{maximum}; need {len(needed)}")


def _check_final_entry(payload, definitions, done, phase, freeze):
    if phase != "final":
        return
    if any(row["trial_id"] not in done for row in definitions if row["phase"] != "final"):
        raise ValueError("final stage blocked: development/validation incomplete")
    viewing = payload["definition"]["protocol"]["viewing_record"]
    if (payload.get("status") != "frozen_pre_result" or not payload.get("frozen_at")
            or viewing.get("final_used_for_tuning")):
        raise ValueError("final stage requires pre-result freeze and viewing record")
    if (freeze.exists() and _behavior_definitions(json.loads(freeze.read_text())["frozen_definitions"])
            != _behavior_definitions(definitions)):
        raise ValueError("final definitions changed after entry; cannot tune after viewing")


def run_validation(protocol_path: Path, data_root: Path, output_dir: Path,
                   *, phase: str = "development_validation", trial_id: str | None = None,
                   payload: dict | None = None, progress=None, cancelled=None) -> dict:
    from hetu_quant.data import DataStore

    supplied_payload = payload is not None
    payload = payload if supplied_payload else _load_protocol(protocol_path)
    ledger = _ledger_path(data_root, payload["study_id"])
    trials = _read_trials(ledger)
    definitions = validation_definitions(payload)
    selected, needed, done = _execution_scope(definitions, trials, data_root, phase, trial_id)
    if selected is None:
        return dict(mode="execute", phase=phase, execution_status="phase_complete", costs=dict(ZERO_COSTS))
    if needed:
        _check_budget(payload, trials, needed, ledger=ledger)
    store = DataStore(data_root)
    if not needed:
        key = selected["trial_id"]
        prior, evaluation = _read_reused_benchmark(
            selected, done[key], store, trials, cancelled=cancelled, **_qualification_input_options(payload, store),
        )
        done[key] = prior
        return _validation_result(payload, selected, done, phase, [], evaluation)
    input_universes, _ = _qualify_pending_trials(payload, store, needed, done, trials, cancelled=cancelled)
    freeze = ledger.parent / "final-entry.json"
    _check_final_entry(payload, definitions, done, phase, freeze)
    _check_admission_evidence(payload, data_root)
    if phase == "final" and not freeze.exists():
        freeze.write_text(json.dumps(dict(recorded_at=datetime.now(tz=CN).isoformat(), frozen_definitions=definitions,
            viewing_record=payload["definition"]["protocol"]["viewing_record"]), ensure_ascii=False, indent=2))
    _append_trial(ledger.parent / "source-uses.jsonl", dict(
        recorded_at=datetime.now(tz=CN).isoformat(), trial_id=selected["trial_id"],
        phase=phase, source=_source_identity(selected["definition"])))
    return _run_engine_once(payload, store, selected, needed, done, phase=phase, queued_payload=supplied_payload,
                            input_universes=input_universes, progress=progress, cancelled=cancelled)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="二期独立验证入口")
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--out", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan-only", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--phase", choices=("development_validation", "final"), default="development_validation")
    parser.add_argument("--trial")
    args = parser.parse_args(argv)

    if args.plan_only:
        plan = plan_validation(Path(args.protocol), Path(args.data_root), Path(args.out))
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    run_validation(Path(args.protocol), Path(args.data_root), Path(args.out), phase=args.phase, trial_id=args.trial)
    return 0


if __name__ == "__main__":
    sys.exit(main())
