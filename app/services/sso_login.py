"""Microsoft 365 single sign-on: start, code redemption and user resolution."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlencode

import httpx
from fastapi import Request

from app.repositories import user_sso_identities as sso_identity_repo
from app.repositories import users as user_repo
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
    "No MyPortal account is linked to this Microsoft account. Contact your administrator."
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
    client_id = await m365_service.get_effective_pkce_client_id(redirect_uri=redirect_uri)
    transaction_id = await m365_oauth_transactions.create(
        flow="sso_login",
        code_verifier=code_verifier,
        nonce=nonce,
        client_id=client_id,
        redirect_uri=redirect_uri,
        binding_hash=_hash_binding(binding),
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
        raise m365_service.M365Error("Microsoft rejected the sign-in code")
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
    if not expected_nonce or not hmac.compare_digest(expected_nonce, received_nonce):
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
    if not (await sso_policy.user_company_ids(user) & {int(c) for c in companies}):
        await _audit_failure("company_mismatch", user, tenant_id, request)
        return _failure("company_mismatch")
    return Outcome(
        user=user,
        identity=identity,
        failure=None,
        mfa_satisfied=mfa_satisfied(claims),
        message="",
    )


async def _resolve_auto_link(
    claims: dict[str, Any],
    companies: list[int],
    tenant_id: str,
    object_id: str,
    request: Request | None,
) -> Outcome:
    address = str(claims.get("email") or claims.get("preferred_username") or "").strip().lower()
    if not address:
        return _failure("no_link")
    candidates = await user_repo.list_active_users_by_email_in_companies(
        address, [int(c) for c in companies]
    )
    if len(candidates) != 1:
        return _failure("no_link")
    user = candidates[0]
    if await sso_identity_repo.get_for_user(int(user["id"]), PROVIDER):
        return _failure("no_link")
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
    await audit_service.record(
        action="auth.sso.link",
        request=request,
        user_id=int(user["id"]),
        entity_type="user_sso_identity",
        entity_id=identity.get("id"),
        metadata={
            "linked_via": "auto",
            "tenant_id": tenant_id,
            "object_id_hash": hashlib.sha256(object_id.encode()).hexdigest()[:16],
        },
    )
    return Outcome(
        user=user,
        identity=identity,
        failure=None,
        mfa_satisfied=mfa_satisfied(claims),
        message="",
    )
