"""二期 04 回测只读报告：读取、导出 ZIP 与并列比较已发布 BacktestRef 产物。

只读 `backtests/<run_id>/` 已发布文件：不修改 evaluation、不重跑、不补算；
导出的表值直接来自已发布 Parquet，空表导出头行。比较只处理既有产物的
清单定义：股票池/区间/执行口径/基准不同则不混排（mixed_ranking_allowed
=False），同池的成本等变更可并列比较并逐项标明差异。
"""

from __future__ import annotations

import json
import re
import zipfile
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


def compare_backtests(ref_a: BacktestRef, ref_b: BacktestRef) -> dict[str, Any]:
    """并列比较两个已发布回测的定义口径，不读取或改写表值。

    返回 ``{"differences": list[str], "mixed_ranking_allowed": bool}``：
    股票池（universe）、区间（start/end）、执行口径（execution，含信号/
    成交时钟）或基准不同 → 差异列出且 ``mixed_ranking_allowed=False``（不
    混排）；其余差异（如成本、预热月数、参数）在同池下可并列比较并标明。
    """
    manifest_a = _load_manifest(_run_dir(ref_a), ref_a.run_id)
    manifest_b = _load_manifest(_run_dir(ref_b), ref_b.run_id)
    definition_a = manifest_a.get("definition", {})
    definition_b = manifest_b.get("definition", {})
    protocol_a = definition_a.get("protocol", {})
    protocol_b = definition_b.get("protocol", {})

    differences: list[str] = []
    incompatible = False
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
    return {
        "differences": differences,
        "mixed_ranking_allowed": not incompatible,
    }
