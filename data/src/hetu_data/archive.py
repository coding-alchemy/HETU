"""离线生命周期和实际行审计；研究/档案共用只读事实扫描，不获取数据。"""

from __future__ import annotations

import json
import re
import shutil
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd

from hetu_data.access import AccessDenied
from hetu_data.models import DataRequest, SnapshotRef
from hetu_data.store import TABLE_SCHEMAS, DataStore, SnapshotTamperedError, _sha256, query_column
from hetu_data.utils import _truthy

_BASE = date(1970, 1, 1)
_BITS: dict[str, int] = {}
_BAR_FIELDS = ["open_raw", "high_raw", "low_raw", "close_raw", "volume_shares", "amount_cny"]
_PRICE_FIELDS = _BAR_FIELDS[:4]
_TABLES = (
    "calendar",
    "daily_bars",
    "security_status",
    "security_events",
    "adjustment_factors",
    "corporate_actions",
    "historical_securities",
    "market_cap",
    "trading_rules",
    "knowledge_evidence",
    "terminal_disposals",
    "financial_reports",
)


ARCHIVE_COLUMNS = ("code", "date", *_BAR_FIELDS, "product_id", "source_ref")


def _valid_archive_bars(values):
    valid = values.notna().all(axis=1) & values.ge(0).all(axis=1) & values[_PRICE_FIELDS].gt(0).all(axis=1)
    return valid & values.high_raw.ge(values[_PRICE_FIELDS].max(axis=1)) & values.low_raw.le(
        values[_PRICE_FIELDS].min(axis=1)
    )


def _archive_trade_evidence(values, valid):
    return valid | values[["volume_shares", "amount_cny"]].gt(0).any(axis=1)


def _official_no_trade(events):
    return events.product_id.eq("cn_a_archive_no_trade") & events.event_type.eq("confirmed_no_trade") & (
        events.source_ref.fillna("").astype(str).str.strip().ne("")
    )


def _query_boolean(value):
    """查询解释必须保留未知值，不能用真值测试把未知变成休市／正常状态。"""
    text = str(value).strip().lower()
    if text in {"true", "1", "1.0"}:
        return True
    if text in {"false", "0", "0.0"}:
        return False
    return None


def open_archive_view(store: DataStore, ref: SnapshotRef) -> duckdb.DuckDBPyConnection:
    """内存目录里的Parquet只读视图；不复制价格表，不是任意SQL的安全沙箱。"""
    paths = store._range_files(ref, "daily_bars")
    con = duckdb.connect()
    try:
        if paths:
            con.read_parquet(paths, union_by_name=True).project(
                ",".join(query_column("daily_bars", c) for c in TABLE_SCHEMAS["daily_bars"]["columns"])
            ).create_view("daily_bars")
        else:
            con.register("daily_bars", pd.DataFrame(columns=TABLE_SCHEMAS["daily_bars"]["columns"]))
        mode = store.load_manifest(ref).get("mode", "free")
        for (pid,) in con.execute("SELECT DISTINCT product_id FROM daily_bars").fetchall():
            product = store.product(str(pid))
            store.check_access(mode, product, operation="read")
            if product.get("fee_class") != "paid":
                continue
            grant = store._grant(str(pid)) or {}
            invalid = con.execute(
                "SELECT count(*) FROM daily_bars WHERE product_id = ? AND (code IS NULL "
                "OR code NOT IN (SELECT unnest(?)) OR split_part(code, '.', -1) NOT IN (SELECT unnest(?)) "
                "OR date IS NULL OR date NOT BETWEEN ? AND ?)",
                [pid, grant.get("codes", []), grant.get("markets", []), grant.get("start", "~"), grant.get("end", "")],
            ).fetchone()[0]
            if invalid:
                raise AccessDenied("SQL view rows exceed current product grant; use bounded read_range")
        return con
    except Exception:
        con.close()
        raise


def query_archive(store, ref, *, codes, start, end):
    """网页和Notebook共同入口；缺口只解释本次范围，不替代全档案资格。"""
    bars = store.read_range(ref, "daily_bars", codes=codes, start=start, end=end, columns=ARCHIVE_COLUMNS)

    def read(table, fields, product_id=None):
        return store.read_range(ref, table, codes=codes, start=start, end=end, columns=fields, product_id=product_id)

    gaps, auxiliary_limits = _archive_query_details(read, bars, codes, start, end)
    return dict(
        snapshot_id=ref.snapshot_id,
        mode=store.load_manifest(ref).get("mode", "free"),
        bars=bars,
        gaps=gaps,
        auxiliary_limits=auxiliary_limits,
    )


def _archive_query_details(read, bars, codes, start, end):
    limitations = []

    def auxiliary(table, fields, product_id=None):
        try:
            return read(table, fields, product_id)
        except AccessDenied as error:
            limitations.append(dict(table=table, reason=str(error)))
            return pd.DataFrame(columns=fields)

    gaps = _archive_gaps(
        bars, auxiliary("calendar", ("date", "is_trading_day", "source_ref")),
        auxiliary("securities", ("code", "ipo_date", "out_date", "source_ref")),
        auxiliary("security_status", ("code", "date", "is_suspended", "source_ref")),
        auxiliary("security_events", ("code", "date", "event_type", "product_id", "source_ref"),
                  "cn_a_archive_no_trade"),
        codes, start, end,
    )
    return gaps, limitations


def _archive_gaps(bars, calendar, identities, status, events, codes, start, end):
    official_dates = set(events.loc[_official_no_trade(events), ["code", "date"]].itertuples(index=False, name=None))
    days = pd.date_range(start, end).strftime("%Y-%m-%d").tolist()
    calendars = {str(day): {_query_boolean(v) for v in group.is_trading_day} for day, group in calendar.groupby("date")}
    statuses = {
        (str(code), str(day)): {_query_boolean(v) for v in group.is_suspended}
        for (code, day), group in status.groupby(["code", "date"])
    }
    missing = []
    for code in codes:
        own = bars[bars.code.eq(code)]
        identity = identities.loc[identities.code.eq(code), ["ipo_date", "out_date", "source_ref"]]
        identity = identity.astype("string").fillna("")
        versions = identity[["ipo_date", "out_date"]].drop_duplicates()
        known = len(versions) == 1 and bool(versions.iloc[0].ipo_date) and identity.source_ref.ne("").all()
        left, right = (str(versions.iloc[0].ipo_date), str(versions.iloc[0].out_date)) if known else ("", "")
        reasons = defaultdict(list)
        if not known:
            reasons["lifecycle_unknown"].extend(days)
        observed = set(own.date.astype(str))
        traded = set()
        if official_dates:
            values = own[_BAR_FIELDS]
            traded = set(own.loc[_archive_trade_evidence(values, _valid_archive_bars(values)), "date"].astype(str))
        for day in days:
            if (code, day) in official_dates:
                reason = "conflicting_trade_evidence" if day in traded else "confirmed_no_trade"
            elif day in observed:
                continue
            elif known and day < left:
                reason = "not_listed"
            elif known and right and day > right:
                reason = "after_exit"
            else:
                flags = calendars.get(day, set())
                if len(flags) != 1 or None in flags:
                    reason = "calendar_unknown"
                elif not next(iter(flags)):
                    continue
                else:
                    values = statuses.get((code, day), set())
                    reason = "suspended" if values == {True} else "missing_bar"
            reasons[reason].append(day)
        if not own.empty:
            incomplete = own[_BAR_FIELDS].isna().any(axis=1)
            reasons["fields_missing"].extend(own.loc[incomplete, "date"].astype(str))
            distinct = own.drop_duplicates(["date", *_BAR_FIELDS])
            conflicts = distinct.duplicated("date", keep=False)
            reasons["conflicting_bars"].extend(distinct.loc[conflicts, "date"].astype(str))
        for reason, dates in reasons.items():
            for first, last in _ranges(dates):
                missing.append(dict(code=code, start=first, end=last, reason=reason))
    return pd.DataFrame(missing, columns=["code", "start", "end", "reason"])


_BACKUP_PRODUCT_FIELDS = {
    "provider", "fee_class", "kind", "tables", "requires_codes", "provider_timezone", "storage_layout",
    "partition", "cross_section", "fixed_market", "markets", "source_api",
}
_BACKUP_GRANT_FIELDS = {
    "product_id", "approved_scope_ref", "license_ref", "allowed_operations", "markets", "codes",
    "start", "end", "acquire_until", "retained_use", "acquire_cross_section", "backup_cross_section",
}


def _backup_config(store, products):
    config = {}
    grants = []
    for product_id in sorted(products):
        raw = store.product(product_id)
        permission = raw.get("permission", {})
        config[product_id] = {key: raw[key] for key in _BACKUP_PRODUCT_FIELDS & raw.keys()}
        config[product_id]["permission"] = {
            key: permission[key] for key in ("status", "allowed_operations") if key in permission
        }
        if grant := store._grant(product_id):
            grants.append({key: grant[key] for key in _BACKUP_GRANT_FIELDS & grant.keys()})
    return {
        Path("data-products.json"): dict(products=config),
        **({Path("data-entitlements.json"): dict(grants=grants)} if grants else {}),
    }


def _locked_backup_file(base, relative, digest, *, label="locked asset mismatch"):
    path = (base / relative).resolve()
    if not path.is_relative_to(base.resolve()) or not path.is_file() or _sha256(path) != digest:
        raise SnapshotTamperedError(f"{label}: {relative}")
    return path


def _check_backup_metadata(document):
    if isinstance(document, dict):
        for key, value in document.items():
            normalized = key.lower().replace("_", "").replace("-", "")
            if any(word in normalized for word in ("token", "password", "secret", "authorization", "apikey")):
                raise AccessDenied("locked metadata contains a credential field")
            _check_backup_metadata(value)
    elif isinstance(document, list):
        for value in document:
            _check_backup_metadata(value)


def _check_backup_query(product, grant, query, observed_at):
    api, params = query.get("api"), query.get("params")
    if not api or api != product.get("source_api") or not isinstance(params, dict):
        raise AccessDenied("raw backup API or query scope is not proven")
    codes = str(params.get("ts_code") or "").split(",") if params.get("ts_code") else ()
    exchanges = {"SSE": "SH", "SZSE": "SZ", "BSE": "BJ"}
    exchange = params.get("exchange")
    if api == "trade_cal" and (codes or exchange not in exchanges):
        raise AccessDenied("raw backup calendar exchange is not proven")
    if codes:
        markets = {code.rsplit(".", 1)[-1] for code in codes}
    elif exchange in exchanges:
        markets = {exchanges[exchange]}
    elif exchange:
        raise AccessDenied("raw backup query market is not proven")
    else:
        markets = set(product.get("markets", ()))
    if (
        not markets or not markets <= set(grant.get("markets", ()))
        or not set(codes) <= set(grant.get("codes", ()))
    ):
        raise AccessDenied("raw backup query outside granted object or market")
    if not codes and api != "trade_cal" and grant.get("backup_cross_section") is not True:
        raise AccessDenied("whole-market original requires explicit backup permission")
    try:
        if "trade_date" in params:
            if {"start_date", "end_date"} & params.keys():
                raise ValueError
            start = end = date.fromisoformat(params["trade_date"]).isoformat()
        elif "start_date" in params or "end_date" in params:
            start, end = (date.fromisoformat(params[key]).isoformat() for key in ("start_date", "end_date"))
        elif api in {"stock_basic", "bse_mapping"} and observed_at:
            observed = datetime.fromisoformat(observed_at)
            if observed.tzinfo is None:
                raise ValueError
            start = end = observed.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
        else:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise AccessDenied("raw backup query period is not proven") from None
    if not grant.get("start", "~") <= start <= end <= grant.get("end", ""):
        raise AccessDenied("raw backup query outside granted period")
    return set(codes), markets, start, end


def _check_backup_raw_files(product, grant, item, base):
    files = item.get("raw_files", ())
    item_query = item.get("query")
    item_time = item.get("source", {}).get("obtained_at")
    # A prepared parquet is the local representation of the provider responses in this item.
    # An imported original, or any unrecognized legacy shape, requires its own recorded query.
    proved = {entry["sha256"] for entry in files if entry.get("api") and "params" in entry}
    for entry in files:
        path = _locked_backup_file(base, entry["path"], entry["sha256"])
        if entry.get("api") and "params" in entry:
            query = entry
        elif item_query:
            query = item_query
        elif proved and re.fullmatch(
            r"response-.+-\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}\.parquet", Path(entry["path"]).name,
        ):
            try:
                digests = pd.read_parquet(path, columns=["_response_sha256"])["_response_sha256"]
            except (OSError, ValueError, KeyError):
                raise AccessDenied("prepared raw backup file has no proven response lineage") from None
            if not digests.isin(proved).all():
                raise AccessDenied("prepared raw backup file has no proven response lineage")
            continue
        else:
            raise AccessDenied("raw backup file has no proven query")
        codes, markets, start, end = _check_backup_query(product, grant, query, entry.get("obtained_at") or item_time)
        try:
            data = json.loads(path.read_bytes())["data"]
            rows = pd.DataFrame(data["items"], columns=data["fields"])
            if query["api"] == "trade_cal" and not rows["exchange"].eq(query["params"]["exchange"]).all():
                raise AccessDenied("raw backup calendar outside granted market")
            if "ts_code" in rows:
                received = rows["ts_code"].astype(str)
                if (codes and not received.isin(codes).all()) or not (
                    received.str.rsplit(".", n=1).str[-1].isin(markets).all()
                ):
                    raise AccessDenied("raw backup response outside granted object or market")
            for clock in ("trade_date", "cal_date"):
                if clock in rows and not rows[clock].map(
                    lambda value: date.fromisoformat(str(value)).isoformat()
                ).between(start, end).all():
                    raise AccessDenied("raw backup response outside granted period")
        except (KeyError, TypeError, ValueError):
            raise AccessDenied("raw backup response scope is not proven") from None


def _snapshot_backup_assets(store, ref, manifest, *, selected=None, raw_products=()):
    base = store._snapshot_dir(ref.snapshot_id).resolve()
    if base.parent != (store.root / "snapshots").resolve() or ref.root.resolve() != store.root.resolve():
        raise ValueError("invalid backup snapshot reference")
    prefix = Path("snapshots") / ref.snapshot_id
    assets = {prefix / "manifest.json": base / "manifest.json"}
    _check_backup_metadata(manifest)
    evidence = {}
    entries = manifest.get("table_files", ())
    if selected is not None:
        entries = [entry for entry in entries if entry["path"] in selected]
    products = {entry["product_id"] for entry in entries} | set(raw_products)
    if selected is None:
        products.update(item["product_id"] for item in manifest.get("items", ()))
    mode = manifest.get("mode", "free")
    for product_id in products:
        product = store.product(product_id)
        store.check_access(mode, product, operation="backup")
        store.check_access(mode, product, operation="read")
    for entry in entries:
        rel, table = entry["path"], entry["table"]
        path = _locked_backup_file(base, rel, entry["sha256"])
        assets[prefix / rel] = path
        pid = entry["product_id"]
        needs_evidence = table in {"knowledge_evidence", "terminal_disposals"}
        if store.product(pid).get("fee_class") == "paid" or needs_evidence:
            frame = pd.read_parquet(path)
            store._check_paid_rows(pid, frame, table)
        if needs_evidence:
            for row in frame.itertuples():
                name, digest = str(row.evidence_ref), str(row.sha256)
                if name in evidence and evidence[name] != digest:
                    raise SnapshotTamperedError(f"conflicting evidence hashes: {name}")
                evidence[name] = digest
    for item in manifest.get("items", ()):
        pid = item["product_id"]
        if pid not in products:
            continue
        product = store.product(pid)
        grant = store._grant(pid) or {}
        raw_files = item.get("raw_files", ())
        if raw_files and product.get("fee_class") == "paid":
            _check_backup_raw_files(product, grant, item, base)
        for entry in raw_files:
            rel, digest = entry["path"], entry["sha256"]
            assets[prefix / rel] = _locked_backup_file(base, rel, digest)
            if name := entry.get("evidence_ref"):
                if name in evidence and evidence[name] != digest:
                    raise SnapshotTamperedError(f"conflicting evidence hashes: {name}")
                evidence[name] = digest
    for name, digest in evidence.items():
        assets[Path("evidence") / name] = _locked_backup_file(
            store.root / "evidence", name, digest, label="evidence missing or changed",
        )
    return assets, products


def _write_backup(destination, assets, documents, index):
    for document in documents.values():
        _check_backup_metadata(document)
    encoded = {path: json.dumps(doc, ensure_ascii=False, indent=2).encode() for path, doc in documents.items()}
    required = sum(p.stat().st_size for p in assets.values()) + sum(map(len, encoded.values()))
    free = shutil.disk_usage(destination.parent).free
    if free < required + 1024 * 1024:
        raise OSError(f"insufficient backup space: need {required} bytes plus reserve, free {free}")
    destination.mkdir()
    for relative, source in assets.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    for relative, body in encoded.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    index.update(
        required_bytes=required, free_before_bytes=free, device_disaster_recovery=False,
        files=[dict(path=str(p), sha256=_sha256(destination / p)) for p in (*assets, *encoded)],
    )
    (destination / "backup-manifest.json").write_text(json.dumps(index, ensure_ascii=False, indent=2))


def backup_archive(store, ref, destination):
    """显式备份一个已发布快照及引用原件；不复制凭据/任务队列、不覆盖目录。"""
    destination = Path(destination).resolve()
    if destination.exists():
        raise FileExistsError(destination)
    source_manifest = store._snapshot_dir(ref.snapshot_id) / "manifest.json"
    if source_manifest.resolve() != source_manifest.absolute():
        raise ValueError("invalid backup snapshot manifest path")
    manifest = store.load_manifest(ref)
    assets, products = _snapshot_backup_assets(store, ref, manifest)
    _write_backup(destination, assets, _backup_config(store, products),
                  dict(snapshot_id=ref.snapshot_id, source_root=str(store.root.resolve())))
    return verify_archive_backup(destination)


def _verify_backup(root):
    index = json.loads((root / "backup-manifest.json").read_text())
    for entry in index["files"]:
        _locked_backup_file(root, entry["path"], entry["sha256"], label="backup asset missing or changed")
    return index


def verify_archive_backup(destination):
    """按备份清单找出具体缺失/变更文件；返回可用DataStore重开的引用。"""
    root = Path(destination).resolve()
    index = _verify_backup(root)
    return SnapshotRef(snapshot_id=index["snapshot_id"], root=root)


def backup_library(store, ref, destination):
    from hetu_data.library import _locked_asset, load_library

    destination = Path(destination).resolve()
    if destination.exists():
        raise FileExistsError(destination)
    doc = load_library(store, ref)
    assets, documents, source_roots = {}, {}, {}
    by_owner = {}
    for asset in doc["asset_snapshots"]:
        owner, snapshot, manifest = _locked_asset(store, asset)
        origin = str(owner.root.resolve())
        selected = {row["path"] for row in doc["table_files"]
                    if row["root"] == origin and row["snapshot_id"] == snapshot.snapshot_id}
        paths, products = _snapshot_backup_assets(
            owner, snapshot, manifest, selected=selected, raw_products=asset.get("raw_products", ()),
        )
        if origin in by_owner and by_owner[origin][0].config != owner.config:
            raise ValueError("backup owner has conflicting product configurations")
        saved_owner, saved_products = by_owner.setdefault(origin, (owner, set()))
        saved_products.update(products)
        folder = source_roots.setdefault(origin, f"owners/{len(source_roots)}")
        assets.update({Path(folder) / path: value for path, value in paths.items()})
    for origin, (owner, products) in by_owner.items():
        folder = Path(source_roots[origin])
        documents.update({folder / path: value for path, value in _backup_config(owner, products).items()})

    for revision in doc["revision_selections"]:
        origin = revision["root"]
        owner = by_owner[origin][0]
        rel = revision["evidence_ref"]
        source = (owner.root / rel).resolve()
        if not source.is_relative_to(owner.root.resolve()) or not source.is_file():
            raise SnapshotTamperedError(f"revision evidence missing: {rel}")
        assets[Path(source_roots[origin]) / rel] = source

    policy = doc["source_policy"]
    if policy.get("basis_ref"):
        for origin, (owner, products) in by_owner.items():
            if policy["primary_product_id"] not in products:
                continue
            rel = policy["basis_ref"]
            source = (owner.root / rel).resolve()
            if (source.is_relative_to(owner.root.resolve()) and source.is_file()
                    and (not policy.get("basis_sha256") or _sha256(source) == policy["basis_sha256"])):
                assets[Path(source_roots[origin]) / rel] = source
                break
        else:
            raise SnapshotTamperedError(f"source policy evidence missing: {policy['basis_ref']}")

    for key in ("asset_snapshots", "table_files", "sources", "revision_selections"):
        for item in doc[key]:
            original = item["root"]
            if key == "asset_snapshots":
                item.setdefault("original_root", original)
                item["config_path"] = None
            elif key == "table_files":
                item["config_path"] = None
            item["root"] = str(destination / source_roots[original])
            if key == "asset_snapshots":
                item.pop("revision_selections", None)  # Verified selections are in the canonical manifest.
    doc["root"] = str(destination)
    library_manifest = Path("libraries") / ref.library_id / "manifest.json"
    documents[library_manifest] = doc
    _write_backup(destination, assets, documents,
                  dict(library_id=ref.library_id, library_manifest=str(library_manifest), source_roots=source_roots))
    return verify_library_backup(destination)


def verify_library_backup(destination):
    from hetu_data.models import LibraryRef

    root = Path(destination).resolve()
    index = _verify_backup(root)
    ref = LibraryRef(index["library_id"], root)
    expected = Path("libraries") / ref.library_id / "manifest.json"
    if Path(index["library_manifest"]) != expected or not (root / expected).is_file():
        raise SnapshotTamperedError(f"backup library manifest missing or changed: {expected}")
    doc = json.loads((root / expected).read_text())
    if doc["library_id"] != ref.library_id or Path(doc["root"]).resolve() != root:
        raise SnapshotTamperedError("backup library reference does not match its manifest")
    tracked = {Path(entry["path"]) for entry in index["files"]}
    required = {expected}
    for asset in doc["asset_snapshots"]:
        owner = Path(asset["root"])
        if owner.resolve() != owner or not owner.is_relative_to(root / "owners"):
            raise SnapshotTamperedError("backup owner root is outside the registered backup")
        relative = owner.relative_to(root)
        required.add(relative / "data-products.json")
        required.add(relative / "snapshots" / asset["snapshot_id"] / "manifest.json")
    for row in doc["table_files"]:
        required.add(Path(row["root"]).relative_to(root) / "snapshots" / row["snapshot_id"] / row["path"])
    if missing := required - tracked:
        raise SnapshotTamperedError(f"backup asset missing from manifest: {min(missing)}")
    return ref


def _mask(days):
    result = 0
    for day in set(str(d)[:10] for d in days):
        if day not in _BITS:
            _BITS[day] = 1 << (date.fromisoformat(day) - _BASE).days
        result |= _BITS[day]
    return result


def _ranges(days, sessions=None):
    days = sorted(set(days))
    if not days:
        return []
    ordering = {d: i for i, d in enumerate(sessions)} if sessions is not None else None
    spans = [[days[0], days[0]]]
    for day in days[1:]:
        previous = spans[-1][1]
        adjacent = (
            (ordering[day] == ordering[previous] + 1)
            if ordering is not None
            else (date.fromisoformat(day) - date.fromisoformat(previous)).days == 1
        )
        if adjacent:
            spans[-1][1] = day
        else:
            spans.append([day, day])
    return spans


def _history_objects(history, start, end, *, segmented=False):
    objects, exclusions, unknown = [], [], []
    for original in history.fillna("").to_dict("records"):
        code = str(original["code"])
        left = str(original.get("valid_from") or original.get("ipo_date") or "")[:10]
        right = str(original.get("valid_to") or original.get("out_date") or "")[:10]
        state = str(original.get("lifecycle_status", "known" if left else "unknown"))
        known = state == "known" and bool(left) and bool(original.get("source_ref"))
        if segmented and known:
            try:
                known = bool(original.get("identity_id") and original.get("code_period_id")) and (
                    date.fromisoformat(left).isoformat() == left
                    and (not right or (date.fromisoformat(right).isoformat() == right and left <= right))
                )
            except ValueError:
                known = False
        required = [max(start, left) if known else start, min(end, right) if known and right else end]
        obj = dict(
            original,
            identity_id=str(original.get("identity_id") or code),
            code=code,
            market=str(original.get("market") or "unknown"),
            security_type=str(original.get("security_type") or "unknown"),
            lifecycle_known=known,
            valid_from=left,
            valid_to=right,
            required=required,
        )
        obj["attributes_known"] = bool(original.get("source_ref")) and all(
            obj[field].strip().lower() not in ("", "unknown") for field in ("market", "security_type")
        )
        objects.append(obj)
        if not obj["attributes_known"]:
            unknown.append(
                dict(
                    code=code,
                    identity_id=obj["identity_id"],
                    required=required,
                    reason="security_attributes_unproved",
                    source_ref=original.get("source_ref", ""),
                )
            )
        if not known:
            unknown.append(
                dict(
                    code=code,
                    identity_id=obj["identity_id"],
                    required=[start, end],
                    reason="identity_or_lifecycle_unproved",
                    source_ref=original.get("source_ref", ""),
                )
            )
        elif left > start:
            exclusions.append(dict(code=code, reason="not_listed", interval=[start, min(end, left)]))
        if known and right and right < end:
            exclusions.append(dict(code=code, reason="after_exit", interval=[max(start, right), end]))
    # 同一代码的不同实体不能用代码键的行情覆盖相互放行。
    identities = defaultdict(set)
    for obj in objects:
        identities[obj["code"]].add(obj["identity_id"])
    overlaps = set()
    if segmented:
        by_code = defaultdict(list)
        for obj in objects:
            by_code[obj["code"]].append(obj)
        for code, segments in by_code.items():
            if len(segments) < 2:
                continue
            periods = sorted(tuple(obj["required"]) for obj in segments if obj["required"][0] <= obj["required"][1])
            if (
                any(not obj["lifecycle_known"] or not obj.get("code_period_id") for obj in segments)
                or len({obj.get("code_period_id") for obj in segments}) < len(segments)
                or any(a[1] >= b[0] for a, b in zip(periods, periods[1:], strict=False))
            ):
                overlaps.add(code)
    for obj in objects:
        if (obj["code"] in overlaps if segmented else len(identities[obj["code"]]) > 1):
            obj["lifecycle_known"] = False
            obj["required"] = [start, end]
            unknown.append(
                dict(
                    code=obj["code"],
                    identity_id=obj["identity_id"],
                    required=[start, end],
                    reason="multiple_identities_need_code_period_proof",
                )
            )
    return objects, exclusions, unknown


def archive_requirements(history, calendar, cutoff, *, years=20, start=None, calendar_scopes=()):
    left = (pd.Timestamp(cutoff) - pd.DateOffset(years=years)).date() if start is None else start
    start, end = left.isoformat(), cutoff.isoformat()
    segmented = "code_period_id" in history
    if segmented:
        history = history.drop_duplicates()
    objects, exclusions, unknown = _history_objects(history, start, end, segmented=segmented)
    for obj in objects:
        if obj["attributes_known"] and (obj["security_type"] != "stock" or obj["market"] not in ("SH", "SZ", "BJ")):
            obj["excluded"] = True
            exclusions.append(dict(code=obj["code"], reason="not_common_stock_or_market", interval=[start, end]))
    market_sessions, unknown_by_market = _market_sessions(calendar, calendar_scopes, objects)
    return dict(
        purpose="daily_archive",
        start=start,
        end=end,
        years=years,
        objects=objects,
        expected_sessions=sorted(
            str(d)[:10]
            for d, flag in zip(calendar.date, calendar.is_trading_day, strict=False)
            if _truthy(flag) and start <= str(d)[:10] <= end
        ),
        calendar_days=sorted(set(calendar.date.astype(str))),
        expected_sessions_by_market=market_sessions,
        calendar_unknown_ranges=unknown_by_market,
        exclusions=exclusions,
        unknown_ranges=unknown,
        target_scope=dict(history.attrs.get("target_scope", {})),
        requested_markets=sorted(set(o["market"] for o in objects)),
    )


def _period_mask(start, end):
    first = (date.fromisoformat(start) - _BASE).days
    last = (date.fromisoformat(end) - _BASE).days
    return ((1 << (last - first + 1)) - 1) << first if first <= last else 0


def _market_sessions(calendar, scopes, objects):
    by_market = defaultdict(lambda: defaultdict(set))
    if {"product_id", "source_ref"} <= set(calendar):
        for scope in scopes:
            if not scope.get("source_ref"):
                continue
            rows = calendar[calendar.product_id.eq(scope["product_id"]) & calendar.source_ref.fillna("").ne("")]
            for row in rows.itertuples():
                day = str(row.date)[:10]
                if scope["valid_from"] <= day <= scope["valid_to"]:
                    by_market[scope["market"]][day].add(_query_boolean(row.is_trading_day))
    market_windows = defaultdict(list)
    for obj in objects:
        if not obj.get("excluded") and obj["required"][0] <= obj["required"][1]:
            market_windows[obj["market"]].append(obj["required"])
    expected, unknown = {}, []
    for market, windows in sorted(market_windows.items()):
        merged = []
        for left, right in sorted(windows):
            if merged and left <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], right)
            else:
                merged.append([left, right])
        days = [day for left, right in merged for day in pd.date_range(left, right).strftime("%Y-%m-%d")]
        flags = by_market[market]
        expected[market] = [day for day in days if flags[day] == {True}]
        missing = _ranges(day for day in days if flags[day] not in ({True}, {False}))
        if missing:
            unknown.append(dict(domain="calendar", market=market, ranges=missing, reason="calendar_unproved"))
    return expected, unknown


@dataclass
class CoverageInventory:
    """实际读取形成的紧凑日期位图；不保存价格库或资格答案。"""

    masks: dict = field(default_factory=dict)
    frames: dict = field(default_factory=dict)
    calendar: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=["date", "is_trading_day"]))
    sources: dict = field(default_factory=dict)
    queries: list = field(default_factory=list)
    action_entries: list = field(default_factory=list)
    failures: list = field(default_factory=list)
    source_differences: list = field(default_factory=list)
    stats: dict = field(default_factory=lambda: dict(files=0, bytes=0, raw_rows=0, tables={}))


def _source_rows(store, ref, asset_snapshots):
    """从登记清单选择原件/标准文件；每个实际文件先检查读取许可与原哈希。"""
    from hetu_data.store import DataStore

    selections = [dict(root=str(store.root), snapshot_id=ref.snapshot_id, tables=list(_TABLES))]
    selections.extend(asset_snapshots)
    seen = set()
    for selection in selections:
        owner = (
            store
            if Path(selection["root"]).resolve() == store.root.resolve()
            else DataStore(
                Path(selection["root"]),
                config_path=Path(selection["config_path"]) if selection.get("config_path") else None,
            )
        )
        base = owner.root / "snapshots" / selection["snapshot_id"]
        manifest = json.loads((base / "manifest.json").read_text())
        for item in manifest.get("items", []):
            if item["product_id"] not in selection.get("raw_products", []):
                continue
            for entry in item.get("raw_files", []):
                yield (
                    owner,
                    manifest,
                    base,
                    dict(entry, product_id=item["product_id"], format="raw", source=item.get("source", {})),
                    selection,
                )
        for entry in manifest.get("table_files", []):
            if entry["table"] not in selection.get("tables", []):
                continue
            path = (base / entry["path"]).resolve()
            if path in seen:
                continue
            seen.add(path)
            yield owner, manifest, base, dict(entry, format="standard"), selection


def _resolve_archive_source_differences(frame, values, valid, conflict, code, policy, differences):
    if not policy or code not in policy["codes"]:
        return
    product = policy["primary_product_id"]
    comparable = {product, *policy["compared_product_ids"]}
    in_scope = frame.date.between(policy["start"], policy["end"]) & frame.date.isin(conflict[conflict].index)
    for day, group in frame[in_scope].groupby("date"):
        primary = group.product_id.eq(product)
        day_values = values.loc[group.index]
        fields = day_values.nunique(dropna=False)
        fields = fields[fields.gt(1)].index.tolist()
        if (
            not primary.any() or not set(group.product_id) <= comparable or not valid.loc[group.index].all()
            or len(day_values[primary].drop_duplicates()) != 1 or not set(fields) <= set(policy["fields"])
        ):
            continue
        rows = group[["product_id", "source_root", "snapshot_id", "source_ref"]].join(day_values)
        differences.append(dict(code=code, date=day, fields=fields, primary_product_id=product,
                                basis_ref=policy["basis_ref"], rows=rows.drop_duplicates().to_dict("records")))
        conflict.loc[day] = False


def scan_local_coverage(store, ref, *, asset_snapshots=(), progress=None, _entries=None, _revisions=(), _policy=None):
    """只读逐块核行；大日线原件按代码逐组处理，内存中只保留日期位图。"""
    from hetu_data.providers.baostock import BaoStockProvider

    inventory = CoverageInventory()
    daily_sources = defaultdict(list)
    extras = defaultdict(list)
    entries = _entries if _entries is not None else _source_rows(store, ref, asset_snapshots)
    for owner, manifest, base, entry, _selection in entries:
        path = (base / entry["path"]).resolve()
        try:
            if not path.is_relative_to(base.resolve()):
                raise ValueError("audit source path outside snapshot")
            owner.check_access(manifest.get("mode", "free"), owner.product(entry["product_id"]), operation="read")
            if _sha256(path) != entry["sha256"]:
                raise ValueError("audit source hash mismatch")
            if entry["format"] == "raw":
                match = re.fullmatch(r"response-(.+)-(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})\.parquet", path.name)
                if not match:
                    inventory.failures.append(dict(source=str(path), reason="raw query scope not proven"))
                    continue
                code = match[1].replace("_", ".")
                daily_sources[code].append((path, entry["product_id"], match[2], match[3], owner.root, base.name))
            else:
                frame = pd.read_parquet(path)
                inventory.stats["files"] += 1
                inventory.stats["bytes"] += path.stat().st_size
                table = entry["table"]
                owner._check_paid_rows(entry["product_id"], frame, table)
                frame["evidence_root"] = str(owner.root)
                if _revisions or _policy:
                    frame["source_root"], frame["snapshot_id"] = str(owner.root.resolve()), base.name
                if table in ("daily_bars", "security_status", "security_events", "adjustment_factors") and len(frame):
                    for code, group in frame.groupby("code"):
                        extras[str(code)].append((table, group, str(path)))
                else:
                    inventory.frames.setdefault(table, []).append(frame)
                if table == "corporate_actions":
                    inventory.action_entries.append(entry)
        except (OSError, ValueError, PermissionError, KeyError) as error:
            failure = dict(source=str(path), reason=str(error))
            if _entries is not None:
                failure["table"] = entry.get("table", "daily_bars")
            inventory.failures.append(failure)
    codes = sorted(set(daily_sources) | set(extras))
    for position, code in enumerate(codes, 1):
        groups = defaultdict(list)
        sources = []
        for table, frame, path in extras[code]:
            groups[table].append(frame)
            sources.append(path)
        for path, product, start, end, root, snapshot in daily_sources[code]:
            raw = pd.read_parquet(path).astype(str)
            inventory.stats["files"] += 1
            inventory.stats["bytes"] += path.stat().st_size
            inventory.stats["raw_rows"] += len(raw)
            inventory.queries.append(
                dict(
                    code=code,
                    product_id=product,
                    start=start,
                    end=end,
                    status="success_empty" if raw.empty else "nonempty",
                    source_ref=str(path),
                )
            )
            sources.append(str(path))
            if raw.empty:
                continue
            if not {"code", "date", "tradestatus", "isST"} <= set(raw):
                inventory.failures.append(
                    dict(source=str(path), reason="raw fields do not prove daily/state semantics")
                )
                continue
            if product == "cn_a_daily":
                if "adjustflag" not in raw or not raw.adjustflag.eq("3").all():
                    inventory.failures.append(dict(source=str(path), reason="unadjusted price basis unproved"))
                else:
                    frame = BaoStockProvider().standardize(product, raw, {})["daily_bars"]
                    if _revisions or _policy:
                        frame = frame.assign(source_root=str(root.resolve()), snapshot_id=snapshot, product_id=product)
                    groups["daily_bars"].append(frame)
            valid = raw.tradestatus.isin(["0", "1"]) & raw.isST.isin(["0", "1"])
            if valid.any():
                frame = BaoStockProvider().standardize("cn_a_status", raw[valid], {})["security_status"]
                if _revisions:
                    frame = frame.assign(source_root=str(root.resolve()), snapshot_id=snapshot,
                                         product_id="cn_a_status")
                groups["security_status"].append(frame)
        masks = dict(
            bars=0,
            status=0,
            suspended=0,
            factors=0,
            invalid=0,
            conflicting=0,
            status_conflicting=0,
            factors_conflicting=0,
        )
        explicitly_trading = 0
        for table, pieces in groups.items():
            frame = pd.concat(pieces, ignore_index=True)
            if not len(frame):
                continue
            frame = frame[frame.code.astype(str).eq(code)].copy()
            frame["date"] = frame.date.astype(str).str[:10]
            if _revisions:
                from hetu_data.library import _select_revisions

                frame = _select_revisions(frame, table, _revisions)
            inventory.stats["tables"][table] = inventory.stats["tables"].get(table, 0) + len(frame)
            if table == "daily_bars":
                values = frame[_BAR_FIELDS].apply(pd.to_numeric, errors="coerce")
                valid = _valid_archive_bars(values)
                numeric = pd.concat([frame[["date"]], values], axis=1)
                conflict = numeric.groupby("date")[_BAR_FIELDS].nunique(dropna=False).gt(1).any(axis=1)
                _resolve_archive_source_differences(frame, values, valid, conflict, code, _policy,
                                                    inventory.source_differences)
                masks["conflicting"] |= _mask(conflict[conflict].index)
                masks["bars"] |= _mask(frame.loc[valid, "date"])
                masks["archive_trade_evidence"] = _mask(frame.loc[_archive_trade_evidence(values, valid), "date"])
                masks["invalid"] |= _mask(frame.loc[~valid, "date"])
            elif table == "security_status":
                columns = ["is_suspended", "is_st", "is_listed"]
                valid = pd.Series(True, index=frame.index)
                normalized = frame[["date"]].copy()
                for column in columns:
                    valid &= frame[column].astype(str).str.lower().isin(["true", "false", "0", "1"])
                    normalized[column] = frame[column].map(_truthy)
                conflict = normalized[valid].groupby("date")[columns].nunique().gt(1).any(axis=1)
                masks["status_conflicting"] |= _mask(conflict[conflict].index)
                masks["status"] |= _mask(frame.loc[valid, "date"])
                masks["suspended"] |= _mask(frame.loc[valid & frame.is_suspended.map(_truthy), "date"])
                explicitly_trading |= _mask(frame.loc[valid & ~frame.is_suspended.map(_truthy), "date"])
                if _entries is not None:
                    states = frame.is_suspended.map(_query_boolean)
                    halted = _mask(frame.loc[states.eq(True), "date"])
                    normal = _mask(frame.loc[states.eq(False), "date"])
                    masks["archive_suspended"] = halted & ~normal
                    masks["archive_status_conflicting"] = halted & normal
                    explicitly_trading |= normal
            elif table == "security_events":
                masks["official_no_trade"] = _mask(frame.loc[_official_no_trade(frame), "date"])
                events = frame[frame.product_id.eq("ts_suspend_day")]
                full = events.event_type.eq("S") & events.suspend_timing.fillna("").astype(str).str.strip().eq("")
                positive = _mask(events.loc[full, "date"])
                mixed = positive & _mask(events.loc[~full, "date"])
                masks["event_suspended"] = positive & ~mixed
                masks["event_conflicting"] = mixed
            elif table == "adjustment_factors":
                values = pd.to_numeric(frame.adj_factor, errors="coerce")
                conflict = values.groupby(frame.date).nunique(dropna=False).gt(1)
                masks["factors_conflicting"] |= _mask(conflict[conflict].index)
                masks["factors"] |= _mask(frame.loc[values.gt(0), "date"])
        masks["bars"] &= ~masks["conflicting"]
        masks["factors"] &= ~masks["factors_conflicting"]
        masks["status"] &= ~masks["status_conflicting"]
        masks["suspended"] &= ~masks["status_conflicting"]
        event_suspended = masks.get("event_suspended", 0)
        masks["event_conflicting"] = masks.get("event_conflicting", 0) | (
            event_suspended & (explicitly_trading | masks["status_conflicting"])
        )
        masks["event_suspended"] = event_suspended & ~masks["event_conflicting"]
        inventory.masks[code] = masks
        inventory.sources[code] = sorted(set(sources))
        if progress and (position % 250 == 0 or position == len(codes)):
            progress(
                dict(
                    codes_done=position,
                    codes_total=len(codes),
                    files_read=inventory.stats["files"],
                    raw_rows=inventory.stats["raw_rows"],
                )
            )
    inventory.frames = {t: pd.concat(pieces, ignore_index=True) for t, pieces in inventory.frames.items() if pieces}
    if _revisions:
        from hetu_data.library import _select_revisions

        inventory.frames = {t: _select_revisions(f, t, _revisions) for t, f in inventory.frames.items()}
    if "calendar" in inventory.frames and _entries is not None:
        inventory.calendar = inventory.frames["calendar"][["product_id", "date", "is_trading_day", "source_ref"]]
    elif "calendar" in inventory.frames:
        inventory.calendar = inventory.frames["calendar"][["date", "is_trading_day"]].drop_duplicates()
        inventory.calendar["date"] = inventory.calendar.date.astype(str).str[:10]
        inventory.calendar["is_trading_day"] = inventory.calendar.is_trading_day.map(_truthy)
        inventory.calendar = inventory.calendar.drop_duplicates()
        conflicts = inventory.calendar.groupby("date").is_trading_day.nunique().gt(1)
        if conflicts.any():
            inventory.failures.append(dict(source="calendar", reason="conflicting trading calendar"))
            inventory.calendar = inventory.calendar[~inventory.calendar.date.isin(conflicts[conflicts].index)]
    return inventory


def _coverage_rows(requirements, inventory, domains, *, raw_library=False):
    rows = []
    for obj in requirements["objects"]:
        if obj.get("excluded") or obj["required"][0] > obj["required"][1] or (
            raw_library and not (obj["lifecycle_known"] and obj["attributes_known"])
        ):
            continue
        sessions = (requirements["expected_sessions_by_market"].get(obj["market"], ()) if raw_library
                    else requirements["expected_sessions"])
        start, end = obj["required"]
        days = [d for d in sessions if start <= d <= end]
        expected = _mask(days)
        own = inventory.masks.get(obj["code"], {})
        for domain, metric in domains.items():
            observed = own.get(metric, 0)
            suspended = expected & own.get("suspended", 0) if domain == "daily_bars" else 0
            missing = expected & ~(observed | suspended)
            conflicting = expected & own.get("conflicting", 0) if domain == "daily_bars" else 0
            state_conflict = expected & own.get("status_conflicting", 0)
            no_trade = 0
            if domain == "daily_bars":
                conflicting |= state_conflict & ~observed
                if requirements.get("purpose") == "daily_archive":
                    event_suspended = own.get("event_suspended", 0)
                    conflicting |= expected & (own.get("event_conflicting", 0) | (event_suspended & observed))
                    suspended = (suspended | (expected & event_suspended)) & ~conflicting
                    official = expected & own.get("official_no_trade", 0)
                    price_evidence = observed | own.get("archive_trade_evidence", 0) | own.get("conflicting", 0)
                    no_trade = official & ~price_evidence
                    conflicting = (conflicting & ~no_trade) | (official & price_evidence)
                    observed &= ~official
                    suspended &= ~official
            elif domain == "security_status":
                conflicting |= state_conflict
            elif domain == "adjustment_factors":
                conflicting |= expected & own.get("factors_conflicting", 0)
                if requirements.get("purpose") == "daily_archive":
                    # 无原价行且已证全天停牌时，没有需要复权的价格。
                    suspended = (
                        expected
                        & (own.get("suspended", 0) | own.get("event_suspended", 0))
                        & ~(observed | own.get("bars", 0) | own.get("conflicting", 0)
                            | own.get("invalid", 0) | state_conflict | own.get("event_conflicting", 0))
                    )
                    no_trade = expected & own.get("official_no_trade", 0) & ~(
                        observed | own.get("bars", 0) | own.get("archive_trade_evidence", 0)
                        | own.get("conflicting", 0) | conflicting
                    )
            if raw_library:
                halted = own.get("archive_suspended", 0) | own.get("event_suspended", 0)
                halt_conflict = own.get("archive_status_conflicting", 0) | own.get("event_conflicting", 0)
                trade = own.get("archive_trade_evidence", 0) | own.get("conflicting", 0)
                official = own.get("official_no_trade", 0)
                untraded = expected & (halted | official) & ~(trade | halt_conflict)
                if domain == "daily_bars":
                    conflicting |= expected & (halt_conflict | (halted | official) & trade)
                    observed &= ~conflicting
                    no_trade, suspended = untraded & official, untraded & ~official
                else:
                    no_trade, suspended = untraded & ~observed, 0
                missing = expected & ~(observed | no_trade | suspended)
            missing &= ~(conflicting | suspended | no_trade)

            def dates(mask, days=days):
                return [d for d in days if _BITS[d] & mask] if mask else []

            rows.append(
                dict(
                    identity_id=obj["identity_id"],
                    code=obj["code"],
                    domain=domain,
                    required=[start, end],
                    expected_sessions=len(days),
                    observed_sessions=(expected & observed).bit_count(),
                    covered=_ranges(dates(expected & observed), days),
                    suspended=_ranges(dates(suspended), days),
                    no_trade=_ranges(dates(no_trade), days),
                    data_gaps=_ranges(dates(missing), days) if obj["lifecycle_known"] else [],
                    unknown_ranges=_ranges(dates(conflicting), days) if obj["lifecycle_known"] else [[start, end]],
                    conflicting_sessions=conflicting.bit_count(),
                    invalid_sessions=(expected & own.get("invalid", 0)).bit_count() if domain == "daily_bars" else 0,
                    source_refs=inventory.sources.get(obj["code"], []),
                )
            )
            if raw_library:
                rows[-1].update(code_period_id=obj.get("code_period_id"), market=obj["market"],
                                no_trade_sessions=(no_trade | suspended).bit_count())
    return rows


def audit_archive(store, ref, requirements, *, inventory=None):
    inventory = inventory or scan_local_coverage(store, ref, asset_snapshots=requirements.get("asset_snapshots", ()))
    rows = _coverage_rows(requirements, inventory, {"daily_bars": "bars", "adjustment_factors": "factors"})
    known_calendar = set(inventory.calendar.date.astype(str))
    unknown_calendar = _ranges(
        [
            str(d.date())
            for d in pd.date_range(requirements["start"], requirements["end"])
            if str(d.date()) not in known_calendar
        ]
    )
    unknown = list(requirements["unknown_ranges"])
    unknown.extend(
        dict(code=r["code"], domain=r["domain"], ranges=r["unknown_ranges"], reason="row_conflict_or_identity_unproved")
        for r in rows
        if r["unknown_ranges"]
    )
    if unknown_calendar:
        unknown.append(dict(domain="calendar", ranges=unknown_calendar, reason="calendar_unproved"))
    scope = requirements.get("target_scope", {})
    target_complete = scope.get("complete") is True and bool(scope.get("source_ref"))
    core = (
        not unknown and not inventory.failures and not any(r["data_gaps"] for r in rows if r["domain"] == "daily_bars")
    )
    complete = core and target_complete and bool(rows) and not any(r["data_gaps"] for r in rows)
    return dict(
        status="complete" if complete else "limited",
        core_complete=core and bool(rows),
        coverage=rows,
        gaps=[r for r in rows if r["data_gaps"]],
        unknown_ranges=unknown,
        target_scope_complete=target_complete,
        units=dict(price="CNY_unadjusted", volume="shares", amount="CNY"),
        row_counts=inventory.stats["tables"],
        bytes=inventory.stats["bytes"],
        source_refs=inventory.sources,
        query_observations=inventory.queries,
        read_failures=inventory.failures,
    )


def _scan_library_coverage(store, ref):
    from hetu_data.library import _locked_asset, archive_source_policy, load_library

    document = load_library(store, ref)
    fixed = {(entry["root"], entry["snapshot_id"], entry["path"]): entry for entry in document["table_files"]}
    failures, observations = [], []

    def entries():
        seen = set()
        for asset in document["asset_snapshots"]:
            identity = asset["root"], asset["snapshot_id"]
            try:
                owner, snapshot, manifest = _locked_asset(store, asset)
            except (OSError, ValueError, PermissionError, KeyError, SnapshotTamperedError) as error:
                for table in asset["tables"]:
                    failures.append(dict(source=str(identity), reason=str(error), table=table))
                continue
            base = owner._snapshot_dir(snapshot.snapshot_id)
            for entry in manifest.get("table_files", ()):
                locked = fixed.get((*identity, entry["path"]))
                if locked and entry["table"] in _TABLES and (base / entry["path"]).resolve() not in seen:
                    seen.add((base / entry["path"]).resolve())
                    if any(locked[key] != entry[key] for key in ("table", "product_id", "sha256")):
                        failures.append(dict(source=entry["path"], table=entry["table"], reason="fixed file mismatch"))
                    else:
                        yield owner, manifest, base, dict(entry, format="standard"), asset
            for item in manifest.get("items", ()):
                if item["product_id"] not in asset.get("raw_products", ()):
                    continue
                try:
                    owner.check_access(
                        manifest.get("mode", "free"), owner.product(item["product_id"]), operation="read",
                    )
                except AccessDenied as error:
                    failures.append(dict(source=str(identity), reason=str(error), table="daily_bars"))
                    continue
                for entry in item.get("raw_files", ()):
                    if entry["path"].endswith(".parquet") and item["product_id"] in {"cn_a_daily", "cn_a_status"}:
                        yield owner, manifest, base, dict(entry, format="raw", product_id=item["product_id"]), asset
                    elif state := entry.get("query_observation"):
                        observations.append(dict(
                            status=state, product_id=item["product_id"], source_ref=str(base / entry["path"]),
                            snapshot_id=snapshot.snapshot_id,
                        ))

    inventory = scan_local_coverage(store, ref, _entries=entries(), _revisions=document["revision_selections"],
                                    _policy=archive_source_policy(document))
    inventory.failures.extend(failures)
    inventory.queries.extend(observations)
    return inventory


def audit_library(store, ref, requirements):
    inventory = _scan_library_coverage(store, ref)
    rows = _coverage_rows(requirements, inventory, {"daily_bars": "bars", "adjustment_factors": "factors"},
                          raw_library=True)
    bars = [row for row in rows if row["domain"] == "daily_bars"]
    factors = [row for row in rows if row["domain"] == "adjustment_factors"]
    unknown = [*requirements["unknown_ranges"], *requirements["calendar_unknown_ranges"]]
    scope = requirements["target_scope"]
    scope_unknown = scope.get("unknown_ranges", [])
    if scope_unknown:
        unknown.append(dict(reason="target_scope_unproved", ranges=scope_unknown))
    target_complete = scope.get("complete") is True and bool(scope.get("source_ref") and scope.get("source_version"))
    target_complete = target_complete and not scope_unknown and not requirements["unknown_ranges"]
    expected = sum(row["expected_sessions"] for row in bars)
    qualified = sum(row["observed_sessions"] for row in bars)
    no_trade = sum(row["no_trade_sessions"] for row in bars)
    allowed = defaultdict(int)
    uncertain_codes = set()
    allowed_days = {
        market: _mask(sessions) for market, sessions in requirements["expected_sessions_by_market"].items()
    }
    for row in requirements["calendar_unknown_ranges"]:
        for left, right in row["ranges"]:
            allowed_days[row["market"]] |= _period_mask(left, right)
    for obj in requirements["objects"]:
        if not obj["lifecycle_known"] or not obj["attributes_known"]:
            uncertain_codes.add(obj["code"])
        if not obj.get("excluded") and obj["lifecycle_known"] and obj["attributes_known"]:
            # Dates lacking calendar evidence are still possible target dates.
            allowed[obj["code"]] |= _period_mask(*obj["required"]) & allowed_days.get(obj["market"], 0)
    target_days = _period_mask(requirements["start"], requirements["end"])
    outside = sum(
        ((facts.get("bars", 0) | facts.get("invalid", 0) | facts.get("conflicting", 0)) & target_days
         & ~allowed[code]).bit_count()
        for code, facts in inventory.masks.items()
        if code not in uncertain_codes and (target_complete or code in allowed)
    )
    counts = dict(expected=expected, qualified_bars=qualified, no_trade=no_trade,
                  unknown_or_conflict=expected - qualified - no_trade, outside_scope=outside)
    core_failures = [f for f in inventory.failures if f.get("table") != "adjustment_factors"]
    known_ratio = (qualified + no_trade) / expected if expected else None
    coverage_ratio = known_ratio if target_complete and not requirements["calendar_unknown_ranges"] else None
    raw_complete = (
        bool(bars) and target_complete and not unknown and not core_failures and not counts["unknown_or_conflict"]
    )
    adjustment_failures = [f for f in inventory.failures if f.get("table") in {
        "calendar", "security_status", "security_events", "adjustment_factors",
    }]
    adjustment_complete = bool(factors) and target_complete and not unknown and not adjustment_failures and not any(
        row["data_gaps"] or row["unknown_ranges"] for row in factors
    )
    unknown.extend(
        dict(code=row["code"], domain=row["domain"], ranges=row["unknown_ranges"], reason="row_conflict")
        for row in rows if row["unknown_ranges"]
    )
    return dict(
        status="complete" if raw_complete else "limited",
        coverage=rows, gaps=[row for row in rows if row["data_gaps"]], unknown_ranges=unknown,
        target_start=requirements["start"], target_end=requirements["end"],
        latest_available_date=max((last for row in bars for _, last in row["covered"]), default=None),
        raw_price_complete=raw_complete, adjustment_complete=adjustment_complete, target_scope_complete=target_complete,
        coverage_counts=counts, coverage_ratio=coverage_ratio, known_scope_ratio=known_ratio,
        units=dict(price="CNY_unadjusted", volume="shares", amount="CNY"), source_refs=inventory.sources,
        query_observations=inventory.queries, read_failures=inventory.failures,
        source_differences=inventory.source_differences,
    )


def list_archive_targets(store):
    out = []
    base = store.root.resolve() / "archive" / "targets"
    for path in sorted(base.glob("*.json")):
        if not path.name.startswith(".") and path.resolve() == path:
            record = json.loads(path.read_text())
            if record["target_id"] != path.stem:
                raise ValueError("archive target filename does not match its identity")
            out.append(dict(target_id=record["target_id"], **{
                name: record["requirements"][name] for name in ("start", "end", "target_scope")
            }))
    return out


def load_archive_coverage(store, ref, target_id):
    if store.root.resolve() != ref.root.resolve():
        raise ValueError("library belongs to another data root")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", target_id):
        raise ValueError("invalid archive target id")
    base = store.root.resolve() / "archive" / "coverage"
    path = base / ref.library_id / f"{target_id}.json"
    if path.resolve() != path:
        raise ValueError("invalid archive coverage path")
    return json.loads(path.read_text()) if path.is_file() else None


def plan_coverage_gaps(audits, store, *, as_of, contracts, domain_products, mode="free"):
    from hetu_data.updates import METERS, plan_update

    grouped = defaultdict(list)
    unknown = []
    for audit in audits:
        unknown.extend(audit.get("unknown_ranges", []))
        for row in audit["coverage"]:
            product = domain_products.get(row["domain"])
            if not product:
                continue
            for start, end in row["data_gaps"]:
                grouped[(product, row["code"])].append((start, end))
    requests, unresolved = [], []
    counts = dict.fromkeys(METERS, 0)
    counts["quota_bytes"] = None
    counts["data_fee_cny"] = 0 if contracts.get("new_data_fee_cny") == 0 else None
    for (product, code), intervals in grouped.items():
        if mode == "free" and store.product(product).get("fee_class") == "paid":
            raise AccessDenied("paid archive gap product is not allowed in free mode")
        merged = []
        for left, right in sorted(set(intervals)):
            if merged and left <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], right)
            else:
                merged.append([left, right])
        for left, right in merged:
            request = DataRequest(
                (product,),
                (code,),
                date.fromisoformat(left),
                date.fromisoformat(right),
                as_of,
                "daily_archive",
                mode=mode,
            )
            plan = plan_update(
                request,
                store,
                contracts,
                audited_segments=[dict(product_id=product, codes=[code], start=left, end=right, refresh=False)],
            )
            requests.extend(plan["segments"])
            unresolved.extend(plan["unresolved"])
            if not plan["segments"]:
                unresolved.append(
                    dict(
                        code=code,
                        product_id=product,
                        period=[left, right],
                        reason="query coverage exists but required rows missing; investigate before reacquisition",
                    )
                )
            for key, value in plan["counts"].items():
                if key == "data_fee_cny":
                    counts[key] = plan["new_data_fee_cny"]
                    continue
                if value is not None and counts[key] is not None:
                    counts[key] += value
    return dict(
        requests=requests,
        counts=counts,
        unknown_ranges=unknown,
        unresolved=unresolved,
        executed=False,
        retries=0,
        new_data_fee_cny=counts["data_fee_cny"],
    )


def filter_bars(
    bars: pd.DataFrame, *, columns: tuple[str, ...], predicates: tuple[dict, ...]
) -> pd.DataFrame:
    """按数值条件取交集，保留来源、行顺序与同日冲突。"""
    import math

    required = ('code', 'date', 'product_id', 'source_ref')
    selected = list(dict.fromkeys((*required, *columns)))
    if any(column not in ARCHIVE_COLUMNS or column not in bars for column in selected):
        raise ValueError('invalid bar column')
    mask = pd.Series(True, index=bars.index)
    for predicate in predicates:
        field, op = predicate.get('field'), predicate.get('op')
        if field not in _BAR_FIELDS or field not in bars:
            raise ValueError('invalid predicate field')
        if op not in ('eq', 'ge', 'le', 'is_null'):
            raise ValueError('invalid predicate operation')
        values = bars[field]
        if op == 'is_null':
            mask &= values.isna()
            continue
        threshold = predicate.get('value')
        if isinstance(threshold, (bool, str)):
            raise ValueError('threshold must be finite numeric')
        try:
            finite = math.isfinite(threshold)
        except (TypeError, ValueError):
            finite = False
        if not finite:
            raise ValueError('threshold must be finite numeric')
        mask &= values.notna() & getattr(values, op)(threshold).fillna(False)
    return bars.loc[mask, selected].copy()
