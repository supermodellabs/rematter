"""Worker functions and the shared _run dispatcher."""

from __future__ import annotations

import concurrent.futures
import fnmatch
import os
import re
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import partial
from pathlib import Path
from typing import Any

import typer
import yaml
from rich.console import Console

from rematter._core import (
    DATE_PREFIX_RE,
    MD_IMAGE_RE,
    TYPE_TAG_RE,
    WIKILINK_IMAGE_RE,
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


# ── config ─────────────────────────────────────────────────────────────────────

_CONFIG_FILENAME = ".rematter.yaml"
_LEGACY_SCHEMA_FILENAME = "_schema.yml"


@dataclass
class MediaConfig:
    """Media sync configuration."""

    source: str  # relative to source dir
    dest: str  # relative to dest dir
    link_prefix: str  # URL prefix for rewritten refs


@dataclass
class RematterConfig:
    """Combined config + schema loaded from .rematter.yaml."""

    properties: dict[str, Any] = field(default_factory=dict)
    link_path_prefix: str | None = None
    dest: str | None = None
    media: MediaConfig | None = None
    ignore: list[str] = field(default_factory=list)
    extract_type_tags: bool = True

    @property
    def schema(self) -> dict[str, Any]:
        """Return the schema portion of the config."""
        return {"properties": self.properties}

    @property
    def no_sync_fields(self) -> set[str]:
        """Return set of property names where sync: false."""
        return {
            name for name, spec in self.properties.items() if spec.get("sync") is False
        }


def _load_config(source_dir: Path, explicit_path: Path | None = None) -> RematterConfig:
    """Load a .rematter.yaml config file.

    Lookup order:
    1. explicit_path (from CLI --schema / --config flag)
    2. .rematter.yaml in source_dir
    3. _schema.yml in source_dir (legacy, with deprecation warning)
    """
    if explicit_path is not None:
        path = explicit_path
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
    else:
        path = source_dir / _CONFIG_FILENAME
        if not path.exists():
            legacy = source_dir / _LEGACY_SCHEMA_FILENAME
            if legacy.exists():
                print(
                    f"⚠️  '{_LEGACY_SCHEMA_FILENAME}' is deprecated — "
                    f"rename to '{_CONFIG_FILENAME}'",
                    file=sys.stderr,
                )
                path = legacy
            else:
                raise FileNotFoundError(
                    f"No config found in {source_dir}. "
                    f"Create a '{_CONFIG_FILENAME}' file."
                )

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    # Parse media config
    media = None
    media_raw = raw.get("media")
    if isinstance(media_raw, dict):
        media = MediaConfig(
            source=media_raw["source"],
            dest=media_raw["dest"],
            link_prefix=media_raw["link_prefix"],
        )

    config = RematterConfig(
        properties=raw.get("properties", {}),
        link_path_prefix=raw.get("link_path_prefix"),
        dest=raw.get("dest"),
        media=media,
        ignore=raw.get("ignore", []),
        extract_type_tags=raw.get("extract_type_tags", True),
    )

    _validate_schema_defaults(config.schema)
    return config


# ── shared runner ──────────────────────────────────────────────────────────────


def _filter_ignored(files: list[Path], base_dir: Path, ignore: list[str]) -> list[Path]:
    """Remove files matching any ignore pattern (relative to base_dir)."""
    if not ignore:
        return files
    result = []
    for f in files:
        rel = str(f.relative_to(base_dir))
        if not any(
            fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(f.name, pat) for pat in ignore
        ):
            result.append(f)
    return result


def _run(
    directory: Path,
    recursive: bool,
    dry_run: bool,
    worker: WorkerFn,
    ignore: list[str] | None = None,
    **kwargs: Any,
) -> None:
    """Discover markdown files and fan out to worker via a thread pool."""
    if not directory.is_dir():
        err_console.print(f"[bold red]❌  Not a directory:[/] {directory}")
        raise typer.Exit(code=1)

    pattern = "**/*.md" if recursive else "*.md"
    files = sorted(p for p in directory.glob(pattern) if not p.name.startswith("_"))
    files = _filter_ignored(files, directory, ignore or [])

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
    link_path_prefix: str,
) -> str:
    """Rewrite wikilinks in body text.

    - Valid links (target stem exists in known_stems) → [label](/prefix/stem)
    - Broken links (target not found) → plain text (label if present, else target)
    """

    def _replace(m: re.Match[str]) -> str:
        target = m.group(1).strip()
        label = m.group(2).strip() if m.group(2) else None
        if target in known_stems:
            display = label if label else target
            slug = _slugify(target)
            prefix = link_path_prefix.rstrip("/")
            return f"[{display}]({prefix}/{slug})"
        return label if label else target

    return WIKILINK_RE.sub(_replace, body)


def _resolve_media_refs(
    body: str,
    media_config: MediaConfig,
    source_dir: Path,
) -> tuple[str, list[tuple[Path, str]]]:
    """Rewrite image references and collect files to copy.

    Handles:
    - Wikilink images: ![[file.png]] / ![[file.png|alt text]]
    - Markdown images pointing to the media source dir: ![alt](source/file.png)

    Returns (new_body, files_to_copy) where files_to_copy is a list of
    (source_path, dest_filename) tuples.
    """
    files_to_copy: list[tuple[Path, str]] = []
    media_source = source_dir / media_config.source
    prefix = media_config.link_prefix.rstrip("/")

    def _replace_wikilink_image(m: re.Match[str]) -> str:
        filename = m.group(1).strip()
        alt = m.group(2).strip() if m.group(2) else filename
        src_path = media_source / filename
        if src_path.exists():
            files_to_copy.append((src_path, filename))
            return f"![{alt}]({prefix}/{filename})"
        return m.group(0)  # leave unchanged if file doesn't exist

    def _replace_md_image(m: re.Match[str]) -> str:
        alt = m.group(1)
        path_str = m.group(2)
        # Only rewrite if path points into the media source dir
        if path_str.startswith(media_config.source + "/") or path_str.startswith(
            media_config.source + "\\"
        ):
            filename = Path(path_str).name
            src_path = media_source / filename
            if src_path.exists():
                files_to_copy.append((src_path, filename))
                return f"![{alt}]({prefix}/{filename})"
        return m.group(0)

    body = WIKILINK_IMAGE_RE.sub(_replace_wikilink_image, body)
    body = MD_IMAGE_RE.sub(_replace_md_image, body)
    return body, files_to_copy


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


_SYNC_NO_SYNC_FIELDS_DEFAULT = {"own", "publish", "created"}


def _sync_worker(
    path: Path,
    *,
    known_stems: set[str],
    link_path_prefix: str,
    dest: Path,
    dry_run: bool,
    media_config: MediaConfig | None = None,
    no_sync_fields: set[str] | None = None,
    schema: dict[str, Any] | None = None,
    extract_type_tags: bool = True,
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

    # Extract type tags from body (when enabled)
    types: list[str] = []
    if extract_type_tags:
        types, cleaned_body = _extract_type_tags(body)

        # Multi-type is not supported — skip with a warning
        if len(types) > 1:
            return (
                "warn",
                f"{path.name}: multiple type tags ({', '.join(sorted(types))}) — skipping",
            )
    else:
        cleaned_body = body

    # Schema validation
    errors = _validate_against_schema(fm, schema) if schema is not None else []
    if errors:
        return "error", f"{path.name}: {'; '.join(errors)}"

    # Resolve creators to name/slug objects
    if "creators" in fm:
        creators = fm["creators"]
        if isinstance(creators, str):
            creators = [creators]
        fm["creators"] = _resolve_creators(creators, known_stems)

    # Resolve body wikilinks
    new_body = _resolve_wikilinks(cleaned_body, known_stems, link_path_prefix)

    # Resolve media references
    media_files: list[tuple[Path, str]] = []
    if media_config is not None:
        new_body, media_files = _resolve_media_refs(new_body, media_config, path.parent)

    # Rewrite hero image path if media config is present
    # Hero values may be wikilinks ([[hero.jpg]]) or raw paths (_media/hero.jpg)
    if media_config is not None and "hero" in fm and fm["hero"] is not None:
        hero_val = str(fm["hero"])
        # Strip wikilink syntax: [[hero.jpg]] → hero.jpg
        wikilink_match = re.fullmatch(r"\[\[([^|\]]+?)(?:\|[^\]]+?)?\]\]", hero_val)
        if wikilink_match:
            hero_val = wikilink_match.group(1)
        hero_filename = Path(hero_val).name
        hero_src = path.parent / media_config.source / hero_filename
        if hero_src.exists():
            media_files.append((hero_src, hero_filename))
            fm["hero"] = f"{media_config.link_prefix.rstrip('/')}/{hero_filename}"

    # Set synced timestamp (match Obsidian's format: "2026-02-04 11:01")
    synced_ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    fm["synced"] = synced_ts

    # Set title from source filename
    fm["title"] = title

    # Set type (multi-type is rejected by validation, so at most one here)
    if len(types) == 1:
        fm["type"] = types[0]

    # Strip source-only fields from dest output
    strip = (
        no_sync_fields if no_sync_fields is not None else _SYNC_NO_SYNC_FIELDS_DEFAULT
    )
    for f in strip:
        fm.pop(f, None)

    content = _dump(fm, new_body)

    media_suffix = f"  (+{len(media_files)} 🖼️)" if media_files else ""

    if dry_run:
        return "dry-run", f"{path.name}  →  {dest_file.name}{media_suffix}"

    dest_file.write_text(content, encoding="utf-8")

    # Copy referenced media files
    if media_files:
        media_dest = dest / media_config.dest  # type: ignore[union-attr]
        media_dest.mkdir(parents=True, exist_ok=True)
        for src_path, filename in media_files:
            shutil.copy2(src_path, media_dest / filename)

    # Stamp synced on source so the modified-comparison gate works next run
    src_fm["synced"] = synced_ts
    path.write_text(_dump(src_fm, body), encoding="utf-8")

    return "done", f"{path.name}  →  {dest_file.name}{media_suffix}"


def _sync_run(
    source: Path,
    dest: Path,
    link_path_prefix: str,
    dry_run: bool,
    recursive: bool = False,
    media_config: MediaConfig | None = None,
    ignore: list[str] | None = None,
    no_sync_fields: set[str] | None = None,
    schema: dict[str, Any] | None = None,
    extract_type_tags: bool = True,
) -> None:
    """Discover files, build corpus, fan out sync workers."""
    if not source.is_dir():
        err_console.print(f"[bold red]❌  Not a directory:[/] {source}")
        raise typer.Exit(code=1)

    pattern = "**/*.md" if recursive else "*.md"
    src_files = sorted(p for p in source.glob(pattern) if not p.name.startswith("_"))
    src_files = _filter_ignored(src_files, source, ignore or [])
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

    console.print(f"[bold]📂  {source}[/]  →  [bold]{dest}[/]\n")

    if dry_run:
        console.print("[dim italic]🔍  Dry run — no files will be modified.[/]\n")

    if not dry_run:
        dest.mkdir(parents=True, exist_ok=True)

    fn = partial(
        _sync_worker,
        known_stems=known_stems,
        link_path_prefix=link_path_prefix,
        dest=dest,
        dry_run=dry_run,
        media_config=media_config,
        no_sync_fields=no_sync_fields,
        schema=schema,
        extract_type_tags=extract_type_tags,
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
    for property, spec in schema.get("properties", {}).items():
        default = spec.get("default")
        if default is None or spec.get("type") != "timestamp":
            continue
        if not isinstance(default, str) or "%" not in default:
            raise ValueError(
                f"schema error: '{property}' is a timestamp — default must be a "
                f"strftime format string (e.g. '%Y-%m-%d %H:%M'), got: {default!r}"
            )
        try:
            datetime.now().strftime(default)
        except ValueError as exc:
            raise ValueError(
                f"schema error: '{property}' has invalid strftime format: {default!r} — {exc}"
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

    # Structural: missing required properties
    for prop, spec in props.items():
        if spec.get("required", False) and prop not in fm:
            errors.append(f"missing required property: {prop}")

    # Structural: unrecognized properties (all properties must be in schema)
    known = set(props.keys())
    unrecognized = set(fm.keys()) - known
    if unrecognized:
        errors.append(f"unrecognized properties: {', '.join(sorted(unrecognized))}")

    if errors:
        return errors

    # Value-level checks
    for property_field, spec in props.items():
        if property_field not in fm:
            continue
        val = fm[property_field]

        if val is None:
            continue

        # Type check
        expected_type = spec.get("type")
        if expected_type and expected_type in _SCHEMA_TYPE_CHECKERS:
            if not _SCHEMA_TYPE_CHECKERS[expected_type](val):
                errors.append(
                    f"'{property_field}' must be {expected_type}, got '{val}'"
                )
                continue

        # Enum check
        allowed = spec.get("enum")
        if allowed and val not in allowed:
            errors.append(
                f"'{property_field}' value '{val}' not in allowed values: {', '.join(str(v) for v in allowed)}"
            )

    # Co-dependency checks (requires)
    for property_field, spec in props.items():
        requires = spec.get("requires")
        if not requires:
            continue
        val = fm.get(property_field)
        if val is None:
            continue
        for companion in requires:
            companion_val = fm.get(companion)
            if companion_val is None:
                errors.append(
                    f"'{property_field}' requires '{companion}' to also have a value"
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
