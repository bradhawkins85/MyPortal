#!/usr/bin/env python3
"""Restricted CLI used by the update coordinator to report execution state."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.services import system_update_history


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("update_id")
    parser.add_argument("status", choices=("running", "succeeded", "failed"))
    # "-" reads the output from standard input, which lets the Docker host
    # coordinator stream its log into the container without sharing a path.
    parser.add_argument("--output-file", type=Path)
    parser.add_argument("--error", default=None)
    args = parser.parse_args()
    output = ""
    if args.output_file and str(args.output_file) == "-":
        output = sys.stdin.read()
    elif args.output_file:
        try:
            output = args.output_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            output = "Update output was unavailable."
    try:
        system_update_history.update(
            args.update_id, status=args.status, output=output or None,
            error=args.error, completed=args.status in {"succeeded", "failed"},
        )
    except (KeyError, ValueError):
        # Report the problem in one line; the coordinator treats reporting as
        # best effort and must carry on with the upgrade.
        print(
            f"No system update record {args.update_id} in "
            f"{system_update_history._HISTORY_DIR}; history was not updated.",
            file=sys.stderr,
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
