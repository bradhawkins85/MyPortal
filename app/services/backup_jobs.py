"""Backup History service.

Owns business rules for backup jobs:
- canonical status definitions and visual variants
- creating / updating jobs (with token regeneration)
- recording status from the public webhook endpoint
- daily seeding of ``unknown`` events for active jobs
- building the per-day history grid used by the admin page and reports
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from app.core.database import db
from app.core.logging import log_info, log_warning
from app.repositories import backup_jobs as backup_jobs_repo


# ---------------------------------------------------------------------------
# Status definitions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StatusDefinition:
    value: str
    label: str
    variant: str  # one of: success, warning, danger, neutral, info
    pdf_color: str  # hex color used in the PDF grid


STATUS_DEFINITIONS: tuple[StatusDefinition, ...] = (
    StatusDefinition("pass", "Pass", "success", "#10b981"),
    StatusDefinition("warn", "Warn", "warning", "#f59e0b"),
    StatusDefinition("fail", "Fail", "danger", "#ef4444"),
    StatusDefinition("unknown", "Unknown", "neutral", "#9ca3af"),
)

DEFAULT_STATUS = "unknown"
KNOWN_STATUSES = frozenset(definition.value for definition in STATUS_DEFINITIONS)
_STATUS_LOOKUP = {definition.value: definition for definition in STATUS_DEFINITIONS}

# Aliases accepted from external callers ("ok", "success", "error" ...).
_STATUS_ALIASES: dict[str, str] = {
    "pass": "pass",
    "passed": "pass",
    "ok": "pass",
    "success": "pass",
    "successful": "pass",
    "good": "pass",
    "warn": "warn",
    "warning": "warn",
    "warnings": "warn",
    "fail": "fail",
    "failed": "fail",
    "failure": "fail",
    "error": "fail",
    "critical": "fail",
    "unknown": "unknown",
    "missing": "unknown",
    "pending": "unknown",
}

_STATUS_MESSAGE_MAX = 1000
_NAME_MAX = 200
_DESCRIPTION_MAX = 2000


def normalise_status(raw: str | None) -> str:
    if raw is None:
        raise ValueError("Status is required")
    value = str(raw).strip().lower()
    if not value:
        raise ValueError("Status is required")
    if value in _STATUS_ALIASES:
        return _STATUS_ALIASES[value]
    raise ValueError(
        f"Unsupported status '{raw}'. "
        f"Supported values: {', '.join(sorted(KNOWN_STATUSES))}"
    )


def status_definition(value: str) -> StatusDefinition | None:
    return _STATUS_LOOKUP.get(value)


def status_variant(value: str | None) -> str:
    if not value:
        return "neutral"
    definition = _STATUS_LOOKUP.get(value)
    return definition.variant if definition else "neutral"


def status_label(value: str | None) -> str:
    if not value:
        return "Unknown"
    definition = _STATUS_LOOKUP.get(value)
    return definition.label if definition else value.title()


def status_pdf_color(value: str | None) -> str:
    if not value:
        return "#9ca3af"
    definition = _STATUS_LOOKUP.get(value)
    return definition.pdf_color if definition else "#9ca3af"


# ---------------------------------------------------------------------------
# Job CRUD
# ---------------------------------------------------------------------------


def _validate_name(name: str | None) -> str:
    if not name or not str(name).strip():
        raise ValueError("Job name is required")
    cleaned = str(name).strip()
    if len(cleaned) > _NAME_MAX:
        raise ValueError(f"Job name must be {_NAME_MAX} characters or fewer")
    return cleaned


def _validate_description(description: str | None) -> str | None:
    if description is None:
        return None
    cleaned = str(description).strip()
    if not cleaned:
        return None
    if len(cleaned) > _DESCRIPTION_MAX:
        raise ValueError(
            f"Description must be {_DESCRIPTION_MAX} characters or fewer"
        )
    return cleaned


async def create_job(
    *,
    company_id: int,
    name: str,
    description: str | None = None,
    is_active: bool = True,
    created_by: int | None = None,
) -> dict[str, Any]:
    cleaned_name = _validate_name(name)
    cleaned_description = _validate_description(description)
    try:
        company_id_int = int(company_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("A valid company is required") from exc
    token = backup_jobs_repo.generate_job_token()
    job = await backup_jobs_repo.create_job(
        company_id=company_id_int,
        name=cleaned_name,
        description=cleaned_description,
        token=token,
        is_active=bool(is_active),
        created_by=created_by,
    )
    log_info("Backup job created", job_id=job["id"], company_id=company_id_int)
    return job


async def update_job(
    job_id: int,
    *,
    name: str | None = None,
    description: str | None = None,
    company_id: int | None = None,
    is_active: bool | None = None,
) -> dict[str, Any] | None:
    cleaned_name = _validate_name(name) if name is not None else None
    cleaned_description = (
        _validate_description(description) if description is not None else None
    )
    company_id_int: int | None = None
    if company_id is not None:
        try:
            company_id_int = int(company_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("A valid company is required") from exc
    return await backup_jobs_repo.update_job(
        job_id,
        name=cleaned_name,
        description=cleaned_description,
        company_id=company_id_int,
        is_active=is_active,
    )


async def regenerate_token(job_id: int) -> dict[str, Any] | None:
    token = backup_jobs_repo.generate_job_token()
    job = await backup_jobs_repo.update_job(job_id, token=token)
    if job:
        log_info("Backup job token regenerated", job_id=job_id)
    return job


async def delete_job(job_id: int) -> None:
    await backup_jobs_repo.delete_job(job_id)
    log_info("Backup job deleted", job_id=job_id)


async def get_job(job_id: int) -> dict[str, Any] | None:
    return await backup_jobs_repo.get_job(job_id)


async def list_jobs(
    *,
    company_id: int | None = None,
    include_inactive: bool = True,
) -> list[dict[str, Any]]:
    return await backup_jobs_repo.list_jobs(
        company_id=company_id, include_inactive=include_inactive
    )


# ---------------------------------------------------------------------------
# Status reporting (webhook)
# ---------------------------------------------------------------------------


async def record_status(
    *,
    job_token: str,
    status: str,
    message: str | None = None,
    source: str | None = None,
    when: datetime | None = None,
) -> dict[str, Any]:
    """Record a status report for the job identified by ``job_token``.

    Raises ``ValueError`` for invalid input and ``LookupError`` when the
    token does not match an active job.
    """
    if not job_token or not str(job_token).strip():
        raise ValueError("job_id is required")
    canonical_status = normalise_status(status)
    cleaned_message: str | None = None
    if message is not None:
        cleaned_message = str(message).strip() or None
        if cleaned_message and len(cleaned_message) > _STATUS_MESSAGE_MAX:
            cleaned_message = cleaned_message[:_STATUS_MESSAGE_MAX]
    job = await backup_jobs_repo.get_job_by_token(str(job_token).strip())
    if job is None:
        raise LookupError("Unknown job_id")
    if not job.get("is_active"):
        raise PermissionError("Job is disabled")
    when = when or datetime.now(timezone.utc)
    event_date = when.astimezone(timezone.utc).date()
    event = await backup_jobs_repo.upsert_event(
        int(job["id"]),
        event_date,
        status=canonical_status,
        status_message=cleaned_message,
        reported_at=when.replace(tzinfo=None) if when.tzinfo else when,
        source=source,
    )
    log_info(
        "Backup job status recorded",
        job_id=int(job["id"]),
        status=canonical_status,
        event_date=event_date.isoformat(),
    )
    return {"job": job, "event": event}


# ---------------------------------------------------------------------------
# Daily seeding
# ---------------------------------------------------------------------------


async def seed_unknown_events_for_date(target_date: date | None = None) -> int:
    """Insert an ``unknown`` event for every active job for ``target_date``.

    Existing events (where the script already reported) are preserved.
    Returns the number of new rows inserted.
    """
    if target_date is None:
        target_date = datetime.now(timezone.utc).date()
    job_ids = await backup_jobs_repo.list_active_job_ids()
    inserted = 0
    for job_id in job_ids:
        try:
            if await backup_jobs_repo.seed_unknown_event(job_id, target_date):
                inserted += 1
        except Exception as exc:  # pragma: no cover - defensive
            log_warning(
                "Failed to seed unknown backup event",
                job_id=job_id,
                event_date=target_date.isoformat(),
                error=str(exc),
            )
    if inserted:
        log_info(
            "Seeded backup unknown events",
            count=inserted,
            event_date=target_date.isoformat(),
        )
    return inserted


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------


def summarise_latest(latest_by_job: Mapping[int, Mapping[str, Any]]) -> dict[str, Any]:
    """Return ``{ "total": int, "by_status": {status: count} }``."""
    counts: dict[str, int] = {definition.value: 0 for definition in STATUS_DEFINITIONS}
    total = 0
    for event in latest_by_job.values():
        total += 1
        value = str(event.get("status") or DEFAULT_STATUS)
        counts[value] = counts.get(value, 0) + 1
    return {"total": total, "by_status": counts}


def _date_range(start: date, end: date) -> list[date]:
    if end < start:
        return []
    days = (end - start).days
    return [start + timedelta(days=offset) for offset in range(days + 1)]


async def build_history_grid(
    *,
    company_id: int | None = None,
    days: int = 30,
    end_date: date | None = None,
    include_inactive: bool = True,
) -> dict[str, Any]:
    """Build the per-day status grid used by the admin page and reports.

    The result has the shape::

        {
            "dates": [date, date, ...],     # oldest -> newest
            "rows": [
                {
                    "job": {...},
                    "events": [
                        {"date": date, "status": str, "message": str | None},
                        ...
                    ],
                },
                ...
            ],
        }
    """
    if days < 1:
        days = 1
    if end_date is None:
        end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=days - 1)

    jobs = await backup_jobs_repo.list_jobs(
        company_id=company_id, include_inactive=include_inactive
    )
    job_ids = [int(job["id"]) for job in jobs]
    events = await backup_jobs_repo.list_events_in_range(
        job_ids=job_ids, start_date=start_date, end_date=end_date
    )

    by_job: dict[int, dict[date, dict[str, Any]]] = {jid: {} for jid in job_ids}
    for event in events:
        ev_date = event.get("event_date")
        if isinstance(ev_date, datetime):
            ev_date = ev_date.date()
        if not isinstance(ev_date, date):
            continue
        by_job.setdefault(int(event["backup_job_id"]), {})[ev_date] = event

    dates = _date_range(start_date, end_date)
    rows: list[dict[str, Any]] = []
    for job in jobs:
        job_events = by_job.get(int(job["id"]), {})
        cells: list[dict[str, Any]] = []
        for day in dates:
            event = job_events.get(day)
            if event is None:
                cells.append(
                    {
                        "date": day,
                        "status": None,
                        "label": "No data",
                        "variant": "neutral",
                        "pdf_color": "#e5e7eb",
                        "message": None,
                        "reported_at": None,
                    }
                )
            else:
                value = str(event.get("status") or DEFAULT_STATUS)
                cells.append(
                    {
                        "date": day,
                        "status": value,
                        "label": status_label(value),
                        "variant": status_variant(value),
                        "pdf_color": status_pdf_color(value),
                        "message": event.get("status_message"),
                        "reported_at": event.get("reported_at"),
                    }
                )
        rows.append({"job": job, "events": cells})
    return {
        "dates": dates,
        "rows": rows,
        "start_date": start_date,
        "end_date": end_date,
    }


async def list_jobs_with_latest(
    *, company_id: int | None = None, include_inactive: bool = True
) -> list[dict[str, Any]]:
    """Return jobs annotated with their most-recent event."""
    jobs = await backup_jobs_repo.list_jobs(
        company_id=company_id, include_inactive=include_inactive
    )
    job_ids = [int(job["id"]) for job in jobs]
    latest = await backup_jobs_repo.latest_event_per_job(job_ids)
    today = datetime.now(timezone.utc).date()
    today_events = await backup_jobs_repo.list_events_in_range(
        job_ids=job_ids, start_date=today, end_date=today
    )
    today_by_job = {int(event["backup_job_id"]): event for event in today_events}
    annotated: list[dict[str, Any]] = []
    for job in jobs:
        latest_event = latest.get(int(job["id"]))
        today_event = today_by_job.get(int(job["id"]))
        annotated.append(
            {
                **job,
                "latest_event": latest_event,
                "latest_status": (latest_event or {}).get("status") or DEFAULT_STATUS,
                "today_event": today_event,
                "today_status": (today_event or {}).get("status") or DEFAULT_STATUS,
            }
        )
    return annotated


def summarise_jobs(jobs: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Summary used by the admin page stat strip (today's status per job)."""
    counts: "OrderedDict[str, int]" = OrderedDict(
        (definition.value, 0) for definition in STATUS_DEFINITIONS
    )
    total = 0
    for job in jobs:
        total += 1
        value = str(job.get("today_status") or DEFAULT_STATUS)
        counts[value] = counts.get(value, 0) + 1
    return {"total": total, "by_status": dict(counts)}


__all__ = [
    "DEFAULT_STATUS",
    "KNOWN_STATUSES",
    "STATUS_DEFINITIONS",
    "StatusDefinition",
    "build_history_grid",
    "create_job",
    "delete_job",
    "get_job",
    "list_jobs",
    "list_jobs_with_latest",
    "normalise_status",
    "record_status",
    "regenerate_token",
    "seed_unknown_events_for_date",
    "status_definition",
    "status_label",
    "status_pdf_color",
    "status_variant",
    "summarise_jobs",
    "summarise_latest",
    "update_job",
]
