"""Shared fixtures for rematter tests."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Fresh copy of the fixture vault in a temp directory."""
    dest = tmp_path / "vault"
    shutil.copytree(FIXTURES_DIR, dest)
    return dest


@pytest.fixture
def empty_vault(tmp_path: Path) -> Path:
    """Empty directory with no .md files."""
    d = tmp_path / "empty"
    d.mkdir()
    return d
