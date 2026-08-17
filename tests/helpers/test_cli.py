import builtins
import subprocess
import sys
from collections.abc import Mapping, Sequence
from types import ModuleType

import pytest
from typer.testing import CliRunner

from hetu_stock.helpers import app

runner = CliRunner()


def _block_helper_import(monkeypatch: pytest.MonkeyPatch, module_name: str) -> None:
    original_import = builtins.__import__

    def import_with_targeted_failure(
        name: str,
        globals: Mapping[str, object] | None = None,
        locals: Mapping[str, object] | None = None,
        fromlist: Sequence[str] | None = None,
        level: int = 0,
    ) -> ModuleType:
        if name == module_name:
            raise ImportError(f"{module_name} unavailable for test")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", import_with_targeted_failure)


def test_helper_help_does_not_import_implementations() -> None:
    sys.modules.pop("hetu_stock.helpers.time", None)
    sys.modules.pop("hetu_stock.helpers.authorization", None)

    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "time-boundary" in result.output
    assert "authorization-check" in result.output
    assert "hetu_stock.helpers.time" not in sys.modules
    assert "hetu_stock.helpers.authorization" not in sys.modules


def test_unknown_helper_fails_without_loading_old_control_plane() -> None:
    for forbidden in ("hetu_stock.workflow", "hetu_stock.report", "jinja2"):
        sys.modules.pop(forbidden, None)

    result = runner.invoke(app, ["missing"])
    assert result.exit_code != 0
    for forbidden in ("hetu_stock.workflow", "hetu_stock.report", "jinja2"):
        assert forbidden not in sys.modules


def test_time_boundary_reports_import_failure_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        sys.modules,
        "hetu_stock.helpers.time",
        ModuleType("hetu_stock.helpers.time"),
    )
    _block_helper_import(monkeypatch, "hetu_stock.helpers.time")

    result = runner.invoke(
        app,
        [
            "time-boundary",
            "--as-of",
            "2026-08-02T09:00:00+08:00",
            "--published-at",
            "2026-08-01T09:00:00+08:00",
        ],
    )

    assert result.exit_code == 1
    assert result.stderr.startswith("time-boundary failed:")
    assert "Traceback" not in result.output


def test_authorization_check_reports_import_failure_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        sys.modules,
        "hetu_stock.helpers.authorization",
        ModuleType("hetu_stock.helpers.authorization"),
    )
    _block_helper_import(monkeypatch, "hetu_stock.helpers.authorization")

    result = runner.invoke(
        app,
        [
            "authorization-check",
            "--registry",
            "registry.yaml",
            "--request",
            "request.json",
        ],
    )

    assert result.exit_code == 1
    assert result.stderr.startswith("authorization-check failed:")
    assert "Traceback" not in result.output


@pytest.mark.parametrize(
    ("arguments", "error_field"),
    [
        (
            [
                "--as-of",
                "",
                "--published-at",
                "2026-07-27T09:30:00+08:00",
            ],
            "as_of",
        ),
        (
            [
                "--as-of",
                "2026-07-27T10:04:35",
                "--published-at",
                "2026-07-27T09:30:00+08:00",
            ],
            "as_of",
        ),
        (
            [
                "--as-of",
                "2026-07-27T10:04:35+08:00",
                "--published-at",
                "not-a-timestamp",
            ],
            "published_at",
        ),
        (
            [
                "--as-of",
                "2026-07-27T10:04:35+08:00",
                "--published-at",
                "not-a-date",
                "--date-only",
            ],
            "published_at",
        ),
        (
            [
                "--as-of",
                "2026-07-27T10:04:35+08:00",
                "--published-at",
                "2026-07-27T09:30:00+08:00",
                "--source-timezone",
                "Invalid/Phase2",
            ],
            "source_timezone",
        ),
    ],
)
def test_time_boundary_rejects_malformed_input_without_traceback(
    arguments: list[str], error_field: str
) -> None:
    result = runner.invoke(app, ["time-boundary", *arguments])

    assert result.exit_code != 0
    assert f"time-boundary failed: {error_field}" in result.stderr
    assert "Traceback" not in result.output


@pytest.mark.parametrize(
    ("arguments", "expected_message", "canary"),
    [
        (
            [
                "--as-of",
                "CANARY-AS-OF",
                "--published-at",
                "2026-07-27T09:30:00+08:00",
            ],
            "as_of is invalid",
            "CANARY-AS-OF",
        ),
        (
            [
                "--as-of",
                "2026-07-27T10:04:35+08:00",
                "--published-at",
                "CANARY-PUBLISHED-AT",
            ],
            "published_at is invalid",
            "CANARY-PUBLISHED-AT",
        ),
    ],
)
def test_time_boundary_parse_errors_do_not_echo_input(
    arguments: list[str],
    expected_message: str,
    canary: str,
) -> None:
    result = runner.invoke(app, ["time-boundary", *arguments])

    assert result.exit_code == 1
    assert result.stderr == f"time-boundary failed: {expected_message}\n"
    assert canary not in result.output
    assert "Traceback" not in result.output


def test_time_boundary_date_without_date_only_flag_has_actionable_error() -> None:
    result = runner.invoke(
        app,
        [
            "time-boundary",
            "--as-of",
            "2026-07-27T10:04:35+08:00",
            "--published-at",
            "2026-07-27",
        ],
    )

    assert result.exit_code == 1
    assert result.stderr == (
        "time-boundary failed: published_at is date-only; pass --date-only\n"
    )
    assert "Traceback" not in result.output


@pytest.mark.parametrize(
    ("arguments", "error_field"),
    [
        (
            [
                "--as-of",
                "0001-01-01T00:00:00+14:00",
                "--published-at",
                "2026-07-27T09:30:00+08:00",
            ],
            "as_of",
        ),
        (
            [
                "--as-of",
                "9999-12-31T23:59:59-14:00",
                "--published-at",
                "2026-07-27T09:30:00+08:00",
            ],
            "as_of",
        ),
        (
            [
                "--as-of",
                "2026-07-27T10:04:35+08:00",
                "--published-at",
                "0001-01-01T00:00:00+14:00",
            ],
            "published_at",
        ),
        (
            [
                "--as-of",
                "2026-07-27T10:04:35+08:00",
                "--published-at",
                "9999-12-31T23:59:59-14:00",
            ],
            "published_at",
        ),
    ],
)
def test_time_boundary_contains_timezone_conversion_overflow(
    arguments: list[str],
    error_field: str,
) -> None:
    result = runner.invoke(app, ["time-boundary", *arguments])

    assert result.exit_code == 1
    assert result.stderr == (
        f"time-boundary failed: {error_field} is outside the supported "
        "timezone conversion range\n"
    )
    assert "Traceback" not in result.output


def test_time_boundary_pydantic_import_failure_is_dependency_local() -> None:
    script = """
import builtins
from typer.testing import CliRunner
from hetu_stock.helpers import app

original_import = builtins.__import__

def import_without_pydantic(name, globals=None, locals=None, fromlist=(), level=0):
    if name == \"pydantic\" or name.startswith(\"pydantic.\"):
        raise ImportError(\"pydantic unavailable for test\")
    return original_import(name, globals, locals, fromlist, level)

builtins.__import__ = import_without_pydantic
runner = CliRunner()
checks = [
    (
        [
            \"time-boundary\",
            \"--as-of\",
            \"2026-08-02T09:00:00+08:00\",
            \"--published-at\",
            \"2026-08-01T09:00:00+08:00\",
        ],
        1,
    ),
    ([\"--help\"], 0),
    ([\"authorization-check\", \"--help\"], 0),
]
for arguments, expected_exit_code in checks:
    result = runner.invoke(app, arguments)
    assert result.exit_code == expected_exit_code, result.output
    assert \"Traceback\" not in result.output
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_authorization_yaml_dependency_failure_is_local() -> None:
    authorization_script = """
import builtins
import sys

original_import = builtins.__import__

def import_without_yaml(name, globals=None, locals=None, fromlist=(), level=0):
    if name == \"yaml\" or name.startswith(\"yaml.\"):
        raise ImportError(\"yaml unavailable for test\")
    return original_import(name, globals, locals, fromlist, level)

builtins.__import__ = import_without_yaml
assert \"yaml\" not in sys.modules
from typer.testing import CliRunner
from hetu_stock.helpers import app
assert \"yaml\" not in sys.modules
result = CliRunner().invoke(
    app,
    [
        \"authorization-check\",
        \"--registry\",
        \"tests/helpers/fixtures/data_sources.yaml\",
        \"--request\",
        \"tests/helpers/fixtures/authorization_request.json\",
    ],
)
assert result.exit_code == 1, result.output
assert result.stderr.startswith(\"authorization-check failed:\")
assert \"Traceback\" not in result.output
for forbidden in (\"hetu_stock.workflow\", \"hetu_stock.report\", \"jinja2\"):
    assert forbidden not in sys.modules
"""
    help_script = """
import builtins
import sys

original_import = builtins.__import__

def import_without_yaml(name, globals=None, locals=None, fromlist=(), level=0):
    if name == \"yaml\" or name.startswith(\"yaml.\"):
        raise ImportError(\"yaml unavailable for test\")
    return original_import(name, globals, locals, fromlist, level)

builtins.__import__ = import_without_yaml
assert \"yaml\" not in sys.modules
from typer.testing import CliRunner
from hetu_stock.helpers import app
assert \"yaml\" not in sys.modules
result = CliRunner().invoke(app, [\"--help\"])
assert result.exit_code == 0, result.output
for forbidden in (\"hetu_stock.workflow\", \"hetu_stock.report\", \"jinja2\"):
    assert forbidden not in sys.modules
"""
    time_script = """
import builtins
import sys

original_import = builtins.__import__

def import_without_yaml(name, globals=None, locals=None, fromlist=(), level=0):
    if name == \"yaml\" or name.startswith(\"yaml.\"):
        raise ImportError(\"yaml unavailable for test\")
    return original_import(name, globals, locals, fromlist, level)

builtins.__import__ = import_without_yaml
assert \"yaml\" not in sys.modules
from typer.testing import CliRunner
from hetu_stock.helpers import app
assert \"yaml\" not in sys.modules
result = CliRunner().invoke(
    app,
    [
        \"time-boundary\",
        \"--as-of\",
        \"2026-08-02T09:00:00+08:00\",
        \"--published-at\",
        \"2026-08-01T09:00:00+08:00\",
    ],
)
assert result.exit_code == 0, result.output
for forbidden in (\"hetu_stock.workflow\", \"hetu_stock.report\", \"jinja2\"):
    assert forbidden not in sys.modules
"""

    for script in (authorization_script, help_script, time_script):
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
