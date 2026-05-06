"""Typer CLI surface for rematter."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from rematter._workers import (
    _date_extract_worker,
    _fix_tables_worker,
    _load_config,
    _load_schema,
    _move_linked_dir,
    _reflow_worker,
    _run,
    _step_headings_worker,
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

utils_app = typer.Typer(
    name="utils",
    help="🛠️  Small markdown processing utilities.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
app.add_typer(utils_app, name="utils")


@utils_app.command("date-extract")
def date_extract(
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
    _run(directory, recursive, dry_run, _date_extract_worker, field=field)


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
    [bold cyan]--dest[/].
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

    _sync_run(
        expanded_source,
        resolved_dest.expanduser(),
        resolved_prefix,
        dry_run,
        recursive=recursive,
        media_config=config.media,
        ignore=config.ignore,
        no_sync_fields=config.no_sync_fields,
        schema=config.schema,
        extract_type_tags=config.extract_type_tags,
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
        directory,
        recursive,
        dry_run,
        _validate_worker,
        ignore=ignore,
        schema=schema_data,
        fix=fix,
    )


@utils_app.command("reflow")
def reflow(
    directory: Annotated[
        Path, typer.Argument(help="Directory containing markdown files")
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
    """🪡  Reflow hard-wrapped markdown into single-line paragraphs.

    Joins consecutive prose lines into one. Preserves frontmatter, fenced code
    blocks, headings, lists, blockquotes, tables, HTML blocks, and horizontal
    rules. Useful for cleaning up LLM-generated text that uses obsolete hard line
    wrapping.
    """
    _run(directory, recursive, dry_run, _reflow_worker)


_TABLE_STYLES = ("compact", "aligned")


@utils_app.command("fix-tables")
def fix_tables(
    directory: Annotated[
        Path, typer.Argument(help="Directory containing markdown files")
    ],
    style: Annotated[
        str,
        typer.Option(
            "--style",
            "-s",
            help="Table style: 'compact' (| a | b |) or 'aligned' (column-padded)",
        ),
    ] = "compact",
    recursive: Annotated[
        bool,
        typer.Option("--recursive", "-r", help="Recurse into subdirectories"),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Preview changes without writing"),
    ] = False,
) -> None:
    """📐  Reformat markdown tables to a consistent style.

    Fixes the most common LLM-generated table problem: missing inner padding
    around pipes ([italic]|foo|bar|[/] instead of [italic]| foo | bar |[/]). Tables
    inside fenced code blocks are left untouched.
    """
    if style not in _TABLE_STYLES:
        err_console.print(
            f"[bold red]❌  Unknown style:[/] {style!r}. "
            f"Expected one of: {', '.join(_TABLE_STYLES)}."
        )
        raise typer.Exit(code=2)
    _run(directory, recursive, dry_run, _fix_tables_worker, style=style)


@utils_app.command("step-headings")
def step_headings(
    directory: Annotated[
        Path, typer.Argument(help="Directory containing markdown files")
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
    """🪜  Pull skipped heading levels back to a stepwise sequence.

    Walks ATX (#) headings; if a heading skips ahead of its parent (e.g. h2 → h4),
    it's pulled up to parent_level + 1, and descendants shift up by the same delta.
    The top-level depth is preserved — if your highest heading is h2, it stays h2.
    Headings inside fenced code blocks and Setext (underline) headings are not
    touched.
    """
    _run(directory, recursive, dry_run, _step_headings_worker)


@utils_app.command("move-linked-dir")
def move_linked_dir(
    target: Annotated[
        Path,
        typer.Argument(
            help="Directory to move (relative to source, or absolute inside source)"
        ),
    ],
    source: Annotated[
        Path | None,
        typer.Option(
            "--from",
            "-f",
            help="Source anchor — the world the operation is aware of "
            "(defaults to current directory)",
        ),
    ] = None,
    to: Annotated[
        Path | None,
        typer.Option(
            "--to",
            "-t",
            help="New location for target, relative to source; "
            "omit to flatten target's contents into source",
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Preview changes without writing"),
    ] = False,
) -> None:
    """🚚  Move/rename a subdirectory and rewrite markdown links to its contents.

    The [bold cyan]--from[/] flag (default: pwd) sets the world the operation
    knows about — workers fan out across every [italic].md[/] file in it. Omit
    [bold cyan]--to[/] to flatten [italic]TARGET[/]'s contents into source.

    Bare wikilinks ([italic]\\[\\[note\\]\\][/]) resolve by filename in Obsidian
    and are left untouched. Only [italic]\\[label](path)[/] and
    [italic]!\\[alt](path)[/] links are rewritten.
    """
    result = _move_linked_dir(target, source=source, to=to, dry_run=dry_run)

    for err in result.errors:
        err_console.print(f"[bold red]❌  {err}[/]")
    if result.errors:
        raise typer.Exit(code=1)

    label = "Would move" if dry_run else "Moved"
    rewrite_label = "would rewrite" if dry_run else "rewrote"
    typer.echo(
        f"{label} {len(result.planned_moves)} file(s); "
        f"{rewrite_label} links in {len(result.rewritten_files)} file(s)."
    )


if __name__ == "__main__":
    app()
