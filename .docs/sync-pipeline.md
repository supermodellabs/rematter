# Sync Pipeline

Syncs Obsidian vault markdown into an Astro content collection. Unlike `filename` and `transform` (which use `_run()` for simple per-file mutations), sync has its own dispatcher `_sync_run()` and a multi-stage worker pipeline.

## Worker Pipeline (`_sync_worker`)

Each source file passes through these stages in order:

1. **Load** — `_load()`, skip if no frontmatter
2. **Publish gate** — `fm.get("publish") is not True` → skip (handles missing, null, false, non-bool)
3. **Slug + dest lookup** — `title = path.stem`, `slug = _slugify(title)`, dest file = `dest / f"{slug}.md"`
4. **Modified comparison** — if dest file exists and has same `modified` value → skip
5. **Type tag extraction** — `_extract_type_tags(body)` finds capitalized Obsidian tags (`#Book`, `#Film`, `#TV`), returns lowercased types + body with tag-only lines stripped
6. **Multi-type gate** — more than one capitalized tag → warn and skip (lowercase tags are ignored, treated as content)
7. **Schema validation** — `_validate_sync_schema(fm, types)` runs structural checks first (missing fields, unrecognized fields), bails early if any fail, then value-level checks (timestamp format, bool type, type-specific fields, status enum)
8. **Creator resolution** — `_resolve_creators()` converts `fm["creators"]` from wikilinks/strings to `{name, slug}` objects
9. **Body wikilink resolution** — `_resolve_wikilinks()` on cleaned body
10. **Set `synced`** — ISO timestamp without microseconds
11. **Set `title`** — source filename stem (original, not slugified)
12. **Set `type`** — scalar from single type tag, omitted if none
13. **Strip source-only fields** — `own`, `publish`, `created` removed from dest output (`_SYNC_NO_SYNC_FIELDS`)
14. **Write dest** — transformed content to slugified dest file
15. **Write source** — stamp `synced` back on source file (original body + frontmatter preserved, only `synced` updated)

Source write-back uses a shallow copy of the frontmatter taken before any mutations (creator resolution, type setting). This ensures the source file retains its wikilinks, type tags, and all original content.

Dry-run skips both writes (steps 14-15).

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

These fields are validated in source but stripped from dest output:

| Field | Reason |
| --- | --- |
| `own` | Obsidian-side tracking, not needed in Astro |
| `publish` | Implied by presence in dest |
| `created` | Never newer than `modified`; Astro site uses `modified` only |

## Schema

Base required fields (`_SYNC_BASE_REQUIRED`): `created`, `modified`, `synced`, `publish`

All must be present in source frontmatter. Type constraints:

| Field | Type | Notes |
| --- | --- | --- |
| `created` | timestamp | date, datetime, or ISO string; must be non-null |
| `modified` | timestamp | date, datetime, or ISO string; must be non-null |
| `synced` | timestamp | may be null in source (gets stamped during sync) |
| `publish` | bool | must be `true` or `false`, not a string or int |

`_is_timestamp_like()` validates timestamps: accepts `date`/`datetime` objects and ISO-parseable strings. Uses `date.fromisoformat()` with `datetime.fromisoformat()` fallback.

Recognized fields (`_SYNC_KNOWN_FIELDS`): base fields + `type` + `status`, `creators`, `own` + `is_meta_catalog`, `is_api`. Any field outside this set triggers an error.

Validation is two-phase to fail fast:

1. **Structural** — missing required, unrecognized fields → bail immediately
2. **Value-level** — timestamp format, bool type, type-specific required fields, status enum

Type-specific required fields (triggered by capitalized tags in body):

| Type | Required Fields |
| --- | --- |
| book, film, anime, manga, comic, tv, music | `status`, `creators`, `own` |
| dataset | `is_meta_catalog`, `is_api` |

Valid `status` values: `not_started`, `in_progress`, `on_hold`, `done`, `cancelled`

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

Sync tests use dedicated `mock_source/` (16 files) and `mock_dest/` (2 files) directories, not the main `fixtures/` vault.

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
| Valid syncs | Publishable Book, Publishable Film, Known Author, Body Links, Heading Not Tag, Valid Dataset |

`mock_dest` has 2 files (slugified names with `title` in frontmatter): an already-synced file (same `modified`) and a dest-only file for corpus inclusion.

## Adding New Type Requirements

Add entries to `_SYNC_TYPE_REQUIRED` in `_workers.py`. Media types sharing the same field set can be added to the comprehension. Types with unique requirements get their own key. Add the new type's fields to `_SYNC_KNOWN_FIELDS` as well.
