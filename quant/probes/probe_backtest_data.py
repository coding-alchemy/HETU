"""二期 01.2 回测数据离线现状清单探查。

默认只读：对定义引用的快照逐表列已有范围、证据状态与缺口，不联网、
不修改快照。`--sample` 显式开启联网小样本（至多 3 只代码、30 次
provider.fetch，重试计入；超限即中止并记录台账），先列出本次将展开
的上游请求。资格结论不由本探查给出，以 prepare_backtest 落盘为准。
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from hetu_quant.backtest_data import STRATEGY_PURPOSES
from hetu_quant.backtest_models import BacktestDefinition, backtest_definition_from_dict
from hetu_quant.data import PURPOSE_TABLES, DataStore
from hetu_quant.models import DataRequest, SnapshotRef

CN = ZoneInfo("Asia/Shanghai")

SAMPLE_MAX_CODES = 3
SAMPLE_MAX_FETCHES = 30

# 缺口对应的免费来源方向（二期设计 §3；仅列方向，不代表已核验权限）
SOURCE_NEEDS = {
    "historical_securities": "含退市证券的证券资料（交易所/中登公开档案），须带公开时点证据",
    "trading_rules": "沪深交易所、中登及官方税费公告的历史生效规则",
    "knowledge_evidence": "每条历史记录的原始出处与公开时点原件",
    "financial_reports": "原公告及修订原件（仅东财阶梯或最新财报不足）",
    "corporate_actions": "按实施日（除息日）口径的公司行动记录与查询覆盖证明",
    "calendar": "交易日历",
    "daily_bars": "日线行情（含退市期间）",
    "security_status": "ST/停牌/上市状态历史",
    "market_cap": "历史市值",
}


class SampleCapExceeded(RuntimeError):
    """小样本取数超过既定上限。"""


class _CountingProvider:
    """计数包装：记录每次 fetch（含失败重试），超过上限即中止。"""

    def __init__(self, inner: object, ledger: list[dict[str, object]], cap: int) -> None:
        self._inner = inner
        self._ledger = ledger
        self._cap = cap
        self.name = getattr(inner, "name", type(inner).__name__)

    def fetch(
        self, product_id: str, codes: tuple[str, ...], start: date, end: date
    ) -> pd.DataFrame:
        entry = {
            "provider": self.name,
            "product_id": product_id,
            "codes": list(codes),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "seq": len(self._ledger) + 1,
        }
        self._ledger.append(entry)
        if len(self._ledger) > self._cap:
            raise SampleCapExceeded(f"fetch 次数超过上限 {self._cap}")
        try:
            frame = self._inner.fetch(product_id, codes, start, end)  # type: ignore[attr-defined]
        except Exception as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
            raise
        entry["rows"] = len(frame)
        return frame

    def __getattr__(self, item: str) -> object:
        return getattr(self._inner, item)


def _table_inventory(store: DataStore, ref: SnapshotRef, table: str) -> dict[str, object]:
    frame = store.read_table(ref, table)
    info: dict[str, object] = {"rows": int(len(frame))}
    if len(frame) and "date" in frame.columns:
        dates = sorted(frame["date"].astype(str))
        info["coverage"] = [dates[0], dates[-1]]
    if not len(frame):
        info["note"] = "empty"
        return info
    if table == "historical_securities":
        codes = frame["code"].astype(str)
        info["codes"] = int(codes.nunique())
        info["delisted_records"] = int(
            ((frame["valid_to"].astype(str) != "") | ~frame["listed"].map(
                lambda v: str(v).strip().lower() in ("true", "1")
            )).sum()
        )
    elif table == "knowledge_evidence":
        verified = frame["verified"].map(lambda v: str(v).strip().lower() in ("true", "1"))
        info["verified"] = int(verified.sum())
        info["unverified"] = int((~verified).sum())
        info["missing_published_at"] = int((frame["published_at"].astype(str) == "").sum())
    elif table == "trading_rules":
        info["markets"] = sorted(set(frame["market"].astype(str)))
        info["boards"] = sorted(set(frame["board"].astype(str)))
    return info


def _sample_fetch(
    store: DataStore,
    definition: BacktestDefinition,
    purpose: str,
    codes: tuple[str, ...],
) -> dict[str, object]:
    """显式授权的小样本联网取数；计数强制上限，台账随结果返回。"""
    from hetu_quant.providers import default_providers

    ledger: list[dict[str, object]] = []
    providers = {
        name: _CountingProvider(provider, ledger, SAMPLE_MAX_FETCHES)
        for name, provider in default_providers().items()
    }
    sample_store = DataStore(
        store.root / "samples" / datetime.now(tz=CN).strftime("%Y%m%dT%H%M%S"),
        providers=providers,
    )
    request = DataRequest(
        product_ids=tuple(store.config.get("products", {}).keys()),
        codes=codes,
        start=definition.start,
        end=definition.end,
        as_of=definition.as_of,
        purpose=purpose,
        mode="free",
    )
    outcome: dict[str, object] = {"codes": list(codes), "fetch_ledger": ledger}
    try:
        ref = sample_store.prepare(request)
        outcome["snapshot_id"] = ref.snapshot_id
    except SampleCapExceeded as exc:
        outcome["aborted"] = str(exc)
    except Exception as exc:
        outcome["error"] = f"{type(exc).__name__}: {exc}"
    outcome["fetch_count"] = len(ledger)
    return outcome


def probe_backtest_data(
    store: DataStore,
    definition: BacktestDefinition,
    output_dir: Path,
    *,
    allow_sample: bool = False,
) -> dict[str, object]:
    strategy_id = definition.strategy_id
    if strategy_id not in STRATEGY_PURPOSES:
        raise ValueError(f"unknown strategy_id for backtest probe: {strategy_id!r}")
    purpose = STRATEGY_PURPOSES[strategy_id]
    required = PURPOSE_TABLES[purpose]

    ref = SnapshotRef(snapshot_id=definition.snapshot_id, root=store.root)
    warmup_months = int(definition.protocol["warmup_months"])
    required_start = str(
        pd.Period(definition.start.isoformat(), freq="M") - warmup_months - 1
    )
    required_start = f"{required_start}-01"
    required_end = definition.end.isoformat()
    gaps: list[str] = []
    tables: dict[str, object] = {}
    snapshot_dir = store.root / "snapshots" / ref.snapshot_id
    if not snapshot_dir.exists():
        gaps.append(f"missing_snapshot: {ref.snapshot_id}")
        for table in required:
            gaps.append(f"missing_table: {table}")
    else:
        manifest = store.load_manifest(ref)
        present = {str(entry["table"]) for entry in manifest.get("table_files", [])}
        for table in required:
            if table not in present:
                gaps.append(f"missing_table: {table}")
                continue
            tables[table] = _table_inventory(store, ref, table)
            info = tables[table]
            if isinstance(info, dict) and info.get("rows") == 0:
                gaps.append(f"empty_table: {table}")
            coverage = info.get("coverage") if isinstance(info, dict) else None
            if (
                isinstance(coverage, list)
                and (coverage[0] > required_start or coverage[1] < required_end)
            ):
                gaps.append(
                    f"window_shortfall: {table} 覆盖 {coverage[0]}..{coverage[1]}，"
                    f"需要 {required_start}..{required_end}"
                )
        knowledge = tables.get("knowledge_evidence")
        if isinstance(knowledge, dict):
            if knowledge.get("unverified"):
                gaps.append(f"unverified_evidence: {knowledge['unverified']} 条未核验")
            if knowledge.get("missing_published_at"):
                gaps.append(
                    f"evidence_without_published_at: {knowledge['missing_published_at']} 条无公开时点"
                )
        hist = tables.get("historical_securities")
        if isinstance(hist, dict) and not hist.get("delisted_records"):
            gaps.append("historical_securities 无退市记录：不能证明覆盖已退市对象")

    sources_needed = sorted(
        {
            gap.split(":", 1)[1].strip().split(" ")[0]
            for gap in gaps
            if gap.startswith(("missing_table:", "empty_table:", "window_shortfall:"))
        }
        & set(SOURCE_NEEDS)
    )

    inventory: dict[str, object] = {
        "snapshot_id": ref.snapshot_id,
        "strategy_id": strategy_id,
        "purpose": purpose,
        "required_tables": list(required),
        "required_window": [required_start, required_end],
        "tables": tables,
        "gaps": gaps,
        "sources_needed": {name: SOURCE_NEEDS[name] for name in sources_needed},
        "sample": None,
        "probed_at": datetime.now(tz=CN).isoformat(),
    }

    if allow_sample:
        candidate_codes: tuple[str, ...] = ()
        hist_info = tables.get("historical_securities")
        if isinstance(hist_info, dict) and hist_info.get("rows"):
            frame = store.read_table(ref, "historical_securities")
            candidate_codes = tuple(sorted(set(frame["code"].astype(str)))[:SAMPLE_MAX_CODES])
        inventory["sample"] = (
            _sample_fetch(store, definition, purpose, candidate_codes)
            if candidate_codes
            else {"skipped": "无可用代码，未发起取数"}
        )

    output_dir = Path(output_dir)
    if output_dir.exists():
        raise FileExistsError(f"output dir already exists: {output_dir}")
    output_dir.mkdir(parents=True)
    (output_dir / "backtest-data-inventory.json").write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return inventory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="回测数据离线现状清单（默认不联网）")
    parser.add_argument("--definition", required=True, help="回测定义 JSON")
    parser.add_argument("--data-root", required=True, help="数据根目录（DataStore root）")
    parser.add_argument("--out", required=True, help="输出目录（必须为新目录）")
    parser.add_argument(
        "--sample",
        action="store_true",
        help=f"联网小样本（至多 {SAMPLE_MAX_CODES} 只代码、{SAMPLE_MAX_FETCHES} 次 fetch）",
    )
    args = parser.parse_args(argv)
    definition: BacktestDefinition = backtest_definition_from_dict(
        json.loads(Path(args.definition).read_text(encoding="utf-8"))
    )
    inventory = probe_backtest_data(
        DataStore(Path(args.data_root)),
        definition,
        Path(args.out),
        allow_sample=bool(args.sample),
    )
    print(json.dumps({"gaps": inventory["gaps"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
