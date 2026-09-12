import hashlib
import json
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

import hetu_stock.skill.installer as installer_module
from hetu_stock.cli import app
from hetu_stock.skill import install_skill

runner = CliRunner()


def _copy_canonical_skill(tmp_path: Path) -> Path:
    target = tmp_path / "hetu-stock-analysis"
    shutil.copytree(Path("skills/hetu-stock-analysis"), target)
    return target


def _make_versioned_package(root: Path, marker: str) -> Path:
    root.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(Path("skills/hetu-stock-analysis"), root)
    (root / "references" / "version-marker.txt").write_text(marker, encoding="utf-8")
    files = {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.relative_to(root).as_posix() != "MANIFEST.json"
    }
    (root / "MANIFEST.json").write_text(
        json.dumps({"files": files}, indent=2), encoding="utf-8"
    )
    return root


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_skill_validate_accepts_canonical_package() -> None:
    result = runner.invoke(app, ["skill", "validate", "skills/hetu-stock-analysis"])

    assert result.exit_code == 0
    assert "valid" in result.stdout.lower()


def test_skill_validate_missing_package_exits_cleanly() -> None:
    result = runner.invoke(app, ["skill", "validate", str(Path("/no/skill"))])

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert result.output.strip()


def test_skill_validate_invalid_utf8_package_exits_cleanly(tmp_path: Path) -> None:
    root = tmp_path / "skill"
    root.mkdir()
    (root / "SKILL.md").write_bytes(b"\xff\xfe")

    result = runner.invoke(app, ["skill", "validate", str(root)])

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert result.output.strip()


def test_skill_validate_rejects_tampered_package(tmp_path: Path) -> None:
    target = _copy_canonical_skill(tmp_path)
    victim = target / "references" / "orchestration.md"
    victim.write_bytes(victim.read_bytes() + b"tampered")

    result = runner.invoke(app, ["skill", "validate", str(target)])

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "sha256 mismatch" in result.output


def test_skill_validate_rejects_package_with_extra_file(tmp_path: Path) -> None:
    target = _copy_canonical_skill(tmp_path)
    (target / "rogue.md").write_text("not in manifest", encoding="utf-8")

    result = runner.invoke(app, ["skill", "validate", str(target)])

    assert result.exit_code != 0
    assert "Traceback" not in result.output


def test_skill_validate_requires_manifest(
    tmp_path: Path,
) -> None:
    root = tmp_path / "skill"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: hetu-stock-analysis\ndescription: dev tree\n---\n\nbody\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["skill", "validate", str(root)])

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "MANIFEST.json" in result.output


@pytest.mark.parametrize(
    ("manifest", "message"),
    [
        ("{not json\n", "MANIFEST.json is invalid"),
        ("[]\n", "MANIFEST.json must contain a 'files' object"),
        ("{}\n", "MANIFEST.json must contain a 'files' object"),
        ('{"files": []}\n', "MANIFEST.json 'files' must be a mapping"),
    ],
)
def test_skill_validate_rejects_malformed_manifest_without_traceback(
    tmp_path: Path, manifest: str, message: str
) -> None:
    target = _copy_canonical_skill(tmp_path)
    target.joinpath("MANIFEST.json").write_text(manifest, encoding="utf-8")

    result = runner.invoke(app, ["skill", "validate", str(target)])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.output
    assert "invalid skill package:" in result.output
    assert message in result.output


def test_public_skill_validate_command_hides_malformed_manifest_traceback(
    tmp_path: Path,
) -> None:
    target = _copy_canonical_skill(tmp_path)
    target.joinpath("MANIFEST.json").write_text("{not json\n", encoding="utf-8")
    executable = Path(sys.executable).with_name("hetu-stock")

    result = subprocess.run(
        [str(executable), "skill", "validate", str(target)],
        check=False,
        capture_output=True,
        text=True,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "Traceback" not in output
    assert "invalid skill package: MANIFEST.json is invalid" in output


# --- Phase-5 stage 05: maintenance commands (status/diagnose/rollback/uninstall)


def _install_two_versions(tmp_path: Path) -> tuple[Path, Path]:
    destination = tmp_path / "skills"
    first = tmp_path / "v1" / "hetu-stock-analysis"
    _make_versioned_package(first, "version-1")
    install_skill(first, destination)
    second = tmp_path / "v2" / "hetu-stock-analysis"
    _make_versioned_package(second, "version-2")
    install_skill(second, destination, force=True)
    return destination, second


def test_skill_status_reports_installed_package(tmp_path: Path) -> None:
    destination, _ = _install_two_versions(tmp_path)

    result = runner.invoke(
        app,
        ["skill", "status", "--host", "codex", "--destination", str(destination)],
    )

    assert result.exit_code == 0
    assert "hetu-stock-analysis" in result.output
    assert "installed" in result.output.lower()
    # Locations are desensitized: the user's home directory must not leak.
    assert str(Path.home()) not in result.output
    assert "backup" in result.output.lower()


def test_skill_status_reports_missing_installation(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["skill", "status", "--host", "codex", "--destination", str(tmp_path / "skills")],
    )

    assert result.exit_code == 0
    assert "not installed" in result.output.lower()


def test_skill_diagnose_lists_backups_and_next_actions(tmp_path: Path) -> None:
    destination, _ = _install_two_versions(tmp_path)

    result = runner.invoke(
        app,
        ["skill", "diagnose", "--host", "codex", "--destination", str(destination)],
    )

    assert result.exit_code == 0
    assert "staging-" in result.output
    assert "next" in result.output.lower()
    assert str(Path.home()) not in result.output


def test_skill_diagnose_reports_integrity_failure_and_rollback_action(
    tmp_path: Path,
) -> None:
    destination, _ = _install_two_versions(tmp_path)
    victim = destination / "hetu-stock-analysis" / "SKILL.md"
    victim.write_bytes(victim.read_bytes() + b"tampered")

    result = runner.invoke(
        app,
        ["skill", "diagnose", "--host", "codex", "--destination", str(destination)],
    )

    assert result.exit_code == 0
    assert "sha256 mismatch" in result.output
    assert "rollback" in result.output.lower()


def test_skill_rollback_restores_previous_version_and_keeps_replaced(
    tmp_path: Path,
) -> None:
    destination, second = _install_two_versions(tmp_path)
    target = destination / "hetu-stock-analysis"
    assert (target / "references" / "version-marker.txt").read_text(
        encoding="utf-8"
    ) == "version-2"

    result = runner.invoke(
        app,
        ["skill", "rollback", "--host", "codex", "--destination", str(destination)],
    )

    assert result.exit_code == 0, result.output
    assert (target / "references" / "version-marker.txt").read_text(
        encoding="utf-8"
    ) == "version-1"
    # The replaced version stays available as a backup, not deleted.
    backups = installer_module.list_skill_backups(destination)
    assert any(
        backup.valid
        and (backup.package / "references" / "version-marker.txt").read_text(
            encoding="utf-8"
        )
        == "version-2"
        for backup in backups
    )


def test_skill_rollback_with_explicit_backup(tmp_path: Path) -> None:
    destination, second = _install_two_versions(tmp_path)
    backups = installer_module.list_skill_backups(destination)
    assert len(backups) == 1

    result = runner.invoke(
        app,
        [
            "skill",
            "rollback",
            "--host",
            "codex",
            "--destination",
            str(destination),
            "--backup",
            str(backups[0].path),
        ],
    )

    assert result.exit_code == 0, result.output
    target = destination / "hetu-stock-analysis"
    assert (target / "references" / "version-marker.txt").read_text(
        encoding="utf-8"
    ) == "version-1"


def test_skill_rollback_unknown_backup_exits_nonzero(tmp_path: Path) -> None:
    destination, _ = _install_two_versions(tmp_path)

    result = runner.invoke(
        app,
        [
            "skill",
            "rollback",
            "--host",
            "codex",
            "--destination",
            str(destination),
            "--backup",
            str(tmp_path / "no-such-backup"),
        ],
    )

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "backup" in result.output.lower()


def test_skill_rollback_without_any_backup_exits_nonzero(tmp_path: Path) -> None:
    first = tmp_path / "v1" / "hetu-stock-analysis"
    _make_versioned_package(first, "version-1")
    destination = tmp_path / "skills"
    install_skill(first, destination)

    result = runner.invoke(
        app,
        ["skill", "rollback", "--host", "codex", "--destination", str(destination)],
    )

    assert result.exit_code != 0
    assert "backup" in result.output.lower()


def test_skill_uninstall_lists_scope_and_requires_confirmation(
    tmp_path: Path,
) -> None:
    destination, _ = _install_two_versions(tmp_path)
    target = destination / "hetu-stock-analysis"

    result = runner.invoke(
        app,
        ["skill", "uninstall", "--host", "codex", "--destination", str(destination)],
    )

    assert result.exit_code != 0
    assert "scope" in result.output.lower()
    assert "hetu-stock-analysis" in result.output
    # Nothing was removed before explicit confirmation.
    assert target.is_dir()
    assert "Traceback" not in result.output


def test_skill_uninstall_removes_skill_and_keeps_user_files(tmp_path: Path) -> None:
    destination, _ = _install_two_versions(tmp_path)
    target = destination / "hetu-stock-analysis"
    user_file = destination / "my-notes.md"
    user_file.write_text("user data", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "skill",
            "uninstall",
            "--host",
            "codex",
            "--destination",
            str(destination),
            "--yes",
        ],
    )

    assert result.exit_code == 0, result.output
    assert not target.exists()
    assert user_file.read_text(encoding="utf-8") == "user data"
    # The managed maintenance area (staging/backups/records) goes with the
    # Skill it describes; the surrounding skills root is untouched.
    maintenance = destination.parent / ".hetu-skill-maintenance"
    assert not maintenance.exists()
    assert destination.is_dir()


def test_skill_uninstall_refuses_symbolic_link_target(tmp_path: Path) -> None:
    destination = tmp_path / "skills"
    target = destination / "hetu-stock-analysis"
    target.parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    _make_versioned_package(outside / "hetu-stock-analysis", "version-x")
    target.symlink_to(outside / "hetu-stock-analysis", target_is_directory=True)

    result = runner.invoke(
        app,
        [
            "skill",
            "uninstall",
            "--host",
            "codex",
            "--destination",
            str(destination),
            "--yes",
        ],
    )

    assert result.exit_code != 0
    assert target.is_symlink()


def _fake_managed_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Path]:
    home = tmp_path / "home"
    managed_root = tmp_path / "data" / "hetu-stock"
    envs_root = managed_root / "envs"
    current = envs_root / "env-current"
    previous = envs_root / "env-previous"
    for env in (current, previous):
        (env / "bin").mkdir(parents=True)
        _write_executable(env / "bin" / "python", f"#!{sys.executable}\n")
        _write_executable(env / "bin" / "hetu-stock", f"#!{sys.executable}\n")
        # Ownership evidence: an installed hetu_stock distribution.
        dist_info = (
            env / "lib" / "python3.11" / "site-packages" / "hetu_stock-0.2.dist-info"
        )
        dist_info.mkdir(parents=True)
    launcher = home / ".local" / "bin" / "hetu-stock"
    launcher.parent.mkdir(parents=True)
    launcher.symlink_to(current / "bin" / "hetu-stock")
    (managed_root / "installation.json").write_text(
        json.dumps(
            {
                "env": str(current),
                "previous_env": str(previous),
                "launcher": str(launcher),
                "skill_target": str(home / ".codex/skills/hetu-stock-analysis"),
                "updated_at": "2026-09-12T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    return {
        "managed_root": managed_root,
        "envs_root": envs_root,
        "current": current,
        "previous": previous,
        "launcher": launcher,
    }


def test_skill_uninstall_keeps_shared_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    managed = _fake_managed_home(tmp_path, monkeypatch)
    first = tmp_path / "v1" / "hetu-stock-analysis"
    _make_versioned_package(first, "version-1")
    codex_root = tmp_path / "home/.codex/skills"
    claude_root = tmp_path / "home/.claude/skills"
    install_skill(first, codex_root)
    install_skill(first, claude_root)

    result = runner.invoke(
        app,
        [
            "skill",
            "uninstall",
            "--host",
            "codex",
            "--yes",
            "--remove-env",
        ],
    )

    assert result.exit_code != 0
    assert "shared" in result.output.lower()
    assert managed["current"].is_dir()
    assert (claude_root / "hetu-stock-analysis").is_dir()
    # The refusal is fail-before-any-mutation: the selected Skill also stays.
    assert (codex_root / "hetu-stock-analysis").is_dir()


def test_skill_uninstall_remove_env_without_other_hosts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    managed = _fake_managed_home(tmp_path, monkeypatch)
    first = tmp_path / "v1" / "hetu-stock-analysis"
    _make_versioned_package(first, "version-1")
    codex_root = tmp_path / "home/.codex/skills"
    install_skill(first, codex_root)

    result = runner.invoke(
        app,
        [
            "skill",
            "uninstall",
            "--host",
            "codex",
            "--yes",
            "--remove-env",
        ],
    )

    assert result.exit_code == 0, result.output
    assert not (codex_root / "hetu-stock-analysis").exists()
    assert not managed["envs_root"].exists()
    assert not managed["launcher"].exists()


# --- Phase-5 stage-05 review fixes: ownership evidence and combo closure -----


def test_uninstall_keeps_foreign_same_shaped_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Directory-name shape is not ownership: a launcher pointing at another
    project's lookalike envs/env-* path must never cause deletion of that
    project's environment on uninstall."""
    managed = _fake_managed_home(tmp_path, monkeypatch)
    foreign = tmp_path / "other-project" / "envs" / "env-theirs"
    (foreign / "bin").mkdir(parents=True)
    _write_executable(foreign / "bin" / "hetu-stock", f"#!{sys.executable}\n")
    notes = foreign / "notes.md"
    notes.write_text("another project's data", encoding="utf-8")
    managed["launcher"].unlink()
    managed["launcher"].symlink_to(foreign / "bin" / "hetu-stock")

    first = tmp_path / "v1" / "hetu-stock-analysis"
    _make_versioned_package(first, "version-1")
    codex_root = tmp_path / "home/.codex/skills"
    install_skill(first, codex_root)

    result = runner.invoke(
        app, ["skill", "uninstall", "--host", "codex", "--yes"]
    )

    assert result.exit_code == 0, result.output
    assert not (codex_root / "hetu-stock-analysis").exists()
    # The foreign environment and its data survive; the unmanaged launcher
    # symlink is left untouched as well.
    assert notes.read_text(encoding="utf-8") == "another project's data"
    assert managed["launcher"].is_symlink()


def _point_launcher(launcher: Path, env_dir: Path) -> None:
    launcher.unlink()
    launcher.symlink_to(env_dir / "bin" / "hetu-stock")


def _find_backup(codex_root: Path, marker: str) -> Path:
    """Locate the maintenance-area directory holding the given Skill version."""
    area = codex_root.parent / ".hetu-skill-maintenance"
    for digest in sorted(area.iterdir()):
        for backup_dir in sorted(digest.iterdir()):
            if not backup_dir.is_dir():
                continue
            marker_file = (
                backup_dir / "hetu-stock-analysis" / "references" / "version-marker.txt"
            )
            if marker_file.is_file() and marker_file.read_text(encoding="utf-8") == marker:
                return backup_dir
    raise AssertionError(f"no backup holding {marker}")


def test_skill_rollback_restores_matching_launcher_combo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rolling the Skill back must restore the helper combo recorded for the
    displaced version, not whatever environment happens to be inferable."""
    managed = _fake_managed_home(tmp_path, monkeypatch)
    first = tmp_path / "v1" / "hetu-stock-analysis"
    _make_versioned_package(first, "version-1")
    codex_root = tmp_path / "home/.codex/skills"
    # Mirror install.sh ordering: the Skill is published while the launcher
    # still tracks the previous environment; the launcher moves afterwards.
    _point_launcher(managed["launcher"], managed["previous"])
    install_skill(first, codex_root)
    second = tmp_path / "v2" / "hetu-stock-analysis"
    _make_versioned_package(second, "version-2")
    install_skill(second, codex_root, force=True)
    _point_launcher(managed["launcher"], managed["current"])

    result = runner.invoke(app, ["skill", "rollback", "--host", "codex"])

    assert result.exit_code == 0, result.output
    target = codex_root / "hetu-stock-analysis"
    assert (target / "references" / "version-marker.txt").read_text(
        encoding="utf-8"
    ) == "version-1"
    assert managed["launcher"].resolve() == (
        managed["previous"] / "bin" / "hetu-stock"
    ).resolve()


def test_rollback_to_older_backup_restores_its_recorded_combo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit rollback to an older backup restores the environment that
    backup is recorded with — even with several environments on disk."""
    managed = _fake_managed_home(tmp_path, monkeypatch)
    third = managed["envs_root"] / "env-third"
    (third / "bin").mkdir(parents=True)
    _write_executable(third / "bin" / "hetu-stock", f"#!{sys.executable}\n")
    dist_info = (
        third / "lib" / "python3.11" / "site-packages" / "hetu_stock-0.2.dist-info"
    )
    dist_info.mkdir(parents=True)
    codex_root = tmp_path / "home/.codex/skills"
    versions = {}
    envs = [managed["previous"], managed["current"], third]
    # Truthful install.sh ordering: each Skill is published while the
    # launcher still tracks that version's environment.
    _point_launcher(managed["launcher"], envs[0])
    for index, marker in enumerate(("version-1", "version-2", "version-3"), start=1):
        source = tmp_path / f"v{index}" / "hetu-stock-analysis"
        _make_versioned_package(source, marker)
        install_skill(source, codex_root, force=(index > 1))
        if index > 1:
            # The exchange keeps the displaced version as the rollback backup.
            versions[f"version-{index - 1}"] = _find_backup(
                codex_root, f"version-{index - 1}"
            )
        if index >= 2:
            # The launcher moves to this version's environment only after the
            # next version's Skill has been published.
            _point_launcher(managed["launcher"], envs[index - 1])

    result = runner.invoke(
        app,
        ["skill", "rollback", "--host", "codex", "--backup", str(versions["version-1"])],
    )

    assert result.exit_code == 0, result.output
    target = codex_root / "hetu-stock-analysis"
    assert (target / "references" / "version-marker.txt").read_text(
        encoding="utf-8"
    ) == "version-1"
    assert managed["launcher"].resolve() == (
        managed["previous"] / "bin" / "hetu-stock"
    ).resolve()


def test_rollback_fails_without_backup_combination_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A backup without a combination record cannot be confirmed: rollback
    must fail before touching anything instead of guessing an environment."""
    managed = _fake_managed_home(tmp_path, monkeypatch)
    first = tmp_path / "v1" / "hetu-stock-analysis"
    _make_versioned_package(first, "version-1")
    codex_root = tmp_path / "home/.codex/skills"
    _point_launcher(managed["launcher"], managed["previous"])
    install_skill(first, codex_root)
    second = tmp_path / "v2" / "hetu-stock-analysis"
    _make_versioned_package(second, "version-2")
    install_skill(second, codex_root, force=True)
    backup = _find_backup(codex_root, "version-1")
    (backup / "combo.json").unlink()
    _point_launcher(managed["launcher"], managed["current"])
    before = managed["launcher"].resolve()

    result = runner.invoke(app, ["skill", "rollback", "--host", "codex"])

    assert result.exit_code != 0
    assert "combination" in result.output.lower()
    # The scene is preserved: current Skill, launcher, and all environments.
    assert (
        codex_root / "hetu-stock-analysis" / "references" / "version-marker.txt"
    ).read_text(encoding="utf-8") == "version-2"
    assert managed["launcher"].resolve() == before


def test_rollback_fails_when_recorded_env_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A recorded combination whose environment no longer exists must fail
    explicitly; the number of remaining environments must not decide."""
    managed = _fake_managed_home(tmp_path, monkeypatch)
    first = tmp_path / "v1" / "hetu-stock-analysis"
    _make_versioned_package(first, "version-1")
    codex_root = tmp_path / "home/.codex/skills"
    _point_launcher(managed["launcher"], managed["previous"])
    install_skill(first, codex_root)
    second = tmp_path / "v2" / "hetu-stock-analysis"
    _make_versioned_package(second, "version-2")
    install_skill(second, codex_root, force=True)
    _point_launcher(managed["launcher"], managed["current"])
    shutil.rmtree(managed["previous"])

    result = runner.invoke(app, ["skill", "rollback", "--host", "codex"])

    assert result.exit_code != 0
    assert (
        codex_root / "hetu-stock-analysis" / "references" / "version-marker.txt"
    ).read_text(encoding="utf-8") == "version-2"
    assert managed["launcher"].resolve() == (
        managed["current"] / "bin" / "hetu-stock"
    ).resolve()


def test_uninstall_removes_host_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Uninstalling a host drops its retained-environment reference so
    pruning never treats it as still needed."""
    managed = _fake_managed_home(tmp_path, monkeypatch)
    first = tmp_path / "v1" / "hetu-stock-analysis"
    _make_versioned_package(first, "version-1")
    installed = runner.invoke(
        app, ["skill", "install", "--host", "codex", "--source", str(first)]
    )
    assert installed.exit_code == 0, installed.output
    hosts_dir = managed["managed_root"] / "hosts"
    assert (hosts_dir / "codex.json").is_file()

    result = runner.invoke(
        app, ["skill", "uninstall", "--host", "codex", "--yes"]
    )

    assert result.exit_code == 0, result.output
    assert not (hosts_dir / "codex.json").exists()


def test_skill_uninstall_keeps_shared_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Uninstalling one host must not remove the shared launcher while any
    other host still has the Skill installed."""
    managed = _fake_managed_home(tmp_path, monkeypatch)
    first = tmp_path / "v1" / "hetu-stock-analysis"
    _make_versioned_package(first, "version-1")
    codex_root = tmp_path / "home/.codex/skills"
    claude_root = tmp_path / "home/.claude/skills"
    install_skill(first, codex_root)
    install_skill(first, claude_root)

    result = runner.invoke(
        app, ["skill", "uninstall", "--host", "codex", "--yes"]
    )

    assert result.exit_code == 0, result.output
    assert not (codex_root / "hetu-stock-analysis").exists()
    assert (claude_root / "hetu-stock-analysis").is_dir()
    assert managed["launcher"].is_symlink()


# --- Phase-5 stage-05 review round 2: residual fixes --------------------------


def test_remove_env_keeps_environment_without_record_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Installing this package in an environment is not ownership: an
    environment that holds hetu_stock but is named by no combo record must
    survive --remove-env with its user data, and the scope must say kept."""
    managed = _fake_managed_home(tmp_path, monkeypatch)
    foreign = managed["envs_root"] / "env-foreign"
    (foreign / "bin").mkdir(parents=True)
    _write_executable(foreign / "bin" / "hetu-stock", f"#!{sys.executable}\n")
    dist_info = (
        foreign / "lib" / "python3.11" / "site-packages" / "hetu_stock-0.2.dist-info"
    )
    dist_info.mkdir(parents=True)
    notes = foreign / "user-notes.md"
    notes.write_text("another project's work", encoding="utf-8")
    managed["launcher"].unlink()
    managed["launcher"].symlink_to(foreign / "bin" / "hetu-stock")
    first = tmp_path / "v1" / "hetu-stock-analysis"
    _make_versioned_package(first, "version-1")
    codex_root = tmp_path / "home/.codex/skills"
    install_skill(first, codex_root)

    result = runner.invoke(
        app, ["skill", "uninstall", "--host", "codex", "--remove-env", "--yes"]
    )

    assert result.exit_code == 0, result.output
    assert notes.read_text(encoding="utf-8") == "another project's work"
    assert "kept" in result.output
    # The record-named previous environment is still ours to remove.
    assert not managed["previous"].exists()
