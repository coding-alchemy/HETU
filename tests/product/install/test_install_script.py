from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from hetu_stock.skill import install_skill, validate_skill_package
from tests.product.cli.help_text import listed_root_commands

ROOT = Path(__file__).resolve().parents[3]
INSTALLER = ROOT / "scripts" / "install.sh"
CANONICAL_SKILL = ROOT / "skills" / "hetu-stock-analysis"
HETU_BINARY = (ROOT / ".venv" / "bin" / "hetu-stock").resolve()

_INSTALLED_SKILL_RELATIVE = "skills/hetu-stock-analysis"

_PHASE_ONE_RESOURCES = (
    "references/controller.md",
    "references/workflow.md",
    "references/pause-resume.md",
    "references/stages",
    "references/examples/stage-result.example.json",
    "references/schema",
    "templates/report.md.j2",
)


def _tree_snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_three_host_installs_are_identical_to_canonical_source(tmp_path: Path) -> None:
    expected = _tree_snapshot(CANONICAL_SKILL)
    snapshots = []
    for host in ("codex", "claude", "opencode"):
        installed = install_skill(CANONICAL_SKILL, tmp_path / host / "skills")
        validate_skill_package(installed)
        snapshots.append(_tree_snapshot(installed))

    assert snapshots == [expected, expected, expected]


def _fake_cli_source() -> str:
    return textwrap.dedent(
        f"""\
        #!{sys.executable}
        import os
        import shutil
        import sys
        from pathlib import Path

        args = sys.argv[1:]
        if args == ["--help"]:
            if os.environ.get("FAKE_CLI_SELFTEST_FAIL", "0") == "1":
                print("fake cli selftest failure", file=sys.stderr)
                raise SystemExit(1)
            print("fake hetu-stock help")
            raise SystemExit(0)

        if args[:2] == ["skill", "validate"] and len(args) == 3:
            if (
                os.environ.get("FAKE_POST_SWITCH_FAIL", "0") == "1"
                and os.path.islink(sys.argv[0])
            ):
                print("post-switch validation failed", file=sys.stderr)
                raise SystemExit(1)
            source = Path(args[2])
            if not (source / "SKILL.md").is_file() or not (source / "MANIFEST.json").is_file():
                print("invalid skill package", file=sys.stderr)
                raise SystemExit(1)
            print(f"Skill package is valid: {{source}}")
            raise SystemExit(0)

        if args[:2] == ["skill", "install"]:
            host = args[args.index("--host") + 1]
            source = Path(args[args.index("--source") + 1])
            force = "--force" in args
            home = Path(os.environ["HOME"])
            if host == "codex":
                root = Path(os.environ.get("CODEX_HOME", home / ".codex")) / "skills"
            elif host == "claude":
                root = home / ".claude" / "skills"
            elif host == "zcode":
                root = home / ".zcode" / "skills"
            else:
                root = (
                    Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
                    / "opencode"
                    / "skills"
                )
            target = root / "hetu-stock-analysis"
            if target.exists():
                if not force:
                    print(f"install failed: {{target}}", file=sys.stderr)
                    raise SystemExit(1)
                backup = root / ".fake-maintenance" / "backup" / "hetu-stock-analysis"
                shutil.rmtree(backup.parent, ignore_errors=True)
                backup.parent.mkdir(parents=True)
                shutil.move(str(target), str(backup))
            root.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__"))
            print(f"Skill installed to: {{target}}")
            raise SystemExit(0)

        if args[:2] == ["skill", "rollback"]:
            home = Path(os.environ["HOME"])
            if "--host" not in args:
                # Mirror the real CLI: --host is required (typer exit code 2).
                print("rollback failed: missing --host", file=sys.stderr)
                raise SystemExit(2)
            if "--destination" in args:
                root = Path(args[args.index("--destination") + 1])
            else:
                host = args[args.index("--host") + 1]
                if host == "codex":
                    root = Path(os.environ.get("CODEX_HOME", home / ".codex")) / "skills"
                elif host == "claude":
                    root = home / ".claude" / "skills"
                elif host == "zcode":
                    root = home / ".zcode" / "skills"
                else:
                    root = (
                        Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
                        / "opencode"
                        / "skills"
                    )
            target = root / "hetu-stock-analysis"
            backup = root / ".fake-maintenance" / "backup" / "hetu-stock-analysis"
            saved = root / ".fake-maintenance" / "saved" / "hetu-stock-analysis"
            if not target.is_dir() or not backup.is_dir():
                print("rollback failed: no complete backup", file=sys.stderr)
                raise SystemExit(1)
            shutil.rmtree(saved.parent, ignore_errors=True)
            saved.parent.mkdir(parents=True)
            shutil.move(str(target), str(saved))
            shutil.move(str(backup), str(target))
            shutil.rmtree(saved.parent, ignore_errors=True)
            print(f"Rolled back to: {{target}}")
            raise SystemExit(0)

        print(f"unexpected fake CLI arguments: {{args}}", file=sys.stderr)
        raise SystemExit(2)
        """
    )


def _fake_python_source() -> str:
    cli_source = repr(_fake_cli_source())
    return textwrap.dedent(
        f"""\
        #!{sys.executable}
        import os
        import shutil
        import stat
        import sys
        from pathlib import Path

        args = sys.argv[1:]
        command_name = Path(sys.argv[0]).name.upper().replace(".", "_")
        if args and args[0] == "-c":
            raise SystemExit(int(os.environ.get(f"FAKE_{{command_name}}_VERSION_EXIT", "0")))
        if args[:3] == ["-m", "venv", "--help"]:
            raise SystemExit(int(os.environ.get("FAKE_VENV_EXIT", "0")))
        if args[:2] == ["-m", "venv"] and len(args) == 3:
            if os.environ.get("FAKE_VENV_EXIT", "0") != "0":
                raise SystemExit(1)
            target = Path(args[2])
            (target / "bin").mkdir(parents=True, exist_ok=True)
            python_target = target / "bin" / "python"
            shutil.copyfile(__file__, python_target)
            python_target.chmod(python_target.stat().st_mode | stat.S_IXUSR)
            raise SystemExit(0)
        if args[:3] == ["-m", "pip", "--version"]:
            raise SystemExit(0)
        if args[:2] == ["-m", "pip"]:
            if os.environ.get("FAKE_PIP_FAIL", "0") == "1":
                print("CERTIFICATE_VERIFY_FAILED", file=sys.stderr)
                raise SystemExit(2)
            cli = Path(sys.argv[0]).parent / "hetu-stock"
            cli.write_text({cli_source}, encoding="utf-8")
            cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
            # Record the installed distribution so the installer can compare
            # versions (managed-environment reuse) and recover combos.
            version = ""
            pyproject = Path(args[-1]) / "pyproject.toml"
            if pyproject.is_file():
                for line in pyproject.read_text(encoding="utf-8").splitlines():
                    if line.startswith("version = "):
                        version = line.split('"')[1]
                        break
            if version:
                venv_root = Path(sys.argv[0]).parent.parent
                dist = (
                    venv_root
                    / "lib"
                    / "python3.11"
                    / "site-packages"
                    / f"hetu_stock-{{version}}.dist-info"
                )
                dist.mkdir(parents=True, exist_ok=True)
                (dist / "METADATA").write_text(
                    f"Metadata-Version: 2.1\\nName: hetu-stock\\nVersion: {{version}}\\n",
                    encoding="utf-8",
                )
            raise SystemExit(0)
        print(f"unexpected fake Python arguments: {{args}}", file=sys.stderr)
        raise SystemExit(2)
        """
    )


def _fake_install_environment(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    fake_bin = tmp_path / "fake bin"
    home.mkdir(parents=True, exist_ok=True)
    for command in ("python3.12", "python3.11", "python3"):
        _write_executable(fake_bin / command, _fake_python_source())

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "XDG_DATA_HOME": str(tmp_path / "data root"),
            "XDG_CONFIG_HOME": str(tmp_path / "config root"),
            "CODEX_HOME": str(tmp_path / "codex root"),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
        }
    )
    return env


def _run_installer(
    tmp_path: Path,
    *args: str,
    env_updates: dict[str, str] | None = None,
    installer: Path = INSTALLER,
) -> subprocess.CompletedProcess[str]:
    env = _fake_install_environment(tmp_path)
    if env_updates:
        env.update(env_updates)
    return subprocess.run(
        ["bash", str(installer), *args],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_requires_host(tmp_path: Path) -> None:
    result = _run_installer(tmp_path)

    assert result.returncode != 0
    assert "--host" in result.stderr


def test_rejects_unknown_host(tmp_path: Path) -> None:
    result = _run_installer(tmp_path, "--host", "other")

    assert result.returncode != 0
    assert "codex, claude, opencode, zcode" in result.stderr


def test_installs_to_zcode_skills_root(tmp_path: Path) -> None:
    result = _run_installer(tmp_path, "--host", "zcode")

    assert result.returncode == 0, result.stderr
    assert (
        tmp_path / "home/.zcode/skills/hetu-stock-analysis/SKILL.md"
    ).is_file()


def _managed_envs(tmp_path: Path) -> list[Path]:
    envs_root = tmp_path / "data root/hetu-stock/envs"
    if not envs_root.is_dir():
        return []
    return sorted(path for path in envs_root.iterdir() if path.is_dir())


def _launcher(tmp_path: Path) -> Path:
    return tmp_path / "home/.local/bin/hetu-stock"


def _managed_root(tmp_path: Path) -> Path:
    return tmp_path / "data root/hetu-stock"


def _skill_target(tmp_path: Path, host: str) -> Path:
    if host == "codex":
        return tmp_path / "codex root/skills/hetu-stock-analysis"
    if host == "claude":
        return tmp_path / "home/.claude/skills/hetu-stock-analysis"
    if host == "zcode":
        return tmp_path / "home/.zcode/skills/hetu-stock-analysis"
    return tmp_path / "config root/opencode/skills/hetu-stock-analysis"


def test_installs_managed_cli_and_codex_skill(tmp_path: Path) -> None:
    result = _run_installer(tmp_path, "--host", "codex")

    assert result.returncode == 0, result.stderr
    launcher = _launcher(tmp_path)
    assert launcher.is_symlink()
    envs = _managed_envs(tmp_path)
    assert len(envs) == 1
    assert launcher.resolve() == (envs[0] / "bin/hetu-stock").resolve()
    assert (
        tmp_path / "codex root/skills/hetu-stock-analysis/SKILL.md"
    ).is_file()
    assert "Installation complete" in result.stdout


def test_force_is_required_to_replace_existing_skill(tmp_path: Path) -> None:
    first = _run_installer(tmp_path, "--host", "claude")
    second = _run_installer(tmp_path, "--host", "claude")
    forced = _run_installer(tmp_path, "--host", "claude", "--force")

    assert first.returncode == 0, first.stderr
    assert second.returncode != 0
    assert "--force" in second.stderr
    assert forced.returncode == 0, forced.stderr


def test_rejects_launcher_not_managed_by_hetu(tmp_path: Path) -> None:
    launcher = tmp_path / "home/.local/bin/hetu-stock"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("do not replace", encoding="utf-8")

    result = _run_installer(tmp_path, "--host", "codex")

    assert result.returncode != 0
    assert "Refusing to replace" in result.stderr
    assert launcher.read_text(encoding="utf-8") == "do not replace"


def test_pip_failure_does_not_install_skill(tmp_path: Path) -> None:
    result = _run_installer(
        tmp_path,
        "--host",
        "codex",
        env_updates={"FAKE_PIP_FAIL": "1"},
    )

    assert result.returncode != 0
    assert "TLS" in result.stderr
    assert not (tmp_path / "codex root/skills/hetu-stock-analysis").exists()


# --- Phase-5 stage 05: versioned helper environments and combo recovery ------


def test_failed_first_install_leaves_no_managed_state(tmp_path: Path) -> None:
    result = _run_installer(
        tmp_path,
        "--host",
        "codex",
        env_updates={"FAKE_PIP_FAIL": "1"},
    )

    assert result.returncode != 0
    assert _managed_envs(tmp_path) == []
    assert not _launcher(tmp_path).exists()
    assert not (tmp_path / "codex root/skills/hetu-stock-analysis").exists()


def test_failed_update_keeps_previous_environment_launcher_and_skill(
    tmp_path: Path,
) -> None:
    first = _run_installer(tmp_path, "--host", "codex")
    assert first.returncode == 0, first.stderr
    envs_before = _managed_envs(tmp_path)
    assert len(envs_before) == 1
    launcher_before = _launcher(tmp_path).resolve()

    failed = _run_installer(
        tmp_path,
        "--host",
        "codex",
        "--force",
        env_updates={"FAKE_PIP_FAIL": "1"},
    )

    assert failed.returncode != 0
    assert _managed_envs(tmp_path) == envs_before
    assert _launcher(tmp_path).resolve() == launcher_before
    assert (tmp_path / "codex root/skills/hetu-stock-analysis/SKILL.md").is_file()


def test_cli_self_check_failure_preserves_previous_combination(
    tmp_path: Path,
) -> None:
    first = _run_installer(tmp_path, "--host", "codex")
    assert first.returncode == 0, first.stderr
    envs_before = _managed_envs(tmp_path)
    launcher_before = _launcher(tmp_path).resolve()

    failed = _run_installer(
        tmp_path,
        "--host",
        "codex",
        "--force",
        env_updates={"FAKE_CLI_SELFTEST_FAIL": "1"},
    )

    assert failed.returncode != 0
    assert "self-check" in failed.stderr
    assert _managed_envs(tmp_path) == envs_before
    assert _launcher(tmp_path).resolve() == launcher_before
    assert (tmp_path / "codex root/skills/hetu-stock-analysis/SKILL.md").is_file()


def test_post_switch_failure_restores_previous_launcher(tmp_path: Path) -> None:
    first = _run_installer(tmp_path, "--host", "codex")
    assert first.returncode == 0, first.stderr
    envs_before = _managed_envs(tmp_path)
    assert len(envs_before) == 1
    previous_cli = envs_before[0] / "bin/hetu-stock"

    failed = _run_installer(
        tmp_path,
        "--host",
        "codex",
        "--force",
        env_updates={"FAKE_POST_SWITCH_FAIL": "1"},
    )

    assert failed.returncode != 0
    assert "restored" in failed.stderr
    # The Skill publish itself succeeded atomically; the launcher must be back
    # on the previous environment and the abandoned new environment removed.
    assert _launcher(tmp_path).resolve() == previous_cli.resolve()
    assert _managed_envs(tmp_path) == envs_before
    assert (tmp_path / "codex root/skills/hetu-stock-analysis/SKILL.md").is_file()


def test_successful_update_keeps_previous_environment_for_rollback(
    tmp_path: Path,
) -> None:
    first = _run_installer(tmp_path, "--host", "codex")
    assert first.returncode == 0, first.stderr
    previous_envs = _managed_envs(tmp_path)

    second = _run_installer(tmp_path, "--host", "codex", "--force")
    assert second.returncode == 0, second.stderr

    envs = _managed_envs(tmp_path)
    assert len(envs) == 2
    assert envs[0] == previous_envs[0]
    launcher = _launcher(tmp_path)
    assert launcher.is_symlink()
    assert launcher.resolve() == (envs[1] / "bin/hetu-stock").resolve()
    for env in envs:
        assert (env / "bin/hetu-stock").is_file()
    assert (
        tmp_path / "codex root/skills/hetu-stock-analysis/SKILL.md"
    ).is_file()


def test_fails_without_compatible_python(tmp_path: Path) -> None:
    result = _run_installer(
        tmp_path,
        "--host",
        "codex",
        env_updates={
            "FAKE_PYTHON3_12_VERSION_EXIT": "1",
            "FAKE_PYTHON3_11_VERSION_EXIT": "1",
            "FAKE_PYTHON3_VERSION_EXIT": "1",
        },
    )

    assert result.returncode != 0
    assert "Python 3.11 or 3.12" in result.stderr


def test_uses_explicit_compatible_python(tmp_path: Path) -> None:
    result = _run_installer(
        tmp_path,
        "--host",
        "codex",
        "--python",
        "python3.11",
    )

    assert result.returncode == 0, result.stderr
    assert "fake bin/python3.11" in result.stdout


def test_warns_when_launcher_directory_is_not_on_path(tmp_path: Path) -> None:
    result = _run_installer(tmp_path, "--host", "opencode")

    assert result.returncode == 0, result.stderr
    assert "$HOME/.local/bin" in result.stdout
    assert "PATH" in result.stdout


def test_supports_repository_path_with_spaces(tmp_path: Path) -> None:
    assert INSTALLER.is_file(), "installer must exist before path test can run"
    copied_root = tmp_path / "repository with spaces"
    (copied_root / "scripts").mkdir(parents=True)
    shutil.copy2(INSTALLER, copied_root / "scripts/install.sh")
    shutil.copytree(ROOT / "skills", copied_root / "skills")

    result = _run_installer(
        tmp_path / "runtime",
        "--host",
        "codex",
        installer=copied_root / "scripts/install.sh",
    )

    assert result.returncode == 0, result.stderr


def test_readme_advertises_skill_first_installer() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    quick_start = readme.split("## 快速开始", 1)[1].split("\n## ", 1)[0]

    assert "git clone https://github.com/coding-alchemy/HETU.git" in quick_start
    assert "./scripts/install.sh --host codex" in quick_start
    assert "./scripts/install.sh --host codex --python python3.12" in quick_start
    assert "python -m pip install -e '.[dev]'" not in quick_start


# --- Phase-2 Plan 03 Task 4: installed package boundary evidence ------------


def _installed_skill_root(tmp_path: Path, host: str) -> Path:
    if host == "codex":
        return tmp_path / "codex root" / _INSTALLED_SKILL_RELATIVE
    if host == "claude":
        return tmp_path / "home" / ".claude" / _INSTALLED_SKILL_RELATIVE
    return tmp_path / "config root" / "opencode" / _INSTALLED_SKILL_RELATIVE


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_installed_matches_canonical(installed: Path) -> None:
    assert (installed / "SKILL.md").is_file()
    assert (installed / "MANIFEST.json").is_file()
    assert _sha256(installed / "SKILL.md") == _sha256(
        CANONICAL_SKILL / "SKILL.md"
    ), "installed SKILL.md hash drifted from canonical"
    assert _sha256(installed / "MANIFEST.json") == _sha256(
        CANONICAL_SKILL / "MANIFEST.json"
    ), "installed MANIFEST.json hash drifted from canonical"
    actual = {
        path.relative_to(installed).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in installed.rglob("*")
        if path.is_file() and path.name != "MANIFEST.json"
    }
    manifest = json.loads(
        (installed / "MANIFEST.json").read_text(encoding="utf-8")
    )
    assert manifest == {"files": dict(sorted(actual.items()))}, (
        "installed MANIFEST.json does not match installed tree"
    )


def _assert_installed_has_core_work_packages(installed: Path) -> None:
    core = installed / "references" / "work-packages" / "core"
    specs = sorted(core.glob("*.md"))
    ids: set[str] = set()
    for p in specs:
        match = re.match(r"(W\d+)", p.stem)
        assert match, f"non-work-package file in core/: {p.name}"
        ids.add(match.group(1))
    assert ids == {f"W{i}" for i in range(11)}, (
        f"installed core work packages drifted: {sorted(ids)}"
    )
    assert len(specs) == 11


def _assert_installed_has_no_phase_one_resources(installed: Path) -> None:
    for relative in _PHASE_ONE_RESOURCES:
        assert not (installed / relative).exists(), (
            f"phase-one resource leaked into install: {relative}"
        )


@pytest.mark.parametrize("host", ["codex", "claude", "opencode"])
def test_installed_package_matches_canonical_byte_for_byte(
    tmp_path: Path,
    host: str,
) -> None:
    result = _run_installer(tmp_path, "--host", host)
    assert result.returncode == 0, result.stderr

    installed = _installed_skill_root(tmp_path, host)
    _assert_installed_matches_canonical(installed)
    _assert_installed_has_core_work_packages(installed)
    _assert_installed_has_no_phase_one_resources(installed)


def test_root_cli_help_remains_lightweight() -> None:
    assert HETU_BINARY.is_file(), (
        f"real hetu-stock binary not found at {HETU_BINARY}"
    )
    result = subprocess.run(
        [str(HETU_BINARY), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    allowed = {"skill", "helper"}
    forbidden = {"request", "run", "report", "schema", "analyze", "research", "legacy"}
    listed = listed_root_commands(result.stdout)
    if listed:
        assert allowed.issubset(listed), (
            f"allowed root commands missing from help: "
            f"{sorted(allowed - listed)}"
        )
        leaked = forbidden.intersection(listed)
        assert not leaked, (
            f"forbidden root commands reappeared in help: {sorted(leaked)}"
        )
    else:
        # Unknown help rendering (Typer/rich version differences): fall back
        # to word-level assertions so the check never depends on box-drawing
        # details, while the command-tree tests pin the closed surface
        # structurally against the registered command objects.
        for word in allowed:
            assert word in result.stdout, f"allowed command missing: {word}"
        for word in ("run init", "run submit", "report render", "legacy"):
            assert word not in result.stdout, f"forbidden command visible: {word}"


# --- Phase-5 stage-05 review fixes: combo closure, ownership, old layout -----


def _versioned_repo_copy(tmp_path: Path, name: str, marker: str) -> Path:
    """Copy scripts/install.sh + canonical skill with a version marker file."""
    copied_root = tmp_path / name
    (coped_scripts := copied_root / "scripts").mkdir(parents=True)
    shutil.copy2(INSTALLER, coped_scripts / "install.sh")
    shutil.copytree(ROOT / "skills", copied_root / "skills")
    (copied_root / "pyproject.toml").write_text(
        f'[project]\nname = "hetu-stock"\nversion = "{marker}"\n',
        encoding="utf-8",
    )
    skill = copied_root / "skills" / "hetu-stock-analysis"
    (skill / "references" / "version.txt").write_text(marker, encoding="utf-8")
    files = {
        path.relative_to(skill).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in skill.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.relative_to(skill).as_posix() != "MANIFEST.json"
    }
    (skill / "MANIFEST.json").write_text(
        json.dumps({"files": files}, indent=2), encoding="utf-8"
    )
    return copied_root


def _installed_marker(tmp_path: Path, host: str = "codex") -> str:
    root = tmp_path / "codex root" / "skills" / "hetu-stock-analysis"
    return (root / "references" / "version.txt").read_text(encoding="utf-8")


def test_old_layout_installation_upgrades_safely(tmp_path: Path) -> None:
    """A v0.2-era official install (hetu-stock/venv) must be upgradable.

    The old layout was created by the previous installer itself, so the
    managed launcher check must recognize it via ownership evidence and keep
    it as the previous combo instead of refusing to run.
    """
    home = tmp_path / "home"
    managed_root = tmp_path / "data root" / "hetu-stock"
    old_venv = managed_root / "venv"
    (old_venv / "bin").mkdir(parents=True)
    _write_executable(old_venv / "bin" / "hetu-stock", "#!/bin/sh\nexit 0\n")
    dist_info = old_venv / "lib" / "python3.11" / "site-packages" / "hetu_stock-0.2.dist-info"
    dist_info.mkdir(parents=True)
    launcher = home / ".local" / "bin" / "hetu-stock"
    launcher.parent.mkdir(parents=True)
    launcher.symlink_to(old_venv / "bin" / "hetu-stock")

    result = _run_installer(tmp_path, "--host", "codex", "--force")

    assert result.returncode == 0, result.stderr
    envs = _managed_envs(tmp_path)
    assert len(envs) == 1
    assert _launcher(tmp_path).resolve() == (envs[0] / "bin" / "hetu-stock").resolve()
    # The old layout stays on disk as the retained previous combo.
    assert old_venv.is_dir()
    record = json.loads(
        (managed_root / "installation.json").read_text(encoding="utf-8")
    )
    assert record["previous_env"] == str(old_venv)


def test_post_switch_failure_restores_previous_skill_and_combo(
    tmp_path: Path,
) -> None:
    """Post-switch failure must roll back the Skill too, not only the launcher.

    Without the Skill rollback the installation is left as "new Skill + old
    environment", an inconsistent combination.
    """
    repo_v1 = _versioned_repo_copy(tmp_path, "repo-v1", "version-1")
    first = _run_installer(
        tmp_path, "--host", "codex", installer=repo_v1 / "scripts" / "install.sh"
    )
    assert first.returncode == 0, first.stderr
    assert _installed_marker(tmp_path) == "version-1"
    envs_before = _managed_envs(tmp_path)
    previous_cli = envs_before[0] / "bin" / "hetu-stock"

    repo_v2 = _versioned_repo_copy(tmp_path, "repo-v2", "version-2")
    failed = _run_installer(
        tmp_path,
        "--host",
        "codex",
        "--force",
        installer=repo_v2 / "scripts" / "install.sh",
        env_updates={"FAKE_POST_SWITCH_FAIL": "1"},
    )

    assert failed.returncode != 0
    # The whole previous combination is back: Skill content, launcher, envs.
    assert _installed_marker(tmp_path) == "version-1"
    assert _launcher(tmp_path).resolve() == previous_cli.resolve()
    assert _managed_envs(tmp_path) == envs_before

    record = json.loads(
        (_managed_root(tmp_path) / "installation.json").read_text(encoding="utf-8")
    )
    assert record["state"] == "finalized"
    assert record["env"] == str(envs_before[0])
    assert record["version"] == "version-1"

    retried = _run_installer(
        tmp_path,
        "--host",
        "codex",
        "--force",
        installer=repo_v2 / "scripts" / "install.sh",
    )
    assert retried.returncode == 0, retried.stderr
    assert _installed_marker(tmp_path) == "version-2"
    record = json.loads(
        (_managed_root(tmp_path) / "installation.json").read_text(encoding="utf-8")
    )
    assert record["state"] == "finalized"
    assert _launcher(tmp_path).resolve() == (
        Path(record["env"]) / "bin" / "hetu-stock"
    ).resolve()


def test_prune_keeps_environment_referenced_by_combo_record(tmp_path: Path) -> None:
    """install.sh must not prune an environment the combo record still names."""
    first = _run_installer(tmp_path, "--host", "codex")
    assert first.returncode == 0, first.stderr
    second = _run_installer(tmp_path, "--host", "codex", "--force")
    assert second.returncode == 0, second.stderr
    envs_after_second = _managed_envs(tmp_path)
    assert len(envs_after_second) == 2
    recorded_current = envs_after_second[1]

    # Simulate a launcher-level rollback outside install.sh: the record still
    # names recorded_current as the current environment.
    launcher = _launcher(tmp_path)
    launcher.unlink()
    launcher.symlink_to(envs_after_second[0] / "bin" / "hetu-stock")

    third = _run_installer(tmp_path, "--host", "codex", "--force")
    assert third.returncode == 0, third.stderr
    assert recorded_current.is_dir(), (
        "environment referenced by the combo record was pruned"
    )


def test_interruption_after_record_write_recovers_on_rerun(tmp_path: Path) -> None:
    """The combo record is written right after the switch (before pruning).

    An interruption after the record write must leave a record that already
    describes the new combination, so rerunning the installer converges and
    the previous environment (rollback combo) is still pruned as retained.
    """
    repo_v1 = _versioned_repo_copy(tmp_path, "repo-v1", "version-1")
    first = _run_installer(
        tmp_path, "--host", "codex", installer=repo_v1 / "scripts" / "install.sh"
    )
    assert first.returncode == 0, first.stderr
    managed_root = _managed_root(tmp_path)
    first_env = _managed_envs(tmp_path)[0]

    repo_v2 = _versioned_repo_copy(tmp_path, "repo-v2", "version-2")
    failed = _run_installer(
        tmp_path,
        "--host",
        "codex",
        "--force",
        installer=repo_v2 / "scripts" / "install.sh",
        env_updates={"FAKE_RECORD_FAIL": "1"},
    )
    assert failed.returncode != 0
    # The record already names the new combination; the version markers make
    # the backup<->environment correspondence explicit for diagnose.
    record = json.loads(
        (managed_root / "installation.json").read_text(encoding="utf-8")
    )
    envs_now = _managed_envs(tmp_path)
    assert record["env"] == str(envs_now[-1])
    assert record["previous_env"] == str(first_env)
    assert record["version"] == "version-2"
    assert record["previous_version"] == "version-1"

    rerun = _run_installer(
        tmp_path,
        "--host",
        "codex",
        "--force",
        installer=repo_v2 / "scripts" / "install.sh",
    )
    assert rerun.returncode == 0, rerun.stderr
    record = json.loads(
        (managed_root / "installation.json").read_text(encoding="utf-8")
    )
    assert _launcher(tmp_path).resolve() == (
        Path(record["env"]) / "bin" / "hetu-stock"
    ).resolve()
    assert first_env.is_dir(), "previous combo environment must survive pruning"


def test_multi_host_installs_reuse_managed_environment(tmp_path: Path) -> None:
    """Installing additional hosts must reuse the existing managed helper
    environment instead of creating one per host: each new environment
    overwrites the single combo record and orphans earlier hosts' retained
    environments, which pruning then deletes."""
    repo_v1 = _versioned_repo_copy(tmp_path, "repo-v1", "version-1")
    for host in ("codex", "claude", "zcode"):
        result = _run_installer(
            tmp_path, "--host", host, installer=repo_v1 / "scripts" / "install.sh"
        )
        assert result.returncode == 0, result.stderr
    managed_root = _managed_root(tmp_path)
    assert len(_managed_envs(tmp_path)) == 1, (
        "additional host installs must reuse the managed environment"
    )
    shared_env = _managed_envs(tmp_path)[0]
    for host in ("codex", "claude", "zcode"):
        assert _skill_target(tmp_path, host).is_dir()

    repo_v2 = _versioned_repo_copy(tmp_path, "repo-v2", "version-2")
    update = _run_installer(
        tmp_path,
        "--host",
        "zcode",
        "--force",
        installer=repo_v2 / "scripts" / "install.sh",
    )
    assert update.returncode == 0, update.stderr
    # The update keeps the shared environment as the previous combo; earlier
    # hosts' Skills stay intact with their combination environment on disk.
    assert shared_env.is_dir()
    assert _skill_target(tmp_path, "codex").is_dir()
    record = json.loads(
        (managed_root / "installation.json").read_text(encoding="utf-8")
    )
    assert record["previous_env"] == str(shared_env)


def _block_launcher_dir(tmp_path: Path) -> Path:
    launcher_dir = tmp_path / "home/.local/bin"
    shutil.rmtree(launcher_dir)
    launcher_dir.write_text("blocked", encoding="utf-8")
    return launcher_dir


def test_failed_launcher_switch_restores_previous_skill_and_combo(
    tmp_path: Path,
) -> None:
    """A failure after the Skill was published but before the launcher
    switched must restore the previous Skill too — never claim "unchanged"
    while the Skill already moved to the new version."""
    repo_v1 = _versioned_repo_copy(tmp_path, "repo-v1", "version-1")
    first = _run_installer(
        tmp_path, "--host", "codex", installer=repo_v1 / "scripts" / "install.sh"
    )
    assert first.returncode == 0, first.stderr
    managed_root = _managed_root(tmp_path)
    first_env = _managed_envs(tmp_path)[0]

    repo_v2 = _versioned_repo_copy(tmp_path, "repo-v2", "version-2")
    _block_launcher_dir(tmp_path)
    failed = _run_installer(
        tmp_path,
        "--host",
        "codex",
        "--force",
        installer=repo_v2 / "scripts" / "install.sh",
    )
    assert failed.returncode != 0
    # The whole previous combination is back, not "Skill new + launcher old".
    assert _installed_marker(tmp_path) == "version-1"
    assert [path.name for path in _managed_envs(tmp_path)] == [first_env.name]
    record = json.loads(
        (managed_root / "installation.json").read_text(encoding="utf-8")
    )
    assert record["state"] == "finalized"
    assert record["env"] == str(first_env)


def test_interrupted_switch_before_launcher_completes_on_rerun(
    tmp_path: Path,
) -> None:
    """Skill published, interruption before the launcher moved (the persisted
    "switching" record covers this window): the next maintenance run finishes
    the combination from the record, and the record matches the result."""
    repo_v1 = _versioned_repo_copy(tmp_path, "repo-v1", "version-1")
    first = _run_installer(
        tmp_path, "--host", "codex", installer=repo_v1 / "scripts" / "install.sh"
    )
    assert first.returncode == 0, first.stderr
    first_env = _managed_envs(tmp_path)[0]

    repo_v2 = _versioned_repo_copy(tmp_path, "repo-v2", "version-2")
    failed = _run_installer(
        tmp_path,
        "--host",
        "codex",
        "--force",
        installer=repo_v2 / "scripts" / "install.sh",
        env_updates={"FAKE_SWITCH_FAIL": "1"},
    )
    assert failed.returncode != 0
    record = json.loads(
        (_managed_root(tmp_path) / "installation.json").read_text(encoding="utf-8")
    )
    assert record["state"] == "switching"
    assert record["version"] == "version-2"
    assert record["previous_env"] == str(first_env)
    # The Skill was already published; the launcher never moved.
    assert _installed_marker(tmp_path) == "version-2"
    assert _launcher(tmp_path).resolve() == (first_env / "bin" / "hetu-stock").resolve()

    resumed = _run_installer(
        tmp_path,
        "--host",
        "codex",
        "--force",
        installer=repo_v2 / "scripts" / "install.sh",
    )
    assert resumed.returncode == 0, resumed.stderr
    assert _installed_marker(tmp_path) == "version-2"
    record = json.loads(
        (_managed_root(tmp_path) / "installation.json").read_text(encoding="utf-8")
    )
    assert record["state"] == "finalized"
    assert record["version"] == "version-2"
    assert _launcher(tmp_path).resolve() == (
        Path(record["env"]) / "bin" / "hetu-stock"
    ).resolve()


def test_interrupted_switch_with_broken_env_rolls_back_then_recovers(
    tmp_path: Path,
) -> None:
    """Skill published, switch interrupted, new environment lost: the next
    maintenance run rolls the Skill back to the previous combination first,
    then performs the requested install cleanly."""
    repo_v1 = _versioned_repo_copy(tmp_path, "repo-v1", "version-1")
    first = _run_installer(
        tmp_path, "--host", "codex", installer=repo_v1 / "scripts" / "install.sh"
    )
    assert first.returncode == 0, first.stderr
    first_env = _managed_envs(tmp_path)[0]

    repo_v2 = _versioned_repo_copy(tmp_path, "repo-v2", "version-2")
    failed = _run_installer(
        tmp_path,
        "--host",
        "codex",
        "--force",
        installer=repo_v2 / "scripts" / "install.sh",
        env_updates={"FAKE_SWITCH_FAIL": "1"},
    )
    assert failed.returncode != 0
    record = json.loads(
        (_managed_root(tmp_path) / "installation.json").read_text(encoding="utf-8")
    )
    pending_env = Path(record["env"])
    assert record["state"] == "switching"

    shutil.rmtree(pending_env)
    rerun = _run_installer(
        tmp_path,
        "--host",
        "codex",
        "--force",
        installer=repo_v2 / "scripts" / "install.sh",
    )
    assert rerun.returncode == 0, rerun.stderr
    assert _installed_marker(tmp_path) == "version-2"
    record = json.loads(
        (_managed_root(tmp_path) / "installation.json").read_text(encoding="utf-8")
    )
    assert record["state"] == "finalized"
    assert record["previous_env"] == str(first_env)
    assert first_env.is_dir()


def test_multi_host_environment_survives_repeated_updates_of_one_host(
    tmp_path: Path,
) -> None:
    """Hosts installed but never updated keep their combination environment:
    reference files per host must survive another host updating repeatedly —
    recency of generations is not the retention rule."""
    repo_v1 = _versioned_repo_copy(tmp_path, "repo-v1", "version-1")
    for host in ("codex", "claude", "zcode"):
        result = _run_installer(
            tmp_path, "--host", host, installer=repo_v1 / "scripts" / "install.sh"
        )
        assert result.returncode == 0, result.stderr
    managed_root = _managed_root(tmp_path)
    shared_env = _managed_envs(tmp_path)[0]
    hosts_dir = managed_root / "hosts"
    assert (hosts_dir / "codex.json").is_file()
    assert (hosts_dir / "claude.json").is_file()
    assert (hosts_dir / "zcode.json").is_file()

    for index, marker in enumerate(("version-2", "version-3", "version-4"), start=2):
        repo = _versioned_repo_copy(tmp_path, f"repo-v{index}", marker)
        update = _run_installer(
            tmp_path,
            "--host",
            "zcode",
            "--force",
            installer=repo / "scripts" / "install.sh",
        )
        assert update.returncode == 0, update.stderr

    assert shared_env.is_dir(), "untouched hosts' combination environment was pruned"
    for host in ("codex", "claude", "zcode"):
        assert _skill_target(tmp_path, host).is_dir()
        ref = json.loads((hosts_dir / f"{host}.json").read_text(encoding="utf-8"))
        assert Path(ref["env"]).is_dir()
    record = json.loads(
        (managed_root / "installation.json").read_text(encoding="utf-8")
    )
    assert record["state"] == "finalized"
    assert record["version"] == "version-4"
