"""Offline tests for packed recovery points using existing synthetic data facts."""

import hashlib
import json
import socket
from datetime import date
from pathlib import Path

import pytest

from hetu_data import DataStore, LibraryRef
from hetu_data.cli import main
from hetu_data.library import publish_library, query_library, query_library_sql
from hetu_data.store import SnapshotTamperedError
from test_library import CODES, END, START, _shard


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("packed backup must work offline")

    monkeypatch.setattr(socket.socket, "connect", no_network)


@pytest.fixture
def repository(tmp_path, monkeypatch):
    from hetu_data import packed_backup

    mount = tmp_path / "T7"
    mount.mkdir()
    target = mount / "data" / "HETU-backups" / "a-share-daily"
    monkeypatch.setattr(packed_backup, "T7_ROOT", mount)
    monkeypatch.setattr(packed_backup, "_volume_identity", lambda _: "synthetic-T7")
    return target


def allow_backup(asset):
    p = Path(asset["root"]) / "data-entitlements.json"
    doc = json.loads(p.read_text())
    doc["grants"][0]["allowed_operations"].append("backup")
    p.write_text(json.dumps(doc))


def make_library(tmp_path, source_facts):
    store = DataStore(tmp_path / "formal")
    asset = _shard(store.root / "owners/history", source_facts, START)
    allow_backup(asset)
    ref = publish_library(store, asset_snapshots=(asset,))
    return store, ref, asset


def backup(store, repository, plan):
    from hetu_data.packed_backup import execute_packed_backup, plan_packed_backup

    return execute_packed_backup(store, plan_packed_backup(store, repository, output=plan))


def restored_bars(repository, point, destination, lib):
    from hetu_data.packed_backup import restore_packed_backup

    result = restore_packed_backup(repository, point, destination)
    assert lib.library_id in [ref["library_id"] for ref in result["library_refs"]]
    return query_library(
        DataStore(destination), LibraryRef(lib.library_id, destination),
        codes=CODES, start=START, end=date(2026, 1, 7),
    )["bars"].close_raw.tolist()


def test_backup_cli_without_source_root(tmp_path, repository, source_facts):
    store, lib, _ = make_library(tmp_path, source_facts)
    out = backup(store, repository, tmp_path / "cli.json")
    store.root.rename(tmp_path / "unavailable")
    args = ["--repository", str(repository), "--point", out["recovery_point"]]
    assert main(["backup", "verify", *args]) == 0
    destination = tmp_path / "restored"
    assert main(["backup", "restore", *args, "--destination", str(destination)]) == 0
    assert not store.root.exists()
    restored = DataStore(destination)
    result = query_library(restored, LibraryRef(lib.library_id, destination),
                           codes=CODES, start=START, end=START, columns=("close_raw",))
    assert result["bars"].close_raw.tolist() == [10]
    assert query_library_sql(restored, result["selection"], "SELECT * FROM daily_bars").close_raw.tolist() == [10]


def test_fixed_library_standard_backup_preserves_read_and_acquisition_limits(tmp_path, repository, source_facts):
    store, lib, asset = make_library(tmp_path, source_facts)
    grant_path = Path(asset["root"]) / "data-entitlements.json"
    config = json.loads(grant_path.read_text())
    config["grants"][0].update(acquire_until="2000-01-01", read_until="2099-01-01", backup_until="2099-01-01")
    grant_path.write_text(json.dumps(config))
    publish_library(store, asset_snapshots=(asset,))
    plan = tmp_path / "fixed.json"
    assert main(["backup", "plan", "--root", str(store.root), "--repository", str(repository),
                 "--library", lib.library_id, "--exclude-raw-originals", "--output", str(plan)]) == 0
    frozen = json.loads(plan.read_text())
    assert frozen["libraries"] == [lib.library_id]
    assert any("/raw/" in row.get("path", "") for row in frozen["excluded"])
    inventory = plan.with_suffix(".json.jsonl").read_text().splitlines()
    assert all("/raw/" not in json.loads(line)["path"] for line in inventory)
    from hetu_data.packed_backup import execute_packed_backup

    point = execute_packed_backup(store, plan)["recovery_point"]
    dest = tmp_path / "restored"
    assert restored_bars(repository, point, dest, lib) == [10]
    restored = json.loads((dest / grant_path.relative_to(store.root)).read_text())["grants"][0]
    assert restored["acquire_until"] == "2000-01-01" and restored["read_until"] == "2099-01-01"
    assert "backup" not in restored["allowed_operations"] and "backup_until" not in restored


def test_same_backup_content_ignores_mac_volume_attributes(tmp_path, repository, source_facts):
    store, _, _ = make_library(tmp_path, source_facts)
    first = backup(store, repository, tmp_path / "one.json")
    pack = repository / "packs" / (first["packs"][0] + ".tar")
    pack.with_name("._" + pack.name).write_bytes(b"synthetic macOS AppleDouble sidecar")
    second = backup(store, repository, tmp_path / "two.json")
    assert second["new_content_bytes"] == 0
    assert second["packs"] == first["packs"]


def test_incremental_backup_catches_up_and_preserves_revisions(tmp_path, repository, source_facts):
    store, first, old = make_library(tmp_path, source_facts)
    p1 = backup(store, repository, tmp_path / "one.json")
    journal = store.root / "updates/approved-usage/test/usage.jsonl"
    journal.parent.mkdir(parents=True)
    scope = store.root / ".hetu/quant/acceptance/phase3/approved.json"
    scope.parent.mkdir(parents=True)
    scope.write_text('{"purpose":"synthetic"}')
    approval_ref = f"{scope.relative_to(store.root)}#personal"
    journal.write_text(json.dumps({"type": "approval_limits", "limits": {"approved_scope_ref": approval_ref}})
                       + '\n{"usage":{"provider_fetch":42}}\n')
    revision = _shard(Path(old["root"]), source_facts, START, price=17, refresh=True)
    r = Path(revision["root"]) / "revision.json"
    revision["revision_selections"] = [dict(
        root=revision["root"], product_id="ts_a_daily", table="daily_bars",
        superseded_snapshot_id=old["snapshot_id"], selected_snapshot_id=revision["snapshot_id"],
        codes=list(CODES), start=START.isoformat(), end=START.isoformat(), evidence_ref=r.name,
    )]
    r.write_text(json.dumps({k: v for k, v in revision["revision_selections"][0].items() if k != "evidence_ref"}))
    extras = [_shard(store.root / f"owners/{day.day}", source_facts, day, price=11)
              for day in (END, date(2026, 1, 7))]
    for asset in (revision, *extras):
        allow_backup(asset)
    second = publish_library(store, asset_snapshots=(revision, *extras), previous=first)
    from hetu_data.library import load_library

    assert load_library(store, second)["revision_selections"]
    coverage = store.root / "archive/coverage" / second.library_id / "fixture.json"
    coverage.parent.mkdir(parents=True)
    coverage.write_text(json.dumps({"library_ref": {"library_id": second.library_id, "root": str(store.root)}}))
    p2 = backup(store, repository, tmp_path / "two.json")
    p3 = backup(store, repository, tmp_path / "three.json")
    assert 0 < p2["new_content_bytes"] < p2["logical_bytes"]
    assert p3["new_content_bytes"] == 0
    assert restored_bars(repository, p1["recovery_point"], tmp_path / "old", first) == [10]
    assert restored_bars(repository, p2["recovery_point"], tmp_path / "new", second) == [17, 11, 11]
    copied_journal = tmp_path / "new" / journal.relative_to(store.root)
    assert copied_journal.read_bytes() == journal.read_bytes()
    recovered_ref = json.loads(copied_journal.read_bytes().splitlines()[0])["limits"]["approved_scope_ref"]
    assert recovered_ref == approval_ref
    assert (tmp_path / "new" / recovered_ref.partition("#")[0]).read_bytes() == scope.read_bytes()
    restored = json.loads((tmp_path / "new" / coverage.relative_to(store.root)).read_text())
    assert restored["library_ref"]["root"] == str(tmp_path / "new")


def test_backup_mount_identity_and_interrupt(tmp_path, repository, source_facts, monkeypatch):
    from hetu_data import packed_backup as module

    store, _, _ = make_library(tmp_path, source_facts)
    path = module.plan_packed_backup(store, repository, output=tmp_path / "plan.json")
    with monkeypatch.context() as patch:
        patch.setattr(module, "_volume_identity", lambda _: "different")
        with pytest.raises(OSError, match="identity"):
            module.execute_packed_backup(store, path)
        assert not repository.exists()
    first = module.execute_packed_backup(store, path)
    assert module.verify_packed_backup(repository, first["recovery_point"])["file_count"] > 0
    extra = _shard(store.root / "owners/new", source_facts, END, price=11)
    allow_backup(extra)
    publish_library(store, asset_snapshots=(extra,))
    next_plan = module.plan_packed_backup(store, repository, output=tmp_path / "next.json")
    original = module._write_pack
    monkeypatch.setattr(module, "PACK_BYTES", 10240 + 512)
    sent = []

    def interrupt(repo, identity, group):
        if sent:
            raise OSError("interrupted")
        digest = original(repo, identity, group)
        sent.append(digest)
        return digest

    with monkeypatch.context() as patch:
        patch.setattr(module, "_write_pack", interrupt)
        with pytest.raises(OSError, match="interrupted"):
            module.execute_packed_backup(store, next_plan)
    assert len(list((repository / "points").iterdir())) == 1
    assert module.verify_packed_backup(repository, first["recovery_point"])
    prior_pack = repository / "packs" / f"{sent[0]}.tar"
    unchanged = prior_pack.stat().st_mtime_ns
    module.execute_packed_backup(store, next_plan)
    assert prior_pack.stat().st_mtime_ns == unchanged


def test_backup_scope_and_consistent_metadata(tmp_path, repository, source_facts):
    from hetu_data import packed_backup as module

    store, lib, asset = make_library(tmp_path, source_facts)
    (store.root / "token.txt").write_text("SYNTHETIC_DO_NOT_COPY")
    (store.root / "last_success.json").write_text('{"value": "frozen"}')
    plan = module.plan_packed_backup(store, repository, output=tmp_path / "plan.json")
    (store.root / "last_success.json").write_text('{"value": "newer"}')
    out = module.execute_packed_backup(store, plan)
    dest = tmp_path / "restored"
    assert restored_bars(repository, out["recovery_point"], dest, lib) == [10]
    assert not (dest / "token.txt").exists()
    assert json.loads((dest / "last_success.json").read_text()) == {"value": "frozen"}
    p = Path(asset["root"]) / "data-entitlements.json"
    doc = json.loads(p.read_text())
    doc["grants"][0]["allowed_operations"].remove("backup")
    p.write_text(json.dumps(doc))
    refused = module.plan_packed_backup(store, repository, output=tmp_path / "refused.json")
    assert json.loads(refused.read_text())["excluded"]
    with pytest.raises(PermissionError, match="eligible"):
        module.execute_packed_backup(store, refused)


def test_backup_restores_registered_ledgers_and_license_original(tmp_path, repository, source_facts):
    store, lib, asset = make_library(tmp_path, source_facts)
    owner = Path(asset["root"])
    entitlement = owner / "data-entitlements.json"
    grant = json.loads(entitlement.read_text())
    license_path = owner / "license.md"
    license_path.write_bytes(b"# Synthetic license\r\nPersonal offline backup is allowed.\r\n")
    secret = owner / "excluded.txt"
    secret.write_text("TOKEN=SYNTHETIC_DO_NOT_COPY\n")
    grant["grants"][0].update(license_ref=str(license_path), read_license_ref="missing.md",
                              approved_scope_ref=str(secret))
    entitlement.write_text(json.dumps(grant))

    migration = store.root / "migration/formal"
    ledger = migration / "historical-ledgers/old/usage.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(b'{"used":42}\n')
    index = migration / "historical-ledger-index.json"
    index.write_text(json.dumps([
        dict(source="/old/root/usage.jsonl", destination=str(ledger.relative_to(migration)),
             sha256=hashlib.sha256(ledger.read_bytes()).hexdigest()),
        dict(source="/old/root/missing.jsonl", destination="historical-ledgers/missing.jsonl", sha256="f" * 64),
    ]))
    active = store.root / "updates/approved-usage/batch/usage.jsonl"
    active.parent.mkdir(parents=True)
    old = b'{"used":1}\n'
    active.write_bytes(old + b'{"used":2}\n')
    active_index = migration / "ledger-index.json"
    active_index.write_text(json.dumps([
        dict(location=str(active.relative_to(store.root)), sha256=hashlib.sha256(old).hexdigest()),
    ]))
    out = backup(store, repository, tmp_path / "ledger.json")
    saved = {p.relative_to(store.root): p.read_bytes() for p in (index, ledger, license_path, active, active_index)}
    store.root.rename(tmp_path / "source-offline")
    destination = tmp_path / "restored-ledger"
    assert restored_bars(repository, out["recovery_point"], destination, lib) == [10]
    for rel, original in saved.items():
        assert (destination / rel).read_bytes() == original
    assert {item["path"] for item in out["excluded"]} >= {
        "historical-ledgers/missing.jsonl", "missing.md", str(secret),
    }
    assert not (destination / secret.relative_to(store.root)).exists()
    restored_grant = json.loads((destination / entitlement.relative_to(store.root)).read_text())["grants"][0]
    assert restored_grant["license_ref"] == str(destination / license_path.relative_to(store.root))


@pytest.mark.parametrize(
    "change", ["unrelated", "copied_basis", "changed_basis", "changed_fragment", "revoked", "expired", "narrowed"],
)
def test_resume_revalidates_only_used_backup_permissions(tmp_path, repository, source_facts, monkeypatch, change):
    from hetu_data import packed_backup as module

    store, lib, asset = make_library(tmp_path, source_facts)
    entitlements = Path(asset["root"]) / "data-entitlements.json"
    basis = entitlements.with_name("license.md")
    basis.write_bytes(b"# Synthetic personal backup license\n")
    doc = json.loads(entitlements.read_text())
    doc["grants"][0].update(license_ref="license.md#personal", read_license_ref="license.md#personal")
    entitlements.write_text(json.dumps(doc))
    plan = module.plan_packed_backup(store, repository, output=tmp_path / "resume.json")
    with monkeypatch.context() as patch:
        def interrupted(*_):
            raise OSError("interrupted before pack")

        patch.setattr(module, "_write_pack", interrupted)
        with pytest.raises(OSError, match="interrupted"):
            module.execute_packed_backup(store, plan)
    doc = json.loads(entitlements.read_text())
    grant = doc["grants"][0]
    if change == "unrelated":
        doc["grants"].append(dict(grant, product_id="ts_suspend_events", acquire_until="2000-01-01"))
    elif change in {"copied_basis", "changed_basis"}:
        copy = entitlements.with_name("license-copy.md")
        copy.write_bytes(basis.read_bytes() if change == "copied_basis" else b"Different license terms\n")
        grant.update(license_ref=f"{copy}#personal", read_license_ref=f"{copy}#personal")
    elif change == "changed_fragment":
        grant["license_ref"] = "license.md#another-use"
    elif change == "revoked":
        grant["allowed_operations"].remove("backup")
    elif change == "expired":
        grant["acquire_until"] = "2000-01-01"
    else:
        grant["end"] = "2000-01-02"
    entitlements.write_text(json.dumps(doc))
    if change not in {"unrelated", "copied_basis"}:
        with pytest.raises(PermissionError):
            module.execute_packed_backup(store, plan)
        assert not list((repository / "points").iterdir())
        return
    out = module.execute_packed_backup(store, plan)
    assert restored_bars(repository, out["recovery_point"], tmp_path / "resumed", lib) == [10]


def test_packed_backup_excludes_out_of_scope_raw_without_dropping_daily_rows(
    tmp_path, repository, source_facts,
):
    from hetu_data import packed_backup as module

    store, lib, asset = make_library(tmp_path, source_facts)
    owner = DataStore(Path(asset["root"]))
    original = owner.load_manifest(module.SnapshotRef(asset["snapshot_id"], owner.root))
    raw = next(row for row in original["items"][0]["raw_files"] if "params" in row)
    # Expand only the observed request, then lock it in a new synthetic library.
    raw["params"]["start_date"] = "19990101"
    raw["query_observation"] = "success_empty"
    manifest = owner.root / "snapshots" / asset["snapshot_id"] / "manifest.json"
    manifest.write_text(json.dumps(original))
    for file in (store.root / "libraries" / lib.library_id).iterdir():
        file.unlink()
    (store.root / "libraries" / lib.library_id).rmdir()
    ref = publish_library(store, asset_snapshots=({**asset, "raw_products": ["ts_a_daily"]},))
    path = module.plan_packed_backup(store, repository, output=tmp_path / "raw.json")
    doc = json.loads(path.read_text())
    assert any("raw backup query outside granted period" in item["reason"] for item in doc["excluded"])
    point = module.execute_packed_backup(store, path)["recovery_point"]
    destination = tmp_path / "raw-restored"
    assert restored_bars(repository, point, destination, ref) == [10]
    assert not any((destination / "owners").glob("*/snapshots/*/raw/**/*.*"))
    from hetu_data.archive import _scan_library_coverage

    coverage = _scan_library_coverage(DataStore(destination), LibraryRef(ref.library_id, destination))
    assert coverage.queries == []
    assert any(row["reason"] == "raw original outside backup scope" for row in coverage.failures)


def test_run_rejects_caller_modified_frozen_plan(tmp_path, repository, source_facts):
    from hetu_data import packed_backup as module

    store, _, _ = make_library(tmp_path, source_facts)
    plan = module.plan_packed_backup(store, repository, output=tmp_path / "plan.json")
    values = json.loads(plan.read_text())
    values["permissions"] = {}
    plan.write_text(json.dumps(values))
    with pytest.raises(PermissionError, match="frozen source registration"):
        module.execute_packed_backup(store, plan)
    assert not repository.exists()


@pytest.mark.parametrize("damage", ["missing", "tampered", "unsafe_path", "unregistered_table"])
def test_restore_requires_complete_verified_dependencies(tmp_path, repository, source_facts, damage):
    from hetu_data import packed_backup as module

    store, _, _ = make_library(tmp_path, source_facts)
    out = backup(store, repository, tmp_path / "plan.json")
    p = next((repository / "packs").glob("*.tar"))
    if damage == "missing":
        p.unlink()
    elif damage == "tampered":
        with p.open("ab") as stream:
            stream.write(b"tampered")
    else:
        point = repository / "points" / out["recovery_point"]
        index = point / "files.jsonl"
        rows = [json.loads(line) for line in index.read_text().splitlines()]
        if damage == "unsafe_path":
            rows[0]["path"] = "../escape"
        else:
            removed = next(row for row in rows if row["path"].endswith(".parquet"))
            rows.remove(removed)
        index.write_text("".join(json.dumps(row) + "\n" for row in rows))
        report = point / "report.json"
        values = json.loads(report.read_text())
        values["manifest_sha256"] = module._sha256(index)
        if damage == "unregistered_table":
            values["file_count"] -= 1
            values["packs"] = sorted({row["pack"] for row in rows})
        report.write_text(json.dumps(values))
    with pytest.raises((OSError, ValueError, SnapshotTamperedError)):
        module.restore_packed_backup(repository, out["recovery_point"], tmp_path / "restore")
    assert not (tmp_path / "restore").exists()


def test_paid_partition_outside_grant_does_not_block_other_libraries(tmp_path, repository, source_facts):
    store, first, asset = make_library(tmp_path, source_facts)
    denied = _shard(store.root / "owners/denied", source_facts, END)
    allow_backup(denied)
    second = publish_library(store, asset_snapshots=(denied,))
    contract = Path(denied["root"]) / "data-entitlements.json"
    value = json.loads(contract.read_text())
    value["grants"][0]["end"] = START.isoformat()
    contract.write_text(json.dumps(value))
    out = backup(store, repository, tmp_path / "partial.json")
    assert out["libraries"] == [first.library_id]
    assert any(item["library_id"] == second.library_id and "period grant" in item["reason"]
               for item in out["excluded"])
    assert restored_bars(repository, out["recovery_point"], tmp_path / "partial", first) == [10]


@pytest.mark.parametrize("custom_config", [False, True])
def test_shared_owner_product_configuration_survives_restore(tmp_path, repository, source_facts, custom_config):
    store, first, asset = make_library(tmp_path, source_facts)
    owner = DataStore(Path(asset["root"]))
    path = owner.root / "data-entitlements.json"
    value = json.loads(path.read_text())
    product = "ts_suspend_events"
    value["grants"].append(dict(value["grants"][0], product_id=product, allowed_operations=[
        "read", "import", "backup",
    ]))
    path.write_text(json.dumps(value))
    raw = owner.root / "events.csv"
    raw.write_text(f"code,date,event_type,suspend_timing\n{CODES[0]},{END},S,\n")
    request = owner.root / "import.json"
    request.write_text(json.dumps(dict(mode="paid", items=[dict(
        product_id=product, table="security_events", files=[raw.name],
        source=dict(name="synthetic", fee_class="paid", obtained_at="2026-01-07T00:00:00+08:00",
                    permission_basis="synthetic"),
    )])))
    event = owner.import_files(request, (raw,))
    event_asset = dict(root=str(owner.root), snapshot_id=event.snapshot_id, tables=["security_events"])
    if custom_config:
        config = owner.root / "alternate.json"
        config.write_text(json.dumps({"products": {product: owner.config["products"][product]}}))
        event_asset["config_path"] = str(config)
    second = publish_library(store, asset_snapshots=[event_asset])
    out = backup(store, repository, tmp_path / "shared.json")
    assert set(out["libraries"]) == {first.library_id, second.library_id}
    destination = tmp_path / "shared"
    assert restored_bars(repository, out["recovery_point"], destination, first) == [10]
    query = query_library(DataStore(destination), LibraryRef(second.library_id, destination),
                          codes=CODES, start=END, end=END)
    assert "suspended" in query["gaps"].reason.tolist()
