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

import json
from pathlib import Path
from typing import Any

import typer

from hetu_stock.helpers import app as helper_app
from hetu_stock.skill import (
    ExtensionError,
    HostTarget,
    SkillValidationError,
    context_for_host,
    default_user_skill_root,
    disable_extension,
    display_path,
    enable_extension,
    inspect_extension,
    inspect_installation,
    install_extension,
    install_skill,
    list_extensions,
    list_skill_backups,
    managed_env_owned,
    plan_uninstall,
    read_combo_association,
    rollback_skill,
    uninstall_extension,
    uninstall_skill,
    update_extension,
    update_host_ref,
    validate_extension,
    validate_skill_package,
    verify_skill_manifest,
)

app = typer.Typer(invoke_without_command=True)
skill_app = typer.Typer(no_args_is_help=True)
extension_app = typer.Typer(
    no_args_is_help=True,
    help="管理本地第三方工作包扩展（x.*）。校验、安装、按宿主自然语言启用、更新与卸载；"
    "这些管理命令不会启动股票分析。",
)
app.add_typer(skill_app, name="skill")
skill_app.add_typer(extension_app, name="extension")
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

    if destination is None:
        report = inspect_installation(destination_root)
        env_dir = (
            Path(report.launcher.target).parent.parent
            if report.launcher.state == "managed"
            else None
        )
        update_host_ref(
            host,
            env_dir,
            version=None,
            skill_target=target,
        )
    typer.echo(f"Skill installed to: {target}")


def _destination_root(host: HostTarget, destination: Path | None) -> Path:
    return destination if destination is not None else default_user_skill_root(host)


def _render_status(destination_root: Path, *, detailed: bool) -> None:
    report = inspect_installation(destination_root)
    typer.echo(f"Skill: {display_path(report.target)}")
    if report.target_state == "valid":
        typer.echo("Status: installed, package integrity OK")
    elif report.target_state == "invalid":
        typer.echo("Status: installed, package INTEGRITY FAILURE")
    else:
        typer.echo("Status: not installed")
    if detailed:
        if report.target_reason:
            typer.echo(f"Reason: {report.target_reason}")
        typer.echo(f"Maintenance record: {report.record_state}")
        launcher = report.launcher
        if launcher.state == "managed":
            typer.echo(f"Launcher: managed ({display_path(Path(launcher.target))})")
        else:
            typer.echo(f"Launcher: {launcher.state}")
        helper = report.helper
        if helper.state == "available":
            typer.echo("Helper environment: available")
        elif helper.state == "missing":
            typer.echo(f"Helper environment: missing ({helper.reason})")
        else:
            typer.echo("Helper environment: none")
    if report.backups:
        typer.echo("Backups:")
        for backup in report.backups:
            label = "complete" if backup.valid else f"INVALID ({backup.reason})"
            typer.echo(f"  {backup.name}: {label}")
    else:
        typer.echo("Backups: none")
    if detailed:
        typer.echo("Next actions:")
        for action in report.next_actions:
            typer.echo(f"  - {action}")


@skill_app.command("status")
def skill_status(
    host: HostTarget = typer.Option(...),  # noqa: B008
    destination: Path | None = typer.Option(None, "--destination"),  # noqa: B008
) -> None:
    """Show the installation state of the managed Skill (read-only)."""
    try:
        _render_status(_destination_root(host, destination), detailed=False)
    except OSError as exc:
        typer.echo(f"status failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@skill_app.command("diagnose")
def skill_diagnose(
    host: HostTarget = typer.Option(...),  # noqa: B008
    destination: Path | None = typer.Option(None, "--destination"),  # noqa: B008
) -> None:
    """Explain the installation state and list concrete next actions (read-only)."""
    try:
        _render_status(_destination_root(host, destination), detailed=True)
    except OSError as exc:
        typer.echo(f"diagnose failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@skill_app.command("rollback")
def skill_rollback(
    host: HostTarget = typer.Option(...),  # noqa: B008
    destination: Path | None = typer.Option(None, "--destination"),  # noqa: B008
    backup: Path | None = typer.Option(None, "--backup"),  # noqa: B008
) -> None:
    """Restore a complete backup over the current Skill, keeping the replaced version."""
    # Combo (launcher/environment) restore only applies to a default managed
    # installation; a custom --destination is not attributed to the combo.
    restore_combo = destination is None
    launcher_before = None
    combo_env: Path | None = None
    combo_recorded = False
    if restore_combo:
        destination_root = _destination_root(host, destination)
        launcher_before = inspect_installation(destination_root).launcher
        backups = [info for info in list_skill_backups(destination_root) if info.valid]
        chosen = next(
            (
                info
                for info in backups
                if backup is not None
                and (info.path == backup or info.package == backup)
            ),
            backups[-1] if backup is None and backups else None,
        )
        if chosen is not None:
            assoc = read_combo_association(chosen.path)
            combo_recorded = assoc is not None
            if assoc is not None and assoc.get("env"):
                combo_env = Path(str(assoc["env"]))
    try:
        target = rollback_skill(
            _destination_root(host, destination),
            backup=backup,
            restore_combo=restore_combo,
        )
    except (OSError, SkillValidationError) as exc:
        typer.echo(f"rollback failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Rolled back to: {target}")
    typer.echo("The replaced version remains registered as a backup.")
    if restore_combo and launcher_before is not None:
        launcher_after = inspect_installation(target.parent).launcher
        restored = (
            launcher_before.state == "managed"
            and launcher_after.state == "managed"
            and launcher_after.target != launcher_before.target
        )
        if restored:
            typer.echo(
                "Helper combo restored: the managed launcher now points at the "
                "environment recorded for the restored version."
            )
            update_host_ref(
                host,
                Path(launcher_after.target).parent.parent,
                skill_target=target,
            )
        elif combo_recorded and combo_env is None:
            # The backup explicitly recorded that it shipped without a
            # managed helper combination.
            typer.echo(
                "This backup has no helper combination recorded; only the "
                "Skill was rolled back."
            )
        elif launcher_before.state == "managed":
            typer.echo(
                "WARNING: helper combo left unchanged: the launcher still "
                "tracks the rolled-away environment. Restore it manually if "
                "needed."
            )


@skill_app.command("uninstall")
def skill_uninstall(
    host: HostTarget = typer.Option(...),  # noqa: B008
    destination: Path | None = typer.Option(None, "--destination"),  # noqa: B008
    yes: bool = typer.Option(False, "--yes"),  # noqa: B008
    remove_env: bool = typer.Option(False, "--remove-env"),  # noqa: B008
) -> None:
    """Remove the managed Skill; user files, other hosts, and shared data are kept."""
    destination_root = _destination_root(host, destination)
    scope = plan_uninstall(destination_root)
    typer.echo("Uninstall scope:")
    typer.echo(f"  Skill directory: {display_path(scope.target)}")
    for backup in scope.backups:
        typer.echo(f"  Backup {backup.name}: removed with the maintenance area")
    if scope.launcher.state == "managed" and not scope.other_host_targets:
        typer.echo(f"  Launcher: {display_path(scope.launcher.path)} (managed, removed)")
    elif scope.launcher.state == "managed":
        typer.echo(
            f"  Launcher: {display_path(scope.launcher.path)} (shared by other hosts, kept)"
        )
    else:
        typer.echo(f"  Launcher: {scope.launcher.state} (kept)")
    if scope.helper.current is not None:
        # Record-owned environments are ours to remove; an environment that
        # merely holds this package stays with its user data.
        if (
            remove_env
            and not scope.other_host_targets
            and managed_env_owned(scope.helper.current)
        ):
            typer.echo(f"  Environment: {display_path(scope.helper.current)} (removed)")
        else:
            typer.echo(f"  Environment: {display_path(scope.helper.current)} (kept)")
    else:
        typer.echo("  Environment: none")
    for note in scope.notes:
        typer.echo(f"  Note: {note}")
    if not yes:
        typer.echo("Nothing was removed. Rerun with --yes to perform the authorized deletion.")
        raise typer.Exit(code=2)

    try:
        uninstall_skill(
            destination_root,
            remove_env=remove_env,
            host=host if destination is None else None,
        )
    except (OSError, SkillValidationError) as exc:
        typer.echo(f"uninstall failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo("Uninstalled. Research, archived reports, authorization configuration,")
    typer.echo("other hosts, and unmanaged files were left untouched.")


# ---------------------------------------------------------------------------
# `skill extension` — Phase-5 stage 06 local third-party work-package extension
# management.  These commands are deterministic management only: they never
# start stock research, and they never print package bodies.
# ---------------------------------------------------------------------------


def _emit(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True))
        return
    for key in sorted(payload):
        value = payload[key]
        if isinstance(value, (dict, list)):
            typer.echo(f"{key}: {json.dumps(value, ensure_ascii=False, default=str)}")
        else:
            typer.echo(f"{key}: {value}")


def _extension_fail(exc: Exception, as_json: bool) -> None:
    if as_json:
        _emit({"completed": False, "error": str(exc)}, as_json=True)
    else:
        typer.echo(f"extension management failed: {exc}", err=True)
    raise typer.Exit(code=1) from exc


@extension_app.command("list")
def extension_list(as_json: bool = typer.Option(False, "--json")) -> None:
    """List installed extensions and per-host enablement bindings."""
    try:
        payload = list_extensions()
    except ExtensionError as exc:
        _extension_fail(exc, as_json)
    _emit(payload, as_json)


@extension_app.command("inspect")
def extension_inspect(
    extension_id: str = typer.Option(..., "--id"),
    version: str | None = typer.Option(None, "--version"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show registry metadata for an extension (never the package body)."""
    try:
        payload = inspect_extension(extension_id, version=version)
    except ExtensionError as exc:
        _extension_fail(exc, as_json)
    _emit(payload, as_json)


@extension_app.command("validate")
def extension_validate(
    source: Path = typer.Option(..., "--source"),  # noqa: B008
    as_json: bool = typer.Option(False, "--json"),  # noqa: B008
) -> None:
    """Validate a candidate extension package without installing it."""
    try:
        checked = validate_extension(source)
    except (ExtensionError, OSError) as exc:
        _extension_fail(exc, as_json)
    payload = {"valid": True, "completed": True, **checked}
    _emit(payload, as_json)


@extension_app.command("install")
def extension_install(
    source: Path = typer.Option(..., "--source"),  # noqa: B008
    as_json: bool = typer.Option(False, "--json"),  # noqa: B008
) -> None:
    """Install an extension version after validation.  Versions are read-only."""
    try:
        checked = install_extension(source)
    except (ExtensionError, OSError) as exc:
        _extension_fail(exc, as_json)
    _emit({"completed": True, **checked}, as_json)


@extension_app.command("update")
def extension_update(
    source: Path = typer.Option(..., "--source"),  # noqa: B008
    as_json: bool = typer.Option(False, "--json"),  # noqa: B008
) -> None:
    """Publish a newer version; existing bindings keep their old version."""
    try:
        changes = update_extension(source)
    except (ExtensionError, OSError) as exc:
        _extension_fail(exc, as_json)
    _emit({"completed": True, **changes}, as_json)


@extension_app.command("enable")
def extension_enable(
    host: HostTarget = typer.Option(...),  # noqa: B008
    extension_id: str = typer.Option(..., "--id"),
    version: str | None = typer.Option(None, "--version"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Bind an extension to a host (defaults to the newest version)."""
    try:
        binding = enable_extension(host, extension_id, version=version)
    except ExtensionError as exc:
        _extension_fail(exc, as_json)
    _emit(
        {
            "completed": True,
            "host": host,
            "id": extension_id,
            "version": binding["version"],
        },
        as_json,
    )


@extension_app.command("disable")
def extension_disable(
    host: HostTarget = typer.Option(...),  # noqa: B008
    extension_id: str = typer.Option(..., "--id"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Remove a host's binding for an extension."""
    try:
        binding = disable_extension(host, extension_id)
    except ExtensionError as exc:
        _extension_fail(exc, as_json)
    _emit(
        {
            "completed": True,
            "host": host,
            "id": extension_id,
            "version": binding["version"],
        },
        as_json,
    )


@extension_app.command("uninstall")
def extension_uninstall(
    extension_id: str = typer.Option(..., "--id"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Remove all versions; refused while any host binding remains."""
    try:
        uninstall_extension(extension_id)
    except ExtensionError as exc:
        _extension_fail(exc, as_json)
    _emit({"completed": True, "id": extension_id}, as_json)


@extension_app.command("context")
def extension_context(
    host: HostTarget = typer.Option(...),  # noqa: B008
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show what a host's next research session would see from extensions."""
    try:
        payload = context_for_host(host)
    except ExtensionError as exc:
        _extension_fail(exc, as_json)
    _emit({**payload, "completed": True}, as_json)
