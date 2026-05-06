"""Tests for the sync command — wikilink resolution, schema validation, and sync pipeline."""

from __future__ import annotations

import re
from pathlib import Path

from typer.testing import CliRunner

from rematter import (
    WIKILINK_RE,
    MediaConfig,
    RematterConfig,
    _extract_type_tags,
    _is_timestamp_like,
    _load_config,
    _resolve_creators,
    _resolve_media_refs,
    _resolve_wikilinks,
    _slugify,
    _sync_worker,
    _validate_against_schema,
    app,
)

# Minimal schema matching the fixture .rematter.yaml for _sync_worker unit tests
SYNC_SCHEMA = {
    "properties": {
        "status": {
            "type": "string",
            "required": False,
            "enum": ["not_started", "in_progress", "on_hold", "done", "cancelled"],
        },
        "creators": {"type": "list", "required": False},
        "own": {"type": "bool", "required": False, "sync": False},
        "is_meta_catalog": {"type": "bool", "required": False},
        "is_api": {"type": "bool", "required": False},
        "url": {"type": "string", "required": False},
        "hero": {"type": "string", "required": False, "requires": ["heroAlt"]},
        "heroAlt": {"type": "string", "required": False, "requires": ["hero"]},
        "created": {"type": "timestamp", "required": True, "sync": False},
        "modified": {"type": "timestamp", "required": True},
        "synced": {"type": "timestamp", "required": True},
        "publish": {"type": "bool", "required": True, "sync": False},
    },
}


runner = CliRunner()


# ── _slugify unit tests ──────────────────────────────────────────────────────


class TestSlugify:
    def test_simple_name(self) -> None:
        assert _slugify("Andy Matuschak") == "andy-matuschak"

    def test_apostrophe_stripped(self) -> None:
        assert _slugify("Why Books Don't Work") == "why-books-don-t-work"

    def test_dash_separator(self) -> None:
        assert (
            _slugify("Zhuangzi - The Complete Writings")
            == "zhuangzi-the-complete-writings"
        )

    def test_single_word(self) -> None:
        assert _slugify("Bodymind") == "bodymind"


# ── _resolve_wikilinks unit tests ─────────────────────────────────────────────


class TestResolveWikilinks:
    """Unit tests for wikilink resolution logic."""

    def test_valid_wikilink_converted_to_markdown(self) -> None:
        body = "Check out [[Andy Matuschak]] for more."
        result = _resolve_wikilinks(body, {"Andy Matuschak"}, "/sky")
        assert result == "Check out [Andy Matuschak](/sky/andy-matuschak) for more."

    def test_valid_wikilink_with_label(self) -> None:
        body = "Study your [[Bodymind|bodymind]] carefully."
        result = _resolve_wikilinks(body, {"Bodymind"}, "/sky")
        assert result == "Study your [bodymind](/sky/bodymind) carefully."

    def test_broken_wikilink_becomes_plain_text(self) -> None:
        body = "See [[Nonexistent Page]] for details."
        result = _resolve_wikilinks(body, {"Other File"}, "/sky")
        assert result == "See Nonexistent Page for details."

    def test_broken_wikilink_with_label_uses_label(self) -> None:
        body = "Study your [[Bodymind|bodymind]] carefully."
        result = _resolve_wikilinks(body, set(), "/sky")
        assert result == "Study your bodymind carefully."

    def test_multiple_wikilinks_mixed(self) -> None:
        body = "By [[Andy Matuschak]] about [[Spaced Repetition|spaced repetition]]."
        known = {"Andy Matuschak"}
        result = _resolve_wikilinks(body, known, "/sky")
        assert result == (
            "By [Andy Matuschak](/sky/andy-matuschak) about spaced repetition."
        )

    def test_no_wikilinks_unchanged(self) -> None:
        body = "Just regular markdown with [a link](https://example.com)."
        result = _resolve_wikilinks(body, {"Something"}, "/sky")
        assert result == body

    def test_output_dir_trailing_slash_stripped(self) -> None:
        body = "See [[Foo]]."
        result = _resolve_wikilinks(body, {"Foo"}, "/sky/")
        assert result == "See [Foo](/sky/foo)."

    def test_slug_lowercased_and_hyphenated(self) -> None:
        body = "[[Why Books Don't Work]]"
        result = _resolve_wikilinks(body, {"Why Books Don't Work"}, "/sky")
        assert result == "[Why Books Don't Work](/sky/why-books-don-t-work)"

    def test_multiple_wikilinks_on_same_line(self) -> None:
        body = "Both [[Bodymind|bodymind]] and [[Lifecraft|lifecraft]] matter."
        known = {"Bodymind", "Lifecraft"}
        result = _resolve_wikilinks(body, known, "/sky")
        assert result == (
            "Both [bodymind](/sky/bodymind) and [lifecraft](/sky/lifecraft) matter."
        )


# ── _extract_type_tags unit tests ────────────────────────────────────────────


class TestExtractTypeTags:
    def test_single_tag(self) -> None:
        tags, body = _extract_type_tags("#Book\n\nSome content.")
        assert tags == ["book"]
        assert body == "Some content."

    def test_multiple_tags(self) -> None:
        tags, body = _extract_type_tags("#Book #Film\n\nContent.")
        assert tags == ["book", "film"]
        assert body == "Content."

    def test_heading_not_tag(self) -> None:
        tags, body = _extract_type_tags("# Book\n\nThis is a heading.")
        assert tags == []
        assert "# Book" in body

    def test_lowercase_ignored(self) -> None:
        tags, body = _extract_type_tags("#book\n\nContent.")
        assert tags == []
        assert "#book" in body

    def test_tag_mid_body(self) -> None:
        tags, body = _extract_type_tags("Some text with #Film in the middle.")
        assert tags == ["film"]
        assert "Some text with" in body
        assert "#Film" in body  # line has non-tag content, so it stays

    def test_two_letter_tag(self) -> None:
        tags, _ = _extract_type_tags("#TV\n\nContent.")
        assert tags == ["tv"]


# ── _resolve_creators unit tests ─────────────────────────────────────────────


class TestResolveCreators:
    def test_known_wikilink_gets_slug(self) -> None:
        result = _resolve_creators(["[[Known Author]]"], {"Known Author"})
        assert result == [{"name": "Known Author", "slug": "known-author"}]

    def test_known_wikilink_with_label(self) -> None:
        result = _resolve_creators(["[[Some Person|Display Name]]"], {"Some Person"})
        assert result == [{"name": "Display Name", "slug": "some-person"}]

    def test_broken_wikilink_no_slug(self) -> None:
        result = _resolve_creators(["[[Unknown Person]]"], set())
        assert result == [{"name": "Unknown Person"}]

    def test_plain_string_no_slug(self) -> None:
        result = _resolve_creators(["Brook Ziporyn"], {"Brook Ziporyn"})
        assert result == [{"name": "Brook Ziporyn"}]

    def test_mixed_creators(self) -> None:
        result = _resolve_creators(
            ["[[Known Author]]", "Plain Name", "[[Ghost]]"],
            {"Known Author"},
        )
        assert result == [
            {"name": "Known Author", "slug": "known-author"},
            {"name": "Plain Name"},
            {"name": "Ghost"},
        ]


# ── _is_timestamp_like unit tests ────────────────────────────────────────────


class TestIsTimestampLike:
    def test_date_object(self) -> None:
        from datetime import date

        assert _is_timestamp_like(date(2026, 1, 1)) is True

    def test_datetime_object(self) -> None:
        from datetime import datetime

        assert _is_timestamp_like(datetime(2026, 1, 1, 12, 0)) is True  # noqa: DTZ001 — testing naive datetime acceptance

    def test_iso_date_string(self) -> None:
        assert _is_timestamp_like("2026-01-01") is True

    def test_iso_datetime_string(self) -> None:
        assert _is_timestamp_like("2026-01-01T12:00:00") is True

    def test_datetime_no_seconds_string(self) -> None:
        assert _is_timestamp_like("2026-01-01 12:00") is True

    def test_invalid_string(self) -> None:
        assert _is_timestamp_like("not-a-date") is False

    def test_integer(self) -> None:
        assert _is_timestamp_like(20260101) is False

    def test_none(self) -> None:
        assert _is_timestamp_like(None) is False


# ── _sync_worker unit tests ──────────────────────────────────────────────────


class TestSyncWorker:
    def _make_file(self, path: Path, content: str) -> Path:
        path.write_text(content, encoding="utf-8")
        return path

    def test_publish_gate_missing(self, tmp_path: Path) -> None:
        src = self._make_file(
            tmp_path / "note.md",
            "---\ncreated: 2026-01-01\nmodified: 2026-01-01\nsynced:\n---\nBody\n",
        )
        dest = tmp_path / "out"
        dest.mkdir()
        status, _ = _sync_worker(
            src, known_stems=set(), link_path_prefix="/sky", dest=dest, dry_run=False
        )
        assert status == "skip"

    def test_publish_gate_false(self, tmp_path: Path) -> None:
        src = self._make_file(
            tmp_path / "note.md",
            "---\ncreated: 2026-01-01\nmodified: 2026-01-01\nsynced:\npublish: false\n---\nBody\n",
        )
        dest = tmp_path / "out"
        dest.mkdir()
        status, _ = _sync_worker(
            src, known_stems=set(), link_path_prefix="/sky", dest=dest, dry_run=False
        )
        assert status == "skip"

    def test_publish_gate_null(self, tmp_path: Path) -> None:
        src = self._make_file(
            tmp_path / "note.md",
            "---\ncreated: 2026-01-01\nmodified: 2026-01-01\nsynced:\npublish:\n---\nBody\n",
        )
        dest = tmp_path / "out"
        dest.mkdir()
        status, _ = _sync_worker(
            src, known_stems=set(), link_path_prefix="/sky", dest=dest, dry_run=False
        )
        assert status == "skip"

    def test_publish_gate_string(self, tmp_path: Path) -> None:
        """publish: 'yes' (string, not bool) should be skipped at the gate."""
        src = self._make_file(
            tmp_path / "note.md",
            '---\ncreated: 2026-01-01\nmodified: 2026-01-01\nsynced:\npublish: "yes"\n---\nBody\n',
        )
        dest = tmp_path / "out"
        dest.mkdir()
        status, _ = _sync_worker(
            src, known_stems=set(), link_path_prefix="/sky", dest=dest, dry_run=False
        )
        assert status == "skip"

    def test_modified_comparison_skips_unchanged(self, tmp_path: Path) -> None:
        src = self._make_file(
            tmp_path / "note.md",
            "---\ncreated: 2026-01-01\nmodified: 2026-03-08 12:00:00\nsynced:\npublish: true\n---\nBody\n",
        )
        dest = tmp_path / "out"
        dest.mkdir()
        self._make_file(
            dest / "note.md",
            "---\nmodified: 2026-03-08 12:00:00\nsynced: 2026-03-08T13:00:00\ntitle: note\n---\nBody\n",
        )
        status, msg = _sync_worker(
            src, known_stems=set(), link_path_prefix="/sky", dest=dest, dry_run=False
        )
        assert status == "skip"
        assert "not modified" in msg

    def test_type_extraction_and_output(self, tmp_path: Path) -> None:
        src = self._make_file(
            tmp_path / "note.md",
            "---\ncreated: 2026-01-01\nmodified: 2026-01-01\nsynced:\npublish: true\n---\n#Book\n\nContent.\n",
        )
        dest = tmp_path / "out"
        dest.mkdir()
        status, _ = _sync_worker(
            src,
            known_stems=set(),
            link_path_prefix="/sky",
            dest=dest,
            dry_run=False,
            schema=SYNC_SCHEMA,
        )
        assert status == "done"
        content = (dest / "note.md").read_text()
        assert "type: book" in content
        assert "#Book" not in content

    def test_valid_book_synced_with_type(self, tmp_path: Path) -> None:
        src = self._make_file(
            tmp_path / "note.md",
            "---\ncreated: 2026-01-01\nmodified: 2026-01-01\nsynced:\npublish: true\n"
            "status: done\ncreators:\n  - Author\nown: true\n---\n#Book\n\nContent.\n",
        )
        dest = tmp_path / "out"
        dest.mkdir()
        status, _ = _sync_worker(
            src, known_stems=set(), link_path_prefix="/sky", dest=dest, dry_run=False
        )
        assert status == "done"
        content = (dest / "note.md").read_text()
        assert "type: book" in content
        assert "title: note" in content
        assert "synced:" in content
        # Type tag line stripped from body
        assert "#Book" not in content
        assert "Content." in content
        # Source-only fields stripped from dest
        assert "own:" not in content
        assert "publish:" not in content
        assert "created:" not in content

    def test_synced_timestamp_set_on_dest(self, tmp_path: Path) -> None:
        src = self._make_file(
            tmp_path / "note.md",
            "---\ncreated: 2026-01-01\nmodified: 2026-01-01\nsynced:\npublish: true\n---\nBody\n",
        )
        dest = tmp_path / "out"
        dest.mkdir()
        _sync_worker(
            src, known_stems=set(), link_path_prefix="/sky", dest=dest, dry_run=False
        )
        content = (dest / "note.md").read_text()
        assert "synced: '20" in content or "synced: 20" in content

    def test_synced_timestamp_matches_obsidian_format(self, tmp_path: Path) -> None:
        """Synced timestamp should match Obsidian format: YYYY-MM-DD HH:MM (no T, no seconds)."""
        import re

        src = self._make_file(
            tmp_path / "note.md",
            "---\ncreated: 2026-01-01\nmodified: 2026-01-01\nsynced:\npublish: true\n---\nBody\n",
        )
        dest = tmp_path / "out"
        dest.mkdir()
        _sync_worker(
            src, known_stems=set(), link_path_prefix="/sky", dest=dest, dry_run=False
        )
        content = (dest / "note.md").read_text()
        # Must be YYYY-MM-DD HH:MM with no T separator and no seconds
        assert re.search(
            r"synced: '?\d{4}-\d{2}-\d{2} \d{2}:\d{2}'?$", content, re.MULTILINE
        )
        assert "T" not in content.split("synced:")[1].split("\n")[0]

    def test_synced_timestamp_set_on_source(self, tmp_path: Path) -> None:
        """Source file should have synced stamped back after sync."""
        src = self._make_file(
            tmp_path / "note.md",
            "---\ncreated: 2026-01-01\nmodified: 2026-01-01\nsynced:\npublish: true\n---\nBody\n",
        )
        dest = tmp_path / "out"
        dest.mkdir()
        _sync_worker(
            src, known_stems=set(), link_path_prefix="/sky", dest=dest, dry_run=False
        )
        src_content = src.read_text()
        assert "synced: '20" in src_content or "synced: 20" in src_content

    def test_source_body_preserved_after_sync(self, tmp_path: Path) -> None:
        """Source should keep its original body — no wikilink resolution."""
        src = self._make_file(
            tmp_path / "note.md",
            "---\ncreated: 2026-01-01\nmodified: 2026-01-01\nsynced:\npublish: true\n"
            "status: done\ncreators:\n  - '[[Known]]'\nown: true\n---\n#Book\n\n[[Known]] is great.\n",
        )
        dest = tmp_path / "out"
        dest.mkdir()
        _sync_worker(
            src,
            known_stems={"Known"},
            link_path_prefix="/sky",
            dest=dest,
            dry_run=False,
        )
        src_content = src.read_text()
        # Source should still have wikilinks and type tags
        assert "[[Known]]" in src_content
        assert "#Book" in src_content
        # Creators should still be wikilinks
        assert "'[[Known]]'" in src_content

    def test_creators_resolved_to_objects(self, tmp_path: Path) -> None:
        """Creator wikilinks become name/slug objects in dest."""
        src = self._make_file(
            tmp_path / "note.md",
            "---\ncreated: 2026-01-01\nmodified: 2026-01-01\nsynced:\npublish: true\n"
            "status: done\ncreators:\n  - '[[Known]]'\nown: true\n---\n#Book\n\nBody.\n",
        )
        dest = tmp_path / "out"
        dest.mkdir()
        _sync_worker(
            src,
            known_stems={"Known"},
            link_path_prefix="/sky",
            dest=dest,
            dry_run=False,
        )
        content = (dest / "note.md").read_text()
        assert "name: Known" in content
        assert "slug: known" in content
        assert "[[" not in content

    def test_title_set_from_source_filename(self, tmp_path: Path) -> None:
        """Dest frontmatter should include title derived from source filename."""
        src = self._make_file(
            tmp_path / "My Great Note.md",
            "---\ncreated: 2026-01-01\nmodified: 2026-01-01\nsynced:\npublish: true\n---\nBody\n",
        )
        dest = tmp_path / "out"
        dest.mkdir()
        status, _ = _sync_worker(
            src, known_stems=set(), link_path_prefix="/sky", dest=dest, dry_run=False
        )
        assert status == "done"
        dest_file = dest / "my-great-note.md"
        assert dest_file.exists()
        content = dest_file.read_text()
        assert "title: My Great Note" in content

    def test_dest_filename_is_slugified(self, tmp_path: Path) -> None:
        """Dest filename should be the slugified source stem."""
        src = self._make_file(
            tmp_path / "Publishable Book.md",
            "---\ncreated: 2026-01-01\nmodified: 2026-01-01\nsynced:\npublish: true\n"
            "status: done\ncreators:\n  - Author\nown: true\n---\n#Book\n\nContent.\n",
        )
        dest = tmp_path / "out"
        dest.mkdir()
        _sync_worker(
            src, known_stems=set(), link_path_prefix="/sky", dest=dest, dry_run=False
        )
        assert (dest / "publishable-book.md").exists()
        assert not (dest / "Publishable Book.md").exists()

    def test_no_sync_fields_stripped_from_dest(self, tmp_path: Path) -> None:
        """Fields with sync: false should be stripped from dest output."""
        src = self._make_file(
            tmp_path / "note.md",
            "---\ncreated: 2026-01-01\nmodified: 2026-01-01\nsynced:\npublish: true\nstatus: done\n---\nBody\n",
        )
        dest = tmp_path / "out"
        dest.mkdir()
        # Strip publish, created, and status (but not modified or synced)
        _sync_worker(
            src,
            known_stems=set(),
            link_path_prefix="/sky",
            dest=dest,
            dry_run=False,
            no_sync_fields={"publish", "created", "status"},
        )
        content = (dest / "note.md").read_text()
        # status should be stripped (in no_sync_fields)
        assert "status:" not in content
        # publish, created should be stripped too
        assert "publish:" not in content
        assert "created:" not in content
        # modified should be preserved (not in no_sync_fields)
        assert "modified:" in content

    def test_default_no_sync_fields_used_when_none(self, tmp_path: Path) -> None:
        """When no_sync_fields is None, hardcoded defaults should be used."""
        src = self._make_file(
            tmp_path / "note.md",
            "---\ncreated: 2026-01-01\nmodified: 2026-01-01\nsynced:\npublish: true\nown: true\n---\nBody\n",
        )
        dest = tmp_path / "out"
        dest.mkdir()
        _sync_worker(
            src, known_stems=set(), link_path_prefix="/sky", dest=dest, dry_run=False
        )
        content = (dest / "note.md").read_text()
        # Default no-sync fields: own, publish, created
        assert "own:" not in content
        assert "publish:" not in content
        assert "created:" not in content

    def test_dry_run_does_not_write(self, tmp_path: Path) -> None:
        original = "---\ncreated: 2026-01-01\nmodified: 2026-01-01\nsynced:\npublish: true\n---\nBody\n"
        src = self._make_file(tmp_path / "note.md", original)
        dest = tmp_path / "out"
        dest.mkdir()
        status, _ = _sync_worker(
            src, known_stems=set(), link_path_prefix="/sky", dest=dest, dry_run=True
        )
        assert status == "dry-run"
        assert not (dest / "note.md").exists()
        assert src.read_text() == original  # source untouched too

    def test_no_frontmatter_skipped(self, tmp_path: Path) -> None:
        src = self._make_file(tmp_path / "bare.md", "No frontmatter here.\n")
        dest = tmp_path / "out"
        dest.mkdir()
        status, _ = _sync_worker(
            src, known_stems=set(), link_path_prefix="/sky", dest=dest, dry_run=False
        )
        assert status == "skip"

    def test_bad_timestamp_errors(self, tmp_path: Path) -> None:
        """Files with non-timestamp created values should fail validation."""
        src = self._make_file(
            tmp_path / "note.md",
            '---\ncreated: "not-a-date"\nmodified: 2026-01-01\nsynced:\npublish: true\n---\nBody\n',
        )
        dest = tmp_path / "out"
        dest.mkdir()
        status, msg = _sync_worker(
            src,
            known_stems=set(),
            link_path_prefix="/sky",
            dest=dest,
            dry_run=False,
            schema=SYNC_SCHEMA,
        )
        assert status == "error"
        assert "timestamp" in msg

    def test_multi_type_skipped_with_warning(self, tmp_path: Path) -> None:
        """Files with multiple type tags should be skipped (warn), not error."""
        src = self._make_file(
            tmp_path / "note.md",
            "---\ncreated: 2026-01-01\nmodified: 2026-01-01\nsynced:\npublish: true\n"
            "status: done\ncreators:\n  - Author\nown: true\n---\n#Book #Film\n\nContent.\n",
        )
        dest = tmp_path / "out"
        dest.mkdir()
        status, msg = _sync_worker(
            src, known_stems=set(), link_path_prefix="/sky", dest=dest, dry_run=False
        )
        assert status == "warn"
        assert "multiple type tags" in msg
        assert not (dest / "note.md").exists()

    def test_unrecognized_field_errors(self, tmp_path: Path) -> None:
        """Files with fields not in the known schema should be rejected."""
        src = self._make_file(
            tmp_path / "note.md",
            "---\ncreated: 2026-01-01\nmodified: 2026-01-01\nsynced:\npublish: true\n"
            "rating: 8.5\n---\nBody\n",
        )
        dest = tmp_path / "out"
        dest.mkdir()
        status, msg = _sync_worker(
            src,
            known_stems=set(),
            link_path_prefix="/sky",
            dest=dest,
            dry_run=False,
            schema=SYNC_SCHEMA,
        )
        assert status == "error"
        assert "unrecognized" in msg
        assert "rating" in msg
        assert not (dest / "note.md").exists()

    def test_valid_dataset_synced(self, tmp_path: Path) -> None:
        """Dataset type with is_meta_catalog and is_api should sync."""
        src = self._make_file(
            tmp_path / "note.md",
            "---\ncreated: 2026-01-01\nmodified: 2026-01-01\nsynced:\npublish: true\n"
            "is_meta_catalog: false\nis_api: true\n---\n#Dataset\n\nData.\n",
        )
        dest = tmp_path / "out"
        dest.mkdir()
        status, _ = _sync_worker(
            src, known_stems=set(), link_path_prefix="/sky", dest=dest, dry_run=False
        )
        assert status == "done"
        content = (dest / "note.md").read_text()
        assert "type: dataset" in content
        assert "created:" not in content
        assert "publish:" not in content

    def test_no_sync_fields_absent_from_dest(self, tmp_path: Path) -> None:
        """own, publish, created should not appear in dest output."""
        src = self._make_file(
            tmp_path / "note.md",
            "---\ncreated: 2026-01-01\nmodified: 2026-01-01\nsynced:\npublish: true\n"
            "status: done\ncreators:\n  - Author\nown: true\n---\n#Book\n\nContent.\n",
        )
        dest = tmp_path / "out"
        dest.mkdir()
        _sync_worker(
            src, known_stems=set(), link_path_prefix="/sky", dest=dest, dry_run=False
        )
        content = (dest / "note.md").read_text()
        assert "own:" not in content
        assert "publish:" not in content
        assert "created:" not in content
        # These should still be present
        assert "modified:" in content
        assert "synced:" in content


# ── CLI integration tests ────────────────────────────────────────────────────


class TestSyncCLI:
    """Integration tests using mock_source/mock_dest fixtures."""

    def test_publishable_files_synced(self, mock_source: Path, mock_dest: Path) -> None:
        runner.invoke(app, ["sync", str(mock_source), "--dest", str(mock_dest)])
        synced_names = {p.name for p in mock_dest.glob("*.md")}
        assert "publishable-book.md" in synced_names
        assert "known-author.md" in synced_names
        assert "body-links.md" in synced_names
        assert "heading-not-tag.md" in synced_names
        assert "valid-dataset.md" in synced_names

    def test_unpublished_files_skipped(
        self, mock_source: Path, mock_dest: Path
    ) -> None:
        runner.invoke(app, ["sync", str(mock_source), "--dest", str(mock_dest)])
        synced_names = {p.name for p in mock_dest.glob("*.md")}
        assert "no-publish-field.md" not in synced_names
        assert "publish-false.md" not in synced_names
        assert "publish-null.md" not in synced_names
        assert "publish-string.md" not in synced_names

    def test_already_synced_skipped(self, mock_source: Path, mock_dest: Path) -> None:
        original = (mock_dest / "already-synced.md").read_text()
        runner.invoke(app, ["sync", str(mock_source), "--dest", str(mock_dest)])
        # File should not be overwritten (same modified)
        assert (mock_dest / "already-synced.md").read_text() == original

    def test_schema_errors_reported(self, mock_source: Path, mock_dest: Path) -> None:
        result = runner.invoke(
            app, ["sync", str(mock_source), "--dest", str(mock_dest)]
        )
        assert result.exit_code == 1
        assert (
            "Missing Modified.md" in result.output
            or "missing required" in result.output
        )

    def test_corpus_includes_dest_files(
        self, mock_source: Path, mock_dest: Path
    ) -> None:
        runner.invoke(app, ["sync", str(mock_source), "--dest", str(mock_dest)])
        content = (mock_dest / "body-links.md").read_text()
        # Known Author is in source → resolved
        assert "[Known Author](" in content
        # Dest Only is in dest → resolved (title from dest frontmatter)
        assert "[Dest Only](" in content
        # Ghost is nowhere → plain text
        assert "Ghost" in content
        assert "[[Ghost]]" not in content

    def test_type_tags_in_output(self, mock_source: Path, mock_dest: Path) -> None:
        runner.invoke(app, ["sync", str(mock_source), "--dest", str(mock_dest)])
        content = (mock_dest / "publishable-book.md").read_text()
        assert "type: book" in content
        assert "#Book" not in content

    def test_title_in_output(self, mock_source: Path, mock_dest: Path) -> None:
        runner.invoke(app, ["sync", str(mock_source), "--dest", str(mock_dest)])
        content = (mock_dest / "publishable-book.md").read_text()
        assert "title: Publishable Book" in content

    def test_no_sync_fields_absent(self, mock_source: Path, mock_dest: Path) -> None:
        runner.invoke(app, ["sync", str(mock_source), "--dest", str(mock_dest)])
        content = (mock_dest / "publishable-book.md").read_text()
        assert "own:" not in content
        assert "publish:" not in content
        assert "created:" not in content

    def test_dry_run(self, mock_source: Path, mock_dest: Path) -> None:
        before = {p.name for p in mock_dest.glob("*.md")}
        runner.invoke(app, ["sync", str(mock_source), "--dest", str(mock_dest), "-n"])
        after = {p.name for p in mock_dest.glob("*.md")}
        assert before == after

    def test_empty_source(self, empty_vault: Path, tmp_path: Path) -> None:
        # Write a minimal config so sync gets past the config check
        (empty_vault / ".rematter.yaml").write_text(
            "link_path_prefix: /sky\nproperties:\n  publish:\n    type: bool\n    required: false\n"
        )
        dest = tmp_path / "out"
        result = runner.invoke(
            app,
            ["sync", str(empty_vault), "--dest", str(dest), "-l", "/sky"],
        )
        assert "No .md files" in result.output

    def test_nonexistent_source(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            [
                "sync",
                str(tmp_path / "nope"),
                "--dest",
                str(tmp_path / "out"),
                "-l",
                "/sky",
            ],
        )
        assert result.exit_code == 1

    def test_creator_objects_in_synced_file(
        self, mock_source: Path, mock_dest: Path
    ) -> None:
        runner.invoke(app, ["sync", str(mock_source), "--dest", str(mock_dest)])
        content = (mock_dest / "publishable-book.md").read_text()
        # [[Known Author]] should become name/slug object
        assert "name: Known Author" in content
        assert "slug: known-author" in content
        assert "[[Known Author]]" not in content

    def test_broken_creator_name_only(self, mock_source: Path, mock_dest: Path) -> None:
        runner.invoke(app, ["sync", str(mock_source), "--dest", str(mock_dest)])
        content = (mock_dest / "publishable-film.md").read_text()
        # [[Unknown Director]] is not in corpus → name only, no slug
        assert "name: Unknown Director" in content
        assert "slug:" not in content
        assert "[[Unknown Director]]" not in content

    def test_multi_type_skipped(self, mock_source: Path, mock_dest: Path) -> None:
        runner.invoke(app, ["sync", str(mock_source), "--dest", str(mock_dest)])
        synced_names = {p.name for p in mock_dest.glob("*.md")}
        assert "multi-type.md" not in synced_names

    def test_unrecognized_field_errors(
        self, mock_source: Path, mock_dest: Path
    ) -> None:
        runner.invoke(app, ["sync", str(mock_source), "--dest", str(mock_dest)])
        synced_names = {p.name for p in mock_dest.glob("*.md")}
        assert "unrecognized-field.md" not in synced_names

    def test_dataset_type_output(self, mock_source: Path, mock_dest: Path) -> None:
        runner.invoke(app, ["sync", str(mock_source), "--dest", str(mock_dest)])
        content = (mock_dest / "valid-dataset.md").read_text()
        assert "type: dataset" in content
        assert "#Dataset" not in content

    def test_bad_timestamp_error_reported(
        self, mock_source: Path, mock_dest: Path
    ) -> None:
        result = runner.invoke(
            app, ["sync", str(mock_source), "--dest", str(mock_dest)]
        )
        assert result.exit_code == 1
        assert "Bad Timestamp.md" in result.output or "timestamp" in result.output

    def test_publish_string_skipped(self, mock_source: Path, mock_dest: Path) -> None:
        runner.invoke(app, ["sync", str(mock_source), "--dest", str(mock_dest)])
        synced_names = {p.name for p in mock_dest.glob("*.md")}
        assert "publish-string.md" not in synced_names


# ── sync path feedback tests ────────────────────────────────────────────────


class TestSyncPathFeedback:
    """Sync output shows source and dest paths at the top."""

    def test_output_shows_path_header(self, tmp_path: Path) -> None:
        """Sync output starts with a source → dest path header."""
        src = tmp_path / "s"
        src.mkdir()
        (src / ".rematter.yaml").write_text(
            "link_path_prefix: /sky\nproperties:\n  publish:\n    type: bool\n    required: true\n"
        )
        (src / "note.md").write_text("---\npublish: true\n---\nContent.\n")
        dest = tmp_path / "d"
        result = runner.invoke(app, ["sync", str(src), "--dest", str(dest)])
        # Rich wraps long paths with newlines + padding — strip all whitespace
        collapsed = re.sub(r"\s+", "", result.output)
        assert "→" in collapsed
        assert re.sub(r"\s+", "", str(src)) in collapsed
        assert re.sub(r"\s+", "", str(dest)) in collapsed


# ── WIKILINK_RE image collision tests ─────────────────────────────────────────


class TestWikilinkImageCollision:
    """Ensure WIKILINK_RE does not match image wikilinks (![[...]])."""

    def test_image_wikilink_not_matched(self) -> None:
        body = "See ![[photo.png]] and [[Normal Link]]."
        matches = WIKILINK_RE.findall(body)
        assert len(matches) == 1
        assert matches[0][0] == "Normal Link"

    def test_image_with_alt_not_matched(self) -> None:
        body = "![[photo.png|My Photo]]"
        matches = WIKILINK_RE.findall(body)
        assert len(matches) == 0

    def test_resolve_wikilinks_ignores_images(self) -> None:
        body = "![[photo.png]] and [[Known]]."
        result = _resolve_wikilinks(body, {"Known"}, "/sky")
        assert "![[photo.png]]" in result
        assert "[Known](/sky/known)" in result


# ── _resolve_media_refs tests ─────────────────────────────────────────────────


class TestResolveMediaRefs:
    def _media_config(self) -> MediaConfig:
        return MediaConfig(source="_media", dest="src/assets", link_prefix="/assets")

    def test_wikilink_image_rewritten(self, tmp_path: Path) -> None:
        (tmp_path / "_media").mkdir()
        (tmp_path / "_media" / "photo.png").write_text("img")
        body = "Before ![[photo.png]] after."
        new_body, files = _resolve_media_refs(body, self._media_config(), tmp_path)
        assert "![photo.png](/assets/photo.png)" in new_body
        assert len(files) == 1
        assert files[0][1] == "photo.png"

    def test_wikilink_image_with_alt(self, tmp_path: Path) -> None:
        (tmp_path / "_media").mkdir()
        (tmp_path / "_media" / "photo.png").write_text("img")
        body = "![[photo.png|My Photo]]"
        new_body, files = _resolve_media_refs(body, self._media_config(), tmp_path)
        assert "![My Photo](/assets/photo.png)" in new_body
        assert len(files) == 1

    def test_markdown_image_rewritten(self, tmp_path: Path) -> None:
        (tmp_path / "_media").mkdir()
        (tmp_path / "_media" / "diagram.svg").write_text("svg")
        body = "![A diagram](_media/diagram.svg)"
        new_body, files = _resolve_media_refs(body, self._media_config(), tmp_path)
        assert "![A diagram](/assets/diagram.svg)" in new_body
        assert len(files) == 1

    def test_nonexistent_media_unchanged(self, tmp_path: Path) -> None:
        (tmp_path / "_media").mkdir()
        body = "![[nonexistent.png]]"
        new_body, files = _resolve_media_refs(body, self._media_config(), tmp_path)
        assert new_body == body
        assert files == []

    def test_only_referenced_files_collected(self, tmp_path: Path) -> None:
        (tmp_path / "_media").mkdir()
        (tmp_path / "_media" / "used.png").write_text("img")
        (tmp_path / "_media" / "unused.png").write_text("img")
        body = "![[used.png]]"
        _, files = _resolve_media_refs(body, self._media_config(), tmp_path)
        filenames = [f[1] for f in files]
        assert "used.png" in filenames
        assert "unused.png" not in filenames

    def test_md_image_outside_media_dir_unchanged(self, tmp_path: Path) -> None:
        body = "![alt](other/path/photo.png)"
        new_body, files = _resolve_media_refs(body, self._media_config(), tmp_path)
        assert new_body == body
        assert files == []


# ── media sync integration tests ──────────────────────────────────────────────


class TestMediaSync:
    def _make_file(self, path: Path, content: str) -> Path:
        path.write_text(content, encoding="utf-8")
        return path

    def test_media_files_copied_to_dest(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        media_dir = src_dir / "_media"
        media_dir.mkdir()
        (media_dir / "photo.png").write_text("fake-img")
        self._make_file(
            src_dir / "note.md",
            "---\ncreated: 2026-01-01\nmodified: 2026-01-01\nsynced:\npublish: true\n---\n"
            "![[photo.png]]\n",
        )
        dest = tmp_path / "out"
        dest.mkdir()
        mc = MediaConfig(source="_media", dest="assets", link_prefix="/assets")
        status, _ = _sync_worker(
            src_dir / "note.md",
            known_stems=set(),
            link_path_prefix="/sky",
            dest=dest,
            dry_run=False,
            media_config=mc,
        )
        assert status == "done"
        assert (dest / "assets" / "photo.png").exists()
        content = (dest / "note.md").read_text()
        assert "![photo.png](/assets/photo.png)" in content

    def test_dry_run_no_media_copy(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        media_dir = src_dir / "_media"
        media_dir.mkdir()
        (media_dir / "photo.png").write_text("fake-img")
        self._make_file(
            src_dir / "note.md",
            "---\ncreated: 2026-01-01\nmodified: 2026-01-01\nsynced:\npublish: true\n---\n"
            "![[photo.png]]\n",
        )
        dest = tmp_path / "out"
        dest.mkdir()
        mc = MediaConfig(source="_media", dest="assets", link_prefix="/assets")
        status, _ = _sync_worker(
            src_dir / "note.md",
            known_stems=set(),
            link_path_prefix="/sky",
            dest=dest,
            dry_run=True,
            media_config=mc,
        )
        assert status == "dry-run"
        assert not (dest / "assets").exists()

    def test_no_media_config_skips_processing(self, tmp_path: Path) -> None:
        self._make_file(
            tmp_path / "note.md",
            "---\ncreated: 2026-01-01\nmodified: 2026-01-01\nsynced:\npublish: true\n---\n"
            "![[photo.png]]\n",
        )
        dest = tmp_path / "out"
        dest.mkdir()
        status, _ = _sync_worker(
            tmp_path / "note.md",
            known_stems=set(),
            link_path_prefix="/sky",
            dest=dest,
            dry_run=False,
            media_config=None,
        )
        assert status == "done"
        content = (dest / "note.md").read_text()
        # Without media config, wikilink images are left as-is
        assert "![[photo.png]]" in content


# ── hero image tests ──────────────────────────────────────────────────────────


class TestHeroImage:
    def _make_file(self, path: Path, content: str) -> Path:
        path.write_text(content, encoding="utf-8")
        return path

    def test_hero_wikilink_rewritten_during_sync(self, tmp_path: Path) -> None:
        """Hero as wikilink [[hero.jpg]] should be resolved and rewritten."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        media_dir = src_dir / "_media"
        media_dir.mkdir()
        (media_dir / "hero.jpg").write_text("fake-jpg")
        self._make_file(
            src_dir / "note.md",
            "---\ncreated: 2026-01-01\nmodified: 2026-01-01\nsynced:\npublish: true\n"
            'hero: "[[hero.jpg]]"\nheroAlt: A hero image\n---\nBody.\n',
        )
        dest = tmp_path / "out"
        dest.mkdir()
        mc = MediaConfig(source="_media", dest="assets", link_prefix="/assets")
        status, _ = _sync_worker(
            src_dir / "note.md",
            known_stems=set(),
            link_path_prefix="/sky",
            dest=dest,
            dry_run=False,
            media_config=mc,
        )
        assert status == "done"
        content = (dest / "note.md").read_text()
        assert "hero: /assets/hero.jpg" in content
        assert "heroAlt: A hero image" in content
        assert (dest / "assets" / "hero.jpg").exists()

    def test_hero_raw_path_still_works(self, tmp_path: Path) -> None:
        """Hero as a raw path should still be handled for backward compat."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        media_dir = src_dir / "_media"
        media_dir.mkdir()
        (media_dir / "hero.jpg").write_text("fake-jpg")
        self._make_file(
            src_dir / "note.md",
            "---\ncreated: 2026-01-01\nmodified: 2026-01-01\nsynced:\npublish: true\n"
            "hero: _media/hero.jpg\nheroAlt: A hero image\n---\nBody.\n",
        )
        dest = tmp_path / "out"
        dest.mkdir()
        mc = MediaConfig(source="_media", dest="assets", link_prefix="/assets")
        status, _ = _sync_worker(
            src_dir / "note.md",
            known_stems=set(),
            link_path_prefix="/sky",
            dest=dest,
            dry_run=False,
            media_config=mc,
        )
        assert status == "done"
        content = (dest / "note.md").read_text()
        assert "hero: /assets/hero.jpg" in content
        assert (dest / "assets" / "hero.jpg").exists()

    def test_hero_without_media_config_unchanged(self, tmp_path: Path) -> None:
        self._make_file(
            tmp_path / "note.md",
            "---\ncreated: 2026-01-01\nmodified: 2026-01-01\nsynced:\npublish: true\n"
            'hero: "[[hero.jpg]]"\nheroAlt: Alt text\n---\nBody.\n',
        )
        dest = tmp_path / "out"
        dest.mkdir()
        status, _ = _sync_worker(
            tmp_path / "note.md",
            known_stems=set(),
            link_path_prefix="/sky",
            dest=dest,
            dry_run=False,
            media_config=None,
        )
        assert status == "done"
        content = (dest / "note.md").read_text()
        # Without media config, hero value passes through unchanged
        assert "[[hero.jpg]]" in content


# ── co-dependency validation tests ────────────────────────────────────────────


class TestRequiresValidation:
    def test_hero_without_heroalt_errors(self) -> None:
        schema = {
            "properties": {
                "hero": {"type": "string", "required": False, "requires": ["heroAlt"]},
                "heroAlt": {
                    "type": "string",
                    "required": False,
                    "requires": ["hero"],
                },
            },
        }
        fm = {"hero": "img.png"}
        errors = _validate_against_schema(fm, schema)
        assert any("heroAlt" in e for e in errors)

    def test_heroalt_without_hero_errors(self) -> None:
        schema = {
            "properties": {
                "hero": {"type": "string", "required": False, "requires": ["heroAlt"]},
                "heroAlt": {
                    "type": "string",
                    "required": False,
                    "requires": ["hero"],
                },
            },
        }
        fm = {"heroAlt": "Alt text"}
        errors = _validate_against_schema(fm, schema)
        assert any("hero" in e for e in errors)

    def test_both_present_passes(self) -> None:
        schema = {
            "properties": {
                "hero": {"type": "string", "required": False, "requires": ["heroAlt"]},
                "heroAlt": {
                    "type": "string",
                    "required": False,
                    "requires": ["hero"],
                },
            },
        }
        fm = {"hero": "img.png", "heroAlt": "Alt text"}
        errors = _validate_against_schema(fm, schema)
        assert errors == []

    def test_neither_present_passes(self) -> None:
        schema = {
            "properties": {
                "hero": {"type": "string", "required": False, "requires": ["heroAlt"]},
                "heroAlt": {
                    "type": "string",
                    "required": False,
                    "requires": ["hero"],
                },
            },
        }
        fm: dict = {}
        errors = _validate_against_schema(fm, schema)
        assert errors == []


# ── config loading tests ──────────────────────────────────────────────────────


class TestConfigLoading:
    def test_cli_override_precedence(self, mock_source: Path, mock_dest: Path) -> None:
        """CLI flags should override config values."""
        runner.invoke(
            app,
            [
                "sync",
                str(mock_source),
                "--dest",
                str(mock_dest),
                "-l",
                "/custom-prefix",
            ],
        )
        # Should use /custom-prefix instead of config's /sky
        content = (mock_dest / "body-links.md").read_text()
        assert "/custom-prefix/" in content

    def test_config_provides_prefix(self, mock_source: Path, mock_dest: Path) -> None:
        """Config link_path_prefix should be used when no CLI flag given."""
        runner.invoke(app, ["sync", str(mock_source), "--dest", str(mock_dest)])
        content = (mock_dest / "body-links.md").read_text()
        assert "/sky/" in content

    def test_no_dest_no_config_errors(self, tmp_path: Path) -> None:
        """No --dest and no config dest should error."""
        result = runner.invoke(app, ["sync", str(tmp_path)])
        assert result.exit_code != 0


# ── extract_type_tags config option tests ─────────────────────────────────────


class TestExtractTypeTagsOption:
    """Unit tests for the extract_type_tags config option via _sync_worker."""

    def _make_file(self, path: Path, content: str) -> Path:
        path.write_text(content, encoding="utf-8")
        return path

    def test_type_tags_not_extracted_when_disabled(self, tmp_path: Path) -> None:
        """When extract_type_tags is False, type tags are left in the body and no type field is set."""
        src = self._make_file(
            tmp_path / "note.md",
            "---\ncreated: 2026-01-01\nmodified: 2026-01-01\nsynced:\npublish: true\n---\n#Book\n\nContent.\n",
        )
        dest = tmp_path / "out"
        dest.mkdir()
        status, _ = _sync_worker(
            src,
            known_stems=set(),
            link_path_prefix="/sky",
            dest=dest,
            dry_run=False,
            extract_type_tags=False,
        )
        assert status == "done"
        content = (dest / "note.md").read_text()
        assert "type:" not in content
        assert "#Book" in content

    def test_type_tags_extracted_by_default(self, tmp_path: Path) -> None:
        """Default behavior (extract_type_tags=True) still extracts type tags."""
        src = self._make_file(
            tmp_path / "note.md",
            "---\ncreated: 2026-01-01\nmodified: 2026-01-01\nsynced:\npublish: true\n---\n#Book\n\nContent.\n",
        )
        dest = tmp_path / "out"
        dest.mkdir()
        status, _ = _sync_worker(
            src,
            known_stems=set(),
            link_path_prefix="/sky",
            dest=dest,
            dry_run=False,
        )
        assert status == "done"
        content = (dest / "note.md").read_text()
        assert "type: book" in content
        assert "#Book" not in content

    def test_multiple_type_tags_allowed_when_disabled(self, tmp_path: Path) -> None:
        """When extract_type_tags is False, multiple type tags do not cause a warning or skip."""
        src = self._make_file(
            tmp_path / "note.md",
            "---\ncreated: 2026-01-01\nmodified: 2026-01-01\nsynced:\npublish: true\n---\n#Book #Film\n\nContent.\n",
        )
        dest = tmp_path / "out"
        dest.mkdir()
        status, _ = _sync_worker(
            src,
            known_stems=set(),
            link_path_prefix="/sky",
            dest=dest,
            dry_run=False,
            extract_type_tags=False,
        )
        assert status == "done"
        content = (dest / "note.md").read_text()
        assert "type:" not in content
        assert "#Book" in content
        assert "#Film" in content

    def test_multiple_type_tags_still_rejected_when_enabled(
        self, tmp_path: Path
    ) -> None:
        """When extract_type_tags is True (default), multiple type tags still warn and skip."""
        src = self._make_file(
            tmp_path / "note.md",
            "---\ncreated: 2026-01-01\nmodified: 2026-01-01\nsynced:\npublish: true\n---\n#Book #Film\n\nContent.\n",
        )
        dest = tmp_path / "out"
        dest.mkdir()
        status, msg = _sync_worker(
            src,
            known_stems=set(),
            link_path_prefix="/sky",
            dest=dest,
            dry_run=False,
            extract_type_tags=True,
        )
        assert status == "warn"
        assert "multiple type tags" in msg

    def test_tag_only_lines_preserved_when_disabled(self, tmp_path: Path) -> None:
        """Tag-only lines should not be stripped from body when extraction is disabled."""
        src = self._make_file(
            tmp_path / "note.md",
            "---\ncreated: 2026-01-01\nmodified: 2026-01-01\nsynced:\npublish: true\n---\n#ShortStory\n\nOnce upon a time.\n",
        )
        dest = tmp_path / "out"
        dest.mkdir()
        status, _ = _sync_worker(
            src,
            known_stems=set(),
            link_path_prefix="/sky",
            dest=dest,
            dry_run=False,
            extract_type_tags=False,
        )
        assert status == "done"
        content = (dest / "note.md").read_text()
        assert "#ShortStory" in content
        assert "type:" not in content

    def test_config_extract_type_tags_default_true(self) -> None:
        """RematterConfig defaults extract_type_tags to True."""
        config = RematterConfig()
        assert config.extract_type_tags is True

    def test_config_extract_type_tags_loaded_from_yaml(self, tmp_path: Path) -> None:
        """extract_type_tags should be loaded from .rematter.yaml."""
        config_path = tmp_path / ".rematter.yaml"
        config_path.write_text(
            "extract_type_tags: false\nlink_path_prefix: /sky\nproperties:\n  publish:\n    type: bool\n    required: false\n"
        )
        config = _load_config(tmp_path)
        assert config.extract_type_tags is False


class TestExtractTypeTagsOptionCLI:
    """CLI integration tests for the extract_type_tags config option."""

    def test_disabled_via_config(self, tmp_path: Path) -> None:
        """extract_type_tags: false in config should skip extraction."""
        src = tmp_path / "src"
        src.mkdir()
        (src / ".rematter.yaml").write_text(
            "link_path_prefix: /sky\nextract_type_tags: false\nproperties:\n"
            "  created:\n    type: timestamp\n    required: true\n"
            "  modified:\n    type: timestamp\n    required: true\n"
            "  synced:\n    type: timestamp\n    required: true\n"
            "  publish:\n    type: bool\n    required: true\n    sync: false\n"
        )
        (src / "note.md").write_text(
            "---\ncreated: 2026-01-01\nmodified: 2026-01-01\nsynced:\npublish: true\n---\n#Author\n\nSome content.\n"
        )
        dest = tmp_path / "dest"
        result = runner.invoke(app, ["sync", str(src), "--dest", str(dest)])
        assert result.exit_code == 0
        content = (dest / "note.md").read_text()
        assert "#Author" in content
        assert "type:" not in content

    def test_multi_type_allowed_when_disabled(self, tmp_path: Path) -> None:
        """Multiple type tags should sync without error when extraction is disabled."""
        src = tmp_path / "src"
        src.mkdir()
        (src / ".rematter.yaml").write_text(
            "link_path_prefix: /sky\nextract_type_tags: false\nproperties:\n"
            "  created:\n    type: timestamp\n    required: true\n"
            "  modified:\n    type: timestamp\n    required: true\n"
            "  synced:\n    type: timestamp\n    required: true\n"
            "  publish:\n    type: bool\n    required: true\n    sync: false\n"
        )
        (src / "note.md").write_text(
            "---\ncreated: 2026-01-01\nmodified: 2026-01-01\nsynced:\npublish: true\n---\n#Author #ShortStory\n\nSome content.\n"
        )
        dest = tmp_path / "dest"
        result = runner.invoke(app, ["sync", str(src), "--dest", str(dest)])
        assert result.exit_code == 0
        content = (dest / "note.md").read_text()
        assert "#Author" in content
        assert "#ShortStory" in content
        assert "type:" not in content
