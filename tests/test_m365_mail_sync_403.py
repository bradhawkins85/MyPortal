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
    """A 403 from Graph should surface a clear re-provision message."""
    _patch_common(monkeypatch)

    async def fake_acquire_token(company_id, **kwargs):
        return "fake-token"

    async def fake_graph_get(access_token: str, url: str):
        raise M365Error(
            "Microsoft Graph request failed (403)", http_status=403
        )

    monkeypatch.setattr(
        m365_mail.m365_service, "acquire_access_token", fake_acquire_token
    )
    monkeypatch.setattr(m365_mail, "_graph_get", fake_graph_get)

    result = await m365_mail.sync_account(1)

    assert result["status"] == "completed_with_errors"
    assert len(result["errors"]) == 1
    error_msg = result["errors"][0]["error"]
    assert "403 Forbidden" in error_msg
    assert "Mail.ReadWrite" in error_msg
    assert "Global Administrator" in error_msg


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


