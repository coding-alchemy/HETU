"""Requests, raw facts, and inspectable outputs for the factor library."""

import json
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

CN = ZoneInfo("Asia/Shanghai")


def close_cutoff(day: date) -> datetime:
    return datetime.combine(day, time(15), CN)


@dataclass(frozen=True)
class TeachingSource:
    case_id: str
    root: Path | None = None


@dataclass(frozen=True)
class FactorRequest:
    factor_id: str
    subjects: tuple[str, ...]
    trade_date: date
    cutoff: datetime
    parameters: dict[str, object]


@dataclass
class FactorResult:
    factor_id: str
    version: str
    parameters: dict
    subjects: tuple[str, ...]
    trade_date: date
    cutoff: datetime
    source: dict
    status: str
    rows: list[dict]
    gaps: list[dict]


class MissingFact(ValueError):
    def __init__(self, field, reason, *, subject=None, period=None, evidence=None, event=None):
        super().__init__(reason)
        self.field = field
        self.subject = subject
        self.period = period
        self.evidence = evidence
        self.event = event


@dataclass
class FactorFacts:
    tables: dict[str, pd.DataFrame]
    source: dict
    gaps: list[dict] = field(default_factory=list)
    versions: dict[str, list[dict]] = field(default_factory=dict)
    daily_authorizations: list[dict] = field(default_factory=list)

    def records(self, name, subject=None):
        frame = self.tables.get(name)
        if frame is None or frame.empty:
            return []
        if subject is not None and "code" in frame:
            frame = frame[frame.code.eq(subject)]
        return frame.astype(object).where(frame.notna(), None).to_dict("records")

    def result(self, request, rows, issues=()):
        gaps = list(self.gaps)
        for error in issues:
            gaps.append(
                dict(
                    subject=error.subject or ", ".join(request.subjects),
                    field=error.field,
                    period=error.period or str(request.trade_date),
                    source=self.source,
                    reason=str(error),
                    **({"evidence": error.evidence} if error.evidence else {}),
                    **({"event": error.event} if error.event else {}),
                )
            )
        status = ("partial" if gaps else "available") if rows else "unavailable"
        return FactorResult(
            request.factor_id,
            "1",
            request.parameters,
            request.subjects,
            request.trade_date,
            request.cutoff,
            self.source,
            status,
            rows,
            gaps,
        )


def output(subject, value, unit, components, inputs):
    return dict(subject=subject, value=value, unit=unit, components=components, inputs=inputs)


def require(condition, field, reason, **coordinate):
    if not condition:
        raise MissingFact(field, reason, **coordinate)


def check_conflict(row, field, *, any_field=False):
    conflicts = row.get("_conflicts") or {}
    if field in conflicts or (any_field and conflicts):
        evidence = conflicts.get(field) or [item for entries in conflicts.values() for item in entries]
        reason = "同一坐标存在互不替代的冲突版本"
        if field == "revision_cycle":
            reason = "记录的替代关系形成循环"
        elif field == "event_identity":
            reason = "行动身份有歧义，缺少区分事项的公告依据"
        elif field == "d":
            if any(item.get("observation_status") == "unavailable_at_cutoff" for item in evidence):
                reason = "缺少与截止时点的分红原件匹配的原生表版本"
            elif any(item.get("matched") is False for item in evidence):
                reason = "所选原生表与截止时点有效的分红公告不匹配"
        elif any("observed_source_ref" in item and item.get("matched") is False for item in evidence):
            reason = "所选原生表金额与截止时点有效的财报凭据不匹配"
        elif any(item.get("observation_status") == "unavailable_at_cutoff" for item in evidence):
            reason = "缺少与截止时点的财报凭据匹配的原生表值"
        _raise_fact_conflict(row, field, reason, evidence)


def _raise_fact_conflict(row, field, reason, evidence):
    raise MissingFact(field, reason, subject=row.get("code"),
                      period=row.get("date") or row.get("period") or row.get("ex_date"),
                      event=row.get("event_id"), evidence=evidence)


def check_revision_cycle(row, field):
    if evidence := (row.get("_revision_cycles") or {}).get(field):
        _raise_fact_conflict(row, "revision_cycle", "记录的替代关系形成循环", evidence)


def iso_day(value):
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        return None


def event_in_window(row, start, end):
    disputed = (row.get("_conflicts") or {}).get("ex_date")
    if disputed:
        alternatives = [
            iso_day(item.get("ex_date", item.get("value"))) for item in disputed
            if item.get("revision_role") != "common_previous"
        ]
        if all(day and not start < day <= end for day in alternatives):
            return None
    day = iso_day(row.get("ex_date"))
    if not disputed and day and not start < day <= end:
        return None
    first = iso_day(row.get("earliest_ex_date"))
    last = iso_day(row.get("latest_ex_date"))
    if not (disputed or day) and ((first and first > end) or (last and last <= start)):
        return None
    for attribute in ("revision_cycle", "kind", "event_identity", "ex_date"):
        check_conflict(row, attribute)
    if day:
        return day
    raise MissingFact(
        "ex_date",
        "行动实施日缺证且可能落入窗口",
        subject=row.get("code"),
        period=[start, end],
        event=row.get("event_id"),
        evidence=[row],
    )


def number(row, field):
    from math import isfinite

    check_revision_cycle(row, field)
    check_conflict(row, field)
    value = row.get(field)
    require(
        isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value),
        field,
        "字段值缺失或无效",
        subject=row.get("code"),
        period=row.get("date") or row.get("period") or row.get("ex_date"),
        event=row.get("event_id"),
        evidence=[row] if row.get("source_ref") or row.get("evidence_ref") else None,
    )
    return float(value)


def share_count(row, field):
    scale = {"股": 1, "万股": 10_000, "手": 100}.get(row.get(f"{field}_unit", "股"))
    require(scale is not None, f"{field}_unit", "股数单位不可核对")
    return number(row, field) * scale


def field_attribute(row, field, attribute):
    evidence = (row.get("field_evidence") or {}).get(field, {})
    if attribute in evidence:
        return evidence[attribute]
    scoped = f"{field}_{attribute}"
    check_conflict(row, scoped)
    return row.get(scoped)


def quotation(row, field, attribute):
    fields = (field,) if isinstance(field, str) else field
    for candidate in fields:
        evidence = (row.get("field_evidence") or {}).get(candidate, {})
        value = field_attribute(row, candidate, attribute)
        if attribute in evidence or value is not None:
            return value
        if attribute == "currency":
            for unit in (evidence.get("unit"), row.get(f"{candidate}_unit")):
                if currency := _unit_currency(unit):
                    return currency
    check_conflict(row, attribute)
    value = row.get(attribute)
    if attribute == "currency" and value is None:
        return _unit_currency(row.get("unit"))
    return value


def _unit_currency(unit):
    if isinstance(unit, str):
        code = unit.split("/", 1)[0]
        if code in ("美元", "人民币"):
            return "USD" if code == "美元" else "CNY"
        if len(code) == 3 and code.isascii() and code.isupper():
            return code
    return None


def money_in_yuan(value, currency, unit, **where):
    require(currency in (None, "CNY") and unit != "USD", "currency", "无人民币换算凭据", **where)
    scale = {None: 1, "CNY": 1, "元": 1, "人民币元": 1, "万元": 10_000, "亿元": 100_000_000}.get(unit)
    require(scale is not None, "unit", "金额单位缺可比凭据", **where)
    return float(Decimal(str(value)) * scale) if scale != 1 else value


def financial_amount_in_yuan(value, currency, unit, **where):
    require(currency == "CNY" and unit in ("元", "万元", "亿元"),
            "currency/unit", "缺人民币和金额单位的可比凭据", **where)
    return money_in_yuan(value, currency, unit, **where)


def yuan_per_stock_share(value, currency, unit, **where):
    # Older A-share records store original close and cash per old share in yuan.
    require(currency in (None, "CNY"), "currency", "每股金额缺人民币换算凭据", **where)
    require(
        unit in (None, "CNY", "元", "元/股", "CNY/股", "分/股"),
        "unit",
        "缺每股人民币金额口径",
        **where,
    )
    return float(Decimal(str(value)) / 100) if unit == "分/股" else value


def stock_share_amount(row, field):
    return yuan_per_stock_share(
        number(row, field),
        quotation(row, field, "currency"),
        quotation(row, field, "unit"),
        subject=row.get("code"),
        period=row.get("ex_date") or row.get("date"),
        event=row.get("event_id"),
        evidence=[row],
    )


def price_per_share(value, currency, unit, **where):
    unit_divisor = {None: 1, currency: 1, "元/份": 1, "分/份": 100, f"{currency}/份": 1}.get(unit)
    require(
        unit_divisor is not None and (unit not in ("元/份", "分/份") or currency == "CNY"),
        "unit",
        "ETF 场价或净值缺同币种每份换算依据",
        **where,
    )
    return value / unit_divisor


def net_assets_unit(row):
    unit = quotation(row, "net_assets", "unit")
    evidence = (row.get("field_evidence") or {}).get("net_assets", {})
    if (
        isinstance(unit, str) and unit.endswith("/份")
        and evidence.get("unit") is None and row.get("net_assets_unit") is None
    ):
        # Legacy ETF rows put the NAV's per-share unit beside a total already expressed in yuan.
        return None
    return unit


def positive(value, field):
    require(value > 0, field, "必须为正数")
    return value


def window(request, key="window", minimum=1):
    value = request.parameters.get(key)
    require(type(value) is int and value >= minimum, key, f"窗口至少为 {minimum}")
    return value


def by_subject(facts, request, calculate):
    rows, issues = [], []
    for subject in request.subjects:
        try:
            rows.append(calculate(subject))
        except MissingFact as error:
            error.subject = error.subject or subject
            issues.append(error)
    return facts.result(request, rows, issues)


def parameter_identity(parameters):
    return json.dumps(parameters, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def resolve_request(definition, request):
    parameters = {**definition.defaults, **request.parameters}
    for key, value in parameters.items():
        if key in ("skip", "months", "lag"):
            require(type(value) is int and value >= 0, key, "需要非负整数")
        elif "window" in key or key in ("short", "long", "lookback", "days", "top", "max_levels"):
            require(type(value) is int and value > 0, key, "需要正整数")
        elif isinstance(definition.defaults.get(key), str):
            require(isinstance(value, str), key, "需要字符串")
        elif isinstance(definition.defaults.get(key), (int, float)):
            number(parameters, key)
    return replace(request, parameters=parameters)
