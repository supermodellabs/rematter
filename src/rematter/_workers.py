"""Worker functions and the shared _run dispatcher."""

from __future__ import annotations

import concurrent.futures
import os
import re
from collections.abc import Callable
from datetime import date, datetime
from functools import partial
from pathlib import Path
from typing import Any

import typer
import yaml
from rich.console import Console

from rematter._core import (
    DATE_PREFIX_RE,
    TYPE_TAG_RE,
    WIKILINK_RE,
    _dump,
    _load,
    _slugify,
)

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
    files = sorted(p for p in directory.glob(pattern) if not p.name.startswith("_"))

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


# ── sync helpers ──────────────────────────────────────────────────────────────

_SYNC_BASE_REQUIRED = {"created", "modified", "synced", "publish"}

_SYNC_TYPE_REQUIRED: dict[str, set[str]] = {
    "dataset": {"is_meta_catalog", "is_api"},
    **{
        t: {"status", "creators", "own"}
        for t in ("book", "film", "anime", "manga", "comic", "tv", "music")
    },
}

_VALID_STATUS = {"not_started", "in_progress", "on_hold", "done", "cancelled"}

_SYNC_KNOWN_FIELDS = (
    _SYNC_BASE_REQUIRED
    | {"type"}
    | {"status", "creators", "own"}
    | {"is_meta_catalog", "is_api"}
)


def _is_timestamp_like(value: Any) -> bool:
    """Check if a value is a date, datetime, or ISO-parseable string."""
    if isinstance(value, (date, datetime)):
        return True
    if isinstance(value, str):
        try:
            date.fromisoformat(value)
            return True
        except ValueError:
            try:
                datetime.fromisoformat(value)
                return True
            except ValueError:
                return False
    return False


def _resolve_wikilinks(
    body: str,
    known_stems: set[str],
    output_dir: str,
) -> str:
    """Rewrite wikilinks in body text.

    - Valid links (target stem exists in known_stems) → [label](/output_dir/stem)
    - Broken links (target not found) → plain text (label if present, else target)
    """

    def _replace(m: re.Match[str]) -> str:
        target = m.group(1).strip()
        label = m.group(2).strip() if m.group(2) else None
        if target in known_stems:
            display = label if label else target
            slug = _slugify(target)
            prefix = output_dir.rstrip("/")
            return f"[{display}]({prefix}/{slug})"
        return label if label else target

    return WIKILINK_RE.sub(_replace, body)


def _extract_type_tags(body: str) -> tuple[list[str], str]:
    """Find capitalized Obsidian tags, return lowercased types + cleaned body."""
    tags = [m.group(1).lower() for m in TYPE_TAG_RE.finditer(body)]
    cleaned_lines = []
    for line in body.split("\n"):
        if TYPE_TAG_RE.search(line) and not TYPE_TAG_RE.sub("", line).strip():
            continue
        cleaned_lines.append(line)
    cleaned = "\n".join(cleaned_lines).lstrip("\n")
    return tags, cleaned


def _resolve_creators(
    creators: list[str],
    known_stems: set[str],
) -> list[dict[str, str]]:
    """Convert creators list to name/slug objects.

    - Wikilinks pointing to a known stem → {name, slug}
    - Wikilinks pointing to an unknown stem → {name} only (broken link)
    - Plain strings → {name} only
    """
    resolved: list[dict[str, str]] = []
    for creator in creators:
        m = WIKILINK_RE.search(str(creator))
        if m:
            target = m.group(1).strip()
            label = m.group(2).strip() if m.group(2) else None
            name = label if label else target
            if target in known_stems:
                resolved.append({"name": name, "slug": _slugify(target)})
            else:
                resolved.append({"name": name})
        else:
            resolved.append({"name": str(creator)})
    return resolved


def _validate_sync_schema(fm: dict[str, Any], types: list[str]) -> list[str]:
    """Return list of error messages for schema violations.

    Checks run in order of severity so we fail fast on structural issues
    (missing fields, unrecognized fields, multi-type) before attempting
    value-level validation (timestamp format, status enum).
    """
    errors = []

    # ── structural checks (fail fast) ────────────────────────────────────
    missing_base = _SYNC_BASE_REQUIRED - set(fm.keys())
    if missing_base:
        errors.append(f"missing required fields: {', '.join(sorted(missing_base))}")

    unrecognized = set(fm.keys()) - _SYNC_KNOWN_FIELDS
    if unrecognized:
        errors.append(f"unrecognized fields: {', '.join(sorted(unrecognized))}")

    # Bail early on structural errors — no point validating values
    if errors:
        return errors

    # ── value-level checks ───────────────────────────────────────────────
    for ts_field in ("created", "modified"):
        val = fm.get(ts_field)
        if val is not None and not _is_timestamp_like(val):
            errors.append(f"'{ts_field}' must be a timestamp, got '{val}'")

    synced_val = fm.get("synced")
    if synced_val is not None and not _is_timestamp_like(synced_val):
        errors.append(f"'synced' must be a timestamp, got '{synced_val}'")

    if "publish" in fm and not isinstance(fm["publish"], bool):
        errors.append(f"'publish' must be a bool, got '{fm['publish']}'")

    for t in types:
        required = _SYNC_TYPE_REQUIRED.get(t, set())
        missing_type = required - set(fm.keys())
        if missing_type:
            errors.append(f"type '{t}' requires: {', '.join(sorted(missing_type))}")

    status_val = fm.get("status")
    if status_val is not None and status_val not in _VALID_STATUS:
        errors.append(
            f"invalid status '{status_val}' (expected: {', '.join(sorted(_VALID_STATUS))})"
        )
    return errors


_SYNC_NO_SYNC_FIELDS = {"own", "publish", "created"}


def _sync_worker(
    path: Path,
    *,
    known_stems: set[str],
    output_dir: str,
    dest: Path,
    dry_run: bool,
) -> Result:
    """Process a single file for sync: gate, validate, transform, copy."""
    parsed = _load(path)
    if parsed is None:
        return "skip", f"{path.name}: no frontmatter"

    fm, body = parsed
    src_fm = dict(fm)  # shallow copy before mutations for source write-back

    # Publish gate
    if fm.get("publish") is not True:
        return "skip", f"{path.name}: not published"

    # Dest filename is the slugified source stem
    title = path.stem
    slug = _slugify(title)
    dest_file = dest / f"{slug}.md"

    # Modified comparison — skip if dest has same modified value
    if dest_file.exists():
        dest_parsed = _load(dest_file)
        if dest_parsed is not None:
            dest_fm, _ = dest_parsed
            if fm.get("modified") is not None and fm.get("modified") == dest_fm.get(
                "modified"
            ):
                return "skip", f"{path.name}: not modified since last sync"

    # Extract type tags from body
    types, cleaned_body = _extract_type_tags(body)

    # Multi-type is not supported — skip with a warning
    if len(types) > 1:
        return (
            "warn",
            f"{path.name}: multiple type tags ({', '.join(sorted(types))}) — skipping",
        )

    # Schema validation
    errors = _validate_sync_schema(fm, types)
    if errors:
        return "error", f"{path.name}: {'; '.join(errors)}"

    # Resolve creators to name/slug objects
    if "creators" in fm:
        creators = fm["creators"]
        if isinstance(creators, str):
            creators = [creators]
        fm["creators"] = _resolve_creators(creators, known_stems)

    # Resolve body wikilinks
    new_body = _resolve_wikilinks(cleaned_body, known_stems, output_dir)

    # Set synced timestamp
    synced_ts = datetime.now().replace(microsecond=0).isoformat()
    fm["synced"] = synced_ts

    # Set title from source filename
    fm["title"] = title

    # Set type (multi-type is rejected by validation, so at most one here)
    if len(types) == 1:
        fm["type"] = types[0]

    # Strip source-only fields from dest output
    for field in _SYNC_NO_SYNC_FIELDS:
        fm.pop(field, None)

    content = _dump(fm, new_body)

    if dry_run:
        return "dry-run", f"{path.name}  →  {dest_file.name}"

    dest_file.write_text(content, encoding="utf-8")

    # Stamp synced on source so the modified-comparison gate works next run
    src_fm["synced"] = synced_ts
    path.write_text(_dump(src_fm, body), encoding="utf-8")

    return "done", f"{path.name}  →  {dest_file.name}"


def _sync_run(
    source: Path,
    dest: Path,
    output_dir: str,
    dry_run: bool,
) -> None:
    """Discover files, build corpus, fan out sync workers."""
    if not source.is_dir():
        err_console.print(f"[bold red]❌  Not a directory:[/] {source}")
        raise typer.Exit(code=1)

    src_files = sorted(p for p in source.glob("*.md") if not p.name.startswith("_"))
    if not src_files:
        console.print("[yellow]🤷  No .md files found in source — nothing to do.[/]")
        raise typer.Exit(code=0)

    # Build the complete corpus of known stems (source ∪ destination)
    # Dest filenames are slugified, so we read titles from frontmatter instead
    src_stems = {p.stem for p in src_files}
    dest_stems: set[str] = set()
    if dest.is_dir():
        for p in dest.glob("*.md"):
            parsed = _load(p)
            if parsed:
                dfm, _ = parsed
                title = dfm.get("title")
                if title:
                    dest_stems.add(title)
    known_stems = src_stems | dest_stems

    if dry_run:
        console.print("[dim italic]🔍  Dry run — no files will be modified.[/]\n")

    if not dry_run:
        dest.mkdir(parents=True, exist_ok=True)

    fn = partial(
        _sync_worker,
        known_stems=known_stems,
        output_dir=output_dir,
        dest=dest,
        dry_run=dry_run,
    )
    num_workers = min(len(src_files), os.cpu_count() or 4)

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as pool:
        results: list[Result] = list(pool.map(fn, src_files))

    done = skipped = errors = 0

    for status, msg in results:
        match status:
            case "done":
                console.print(f"[green]✅[/]  {msg}")
                done += 1
            case "dry-run":
                console.print(f"[cyan]🔍[/]  {msg}")
                done += 1
            case "warn":
                err_console.print(f"[yellow]⚠️[/]  {msg}")
                skipped += 1
            case "skip":
                skipped += 1
            case "error":
                err_console.print(f"[bold red]❌[/]  {msg}")
                errors += 1

    label = "would sync" if dry_run else "synced"
    console.print()
    console.print(
        f"[bold]Done:[/] {label} [green]{done}[/] · "
        f"skipped [dim]{skipped}[/] · "
        f"errors [{'red' if errors else 'dim'}]{errors}[/]"
    )

    if errors:
        raise typer.Exit(code=1)


# ── validate helpers ───────────────────────────────────────────────────────────


def _load_schema(path: Path) -> dict[str, Any]:
    """Load and return a validate schema from a YAML file."""
    if not path.exists():
        raise FileNotFoundError(f"Schema file not found: {path}")
    schema = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    _validate_schema_defaults(schema)
    return schema


def _validate_schema_defaults(schema: dict[str, Any]) -> None:
    """Validate that timestamp defaults are well-formed strftime format strings."""
    for field, spec in schema.get("properties", {}).items():
        default = spec.get("default")
        if default is None or spec.get("type") != "timestamp":
            continue
        if not isinstance(default, str) or "%" not in default:
            raise ValueError(
                f"schema error: '{field}' is a timestamp — default must be a "
                f"strftime format string (e.g. '%Y-%m-%d %H:%M'), got: {default!r}"
            )
        try:
            datetime.now().strftime(default)
        except ValueError as exc:
            raise ValueError(
                f"schema error: '{field}' has invalid strftime format: {default!r} — {exc}"
            ) from exc


_SCHEMA_TYPE_CHECKERS: dict[str, Callable[[Any], bool]] = {
    "timestamp": lambda v: _is_timestamp_like(v),
    "bool": lambda v: isinstance(v, bool),
    "string": lambda v: isinstance(v, str),
    "list": lambda v: isinstance(v, list),
    "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "float": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
}


def _validate_against_schema(fm: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """Return list of error messages for schema violations."""
    errors: list[str] = []
    props = schema.get("properties", {})
    allow_extra = schema.get("allow_extra", False)

    # Structural: missing required fields
    for field, spec in props.items():
        if spec.get("required", False) and field not in fm:
            errors.append(f"missing required field: {field}")

    # Structural: unrecognized fields
    if not allow_extra:
        known = set(props.keys())
        unrecognized = set(fm.keys()) - known
        if unrecognized:
            errors.append(f"unrecognized fields: {', '.join(sorted(unrecognized))}")

    if errors:
        return errors

    # Value-level checks
    for field, spec in props.items():
        if field not in fm:
            continue
        val = fm[field]

        if val is None:
            continue

        # Type check
        expected_type = spec.get("type")
        if expected_type and expected_type in _SCHEMA_TYPE_CHECKERS:
            if not _SCHEMA_TYPE_CHECKERS[expected_type](val):
                errors.append(f"'{field}' must be {expected_type}, got '{val}'")
                continue

        # Enum check
        allowed = spec.get("enum")
        if allowed and val not in allowed:
            errors.append(
                f"'{field}' value '{val}' not in allowed values: {', '.join(str(v) for v in allowed)}"
            )

    return errors


_MISSING = object()
"""Sentinel distinguishing 'no default in schema' from 'default: null'."""


def _resolve_default(spec: dict[str, Any]) -> Any:
    """Resolve a schema default value, expanding strftime format strings for timestamps.

    Returns ``_MISSING`` when the spec has no ``default`` key at all, so callers
    can distinguish "default is null" (add key with null value) from "no default"
    (unfixable).
    """
    if "default" not in spec:
        return _MISSING
    default = spec["default"]
    if default is None:
        return None
    if spec.get("type") == "timestamp" and isinstance(default, str) and "%" in default:
        return datetime.now().strftime(default)
    return default


def _validate_worker(
    path: Path, *, schema: dict[str, Any], fix: bool, dry_run: bool
) -> Result:
    """Validate a single file against a schema, optionally fixing missing defaults."""
    parsed = _load(path)
    if parsed is None:
        return "skip", path.name

    fm, body = parsed
    errors = _validate_against_schema(fm, schema)

    if not errors and not fix:
        return "skip", path.name

    if fix:
        props = schema.get("properties", {})
        fixed_fields: list[str] = []
        unfixable: list[str] = []

        for field, spec in props.items():
            if field not in fm:
                if not spec.get("required", False):
                    continue
                default = _resolve_default(spec)
                if default is not _MISSING:
                    fm[field] = default
                    fixed_fields.append(field)
                else:
                    unfixable.append(field)

        if unfixable:
            return (
                "error",
                f"{path.name}: cannot fix missing required fields without defaults: {', '.join(sorted(unfixable))}",
            )

        # Reorder keys to match schema order (known keys first, extras after)
        prop_order = list(props)
        ordered_fm = {k: fm[k] for k in prop_order if k in fm}
        ordered_fm.update({k: v for k, v in fm.items() if k not in ordered_fm})
        reordered = list(ordered_fm) != list(fm)
        fm = ordered_fm

        changes: list[str] = []
        if fixed_fields:
            changes.append(f"set {', '.join(sorted(fixed_fields))}")
        if reordered:
            changes.append("reorder keys")

        if not changes:
            if not errors:
                return "skip", path.name
            return "error", f"{path.name}: {'; '.join(errors)}"

        summary = "; ".join(changes)

        if dry_run:
            return "dry-run", f"{path.name}: would {summary}"

        path.write_text(_dump(fm, body), encoding="utf-8")
        return "done", f"{path.name}: {summary}"

    # Report-only mode
    return "error", f"{path.name}: {'; '.join(errors)}"


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
