# Architecture

## Package Structure

```text
src/rematter/
├── __init__.py     re-exports everything; keeps test imports stable
├── _core.py        _load(), _dump(), regex constants, _slugify()
├── _workers.py     all workers, config/schema loading, sync/validate helpers, dispatchers, ignore filtering, console singletons
└── cli.py          Typer app: date-extract, transform, sync, validate commands
tests/
├── conftest.py     mock_source, mock_dest, empty_vault fixtures
├── mock_source/    29 .md files + .rematter.yaml + _media/ — all fixture data lives here
├── mock_dest/      2 synthetic files (already-synced + dest-only for corpus)
├── test_helpers.py _load / _dump unit tests
├── test_date_extract.py
├── test_transform.py
├── test_sync.py    wikilinks, type tags, creator resolution, schema validation, sync pipeline, media sync, hero images
└── test_validate.py schema validation, fix mode, config loading, CLI integration
```

## Regex Constants in `_core.py`

| Name | Purpose |
| --- | --- |
| `FRONTMATTER_RE` | Extracts YAML frontmatter block and body |
| `DATE_PREFIX_RE` | Detects files already prefixed with `YYYY-MM-DD -` |
| `WIKILINK_RE` | Matches `[[target]]` and `[[target\|label]]` — has `(?<!\!)` lookbehind to skip image refs |
| `WIKILINK_IMAGE_RE` | Matches `![[file.png]]` and `![[file.png\|alt]]` image refs |
| `MD_IMAGE_RE` | Matches standard markdown images `![alt](path)` |
| `TYPE_TAG_RE` | Matches capitalized Obsidian tags like `#Book` |

## Design Principles

- Always operates on a flat directory of `.md` files — convention over flexibility
- Files without frontmatter, or missing the target field, are **silently skipped**
- Bad values (unparseable dates, collision targets) return `"error"` status and are surfaced to the user
- Thread pool size: `min(len(files), cpu_count)` — scales with directory size
- `_run()` owns discovery, threading, result aggregation, and summary output

## Key Gotcha: PyYAML 6 + Python 3.14 Datetime Parsing

PyYAML's `safe_load` does **not** auto-convert `2026-02-12 15:03` (no seconds) to a `datetime` object — it returns a plain string. `_date_extract_worker` handles all three value types:

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
