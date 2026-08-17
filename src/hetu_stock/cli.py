"""Root command surface after the Phase-3 C3 legacy retirement.

This module is a lightweight assembler: it wires exactly two sub-apps to
the root Typer app - ``skill`` (canonical Agent Skill management) and
``helper`` (deterministic support commands). No Phase-1 workflow, report,
config, business-model or legacy import happens at module import time; the
import gate in ``tests/product/cli/test_command_tree.py`` asserts this.

The Phase-1 implementations and the read-only legacy surface were retired in
Phase-3 C3 and are retrievable only through Git history.
"""

from __future__ import annotations

from pathlib import Path

import typer

from hetu_stock.helpers import app as helper_app
from hetu_stock.skill import (
    HostTarget,
    SkillValidationError,
    default_user_skill_root,
    install_skill,
    validate_skill_package,
    verify_skill_manifest,
)

app = typer.Typer(invoke_without_command=True)
skill_app = typer.Typer(no_args_is_help=True)
app.add_typer(skill_app, name="skill")
app.add_typer(helper_app, name="helper")


@app.callback()
def root(ctx: typer.Context) -> None:
    """Show root help when no command group is selected."""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


@skill_app.command("validate")
def validate_skill(path: Path) -> None:
    try:
        validate_skill_package(path, require_manifest=True)
        verify_skill_manifest(path)
    except OSError as exc:
        typer.echo(f"skill path cannot be read: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except UnicodeError as exc:
        typer.echo(f"skill file is not valid UTF-8: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except SkillValidationError as exc:
        typer.echo(f"invalid skill package: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Skill package is valid: {path}")


@skill_app.command("install")
def install_skill_command(
    host: HostTarget = typer.Option(...),  # noqa: B008
    source: Path = typer.Option(Path("skills/hetu-stock-analysis"), "--source"),  # noqa: B008
    destination: Path | None = typer.Option(None, "--destination"),  # noqa: B008
    force: bool = typer.Option(False, "--force"),  # noqa: B008
) -> None:
    destination_root = destination if destination is not None else default_user_skill_root(host)
    try:
        target = install_skill(source, destination_root, force=force)
    except (OSError, SkillValidationError, FileExistsError) as exc:
        typer.echo(f"install failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Skill installed to: {target}")
