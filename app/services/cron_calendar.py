from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from croniter import CroniterBadCronError, croniter

_MAX_EVENTS = 1000


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def build_calendar_events(
    tasks: list[dict[str, Any]],
    *,
    start: datetime,
    end: datetime,
    limit: int = _MAX_EVENTS,
) -> list[dict[str, Any]]:
    """Expand cron-based scheduled tasks into UTC calendar event instances."""

    start_utc = _as_utc(start)
    end_utc = _as_utc(end)
    if end_utc <= start_utc:
        return []

    capped_limit = max(1, min(limit, _MAX_EVENTS))
    events: list[dict[str, Any]] = []
    for task in tasks:
        cron_expression = str(task.get("cron") or "").strip()
        if not cron_expression:
            continue
        try:
            iterator = croniter(cron_expression, start_utc)
        except (CroniterBadCronError, ValueError, KeyError):
            continue

        while len(events) < capped_limit:
            try:
                next_run = iterator.get_next(datetime)
            except (CroniterBadCronError, ValueError, KeyError):
                break
            next_run_utc = _as_utc(next_run)
            if next_run_utc >= end_utc:
                break
            events.append(
                {
                    "id": f"task-{task.get('id')}-{next_run_utc.isoformat()}",
                    "task_id": int(task.get("id") or 0),
                    "title": str(task.get("name") or task.get("command") or "Scheduled task"),
                    "command": str(task.get("command") or ""),
                    "cron": cron_expression,
                    "company_id": task.get("company_id"),
                    "company_name": task.get("company_name") or "All companies",
                    "active": bool(task.get("active")),
                    "start": next_run_utc.isoformat(),
                    "url": f"/admin/scheduled-tasks?taskId={task.get('id')}",
                    "last_status": task.get("last_status"),
                }
            )
        if len(events) >= capped_limit:
            break
    events.sort(key=lambda event: (event["start"], event["title"]))
    return events
