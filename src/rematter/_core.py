"""Core parsing and serialization helpers for markdown frontmatter."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?(.*)", re.DOTALL)
DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2} - ")


def _load(path: Path) -> tuple[dict[str, Any], str] | None:
    """Parse YAML frontmatter from a markdown file.

    Returns (frontmatter_dict, body) or None if the file has no valid frontmatter.
    Files without frontmatter are silently skipped by callers.
    """
    text = path.read_text(encoding="utf-8")
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None
    try:
        fm: Any = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(fm, dict):
        return None
    return fm, m.group(2).lstrip("\n")


def _dump(fm: dict[str, Any], body: str) -> str:
    """Serialize a frontmatter dict and body back to markdown content."""
    if not fm:
        return body
    fm_str = yaml.dump(
        fm,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).rstrip("\n")
    return f"---\n{fm_str}\n---\n{body}"
