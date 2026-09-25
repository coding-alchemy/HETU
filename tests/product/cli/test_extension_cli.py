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


# --- malformed registry fails in a controlled way (5-A2) -----------------------

_EXTENSION_ID = "x.demo.rules"


def _null_bindings(payload: dict) -> None:
    payload["bindings"] = None


def _host_binding_not_object(payload: dict) -> None:
    payload["bindings"]["codex"] = "0.1.0"


def _bare_string_binding(payload: dict) -> None:
    payload["bindings"]["codex"][_EXTENSION_ID] = "0.1.0"


def _binding_missing_version(payload: dict) -> None:
    payload["bindings"]["codex"][_EXTENSION_ID] = {"enabled_at": "2026-09-18T00:00:00Z"}


def _binding_version_wrong_type(payload: dict) -> None:
    payload["bindings"]["codex"][_EXTENSION_ID] = {"version": 3}


def _extensions_not_object(payload: dict) -> None:
    payload["extensions"] = []


def _entry_not_object(payload: dict) -> None:
    payload["extensions"][_EXTENSION_ID] = "broken"


def _versions_not_object(payload: dict) -> None:
    payload["extensions"][_EXTENSION_ID]["versions"] = []


def _version_record_not_object(payload: dict) -> None:
    payload["extensions"][_EXTENSION_ID]["versions"]["0.1.0"] = "broken"


def _empty_versions(payload: dict) -> None:
    payload["extensions"][_EXTENSION_ID]["versions"] = {}


def _version_capabilities_null(payload: dict) -> None:
    payload["extensions"][_EXTENSION_ID]["versions"]["0.1.0"]["capabilities"] = None


def _version_capabilities_not_list(payload: dict) -> None:
    payload["extensions"][_EXTENSION_ID]["versions"]["0.1.0"]["capabilities"] = (
        "local-files-read"
    )


def _version_provides_not_list(payload: dict) -> None:
    payload["extensions"][_EXTENSION_ID]["versions"]["0.1.0"]["provides"] = (
        "x.demo.rules"
    )


def _version_requires_not_list(payload: dict) -> None:
    payload["extensions"][_EXTENSION_ID]["versions"]["0.1.0"]["requires"] = "W0"


def _version_files_not_object(payload: dict) -> None:
    payload["extensions"][_EXTENSION_ID]["versions"]["0.1.0"]["files"] = ["a.md"]


def _version_files_digest_not_string(payload: dict) -> None:
    payload["extensions"][_EXTENSION_ID]["versions"]["0.1.0"]["files"] = {"a.md": 3}


def _version_path_not_string(payload: dict) -> None:
    payload["extensions"][_EXTENSION_ID]["versions"]["0.1.0"]["path"] = 3


def _version_summary_not_string(payload: dict) -> None:
    payload["extensions"][_EXTENSION_ID]["versions"]["0.1.0"]["summary"] = None


def _version_compatibility_not_string(payload: dict) -> None:
    payload["extensions"][_EXTENSION_ID]["versions"]["0.1.0"]["compatibility"] = 1


_MALFORMED_RECORD_FIELD_CASES = (
    (
        "capabilities_null",
        _version_capabilities_null,
        ["skill", "extension", "inspect", "--id", _EXTENSION_ID, "--json"],
        "capabilities",
    ),
    (
        "capabilities_not_list",
        _version_capabilities_not_list,
        ["skill", "extension", "context", "--host", "codex", "--json"],
        "capabilities",
    ),
    (
        "provides_not_list",
        _version_provides_not_list,
        ["skill", "extension", "list", "--json"],
        "provides",
    ),
    (
        "requires_not_list",
        _version_requires_not_list,
        ["skill", "extension", "list", "--json"],
        "requires",
    ),
    (
        "files_not_object",
        _version_files_not_object,
        ["skill", "extension", "list", "--json"],
        "files",
    ),
    (
        "files_digest_not_string",
        _version_files_digest_not_string,
        ["skill", "extension", "list", "--json"],
        "files",
    ),
    (
        "path_not_string",
        _version_path_not_string,
        ["skill", "extension", "context", "--host", "codex", "--json"],
        "path",
    ),
    (
        "summary_not_string",
        _version_summary_not_string,
        ["skill", "extension", "inspect", "--id", _EXTENSION_ID, "--json"],
        "summary",
    ),
    (
        "compatibility_not_string",
        _version_compatibility_not_string,
        ["skill", "extension", "enable", "--host", "codex", "--id", _EXTENSION_ID,
         "--json"],
        "compatibility",
    ),
)


_MALFORMED_REGISTRY_CASES = (
    ("bindings_null", _null_bindings, ["skill", "extension", "list", "--json"], "bindings"),
    (
        "host_binding_not_object",
        _host_binding_not_object,
        ["skill", "extension", "list", "--json"],
        "bindings.codex",
    ),
    (
        "bare_string_binding",
        _bare_string_binding,
        ["skill", "extension", "context", "--host", "codex", "--json"],
        "bindings.codex.x.demo.rules",
    ),
    (
        "binding_missing_version",
        _binding_missing_version,
        ["skill", "extension", "disable", "--host", "codex", "--id", _EXTENSION_ID, "--json"],
        "bindings.codex.x.demo.rules",
    ),
    (
        "binding_version_wrong_type",
        _binding_version_wrong_type,
        ["skill", "extension", "context", "--host", "codex", "--json"],
        "bindings.codex.x.demo.rules",
    ),
    (
        "extensions_not_object",
        _extensions_not_object,
        ["skill", "extension", "list", "--json"],
        "extensions",
    ),
    (
        "entry_not_object",
        _entry_not_object,
        ["skill", "extension", "list", "--json"],
        "extensions.x.demo.rules",
    ),
    (
        "versions_not_object",
        _versions_not_object,
        ["skill", "extension", "list", "--json"],
        "extensions.x.demo.rules.versions",
    ),
    (
        "version_record_not_object",
        _version_record_not_object,
        ["skill", "extension", "list", "--json"],
        "extensions.x.demo.rules.versions",
    ),
    (
        "empty_versions_rejected_before_default_selection",
        _empty_versions,
        ["skill", "extension", "enable", "--host", "codex", "--id", _EXTENSION_ID, "--json"],
        "extensions.x.demo.rules",
    ),
)


@pytest.mark.parametrize(
    ("mutate", "command_args", "location"),
    [case[1:] for case in (*_MALFORMED_REGISTRY_CASES, *_MALFORMED_RECORD_FIELD_CASES)],
    ids=[case[0] for case in (*_MALFORMED_REGISTRY_CASES, *_MALFORMED_RECORD_FIELD_CASES)],
)
def test_malformed_registry_fails_controlled_without_touching_registry(
    managed_home: Path, mutate, command_args: list, location: str
) -> None:
    """畸形登记表：exit 1、无 Traceback、含准确位置、登记表字节前后不变。"""
    _install(build_extension_package(managed_home / "candidate"))
    registry_path = (
        managed_home / "data" / "hetu-stock" / "extensions" / "registry.json"
    )
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    mutate(payload)
    registry_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    before = registry_path.read_bytes()
    result = runner.invoke(app, command_args)
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert registry_path.read_bytes() == before
    failure = json.loads(result.output)
    assert failure["completed"] is False
    assert location in failure["error"]


def test_malformed_version_record_rejects_update_before_any_write(
    managed_home: Path,
) -> None:
    """5-A2 复评缺口：版本记录字段畸形时 update 必须在写回前拒绝——
    不允许先改登记表再抛未捕获异常。"""
    _install(build_extension_package(managed_home / "candidate"))
    newer = build_extension_package(managed_home / "candidate-v2", version="0.2.0")
    registry_path = (
        managed_home / "data" / "hetu-stock" / "extensions" / "registry.json"
    )
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    payload["extensions"][_EXTENSION_ID]["versions"]["0.1.0"]["capabilities"] = None
    registry_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    before = registry_path.read_bytes()
    result = runner.invoke(
        app, ["skill", "extension", "update", "--source", str(newer), "--json"]
    )
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    failure = json.loads(result.output)
    assert failure["completed"] is False
    assert "capabilities" in failure["error"]
    assert registry_path.read_bytes() == before
    after = json.loads(registry_path.read_text(encoding="utf-8"))
    assert "0.2.0" not in after["extensions"][_EXTENSION_ID]["versions"]
