"""原八定义与独立目标审计；资格复用默认只引用，不生成策略信号。"""

from __future__ import annotations

import importlib.util
import json
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from hetu_quant import backtest_data
from hetu_quant.backtest_data import (
    _evidence_original_ok,
    _proven_month_ends,
    _signal_days,
    _truthy,
    _warmup_start,
    select_known_versions,
)
from hetu_quant.data import action_query_covers
from hetu_quant.data_archive import _coverage_rows, _history_objects, _ranges, audit_archive, scan_local_coverage

CN = ZoneInfo("Asia/Shanghai")


def _definitions(payload):
    # 复用既有脚本的纯定义展开，避免复制冻结/阶段依赖逻辑。
    path = Path(__file__).resolve().parents[2] / "research/run_validation.py"
    spec = importlib.util.spec_from_file_location("hetu_frozen_validation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validation_definitions(dict(payload))


def research_data_requirements(payload, calendar, history):
    definitions = _definitions(payload)
    signals, starts = set(), []
    for item in definitions:
        definition = item["definition"]
        days = _signal_days(
            calendar,
            date.fromisoformat(definition["start"]),
            date.fromisoformat(definition["end"]),
            definition["protocol"]["rebalance"]["frequency"],
        )
        item["signal_dates"] = days
        item["history_start"] = (
            _warmup_start(days[0], definition["protocol"]["warmup_months"]).isoformat() if days else definition["start"]
        )
        starts.append(item["history_start"])
        signals.update(days)
    start = min(starts)
    study = payload["definition"]
    objects, outside, unknown = _history_objects(history, start, study["end"])
    legal = []
    for obj in objects:
        if not obj["attributes_known"]:
            continue
        reason = (
            "outside_research_market"
            if obj["market"] not in ("SH", "SZ")
            else "not_common_stock"
            if obj["security_type"] != "stock"
            else "pre_study_exit"
            if obj["lifecycle_known"] and obj["valid_to"] and obj["valid_to"] < study["start"]
            else "not_listed_during_study"
            if obj["lifecycle_known"] and obj["valid_from"] > study["end"]
            else None
        )
        if reason:
            obj["excluded"] = True
            legal.append(
                dict(
                    identity_id=obj["identity_id"],
                    code=obj["code"],
                    reason=reason,
                    source_ref=obj.get("source_ref", ""),
                )
            )
    return dict(
        purpose="research",
        definitions=definitions,
        objects=objects,
        start=start,
        end=study["end"],
        study_start=study["start"],
        signal_dates=sorted(signals),
        required_intervals=[
            dict(identity_id=o["identity_id"], code=o["code"], interval=o["required"]) for o in objects
        ],
        expected_sessions=sorted(
            str(d)[:10]
            for d, flag in zip(calendar.date, calendar.is_trading_day, strict=False)
            if _truthy(flag) and start <= str(d)[:10] <= study["end"]
        ),
        calendar_days=sorted(set(calendar.date.astype(str))),
        lifecycle_exclusions=outside,
        legal_exclusions=legal,
        unknown_ranges=[r for r in unknown if not any(e["identity_id"] == r["identity_id"] for e in legal)],
        target_scope=dict(history.attrs.get("target_scope", {})),
    )


def _historical_versions(inventory, at):
    hist = inventory.frames.get("historical_securities", pd.DataFrame())
    knowledge = inventory.frames.get("knowledge_evidence", pd.DataFrame())
    if hist.empty or knowledge.empty:
        return pd.DataFrame()
    evidence = knowledge[knowledge.table_name.eq("historical_securities")].copy()
    evidence = evidence.rename(columns={"record_key": "code"})
    cols = ["code", "evidence_ref", "sha256", "revision_id", "supersedes", "published_at", "verified", "evidence_root"]
    merged = hist.drop(columns=["evidence_root"], errors="ignore").merge(evidence[cols], on=["code", "evidence_ref"])
    known = select_known_versions(merged, at, keys=("code",))
    if known.empty:
        return known
    return known[known.apply(lambda r: _evidence_original_ok(Path(r.evidence_root), r.evidence_ref, r.sha256), axis=1)]


def _known_capital(caps, knowledge, code, day, at):
    if caps.empty:
        return False
    own = caps[caps.code.eq(code) & caps.date.astype(str).eq(day)]
    if own.empty:
        return False
    legacy = own[own.revision_id.fillna("").eq("")] if "revision_id" in own else own
    if len(legacy):
        available = pd.to_datetime(legacy.available_at, format="ISO8601", errors="coerce", utc=True)
        if ((available <= pd.Timestamp(at)) & pd.to_numeric(legacy.market_cap_cny, errors="coerce").gt(0)).any():
            return True
    if "revision_id" in own:
        own = own[own.revision_id.fillna("").ne("")]
    if own.empty:
        return False
    if "revision_id" in own and own.revision_id.notna().any():
        if knowledge.empty:
            return False
        evidence = knowledge[knowledge.table_name.eq("market_cap")].copy()
        evidence["code"] = evidence.record_key.astype(str).str.split("|").str[0]
        columns = [
            "code",
            "revision_id",
            "published_at",
            "verified",
            "supersedes",
            "evidence_ref",
            "sha256",
            "evidence_root",
        ]
        merged = own.drop(
            columns=["published_at", "verified", "supersedes", "evidence_ref", "sha256", "evidence_root"],
            errors="ignore",
        ).merge(evidence[columns], on=["code", "revision_id"])
        if merged.empty:
            return False
        selected = select_known_versions(merged, at, keys=("code", "date"))
        return len(selected) == 1 and _evidence_original_ok(
            Path(selected.iloc[0].evidence_root), selected.iloc[0].evidence_ref, selected.iloc[0].sha256
        )
    # 保留既有未版本化合同的available_at语义，不把新版本观察时间升级为公开时间。
    available = pd.to_datetime(own.available_at, format="ISO8601", errors="coerce", utc=True)
    return bool(((available <= pd.Timestamp(at)) & pd.to_numeric(own.market_cap_cny, errors="coerce").gt(0)).any())


def audit_research_coverage(requirements, store, ref, *, inventory=None):
    inventory = inventory or scan_local_coverage(store, ref, asset_snapshots=requirements.get("asset_snapshots", ()))
    rows = _coverage_rows(requirements, inventory, {"daily_bars": "bars", "security_status": "status"})
    # 复用档案日历审计，但不把档案因子门强加研究。
    base = audit_archive(store, ref, requirements, inventory=inventory)
    caps = inventory.frames.get("market_cap", pd.DataFrame())
    caps_by_code = {str(code): group for code, group in caps.groupby("code")} if len(caps) else {}
    empty_caps = pd.DataFrame()
    knowledge = inventory.frames.get("knowledge_evidence", pd.DataFrame())
    actions = inventory.frames.get("corporate_actions", pd.DataFrame())
    signal_evidence, limitations = [], []
    month_anchors = _proven_month_ends(inventory.calendar)
    needed = [o for o in requirements["objects"] if not o.get("excluded")]
    history_gaps, capital_gaps = defaultdict(list), defaultdict(list)
    applicable_signals = defaultdict(list)
    rules = inventory.frames.get("trading_rules", pd.DataFrame())
    for day in requirements["signal_dates"]:
        at = datetime.fromisoformat(day + "T21:00:00+08:00")
        known = _historical_versions(inventory, at)
        for obj in needed:
            if obj["lifecycle_known"] and (obj["valid_from"] > day or (obj["valid_to"] and obj["valid_to"] < day)):
                continue
            applicable_signals[obj["identity_id"]].append(day)
            own = known[known.code.eq(obj["code"])] if len(known) else known
            revisions = own.revision_id.astype(str).tolist() if len(own) else []
            if not revisions:
                history_gaps[obj["identity_id"]].append(day)
            cap_known = _known_capital(caps_by_code.get(obj["code"], empty_caps), knowledge, obj["code"], day, at)
            if not cap_known:
                capital_gaps[obj["identity_id"]].append(day)
            signal_evidence.append(
                dict(
                    code=obj["code"],
                    identity_id=obj["identity_id"],
                    date=day,
                    history_revision=revisions,
                    market_cap_known=cap_known,
                )
            )
    for obj in needed:
        for domain, gaps in [("historical_public_evidence", history_gaps), ("market_cap", capital_gaps)]:
            rows.append(
                dict(
                    code=obj["code"],
                    identity_id=obj["identity_id"],
                    domain=domain,
                    required=obj["required"],
                    covered=_ranges(
                        set(applicable_signals[obj["identity_id"]]) - set(gaps[obj["identity_id"]]),
                        requirements["signal_dates"],
                    ),
                    observed_signals=len(applicable_signals[obj["identity_id"]]) - len(gaps[obj["identity_id"]]),
                    data_gaps=[],
                    unknown_ranges=_ranges(gaps[obj["identity_id"]], requirements["signal_dates"]),
                    signal_dates_unknown=gaps[obj["identity_id"]],
                    source_refs=[obj.get("source_ref", "")],
                )
            )
        start, end = obj["required"]
        anchors = [day for day in month_anchors if day[:7] == start[:7]]
        action_start = anchors[0] if anchors else start
        entries = [e for e in inventory.action_entries if e.get("partition", "").split("|")[0] == "code=" + obj["code"]]
        action_covered = any(action_query_covers(entry, action_start, end) for entry in entries)
        rows.append(
            dict(
                code=obj["code"],
                identity_id=obj["identity_id"],
                domain="corporate_actions",
                required=[action_start, end],
                covered=[[action_start, end]] if action_covered else [],
                data_gaps=[],
                unknown_ranges=[] if action_covered else [[action_start, end]],
                source_refs=[e.get("path", "") for e in entries],
                coverage_semantics="operate/ex_date查询证明；不等于事件内容及字段齐备",
            )
        )
        if not action_covered:
            limitations.append(
                dict(
                    code=obj["code"],
                    domain="corporate_actions",
                    reason="operate_ex_date_coverage_unproved",
                    required=[action_start, end],
                )
            )
        own = actions[actions.code.eq(obj["code"])] if len(actions) else actions
        for row in own.to_dict("records"):
            if not action_start <= str(row.get("ex_date", "")) <= end:
                continue
            kind = row.get("action_type")
            required = ["d"] if kind == "cash_dividend" else ["m"]
            if str(row.get("ex_date", "")) >= requirements["study_start"]:
                required += (
                    ["record_date", "pay_date"] if kind == "cash_dividend" else ["record_date", "stock_listing_date"]
                )
            missing = [k for k in required if not row.get(k) or pd.isna(row.get(k))]
            if kind not in ("cash_dividend", "bonus_split") or missing:
                limitations.append(
                    dict(
                        code=obj["code"],
                        domain="corporate_actions",
                        date=row.get("ex_date"),
                        reason="unsupported_or_missing_event_fields",
                        action_type=kind,
                        missing_fields=missing,
                    )
                )
        if (
            obj["lifecycle_known"]
            and obj["valid_to"]
            and requirements["study_start"] <= obj["valid_to"] <= requirements["end"]
        ):
            disposals = inventory.frames.get("terminal_disposals", pd.DataFrame())
            if disposals.empty or not disposals.code.eq(obj["code"]).any():
                limitations.append(dict(code=obj["code"], domain="terminal_disposals", reason="exit_disposal_unproved"))
        rule_start = max(requirements["study_start"], start)
        sessions = [d for d in requirements["expected_sessions"] if rule_start <= d <= end]
        covered_rules = set()
        refs = []
        if len(rules) and obj.get("board") and obj["lifecycle_known"]:
            own_rules = rules[
                rules.market.eq(obj["market"]) & rules.board.eq(obj["board"]) & rules.security_type.eq("stock")
            ]
            for rule in own_rules.to_dict("records"):
                fields = [
                    "min_buy_qty",
                    "qty_step",
                    "tick_size",
                    "settlement",
                    "limit_rule",
                    "fee_rule",
                    "source_ref",
                ]
                if any(pd.isna(rule.get(k)) or not rule.get(k) for k in fields):
                    continue
                left, right = str(rule.get("valid_from", "")), str(rule.get("valid_to") or end)
                covered_rules.update(d for d in sessions if left <= d <= right)
                refs.append(rule["source_ref"])
        rows.append(
            dict(
                code=obj["code"],
                identity_id=obj["identity_id"],
                domain="trading_rules",
                required=[rule_start, end],
                covered=_ranges(covered_rules, sessions),
                observed_sessions=len(covered_rules),
                data_gaps=[],
                unknown_ranges=_ranges(set(sessions) - covered_rules, sessions),
                source_refs=sorted(set(refs)),
                coverage_semantics="已存普通股规则的有效期；身份/板块/PIT及特殊状态仍分别核验",
            )
        )
    need_financials = any(d["definition"]["strategy_id"] == "ep_value" for d in requirements["definitions"])
    if need_financials and inventory.frames.get("financial_reports", pd.DataFrame()).empty:
        limitations.append(dict(domain="financial_reports", reason="ep_PIT_financials_missing"))
    unknown = [item for item in base["unknown_ranges"] if item.get("domain") != "adjustment_factors"]
    ready = bool(needed) and requirements["target_scope"].get("complete") is True and not unknown
    ready = (
        ready
        and not limitations
        and not inventory.failures
        and not any(r["data_gaps"] or r["unknown_ranges"] for r in rows)
    )
    return dict(
        full_scope_ready=ready,
        coverage=rows,
        legal_exclusions=requirements["legal_exclusions"],
        unknown_ranges=unknown,
        event_limitations=limitations,
        signal_evidence=signal_evidence,
        optional_limitations=["industry_exposure"] + ([] if need_financials else ["financial_reports_for_EP_only"]),
        target_scope_complete=requirements["target_scope"].get("complete") is True,
        read_failures=inventory.failures,
        source_stats=inventory.stats,
    )


def prepare_research_delivery(payload, store, ref, output_dir, *, inventory=None):
    from hetu_quant.backtest_models import backtest_definition_from_dict

    output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=False, parents=True)
    inventory = inventory or scan_local_coverage(store, ref, asset_snapshots=payload.get("asset_snapshots", ()))
    if payload.get("target_history_ref"):
        history = pd.read_csv(payload["target_history_ref"], dtype=str).fillna("")
    else:
        history = inventory.frames.get("historical_securities", pd.DataFrame(columns=["code"]))
    history.attrs["target_scope"] = dict(payload.get("target_scope", {}))
    requirements = research_data_requirements(payload, inventory.calendar, history)
    coverage = audit_research_coverage(requirements, store, ref, inventory=inventory)
    mode = payload.get("qualification_mode", "reuse")
    if mode not in ("reuse", "prepare"):
        raise ValueError("unknown qualification mode")
    qualifications, completed = [], {}
    for item in requirements["definitions"]:
        definition = dict(item["definition"], snapshot_id=ref.snapshot_id)
        key = (
            definition["start"],
            definition["end"],
            definition["protocol"]["warmup_months"],
            "backtest_ep" if definition["strategy_id"] == "ep_value" else "backtest_low_volatility",
            definition["mode"],
        )
        if mode == "prepare":
            if key not in completed:
                report = backtest_data.prepare_backtest(
                    backtest_definition_from_dict(definition), store, output_dir / f"qualification-{len(completed)}"
                )
                completed[key] = dict(can_run=report.can_run, eligible_ids=list(report.eligible_ids))
            qualifications.append(dict(trial_id=item["trial_id"], reused_need=key, report=completed[key]))
        else:
            qualifications.append(
                dict(
                    trial_id=item["trial_id"],
                    status="not_recomputed",
                    evidence_refs=list(payload.get("qualification_references", [])),
                )
            )
    handoff = dict(
        new_qualifications=len(completed),
        research_runs=0,
        qualification_mode=mode,
        qualification_references=list(payload.get("qualification_references", [])),
        full_scope_ready=coverage["full_scope_ready"],
        study_id=payload.get("study_id"),
    )
    result = dict(requirements=requirements, coverage=coverage, qualifications=qualifications, handoff=handoff)
    for name, value in result.items():
        (output_dir / f"{name}.json").write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")
    return result
