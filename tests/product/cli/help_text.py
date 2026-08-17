"""Parser for the Typer ``--help`` Commands section.

Extracted from ``tests/product/cli/test_command_tree.py`` so both the command
tree tests and the installer's lightweight-help check share one robust parser
instead of fragile substring matching. A forbidden group is detected by name
even if its description prose happens to contain an allowed word (and vice
versa): only command names actually rendered in the Commands section are
returned.

Two rendering modes are supported because ``rich`` is an optional Typer
extra and not a declared dependency of this project:

* rich table output (box-drawing borders ``│`` / ``╰``) when rich is
  importable in the host environment;
* Click plain-text output (two-space indented command rows) otherwise.

On an unrecognized format the parser returns an empty set and dependent
tests fail loudly (fail-safe by design); the recursive registered-command
enumeration test in ``test_command_tree.py`` provides an independent safety
net.
"""

from __future__ import annotations


def listed_root_commands(output: str) -> set[str]:
    """Return the command names listed in the ``--help`` Commands section."""
    cmds_section = output.split("Commands", 1)
    if len(cmds_section) < 2:
        return set()
    body = cmds_section[1]
    if "╰" in body:
        return _rich_table_names(body.split("╰", 1)[0])
    return _plain_text_names(body)


def _rich_table_names(body: str) -> set[str]:
    names: set[str] = set()
    for line in body.splitlines():
        stripped = line.lstrip()
        if not stripped.startswith("│"):
            continue
        inner = stripped[1:].strip()
        if not inner or inner.startswith("─"):
            continue
        token = inner.split()[0]
        if token and not token.startswith("│") and not token.startswith("╮"):
            names.add(token)
    return names


def _plain_text_names(body: str) -> set[str]:
    names: set[str] = set()
    seen_row = False
    for line in body.splitlines():
        if not line.strip():
            if seen_row:
                break
            continue
        # Skip the trailing punctuation of the "Commands" heading (Click
        # renders "Commands:" while rich renders "╭─ Commands ─..." and
        # never reaches this branch).
        if not seen_row and line.strip().strip(":─ ") == "":
            continue
        if not line.startswith("  "):
            break
        token = line.split()[0]
        if token.startswith("-"):
            break
        names.add(token)
        seen_row = True
    return names
