#!/usr/bin/env python3
"""Restricted CLI used by the update coordinator to report execution state."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.services import system_update_history


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("update_id")
    parser.add_argument("status", choices=("running", "succeeded", "failed"))
    parser.add_argument("--output-file", type=Path)
    parser.add_argument("--error", default=None)
    args = parser.parse_args()
    output = ""
    if args.output_file:
        try:
            output = args.output_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            output = "Update output was unavailable."
    system_update_history.update(
        args.update_id, status=args.status, output=output or None,
        error=args.error, completed=args.status in {"succeeded", "failed"},
    )


if __name__ == "__main__":
    main()
