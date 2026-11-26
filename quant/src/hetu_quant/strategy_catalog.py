"""只读策略索引与证据导入，适配状态由现有信号注册决定。"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path

from hetu_quant.strategies import SIGNAL_STRATEGIES

_INDEX = Path(__file__).resolve().parents[2] / "research" / "strategy-catalog.json"
_FIELDS = (
    "name", "markets", "hypothesis", "frequency", "source_refs", "license", "rules_ref",
    "adaptation_refs", "code_refs", "notebook_refs", "data_refs", "study_ids", "evidence_refs",
)
_ATTACHMENTS = ("rules_ref", "adaptation_refs", "code_refs", "notebook_refs", "data_refs", "evidence_refs")
_STATUSES = ("execution_status", "research_status", "research_conclusion", "economic_status")
_REDISTRIBUTABLE_LICENSES = frozenset({
    "apache-2.0", "bsd-2-clause", "bsd-3-clause", "cc0-1.0", "cc-by-4.0", "cc-by-sa-4.0", "mit",
    "允许导出附件",
})


def _identifier(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]+", value) is None:
        raise ValueError("invalid strategy or study identifier")
    return value


def _builtins() -> dict[str, dict]:
    return {item["strategy_id"]: item for item in json.loads(_INDEX.read_text(encoding="utf-8"))}


def _records(trials_file: Path) -> list:
    if not trials_file.is_file():
        return []
    return [json.loads(line) for line in trials_file.read_text(encoding="utf-8").splitlines() if line.strip()]


def _frozen_behavior_differences(actual: dict, expected: dict) -> list[str]:
    different = [key for key in ("start", "end", "market", "parameters") if actual.get(key) != expected[key]]
    protocol = actual.get("protocol", {})
    frozen = expected["protocol"]
    for key in ("stage", "universe", "warmup_months", "initial_cash_cny", "currency", "account_type", "leverage",
                "cash_interest_rate", "investor_tax_profile", "rebalance", "benchmark_id", "time_split",
                "research_phase", "evaluation_window", "comparison"):
        if protocol.get(key) != frozen.get(key):
            different.append(f"protocol.{key}")
    for key in ("execution", "costs"):
        values = protocol.get(key, {})
        if not isinstance(values, dict):
            different.append(f"protocol.{key}")
            continue
        for field in sorted(values.keys() | frozen[key].keys()):
            if field != "statutory_rules_ref" and values.get(field) != frozen[key].get(field):
                different.append(f"protocol.{key}.{field}")
    viewing = protocol.get("viewing_record") or {}
    if frozen["research_phase"] == "final" and viewing.get("final_used_for_tuning"):
        different.append("protocol.viewing_record.final_used_for_tuning")
    return different


def research_trial_strategy(study_id: str, trial: dict, account: dict | None = None) -> str | None:
    """核对台账、冻结定义与账户，返回有证据支持的试验归属。"""
    trial_id = trial.get("trial_id")
    run_id = trial.get("run_id")
    entry_role = trial.get("kind")
    roles = {entry_role} if entry_role in ("candidate", "benchmark") else set()
    strategies = {trial["strategy_id"]} if trial.get("strategy_id") else set()
    frozen = None
    if study_id == "phase2-lowvol":
        from hetu_quant.data_research import _definitions

        protocol = json.loads((_INDEX.parent / "phase2-protocol.json").read_text(encoding="utf-8"))
        study_definition = protocol["definition"]
        row = next((row for row in _definitions(protocol) if row["trial_id"] == trial_id), None)
        if row:
            frozen = row["definition"]
            roles.add("benchmark" if row["reference_trial"] is None else "candidate")
            strategies.add(frozen["strategy_id"])
        known = {
            "candidate": study_definition["strategy_id"],
            "benchmark": study_definition["protocol"]["benchmark_id"],
        }
        strategies.update(known[role] for role in roles)
    conflict = f"trial/account ownership differs: trial_id={trial_id}, run_id={run_id}"
    if len(strategies) > 1:
        raise ValueError(conflict)
    if account is None:
        return next(iter(strategies), None)
    definition = account.get("definition") or {}
    actual = definition.get("strategy_id")
    contexts = [value for value in (account.get("research"),
                                   definition.get("protocol", {}).get("research_execution")) if value]
    if (account.get("run_id") != run_id or account.get("status") not in ("complete", "limited")
            or not actual or (strategies and actual not in strategies)
            or (roles and account.get("kind") and account["kind"] not in roles)
            or any(ctx.get("study_id") != study_id or ctx.get("trial_id") != trial_id for ctx in contexts)
            or not (strategies or contexts)):
        raise ValueError(conflict)
    if frozen is not None:
        different = _frozen_behavior_differences(definition, frozen)
        if different:
            raise ValueError(f"{conflict}; frozen behavior differs: {', '.join(different)}")
    return actual


def _trial_evidence(root: Path, study_id: str, trial: dict) -> tuple[str | None, dict]:
    run_id = trial.get("run_id")
    manifest = None
    if isinstance(run_id, str) and re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
        backtests = root / "backtests"
        run_dir = backtests / run_id
        path = run_dir / "backtest.json"
        if path.resolve().is_relative_to(run_dir) and run_dir.resolve().is_relative_to(backtests) and path.is_file():
            manifest = json.loads(path.read_text(encoding="utf-8"))
    try:
        owner = research_trial_strategy(study_id, trial, manifest)
    except ValueError:
        return None, {}
    if manifest is None:
        return owner, {}
    path = run_dir / "evaluation.json"
    evaluation = (json.loads(path.read_text(encoding="utf-8"))
                  if path.resolve().is_relative_to(run_dir) and path.is_file() else {})
    return owner, evaluation


def _explicit_states(record: dict) -> dict:
    return {key: record[key] for key in _STATUSES if record.get(key)}


def research_trial_states(trial: dict, evaluation: dict) -> dict:
    """指定试验的原记录和评价状态；无证据的字段维持未知。"""
    defaults = dict.fromkeys(_STATUSES, "unknown")
    if trial.get("status") == "failed":
        defaults.update(execution_status="failed", economic_status="not_evaluated")
    elif trial.get("status") == "blocked":
        defaults["research_status"] = "blocked"
    return {**defaults, **_explicit_states(trial), **_explicit_states(evaluation)}


def read_strategy_asset(root: Path, strategy_id: str) -> dict:
    strategy_id = _identifier(strategy_id)
    root = Path(root).resolve()
    item = _builtins().get(strategy_id)
    imported = item is None
    if imported:
        entry = root / "strategy-assets" / strategy_id / "manifest.json"
        if not entry.resolve().is_relative_to(root / "strategy-assets") or not entry.is_file():
            raise KeyError(f"unknown strategy: {strategy_id}")
        item = json.loads(entry.read_text(encoding="utf-8"))
    trials = list(item.get("trials", []))
    states = {}
    for trial in trials:
        states.update(_explicit_states(trial))
    for study_id in item.get("study_ids", ()):
        path = root / "research" / _identifier(study_id) / "trials.jsonl"
        if not path.resolve().is_relative_to(path.parent):
            continue
        for trial in _records(path):
            owner, evaluation = _trial_evidence(root, study_id, trial)
            if owner == strategy_id:
                trial_states = _explicit_states(trial)
                # 调度器只在原评价的研究状态受阻时写入 blocked。
                if trial.get("status") == "blocked":
                    trial_states.setdefault("research_status", "blocked")
                states.update(trial_states)
                states.update(_explicit_states(evaluation))
                if evaluation:
                    trial = {**trial, "evaluation": evaluation}
            trials.append(trial)
    states.update(_explicit_states(item.get("statuses", {})))
    return {
        "strategy_id": strategy_id,
        **{key: item.get(key) for key in _FIELDS},
        **({"attachment_licenses": item["attachment_licenses"]} if item.get("attachment_licenses") else {}),
        "trials": trials,
        "statuses": {
            "evidence_import": "imported" if imported else "catalogued",
            "execution_adapter": "registered" if strategy_id in SIGNAL_STRATEGIES else "collected",
            **{key: states.get(key) or "unknown" for key in _STATUSES},
        },
        "missing": [key for key in _FIELDS if not item.get(key)],
    }


def list_strategy_assets(
    root: Path, *, market: str | None = None, hypothesis: str | None = None,
    frequency: str | None = None, research_status: str | None = None,
) -> list[dict]:
    root = Path(root)
    imported = (root / "strategy-assets").glob("*/manifest.json")
    strategy_ids = sorted(set(_builtins()) | {path.parent.name for path in imported})
    rows = [read_strategy_asset(root, strategy_id) for strategy_id in strategy_ids]
    return [
        row for row in rows
        if (market is None or market in (row["markets"] or ()))
        and (hypothesis is None or hypothesis == row["hypothesis"])
        and (frequency is None or frequency == row["frequency"])
        and (research_status is None or research_status == row["statuses"]["research_status"])
    ]


def import_strategy_evidence(root: Path, manifest_path: Path) -> dict:
    """复制登记的本地证据原字节，不执行附件或更新策略注册表。"""
    root, manifest_path = Path(root).resolve(), Path(manifest_path)
    item = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(item, dict):
        raise ValueError("strategy manifest must be an object")
    strategy_id = _identifier(item.get("strategy_id"))
    target = root / "strategy-assets" / strategy_id
    if target.parent.resolve() != target.parent:
        raise ValueError("invalid strategy evidence location")
    if strategy_id in _builtins() or target.exists():
        raise FileExistsError(f"strategy evidence already exists: {strategy_id}")
    trials, statuses = item.get("trials", []), item.get("statuses", {})
    if (
        not isinstance(trials, list)
        or not all(isinstance(row, dict) for row in trials)
        or not isinstance(statuses, dict)
        or not all(isinstance(value, str) or value is None for value in statuses.values())
    ):
        raise ValueError("invalid trials or statuses")
    for field in ("name", "hypothesis", "frequency", "license", "rules_ref"):
        if item.get(field) is not None and not isinstance(item[field], str):
            raise ValueError(f"invalid strategy metadata: {field}")
    for field in ("markets", "source_refs", "study_ids", *_ATTACHMENTS[1:]):
        if not isinstance(item.get(field, []), list) or not all(isinstance(x, str) for x in item.get(field, [])):
            raise ValueError(f"invalid strategy metadata: {field}")
    for study_id in item.get("study_ids", []):
        _identifier(study_id)
    refs = strategy_attachment_refs(item)
    licenses = item.get("attachment_licenses", {})
    if not isinstance(licenses, dict) or any(
        relative not in refs or not isinstance(license_text, str) or not license_text.strip()
        for relative, license_text in licenses.items()
    ):
        raise ValueError("invalid attachment_licenses: supply a license for each named attachment")
    source = manifest_path.parent.resolve()
    attachments = {}
    for value in refs:
        relative = Path(value)
        original = (source / relative).resolve()
        if (relative.is_absolute() or ".." in relative.parts or relative == Path("manifest.json")
                or not original.is_relative_to(source) or not original.is_file()):
            raise ValueError(f"invalid or unavailable attachment: {value}")
        attachments[relative] = original
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".strategy-", dir=target.parent) as tmp:
        staged = Path(tmp) / "evidence"
        staged.mkdir()
        shutil.copyfile(manifest_path, staged / "manifest.json")
        for relative, original in attachments.items():
            destination = staged / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, destination)
        staged.rename(target)
    return read_strategy_asset(root, strategy_id)


def strategy_text_attachments(root: Path, item: dict) -> dict[str, Path]:
    """仅定位该策略已登记的本地附件，供页面按需查看文本。"""
    return {relative: path for relative, path in strategy_attachments(root, item).items()
            if path.suffix in (".md", ".json", ".py", ".ipynb")}


def strategy_attachments(root: Path, item: dict) -> dict[str, Path]:
    """定位已登记的本地原件。"""
    if item["statuses"]["evidence_import"] == "catalogued":
        source = _INDEX.parent
        allowed = source.parent
    else:
        source = Path(root).resolve() / "strategy-assets" / _identifier(item["strategy_id"])
        allowed = source
    output = {}
    for relative in strategy_attachment_refs(item):
        path = (source / relative).resolve()
        if path.is_relative_to(allowed) and path.is_file():
            output[relative] = path
    return output


def strategy_attachment_refs(item: dict) -> dict[str, str]:
    """按登记顺序返回附件路径及用途，合并对同一原件的重复引用。"""
    refs = {}
    for field in _ATTACHMENTS:
        values = item.get(field) or []
        for relative in ([values] if isinstance(values, str) else values):
            refs.setdefault(relative, field)
    return refs


def strategy_attachment_license(item: dict, relative: str) -> tuple[str | None, bool]:
    """优先使用按原件登记的许可，策略许可提供默认值。"""
    license_text = item.get("attachment_licenses", {}).get(relative, item.get("license"))
    if license_text:
        return license_text, license_text.strip().lower() in _REDISTRIBUTABLE_LICENSES
    # 内置目录的文件均为仓库登记的本地规则、代码或复做说明。
    catalogued = item["statuses"]["evidence_import"] == "catalogued"
    return ("HETU 本地规则与实现" if catalogued else None), catalogued
