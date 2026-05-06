"""Tests for the M365 diagnostics repair endpoint and service function.

Covers:
* repair_enterprise_app_permissions() service function – grants missing
  permissions using the stored delegated token and re-checks results.
* POST /m365/diagnostics/repair route – success, no-token fallback to
  connect flow, and error cases.
* Connect callback with return_to=diagnostics – redirects to diagnostics
  after granting permissions.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.main as main_module
from app.main import app
from app.security.session import SessionData, session_manager
from app.services import m365 as m365_service
from app.services.m365 import M365Error, M365NoDelegatedTokenError
from app.main import scheduler_service  # type: ignore[attr-defined]


def _signed_state(payload: dict) -> str:
    from app.main import oauth_state_serializer  # type: ignore[attr-defined]
    return oauth_state_serializer.dumps(payload)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def mock_startup(monkeypatch):
    """Stub out DB/scheduler startup so tests don't need a running database."""
    from app.core.database import db

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

    async def fake_noop(*_, **__):
        return None

    monkeypatch.setattr(db, "connect", fake_connect)
    monkeypatch.setattr(db, "disconnect", fake_disconnect)
    monkeypatch.setattr(db, "run_migrations", fake_run_migrations)
    monkeypatch.setattr(scheduler_service, "start", fake_start)
    monkeypatch.setattr(scheduler_service, "stop", fake_stop)
    monkeypatch.setattr(main_module.change_log_service, "sync_change_log_sources", fake_noop)
    monkeypatch.setattr(main_module.modules_service, "ensure_default_modules", fake_noop)
    monkeypatch.setattr(main_module.automations_service, "refresh_all_schedules", fake_noop)

    now = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    _session = SessionData(
        id=1,
        user_id=1,
        session_token="session-token",
        csrf_token="test-csrf-token",
        created_at=now,
        expires_at=now + timedelta(hours=1),
        last_seen_at=now,
        ip_address="127.0.0.1",
        user_agent="pytest",
        active_company_id=None,
    )

    async def fake_load_session(request, *, allow_inactive: bool = False):
        return _session

    monkeypatch.setattr(session_manager, "load_session", fake_load_session)
    monkeypatch.setattr(main_module.session_manager, "load_session", fake_load_session)


@pytest.fixture
async def async_client():
    from httpx import AsyncClient, ASGITransport
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


# ---------------------------------------------------------------------------
# Service layer tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_repair_enterprise_app_permissions_success():
    """repair_enterprise_app_permissions grants missing permissions and re-checks."""
    fake_creds = {"client_id": "app-id", "tenant_id": "t-123", "refresh_token": "rt"}
    fake_results = [{"name": "Microsoft Graph", "app_id": "g-id", "permissions": [], "all_ok": True}]

    with (
        patch.object(m365_service, "get_credentials", AsyncMock(return_value=fake_creds)),
        patch.object(m365_service, "acquire_delegated_token", AsyncMock(return_value="delegated-token")),
        patch.object(m365_service, "try_grant_missing_permissions", AsyncMock(return_value=True)),
        patch.object(m365_service, "check_enterprise_app_permissions", AsyncMock(return_value=fake_results)),
    ):
        result = await m365_service.repair_enterprise_app_permissions(company_id=1)

    assert result["granted"] is True
    assert result["results"] == fake_results


@pytest.mark.anyio("asyncio")
async def test_repair_enterprise_app_permissions_no_grant_needed():
    """repair_enterprise_app_permissions returns granted=False when nothing to grant."""
    fake_creds = {"client_id": "app-id", "tenant_id": "t-123", "refresh_token": "rt"}
    fake_results: list[dict] = []

    with (
        patch.object(m365_service, "get_credentials", AsyncMock(return_value=fake_creds)),
        patch.object(m365_service, "acquire_delegated_token", AsyncMock(return_value="delegated-token")),
        patch.object(m365_service, "try_grant_missing_permissions", AsyncMock(return_value=False)),
        patch.object(m365_service, "check_enterprise_app_permissions", AsyncMock(return_value=fake_results)),
    ):
        result = await m365_service.repair_enterprise_app_permissions(company_id=1)

    assert result["granted"] is False


@pytest.mark.anyio("asyncio")
async def test_repair_enterprise_app_permissions_no_credentials():
    """repair_enterprise_app_permissions raises M365Error when no credentials."""
    with patch.object(m365_service, "get_credentials", AsyncMock(return_value=None)):
        with pytest.raises(M365Error, match="No M365 credentials"):
            await m365_service.repair_enterprise_app_permissions(company_id=1)

@pytest.mark.anyio("asyncio")
async def test_repair_enterprise_app_permissions_no_delegated_token():
    """repair_enterprise_app_permissions raises M365NoDelegatedTokenError when no delegated token."""
    fake_creds = {"client_id": "app-id", "tenant_id": "t-123"}

    with (
        patch.object(m365_service, "get_credentials", AsyncMock(return_value=fake_creds)),
        patch.object(m365_service, "acquire_delegated_token", AsyncMock(return_value=None)),
    ):
        with pytest.raises(M365NoDelegatedTokenError):
            await m365_service.repair_enterprise_app_permissions(company_id=1)


# ---------------------------------------------------------------------------
# Route tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_repair_route_success(async_client):
    """POST /m365/diagnostics/repair redirects with success message on grant."""
    async def fake_load_license_context(request):
        user = {"id": 1, "is_super_admin": True}
        company = {"id": 1, "name": "Test Co"}
        return user, None, company, 1, None

    async def fake_repair(company_id):
        return {"granted": True, "results": []}

    with (
        patch("app.main._load_license_context", side_effect=fake_load_license_context),
        patch("app.main.m365_service.repair_enterprise_app_permissions", side_effect=fake_repair),
    ):
        response = await async_client.post(
            "/m365/diagnostics/repair",
            headers={"X-CSRF-Token": "test-csrf-token"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert "/m365/diagnostics" in response.headers["location"]
    assert "success" in response.headers["location"]


@pytest.mark.anyio("asyncio")
async def test_repair_route_no_new_permissions(async_client):
    """POST /m365/diagnostics/repair redirects with 'already granted' message."""
    async def fake_load_license_context(request):
        user = {"id": 1, "is_super_admin": True}
        company = {"id": 1, "name": "Test Co"}
        return user, None, company, 1, None

    async def fake_repair(company_id):
        return {"granted": False, "results": []}

    with (
        patch("app.main._load_license_context", side_effect=fake_load_license_context),
        patch("app.main.m365_service.repair_enterprise_app_permissions", side_effect=fake_repair),
    ):
        response = await async_client.post(
            "/m365/diagnostics/repair",
            headers={"X-CSRF-Token": "test-csrf-token"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    location = response.headers["location"]
    assert "/m365/diagnostics" in location
    assert "success" in location


@pytest.mark.anyio("asyncio")
async def test_repair_route_no_token_redirects_to_connect(async_client):
    """POST /m365/diagnostics/repair redirects to MS OAuth when no delegated token."""
    async def fake_load_license_context(request):
        user = {"id": 1, "is_super_admin": True}
        company = {"id": 1, "name": "Test Co"}
        return user, None, company, 1, None

    async def fake_repair(company_id):
        raise M365NoDelegatedTokenError(
            "No delegated admin token is available. "
            "Please use 'Authorize portal access' on the Office 365 page first."
        )

    fake_creds = {
        "client_id": "app-client-id",
        "tenant_id": "tenant-123",
        "client_secret": "s",
    }

    with (
        patch("app.main._load_license_context", side_effect=fake_load_license_context),
        patch("app.main.m365_service.repair_enterprise_app_permissions", side_effect=fake_repair),
        patch("app.main.m365_service.get_credentials", AsyncMock(return_value=fake_creds)),
    ):
        response = await async_client.post(
            "/m365/diagnostics/repair",
            headers={"X-CSRF-Token": "test-csrf-token"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    location = response.headers["location"]
    # Should redirect to Microsoft OAuth, not diagnostics
    assert "login.microsoftonline.com" in location


@pytest.mark.anyio("asyncio")
async def test_repair_route_non_admin_redirected(async_client):
    """POST /m365/diagnostics/repair redirects non-super-admins away."""
    async def fake_load_license_context(request):
        user = {"id": 2, "is_super_admin": False}
        company = {"id": 1, "name": "Test Co"}
        return user, None, company, 1, None

    with patch("app.main._load_license_context", side_effect=fake_load_license_context):
        response = await async_client.post(
            "/m365/diagnostics/repair",
            headers={"X-CSRF-Token": "test-csrf-token"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/m365"


# ---------------------------------------------------------------------------
# Callback return_to=diagnostics test
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_connect_callback_return_to_diagnostics(async_client):
    """Connect callback with return_to=diagnostics redirects to diagnostics page."""
    state = _signed_state(
        {
            "company_id": 1,
            "user_id": 1,
            "flow": "connect",
            "return_to": "diagnostics",
        }
    )

    fake_creds = {
        "tenant_id": "tenant-123",
        "client_id": "app-client-id",
        "client_secret": "secret",
    }

    async def fake_token_post(url, *, data=None, **kwargs):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "access_token": "at",
            "refresh_token": "rt",
            "expires_in": 3600,
        }
        return mock_resp

    with (
        patch("app.main.m365_repo.update_tokens", AsyncMock(return_value={})),
        patch("app.main.m365_service.get_credentials", AsyncMock(return_value=fake_creds)),
        patch("app.main.m365_service.try_grant_missing_permissions", AsyncMock(return_value=True)),
        patch("app.main.m365_service.check_enterprise_app_permissions", AsyncMock(return_value=[])),
        patch("app.main.httpx.AsyncClient") as mock_http,
    ):
        mock_http.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock(post=AsyncMock(side_effect=fake_token_post))
        )
        mock_http.return_value.__aexit__ = AsyncMock(return_value=False)

        response = await async_client.get(
            f"/m365/callback?code=auth-code&state={state}",
            follow_redirects=False,
        )

    assert response.status_code == 303
    location = response.headers["location"]
    assert "/m365/diagnostics" in location
    assert "success" in location

