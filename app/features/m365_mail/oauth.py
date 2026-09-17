"""M365 mail OAuth helpers for the ``m365_mail`` feature pack."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from types import TracebackType
from typing import Any, Protocol, TypeAlias

from fastapi import Request
from fastapi.responses import RedirectResponse

from app.security.flash import flash_redirect

__all__ = ["handle_m365_mail_auth_callback"]


class M365OAuthService(Protocol):
    async def get_effective_pkce_client_id_for_company(
        self,
        company_id: int,
        *,
        redirect_uri: str | None = None,
    ) -> str: ...

    async def get_effective_pkce_client_id(
        self,
        *,
        redirect_uri: str | None = None,
    ) -> str: ...

    def extract_tenant_id_from_token(self, token: str) -> str: ...


class M365MailOAuthService(Protocol):
    DELEGATED_MAIL_SCOPE: str

    async def store_delegated_tokens(
        self,
        account_id: int,
        *,
        tenant_id: str,
        refresh_token: str,
        access_token: str,
        expires_at: datetime | None,
    ) -> None: ...

    async def get_account(self, account_id: int) -> dict[str, Any] | None: ...


class AsyncPostClient(Protocol):
    async def __aenter__(self) -> "AsyncPostClient": ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None: ...

    async def post(self, url: str, *, data: dict[str, Any]) -> Any: ...


HttpClientFactory: TypeAlias = Callable[..., AsyncPostClient]
BuildRedirectUri: TypeAlias = Callable[[Request], str]
LogError: TypeAlias = Callable[..., None]


async def handle_m365_mail_auth_callback(
    request: Request,
    *,
    state_data: dict[str, Any],
    code: str,
    company_id: int,
    m365_service: M365OAuthService,
    m365_mail_service: M365MailOAuthService,
    http_client_class: HttpClientFactory,
    build_m365_redirect_uri: BuildRedirectUri,
    log_error: LogError,
) -> RedirectResponse:
    """Handle the M365 mail delegated-auth callback flow."""
    account_id_raw = state_data.get("account_id")
    try:
        account_id = int(account_id_raw)
    except (TypeError, ValueError):
        account_id = 0
    code_verifier: str | None = state_data.get("code_verifier")
    redirect_uri = build_m365_redirect_uri(request)

    def _mail_auth_error(msg: str) -> RedirectResponse:
        return flash_redirect("/admin/modules/m365-mail", msg, "error")

    if not account_id:
        return _mail_auth_error("Invalid account in OAuth state.")
    if not code_verifier:
        return _mail_auth_error("Missing PKCE code verifier.")

    token_endpoint = "https://login.microsoftonline.com/organizations/oauth2/v2.0/token"
    token_data = {
        "client_id": await m365_service.get_effective_pkce_client_id_for_company(
            company_id, redirect_uri=redirect_uri
        )
        if company_id
        else await m365_service.get_effective_pkce_client_id(redirect_uri=redirect_uri),
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
        "scope": m365_mail_service.DELEGATED_MAIL_SCOPE,
    }
    async with http_client_class(timeout=30) as client:
        token_response = await client.post(token_endpoint, data=token_data)
    if token_response.status_code != 200:
        log_error(
            "M365 mail account OAuth token exchange failed",
            account_id=account_id,
            status=token_response.status_code,
            body=token_response.text[:500] if token_response.text else "",
        )
        return _mail_auth_error("Sign-in failed. Please try again.")

    token_payload = token_response.json()
    access_token = token_payload.get("access_token", "")
    refresh_token = token_payload.get("refresh_token")
    expires_in = token_payload.get("expires_in")
    expires_at: datetime | None = None
    if isinstance(expires_in, (int, float)):
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=float(expires_in))

    if not access_token or not refresh_token:
        return _mail_auth_error(
            "Sign-in did not return the required tokens. "
            "Ensure offline_access permission is granted."
        )

    try:
        tenant_id = m365_service.extract_tenant_id_from_token(access_token)
    except Exception:
        id_token = token_payload.get("id_token", "")
        try:
            tenant_id = m365_service.extract_tenant_id_from_token(id_token)
        except Exception:
            return _mail_auth_error("Unable to determine tenant ID from the sign-in response.")

    await m365_mail_service.store_delegated_tokens(
        account_id,
        tenant_id=tenant_id,
        refresh_token=refresh_token,
        access_token=access_token,
        expires_at=expires_at,
    )

    account = await m365_mail_service.get_account(account_id)
    label = account.get("name") if account else f"#{account_id}"
    message = f"Successfully signed in for mailbox {label}."
    return flash_redirect("/admin/modules/m365-mail", message, "success")
