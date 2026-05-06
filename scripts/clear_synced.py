#!/usr/bin/env python3
"""Clear the value of 'synced' in all markdown files, leaving the key as null.

Scans markdown files recursively in a directory and sets any non-null 'synced'
value to null. The key is preserved — only the value is cleared.

Usage:
    uv run scripts/clear_synced.py <directory> [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rematter._core import _dump, _load


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="directory of markdown files")
    parser.add_argument("--dry-run", action="store_true", help="show what would change without writing")
    args = parser.parse_args()

    directory: Path = args.directory.resolve()
    if not directory.is_dir():
        print(f"error: {directory} is not a directory", file=sys.stderr)
        return 1

    cleared_total = 0

    for md in sorted(directory.rglob("*.md")):
        if md.name.startswith("_"):
            continue

        parsed = _load(md)
        if parsed is None:
            continue

        fm, body = parsed

        if "synced" not in fm or fm["synced"] is None:
            continue

        old_val = fm["synced"]
        fm["synced"] = None
        cleared_total += 1

        if args.dry_run:
            print(f"  [dry-run] {md.name}: would clear synced (was: {old_val})")
        else:
            md.write_text(_dump(fm, body), encoding="utf-8")
            print(f"  cleared {md.name}: synced (was: {old_val})")

    if cleared_total == 0:
        print("no files had a non-null synced value — nothing to clear")
    elif args.dry_run:
        print(f"\n{cleared_total} file(s) would be cleared (dry-run, no changes written)")
    else:
        print(f"\n{cleared_total} file(s) cleared")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
