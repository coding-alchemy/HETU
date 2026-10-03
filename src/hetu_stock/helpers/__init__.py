import json
from pathlib import Path

import typer

app = typer.Typer(no_args_is_help=True)


@app.command("time-boundary")
def run_time_boundary(
    as_of: str = typer.Option(..., "--as-of"),
    published_at: str = typer.Option(..., "--published-at"),
    date_only: bool = typer.Option(False, "--date-only"),
    source_timezone: str = typer.Option("Asia/Shanghai", "--source-timezone"),
) -> None:
    try:
        from hetu_stock.helpers.time import evaluate_availability_json

        output = evaluate_availability_json(
            as_of=as_of,
            published_at=published_at,
            date_only=date_only,
            source_timezone=source_timezone,
        )
    except (ImportError, OSError, ValueError) as exc:
        typer.echo(f"time-boundary failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(output)


@app.command("authorization-check")
def run_authorization_check(
    registry: Path = typer.Option(..., "--registry"),  # noqa: B008
    request: Path = typer.Option(..., "--request"),  # noqa: B008
) -> None:
    try:
        from hetu_stock.helpers.authorization import evaluate_authorization_files

        output = evaluate_authorization_files(registry=registry, request=request)
    except (ImportError, OSError, ValueError) as exc:
        typer.echo(f"authorization-check failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(output)


@app.command("archive-inspect")
def run_archive_inspect(
    source: Path = typer.Option(..., "--source"),  # noqa: B008
) -> None:
    """Read-only inspection of a legacy (schema-version-3) run directory."""
    try:
        from hetu_stock.helpers.archive import inspect_archive

        output = inspect_archive(source)
    except (ImportError, OSError, ValueError) as exc:
        typer.echo(f"archive-inspect failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))


@app.command("archive-export")
def run_archive_export(
    source: Path = typer.Option(..., "--source"),  # noqa: B008
    output: Path = typer.Option(..., "--output"),  # noqa: B008
) -> None:
    """Safely export readable copies of a legacy run into a fresh directory."""
    try:
        from hetu_stock.helpers.archive import export_archive

        result = export_archive(source, output)
    except (ImportError, OSError, ValueError) as exc:
        typer.echo(f"archive-export failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
