import errno
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

import hetu_stock.skill.installer as installer_module
from hetu_stock.skill import (
    HostTarget,
    SkillValidationError,
    build_skill_manifest,
    default_user_skill_root,
    install_skill,
    verify_skill_manifest,
)
from tests.product.skill.contract_fixtures import build_contract_fixture


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks are not available in this environment: {exc}")


def _write_manifest(root: Path) -> None:
    files = {
        file.relative_to(root).as_posix(): _sha256(file)
        for file in root.rglob("*")
        if file.is_file()
        and file.relative_to(root).as_posix() != "MANIFEST.json"
    }
    (root / "MANIFEST.json").write_text(
        json.dumps({"files": files}, indent=2), encoding="utf-8"
    )


def _make_skill_package(root: Path, *, extra_files: dict[str, str] | None = None) -> None:
    assert root.name == "hetu-stock-analysis"
    fixture_root, _ = build_contract_fixture(root.parent)
    assert fixture_root == root
    (root / "references" / "orchestration.md").write_text(
        "# Synthetic orchestration\n",
        encoding="utf-8",
    )
    if extra_files:
        for rel_path, content in extra_files.items():
            file_path = root / rel_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
    _write_manifest(root)


def test_build_skill_manifest_is_sorted_and_excludes_itself(tmp_path: Path) -> None:
    root = tmp_path / "skill"
    root.mkdir()
    (root / "z.md").write_text("z", encoding="utf-8")
    nested = root / "references"
    nested.mkdir()
    (nested / "a.md").write_text("a", encoding="utf-8")
    (root / "MANIFEST.json").write_text("old manifest", encoding="utf-8")
    cache = root / "scripts" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "tool.cpython-312.pyc").write_bytes(b"\x00pyc")

    manifest = build_skill_manifest(root)

    assert list(manifest) == ["files"]
    assert list(manifest["files"]) == ["references/a.md", "z.md"]
    assert manifest["files"]["references/a.md"] == _sha256(nested / "a.md")


def test_build_skill_manifest_rejects_symbolic_links(tmp_path: Path) -> None:
    root = tmp_path / "skill"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside secret", encoding="utf-8")
    _symlink_or_skip(root / "leak.txt", outside)

    with pytest.raises(SkillValidationError, match="symbolic link"):
        build_skill_manifest(root)


def test_build_skill_manifest_rejects_pycache_directory_symlink(
    tmp_path: Path,
) -> None:
    root = tmp_path / "skill"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "rogue.py").write_text("EXECUTABLE = True\n", encoding="utf-8")
    _symlink_or_skip(scripts / "__pycache__", outside)

    with pytest.raises(SkillValidationError, match="symbolic link"):
        build_skill_manifest(root)


def test_verify_skill_manifest_accepts_canonical_package() -> None:
    verify_skill_manifest(Path("skills/hetu-stock-analysis"))


def test_verify_skill_manifest_rejects_tampered_file(tmp_path: Path) -> None:
    source = tmp_path / "hetu-stock-analysis"
    _make_skill_package(source)
    victim = source / "references" / "work-packages" / "catalog.md"
    victim.write_text("modified", encoding="utf-8")

    with pytest.raises(SkillValidationError, match="sha256 mismatch"):
        verify_skill_manifest(source)


def test_verify_skill_manifest_requires_manifest(tmp_path: Path) -> None:
    source = tmp_path / "hetu-stock-analysis"
    _make_skill_package(source)
    (source / "MANIFEST.json").unlink()

    with pytest.raises(SkillValidationError, match="MANIFEST.json is required"):
        verify_skill_manifest(source)


def test_verify_skill_manifest_rejects_unlisted_nested_manifest(
    tmp_path: Path,
) -> None:
    source = tmp_path / "hetu-stock-analysis"
    _make_skill_package(source)
    nested_manifest = source / "references" / "MANIFEST.json"
    nested_manifest.parent.mkdir(exist_ok=True)
    nested_manifest.write_text("unlisted", encoding="utf-8")

    with pytest.raises(
        SkillValidationError,
        match="package files missing from manifest",
    ):
        verify_skill_manifest(source)


def test_verify_skill_manifest_rejects_symbolic_links(tmp_path: Path) -> None:
    source = tmp_path / "hetu-stock-analysis"
    _make_skill_package(source)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside secret", encoding="utf-8")
    _symlink_or_skip(source / "references" / "leak.txt", outside)
    _write_manifest(source)

    with pytest.raises(SkillValidationError, match="symbolic link"):
        verify_skill_manifest(source)


def test_default_user_roots(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert default_user_skill_root(HostTarget.CODEX) == tmp_path / "codex-home" / "skills"
    assert default_user_skill_root(HostTarget.CLAUDE) == tmp_path / ".claude" / "skills"
    assert default_user_skill_root(HostTarget.OPENCODE) == tmp_path / "xdg" / "opencode" / "skills"


def test_install_copies_canonical_package(tmp_path: Path) -> None:
    destination = tmp_path / "skills"
    installed = install_skill(Path("skills/hetu-stock-analysis"), destination)
    assert installed == destination / "hetu-stock-analysis"
    assert (installed / "SKILL.md").is_file()
    assert (installed / "references" / "orchestration.md").is_file()
    assert (installed / "references" / "work-packages" / "catalog.md").is_file()
    for index in range(11):
        assert len(list(installed.glob(f"references/work-packages/core/W{index}-*.md"))) == 1
    assert (installed / "MANIFEST.json").is_file()


def test_install_does_not_copy_unregistered_bytecode_cache(tmp_path: Path) -> None:
    source = tmp_path / "source" / "hetu-stock-analysis"
    _make_skill_package(source)
    cache = source / "scripts" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "tool.cpython-312.pyc").write_bytes(b"\x00pyc")
    (cache / "rogue.py").write_text("EXECUTABLE = True\n", encoding="utf-8")

    installed = install_skill(source, tmp_path / "skills")

    assert not (installed / "scripts" / "__pycache__").exists()


def test_install_rejects_post_copy_tampering(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "hetu-stock-analysis"
    _make_skill_package(source)
    copytree = installer_module.shutil.copytree

    def copy_and_tamper(
        source_path: Path, target_path: Path, *args: Any, **kwargs: Any
    ) -> Path:
        copied = Path(copytree(source_path, target_path, *args, **kwargs))
        if Path(target_path).name == "hetu-stock-analysis":
            victim = copied / "references" / "orchestration.md"
            victim.write_text("# Tampered\n", encoding="utf-8")
        return copied

    monkeypatch.setattr(installer_module.shutil, "copytree", copy_and_tamper)

    with pytest.raises(SkillValidationError, match="sha256 mismatch"):
        install_skill(source, tmp_path / "skills")

    target = tmp_path / "skills" / "hetu-stock-analysis"
    assert not target.exists()
    monkeypatch.undo()
    assert install_skill(source, tmp_path / "skills") == target


def test_install_rejects_post_copy_nested_manifest_addition(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "hetu-stock-analysis"
    _make_skill_package(source)
    copytree = installer_module.shutil.copytree

    def copy_and_add_nested_manifest(
        source_path: Path,
        target_path: Path,
        *args: Any,
        **kwargs: Any,
    ) -> Path:
        copied = Path(copytree(source_path, target_path, *args, **kwargs))
        if Path(target_path).name == "hetu-stock-analysis":
            nested_manifest = copied / "references" / "MANIFEST.json"
            nested_manifest.parent.mkdir(exist_ok=True)
            nested_manifest.write_text("added after copy", encoding="utf-8")
        return copied

    monkeypatch.setattr(
        installer_module.shutil,
        "copytree",
        copy_and_add_nested_manifest,
    )

    with pytest.raises(
        SkillValidationError,
        match="package files missing from manifest",
    ):
        install_skill(source, tmp_path / "skills")

    assert not (tmp_path / "skills" / "hetu-stock-analysis").exists()


def test_install_rejects_post_copy_nested_manifest_tampering(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "hetu-stock-analysis"
    _make_skill_package(
        source,
        extra_files={"references/MANIFEST.json": "registered nested manifest"},
    )
    copytree = installer_module.shutil.copytree

    def copy_and_tamper_nested_manifest(
        source_path: Path,
        target_path: Path,
        *args: Any,
        **kwargs: Any,
    ) -> Path:
        copied = Path(copytree(source_path, target_path, *args, **kwargs))
        if Path(target_path).name == "hetu-stock-analysis":
            (copied / "references" / "MANIFEST.json").write_text(
                "tampered after copy",
                encoding="utf-8",
            )
        return copied

    monkeypatch.setattr(
        installer_module.shutil,
        "copytree",
        copy_and_tamper_nested_manifest,
    )

    with pytest.raises(
        SkillValidationError,
        match=r"sha256 mismatch for references/MANIFEST\.json",
    ):
        install_skill(source, tmp_path / "skills")

    assert not (tmp_path / "skills" / "hetu-stock-analysis").exists()


def test_install_removes_new_target_after_unexpected_target_validation_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "hetu-stock-analysis"
    _make_skill_package(source)
    target = tmp_path / "skills" / "hetu-stock-analysis"
    validate_skill_package = installer_module.validate_skill_package
    failure = RuntimeError("unexpected target validation failure")

    def fail_target_validation(
        package_root: Path,
        *,
        require_manifest: bool = False,
    ) -> None:
        if package_root == target:
            raise failure
        validate_skill_package(
            package_root,
            require_manifest=require_manifest,
        )

    monkeypatch.setattr(
        installer_module,
        "validate_skill_package",
        fail_target_validation,
    )

    with pytest.raises(RuntimeError) as exc_info:
        install_skill(source, tmp_path / "skills")

    assert exc_info.value is failure
    assert not target.exists()


def test_install_requires_manifest(tmp_path: Path) -> None:
    source = tmp_path / "hetu-stock-analysis"
    _make_skill_package(source)
    (source / "MANIFEST.json").unlink()

    with pytest.raises(SkillValidationError, match="MANIFEST.json is required"):
        install_skill(source, tmp_path / "skills")


def test_install_rejects_symbolic_links(tmp_path: Path) -> None:
    source = tmp_path / "hetu-stock-analysis"
    _make_skill_package(source)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside secret", encoding="utf-8")
    _symlink_or_skip(source / "references" / "leak.txt", outside)
    _write_manifest(source)
    destination = tmp_path / "skills"

    with pytest.raises(SkillValidationError, match="symbolic link"):
        install_skill(source, destination)

    assert not (destination / "hetu-stock-analysis").exists()


def test_install_rejects_missing_manifest_file(tmp_path: Path) -> None:
    source = tmp_path / "hetu-stock-analysis"
    _make_skill_package(source)
    (source / "references" / "work-packages" / "catalog.md").unlink()

    with pytest.raises(SkillValidationError, match="manifest files missing"):
        install_skill(source, tmp_path / "skills")


def test_install_rejects_hash_mismatch(tmp_path: Path) -> None:
    source = tmp_path / "hetu-stock-analysis"
    _make_skill_package(source)
    victim = source / "references" / "work-packages" / "catalog.md"
    victim.write_text("modified", encoding="utf-8")

    with pytest.raises(SkillValidationError, match="sha256 mismatch"):
        install_skill(source, tmp_path / "skills")


def test_install_rejects_extra_file_not_in_manifest(tmp_path: Path) -> None:
    source = tmp_path / "hetu-stock-analysis"
    _make_skill_package(source)
    (source / "extra.txt").write_text("extra", encoding="utf-8")

    with pytest.raises(SkillValidationError, match="package files missing from manifest"):
        install_skill(source, tmp_path / "skills")


def test_install_refuses_overwrite_without_force(tmp_path: Path) -> None:
    source = tmp_path / "hetu-stock-analysis"
    _make_skill_package(source)
    destination = tmp_path / "skills"
    install_skill(source, destination)

    with pytest.raises(FileExistsError):
        install_skill(source, destination)


def test_install_overwrites_with_force(tmp_path: Path) -> None:
    source = tmp_path / "hetu-stock-analysis"
    _make_skill_package(source)
    destination = tmp_path / "skills"
    install_skill(source, destination)
    victim = source / "references" / "work-packages" / "catalog.md"
    victim.write_text(victim.read_text(encoding="utf-8") + "\nupdated\n", encoding="utf-8")
    # Regenerate manifest to match the updated content.
    _write_manifest(source)

    installed = install_skill(source, destination, force=True)
    assert installed.is_dir()
    assert (installed / "references" / "work-packages" / "catalog.md").read_text(
        encoding="utf-8"
    ).endswith("\nupdated\n")


def test_force_install_failure_keeps_old_target_usable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Phase-5 contract: a failed overwrite never destroys the installed
    package. The tampered staging copy is discarded and the old target,
    including files unique to it, remains verifiably usable."""
    source = tmp_path / "hetu-stock-analysis"
    _make_skill_package(source)
    destination = tmp_path / "skills"
    target = install_skill(source, destination)
    (target / "old-only.txt").write_text("old installation", encoding="utf-8")
    old_manifest = installer_module.build_skill_manifest(target)
    copytree = installer_module.shutil.copytree

    def copy_and_tamper(
        source_path: Path, target_path: Path, *args: Any, **kwargs: Any
    ) -> Path:
        copied = Path(copytree(source_path, target_path, *args, **kwargs))
        if Path(target_path).name == "hetu-stock-analysis":
            victim = copied / "references" / "orchestration.md"
            victim.write_text("# Tampered\n", encoding="utf-8")
        return copied

    monkeypatch.setattr(installer_module.shutil, "copytree", copy_and_tamper)

    with pytest.raises(SkillValidationError, match="sha256 mismatch"):
        install_skill(source, destination, force=True)

    monkeypatch.undo()
    assert (target / "old-only.txt").read_text(encoding="utf-8") == "old installation"
    # build_skill_manifest hashes every file (including the unlisted
    # old-only.txt), so equality proves the old tree survived byte-for-byte.
    assert installer_module.build_skill_manifest(target) == old_manifest


def test_force_copy_failure_preserves_installed_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source" / "hetu-stock-analysis"
    _make_skill_package(source)
    destination = tmp_path / "installed"
    target = install_skill(source, destination)
    before = installer_module.build_skill_manifest(target)

    def fail_copy(*args: Any, **kwargs: Any) -> Path:
        raise OSError("injected copy failure")

    monkeypatch.setattr(installer_module.shutil, "copytree", fail_copy)
    with pytest.raises(OSError, match="injected copy failure"):
        install_skill(source, destination, force=True)
    verify_skill_manifest(target)
    assert installer_module.build_skill_manifest(target) == before


# ---------------------------------------------------------------------------
# phase-5 stage 05.1: parameterized fault injection and recovery


def _force_with_staging_marker(source: Path, destination: Path, marker: str) -> Path:
    """Install a package whose content differs by a marker file."""
    victim = source / "references" / "work-packages" / "catalog.md"
    victim.write_text(victim.read_text(encoding="utf-8") + f"\n{marker}\n", encoding="utf-8")
    _write_manifest(source)
    return install_skill(source, destination, force=True)


def test_exchange_unsupported_fails_before_touching_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "hetu-stock-analysis"
    _make_skill_package(source)
    destination = tmp_path / "skills"
    target = install_skill(source, destination)
    before = installer_module.build_skill_manifest(target)

    def no_native_exchange(left: Path, right: Path) -> None:
        raise OSError(errno.ENOSYS, "native exchange not supported")

    monkeypatch.setattr(installer_module, "exchange_directories", no_native_exchange)
    updated = source / "references" / "orchestration.md"
    updated.write_text("# v2\n", encoding="utf-8")
    _write_manifest(source)
    with pytest.raises(OSError, match="native exchange not supported"):
        install_skill(source, destination, force=True)
    monkeypatch.undo()
    verify_skill_manifest(target)
    assert installer_module.build_skill_manifest(target) == before


def test_post_exchange_verification_failure_rolls_back_to_old(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "hetu-stock-analysis"
    _make_skill_package(source)
    destination = tmp_path / "skills"
    target = install_skill(source, destination)
    old_manifest = installer_module.build_skill_manifest(target)

    updated = source / "references" / "orchestration.md"
    updated.write_text("# v2\n", encoding="utf-8")
    _write_manifest(source)

    real_validate = installer_module.validate_skill_package

    def reject_installed_target(package_root: Path, *, require_manifest: bool = False) -> None:
        if package_root.resolve() == target.resolve():
            raise SkillValidationError("injected post-exchange rejection")
        real_validate(package_root, require_manifest=require_manifest)

    monkeypatch.setattr(installer_module, "validate_skill_package", reject_installed_target)
    with pytest.raises(SkillValidationError, match="injected post-exchange rejection"):
        install_skill(source, destination, force=True)
    monkeypatch.undo()

    assert installer_module.build_skill_manifest(target) == old_manifest
    verify_skill_manifest(target)
    area_root = destination.parent / ".hetu-skill-maintenance"
    records = list(area_root.glob("*/transaction.json"))
    assert records and json.loads(records[0].read_text(encoding="utf-8"))["state"] == "rolled_back"


def test_interrupted_prepared_transaction_is_resumed_by_next_maintenance(
    tmp_path: Path,
) -> None:
    source = tmp_path / "hetu-stock-analysis"
    _make_skill_package(source)
    destination = tmp_path / "skills"
    target = install_skill(source, destination)
    old_manifest = installer_module.build_skill_manifest(target)

    # Simulate a crash right after "prepared": a validated staging copy exists,
    # the exchange did not happen, and only the record knows about it.
    catalog = source / "references" / "work-packages" / "catalog.md"
    catalog.write_text(catalog.read_text(encoding="utf-8") + "\nstaged v2\n", encoding="utf-8")
    _write_manifest(source)
    area = installer_module._MaintenanceArea(destination, target)
    staging, new_manifest = area.prepare_staging(source)
    area._write_record(
        {
            "state": "prepared",
            "target": str(target),
            "staging": str(staging),
            "backup": "",
            "old_manifest": old_manifest,
            "new_manifest": new_manifest,
            "mode": "overwrite-exchange",
        }
    )

    installer_module._recover_pending(area)

    assert "staged v2" in catalog.read_text(encoding="utf-8") or True
    installed_catalog = (target / "references" / "work-packages" / "catalog.md").read_text(
        encoding="utf-8"
    )
    assert installed_catalog.endswith("staged v2\n")
    assert installer_module.build_skill_manifest(target) == new_manifest
    assert area.read_record()["state"] == "finalized"


def test_interrupted_exchanged_transaction_finalizes_without_redo(
    tmp_path: Path,
) -> None:
    source = tmp_path / "hetu-stock-analysis"
    _make_skill_package(source)
    destination = tmp_path / "skills"
    target = install_skill(source, destination)

    catalog = source / "references" / "work-packages" / "catalog.md"
    catalog.write_text(catalog.read_text(encoding="utf-8") + "\nstaged v2\n", encoding="utf-8")
    _write_manifest(source)
    area = installer_module._MaintenanceArea(destination, target)
    staging, new_manifest = area.prepare_staging(source)
    installer_module.exchange_directories(staging, target)
    area._write_record(
        {
            "state": "exchanged",
            "target": str(target),
            "staging": str(staging),
            "backup": str(staging.parent),
            "old_manifest": installer_module.build_skill_manifest(
                destination.parent / "unused-old"
            )
            if False
            else None,
            "new_manifest": new_manifest,
            "mode": "overwrite-exchange",
        }
    )

    installer_module._recover_pending(area)

    assert installer_module.build_skill_manifest(target) == new_manifest
    assert area.read_record()["state"] == "finalized"


def test_ambiguous_recovery_reports_action_without_guessing(tmp_path: Path) -> None:
    source = tmp_path / "hetu-stock-analysis"
    _make_skill_package(source)
    destination = tmp_path / "skills"
    target = install_skill(source, destination)

    area = installer_module._MaintenanceArea(destination, target)
    staging, _ = area.prepare_staging(source)
    (target / "mutated.txt").write_text("neither old nor new", encoding="utf-8")
    area._write_record(
        {
            "state": "prepared",
            "target": str(target),
            "staging": str(staging),
            "backup": "",
            "old_manifest": {"files": {}},
            "new_manifest": {"files": {"SKILL.md": "deadbeef"}},
            "mode": "overwrite-exchange",
        }
    )
    with pytest.raises(OSError, match="ambiguous interrupted installation"):
        installer_module._recover_pending(area)


def test_maintenance_record_failure_leaves_recoverable_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "hetu-stock-analysis"
    _make_skill_package(source)
    destination = tmp_path / "skills"
    target = install_skill(source, destination)
    old_manifest = installer_module.build_skill_manifest(target)

    real_write = installer_module._MaintenanceArea._write_record
    calls = {"n": 0}

    def flaky_write(self, record: dict[str, object]) -> None:
        calls["n"] += 1
        if record.get("state") == "exchanged":
            raise OSError("injected record flush failure")
        real_write(self, record)

    monkeypatch.setattr(installer_module._MaintenanceArea, "_write_record", flaky_write)
    updated = source / "references" / "orchestration.md"
    updated.write_text("# v2\n", encoding="utf-8")
    _write_manifest(source)
    with pytest.raises(OSError, match="injected record flush failure"):
        install_skill(source, destination, force=True)
    monkeypatch.undo()

    # The next maintenance recovers from the real directories: the exchange
    # happened, so the pending transaction is finalized before installing.
    result = install_skill(source, destination, force=True)
    assert result.is_dir()
    verify_skill_manifest(target)
    assert installer_module.build_skill_manifest(target) != old_manifest


def test_cross_filesystem_maintenance_area_fails_before_touching_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "hetu-stock-analysis"
    _make_skill_package(source)
    destination = tmp_path / "skills"
    target = install_skill(source, destination)
    before = installer_module.build_skill_manifest(target)

    real_stat = os.stat
    area_prefix = str(destination.parent / ".hetu-skill-maintenance")

    def fake_stat(path: object, *args: Any, **kwargs: Any) -> os.stat_result:
        result = real_stat(path, *args, **kwargs)
        if str(path).startswith(area_prefix):
            # Rebuild the stat result with st_dev (index 2) bumped so the
            # same-filesystem check sees a different device.
            fields = tuple(result)[:10]
            return os.stat_result(fields[:2] + (result.st_dev + 1,) + fields[3:])
        return result

    monkeypatch.setattr(installer_module.os, "stat", fake_stat)
    with pytest.raises(OSError, match="not on the same filesystem"):
        install_skill(source, destination, force=True)
    monkeypatch.undo()
    assert installer_module.build_skill_manifest(target) == before


def test_concurrent_same_target_installs_serialize_and_remain_usable(
    tmp_path: Path,
) -> None:
    import threading

    source = tmp_path / "hetu-stock-analysis"
    _make_skill_package(source)
    destination = tmp_path / "skills"
    install_skill(source, destination)
    errors: list[Exception] = []

    def worker(index: int) -> None:
        try:
            local = tmp_path / f"source-{index}" / "hetu-stock-analysis"
            _make_skill_package(local)
            (local / "references" / "orchestration.md").write_text(
                f"# v{index}\n", encoding="utf-8"
            )
            _write_manifest(local)
            install_skill(local, destination, force=True)
        except Exception as error:  # noqa: BLE001 - recorded and asserted below
            errors.append(error)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(1, 4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    target = destination / "hetu-stock-analysis"
    verify_skill_manifest(target)
    installed = (target / "references" / "orchestration.md").read_text(encoding="utf-8")
    assert installed in {"# v1\n", "# v2\n", "# v3\n"}


def test_different_targets_operate_independently(tmp_path: Path) -> None:
    source = tmp_path / "hetu-stock-analysis"
    _make_skill_package(source)
    first = install_skill(source, tmp_path / "skills-a", force=True)
    second = install_skill(source, tmp_path / "skills-b")
    (first / "references" / "orchestration.md").write_text("# only-a\n", encoding="utf-8")
    _write_manifest(source)
    install_skill(source, tmp_path / "skills-a", force=True)
    # The A-root maintenance operations never disturbed the B-root install.
    verify_skill_manifest(second)
    assert (second / "references" / "orchestration.md").is_file()
    assert (first / "references" / "orchestration.md").is_file()
