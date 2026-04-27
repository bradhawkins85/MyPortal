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
    alert_no_success_days: int | None = None,
    alert_fail_days: int | None = None,
    alert_unknown_days: int | None = None,
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
        alert_no_success_days=alert_no_success_days,
        alert_fail_days=alert_fail_days,
        alert_unknown_days=alert_unknown_days,
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
    alert_no_success_days: int | None = None,
    alert_fail_days: int | None = None,
    alert_unknown_days: int | None = None,
    clear_alert_no_success_days: bool = False,
    clear_alert_fail_days: bool = False,
    clear_alert_unknown_days: bool = False,
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
        alert_no_success_days=alert_no_success_days,
        alert_fail_days=alert_fail_days,
        alert_unknown_days=alert_unknown_days,
        clear_alert_no_success_days=clear_alert_no_success_days,
        clear_alert_fail_days=clear_alert_fail_days,
        clear_alert_unknown_days=clear_alert_unknown_days,
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
# Alert checks (runs at midnight)
# ---------------------------------------------------------------------------

_ALERT_MODULE_SLUG = "backup-alerts"


async def check_backup_alerts() -> dict[str, int]:
    """Check all active backup jobs for alert conditions and create tickets.

    Runs once per day (at midnight) via the scheduler.  For each active job
    that has at least one threshold configured, we look at the most-recent
    events and raise a ticket when the threshold is exceeded.

    To avoid flooding the ticket queue, one open ticket per job per alert
    type is kept alive.  A new ticket is only created when no open ticket
    with the same ``external_reference`` exists.

    Returns a dict ``{"checked": int, "tickets_created": int}``.
    """
    # Import here to avoid circular imports at module level.
    from app.repositories import tickets as tickets_repo
    from app.services import tickets as tickets_service

    today = datetime.now(timezone.utc).date()
    jobs = await backup_jobs_repo.list_jobs(include_inactive=False)
    checked = 0
    tickets_created = 0

    for job in jobs:
        job_id = int(job["id"])
        company_id = int(job["company_id"])
        job_name = str(job.get("name") or "")

        alert_no_success = job.get("alert_no_success_days") or 0
        alert_fail = job.get("alert_fail_days") or 0
        alert_unknown = job.get("alert_unknown_days") or 0

        if not (alert_no_success or alert_fail or alert_unknown):
            continue

        checked += 1

        # Fetch enough events to cover the widest threshold.
        window = max(alert_no_success, alert_fail, alert_unknown)
        start_date = today - timedelta(days=window - 1)
        events = await backup_jobs_repo.list_events_in_range(
            job_ids=[job_id], start_date=start_date, end_date=today
        )

        # Build a date → status map for quick lookup.
        status_by_date: dict[date, str] = {}
        for ev in events:
            ev_date = ev.get("event_date")
            if isinstance(ev_date, datetime):
                ev_date = ev_date.date()
            if isinstance(ev_date, date):
                status_by_date[ev_date] = str(ev.get("status") or DEFAULT_STATUS)

        # ---- No successful backup in X days --------------------------------
        if alert_no_success:
            ref = f"backup_alert:no_success:{job_id}"
            window_dates = [
                today - timedelta(days=i) for i in range(alert_no_success)
            ]
            has_success = any(
                status_by_date.get(d) == "pass" for d in window_dates
            )
            if not has_success:
                created = await _maybe_create_alert_ticket(
                    tickets_repo=tickets_repo,
                    tickets_service=tickets_service,
                    external_reference=ref,
                    company_id=company_id,
                    subject=f"No Successful Backups in {alert_no_success} Day{'s' if alert_no_success != 1 else ''} — {job_name}",
                    description=(
                        f"Backup job **{job_name}** has not reported a successful "
                        f"backup in the last {alert_no_success} day{'s' if alert_no_success != 1 else ''}.\n\n"
                        f"Please investigate the backup job and resolve any issues."
                    ),
                )
                if created:
                    tickets_created += 1

        # ---- Failed backups for X days -------------------------------------
        if alert_fail:
            ref = f"backup_alert:fail:{job_id}"
            window_dates = [today - timedelta(days=i) for i in range(alert_fail)]
            all_fail = all(
                status_by_date.get(d) == "fail" for d in window_dates
            )
            if all_fail:
                created = await _maybe_create_alert_ticket(
                    tickets_repo=tickets_repo,
                    tickets_service=tickets_service,
                    external_reference=ref,
                    company_id=company_id,
                    subject=f"Failed Backups for {alert_fail} Day{'s' if alert_fail != 1 else ''} — {job_name}",
                    description=(
                        f"Backup job **{job_name}** has reported a **failed** status "
                        f"for the last {alert_fail} consecutive day{'s' if alert_fail != 1 else ''}.\n\n"
                        f"Please investigate the backup job and resolve any issues."
                    ),
                )
                if created:
                    tickets_created += 1

        # ---- Unknown job status for X days ---------------------------------
        if alert_unknown:
            ref = f"backup_alert:unknown:{job_id}"
            window_dates = [
                today - timedelta(days=i) for i in range(alert_unknown)
            ]
            # A missing event defaults to DEFAULT_STATUS ("unknown"), so days
            # without any report are treated as unknown — which is the intended
            # behaviour given the daily seed task.
            all_unknown = all(
                status_by_date.get(d, DEFAULT_STATUS) == DEFAULT_STATUS
                for d in window_dates
            )
            if all_unknown:
                created = await _maybe_create_alert_ticket(
                    tickets_repo=tickets_repo,
                    tickets_service=tickets_service,
                    external_reference=ref,
                    company_id=company_id,
                    subject=f"Unknown Backup Job Status for {alert_unknown} Day{'s' if alert_unknown != 1 else ''} — {job_name}",
                    description=(
                        f"Backup job **{job_name}** has had an **unknown** status "
                        f"for the last {alert_unknown} consecutive day{'s' if alert_unknown != 1 else ''}. "
                        f"No status reports have been received from the backup script.\n\n"
                        f"Please verify that the backup script is running and reporting correctly."
                    ),
                )
                if created:
                    tickets_created += 1

    if checked:
        log_info(
            "Backup alert check completed",
            checked=checked,
            tickets_created=tickets_created,
            check_date=today.isoformat(),
        )
    return {"checked": checked, "tickets_created": tickets_created}


async def _maybe_create_alert_ticket(
    *,
    tickets_repo: Any,
    tickets_service: Any,
    external_reference: str,
    company_id: int,
    subject: str,
    description: str,
) -> bool:
    """Create a ticket for the given alert if no open ticket already exists.

    Returns ``True`` when a new ticket was created, ``False`` when skipped.
    """
    existing = await tickets_repo.find_open_ticket_by_external_reference(
        external_reference
    )
    if existing:
        return False
    try:
        await tickets_service.create_ticket(
            subject=subject,
            description=description,
            requester_id=None,
            company_id=company_id,
            assigned_user_id=None,
            priority="normal",
            status="open",
            category=None,
            module_slug=_ALERT_MODULE_SLUG,
            external_reference=external_reference,
            trigger_automations=True,
        )
        log_info(
            "Backup alert ticket created",
            external_reference=external_reference,
            company_id=company_id,
        )
        return True
    except Exception as exc:  # pragma: no cover - defensive
        log_warning(
            "Failed to create backup alert ticket",
            external_reference=external_reference,
            error=str(exc),
        )
        return False





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
    "check_backup_alerts",
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
