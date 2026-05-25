"""M365 mail OAuth helpers for the ``m365_mail`` feature pack."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from fastapi import Request, status
from fastapi.responses import RedirectResponse

__all__ = ["handle_m365_mail_auth_callback"]


def _main():
    from app import main as main_module

    return main_module


async def handle_m365_mail_auth_callback(
    request: Request,
    *,
    state_data: dict[str, Any],
    code: str,
    company_id: int,
) -> RedirectResponse:
    """Handle the M365 mail delegated-auth callback flow."""
    main_module = _main()
    account_id_raw = state_data.get("account_id")
    try:
        account_id = int(account_id_raw)
    except (TypeError, ValueError):
        account_id = 0
    code_verifier: str | None = state_data.get("code_verifier")
    redirect_uri = main_module._build_m365_redirect_uri(request)

    def _mail_auth_error(msg: str) -> RedirectResponse:
        return RedirectResponse(
            url=f"/admin/modules/m365-mail?error={quote(msg)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    if not account_id:
        return _mail_auth_error("Invalid account in OAuth state.")
    if not code_verifier:
        return _mail_auth_error("Missing PKCE code verifier.")

    token_endpoint = "https://login.microsoftonline.com/organizations/oauth2/v2.0/token"
    token_data = {
        "client_id": await main_module.m365_service.get_effective_pkce_client_id_for_company(
            company_id, redirect_uri=redirect_uri
        )
        if company_id
        else await main_module.m365_service.get_effective_pkce_client_id(redirect_uri=redirect_uri),
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
        "scope": main_module.m365_mail_service.DELEGATED_MAIL_SCOPE,
    }
    async with main_module.httpx.AsyncClient(timeout=30) as client:
        token_response = await client.post(token_endpoint, data=token_data)
    if token_response.status_code != 200:
        main_module.log_error(
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
        tenant_id = main_module.m365_service.extract_tenant_id_from_token(access_token)
    except Exception:
        id_token = token_payload.get("id_token", "")
        try:
            tenant_id = main_module.m365_service.extract_tenant_id_from_token(id_token)
        except Exception:
            return _mail_auth_error("Unable to determine tenant ID from the sign-in response.")

    await main_module.m365_mail_service.store_delegated_tokens(
        account_id,
        tenant_id=tenant_id,
        refresh_token=refresh_token,
        access_token=access_token,
        expires_at=expires_at,
    )

    account = await main_module.m365_mail_service.get_account(account_id)
    label = account.get("name") if account else f"#{account_id}"
    message = quote(f"Successfully signed in for mailbox {label}.")
    return RedirectResponse(
        url=f"/admin/modules/m365-mail?success={message}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
