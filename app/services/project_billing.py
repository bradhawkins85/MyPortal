"""Which tickets' time stays off ticket invoices because of a fixed-fee project.

Kept apart from :mod:`app.services.projects` (which bills milestones through
the invoice generator) so the invoice generator and the Xero ticket sync can
use it without an import cycle.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from loguru import logger

from app.repositories import projects as projects_repo


async def fixed_fee_ticket_ids(ticket_ids: Iterable[Any]) -> set[int]:
    """Tickets whose time must stay off ticket invoices (tasks of fixed-fee projects).

    Never raises: if projects can't be read, nothing is excluded, so a
    projects problem can't stop invoicing."""

    ids = []
    for value in ticket_ids:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    if not ids:
        return set()
    try:
        return await projects_repo.fixed_fee_ticket_ids(ids)
    except Exception as exc:  # noqa: BLE001 - invoicing must not fail because of projects
        logger.warning("Could not check fixed-fee project tickets: {}", exc)
        return set()
