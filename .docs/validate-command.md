# Validate Command

Schema-based frontmatter validation for markdown files. Uses the standard `_run()` dispatcher (not a custom runner like sync).

## Schema Format

Schema lives in `.rematter.yaml` by default (see `.docs/config-and-media.md` for the combined config+schema format). Legacy `_schema.yml` still works with a deprecation warning. Overridable with `--schema`.

```yaml
properties:
  created:
    type: timestamp
    required: true
    default: "%Y-%m-%d %H:%M"  # strftime format → stamped with current time on --fix
    sync: false                 # stripped from dest during sync
  synced:
    type: timestamp
    required: true
    default: null               # adds key with null value — never stamp a fake sync time
  publish:
    type: bool
    required: true
    default: false
    sync: false
  status:
    type: string
    required: false
    enum: [not_started, in_progress, on_hold, done, cancelled]
    default: not_started
  creators:
    type: list
    required: false
```

### Property Spec Fields

| Field | Purpose |
| --- | --- |
| `type` | One of: `timestamp`, `bool`, `string`, `list`, `int`, `float` |
| `required` | Key must exist in frontmatter (null values are valid) |
| `default` | Value to set on `--fix` if missing. See default semantics below |
| `enum` | Allowed values (string fields) |
| `requires` | List of companion fields that must also have values (co-dependency) |
| `sync` | Boolean (default `true`). When `false`, field is recognized but stripped from dest during sync |

### Default Semantics

Three distinct cases, distinguished by a `_MISSING` sentinel in `_resolve_default`:

| Schema | Meaning | `--fix` behavior |
| --- | --- | --- |
| `default: "%Y-%m-%d %H:%M"` | strftime format | Stamps `datetime.now()` formatted by the string |
| `default: null` | Explicit null | Adds the key with a null value |
| *(no default key)* | No default | Cannot auto-fix — errors as unfixable |

Timestamp defaults are validated at schema load time (`_validate_schema_defaults`). A timestamp default **must** be a strftime format string containing `%` directives. Literal date strings (e.g. `default: "2026-01-01"`) and invalid format codes are rejected immediately.

### Semantics

- `required: true` means the key must exist — null is a valid value (e.g. `synced: null` before first sync)
- All properties must be declared in the schema — unrecognized frontmatter keys always error
- `sync: false` marks a property as source-only — recognized by validation but stripped from dest during sync
- Validation is two-phase: structural (missing/unrecognized) → value-level (type, enum). Structural errors bail early.
- `--fix` reorders keys to match schema `properties` order. Schema-defined keys come first (in declaration order). This is cosmetic but matters for Obsidian vault scanning — properties have colored icons and a consistent order makes them easy to scan visually.

## CLI

```bash
rematter validate <directory> [--schema PATH] [--fix] [--recursive] [--dry-run]
```

- Default mode: report-only, exit 1 if any files fail
- `--fix`: set defaults for missing properties that have a `default` defined, and reorder keys to match schema property order (schema-defined keys first, extras appended)
- `--fix` with required field missing and no default → still errors
- `--dry-run` + `--fix`: show what would change without writing

## Implementation

Key functions in `_workers.py`:

- `_load_config(source_dir, explicit_path=None)` — finds and loads `.rematter.yaml` or legacy `_schema.yml`, returns `RematterConfig`
- `_load_schema(path)` — reads standalone YAML schema, validates timestamp defaults (backward compat)
- `_validate_schema_defaults(schema)` — rejects literal dates and invalid strftime codes for timestamp defaults
- `_validate_against_schema(fm, schema)` — pure validation including `requires` co-dependency checks, returns error list
- `_resolve_default(spec)` — returns resolved default, `None` for explicit null, or `_MISSING` sentinel when no default key exists
- `_validate_worker(path, *, schema, fix, dry_run)` — per-file worker following standard `Result` pattern

Type checking uses `_SCHEMA_TYPE_CHECKERS` dict mapping type names to lambdas. The `timestamp` checker reuses `_is_timestamp_like()` from the sync pipeline.

## One-Off Scripts

`scripts/` contains recovery scripts for vault data issues. Not part of the CLI — run directly with `uv run scripts/<name>.py`.

- `fix_literal_formats.py` — finds frontmatter values containing strftime directives (`%Y`, etc.) and replaces with `datetime.now()` formatted by that string. Schema-free: scans all string values in all `.md` files.
- `clear_synced.py` — sets all non-null `synced` values to null. For resetting sync state after bad data.

Both support `--dry-run` and recurse into subdirectories.

## Planned Enhancements

- `nullable: false` schema option for strict non-null enforcement
- Type-tag conditional validation (different required fields per type tag)
