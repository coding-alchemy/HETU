"""Register immutable collections of existing snapshots and read bounded selections."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import duckdb
import pandas as pd

from hetu_data.archive import _BAR_FIELDS, ARCHIVE_COLUMNS, _archive_query_details, filter_bars
from hetu_data.locking import worker_lock
from hetu_data.models import LibraryRef, SnapshotRef
from hetu_data.store import TABLE_SCHEMAS, DataStore, SnapshotTamperedError, _sha256, query_column

_PROVENANCE = ("source_root", "snapshot_id")
_SUMMARY = ("library_id", "previous_library_id", "created_at", "status", "source_policy", "sources")


def _manifest_path(store, ref):
    root = store.root.resolve()
    if ref.root.resolve() != root:
        raise ValueError("library belongs to another data root")
    path = root / "libraries" / ref.library_id / "manifest.json"
    if path.resolve() != path:
        raise ValueError("invalid library manifest path")
    return path


def load_library(store, ref):
    document = json.loads(_manifest_path(store, ref).read_text(encoding="utf-8"))
    if document["library_id"] != ref.library_id or Path(document["root"]).resolve() != ref.root.resolve():
        raise SnapshotTamperedError("library identity does not match its reference")
    return document


def list_libraries(store):
    output = []
    for path in sorted((store.root / "libraries").glob("*/manifest.json")):
        if path.parent.name.startswith("."):
            continue
        ref = LibraryRef(path.parent.name, store.root)
        summary = _manifest_path(store, ref).with_name("summary.json")
        if summary.is_file() and not summary.is_symlink():
            document = json.loads(summary.read_text(encoding="utf-8"))
            if document["library_id"] != ref.library_id:
                raise SnapshotTamperedError("library summary does not match its reference")
        else:
            document = load_library(store, ref)
        output.append({key: document[key] for key in _SUMMARY})
    return output


def _asset_owner(store, asset):
    root = Path(asset["root"]).resolve(strict=True)
    config = Path(asset["config_path"]) if asset.get("config_path") else None
    return store if root == store.root.resolve() and config is None else DataStore(root, config_path=config)


def _locked_asset(store, asset):
    owner = _asset_owner(store, asset)
    ref = SnapshotRef(asset["snapshot_id"], owner.root)
    if Path(ref.snapshot_id).name != ref.snapshot_id or ref.snapshot_id.startswith("."):
        raise ValueError("invalid snapshot identifier")
    base = (owner.root / "snapshots" / ref.snapshot_id).resolve()
    path = base / "manifest.json"
    if base.parent != (owner.root / "snapshots").resolve() or path.resolve() != path:
        raise ValueError("invalid snapshot reference")
    if not path.is_file() or (
        asset.get("manifest_sha256") is not None and _sha256(path) != asset["manifest_sha256"]
    ):
        raise SnapshotTamperedError(f"locked snapshot manifest mismatch: {path}")
    return owner, ref, owner.load_manifest(ref)


def _safe_evidence(owner, evidence_ref):
    path = (owner.root / evidence_ref).resolve()
    return path if path.is_relative_to(owner.root.resolve()) and path.is_file() else None


def archive_source_policy(document):
    """只接受发布时锁定、明确限定原价档案用途及差异范围的依据。"""
    policy = document["source_policy"]
    product, ref, digest = (policy.get(key) for key in ("primary_product_id", "basis_ref", "basis_sha256"))
    if not (product and ref and digest):
        return None
    roots = {source["root"] for source in document["sources"] if source["product_id"] == product}
    for root in sorted(roots):
        try:
            folder = Path(root).resolve()
            path = (folder / ref).resolve()
            if not path.is_relative_to(folder):
                continue
            raw = path.read_bytes()
            if sha256(raw).hexdigest() != digest:
                continue
            scope = json.loads(raw)
            if (
                scope.get("purpose") != "daily_archive" or scope.get("primary_product_id") != product
                or not isinstance(scope.get("basis"), str) or not scope["basis"].strip()
                or date.fromisoformat(scope["start"]) > date.fromisoformat(scope["end"])
            ):
                return None
            for field in ("codes", "compared_product_ids", "fields"):
                if not isinstance(scope[field], list) or not scope[field] or not all(
                    isinstance(value, str) and value for value in scope[field]
                ):
                    return None
            if product in scope["compared_product_ids"] or not set(scope["fields"]) <= set(_BAR_FIELDS):
                return None
            return dict(scope, basis_ref=ref)
        except (OSError, ValueError, AttributeError, KeyError, TypeError):
            continue
    return None


def _validated_revision(store, revision, assets, *, inherited_origins=None):
    keys = ("root", "product_id", "table", "superseded_snapshot_id", "selected_snapshot_id",
            "codes", "start", "end", "evidence_ref")
    try:
        rule = {key: revision[key] for key in keys}
        rule["root"] = str(Path(rule["root"]).resolve())
        old_key = rule["root"], rule["superseded_snapshot_id"]
        new_key = rule["root"], rule["selected_snapshot_id"]
        old, new = assets[old_key], assets[new_key]
        if old == new or not set(rule["codes"]) and "code" in TABLE_SCHEMAS[rule["table"]]["columns"]:
            return None
        if date.fromisoformat(rule["start"]) > date.fromisoformat(rule["end"]):
            return None
        owner, _, manifest = _locked_asset(store, new)
        path = _safe_evidence(owner, rule["evidence_ref"])
        if path is None:
            return None
        for asset in (old, new):
            _, _, doc = _locked_asset(store, asset)
            if rule["table"] not in asset["tables"] or not any(
                row["product_id"] == rule["product_id"] and row["table"] == rule["table"]
                for row in doc["table_files"]
            ):
                return None
        try:
            evidence = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeError, ValueError):
            evidence = {}
        # A detached revision must bind both snapshots, product, table, dates and code scope.
        if isinstance(evidence, dict) and all(
            evidence.get(key) == rule[key] for key in keys if key not in ("root", "evidence_ref")
        ):
            expected_roots = {rule["root"]}
            if inherited_origins:
                origin = inherited_origins.get(old_key)
                if origin and origin == inherited_origins.get(new_key):
                    expected_roots.add(origin)
            if evidence.get("root") in expected_roots:
                return rule
        refresh = manifest.get("calendar_refresh", {}).get(rule["product_id"], {})
        if (rule["table"] == "calendar" and rule["superseded_snapshot_id"] in refresh.get("supersedes", ())
                and refresh["start"] <= rule["start"] <= rule["end"] <= refresh["end"]):
            return rule
    except (KeyError, TypeError, ValueError):
        return None
    return None


def publish_library(store, *, asset_snapshots, previous=None):
    prior = load_library(store, previous) if previous else {}
    assets = {}
    entries, sources, rules = {}, {}, []
    policy = prior.get("source_policy", {"primary_product_id": None, "basis_ref": None})
    prior_assets = prior.get("asset_snapshots", ())
    for index, original in enumerate((*prior_assets, *asset_snapshots)):
        asset = json.loads(json.dumps(dict(original), default=str))
        asset["root"] = str(Path(asset["root"]).resolve())
        if asset.get("config_path"):
            asset["config_path"] = str(Path(asset["config_path"]).resolve())
        asset.setdefault("config_path", None)
        if "tables" not in asset:
            raise ValueError("asset must declare registered tables")
        owner, ref, manifest = _locked_asset(store, asset)
        source_dir = owner.root / "snapshots" / ref.snapshot_id
        asset["manifest_sha256"] = _sha256(source_dir / "manifest.json")
        key = asset["root"], ref.snapshot_id
        if key in assets:
            prior_asset = assets[key]
            if prior_asset["config_path"] != asset["config_path"]:
                raise ValueError("conflicting configuration for the same snapshot")
            asset["tables"] = sorted(set(prior_asset["tables"]) | set(asset["tables"]))
            asset["raw_products"] = sorted(
                set(prior_asset.get("raw_products", ())) | set(asset.get("raw_products", ()))
            )
        assets[key] = asset
        mode = manifest.get("mode", "free")
        for item in manifest.get("table_files", ()):
            if item["table"] not in asset["tables"]:
                continue
            target = (source_dir / item["path"]).resolve()
            if not target.is_relative_to(source_dir.resolve()):
                raise ValueError("registered file escapes its snapshot")
            pid = item["product_id"]
            owner.check_access(mode, owner.product(pid), operation="read")
            provenance = dict(root=asset["root"], snapshot_id=ref.snapshot_id, product_id=pid,
                              mode=mode, manifest_sha256=asset["manifest_sha256"])
            sources[(*key, pid)] = provenance
            entries[str(target)] = dict(**provenance, config_path=asset["config_path"], table=item["table"],
                                        path=item["path"], sha256=item["sha256"], partition=item.get("partition", ""))
        rules.extend(original.get("revision_selections", ()))
        candidate = original.get("source_policy", {})
        if candidate.get("primary_product_id") and (*key, candidate["primary_product_id"]) in sources:
            basis = _safe_evidence(owner, candidate.get("basis_ref") or ".")
            if basis and index >= len(prior_assets):
                policy = dict(primary_product_id=candidate["primary_product_id"], basis_ref=candidate["basis_ref"],
                              basis_sha256=_sha256(basis))
    inherited_rules = prior.get("revision_selections", ())
    origins = {(asset["root"], asset["snapshot_id"]): asset.get("original_root")
               for asset in prior.get("asset_snapshots", ())}
    rules.extend(inherited_rules)
    revisions, unproved_revision = [], False
    for rule in rules:
        verified = _validated_revision(
            store, rule, assets, inherited_origins=origins if rule in inherited_rules else None,
        )
        if verified is None:
            unproved_revision = True
        elif verified not in revisions:
            revisions.append(verified)
    if policy["primary_product_id"] not in {source["product_id"] for source in sources.values()}:
        policy = {"primary_product_id": None, "basis_ref": None}
    library_id = f"lib-{uuid4().hex}"
    ref = LibraryRef(library_id, store.root.resolve())
    document = dict(library_id=library_id, root=str(ref.root),
                    previous_library_id=previous.library_id if previous else None,
                    created_at=datetime.now(UTC).isoformat(),
                    status="readable" if policy["primary_product_id"] and not unproved_revision else "limited",
                    asset_snapshots=list(assets.values()), table_files=list(entries.values()),
                    revision_selections=revisions, source_policy=policy, sources=list(sources.values()))
    with worker_lock(store.root, reentrant=True):
        target = _manifest_path(store, ref)
        target.parent.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".publish-", dir=target.parent.parent) as scratch:
            staging = Path(scratch) / "library"
            staging.mkdir()
            (staging / "manifest.json").write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            (staging / "summary.json").write_text(
                json.dumps({key: document[key] for key in _SUMMARY}, ensure_ascii=False), encoding="utf-8"
            )
            staging.rename(target.parent)
    return ref


def _select_revisions(frame, table, revisions):
    clock = next((key for key in ("date", "ex_date") if key in frame), None)
    if clock is None or frame.empty:
        return frame
    keep = pd.Series(True, index=frame.index)
    for rule in revisions:
        if rule["table"] != table:
            continue
        mask = frame.source_root.eq(rule["root"]) & frame.product_id.eq(rule["product_id"])
        mask &= frame[clock].between(rule["start"], rule["end"])
        if "code" in frame:
            mask &= frame.code.isin(rule["codes"])
        selected = frame[mask & frame.snapshot_id.eq(rule["selected_snapshot_id"])]
        keys = ["code", clock] if "code" in frame else [clock]
        selected_keys = set(selected[keys].itertuples(index=False, name=None))
        old = frame[mask & frame.snapshot_id.eq(rule["superseded_snapshot_id"])]
        superseded = [index for index, key in zip(old.index, old[keys].itertuples(index=False, name=None), strict=True)
                      if key in selected_keys]
        keep.loc[superseded] = False
    return frame.loc[keep]


def _relevant(entry, codes, start, end):
    parts = dict(piece.split("=", 1) for piece in entry["partition"].split("|") if "=" in piece)
    span = parts.get("month") or parts.get("year")
    return (not codes or not parts.get("code") or parts["code"] in codes) and (
        not span or start.isoformat()[:len(span)] <= span <= end.isoformat()[:len(span)]
    )


def _read_library_range(store, manifest, name, *, codes, start, end, columns, product_id=None):
    schema = TABLE_SCHEMAS.get(name)
    if not schema or not columns or not set(columns).issubset((*schema["columns"], *_PROVENANCE)):
        raise ValueError("unknown table or column")
    if start > end:
        raise ValueError("start must not exceed end")
    frames = []
    eligible = {
        (row["root"], row["snapshot_id"]) for row in manifest["table_files"]
        if row["table"] == name and (product_id is None or row["product_id"] == product_id)
        and _relevant(row, codes, start, end)
    }
    for asset in manifest["asset_snapshots"]:
        if (asset["root"], asset["snapshot_id"]) not in eligible:
            continue
        owner, ref, _ = _locked_asset(store, asset)
        frame = owner.read_range(ref, name, codes=codes, start=start, end=end, columns=tuple(schema["columns"]),
                                 product_id=product_id)
        if not frame.empty:
            frames.append(frame.assign(source_root=str(owner.root.resolve()), snapshot_id=ref.snapshot_id))
    if not frames:
        return pd.DataFrame({
            column: pd.Series(dtype="float64" if query_column(name, column).startswith("TRY_CAST") else "object")
            for column in columns
        })
    merged = _select_revisions(pd.concat(frames, ignore_index=True), name, manifest["revision_selections"])
    for asset in manifest["asset_snapshots"]:
        if asset.get("original_root"):
            relocated = merged.source_root.eq(asset["root"]) & merged.snapshot_id.eq(asset["snapshot_id"])
            merged.loc[relocated, "source_root"] = asset["original_root"]
    fact_columns = [c for c in schema["columns"] if c != "source_ref"]
    order = [column for column in ("code", "date", "ex_date", "product_id", *_PROVENANCE, "source_ref")
             if column in merged]
    return merged.drop_duplicates(fact_columns).sort_values(order).loc[:, list(columns)].reset_index(drop=True)


def read_library_range(store, ref, name, *, codes, start, end, columns, product_id=None):
    return _read_library_range(store, load_library(store, ref), name, codes=codes, start=start, end=end,
                               columns=columns, product_id=product_id)


def query_library(store, ref, *, codes, start, end, markets=(), columns=ARCHIVE_COLUMNS, predicates=()):
    if start > end:
        raise ValueError("start must not exceed end")
    if not codes or set(markets) - {"SH", "SZ", "BJ"}:
        raise ValueError("an explicit code selection and valid markets are required")
    selection = json.loads(json.dumps(dict(library_id=ref.library_id, root=str(ref.root.resolve()), codes=codes,
                                           markets=markets, start=start.isoformat(), end=end.isoformat(),
                                           columns=columns, predicates=predicates)))
    manifest = load_library(store, ref)
    selected = tuple(code for code in codes if not markets or code.rsplit(".", 1)[-1] in markets)

    def read(table, fields, product_id=None):
        if not selected:
            return pd.DataFrame(columns=fields)
        return _read_library_range(store, manifest, table, codes=selected, start=start, end=end,
                                   columns=fields, product_id=product_id)

    bars = read("daily_bars", (*ARCHIVE_COLUMNS, *_PROVENANCE))
    gaps, auxiliary_limits = _archive_query_details(read, bars, selected, start, end)
    has_conflict = gaps.reason.isin(("conflicting_bars", "conflicting_trade_evidence")).any()
    status = "limited" if has_conflict or auxiliary_limits else manifest["status"]
    filtered = filter_bars(bars, columns=tuple(c for c in columns if c not in _PROVENANCE), predicates=predicates)
    return dict(selection=selection, status=status, bars=filtered.join(bars[list(_PROVENANCE)]),
                gaps=gaps, auxiliary_limits=auxiliary_limits, sources=manifest["sources"])


def _sql_nodes(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _sql_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _sql_nodes(child)


def query_library_sql(store, selection, sql, parameters=()):
    ref = LibraryRef(selection["library_id"], Path(selection["root"]))
    selected = query_library(store, ref, codes=tuple(selection["codes"]), markets=tuple(selection["markets"]),
                             start=date.fromisoformat(selection["start"]), end=date.fromisoformat(selection["end"]),
                             columns=tuple(selection["columns"]), predicates=tuple(selection["predicates"]))
    config = dict(enable_external_access=False, autoload_known_extensions=False, autoinstall_known_extensions=False)
    with duckdb.connect(config=config) as conn:
        try:
            statement, = conn.extract_statements(sql)
            if statement.type != duckdb.StatementType.SELECT:
                raise ValueError("library SQL accepts one SELECT")
            tree = json.loads(conn.execute("SELECT json_serialize_sql(?)", [sql]).fetchone()[0])
        except (ValueError, duckdb.Error) as error:
            raise ValueError("library SQL accepts one SELECT") from error
        tables = [node for node in _sql_nodes(tree) if node.get("type") in ("BASE_TABLE", "TABLE_FUNCTION")]
        if tree.get("error") or not tables or any(
            table.get("table_name", "").casefold() != "daily_bars"
            or table.get("schema_name") or table.get("catalog_name")
            for table in tables
        ):
            raise ValueError("library SQL may only read the selected daily_bars")
        conn.register("daily_bars", selected["bars"])
        return conn.execute(sql, parameters).df()
