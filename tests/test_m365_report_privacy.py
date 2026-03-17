"""Tests for the mailbox report privacy check.

Covers:
- check_report_privacy returns False when identifiers are normal UPNs
- check_report_privacy returns True when identifiers are obfuscated hex hashes
- check_report_privacy returns False for an empty report
- check_report_privacy raises M365Error on API failure
- POST /m365/checks/report-privacy route redirects with error when concealed
- POST /m365/checks/report-privacy route redirects with success when not concealed
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import app.main as main_module
from app.main import app, scheduler_service
from app.core.database import db
from app.services import m365 as m365_service
from app.services.m365 import M365Error


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def disable_startup_and_csrf(monkeypatch):
    """Disable DB startup, scheduler, and CSRF for route tests."""
    async def _noop():
        return None

    monkeypatch.setattr(db, "connect", _noop)
    monkeypatch.setattr(db, "disconnect", _noop)
    monkeypatch.setattr(db, "run_migrations", _noop)
    monkeypatch.setattr(scheduler_service, "start", _noop)
    monkeypatch.setattr(scheduler_service, "stop", _noop)
    monkeypatch.setattr(main_module.settings, "enable_csrf", False)


# ---------------------------------------------------------------------------
# check_report_privacy – service-layer unit tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_check_report_privacy_returns_false_for_normal_upns():
    """check_report_privacy returns False when UPNs look like real addresses."""
    report_items = [
        {"userPrincipalName": "alice@contoso.com", "isDeleted": False},
        {"userPrincipalName": "bob@contoso.com", "isDeleted": False},
        {"userPrincipalName": "charlie@contoso.com", "isDeleted": False},
    ]
    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_fetch_mailbox_usage_report", AsyncMock(return_value=report_items)),
    ):
        result = await m365_service.check_report_privacy(1)

    assert result is False


@pytest.mark.anyio("asyncio")
async def test_check_report_privacy_returns_true_when_identifiers_are_obfuscated():
    """check_report_privacy returns True when the majority of UPNs are hex hashes."""
    report_items = [
        {"userPrincipalName": "013944b66cad4ddfef8efc07e81d550f", "isDeleted": False},
        {"userPrincipalName": "01c9b02a1714e53f85ee6d08a7780167", "isDeleted": False},
        {"userPrincipalName": "02a1b3c4d5e6f7a8b9c0d1e2f3a4b5c6", "isDeleted": False},
    ]
    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_fetch_mailbox_usage_report", AsyncMock(return_value=report_items)),
    ):
        result = await m365_service.check_report_privacy(1)

    assert result is True


@pytest.mark.anyio("asyncio")
async def test_check_report_privacy_returns_false_for_empty_report():
    """check_report_privacy returns False (not concealed) when the report is empty."""
    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_fetch_mailbox_usage_report", AsyncMock(return_value=[])),
    ):
        result = await m365_service.check_report_privacy(1)

    assert result is False


@pytest.mark.anyio("asyncio")
async def test_check_report_privacy_raises_on_api_error():
    """check_report_privacy propagates M365Error from the Graph API call."""
    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(
            m365_service,
            "_fetch_mailbox_usage_report",
            AsyncMock(side_effect=M365Error("Graph failed", http_status=403)),
        ),
    ):
        with pytest.raises(M365Error):
            await m365_service.check_report_privacy(1)


@pytest.mark.anyio("asyncio")
async def test_check_report_privacy_skips_deleted_entries():
    """Deleted mailbox entries are excluded from the obfuscation count."""
    report_items = [
        # Obfuscated but deleted – should not count
        {"userPrincipalName": "013944b66cad4ddfef8efc07e81d550f", "isDeleted": True},
        # Real UPN, not deleted
        {"userPrincipalName": "alice@contoso.com", "isDeleted": False},
    ]
    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_fetch_mailbox_usage_report", AsyncMock(return_value=report_items)),
    ):
        result = await m365_service.check_report_privacy(1)

    assert result is False


# ---------------------------------------------------------------------------
# POST /m365/checks/report-privacy – route tests
# ---------------------------------------------------------------------------

_SUPER_ADMIN = {"id": 1, "username": "admin", "is_super_admin": True, "company_id": 1}
_MEMBERSHIP = {"company_id": 1, "role": "admin"}
_COMPANY = {"id": 1, "name": "Test Co"}


@pytest.fixture
async def async_client():
    from httpx import AsyncClient, ASGITransport
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.anyio("asyncio")
async def test_route_report_privacy_check_concealed_redirects_with_error(async_client):
    """Route redirects to /m365 with error message when identifiers are concealed."""
    with (
        patch("app.main._load_license_context", AsyncMock(
            return_value=(_SUPER_ADMIN, _MEMBERSHIP, _COMPANY, 1, None)
        )),
        patch.object(m365_service, "check_report_privacy", AsyncMock(return_value=True)),
    ):
        response = await async_client.post(
            "/m365/checks/report-privacy", follow_redirects=False
        )

    assert response.status_code == 303
    location = response.headers["location"]
    assert "/m365" in location
    assert "error" in location
    assert "concealing" in location.lower() or "concealed" in location.lower()


@pytest.mark.anyio("asyncio")
async def test_route_report_privacy_check_ok_redirects_with_success(async_client):
    """Route redirects to /m365 with success message when identifiers are not concealed."""
    with (
        patch("app.main._load_license_context", AsyncMock(
            return_value=(_SUPER_ADMIN, _MEMBERSHIP, _COMPANY, 1, None)
        )),
        patch.object(m365_service, "check_report_privacy", AsyncMock(return_value=False)),
    ):
        response = await async_client.post(
            "/m365/checks/report-privacy", follow_redirects=False
        )

    assert response.status_code == 303
    location = response.headers["location"]
    assert "/m365" in location
    assert "success" in location


@pytest.mark.anyio("asyncio")
async def test_route_report_privacy_check_api_error_redirects_with_error(async_client):
    """Route redirects to /m365 with error when the API call fails."""
    with (
        patch("app.main._load_license_context", AsyncMock(
            return_value=(_SUPER_ADMIN, _MEMBERSHIP, _COMPANY, 1, None)
        )),
        patch.object(
            m365_service,
            "check_report_privacy",
            AsyncMock(side_effect=M365Error("Graph API error")),
        ),
    ):
        response = await async_client.post(
            "/m365/checks/report-privacy", follow_redirects=False
        )

    assert response.status_code == 303
    location = response.headers["location"]
    assert "/m365" in location
    assert "error" in location
