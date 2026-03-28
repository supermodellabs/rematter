# Config File, Media Sync, and Hero Images

## Config File (`.rematter.yaml`)

Combined config + schema file. Replaces the old `_schema.yml` (which is still loaded as a legacy fallback with a deprecation warning to stderr).

### Format

```yaml
# Config keys (top-level)
link_path_prefix: /sky
dest: ~/dev/winnie-sh/src/content/sky/
ignore:
  - draft-*
  - private/*
media:
  source: _media
  dest: src/assets
  link_prefix: /assets

# Schema
properties:
  status:
    type: string
    required: false
    enum: [not_started, in_progress, on_hold, done, cancelled]
  own:
    type: bool
    required: false
    sync: false
  hero:
    type: string
    required: false
    requires: [heroAlt]
  created:
    type: timestamp
    required: true
    sync: false
  publish:
    type: bool
    required: true
    sync: false
```

### Config Keys

| Key | Type | Purpose | CLI override |
| --- | --- | --- | --- |
| `link_path_prefix` | string | URL prefix for resolved wikilinks (e.g. `/sky`) | `--link-path-prefix / -l` |
| `dest` | string | Destination directory for synced files | `--dest / -d` |
| `media` | object | Media sync config (see below) | *(none — config only)* |
| `ignore` | list | Glob patterns for files/dirs to skip (matched against filename and relative path) | *(none — config only)* |

`dry_run` is CLI-only — it would not make sense in a config file.

### Loading (`_load_config`)

Lookup order:

1. Explicit path (from `--schema` or `--config` flag)
2. `.rematter.yaml` in source directory
3. `_schema.yml` in source directory (legacy, prints deprecation warning)
4. `FileNotFoundError` with migration instructions

Returns a `RematterConfig` dataclass. The `.schema` property returns `{properties}` for `_validate_against_schema`. The `.no_sync_fields` property derives the set of field names where `sync: false`.

`_load_schema()` is kept for backward compat when loading standalone schema files via `--schema`.

### Ignore Patterns

The `ignore` key is a list of glob patterns. Each file is checked against patterns using both its filename and its path relative to the base directory. Uses stdlib `fnmatch`. `_filter_ignored()` applies filtering in both `_run()` (filename/transform/validate) and `_sync_run()` (sync).

### Per-Property `sync` Key

Boolean, defaults to `true`. When `sync: false`, the property is recognized by validation (not flagged as unrecognized) but stripped from dest output during sync. All properties must be declared in the schema — there is no `allow_extra` toggle.

## Media Sync

Configured via the `media` top-level key in `.rematter.yaml`. When absent, media processing is skipped entirely.

### `MediaConfig` Dataclass

```python
@dataclass
class MediaConfig:
    source: str    # relative to source dir, e.g. "_media"
    dest: str      # relative to dest dir, e.g. "src/assets"
    link_prefix: str  # URL prefix for rewritten refs, e.g. "/assets"
```

### Image Reference Handling

`_resolve_media_refs()` handles two image syntaxes:

| Syntax | Example | Output |
| --- | --- | --- |
| Wikilink image | `![[photo.png]]` | `![photo.png](/assets/photo.png)` |
| Wikilink image with alt | `![[photo.png\|My Photo]]` | `![My Photo](/assets/photo.png)` |
| Markdown image | `![alt](_media/photo.png)` | `![alt](/assets/photo.png)` |

Only images where the source file actually exists in the media source directory are rewritten and added to the copy list. Nonexistent references are left unchanged.

Markdown images are only rewritten when their path starts with the configured `media.source` prefix (e.g. `_media/`). Images pointing elsewhere are untouched.

### WIKILINK_RE Image Collision Fix

`WIKILINK_RE` has a `(?<!\!)` negative lookbehind so it does not match `![[image.png]]` (which would previously have been incorrectly resolved as a regular wikilink). `WIKILINK_IMAGE_RE` handles image refs separately.

### File Copying

During sync, referenced media files are copied with `shutil.copy2` to `dest / media_config.dest`. The dest media directory is created with `mkdir(parents=True, exist_ok=True)`. Only files actually referenced in markdown bodies (or hero fields) are copied — not the entire media directory.

Dry-run skips all file copies.

## Hero Images

Hero images use two co-dependent frontmatter properties. The hero value is typically a wikilink in Obsidian (the natural way to reference images in frontmatter), but raw paths are also supported:

```yaml
# Wikilink format (preferred in Obsidian)
hero: "[[hero-banner.jpg]]"
heroAlt: A description of the hero image

# Raw path format (also works)
hero: _media/hero-banner.jpg
heroAlt: A description of the hero image
```

### Schema: `requires` Key

Generic co-dependency validation, not hero-specific:

```yaml
hero:
  type: string
  required: false
  requires: [heroAlt]
heroAlt:
  type: string
  required: false
  requires: [hero]
```

In `_validate_against_schema()`: if a field has a non-null value and any field listed in its `requires` array is missing or null, an error is emitted. Both absent → valid. Both present → valid. One without the other → error.

### Sync Behavior

After body media resolution, `_sync_worker` checks for `hero` in frontmatter:

1. If `hero` is present and `media_config` exists
2. Strip wikilink syntax if present (`[[hero.jpg]]` → `hero.jpg`) via regex
3. Extract the filename from the resulting path
4. Check if the source file exists in the media source dir
5. If so: rewrite the hero value to `{link_prefix}/{filename}` and add the file to the media copy list

Without `media_config`, the hero path passes through unchanged.

## Tests

New test classes in `test_sync.py`:

| Class | Coverage |
| --- | --- |
| `TestWikilinkImageCollision` | `WIKILINK_RE` does not match `![[...]]`, `_resolve_wikilinks` ignores image refs |
| `TestResolveMediaRefs` | Wikilink/markdown image rewriting, nonexistent files unchanged, selective collection |
| `TestMediaSync` | Integration: files copied to dest, dry-run safety, no-config skip |
| `TestHeroImage` | Hero wikilink + raw path rewriting, hero without media config unchanged |
| `TestRequiresValidation` | Co-dependency: hero without heroAlt errors, both present OK, neither present OK |
| `TestConfigLoading` | CLI override precedence, config provides prefix, no-dest errors |

In `test_validate.py`:

| Test | Coverage |
| --- | --- |
| `test_load_config_from_directory` | Happy path with `.rematter.yaml` |
| `test_load_config_missing_raises` | No config file → `FileNotFoundError` |
| `test_load_config_legacy_fallback` | `_schema.yml` still loads with deprecation warning |
| `test_load_config_explicit_path` | Custom path via `explicit_path` parameter |
| `test_load_schema_legacy` | `_load_schema` backward compat |
