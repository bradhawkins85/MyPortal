"""Request-scoped Super Admin role simulation.

The selected role is stored on the authenticated session, while the fact that
the account is a Super Admin is always re-read from the database.  This keeps
the switcher available without allowing a client to opt itself into it.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Mapping

from fastapi import Request

from app.repositories import roles as role_repo
from app.security.menu_permissions import menu_permissions_to_legacy, normalize_menu_permissions


_effective_permissions: ContextVar[frozenset[str] | None] = ContextVar(
    "effective_role_permissions", default=None
)


def effective_role_has_permission(permission: str) -> bool | None:
    """Return the simulated role decision, or ``None`` outside simulation."""

    permissions = _effective_permissions.get()
    if permissions is None:
        return None
    return permission in permissions


def clear_effective_role() -> None:
    _effective_permissions.set(None)


def _virtual_membership(role: Mapping[str, Any], company_id: int | None) -> dict[str, Any]:
    menu_permissions = normalize_menu_permissions(role.get("permissions") or {})
    legacy = set(menu_permissions_to_legacy(menu_permissions))
    permission_fields = {
        "can_manage_licenses": "licenses.manage",
        "can_order_licenses": "licenses.order",
        "can_manage_staff": "staff.manage",
        "can_access_shop": "shop.access",
        "can_access_cart": "cart.access",
        "can_access_orders": "orders.access",
        "can_access_forms": "forms.access",
        "can_manage_assets": "assets.manage",
        "can_manage_invoices": "invoices.manage",
        "can_manage_office_groups": "office_groups.manage",
        "can_manage_issues": "issues.manage",
        "is_admin": "company.admin",
        "can_view_compliance": "compliance.access",
        "can_view_bcp": "continuity.access",
        "can_view_m365_best_practices": "m365_best_practices.access",
        "can_view_compliance_checks": "compliance_checks.access",
        "can_manage_compliance_checks": "compliance_checks.manage",
        "can_view_m365_user_mailboxes": "m365_user_mailboxes.access",
        "can_view_m365_shared_mailboxes": "m365_shared_mailboxes.access",
        "can_access_chat": "chat.access",
    }
    membership: dict[str, Any] = {
        "company_id": company_id,
        "role_id": role.get("id"),
        "role_name": role.get("name"),
        "permissions": menu_permissions,
        "menu_permissions": menu_permissions,
        "combined_permissions": sorted(legacy),
        "staff_permission": 0,
        "status": "active",
    }
    membership.update({field: permission in legacy for field, permission in permission_fields.items()})
    membership["can_access_quotes"] = menu_permissions.get("menu.quotes", "none") != "none"
    return membership


async def apply_selected_role(request: Request, user: dict[str, Any], session: Any) -> dict[str, Any]:
    """Apply a valid session role selection to a database-authenticated user."""

    clear_effective_role()
    is_actor_super_admin = bool(user.get("is_super_admin"))
    request.state.role_switcher_allowed = is_actor_super_admin
    request.state.selected_role = None
    if not is_actor_super_admin or session is None or session.selected_role_id is None:
        return user

    role = await role_repo.get_role_by_id(int(session.selected_role_id))
    if role is None:
        return user

    effective_user = dict(user)
    effective_user["is_super_admin"] = False
    effective_user["role_switcher_allowed"] = True
    effective_user["selected_role_id"] = int(role["id"])
    request.state.selected_role = role
    request.state.active_membership = _virtual_membership(role, session.active_company_id)
    _effective_permissions.set(frozenset(role.get("legacy_permissions") or ()))
    return effective_user


def effective_membership(request: Request, membership: dict[str, Any] | None) -> dict[str, Any] | None:
    """Prefer the request's simulated membership over a persisted membership."""

    state = getattr(request, "state", None)
    if state is not None and getattr(state, "selected_role", None) is not None:
        return getattr(state, "active_membership", None)
    return membership
