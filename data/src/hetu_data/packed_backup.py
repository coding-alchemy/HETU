"""Manual, content-addressed external backups of registered library assets."""
from __future__ import annotations

import hashlib
import io
import json
import os
import plistlib
import re
import shlex
import shutil
import sqlite3
import subprocess
import tarfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from hetu_data.access import AccessDenied
from hetu_data.archive import _backup_config, _check_backup_metadata, _snapshot_backup_assets
from hetu_data.library import _asset_owner, _locked_asset, load_library
from hetu_data.locking import worker_lock
from hetu_data.models import LibraryRef, SnapshotRef
from hetu_data.store import DataStore, SnapshotTamperedError

T7_ROOT = Path("/Volumes/T7")
T7_UUID = "70F039F7-51AD-3716-9E14-CD2307D8F892"
PACK_BYTES = 1024**3
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?im)^\s*(?:export\s+)?[\x60'\"-]*"
    r"(?:[\w-]*token|[\w-]*password|[\w-]*secret|api[_-]?key|authorization)[\x60'\"]*\s*[:=]\s*\S",
)


def _read(path):
    return json.loads(path.read_text())


def _body(value):
    _check_backup_metadata(value)
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


def _write(path, body):
    with path.open("wb") as output:
        output.write(body)
        output.flush()
        os.fsync(output.fileno())


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _relative(name):
    path = Path(name)
    if path.is_absolute() or not path.parts or ".." in path.parts or path.as_posix() != name:
        raise ValueError(f"unsafe relative path: {name}")
    return path


def _inside(root, relative):
    path = root / _relative(str(relative))
    if path.resolve() != path:
        raise ValueError(f"symlink in backup path: {path}")
    return path


def _volume_identity(mount):
    if not mount.is_mount() or mount.resolve() != mount:
        raise OSError("T7 is not mounted at the expected path")
    response = subprocess.run(
        ["/usr/sbin/diskutil", "info", "-plist", str(mount)], check=False, capture_output=True, timeout=15,
    )
    if response.returncode:
        raise OSError("cannot verify T7 volume identity with diskutil")
    info = plistlib.loads(response.stdout)
    if info.get("MountPoint") != str(mount) or info.get("VolumeUUID") != T7_UUID:
        raise OSError("wrong T7 volume identity")
    return info["VolumeUUID"]


def _repository(repository, identity=None):
    mount_id = _volume_identity(T7_ROOT)
    path = Path(repository).absolute()
    expected = T7_ROOT / "data/HETU-backups/a-share-daily"
    if path != expected or path.resolve() != path:
        raise ValueError("backup repository must be the designated directory on T7")
    if identity is not None and identity != mount_id:
        raise OSError("T7 volume identity changed")
    return path, mount_id


def _row(path, *, source=None, body=None, locked_hash=None):
    if body is not None:
        return dict(path=str(path), size=len(body), sha256=_digest(body), text=body.decode())
    return dict(path=str(path), size=source.stat().st_size, sha256=locked_hash or _sha256(source), source=str(source))


def _add_file(rows, path, source, *, locked_hash=None):
    row = _row(path, source=source, locked_hash=locked_hash)
    if path in rows and rows[path]["sha256"] != row["sha256"]:
        raise SnapshotTamperedError(f"conflicting backup contents: {path}")
    rows[path] = row


def _owner_path(source, owner):
    try:
        return owner.relative_to(source)
    except ValueError:
        return Path("owners") / _digest(str(owner).encode())[:24]


def _config_path(config):
    return Path("backup-config") / f"{_digest(str(config).encode())}.json" if config else Path("data-products.json")


def _merge_entries(rows, entries):
    for path, row in entries.items():
        if path in rows and rows[path]["sha256"] != row["sha256"]:
            old, new = rows[path], row
            if path.name == "data-entitlements.json":
                key = "grants"
                records = {item["product_id"]: item for item in json.loads(old["text"])[key]}
                added = {item["product_id"]: item for item in json.loads(new["text"])[key]}
            elif path.name == "data-products.json" or path.parent.name == "backup-config":
                key = "products"
                records = json.loads(old["text"])[key]
                added = json.loads(new["text"])[key]
            else:
                raise SnapshotTamperedError(f"conflicting registered backup contents: {path}")
            if any(records[product] != added[product] for product in records.keys() & added.keys()):
                raise SnapshotTamperedError(f"conflicting frozen product configuration: {path}")
            records.update(added)
            row = _row(path, body=_body({key: list(records.values()) if key == "grants" else records}))
        rows[path] = row


def _products(owner, asset, rows, permissions):
    products = {row["product_id"] for row in rows} | set(asset.get("raw_products", ()))
    allowed, denied = set(), {}
    for product in products:
        mode = next((row.get("mode", "free") for row in rows if row["product_id"] == product), None)
        if mode is None:
            manifest = owner.load_manifest(SnapshotRef(asset["snapshot_id"], owner.root))
            mode = manifest.get("mode", "free")
        key = owner.root, asset.get("config_path"), product, mode
        if key not in permissions:
            try:
                product_config = owner.product(product)
                for operation in ("read", "backup"):
                    owner.check_access(mode, product_config, operation=operation)
            except AccessDenied as error:
                permissions[key] = str(error)
            else:
                permissions[key] = None
        if permissions[key]:
            denied[product] = permissions[key]
        else:
            allowed.add(product)
    return allowed, denied


def _scan_library(store, doc, captures, permissions, owners, snapshot_cache, *, exclude_raw_originals):
    """Return only a whole, queryable library or report why its dependencies are unavailable."""
    sources = defaultdict(list)
    for row in doc["table_files"]:
        sources[row["root"], row["snapshot_id"]].append(row)
    unavailable = []
    eligible = []
    for asset in doc["asset_snapshots"]:
        if not Path(asset["root"]).is_relative_to(store.root.resolve()):
            unavailable.append(dict(root=asset["root"], snapshot_id=asset["snapshot_id"],
                                    reason="asset outside registered formal root"))
            continue
        identity = asset["root"], asset.get("config_path")
        if identity not in owners:
            owners[identity] = _asset_owner(store, asset)
        owner = owners[identity]
        rows = sources[asset["root"], asset["snapshot_id"]]
        allowed, denied = _products(owner, asset, rows, permissions)
        for product, reason in sorted(denied.items()):
            unavailable.append(dict(root=asset["root"], snapshot_id=asset["snapshot_id"],
                                    products=[product], reason=reason))
        if allowed:
            eligible.append((asset, allowed))

    entries = {}
    by_root = defaultdict(set)
    by_config = defaultdict(set)
    base = store.root.resolve()
    excluded = []
    for asset, allowed in eligible:
        owner = owners[asset["root"], asset.get("config_path")]
        origin = owner.root.resolve()
        relative = _owner_path(base, origin)
        sid = asset["snapshot_id"]
        rows = [r for r in sources[asset["root"], sid] if r["product_id"] in allowed]
        raw_products = tuple(sorted(set(asset.get("raw_products", ())) & allowed))
        signature = (
            origin, asset.get("config_path"), sid, asset.get("manifest_sha256"),
            tuple(sorted((r["path"], r["sha256"]) for r in rows)), raw_products,
        )
        if signature not in snapshot_cache:
            _, ref, manifest = _locked_asset(store, asset)
            rejected, table_rejected = [], []
            assets, products = _snapshot_backup_assets(
                owner, ref, manifest, selected={r["path"] for r in rows}, raw_products=raw_products,
                raw_exclusions=rejected, table_exclusions=table_rejected,
                exclude_raw_originals=exclude_raw_originals,
            )
            snapshot_cache[signature] = assets, products, rejected, table_rejected
        assets, products, rejected, table_rejected = snapshot_cache[signature]
        excluded.extend({**x, "root": str(origin), "snapshot_id": sid} for x in rejected)
        unavailable.extend({**x, "root": str(origin), "snapshot_id": sid} for x in table_rejected)
        if all(rel.name == "manifest.json" for rel in assets):
            unavailable.extend({**x, "root": str(origin), "snapshot_id": sid} for x in rejected)
            continue
        by_root[origin].update(products)
        by_config[origin, asset.get("config_path")].update(products)
        locked = {Path("snapshots") / sid / r["path"]: r["sha256"] for r in rows}
        for rel, path in assets.items():
            _add_file(entries, relative / rel, path, locked_hash=locked.get(rel))
    for (origin, config), products in by_config.items():
        owner = owners[str(origin), config]
        contract = _backup_config(owner, products, omit_backup=exclude_raw_originals)
        for rel, value in contract.items():
            if rel.name == "data-products.json":
                rel = _config_path(config)
            path = _owner_path(base, origin) / rel
            _merge_entries(entries, {path: _row(path, body=_body(value))})
        for grant in contract.get(Path("data-entitlements.json"), {}).get("grants", ()):
            for field in ("license_ref", "read_license_ref", "approved_scope_ref"):
                _capture_reference(entries, base, origin, grant.get(field), excluded)
        for pid in products:
            product = owner.product(pid)
            grant = owner._grant(pid) if product.get("fee_class") == "paid" else None
            captures[str(origin), config, pid] = dict(
                root=str(origin), config_path=config, product_id=pid, fee_class=product.get("fee_class"),
                contract={key: product.get(key) for key in ("fixed_market", "source_api", "cross_section")},
                scope=_backup_scope(grant, base, origin),
            )

    if unavailable:
        return entries, unavailable + excluded, False
    evidence = [(Path(r["root"]), r["evidence_ref"], None) for r in doc["revision_selections"]]
    policy = doc["source_policy"]
    if policy.get("basis_ref"):
        candidates = [root for root, p in by_root.items() if policy["primary_product_id"] in p]
        rel = policy["basis_ref"]
        valid = [root for root in candidates if _inside(root, rel).is_file()
                 and _sha256(_inside(root, rel)) == policy.get("basis_sha256")]
        if not valid:
            raise SnapshotTamperedError(f"missing source policy: {rel}")
        evidence.append((valid[0], rel, policy["basis_sha256"]))
    for root, rel, digest in evidence:
        path = _owner_path(base, root) / rel
        entries[path] = _row(path, source=_inside(root, rel), locked_hash=digest)
    return entries, excluded, True


def _checked_metadata(payload, suffix):
    if suffix == ".json":
        _check_backup_metadata(json.loads(payload))
    elif suffix == ".jsonl":
        for line in payload.splitlines():
            _check_backup_metadata(json.loads(line))
    elif suffix in {".md", ".txt"}:
        if _CREDENTIAL_ASSIGNMENT.search(payload.decode("utf-8")):
            raise AccessDenied("registered text contains a credential assignment")


def _reference_candidates(root, owner, ref):
    reference = Path(ref.partition("#")[0])
    return (reference,) if reference.is_absolute() else (owner / reference, root / reference)


def _capture_reference(metadata, root, owner, ref, excluded=None):
    if not ref or not isinstance(ref, str):
        return
    reason = "registered reference missing"
    for path in _reference_candidates(root, owner, ref):
        if not path.is_relative_to(root) or not path.resolve().is_relative_to(root):
            reason = "registered reference outside the formal root"
            continue
        if not path.is_file():
            continue
        if path.resolve() != path or path.suffix not in {".json", ".jsonl", ".md", ".txt"}:
            reason = "registered reference uses an unsupported format or indirect path"
            continue
        relative = path.relative_to(root)
        if relative not in metadata:
            payload = path.read_bytes()
            try:
                _checked_metadata(payload, path.suffix)
            except (AccessDenied, ValueError) as error:
                if excluded is not None:
                    excluded.append(dict(path=ref, reason=str(error)))
                return
            metadata[relative] = _row(relative, body=payload)
        return
    if excluded is not None:
        excluded.append(dict(path=ref, reason=reason))


def _registered_ledgers(root, metadata, excluded):
    for pattern, field in (("historical-ledger-index.json", "destination"), ("ledger-index.json", "location")):
        for index in sorted((root / "migration").glob(f"*/{pattern}")):
            index_rel = index.relative_to(root)
            if index.resolve() != index:
                excluded.append(dict(path=str(index_rel), reason="ledger index is not a local file"))
                continue
            payload = index.read_bytes()
            _checked_metadata(payload, ".json")
            for item in json.loads(payload):
                registered = item[field]
                try:
                    rel = _relative(registered)
                    if field == "destination":
                        rel = index_rel.parent / rel
                    path = _inside(root, rel)
                    if path.suffix not in {".json", ".jsonl"}:
                        raise ValueError("unsupported ledger format")
                    content = path.read_bytes()
                    locked = item["sha256"]
                    if _digest(content) != locked:
                        # A live journal may have a registered historical prefix and newer events.
                        prefix = hashlib.sha256()
                        for line in content.splitlines(keepends=True) if field == "location" else ():
                            prefix.update(line)
                            if prefix.hexdigest() == locked:
                                break
                        else:
                            raise SnapshotTamperedError("registered ledger bytes do not match")
                    _checked_metadata(content, path.suffix)
                except (OSError, ValueError, AccessDenied, SnapshotTamperedError) as error:
                    excluded.append(dict(path=registered, index=str(index_rel), reason=str(error)))
                    continue
                metadata[rel] = _row(rel, body=content)
            metadata[index_rel] = _row(index_rel, body=payload)


def _frozen_metadata(root, excluded):
    metadata = {}
    for name in ("last_success.json", "README.md"):
        path = root / name
        if path.is_file() and not path.is_symlink():
            body = _body(_read(path)) if path.suffix == ".json" else path.read_bytes()
            metadata[Path(name)] = _row(name, body=body)
    # The main source root keeps the already-counted attempt history. Raw payloads
    # under the ledger are separate assets, not part of the accounting journal.
    _registered_ledgers(root, metadata, excluded)
    rejected = {item["path"] for item in excluded}
    for path in sorted((root / "updates/approved-usage").glob("*/usage.jsonl")):
        name = path.relative_to(root)
        if str(name) in rejected:
            continue
        try:
            payload = metadata[name]["text"].encode() if name in metadata else _inside(root, name).read_bytes()
            _checked_metadata(payload, ".jsonl")
        except (OSError, ValueError, AccessDenied) as error:
            excluded.append(dict(path=str(name), reason=str(error)))
            continue
        for line in payload.splitlines():
            item = json.loads(line)
            if item.get("type") == "approval_limits":
                _capture_reference(metadata, root, root, item["limits"].get("approved_scope_ref"), excluded)
        metadata[name] = _row(name, body=payload)
    return metadata


def _frozen_coverage(root, libraries):
    metadata = {}
    for library in libraries:
        for path in sorted((root / "archive/coverage" / library).glob("*.json")):
            value = _read(path)
            relative = path.relative_to(root)
            metadata[relative] = _row(relative, body=_body(value))
            target = root / "archive/targets" / path.name
            if target.is_file():
                relative = target.relative_to(root)
                metadata[relative] = _row(relative, body=_body(_read(target)))
            for field in ("detail_ref", "delta_evidence_ref"):
                ref = value.get(field)
                _capture_reference(metadata, root, root, ref)
    return metadata


def plan_packed_backup(
    store, repository: Path, *, output: Path, library_id: str | None = None, exclude_raw_originals: bool = False,
) -> Path:
    repository, identity = _repository(repository)
    root = store.root.resolve()
    output = Path(output).absolute()
    if output.exists() or output.is_relative_to(root) or output.is_relative_to(T7_ROOT):
        raise ValueError("use a new local plan file outside the data root and T7")
    excluded, libraries, permissions = [], [], {}
    with worker_lock(root, reentrant=True):
        selected = (
            [_inside(root, f"libraries/{LibraryRef(library_id, root).library_id}/manifest.json")]
            if library_id else sorted((root / "libraries").glob("*/manifest.json"))
        )
        documents = [
            (path, path.stat().st_size, path.stat().st_mtime_ns)
            for path in selected
            if not path.parent.name.startswith(".")
        ]
        frozen = _frozen_metadata(root, excluded)
    plan_id = uuid4().hex
    inventory = output.with_name(output.name + ".jsonl")
    rows = {}
    checked_permissions, owners, snapshot_cache = {}, {}, {}
    for path, size, modified in documents:
        doc = load_library(store, LibraryRef(path.parent.name, root))
        stat = path.stat()
        if (stat.st_size, stat.st_mtime_ns) != (size, modified):
            raise SnapshotTamperedError(f"published library changed after plan freeze: {path}")
        entries, rejected, complete = _scan_library(
            store, doc, permissions, checked_permissions, owners, snapshot_cache,
            exclude_raw_originals=exclude_raw_originals,
        )
        excluded.extend(dict(library_id=doc["library_id"], **entry) for entry in rejected)
        if not entries:
            continue
        _merge_entries(rows, entries)
        if not complete:
            continue
        lib = doc["library_id"]
        libraries.append(lib)
        excluded_raw = defaultdict(list)
        for entry in rejected:
            if entry.get("snapshot_id") and entry.get("path"):
                excluded_raw[entry["root"], entry["snapshot_id"]].append(entry["path"])
        for asset in doc["asset_snapshots"]:
            if paths := excluded_raw.get((asset["root"], asset["snapshot_id"])):
                prefix = Path("snapshots") / asset["snapshot_id"]
                asset["unavailable_raw_paths"] = [str(Path(path).relative_to(prefix)) for path in paths]
        rel = Path("libraries") / lib / "manifest.json"
        if excluded_raw:
            rows[rel] = _row(rel, body=_body(doc))
        else:
            _check_backup_metadata(doc)
            _add_file(rows, rel, path)
    if rows:
        with worker_lock(root, reentrant=True):
            frozen.update(_frozen_coverage(root, libraries))
        _merge_entries(rows, frozen)
    with inventory.open("xb") as stream:
        for row in rows.values():
            stream.write(_body(row))
    plan = dict(
        point=plan_id, source_root=str(root), repository=str(repository), volume=identity,
        inventory=inventory.name, inventory_sha256=_sha256(inventory), libraries=libraries,
        excluded=excluded, permissions=list(permissions.values()), frozen_at=datetime.now(UTC).isoformat(),
        file_count=len(rows), logical_bytes=sum(row["size"] for row in rows.values()),
        ledger_raw_scope="Attempt journals are retained verbatim. Raw payloads are included only as registered, "
                         "backup-authorized assets. Unregistered raw references are historical provenance.",
        logical_bytes_upper_bound=True, repository_free_bytes=shutil.disk_usage(T7_ROOT).free,
        recoverable_windows=[
            dict(library_id=rel.parts[2], target_id=rel.stem,
                 start=value.get("target_start"), end=value.get("target_end"),
                 latest_available_date=value.get("latest_available_date"), status=value.get("status"))
            for rel, row in frozen.items() if rel.parts[:2] == ("archive", "coverage")
            and rel.parts[2] in libraries for value in (json.loads(row["text"]),)
        ],
    )
    with output.open("xb") as stream:
        stream.write(_body(plan))
    if rows:
        with worker_lock(root, reentrant=True):
            authorizations = _inside(root, "updates/packed-backup-plans")
            authorizations.mkdir(parents=True, exist_ok=True)
            with (authorizations / f"{plan_id}.sha256").open("x") as stream:
                stream.write(_sha256(output) + "\n")
    return output


def _inventory(path):
    with path.open() as stream:
        for line in stream:
            row = json.loads(line)
            _relative(row["path"])
            yield row


def _tar_path(repository, digest):
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("invalid content or pack digest")
    return _inside(repository, f"packs/{digest}.tar")


def _content_index(repository, connection):
    connection.execute("CREATE TABLE IF NOT EXISTS objects (hash TEXT PRIMARY KEY, pack TEXT NOT NULL)")
    for pack in (repository / "packs").glob("*.tar"):
        if pack.name.startswith("._"):
            continue  # macOS file attributes on exFAT are not backup packs.
        if _tar_path(repository, pack.stem) != pack:
            raise ValueError("invalid pack path")
        if connection.execute("SELECT 1 FROM objects WHERE pack = ? LIMIT 1", (pack.stem,)).fetchone():
            continue
        if _sha256(pack) != pack.stem:
            raise SnapshotTamperedError(f"changed pack: {pack.name}")
        with tarfile.open(pack) as archive:
            for member in archive:
                _tar_path(repository, member.name)
                if not member.isfile():
                    raise SnapshotTamperedError("invalid pack member")
                connection.execute("INSERT OR IGNORE INTO objects VALUES (?,?)", (member.name, pack.stem))
    connection.commit()


def _write_pack(repository, identity, group):
    _repository(repository, identity)
    device = T7_ROOT.stat().st_dev
    temporary = _inside(repository, f"staging/{uuid4().hex}.tmp")
    with temporary.open("xb") as output:
        with tarfile.open(fileobj=output, mode="w") as archive:
            for row in group:
                _repository_check(repository, device)
                member = tarfile.TarInfo(row["sha256"])
                member.size = row["size"]
                if "text" in row:
                    archive.addfile(member, io.BytesIO(row["text"].encode()))
                else:
                    source = Path(row["source"])
                    if source.resolve() != source or source.stat().st_size != row["size"]:
                        raise SnapshotTamperedError(f"changed backup source: {source}")
                    with source.open("rb") as stream:
                        archive.addfile(member, stream)
        output.flush()
        os.fsync(output.fileno())
    _repository(repository, identity)
    with tarfile.open(temporary) as archive:
        for member in archive:
            with archive.extractfile(member) as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != member.name:
                    raise SnapshotTamperedError("pack content hash mismatch")
    digest = _sha256(temporary)
    final = _tar_path(repository, digest)
    if final.exists():
        if _sha256(final) != digest:
            raise SnapshotTamperedError(f"changed pack: {final.name}")
        temporary.unlink()
    else:
        temporary.rename(final)
    return digest


def _repository_check(repository, device):
    if repository.stat().st_dev != device or T7_ROOT.stat().st_dev != device:
        raise OSError("backup repository is not on the mounted T7")


def _basis_identity(root, owner, ref):
    if not ref:
        return None
    for path in _reference_candidates(root, owner, ref):
        if path.is_relative_to(root) and path.resolve() == path and path.is_file():
            return dict(sha256=_sha256(path), fragment=ref.partition("#")[2])
    return dict(unresolved_ref=ref)


def _backup_scope(grant, root, owner):
    if grant is None:
        return None
    fields = ("start", "end", "backup_cross_section")
    return {key: grant.get(key) for key in fields} | {
        key: sorted(set(grant.get(key, ()))) for key in ("markets", "codes")
    } | {
        key: _basis_identity(root, owner, grant.get(key)) for key in ("license_ref", "read_license_ref")
    }


def _covers_backup_scope(current, frozen):
    return (
        (current["start"] == frozen["start"] or (current["start"] and frozen["start"]
                                                and current["start"] <= frozen["start"]))
        and (current["end"] == frozen["end"] or (current["end"] and frozen["end"]
                                                and current["end"] >= frozen["end"]))
        and all(set(frozen[key]).issubset(current[key]) for key in ("markets", "codes"))
        and (frozen["backup_cross_section"] is not True or current["backup_cross_section"] is True)
        and all(frozen[key] == current[key] for key in ("license_ref", "read_license_ref"))
    )


def _validate_plan_permissions(plan):
    owners = {}
    for frozen in plan["permissions"]:
        root, config, pid = (frozen[key] for key in ("root", "config_path", "product_id"))
        key = root, config
        if key not in owners:
            owners[key] = DataStore(Path(root), config_path=Path(config) if config else None)
        owner = owners[key]
        product = owner.product(pid)
        mode = frozen["fee_class"]
        if product.get("fee_class") != mode or any(
            product.get(key) != value for key, value in frozen["contract"].items()
        ):
            raise AccessDenied(f"backup product contract changed: {pid}")
        for operation in ("read", "backup"):
            owner.check_access(mode, product, operation=operation)
        if mode == "paid" and not _covers_backup_scope(
            _backup_scope(owner._grant(pid), Path(plan["source_root"]), Path(root)), frozen["scope"],
        ):
            raise AccessDenied(f"backup permission scope changed: {pid}")


def execute_packed_backup(store, plan_path: Path) -> dict:
    plan_path = Path(plan_path).resolve()
    plan_bytes = plan_path.read_bytes()
    plan = json.loads(plan_bytes)
    if str(store.root.resolve()) != plan["source_root"]:
        raise ValueError("plan belongs to a different source root")
    repository, identity = _repository(plan["repository"], plan["volume"])
    if not plan["file_count"]:
        raise PermissionError("no eligible registered assets; see plan exclusions")
    point_id = _relative(plan["point"])
    if len(point_id.parts) != 1:
        raise ValueError("invalid recovery point identifier")
    approval = _inside(store.root.resolve(), f"updates/packed-backup-plans/{point_id}.sha256")
    if not approval.is_file() or approval.read_text().strip() != _digest(plan_bytes):
        raise AccessDenied("backup plan does not match its authoritative frozen source registration")
    inventory = _inside(plan_path.parent, plan["inventory"])
    digest = hashlib.sha256()
    rows = []
    with inventory.open("rb") as stream:
        for line in stream:
            digest.update(line)
            row = json.loads(line)
            _relative(row["path"])
            rows.append(row)
    if digest.hexdigest() != plan["inventory_sha256"]:
        raise SnapshotTamperedError("frozen backup inventory changed")
    for row in rows:
        if "source" in row:
            source = Path(row["source"])
            if not source.is_relative_to(store.root.resolve()) or source.resolve() != source:
                raise AccessDenied(f"source outside the registered formal root: {source}")
    _validate_plan_permissions(plan)
    _repository(repository, identity)
    device = T7_ROOT.stat().st_dev
    directory = T7_ROOT
    for part in repository.relative_to(T7_ROOT).parts:
        _repository_check(directory, device)
        directory = _inside(directory, part)
        directory.mkdir(exist_ok=True)
    _repository_check(repository, device)
    for directory in ("packs", "staging", "points"):
        _inside(repository, directory).mkdir(exist_ok=True)
    with worker_lock(repository):
        point = _inside(repository, f"points/{plan['point']}")
        if point.exists():
            return verify_packed_backup(repository, plan["point"])
        index = _inside(repository, "content.sqlite")
        with sqlite3.connect(index) as connection:
            _content_index(repository, connection)
            pending = {}
            for row in rows:
                if not connection.execute("SELECT 1 FROM objects WHERE hash = ?", (row["sha256"],)).fetchone():
                    pending.setdefault(row["sha256"], row)
            payload = sum(row["size"] for row in pending.values())
            if shutil.disk_usage(repository).free < payload + 2048 * len(pending) + inventory.stat().st_size:
                raise OSError("insufficient T7 space for the frozen difference")
            group, size, new_packs = [], 10240, []

            def flush():
                _validate_plan_permissions(plan)
                digest = _write_pack(repository, identity, group)
                connection.executemany("INSERT INTO objects VALUES (?,?)", [(r["sha256"], digest) for r in group])
                connection.commit()
                new_packs.append(digest)

            for row in pending.values():
                storage = ((row["size"] + 511) // 512 + 1) * 512
                if group and size + storage > PACK_BYTES:
                    flush()
                    group, size = [], 10240
                group.append(row)
                size += storage
            if group:
                flush()

            _repository(repository, identity)
            _validate_plan_permissions(plan)
            staging = _inside(repository, f"staging/point-{uuid4().hex}")
            staging.mkdir()
            packs = set()
            with (staging / "files.jsonl").open("wb") as output:
                for row in rows:
                    pack, = connection.execute("SELECT pack FROM objects WHERE hash = ?", (row["sha256"],)).fetchone()
                    packs.add(pack)
                    output.write(_body({k: row[k] for k in ("path", "size", "sha256")} | {"pack": pack}))
                output.flush()
                os.fsync(output.fileno())
            report = dict(
                recovery_point=plan["point"], source_root=plan["source_root"], volume=identity,
                libraries=plan["libraries"], excluded=plan["excluded"], frozen_at=plan["frozen_at"],
                backed_up_at=datetime.now(UTC).isoformat(), file_count=plan["file_count"],
                logical_bytes=plan["logical_bytes"], new_content_bytes=payload, new_content_files=len(pending),
                new_pack_bytes=sum(_tar_path(repository, p).stat().st_size for p in new_packs),
                packs=sorted(packs), manifest_sha256=_sha256(staging / "files.jsonl"),
                recoverable_windows=plan["recoverable_windows"],
                ledger_raw_scope=plan["ledger_raw_scope"],
            )
            repo_arg, point_arg = shlex.quote(str(repository)), shlex.quote(plan["point"])
            report["verify_command"] = f"hetu-data backup verify --repository {repo_arg} --point {point_arg}"
            report["restore_command"] = (
                f"hetu-data backup restore --repository {repo_arg} --point {point_arg}"
                f' --destination "$HOME/data/hetu/restore-checks/a-share-daily/{point_arg}"'
            )
            _write(staging / "report.json", _body(report))
            _verify(staging, repository, identity)
            _write(staging / "README.md", (
                f"# HETU recovery point {plan['point']}\n\n"
                "Check the report for covered libraries, windows and exclusions.\n\n"
                f"```sh\n{report['verify_command']}\n{report['restore_command']}\n```\n"
                "Use the restored root and a listed fixed library. Market updates are not started by restore.\n"
            ).encode())
            _repository(repository, identity)
            staging.rename(point)
    return report


def _verify(point, repository, identity):
    report = _read(_inside(point, "report.json"))
    manifest = _inside(point, "files.jsonl")
    if report["volume"] != identity or _sha256(manifest) != report["manifest_sha256"]:
        raise SnapshotTamperedError("backup point identity or manifest mismatch")
    paths, packs = {}, defaultdict(dict)
    for row in _inventory(manifest):
        if row["path"] in paths:
            raise SnapshotTamperedError("duplicate restored path")
        paths[row["path"]] = row
        packs[row["pack"]][row["sha256"]] = row["size"]
    if len(paths) != report["file_count"] or set(packs) != set(report["packs"]):
        raise SnapshotTamperedError("backup dependency set mismatch")
    documents = {}
    for library in report["libraries"]:
        row = paths.get(f"libraries/{library}/manifest.json")
        if row is None:
            raise SnapshotTamperedError("library not included in backup point")
        documents.setdefault(row["pack"], {})[row["sha256"]] = library
    device = T7_ROOT.stat().st_dev
    for digest, required in packs.items():
        _repository_check(repository, device)
        pack = _tar_path(repository, digest)
        if _sha256(pack) != digest:
            raise SnapshotTamperedError(f"missing or changed pack: {digest}")
        with tarfile.open(pack) as archive:
            supplied = {}
            for member in archive:
                _tar_path(repository, member.name)
                if not member.isfile() or member.name in supplied:
                    raise SnapshotTamperedError("invalid pack member")
                supplied[member.name] = member.size
                if member.name in required:
                    with archive.extractfile(member) as stream:
                        if hashlib.file_digest(stream, "sha256").hexdigest() != member.name:
                            raise SnapshotTamperedError(f"changed content object: {member.name}")
                if library := documents.get(digest, {}).get(member.name):
                    with archive.extractfile(member) as stream:
                        _verify_library_dependencies(json.load(stream), paths, report["source_root"], library)
        if any(supplied.get(key) != size for key, size in required.items()):
            raise SnapshotTamperedError("pack lacks a required content object")
    return report


def _verify_library_dependencies(doc, paths, source_root, library):
    if doc["library_id"] != library or doc["root"] != source_root:
        raise SnapshotTamperedError("backup library identity does not match its manifest")
    source = Path(source_root)

    def require(path, expected=None):
        row = paths.get(str(path))
        if row is None or (expected is not None and row["sha256"] != expected):
            raise SnapshotTamperedError(f"missing or changed library dependency: {path}")

    for asset in doc["asset_snapshots"]:
        owner = _owner_path(source, Path(asset["root"]))
        require(owner / "snapshots" / asset["snapshot_id"] / "manifest.json", asset.get("manifest_sha256"))
        require(owner / _config_path(asset.get("config_path")))
    for row in doc["table_files"]:
        owner = _owner_path(source, Path(row["root"]))
        require(owner / "snapshots" / row["snapshot_id"] / _relative(row["path"]), row["sha256"])
        if row.get("mode") == "paid":
            require(owner / "data-entitlements.json")
    for selection in doc["revision_selections"]:
        require(_owner_path(source, Path(selection["root"])) / _relative(selection["evidence_ref"]))
    policy = doc["source_policy"]
    if ref := policy.get("basis_ref"):
        owners = (_owner_path(source, Path(row["root"])) for row in doc["sources"]
                  if row["product_id"] == policy["primary_product_id"])
        if not any(paths.get(str(owner / _relative(ref)), {}).get("sha256") == policy["basis_sha256"]
                   for owner in owners):
            raise SnapshotTamperedError(f"missing or changed source policy: {ref}")


def _point(repository, recovery_point):
    if _relative(recovery_point).name != recovery_point:
        raise ValueError("invalid recovery point identifier")
    return _inside(repository, f"points/{recovery_point}")


def verify_packed_backup(repository: Path, recovery_point: str) -> dict:
    repository, identity = _repository(repository)
    point = _point(repository, recovery_point)
    report = _verify(point, repository, identity)
    if report["recovery_point"] != recovery_point:
        raise SnapshotTamperedError("recovery point identifier mismatch")
    return report


def _relocate(destination, staging, report, rows):
    source = Path(report["source_root"])
    for row in rows:
        relative = Path(row["path"])
        if relative.name != "data-entitlements.json":
            continue
        path = _inside(staging, relative)
        document = _read(path)
        changed = False
        for grant in document["grants"]:
            for field in ("license_ref", "read_license_ref"):
                value = grant.get(field)
                if not value:
                    continue
                ref, sep, fragment = value.partition("#")
                old = Path(ref)
                if old.is_relative_to(source) and _inside(staging, old.relative_to(source)).is_file():
                    grant[field] = str(destination / old.relative_to(source)) + sep + fragment
                    changed = True
        if changed:
            _write(path, _body(document))
    for library in report["libraries"]:
        path = _inside(staging, f"libraries/{library}/manifest.json")
        doc = _read(path)
        doc["root"] = str(destination)
        for key in ("asset_snapshots", "table_files", "sources", "revision_selections"):
            for item in doc[key]:
                owner = Path(item["root"])
                if key == "asset_snapshots":
                    item.setdefault("original_root", str(owner))
                    item.pop("revision_selections", None)
                item["root"] = str(destination / _owner_path(source, owner))
                if item.get("config_path"):
                    item["config_path"] = str(Path(item["root"]) / _config_path(item["config_path"]))
                if key == "asset_snapshots":
                    folder = _owner_path(source, owner) / "snapshots" / item["snapshot_id"]
                    _inside(staging, str(folder / "manifest.json")).stat()
        for item in doc["table_files"]:
            base = Path(item["root"]).relative_to(destination) / "snapshots" / item["snapshot_id"]
            _inside(staging, str(base / _relative(item["path"]))).stat()
        _write(path, _body(doc))
    for path in (staging / "archive/coverage").glob("*/*.json"):
        value = _read(path)
        value["library_ref"]["root"] = str(destination)
        for key in ("detail_ref", "delta_evidence_ref"):
            old = value.get(key)
            if old:
                relative = Path(old)
                if relative.is_relative_to(source):
                    relative = relative.relative_to(source)
                if not relative.is_absolute() and _inside(staging, relative).is_file():
                    value[key] = str(destination / relative)
                else:
                    value.setdefault("unavailable_backup_references", {})[key] = value.pop(key)
        _write(path, _body(value))


def restore_packed_backup(repository: Path, recovery_point: str, destination: Path) -> dict:
    repository, identity = _repository(repository)
    report = verify_packed_backup(repository, recovery_point)
    point = _point(repository, recovery_point)
    destination = Path(destination).absolute()
    source = Path(report["source_root"])
    if (destination.exists() or destination.is_symlink() or destination.resolve() != destination
            or destination.is_relative_to(source) or source.is_relative_to(destination)
            or destination.is_relative_to(T7_ROOT)):
        raise ValueError("restore requires a new directory outside the source and T7")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(destination.parent).free < report["logical_bytes"]:
        raise OSError("insufficient restore space")
    staging = destination.with_name(f".{destination.name}.{uuid4().hex}.restore")
    staging.mkdir()
    by_pack = defaultdict(list)
    for row in _inventory(point / "files.jsonl"):
        by_pack[row["pack"]].append(row)
    try:
        device = T7_ROOT.stat().st_dev
        for digest, rows in by_pack.items():
            _repository_check(repository, device)
            with tarfile.open(_tar_path(repository, digest)) as archive:
                members = {m.name: m for m in archive}
                for row in rows:
                    path = _inside(staging, row["path"])
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open("xb") as output, archive.extractfile(members[row["sha256"]]) as content:
                        shutil.copyfileobj(content, output)
                    if path.stat().st_size != row["size"] or _sha256(path) != row["sha256"]:
                        raise SnapshotTamperedError(f"restored bytes differ: {row['path']}")
        _relocate(destination, staging, report, (row for rows in by_pack.values() for row in rows))
        if destination.exists():
            raise FileExistsError(destination)
        staging.rename(destination)
    except BaseException:
        shutil.rmtree(staging)
        raise
    return dict(
        recovery_point=recovery_point, root=str(destination),
        library_refs=[dict(library_id=lib, root=str(destination)) for lib in report["libraries"]],
        file_count=report["file_count"], routine_M_active=False,
    )
