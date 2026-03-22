"""Tests for the `filename` command and its worker."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from rematter import _filename_worker, app

runner = CliRunner()


# ── worker unit tests ──────────────────────────────────────────────────────────


def test_datetime_field_uses_date_part(tmp_path: Path) -> None:
    """Obsidian's default datetime format (2026-02-12 15:03) should strip time."""
    f = tmp_path / "Andy Matuschak.md"
    f.write_text(
        "---\ncreated: 2026-02-12 15:03\nmodified: 2026-02-12 15:03\n---\n#Author\n"
    )
    status, msg = _filename_worker(f, field="created", dry_run=False)
    assert status == "done"
    assert "2026-02-12 - Andy Matuschak.md" in msg
    expected = tmp_path / "2026-02-12 - Andy Matuschak.md"
    assert expected.exists()
    assert not f.exists()


def test_date_field_string(tmp_path: Path) -> None:
    """Quoted date string (stays as str in YAML) should also parse correctly."""
    f = tmp_path / "note.md"
    f.write_text('---\nDate: "2026-01-15"\n---\nBody.\n')
    status, _ = _filename_worker(f, field="Date", dry_run=False)
    assert status == "done"
    assert (tmp_path / "2026-01-15 - note.md").exists()


def test_date_field_native_date(tmp_path: Path) -> None:
    """PyYAML-parsed date object (bare YYYY-MM-DD) should work."""
    f = tmp_path / "note.md"
    f.write_text("---\nDate: 2026-03-01\n---\nBody.\n")
    status, _ = _filename_worker(f, field="Date", dry_run=False)
    assert status == "done"
    assert (tmp_path / "2026-03-01 - note.md").exists()


def test_already_prefixed_is_skipped(tmp_path: Path) -> None:
    f = tmp_path / "2026-01-01 - note.md"
    f.write_text("---\ncreated: 2026-01-01\n---\nBody.\n")
    status, _ = _filename_worker(f, field="created", dry_run=False)
    assert status == "skip"
    assert f.exists()  # untouched


def test_no_frontmatter_is_skipped(tmp_path: Path) -> None:
    f = tmp_path / "note.md"
    f.write_text("Just plain text, no frontmatter.\n")
    status, _ = _filename_worker(f, field="created", dry_run=False)
    assert status == "skip"


def test_missing_field_is_skipped(tmp_path: Path) -> None:
    """Wikidata-style files with no date field should be silently skipped."""
    f = tmp_path / "Wikidata.md"
    f.write_text(
        "---\nis_meta_catalog: true\nis_api: false\nurl: https://example.com\n---\n#Dataset\n"
    )
    status, _ = _filename_worker(f, field="created", dry_run=False)
    assert status == "skip"
    assert f.exists()


def test_invalid_date_string_returns_error(tmp_path: Path) -> None:
    f = tmp_path / "note.md"
    f.write_text('---\nDate: "not-a-date"\n---\nBody.\n')
    status, msg = _filename_worker(f, field="Date", dry_run=False)
    assert status == "error"
    assert "invalid date" in msg


def test_target_already_exists_returns_error(tmp_path: Path) -> None:
    f = tmp_path / "note.md"
    f.write_text("---\ncreated: 2026-02-12 15:03\n---\nBody.\n")
    existing = tmp_path / "2026-02-12 - note.md"
    existing.write_text("already here")
    status, msg = _filename_worker(f, field="created", dry_run=False)
    assert status == "error"
    assert "already exists" in msg
    assert f.exists()  # original untouched


def test_dry_run_makes_no_changes(tmp_path: Path) -> None:
    f = tmp_path / "note.md"
    original = "---\ncreated: 2026-02-12 15:03\n---\nBody.\n"
    f.write_text(original)
    status, _ = _filename_worker(f, field="created", dry_run=True)
    assert status == "dry-run"
    assert f.exists()
    assert f.read_text() == original
    assert not (tmp_path / "2026-02-12 - note.md").exists()


def test_field_stripped_from_frontmatter(tmp_path: Path) -> None:
    """The processed field must not appear in the renamed file."""
    f = tmp_path / "Wong Kar Wai.md"
    f.write_text(
        "---\ncreated: 2026-01-30 00:57\nmodified: 2026-03-08 12:00\n---\n#Director\n"
    )
    _filename_worker(f, field="created", dry_run=False)
    renamed = tmp_path / "2026-01-30 - Wong Kar Wai.md"
    assert renamed.exists()
    content = renamed.read_text()
    assert "created:" not in content
    assert "modified:" in content  # other fields preserved


def test_other_fields_preserved_in_output(tmp_path: Path) -> None:
    """Zhuangzi-style note: multiple fields, only the date field removed."""
    f = tmp_path / "Zhuangzi - The Complete Writings.md"
    f.write_text(
        "---\ncreators:\n  - Brook Ziporyn\nown: false\nstatus: not_started\n"
        "created: 2026-02-04 11:37\nmodified: 2026-03-08 13:14\n---\n#Book\n"
    )
    status, _ = _filename_worker(f, field="created", dry_run=False)
    assert status == "done"
    renamed = tmp_path / "2026-02-04 - Zhuangzi - The Complete Writings.md"
    assert renamed.exists()
    content = renamed.read_text()
    assert "creators:" in content
    assert "own:" in content
    assert "status:" in content
    assert "modified:" in content
    assert "created:" not in content


def test_only_field_strips_frontmatter_entirely(tmp_path: Path) -> None:
    """If the date field is the only frontmatter entry, the block is dropped."""
    f = tmp_path / "note.md"
    f.write_text("---\ncreated: 2026-01-01 09:00\n---\nJust a body.\n")
    _filename_worker(f, field="created", dry_run=False)
    renamed = tmp_path / "2026-01-01 - note.md"
    assert renamed.exists()
    content = renamed.read_text()
    assert not content.startswith("---")
    assert "Just a body." in content


def test_body_separator_preserved(tmp_path: Path) -> None:
    """XmR-style: body '---' must survive the operation intact."""
    f = tmp_path / "XmR charts.md"
    f.write_text(
        "---\ncreated: 2026-01-21 13:58\nmodified: 2026-02-13 14:20\n---\n"
        "Intro.\n\n---\n\n## Section\n"
    )
    status, _ = _filename_worker(f, field="created", dry_run=False)
    assert status == "done"
    renamed = tmp_path / "2026-01-21 - XmR charts.md"
    body = renamed.read_text()
    assert "---\n\n## Section" in body


# ── CLI integration ────────────────────────────────────────────────────────────


def test_cli_filename_basic(mock_source: Path) -> None:
    result = runner.invoke(app, ["filename", str(mock_source), "--field", "created"])
    # Bad Timestamp.md has an invalid date → exit code 1
    assert result.exit_code == 1
    dated = [f for f in mock_source.iterdir() if f.name.startswith("2026-")]
    # 28 of 29 files have valid 'created' — all should be renamed
    assert len(dated) == 28


def test_cli_filename_dry_run_makes_no_changes(mock_source: Path) -> None:
    before = {f.name for f in mock_source.glob("*.md")}
    result = runner.invoke(
        app, ["filename", str(mock_source), "--field", "created", "--dry-run"]
    )
    assert result.exit_code == 1  # Bad Timestamp still errors in dry-run
    after = {f.name for f in mock_source.glob("*.md")}
    assert before == after


def test_cli_filename_nonexistent_directory(tmp_path: Path) -> None:
    result = runner.invoke(app, ["filename", str(tmp_path / "nope")])
    assert result.exit_code != 0


def test_cli_filename_empty_vault(empty_vault: Path) -> None:
    result = runner.invoke(app, ["filename", str(empty_vault)])
    assert result.exit_code == 0


def test_cli_filename_skips_already_prefixed(mock_source: Path) -> None:
    pre = mock_source / "2026-01-01 - already.md"
    pre.write_text("---\ncreated: 2026-05-01 10:00\n---\nBody.\n")
    result = runner.invoke(app, ["filename", str(mock_source), "--field", "created"])
    assert result.exit_code == 1  # Bad Timestamp still errors
    # Should still exist unchanged under its original name
    assert pre.exists()
