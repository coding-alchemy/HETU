"""Unit tests for the Commands-section parser in ``help_text``.

The parser must extract command names from both rendering modes: the rich
box-drawing table used when ``rich`` is importable and Click's plain-text
layout used in minimal environments (for example the CI install, where
``typer`` is installed without extras). Output format fixtures are pinned
here so a Typer rendering change is caught independently of whichever
environment happens to run the suite.
"""

from __future__ import annotations

from tests.product.cli.help_text import listed_root_commands

_RICH_TABLE_OUTPUT = """\
 Usage: hetu-stock [OPTIONS] COMMAND [ARGS]...

 Show root help when no command group is selected.

╭─ Commands ────────────────────────────────────────────────────────────╮
│ helper  Deterministic support commands available to Agent hosts       │
│ skill   Manage the canonical Agent Skill package                      │
╰───────────────────────────────────────────────────────────────────────╯
╭─ Options ─────────────────────────────────────────────────────────────╮
│ --help  Show this message and exit.                                  │
╰───────────────────────────────────────────────────────────────────────╯
"""

_PLAIN_TEXT_OUTPUT = """\
Usage: hetu-stock [OPTIONS] COMMAND [ARGS]...

Show root help when no command group is selected.

Commands:
  helper  Deterministic support commands available to Agent hosts
  skill   Manage the canonical Agent Skill package

Options:
  --help  Show this message and exit.
"""


def test_rich_table_commands_are_extracted() -> None:
    assert listed_root_commands(_RICH_TABLE_OUTPUT) == {"skill", "helper"}


def test_plain_text_commands_are_extracted() -> None:
    assert listed_root_commands(_PLAIN_TEXT_OUTPUT) == {"skill", "helper"}


def test_plain_text_parser_stops_at_options_section() -> None:
    plain = (
        "Commands\n"
        "  helper  Deterministic support commands\n"
        "\n"
        "Options\n"
        "  --install-completion  Install completion...\n"
        "  --help                Show this message and exit.\n"
    )
    assert listed_root_commands(plain) == {"helper"}


def test_unrecognized_format_returns_empty_set() -> None:
    assert listed_root_commands("no commands section here") == set()
