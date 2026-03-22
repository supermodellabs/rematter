# Mermaid Rendering

Renders mermaid code blocks to SVG files during sync, behind the `--render / -r` flag.

## Architecture

Mermaid rendering is a **post-processing step** in `_sync_run()`, deliberately decoupled from the per-file sync workers. The main sync pipeline (parallel, fast) completes and prints results first. Then, if `--render` is set, a second pass identifies synced dest files containing mermaid blocks and renders them sequentially.

This design ensures non-mermaid files sync at full speed with zero overhead.

### Flow

1. Main sync completes, results print immediately
2. `_sync_run()` scans successfully synced dest files for `MERMAID_RE` matches
3. For each file with mermaid blocks:
   - Prints `🎨 rendering...` status
   - Calls `_render_mermaid_blocks(body, slug, dest)` on the dest file body
   - Rewrites the dest file with rendered body
   - Prints `✅ rendered` with diagram count
4. Dry-run reports counts without rendering or writing

## Library: `mermaid-py`

Uses the `mermaid-py` package (PyPI: `mermaid-py`), which renders via the [mermaid.ink](https://mermaid.ink) API. This was chosen over `mmdc` (PhantomJS-based) because PhantomJS cannot handle modern Mermaid syntax — HTML-in-nodes (`<b>`, `<br/>`, `<i>`) produces broken SVGs with tag mismatches.

Trade-off: requires network access. Future config file work (see CLAUDE.md to-do) will enable caching/skip logic to avoid re-rendering unchanged diagrams.

### IPython Warning

`mermaid-py` prints `"Warning: IPython is not installed..."` to stdout on import. The lazy import in `_render_mermaid_blocks` suppresses this with `contextlib.redirect_stdout(io.StringIO())`. Note: it prints to **stdout**, not stderr.

## Key Functions

| Function | Location | Purpose |
| --- | --- | --- |
| `_render_mermaid_blocks(body, slug, dest, media_config=None)` | `_workers.py` | Finds mermaid blocks via `MERMAID_RE`, renders each to SVG via `mermaid-py`, writes SVG files, returns updated body with `<img>` tags |
| `MERMAID_RE` | `_core.py` | `` ```mermaid\n(.*?)``` `` with `re.DOTALL` |

## Output

Each mermaid block becomes an SVG file and an img tag. Where the SVG is written and how it's referenced depends on whether `media_config` is provided:

**With `media_config`** (typical for Astro destinations):
- SVG written to: `dest / media_config.dest / {slug}-mermaid-{n}.svg`
- Img tag: `<img src="{link_prefix}/{slug}-mermaid-{n}.svg" alt="Mermaid diagram {n}" />`
- The media dest directory is created automatically (`mkdir(parents=True)`)

**Without `media_config`** (fallback):
- SVG written to: `dest / {slug}-mermaid-{n}.svg` (co-located with the doc)
- Img tag: `<img src="{slug}-mermaid-{n}.svg" alt="Mermaid diagram {n}" />`

This mirrors how `_resolve_media_refs` handles regular images — SVGs are treated as media assets, landing in the same configured directory with the same link prefix.

## Testing

All mermaid tests mock `_render_mermaid_blocks` via `_mock_render` in `test_sync.py` to avoid network calls to mermaid.ink. The mock mirrors the real function's `media_config` handling — writing to the media dest dir when configured, co-located otherwise.

Test classes:
- `TestMermaidRegex` — regex matching
- `TestRenderMermaidBlocks` — body replacement, file writing, surrounding content preservation (uses `_mock_render`)
- `TestSyncMermaidPostProcessing` — verifies sync worker doesn't render, post-processing is separate
- `TestMermaidMediaDir` — SVGs written to media dest dir with link prefix when `media_config` present, co-located without
- `TestSyncCLI` — integration tests for `--render` flag, dry-run, and status output (mocked)
