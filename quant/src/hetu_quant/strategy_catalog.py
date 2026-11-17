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
# 旧台账在未发布时只登记候选／基准角色，归属来自二期冻结协议。
_PHASE2_TRIAL_STRATEGIES = {"candidate": "low_volatility", "benchmark": "universe_vw"}


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


def _trial_evidence(root: Path, study_id: str, trial: dict) -> tuple[str | None, dict]:
    strategy_id = trial.get("strategy_id")
    run_id = trial.get("run_id")
    if isinstance(run_id, str) and re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
        run_dir = root / "backtests" / run_id
        path = run_dir / "backtest.json"
        if path.resolve().is_relative_to(run_dir) and path.is_file():
            manifest = json.loads(path.read_text(encoding="utf-8"))
            actual_strategy = manifest["definition"]["strategy_id"]
            research = manifest.get("research")
            if (manifest.get("run_id") != run_id or manifest.get("status") not in ("complete", "limited")
                    or (strategy_id and strategy_id != actual_strategy)
                    or (research is not None and (research.get("study_id") != study_id
                        or research.get("trial_id") != trial.get("trial_id")))):
                return None, {}
            path = run_dir / "evaluation.json"
            evaluation = (json.loads(path.read_text(encoding="utf-8"))
                          if path.resolve().is_relative_to(run_dir) and path.is_file() else {})
            return actual_strategy, evaluation
    if strategy_id is None and study_id == "phase2-lowvol":
        strategy_id = _PHASE2_TRIAL_STRATEGIES.get(trial.get("kind"))
    return strategy_id, {}


def _explicit_states(record: dict) -> dict:
    return {key: record[key] for key in _STATUSES if record.get(key)}


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
    source = manifest_path.parent.resolve()
    attachments = {}
    for field in _ATTACHMENTS:
        values = item.get(field) or []
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            raise ValueError(f"invalid attachments: {field}")
        for value in values:
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
    if item["statuses"]["evidence_import"] == "catalogued":
        source = _INDEX.parent
        allowed = source.parent
    else:
        source = Path(root).resolve() / "strategy-assets" / _identifier(item["strategy_id"])
        allowed = source
    output = {}
    for field in _ATTACHMENTS:
        values = item.get(field) or []
        for relative in ([values] if isinstance(values, str) else values):
            path = (source / relative).resolve()
            if path.is_relative_to(allowed) and path.is_file() and path.suffix in (".md", ".json", ".py", ".ipynb"):
                output[relative] = path
    return output
