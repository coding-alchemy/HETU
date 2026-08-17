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
