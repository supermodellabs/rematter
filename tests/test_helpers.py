"""Unit tests for _load and _dump helpers."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from rematter import _dump, _load

# ── _load ──────────────────────────────────────────────────────────────────────


def test_load_valid_frontmatter(tmp_path: Path) -> None:
    f = tmp_path / "note.md"
    f.write_text("---\ntitle: Hello\ntags: [a, b]\n---\nBody text.\n")
    result = _load(f)
    assert result is not None
    fm, body = result
    assert fm == {"title": "Hello", "tags": ["a", "b"]}
    assert body == "Body text.\n"


def test_load_datetime_string_stays_as_string(tmp_path: Path) -> None:
    """PyYAML 6 safe_load does NOT auto-convert 'YYYY-MM-DD HH:MM' to datetime.
    The value is returned as a plain string, which _filename_worker handles via
    datetime.fromisoformat() fallback."""
    f = tmp_path / "note.md"
    f.write_text("---\ncreated: 2026-02-12 15:03\n---\nBody.\n")
    result = _load(f)
    assert result is not None
    fm, _ = result
    assert isinstance(fm["created"], str)
    assert fm["created"] == "2026-02-12 15:03"


def test_load_date_field_is_date_object(tmp_path: Path) -> None:
    """PyYAML parses bare date strings into date objects."""
    f = tmp_path / "note.md"
    f.write_text("---\nDate: 2026-01-15\n---\nBody.\n")
    result = _load(f)
    assert result is not None
    fm, _ = result
    assert isinstance(fm["Date"], date)
    assert not isinstance(fm["Date"], datetime)


def test_load_no_frontmatter(tmp_path: Path) -> None:
    f = tmp_path / "note.md"
    f.write_text("Just plain text.\n")
    assert _load(f) is None


def test_load_no_opening_delimiter(tmp_path: Path) -> None:
    f = tmp_path / "note.md"
    f.write_text("title: Hello\n---\nBody.\n")
    assert _load(f) is None


def test_load_invalid_yaml(tmp_path: Path) -> None:
    f = tmp_path / "note.md"
    f.write_text("---\n: bad: yaml: [\n---\nBody.\n")
    assert _load(f) is None


def test_load_non_dict_yaml(tmp_path: Path) -> None:
    """Scalar YAML at the top level (not a mapping) should return None."""
    f = tmp_path / "note.md"
    f.write_text("---\n- item1\n- item2\n---\nBody.\n")
    assert _load(f) is None


def test_load_body_with_separator(tmp_path: Path) -> None:
    """Body containing '---' must not confuse frontmatter parsing (XmR charts case)."""
    content = "---\ncreated: 2026-01-21 13:58\nmodified: 2026-02-13 14:20\n---\nIntro.\n\n---\n\n## Section\n"
    f = tmp_path / "xmr.md"
    f.write_text(content)
    result = _load(f)
    assert result is not None
    fm, body = result
    assert list(fm.keys()) == ["created", "modified"]
    assert "---" in body
    assert "## Section" in body


def test_load_empty_frontmatter_returns_none(tmp_path: Path) -> None:
    """Empty frontmatter ('---\\n---') returns None — the regex requires at least
    a newline of content. These files are silently skipped, same as no frontmatter."""
    f = tmp_path / "note.md"
    f.write_text("---\n---\nBody.\n")
    assert _load(f) is None


def test_load_preserves_list_value(tmp_path: Path) -> None:
    f = tmp_path / "note.md"
    f.write_text('---\ncreators:\n  - "[[Andy Matuschak]]"\n---\nBody.\n')
    result = _load(f)
    assert result is not None
    fm, _ = result
    assert fm["creators"] == ["[[Andy Matuschak]]"]


# ── _dump ──────────────────────────────────────────────────────────────────────


def test_dump_with_frontmatter() -> None:
    out = _dump({"title": "Hello", "count": 3}, "Body text.\n")
    assert out.startswith("---\n")
    assert "title: Hello" in out
    assert "count: 3" in out
    assert out.endswith("Body text.\n")


def test_dump_empty_frontmatter_strips_block() -> None:
    """Empty dict should produce body only — no frontmatter delimiters."""
    out = _dump({}, "Body.\n")
    assert out == "Body.\n"
    assert "---" not in out


def test_dump_unicode_preserved() -> None:
    out = _dump({"note": "日本語テスト"}, "Body.\n")
    assert "日本語テスト" in out


def test_dump_round_trip(tmp_path: Path) -> None:
    """_load → _dump → _load should produce identical frontmatter."""
    original = "---\ntitle: Round Trip\ntags: [x, y]\n---\nBody.\n"
    f = tmp_path / "note.md"
    f.write_text(original)
    result = _load(f)
    assert result is not None
    fm, body = result

    rebuilt = _dump(fm, body)
    f2 = tmp_path / "note2.md"
    f2.write_text(rebuilt)
    result2 = _load(f2)
    assert result2 is not None
    assert result2[0] == fm
    assert result2[1] == body


def test_dump_bool_values() -> None:
    """YAML booleans must round-trip without becoming strings."""
    out = _dump({"publish": True, "draft": False}, "")
    assert "publish: true" in out
    assert "draft: false" in out
