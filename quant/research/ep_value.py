"""E/P 价值条件研究脚本：只读时点资格合格的历史财务版本。

信号为最近已报告扣非净利润 / 信号时点可知总市值，按月末排序；
不产生目标权重，不解释为可实现收益。免费历史财务时点不合格即受阻，
不以当前 PE/TTM 静默替代，不用当前财务回填历史。

CLI 参数与 low_volatility.py 相同。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from hetu_quant.data import AccessDenied

CN = ZoneInfo("Asia/Shanghai")
PURPOSE = "ep_value_preparation"
SMALLEST_EXCLUDE_RATIO = 0.3

EXTERNAL_EVIDENCE = {
    "source": "Liu, Stambaugh, Yuan (2019), Size and Value in China",
    "scope": "原文样本 2000–2016 A 股；原文结论为因子层面证据，不当作多头净收益",
    "url": "https://rodneywhitecenter.wharton.upenn.edu/wp-content/uploads/2019/04/01-18-Stambaugh.Published.BlumePrize.pdf",
}

ADAPTATIONS = [
    "universe: 保留原文剔除最小市值 30% 规则，按当时可知总市值实现",
    "earnings: 只用公告时点不晚于截止的历史版本；晚修订不覆盖过去已知值",
    "direction: 一期只输出 E/P 排序信号，不产生目标权重",
]

UNTESTED = [
    "成本/换手与成交冲击",
    "样本外与压力检验",
    "净收益与经济评价（二期，protocol.json 为 preparation_only）",
]


def _report(
    quality: object,
    excluded: dict[str, list[str]],
    executed: list[str],
    research_status: str,
    signal_date: date,
    snapshot_id: str,
    gaps: list[str] | None = None,
) -> dict:
    qualification = (
        {
            "can_run": quality.can_run,
            "allowed_uses": list(quality.allowed_uses),
            "limitations": list(quality.limitations),
            "qualify_excluded": {k: list(v) for k, v in quality.excluded.items()},
        }
        if quality is not None
        else {"can_run": False, "allowed_uses": [], "limitations": [], "qualify_excluded": {}}
    )
    qualification["gaps"] = gaps or []
    return {
        "experiment": "ep_value",
        "external_evidence": EXTERNAL_EVIDENCE,
        "adaptations": ADAPTATIONS,
        "data_qualification": qualification,
        "excluded": excluded,
        "executed_checks": executed,
        "untested": UNTESTED,
        "research_status": research_status,
        "economic_status": "not_evaluated",
        "signal_date": signal_date.isoformat(),
        "snapshot_id": snapshot_id,
    }


def _write(output_dir: Path, signals: pd.DataFrame | None, report: dict) -> None:
    output_dir.mkdir(parents=True, exist_ok=False)
    if signals is not None:
        signals.to_parquet(output_dir / "signals.parquet", index=False)
    (output_dir / "preparation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def prepare(store, request, snapshot, output_dir) -> dict:
    """按原文盈利期间与扣非净利润/总市值计算月末 E/P；时点不合格即受阻。"""
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise FileExistsError(f"experiment output exists, refusing to overwrite: {output_dir}")
    signal_date = request.end
    if request.as_of.date() != signal_date:
        raise ValueError(
            f"as_of ({request.as_of.date()}) and signal_date ({signal_date}) must be the same market date"
        )

    excluded: dict[str, list[str]] = {}
    executed: list[str] = []
    try:
        quality = store.qualify(request, snapshot)
    except AccessDenied as error:
        report = _report(
            None, excluded, ["access_denied"], "blocked", signal_date,
            snapshot.snapshot_id,
            gaps=[f"ep_value 真实条件测试受阻：财务/市值产品准入未建立（unverified）：{error}"],
        )
        _write(output_dir, None, report)
        return report
    executed.append("qualify")
    if not quality.can_run:
        report = _report(
            quality, excluded, executed, "blocked", signal_date, snapshot.snapshot_id,
            gaps=["ep_value_preparation 用途资格不成立"],
        )
        _write(output_dir, None, report)
        return report

    financials = store.read_table(snapshot, "financial_reports")
    market_cap = store.read_table(snapshot, "market_cap")
    eligible = set(quality.eligible_ids)

    entries: dict[str, dict[str, float]] = {}
    for code in request.codes:
        reasons: list[str] = []
        own = financials[financials["code"] == code] if len(financials) else financials
        available = own["available_at"].astype(str) if len(own) else own
        known = own[available.notna() & (available <= request.as_of.isoformat())] if len(own) else own
        if len(own) and not len(known):
            if (own["available_at"].isna() | (own["available_at"].astype(str) == "")).all():
                reasons.append("no_announcement_time")
            else:
                reasons.append("no_pit_financials")
        profit = None
        if len(known):
            latest_period = known["report_period"].astype(str).max()
            versions = known[known["report_period"].astype(str) == latest_period]
            row = versions.sort_values("available_at").iloc[-1]
            profit = pd.to_numeric(
                pd.Series([row.get("net_profit_ex")]), errors="coerce"
            ).iloc[0]
            if pd.isna(profit):
                reasons.append("missing_profit_field")
        cap = None
        own_cap = market_cap[market_cap["code"] == code] if len(market_cap) else market_cap
        if len(own_cap):
            known_cap = own_cap[
                (own_cap["date"].astype(str) <= signal_date.isoformat())
                & (own_cap["available_at"].astype(str) <= request.as_of.isoformat())
            ]
            if len(known_cap):
                cap_value = pd.to_numeric(
                    pd.Series([known_cap.sort_values("date").iloc[-1]["market_cap_cny"]]),
                    errors="coerce",
                ).iloc[0]
                cap = None if pd.isna(cap_value) else float(cap_value)
        if cap is None:
            reasons.append("no_known_market_cap")
        if reasons:
            excluded[code] = reasons
            continue
        if code not in eligible:
            excluded[code] = ["not_eligible_by_qualify"]
            continue
        entries[code] = {"profit": float(profit), "market_cap": float(cap)}
    executed.append("pit_financial_versions")

    if not entries:
        report = _report(
            quality, excluded, executed, "blocked", signal_date, snapshot.snapshot_id,
            gaps=["无时点资格合格的历史财务版本"],
        )
        _write(output_dir, None, report)
        return report

    # 原文样本规则：剔除最小市值 30%
    drop = int(len(entries) * SMALLEST_EXCLUDE_RATIO)
    by_cap = sorted(entries.items(), key=lambda item: (item[1]["market_cap"], item[0]))
    for code, _ in by_cap[:drop]:
        excluded[code] = ["smallest_30pct_market_cap"]
        del entries[code]
    executed.append("smallest_30pct_excluded")

    signals = pd.DataFrame(
        {
            "code": sorted(entries),
            "signal_date": signal_date.isoformat(),
            "signal": [
                entries[code]["profit"] / entries[code]["market_cap"] for code in sorted(entries)
            ],
            "target_weight": None,
        }
    )
    report = _report(quality, excluded, executed, "signals_ready", signal_date, snapshot.snapshot_id)
    _write(output_dir, signals, report)
    return report


def _products_for_purpose(store) -> tuple[str, ...]:
    from hetu_quant.data import PURPOSE_TABLES

    chosen = []
    for table in PURPOSE_TABLES[PURPOSE]:
        for product_id, product in store.config["products"].items():
            if table in product.get("tables", []):
                chosen.append(product_id)
                break
        else:
            raise ValueError(f"no product serves table {table} in data-root config")
    return tuple(chosen)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="E/P 价值条件研究脚本")
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--signal-date", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    from hetu_quant.data import DataStore
    from hetu_quant.models import DataRequest, SnapshotRef

    data_root = Path(args.data_root)
    store = DataStore(data_root)
    snapshot = SnapshotRef(snapshot_id=args.snapshot_id, root=data_root)
    as_of = datetime.fromisoformat(args.as_of)
    as_of = as_of.replace(tzinfo=CN) if as_of.tzinfo is None else as_of.astimezone(CN)
    signal_date = date.fromisoformat(args.signal_date)
    start = date(signal_date.year - 1, signal_date.month, 1)
    securities = store.read_table(snapshot, "securities")
    if len(securities):
        codes = tuple(
            sorted(
                securities.loc[
                    securities["security_type"].astype(str) == "stock", "code"
                ].astype(str)
            )
        )
    else:
        bars = store.read_table(snapshot, "daily_bars")
        codes = tuple(sorted(bars["code"].astype(str).unique()))
    request = DataRequest(
        product_ids=_products_for_purpose(store),
        codes=codes,
        start=start,
        end=signal_date,
        as_of=as_of,
        purpose=PURPOSE,
    )
    report = prepare(store, request, snapshot, Path(args.out))
    print(
        json.dumps(
            {
                "research_status": report["research_status"],
                "economic_status": report["economic_status"],
                "out": str(args.out),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
