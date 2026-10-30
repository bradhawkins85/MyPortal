"""Per-company single sign-on policy: modes, discovery and enforcement."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.repositories import companies as company_repo
from app.repositories import m365 as m365_repo
from app.repositories import user_companies as user_company_repo
from app.repositories import user_sso_identities as sso_identity_repo
from app.services import audit as audit_service

MODES: tuple[str, ...] = ("off", "optional", "required")


def _normalise_mode(value: Any) -> str:
    return value if value in MODES else "off"


async def get_company_sso_mode(company_id: int) -> str:
    company = await company_repo.get_company_by_id(company_id)
    return _normalise_mode(company.get("sso_mode") if company else None)


async def company_tenant_id(company_id: int) -> str | None:
    company = await company_repo.get_company_by_id(company_id)
    csp_tenant = str((company or {}).get("csp_tenant_id") or "").strip()
    if csp_tenant:
        return csp_tenant.lower()
    credentials = await m365_repo.get_credentials(company_id)
    credential_tenant = str((credentials or {}).get("tenant_id") or "").strip()
    return credential_tenant.lower() or None


async def company_has_tenant(company_id: int) -> bool:
    return await company_tenant_id(company_id) is not None


async def companies_for_tenant(tenant_id: str) -> list[int]:
    return await company_repo.list_sso_company_ids_for_tenant(tenant_id)


async def user_company_ids(user: dict[str, Any]) -> set[int]:
    company_ids: set[int] = set()
    if user.get("company_id") is not None:
        company_ids.add(int(user["company_id"]))
    for row in await user_company_repo.list_companies_for_user(int(user["id"])):
        if row.get("company_id") is not None:
            company_ids.add(int(row["company_id"]))
    return company_ids


async def password_login_allowed(user: dict[str, Any]) -> bool:
    if user.get("is_super_admin"):
        return True
    identity = await sso_identity_repo.get_for_user(int(user["id"]))
    if not identity:
        return True
    tenant_id = str(identity.get("tenant_id") or "").strip().lower()
    member_of = await user_company_ids(user)
    # A link whose tenant no longer reaches any of the user's companies cannot
    # be used to sign in, so it must not lock the password out either.
    if not tenant_id or not member_of & {int(c) for c in await companies_for_tenant(tenant_id)}:
        return True
    for company_id in member_of:
        if await get_company_sso_mode(company_id) == "required":
            return False
    return True


async def discover(email: str) -> str:
    local, separator, domain = (email or "").strip().rpartition("@")
    domain = domain.strip().lower()
    if not separator or not local.strip() or not domain:
        return "off"
    company = await company_repo.get_company_by_email_domain(domain)
    if not company:
        return "off"
    mode = _normalise_mode(company.get("sso_mode"))
    if mode == "off" or not await company_has_tenant(int(company["id"])):
        return "off"
    return mode


async def can_manage_link(
    actor: dict[str, Any],
    target_user: dict[str, Any],
    company_id: int | None,
    managed_company_ids: set[int],
) -> bool:
    if actor.get("is_super_admin"):
        return True
    if target_user.get("is_super_admin") or company_id not in managed_company_ids:
        return False
    target_company_ids = await user_company_ids(target_user)
    return company_id in target_company_ids and target_company_ids <= managed_company_ids


async def set_company_sso_mode(
    company_id: int, mode: str, *, actor: dict[str, Any], request: Request | None
) -> None:
    if mode not in MODES:
        raise ValueError("Unknown sign-in mode.")
    if mode == "required":
        if not await company_has_tenant(company_id):
            raise ValueError(
                "Connect this company's Microsoft 365 tenant before requiring Microsoft sign-in."
            )
        if not await sso_identity_repo.count_for_company(company_id):
            raise ValueError("Link at least one user before requiring Microsoft sign-in.")
    previous = await get_company_sso_mode(company_id)
    await company_repo.update_company(company_id, sso_mode=mode)
    await audit_service.record(
        action="company.sso_mode.update",
        request=request,
        user_id=actor["id"],
        entity_type="company",
        entity_id=company_id,
        before={"sso_mode": previous},
        after={"sso_mode": mode},
    )
