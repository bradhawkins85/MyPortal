"""Tests for 403 error handling in M365 mail sync_account."""
from __future__ import annotations

from typing import Any

import pytest

from app.services import m365_mail
from app.services.m365 import M365Error


pytestmark = pytest.mark.anyio("asyncio")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_account(company_id: int | None = 5) -> dict[str, Any]:
    return {
        "id": 1,
        "active": True,
        "company_id": company_id,
        "user_principal_name": "user@example.com",
        "folder": "Inbox",
        "process_unread_only": True,
        "mark_as_read": True,
    }


def _patch_common(monkeypatch):
    """Set up common monkeypatches for sync_account tests."""
    monkeypatch.setattr(m365_mail.system_state, "is_restart_pending", lambda: False)

    async def fake_get_module(slug: str, *, redact: bool = True):
        return {"enabled": True}

    async def fake_get_account(account_id: int):
        return _fake_account()

    async def fake_update_account(account_id, **fields):
        return None

    async def fake_acquire_delegated_token(company_id):
        return None

    monkeypatch.setattr(m365_mail.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(m365_mail.mail_repo, "get_account", fake_get_account)
    monkeypatch.setattr(m365_mail.mail_repo, "update_account", fake_update_account)
    monkeypatch.setattr(
        m365_mail.m365_service, "acquire_delegated_token", fake_acquire_delegated_token
    )


# ---------------------------------------------------------------------------
# 403 error produces actionable message when remediation fails
# ---------------------------------------------------------------------------


async def test_sync_account_403_returns_actionable_error(monkeypatch):
    """A 403 from Graph should attempt remediation, then surface a clear re-provision message."""
    _patch_common(monkeypatch)

    async def fake_acquire_token(company_id, **kwargs):
        return "fake-token"

    async def fake_graph_get(access_token: str, url: str):
        raise M365Error(
            "Microsoft Graph request failed (403)", http_status=403
        )

    async def fake_try_grant(company_id, token):
        return False

    monkeypatch.setattr(
        m365_mail.m365_service, "acquire_access_token", fake_acquire_token
    )
    monkeypatch.setattr(m365_mail, "_graph_get", fake_graph_get)
    monkeypatch.setattr(
        m365_mail.m365_service, "try_grant_missing_permissions", fake_try_grant
    )

    result = await m365_mail.sync_account(1)

    assert result["status"] == "completed_with_errors"
    assert len(result["errors"]) == 1
    error_msg = result["errors"][0]["error"]
    assert "403 Forbidden" in error_msg
    assert "Mail.ReadWrite" in error_msg
    assert "Re-provision" in error_msg


async def test_sync_account_403_non_403_error_not_intercepted(monkeypatch):
    """Non-403 errors should propagate normally without the remediation message."""
    _patch_common(monkeypatch)

    async def fake_acquire_token(company_id, **kwargs):
        return "fake-token"

    async def fake_graph_get(access_token: str, url: str):
        raise M365Error(
            "Microsoft Graph request failed (500)", http_status=500
        )

    monkeypatch.setattr(
        m365_mail.m365_service, "acquire_access_token", fake_acquire_token
    )
    monkeypatch.setattr(m365_mail, "_graph_get", fake_graph_get)

    result = await m365_mail.sync_account(1)

    assert result["status"] == "completed_with_errors"
    assert len(result["errors"]) == 1
    error_msg = result["errors"][0]["error"]
    # Should be the raw error, not the 403-specific message
    assert "Mail.ReadWrite" not in error_msg
    assert "500" in error_msg


# ---------------------------------------------------------------------------
# 403 auto-remediation: attempt try_grant_missing_permissions then retry
# ---------------------------------------------------------------------------


async def test_sync_account_403_attempts_remediation_and_retries(monkeypatch):
    """On 403, sync should call try_grant_missing_permissions, re-acquire token, and retry."""
    _patch_common(monkeypatch)

    call_count = {"graph_get": 0, "acquire": 0, "try_grant": 0}

    async def fake_acquire_token(company_id, **kwargs):
        call_count["acquire"] += 1
        return f"token-{call_count['acquire']}"

    async def fake_graph_get(access_token: str, url: str):
        call_count["graph_get"] += 1
        if call_count["graph_get"] == 1:
            raise M365Error("Microsoft Graph request failed (403)", http_status=403)
        # Second call succeeds after remediation
        return {"value": [], "@odata.nextLink": None}

    async def fake_try_grant(company_id, token):
        call_count["try_grant"] += 1
        return True

    monkeypatch.setattr(
        m365_mail.m365_service, "acquire_access_token", fake_acquire_token
    )
    monkeypatch.setattr(m365_mail, "_graph_get", fake_graph_get)
    monkeypatch.setattr(
        m365_mail.m365_service, "try_grant_missing_permissions", fake_try_grant
    )

    result = await m365_mail.sync_account(1)

    assert result["status"] == "succeeded"
    # Should have tried graph_get twice (first 403, then success)
    assert call_count["graph_get"] == 2
    # Should have acquired token twice (initial + re-acquire after grant)
    assert call_count["acquire"] == 2
    # Should have called try_grant once
    assert call_count["try_grant"] == 1


async def test_sync_account_403_remediation_fails_returns_error(monkeypatch):
    """When try_grant returns False, sync should surface the actionable error."""
    _patch_common(monkeypatch)

    call_count = {"graph_get": 0, "acquire": 0, "try_grant": 0}

    async def fake_acquire_token(company_id, **kwargs):
        call_count["acquire"] += 1
        return f"token-{call_count['acquire']}"

    async def fake_graph_get(access_token: str, url: str):
        call_count["graph_get"] += 1
        raise M365Error("Microsoft Graph request failed (403)", http_status=403)

    async def fake_try_grant(company_id, token):
        call_count["try_grant"] += 1
        return False

    monkeypatch.setattr(
        m365_mail.m365_service, "acquire_access_token", fake_acquire_token
    )
    monkeypatch.setattr(m365_mail, "_graph_get", fake_graph_get)
    monkeypatch.setattr(
        m365_mail.m365_service, "try_grant_missing_permissions", fake_try_grant
    )

    result = await m365_mail.sync_account(1)

    assert result["status"] == "completed_with_errors"
    assert "403 Forbidden" in result["errors"][0]["error"]
    # Remediation attempted but failed; no retry
    assert call_count["graph_get"] == 1
    assert call_count["acquire"] == 1
    assert call_count["try_grant"] == 1


async def test_sync_account_403_retry_still_403_gives_error(monkeypatch):
    """When retry after successful grant still returns 403, surface the error."""
    _patch_common(monkeypatch)

    call_count = {"graph_get": 0, "acquire": 0, "try_grant": 0}

    async def fake_acquire_token(company_id, **kwargs):
        call_count["acquire"] += 1
        return f"token-{call_count['acquire']}"

    async def fake_graph_get(access_token: str, url: str):
        call_count["graph_get"] += 1
        raise M365Error("Microsoft Graph request failed (403)", http_status=403)

    async def fake_try_grant(company_id, token):
        call_count["try_grant"] += 1
        return True

    monkeypatch.setattr(
        m365_mail.m365_service, "acquire_access_token", fake_acquire_token
    )
    monkeypatch.setattr(m365_mail, "_graph_get", fake_graph_get)
    monkeypatch.setattr(
        m365_mail.m365_service, "try_grant_missing_permissions", fake_try_grant
    )

    result = await m365_mail.sync_account(1)

    assert result["status"] == "completed_with_errors"
    assert "403 Forbidden" in result["errors"][0]["error"]
    # Should have tried graph_get twice, acquired twice, and granted once
    assert call_count["graph_get"] == 2
    assert call_count["acquire"] == 2
    assert call_count["try_grant"] == 1


async def test_sync_account_403_grant_exception_falls_through(monkeypatch):
    """If try_grant raises an exception, sync still surfaces the actionable error."""
    _patch_common(monkeypatch)

    async def fake_acquire_token(company_id, **kwargs):
        return "fake-token"

    async def fake_graph_get(access_token: str, url: str):
        raise M365Error(
            "Microsoft Graph request failed (403)", http_status=403
        )

    async def fake_try_grant(company_id, token):
        raise RuntimeError("Grant exploded")

    monkeypatch.setattr(
        m365_mail.m365_service, "acquire_access_token", fake_acquire_token
    )
    monkeypatch.setattr(m365_mail, "_graph_get", fake_graph_get)
    monkeypatch.setattr(
        m365_mail.m365_service, "try_grant_missing_permissions", fake_try_grant
    )

    result = await m365_mail.sync_account(1)

    assert result["status"] == "completed_with_errors"
    error_msg = result["errors"][0]["error"]
    assert "403 Forbidden" in error_msg
    assert "Mail.ReadWrite" in error_msg


# ---------------------------------------------------------------------------
# 403 auto-remediation: prefer delegated token from stored refresh token
# ---------------------------------------------------------------------------


async def test_sync_account_403_prefers_delegated_token_for_remediation(monkeypatch):
    """On 403, remediation should use the delegated token (from the stored
    refresh token) rather than the client_credentials token, because the
    delegated token carries AppRoleAssignment.ReadWrite.All."""
    _patch_common(monkeypatch)

    grant_tokens: list[str] = []

    async def fake_acquire_token(company_id, **kwargs):
        return "client-creds-token"

    async def fake_acquire_delegated(company_id):
        return "delegated-token"

    async def fake_graph_get(access_token: str, url: str):
        raise M365Error("Microsoft Graph request failed (403)", http_status=403)

    async def fake_try_grant(company_id, token):
        grant_tokens.append(token)
        return False

    monkeypatch.setattr(
        m365_mail.m365_service, "acquire_access_token", fake_acquire_token
    )
    monkeypatch.setattr(
        m365_mail.m365_service, "acquire_delegated_token", fake_acquire_delegated
    )
    monkeypatch.setattr(m365_mail, "_graph_get", fake_graph_get)
    monkeypatch.setattr(
        m365_mail.m365_service, "try_grant_missing_permissions", fake_try_grant
    )

    result = await m365_mail.sync_account(1)

    assert result["status"] == "completed_with_errors"
    # The delegated token must have been passed to try_grant_missing_permissions
    assert grant_tokens == ["delegated-token"]


async def test_sync_account_403_falls_back_to_client_creds_when_no_delegated(
    monkeypatch,
):
    """When no delegated token is available, remediation falls back to the
    client_credentials token."""
    _patch_common(monkeypatch)

    grant_tokens: list[str] = []

    async def fake_acquire_token(company_id, **kwargs):
        return "client-creds-token"

    async def fake_graph_get(access_token: str, url: str):
        raise M365Error("Microsoft Graph request failed (403)", http_status=403)

    async def fake_try_grant(company_id, token):
        grant_tokens.append(token)
        return False

    monkeypatch.setattr(
        m365_mail.m365_service, "acquire_access_token", fake_acquire_token
    )
    # acquire_delegated_token returns None (from _patch_common)
    monkeypatch.setattr(m365_mail, "_graph_get", fake_graph_get)
    monkeypatch.setattr(
        m365_mail.m365_service, "try_grant_missing_permissions", fake_try_grant
    )

    result = await m365_mail.sync_account(1)

    assert result["status"] == "completed_with_errors"
    # Falls back to the client_credentials token when no delegated token
    assert grant_tokens == ["client-creds-token"]


# ---------------------------------------------------------------------------
# _graph_get raises M365Error with http_status
# ---------------------------------------------------------------------------


async def test_graph_get_raises_m365error_with_status():
    """_graph_get should raise M365Error with the HTTP status code."""
    import httpx

    # Create a mock response
    fake_response = httpx.Response(
        status_code=403,
        text='{"error":{"code":"ErrorAccessDenied"}}',
        request=httpx.Request("GET", "https://graph.microsoft.com/test"),
    )

    import unittest.mock as mock

    with mock.patch("app.services.m365_mail.httpx.AsyncClient") as mock_client:
        mock_instance = mock.AsyncMock()
        mock_client.return_value.__aenter__ = mock.AsyncMock(
            return_value=mock_instance
        )
        mock_client.return_value.__aexit__ = mock.AsyncMock(return_value=False)
        mock_instance.get.return_value = fake_response

        with pytest.raises(M365Error) as exc_info:
            await m365_mail._graph_get("fake-token", "https://graph.microsoft.com/test")

    assert exc_info.value.http_status == 403
    assert "403" in str(exc_info.value)


# ---------------------------------------------------------------------------
# UPN URL-encoding: @ must be percent-encoded in Graph API paths
# ---------------------------------------------------------------------------


async def test_sync_account_encodes_upn_in_graph_url(monkeypatch):
    """The UPN's '@' must be percent-encoded (%40) in Graph API path segments."""
    _patch_common(monkeypatch)

    captured_urls: list[str] = []

    async def fake_acquire_token(company_id, **kwargs):
        return "fake-token"

    async def fake_graph_get(access_token: str, url: str):
        captured_urls.append(url)
        # Return an empty page so sync completes
        return {"value": [], "@odata.nextLink": None}

    monkeypatch.setattr(
        m365_mail.m365_service, "acquire_access_token", fake_acquire_token
    )
    monkeypatch.setattr(m365_mail, "_graph_get", fake_graph_get)

    result = await m365_mail.sync_account(1)

    assert result["status"] == "succeeded"
    assert len(captured_urls) == 1
    # The '@' in the UPN must be percent-encoded
    assert "user%40example.com" in captured_urls[0]
    assert "user@example.com" not in captured_urls[0]
