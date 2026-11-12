"""Microsoft 365 single sign-on: start, code redemption and user resolution."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import quote, urlencode

import aiomysql
import httpx
from fastapi import Request

from app.repositories import companies as company_repo
from app.repositories import sso_provisioning
from app.repositories import user_sso_identities as sso_identity_repo
from app.repositories import users as user_repo
from app.security.passwords import hash_password
from app.services import audit as audit_service
from app.services import m365 as m365_service
from app.services import m365_oauth_transactions, sso_policy
from app.services.monitored_http import monitored_client

PROVIDER = "m365"
SCOPE = "openid profile email"
AUTHORIZE_URL = "https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize"
TOKEN_URL = "https://login.microsoftonline.com/organizations/oauth2/v2.0/token"
AUTHENTICATION_METHOD = "m365_sso"
GENERIC_FAILURE = (
    "This Microsoft account is not eligible to sign in to MyPortal. Contact your administrator."
)
TENANT_NOT_ENABLED_MESSAGE = "Microsoft sign-in is not enabled for your organisation."


@dataclass(frozen=True)
class StartResult:
    authorize_url: str
    state_transaction_id: str
    binding: str


@dataclass(frozen=True)
class Outcome:
    user: dict[str, Any] | None
    identity: dict[str, Any] | None
    failure: str | None
    mfa_satisfied: bool
    message: str


def _hash_binding(binding: str) -> str:
    return hashlib.sha256(binding.encode()).hexdigest()


async def start(
    *,
    redirect_uri: str,
    login_hint: str | None,
    next_path: str | None,
    state_from_transaction: Callable[[str], str] = lambda transaction_id: transaction_id,
) -> StartResult:
    code_verifier, code_challenge = m365_service.generate_pkce_pair()
    nonce = secrets.token_urlsafe(16)
    binding = secrets.token_urlsafe(32)
    # Anonymous visitors must never cause an app registration to be created.
    client_id = await m365_service.get_effective_pkce_client_id(provision=False)
    if not client_id or client_id == m365_service.AZURE_CLI_CLIENT_ID:
        raise m365_service.M365Error("No MyPortal sign-in app is registered with Microsoft")
    transaction_id = await m365_oauth_transactions.create(
        flow="sso_login",
        code_verifier=code_verifier,
        nonce=nonce,
        client_id=client_id,
        redirect_uri=redirect_uri,
        binding_hash=_hash_binding(binding),
        login_hint=login_hint,
        next_path=next_path,
    )
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": SCOPE,
        "state": state_from_transaction(transaction_id),
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    if login_hint:
        params["login_hint"] = login_hint
    return StartResult(
        authorize_url=AUTHORIZE_URL + "?" + urlencode(params),
        state_transaction_id=transaction_id,
        binding=binding,
    )


def binding_matches(transaction: dict[str, Any], binding: str | None) -> bool:
    expected = str(transaction.get("binding_hash") or "")
    if not binding or not expected:
        return False
    return hmac.compare_digest(_hash_binding(binding), expected)


def mfa_satisfied(claims: dict[str, Any]) -> bool:
    amr = claims.get("amr")
    if isinstance(amr, str):
        amr = amr.split()
    if not isinstance(amr, (list, tuple)):
        return False
    return "mfa" in amr


async def redeem_code(transaction: dict[str, Any], code: str) -> dict[str, Any]:
    client_id = str(transaction.get("client_id") or "")
    data = {
        "client_id": client_id,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": str(transaction.get("redirect_uri") or ""),
        "code_verifier": str(transaction.get("code_verifier") or ""),
        "scope": SCOPE,
    }
    try:
        async with monitored_client(httpx.AsyncClient, timeout=15) as client:
            response = await client.post(TOKEN_URL, data=data)
    except httpx.HTTPError as exc:
        raise m365_service.M365Error("Microsoft token request failed") from exc
    if response.status_code < 200 or response.status_code >= 300:
        message = "Microsoft rejected the sign-in code"
        try:
            failure = response.json()
        except ValueError:
            failure = None
        error_codes = failure.get("error_codes") if isinstance(failure, dict) else None
        if isinstance(error_codes, list):
            # Keep only bounded numeric AADSTS codes; descriptions can contain
            # identities, authorization codes or other credentials.
            codes = [
                "AADSTS" + str(value)
                for value in error_codes[:5]
                if type(value) is int and 0 <= value <= 999999999
            ]
            if codes:
                message += " (" + ", ".join(codes) + ")"
        raise m365_service.M365Error(message, http_status=response.status_code)
    try:
        payload = response.json()
    except ValueError as exc:
        raise m365_service.M365Error("Microsoft returned an unreadable token response") from exc
    id_token = str(payload.get("id_token") or "") if isinstance(payload, dict) else ""
    if not id_token:
        raise m365_service.M365Error("Microsoft did not return an identity token")
    claims = await m365_service.validate_microsoft_id_token(id_token, client_id=client_id)
    expected_nonce = str(transaction.get("nonce") or "")
    received_nonce = str(claims.get("nonce") or "")
    if not expected_nonce or not hmac.compare_digest(expected_nonce.encode(), received_nonce.encode()):
        raise m365_service.M365Error("Microsoft identity nonce did not match")
    if not str(claims.get("oid") or "").strip():
        raise m365_service.M365Error("Microsoft identity did not include an object id")
    return claims


def _failure(reason: str) -> Outcome:
    message = TENANT_NOT_ENABLED_MESSAGE if reason == "tenant_not_enabled" else GENERIC_FAILURE
    return Outcome(user=None, identity=None, failure=reason, mfa_satisfied=False, message=message)


async def _audit_failure(
    reason: str, user: dict[str, Any], tenant_id: str, request: Request | None
) -> None:
    await audit_service.record(
        action="auth.sso.fail",
        request=request,
        user_id=int(user["id"]),
        entity_type="user",
        entity_id=int(user["id"]),
        metadata={
            "reason": reason,
            "tenant_id": tenant_id,
            "authentication_method": AUTHENTICATION_METHOD,
        },
    )


async def resolve(claims: dict[str, Any], *, request: Request | None) -> Outcome:
    tenant_id = str(claims.get("tid") or "").strip().lower()
    object_id = str(claims.get("oid") or "").strip().lower()
    if not tenant_id or not object_id:
        return _failure("no_link")

    companies = await sso_policy.companies_for_tenant(tenant_id)
    if not companies:
        return _failure("tenant_not_enabled")

    identity = await sso_identity_repo.get_by_subject(PROVIDER, tenant_id, object_id)
    if identity:
        return await _resolve_linked(identity, claims, companies, tenant_id, request)
    return await _resolve_auto_link(claims, companies, tenant_id, object_id, request)


async def _resolve_linked(
    identity: dict[str, Any],
    claims: dict[str, Any],
    companies: list[int],
    tenant_id: str,
    request: Request | None,
) -> Outcome:
    user = await user_repo.get_user_by_id(int(identity["user_id"]))
    if not user:
        return _failure("no_link")
    if int(user.get("is_active", 1)) != 1:
        await _audit_failure("account_disabled", user, tenant_id, request)
        return _failure("account_disabled")
    matching_companies = await sso_policy.user_company_ids(user) & {int(c) for c in companies}
    if not matching_companies:
        await _audit_failure("company_mismatch", user, tenant_id, request)
        return _failure("company_mismatch")
    if not await _enabled_directory_user(min(matching_companies), str(claims.get("oid") or "").lower()):
        await _audit_failure("microsoft_account_disabled", user, tenant_id, request)
        return _failure("microsoft_account_disabled")
    return Outcome(
        user=user,
        identity=identity,
        failure=None,
        mfa_satisfied=mfa_satisfied(claims),
        message="",
    )


def _email_verified(claims: dict[str, Any]) -> bool:
    flag = claims.get("xms_edov")
    if isinstance(flag, bool):
        return flag
    return isinstance(flag, str) and flag.strip().lower() in ("true", "1")


def _auto_link_address(claims: dict[str, Any]) -> str | None:
    """The address used to match a MyPortal user: a verified email, otherwise the UPN."""
    raw = claims.get("email") if _email_verified(claims) else claims.get("preferred_username")
    address = str(raw or "").strip().lower()
    local, _, domain = address.partition("@")
    if (
        len(address) > 255 or address.count("@") != 1 or not local or not domain
        or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in address)
    ):
        return None
    return address


async def _enabled_directory_user(company_id: int, object_id: str) -> dict[str, Any] | None:
    """Check the immutable subject in the company's effective Microsoft tenant."""
    try:
        token = await m365_service.acquire_access_token(company_id)
        user = await m365_service._graph_get(
            token,
            "https://graph.microsoft.com/v1.0/users/" + quote(object_id, safe="")
            + "?$select=id,accountEnabled,givenName,surname",
        )
    except m365_service.M365Error as exc:
        if exc.http_status == 404:
            return None
        raise
    if (
        not isinstance(user, dict) or user.get("accountEnabled") is not True
        or str(user.get("id") or "").lower() != object_id
    ):
        return None
    return user


async def _audit_link(
    user: dict[str, Any], identity: dict[str, Any], tenant_id: str, object_id: str,
    request: Request | None, *, provisioned: bool = False,
) -> None:
    await audit_service.record(
        action="auth.sso.link", request=request, user_id=int(user["id"]),
        entity_type="user_sso_identity", entity_id=identity.get("id"),
        metadata={
            "linked_via": "auto", "tenant_id": tenant_id,
            "object_id_hash": hashlib.sha256(object_id.encode()).hexdigest()[:16],
            "account_provisioned": provisioned,
        },
    )


async def _resolve_auto_link(
    claims: dict[str, Any],
    companies: list[int],
    tenant_id: str,
    object_id: str,
    request: Request | None,
) -> Outcome:
    company_ids = [int(c) for c in companies]
    address = _auto_link_address(claims)
    if not address:
        return _failure("no_link")
    domain = address.rpartition("@")[2]
    if domain not in await company_repo.list_email_domains_for_companies(company_ids):
        return _failure("no_link")
    company = await company_repo.get_company_by_email_domain(domain)
    if not company or int(company["id"]) not in company_ids:
        return _failure("no_link")
    candidates = await user_repo.list_active_users_by_email_in_companies(address, company_ids)
    if not candidates:
        # Never replace a disabled account or one in an unrelated company.
        if await user_repo.get_user_by_email(address):
            return _failure("no_link")
        directory_user = await _enabled_directory_user(int(company["id"]), object_id)
        if not directory_user:
            return _failure("microsoft_account_disabled")
        try:
            user, identity = await sso_provisioning.create_user_with_identity(
                email=address, password_hash=hash_password(secrets.token_urlsafe(48)),
                first_name=str(directory_user.get("givenName") or "")[:255] or None,
                last_name=str(directory_user.get("surname") or "")[:255] or None,
                company_id=int(company["id"]), tenant_id=tenant_id, object_id=object_id,
                upn=claims.get("preferred_username"), identity_email=claims.get("email"),
            )
        except (aiomysql.IntegrityError, sqlite3.IntegrityError):
            # A concurrent callback may already have provisioned this subject.
            identity = await sso_identity_repo.get_by_subject(PROVIDER, tenant_id, object_id)
            if identity:
                return await _resolve_linked(identity, claims, companies, tenant_id, request)
            return _failure("no_link")
        await _audit_link(user, identity, tenant_id, object_id, request, provisioned=True)
        return Outcome(user, identity, None, mfa_satisfied(claims), "")
    if len(candidates) != 1:
        return _failure("no_link")
    user = candidates[0]
    if await sso_identity_repo.get_for_user(int(user["id"]), PROVIDER):
        return _failure("no_link")
    if not await sso_policy.user_company_ids(user) <= set(company_ids):
        return _failure("no_link")
    if not await _enabled_directory_user(int(company["id"]), object_id):
        return _failure("microsoft_account_disabled")
    identity = await sso_identity_repo.create(
        user_id=int(user["id"]),
        provider=PROVIDER,
        tenant_id=tenant_id,
        object_id=object_id,
        upn=claims.get("preferred_username"),
        email=claims.get("email"),
        linked_via="auto",
        linked_by_user_id=None,
    )
    await _audit_link(user, identity, tenant_id, object_id, request)
    return Outcome(
        user=user,
        identity=identity,
        failure=None,
        mfa_satisfied=mfa_satisfied(claims),
        message="",
    )
