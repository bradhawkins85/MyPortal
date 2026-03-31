"""Tests for the admin /admin/modules/m365-mail/accounts/{id}/sync endpoint.

Covers:
- 403 errors from sync_account are surfaced as ?error= (not buried in ?success=)
- Successful sync redirects to ?success= with import count
- Partial success (some imported, some errored) redirects to ?error= with detail
- Skipped sync redirects to ?error=
"""

from __future__ import annotations

import app.main as main_module
import app.services.m365_mail as m365_mail_service
import pytest
from app.core.database import db
from app.main import app, scheduler_service
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def mock_startup(monkeypatch):
    async def fake_connect():
        return None

    async def fake_disconnect():
        return None

    async def fake_run_migrations():
        return None

    async def fake_start():
        return None

    async def fake_stop():
        return None

    monkeypatch.setattr(db, "connect", fake_connect)
    monkeypatch.setattr(db, "disconnect", fake_disconnect)
    monkeypatch.setattr(db, "run_migrations", fake_run_migrations)
    monkeypatch.setattr(scheduler_service, "start", fake_start)
    monkeypatch.setattr(scheduler_service, "stop", fake_stop)
    monkeypatch.setattr(main_module.settings, "enable_csrf", False)


def _super_admin_context():
    async def fake_require_super_admin_page(request):
        user = {"id": 1, "is_super_admin": True, "company_id": 42}
        return user, None

    return fake_require_super_admin_page


# ---------------------------------------------------------------------------
# 403 error is surfaced as ?error= (not ?success=)
# ---------------------------------------------------------------------------


def test_sync_403_error_redirects_to_error_param(monkeypatch):
    """When sync_account returns completed_with_errors due to a 403, the
    handler must redirect to ?error= so the user sees the actionable message."""

    async def fake_sync_account(account_id: int):
        return {
            "status": "completed_with_errors",
            "processed": 0,
            "errors": [
                {
                    "error": (
                        "Mail sync failed (403 Forbidden). The enterprise app "
                        "may be missing the Mail.ReadWrite permission. "
                        "Re-provision or re-authorise the enterprise app in "
                        "Microsoft 365 settings to grant the required "
                        "permissions."
                    )
                }
            ],
        }

    monkeypatch.setattr(main_module, "_require_super_admin_page", _super_admin_context())
    monkeypatch.setattr(m365_mail_service, "sync_account", fake_sync_account)

    with TestClient(app, follow_redirects=False) as client:
        response = client.post("/admin/modules/m365-mail/accounts/2/sync")

    assert response.status_code == 303
    location = response.headers["location"]
    assert "error=" in location
    assert "success=" not in location
    assert "Mail.ReadWrite" in location


# ---------------------------------------------------------------------------
# Successful sync redirects to ?success=
# ---------------------------------------------------------------------------


def test_sync_success_redirects_to_success_param(monkeypatch):
    """A fully successful sync should redirect to ?success= with import count."""

    async def fake_sync_account(account_id: int):
        return {"status": "succeeded", "processed": 3, "errors": []}

    monkeypatch.setattr(main_module, "_require_super_admin_page", _super_admin_context())
    monkeypatch.setattr(m365_mail_service, "sync_account", fake_sync_account)

    with TestClient(app, follow_redirects=False) as client:
        response = client.post("/admin/modules/m365-mail/accounts/2/sync")

    assert response.status_code == 303
    location = response.headers["location"]
    assert "success=" in location
    assert "error=" not in location
    assert "3" in location


# ---------------------------------------------------------------------------
# Partial success (some imported, some errored) redirects to ?error= with count
# ---------------------------------------------------------------------------


def test_sync_partial_success_redirects_to_error_with_count(monkeypatch):
    """When some messages imported but errors also occurred, the redirect should
    be ?error= and include how many messages were imported."""

    async def fake_sync_account(account_id: int):
        return {
            "status": "completed_with_errors",
            "processed": 5,
            "errors": [{"error": "Failed to process 1 message"}],
        }

    monkeypatch.setattr(main_module, "_require_super_admin_page", _super_admin_context())
    monkeypatch.setattr(m365_mail_service, "sync_account", fake_sync_account)

    with TestClient(app, follow_redirects=False) as client:
        response = client.post("/admin/modules/m365-mail/accounts/2/sync")

    assert response.status_code == 303
    location = response.headers["location"]
    assert "error=" in location
    assert "success=" not in location
    # Should mention imported count
    assert "5" in location


# ---------------------------------------------------------------------------
# Skipped sync redirects to ?error=
# ---------------------------------------------------------------------------


def test_sync_skipped_redirects_to_error(monkeypatch):
    """A skipped sync should redirect to ?error=."""

    async def fake_sync_account(account_id: int):
        return {"status": "skipped", "reason": "Module disabled"}

    monkeypatch.setattr(main_module, "_require_super_admin_page", _super_admin_context())
    monkeypatch.setattr(m365_mail_service, "sync_account", fake_sync_account)

    with TestClient(app, follow_redirects=False) as client:
        response = client.post("/admin/modules/m365-mail/accounts/2/sync")

    assert response.status_code == 303
    location = response.headers["location"]
    assert "error=" in location
