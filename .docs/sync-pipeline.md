# Sync Pipeline

Syncs Obsidian vault markdown into an Astro content collection. Unlike `filename` and `transform` (which use `_run()` for simple per-file mutations), sync has its own dispatcher `_sync_run()` and a multi-stage worker pipeline.

## Config Resolution

The sync command resolves settings from both CLI flags and `.rematter.yaml` config files. CLI flags always win. See `.docs/config-and-media.md` for the config file format.

Resolution order for each setting:

| Setting | CLI flag | Config key | Required? |
| --- | --- | --- | --- |
| dest | `--dest / -d` | `dest` | Yes — errors if neither provided |
| link_path_prefix | `--link-path-prefix / -l` | `link_path_prefix` | Yes — errors if neither provided |
| render | `--render / -g` | `render` | No — defaults to `false` |
| media | *(none)* | `media` | No — media sync skipped if absent |

## Worker Pipeline (`_sync_worker`)

Each source file passes through these stages in order:

1. **Load** — `_load()`, skip if no frontmatter
2. **Publish gate** — `fm.get("publish") is not True` → skip (handles missing, null, false, non-bool)
3. **Slug + dest lookup** — `title = path.stem`, `slug = _slugify(title)`, dest file = `dest / f"{slug}.md"`
4. **Modified comparison** — if dest file exists and has same `modified` value → skip
5. **Type tag extraction** — `_extract_type_tags(body)` finds capitalized Obsidian tags (`#Book`, `#Film`, `#TV`), returns lowercased types + body with tag-only lines stripped
6. **Multi-type gate** — more than one capitalized tag → warn and skip (lowercase tags are ignored, treated as content)
7. **Schema validation** — `_validate_against_schema(fm, schema)` when a schema is provided; same function used by the `validate` command. Schema is the single source of truth for field names, types, and requirements
8. **Creator resolution** — `_resolve_creators()` converts `fm["creators"]` from wikilinks/strings to `{name, slug}` objects
9. **Body wikilink resolution** — `_resolve_wikilinks()` on cleaned body. `WIKILINK_RE` has a `(?<!\!)` lookbehind so `![[image.png]]` refs are not mangled
10. **Media resolution** — if `media_config` is present, `_resolve_media_refs()` rewrites `![[image.png]]` and `![alt](_media/image.png)` refs, collects files to copy
11. **Hero rewrite** — if `media_config` and `hero` field present, strips wikilink syntax (`[[hero.jpg]]` → `hero.jpg`), rewrites hero path, adds to media copy list
12. **Set `synced`** — `strftime("%Y-%m-%d %H:%M")` to match Obsidian's native format (space separator, no seconds)
13. **Set `title`** — source filename stem (original, not slugified)
14. **Set `type`** — scalar from single type tag, omitted if none
15. **Strip no-sync fields** — fields with `sync: false` in schema are removed from dest output (falls back to `_SYNC_NO_SYNC_FIELDS_DEFAULT`: `own`, `publish`, `created` when no schema provided)
16. **Write dest** — transformed content to slugified dest file
17. **Copy media** — referenced media files copied to dest media dir (mkdir + `shutil.copy2`)
18. **Write source** — stamp `synced` back on source file (original body + frontmatter preserved, only `synced` updated)

Source write-back uses a shallow copy of the frontmatter taken before any mutations (creator resolution, type setting). This ensures the source file retains its wikilinks, type tags, and all original content.

Dry-run skips writes (steps 16-18) and media copies.

## Output

Sync output starts with a `📂 source → dest` path header showing where files are being synced, followed by per-file results and the summary line.

## Mermaid Post-Processing

When `--render` is passed, `_sync_run()` runs a second pass after the main sync completes. This is deliberately decoupled from the sync pipeline so non-mermaid files complete at full speed. `media_config` is threaded through to `_render_mermaid_blocks` so generated SVGs land in the media dest directory (not co-located with docs) when media is configured.

See `.docs/mermaid-rendering.md` for details.

## Filename Slugification

Dest filenames are slugified using `python-slugify` via `_slugify()` in `_core.py`. Source filename `"Publishable Book.md"` → dest filename `"publishable-book.md"`. The original filename is preserved as the `title` property in dest frontmatter.

The `_sync_run` corpus builder reads `title` from dest frontmatter (not dest filenames) to build `known_stems` for wikilink resolution.

## Creator Objects

Creator wikilinks in source are converted to `{name, slug}` YAML objects in dest:

```yaml
# source
creators:
  - "[[Naomi Alderman]]"
  - Brian Eno

# dest
creators:
  - name: Naomi Alderman
    slug: naomi-alderman
  - name: Brian Eno
    slug: brian-eno
```

All creators get both `name` and `slug`, whether they were wikilinks or plain strings.

## Source-Only Fields

Fields with `sync: false` in the schema are validated in source but stripped from dest output. The set is derived from `RematterConfig.no_sync_fields` and threaded into `_sync_worker` as `no_sync_fields`. When no schema is provided, `_SYNC_NO_SYNC_FIELDS_DEFAULT` is used:

| Field | Reason |
| --- | --- |
| `own` | Obsidian-side tracking, not needed in Astro |
| `publish` | Implied by presence in dest |
| `created` | Never newer than `modified`; Astro site uses `modified` only |

## Schema Validation

Sync uses `_validate_against_schema()` — the same function as the `validate` command. The schema from `.rematter.yaml` is threaded into `_sync_worker` via `_sync_run`. There are no hardcoded field lists; the schema is the single source of truth for field names, types, required/optional status, enums, and co-dependencies.

If no `.rematter.yaml` exists, sync errors immediately — config is required.

`_is_timestamp_like()` validates timestamp fields: accepts `date`/`datetime` objects and ISO-parseable strings. Uses `date.fromisoformat()` with `datetime.fromisoformat()` fallback.

## Type Tag Regex

```python
TYPE_TAG_RE = re.compile(r"(?<!\w)#([A-Z][a-zA-Z]+)")
```

- Matches `#Book`, `#Film`, `#TV` (min 2 chars total)
- Does not match `# Book` (heading — space after `#`)
- Does not match `#book` (lowercase — requires initial capital)
- Negative lookbehind `(?<!\w)` prevents matching mid-word

Lines consisting entirely of type tags are stripped from the output body. Lines where tags appear alongside other content are preserved (tags remain in text).

Multiple capitalized tags (e.g., `#Book #Film`) are skipped with a warning — not an error. Lowercase tags are ignored entirely.

## Corpus and Link Resolution

`_sync_run()` builds `known_stems = src_stems | dest_stems` regardless of publish status. Source stems come from filenames; dest stems come from `title` in frontmatter (since dest filenames are slugified). This means:

- Links to unpublished source files resolve correctly
- Links to files only in dest (from previous syncs) resolve correctly
- Broken links (target in neither) become plain text

Body wikilinks resolve to markdown links for known stems, plain text for broken ones.

## Test Fixtures

Sync tests use dedicated `mock_source/` (29 .md files + `.rematter.yaml` + `_media/`) and `mock_dest/` (2 files) directories.

`mock_source` coverage:

| Category | Files |
| --- | --- |
| Publish gating | true, false, null, missing, string ("yes") |
| Modified comparison | Already Synced (same modified in src + dest) |
| Schema: missing fields | Missing Modified |
| Schema: unrecognized fields | Unrecognized Field (has `rating`) |
| Multi-type skip | Multi Type (`#Book #Film`) |
| Schema: bad values | Bad Timestamp (non-ISO `created`) |
| Type-specific validation | Book Missing Fields (missing creators/status/own) |
| Valid syncs | Publishable Book, Publishable Film, Known Author, Body Links, Heading Not Tag, Valid Dataset, Mermaid Diagram and Table |

`mock_dest` has 2 files (slugified names with `title` in frontmatter): an already-synced file (same `modified`) and a dest-only file for corpus inclusion.

Additional test classes for sync output:
- `TestSyncPathFeedback` — verifies source → dest path header appears in output
- `TestMermaidMediaDir` — SVGs go to media dir with link prefix when configured (in `test_sync.py`)

## Adding New Fields

Add new properties to the `properties` section of `.rematter.yaml`. The schema drives all validation for both `sync` and `validate` commands — no code changes needed for new fields.
