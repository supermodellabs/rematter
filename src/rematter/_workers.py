"""Worker functions and the shared _run dispatcher."""

from __future__ import annotations

import concurrent.futures
import os
from collections.abc import Callable
from datetime import date, datetime
from functools import partial
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from rematter._core import DATE_PREFIX_RE, _dump, _load

console = Console()
err_console = Console(stderr=True)

# ── types ──────────────────────────────────────────────────────────────────────

Status = str  # "done" | "dry-run" | "skip" | "error"
Result = tuple[Status, str]
WorkerFn = Callable[..., Result]


# ── shared runner ──────────────────────────────────────────────────────────────


def _run(
    directory: Path,
    recursive: bool,
    dry_run: bool,
    worker: WorkerFn,
    **kwargs: Any,
) -> None:
    """Discover markdown files and fan out to worker via a thread pool."""
    if not directory.is_dir():
        err_console.print(f"[bold red]❌  Not a directory:[/] {directory}")
        raise typer.Exit(code=1)

    pattern = "**/*.md" if recursive else "*.md"
    files = sorted(directory.glob(pattern))

    if not files:
        console.print("[yellow]🤷  No .md files found — nothing to do.[/]")
        raise typer.Exit(code=0)

    if dry_run:
        console.print("[dim italic]🔍  Dry run — no files will be modified.[/]\n")

    fn = partial(worker, dry_run=dry_run, **kwargs)
    num_workers = min(len(files), os.cpu_count() or 4)

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as pool:
        results: list[Result] = list(pool.map(fn, files))

    done = skipped = errors = 0

    for status, msg in results:
        match status:
            case "done":
                console.print(f"[green]✅[/]  {msg}")
                done += 1
            case "dry-run":
                console.print(f"[cyan]🔍[/]  {msg}")
                done += 1
            case "skip":
                skipped += 1
            case "error":
                err_console.print(f"[bold red]❌[/]  {msg}")
                errors += 1

    label = "would process" if dry_run else "processed"
    console.print()
    console.print(
        f"[bold]Done:[/] {label} [green]{done}[/] · "
        f"skipped [dim]{skipped}[/] · "
        f"errors [{'red' if errors else 'dim'}]{errors}[/]"
    )

    if errors:
        raise typer.Exit(code=1)


# ── filename worker ────────────────────────────────────────────────────────────


def _filename_worker(path: Path, *, field: str, dry_run: bool) -> Result:
    if DATE_PREFIX_RE.match(path.name):
        return "skip", path.name

    parsed = _load(path)
    if parsed is None:
        return "skip", path.name

    fm, body = parsed

    if field not in fm:
        return "skip", path.name

    raw = fm[field]
    if isinstance(raw, datetime):  # must check datetime before date (it's a subclass)
        d = raw.date()
    elif isinstance(raw, date):
        d = raw
    else:
        raw_str = str(raw).strip()
        try:
            d = date.fromisoformat(raw_str)
        except ValueError:
            try:
                # Handles datetime strings like "2026-02-12 15:03" that PyYAML returns
                # as strings rather than parsing into datetime objects (Python 3.11+)
                d = datetime.fromisoformat(raw_str).date()
            except ValueError:
                return "error", f"invalid date '{raw}' in {path.name}"

    new_name = f"{d} - {path.name}"
    new_path = path.with_name(new_name)

    if new_path.exists():
        return "error", f"target already exists: {new_name}"

    del fm[field]
    content = _dump(fm, body)

    if dry_run:
        return "dry-run", f"{path.name}  →  {new_name}"

    new_path.write_text(content, encoding="utf-8")
    path.unlink()
    return "done", f"{path.name}  →  {new_name}"


# ── transform worker ───────────────────────────────────────────────────────────


def _transform_worker(
    path: Path, *, from_field: str, to_field: str, dry_run: bool
) -> Result:
    parsed = _load(path)
    if parsed is None:
        return "skip", path.name

    fm, body = parsed

    if from_field not in fm:
        return "skip", path.name

    if to_field in fm:
        return (
            "error",
            f"field '{to_field}' already exists in {path.name} — refusing to overwrite",
        )

    # Rebuild preserving insertion order, renaming the key in-place
    new_fm: dict[str, Any] = {
        (to_field if k == from_field else k): v for k, v in fm.items()
    }

    if dry_run:
        return "dry-run", f"{path.name}: '{from_field}'  →  '{to_field}'"

    path.write_text(_dump(new_fm, body), encoding="utf-8")
    return "done", f"{path.name}: '{from_field}'  →  '{to_field}'"
