from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

BLOCKED_NORMAL_MODULES = {
    "hetu_stock.workflow",
    "hetu_stock.report",
    "hetu_stock.config",
    "hetu_stock.models.workflow",
    "hetu_stock.models.thesis",
    "hetu_stock.models.evidence",
    "hetu_stock.workflow.engine",
    "hetu_stock.report.renderer",
    "hetu_stock.legacy_cli",
    "jinja2",
}

PHASE2_EVIDENCE_ROOTS = (Path("tests/product"), Path("tests/helpers"))
BLOCKED_PHASE2_TEST_IMPORTS = (
    "tests.frozen",
    "tests.legacy",
    "hetu_stock.config",
    "hetu_stock.models",
    "hetu_stock.report",
    "hetu_stock.workflow",
    "hetu_stock.legacy_cli",
)


def _imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    try:
        tests_index = path.parts.index("tests")
    except ValueError:
        package_parts: tuple[str, ...] = ()
    else:
        package_parts = path.parts[tests_index:-1]

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parents = node.level - 1
                retained = package_parts[: max(0, len(package_parts) - parents)]
                base_parts = (*retained, *(node.module or "").split("."))
            else:
                base_parts = tuple((node.module or "").split("."))
            base = ".".join(part for part in base_parts if part)
            if base:
                modules.add(base)
            modules.update(
                f"{base}.{alias.name}" if base else alias.name
                for alias in node.names
                if alias.name != "*"
            )
    return modules


@pytest.mark.parametrize(
    ("source", "blocked_module"),
    (
        ("from ...frozen import factories\n", "tests.frozen"),
        ("from hetu_stock import models\n", "hetu_stock.models"),
        ("from hetu_stock import config\n", "hetu_stock.config"),
        ("from hetu_stock import report\n", "hetu_stock.report"),
    ),
)
def test_import_guard_canonicalizes_relative_and_member_imports(
    tmp_path: Path,
    source: str,
    blocked_module: str,
) -> None:
    path = tmp_path / "tests/product/engineering/example.py"
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")

    modules = _imported_modules(path)

    assert any(
        module == blocked_module or module.startswith(f"{blocked_module}.")
        for module in modules
    )


def test_product_and_helper_evidence_excludes_frozen_business_surfaces() -> None:
    violations: list[str] = []
    for root in PHASE2_EVIDENCE_ROOTS:
        for path in sorted(root.rglob("*.py")):
            for module in sorted(_imported_modules(path)):
                if any(
                    module == blocked or module.startswith(f"{blocked}.")
                    for blocked in BLOCKED_PHASE2_TEST_IMPORTS
                ):
                    violations.append(f"{path.as_posix()}: {module}")

    assert violations == []


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--help"],
        ["skill", "validate", "skills/hetu-stock-analysis"],
        ["helper", "--help"],
        ["helper", "time-boundary", "--help"],
        ["helper", "authorization-check", "--help"],
    ],
    ids=(
        "root",
        "root-help",
        "skill-validate",
        "helper-help",
        "time-helper-help",
        "authorization-helper-help",
    ),
)
def test_normal_entrypoints_exclude_frozen_modules(argv: list[str]) -> None:
    probe = (
        "import json,sys; from typer.testing import CliRunner; "
        "from hetu_stock.cli import app; "
        "result=CliRunner().invoke(app,json.loads(sys.argv[1])); "
        "print(json.dumps({'exit_code':result.exit_code,'modules':sorted(sys.modules)}))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe, json.dumps(argv)],
        check=True,
        capture_output=True,
        text=True,
    )

    trace = json.loads(completed.stdout.splitlines()[-1])
    assert trace["exit_code"] == 0
    assert not BLOCKED_NORMAL_MODULES.intersection(trace["modules"])
