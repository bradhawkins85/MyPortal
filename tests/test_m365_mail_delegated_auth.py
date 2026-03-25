"""Tests for per-account delegated OAuth authentication in M365 mail."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.services import m365_mail


pytestmark = pytest.mark.anyio("asyncio")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_account(
    *,
    company_id: int | None = 5,
    refresh_token: str | None = None,
    access_token: str | None = None,
    token_expires_at: datetime | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    return {
        "id": 1,
        "active": True,
        "company_id": company_id,
        "user_principal_name": "shared@contoso.com",
        "folder": "Inbox",
        "process_unread_only": True,
        "mark_as_read": True,
        "refresh_token": refresh_token,
        "access_token": access_token,
        "token_expires_at": token_expires_at,
        "tenant_id": tenant_id,
    }


# ---------------------------------------------------------------------------
# account_auth_status / enrich_account_response
# ---------------------------------------------------------------------------


def test_auth_status_signed_in():
    account = _fake_account(refresh_token="enc:token")
    assert m365_mail.account_auth_status(account) == "signed_in"


def test_auth_status_company_credentials():
    account = _fake_account(company_id=5)
    assert m365_mail.account_auth_status(account) == "company_credentials"


def test_auth_status_not_configured():
    account = _fake_account(company_id=None)
    assert m365_mail.account_auth_status(account) == "not_configured"


def test_enrich_strips_tokens():
    account = _fake_account(refresh_token="secret-rt", access_token="secret-at")
    enriched = m365_mail.enrich_account_response(account)
    assert "refresh_token" not in enriched
    assert "access_token" not in enriched
    assert enriched["auth_status"] == "signed_in"


# ---------------------------------------------------------------------------
# _account_has_delegated_tokens
# ---------------------------------------------------------------------------


def test_has_delegated_tokens_true():
    account = _fake_account(refresh_token="enc:token")
    assert m365_mail._account_has_delegated_tokens(account) is True


def test_has_delegated_tokens_false_empty():
    account = _fake_account(refresh_token=None)
    assert m365_mail._account_has_delegated_tokens(account) is False


def test_has_delegated_tokens_false_blank():
    account = _fake_account(refresh_token="")
    assert m365_mail._account_has_delegated_tokens(account) is False


# ---------------------------------------------------------------------------
# sync_account with delegated tokens
# ---------------------------------------------------------------------------


def _patch_common(monkeypatch):
    """Set up common monkeypatches for sync_account tests."""
    monkeypatch.setattr(m365_mail.system_state, "is_restart_pending", lambda: False)

    async def fake_get_module(slug: str, *, redact: bool = True):
        return {"enabled": True}

    async def fake_update_account(account_id, **fields):
        return None

    monkeypatch.setattr(m365_mail.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(m365_mail.mail_repo, "update_account", fake_update_account)


async def test_sync_uses_delegated_token_when_available(monkeypatch):
    """sync_account should use per-account delegated tokens when stored."""
    account = _fake_account(
        company_id=None,  # No company => would fail in legacy flow
        refresh_token="enc:refresh",
        access_token="enc:access",
        tenant_id="tenant-123",
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    _patch_common(monkeypatch)
    monkeypatch.setattr(m365_mail.mail_repo, "get_account", lambda _: _coro(account))

    acquired_token = None

    async def fake_acquire_delegated(acct):
        nonlocal acquired_token
        acquired_token = "delegated-token"
        return acquired_token

    monkeypatch.setattr(m365_mail, "_acquire_delegated_access_token", fake_acquire_delegated)

    # Simulate successful graph response with empty messages
    async def fake_graph_get(token, url):
        assert token == "delegated-token"
        return {"value": [], "@odata.nextLink": None}

    monkeypatch.setattr(m365_mail, "_graph_get", fake_graph_get)

    result = await m365_mail.sync_account(1)
    assert result["status"] == "succeeded"
    assert acquired_token == "delegated-token"


async def test_sync_returns_error_when_delegated_token_fails(monkeypatch):
    """sync_account returns a sign-in error when delegated token refresh fails."""
    account = _fake_account(
        company_id=None,
        refresh_token="enc:refresh",
        tenant_id="tenant-123",
    )
    _patch_common(monkeypatch)
    monkeypatch.setattr(m365_mail.mail_repo, "get_account", lambda _: _coro(account))

    async def fail_acquire(acct):
        raise Exception("Token refresh failed")

    monkeypatch.setattr(m365_mail, "_acquire_delegated_access_token", fail_acquire)

    result = await m365_mail.sync_account(1)
    assert result["status"] == "error"
    assert "sign in again" in result["error"]


async def test_sync_falls_back_to_company_credentials(monkeypatch):
    """When no delegated tokens, sync falls back to company credentials."""
    account = _fake_account(company_id=5, refresh_token=None)
    _patch_common(monkeypatch)
    monkeypatch.setattr(m365_mail.mail_repo, "get_account", lambda _: _coro(account))

    used_company_token = False

    async def fake_acquire_token(company_id, **kwargs):
        nonlocal used_company_token
        assert company_id == 5
        used_company_token = True
        return "company-token"

    monkeypatch.setattr(m365_mail.m365_service, "acquire_access_token", fake_acquire_token)

    async def fake_graph_get(token, url):
        assert token == "company-token"
        return {"value": [], "@odata.nextLink": None}

    monkeypatch.setattr(m365_mail, "_graph_get", fake_graph_get)

    result = await m365_mail.sync_account(1)
    assert result["status"] == "succeeded"
    assert used_company_token is True


async def test_sync_delegated_403_shows_signin_message(monkeypatch):
    """A 403 with delegated auth should tell the user to sign in again."""
    from app.services.m365 import M365Error

    account = _fake_account(
        company_id=None,
        refresh_token="enc:refresh",
        tenant_id="tenant-123",
    )
    _patch_common(monkeypatch)
    monkeypatch.setattr(m365_mail.mail_repo, "get_account", lambda _: _coro(account))

    async def fake_acquire_delegated(acct):
        return "delegated-token"

    monkeypatch.setattr(m365_mail, "_acquire_delegated_access_token", fake_acquire_delegated)

    async def fake_graph_get(token, url):
        raise M365Error("Forbidden", http_status=403)

    monkeypatch.setattr(m365_mail, "_graph_get", fake_graph_get)

    result = await m365_mail.sync_account(1)
    assert result["status"] == "completed_with_errors"
    assert len(result["errors"]) == 1
    error_msg = result["errors"][0]["error"]
    assert "sign in" in error_msg.lower()
    # Should NOT mention re-provisioning (that's the CSP error)
    assert "Re-provision" not in error_msg


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _coro(val):
    return val
