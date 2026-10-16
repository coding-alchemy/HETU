"""低波动多头独立研究脚本：36 个月月收益波动率信号与市值目标权重。

只计算信号及规则指定目标权重；不产生账户成交、净值、成本后收益或样本外结论。
收益口径固定为 01 核验的税前除息日收盘再投资总收益研究序列（close_total_return）；
原始收盘价不拼复权序列，排名价格收益不自动替代本协议。

CLI：
    quant/.venv/bin/python quant/research/low_volatility.py \
      --data-root .hetu/quant --snapshot-id <已合格快照> \
      --as-of "<信息截止时间>" --signal-date <月末交易日> \
      --out .hetu/quant/research/<实验名>
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

CN = ZoneInfo("Asia/Shanghai")
WARMUP_MONTHS = 36
MONTH_END_POINTS = WARMUP_MONTHS + 1
MIN_POOL = 10
RETURN_COLUMN = "close_total_return"
PURPOSE = "low_volatility_preparation"
PURPOSE_TABLES = (
    "calendar",
    "daily_bars",
    "security_status",
    "corporate_actions",
    "market_cap",
)

EXTERNAL_EVIDENCE = {
    "source": "Blitz, Hanauer, van Vliet (2021), The Volatility Effect in China",
    "scope": "原文样本 2000–2018 A 股、美元收益、历史 MSCI 股票池；外部实证不等于本项目已验证",
    "url": "https://link.springer.com/article/10.1057/s41260-021-00218-0",
}

ADAPTATIONS = [
    "universe: 历史 MSCI 股票池/美元 → 信号日历史登记仍上市的 A 股普通股票/CNY（免费数据无法复现原股票池）",
    "return_basis: 原文总收益 → 01 核验的税前除息日收盘再投资研究序列（非账户现金、非税后）",
    "selection: 低波动分组 → 合格池最低十分位 max(1, floor(N/10)) 且 N>=10",
    "signal_date: 原文月末 → 显式选择的已完成月末，收盘数据已可得",
    "ties: 原文未规定 → 同波动按代码稳定截取（边界并列截取另行记录）",
]

UNTESTED = [
    "成本/换手与成交冲击",
    "样本外与压力检验",
    "净收益与经济评价（二期，protocol.json 为 preparation_only）",
]

COMPARE_KEYS = (
    "universe",
    "currency",
    "window",
    "volatility",
    "weighting",
    "rebalance",
    "direction",
)
PROTOCOL_REQUIRED = (
    "universe",
    "parameter_selection",
    "training_window",
    "final_test_window",
    "costs",
    "benchmark",
    "thresholds",
    "decision_basis",
)


def describe_method(original: dict, local: dict) -> dict:
    """逐项比较原规则与本地方法；原文结论不设置本地通过。"""
    adaptations = [key for key in COMPARE_KEYS if original.get(key) != local.get(key)]
    return {
        "method_status": "adapted" if adaptations else "original",
        "adaptations": adaptations,
        "local_validation": "not_tested",
    }


def require_economic_protocol(protocol: dict) -> None:
    """声明正式经济验证时，缺任一必备项即拒绝。"""
    missing = [key for key in PROTOCOL_REQUIRED if not protocol.get(key)]
    if missing:
        raise ValueError(f"incomplete_economic_protocol: missing {', '.join(missing)}")


def _month_end_dates(
    calendar: pd.DataFrame, signal_date: date
) -> tuple[list[str] | None, str | None]:
    """日历给出的真实月末点：该月最后一个交易日，且不得晚于 signal_date。

    signal_date 落在月中时，该月真实月末尚未到时点，整月拒绝（月中截断值
    不得冒充月末点）。此外，该月必须能被日历本身证明完整：日历表记录（与
    is_trading_day 取值无关）延伸到该月自然月末当日或之后——即"日历覆盖到
    月底"，而非"最后一个交易日是月底"；完整日历含非交易日行（如月末落在
    周末）时，月末点仍取该月最后一个交易日。日历截断在月内时（如日历与
    行情截至 6-14，6-15 之后无任何记录），月内最大日历日只是截断日，不能
    作为月末点。缺月、未完成月与无法证明完整的月分别记录原因。
    返回 (月末点, 原因)，成功时原因为 None。
    """
    flag = calendar["is_trading_day"].astype(str).str.lower().isin(("true", "1"))
    days = sorted(calendar.loc[flag, "date"].astype(str))
    if not days:
        return None, "calendar_empty: 日历无交易日记录"
    by_month: dict[str, str] = {}
    for day in days:
        by_month[day[:7]] = day  # days 升序，逐月覆盖后得到该月最后一个交易日
    # 覆盖证明用日历表全部记录（含非交易日行）的最大日期：日历含周末月末的
    # 非交易日行时，记录延伸过自然月末即证明该月完整，即使最后交易日早于月底。
    last_record = max(str(day) for day in calendar["date"])
    expected = [str(p) for p in pd.period_range(end=signal_date, periods=MONTH_END_POINTS, freq="M")]
    unfinished = [
        month for month in expected
        if month in by_month and by_month[month] > signal_date.isoformat()
    ]
    if unfinished:
        detail = "; ".join(f"{month} 真实月末 {by_month[month]} 晚于信号日" for month in unfinished)
        return None, f"month_unfinished: {detail}"
    truncated = [
        month
        for month in expected
        if month in by_month
        and last_record < pd.Period(month, freq="M").end_time.date().isoformat()
    ]
    if truncated:
        detail = "; ".join(
            f"{month} 月日历截断于 {last_record}，无法证明该月完整" for month in truncated
        )
        return None, f"calendar_truncated_months: {detail}"
    missing = [month for month in expected if month not in by_month]
    if missing:
        return None, f"calendar_missing_months: {', '.join(missing)}"
    return [by_month[month] for month in expected], None


def _known_market_cap(
    market_cap: pd.DataFrame, code: str, signal_date: date, as_of: datetime
) -> float | None:
    own = market_cap[market_cap["code"] == code]
    if not len(own):
        return None
    known = own[
        (own["date"].astype(str) <= signal_date.isoformat())
        & (own["available_at"].astype(str) <= as_of.isoformat())
    ]
    if not len(known):
        return None
    latest = known.sort_values("date").iloc[-1]
    value = pd.to_numeric(pd.Series([latest["market_cap_cny"]]), errors="coerce").iloc[0]
    return None if pd.isna(value) else float(value)


def _action_coverage_reason(manifest: dict, code: str, start: str, end: str) -> str:
    """只消费本快照的对象、实施日期期间和查询口径证据，不由现配置推断旧口径。"""
    from hetu_quant.data import action_query_covers

    failed = {str(x["product_id"]) for x in manifest.get("failures", [])}
    failed.update(manifest.get("gaps", {}))
    for entry in manifest.get("table_files", []):
        if (entry.get("table") != "corporate_actions"
                or entry.get("partition") != f"code={code}"
                or entry.get("product_id") in failed):
            continue
        # 部分合并只能消费明确证明的实施日子区间，不提升旧公告年/未知段。
        if action_query_covers(entry, start, end):
            return ""
    return (f"corporate_actions_coverage: {code} {start}..{end} "
            "缺少无失败的对象/完整期间/实施日期查询口径证明；未知不得当无行动")


def _report(
    quality: object,
    excluded: dict[str, list[str]],
    executed: list[str],
    research_status: str,
    signal_date: date,
    snapshot_id: str,
    data_window: dict[str, object],
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
        "experiment": "low_volatility",
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
        "data_window": data_window,
    }


def prepare(store, request, snapshot, output_dir) -> dict:
    """先按用途资格过滤，再构造 36 个月月收益波动率信号与市值目标权重。

    总收益序列在消费侧由 close_raw + corporate_actions 经
    build_total_return_series 构建（与排名侧 build_price_series 同一模式；
    prepare 不预填连续价列，2026-10-16 前直接读取全空列导致全体对象
    return_basis_unverified）。未核验行动类型按对象排除，不中断整个运行。
    """
    from hetu_quant.data import (  # noqa: F401  # 类型提示（runpy 加载时不强制）
        AccessDenied,
        DataStore,
        UnsupportedActionError,
        build_total_return_series,
    )

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
        # 真实免费运行实测（2026-10-14）：unverified 产品在准入层拒绝，
        # 受阻原因如实落报告，不向上抛、不伪装通过。
        report = _report(
            None, excluded, ["access_denied"], "blocked", signal_date,
            snapshot.snapshot_id,
            {"reason": f"access_denied: {error}"},
            gaps=["低波动真实条件测试受阻：市值产品准入未建立（unverified），信号与权重不产出"],
        )
        _write(output_dir, None, report)
        return report
    executed.append("qualify")
    if not quality.can_run:
        report = _report(
            quality, excluded, executed, "blocked", signal_date, snapshot.snapshot_id,
            {"reason": "purpose_not_qualified"},
            gaps=["low_volatility_preparation 用途资格不成立"],
        )
        _write(output_dir, None, report)
        return report

    manifest = store.load_manifest(snapshot)
    present_tables = {entry["table"] for entry in manifest.get("table_files", [])}
    if "corporate_actions" not in present_tables:
        report = _report(
            quality, excluded, executed, "blocked", signal_date, snapshot.snapshot_id,
            {"reason": "corporate_actions_unproven"},
            gaps=["公司行动记录完整性未证明，收益口径用途受阻"],
        )
        _write(output_dir, None, report)
        return report
    executed.append("corporate_actions_present")

    calendar = store.read_table(snapshot, "calendar")
    bars = store.read_table(snapshot, "daily_bars")
    status = store.read_table(snapshot, "security_status")
    market_cap = store.read_table(snapshot, "market_cap")
    actions = store.read_table(snapshot, "corporate_actions")

    month_ends, calendar_reason = _month_end_dates(calendar, signal_date)
    if month_ends is None:
        report = _report(
            quality, excluded, executed, "blocked", signal_date, snapshot.snapshot_id,
            {"reason": "calendar_incomplete", "detail": calendar_reason},
            gaps=[f"月末点校验失败：{calendar_reason}"],
        )
        _write(output_dir, None, report)
        return report
    executed.append("month_end_calendar")

    eligible = set(quality.eligible_ids)
    vols: dict[str, float] = {}
    for code in request.codes:
        reasons: list[str] = []
        coverage_reason = _action_coverage_reason(
            manifest, code, month_ends[0], signal_date.isoformat()
        )
        if coverage_reason:
            excluded[code] = [coverage_reason]
            continue
        own = bars[(bars["code"] == code)
                   & (bars["date"].astype(str) >= month_ends[0])
                   & (bars["date"].astype(str) <= signal_date.isoformat())]
        try:
            own = build_total_return_series(own, actions)
        except UnsupportedActionError as error:
            # 未核验行动类型只限制受影响对象（协议：配股等事件限制对象，不硬套公式）
            excluded[code] = [f"unverified_action:{error}"]
            continue
        own_status = status[status["code"] == code]
        prices: list[float] = []
        for day in month_ends:
            row = own[own["date"].astype(str) == day]
            if len(row):
                value = pd.to_numeric(row[RETURN_COLUMN], errors="coerce").iloc[-1]
                if pd.isna(value):
                    reasons.append("return_basis_unverified")
                    break
                prices.append(float(value))
                continue
            st = own_status[own_status["date"].astype(str) == day]
            if len(st) and str(st.iloc[-1]["is_suspended"]).lower() in ("true", "1"):
                reasons.append("suspended_month_end_no_close")
            elif len(st) and str(st.iloc[-1]["is_listed"]).lower() in ("false", "0"):
                pass  # 当时尚未上市：历史短的事实，不构成未决状态
            else:
                reasons.append("unresolved_trading_status")
        if len(prices) < MONTH_END_POINTS:
            reasons.append("insufficient_36_month_returns")
        if reasons:
            excluded[code] = reasons
            continue
        if code not in eligible:
            excluded[code] = ["not_eligible_by_qualify"]
            continue
        returns = pd.Series(prices).pct_change().dropna()
        vols[code] = float(returns.std(ddof=1))
    executed.append("warmup_36m_volatility")

    gaps: list[str] = []
    if len(vols) < MIN_POOL:
        report = _report(
            quality, excluded, executed, "blocked", signal_date, snapshot.snapshot_id,
            {"reason": "pool_below_10", "qualified": len(vols)},
            gaps=[f"合格池 {len(vols)} < {MIN_POOL}，十分位分组不成立"],
        )
        _write(output_dir, None, report)
        return report

    ordered = sorted(vols.items(), key=lambda item: (item[1], item[0]))
    count = max(1, len(ordered) // 10)
    if len(ordered) > count and ordered[count - 1][1] == ordered[count][1]:
        gaps.append("边界并列按代码稳定截取（本地适配）")
    selected = ordered[:count]
    executed.append("lowest_decile_selection")

    weights: dict[str, float] = {}
    for code, _vol in selected:
        cap = _known_market_cap(market_cap, code, signal_date, request.as_of)
        if cap is None or cap <= 0:
            excluded[code] = ["no_known_market_cap"]
            continue
        weights[code] = cap
    total = sum(weights.values())
    if not total:
        report = _report(
            quality, excluded, executed, "blocked", signal_date, snapshot.snapshot_id,
            {"reason": "no_known_market_cap"},
            gaps=["入选对象缺少信号时点可知总市值"],
        )
        _write(output_dir, None, report)
        return report
    executed.append("market_cap_weights")

    signals = pd.DataFrame(
        {
            "code": [code for code, _ in selected if code in weights],
            "signal_date": signal_date.isoformat(),
            "signal": [vols[code] for code, _ in selected if code in weights],
            "target_weight": [weights[code] / total for code, _ in selected if code in weights],
        }
    )
    data_window = {
        "warmup_first_month_end": month_ends[0],
        "signal_date": signal_date.isoformat(),
        "month_end_points": len(month_ends),
        "pool_size": len(vols),
        "selected": len(signals),
        "return_basis": "total_return",
        "currency": "CNY",
    }
    report = _report(
        quality, excluded, executed, "signals_ready", signal_date,
        snapshot.snapshot_id, data_window, gaps=gaps,
    )
    _write(output_dir, signals, report)
    return report


def _write(output_dir: Path, signals: pd.DataFrame | None, report: dict) -> None:
    output_dir.mkdir(parents=True, exist_ok=False)
    if signals is not None:
        signals.to_parquet(output_dir / "signals.parquet", index=False)
    (output_dir / "preparation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _products_for_purpose(store) -> tuple[str, ...]:
    """按冻结用途所需表从数据根配置解析产品；不绕过资格直接读文件。"""
    from hetu_quant.data import PURPOSE_TABLES

    needed = PURPOSE_TABLES[PURPOSE]
    chosen = []
    for table in needed:
        for product_id, product in store.config["products"].items():
            if table in product.get("tables", []):
                chosen.append(product_id)
                break
        else:
            raise ValueError(f"no product serves table {table} in data-root config")
    return tuple(chosen)


def _candidate_codes(store, snapshot) -> list[str]:
    securities = store.read_table(snapshot, "securities")
    if len(securities):
        frame = securities[securities["security_type"].astype(str) == "stock"]
        return sorted(frame["code"].astype(str))
    bars = store.read_table(snapshot, "daily_bars")
    return sorted(bars["code"].astype(str).unique())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="低波动多头独立研究脚本")
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
    start = (pd.Period(signal_date, freq="M") - WARMUP_MONTHS).start_time.date()
    request = DataRequest(
        product_ids=_products_for_purpose(store),
        codes=tuple(_candidate_codes(store, snapshot)),
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
