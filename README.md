# rematter

Frontmatter management CLI for Obsidian vaults. Validate metadata against a schema, auto-fix missing fields, sync markdown to external destinations like Astro content collections, rename fields, and prepend dates to filenames -- all async with parallel threads for great performance on large vaults. Every command comes with dry-run support to preview the operations, your notes are precious, and we value that! We're Obsidian weirdos too.

The tool is still very early, there is much more to come.

## Install

The easiest way to install if you're a Python user is via `uv`, but there's also a compiled binary (via PyInstaller) available on Homebrew for macOS and Linux:

```bash
# uv (recommended)
uv tool install rematter

# Homebrew
brew install g15r/tap/rematter
```

## Quick start

```bash
# Validate frontmatter against a schema
rematter validate ~/vault/notes

# Auto-fix missing fields with defaults
rematter validate ~/vault/notes --fix

# Sync to an Astro content collection
rematter sync ~/vault/notes --dest ~/site/src/content/notes

# Preview any command without writing
rematter sync ~/vault/notes -n
```

## Commands

### `validate` -- Check frontmatter against a schema

```bash
rematter validate <directory> [--schema PATH] [--fix] [--recursive] [--dry-run]
```

Validates every markdown file against the `.rematter.yaml` schema in the target directory. Report-only by default -- exits `1` on failures. Unrecognized fields always error.

`--fix` sets defaults for missing properties and reorders keys to match schema order. Combine with `--dry-run` (`-n`) to preview what would change.

### `sync` -- Sync vault markdown to an external destination

```bash
rematter sync [source] [--dest PATH] [--link-path-prefix PREFIX] [--recursive] [--dry-run]
```

Syncs publishable markdown files to an external directory. The pipeline per file:

1. Skip files where `publish` is not `true`
2. Slugify the filename for the destination
3. Skip if dest has the same `modified` value (no changes)
4. Extract type from capitalized Obsidian tags (`#Book` -> `type: book`)
5. Validate against schema
6. Resolve creator wikilinks to `{name, slug}` objects
7. Resolve body wikilinks to markdown links (broken links -> plain text)
8. Resolve media references and rewrite hero image paths
9. Stamp `synced` timestamp, set `title` from source filename
10. Strip `sync: false` fields from destination output
11. Write dest file, copy referenced media, stamp `synced` back on source

Requires a `.rematter.yaml` config with at least `dest` (or pass `--dest`). CLI flags override config values.

### `transform` -- Rename a frontmatter field

```bash
rematter transform <directory> --field OLD --to NEW [--recursive] [--dry-run]
```

Renames a field across all markdown files. Key order is preserved. Files where the target name already exists are skipped with an error.

### `date-extract` -- Prepend dates to filenames

```bash
rematter date-extract <directory> [--field DATE_FIELD] [--recursive] [--dry-run]
```

Reads a date field (default: `Date`), prepends `YYYY-MM-DD -` to the filename, and removes the field from frontmatter. Already-prefixed files are skipped. Useful for systems like Notion that export a date field, when you want to pull that out into the filename for Obsidian's filesystem-centric approach.

## Configuration

Each directory can be given a `.rematter.yaml` that combines sync config and frontmatter schema:

```yaml
# Sync config
link_path_prefix: /notes
dest: ~/site/src/content/notes/
ignore:
  - draft-*
  - private/*
media:
  source: _media
  dest: src/assets
  link_prefix: /assets

# Frontmatter schema
properties:
  status:
    type: string
    required: false
    enum: [not_started, in_progress, on_hold, done, cancelled]
    default: not_started
  creators:
    type: list
    required: false
  own:
    type: bool
    required: false
    default: false
    sync: false          # validated but stripped from dest
  hero:
    type: string
    required: false
    requires: [heroAlt]  # co-dependency: both or neither
  heroAlt:
    type: string
    required: false
    requires: [hero]
  created:
    type: timestamp
    required: true
    default: "%Y-%m-%d %H:%M"   # strftime format, stamps current time on --fix
    sync: false
  modified:
    type: timestamp
    required: true
    default: "%Y-%m-%d %H:%M"
  publish:
    type: bool
    required: true
    default: false
    sync: false
```

### Property spec fields

| Field | Purpose |
| --- | --- |
| `type` | `timestamp`, `bool`, `string`, `list`, `int`, `float` |
| `required` | Key must exist (null values are valid) |
| `default` | Value set by `--fix` when missing. strftime string for timestamps, literal for others, `null` for explicit null |
| `enum` | Allowed values (string fields) |
| `requires` | Companion fields that must also have values (co-dependency) |
| `sync` | `true` (default) or `false` -- field is validated but stripped from dest during sync |

### Sync config fields

| Field | Purpose |
| --- | --- |
| `dest` | Destination directory (overridable via `--dest`) |
| `link_path_prefix` | URL prefix for resolved wikilinks (overridable via `--link-path-prefix`) |
| `ignore` | Glob patterns for files to skip |
| `media.source` | Source media directory name (relative to vault dir) |
| `media.dest` | Destination media directory (relative to dest root) |
| `media.link_prefix` | URL prefix for media links in dest output |

## Development

```bash
uv sync --dev          # install deps
uv run pytest -v       # run tests
uv run rematter --help # run locally
```
