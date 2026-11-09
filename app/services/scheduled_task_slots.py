"""Default schedules for month-end billing tasks.

Generating invoices and syncing them to Xero run on the last day of the
month, one company at a time. New tasks for these commands default to the
minute after the latest existing task for the same command so companies
queue up one minute apart (``3 18 L * *`` → ``4 18 L * *``) instead of all
starting at the same moment.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

BASE_COMMAND_CRONS: dict[str, str] = {
    "unbill_time_entries": "0 0 L * *",
    "generate_invoice": "1 0 L * *",
    "sync_to_xero": "1 15 L * *",
    "sync_to_xero_auto_send": "1 16 L * *",
}

SEQUENTIAL_COMMANDS: frozenset[str] = frozenset(
    {"generate_invoice", "sync_to_xero", "sync_to_xero_auto_send"}
)

_MINUTES_PER_DAY = 24 * 60
_MONTH_END_PATTERN = re.compile(r"^(\d{1,2})\s+(\d{1,2})\s+L\s+\*\s+\*$")


def _month_end_slot(cron: Any) -> int | None:
    """Return the minute-of-day for a ``M H L * *`` cron, else ``None``."""
    if not isinstance(cron, str):
        return None
    match = _MONTH_END_PATTERN.match(cron.strip())
    if not match:
        return None
    minute, hour = int(match.group(1)), int(match.group(2))
    if minute > 59 or hour > 23:
        return None
    return hour * 60 + minute


def _slot_to_cron(slot: int) -> str:
    hour, minute = divmod(slot % _MINUTES_PER_DAY, 60)
    return f"{minute} {hour} L * *"


def _occupied_slots(tasks: Iterable[Mapping[str, Any]], command: str) -> tuple[set[int], int | None]:
    occupied: set[int] = set()
    latest: int | None = None
    for task in tasks:
        slot = _month_end_slot(task.get("cron"))
        if slot is None:
            continue
        occupied.add(slot)
        if task.get("command") == command and (latest is None or slot > latest):
            latest = slot
    return occupied, latest


def _next_free(start: int, occupied: set[int]) -> int:
    for offset in range(_MINUTES_PER_DAY):
        candidate = (start + offset) % _MINUTES_PER_DAY
        if candidate not in occupied:
            return candidate
    return start % _MINUTES_PER_DAY


def next_month_end_cron(command: str, tasks: Iterable[Mapping[str, Any]]) -> str | None:
    """Return the default cron for a new ``command`` task.

    For sequential billing commands this is the minute after the latest
    existing task for the same command, skipping any minute already taken by
    another month-end task. With no existing task for the command the base
    default is used (or the next free minute after it).
    """
    base = BASE_COMMAND_CRONS.get(command)
    if base is None or command not in SEQUENTIAL_COMMANDS:
        return base
    occupied, latest = _occupied_slots(tasks, command)
    base_slot = _month_end_slot(base)
    start = latest + 1 if latest is not None else (base_slot if base_slot is not None else 0)
    return _slot_to_cron(_next_free(start, occupied))


def allocate_month_end_crons(
    command: str,
    tasks: Iterable[Mapping[str, Any]],
    count: int,
    start_cron: str | None = None,
) -> list[str] | None:
    """Allocate ``count`` consecutive free month-end slots for ``command``.

    Returns ``None`` when the command is not a sequential billing command or
    ``start_cron`` is given but is not a ``M H L * *`` expression, so the
    caller can fall back to its own scheduling.
    """
    if command not in SEQUENTIAL_COMMANDS:
        return None
    task_list = list(tasks)
    if start_cron:
        start = _month_end_slot(start_cron)
        if start is None:
            return None
    else:
        start = _month_end_slot(next_month_end_cron(command, task_list))
        if start is None:  # pragma: no cover - base defaults are month-end
            return None
    occupied, _ = _occupied_slots(task_list, command)
    crons: list[str] = []
    cursor = start
    for _ in range(max(count, 0)):
        slot = _next_free(cursor, occupied)
        occupied.add(slot)
        crons.append(_slot_to_cron(slot))
        cursor = slot + 1
    return crons


def command_default_crons(tasks: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """Return default crons for every command that has one."""
    task_list = list(tasks)
    defaults: dict[str, str] = {}
    for command in BASE_COMMAND_CRONS:
        cron = next_month_end_cron(command, task_list)
        if cron:
            defaults[command] = cron
    return defaults
