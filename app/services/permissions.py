from __future__ import annotations

from typing import Any, Iterable, Mapping, Set

from app.repositories import company_memberships as membership_repo


async def collect_role_permissions(user_id: int) -> Set[str]:
    """Collect the distinct role permissions granted to the specified user."""

    memberships = await membership_repo.list_memberships_for_user(user_id)
    permissions: set[str] = set()
    for membership in memberships:
        if membership.get("status") and membership["status"] != "active":
            continue
        for permission in _normalise_permissions(membership.get("permissions")):
            permissions.add(permission)
    return permissions


async def user_has_role_permission(user: Mapping[str, Any], permission: str) -> bool:
    """Return True when the provided user is allowed the given role permission."""

    if bool(user.get("is_super_admin")):
        return True
    user_id = user.get("id")
    if user_id is None:
        return False
    permissions = await collect_role_permissions(int(user_id))
    return permission in permissions


def _normalise_permissions(raw_permissions: Any) -> Iterable[str]:
    if raw_permissions is None:
        return []
    if isinstance(raw_permissions, (set, tuple, list)):
        return (str(item) for item in raw_permissions if item)
    return [str(raw_permissions)]
