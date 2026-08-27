"""Minimal source adapter for the four structured data domains (stage 04).

The adapter is a mechanical helper, not a sixth analysis candidate: it only
parses an explicitly saved raw response, classifies the recorded fetch outcome
into the nine-value closed set, verifies the field mapping, computes the
mechanical equivalence boundaries and emits a fixed-key metadata envelope. It
never fetches anything, never selects or switches sources, never falls back on
its own and never adjudicates adoption; whether to call it, how to save raw
responses and whether to adopt results stay with the Agent. The industry
domain (行业需求与竞争) stays contract-only and is formally rejected here.

Envelope keys are fixed: ``schema_version``, ``adapter``, ``domain``,
``source_id``, ``called``, ``disabled``, ``status``, ``source_metadata``,
``normalized``, ``equivalence``, ``raw_input_sha256``. An available source
reports ``called=true``/``disabled=false`` and one of ``success``,
``not_found``, ``rate_limited``, ``transport_error``, ``permission_denied``,
``parse_error``, ``incomplete_pagination``, ``out_of_asof`` or
``scope_mismatch``, classified in a fixed order: recorded HTTP status (429 →
``rate_limited``; 401/403 → ``permission_denied``; 404 → ``not_found``; any
other non-2xx → ``transport_error``), body parse (structure change →
``parse_error``), pagination / closed-empty retrieval surface, the
subject/period/scope/unit/purpose axes (mismatch → ``scope_mismatch`` with
both original values kept), then content after ``as_of`` (all content later →
``out_of_asof``).

Two evidenced shape tolerances (stage-04 online validation batch; evidence
pointers live in the source contracts, not in this script) are owned by this
adapter, never by the stage-03 tools. First, real cninfo
``hisAnnouncement`` responses carry no body ``pageNum`` and an unreliable
``totalpages`` (saved evidence: ``totalpages=2`` while ``totalAnnouncement=82``),
so announcement pagination closes on the consistent ``totalAnnouncement``
totals, the returned count across pages and the final saved page's
``hasMore``; the request's own page numbers are the saved page order (the
i-th saved page is request page i) unless every page carries an explicit
integer ``pageNum`` (the synthetic payload shape). Second, real Sina
``getFinanceReport2022`` responses hold ``null`` ``item_value`` rows
(not-applicable bank/insurance template items, dropped) and numeric
``item_tongbi`` year-over-year values (coerced to their string form) before
the stage-03 normalizer is invoked; a report whose every item value is null
maps to ``not_found`` (closed surface, no target values).

A disabled source short-circuits before any parsing:
``called=false``, ``disabled=true``, ``status=null``, ``normalized=null`` —
not one of the nine fetch states and not a fetch failure. Source metadata
passes through verbatim; the adapter never generates or backfills it.
``raw_input_sha256`` hashes the canonical JSON serialization of the saved
payload (sorted keys, compact separators, UTF-8).

CLI: ``source_adapter.py --input IN --output OUT`` where the input JSON object
holds ``domain``, ``source_id``, ``saved_payload``, ``subject``, a
timezone-aware ``as_of``, optional ``period``/``scope``/``unit``, ``purpose``
and the formal ``disabled_sources`` list. Exit 0 writes a success envelope;
exit 1 writes a failure envelope for unparseable input or a rejected call
payload (unknown or contract-only domain, missing ``disabled_sources``, or a
saved payload without the ``source_metadata``/``response``/``body`` envelope
blocks); exit 2 covers argument errors, unreadable input, an output path equal
to the input path, or an existing output file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone, tzinfo
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import _artifact_io
import announcement_index
import financial_statements
import numeric_consistency

ADAPTER_NAME = "source_adapter"
SCHEMA_VERSION = "1.0"

DOMAIN_ANNOUNCEMENTS = "公告及附件"
DOMAIN_FINANCIAL = "财务报表"
DOMAIN_PEER_VALUATION = "同业市场估值"
DOMAIN_MARKET_SNAPSHOT = "交易状态与市场快照"
DOMAIN_INDUSTRY = "行业需求与竞争"

STRUCTURED_DOMAINS = frozenset(
    {
        DOMAIN_ANNOUNCEMENTS,
        DOMAIN_FINANCIAL,
        DOMAIN_PEER_VALUATION,
        DOMAIN_MARKET_SNAPSHOT,
    }
)

SINA_BODY_FORMAT = "sina_report_list"
EASTMONEY_BODY_FORMAT = "eastmoney_push2_indicators"

# EastMoney push2 f-code dictionary owned by this adapter (stage 04). The
# stage-03 financial normalizer stays Sina-only and formally rejects EastMoney
# shapes. First-hand basis: the frozen forensic dictionary at
# tests/product/fixtures/forensic/eastmoney-field-dictionary.json.
EASTMONEY_FIELD_DICTIONARY = {
    "f183": "营业总收入",
    "f184": "营业总收入同比增长",
    "f185": "归母净利润同比增长",
    "f186": "销售毛利率",
    "f188": "资产负债率",
}

_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")


def _require_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _optional_text(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _require_text(value, name)


def _require_as_of(value: object) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("as_of must be a timezone-aware datetime")
    return value


def _validate_disabled_sources(values: object) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(
        values, (list, tuple, set, frozenset)
    ):
        raise ValueError("disabled_sources must be a list of source id strings")
    return tuple(_require_text(value, "disabled_sources entry") for value in values)


def _validate_domain(domain: object) -> str:
    if isinstance(domain, str) and domain in STRUCTURED_DOMAINS:
        return domain
    if domain == DOMAIN_INDUSTRY:
        raise ValueError(
            f"domain {DOMAIN_INDUSTRY!r} is contract-only and has no adapter"
        )
    raise ValueError(
        "domain must be one of the four structured domains: "
        f"{sorted(STRUCTURED_DOMAINS)}"
    )


def _envelope(
    domain: str,
    source_id: str,
    *,
    status: str | None,
    source_metadata: dict[str, Any] | None,
    normalized: dict[str, Any] | None,
    equivalence: dict[str, Any] | None,
    raw_input_sha256: str | None,
    disabled: bool = False,
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "adapter": ADAPTER_NAME,
        "domain": domain,
        "source_id": source_id,
        "called": not disabled,
        "disabled": disabled,
        "status": status,
        "source_metadata": source_metadata,
        "normalized": normalized,
        "equivalence": equivalence,
        "raw_input_sha256": raw_input_sha256,
    }


def _hash_saved_payload(saved_payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        saved_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _recorded_status_code(response: object) -> int:
    if not isinstance(response, dict):
        raise ValueError("saved_payload must contain a response object")
    code = response.get("status_code")
    if not isinstance(code, int) or isinstance(code, bool):
        raise ValueError("response.status_code must be an integer")
    return code


def _transport_status(code: int) -> str | None:
    if 200 <= code < 300:
        return None
    if code == 429:
        return "rate_limited"
    if code in (401, 403):
        return "permission_denied"
    if code == 404:
        return "not_found"
    return "transport_error"


def _decimal_value(value: object, *, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError(f"{name} must be a decimal number")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be a decimal number") from exc
    if not number.is_finite():
        raise ValueError(f"{name} must be finite")
    return number


def _parse_date(value: object, *, name: str) -> date:
    if not isinstance(value, str) or not _DATE_PATTERN.fullmatch(value):
        raise ValueError(f"{name} must be a YYYY-MM-DD date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a calendar YYYY-MM-DD date") from exc


def _announcements_content_moment(
    pages: list[Any], as_of: datetime
) -> datetime | None:
    latest: datetime | None = None
    for page in pages:
        if not isinstance(page, dict):
            continue
        announcements = page.get("announcements")
        if not isinstance(announcements, list):
            continue
        for item in announcements:
            if not isinstance(item, dict):
                continue
            timestamp = item.get("announcementTime")
            if isinstance(timestamp, int) and not isinstance(timestamp, bool):
                moment = datetime.fromtimestamp(timestamp / 1000, tz=as_of.tzinfo)
                if latest is None or moment > latest:
                    latest = moment
    return latest


def _explicit_page_number(page: dict[str, Any]) -> bool:
    number = page.get("pageNum")
    return isinstance(number, int) and not isinstance(number, bool)


def _cninfo_pagination_closed(pages: list[Any]) -> bool:
    """Close saved cninfo pagination without trusting ``pageNum``/``totalpages``.

    Real ``hisAnnouncement`` responses carry no body ``pageNum`` and report a
    ``totalpages`` that contradicts the record count (saved evidence:
    ``totalpages=2`` with ``totalAnnouncement=82``), so neither is a closure
    signal here. Closure needs: one consistent ``totalAnnouncement`` across
    pages, a returned announcement count equal to it, consecutive request
    page numbers — every page carrying an explicit integer ``pageNum`` (the
    synthetic payload shape) or, when none does, the saved page order itself
    (the i-th saved page is request page i) — and no open tail signal
    (``hasMore`` true on the final page). The stage-03 tool keeps its own
    stricter ``pageNum``-based rule; this judgment is adapter-owned.
    """
    if not pages:
        return False
    if all(_explicit_page_number(page) for page in pages):
        numbers = [page["pageNum"] for page in pages]
        final_page = max(pages, key=lambda page: page["pageNum"])
    elif not any(_explicit_page_number(page) for page in pages):
        numbers = list(range(1, len(pages) + 1))
        final_page = pages[-1]
    else:
        # Mixed page-number evidence cannot establish the request pages.
        return False
    if sorted(numbers) != list(range(1, max(numbers) + 1)):
        return False
    totals = {page["totalAnnouncement"] for page in pages}
    if len(totals) > 1:
        return False
    returned = sum(len(page["announcements"]) for page in pages)
    return returned == totals.pop() and final_page.get("hasMore") is not True


def _normalize_announcements(
    body: dict[str, Any], as_of: datetime
) -> tuple[dict[str, Any], date | None, datetime | None]:
    pages = body.get("pages")
    if not isinstance(pages, list):
        raise ValueError("announcements body must contain a pages list")
    index = announcement_index.normalize_announcement_pages(pages, as_of=as_of)
    # The stage-03 tool can only close pagination on body ``pageNum``, which
    # real cninfo responses do not carry; the adapter re-judges completeness
    # on totalAnnouncement/hasMore plus the request page order.
    index["page_complete"] = _cninfo_pagination_closed(pages)
    moment = _announcements_content_moment(pages, as_of)
    return index, (moment.date() if moment is not None else None), moment


def _tolerated_sina_report_list(body: dict[str, Any]) -> dict[str, Any]:
    """Apply the evidenced Sina shape tolerance before stage-03 delegation.

    Real ``getFinanceReport2022`` responses hold ``null`` ``item_value`` rows
    (not-applicable bank/insurance template items) and numeric
    ``item_tongbi`` year-over-year values; the stage-03 normalizer keeps its
    string-only schema and formally rejects both. The adapter drops rows
    whose ``item_value`` key is present with a ``null`` value and coerces
    finite numeric ``item_tongbi`` values to their string form
    (``repr``/``str`` are identical for these types); every other value
    passes through verbatim — including rows missing the ``item_value`` key
    entirely, which fall outside the evidenced tolerance and keep mapping to
    ``parse_error`` via the stage-03 rejection path.
    """
    report_list = body["result"]["data"]["report_list"]
    tolerated: dict[str, Any] = {}
    for period, report in report_list.items():
        data = report.get("data") if isinstance(report, dict) else None
        if not isinstance(data, list):
            tolerated[period] = report  # rejected by the stage-03 tool
            continue
        rows: list[Any] = []
        for item in data:
            if not isinstance(item, dict):
                rows.append(item)  # rejected by the stage-03 tool
                continue
            if "item_value" in item and item["item_value"] is None:
                continue
            year_over_year = item.get("item_tongbi")
            if isinstance(year_over_year, float) or (
                isinstance(year_over_year, int)
                and not isinstance(year_over_year, bool)
            ):
                _decimal_value(year_over_year, name="item_tongbi")
                item = {**item, "item_tongbi": str(year_over_year)}
            rows.append(item)
        tolerated[period] = {**report, "data": rows}
    return {"result": {"data": {"report_list": tolerated}}}


def _normalize_financial(
    body: dict[str, Any], as_of: datetime
) -> tuple[dict[str, Any], date | None, datetime | None]:
    body_format = body.get("format")
    if body_format == EASTMONEY_BODY_FORMAT:
        data = body.get("data")
        if not isinstance(data, dict) or not data:
            raise ValueError("eastmoney body must contain a non-empty data object")
        for key, value in data.items():
            if key not in EASTMONEY_FIELD_DICTIONARY:
                raise ValueError(f"unknown EastMoney field code: {key}")
            _decimal_value(value, name=f"eastmoney field {key}")
        period_date = _parse_date(body.get("period"), name="eastmoney period")
        indicators = [
            {
                "code": key,
                "name": EASTMONEY_FIELD_DICTIONARY[key],
                "value": data[key],
            }
            for key in sorted(data)
        ]
        return {"period": body["period"], "indicators": indicators}, period_date, None
    if body_format == SINA_BODY_FORMAT:
        try:
            report_list = body["result"]["data"]["report_list"]
        except (KeyError, TypeError) as exc:
            raise ValueError(
                "sina body must contain result.data.report_list"
            ) from exc
        if not isinstance(report_list, dict):
            raise ValueError("sina report_list must be an object")
        period_count = len(report_list)
        rows = financial_statements.normalize_finance_report(
            _tolerated_sina_report_list(body), limit=max(period_count, 1)
        )
        period_dates = [
            datetime.strptime(key, "%Y%m%d").date() for key in report_list
        ]
        rows = [
            row
            for row in rows
            if date.fromisoformat(str(row["报告期"])) <= as_of.date()
        ]
        content_date = max(period_dates) if period_dates else None
        return {"rows": rows, "period_count": period_count}, content_date, None
    raise ValueError("financial body must declare a known format")


_TRADE_TIME_PATTERN = __import__("re").compile(r"^\d{2}:\d{2}(:\d{2})?$")


_OFFSET_TIMEZONE_PATTERN = re.compile(r"^[+-]\d{2}:\d{2}$")
_IANA_TIMEZONE_PATTERN = re.compile(r"[A-Za-z_]+/[A-Za-z_+]+")
# Saved evidence declares timezones in decorated prose (e.g.
# "北京时间 (Asia/Shanghai)"); only these closed standard-time names map.
_CHINESE_STANDARD_TIME = {
    "北京时间": "Asia/Shanghai",
    "中国标准时间": "Asia/Shanghai",
}


def _resolve_source_timezone(raw: Any) -> tzinfo:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(
            "snapshot trade_time requires a declared timezone "
            "(body or source_metadata 'timezone': IANA name or ±HH:MM)"
        )
    stripped = raw.strip()
    if _OFFSET_TIMEZONE_PATTERN.fullmatch(stripped):
        hours, minutes = (int(part) for part in stripped[1:].split(":"))
        offset = timedelta(hours=hours, minutes=minutes)
        return timezone(-offset if stripped[0] == "-" else offset)
    try:
        return ZoneInfo(stripped)
    except Exception:
        pass
    for name, iana in _CHINESE_STANDARD_TIME.items():
        if name in stripped:
            return ZoneInfo(iana)
    matched = _IANA_TIMEZONE_PATTERN.search(stripped)
    if matched is not None:
        try:
            return ZoneInfo(matched.group(0))
        except Exception:
            pass
    raise ValueError(f"unknown source timezone: {raw}")


def _snapshot_content_moment(
    body: dict[str, Any],
    trade_date: date,
    source_metadata: dict[str, Any],
) -> datetime | None:
    trade_time = body.get("trade_time")
    if trade_time is None:
        return None
    if not isinstance(trade_time, str) or not _TRADE_TIME_PATTERN.fullmatch(trade_time):
        raise ValueError("snapshot trade_time must be an HH:MM[:SS] string")
    # The wall-clock time belongs to the SOURCE's timezone, not as_of's:
    # binding it to as_of.tzinfo misplaces same-day instants across zones.
    tz = _resolve_source_timezone(
        body.get("timezone", source_metadata.get("timezone"))
    )
    parts = trade_time.split(":")
    hour, minute = int(parts[0]), int(parts[1])
    second = int(parts[2]) if len(parts) == 3 else 0
    return datetime(
        trade_date.year,
        trade_date.month,
        trade_date.day,
        hour,
        minute,
        second,
        tzinfo=tz,
    )


def _normalize_snapshot(
    body: dict[str, Any],
    as_of: datetime,
    source_metadata: dict[str, Any],
) -> tuple[dict[str, Any], date | None, datetime | None]:
    trade_date = _parse_date(body.get("trade_date"), name="snapshot trade_date")
    if "price" not in body:
        raise ValueError("snapshot body must contain a price")
    _decimal_value(body["price"], name="snapshot price")
    for unit_key in (
        "price_unit",
        "total_shares_unit",
        "float_shares_unit",
        "market_cap_unit",
    ):
        value = body.get(unit_key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"snapshot {unit_key} must be a non-empty string")
    return (
        dict(body),
        trade_date,
        _snapshot_content_moment(body, trade_date, source_metadata),
    )


def _normalize_domain_body(
    domain: str,
    body: dict[str, Any],
    as_of: datetime,
    source_metadata: dict[str, Any],
) -> tuple[dict[str, Any], date | None, datetime | None]:
    if domain == DOMAIN_ANNOUNCEMENTS:
        return _normalize_announcements(body, as_of)
    if domain == DOMAIN_FINANCIAL:
        return _normalize_financial(body, as_of)
    return _normalize_snapshot(body, as_of, source_metadata)


def _snapshot_checks(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Mechanically check labeled market caps against price-times-shares.

    Both the labeled and the recomputed value are always kept; labels are
    never swapped. A verdict is only produced when the labeled unit equals the
    price unit; otherwise the check records both values with a null verdict.
    """
    recomputed: dict[str, Decimal] = {}
    needed = (
        "price_unit",
        "total_shares",
        "total_shares_unit",
        "float_shares",
        "float_shares_unit",
    )
    if all(key in body for key in needed):
        total_cap, float_cap = numeric_consistency.market_cap_values(
            price=body["price"],
            price_unit=body["price_unit"],
            total_shares=body["total_shares"],
            total_shares_unit=body["total_shares_unit"],
            float_shares=body["float_shares"],
            float_shares_unit=body["float_shares_unit"],
        )
        recomputed = {
            "total_market_cap": total_cap,
            "float_market_cap": float_cap,
        }
    market_cap_unit = body.get("market_cap_unit")
    price_unit = body.get("price_unit")
    checks: list[dict[str, Any]] = []
    for key, label in (
        ("total_market_cap", "total_market_cap_vs_price_times_shares"),
        ("float_market_cap", "float_market_cap_vs_price_times_shares"),
    ):
        if key not in body or market_cap_unit is None:
            continue
        labeled = _decimal_value(body[key], name=f"snapshot {key}")
        comparable = key in recomputed and market_cap_unit == price_unit
        checks.append(
            {
                "check": label,
                "labeled": {"value": format(labeled, "f"), "unit": market_cap_unit},
                "recomputed": (
                    {
                        "value": format(recomputed[key], "f"),
                        "unit": price_unit,
                    }
                    if key in recomputed
                    else None
                ),
                "consistent": (
                    labeled == recomputed[key] if comparable else None
                ),
            }
        )
    return checks


def _equivalence(
    saved_payload: dict[str, Any],
    *,
    subject: str,
    period: str | None,
    scope: str | None,
    unit: str | None,
    purpose: str,
    as_of: datetime,
    content_date: date | None,
    content_moment: datetime | None,
    body_unit: str | None,
    body_subject: str | None,
    body_periods: set[str] | None,
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    axes: dict[str, dict[str, Any]] = {}

    # Subject axis: cross-check the body's actual security identifier on
    # canonical exchange-marked codes; uncanonicalizable identifiers stay
    # mechanically unverifiable (match=null), never silently equivalent.
    declared_subject = saved_payload.get("subject")
    known_subjects = [
        value
        for value in (subject, declared_subject, body_subject)
        if value is not None
    ]
    canonical_subjects = [_canonical_security(value) for value in known_subjects]
    axes["subject"] = {
        "requested": subject,
        "payload": declared_subject,
        "body": body_subject,
        "match": (
            None
            if len(known_subjects) < 2
            or not all(c is not None for c in canonical_subjects)
            else len(set(canonical_subjects)) == 1
        ),
    }

    # Period axis: cross-check the body's actual period coverage (snapshot
    # trade_date; financial report periods). A requested period the body
    # demonstrably does not cover is a real mismatch.
    declared_period = saved_payload.get("period")
    body_period_list = sorted(body_periods) if body_periods else None
    single_values = [
        value
        for value in (period, declared_period)
        if value is not None
    ]
    if len(single_values) >= 2 or (single_values and body_period_list):
        canonical_singles = set()
        for value in single_values:
            try:
                canonical_singles.add(date.fromisoformat(value).isoformat())
            except ValueError:
                canonical_singles.add(None)
        canonical_body = set()
        for value in body_period_list or []:
            try:
                canonical_body.add(date.fromisoformat(value).isoformat())
            except ValueError:
                canonical_body.add(None)
        if None in canonical_singles or None in canonical_body:
            period_match = None
        else:
            period_match = len(canonical_singles) <= 1 and (
                not canonical_body or canonical_singles <= canonical_body
            )
    else:
        period_match = None
    axes["period"] = {
        "requested": period,
        "payload": declared_period,
        "body": body_period_list,
        "match": period_match,
    }

    for name, requested, recorded in (
        ("scope", scope, saved_payload.get("scope")),
        ("purpose", purpose, saved_payload.get("purpose")),
    ):
        match = None if requested is None or recorded is None else (
            requested == recorded
        )
        axes[name] = {"requested": requested, "payload": recorded, "match": match}
    # The unit axis cross-checks the body's actual unit (snapshot price_unit)
    # against the wrapper-declared and requested values. Comparison runs on
    # canonical currency families (e.g. 元/CNY/RMB are one family): a real
    # contradiction (CNY vs USD) is match=false, while descriptive labels
    # that do not canonicalize (e.g. "价格元、市值亿元（腾讯标签）") are
    # mechanically unverifiable — match=null, never silently equivalent.
    declared_unit = saved_payload.get("unit")
    known_units = [value for value in (unit, declared_unit, body_unit) if value is not None]
    canonical_units = [_canonical_unit(value) for value in known_units]
    axes["unit"] = {
        "requested": unit,
        "payload": declared_unit,
        "body": body_unit,
        "match": (
            None
            if len(known_units) < 2 or not all(c is not None for c in canonical_units)
            else len(set(canonical_units)) == 1
        ),
    }
    if content_moment is not None:
        after_asof = content_moment > as_of
    elif content_date is None:
        after_asof = None
    elif content_date != as_of.date():
        after_asof = content_date > as_of.date()
    else:
        # Same-day snapshot without a time: the instant relative to an
        # intraday as_of is indeterminate — recorded as null, never as a
        # silent claim that the content precedes as_of.
        after_asof = None
    return {
        "axes": axes,
        "as_of": {
            "as_of": as_of.isoformat(),
            "content_date": (
                content_date.isoformat() if content_date is not None else None
            ),
            "content_moment": (
                content_moment.isoformat()
                if content_moment is not None
                else None
            ),
            "content_after_asof": after_asof,
        },
        "checks": checks,
    }


def _completeness_status(
    domain: str, normalized: dict[str, Any]
) -> str | None:
    if domain == DOMAIN_ANNOUNCEMENTS:
        if not normalized["page_complete"]:
            return "incomplete_pagination"
        if normalized["total_count"] == 0:
            return "not_found"
        return None
    if domain == DOMAIN_FINANCIAL:
        if normalized.get("period_count") == 0:
            return "not_found"
        if "rows" in normalized and normalized["rows"] and not any(
            key != "报告期" for row in normalized["rows"] for key in row
        ):
            # Every item value in the closed report_list was null: the
            # retrieval surface is closed but no target values exist.
            return "not_found"
    return None


def _all_content_after_asof(
    domain: str,
    normalized: dict[str, Any],
    content_date: date | None,
    content_moment: datetime | None,
    as_of: datetime,
) -> bool:
    if content_moment is not None:
        if content_moment <= as_of:
            return False
    elif content_date is None or content_date <= as_of.date():
        return False
    if domain == DOMAIN_ANNOUNCEMENTS:
        return normalized["usable_count"] == 0 and normalized["total_count"] > 0
    if domain == DOMAIN_FINANCIAL and "period_count" in normalized:
        return not normalized["rows"] and normalized["period_count"] > 0
    return True


_SECURITY_CODE_PATTERN = re.compile(
    r"^(?:([A-Za-z]{2}))?(\d{6})(?:\.([A-Za-z]{2}))?$"
)


def _canonical_security(value: str) -> str | None:
    """Canonicalize exchange-marked A-share codes to code-dot-exchange.

    An exchange-prefixed code and its suffixed spelling canonicalize to the
    same value; a bare six-digit code without an exchange marker, or a
    display name, is mechanically unverifiable.
    """
    matched = _SECURITY_CODE_PATTERN.fullmatch(value.strip())
    if matched is None:
        return None
    prefix, code, suffix = matched.group(1), matched.group(2), matched.group(3)
    exchange = (suffix or prefix or "").upper()
    if exchange in {"SZ", "SH", "BJ"}:
        return f"{code}.{exchange}"
    return None


_UNIT_FAMILIES: tuple[frozenset[str], ...] = (
    frozenset({"元", "cny", "rmb", "人民币", "人民币元", "￥"}),
    frozenset({"usd", "美元", "us$", "$"}),
    frozenset({"hkd", "港币", "港元", "hk$"}),
    frozenset({"eur", "欧元", "€"}),
)


def _canonical_unit(value: str) -> str | None:
    normalized = value.strip().lower()
    for family in _UNIT_FAMILIES:
        if normalized in family:
            return sorted(family)[0]
    return None


def _financial_body_periods(body: dict[str, Any]) -> set[str] | None:
    body_format = body.get("format")
    if body_format == SINA_BODY_FORMAT:
        container = body
        for key in ("result", "data", "report_list"):
            if not isinstance(container, dict):
                return None
            container = container.get(key)
        if not isinstance(container, dict) or not container:
            return None
        return {
            f"{key[:4]}-{key[4:6]}-{key[6:8]}" for key in container
        }
    if body_format == EASTMONEY_BODY_FORMAT:
        period = body.get("period")
        return {period} if isinstance(period, str) else None
    return None


_METADATA_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("identity", ("provider", "producer", "channel", "identity", "source")),
    ("entry", ("entry", "endpoint", "url")),
    (
        "captured_at",
        ("fetch_request_time", "fetch_request_times", "captured_at", "collected_at"),
    ),
    (
        "field_basis",
        (
            "field_basis",
            "raw_file",
            "raw_files",
            "annual_report_reference",
            "share_capital_basis",
            "field_dictionary",
        ),
    ),
    ("license", ("license", "licensing")),
)


def _metadata_value_shape_ok(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list) and value:
        return all(isinstance(item, str) and item.strip() for item in value)
    return False


def _validate_source_metadata(metadata: dict[str, Any], *, source_id: str) -> None:
    """Require the five metadata categories the plan demands, verbatim.

    The adapter never generates or backfills metadata; it only rejects saved
    payloads whose metadata cannot attest identity, entry, capture time,
    field basis and licensing. Key aliases cover the canonical synthetic
    schema and the saved batch shapes; values must be non-empty (a non-empty
    list of non-empty strings where lists are used).
    """
    for category, keys in _METADATA_CATEGORIES:
        if not any(_metadata_value_shape_ok(metadata.get(key)) for key in keys):
            raise ValueError(
                "source_metadata must carry a usable "
                f"{category} field ({'/'.join(keys)})"
            )
    entry = next(
        (metadata[key] for key in _METADATA_CATEGORIES[1][1] if key in metadata),
        None,
    )
    if not isinstance(entry, str) or not (
        entry.startswith("http://") or entry.startswith("https://")
    ):
        raise ValueError("source_metadata entry must be an http(s) URL")
    captured = next(
        (metadata[key] for key in _METADATA_CATEGORIES[2][1] if key in metadata),
        None,
    )
    stamps = captured if isinstance(captured, list) else [captured]
    for stamp in stamps:
        if not isinstance(stamp, str):
            raise ValueError("source_metadata capture time must be a string")
        try:
            moment = datetime.fromisoformat(stamp)
        except ValueError as exc:
            raise ValueError(
                "source_metadata capture time must be an ISO-8601 datetime"
            ) from exc
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError(
                "source_metadata capture time must be timezone-aware"
            )
    declared_id = metadata.get("source_id")
    if isinstance(declared_id, str) and declared_id != source_id:
        raise ValueError(
            "source_metadata source_id must match the caller's source_id"
        )


def adapt_saved_response(
    domain: str,
    source_id: str,
    saved_payload: dict[str, Any],
    *,
    subject: str,
    as_of: datetime,
    period: str | None,
    scope: str | None,
    unit: str | None,
    purpose: str,
    disabled_sources: tuple[str, ...] = (),
) -> dict[str, object]:
    """Adapt one explicitly saved raw response into a metadata envelope."""
    _validate_domain(domain)
    resolved_source_id = _require_text(source_id, "source_id")
    resolved_subject = _require_text(subject, "subject")
    resolved_purpose = _require_text(purpose, "purpose")
    resolved_as_of = _require_as_of(as_of)
    resolved_period = _optional_text(period, "period")
    resolved_scope = _optional_text(scope, "scope")
    resolved_unit = _optional_text(unit, "unit")
    disabled = _validate_disabled_sources(disabled_sources)

    if resolved_source_id in disabled:
        # Disabled sources short-circuit before the payload is touched at all:
        # nothing is parsed, hashed or classified, and the outcome is not a
        # fetch failure. Whether to use a substitute stays with the Agent.
        return _envelope(
            domain,
            resolved_source_id,
            status=None,
            source_metadata=None,
            normalized=None,
            equivalence=None,
            raw_input_sha256=None,
            disabled=True,
        )

    if not isinstance(saved_payload, dict):
        raise ValueError("saved_payload must be an object")
    source_metadata = saved_payload.get("source_metadata")
    if not isinstance(source_metadata, dict):
        raise ValueError("saved_payload must contain a source_metadata object")
    _validate_source_metadata(source_metadata, source_id=resolved_source_id)
    status_code = _recorded_status_code(saved_payload.get("response"))
    for key in ("subject", "period", "scope", "unit", "purpose"):
        _optional_text(saved_payload.get(key), f"saved_payload {key}")
    raw_input_sha256 = _hash_saved_payload(saved_payload)

    transport_status = _transport_status(status_code)
    if transport_status is not None:
        return _envelope(
            domain,
            resolved_source_id,
            status=transport_status,
            source_metadata=source_metadata,
            normalized=None,
            equivalence=None,
            raw_input_sha256=raw_input_sha256,
        )

    body = saved_payload.get("body")
    if not isinstance(body, dict):
        raise ValueError("saved_payload must contain a body object for a 2xx response")

    try:
        normalized, content_date, content_moment = _normalize_domain_body(
            domain, body, resolved_as_of, source_metadata
        )
        checks = (
            _snapshot_checks(body)
            if domain in (DOMAIN_PEER_VALUATION, DOMAIN_MARKET_SNAPSHOT)
            else []
        )
    except ValueError:
        # A body that no longer fits the domain structure is a classified
        # source-side parse failure, not a rejected adapter call.
        return _envelope(
            domain,
            resolved_source_id,
            status="parse_error",
            source_metadata=source_metadata,
            normalized=None,
            equivalence=None,
            raw_input_sha256=raw_input_sha256,
        )

    equivalence = _equivalence(
        saved_payload,
        subject=resolved_subject,
        period=resolved_period,
        scope=resolved_scope,
        unit=resolved_unit,
        purpose=resolved_purpose,
        as_of=resolved_as_of,
        content_date=content_date,
        content_moment=content_moment,
        body_unit=(
            body.get("price_unit")
            if domain in (DOMAIN_PEER_VALUATION, DOMAIN_MARKET_SNAPSHOT)
            and isinstance(body.get("price_unit"), str)
            else None
        ),
        body_subject=(
            body.get("security")
            if domain in (DOMAIN_PEER_VALUATION, DOMAIN_MARKET_SNAPSHOT)
            and isinstance(body.get("security"), str)
            else None
        ),
        body_periods=(
            _financial_body_periods(body)
            if domain == DOMAIN_FINANCIAL
            else (
                {content_date.isoformat()}
                if domain in (DOMAIN_PEER_VALUATION, DOMAIN_MARKET_SNAPSHOT)
                and content_date is not None
                else None
            )
        ),
        checks=checks,
    )
    completeness = _completeness_status(domain, normalized)
    if completeness is not None:
        status = completeness
    elif any(axis["match"] is False for axis in equivalence["axes"].values()):
        status = "scope_mismatch"
    elif _all_content_after_asof(
        domain, normalized, content_date, content_moment, resolved_as_of
    ):
        status = "out_of_asof"
    else:
        status = "success"
    return _envelope(
        domain,
        resolved_source_id,
        status=status,
        source_metadata=source_metadata,
        normalized=normalized,
        equivalence=equivalence,
        raw_input_sha256=raw_input_sha256,
    )


def _transform(payload: dict[str, Any]) -> object:
    as_of_raw = payload.get("as_of")
    if not isinstance(as_of_raw, str):
        raise ValueError("input must contain an as_of ISO-8601 datetime string")
    try:
        as_of = datetime.fromisoformat(as_of_raw)
    except ValueError as exc:
        raise ValueError("as_of must be an ISO-8601 datetime") from exc
    if "disabled_sources" not in payload:
        raise ValueError("input must contain the formal disabled_sources list")
    return adapt_saved_response(
        payload.get("domain"),
        payload.get("source_id"),
        payload.get("saved_payload"),
        subject=payload.get("subject"),
        as_of=as_of,
        period=payload.get("period"),
        scope=payload.get("scope"),
        unit=payload.get("unit"),
        purpose=payload.get("purpose"),
        disabled_sources=payload["disabled_sources"],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Adapt one saved raw response for a structured domain."
    )
    parser.add_argument(
        "--input", required=True, help="saved response and request JSON path"
    )
    parser.add_argument(
        "--output", required=True, help="envelope JSON path to create"
    )
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    output_path = Path(args.output)
    if input_path.resolve() == output_path.resolve():
        print("output_path must differ from input_path", file=sys.stderr)
        return 2
    if output_path.exists():
        print("output_path already exists", file=sys.stderr)
        return 2
    if not input_path.is_file():
        print(f"input file not found: {input_path}", file=sys.stderr)
        return 2

    try:
        _artifact_io.run_transform(
            tool_name="source_adapter",
            input_path=input_path,
            output_path=output_path,
            transform=_transform,
        )
    except _artifact_io.OutputConflictError as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 2
    except _artifact_io.InputUnreadableError as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 2
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
