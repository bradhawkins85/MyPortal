"""Rules for the log file paths the AI troubleshooter and health check may read.

Shared by :mod:`app.services.ai_troubleshooter` and
:mod:`app.services.ai_required_logs`. They mirror the tray agent's checks
(tray/internal/agent/logfiles.go), which are applied again on the device.
"""

from __future__ import annotations

import re

MAX_FILE_PATTERN_LEN = 400
# Environment variables articles use in log paths, mapped to the folders the
# agent (running as SYSTEM) should read instead.
WINDOWS_PATH_VARIABLES: dict[str, str] = {
    "%systemroot%": "C:\\Windows",
    "%windir%": "C:\\Windows",
    "%systemdrive%": "C:",
    "%programdata%": "C:\\ProgramData",
    "%allusersprofile%": "C:\\ProgramData",
    "%userprofile%": "C:\\Users\\*",
    "%appdata%": "C:\\Users\\*\\AppData\\Roaming",
    "%localappdata%": "C:\\Users\\*\\AppData\\Local",
    "%temp%": "C:\\Users\\*\\AppData\\Local\\Temp",
    "%tmp%": "C:\\Users\\*\\AppData\\Local\\Temp",
}
WINDOWS_PATH_VARIABLE_RE = re.compile(r"%[A-Za-z]+%")


def split_path(path: str, windows: bool) -> list[str] | None:
    """Mirror of pathStyle.splitPath in the tray agent."""

    if not path or len(path) > MAX_FILE_PATTERN_LEN:
        return None
    if any(ch in path for ch in '[]"<>|\x00') or any(ord(ch) < 0x20 for ch in path):
        return None
    if windows:
        path = path.replace("/", "\\")
        if len(path) < 3 or not path[0].isascii() or not path[0].isalpha() or path[1:3] != ":\\":
            return None
        if ":" in path[2:]:
            return None
        parts = path.split("\\")
    else:
        if not path.startswith("/"):
            return None
        parts = path.split("/")
    segments: list[str] = []
    for index, part in enumerate(parts):
        if part == "" and index > 0:
            continue
        if part in {".", ".."}:
            return None
        if windows and index > 0 and part.rstrip(". ") != part:
            return None
        segments.append(part)
    return segments


def expand_windows_variables(path: str) -> str:
    return WINDOWS_PATH_VARIABLE_RE.sub(
        lambda match: WINDOWS_PATH_VARIABLES.get(match.group(0).lower(), match.group(0)), path
    )


def valid_extra_root(folder: str, windows: bool) -> bool:
    """Mirror of validExtraRoot in the tray agent."""

    segments = split_path(folder, windows)
    if not segments or len(segments) < 3 or segments[1] == "*":
        return False
    literal = 0
    for segment in segments[1:]:
        if segment == "*":
            continue
        if any(ch in segment for ch in "*?"):
            return False
        literal += 1
    return literal >= 2


__all__ = [
    "MAX_FILE_PATTERN_LEN",
    "WINDOWS_PATH_VARIABLES",
    "expand_windows_variables",
    "split_path",
    "valid_extra_root",
]
