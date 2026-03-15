# Architecture

## Package Structure

```text
src/rematter/
├── __init__.py     re-exports everything; keeps test imports stable
├── _core.py        _load(), _dump(), regex constants (FRONTMATTER_RE, DATE_PREFIX_RE, WIKILINK_RE, TYPE_TAG_RE)
├── _workers.py     all workers, sync pipeline helpers, _run()/_sync_run() dispatchers, console singletons
└── cli.py          Typer app: filename, transform, sync commands
tests/
├── conftest.py     vault, empty_vault, mock_source, mock_dest fixtures
├── fixtures/       12 real Obsidian notes covering main frontmatter shapes
├── mock_source/    16 synthetic files for isolated sync testing (see .docs/sync-pipeline.md)
├── mock_dest/      2 synthetic files (already-synced + dest-only for corpus)
├── test_helpers.py _load / _dump unit tests
├── test_filename.py
├── test_transform.py
└── test_sync.py    wikilinks, type tags, creator resolution, schema validation, sync pipeline
```

## Design Principles

- Always operates on a flat directory of `.md` files — convention over flexibility
- Files without frontmatter, or missing the target field, are **silently skipped**
- Bad values (unparseable dates, collision targets) return `"error"` status and are surfaced to the user
- Thread pool size: `min(len(files), cpu_count)` — scales with directory size
- `_run()` owns discovery, threading, result aggregation, and summary output

## Key Gotcha: PyYAML 6 + Python 3.14 Datetime Parsing

PyYAML's `safe_load` does **not** auto-convert `2026-02-12 15:03` (no seconds) to a `datetime` object — it returns a plain string. `_filename_worker` handles all three value types:

1. `datetime.datetime` object → `.date()`
2. `datetime.date` object → used directly
3. String → try `date.fromisoformat()`, fallback to `datetime.fromisoformat().date()`

The `isinstance(raw, datetime)` check must come **before** `isinstance(raw, date)` since `datetime` is a subclass of `date`.

## Frontmatter Regex

`FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?(.*)", re.DOTALL)`

- Requires at least one newline of content — empty frontmatter (`---\n---`) returns `None` (treated as no frontmatter, silently skipped)
- Non-greedy `.*?` stops at the first `\n---`, so body `---` separators (e.g. Obsidian callout blocks) are preserved correctly

## Adding a New Command

1. Add a `_<name>_worker(path: Path, *, ..., dry_run: bool) -> Result` function in `_workers.py`
2. Add a `@app.command()` in `cli.py` that calls `_run(..., _<name>_worker, **kwargs)`
3. Re-export the worker from `__init__.py`
4. Add `tests/test_<name>.py` with worker unit tests + CLI integration tests via `typer.testing.CliRunner`

Note: `sync` is a special case — it uses `_sync_run()` instead of `_run()` because it operates on source+dest directory pairs and builds a shared corpus for link resolution. See `.docs/sync-pipeline.md`.
