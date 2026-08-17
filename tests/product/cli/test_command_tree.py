"""Phase-3 C3: the post-legacy command tree.

These tests pin the closed root command surface: ``hetu-stock`` exposes only
``skill`` and ``helper`` at the root, the ``legacy`` group and every
historical state-write / resume / report-render leaf are unregistered, and
importing ``hetu_stock.cli`` never pulls the Phase-1 workflow / report /
config / old business-model stack.

The import gate runs in a fresh subprocess so it is independent of the
modules other tests in the same session may have already loaded.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.main import get_command
from typer.testing import CliRunner

from hetu_stock.cli import app
from tests.product.cli.help_text import listed_root_commands
from tests.product.cli.import_gate import (
    blocked_modules,
    fresh_process_command_loaded_modules,
    fresh_process_loaded_modules,
)

runner = CliRunner()

_ALLOWED_ROOT_COMMANDS = frozenset({"skill", "helper"})
_FORBIDDEN_ROOT_COMMANDS = frozenset(
    {"request", "run", "report", "schema", "analyze", "research", "legacy"}
)


def test_root_command_tree_is_closed() -> None:
    # Authoritative closed-surface assertion: the registered command objects,
    # independent of how any environment's Typer/rich renders ``--help``.
    root = get_command(app)
    assert set(root.commands) == _ALLOWED_ROOT_COMMANDS, (
        f"root command surface drifted: {sorted(root.commands)}"
    )
    # Visibility assertion: the rendered help must list the same commands.
    help_result = runner.invoke(app, ["--help"])
    assert help_result.exit_code == 0
    listed = listed_root_commands(help_result.output)
    if listed:
        assert listed == set(root.commands), (
            f"help output disagrees with registered commands: "
            f"{sorted(listed)} vs {sorted(root.commands)}"
        )
    else:
        # Unknown rendering format: fall back to word-level visibility so the
        # test never depends on box-drawing details, while the structural
        # assertion above still pins the closed surface.
        assert all(word in help_result.output for word in _ALLOWED_ROOT_COMMANDS)


def test_old_mutating_and_rendering_commands_are_not_registered() -> None:
    for argv in (
        ["run", "init"],
        ["run", "submit"],
        ["run", "resume"],
        ["report", "render"],
        ["legacy", "--help"],
        ["legacy", "run", "init"],
        ["legacy", "run", "submit"],
        ["legacy", "run", "resume"],
        ["legacy", "report", "render"],
        ["legacy", "request", "validate"],
        ["legacy", "run", "show"],
        ["legacy", "report", "validate"],
        ["legacy", "schema", "export"],
    ):
        assert runner.invoke(app, argv).exit_code != 0


def test_cli_module_no_longer_exposes_schema_helpers() -> None:
    # The historical cli.py exported SCHEMA_EXPORT_MODELS and
    # exported_model_schema; the current cli.py is a lean assembler and
    # must not re-expose them.
    import hetu_stock.cli as cli_module

    assert not hasattr(cli_module, "SCHEMA_EXPORT_MODELS")
    assert not hasattr(cli_module, "exported_model_schema")


def _collect_leaf_paths(group: object, prefix: str = "") -> set[str]:
    """Recursively walk a Typer/Click group and return every leaf command path.

    A leaf is a registered command object with no ``commands`` mapping of its
    own (a Click ``Command``, exposed by Typer as ``TyperCommand``). Groups
    (``click.Group`` / ``TyperGroup``) are descended into. Paths are
    space-joined from the root, e.g. ``"legacy run show"``.
    """
    leaves: set[str] = set()
    commands = getattr(group, "commands", None)
    if not commands:
        if prefix:
            leaves.add(prefix.rstrip())
        return leaves
    for name, sub in commands.items():
        leaves |= _collect_leaf_paths(sub, f"{prefix}{name} ")
    return leaves


def test_recursive_command_tree_has_exact_leaf_set() -> None:
    """The registered command objects expose exactly four leaf paths.

    Uses Typer/Click's registered command objects (via ``get_command`` and
    ``Group.commands``), not help-text substring inference, so a forbidden
    leaf cannot hide behind prose. The set is the closed post-C3 surface:
    two ``skill`` leaves and two ``helper`` leaves.
    """
    expected = {
        "skill validate",
        "skill install",
        "helper time-boundary",
        "helper authorization-check",
    }
    root = get_command(app)
    actual = _collect_leaf_paths(root)
    assert actual == expected, (
        f"command tree leaf set drifted.\n"
        f"  missing: {sorted(expected - actual)}\n"
        f"  extra:   {sorted(actual - expected)}"
    )


def test_importing_cli_does_not_load_blocked_modules() -> None:
    loaded = blocked_modules(fresh_process_loaded_modules())
    assert not loaded, sorted(loaded)


def test_blocked_module_contract_covers_every_legacy_model_submodule() -> None:
    candidates = {
        "hetu_stock.models",
        "hetu_stock.models.request",
        "hetu_stock.models._frozen",
        "hetu_stock.models.future_legacy_model",
    }

    assert blocked_modules(candidates) == candidates


@pytest.mark.parametrize(
    "argv",
    (
        ["--help"],
        ["skill", "validate", "skills/hetu-stock-analysis"],
        [
            "helper",
            "time-boundary",
            "--as-of",
            "2026-08-02T09:00:00+08:00",
            "--published-at",
            "2026-08-01T09:00:00+08:00",
        ],
        [
            "helper",
            "authorization-check",
            "--registry",
            "tests/helpers/fixtures/data_sources.yaml",
            "--request",
            "tests/helpers/fixtures/authorization_request.json",
        ],
    ),
    ids=("root help", "skill validate", "time helper", "authorization helper"),
)
def test_normal_command_execution_does_not_load_blocked_modules(
    argv: list[str],
) -> None:
    loaded = blocked_modules(fresh_process_command_loaded_modules(argv))
    assert not loaded, sorted(loaded)


def test_skill_install_execution_does_not_load_blocked_modules(
    tmp_path: Path,
) -> None:
    argv = [
        "skill",
        "install",
        "--host",
        "codex",
        "--source",
        "skills/hetu-stock-analysis",
        "--destination",
        str(tmp_path / "skills"),
    ]

    loaded = blocked_modules(fresh_process_command_loaded_modules(argv))

    assert not loaded, sorted(loaded)
