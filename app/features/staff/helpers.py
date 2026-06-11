"""Shared helpers for the ``staff`` feature pack."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.responses import RedirectResponse


def _main():
    from app import main as main_module

    return main_module


async def _load_staff_context(
    request: Request,
    *,
    require_admin: bool = False,
    require_super_admin: bool = False,
):
    main_module = _main()
    user, redirect = await main_module._require_authenticated_user(request)
    if redirect:
        return user, None, None, 0, None, redirect
    is_super_admin = bool(user.get("is_super_admin"))
    if require_super_admin and not is_super_admin:
        return user, None, None, 0, None, RedirectResponse(
            url="/", status_code=status.HTTP_303_SEE_OTHER
        )
    company_id_raw = user.get("company_id")
    if company_id_raw is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No company associated with the current user",
        )
    try:
        company_id = int(company_id_raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid company identifier",
        ) from exc
    membership = await main_module.user_company_repo.get_user_company(user["id"], company_id)
    membership_data = membership or {}
    staff_permission = int(membership_data.get("staff_permission", 0)) if membership else 0
    raw_staff_menu_access = main_module.normalize_menu_permissions(
        membership_data.get("menu_permissions")
    ).get("menu.staff", "none")
    has_readonly_staff_menu_access = raw_staff_menu_access == "read"
    has_write_staff_menu_access = raw_staff_menu_access == "write"
    legacy_staff_menu_access = main_module._membership_menu_can(
        user, membership, "menu.staff", write=require_admin or require_super_admin
    )
    has_staff_menu_access = (
        has_write_staff_menu_access
        if require_admin or require_super_admin
        else raw_staff_menu_access in {"read", "write"} or legacy_staff_menu_access
    )
    if has_readonly_staff_menu_access and (require_admin or require_super_admin):
        has_staff_menu_access = False
    if not is_super_admin and staff_permission <= 0 and not has_staff_menu_access:
        return user, membership, None, staff_permission, company_id, RedirectResponse(
            url="/", status_code=status.HTTP_303_SEE_OTHER
        )
    if require_admin and not (
        is_super_admin
        or has_staff_menu_access
        or (not has_readonly_staff_menu_access and membership and membership.get("is_admin"))
    ):
        return user, membership, None, staff_permission, company_id, RedirectResponse(
            url="/", status_code=status.HTTP_303_SEE_OTHER
        )
    company = await main_module.company_repo.get_company_by_id(company_id)
    return user, membership, company, staff_permission, company_id, None


def _company_email_domain_set(company_email_domains: list[str]) -> set[str]:
    return {
        str(domain).strip().lower()
        for domain in company_email_domains
        if str(domain).strip()
    }


def _staff_member_matches_company_email_domains(
    staff_member: dict[str, Any], company_email_domains: list[str]
) -> bool:
    """Return whether a staff member should be visible for company domain filtering.

    Staff without an email address are always visible. When no email domains are
    configured for the company all staff are visible. Staff with email addresses
    are only visible when the email domain is present in the company's configured
    email domain list.
    """

    allowed_domains = _company_email_domain_set(company_email_domains)
    if not allowed_domains:
        return True
    email = str(staff_member.get("email") or "").strip().lower()
    if not email:
        return True
    if "@" not in email:
        return False
    _, domain = email.rsplit("@", 1)
    return domain in allowed_domains


def _staff_member_is_offboarding_mail_choice(
    staff_member: dict[str, Any], company_email_domains: list[str]
) -> bool:
    """Return whether staff can be used as an offboarding mail target."""

    email = str(staff_member.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return False
    allowed_domains = _company_email_domain_set(company_email_domains)
    if not allowed_domains:
        return True
    _, domain = email.rsplit("@", 1)
    return domain in allowed_domains


def _filter_staff_for_offboarding_choices(
    staff_members: list[dict[str, Any]], company_email_domains: list[str]
) -> list[dict[str, Any]]:
    """Return unique active staff choices that match company email domains."""

    filtered: list[dict[str, Any]] = []
    seen_emails: set[str] = set()
    for staff_member in staff_members:
        if not _staff_member_is_offboarding_mail_choice(staff_member, company_email_domains):
            continue
        email = str(staff_member.get("email") or "").strip().lower()
        if email in seen_emails:
            continue
        seen_emails.add(email)
        filtered.append(staff_member)
    return filtered


__all__ = [
    "_filter_staff_for_offboarding_choices",
    "_load_staff_context",
    "_staff_member_is_offboarding_mail_choice",
    "_staff_member_matches_company_email_domains",
]
