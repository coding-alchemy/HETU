"""Phase 5 stage 06.1/06.2: `skill extension` CLI surface.

Readable output and --json carry the same facts: actual target, version,
enablement, completion state, and error reason.  Management commands never
start research and never print package bodies.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from hetu_stock.cli import app
from tests.product.skill.extension_fixtures import build_extension_package

runner = CliRunner()


@pytest.fixture()
def managed_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("HOME", raising=False)
    return tmp_path


def _install(source: Path) -> None:
    result = runner.invoke(
        app, ["skill", "extension", "install", "--source", str(source)]
    )
    assert result.exit_code == 0, result.output


def test_install_and_list_json(managed_home: Path) -> None:
    source = build_extension_package(managed_home / "candidate")
    _install(source)
    result = runner.invoke(app, ["skill", "extension", "list", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["extensions"]["x.demo.rules"]["versions"] == ["0.1.0"]
    assert payload["bindings"]["codex"] == {}


def test_validate_command_reports_checked_summary(managed_home: Path) -> None:
    source = build_extension_package(managed_home / "candidate")
    result = runner.invoke(
        app, ["skill", "extension", "validate", "--source", str(source), "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["id"] == "x.demo.rules"
    assert payload["valid"] is True


def test_validate_command_rejects_script_package(managed_home: Path) -> None:
    source = build_extension_package(
        managed_home / "candidate", references=("references/tool.py",)
    )
    (source / "references" / "tool.py").write_text("print('x')\n", encoding="utf-8")
    result = runner.invoke(
        app, ["skill", "extension", "validate", "--source", str(source), "--json"]
    )
    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert payload["completed"] is False
    assert "脚本" in payload["error"] or "代码" in payload["error"]


def test_inspect_shows_metadata_without_body(managed_home: Path) -> None:
    source = build_extension_package(managed_home / "candidate")
    _install(source)
    result = runner.invoke(
        app, ["skill", "extension", "inspect", "--id", "x.demo.rules", "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["id"] == "x.demo.rules"
    assert payload["version"] == "0.1.0"
    assert payload["location"]
    assert "演示扩展" in payload["summary"]
    assert "## 研究目标" not in result.output


def test_enable_disable_per_host(managed_home: Path) -> None:
    source = build_extension_package(managed_home / "candidate")
    _install(source)
    enabled = runner.invoke(
        app,
        ["skill", "extension", "enable", "--host", "claude", "--id", "x.demo.rules"],
    )
    assert enabled.exit_code == 0, enabled.output
    assert "claude" in enabled.output
    assert "x.demo.rules" in enabled.output

    listed = runner.invoke(app, ["skill", "extension", "list", "--json"])
    payload = json.loads(listed.output)
    assert payload["bindings"]["claude"]["x.demo.rules"]["version"] == "0.1.0"
    assert payload["bindings"]["codex"] == {}

    disabled = runner.invoke(
        app,
        ["skill", "extension", "disable", "--host", "claude", "--id", "x.demo.rules"],
    )
    assert disabled.exit_code == 0, disabled.output
    payload = json.loads(runner.invoke(app, ["skill", "extension", "list", "--json"]).output)
    assert payload["bindings"]["claude"] == {}


def test_update_reports_changes_and_keeps_binding(managed_home: Path) -> None:
    source = build_extension_package(managed_home / "candidate")
    _install(source)
    runner.invoke(
        app,
        ["skill", "extension", "enable", "--host", "codex", "--id", "x.demo.rules"],
    )
    newer = build_extension_package(
        managed_home / "candidate-v2", version="0.2.0", summary="第二版规则"
    )
    updated = runner.invoke(
        app, ["skill", "extension", "update", "--source", str(newer)]
    )
    assert updated.exit_code == 0, updated.output
    assert "0.1.0" in updated.output and "0.2.0" in updated.output
    payload = json.loads(runner.invoke(app, ["skill", "extension", "list", "--json"]).output)
    assert payload["extensions"]["x.demo.rules"]["versions"] == ["0.1.0", "0.2.0"]
    assert payload["bindings"]["codex"]["x.demo.rules"]["version"] == "0.1.0"


def test_uninstall_blocked_while_other_host_binds(managed_home: Path) -> None:
    source = build_extension_package(managed_home / "candidate")
    _install(source)
    runner.invoke(
        app,
        ["skill", "extension", "enable", "--host", "codex", "--id", "x.demo.rules"],
    )
    result = runner.invoke(
        app, ["skill", "extension", "uninstall", "--id", "x.demo.rules", "--json"]
    )
    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert payload["completed"] is False
    assert "codex" in payload["error"]
    runner.invoke(
        app,
        ["skill", "extension", "disable", "--host", "codex", "--id", "x.demo.rules"],
    )
    removed = runner.invoke(
        app, ["skill", "extension", "uninstall", "--id", "x.demo.rules"]
    )
    assert removed.exit_code == 0, removed.output


def test_context_command_per_host(managed_home: Path) -> None:
    source = build_extension_package(managed_home / "candidate")
    _install(source)
    empty = runner.invoke(
        app, ["skill", "extension", "context", "--host", "codex", "--json"]
    )
    assert empty.exit_code == 0, empty.output
    assert json.loads(empty.output)["candidates"] == []

    runner.invoke(
        app,
        ["skill", "extension", "enable", "--host", "codex", "--id", "x.demo.rules"],
    )
    context = runner.invoke(
        app, ["skill", "extension", "context", "--host", "codex", "--json"]
    )
    payload = json.loads(context.output)
    assert [c["id"] for c in payload["candidates"]] == ["x.demo.rules"]
    other = json.loads(
        runner.invoke(
            app, ["skill", "extension", "context", "--host", "zcode", "--json"]
        ).output
    )
    assert other["candidates"] == []


def test_extension_help_mentions_natural_language_management(managed_home: Path) -> None:
    result = runner.invoke(app, ["skill", "extension", "--help"])
    assert result.exit_code == 0
    assert "自然语言" in result.output or "宿主" in result.output
