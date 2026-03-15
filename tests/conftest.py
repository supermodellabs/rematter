"""Shared fixtures for rematter tests."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"
MOCK_SOURCE_DIR = Path(__file__).parent / "mock_source"
MOCK_DEST_DIR = Path(__file__).parent / "mock_dest"


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


@pytest.fixture
def mock_source(tmp_path: Path) -> Path:
    """Fresh copy of mock_source in a temp directory."""
    dest = tmp_path / "mock_source"
    shutil.copytree(MOCK_SOURCE_DIR, dest)
    return dest


@pytest.fixture
def mock_dest(tmp_path: Path) -> Path:
    """Fresh copy of mock_dest in a temp directory."""
    dest = tmp_path / "mock_dest"
    shutil.copytree(MOCK_DEST_DIR, dest)
    return dest
