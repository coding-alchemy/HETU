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
        if path.is_file()
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
            print("fake hetu-stock help")
            raise SystemExit(0)

        if args[:2] == ["skill", "validate"] and len(args) == 3:
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
                shutil.rmtree(target)
            root.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, target)
            print(f"Skill installed to: {{target}}")
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
    assert "codex, claude, opencode" in result.stderr


def test_installs_managed_cli_and_codex_skill(tmp_path: Path) -> None:
    result = _run_installer(tmp_path, "--host", "codex")

    assert result.returncode == 0, result.stderr
    launcher = tmp_path / "home/.local/bin/hetu-stock"
    assert launcher.is_symlink()
    assert launcher.resolve() == tmp_path / "data root/hetu-stock/venv/bin/hetu-stock"
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
