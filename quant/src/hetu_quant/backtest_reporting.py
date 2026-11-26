"""二期 04 回测只读报告：读取、导出 ZIP 与并列比较已发布 BacktestRef 产物。

只读 `backtests/<run_id>/` 已发布文件：不修改 evaluation、不重跑、不补算；
导出的表值直接来自已发布 Parquet，空表导出头行。比较读取原定义、账户轨迹、
评价和试验台账。股票池、区间、执行口径或基准不同则不混排。
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd

from hetu_quant.backtest import TABLE_COLUMNS
from hetu_quant.backtest_models import BacktestRef

# 不兼容口径：任一不同即不可混排（differences 仍逐项显示）
_INCOMPATIBLE_PROTOCOL_FIELDS = ("universe", "benchmark_id", "execution")

# 兼容但需标明的定义字段（同池故意改成本仍可并列比较）
_COMPARABLE_DEFINITION_FIELDS = ("start", "end")


def _run_dir(ref: BacktestRef) -> Path:
    return Path(ref.root) / "backtests" / ref.run_id


def _load_manifest(run_dir: Path, run_id: str) -> dict[str, Any]:
    manifest_path = run_dir / "backtest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"backtest manifest missing: {run_id}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def publish_research_handoff(root: Path, study_id: str, release_id: str) -> Path:
    """Publish one immutable ZIP from an existing ledger and its published runs."""
    from hetu_quant.strategy_catalog import (
        read_strategy_asset,
        research_trial_strategy,
        strategy_attachment_license,
        strategy_attachment_refs,
        strategy_attachments,
    )

    if not all(re.fullmatch(r"[A-Za-z0-9_-]+", name) for name in (study_id, release_id)):
        raise ValueError("invalid study or release identifier")
    root = Path(root).resolve()
    study = root / "research" / study_id
    if not study.resolve().is_relative_to(root):
        raise ValueError("study resolves outside the research root")
    ledger = (study / "trials.jsonl").read_bytes()
    rows = [json.loads(line) for line in ledger.splitlines() if line.strip()]
    if not rows:
        raise ValueError("the original trial ledger is empty")
    target = study / "releases" / f"{release_id}.zip"
    if not target.resolve().is_relative_to(study) or target.exists():
        raise FileExistsError(target)
    target.parent.mkdir(exist_ok=True)
    index = dict(study_id=study_id, release_id=release_id, runs={}, files=[], attachments=[], limitations=[])
    strategies = set()
    with tempfile.NamedTemporaryFile(dir=target.parent) as temporary:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            def include(path, destination, *, required=False):
                if path.is_file() and path.resolve().is_relative_to(root):
                    archive.write(path, destination)
                    index["files"].append(destination)
                elif required:
                    index["limitations"].append(f"missing published file: {destination}")

            archive.writestr("trials.jsonl", ledger)
            for name in ("source-uses.jsonl", "final-entry.json", "study-report.md"):
                include(study / name, name, required=name == "study-report.md")
            for row in rows:
                owner = research_trial_strategy(study_id, row)
                if owner:
                    strategies.add(owner)
                ident = row.get("run_id")
                if row.get("status") not in ("complete", "blocked") or not isinstance(ident, str):
                    continue
                if not re.fullmatch(r"[A-Za-z0-9_-]+", ident):
                    raise ValueError(f"invalid run reference: {ident!r}")
                directory = root / "backtests" / ident
                if not (directory / "backtest.json").resolve().is_relative_to(directory):
                    raise ValueError(f"run resolves outside the account root: {ident!r}")
                if not (directory / "backtest.json").is_file():
                    index["limitations"].append(f"{row.get('trial_id')}: missing account {ident}")
                    continue
                account = _load_manifest(directory, ident)
                owner = research_trial_strategy(study_id, row, account)
                if ident in index["runs"]:
                    continue
                index["runs"][ident] = dict(trial_id=row.get("trial_id"), account_status=account.get("status"),
                                            strategy_id=owner)
                strategies.add(owner)
                names = [
                    "backtest.json", "evaluation.json", "report.md", "factor-labels.parquet",
                    "bundle/signals.parquet", "bundle/qualification.json",
                    *(f"{name}.parquet" for name in TABLE_COLUMNS),
                ]
                for name in names:
                    include(directory / name, f"runs/{ident}/{name}", required=name != "factor-labels.parquet")
            for strategy in sorted(strategies):
                try:
                    asset = read_strategy_asset(root, strategy)
                except KeyError:
                    index["limitations"].append(f"strategy attachment inventory missing: {strategy}")
                    continue
                attachments = strategy_attachments(root, asset)
                source_refs = asset.get("source_refs") or []
                catalogued = asset["statuses"]["evidence_import"] == "catalogued"
                for relative, field in strategy_attachment_refs(asset).items():
                    path = attachments.get(relative)
                    license_text, can_export = strategy_attachment_license(asset, relative)
                    location = f"quant/research/{relative}" if catalogued else f"策略目录 {strategy} / {relative}"
                    record = dict(strategy_id=strategy, scope=relative, field=field, source_refs=source_refs,
                                  license=license_text, acquisition=[location, *source_refs])
                    if not can_export:
                        record["restriction"] = license_text or "未登记附件交付许可"
                    elif path is None:
                        record["restriction"] = "缺少已登记的本地原件"
                        index["limitations"].append(f"missing strategy attachment: {strategy}/{relative}")
                    else:
                        member = Path(relative)
                        archive_name = member.name if ".." in member.parts else member.as_posix()
                        destination = f"attachments/{strategy}/{archive_name}"
                        archive.write(path, destination)
                        index["files"].append(destination)
                        record["file"] = destination
                    index["attachments"].append(record)
            archive.writestr("manifest.json", json.dumps(index, ensure_ascii=False))
        temporary.flush()
        os.link(temporary.name, target)
    return target


def read_research_handoff(archive: Path) -> dict[str, Any]:
    """Read the exact evidence made available to ZIP, web and Notebook users."""
    with zipfile.ZipFile(archive) as bundle:
        index = json.loads(bundle.read("manifest.json"))
        files = set(index["files"])
        rows = [json.loads(line) for line in bundle.read("trials.jsonl").splitlines() if line.strip()]
        runs = {}
        for run_id in index["runs"]:
            prefix = f"runs/{run_id}/"
            evaluation = prefix + "evaluation.json"
            run = dict(evaluation=json.loads(bundle.read(evaluation)) if evaluation in files else {})
            for table in ("nav", "costs"):
                name = prefix + f"{table}.parquet"
                if name in files:
                    frame = pd.read_parquet(BytesIO(bundle.read(name)))
                    run[table] = frame.astype(object).where(frame.notna(), None).to_dict("records")
            runs[run_id] = run
        report = bundle.read("study-report.md").decode("utf-8") if "study-report.md" in files else None
        return dict(manifest=index, trials=rows, report=report, runs=runs)


def read_research_handoff_artifact(archive: Path, name: str) -> bytes:
    """Read one explicitly registered artifact without extracting or running it."""
    with zipfile.ZipFile(archive) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
        if name not in manifest["files"]:
            raise KeyError(name)
        return bundle.read(name)


def research_handoff_summary(archive: Path, payload: dict, strategy_id: str) -> dict:
    """从所选发布物的台账和评价提取各试验状态。"""
    from hetu_quant.strategy_catalog import research_trial_states, research_trial_strategy

    index = payload["manifest"]
    own_trials = {}
    development_validation = []
    latest_failure = None
    for trial in payload["trials"]:
        trial_id = trial.get("trial_id")
        run_id = trial.get("run_id")
        run = index["runs"].get(run_id, {})
        attached = bool(run) and run.get("trial_id") == trial_id
        owner = run.get("strategy_id") if attached else None
        if attached and owner is None:
            # 旧版本清单的归属可从随包归档的原账户定义读取。
            name = f"runs/{run_id}/backtest.json"
            if name in index["files"]:
                account = json.loads(read_research_handoff_artifact(archive, name))
                owner = account.get("definition", {}).get("strategy_id")
        if owner is None:
            owner = research_trial_strategy(index["study_id"], trial)
        evaluation = payload["runs"][run_id]["evaluation"] if attached else {}
        status = {
            "trial_id": trial_id, "run_id": run_id, "status": trial.get("status"),
            **research_trial_states(trial, evaluation),
        }
        if trial.get("detail"):
            status["detail"] = trial["detail"]
        if (trial.get("phase") == "development_validation"
                or (isinstance(trial_id, str) and trial_id.startswith("dev_"))):
            development_validation.append(status)
        if owner == strategy_id:
            own_trials.pop(trial_id, None)
            own_trials[trial_id] = status
        if trial.get("status") == "failed":
            latest_failure = status

    unknown = research_trial_states({}, {})
    if index["study_id"] != "phase2-lowvol" or strategy_id != "low_volatility":
        return {"statuses": next(reversed(own_trials.values()), unknown)}
    final_main = own_trials.get("final_main")
    summary = {
        "statuses": final_main or {
            **unknown, "trial_id": "final_main", "status": "missing", "economic_status": "not_evaluated",
        },
        "diagnostics": [own_trials[key] for key in ("final_double_costs", "final_capacity") if key in own_trials],
        "latest_failure": latest_failure,
    }
    if final_main is None:
        summary["development_validation"] = development_validation
    return summary


def read_backtest(ref: BacktestRef) -> dict[str, Any]:
    """只读重开已发布回测：backtest.json、evaluation.json 与九张轨迹表。"""
    run_dir = _run_dir(ref)
    manifest = _load_manifest(run_dir, ref.run_id)
    evaluation_path = run_dir / "evaluation.json"
    evaluation = (
        json.loads(evaluation_path.read_text(encoding="utf-8"))
        if evaluation_path.exists()
        else {}
    )
    tables = {
        name: pd.read_parquet(run_dir / f"{name}.parquet") for name in TABLE_COLUMNS
    }
    return {"manifest": manifest, "evaluation": evaluation, "tables": tables}


def export_backtest(ref: BacktestRef, destination: Path) -> Path:
    """把 ``backtests/<run_id>/`` 的已发布产物导出为 ZIP，返回 ZIP 路径。

    ``destination`` 为目录时写入 ``<run_id>.zip``；逐表读自已发布 Parquet，
    与发布值一致，空表可导出；不修改 evaluation、不重新运行。
    """
    run_dir = _run_dir(ref)
    manifest = _load_manifest(run_dir, ref.run_id)
    destination = Path(destination)
    zip_path = (
        destination
        if destination.suffix == ".zip"
        else destination / f"{ref.run_id}.zip"
    )
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(run_dir / "backtest.json", "backtest.json")
        zf.writestr("engine.json", json.dumps(manifest.get("engine", {}), ensure_ascii=False, indent=2))
        # 保留发布时的原表字节、标签和资格/制度依据；CSV仍供表格工具直接阅读。
        for path in sorted(run_dir.glob("*.parquet")):
            zf.write(path, path.name)
        for relative in ("qualification.json", "universe.parquet", "sources.json",
                         "tables/trading_rules.parquet"):
            path = run_dir / "bundle" / relative
            if path.exists():
                zf.write(path, f"bundle/{relative}")
        research = manifest.get("research")
        if research:
            zf.writestr("research.json", json.dumps(research, ensure_ascii=False, indent=2))
            study_id = str(research.get("study_id", ""))
            if re.fullmatch(r"[A-Za-z0-9_-]+", study_id):
                for name in ("trials.jsonl", "source-uses.jsonl", "final-entry.json"):
                    record = Path(ref.root) / "research" / study_id / name
                    if record.exists():
                        zf.write(record, f"research/{name}")
        snapshot_id = str(manifest.get("sources", {}).get("snapshot_id", ""))
        if snapshot_id and Path(snapshot_id).name == snapshot_id:
            snapshot = Path(ref.root) / "snapshots" / snapshot_id / "manifest.json"
            if snapshot.exists():
                zf.write(snapshot, "snapshot-manifest.json")
        zf.writestr(
            "definition.json",
            json.dumps(manifest["definition"], ensure_ascii=False, indent=2),
        )
        zf.writestr(
            "sources.json",
            json.dumps(manifest["sources"], ensure_ascii=False, indent=2),
        )
        zf.writestr(
            "limitations.json",
            json.dumps(manifest.get("limitations", []), ensure_ascii=False, indent=2),
        )
        evaluation_path = run_dir / "evaluation.json"
        if evaluation_path.exists():
            zf.writestr("evaluation.json", evaluation_path.read_text(encoding="utf-8"))
        report_path = run_dir / "report.md"
        if report_path.exists():
            zf.writestr("report.md", report_path.read_text(encoding="utf-8"))
        for name in TABLE_COLUMNS:
            table_path = run_dir / f"{name}.parquet"
            if not table_path.exists():
                raise FileNotFoundError(f"backtest table missing: {name}")
            frame = pd.read_parquet(table_path)
            zf.writestr(f"{name}.csv", frame.to_csv(index=False))
    return zip_path


def _describe(field: str, value_a: Any, value_b: Any) -> str:
    return f"{field}: {value_a!r} ↔ {value_b!r}"


def _actual_sample(ref: BacktestRef) -> set[tuple[str, str]] | None:
    path = _run_dir(ref) / "bundle" / "universe.parquet"
    if not path.is_file():
        return None
    try:
        rows = pd.read_parquet(path, columns=["signal_date", "code"])
    except (OSError, ValueError, KeyError):
        return None
    if rows.empty or rows.isna().any().any():
        return None
    return set(rows.astype(str).itertuples(index=False, name=None))


def _published_table(run: Path, name: str) -> pd.DataFrame | None:
    path = run / f"{name}.parquet"
    if not path.is_file():
        return None
    try:
        return pd.read_parquet(path)
    except (OSError, ValueError, KeyError):
        return None


def _nav_values(rows: pd.DataFrame | None, column: str) -> dict[str, float] | None:
    if rows is None or not {"date", column}.issubset(rows.columns):
        return None
    try:
        dates = pd.to_datetime(rows["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        values = pd.to_numeric(rows[column], errors="coerce")
    except (ValueError, TypeError, AttributeError):
        return None
    if rows.empty or dates.isna().any() or dates.duplicated().any():
        return None
    if values.isna().any() or not values.map(math.isfinite).all():
        return None
    return dict(zip(dates, values, strict=True))


def _sample_counts(sample: set[tuple[str, str]] | None) -> dict[str, int] | None:
    if sample is None:
        return None
    return dict(security_signal_dates=len(sample), signal_dates=len({day for day, _ in sample}),
                securities=len({code for _, code in sample}))


def _ledger_rows(path: Path, research: Path) -> list[dict]:
    if not path.is_file() or not path.resolve().is_relative_to(research.resolve()):
        return []
    return [json.loads(line) for line in path.read_bytes().splitlines() if line.strip()]


def _trial_counts(ref: BacktestRef, manifest: dict) -> dict:
    protocol = manifest.get("definition", {}).get("protocol", {})
    research = Path(ref.root) / "research"
    representative = protocol.get("representative_execution") or {}
    submission = representative.get("submission_id")
    context = manifest.get("research") or protocol.get("research_execution") or {}
    study_id = context.get("study_id")
    trial_id = context.get("trial_id")
    representatives = research / "representative-runs"
    if isinstance(submission, str) and re.fullmatch(r"[A-Za-z0-9_-]+", submission):
        attempts = [representatives / submission / "attempt.jsonl"]
    elif not context:
        # 独立获准的基准清单没有代表任务参数，按产物编号查其执行登记。
        attempts = sorted(representatives.glob("*/attempt.jsonl"))
    else:
        attempts = []
    representative_matches = []
    for path in attempts:
        rows = _ledger_rows(path, research)
        if rows and rows[-1].get("run_id") == ref.run_id:
            representative_matches.append(dict(
                submission_id=path.parent.name, study_id=rows[0].get("study_id"), role=rows[0].get("role"),
                approved_run_count=rows[0].get("approved_run_count"),
                actual_run_count=rows[-1].get("actual_run_count"),
            ))
    if len(representative_matches) == 1:
        return representative_matches[0]
    if submission:
        return dict(submission_id=submission, approved_run_count=None, actual_run_count=None)

    if isinstance(study_id, str) and re.fullmatch(r"[A-Za-z0-9_-]+", study_id):
        ledgers = [research / study_id / "trials.jsonl"]
    else:
        # 旧独立脚本没有写入 research，使用台账的 run_id 追溯归属。
        ledgers = sorted(research.glob("*/trials.jsonl"))
    matches = []
    for path in ledgers:
        rows = _ledger_rows(path, research)
        own = next((row for row in rows if row.get("run_id") == ref.run_id
                    and (trial_id is None or trial_id == row.get("trial_id"))), None)
        if own is not None:
            run_count = sum(row.get("status") not in (None, "refused", "refused_budget", "reused")
                            and not row.get("reused") for row in rows)
            matches.append((path.parent.name, own.get("trial_id"), run_count))
    if len(matches) == 1:
        study_id, trial_id, count = matches[0]
    else:
        count = None
    return dict(study_id=study_id, trial_id=trial_id, actual_run_count=count,
                approved_run_count=protocol.get("trial_budget", {}).get("max_runs"))


def _comparison_evidence(ref: BacktestRef, manifest: dict, sample, nav, net_nav, common_end) -> dict:
    directory = _run_dir(ref)
    definition = manifest.get("definition", {})
    protocol = definition.get("protocol", {})
    evaluation_path = directory / "evaluation.json"
    evaluation = json.loads(evaluation_path.read_bytes()) if evaluation_path.is_file() else {}
    resolved = manifest.get("resolved_inputs", {}).get("benchmark") or {}
    gross_nav = _nav_values(nav, "gross_nav")
    costs = _published_table(directory, "costs")
    by_type = None
    if costs is not None and {"cost_type", "amount"}.issubset(costs.columns):
        amounts = pd.to_numeric(costs["amount"], errors="coerce")
        if not amounts.isna().any() and amounts.map(math.isfinite).all() and costs["cost_type"].notna().all():
            by_type = {str(kind): float(total) for kind, total in amounts.groupby(costs["cost_type"]).sum().items()}
    return dict(
        run_id=ref.run_id,
        window=dict(start=definition.get("start"), end=definition.get("end")),
        actual_sample=_sample_counts(sample),
        benchmark={"benchmark_id": protocol.get("benchmark_id"), **resolved},
        costs=dict(assumptions=protocol.get("costs"), actual_by_type=by_type,
                   actual_total=sum(by_type.values()) if by_type is not None else None),
        trials=_trial_counts(ref, manifest),
        uncertainty=dict(statistics=evaluation.get("statistics"), reasons=evaluation.get("reasons"),
                         limitations=evaluation.get("limitations"), account_limitations=manifest.get("limitations")),
        last_nav_date=max(net_nav) if net_nav else None,
        ending_net_nav=net_nav[max(net_nav)] if net_nav else None,
        common_end_net_nav=net_nav.get(common_end) if net_nav else None,
        common_end_gross_nav=gross_nav.get(common_end) if gross_nav else None,
    )


def compare_backtests(ref_a: BacktestRef, ref_b: BacktestRef) -> dict[str, Any]:
    """并列比较两个已发布回测的定义口径、实际历史样本及正式基准。

    仅从已发布净值、费用、评价与台账展示证据。同池成本变化可并列展示，
    样本、区间、执行口径或正式基准不同则限制混排。
    """
    manifest_a = _load_manifest(_run_dir(ref_a), ref_a.run_id)
    manifest_b = _load_manifest(_run_dir(ref_b), ref_b.run_id)
    definition_a = manifest_a.get("definition", {})
    definition_b = manifest_b.get("definition", {})
    protocol_a = definition_a.get("protocol", {})
    protocol_b = definition_b.get("protocol", {})

    differences: list[str] = []
    incompatible = False
    sample_a, sample_b = _actual_sample(ref_a), _actual_sample(ref_b)
    nav_a, nav_b = (_published_table(_run_dir(ref), "nav") for ref in (ref_a, ref_b))
    net_a, net_b = (_nav_values(nav, "net_nav") for nav in (nav_a, nav_b))
    common_dates = sorted(net_a.keys() & net_b.keys()) if net_a and net_b else []
    common_end = common_dates[-1] if common_dates else None
    for side, ref, net in (("left", ref_a, net_a), ("right", ref_b, net_b)):
        if net is None:
            differences.append(f"actual_account_nav: {side} ({ref.run_id}) 已发布账户净值缺失或无效")
            incompatible = True
    if sample_a is None or sample_b is None:
        differences.append("actual_historical_universe: 已发布样本证据不足")
        incompatible = True
    elif sample_a != sample_b:
        differences.append(
            f"actual_historical_universe: 仅本结果 {len(sample_a - sample_b)} 个对象日，"
            f"仅对比结果 {len(sample_b - sample_a)} 个对象日"
        )
        incompatible = True
    if "formal" in (protocol_a.get("stage"), protocol_b.get("stage")):
        benchmark_a, benchmark_b = (_nav_values(nav, "benchmark_nav") for nav in (nav_a, nav_b))
        if benchmark_a is None or benchmark_b is None:
            differences.append("actual_benchmark_nav: 已发布基准日期或净值证据不足")
            incompatible = True
        elif benchmark_a != benchmark_b:
            differences.append("actual_benchmark_nav: 已发布基准日期或净值不同")
            incompatible = True
    for field in _COMPARABLE_DEFINITION_FIELDS:
        if definition_a.get(field) != definition_b.get(field):
            differences.append(
                _describe(f"definition.{field}", definition_a.get(field), definition_b.get(field))
            )
            incompatible = True
    for field in _INCOMPATIBLE_PROTOCOL_FIELDS:
        value_a = protocol_a.get(field)
        value_b = protocol_b.get(field)
        if value_a != value_b:
            differences.append(_describe(f"protocol.{field}", value_a, value_b))
            incompatible = True
    for field in ("costs", "warmup_months", "initial_cash_cny", "rebalance"):
        value_a = protocol_a.get(field)
        value_b = protocol_b.get(field)
        if value_a != value_b:
            differences.append(
                _describe(f"protocol.{field}", value_a, value_b) + "（同池可比，已标明变更）"
            )
    parameters_a = definition_a.get("parameters", {})
    parameters_b = definition_b.get("parameters", {})
    if parameters_a != parameters_b:
        differences.append(
            _describe("definition.parameters", parameters_a, parameters_b)
            + "（同池可比，已标明变更）"
        )
    left = _comparison_evidence(ref_a, manifest_a, sample_a, nav_a, net_a, common_end)
    right = _comparison_evidence(ref_b, manifest_b, sample_b, nav_b, net_b, common_end)
    return {
        "differences": differences,
        "mixed_ranking_allowed": not incompatible,
        "common_interval": dict(start=common_dates[0] if common_dates else None, end=common_end,
                                nav_dates=len(common_dates) if net_a is not None and net_b is not None else None),
        "common_sample": _sample_counts(sample_a & sample_b) if sample_a is not None and sample_b is not None else None,
        "left": left,
        "right": right,
        "common_end_net_nav_difference": (right["common_end_net_nav"] - left["common_end_net_nav"]
                                         if common_end is not None else None),
    }
