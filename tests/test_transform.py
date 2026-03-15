"""Tests for the `transform` command and its worker."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from rematter import _transform_worker, app

runner = CliRunner()


# ── worker unit tests ──────────────────────────────────────────────────────────


def test_basic_rename(tmp_path: Path) -> None:
    f = tmp_path / "note.md"
    f.write_text("---\ncreated: 2026-02-12 15:03\nmodified: 2026-02-12 15:03\n---\nBody.\n")
    status, msg = _transform_worker(f, from_field="created", to_field="date_created", dry_run=False)
    assert status == "done"
    assert "created" in msg and "date_created" in msg
    from rematter import _load
    result = _load(f)
    assert result is not None
    fm, _ = result
    assert "date_created" in fm
    assert "created" not in fm


def test_value_is_preserved(tmp_path: Path) -> None:
    f = tmp_path / "note.md"
    f.write_text("---\ncreated: 2026-02-12 15:03\n---\nBody.\n")
    _transform_worker(f, from_field="created", to_field="date_created", dry_run=False)
    from rematter import _load
    result = _load(f)
    assert result is not None
    fm, _ = result
    # PyYAML 6 returns "2026-02-12 15:03" as a string; the value is preserved as-is
    assert fm["date_created"] == "2026-02-12 15:03"
    assert "created" not in fm


def test_key_order_preserved(tmp_path: Path) -> None:
    """Renamed key should appear in the same position in the YAML output."""
    f = tmp_path / "note.md"
    f.write_text(
        "---\ncreators:\n  - Brook Ziporyn\nown: false\nstatus: not_started\n"
        "created: 2026-02-04 11:37\nmodified: 2026-03-08 13:14\n---\n#Book\n"
    )
    _transform_worker(f, from_field="created", to_field="date_created", dry_run=False)
    content = f.read_text()
    # 'date_created' should appear before 'modified' in the output
    assert content.index("date_created:") < content.index("modified:")
    # and after 'status:'
    assert content.index("status:") < content.index("date_created:")


def test_no_frontmatter_is_skipped(tmp_path: Path) -> None:
    f = tmp_path / "note.md"
    original = "No frontmatter here.\n"
    f.write_text(original)
    status, _ = _transform_worker(f, from_field="created", to_field="date_created", dry_run=False)
    assert status == "skip"
    assert f.read_text() == original


def test_missing_field_is_skipped(tmp_path: Path) -> None:
    """Wikidata-style file has no 'created' — should be skipped silently."""
    f = tmp_path / "Wikidata.md"
    original = "---\nis_meta_catalog: true\nis_api: false\nurl: https://example.com\n---\n#Dataset\n"
    f.write_text(original)
    status, _ = _transform_worker(f, from_field="created", to_field="date_created", dry_run=False)
    assert status == "skip"
    assert f.read_text() == original


def test_target_field_exists_returns_error(tmp_path: Path) -> None:
    """Refuse to clobber an existing field."""
    f = tmp_path / "note.md"
    f.write_text("---\ncreated: 2026-01-01 10:00\ndate_created: 2025-06-01\n---\nBody.\n")
    status, msg = _transform_worker(f, from_field="created", to_field="date_created", dry_run=False)
    assert status == "error"
    assert "already exists" in msg


def test_target_field_exists_leaves_file_unchanged(tmp_path: Path) -> None:
    f = tmp_path / "note.md"
    original = "---\ncreated: 2026-01-01 10:00\ndate_created: 2025-06-01\n---\nBody.\n"
    f.write_text(original)
    _transform_worker(f, from_field="created", to_field="date_created", dry_run=False)
    assert f.read_text() == original


def test_dry_run_makes_no_changes(tmp_path: Path) -> None:
    f = tmp_path / "note.md"
    original = "---\ncreated: 2026-02-12 15:03\n---\nBody.\n"
    f.write_text(original)
    status, _ = _transform_worker(f, from_field="created", to_field="date_created", dry_run=True)
    assert status == "dry-run"
    assert f.read_text() == original


def test_list_value_preserved(tmp_path: Path) -> None:
    """List-typed values (e.g. creators) must survive renaming intact."""
    f = tmp_path / "note.md"
    f.write_text('---\ncreators:\n  - "[[Andy Matuschak]]"\ncreated: 2026-02-12 15:03\n---\nBody.\n')
    _transform_worker(f, from_field="creators", to_field="authors", dry_run=False)
    from rematter import _load
    result = _load(f)
    assert result is not None
    fm, _ = result
    assert fm["authors"] == ["[[Andy Matuschak]]"]
    assert "creators" not in fm


def test_bool_value_preserved(tmp_path: Path) -> None:
    """Bool values must not be stringified during key rename."""
    f = tmp_path / "note.md"
    f.write_text("---\nis_meta_catalog: true\nis_api: false\nurl: https://example.com\n---\nBody.\n")
    _transform_worker(f, from_field="is_meta_catalog", to_field="meta_catalog", dry_run=False)
    from rematter import _load
    result = _load(f)
    assert result is not None
    fm, _ = result
    assert fm["meta_catalog"] is True
    assert "is_meta_catalog" not in fm


# ── CLI integration ────────────────────────────────────────────────────────────


def test_cli_transform_basic(vault: Path) -> None:
    result = runner.invoke(app, ["transform", str(vault), "--field", "created", "--to", "date_created"])
    assert result.exit_code == 0
    # All files that had 'created' now have 'date_created'
    from rematter import _load
    for f in vault.glob("*.md"):
        parsed = _load(f)
        if parsed is not None:
            fm, _ = parsed
            assert "created" not in fm


def test_cli_transform_dry_run(vault: Path) -> None:
    before = {f.name: f.read_text() for f in vault.glob("*.md")}
    result = runner.invoke(app, ["transform", str(vault), "--field", "created", "--to", "date_created", "--dry-run"])
    assert result.exit_code == 0
    after = {f.name: f.read_text() for f in vault.glob("*.md")}
    assert before == after


def test_cli_transform_same_field_exits_error(vault: Path) -> None:
    result = runner.invoke(app, ["transform", str(vault), "--field", "created", "--to", "created"])
    assert result.exit_code != 0
    assert "identical" in result.output.lower() or "identical" in (result.stderr or "").lower()


def test_cli_transform_nonexistent_directory(tmp_path: Path) -> None:
    result = runner.invoke(app, ["transform", str(tmp_path / "nope"), "--field", "created", "--to", "date_created"])
    assert result.exit_code != 0


def test_cli_transform_empty_vault(empty_vault: Path) -> None:
    result = runner.invoke(app, ["transform", str(empty_vault), "--field", "created", "--to", "date_created"])
    assert result.exit_code == 0


def test_cli_transform_files_without_field_are_skipped(vault: Path) -> None:
    """DuckDB.md has no 'status' field — must be untouched by a status rename."""
    duckdb = vault / "DuckDB.md"
    duckdb_before = duckdb.read_text()

    runner.invoke(app, ["transform", str(vault), "--field", "status", "--to", "reading_status"])

    assert duckdb.read_text() == duckdb_before
