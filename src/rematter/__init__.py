"""rematter — frontmatter transformation tool for Obsidian vaults."""

from rematter._core import DATE_PREFIX_RE, FRONTMATTER_RE, TYPE_TAG_RE, WIKILINK_RE, _dump, _load, _slugify
from rematter._workers import (
    Result,
    Status,
    WorkerFn,
    _extract_type_tags,
    _filename_worker,
    _is_timestamp_like,
    _resolve_creators,
    _resolve_wikilinks,
    _run,
    _sync_run,
    _sync_worker,
    _transform_worker,
    _validate_sync_schema,
)
from rematter.cli import app

__all__ = [
    "DATE_PREFIX_RE",
    "FRONTMATTER_RE",
    "TYPE_TAG_RE",
    "WIKILINK_RE",
    "_dump",
    "_load",
    "_slugify",
    "Result",
    "Status",
    "WorkerFn",
    "_extract_type_tags",
    "_filename_worker",
    "_is_timestamp_like",
    "_resolve_creators",
    "_resolve_wikilinks",
    "_run",
    "_sync_run",
    "_sync_worker",
    "_transform_worker",
    "_validate_sync_schema",
    "app",
]
