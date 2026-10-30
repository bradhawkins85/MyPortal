"""Administrative management of per-company Microsoft 365 sign-in links."""

from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import quote

from fastapi import Request

from app.repositories import user_sso_identities as sso_identity_repo
from app.repositories import users as user_repo
from app.services import audit as audit_service
from app.services import m365 as m365_service
from app.services import sso_policy

PROVIDER = "m365"
GRAPH_USERS_URL = "https://graph.microsoft.com/v1.0/users"
DIRECTORY_LIMIT = 25
_GUID_PATTERN = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

NO_TENANT_MESSAGE = "Connect this company's Microsoft 365 tenant first."
UNREACHABLE_MESSAGE = "Microsoft 365 could not be reached. Try again."
NOT_FOUND_MESSAGE = "That Microsoft user was not found in this company's tenant."
MUST_STAY_LINKED_MESSAGE = (
    "Your organisation requires Microsoft sign-in, so this account must stay linked."
)


class SsoAdminError(Exception):
    """A link-management failure carrying an HTTP status and user-facing copy."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def is_guid(value: str) -> bool:
    return bool(_GUID_PATTERN.fullmatch(value or ""))


def _object_id_hash(object_id: str) -> str:
    return hashlib.sha256(object_id.encode()).hexdigest()[:16]


async def _load_manageable_user(
    actor: dict[str, Any],
    target_user_id: int,
    company_id: int | None,
    managed_company_ids: set[int],
) -> dict[str, Any]:
    target = await user_repo.get_user_by_id(target_user_id)
    if not target:
        raise SsoAdminError(404, "User not found.")
    if not await sso_policy.can_manage_link(actor, target, company_id, managed_company_ids):
        raise SsoAdminError(403, "You do not have permission to manage this user's Microsoft sign-in.")
    return target


async def _require_tenant(company_id: int) -> str:
    tenant_id = await sso_policy.company_tenant_id(company_id)
    if not tenant_id:
        raise SsoAdminError(400, NO_TENANT_MESSAGE)
    return tenant_id


async def link_identity(
    *,
    actor: dict[str, Any],
    target_user_id: int,
    company_id: int,
    object_id: str,
    managed_company_ids: set[int],
    request: Request | None,
) -> dict[str, Any]:
    if not is_guid(object_id):
        raise SsoAdminError(400, "That is not a valid Microsoft user ID.")
    object_id = object_id.lower()
    target = await _load_manageable_user(actor, target_user_id, company_id, managed_company_ids)
    tenant_id = await _require_tenant(company_id)

    try:
        access_token = await m365_service.acquire_access_token(company_id)
        directory_user = await m365_service._graph_get(
            access_token,
            GRAPH_USERS_URL + "/" + quote(object_id, safe="")
            + "?$select=id,userPrincipalName,mail",
        )
    except m365_service.M365Error as exc:
        if exc.http_status == 404:
            raise SsoAdminError(404, NOT_FOUND_MESSAGE) from exc
        raise SsoAdminError(502, UNREACHABLE_MESSAGE) from exc

    returned_id = str(directory_user.get("id") or object_id).lower()
    if returned_id != object_id:
        raise SsoAdminError(502, UNREACHABLE_MESSAGE)

    existing = await sso_identity_repo.get_by_subject(PROVIDER, tenant_id, object_id)
    if existing and int(existing["user_id"]) != int(target["id"]):
        owner = await user_repo.get_user_by_id(int(existing["user_id"]))
        owner_email = (owner or {}).get("email") or "another user"
        raise SsoAdminError(409, f"That Microsoft account is already linked to {owner_email}.")

    identity = await sso_identity_repo.replace_for_user(
        user_id=int(target["id"]),
        provider=PROVIDER,
        tenant_id=tenant_id,
        object_id=object_id,
        upn=directory_user.get("userPrincipalName"),
        email=directory_user.get("mail"),
        linked_via="admin",
        linked_by_user_id=int(actor["id"]),
    )
    await audit_service.record(
        action="auth.sso.link",
        request=request,
        user_id=int(actor["id"]),
        entity_type="user_sso_identity",
        entity_id=identity.get("id"),
        metadata={
            "linked_via": "admin",
            "tenant_id": tenant_id,
            "object_id_hash": _object_id_hash(object_id),
            "company_id": company_id,
        },
    )
    return identity


async def _record_unlink(
    *,
    actor_id: int,
    deleted: dict[str, Any],
    company_id: int | None,
    request: Request | None,
) -> None:
    await audit_service.record(
        action="auth.sso.unlink",
        request=request,
        user_id=actor_id,
        entity_type="user_sso_identity",
        entity_id=deleted.get("id"),
        metadata={
            "linked_via": deleted.get("linked_via"),
            "tenant_id": deleted.get("tenant_id"),
            "object_id_hash": _object_id_hash(str(deleted.get("object_id") or "")),
            "company_id": company_id,
        },
    )


async def unlink_identity(
    *,
    actor: dict[str, Any],
    target_user_id: int,
    company_id: int | None,
    managed_company_ids: set[int],
    request: Request | None,
) -> dict[str, Any] | None:
    target = await _load_manageable_user(actor, target_user_id, company_id, managed_company_ids)
    deleted = await sso_identity_repo.delete_for_user(int(target["id"]), PROVIDER)
    if not deleted:
        return None
    await _record_unlink(
        actor_id=int(actor["id"]), deleted=deleted, company_id=company_id, request=request
    )
    return deleted


async def must_stay_linked(user: dict[str, Any]) -> bool:
    for company_id in await sso_policy.user_company_ids(user):
        if await sso_policy.get_company_sso_mode(company_id) == "required":
            return True
    return False


async def unlink_own_identity(
    *, user: dict[str, Any], request: Request | None
) -> dict[str, Any] | None:
    if await must_stay_linked(user):
        raise SsoAdminError(403, MUST_STAY_LINKED_MESSAGE)
    deleted = await sso_identity_repo.delete_for_user(int(user["id"]), PROVIDER)
    if not deleted:
        return None
    await _record_unlink(
        actor_id=int(user["id"]), deleted=deleted, company_id=None, request=request
    )
    return deleted


async def company_sso_summary(company_id: int) -> dict[str, Any]:
    return {
        "mode": await sso_policy.get_company_sso_mode(company_id),
        "tenant_connected": await sso_policy.company_has_tenant(company_id),
        "identities": await sso_identity_repo.list_for_company(company_id, PROVIDER),
    }


async def search_directory(company_id: int, query: str) -> list[dict[str, Any]]:
    term = re.sub(r'["\\]', "", query or "").strip()
    if len(term) < 2:
        raise SsoAdminError(400, "Enter at least two characters to search.")
    await _require_tenant(company_id)
    expression = '"displayName:' + term + '" OR "userPrincipalName:' + term + '"'
    url = (
        GRAPH_USERS_URL
        + "?$search=" + quote(expression, safe="")
        + "&$select=id,displayName,userPrincipalName,mail"
        + "&$top=" + str(DIRECTORY_LIMIT)
    )
    try:
        access_token = await m365_service.acquire_access_token(company_id)
        data = await m365_service._graph_get(
            access_token, url, extra_headers={"ConsistencyLevel": "eventual"}
        )
    except m365_service.M365Error as exc:
        raise SsoAdminError(502, UNREACHABLE_MESSAGE) from exc
    return [
        {
            "object_id": item.get("id"),
            "display_name": item.get("displayName"),
            "upn": item.get("userPrincipalName"),
            "mail": item.get("mail"),
        }
        for item in (data.get("value") or [])[:DIRECTORY_LIMIT]
        if item.get("id")
    ]
