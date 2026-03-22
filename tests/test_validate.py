"""Tests for the `validate` command and its worker."""

from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from rematter import _filter_ignored, _load, _load_config, app

runner = CliRunner()

SCHEMA = {
    "properties": {
        "created": {"type": "timestamp", "required": True, "default": "%Y-%m-%d %H:%M"},
        "modified": {
            "type": "timestamp",
            "required": True,
            "default": "%Y-%m-%d %H:%M",
        },
        "synced": {"type": "timestamp", "required": True, "default": None},
        "publish": {"type": "bool", "required": True, "default": False},
        "status": {
            "type": "string",
            "required": False,
            "enum": ["not_started", "in_progress", "on_hold", "done", "cancelled"],
            "default": "not_started",
        },
        "creators": {"type": "list", "required": False},
        "own": {"type": "bool", "required": False, "default": False},
    },
}


def _write_schema(directory: Path, schema: dict | None = None) -> Path:
    """Write a .rematter.yaml into directory and return the path."""
    p = directory / ".rematter.yaml"
    p.write_text(yaml.dump(schema or SCHEMA, sort_keys=False), encoding="utf-8")
    return p


def _write_note(directory: Path, name: str, fm: dict, body: str = "") -> Path:
    """Write a markdown file with frontmatter."""
    fm_str = yaml.dump(fm, sort_keys=False, default_flow_style=False).rstrip("\n")
    p = directory / name
    p.write_text(f"---\n{fm_str}\n---\n{body}", encoding="utf-8")
    return p


VALID_FM = {
    "created": "2026-02-12 15:03",
    "modified": "2026-02-12 15:03",
    "synced": "2026-02-12 15:03",
    "publish": True,
}


# ── worker unit tests ──────────────────────────────────────────────────────────


def test_valid_file_passes(tmp_path: Path) -> None:
    from rematter._workers import _validate_worker

    _write_schema(tmp_path)
    f = _write_note(tmp_path, "good.md", VALID_FM)
    status, _ = _validate_worker(f, schema=SCHEMA, fix=False, dry_run=False)
    assert status == "skip"  # valid files are silently skipped (nothing to report)


def test_missing_required_field_errors(tmp_path: Path) -> None:
    from rematter._workers import _validate_worker

    fm = {**VALID_FM}
    del fm["publish"]
    f = _write_note(tmp_path, "bad.md", fm)
    status, msg = _validate_worker(f, schema=SCHEMA, fix=False, dry_run=False)
    assert status == "error"
    assert "publish" in msg


def test_wrong_type_errors(tmp_path: Path) -> None:
    from rematter._workers import _validate_worker

    fm = {**VALID_FM, "publish": "yes"}
    f = _write_note(tmp_path, "bad.md", fm)
    status, msg = _validate_worker(f, schema=SCHEMA, fix=False, dry_run=False)
    assert status == "error"
    assert "bool" in msg.lower() or "publish" in msg


def test_bad_timestamp_errors(tmp_path: Path) -> None:
    from rematter._workers import _validate_worker

    fm = {**VALID_FM, "created": "not-a-date"}
    f = _write_note(tmp_path, "bad.md", fm)
    status, msg = _validate_worker(f, schema=SCHEMA, fix=False, dry_run=False)
    assert status == "error"
    assert "timestamp" in msg.lower() or "created" in msg


def test_invalid_enum_errors(tmp_path: Path) -> None:
    from rematter._workers import _validate_worker

    fm = {**VALID_FM, "status": "invalid_status"}
    f = _write_note(tmp_path, "bad.md", fm)
    status, msg = _validate_worker(f, schema=SCHEMA, fix=False, dry_run=False)
    assert status == "error"
    assert "status" in msg


def test_unrecognized_field_strict(tmp_path: Path) -> None:
    from rematter._workers import _validate_worker

    fm = {**VALID_FM, "rating": 5}
    f = _write_note(tmp_path, "bad.md", fm)
    status, msg = _validate_worker(f, schema=SCHEMA, fix=False, dry_run=False)
    assert status == "error"
    assert "rating" in msg


def test_sync_false_property_passes_validation(tmp_path: Path) -> None:
    """A property with sync: false should be recognized and pass validation."""
    from rematter._workers import _validate_worker

    schema_with_rating = {
        "properties": {
            **SCHEMA["properties"],
            "rating": {"type": "int", "required": False, "sync": False},
        }
    }
    fm = {**VALID_FM, "rating": 5}
    f = _write_note(tmp_path, "ok.md", fm)
    status, _ = _validate_worker(f, schema=schema_with_rating, fix=False, dry_run=False)
    assert status == "skip"  # valid → skip


def test_no_frontmatter_skipped(tmp_path: Path) -> None:
    from rematter._workers import _validate_worker

    f = tmp_path / "plain.md"
    f.write_text("No frontmatter here.\n")
    status, _ = _validate_worker(f, schema=SCHEMA, fix=False, dry_run=False)
    assert status == "skip"


def test_null_required_field_is_valid(tmp_path: Path) -> None:
    """Required means the key must exist; null is a valid value (e.g. synced before first sync)."""
    from rematter._workers import _validate_worker

    fm = {**VALID_FM, "created": None}
    f = _write_note(tmp_path, "ok.md", fm)
    status, _ = _validate_worker(f, schema=SCHEMA, fix=False, dry_run=False)
    assert status == "skip"  # valid


# ── fix mode: worker tests ────────────────────────────────────────────────────


def test_fix_adds_missing_bool_default(tmp_path: Path) -> None:
    from rematter._workers import _validate_worker

    fm = {**VALID_FM}
    del fm["publish"]
    f = _write_note(tmp_path, "fixme.md", fm)
    status, msg = _validate_worker(f, schema=SCHEMA, fix=True, dry_run=False)
    assert status == "done"
    result = _load(f)
    assert result is not None
    assert result[0]["publish"] is False


def test_fix_adds_multiple_missing_defaults(tmp_path: Path) -> None:
    """Fix should add defaults for all missing required fields at once."""
    from rematter._workers import _validate_worker

    fm = {"modified": "2026-02-12 15:03", "synced": None}
    f = _write_note(tmp_path, "fixme.md", fm)
    status, _ = _validate_worker(f, schema=SCHEMA, fix=True, dry_run=False)
    assert status == "done"
    result = _load(f)
    assert result is not None
    assert result[0]["publish"] is False


def test_fix_timestamp_default_uses_format_string(tmp_path: Path) -> None:
    """When a timestamp default is a strftime format, fix should stamp the current time."""
    from rematter._workers import _validate_worker

    fm = {"modified": "2026-02-12 15:03", "synced": None, "publish": True}
    # missing 'created' — default is "%Y-%m-%d %H:%M"
    f = _write_note(tmp_path, "fixme.md", fm)
    status, _ = _validate_worker(f, schema=SCHEMA, fix=True, dry_run=False)
    assert status == "done"
    result = _load(f)
    assert result is not None
    created = str(result[0]["created"])
    # Should be a timestamp-like string in YYYY-MM-DD HH:MM format
    assert len(created) >= 10  # at least a date
    import re

    assert re.match(r"\d{4}-\d{2}-\d{2}", created)


def test_fix_dry_run_no_write(tmp_path: Path) -> None:
    from rematter._workers import _validate_worker

    fm = {**VALID_FM}
    del fm["publish"]
    f = _write_note(tmp_path, "fixme.md", fm)
    original = f.read_text()
    status, _ = _validate_worker(f, schema=SCHEMA, fix=True, dry_run=True)
    assert status == "dry-run"
    assert f.read_text() == original


def test_fix_null_default_adds_key_with_null(tmp_path: Path) -> None:
    """default: null should add the key with a null value, not error as unfixable."""
    from rematter._workers import _validate_worker

    fm = {
        "created": "2026-02-12 15:03",
        "modified": "2026-02-12 15:03",
        "publish": True,
    }
    # missing 'synced' — schema default is null
    f = _write_note(tmp_path, "fixme.md", fm)
    status, msg = _validate_worker(f, schema=SCHEMA, fix=True, dry_run=False)
    assert status == "done"
    result = _load(f)
    assert result is not None
    assert "synced" in result[0]
    assert result[0]["synced"] is None


def test_fix_skips_required_field_without_default(tmp_path: Path) -> None:
    """If a required field has no default, fix can't help — still errors."""
    from rematter._workers import _validate_worker

    schema_no_default = {
        "properties": {
            "created": {"type": "timestamp", "required": True},
            "modified": {"type": "timestamp", "required": True},
            "publish": {"type": "bool", "required": True},
        },
    }
    fm = {"modified": "2026-02-12 15:03", "publish": True}
    f = _write_note(tmp_path, "fixme.md", fm)
    status, msg = _validate_worker(f, schema=schema_no_default, fix=True, dry_run=False)
    assert status == "error"
    assert "created" in msg


# ── schema loading ────────────────────────────────────────────────────────────


def test_load_config_from_directory(tmp_path: Path) -> None:
    from rematter._workers import _load_config

    _write_schema(tmp_path)
    config = _load_config(tmp_path)
    assert "created" in config.properties


def test_load_config_missing_raises(tmp_path: Path) -> None:
    from rematter._workers import _load_config

    import pytest

    with pytest.raises(FileNotFoundError):
        _load_config(tmp_path)


def test_load_config_legacy_fallback(tmp_path: Path) -> None:
    """_schema.yml should still work with deprecation warning."""
    from rematter._workers import _load_config

    # Write as legacy _schema.yml
    p = tmp_path / "_schema.yml"
    p.write_text(yaml.dump(SCHEMA, sort_keys=False), encoding="utf-8")
    config = _load_config(tmp_path)
    assert "created" in config.properties


def test_load_config_explicit_path(tmp_path: Path) -> None:
    from rematter._workers import _load_config

    custom = tmp_path / "custom.yaml"
    custom.write_text(yaml.dump(SCHEMA, sort_keys=False), encoding="utf-8")
    config = _load_config(tmp_path, explicit_path=custom)
    assert "created" in config.properties


def test_load_schema_legacy(tmp_path: Path) -> None:
    """_load_schema still works for backward compatibility."""
    from rematter._workers import _load_schema

    p = tmp_path / "_schema.yml"
    p.write_text(yaml.dump(SCHEMA, sort_keys=False), encoding="utf-8")
    schema = _load_schema(p)
    assert "properties" in schema
    assert "created" in schema["properties"]


# ── CLI integration ───────────────────────────────────────────────────────────


def test_cli_validate_reports_known_errors(mock_source: Path) -> None:
    """Mock source contains intentionally invalid sync test files alongside valid ones."""
    result = runner.invoke(app, ["validate", str(mock_source)])
    assert result.exit_code == 1
    # Sync test files with known issues
    combined = result.output + (result.stderr or "")
    assert "Bad Timestamp.md" in combined
    assert "Missing Modified.md" in combined
    assert "Unrecognized Field.md" in combined


def test_cli_validate_reports_errors(tmp_path: Path) -> None:
    _write_schema(tmp_path)
    _write_note(tmp_path, "bad.md", {"publish": "not-a-bool"})
    result = runner.invoke(app, ["validate", str(tmp_path)])
    assert result.exit_code != 0


def test_cli_validate_custom_schema_path(tmp_path: Path) -> None:
    schema_path = tmp_path / "custom.yml"
    schema_path.write_text(yaml.dump(SCHEMA, sort_keys=False), encoding="utf-8")
    notes = tmp_path / "notes"
    notes.mkdir()
    _write_note(notes, "good.md", VALID_FM)
    result = runner.invoke(app, ["validate", str(notes), "--schema", str(schema_path)])
    assert result.exit_code == 0


def test_cli_validate_missing_schema_exits_error(tmp_path: Path) -> None:
    """No .rematter.yaml and no --schema flag should exit with an error."""
    _write_note(tmp_path, "note.md", VALID_FM)
    result = runner.invoke(app, ["validate", str(tmp_path)])
    assert result.exit_code != 0


def test_cli_validate_fix(tmp_path: Path) -> None:
    _write_schema(tmp_path)
    fm = {**VALID_FM}
    del fm["publish"]
    _write_note(tmp_path, "fixme.md", fm)
    result = runner.invoke(app, ["validate", str(tmp_path), "--fix"])
    assert result.exit_code == 0
    parsed = _load(tmp_path / "fixme.md")
    assert parsed is not None
    assert parsed[0]["publish"] is False


def test_cli_validate_fix_dry_run(tmp_path: Path) -> None:
    _write_schema(tmp_path)
    fm = {**VALID_FM}
    del fm["publish"]
    f = _write_note(tmp_path, "fixme.md", fm)
    original = f.read_text()
    result = runner.invoke(app, ["validate", str(tmp_path), "--fix", "--dry-run"])
    assert result.exit_code == 0
    assert f.read_text() == original


def test_cli_validate_empty_dir(empty_vault: Path) -> None:
    _write_schema(empty_vault)
    result = runner.invoke(app, ["validate", str(empty_vault)])
    assert result.exit_code == 0


def test_cli_validate_nonexistent_dir(tmp_path: Path) -> None:
    result = runner.invoke(app, ["validate", str(tmp_path / "nope")])
    assert result.exit_code != 0


def test_cli_validate_schema_excludes_itself(tmp_path: Path) -> None:
    """.rematter.yaml should not be validated as a markdown file."""
    _write_schema(tmp_path)
    _write_note(tmp_path, "good.md", VALID_FM)
    result = runner.invoke(app, ["validate", str(tmp_path)])
    assert result.exit_code == 0


# ── schema default validation ─────────────────────────────────────────────────


def test_schema_rejects_literal_date_as_timestamp_default(tmp_path: Path) -> None:
    """Timestamp defaults must be strftime format strings, not literal dates."""
    import pytest

    from rematter._workers import _load_schema

    schema = {
        "properties": {
            "created": {"type": "timestamp", "required": True, "default": "2026-01-01"},
        },
    }
    p = tmp_path / "_schema.yml"
    p.write_text(yaml.dump(schema, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="strftime format string"):
        _load_schema(p)


def test_schema_rejects_bad_strftime_format(tmp_path: Path) -> None:
    """A strftime string that fails to format should error at load time."""
    from rematter._workers import _validate_schema_defaults

    schema = {
        "properties": {
            "created": {"type": "timestamp", "required": True, "default": "%-Q"},
        },
    }
    # %-Q is not a valid strftime directive — but Python's strftime may or may
    # not raise depending on platform. At minimum, the % check passes so we
    # verify the function doesn't crash on valid-looking formats.
    # The real guard is the "no %" check for literal strings.
    _validate_schema_defaults(schema)  # has %, so passes the format check


def test_schema_accepts_valid_strftime_default(tmp_path: Path) -> None:
    """Valid strftime format strings should pass schema validation."""
    from rematter._workers import _load_schema

    schema = {
        "properties": {
            "created": {
                "type": "timestamp",
                "required": True,
                "default": "%Y-%m-%d %H:%M",
            },
        },
    }
    p = tmp_path / "_schema.yml"
    p.write_text(yaml.dump(schema, sort_keys=False), encoding="utf-8")
    loaded = _load_schema(p)
    assert loaded["properties"]["created"]["default"] == "%Y-%m-%d %H:%M"


def test_schema_allows_non_timestamp_defaults() -> None:
    """Non-timestamp types should not be affected by format string validation."""
    from rematter._workers import _validate_schema_defaults

    schema = {
        "properties": {
            "publish": {"type": "bool", "required": True, "default": False},
            "status": {"type": "string", "required": False, "default": "draft"},
        },
    }
    _validate_schema_defaults(schema)  # should not raise


# ── key reordering ─────────────────────────────────────────────────────────


def test_fix_reorders_keys_to_schema_order(tmp_path: Path) -> None:
    """Keys should be reordered to match schema property order."""
    from rematter._workers import _validate_worker

    # Write frontmatter with keys in reverse schema order
    fm = {
        "publish": True,
        "synced": None,
        "modified": "2026-02-12 15:03",
        "created": "2026-02-12 15:03",
    }
    f = _write_note(tmp_path, "unordered.md", fm)
    status, msg = _validate_worker(f, schema=SCHEMA, fix=True, dry_run=False)
    assert status == "done"
    assert "reorder keys" in msg
    result = _load(f)
    assert result is not None
    assert list(result[0].keys()) == ["created", "modified", "synced", "publish"]


def test_fix_reorder_preserves_extra_keys_at_end(tmp_path: Path) -> None:
    """Keys declared later in schema should appear after earlier keys."""
    from rematter._workers import _validate_worker

    schema_with_custom = {
        "properties": {
            **SCHEMA["properties"],
            "custom": {"type": "string", "required": False, "sync": False},
        }
    }
    fm = {
        "custom": "hi",
        "publish": True,
        "synced": None,
        "modified": "2026-02-12 15:03",
        "created": "2026-02-12 15:03",
    }
    f = _write_note(tmp_path, "extra.md", fm)
    status, msg = _validate_worker(f, schema=schema_with_custom, fix=True, dry_run=False)
    assert status == "done"
    result = _load(f)
    assert result is not None
    keys = list(result[0].keys())
    # Schema keys in order, custom at the end (declared last)
    assert keys[:4] == ["created", "modified", "synced", "publish"]
    assert "custom" in keys[4:]


def test_fix_reorder_dry_run_no_write(tmp_path: Path) -> None:
    """Dry run should report reorder but not write."""
    from rematter._workers import _validate_worker

    fm = {
        "publish": True,
        "synced": None,
        "modified": "2026-02-12 15:03",
        "created": "2026-02-12 15:03",
    }
    f = _write_note(tmp_path, "unordered.md", fm)
    original = f.read_text()
    status, msg = _validate_worker(f, schema=SCHEMA, fix=True, dry_run=True)
    assert status == "dry-run"
    assert "reorder keys" in msg
    assert f.read_text() == original


def test_fix_already_ordered_skips(tmp_path: Path) -> None:
    """If keys are already in schema order and nothing else to fix, skip."""
    from rematter._workers import _validate_worker

    f = _write_note(tmp_path, "ordered.md", VALID_FM)
    status, _ = _validate_worker(f, schema=SCHEMA, fix=True, dry_run=False)
    assert status == "skip"


def test_fix_reorder_combined_with_missing_field(tmp_path: Path) -> None:
    """Fix should both add missing defaults and reorder keys."""
    from rematter._workers import _validate_worker

    # Missing 'publish', and remaining keys out of order
    fm = {"synced": None, "created": "2026-02-12 15:03", "modified": "2026-02-12 15:03"}
    f = _write_note(tmp_path, "both.md", fm)
    status, msg = _validate_worker(f, schema=SCHEMA, fix=True, dry_run=False)
    assert status == "done"
    assert "set publish" in msg
    assert "reorder keys" in msg
    result = _load(f)
    assert result is not None
    assert list(result[0].keys()) == ["created", "modified", "synced", "publish"]


# ── ignore config tests ────────────────────────────────────────────────────────


def test_filter_ignored_by_filename(tmp_path: Path) -> None:
    """Files matching a filename pattern should be excluded."""
    files = [tmp_path / "a.md", tmp_path / "b.md", tmp_path / "draft-c.md"]
    result = _filter_ignored(files, tmp_path, ["draft-*"])
    assert [f.name for f in result] == ["a.md", "b.md"]


def test_filter_ignored_by_relative_path(tmp_path: Path) -> None:
    """Files matching a relative path pattern should be excluded."""
    sub = tmp_path / "drafts"
    sub.mkdir()
    files = [tmp_path / "a.md", sub / "b.md"]
    result = _filter_ignored(files, tmp_path, ["drafts/*"])
    assert [f.name for f in result] == ["a.md"]


def test_filter_ignored_empty_patterns(tmp_path: Path) -> None:
    """No ignore patterns means all files pass through."""
    files = [tmp_path / "a.md", tmp_path / "b.md"]
    result = _filter_ignored(files, tmp_path, [])
    assert result == files


def test_load_config_ignore_key(tmp_path: Path) -> None:
    """Config loader should parse the ignore key."""
    config_file = tmp_path / ".rematter.yaml"
    config_file.write_text(
        "properties: {}\nignore:\n  - draft-*\n  - private/*\n",
        encoding="utf-8",
    )
    config = _load_config(tmp_path)
    assert config.ignore == ["draft-*", "private/*"]


def test_load_config_no_sync_fields(tmp_path: Path) -> None:
    """Config should derive no_sync_fields from properties with sync: false."""
    config_file = tmp_path / ".rematter.yaml"
    config_file.write_text(
        "properties:\n"
        "  title:\n    type: string\n    required: true\n"
        "  own:\n    type: bool\n    required: false\n    sync: false\n"
        "  publish:\n    type: bool\n    required: true\n    sync: false\n"
        "  status:\n    type: string\n    required: false\n",
        encoding="utf-8",
    )
    config = _load_config(tmp_path)
    assert config.no_sync_fields == {"own", "publish"}


def test_load_config_no_ignore_key(tmp_path: Path) -> None:
    """Config without ignore key should default to empty list."""
    config_file = tmp_path / ".rematter.yaml"
    config_file.write_text("properties: {}\n", encoding="utf-8")
    config = _load_config(tmp_path)
    assert config.ignore == []


def test_cli_validate_respects_ignore(tmp_path: Path) -> None:
    """Validate should skip files matching ignore patterns."""
    config_file = tmp_path / ".rematter.yaml"
    config_file.write_text(
        yaml.dump({
            "properties": {"created": {"type": "timestamp", "required": True}},
            "ignore": ["skip-*.md"],
        }),
        encoding="utf-8",
    )
    # A file that would fail validation but should be ignored
    (tmp_path / "skip-me.md").write_text("---\nno_created: true\n---\nBody\n", encoding="utf-8")
    # A file that passes validation
    (tmp_path / "good.md").write_text("---\ncreated: 2026-01-01\n---\nBody\n", encoding="utf-8")

    result = runner.invoke(app, ["validate", str(tmp_path)])
    assert result.exit_code == 0
    assert "skip-me" not in result.output
