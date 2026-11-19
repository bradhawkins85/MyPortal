"""Who may rate an AI output.

A person can rate output they can see: their own AI answers, AI content on a
ticket they requested or watch, and anything a technician can work on.
"""
from __future__ import annotations

from typing import Any, Mapping

from app.repositories import company_memberships as membership_repo
from app.repositories import tickets as tickets_repo

# Same key as app.services.tickets.HELPDESK_PERMISSION_KEY; kept local so the
# ticket service can depend on this module without an import cycle.
HELPDESK_PERMISSION_KEY = "helpdesk.technician"


def _user_id(user: Mapping[str, Any]) -> int | None:
    try:
        return int(user.get("id"))
    except (TypeError, ValueError):
        return None


async def is_technician(user: Mapping[str, Any]) -> bool:
    """Super admins and users holding the helpdesk technician permission."""
    if user.get("is_super_admin"):
        return True
    user_id = _user_id(user)
    if user_id is None:
        return False
    try:
        return bool(await membership_repo.user_has_permission(user_id, HELPDESK_PERMISSION_KEY))
    except RuntimeError:
        return False


async def _can_see_ticket(user_id: int, ticket_id: str | None) -> bool:
    try:
        ticket_id_int = int(ticket_id or "")
    except ValueError:
        return False
    ticket = await tickets_repo.get_ticket(ticket_id_int)
    if not ticket:
        return False
    if ticket.get("requester_id") == user_id:
        return True
    return bool(await tickets_repo.is_ticket_watcher(ticket_id_int, user_id))


async def can_rate(user: Mapping[str, Any], response: Mapping[str, Any], *, is_technician: bool) -> bool:
    user_id = _user_id(user)
    if user_id is None:
        return False
    if response.get("user_id") is not None and int(response["user_id"]) == user_id:
        return True
    if is_technician:
        return True
    if response.get("subject_type") == "ticket":
        return await _can_see_ticket(user_id, response.get("subject_id"))
    return False
