#!/usr/bin/env python3
"""Remove obsolete immutable releases while preserving the active release."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from collections.abc import Iterable
from pathlib import Path


def _release_containing(root: Path, path: Path) -> Path | None:
    """Return the release directly below ``root`` that contains ``path``."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        return None
    return root / relative.parts[0] if relative.parts else None


def _releases_in_use(root: Path, proc_root: Path) -> set[Path]:
    """Return releases that are the working directory of a running process.

    Instance units ``cd`` into their release before starting uvicorn, and a
    worker keeps serving from that path until it is restarted, even after the
    instance and current links move on. Deleting it leaves the worker unable to
    load any template it has not yet cached.
    """
    in_use: set[Path] = set()
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return in_use
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            cwd = Path(os.readlink(entry / "cwd"))
        except OSError:
            # The process exited, or belongs to a user we cannot inspect.
            continue
        release = _release_containing(root, cwd)
        if release is not None:
            in_use.add(release)
    return in_use


def cleanup_releases(
    release_root: Path,
    current_link: Path,
    retain: int = 3,
    protected_links: Iterable[Path] = (),
    proc_root: Path | None = Path("/proc"),
) -> list[Path]:
    """Delete release directories older than the newest ``retain`` entries.

    The active release is always retained in addition to the normal retention
    set, as are releases targeted by ``protected_links`` (the blue/green
    instance links) and releases a running process is working from. Only real
    directories immediately below the resolved release root are eligible,
    preventing a malformed entry from redirecting deletion elsewhere.
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
    for link in protected_links:
        if not link.is_symlink():
            continue
        try:
            target = link.resolve(strict=True)
        except OSError:
            continue
        release = _release_containing(root, target)
        if release is not None:
            protected.add(release)
    if proc_root is not None:
        protected |= _releases_in_use(root, proc_root)
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
    parser.add_argument(
        "--protect",
        type=Path,
        action="append",
        default=[],
        help="Symlink whose release target must be retained (repeatable).",
    )
    args = parser.parse_args()

    try:
        removed = cleanup_releases(
            args.release_root, args.current_link, args.retain, protected_links=args.protect
        )
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
