#!/usr/bin/env python3
"""Lint script: find raw .strftime() calls in Jinja2 templates.

Usage:
    python scripts/check_strftime.py [templates_dir]

Why: timestamps in templates should use the data-utc pattern so they are
formatted in the user's local timezone by the browser JS.  Raw Python
strftime() calls produce UTC strings without any timezone offset display.

The canonical pattern is:
    <span data-utc="{{ some_dt_iso }}"></span>

Exit code: 0 = no raw strftime found; 1 = violations found.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

STRFTIME_PATTERN = re.compile(r"\.strftime\s*\(")

def scan(templates_dir: Path) -> list[tuple[Path, int, str]]:
    violations: list[tuple[Path, int, str]] = []
    for path in sorted(templates_dir.rglob("*.html")):
        if "__pycache__" in str(path):
            continue
        lines = path.read_text(errors="replace").splitlines()
        for lineno, line in enumerate(lines, 1):
            if STRFTIME_PATTERN.search(line):
                violations.append((path, lineno, line.strip()))
    return violations


def main(argv: list[str] | None = None) -> int:
    args = argv or sys.argv[1:]
    templates_dir = Path(args[0]) if args else Path(__file__).parent.parent / "app" / "templates"
    if not templates_dir.is_dir():
        print(f"ERROR: {templates_dir} is not a directory", file=sys.stderr)
        return 2

    violations = scan(templates_dir)
    if not violations:
        print("✓ No raw .strftime() calls found in templates.")
        return 0

    print(f"✗ Found {len(violations)} raw .strftime() call(s) — use data-utc instead:\n")
    for path, lineno, line in violations:
        print(f"  {path.relative_to(templates_dir.parent.parent)}:{lineno}")
        print(f"    {line}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
