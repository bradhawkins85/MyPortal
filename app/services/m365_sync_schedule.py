"""Automatic scheduling of the per-company Microsoft 365 sync tasks.

Once a company's tenant is connected, MyPortal keeps one scheduled task each
for the contact, mailbox and licence syncs.  Each task runs daily at a random
time so that tenants do not all hit Microsoft Graph at once; technicians can
change the schedule afterwards.  A task that already exists for the company
(active or not) is left alone, so a deactivated or rescheduled task is never
recreated or moved.
"""

from __future__ import annotations

import random
from typing import Any

from app.core.database import db
from app.core.logging import log_error, log_info, log_warning
from app.repositories import companies as company_repo
from app.repositories import m365 as m365_repo
from app.repositories import m365_connections as connection_repo
from app.repositories import scheduled_tasks as scheduled_tasks_repo

M365_SYNC_TASKS: tuple[tuple[str, str], ...] = (
    ("sync_m365_contacts", "Sync Microsoft 365 contacts"),
    ("sync_m365_mailboxes", "Sync Microsoft 365 mailboxes"),
    ("sync_m365_licenses", "Sync Microsoft 365 licenses"),
)

_CREDENTIAL_KEYS = ("tenant_id", "client_id", "client_secret")


def random_daily_cron() -> str:
    """Return a daily cron expression (``MM HH * * *``) at a random UTC time."""
    return f"{random.randint(0, 59)} {random.randint(0, 23)} * * *"


async def is_tenant_connected(company_id: int) -> bool:
    """Return whether *company_id* has a usable, active M365 connection.

    Complete credentials are required.  A first connection that has been
    staged but not yet verified (a pending candidate with no active
    connection) does not count; legacy connections that predate managed
    connections have no connection rows and do count.
    """
    credentials = await m365_repo.get_credentials(company_id)
    if not credentials or not all(
        str(credentials.get(key) or "").strip() for key in _CREDENTIAL_KEYS
    ):
        return False
    if await connection_repo.get_active(company_id):
        return True
    return await connection_repo.get_pending(company_id) is None


async def ensure_sync_tasks(company_id: int) -> list[str]:
    """Create any missing M365 sync tasks for a connected company.

    Returns the commands that were created.  Does nothing when the tenant is
    not connected.
    """
    if not await is_tenant_connected(company_id):
        return []
    # Several workers run startup and callbacks concurrently; the lock stops
    # two of them from both seeing a task as missing and creating it twice.
    async with db.acquire_lock(f"m365_sync_tasks_{company_id}", timeout=10) as acquired:
        if not acquired:
            log_warning(
                "Skipped scheduling Microsoft 365 sync tasks; another worker holds the lock",
                company_id=company_id,
            )
            return []
        existing = await scheduled_tasks_repo.get_commands_for_company(company_id)
        missing = [(cmd, label) for cmd, label in M365_SYNC_TASKS if cmd not in existing]
        if not missing:
            return []
        company = await company_repo.get_company_by_id(company_id)
        company_name = str((company or {}).get("name") or "").strip()
        created: list[str] = []
        for command, label in missing:
            await scheduled_tasks_repo.create_task(
                name=f"{company_name} - {label}" if company_name else label,
                command=command,
                cron=random_daily_cron(),
                company_id=company_id,
                active=True,
            )
            created.append(command)
    log_info(
        "Scheduled Microsoft 365 sync tasks for connected tenant",
        company_id=company_id,
        commands=created,
    )
    return created


async def ensure_sync_tasks_and_refresh(company_id: int) -> list[str]:
    """Best-effort :func:`ensure_sync_tasks` followed by a scheduler reload.

    Failures are logged rather than raised so that completing a connection is
    never reported as failed because scheduling went wrong.
    """
    try:
        created = await ensure_sync_tasks(company_id)
        if created:
            from app.services.scheduler import scheduler_service

            await scheduler_service.refresh()
        return created
    except Exception as exc:  # noqa: BLE001
        log_error(
            "Failed to schedule Microsoft 365 sync tasks",
            company_id=company_id,
            error=str(exc),
        )
        return []


async def backfill_sync_tasks() -> dict[str, Any]:
    """Add missing sync tasks for every tenant that is already connected."""
    company_ids = sorted(await m365_repo.list_provisioned_company_ids())
    companies_updated = 0
    tasks_created = 0
    for company_id in company_ids:
        try:
            created = await ensure_sync_tasks(company_id)
        except Exception as exc:  # noqa: BLE001
            log_error(
                "Failed to backfill Microsoft 365 sync tasks",
                company_id=company_id,
                error=str(exc),
            )
            continue
        if created:
            companies_updated += 1
            tasks_created += len(created)
    if tasks_created:
        log_info(
            "Backfilled Microsoft 365 sync tasks",
            companies_updated=companies_updated,
            tasks_created=tasks_created,
        )
    return {"companies_updated": companies_updated, "tasks_created": tasks_created}
