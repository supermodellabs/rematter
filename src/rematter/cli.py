"""Typer CLI surface for rematter."""

from __future__ import annotations

from pathlib import Path

import typer
from typing_extensions import Annotated

from rematter._workers import (
    _filename_worker,
    _load_schema,
    _run,
    _sync_run,
    _transform_worker,
    _validate_worker,
    err_console,
)

app = typer.Typer(
    name="rematter",
    help="✨ Frontmatter transformation tool for Obsidian vaults.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


@app.command()
def filename(
    directory: Annotated[
        Path, typer.Argument(help="Directory containing markdown files")
    ],
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
    directory: Annotated[
        Path, typer.Argument(help="Directory containing markdown files")
    ],
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

    _run(
        directory, recursive, dry_run, _transform_worker, from_field=field, to_field=to
    )


DEFAULT_DEST = "~/dev/winnie-sh/src/content/sky/"


@app.command()
def sync(
    source: Annotated[
        Path,
        typer.Argument(help="Source directory of markdown files"),
    ] = Path("."),
    dest: Annotated[
        Path,
        typer.Option("--dest", "-d", help="Destination directory for synced files"),
    ] = Path(DEFAULT_DEST),
    output_dir: Annotated[
        str,
        typer.Option("--output-dir", "-o", help="URL path prefix for markdown links"),
    ] = "/sky",
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Preview changes without writing"),
    ] = False,
) -> None:
    """🔁  Sync markdown files into an Astro content collection.

    Validates frontmatter schema, resolves wikilinks against the combined corpus
    of source and destination files, converts valid wikilinks to markdown links,
    and replaces broken wikilinks with plain text. Copies transformed files to
    [bold cyan]--dest[/].
    """
    _sync_run(source.expanduser(), dest.expanduser(), output_dir, dry_run)


@app.command()
def validate(
    directory: Annotated[
        Path, typer.Argument(help="Directory containing markdown files")
    ],
    schema: Annotated[
        Path | None,
        typer.Option(
            "--schema",
            "-s",
            help="Path to schema YAML (default: <directory>/_schema.yml)",
        ),
    ] = None,
    fix: Annotated[
        bool,
        typer.Option("--fix", help="Set default values for missing properties"),
    ] = False,
    recursive: Annotated[
        bool,
        typer.Option("--recursive", "-r", help="Recurse into subdirectories"),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Preview changes without writing"),
    ] = False,
) -> None:
    """Validate markdown frontmatter against a schema.

    Reads [bold cyan]_schema.yml[/] from the target directory (or [bold cyan]--schema[/])
    and checks each file's frontmatter for missing fields, wrong types, and
    unrecognized properties. Use [bold cyan]--fix[/] to set default values for
    missing properties that define a default in the schema.
    """
    schema_path = schema or (directory / "_schema.yml")
    try:
        schema_data = _load_schema(schema_path)
    except FileNotFoundError:
        err_console.print(f"[bold red]No schema found at:[/] {schema_path}")
        raise typer.Exit(code=1)

    _run(directory, recursive, dry_run, _validate_worker, schema=schema_data, fix=fix)


if __name__ == "__main__":
    app()
