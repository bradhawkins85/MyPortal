"""Authorisation helpers for chat rooms.

Visibility mirrors the chat room list: staff (super admins and helpdesk
technicians) see every room, while other users only see rooms they created or
participate in.
"""

from __future__ import annotations

from typing import Any

from app.repositories import chat as chat_repo
from app.repositories import company_memberships as membership_repo

HELPDESK_PERMISSION_KEY = "helpdesk.technician"


def _user_id(user: dict[str, Any]) -> int | None:
    try:
        return int(user.get("id"))
    except (TypeError, ValueError):
        return None


async def is_chat_staff(user: dict[str, Any]) -> bool:
    """Return True for super admins and helpdesk technicians."""

    if user.get("is_super_admin") or user.get("is_helpdesk_technician"):
        return True
    user_id = _user_id(user)
    if user_id is None:
        return False
    try:
        return await membership_repo.user_has_permission(user_id, HELPDESK_PERMISSION_KEY)
    except RuntimeError:
        return False


async def can_access_room(room: dict[str, Any], user: dict[str, Any]) -> bool:
    """Return whether ``user`` may read or post in ``room``."""

    if await is_chat_staff(user):
        return True
    user_id = _user_id(user)
    if user_id is None:
        return False
    creator_id = room.get("created_by_user_id")
    if creator_id is not None:
        try:
            if int(creator_id) == user_id:
                return True
        except (TypeError, ValueError):
            pass
    participant = await chat_repo.get_participant(int(room["id"]), user_id=user_id)
    return participant is not None
