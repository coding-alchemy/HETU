import hashlib
import json
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


def test_force_install_failure_removes_new_target_without_restoring_old_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "hetu-stock-analysis"
    _make_skill_package(source)
    destination = tmp_path / "skills"
    target = install_skill(source, destination)
    (target / "old-only.txt").write_text("old installation", encoding="utf-8")
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

    assert not target.exists()
    monkeypatch.undo()
    assert install_skill(source, destination) == target
