"""Point-in-time evidence shared by account qualification and execution."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path
from zoneinfo import ZoneInfo


def execution_cutoff(day: str) -> datetime:
    return datetime.combine(date.fromisoformat(day), time(9, 30), ZoneInfo("Asia/Shanghai"))


def _publication(value) -> datetime | None:
    try:
        published = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return published if published.tzinfo is not None else None


def original_record(directory: Path, evidence_ref, sha256):
    directory = directory.resolve()
    path = (directory / str(evidence_ref)).resolve()
    if not path.is_relative_to(directory) or not path.is_file():
        return None
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != str(sha256):
        return None
    try:
        original = json.loads(raw)
    except (ValueError, UnicodeError):
        return None
    return original if isinstance(original, dict) else None


def cny_per_share(value, currency, unit):
    if currency != "CNY" or unit not in ("元/股", "分/股", "CNY/share", "fen/share"):
        return None
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount < 0:
        return None
    return amount / 100 if unit in ("分/股", "fen/share") else amount


def _iso_date(value):
    try:
        day = date.fromisoformat(str(value))
    except ValueError:
        return None
    return day.isoformat()


def _present(value):
    return str(value) not in ("", "None", "<NA>", "nan", "NaT")


@dataclass
class _DispositionClaim:
    kind: str
    source: str
    row: dict
    original: dict
    missing_original: bool = False

    @property
    def coordinate(self):
        root = self.row.get("evidence_root")
        return self.kind, self.original.get("event_id"), str(root) if _present(root) else None, self.source


def _cash_terms(claim, cutoff):
    original, row = claim.original, claim.row
    currency, unit = original.get("currency"), original.get("unit")
    price = cny_per_share(original.get("cash_per_share"), currency, unit)
    prepared = cny_per_share(
        row.get("cash_per_share"),
        row["currency"] if _present(row.get("currency")) else currency,
        row["unit"] if _present(row.get("unit")) else unit,
    )
    claim_time = _publication(row.get("available_at"))
    matching = (bool(original) and (not _present(row.get("event_id")) or row["event_id"] == original["event_id"])
                and claim_time is not None and claim_time <= cutoff and price is not None and price == prepared)
    return price, prepared, matching


def _exchange_entitlement(original, day):
    old_shares, new_shares = original.get("old_shares"), original.get("new_shares")
    ratio = (Fraction(new_shares, old_shares)
             if type(new_shares) is type(old_shares) is int and new_shares > 0 and old_shares > 0 else None)
    credit = _iso_date(original.get("credit_date"))
    return (ratio, credit if credit and credit >= day else None,
            original.get("share_rule") if original.get("share_rule") == "whole_shares" else None)


def _load_originals(groups, rows, evidence_dir, start, end, as_of, *, kind, code_field, day_field):
    for row in rows:
        day = _iso_date(row[day_field])
        if not day or not start <= day <= end:
            continue
        code, source = str(row[code_field]), str(row.get("evidence_ref"))
        cutoff = min(execution_cutoff(day), as_of) if as_of is not None else execution_cutoff(day)
        root = Path(row["evidence_root"]) / "evidence" if _present(row.get("evidence_root")) else evidence_dir
        original = original_record(root, source, row.get("sha256")) or {}
        published = _publication(original.get("available_at"))
        event = original.get("event_id")
        proved = (original.get("kind") == kind and original.get(code_field) == code and original.get(day_field) == day
                  and isinstance(event, str) and bool(event) and published is not None)
        if kind == "special_exchange":
            proved = (proved and _present(row.get("event_id")) and row["event_id"] == event
                      and original.get("to_code") == row["to_code"] and code != row["to_code"])
        if published and published > cutoff and (proved or kind == "cash_exit"):
            continue
        groups[code, day].append(_DispositionClaim(kind, source, row, original if proved else {}, not original))


def _effective_claims(claims):
    """Resolve replacement paths while every dated original is still visible."""
    by_source, by_announcement, proved = defaultdict(set), defaultdict(set), {}
    for claim in claims:
        if not claim.original:
            continue
        coordinate = claim.coordinate
        proved[coordinate] = claim.original
        by_source[claim.source].add(coordinate)
        for label in (claim.source, claim.original.get("revision_id")):
            if isinstance(label, str):
                by_announcement[(*coordinate[:2], label)].add(coordinate)

    previous = {}
    local = set()
    for coordinate, original in proved.items():
        supersedes = original.get("supersedes")
        if not isinstance(supersedes, str):
            continue
        targets = by_announcement[(*coordinate[:2], supersedes)]
        if targets:
            if len(targets) == 1 and coordinate not in targets:
                previous[coordinate] = next(iter(targets))
                local.add(coordinate)
            continue
        # A documented withdrawal precedes settlement checks on the remaining terms.
        targets = by_source[supersedes]
        if len(targets) == 1 and coordinate not in targets:
            previous[coordinate] = next(iter(targets))

    def prior_chain(coordinate):
        visited = {coordinate}
        while coordinate in previous:
            coordinate = previous[coordinate]
            if coordinate in visited:
                return
            visited.add(coordinate)
            yield coordinate

    # Circular declarations do not establish which original replaces the other.
    cyclic = {coordinate for coordinate, prior in previous.items() if coordinate in prior_chain(prior)}
    previous = {coordinate: prior for coordinate, prior in previous.items() if coordinate not in cyclic}
    withdrawn = set(previous.values())
    active = [claim for claim in claims if not claim.original or claim.coordinate not in withdrawn]

    def version(coordinate):
        kind, event, root, source = coordinate
        original = proved[coordinate]
        return dict(kind=kind, source_ref=source, evidence_root=root, event_id=event,
                    revision_id=original.get("revision_id"), supersedes=original.get("supersedes"),
                    locally_superseded_sources=[previous[coordinate][3]] if (
                        coordinate in local and coordinate in previous) else [])

    versions = {}
    for claim in active:
        if not claim.original:
            continue
        coordinate = current = claim.coordinate
        local_history, cross_history = [], []
        for prior in prior_chain(coordinate):
            (local_history if current in local else cross_history).append(version(prior))
            current = prior
        versions[coordinate] = dict(version(coordinate), prior_local_versions=local_history,
                                    prior_cross_versions=cross_history)
    return active, versions


def _share_exchange(active, versions, code, day):
    if not active:
        return None, None
    source_refs = sorted({claim.source for claim in active})
    parsed = []
    for claim in active:
        original = claim.original
        ratio, credit, share_rule = _exchange_entitlement(original, day)
        sellable, payment = map(_iso_date, (original.get("sellable_date"), original.get("fraction_pay_date")))
        parsed.append({
            "event_id": original.get("event_id"),
            "code": original.get("to_code"),
            "ratio": ratio,
            "credit_date": credit,
            "sellable_date": sellable if credit and sellable and sellable >= credit else None,
            "share_rule": share_rule,
            "fraction_rule": original.get("fraction_rule") if original.get("fraction_rule") == "cash_in_lieu" else None,
            "fraction_cash_cny": cny_per_share(
                original.get("fraction_cash_per_share"), original.get("fraction_currency"),
                original.get("fraction_unit"),
            ),
            "fraction_pay_date": payment if credit and payment and payment >= credit else None,
        })
    candidates = []
    if not all(claim.original for claim in active):
        terms, missing = {}, ["original_terms"]
    elif any((terms["event_id"], terms["code"]) != (parsed[0]["event_id"], parsed[0]["code"]) for terms in parsed[1:]):
        terms, missing = {}, ["conflicting_originals"]
    else:
        terms = {name: value if all(other[name] == value for other in parsed[1:]) else None
                 for name, value in parsed[0].items()}
        missing = [name for name, value in terms.items() if value is None]
        candidates = [dict(source_ref=claim.source, cash_per_new_share_cny=float(facts["fraction_cash_cny"]))
                      for claim, facts in zip(active, parsed, strict=True) if facts["fraction_cash_cny"] is not None]
        candidates.sort(key=lambda item: (item["source_ref"], item["cash_per_new_share_cny"]))
    event = {
        **terms, "from_code": code, "effective_date": day, "kind": "special_exchange",
        "source_refs": source_refs, "missing_fields": missing,
        "original_versions": _version_details(active, versions), "evidence_ref": min(source_refs),
        "unproved_original_refs": sorted({claim.source for claim in active if not claim.original}),
    }
    if "fraction_cash_cny" in missing and candidates:
        event["fraction_cash_candidates"] = candidates
    gap = None
    if missing:
        gap = {"code": code, "date": day, "reason": "share_exchange_terms_unproved",
               "fields": missing, "source_refs": source_refs,
               "event_ids": sorted({str(c.row["event_id"]) for c in active if _present(c.row.get("event_id"))})}
        if missing_originals := sorted({claim.source for claim in active if claim.missing_original}):
            gap.update(reason="share_exchange_original_unavailable", blocks_start=True,
                       expected_originals=missing_originals)
    return event, gap


def _version_details(active, versions):
    coordinates = {claim.coordinate for claim in active if claim.original}
    return [versions[coordinate] for coordinate in sorted(coordinates, key=lambda coord: (coord[3], coord[2] or ""))]


def _cash_disposal(active, versions, code, day, cutoff):
    if not active:
        return None, None
    entries = [(claim, *_cash_terms(claim, cutoff)) for claim in active]
    source_refs = sorted({claim.source for claim in active})
    original_versions = _version_details(active, versions)
    values = {(claim.original.get("event_id"), price) for claim, price, _, _ in entries}
    if not all(matching for _, _, _, matching in entries) or len(values) != 1:
        observed = [dict(source_ref=claim.source, event_id=claim.original["event_id"],
                         cash_per_share=float(price) if price is not None else None,
                         prepared_cash_per_share=float(prepared) if prepared is not None else None)
                    for claim, price, prepared, _ in sorted(entries, key=lambda entry: entry[0].source)
                    if claim.original]
        return None, {
            "code": code, "date": day, "reason": "exit_disposal_conflicting_terms" if len(values) > 1
            else "exit_disposal_terms_unproved", "source_refs": source_refs,
            "cash_claims": observed, "original_versions": original_versions,
            "unproved_original_refs": sorted({claim.source for claim in active if not claim.original}),
        }
    claim, price, _, _ = min(entries, key=lambda entry: entry[0].source)
    return {
        **claim.row, "event_id": claim.original["event_id"], "cash_per_share": float(price), "currency": "CNY",
        "unit": "元/股", "source_refs": source_refs, "original_versions": original_versions,
    }, None


def _cash_price_label(amount):
    return "unknown" if amount is None else f"{amount:g}"


def account_dispositions(cash_rows, exchange_rows, evidence_dir, start, end, *, as_of=None):
    """Choose mutually exclusive cash and exchange exits before either can change the entitlement."""
    groups = defaultdict(list)
    _load_originals(groups, cash_rows, evidence_dir, start, end, as_of,
                    kind="cash_exit", code_field="code", day_field="date")
    _load_originals(groups, exchange_rows, evidence_dir, start, end, as_of,
                    kind="special_exchange", code_field="from_code", day_field="effective_date")
    cash, exchanges, cash_gaps, exchange_gaps, conflicts = {}, {}, [], [], []
    for code, day in sorted(groups):
        key = code, day
        cutoff = min(execution_cutoff(day), as_of) if as_of is not None else execution_cutoff(day)
        active, versions = _effective_claims(groups[key])
        payout, cash_gap = _cash_disposal([c for c in active if c.kind == "cash_exit"], versions, code, day, cutoff)
        exchange, exchange_gap = _share_exchange(
            [c for c in active if c.kind == "special_exchange"], versions, code, day,
        )
        if cash_gap:
            cash_gaps.append(cash_gap)
        if exchange_gap:
            exchange_gaps.append(exchange_gap)
        if payout:
            cash[key] = payout
        if exchange:
            exchanges[key] = exchange
        payout = payout or (cash_gap if cash_gap and cash_gap["cash_claims"] else None)
        if not payout or not exchange:
            continue
        ratio = str(exchange["ratio"]) if exchange.get("ratio") else "unknown"
        references = sorted(set(payout["source_refs"] + exchange["source_refs"]))
        cash_claims = payout.get("cash_claims") or [dict(source_ref=ref, event_id=payout["event_id"],
                      cash_per_share=payout["cash_per_share"]) for ref in payout["source_refs"]]
        observed_prices = {claim["cash_per_share"] for claim in cash_claims}
        prices = " or ".join(_cash_price_label(price) for price in sorted(observed_prices - {None})) or "unknown"
        mismatches = sorted({f"{claim['source_ref']}: prepared {_cash_price_label(claim['prepared_cash_per_share'])}"
                             " CNY/share" for claim in cash_claims
                             if "prepared_cash_per_share" in claim
                             and claim["prepared_cash_per_share"] != claim["cash_per_share"]})
        versions = payout["original_versions"] + exchange["original_versions"]
        event_ids = sorted({entry["event_id"] for entry in versions})
        conflicts.append(dict(code=code, date=day, reason="incompatible_account_dispositions",
                              blocks_start=True, source_refs=references, cash_claims=cash_claims,
                              original_versions=versions,
                              terms=f"cash_exit {prices} CNY/share; share_exchange {ratio}"
                                    f" → {exchange.get('code') or 'unknown'}"
                                    + ("; " + "; ".join(mismatches) if mismatches else ""),
                              event_ids=event_ids))
        cash.pop(key, None)
        exchanges[key] = {**exchange, "ratio": None, "source_refs": references}
    return cash, exchanges, cash_gaps, exchange_gaps, conflicts
