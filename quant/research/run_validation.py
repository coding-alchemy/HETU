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
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
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


def _gaps(protocol_path: Path, payload: dict, data_root: Path) -> list[str]:
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
    return gaps


def plan_validation(protocol_path: Path, data_root: Path, output_dir: Path) -> dict:
    """只读现有资产，返回范围／缺口／次数与成本；不计算策略、不调引擎。"""
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
        "completed_trials": used,
        "remaining_budget": max_runs - used,
        "gaps": _gaps(Path(protocol_path), payload, Path(data_root)),
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


def _resolve_references(definitions: list[dict], trials: list[dict], data_root: Path) -> dict[str, dict]:
    from hetu_quant.backtest import validate_benchmark_reference
    from hetu_quant.backtest_models import BacktestRef, backtest_definition_from_dict

    done = _resolved(trials)
    for row in definitions:
        if row["reference_trial"] is not None or row["trial_id"] in done:
            continue
        for prior in reversed(trials):
            if prior.get("kind") != "benchmark" or prior.get("status") != "complete" or not prior.get("run_id"):
                continue
            ref = BacktestRef(run_id=prior["run_id"], root=data_root, status="complete")
            try:
                validate_benchmark_reference(backtest_definition_from_dict(row["definition"]), ref)
            except (ValueError, FileNotFoundError):
                continue
            done[row["trial_id"]] = {**prior, "trial_id": row["trial_id"], "reused": True}
            break
    return done


def _run_engine_once(protocol_path: Path, data_root: Path, output_dir: Path,
                     *, phase: str = "development_validation", trial_id: str | None = None,
                     payload: dict | None = None, progress=None, cancelled=None) -> dict:
    from hetu_quant.backtest import run_backtest, validate_benchmark_reference
    from hetu_quant.backtest_models import BacktestRef, backtest_definition_from_dict
    from hetu_quant.data import DataStore

    queued_payload = payload is not None
    payload = payload if queued_payload else _load_protocol(protocol_path)
    ledger = _ledger_path(data_root, payload["study_id"])
    trials = _read_trials(ledger)
    definitions = validation_definitions(payload)
    by_id = {row["trial_id"]: row for row in definitions}
    done = _resolve_references(definitions, trials, data_root)
    selected = next(row for row in definitions if row["trial_id"] == trial_id)
    needed = []

    def collect(row):
        if row["trial_id"] in done:
            return
        for dependency in row["dependencies"]:
            collect(by_id[dependency])
        if row not in needed:
            needed.append(row)
    collect(selected)
    runs = []
    store = DataStore(data_root)
    for index, row in enumerate(needed):
        definition = backtest_definition_from_dict(row["definition"])
        reference = None
        if row["reference_trial"]:
            prior = done[row["reference_trial"]]
            reference = BacktestRef(run_id=prior["run_id"], root=data_root, status="complete")
            validate_benchmark_reference(definition, reference)
        try:
            callbacks = {}
            if queued_payload:
                callbacks['research_context'] = dict(study_id=payload['study_id'],
                    trial_id=row['trial_id'], phase=phase, payload=payload)
            if progress is not None:
                callbacks['progress'] = lambda value, i=index: progress((i + value) / len(needed))
            if cancelled is not None:
                callbacks['cancelled'] = cancelled
            ref = run_backtest(definition, store, data_root, benchmark=reference, **callbacks)
        except Exception as exc:
            _append_trial(ledger, dict(trial_id=row["trial_id"], phase=phase, run_id=None,
                kind="benchmark" if row["reference_trial"] is None else "candidate",
                status="failed", economic_status="not_evaluated",
                recorded_at=datetime.now(tz=CN).isoformat(), detail=f"{type(exc).__name__}: {exc}"))
            raise
        evaluation = json.loads((data_root / "backtests" / ref.run_id / "evaluation.json").read_text())
        record = dict(trial_id=row["trial_id"], phase=phase, run_id=ref.run_id,
            kind="benchmark" if row["reference_trial"] is None else "candidate",
            status="blocked" if evaluation["research_status"] == "blocked" else ref.status,
            economic_status=evaluation["economic_status"], recorded_at=datetime.now(tz=CN).isoformat())
        _append_trial(ledger, record)
        if ref.status != "complete" or evaluation["research_status"] == "blocked":
            raise ValueError(f"trial blocked: {row['trial_id']}; retained against budget")
        done[row["trial_id"]] = record
        runs.append({**record, "reused": False})
    result = done[selected["trial_id"]]
    evaluation = json.loads((data_root / "backtests" / result["run_id"] / "evaluation.json").read_text())
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


def run_validation(protocol_path: Path, data_root: Path, output_dir: Path,
                   *, phase: str = "development_validation", trial_id: str | None = None,
                   payload: dict | None = None, progress=None, cancelled=None) -> dict:
    supplied_payload = payload is not None
    payload = payload if supplied_payload else _load_protocol(protocol_path)
    ledger = _ledger_path(data_root, payload["study_id"])
    trials = _read_trials(ledger)
    definitions = validation_definitions(payload)
    by_id = {row["trial_id"]: row for row in definitions}
    done = _resolve_references(definitions, trials, data_root)
    attempted = {row.get("trial_id") for row in trials if row.get("status") != "refused_budget"}
    candidates = [row for row in definitions if row["phase"] == phase
                  and row["trial_id"] not in attempted and row["reference_trial"] is not None]
    selected = by_id.get(trial_id) if trial_id else next(iter(candidates), None)
    if selected is None:
        return dict(mode="execute", phase=phase, execution_status="phase_complete", costs=dict(ZERO_COSTS))
    if selected["phase"] != phase or selected["trial_id"] in attempted:
        raise ValueError("trial already attempted or phase mismatch; no automatic retry")
    needed = set()
    def collect(row):
        if row["trial_id"] in done:
            return
        needed.add(row["trial_id"])
        for dependency in row["dependencies"]:
            collect(by_id[dependency])
    collect(selected)
    used = _count_runs(trials)
    maximum = int(payload["definition"]["protocol"]["trial_budget"]["max_runs"])
    if used + len(needed) > maximum:
        _append_trial(ledger, dict(run_id=f"refused-{used + 1}", status="refused_budget",
            recorded_at=datetime.now(tz=CN).isoformat(), needed_runs=len(needed)))
        raise ValueError(f"trial_budget exhausted: {used}/{maximum}; need {len(needed)}")
    if any(key in attempted and key not in done for key in needed):
        raise ValueError("failed dependency retained; explicit review required before further execution")
    if phase == "final":
        if any(row["trial_id"] not in done for row in definitions if row["phase"] != "final"):
            raise ValueError("final stage blocked: development/validation incomplete")
        viewing = payload["definition"]["protocol"]["viewing_record"]
        if (payload.get("status") != "frozen_pre_result" or not payload.get("frozen_at")
                or viewing.get("final_used_for_tuning")):
            raise ValueError("final stage requires pre-result freeze and viewing record")
        freeze = ledger.parent / "final-entry.json"
        if not freeze.exists():
            freeze.write_text(json.dumps(dict(recorded_at=datetime.now(tz=CN).isoformat(),
                frozen_definitions=definitions, viewing_record=viewing), ensure_ascii=False, indent=2))
        elif (_behavior_definitions(json.loads(freeze.read_text())["frozen_definitions"])
                != _behavior_definitions(definitions)):
            raise ValueError("final definitions changed after entry; cannot tune after viewing")
    gaps = _gaps(protocol_path, payload, data_root)
    if gaps:
        raise ValueError("run_validation blocked: " + "; ".join(gaps))
    _append_trial(ledger.parent / "source-uses.jsonl", dict(
        recorded_at=datetime.now(tz=CN).isoformat(), trial_id=selected["trial_id"],
        phase=phase, source=_source_identity(selected["definition"])))
    options = {}
    if supplied_payload:
        options['payload'] = payload
    if progress is not None:
        options['progress'] = progress
    if cancelled is not None:
        options['cancelled'] = cancelled
    return _run_engine_once(protocol_path, data_root, output_dir, phase=phase,
                            trial_id=selected["trial_id"], **options)


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
