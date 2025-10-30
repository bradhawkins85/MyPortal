"""Utilities that support privileged user impersonation flows."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable

from fastapi import Request

from app.core.logging import log_error, log_info
from app.repositories import auth as auth_repo
from app.repositories import company_memberships as membership_repo
from app.repositories import users as user_repo
from app.security.session import SessionData, session_manager
from app.services import audit as audit_service
from app.services import company_access


class ImpersonationError(Exception):
    """Base class for impersonation failures."""


class NotImpersonatingError(ImpersonationError):
    """Raised when an exit is attempted without an active impersonation."""


class NotImpersonatableError(ImpersonationError):
    """Raised when the target user does not satisfy impersonation criteria."""


class AlreadyImpersonatingError(ImpersonationError):
    """Raised when an actor already operates under an impersonated session."""


class SelfImpersonationError(ImpersonationError):
    """Raised when an actor attempts to impersonate their own account."""


class OriginalSessionUnavailableError(ImpersonationError):
    """Raised when the original session cannot be restored."""


def _parse_permissions(raw: Any) -> list[str]:
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except (TypeError, ValueError):
            return []
        if isinstance(decoded, list):
            return [str(item) for item in decoded if isinstance(item, (str, bytes))]
        return []
    if isinstance(raw, Iterable):
        return [str(item) for item in raw if isinstance(item, (str, bytes))]
    return []


def _membership_grants_permissions(membership: dict[str, Any]) -> bool:
    permissions = membership.get("permissions")
    parsed = _parse_permissions(permissions)
    if parsed:
        return True
    return bool(membership.get("is_admin"))


async def user_is_impersonatable(user_id: int) -> bool:
    memberships = await membership_repo.list_memberships_for_user(user_id, status="active")
    for membership in memberships:
        if _membership_grants_permissions(membership):
            return True
    user = await user_repo.get_user_by_id(user_id)
    return bool(user and user.get("is_super_admin"))


async def list_impersonatable_users() -> list[dict[str, Any]]:
    memberships = await membership_repo.list_impersonatable_memberships()
    aggregated: dict[int, dict[str, Any]] = {}
    for row in memberships:
        try:
            user_id = int(row.get("user_id"))
        except (TypeError, ValueError):
            continue
        entry = aggregated.setdefault(
            user_id,
            {
                "id": user_id,
                "email": row.get("email"),
                "first_name": row.get("first_name"),
                "last_name": row.get("last_name"),
                "is_super_admin": bool(row.get("is_super_admin", 0)),
                "memberships": [],
                "has_permissions": False,
            },
        )
        membership_info = {
            "company_id": row.get("company_id"),
            "company_name": row.get("company_name"),
            "role_name": row.get("role_name"),
            "is_admin": bool(row.get("is_admin")),
            "permissions": _parse_permissions(row.get("permissions")),
        }
        entry["memberships"].append(membership_info)
        if not entry["has_permissions"] and (
            membership_info["permissions"] or membership_info["is_admin"]
        ):
            entry["has_permissions"] = True

    # Include super administrators without memberships.
    try:
        users = await user_repo.list_users()
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to list users for impersonation", error=str(exc))
        users = []
    for user in users:
        if not user.get("is_super_admin"):
            continue
        try:
            user_id = int(user.get("id"))
        except (TypeError, ValueError):
            continue
        entry = aggregated.setdefault(
            user_id,
            {
                "id": user_id,
                "email": user.get("email"),
                "first_name": user.get("first_name"),
                "last_name": user.get("last_name"),
                "is_super_admin": True,
                "memberships": [],
                "has_permissions": True,
            },
        )
        entry["has_permissions"] = True

    eligible: list[dict[str, Any]] = []
    for entry in aggregated.values():
        if entry["has_permissions"] or entry["is_super_admin"]:
            eligible.append(entry)

    def _sort_key(record: dict[str, Any]) -> tuple[str, int]:
        email = str(record.get("email") or "").lower()
        return (email, record["id"])

    eligible.sort(key=_sort_key)
    return eligible


async def start_impersonation(
    *,
    request: Request,
    actor_user: dict[str, Any],
    actor_session: SessionData,
    target_user_id: int,
) -> tuple[dict[str, Any], SessionData]:
    if actor_session.impersonator_user_id is not None:
        raise AlreadyImpersonatingError("Actor already has an active impersonation session")
    try:
        actor_id = int(actor_user.get("id"))
    except (TypeError, ValueError):
        raise NotImpersonatableError("Invalid actor context")
    if actor_id == target_user_id:
        raise SelfImpersonationError("Cannot impersonate the currently authenticated account")
    if not actor_user.get("is_super_admin"):
        raise NotImpersonatableError("Only super administrators can impersonate users")
    target_user = await user_repo.get_user_by_id(target_user_id)
    if not target_user:
        raise NotImpersonatableError("User not found")
    if not await user_is_impersonatable(target_user_id):
        raise NotImpersonatableError("User does not have assigned permissions")

    active_company_id = await company_access.first_accessible_company_id(target_user)
    impersonated_session = await session_manager.create_session(
        target_user_id,
        request,
        active_company_id=active_company_id,
        impersonator_user_id=actor_id,
        impersonator_session_id=actor_session.id,
    )
    if active_company_id is not None:
        target_user["company_id"] = active_company_id

    await audit_service.log_action(
        action="impersonation.start",
        user_id=actor_id,
        entity_type="user",
        entity_id=target_user_id,
        metadata={
            "impersonated_session_id": impersonated_session.id,
            "impersonator_session_id": actor_session.id,
        },
        request=request,
    )
    log_info(
        "Impersonation session created",
        actor_id=actor_id,
        target_user_id=target_user_id,
        impersonated_session_id=impersonated_session.id,
    )
    return target_user, impersonated_session


async def end_impersonation(
    *,
    request: Request,
    session: SessionData,
) -> tuple[dict[str, Any], SessionData]:
    if session.impersonator_session_id is None or session.impersonator_user_id is None:
        raise NotImpersonatingError("No impersonation session to exit")

    original_record = await auth_repo.get_session_by_id(session.impersonator_session_id)
    if not original_record or int(original_record.get("is_active", 0)) != 1:
        await auth_repo.deactivate_session(session.id)
        raise OriginalSessionUnavailableError("Original session is no longer available")

    original_session = session_manager.hydrate_session(original_record)
    current_time = datetime.now(timezone.utc)
    await auth_repo.update_session(
        original_session.id,
        last_seen_at=current_time,
        expires_at=current_time + session_manager.session_ttl,
    )

    impersonator_user = await user_repo.get_user_by_id(original_session.user_id)
    if not impersonator_user:
        await auth_repo.deactivate_session(session.id)
        raise OriginalSessionUnavailableError("Original user record is unavailable")
    if original_session.active_company_id is not None:
        impersonator_user["company_id"] = original_session.active_company_id

    await auth_repo.deactivate_session(session.id)
    await audit_service.log_action(
        action="impersonation.stop",
        user_id=session.impersonator_user_id,
        entity_type="user",
        entity_id=session.user_id,
        metadata={
            "restored_session_id": original_session.id,
            "impersonated_session_id": session.id,
        },
        request=request,
    )
    log_info(
        "Impersonation session exited",
        actor_id=session.impersonator_user_id,
        impersonated_user_id=session.user_id,
        restored_session_id=original_session.id,
    )
    return impersonator_user, original_session
