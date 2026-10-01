"""Turn reporting query results into reusable stat-strip tiles."""

from __future__ import annotations
import re
from typing import Any

_SUCCESS = {
    "pass",
    "passed",
    "success",
    "successful",
    "operational",
    "resolved",
    "closed",
    "ready",
    "compliant",
}
_DANGER = {
    "fail",
    "failed",
    "failure",
    "failures",
    "danger",
    "outage",
    "overdue",
    "compromises",
    "detections",
}
_WARNING = {
    "warn",
    "warning",
    "unknown",
    "degraded",
    "pending",
    "stale",
    "attention",
    "not_started",
}
_INFO = {"maintenance", "in_progress", "partial_outage", "queued", "running", "open"}
_TOTAL = {
    "total",
    "checks",
    "jobs",
    "attempts",
    "services",
    "events",
    "users",
    "assets",
    "tickets",
}


def variant_for_label(label: str) -> str:
    """Choose the standard stat-strip palette from a reporting column label."""
    normalised = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    if "partial_outage" in normalised:
        return "partial_outage"
    words = set(normalised.split("_"))
    if words & _DANGER:
        return "danger"
    if words & _WARNING:
        return "warning"
    if words & _SUCCESS:
        return "success"
    if words & _INFO:
        return "info"
    if words & _TOTAL or any(word.startswith("total") for word in words):
        return "total"
    return "neutral"


def build_items(result: dict[str, Any], *, limit: int = 24) -> list[dict[str, Any]]:
    """Flatten a query result into labelled tiles, retaining useful row context."""
    columns = [str(column) for column in result.get("columns") or []]
    rows = result.get("rows") or []
    items: list[dict[str, Any]] = []
    for row in rows:
        context_columns = [
            column
            for column in columns
            if not isinstance(row.get(column), (int, float, bool))
            and row.get(column) not in (None, "")
        ]
        value_columns = [column for column in columns if column not in context_columns]
        if len(rows) == 1:  # Text values may themselves be useful single-row KPIs.
            context_columns, value_columns = [], columns
        context = " · ".join(str(row.get(column)) for column in context_columns)
        for column in value_columns:
            label = column.replace("_", " ").strip().title()
            if context:
                label = f"{context} · {label}"
            items.append(
                {
                    "label": label,
                    "value": row.get(column) if row.get(column) is not None else "—",
                    "variant": variant_for_label(column),
                }
            )
            if len(items) >= limit:
                return items
    return items
