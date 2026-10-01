#!/usr/bin/env python3
"""Remove obsolete immutable releases while preserving the active release."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def cleanup_releases(release_root: Path, current_link: Path, retain: int = 3) -> list[Path]:
    """Delete release directories older than the newest ``retain`` entries.

    The active release is always retained in addition to the normal retention
    set. Only real directories immediately below the resolved release root are
    eligible, preventing a malformed entry from redirecting deletion elsewhere.
    """
    if retain < 1:
        raise ValueError("retain must be at least one")

    root = release_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"release root is not a directory: {release_root}")
    if not current_link.is_symlink():
        raise ValueError(f"active release path is not a symlink: {current_link}")

    active = current_link.resolve(strict=True)
    if active.parent != root or not active.is_dir():
        raise ValueError(f"active release is outside the release root: {active}")

    releases = [
        entry
        for entry in root.iterdir()
        if entry.is_dir() and not entry.is_symlink() and entry.resolve() == entry
    ]
    if active not in releases:
        raise ValueError(f"active release is not an eligible release directory: {active}")

    newest = set(sorted(releases, key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)[:retain])
    protected = newest | {active}
    removed: list[Path] = []
    for release in releases:
        if release in protected:
            continue
        # Revalidate immediately before deletion to guard against path changes.
        if release.parent != root or release.is_symlink() or release.resolve() != release:
            raise ValueError(f"refusing to remove unsafe release path: {release}")
        shutil.rmtree(release)
        removed.append(release)
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release_root", type=Path)
    parser.add_argument("current_link", type=Path)
    parser.add_argument("--retain", type=int, default=3)
    args = parser.parse_args()

    try:
        removed = cleanup_releases(args.release_root, args.current_link, args.retain)
    except (OSError, ValueError) as exc:
        print(f"Release cleanup failed: {exc}", file=sys.stderr)
        return 1

    if removed:
        print("Release cleanup removed: " + ", ".join(path.name for path in removed), file=sys.stderr)
    else:
        print("Release cleanup found no obsolete releases.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
