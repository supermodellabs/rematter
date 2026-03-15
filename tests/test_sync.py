"""Tests for the sync command — wikilink resolution, schema validation, and sync pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from rematter import (
    _extract_type_tags,
    _is_timestamp_like,
    _resolve_creators,
    _resolve_wikilinks,
    _slugify,
    _sync_worker,
    _validate_sync_schema,
    app,
)

runner = CliRunner()


# ── _slugify unit tests ──────────────────────────────────────────────────────


class TestSlugify:
    def test_simple_name(self) -> None:
        assert _slugify("Andy Matuschak") == "andy-matuschak"

    def test_apostrophe_stripped(self) -> None:
        assert _slugify("Why Books Don't Work") == "why-books-don-t-work"

    def test_dash_separator(self) -> None:
        assert _slugify("Zhuangzi - The Complete Writings") == "zhuangzi-the-complete-writings"

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
        result = _resolve_creators(
            ["[[Some Person|Display Name]]"], {"Some Person"}
        )
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


# ── _validate_sync_schema unit tests ────────────────────────────────────────


class TestIsTimestampLike:
    def test_date_object(self) -> None:
        from datetime import date
        assert _is_timestamp_like(date(2026, 1, 1)) is True

    def test_datetime_object(self) -> None:
        from datetime import datetime
        assert _is_timestamp_like(datetime(2026, 1, 1, 12, 0)) is True

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


class TestValidateSyncSchema:
    def _base_fm(self, **overrides: object) -> dict:
        fm = {
            "created": "2026-01-01",
            "modified": "2026-01-01",
            "synced": None,
            "publish": True,
        }
        fm.update(overrides)
        return fm

    def test_base_fields_missing_modified(self) -> None:
        errors = _validate_sync_schema(
            {"created": "2026-01-01", "synced": None, "publish": True}, []
        )
        assert any("modified" in e for e in errors)

    def test_base_fields_missing_synced(self) -> None:
        errors = _validate_sync_schema(
            {"created": "2026-01-01", "modified": "2026-01-01", "publish": True}, []
        )
        assert any("synced" in e for e in errors)

    def test_base_fields_missing_publish(self) -> None:
        errors = _validate_sync_schema(
            {"created": "2026-01-01", "modified": "2026-01-01", "synced": None}, []
        )
        assert any("publish" in e for e in errors)

    def test_type_specific_fields_missing(self) -> None:
        fm = self._base_fm()
        errors = _validate_sync_schema(fm, ["book"])
        assert any("creators" in e for e in errors)
        assert any("status" in e for e in errors)
        assert any("own" in e for e in errors)

    def test_invalid_status(self) -> None:
        fm = self._base_fm(status="garbage", creators=[], own=True)
        errors = _validate_sync_schema(fm, ["book"])
        assert any("invalid status" in e for e in errors)

    def test_valid_book(self) -> None:
        fm = self._base_fm(status="done", creators=["Author"], own=True)
        errors = _validate_sync_schema(fm, ["book"])
        assert errors == []

    def test_valid_no_type(self) -> None:
        fm = self._base_fm()
        errors = _validate_sync_schema(fm, [])
        assert errors == []

    def test_dataset_type(self) -> None:
        fm = self._base_fm()
        errors = _validate_sync_schema(fm, ["dataset"])
        assert any("is_meta_catalog" in e for e in errors)
        assert any("is_api" in e for e in errors)

    def test_invalid_created_timestamp(self) -> None:
        fm = self._base_fm(created="not-a-date")
        errors = _validate_sync_schema(fm, [])
        assert any("'created' must be a timestamp" in e for e in errors)

    def test_invalid_modified_timestamp(self) -> None:
        fm = self._base_fm(modified="garbage")
        errors = _validate_sync_schema(fm, [])
        assert any("'modified' must be a timestamp" in e for e in errors)

    def test_invalid_synced_timestamp(self) -> None:
        fm = self._base_fm(synced="bad-value")
        errors = _validate_sync_schema(fm, [])
        assert any("'synced' must be a timestamp" in e for e in errors)

    def test_null_synced_is_valid(self) -> None:
        fm = self._base_fm(synced=None)
        errors = _validate_sync_schema(fm, [])
        assert errors == []

    def test_publish_must_be_bool(self) -> None:
        fm = self._base_fm(publish="yes")
        errors = _validate_sync_schema(fm, [])
        assert any("'publish' must be a bool" in e for e in errors)

    def test_publish_integer_not_bool(self) -> None:
        fm = self._base_fm(publish=1)
        errors = _validate_sync_schema(fm, [])
        assert any("'publish' must be a bool" in e for e in errors)

    def test_unrecognized_field_rejected(self) -> None:
        fm = self._base_fm(rating=8.5)
        errors = _validate_sync_schema(fm, [])
        assert any("unrecognized" in e for e in errors)
        assert any("rating" in e for e in errors)

    def test_multiple_unrecognized_fields(self) -> None:
        fm = self._base_fm(rating=8.5, color="blue")
        errors = _validate_sync_schema(fm, [])
        assert any("rating" in e for e in errors)
        assert any("color" in e for e in errors)

    def test_structural_errors_bail_early(self) -> None:
        """Missing fields + bad values: only structural errors returned."""
        fm = {"created": "not-a-date", "publish": True}
        errors = _validate_sync_schema(fm, [])
        # Should report missing modified/synced but NOT the bad timestamp
        assert any("missing required" in e for e in errors)
        assert not any("timestamp" in e for e in errors)


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
            src, known_stems=set(), output_dir="/sky", dest=dest, dry_run=False
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
            src, known_stems=set(), output_dir="/sky", dest=dest, dry_run=False
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
            src, known_stems=set(), output_dir="/sky", dest=dest, dry_run=False
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
            src, known_stems=set(), output_dir="/sky", dest=dest, dry_run=False
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
            src, known_stems=set(), output_dir="/sky", dest=dest, dry_run=False
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
            output_dir="/sky",
            dest=dest,
            dry_run=False,
        )
        # Book requires status/creators/own — should error
        assert status == "error"

    def test_valid_book_synced_with_type(self, tmp_path: Path) -> None:
        src = self._make_file(
            tmp_path / "note.md",
            "---\ncreated: 2026-01-01\nmodified: 2026-01-01\nsynced:\npublish: true\n"
            "status: done\ncreators:\n  - Author\nown: true\n---\n#Book\n\nContent.\n",
        )
        dest = tmp_path / "out"
        dest.mkdir()
        status, _ = _sync_worker(
            src, known_stems=set(), output_dir="/sky", dest=dest, dry_run=False
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
            src, known_stems=set(), output_dir="/sky", dest=dest, dry_run=False
        )
        content = (dest / "note.md").read_text()
        assert "synced: '20" in content or "synced: 20" in content

    def test_synced_timestamp_set_on_source(self, tmp_path: Path) -> None:
        """Source file should have synced stamped back after sync."""
        src = self._make_file(
            tmp_path / "note.md",
            "---\ncreated: 2026-01-01\nmodified: 2026-01-01\nsynced:\npublish: true\n---\nBody\n",
        )
        dest = tmp_path / "out"
        dest.mkdir()
        _sync_worker(
            src, known_stems=set(), output_dir="/sky", dest=dest, dry_run=False
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
            src, known_stems={"Known"}, output_dir="/sky", dest=dest, dry_run=False
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
            src, known_stems={"Known"}, output_dir="/sky", dest=dest, dry_run=False
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
            src, known_stems=set(), output_dir="/sky", dest=dest, dry_run=False
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
            src, known_stems=set(), output_dir="/sky", dest=dest, dry_run=False
        )
        assert (dest / "publishable-book.md").exists()
        assert not (dest / "Publishable Book.md").exists()

    def test_dry_run_does_not_write(self, tmp_path: Path) -> None:
        original = "---\ncreated: 2026-01-01\nmodified: 2026-01-01\nsynced:\npublish: true\n---\nBody\n"
        src = self._make_file(tmp_path / "note.md", original)
        dest = tmp_path / "out"
        dest.mkdir()
        status, _ = _sync_worker(
            src, known_stems=set(), output_dir="/sky", dest=dest, dry_run=True
        )
        assert status == "dry-run"
        assert not (dest / "note.md").exists()
        assert src.read_text() == original  # source untouched too

    def test_no_frontmatter_skipped(self, tmp_path: Path) -> None:
        src = self._make_file(tmp_path / "bare.md", "No frontmatter here.\n")
        dest = tmp_path / "out"
        dest.mkdir()
        status, _ = _sync_worker(
            src, known_stems=set(), output_dir="/sky", dest=dest, dry_run=False
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
            src, known_stems=set(), output_dir="/sky", dest=dest, dry_run=False
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
            src, known_stems=set(), output_dir="/sky", dest=dest, dry_run=False
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
            src, known_stems=set(), output_dir="/sky", dest=dest, dry_run=False
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
            src, known_stems=set(), output_dir="/sky", dest=dest, dry_run=False
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
            src, known_stems=set(), output_dir="/sky", dest=dest, dry_run=False
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

    def test_publishable_files_synced(
        self, mock_source: Path, mock_dest: Path
    ) -> None:
        runner.invoke(
            app, ["sync", str(mock_source), "--dest", str(mock_dest)]
        )
        synced_names = {p.name for p in mock_dest.glob("*.md")}
        assert "publishable-book.md" in synced_names
        assert "known-author.md" in synced_names
        assert "body-links.md" in synced_names
        assert "heading-not-tag.md" in synced_names
        assert "valid-dataset.md" in synced_names

    def test_unpublished_files_skipped(
        self, mock_source: Path, mock_dest: Path
    ) -> None:
        runner.invoke(
            app, ["sync", str(mock_source), "--dest", str(mock_dest)]
        )
        synced_names = {p.name for p in mock_dest.glob("*.md")}
        assert "no-publish-field.md" not in synced_names
        assert "publish-false.md" not in synced_names
        assert "publish-null.md" not in synced_names
        assert "publish-string.md" not in synced_names

    def test_already_synced_skipped(
        self, mock_source: Path, mock_dest: Path
    ) -> None:
        original = (mock_dest / "already-synced.md").read_text()
        runner.invoke(
            app, ["sync", str(mock_source), "--dest", str(mock_dest)]
        )
        # File should not be overwritten (same modified)
        assert (mock_dest / "already-synced.md").read_text() == original

    def test_schema_errors_reported(
        self, mock_source: Path, mock_dest: Path
    ) -> None:
        result = runner.invoke(
            app, ["sync", str(mock_source), "--dest", str(mock_dest)]
        )
        assert result.exit_code == 1
        assert "Missing Modified.md" in result.output or "missing required" in result.output

    def test_corpus_includes_dest_files(
        self, mock_source: Path, mock_dest: Path
    ) -> None:
        runner.invoke(
            app, ["sync", str(mock_source), "--dest", str(mock_dest)]
        )
        content = (mock_dest / "body-links.md").read_text()
        # Known Author is in source → resolved
        assert "[Known Author](" in content
        # Dest Only is in dest → resolved (title from dest frontmatter)
        assert "[Dest Only](" in content
        # Ghost is nowhere → plain text
        assert "Ghost" in content
        assert "[[Ghost]]" not in content

    def test_type_tags_in_output(
        self, mock_source: Path, mock_dest: Path
    ) -> None:
        runner.invoke(
            app, ["sync", str(mock_source), "--dest", str(mock_dest)]
        )
        content = (mock_dest / "publishable-book.md").read_text()
        assert "type: book" in content
        assert "#Book" not in content

    def test_title_in_output(
        self, mock_source: Path, mock_dest: Path
    ) -> None:
        runner.invoke(
            app, ["sync", str(mock_source), "--dest", str(mock_dest)]
        )
        content = (mock_dest / "publishable-book.md").read_text()
        assert "title: Publishable Book" in content

    def test_no_sync_fields_absent(
        self, mock_source: Path, mock_dest: Path
    ) -> None:
        runner.invoke(
            app, ["sync", str(mock_source), "--dest", str(mock_dest)]
        )
        content = (mock_dest / "publishable-book.md").read_text()
        assert "own:" not in content
        assert "publish:" not in content
        assert "created:" not in content

    def test_dry_run(self, mock_source: Path, mock_dest: Path) -> None:
        before = {p.name for p in mock_dest.glob("*.md")}
        runner.invoke(
            app, ["sync", str(mock_source), "--dest", str(mock_dest), "-n"]
        )
        after = {p.name for p in mock_dest.glob("*.md")}
        assert before == after

    def test_empty_source(self, empty_vault: Path, tmp_path: Path) -> None:
        dest = tmp_path / "out"
        result = runner.invoke(
            app, ["sync", str(empty_vault), "--dest", str(dest)]
        )
        assert "No .md files" in result.output

    def test_nonexistent_source(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app, ["sync", str(tmp_path / "nope"), "--dest", str(tmp_path / "out")]
        )
        assert result.exit_code == 1

    def test_creator_objects_in_synced_file(
        self, mock_source: Path, mock_dest: Path
    ) -> None:
        runner.invoke(
            app, ["sync", str(mock_source), "--dest", str(mock_dest)]
        )
        content = (mock_dest / "publishable-book.md").read_text()
        # [[Known Author]] should become name/slug object
        assert "name: Known Author" in content
        assert "slug: known-author" in content
        assert "[[Known Author]]" not in content

    def test_broken_creator_name_only(
        self, mock_source: Path, mock_dest: Path
    ) -> None:
        runner.invoke(
            app, ["sync", str(mock_source), "--dest", str(mock_dest)]
        )
        content = (mock_dest / "publishable-film.md").read_text()
        # [[Unknown Director]] is not in corpus → name only, no slug
        assert "name: Unknown Director" in content
        assert "slug:" not in content
        assert "[[Unknown Director]]" not in content

    def test_multi_type_skipped(
        self, mock_source: Path, mock_dest: Path
    ) -> None:
        runner.invoke(
            app, ["sync", str(mock_source), "--dest", str(mock_dest)]
        )
        synced_names = {p.name for p in mock_dest.glob("*.md")}
        assert "multi-type.md" not in synced_names

    def test_unrecognized_field_errors(
        self, mock_source: Path, mock_dest: Path
    ) -> None:
        runner.invoke(
            app, ["sync", str(mock_source), "--dest", str(mock_dest)]
        )
        synced_names = {p.name for p in mock_dest.glob("*.md")}
        assert "unrecognized-field.md" not in synced_names

    def test_dataset_type_output(
        self, mock_source: Path, mock_dest: Path
    ) -> None:
        runner.invoke(
            app, ["sync", str(mock_source), "--dest", str(mock_dest)]
        )
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

    def test_publish_string_skipped(
        self, mock_source: Path, mock_dest: Path
    ) -> None:
        runner.invoke(
            app, ["sync", str(mock_source), "--dest", str(mock_dest)]
        )
        synced_names = {p.name for p in mock_dest.glob("*.md")}
        assert "publish-string.md" not in synced_names
