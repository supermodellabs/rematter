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
    ATX_HEADING_RE,
    DATE_PREFIX_RE,
    FENCE_RE,
    MD_IMAGE_RE,
    SPECIAL_LINE_RE,
    TABLE_LINE_RE,
    TYPE_TAG_RE,
    WIKILINK_IMAGE_RE,
    WIKILINK_RE,
    _dump,
    _load,
    _slugify,
    _split_frontmatter,
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


# ── date-extract worker ────────────────────────────────────────────────────────


def _date_extract_worker(path: Path, *, field: str, dry_run: bool) -> Result:
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
            display = label or target
            slug = _slugify(target)
            prefix = link_path_prefix.rstrip("/")
            return f"[{display}]({prefix}/{slug})"
        return label or target

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
        if path_str.startswith((media_config.source + "/", media_config.source + "\\")):
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
            name = label or target
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
    synced_ts = datetime.now().strftime("%Y-%m-%d %H:%M")  # noqa: DTZ005 — Obsidian uses local time
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
            datetime.now().strftime(default)  # noqa: DTZ005 — format validation only
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
        if (
            expected_type
            and expected_type in _SCHEMA_TYPE_CHECKERS
            and not _SCHEMA_TYPE_CHECKERS[expected_type](val)
        ):
            errors.append(f"'{property_field}' must be {expected_type}, got '{val}'")
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
        return datetime.now().strftime(default)  # noqa: DTZ005 — Obsidian uses local time
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


# ── reflow worker ──────────────────────────────────────────────────────────────


def _reflow_text(text: str) -> str:
    """Reflow hard-wrapped markdown into single-line paragraphs.

    Joins consecutive lines of plain prose into one line. Preserves fenced code
    blocks, headings, lists, blockquotes, tables, HTML blocks, and horizontal rules.
    """
    out: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            out.append(" ".join(s.strip() for s in buf))
            buf.clear()

    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = FENCE_RE.match(line)
        if m:
            flush()
            out.append(line)
            fence = m.group(2)[0] * 3
            i += 1
            while i < len(lines):
                out.append(lines[i])
                if re.match(rf"^\s*{re.escape(fence)}+\s*$", lines[i]):
                    i += 1
                    break
                i += 1
            continue

        if line.strip() == "":
            flush()
            out.append("")
        elif SPECIAL_LINE_RE.match(line):
            flush()
            out.append(line)
        else:
            buf.append(line)
        i += 1

    flush()
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def _reflow_worker(path: Path, *, dry_run: bool) -> Result:
    text = path.read_text(encoding="utf-8")
    fm_block, body = _split_frontmatter(text)
    new_body = _reflow_text(body)
    if new_body == body:
        return "skip", path.name
    if dry_run:
        return "dry-run", path.name
    path.write_text(fm_block + new_body, encoding="utf-8")
    return "done", path.name


# ── fix-tables worker ──────────────────────────────────────────────────────────


_TABLE_STYLES = {"compact", "aligned"}
_SEP_CELL_RE = re.compile(r"^\s*:?-{1,}:?\s*$")


def _split_table_row(line: str) -> list[str]:
    """Split a table row line into cell contents (trimmed), dropping outer pipes."""
    stripped = line.strip().removeprefix("|").removesuffix("|")
    return [c.strip() for c in stripped.split("|")]


def _is_separator_row(cells: list[str]) -> bool:
    return bool(cells) and all(_SEP_CELL_RE.match(c) for c in cells)


def _format_compact(rows: list[list[str]]) -> list[str]:
    return ["| " + " | ".join(cells) + " |" for cells in rows]


def _expand_separator(cell: str, width: int) -> str:
    """Expand a separator cell to `width` characters, preserving alignment colons."""
    left = cell.startswith(":")
    right = cell.endswith(":")
    inner = max(width - int(left) - int(right), 1)
    body = "-" * inner
    return f"{':' if left else ''}{body}{':' if right else ''}"


def _format_aligned(rows: list[list[str]], sep_idx: int | None) -> list[str]:
    if not rows:
        return []
    n_cols = max(len(r) for r in rows)
    # Normalize all rows to same column count
    norm = [r + [""] * (n_cols - len(r)) for r in rows]
    widths = [0] * n_cols
    for r_i, row in enumerate(norm):
        for c_i, cell in enumerate(row):
            length = len(cell)
            if r_i == sep_idx:
                # Separator's minimum length is its current length (keeps colons)
                length = max(length, 3)
            widths[c_i] = max(widths[c_i], length)

    out: list[str] = []
    for r_i, row in enumerate(norm):
        if r_i == sep_idx:
            cells = [_expand_separator(row[c], widths[c]) for c in range(n_cols)]
        else:
            cells = [row[c].ljust(widths[c]) for c in range(n_cols)]
        out.append("| " + " | ".join(cells) + " |")
    return out


def _format_table_block(lines: list[str], style: str) -> list[str]:
    """Reformat a contiguous block of table lines in the requested style."""
    rows = [_split_table_row(line) for line in lines]
    sep_idx = None
    for i, cells in enumerate(rows):
        if _is_separator_row(cells):
            sep_idx = i
            break

    if style == "aligned":
        return _format_aligned(rows, sep_idx)
    return _format_compact(rows)


def _fix_tables_text(text: str, *, style: str) -> str:
    """Rewrite all markdown tables in `text` to the requested style."""
    if style not in _TABLE_STYLES:
        raise ValueError(
            f"unknown table style: {style!r} (expected one of {sorted(_TABLE_STYLES)})"
        )

    lines = text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    in_code = False
    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip("\n")
        if FENCE_RE.match(line):
            in_code = not in_code
            out.append(raw)
            i += 1
            continue
        if in_code or not TABLE_LINE_RE.match(line):
            out.append(raw)
            i += 1
            continue

        # Collect the full contiguous table block
        block: list[str] = []
        block_terminators: list[str] = []
        j = i
        while j < len(lines):
            cur = lines[j].rstrip("\n")
            if not TABLE_LINE_RE.match(cur):
                break
            block.append(cur)
            # Preserve trailing newlines per source line
            block_terminators.append("\n" if lines[j].endswith("\n") else "")
            j += 1

        formatted = _format_table_block(block, style)
        for formatted_line, terminator in zip(formatted, block_terminators):
            out.append(formatted_line + terminator)
        i = j

    return "".join(out)


def _fix_tables_worker(path: Path, *, style: str, dry_run: bool) -> Result:
    text = path.read_text(encoding="utf-8")
    fm_block, body = _split_frontmatter(text)
    new_body = _fix_tables_text(body, style=style)
    if new_body == body:
        return "skip", path.name
    if dry_run:
        return "dry-run", path.name
    path.write_text(fm_block + new_body, encoding="utf-8")
    return "done", path.name


# ── step-headings worker ───────────────────────────────────────────────────────


def _step_headings_text(text: str) -> str:
    """Rewrite ATX heading levels so no level is skipped relative to its parent.

    The top-level heading depth is preserved (we never promote, e.g., h2 → h1).
    Walking through the document, each heading whose level skips ahead of its
    parent is pulled up to parent_level + 1; descendants shift up by the same
    delta so the structure stays consistent. Headings inside fenced code blocks
    are not touched. Setext (underline) headings are left alone.
    """
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    in_code = False
    # Stack of (input_level, output_level) for ancestor headings.
    stack: list[tuple[int, int]] = []
    for raw in lines:
        line = raw.rstrip("\n")
        if FENCE_RE.match(line):
            in_code = not in_code
            out.append(raw)
            continue
        if in_code:
            out.append(raw)
            continue
        m = ATX_HEADING_RE.match(line)
        if not m:
            out.append(raw)
            continue
        in_lvl = len(m.group(1))
        rest = m.group(2)
        while stack and stack[-1][0] >= in_lvl:
            stack.pop()
        if not stack:
            out_lvl = in_lvl  # top-level: preserve depth
        else:
            _parent_in, parent_out = stack[-1]
            out_lvl = min(parent_out + 1, in_lvl)
        stack.append((in_lvl, out_lvl))
        suffix = "\n" if raw.endswith("\n") else ""
        out.append("#" * out_lvl + rest + suffix)
    return "".join(out)


# ── move-linked-dir ────────────────────────────────────────────────────────────


_MD_LINK_RE = re.compile(r"(!?)\[([^\]]*)\]\(([^)\s]+)(\s+\"[^\"]*\")?\)")


@dataclass
class MoveLinkedDirResult:
    """Outcome of a `move-linked-dir` invocation.

    Carries planned moves and rewritten files for dry-run reporting plus any
    errors that prevented the operation from completing.
    """

    planned_moves: list[str] = field(default_factory=list)
    rewritten_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _is_external_or_anchor(target: str) -> bool:
    """True if a markdown link target is a URL, mailto, or in-page anchor."""
    if target.startswith("#"):
        return True
    if "://" in target.split("#", 1)[0].split("?", 1)[0]:
        return True
    return bool(target.startswith("mailto:"))


def _split_target(target: str) -> tuple[str, str]:
    """Split a link target into (path_part, suffix) where suffix is #frag/?q/empty."""
    for sep in ("#", "?"):
        i = target.find(sep)
        if i >= 0:
            return target[:i], target[i:]
    return target, ""


def _rewrite_md_links(
    text: str,
    md_old_dir: Path,
    md_new_dir: Path,
    move_map: dict[Path, Path],
) -> str:
    """Rewrite markdown-style links/images whose targets fall inside `move_map`.

    Targets are resolved relative to `md_old_dir` (the file's pre-move location).
    Matched targets are rewritten relative to `md_new_dir` (the file's post-move
    location). External URLs, mailto, and pure anchors are left alone.
    """

    md_is_moving = md_old_dir != md_new_dir

    def _sub(m: re.Match[str]) -> str:
        bang, label, target, title = (
            m.group(1),
            m.group(2),
            m.group(3),
            m.group(4) or "",
        )
        if _is_external_or_anchor(target):
            return m.group(0)
        path_part, suffix = _split_target(target)
        if not path_part:
            return m.group(0)
        try:
            abs_old = (md_old_dir / path_part).resolve(strict=False)
        except (OSError, RuntimeError):
            return m.group(0)
        target_moved = abs_old in move_map
        if not target_moved and not md_is_moving:
            return m.group(0)
        new_abs = move_map[abs_old] if target_moved else abs_old
        try:
            new_rel = os.path.relpath(new_abs, md_new_dir)
        except ValueError:
            return m.group(0)
        # Use forward slashes for portability in markdown links
        new_rel = new_rel.replace(os.sep, "/")
        if new_rel == path_part:
            return m.group(0)
        return f"{bang}[{label}]({new_rel}{suffix}{title})"

    return _MD_LINK_RE.sub(_sub, text)


def _move_linked_dir_worker(
    path: Path, *, move_map: dict[Path, Path], dry_run: bool
) -> Result:
    """Rewrite markdown links in one file based on a precomputed move plan.

    The worker knows: (a) where the file lives now, (b) where it'll live after
    the moves are applied (looked up in `move_map`, defaulting to "in place"),
    and (c) the full set of paths being relocated. It rewrites each link
    accordingly without performing any actual moves — those happen after the
    fan-out, in `_move_linked_dir`.
    """
    md_abs = path.resolve()
    new_md_abs = move_map.get(md_abs, md_abs)
    text = path.read_text(encoding="utf-8")
    new_text = _rewrite_md_links(
        text,
        md_old_dir=md_abs.parent,
        md_new_dir=new_md_abs.parent,
        move_map=move_map,
    )
    if new_text == text:
        return "skip", path.name
    if dry_run:
        return "dry-run", path.name
    path.write_text(new_text, encoding="utf-8")
    return "done", path.name


def _move_linked_dir(
    target: Path,
    *,
    source: Path | None = None,
    to: Path | None = None,
    dry_run: bool = False,
) -> MoveLinkedDirResult:
    """Move/rename `target` (a subdir of `source`) and rewrite markdown links.

    - `source`: the anchor world. Defaults to `Path.cwd()`. The fan-out covers
      every `.md` file under `source` recursively; we never look outside it.
    - `target`: directory being moved. Relative paths resolve against `source`;
      absolute paths must live inside `source`.
    - `to`: optional destination. Same resolution rules as `target`. When
      omitted, `target`'s contents are flattened into `source` and `target`
      itself is removed.
    - `dry_run`: don't touch the filesystem; report the plan instead.

    Bare wikilinks (`[[note]]`) resolve by filename in Obsidian and are not
    rewritten. Path-prefixed wikilinks are out of scope for now.
    """
    result = MoveLinkedDirResult()

    src_path = (source if source is not None else Path.cwd()).resolve()
    if not src_path.is_dir():
        result.errors.append(f"source is not a directory: {src_path}")
        return result

    if target.is_absolute():
        tgt_path = target.resolve()
    else:
        tgt_path = (src_path / target).resolve()

    if tgt_path == src_path:
        result.errors.append("target cannot equal source")
        return result
    try:
        tgt_path.relative_to(src_path)
    except ValueError:
        result.errors.append(f"target is outside source ({src_path}): {tgt_path}")
        return result
    if not tgt_path.exists():
        result.errors.append(f"target does not exist: {tgt_path}")
        return result
    if not tgt_path.is_dir():
        result.errors.append(f"target is not a directory: {tgt_path}")
        return result

    if to is None:
        dest_path = src_path  # flatten target into source
    elif to.is_absolute():
        dest_path = to.resolve()
    else:
        dest_path = (src_path / to).resolve()
    try:
        dest_path.relative_to(src_path)
    except ValueError:
        result.errors.append(f"destination is outside source ({src_path}): {dest_path}")
        return result

    # When --to is given, dest must not already exist (avoids ambiguous merges).
    # Flattening (dest == source) is fine — that's a merge into the anchor by design.
    if dest_path != src_path and dest_path.exists():
        result.errors.append(f"destination already exists: {dest_path}")
        return result

    # Build move_map: every descendant of target → its new absolute path.
    move_map: dict[Path, Path] = {}
    for old in tgt_path.rglob("*"):
        rel = old.relative_to(tgt_path)
        move_map[old] = dest_path / rel

    # Detect file-level collisions (relevant when flattening into source)
    for old, new in move_map.items():
        if old.is_file() and new.exists() and new.resolve() != old:
            result.errors.append(f"destination already exists: {new}")
    if result.errors:
        return result

    # Phase 1: fan workers out over every .md file in source.
    md_files = sorted(p for p in src_path.rglob("*.md") if p.is_file())
    worker_results: list[Result] = []
    if md_files:
        fn = partial(_move_linked_dir_worker, move_map=move_map, dry_run=dry_run)
        num_workers = min(len(md_files), os.cpu_count() or 4)
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as pool:
            worker_results = list(pool.map(fn, md_files))

    for (status, msg), md in zip(worker_results, md_files):
        if status in ("done", "dry-run"):
            result.rewritten_files.append(str(md))
        elif status == "error":
            result.errors.append(msg)

    if result.errors:
        return result

    for old, new in move_map.items():
        if old.is_file():
            result.planned_moves.append(f"{old} → {new}")

    if dry_run:
        return result

    # Phase 2: apply moves.
    for old, new in sorted(move_map.items(), key=lambda kv: len(kv[0].parts)):
        if old.is_dir():
            new.mkdir(parents=True, exist_ok=True)
    for old, new in move_map.items():
        if old.is_file():
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old), str(new))

    # Clean up empty dirs inside target, deepest first; then target itself.
    for d in sorted(
        (p for p in tgt_path.rglob("*") if p.is_dir()),
        key=lambda p: len(p.parts),
        reverse=True,
    ):
        try:
            d.rmdir()
        except OSError:
            pass
    try:
        tgt_path.rmdir()
    except OSError:
        pass

    return result


def _step_headings_worker(path: Path, *, dry_run: bool) -> Result:
    text = path.read_text(encoding="utf-8")
    fm_block, body = _split_frontmatter(text)
    new_body = _step_headings_text(body)
    if new_body == body:
        return "skip", path.name
    if dry_run:
        return "dry-run", path.name
    path.write_text(fm_block + new_body, encoding="utf-8")
    return "done", path.name
