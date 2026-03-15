---
created: 2026-03-09 11:01
modified: 2026-03-09 11:01
synced:
publish:
---
The 'sky/' is full of 'stars'. Some connect into 'constellations', linking creators and their work. More connections will evolve.

Types are defined using capitalized tags. Tag types have a set of properties they need to have. Multiple tags requires merging all the properties of the specified tags.

## Base

All 'star' notes require:

```yaml
created: timestamp # set automatically on note creation or frontmatter insertion
modified: timestamp # set automatically when file with this property is modified
synced: timestamp # set automatically when the export-import to Astro homepage script is run
publish: boolean # indicates whether that script should process this star note or not
```

Some notes that can change status have a unified `status` property, consisting of 5 self-explanatory states. Obsidian does not yet have an enum/set/select data type, so this is a string, but any statuses outside the below should be considered invalid. No customized statuses should be used outside of the presentation layer (e.g., in a Reading List base, you could choose to have the labels show up as something more customized for books, like mapping 'cancelled' to 'Did Not Finish'). Another example: do not add unique properties like 'phase' to essays with states like 'editing', 'published', etc. — use the unified `status` property and map those to what you need.

```yaml
status: string # one of {'not_started', 'in_progress', 'on_hold', 'done', 'cancelled'}
```

## Types

These are all additive onto the base.

### Dataset

```yaml
is_meta_catalog: boolean # is this a collection of many resources?
is_api: boolean # is this the public API of a service (as opposed to a static dataset)
```

### Book, Film, Anime, Manga, Comic, TV, Music

Media types get these additional properties.

```yaml
status: # see above, corresponds to watching/reading/listening
creators: [] # list of strings (which can be internal wikilinks)
own: boolean # do I own this in some form?
```