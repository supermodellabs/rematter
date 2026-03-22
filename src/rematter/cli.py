"""Typer CLI surface for rematter."""

from __future__ import annotations

from pathlib import Path

import typer
from typing_extensions import Annotated

from rematter._workers import (
    _filename_worker,
    _load_config,
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


@app.command()
def sync(
    source: Annotated[
        Path,
        typer.Argument(help="Source directory of markdown files"),
    ] = Path("."),
    dest: Annotated[
        Path | None,
        typer.Option("--dest", "-d", help="Destination directory for synced files"),
    ] = None,
    link_path_prefix: Annotated[
        str | None,
        typer.Option(
            "--link-path-prefix",
            "-l",
            help="URL path prefix for markdown links",
        ),
    ] = None,
    render: Annotated[
        bool | None,
        typer.Option(
            "--render",
            "-g",
            help="Render mermaid code blocks to SVG files",
        ),
    ] = None,
    recursive: Annotated[
        bool,
        typer.Option("--recursive", "-r", help="Recurse into subdirectories"),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Preview changes without writing"),
    ] = False,
) -> None:
    """🔁  Sync markdown files into an Astro content collection.

    Validates frontmatter schema, resolves wikilinks against the combined corpus
    of source and destination files, converts valid wikilinks to markdown links,
    and replaces broken wikilinks with plain text. Copies transformed files to
    [bold cyan]--dest[/]. Use [bold cyan]--render[/] to convert mermaid diagrams
    to SVG.
    """
    expanded_source = source.expanduser()

    try:
        config = _load_config(expanded_source)
    except FileNotFoundError:
        err_console.print(
            f"[bold red]❌  No config found in:[/] {expanded_source}. "
            "Create a '.rematter.yaml' file."
        )
        raise typer.Exit(code=1)

    # Resolve dest: CLI flag > config > error
    resolved_dest = dest
    if resolved_dest is None and config.dest is not None:
        resolved_dest = Path(config.dest)
    if resolved_dest is None:
        err_console.print(
            "[bold red]❌  No destination specified.[/] "
            "Use --dest or set 'dest' in .rematter.yaml."
        )
        raise typer.Exit(code=1)

    # Resolve link_path_prefix: CLI flag > config > error
    resolved_prefix = link_path_prefix
    if resolved_prefix is None:
        resolved_prefix = config.link_path_prefix
    if resolved_prefix is None:
        err_console.print(
            "[bold red]❌  No link path prefix specified.[/] "
            "Use --link-path-prefix or set 'link_path_prefix' in .rematter.yaml."
        )
        raise typer.Exit(code=1)

    # Resolve render: CLI flag > config > default False
    resolved_render = render
    if resolved_render is None:
        resolved_render = config.render
    if resolved_render is None:
        resolved_render = False

    _sync_run(
        expanded_source,
        resolved_dest.expanduser(),
        resolved_prefix,
        resolved_render,
        dry_run,
        recursive=recursive,
        media_config=config.media,
        ignore=config.ignore,
        no_sync_fields=config.no_sync_fields,
        schema=config.schema,
    )


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
            help="Path to schema YAML (default: <directory>/.rematter.yaml)",
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

    Reads [bold cyan].rematter.yaml[/] from the target directory (or
    [bold cyan]--schema[/]) and checks each file's frontmatter for missing fields,
    wrong types, and unrecognized properties. Use [bold cyan]--fix[/] to set default
    values for missing properties that define a default in the schema.
    """
    ignore: list[str] = []
    if schema is not None:
        # Explicit path — could be old _schema.yml or new .rematter.yaml
        try:
            schema_data = _load_schema(schema)
        except FileNotFoundError:
            err_console.print(f"[bold red]No schema found at:[/] {schema}")
            raise typer.Exit(code=1)
    else:
        try:
            config = _load_config(directory)
            schema_data = config.schema
            ignore = config.ignore
        except FileNotFoundError:
            err_console.print(
                f"[bold red]No config found in:[/] {directory}. "
                "Create a '.rematter.yaml' file."
            )
            raise typer.Exit(code=1)

    _run(
        directory, recursive, dry_run, _validate_worker,
        ignore=ignore, schema=schema_data, fix=fix,
    )


if __name__ == "__main__":
    app()
