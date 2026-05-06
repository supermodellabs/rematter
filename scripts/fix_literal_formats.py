#!/usr/bin/env python3
"""Fix frontmatter values that contain literal strftime format strings.

Scans markdown files in a directory and replaces any frontmatter value that
contains strftime directives (e.g. "%Y-%m-%d %H:%M") with datetime.now()
formatted by that string. No schema needed — any value with a `%` directive
is wrong and gets fixed.

Usage:
    uv run scripts/fix_literal_formats.py <directory> [--dry-run]
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

from rematter._core import _dump, _load

# Matches common strftime directives
_STRFTIME_RE = re.compile(r"%[YymdHIMSpBbAaZzjUWcxX]")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="directory of markdown files")
    parser.add_argument("--dry-run", action="store_true", help="show what would change without writing")
    args = parser.parse_args()

    directory: Path = args.directory.resolve()
    if not directory.is_dir():
        print(f"error: {directory} is not a directory", file=sys.stderr)
        return 1

    now = datetime.now()  # noqa: DTZ005 — local time matches Obsidian's frontmatter format
    fixed_total = 0

    for md in sorted(directory.rglob("*.md")):
        if md.name.startswith("_"):
            continue

        parsed = _load(md)
        if parsed is None:
            continue

        fm, body = parsed
        fixed_fields: list[str] = []

        for field, val in fm.items():
            if not isinstance(val, str):
                continue
            if _STRFTIME_RE.search(val):
                fm[field] = now.strftime(val)
                fixed_fields.append(field)

        if not fixed_fields:
            continue

        fixed_total += 1
        label = ", ".join(fixed_fields)
        if args.dry_run:
            print(f"  [dry-run] {md.name}: would fix {label}")
        else:
            md.write_text(_dump(fm, body), encoding="utf-8")
            print(f"  fixed {md.name}: {label}")

    if fixed_total == 0:
        print("no files contained literal format strings — nothing to fix")
    elif args.dry_run:
        print(f"\n{fixed_total} file(s) would be fixed (dry-run, no changes written)")
    else:
        print(f"\n{fixed_total} file(s) fixed")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
