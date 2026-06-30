"""Helpers for formatting billable time on invoice descriptions."""
from __future__ import annotations


def format_billable_minutes(minutes: int) -> str:
    """Return a human-readable invoice duration for whole billable minutes.

    Examples:
        30 -> "30 Mins"
        150 -> "2 Hours 30 Mins"
    """
    total_minutes = max(0, int(minutes))
    hours, remaining_minutes = divmod(total_minutes, 60)

    parts: list[str] = []
    if hours:
        parts.append(f"{hours} {'Hour' if hours == 1 else 'Hours'}")
    if remaining_minutes or not parts:
        parts.append(f"{remaining_minutes} {'Min' if remaining_minutes == 1 else 'Mins'}")
    return " ".join(parts)
