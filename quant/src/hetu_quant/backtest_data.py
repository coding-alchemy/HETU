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
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from hetu_data.archive import list_archive_targets, load_archive_coverage
from hetu_data.library import read_known_closes, selected_snapshot_assets
from hetu_data.models import LibraryRef
from hetu_data.utils import _truthy

from hetu_quant.account_evidence import account_dispositions, execution_cutoff
from hetu_quant.backtest_models import BacktestDefinition, backtest_definition_to_dict
from hetu_quant.data import PURPOSE_TABLES, TABLE_SCHEMAS, AccessDenied, DataStore, action_query_covers
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
_ACTION_RETURN_FIELDS = {"cash_dividend": "d", "bonus_split": "m"}


def _rename_proof(old, new, flags, cutoff):
    proof = new.get("continuation")
    left, right = old.get("valid_to"), new.get("valid_from")
    identity = new.get("identity_id")
    if (
        not isinstance(proof, dict) or not identity or identity != old.get("identity_id")
        or old["code"] == new["code"] or not left or not right or left >= right
        or proof.get("from_code") != old["code"] or proof.get("effective_date") != right
        or proof.get("kind") != "administrative_rename" or proof.get("ratio") != 1
        or isinstance(proof["ratio"], bool)
        or not _public_at(proof.get("published_at"), cutoff)
        or flags.get(right) is not True
    ):
        return None
    between = pd.date_range(left, right, inclusive="neither").strftime("%Y-%m-%d")
    if any(flags.get(day) is not False for day in between):
        return None
    return {**proof, "code": new["code"], "identity_id": identity, "from_valid_to": left}


def _snapshot_identity_roster(store, ref, definition, start, *, qualification_delivery=None):
    return _scope_objects(store, ref, start, definition.end.isoformat(), definition.as_of, qualification_delivery)


def _roster_renames(objects, flags, cutoff):
    by_code = {}
    by_identity = {}
    for item in objects:
        by_code.setdefault(item["code"], []).append(item)
        if item.get("identity_id"):
            by_identity.setdefault(item["identity_id"], []).append(item)
    for group in by_identity.values():
        periods = sorted(group, key=lambda item: item.get("valid_from") or "")
        for old, new in zip(periods, periods[1:], strict=False):
            if len(by_code[old["code"]]) == len(by_code[new["code"]]) == 1:
                event = _rename_proof(old, new, flags, cutoff)
                if event:
                    yield event


def _segment_rows(frames, segments, date_field, empty):
    pieces = []
    for code, start, end in segments:
        own = frames.get(code)
        if own is not None:
            dates = own[date_field].astype(str)
            keep = dates.ge(start) & (dates.le(end) if end else True)
            if date_field == "ex_date":
                keep |= pd.to_datetime(own[date_field], errors="coerce").isna()
            pieces.append(own.loc[keep])
    return pd.concat(pieces, ignore_index=True) if pieces else empty


def _account_renames(objects, calendar, hist, knowledge, root, cutoff):
    columns = ("record_key", "evidence_ref", "published_at", "revision_id", "supersedes", "verified", "sha256")
    proof = knowledge.loc[knowledge.table_name.eq("historical_securities"), list(columns)].rename(
        columns={"record_key": "code"}
    )
    versions = hist.merge(proof, on=["code", "evidence_ref"])
    for event in _roster_renames(objects, _calendar_flags(calendar), cutoff):
        day = event["effective_date"]
        before_open = min(execution_cutoff(day), cutoff)
        if not _public_at(event["published_at"], before_open):
            continue
        codes = (event["from_code"], event["code"])
        source = versions[versions.code.isin(codes)]
        chosen = select_known_versions(source, before_open, ("code",))
        old = chosen[chosen.code.eq(codes[0])]
        new = chosen[chosen.code.eq(codes[1])]
        if (len(old) == len(new) == 1 and str(old.iloc[0].valid_to) == event["from_valid_to"]
                and str(new.iloc[0].valid_from) == day
                and all(_evidence_original_ok(root, row.evidence_ref, row.sha256) for row in chosen.itertuples())):
            yield event



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
    """信号日沪深收盘时点。"""
    return datetime.combine(date.fromisoformat(signal_day), time(15, 0), tzinfo=CN).isoformat()


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


def select_signal_market_cap(
    caps: pd.DataFrame, knowledge: pd.DataFrame, code: str, signal_day: str, evidence_root: Path
) -> dict | None:
    """Select the documented version available at close, with its evidence."""
    if caps.empty:
        return None
    own = caps[caps["code"].astype(str).eq(code) & caps["date"].astype(str).eq(signal_day)]
    if own.empty:
        return None
    at = datetime.fromisoformat(_signal_cutoff(signal_day))
    legacy = own[own["revision_id"].fillna("").eq("")] if "revision_id" in own else own
    selected = pd.DataFrame()
    if "revision_id" in own and not knowledge.empty:
        documents = knowledge[
            knowledge["table_name"].eq("market_cap") & knowledge["record_key"].eq(f"{code}|{signal_day}")
        ]
        columns = ["revision_id", "supersedes", "published_at", "verified", "evidence_ref", "sha256"]
        if "evidence_root" in documents:
            columns.append("evidence_root")
        revisions = own[own["revision_id"].fillna("").ne("")]
        if "evidence_root" in columns:
            revisions = revisions.drop(columns=["evidence_root"], errors="ignore")
        versions = revisions.drop(columns=columns[1:], errors="ignore").merge(
            documents[columns].drop_duplicates(), on="revision_id"
        )
        selected = select_known_versions(versions, at, ("code", "date"))
        if not selected.empty:
            if len(selected) != 1:
                return None
            fact = selected.iloc[0]
            root = Path(fact["evidence_root"]) if "evidence_root" in fact else evidence_root
            if not _evidence_original_ok(root, fact["evidence_ref"], fact["sha256"]):
                return None
    if selected.empty:
        published = pd.to_datetime(legacy["available_at"], format="ISO8601", utc=True, errors="coerce")
        eligible = published[published <= pd.Timestamp(at)]
        if eligible.empty:
            return None
        selected = legacy.loc[eligible[eligible == eligible.max()].index]
        if selected["market_cap_cny"].nunique() != 1:
            return None
    result = selected.iloc[0].to_dict()
    value = pd.to_numeric(result["market_cap_cny"], errors="coerce")
    if not pd.notna(value) or not 0 < value < float("inf"):
        return None
    result["market_cap_cny"] = float(value)
    return result


def _write_json(path: Path, doc: Mapping[str, object]) -> None:
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def _action_availability(available_at: pd.Series, signal_day: str) -> tuple[pd.Series, pd.Series]:
    """Return which action records were known by close and which were clearly later."""
    available = available_at.astype("string").str.strip()
    dated = available.str.fullmatch(r"\d{4}-\d{2}-\d{2}", na=False)
    timed = available.str.contains(r"(?:Z|[+-]\d{2}:?\d{2})$", na=False)
    timestamps = pd.to_datetime(available.where(timed), format="ISO8601", utc=True, errors="coerce")
    cutoff = pd.Timestamp(_signal_cutoff(signal_day))
    known = ((dated & available.lt(signal_day)) | timestamps.le(cutoff)).fillna(False)
    late = ((dated & available.gt(signal_day)) | timestamps.gt(cutoff)).fillna(False)
    return known, late


def _signal_action_inputs(
    actions: pd.DataFrame, first_close: str, signal_day: str, *, knowledge_day: str | None = None,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Choose terms known at the signal close and report unproved total returns."""
    if actions.empty or first_close >= signal_day:
        return actions.iloc[:0], []
    actions = actions.reset_index(drop=True)
    ex_dates = pd.to_datetime(actions["ex_date"].astype("string").str.strip(), format="%Y-%m-%d", errors="coerce")
    window_start, window_end = pd.Timestamp(first_close), pd.Timestamp(signal_day)
    known, late = _action_availability(actions["available_at"], knowledge_day or signal_day)
    selected, gaps = [], []

    def record_gap(group: pd.DataFrame, day: str | None, kind: str, field: str) -> None:
        if group.empty:
            return
        refs = sorted(set(group["source_ref"].dropna().astype(str)) - {""})
        gaps.append({"date": signal_day, "code": str(group["code"].iloc[0]), "scope": "eligibility",
                     "field": "corporate_action_terms", "action_type": kind, "action_date": day,
                     "missing_fields": [field], "required": [first_close, signal_day],
                     "reason": (f"total_return_action_terms_unproved: {day or '实施日未知'} {kind} {field}；"
                                f"收盘前证据不足或冲突（{first_close}..{signal_day}）"),
                     "source_refs": refs, "source_ref": ", ".join(refs)})

    undated = ex_dates.isna() & ~late
    if "record_date" in actions:
        registration = pd.to_datetime(actions["record_date"], format="%Y-%m-%d", errors="coerce")
        # 已证登记日不早于窗口末日时，须在登记日之后发生的实施日尚未进入窗口。
        undated &= ~(known & registration.ge(window_end))
    for kind, group in actions[undated].groupby("action_type", sort=False, dropna=False):
        record_gap(group, None, str(kind), "ex_date")

    in_window = ex_dates.gt(window_start) & ex_dates.le(window_end)
    events = actions.loc[in_window].assign(ex_date=ex_dates[in_window].dt.strftime("%Y-%m-%d"))
    for (day, kind), group in events.groupby(["ex_date", "action_type"], sort=False, dropna=False):
        field = _ACTION_RETURN_FIELDS.get(kind)
        if field is None:
            record_gap(group[~late.loc[group.index]], str(day), str(kind), "action_type")
            continue
        # 已登记实施日的现金分红和送转需要分别证明条款在收盘前可知。
        versions = group[known.loc[group.index]]
        values = pd.to_numeric(versions[field], errors="coerce")
        if len(values) and values.notna().all() and values.lt(float("inf")).all() and values.nunique() == 1 and (
            values.ge(0).all() if field == "d" else values.gt(0).all()
        ):
            selected.append(versions.iloc[:1])
            continue
        record_gap(group, str(day), str(kind), field)
    return (pd.concat(selected) if selected else events.iloc[:0]), gaps


def _rebuild_universe(
    definition: BacktestDefinition,
    store: DataStore,
    ref: SnapshotRef,
    manifest: Mapping[str, object],
    calendar: pd.DataFrame,
    signals: list[str],
    warmup_months: int,
    *,
    qualification_delivery: Path | None = None,
) -> tuple[list[dict], dict[str, set[str]], list[dict[str, str]]]:
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
    actions = store.read_table(ref, "corporate_actions")
    actions_by_code = {str(code): frame for code, frame in actions.groupby("code")}
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
    flags = _calendar_flags(calendar)
    if signals:
        _, roster = _snapshot_identity_roster(
            store, ref, definition, _warmup_start(signals[0], warmup_months).isoformat(),
            qualification_delivery=qualification_delivery,
        )
    else:
        roster = []
    roster_by_code = {item["code"]: item for item in roster}
    periods_by_identity = {}
    for item in roster:
        periods_by_identity.setdefault(item.get("identity_id"), []).append(item)
    renames = {event["code"]: event for event in _roster_renames(roster, flags, definition.as_of)}
    rename_codes = {*renames, *(event["from_code"] for event in renames.values())}

    # 公司行动实施日覆盖证据：manifest 中 code 分区条目
    actions_entries: dict[str, Mapping] = {}
    for entry in manifest.get("table_files", []):
        if entry.get("table") != "corporate_actions":
            continue
        partition = str(entry.get("partition", ""))
        if partition.startswith("code="):
            actions_entries[partition[len("code="):].split("|", 1)[0]] = entry

    universe_rows: list[dict] = []
    exclusion_rows: list[dict[str, str]] = []
    excluded: dict[str, set[str]] = {}
    for signal_day in signals:
        warmup = _warmup_start(signal_day, warmup_months)
        required_points = ([signal_day] if warmup_months == 0 else
                           [point for point in month_ends if warmup.isoformat() <= point <= signal_day])
        first_required = date.fromisoformat(required_points[0]) if required_points else warmup
        at = datetime.fromisoformat(_signal_cutoff(signal_day))
        chosen = select_known_versions(merged, at, keys=("code",))
        known = {}
        if rename_codes:
            for selected_code, candidates in chosen[chosen.code.isin(rename_codes)].groupby("code"):
                if len(candidates) == 1:
                    fact = candidates.iloc[0]
                    if _evidence_original_ok(store.root, str(fact.evidence_ref), fact.sha256):
                        known[str(selected_code)] = fact
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
            first = first_required.isoformat()
            segments = [(code, valid_from, valid_to)]
            linked = roster_by_code.get(code)
            while segments[0][1] > first:
                next_code, next_start, _ = segments[0]
                event = renames.get(next_code)
                if not event or not _public_at(event["published_at"], at) or next_start != event["effective_date"]:
                    break
                previous_code = event["from_code"]
                previous = known.get(previous_code)
                if (previous is None or next_code not in known or str(previous.valid_to) != event["from_valid_to"]
                        or str(previous.valid_from) != roster_by_code[previous_code]["valid_from"]):
                    break
                segments.insert(0, (previous_code, str(previous.valid_from), str(previous.valid_to)))
            if segments[0][1] > first:
                has_predecessor = linked and (
                    linked.get("continuation") or any(
                        peer["code"] != code and peer.get("identity_id") == linked.get("identity_id")
                        and peer.get("valid_from", "~") < valid_from
                        and (peer.get("valid_to") or "~") >= first
                        for peer in periods_by_identity[linked.get("identity_id")]
                    )
                )
                field = "code_continuity" if has_predecessor else "warmup"
                reason = ("code_continuity_unproved: 预热窗口内缺有效的跨码接续依据" if has_predecessor
                          else f"insufficient_warmup: listed {valid_from} after first required close {first_required}")
                excluded.setdefault(code, set()).add(
                    reason
                )
                exclusion_rows.append(
                    {
                        "date": signal_day,
                        "code": code,
                        "scope": "universe",
                        "field": field,
                        "reason": reason if has_predecessor else "insufficient_warmup",
                        "source_ref": str(row.source_ref),
                    }
                )
                continue
            # 逐对象逐信号窗口必需数据核验：不足者按日排除并保留理由
            window_reasons: list[str] = []
            if len(required_points) != warmup_months + 1:
                window_reasons.append('bars_warmup_gap: calendar lacks required complete monthly return endpoints')
            frame = (bars_by_code.get(code, bars.iloc[:0]) if len(segments) == 1
                     else _segment_rows(bars_by_code, segments, "date", bars.iloc[:0]))
            if frame.empty:
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
                required = set(required_points)
                for previous, next_segment in zip(segments, segments[1:], strict=False):
                    prior_trading = max((d for d, trading in flags.items() if trading and d <= previous[2]), default="")
                    required.update((prior_trading, next_segment[1]))
                for point in sorted(required):
                    if point in bar_days:
                        continue
                    active_code = next(
                        (key for key, start, end in segments if start <= point and (not end or point <= end)), code
                    )
                    suspended = suspended_by_code.get(active_code, set())
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
            if caps is None or select_signal_market_cap(caps, knowledge, code, signal_day, store.root) is None:
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
            if any(not action_query_covers(actions_entries.get(key, {}), max(first, start), min(signal_day, end)
                                           if end else signal_day) for key, start, end in segments):
                excluded.setdefault(code, set()).add(
                    "actions_not_proven: corporate_actions 缺 code 分区或实施日查询覆盖不足 "
                    f"({first_required}..{signal_day})"
                )
                exclusion_rows.append({"date": signal_day, "code": code, "scope": "eligibility",
                                       "field": "corporate_actions_coverage", "reason": "actions_not_proven",
                                       "source_ref": "manifest"})
                continue
            signal_actions = (actions_by_code.get(code, actions.iloc[:0]) if len(segments) == 1 else
                              _segment_rows(actions_by_code, segments, "ex_date", actions.iloc[:0]))
            if len(signal_actions):
                _, gaps = _signal_action_inputs(signal_actions, first, signal_day)
                if gaps:
                    excluded.setdefault(code, set()).update(gap["reason"] for gap in gaps)
                    exclusion_rows.extend({field: gap[field] for field in _EXCLUSION_COLUMNS} for gap in gaps)
                    continue
            universe_rows.append({"signal_date": signal_day, "code": code, "segments": segments})
    return universe_rows, excluded, exclusion_rows


def prepare_backtest(
    definition: BacktestDefinition, store: DataStore, bundle_dir: Path, *, record_qualification: bool = True,
    qualification_delivery: Path | None = None,
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
        record=record_qualification,
    )
    limitations.extend(base.limitations)

    universe_rows, excluded, exclusion_rows = _rebuild_universe(
        definition, store, ref, manifest, calendar, signals, warmup_months,
        qualification_delivery=qualification_delivery,
    )
    action_gaps = [row for row in exclusion_rows if row["field"] in ("corporate_action_terms", "code_continuity")]
    limitations.extend(f"{row['date']} {row['code']}: {row['reason']}" for row in action_gaps)
    account_events = {
        table: store.read_table(ref, table) for table in ("terminal_disposals", "share_exchanges")
        if any(entry["table"] == table for entry in manifest.get("table_files", ()))
    }
    for table, frame in account_events.items():
        for product_id in frame["product_id"].dropna().unique():
            if not _purpose_allowed(store, manifest, table, product_id, purpose):
                limitations.append(f"{table}: research_input_use_unproved ({product_id})")
                action_gaps.append(table)
    _, _, exit_gaps, exchange_gaps, conflicts = account_dispositions(
        account_events.get("terminal_disposals", pd.DataFrame()).to_dict("records"),
        account_events.get("share_exchanges", pd.DataFrame()).to_dict("records"),
        store.root / "evidence",
        definition.start.isoformat(), definition.end.isoformat(), as_of=definition.as_of,
    )
    for gap in exchange_gaps:
        gap["reason"] += ": " + ", ".join(gap["fields"])
        gap["field"] = "share_exchanges"
    for gap in [*exit_gaps, *exchange_gaps, *conflicts]:
        if terms := gap.get("terms"):
            gap["reason"] += f": {terms}"
        if gap.get("expected_originals"):
            gap["reason"] += ": event=" + ", ".join(gap["event_ids"])
        limitations.append(f"{gap['date']} {gap['code']}: {gap['reason']} ({', '.join(gap['source_refs'])})")
        exclusion_rows.append(dict(date=gap["date"], code=gap["code"], scope="account",
                                   field=gap.get("field", "terminal_disposals"), reason=gap["reason"],
                                   source_ref="; ".join(gap["source_refs"])))

    eligible_ids = tuple(sorted({row["code"] for row in universe_rows}))
    if not universe_rows:
        limitations.append("empty_historical_universe: 无任何证券通过历史时点与覆盖核验")
    report = QualityReport(
        can_run=(base.can_run and bool(signals) and bool(universe_rows) and not action_gaps and not exit_gaps
                 and not conflicts and not any(gap.get("blocks_start") for gap in exchange_gaps)),
        allowed_uses=base.allowed_uses,
        eligible_ids=eligible_ids,
        excluded={code: tuple(sorted(reasons)) for code, reasons in excluded.items()},
        limitations=tuple(limitations),
    )
    _write_bundle(
        definition, store, ref, purpose, bundle_dir, report, universe_rows, exclusion_rows,
        qualification_delivery=qualification_delivery, account_events=account_events,
    )
    return report


def _write_bundle(
    definition: BacktestDefinition,
    store: DataStore,
    ref: SnapshotRef,
    purpose: str,
    bundle_dir: Path,
    report: QualityReport,
    universe_rows: list[dict],
    exclusion_rows: list[dict[str, str]],
    *,
    qualification_delivery: Path | None = None,
    account_events: dict[str, pd.DataFrame] | None = None,
) -> None:
    bundle_dir.mkdir(parents=True)
    _write_json(bundle_dir / "definition.json", backtest_definition_to_dict(definition))
    qualification = {
        "purpose": purpose,
        "snapshot_id": ref.snapshot_id,
        "can_run": report.can_run,
        "allowed_uses": list(report.allowed_uses),
        "eligible_ids": list(report.eligible_ids),
        "excluded": {k: list(v) for k, v in report.excluded.items()},
        "limitations": list(report.limitations),
    }
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
        _write_json(bundle_dir / "qualification.json", qualification)
        return
    tables_dir = bundle_dir / "tables"
    tables_dir.mkdir()
    for table in PURPOSE_TABLES[purpose]:
        store.read_table(ref, table).to_parquet(
            tables_dir / f"{table}.parquet", index=False
        )
    calendar = store.read_table(ref, "calendar")
    signals = _signal_days(
        calendar, definition.start, definition.end, str(definition.protocol["rebalance"]["frequency"]),
    )
    first = _warmup_start(signals[0], int(definition.protocol["warmup_months"])).isoformat()
    roster_ref, roster = _snapshot_identity_roster(
        store, ref, definition, first, qualification_delivery=qualification_delivery,
    )
    transitions = [
        event for event in _account_renames(roster, calendar, store.read_table(ref, "historical_securities"),
                                            store.read_table(ref, "knowledge_evidence"), store.root, definition.as_of)
        if first <= event["effective_date"] <= definition.end.isoformat()
    ] if roster else []
    if transitions:
        original = (store.root / roster_ref).read_bytes()
        checksum = hashlib.sha256(original).hexdigest()
        evidence_ref = f"identity-roster-{checksum}.json"
        evidence_dir = bundle_dir / "evidence"
        evidence_dir.mkdir()
        (evidence_dir / evidence_ref).write_bytes(original)
        pd.DataFrame(transitions).assign(source_ref=roster_ref, evidence_ref=evidence_ref, sha256=checksum).to_parquet(
            tables_dir / "identity_transitions.parquet", index=False
        )
    for table, frame in (account_events or {}).items():
        clock = "effective_date" if table == "share_exchanges" else "date"
        relevant = frame[frame[clock].astype(str).between(
            definition.start.isoformat(), definition.end.isoformat(),
        )]
        if relevant.empty:
            continue
        relevant.to_parquet(tables_dir / f"{table}.parquet", index=False)
        for name in relevant["evidence_ref"].astype(str):
            source = (store.root / "evidence" / name).resolve()
            if not source.is_relative_to((store.root / "evidence").resolve()):
                raise ValueError("account_event_evidence_path_invalid")
            target = bundle_dir / "evidence" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    _write_json(bundle_dir / "qualification.json", qualification)


SIGNAL_LOOKBACK_MONTHS = 36
_SIGNAL_USE = "low_volatility_preparation"


@dataclass
class SignalInputs:
    signal_date: date
    cutoff: datetime
    day_kind: str
    executable_from: date | None
    pool_size: int | None = None
    month_ends: tuple[str, ...] = ()
    closes: pd.DataFrame = field(default_factory=pd.DataFrame)
    actions: pd.DataFrame = field(default_factory=pd.DataFrame)
    market_cap: pd.DataFrame = field(default_factory=pd.DataFrame)
    identities: pd.DataFrame = field(default_factory=pd.DataFrame)
    gaps: list[dict] = field(default_factory=list)


def _calendar_flags(calendar):
    flags = {}
    for key, group in calendar.groupby("date"):
        values = set(group.is_trading_day.dropna().astype(str).str.lower())
        if values and values <= {"true", "1"}:
            flags[str(key)] = True
        elif values and values <= {"false", "0"}:
            flags[str(key)] = False
    return flags


def _complete_month_ends(calendar):
    flags = _calendar_flags(calendar)
    calendar = pd.DataFrame(flags.items(), columns=["date", "is_trading_day"])
    dates = set(flags)
    counts = pd.Series(sorted(dates), dtype=str).str[:7].value_counts()
    return [day for day in _proven_month_ends(calendar)
            if counts.get(day[:7], 0) == pd.Period(day, freq="M").days_in_month]


def split_signal_calendar(calendar, day):
    """Classify a session and return its next consecutively documented session."""
    flags = _calendar_flags(calendar)
    if not flags.get(day):
        return "unknown", None
    next_day = date.fromisoformat(day) + timedelta(days=1)
    while flags.get(next_day.isoformat()) is False:
        next_day += timedelta(days=1)
    executable = next_day.isoformat() if flags.get(next_day.isoformat()) else None
    if any(value and key[:7] == day[:7] and key > day for key, value in flags.items()):
        return "ordinary", executable
    normalized = pd.DataFrame(flags.items(), columns=["date", "is_trading_day"])
    if day in _complete_month_ends(normalized):
        return "month_end", executable
    return "unknown", None


def _permission_sources(owner, manifest, table, product_id):
    product = owner.product(product_id)
    owner.check_access(manifest.get("mode", "free"), product, operation="read")
    sources = [item.get("source", {}) for item in manifest.get("items", ())
               if item["product_id"] == product_id and item.get("table", table) == table]
    permission = (owner._grant(product_id) if product.get("fee_class") == "paid" else product.get("permission")) or {}
    return sources, permission


def _purpose_allowed(owner, manifest, table, product_id, purpose):
    sources, permission = _permission_sources(owner, manifest, table, product_id)
    uses = [entry["allowed_uses"] for entry in [permission, *sources] if "allowed_uses" in entry]
    return bool(sources and uses) and all(isinstance(scope, list) and purpose in scope for scope in uses)


def _signal_table(store, source, table, *, purpose=_SIGNAL_USE):
    """Return facts only from registered assets with an explicit grant for this use."""
    frames, denied, entries = [], [], []
    for owner, ref, manifest in selected_snapshot_assets(store, source, table):
        available = [row for row in manifest["table_files"] if row["table"] == table]
        if not available:
            continue
        products = {row["product_id"] for row in available}
        permitted = set()
        for product in products:
            try:
                if _purpose_allowed(owner, manifest, table, product, purpose):
                    permitted.add(product)
                    continue
            except AccessDenied:
                pass
            denied.append(f"{ref.snapshot_id}/{product}")
        if not permitted:
            continue
        # Do not open an unlicensed product's file even when another product passed.
        if products != permitted:
            continue
        try:
            frame = owner.read_table(ref, table)
        except AccessDenied:
            denied.extend(f"{ref.snapshot_id}/{product}" for product in sorted(products))
            continue
        if not frame.empty:
            frames.append(frame.assign(evidence_root=str(owner.root), snapshot_id=ref.snapshot_id))
        entries.extend((owner, ref, manifest, entry) for entry in available)
    empty = pd.DataFrame(columns=[*TABLE_SCHEMAS[table]["columns"], "evidence_root", "snapshot_id"])
    return (pd.concat(frames, ignore_index=True) if frames else empty), denied, entries


def _public_at(value, cutoff):
    try:
        stamp = datetime.fromisoformat(str(value))
        return stamp.tzinfo is not None and stamp <= cutoff
    except ValueError:
        return False


def _signal_calendar(store, source, cutoff):
    calendar, _, entries = _signal_table(store, source, "calendar")
    public = {}
    for owner, ref, manifest, entry in entries:
        sources, _ = _permission_sources(owner, manifest, "calendar", entry["product_id"])
        clocks = [item.get("published_at", item.get("obtained_at")) for item in sources]
        public[str(owner.root), ref.snapshot_id, entry["product_id"]] = any(_public_at(t, cutoff) for t in clocks)
    if calendar.empty:
        return calendar
    mask = [public.get((r.evidence_root, r.snapshot_id, r.product_id), False)
            for r in calendar.itertuples(index=False)]
    return calendar.loc[mask]


def _scope_objects(store, source, start, end, cutoff, qualification_delivery=None):
    from hetu_quant.data_research import _read_delivery_file

    if isinstance(source, SnapshotRef):
        manifest = store.load_manifest(source)
        directory = (Path(qualification_delivery) if qualification_delivery is not None else
                     store.root / manifest.get("qualification_delivery", "research/phase2-lowvol/qualification"))
        handoff = _read_delivery_file(directory, "handoff.json")
        if (handoff.get("snapshot_id") != source.snapshot_id or handoff.get("mode") != manifest.get("mode", "free")
                or handoff.get("supplemented_snapshots")):
            return None, []
        candidates = [_read_delivery_file(directory, "requirements.json", "research-requirements.json")]
    else:
        candidates = []
        for target in list_archive_targets(store):
            if target["start"] > start or target["end"] < end:
                continue
            audit = load_archive_coverage(store, source, target["target_id"])
            if not audit or not audit.get("target_scope_complete"):
                continue
            path = store.root / "archive" / "targets" / f"{target['target_id']}.json"
            candidates.append(json.loads(path.read_text(encoding="utf-8"))["requirements"])
    matched = []
    for req in candidates:
        scope = req.get("target_scope", {})
        ref = str(scope.get("source_ref") or "")
        path = (store.root / ref).resolve()
        if (scope.get("complete") is True and ref and path.is_relative_to(store.root.resolve()) and path.is_file()
                and req.get("start", "") <= start <= end <= req.get("end", "")
                and ("published_at" not in scope or _public_at(scope["published_at"], cutoff))):
            objects = req.get("objects", [])
            if _original_roster_matches(path, objects):
                matched.append((ref, objects))
    return matched[0] if len(matched) == 1 else (None, [])



def _original_roster_matches(path, objects):
    """Confirm the independent roster actually covers the registered segment facts."""
    def coordinate(row):
        return (str(row.get("code", "")), str(row.get("identity_id") or row.get("code", "")),
                str(row.get("valid_from") or row.get("ipo_date") or ""),
                str(row.get("valid_to") or row.get("out_date") or ""))

    try:
        if path.suffix.lower() == ".csv":
            entries = pd.read_csv(path, dtype=str).fillna("").to_dict("records")
        else:
            entries = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(entries, dict):
                entries = entries["objects"]
        by_key = {coordinate(row): row for row in entries}
        if len(by_key) != len(entries):
            return False
        return bool(objects) and all(
            coordinate(row) in by_key
            and all(row.get(key) in (None, "", by_key[coordinate(row)].get(key))
                    for key in ("market", "security_type", "continuation"))
            for row in objects
        ) and set(by_key) <= {coordinate(row) for row in objects}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False

def _signal_identities(hist, knowledge, objects, start, day, cutoff, add_gap):
    evidence = knowledge[knowledge.table_name.eq("historical_securities")].rename(columns={"record_key": "code"})
    columns = ["code", "evidence_ref", "sha256", "revision_id", "supersedes", "published_at",
               "verified", "evidence_root"]
    merged = hist.merge(evidence[columns].drop_duplicates(), on=["code", "evidence_ref", "evidence_root"])
    known = select_known_versions(merged, cutoff, ("code",))
    attestations, result, active = {}, [], set()
    for obj in objects:
        code = str(obj["code"])
        left = obj.get("valid_from") or ""
        if obj.get("published_at") and not _public_at(obj["published_at"], cutoff):
            if left and left > day:
                continue
            add_gap("historical_securities", "身份或代码段在收盘前缺证", code, refs=[obj.get("source_ref")])
            continue
        if left and left > day:
            continue
        if obj.get("market") in ("BJ", "HK") or obj.get("security_type") not in ("stock", "", None, "unknown"):
            continue
        own = known[known.code.eq(code)]
        own = own[own.valid_from.astype(str).eq(left)]
        valid = []
        for row in own.itertuples():
            key = Path(row.evidence_root), row.evidence_ref, row.sha256
            if key not in attestations:
                attestations[key] = _evidence_original_ok(*key)
            if attestations[key]:
                valid.append(row)
        refs = [obj.get("source_ref"), *[row.source_ref for row in valid]]
        if len(valid) != 1:
            add_gap("historical_securities", "收盘时点身份或生命周期缺证／冲突", code, refs=refs)
            continue
        row = valid[0]
        # The independently supplied roster identifies the segment; its later lifecycle annotations
        # cannot override the version actually known at the original cutoff.
        right = "" if pd.isna(row.valid_to) else str(row.valid_to)
        if right and right < start:
            continue
        if row.market not in _ELIGIBLE_MARKETS or row.board not in _ELIGIBLE_BOARDS:
            if not row.market or not row.board or row.market == "unknown" or row.board == "unknown":
                add_gap("historical_securities", "市场或板块属性未证", code, refs=refs)
            continue
        identity = obj.get("identity_id") or ""
        if obj.get("security_type") != "stock":
            add_gap("security_type", "普通股属性未证", code, refs=refs)
            continue
        if right and right < day:
            current = False
        else:
            current = _truthy(row.listed)
            if current:
                active.add(code)
        result.append(dict(identity_id=identity, code=code, valid_from=left, valid_to=right,
                           active=current, source_ref=str(row.source_ref), evidence_ref=str(row.evidence_ref),
                           continuation=obj.get("continuation")))
    return result, active



def _identity_windows(identities, start, cutoff, calendar, add_gap):
    """Build return windows across explicitly proven 1:1 administrative code changes."""
    flags = _calendar_flags(calendar)
    windows = {}
    for live in sorted((item for item in identities if item["active"]), key=lambda item: item["code"]):
        code = live["code"]
        group = [item for item in identities if live["identity_id"] and item["identity_id"] == live["identity_id"]
                 and item["code"] != code]
        if live["valid_from"] <= start:
            if any(not item["valid_to"] or item["valid_to"] >= live["valid_from"] for item in group):
                add_gap("code_continuity", "同一证券的代码段重叠，预热接续未证", code, refs=[live["source_ref"]])
            else:
                windows[code] = [live]
            continue
        chain = [live]
        while chain[0]["valid_from"] > start:
            successor = chain[0]
            proof = successor.get("continuation") or {}
            old = next((item for item in group if item["code"] == proof.get("from_code")), None)
            if not old or old in chain or not _rename_proof(old, successor, flags, cutoff):
                break
            chain.insert(0, old)
        if chain[0]["valid_from"] > start:
            add_gap("code_continuity" if group else "warmup",
                    "预热窗口内跨码接续未证" if group else "上市晚于预热首个收盘日", code,
                    refs=[live["source_ref"], *[item["source_ref"] for item in group]], blocking=bool(group))
            continue
        if any(item["active"] and item["code"] != code for item in group):
            add_gap("code_continuity", "同一证券存在多段当前代码", code, refs=[live["source_ref"]])
            continue
        windows[code] = chain
    return windows


def _action_coverage(entries, code, first, last):
    from hetu_quant.data import _action_query_records

    records = [span for *_, entry in entries if f"code={code}" in entry.get("partition", "").split("|")
               for span in _action_query_records(entry)]
    return action_query_covers({"query_coverage": records}, first, last)


def read_low_volatility_inputs(
    store: DataStore, source: SnapshotRef | LibraryRef, *, signal_date: date,
) -> SignalInputs:
    """Prepare the frozen monthly signal using only facts admissible by its close."""
    day = signal_date.isoformat()
    cutoff = datetime.fromisoformat(_signal_cutoff(day))
    calendar = _signal_calendar(store, source, cutoff)
    kind, next_session = split_signal_calendar(calendar, day)
    inputs = SignalInputs(signal_date, cutoff, kind, date.fromisoformat(next_session) if next_session else None)
    if kind != "month_end":
        if kind == "unknown":
            inputs.gaps.append(dict(code=None, date=day, field="calendar", reason="未能证明交易日或月末",
                                    required=[day, str(pd.Period(day, freq="M").end_time.date())], source_refs=[]))
        return inputs
    ends = [value for value in _complete_month_ends(calendar) if value <= day][-(SIGNAL_LOOKBACK_MONTHS + 1):]
    inputs.month_ends = tuple(ends)
    warmup = _warmup_start(day, SIGNAL_LOOKBACK_MONTHS).isoformat()
    start = ends[0] if ends and ends[0] >= warmup else warmup

    def gap(field, reason, code=None, *, at=day, refs=(), blocking=True):
        inputs.gaps.append(dict(code=code, date=at, field=field, reason=reason, required=[start, day],
                                source_refs=sorted({str(ref) for ref in refs if ref}), blocking=blocking))

    expected = [str(p) for p in pd.period_range(warmup, day, freq="M")]
    if [value[:7] for value in ends] != expected:
        gap("calendar", "未证实 37 个连续完整月末")
        return inputs
    start = ends[0]
    roster_ref, objects = _scope_objects(store, source, start, day, cutoff)
    if not roster_ref or not objects:
        gap("historical_pool", "独立历史普通股原池及覆盖分母未证", refs=[roster_ref])
        return inputs
    frames, action_entries = {}, []
    for table in ("historical_securities", "knowledge_evidence", "security_status", "corporate_actions", "market_cap"):
        frames[table], denied, entries = _signal_table(store, source, table)
        if table == "corporate_actions":
            action_entries = entries
        if denied:
            gap(table, "低波研究用途许可未证", refs=denied, blocking=table != "market_cap")
    identities, active = _signal_identities(frames["historical_securities"], frames["knowledge_evidence"],
                                           objects, start, day, cutoff, gap)
    identity_fields = {"historical_securities", "security_type", "knowledge_evidence"}
    if not any(item["field"] in identity_fields for item in inputs.gaps):
        inputs.pool_size = len(active)
    inputs.identities = pd.DataFrame(identities)
    if not active:
        return inputs
    for owner, ref, manifest in selected_snapshot_assets(store, source, "daily_bars"):
        for entry in manifest["table_files"]:
            if entry["table"] == "daily_bars" and not _purpose_allowed(
                owner, manifest, "daily_bars", entry["product_id"], _SIGNAL_USE
            ):
                gap("daily_bars", "低波研究用途许可未证", refs=[ref.snapshot_id, entry["product_id"]])
    if any(g["field"] == "daily_bars" for g in inputs.gaps):
        return inputs
    windows = _identity_windows(identities, start, cutoff, calendar, gap)
    codes = tuple(sorted({segment["code"] for chain in windows.values() for segment in chain}))
    try:
        closes, missing = read_known_closes(store, source, codes=codes, start=date.fromisoformat(start),
                                           end=signal_date, at=cutoff)
    except AccessDenied as error:
        gap("daily_bars", f"所需日线或公开版本证据未获许可。{error}")
        return inputs
    actions, caps, status = (frames[name] for name in ("corporate_actions", "market_cap", "security_status"))
    cap_rows, action_rows, close_rows = [], [], []
    for code, segments in windows.items():
        cap = select_signal_market_cap(caps, frames["knowledge_evidence"], code, day, store.root)
        if cap:
            cap_rows.append(cap)
        for segment in segments:
            own_code = segment["code"]
            first = max(start, segment["valid_from"])
            last = min(day, segment["valid_to"]) if segment["valid_to"] else day
            scoped_actions = actions[actions.code.eq(own_code)]
            dates = pd.to_datetime(scoped_actions.ex_date, format="%Y-%m-%d", errors="coerce")
            scoped_actions = scoped_actions[dates.isna() | dates.between(first, last)]
            own, errors = _signal_action_inputs(scoped_actions, start, day)
            action_rows.append(own.assign(target_code=code))
            inputs.gaps.extend({**error, "target_code": code, "blocking": True} for error in errors)
            if not _action_coverage(action_entries, own_code, first, last):
                gap("corporate_actions", "实施日查询覆盖未证，无法核实总收益", own_code,
                    refs=[r.snapshot_id for _, r, _, _ in action_entries])
                inputs.gaps[-1]["target_code"] = code
            required = {point for point in ends if first <= point <= last} | set(own.ex_date.astype(str))
            scoped_closes = closes[closes.code.eq(own_code) & closes.date.between(first, last)]
            close_rows.append(scoped_closes.assign(target_code=code))
            have = set(scoped_closes.date)
            suspended = set(status[status.code.eq(own_code) & status.is_suspended.map(_truthy)].date.astype(str))
            for price_day in sorted(required - have):
                existing = missing[missing.code.eq(own_code) & missing.date.eq(price_day)]
                if len(existing):
                    inputs.gaps.extend({**fact, "required": [start, day], "target_code": code, "blocking": True}
                                       for fact in existing.to_dict("records"))
                else:
                    gap("close_raw", "月末有证停牌" if price_day in suspended else "必要原收盘缺证",
                        own_code, at=price_day, refs=[segment["source_ref"]], blocking=price_day not in suspended)
                    inputs.gaps[-1]["target_code"] = code
    inputs.closes = pd.concat(close_rows, ignore_index=True) if close_rows else closes.iloc[:0]
    inputs.actions = pd.concat(action_rows, ignore_index=True) if action_rows else actions.iloc[:0]
    inputs.market_cap = pd.DataFrame(cap_rows)
    return inputs
