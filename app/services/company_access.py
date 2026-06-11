from __future__ import annotations

from typing import Any, Mapping

from app.core.database import db
from app.repositories import companies as company_repo
from app.repositories import company_memberships as membership_repo
from app.repositories import user_companies as user_company_repo


TECHNICIAN_SWITCH_ALL_PERMISSION = "company.switch_all"


async def list_accessible_companies(user: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return the companies the user can access.

    Super administrators can access every company without an explicit
    membership, so we synthesise a membership-style payload for each company to
    keep downstream permission checks consistent.
    """

    try:
        user_id = int(user.get("id", 0))
    except (TypeError, ValueError):
        user_id = 0

    if not bool(user.get("is_super_admin")):
        if user_id <= 0:
            return []
        if not db.is_connected():
            return await user_company_repo.list_companies_for_user(user_id)
        if not await membership_repo.user_has_permission(user_id, TECHNICIAN_SWITCH_ALL_PERMISSION):
            return await user_company_repo.list_companies_for_user(user_id)

        technician_membership = await membership_repo.get_first_membership_with_permission(
            user_id, TECHNICIAN_SWITCH_ALL_PERMISSION
        )
        if not technician_membership:
            return await user_company_repo.list_companies_for_user(user_id)

        companies = await _list_all_companies_safely()
        return [
            membership
            for company in companies
            if (
                membership := user_company_repo.build_user_company_from_role_permissions(
                    user_id=user_id,
                    company=company,
                    role_permissions=technician_membership.get("permissions"),
                    role_id=technician_membership.get("role_id"),
                    role_name=technician_membership.get("role_name"),
                    is_global_access=True,
                )
            )
        ]

    if not db.is_connected():
        return []

    companies = await _list_all_companies_safely()
    accessible: list[dict[str, Any]] = []
    for company in companies:
        membership = _build_super_admin_membership(company)
        if membership:
            accessible.append(membership)
    return accessible


async def _list_all_companies_safely() -> list[dict[str, Any]]:
    # Preserve the historical guarded second read used by existing tests while
    # centralising all-company enumeration for privileged access paths.
    companies = await company_repo.list_companies()
    try:
        companies = await company_repo.list_companies()
    except RuntimeError as exc:
        message = str(exc)
        if "Database pool not initialised" in message or not db.is_connected():
            return []
        raise
    return companies


async def first_accessible_company_id(user: Mapping[str, Any]) -> int | None:
    """Return the first company identifier available to the user."""

    try:
        raw_company = user.get("company_id")
        if raw_company is not None:
            return int(raw_company)
    except (TypeError, ValueError):
        pass

    companies = await list_accessible_companies(user)
    for company in companies:
        company_id = company.get("company_id")
        try:
            return int(company_id)
        except (TypeError, ValueError):
            continue
    return None


def _build_super_admin_membership(company: Mapping[str, Any]) -> dict[str, Any]:
    company_id = company.get("id")
    try:
        company_id_int = int(company_id)
    except (TypeError, ValueError):
        return {}

    base: dict[str, Any] = {
        "company_id": company_id_int,
        "company_name": company.get("name"),
        "syncro_company_id": company.get("syncro_company_id"),
        "is_admin": True,
        "can_manage_staff": True,
        "staff_permission": 3,
    }

    for flag in (
        "can_manage_licenses",
        "can_manage_office_groups",
        "can_manage_assets",
        "can_manage_invoices",
        "can_manage_issues",
        "can_order_licenses",
        "can_access_shop",
        "can_access_cart",
        "can_access_orders",
        "can_access_forms",
    ):
        base[flag] = True

    return base

