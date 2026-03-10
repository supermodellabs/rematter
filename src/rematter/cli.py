"""Typer CLI surface for rematter."""

from __future__ import annotations

from pathlib import Path

import typer
from typing_extensions import Annotated

from rematter._workers import _filename_worker, _run, _transform_worker, err_console

app = typer.Typer(
    name="rematter",
    help="✨ Frontmatter transformation tool for Obsidian vaults.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


@app.command()
def filename(
    directory: Annotated[Path, typer.Argument(help="Directory containing markdown files")],
    field: Annotated[
        str,
        typer.Option("--field", "-f", help="Frontmatter field containing the ISO date"),
    ] = "Date",
    recursive: Annotated[
        bool,
        typer.Option("--recursive", "-r", help="Recurse into subdirectories"),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Preview changes without writing"),
    ] = False,
) -> None:
    """📅  Prepend a date frontmatter field to filenames and strip it from the file.

    Reads [bold cyan]--field[/] (default: [italic]Date[/]), parses it as an ISO date,
    prepends [italic]YYYY-MM-DD - [/] to the filename, removes the field from
    frontmatter, and writes the updated content. Strips frontmatter entirely when no
    fields remain. Files that already carry the date prefix are silently skipped.
    """
    _run(directory, recursive, dry_run, _filename_worker, field=field)


@app.command()
def transform(
    directory: Annotated[Path, typer.Argument(help="Directory containing markdown files")],
    field: Annotated[
        str,
        typer.Option("--field", "-f", help="Frontmatter field to rename"),
    ],
    to: Annotated[
        str,
        typer.Option("--to", "-t", help="New name for the field"),
    ],
    recursive: Annotated[
        bool,
        typer.Option("--recursive", "-r", help="Recurse into subdirectories"),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Preview changes without writing"),
    ] = False,
) -> None:
    """🔄  Rename a frontmatter field across all markdown files.

    Finds [bold cyan]--field[/] in each file's frontmatter and renames it to
    [bold cyan]--to[/]. Key order is preserved. If the target field already exists in
    a file, that file is skipped with an error rather than silently overwriting data.
    """
    if field == to:
        err_console.print(
            f"[bold red]❌  --field and --to are identical:[/] '{field}' — nothing to do."
        )
        raise typer.Exit(code=1)

    _run(directory, recursive, dry_run, _transform_worker, from_field=field, to_field=to)


if __name__ == "__main__":
    app()
