"""二期回测数据资格与历史时点版本选择。

历史股票池只消费“当时已公开且已核验”的证券版本：knowledge_evidence
记录每个版本的公开时点与核验状态，select_known_versions 按信号日时点
选出当时可见且未被已知修订取代的版本。prepare_backtest 在此之上重建
逐信号日宇宙、核验公司行动实施日覆盖，并按策略用途给出资格结论；
资格与宇宙明细落盘为 bundle，供 02 的引擎执行消费。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from hetu_quant.backtest_models import BacktestDefinition, backtest_definition_to_dict
from hetu_quant.data import PURPOSE_TABLES, DataStore, action_query_covers
from hetu_quant.models import CN_TZ_NAME, DataRequest, QualityReport, SnapshotRef

CN = ZoneInfo(CN_TZ_NAME)

# 策略 → 数据用途：未知策略不得静默映射到任一用途
STRATEGY_PURPOSES = {
    "low_volatility": "backtest_low_volatility",
    "universe_vw": "backtest_low_volatility",
    "ep_value": "backtest_ep",
}

_ELIGIBLE_MARKETS = ("SH", "SZ")
_ELIGIBLE_BOARDS = ("main", "chinext", "star")
_KNOWLEDGE_REQUIRED = ("revision_id", "supersedes", "published_at", "verified")
_UNIVERSE_COLUMNS = ["signal_date", "code"]
_EXCLUSION_COLUMNS = ["date", "code", "scope", "field", "reason", "source_ref"]


def _truthy(value: object) -> bool:
    return str(value).strip().lower() in ("true", "1")


def select_known_versions(
    frame: pd.DataFrame, at: datetime, keys: tuple[str, ...]
) -> pd.DataFrame:
    """按时点选出当时已公开、已核验且未被已知修订取代的记录版本。

    入选条件：verified 为真、published_at 非空且不晚于 at；同 keys 组内，
    被其他入选版本 supersedes 的版本出局。无证据（无公开时点）或核验失败
    的版本任何时点都不得入选。
    """
    if at.tzinfo is None:
        raise ValueError("at must be timezone-aware")
    missing = [c for c in (*keys, *_KNOWLEDGE_REQUIRED) if c not in frame.columns]
    if missing:
        raise ValueError(
            f"knowledge frame missing required columns: {', '.join(missing)}"
        )
    if not len(frame):
        return frame.iloc[0:0].copy()
    published = pd.to_datetime(
        frame["published_at"].replace("", None), format="ISO8601", utc=True, errors="coerce"
    )
    verified = frame["verified"].map(_truthy)
    at_utc = pd.Timestamp(at).tz_convert("utc")
    known = frame[verified & published.notna() & (published <= at_utc)]
    pieces = []
    for _, group in known.groupby(list(keys), sort=False):
        superseded = {str(s) for s in group["supersedes"] if str(s)}
        pieces.append(group[~group["revision_id"].astype(str).isin(superseded)])
    if not pieces:
        return frame.iloc[0:0].copy()
    return pd.concat(pieces)


def _proven_month_ends(calendar: pd.DataFrame) -> list[str]:
    """完整日历证明的各月真实月末交易日（升序）。

    月内最大交易日只在日历记录（含非交易日行）延伸到该月自然月末时
    才是该月真实月末；日历截断在月内则无法证明月完整，该月不产生月末点。
    """
    rows = [
        (str(day), flag)
        for day, flag in zip(calendar["date"], calendar["is_trading_day"], strict=False)
    ]
    trading = sorted(day for day, flag in rows if _truthy(flag))
    if not trading:
        return []
    month_ends: dict[str, str] = {}
    for day in trading:
        month_ends[day[:7]] = day  # 升序覆盖后得到该月最后一个交易日
    last_record = max(day for day, _flag in rows)
    return [
        day
        for month, day in sorted(month_ends.items())
        if last_record >= pd.Period(month, freq="M").end_time.date().isoformat()
    ]


def _signal_days(calendar: pd.DataFrame, start: date, end: date, frequency: str = 'monthly') -> list[str]:
    """月末交易日信号日序列；首信号为 start 前一月的月末交易日。

    先用完整日历证明各月真实月末（见 `_proven_month_ends`），再按研究
    范围筛选；月中截止或日历截断产生的月内最大日期不得冒充月末。
    """
    if frequency == 'weekly':
        rows = [(str(day), flag) for day, flag in zip(calendar['date'], calendar['is_trading_day'], strict=False)]
        if not rows:
            return []
        last_record = max(day for day, _ in rows)
        ends = {}
        for day, flag in sorted(rows):
            if _truthy(flag):
                ends[pd.Period(day, freq='W-SUN')] = day
        first_week = pd.Period(start.isoformat(), freq='W-SUN') - 1
        return [day for week, day in sorted(ends.items()) if week >= first_week
                and day <= end.isoformat() and last_record >= week.end_time.date().isoformat()]
    if frequency != 'monthly':
        raise ValueError(f'unsupported rebalance frequency: {frequency}')
    first_month = str(pd.Period(start.isoformat(), freq="M") - 1)
    return [
        day
        for day in _proven_month_ends(calendar)
        if day[:7] >= first_month and day <= end.isoformat()
    ]


def _warmup_start(signal_day: str, warmup_months: int) -> date:
    return (pd.Period(signal_day, freq="M") - warmup_months).start_time.date()


def _signal_cutoff(signal_day: str) -> str:
    """信号时点：信号日 21:00（CN），与宇宙重建的时点口径一致。"""
    return datetime.combine(date.fromisoformat(signal_day), time(21, 0), tzinfo=CN).isoformat()


def _evidence_original_ok(root: Path, evidence_ref: object, sha256: object) -> bool:
    """核验证据原件：ref 必须是数据根 evidence/ 下的安全文件名，文件存在且
    sha256 与登记值一致。原件缺失、路径越界或哈希为占位文本均不可放行。"""
    ref = str(evidence_ref)
    if not ref or "/" in ref or "\\" in ref or ref.startswith(".") or ".." in ref:
        return False
    original = Path(root) / "evidence" / ref
    if not original.is_file():
        return False
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    return digest == str(sha256).strip().lower()


def _write_json(path: Path, doc: Mapping[str, object]) -> None:
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def _rebuild_universe(
    definition: BacktestDefinition,
    store: DataStore,
    ref: SnapshotRef,
    manifest: Mapping[str, object],
    calendar: pd.DataFrame,
    signals: list[str],
    warmup_months: int,
) -> tuple[list[dict[str, str]], dict[str, set[str]], list[dict[str, str]]]:
    """逐信号日重建历史宇宙；返回 (universe_rows, excluded, exclusion_rows)。

    宇宙只来自 historical_securities 中当时已公开且已核验的版本（含退市
    证券的历史身份），并按信号日逐日判定上市窗口、市场/板块与 warmup；
    公司行动要求按 code 分区的实施日（ex_date）查询覆盖证明，不足者排除。
    证据原件按信号时点逐版本核验（evidence/ 下原件存在且 sha256 与登记
    一致）：某版本原件未核验只排除该版本被实际选用的信号日，后续版本的
    坏证据不追溯排除此前有效版本。每个信号窗口逐对象核验必需数据：行情
    覆盖 warmup 起点至信号日的全部必需月末点（完整月收益点），有证据停牌
    与采集缺失分别记录，不足者按对象按信号日排除并保留理由，不以表级
    通过放行，也不只核窗口首尾。
    """
    hist = store.read_table(ref, "historical_securities")
    knowledge = store.read_table(ref, "knowledge_evidence")
    hist_evidence = knowledge[knowledge["table_name"].astype(str) == "historical_securities"]
    evidence = hist_evidence[
        [
            "record_key",
            "evidence_ref",
            "sha256",
            "revision_id",
            "supersedes",
            "published_at",
            "verified",
        ]
    ].rename(columns={"record_key": "code"})

    bars = store.read_table(ref, "daily_bars")
    market_cap = store.read_table(ref, "market_cap")
    status = store.read_table(ref, "security_status")
    bars_by_code = (
        {str(code): group for code, group in bars.groupby(bars["code"].astype(str))}
        if len(bars)
        else {}
    )
    caps_by_code = (
        {str(code): group for code, group in market_cap.groupby(market_cap["code"].astype(str))}
        if len(market_cap)
        else {}
    )
    suspended_by_code: dict[str, set[str]] = {}
    if len(status):
        susp = status[status["is_suspended"].map(_truthy)]
        for code, group in susp.groupby(susp["code"].astype(str)):
            suspended_by_code[str(code)] = set(group["date"].astype(str))
    month_ends = _proven_month_ends(calendar)
    evidence_ok: dict[str, bool] = {}

    merged = hist.merge(evidence, how="inner", on=["code", "evidence_ref"])

    # 公司行动实施日覆盖证据：manifest 中 code 分区条目
    actions_entries: dict[str, Mapping] = {}
    for entry in manifest.get("table_files", []):
        if entry.get("table") != "corporate_actions":
            continue
        partition = str(entry.get("partition", ""))
        if partition.startswith("code="):
            actions_entries[partition[len("code="):].split("|", 1)[0]] = entry

    universe_rows: list[dict[str, str]] = []
    exclusion_rows: list[dict[str, str]] = []
    excluded: dict[str, set[str]] = {}
    # code -> [最早 warmup 起点, 最晚入选信号日]（行动覆盖所需窗口）
    windows: dict[str, list[object]] = {}
    pending: dict[str, list[dict[str, str]]] = {}
    for signal_day in signals:
        warmup = _warmup_start(signal_day, warmup_months)
        required_points = ([signal_day] if warmup_months == 0 else
                           [point for point in month_ends if warmup.isoformat() <= point <= signal_day])
        first_required = date.fromisoformat(required_points[0]) if required_points else warmup
        at = datetime.combine(date.fromisoformat(signal_day), time(21, 0), tzinfo=CN)
        cutoff = at.isoformat()
        chosen = select_known_versions(merged, at, keys=("code",))
        for row in chosen.itertuples():
            code = str(row.code)
            # 证据原件按所选版本逐信号日核验：只排除该版本被实际选用的时点，
            # 不追溯排除此前有效版本，也不波及其他对象
            evidence_ref = str(row.evidence_ref)
            ok = evidence_ok.get(evidence_ref)
            if ok is None:
                ok = _evidence_original_ok(store.root, evidence_ref, row.sha256)
                evidence_ok[evidence_ref] = ok
            if not ok:
                excluded.setdefault(code, set()).add(
                    "evidence_original_unverified: 原件缺失、路径非法或哈希不符 "
                    f"({evidence_ref})"
                )
                exclusion_rows.append(
                    {
                        "date": signal_day,
                        "code": code,
                        "scope": "evidence",
                        "field": "evidence_ref",
                        "reason": "evidence_original_unverified",
                        "source_ref": evidence_ref,
                    }
                )
                continue
            valid_from = str(row.valid_from)
            valid_to = str(row.valid_to)
            if not _truthy(row.listed):
                continue
            if not (valid_from <= signal_day):
                continue
            if valid_to and valid_to < signal_day:
                continue
            if str(row.market) not in _ELIGIBLE_MARKETS:
                continue
            if str(row.board) not in _ELIGIBLE_BOARDS:
                continue
            if valid_from > first_required.isoformat():
                excluded.setdefault(code, set()).add(
                    f"insufficient_warmup: listed {valid_from} after first required close {first_required}"
                )
                exclusion_rows.append(
                    {
                        "date": signal_day,
                        "code": code,
                        "scope": "universe",
                        "field": "warmup",
                        "reason": "insufficient_warmup",
                        "source_ref": str(row.source_ref),
                    }
                )
                continue
            # 逐对象逐信号窗口必需数据核验：不足者按日排除并保留理由
            window_reasons: list[str] = []
            if len(required_points) != warmup_months + 1:
                window_reasons.append('bars_warmup_gap: calendar lacks required complete monthly return endpoints')
            frame = bars_by_code.get(code)
            if frame is None or not len(frame):
                window_reasons.append("bars_missing: 该对象无行情记录")
            else:
                bar_days = set(frame["date"].astype(str))
                first_bar, last_bar = min(bar_days), max(bar_days)
                if first_bar > first_required.isoformat():
                    window_reasons.append(
                        f"bars_warmup_gap: 行情起点 {first_bar} 晚于首个必需月末 {first_required}"
                    )
                if last_bar < signal_day:
                    window_reasons.append(
                        f"bars_truncated: 行情止于 {last_bar} 早于信号日 {signal_day}"
                    )
                # 窗口内部完整性：每个必需月末点（完整月收益点）须有 bar；
                # 有证据停牌与采集缺失分别记录，不得只核首尾
                suspended = suspended_by_code.get(code, set())
                for point in month_ends:
                    if not (warmup.isoformat() <= point <= signal_day):
                        continue
                    if point in bar_days:
                        continue
                    if point in suspended:
                        window_reasons.append(
                            f"bars_suspended_at_required_point: {point} 有证据停牌，"
                            "无该月末价格可用"
                        )
                    else:
                        window_reasons.append(
                            f"bars_collection_gap: 缺必需月末点 {point}"
                            "（无行情亦无停牌证据）"
                        )
            caps = caps_by_code.get(code)
            known_caps = (
                caps[
                    (caps["date"].astype(str) == signal_day)
                    & (caps["available_at"].astype(str) <= cutoff)
                ]
                if caps is not None and len(caps)
                else None
            )
            if known_caps is None or not len(known_caps):
                window_reasons.append(
                    "market_cap_unknown_at_signal: 信号时点无已公开市值"
                )
            if window_reasons:
                excluded.setdefault(code, set()).update(window_reasons)
                for reason in window_reasons:
                    exclusion_rows.append(
                        {
                            "date": signal_day,
                            "code": code,
                            "scope": "eligibility",
                            "field": "window_data",
                            "reason": reason,
                            "source_ref": str(row.source_ref),
                        }
                    )
                continue
            pending.setdefault(code, []).append(
                {"signal_date": signal_day, "code": code}
            )
            window = windows.setdefault(code, [first_required, signal_day])
            window[0] = min(window[0], first_required)  # type: ignore[arg-type]
            window[1] = max(window[1], signal_day)  # type: ignore[arg-type]

    # 行动覆盖核验：不足者整代码排除（空事件不得当无行动）
    for code, rows in pending.items():
        start, last = windows[code]
        entry = actions_entries.get(code)
        proven = entry is not None and action_query_covers(
            entry, str(start), str(last)
        )
        if proven:
            universe_rows.extend(rows)
        else:
            excluded.setdefault(code, set()).add(
                "actions_not_proven: corporate_actions 缺 code 分区或实施日查询覆盖不足 "
                f"({start}..{last})"
            )
            exclusion_rows.append(
                {
                    "date": "",
                    "code": code,
                    "scope": "eligibility",
                    "field": "corporate_actions_coverage",
                    "reason": "actions_not_proven",
                    "source_ref": "manifest",
                }
            )
    return universe_rows, excluded, exclusion_rows


def prepare_backtest(
    definition: BacktestDefinition, store: DataStore, bundle_dir: Path
) -> QualityReport:
    """重建历史宇宙并给出按用途资格；资格与输入明细落盘 bundle。

    宇宙重建见 `_rebuild_universe`；资格结论 = 表级用途闸门（qualify）
    且宇宙非空。
    """
    strategy_id = definition.strategy_id
    if strategy_id not in STRATEGY_PURPOSES:
        raise ValueError(
            f"unknown strategy_id for backtest: {strategy_id!r} "
            f"(known: {', '.join(sorted(STRATEGY_PURPOSES))})"
        )
    purpose = STRATEGY_PURPOSES[strategy_id]
    bundle_dir = Path(bundle_dir)
    if bundle_dir.exists():
        raise FileExistsError(f"bundle_dir already exists: {bundle_dir}")
    warmup_months = int(definition.protocol["warmup_months"])

    ref = SnapshotRef(snapshot_id=definition.snapshot_id, root=store.root)
    manifest = store.load_manifest(ref)
    calendar = store.read_table(ref, "calendar")
    signals = _signal_days(calendar, definition.start, definition.end,
                           str(definition.protocol['rebalance']['frequency']))
    first_warmup = (
        _warmup_start(signals[0], warmup_months) if signals else definition.start
    )

    hist = store.read_table(ref, "historical_securities")
    if len(hist):
        codes = tuple(sorted(set(hist["code"].astype(str))))
    else:
        bars0 = store.read_table(ref, "daily_bars")
        codes = tuple(sorted(set(bars0["code"].astype(str)))) if len(bars0) else ()

    limitations: list[str] = []
    if not codes:
        limitations.append("no_codes: snapshot has neither historical securities nor bars")
        report = QualityReport(
            can_run=False,
            allowed_uses=(),
            eligible_ids=(),
            excluded={},
            limitations=tuple(limitations),
        )
        _write_bundle(definition, store, ref, purpose, bundle_dir, report, [], [])
        return report

    product_ids = tuple(str(item["product_id"]) for item in manifest.get("items", []))
    base = store.qualify(
        DataRequest(
            product_ids=product_ids,
            codes=codes,
            start=first_warmup,
            end=definition.end,
            as_of=definition.as_of,
            purpose=purpose,
            mode=definition.mode,
        ),
        ref,
    )
    limitations.extend(base.limitations)

    universe_rows, excluded, exclusion_rows = _rebuild_universe(
        definition, store, ref, manifest, calendar, signals, warmup_months
    )

    eligible_ids = tuple(sorted({row["code"] for row in universe_rows}))
    if not universe_rows:
        limitations.append("empty_historical_universe: 无任何证券通过历史时点与覆盖核验")
    report = QualityReport(
        can_run=base.can_run and bool(signals) and bool(universe_rows),
        allowed_uses=base.allowed_uses,
        eligible_ids=eligible_ids,
        excluded={code: tuple(sorted(reasons)) for code, reasons in excluded.items()},
        limitations=tuple(limitations),
    )
    _write_bundle(
        definition, store, ref, purpose, bundle_dir, report, universe_rows, exclusion_rows
    )
    return report


def _write_bundle(
    definition: BacktestDefinition,
    store: DataStore,
    ref: SnapshotRef,
    purpose: str,
    bundle_dir: Path,
    report: QualityReport,
    universe_rows: list[dict[str, str]],
    exclusion_rows: list[dict[str, str]],
) -> None:
    bundle_dir.mkdir(parents=True)
    _write_json(bundle_dir / "definition.json", backtest_definition_to_dict(definition))
    _write_json(
        bundle_dir / "qualification.json",
        {
            "purpose": purpose,
            "snapshot_id": ref.snapshot_id,
            "can_run": report.can_run,
            "allowed_uses": list(report.allowed_uses),
            "eligible_ids": list(report.eligible_ids),
            "excluded": {k: list(v) for k, v in report.excluded.items()},
            "limitations": list(report.limitations),
        },
    )
    pd.DataFrame(universe_rows, columns=_UNIVERSE_COLUMNS).to_parquet(
        bundle_dir / "universe.parquet", index=False
    )
    pd.DataFrame(exclusion_rows, columns=_EXCLUSION_COLUMNS).to_parquet(
        bundle_dir / "exclusions.parquet", index=False
    )
    _write_json(
        bundle_dir / "sources.json",
        {"snapshot_id": ref.snapshot_id, "purpose": purpose},
    )
    # 事实表副本是引擎执行的输入包；资格不通过时不复制整表
    if not report.can_run:
        return
    tables_dir = bundle_dir / "tables"
    tables_dir.mkdir()
    for table in PURPOSE_TABLES[purpose]:
        store.read_table(ref, table).to_parquet(
            tables_dir / f"{table}.parquet", index=False
        )
    manifest = store.load_manifest(ref)
    if any(e.get("table") == "terminal_disposals" for e in manifest.get("table_files", [])):
        import shutil

        disposals = store.read_table(ref, "terminal_disposals")
        disposals.to_parquet(tables_dir / "terminal_disposals.parquet", index=False)
        for name in disposals["evidence_ref"].astype(str):
            source = (store.root / "evidence" / name).resolve()
            if not source.is_relative_to((store.root / "evidence").resolve()):
                raise ValueError("terminal_disposal_evidence_path_invalid")
            target = bundle_dir / "evidence" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
