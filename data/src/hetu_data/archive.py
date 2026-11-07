"""离线生命周期和实际行审计；研究/档案共用只读事实扫描，不获取数据。"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

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
    calendar = store.read_range(
        ref, "calendar", codes=(), start=start, end=end, columns=("date", "is_trading_day", "source_ref")
    )
    identities = store.read_range(
        ref, "securities", codes=codes, start=start, end=end, columns=("code", "ipo_date", "out_date", "source_ref")
    )
    status = store.read_range(
        ref,
        "security_status",
        codes=codes,
        start=start,
        end=end,
        columns=("code", "date", "is_suspended", "source_ref"),
    )
    events = store.read_range(
        ref, "security_events", codes=codes, start=start, end=end,
        columns=("code", "date", "event_type", "product_id", "source_ref"),
        product_id="cn_a_archive_no_trade",
    )
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
        identity = identities[identities.code.eq(code)].fillna("")
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
    return dict(
        snapshot_id=ref.snapshot_id,
        mode=store.load_manifest(ref).get("mode", "free"),
        bars=bars,
        gaps=pd.DataFrame(missing, columns=["code", "start", "end", "reason"]),
    )


def backup_archive(store, ref, destination):
    """显式备份一个已发布快照及引用原件；不复制凭据/任务队列、不覆盖目录。"""
    destination = Path(destination).resolve()
    if destination.exists():
        raise FileExistsError(destination)
    manifest = store.load_manifest(ref)
    base = store._snapshot_dir(ref.snapshot_id)
    assets = {Path("snapshots") / ref.snapshot_id / "manifest.json": base / "manifest.json"}
    evidence = {}
    for table in sorted({e["table"] for e in manifest.get("table_files", [])}):
        store._range_files(ref, table)  # 表级读取许可、路径和哈希。
        frame = store.read_table(ref, table)  # 备份整个授权样本，不能只核部分行权限。
        if table in {"knowledge_evidence", "terminal_disposals"}:
            for row in frame.itertuples():
                name = str(row.evidence_ref)
                digest = str(row.sha256)
                if name in evidence and evidence[name] != digest:
                    raise SnapshotTamperedError(f"conflicting evidence hashes: {name}")
                evidence[name] = digest
    for entry in [
        *manifest.get("table_files", []),
        *(r for i in manifest.get("items", []) for r in i.get("raw_files", [])),
    ]:
        source = (base / entry["path"]).resolve()
        if not source.is_relative_to(base.resolve()) or not source.is_file() or _sha256(source) != entry["sha256"]:
            raise SnapshotTamperedError(f"locked asset mismatch: {entry['path']}")
        assets[Path("snapshots") / ref.snapshot_id / entry["path"]] = source
    for name, digest in evidence.items():
        source = (store.root / "evidence" / name).resolve()
        if (
            not source.is_relative_to((store.root / "evidence").resolve())
            or not source.is_file()
            or _sha256(source) != digest
        ):
            raise SnapshotTamperedError(f"evidence missing or changed: {name}")
        assets[Path("evidence") / name] = source
    grants = store.root / "data-entitlements.json"
    if grants.is_file():
        assets[Path("data-entitlements.json")] = grants
    required = sum(p.stat().st_size for p in assets.values()) + len(json.dumps(store.config).encode())
    free = shutil.disk_usage(destination.parent).free
    if free < required + 1024 * 1024:
        raise OSError(f"insufficient backup space: need {required} bytes plus reserve, free {free}")
    destination.mkdir()  # 已存在即失败；不会覆盖已有备份。
    for relative, source in assets.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    (destination / "data-products.json").write_text(json.dumps(store.config, ensure_ascii=False, indent=2))
    files = [*assets, Path("data-products.json")]
    index = dict(
        snapshot_id=ref.snapshot_id,
        source_root=str(store.root.resolve()),
        required_bytes=required,
        free_before_bytes=free,
        device_disaster_recovery=False,
        files=[dict(path=str(p), sha256=_sha256(destination / p)) for p in files],
    )
    (destination / "backup-manifest.json").write_text(json.dumps(index, ensure_ascii=False, indent=2))
    return verify_archive_backup(destination)


def verify_archive_backup(destination):
    """按备份清单找出具体缺失/变更文件；返回可用DataStore重开的引用。"""
    root = Path(destination).resolve()
    index = json.loads((root / "backup-manifest.json").read_text())
    for entry in index["files"]:
        path = (root / entry["path"]).resolve()
        if not path.is_relative_to(root) or not path.is_file() or _sha256(path) != entry["sha256"]:
            raise SnapshotTamperedError(f"backup asset missing or changed: {entry['path']}")
    return SnapshotRef(snapshot_id=index["snapshot_id"], root=root)


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


def _history_objects(history, start, end):
    objects, exclusions, unknown = [], [], []
    for original in history.fillna("").to_dict("records"):
        code = str(original["code"])
        left = str(original.get("valid_from") or original.get("ipo_date") or "")[:10]
        right = str(original.get("valid_to") or original.get("out_date") or "")[:10]
        state = str(original.get("lifecycle_status", "known" if left else "unknown"))
        known = state == "known" and bool(left) and bool(original.get("source_ref"))
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
    for obj in objects:
        if len(identities[obj["code"]]) > 1:
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


def archive_requirements(history, calendar, cutoff, *, years=20, start=None):
    left = (pd.Timestamp(cutoff) - pd.DateOffset(years=years)).date() if start is None else start
    start, end = left.isoformat(), cutoff.isoformat()
    objects, exclusions, unknown = _history_objects(history, start, end)
    for obj in objects:
        if obj["attributes_known"] and (obj["security_type"] != "stock" or obj["market"] not in ("SH", "SZ", "BJ")):
            obj["excluded"] = True
            exclusions.append(dict(code=obj["code"], reason="not_common_stock_or_market", interval=[start, end]))
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
        exclusions=exclusions,
        unknown_ranges=unknown,
        target_scope=dict(history.attrs.get("target_scope", {})),
        requested_markets=sorted(set(o["market"] for o in objects)),
    )


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


def scan_local_coverage(store, ref, *, asset_snapshots=(), progress=None):
    """只读逐块核行；大日线原件按代码逐组处理，内存中只保留日期位图。"""
    from hetu_data.providers.baostock import BaoStockProvider

    inventory = CoverageInventory()
    daily_sources = defaultdict(list)
    extras = defaultdict(list)
    for owner, manifest, base, entry, _selection in _source_rows(store, ref, asset_snapshots):
        path = (base / entry["path"]).resolve()
        try:
            if not path.is_relative_to(base.resolve()):
                raise ValueError("audit source path outside snapshot")
            owner.check_access(manifest.get("mode", "free"), owner.product(entry["product_id"]), operation="read")
            if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                raise ValueError("audit source hash mismatch")
            if entry["format"] == "raw":
                match = re.fullmatch(r"response-(.+)-(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})\.parquet", path.name)
                if not match:
                    inventory.failures.append(dict(source=str(path), reason="raw query scope not proven"))
                    continue
                code = match[1].replace("_", ".")
                daily_sources[code].append((path, entry["product_id"], match[2], match[3]))
            else:
                frame = pd.read_parquet(path)
                inventory.stats["files"] += 1
                inventory.stats["bytes"] += path.stat().st_size
                table = entry["table"]
                owner._check_paid_rows(entry["product_id"], frame, table)
                frame["evidence_root"] = str(owner.root)
                if table in ("daily_bars", "security_status", "security_events", "adjustment_factors") and len(frame):
                    for code, group in frame.groupby("code"):
                        extras[str(code)].append((table, group, str(path)))
                else:
                    inventory.frames.setdefault(table, []).append(frame)
                if table == "corporate_actions":
                    inventory.action_entries.append(entry)
        except (OSError, ValueError, PermissionError, KeyError) as error:
            inventory.failures.append(dict(source=str(path), reason=str(error)))
    codes = sorted(set(daily_sources) | set(extras))
    for position, code in enumerate(codes, 1):
        groups = defaultdict(list)
        sources = []
        for table, frame, path in extras[code]:
            groups[table].append(frame)
            sources.append(path)
        for path, product, start, end in daily_sources[code]:
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
                    groups["daily_bars"].append(BaoStockProvider().standardize(product, raw, {})["daily_bars"])
            valid = raw.tradestatus.isin(["0", "1"]) & raw.isST.isin(["0", "1"])
            if valid.any():
                groups["security_status"].append(
                    BaoStockProvider().standardize("cn_a_status", raw[valid], {})["security_status"]
                )
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
            inventory.stats["tables"][table] = inventory.stats["tables"].get(table, 0) + len(frame)
            if table == "daily_bars":
                values = frame[_BAR_FIELDS].apply(pd.to_numeric, errors="coerce")
                valid = _valid_archive_bars(values)
                numeric = pd.concat([frame[["date"]], values], axis=1)
                conflict = numeric.groupby("date")[_BAR_FIELDS].nunique(dropna=False).gt(1).any(axis=1)
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
    if "calendar" in inventory.frames:
        inventory.calendar = inventory.frames["calendar"][["date", "is_trading_day"]].drop_duplicates()
        inventory.calendar["date"] = inventory.calendar.date.astype(str).str[:10]
        inventory.calendar["is_trading_day"] = inventory.calendar.is_trading_day.map(_truthy)
        inventory.calendar = inventory.calendar.drop_duplicates()
        conflicts = inventory.calendar.groupby("date").is_trading_day.nunique().gt(1)
        if conflicts.any():
            inventory.failures.append(dict(source="calendar", reason="conflicting trading calendar"))
            inventory.calendar = inventory.calendar[~inventory.calendar.date.isin(conflicts[conflicts].index)]
    return inventory


def _coverage_rows(requirements, inventory, domains):
    sessions = requirements["expected_sessions"]
    rows = []
    for obj in requirements["objects"]:
        if obj.get("excluded") or obj["required"][0] > obj["required"][1]:
            continue
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


def plan_coverage_gaps(audits, store, *, as_of, contracts, domain_products):
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
    for (product, code), intervals in grouped.items():
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
                mode="paid" if store.product(product).get("fee_class") == "paid" else "free",
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
                if value is not None and counts[key] is not None:
                    counts[key] += value
    return dict(
        requests=requests,
        counts=counts,
        unknown_ranges=unknown,
        unresolved=unresolved,
        executed=False,
        retries=0,
        new_data_fee_cny=0,
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
