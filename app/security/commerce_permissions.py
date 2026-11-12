"""Company-scoped visibility for quotes, orders and invoices."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import HTTPException, status

from app.security.menu_permissions import normalize_menu_permissions

CommerceScope = Literal["none", "mine", "all"]
COMMERCE_PERMISSION_FIELDS = {
    "can_access_quotes": "menu.quotes",
    "can_access_orders": "menu.orders",
    "can_manage_invoices": "menu.invoices",
}


def commerce_scope(
    user: dict[str, Any], membership: dict[str, Any] | None, key: str
) -> CommerceScope:
    if user.get("is_super_admin"):
        return "all"
    if not membership:
        return "none"
    permissions = normalize_menu_permissions(membership.get("menu_permissions"))
    if (
        membership.get("is_admin")
        or permissions["menu.admin.company"] == "write"
        or permissions["menu.admin.technician"] != "none"
    ):
        return "all"
    if "menu_permissions" in membership:
        return {"none": "none", "read": "mine", "write": "all"}[permissions[key]]
    # Old company permissions were booleans granting company-wide visibility.
    field = next(
        field for field, menu_key in COMMERCE_PERMISSION_FIELDS.items() if menu_key == key
    )
    return "all" if membership.get(field) else "none"


def commerce_record_visible(
    user: dict[str, Any], scope: CommerceScope, record: dict[str, Any], key: str
) -> bool:
    if scope == "all":
        return True
    if scope != "mine" or user.get("id") is None:
        return False
    owner_fields = (
        ("created_by", "assigned_user_id")
        if key == "menu.invoices" else ("user_id", "assigned_user_id")
    )
    return any(
        record.get(field) is not None and str(record[field]) == str(user["id"])
        for field in owner_fields
    )


def require_commerce_record(
    user: dict[str, Any], scope: CommerceScope, record: dict[str, Any], key: str
) -> None:
    if not commerce_record_visible(user, scope, record, key):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found")
