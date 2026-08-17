"""Fresh-process import gate for ``hetu_stock.cli``.

Extracted from the four Phase-2 Plan 03 CLI closure tests
(``tests/product/cli/test_run_cli.py``, ``tests/product/cli/test_schema_cli.py``,
``tests/product/cli/test_request_cli.py`` and ``tests/product/cli/test_command_tree.py``)
which each carried an identical copy of the blocked-set and the fresh-interpreter
subprocess probe. Keeping the probe here lets every consumer assert against the
same blocked set and the same subprocess mechanism; each test file still owns
its own thin ``test_importing_cli_does_not_load_blocked_modules`` function
calling :func:`fresh_process_loaded_modules` (the layering is intentional).

The probe runs in a fresh subprocess so it is independent of the modules other
tests in the same session may have already loaded.

``REPO_ROOT`` is computed from *this* module's location
(``tests/product/cli/import_gate.py`` -> ``parents[3]``) so it is correct for
every consumer regardless of how deep that consumer sits in the tree.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

BLOCKED_MODULES = frozenset(
    {
        "hetu_stock.workflow",
        "hetu_stock.report",
        "hetu_stock.config",
        "hetu_stock.models",
        "hetu_stock.legacy_cli",
        "jinja2",
    }
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def blocked_modules(loaded_modules: set[str]) -> set[str]:
    """Return blocked modules, treating each contract entry as a prefix."""
    return {
        module
        for module in loaded_modules
        if any(
            module == blocked or module.startswith(f"{blocked}.")
            for blocked in BLOCKED_MODULES
        )
    }


def fresh_process_loaded_modules() -> set[str]:
    """Import ``hetu_stock.cli`` in a fresh interpreter and return sys.modules."""
    script = (
        "import json, sys; "
        "import hetu_stock.cli; "  # noqa: FLY002
        "print('<<<MODULES>>>'); "
        "print(json.dumps(sorted(sys.modules)))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        check=True,
    )
    payload = result.stdout.split("<<<MODULES>>>", 1)[1].strip()
    return set(json.loads(payload))


def fresh_process_command_loaded_modules(argv: list[str]) -> set[str]:
    """Execute one root command in a fresh interpreter and return sys.modules."""
    script = (
        "import json, sys; "
        "from typer.testing import CliRunner; "
        "from hetu_stock.cli import app; "
        f"result = CliRunner().invoke(app, {argv!r}); "
        "assert result.exit_code == 0, result.output; "
        "print('<<<MODULES>>>'); "
        "print(json.dumps(sorted(sys.modules)))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        check=True,
    )
    payload = result.stdout.split("<<<MODULES>>>", 1)[1].strip()
    return set(json.loads(payload))
