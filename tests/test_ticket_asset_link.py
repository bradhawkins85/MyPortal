"""Tests for linking assets to tickets via the admin ticket details form.

Regression tests for: POST /admin/tickets/{id}/details returning 400 when
saving ticket changes that include a linked asset.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.api.dependencies import database as database_dependencies
from app.api.routes import auth as auth_routes
from app import main as main_module
from app.main import app, scheduler_service
from app.core.database import db
from app.security.session import SessionData, session_manager


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
    monkeypatch.setattr(
        main_module.change_log_service,
        "sync_change_log_sources",
        AsyncMock(return_value=None),
    )


@pytest.fixture(autouse=True)
def patch_pending_access(monkeypatch):
    mock = AsyncMock(return_value=None)
    monkeypatch.setattr(
        auth_routes.staff_access_service,
        "apply_pending_access_for_user",
        mock,
    )
    return mock


@pytest.fixture
def active_session() -> SessionData:
    now = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    return SessionData(
        id=7,
        user_id=5,
        session_token="session-token",
        csrf_token="csrf-token",
        created_at=now,
        expires_at=now + timedelta(hours=1),
        last_seen_at=now,
        ip_address=None,
        user_agent=None,
        active_company_id=None,
        pending_totp_secret=None,
    )


def _setup_common_mocks(monkeypatch, ticket: dict, company_id: int = 5) -> None:
    """Set up the mocks common to all ticket-details POST tests."""
    monkeypatch.setattr(
        main_module,
        "_require_helpdesk_page",
        AsyncMock(return_value=({"id": 10, "email": "tech@example.com", "is_super_admin": False}, None)),
    )
    monkeypatch.setattr(main_module.tickets_repo, "get_ticket", AsyncMock(return_value=ticket))
    monkeypatch.setattr(
        main_module.tickets_service,
        "validate_status_choice",
        AsyncMock(return_value="open"),
    )
    monkeypatch.setattr(
        main_module.company_repo,
        "get_company_by_id",
        AsyncMock(return_value={"id": company_id, "name": "Test Company"}),
    )
    monkeypatch.setattr(main_module.tickets_repo, "update_ticket", AsyncMock(return_value=ticket))
    monkeypatch.setattr(main_module.tickets_repo, "set_ticket_status", AsyncMock(return_value=None))
    monkeypatch.setattr(
        main_module.tickets_service, "refresh_ticket_ai_summary", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        main_module.tickets_service, "refresh_ticket_ai_tags", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        main_module.tickets_service, "broadcast_ticket_event", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        main_module.tickets_service,
        "emit_ticket_details_updated_event",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        main_module.tickets_repo, "replace_ticket_assets", AsyncMock(return_value=[])
    )


def test_link_asset_to_ticket_succeeds(monkeypatch, active_session):
    """Linking a valid asset (matching the ticket's company) should save and redirect."""
    ticket = {
        "id": 135,
        "status": "open",
        "priority": "normal",
        "company_id": 5,
        "requester_id": None,
        "assigned_user_id": None,
    }
    _setup_common_mocks(monkeypatch, ticket, company_id=5)

    # Asset 20 belongs to company 5
    monkeypatch.setattr(
        main_module.assets_repo,
        "get_asset_by_id",
        AsyncMock(return_value={"id": 20, "company_id": 5, "name": "Laptop"}),
    )
    monkeypatch.setattr(session_manager, "load_session", AsyncMock(return_value=active_session))

    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    try:
        with TestClient(app) as client:
            client.cookies.set("myportal_session_csrf", active_session.csrf_token)
            response = client.post(
                "/admin/tickets/135/details",
                data={
                    "status": "open",
                    "priority": "normal",
                    "companyId": "5",
                    "assetIds": "20",
                    "returnUrl": "/admin/tickets/135",
                },
                headers={"X-CSRF-Token": "csrf-token"},
                follow_redirects=False,
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert "/admin/tickets/135" in response.headers["location"]
    replace_mock = main_module.tickets_repo.replace_ticket_assets
    replace_mock.assert_awaited_once_with(135, [20])


def test_link_asset_wrong_company_is_skipped_not_400(monkeypatch, active_session):
    """An asset that belongs to a different company should be silently skipped.

    Previously this would return a 400 error; after the fix the valid assets are
    saved and the stale asset is ignored so the form save succeeds.
    """
    ticket = {
        "id": 135,
        "status": "open",
        "priority": "normal",
        "company_id": 5,
        "requester_id": None,
        "assigned_user_id": None,
    }
    _setup_common_mocks(monkeypatch, ticket, company_id=5)

    # Asset 99 belongs to a different company (3)
    monkeypatch.setattr(
        main_module.assets_repo,
        "get_asset_by_id",
        AsyncMock(return_value={"id": 99, "company_id": 3, "name": "Old Laptop"}),
    )
    monkeypatch.setattr(session_manager, "load_session", AsyncMock(return_value=active_session))

    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    try:
        with TestClient(app) as client:
            client.cookies.set("myportal_session_csrf", active_session.csrf_token)
            response = client.post(
                "/admin/tickets/135/details",
                data={
                    "status": "open",
                    "priority": "normal",
                    "companyId": "5",
                    "assetIds": "99",
                    "returnUrl": "/admin/tickets/135",
                },
                headers={"X-CSRF-Token": "csrf-token"},
                follow_redirects=False,
            )
    finally:
        app.dependency_overrides.clear()

    # Should succeed (not 400) — the mismatched asset is silently skipped
    assert response.status_code == status.HTTP_303_SEE_OTHER
    # The stale asset should not be saved
    replace_mock = main_module.tickets_repo.replace_ticket_assets
    replace_mock.assert_awaited_once_with(135, [])


def test_link_asset_mixed_valid_and_stale(monkeypatch, active_session):
    """Only valid assets (matching company) are saved; stale assets are silently removed."""
    ticket = {
        "id": 135,
        "status": "open",
        "priority": "normal",
        "company_id": 5,
        "requester_id": None,
        "assigned_user_id": None,
    }
    _setup_common_mocks(monkeypatch, ticket, company_id=5)

    asset_lookup = {
        20: {"id": 20, "company_id": 5, "name": "Valid Laptop"},   # correct company
        99: {"id": 99, "company_id": 3, "name": "Stale Desktop"},  # wrong company
    }

    async def fake_get_asset_by_id(asset_id: int):
        return asset_lookup.get(asset_id)

    monkeypatch.setattr(main_module.assets_repo, "get_asset_by_id", fake_get_asset_by_id)
    monkeypatch.setattr(session_manager, "load_session", AsyncMock(return_value=active_session))

    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    try:
        with TestClient(app) as client:
            client.cookies.set("myportal_session_csrf", active_session.csrf_token)
            response = client.post(
                "/admin/tickets/135/details",
                data={
                    "status": "open",
                    "priority": "normal",
                    "companyId": "5",
                    # Both assets submitted; only asset 20 (company 5) should be saved
                    "assetIds": ["20", "99"],
                    "returnUrl": "/admin/tickets/135",
                },
                headers={"X-CSRF-Token": "csrf-token"},
                follow_redirects=False,
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == status.HTTP_303_SEE_OTHER
    replace_mock = main_module.tickets_repo.replace_ticket_assets
    replace_mock.assert_awaited_once_with(135, [20])


def test_link_asset_without_company_returns_400(monkeypatch, active_session):
    """Submitting asset IDs without a company linked should still return 400."""
    ticket = {
        "id": 135,
        "status": "open",
        "priority": "normal",
        "company_id": None,
        "requester_id": None,
        "assigned_user_id": None,
    }
    # Override: no company provided in form (empty companyId)
    monkeypatch.setattr(
        main_module,
        "_require_helpdesk_page",
        AsyncMock(return_value=({"id": 10, "email": "tech@example.com", "is_super_admin": False}, None)),
    )
    monkeypatch.setattr(main_module.tickets_repo, "get_ticket", AsyncMock(return_value=ticket))
    monkeypatch.setattr(
        main_module.tickets_service,
        "validate_status_choice",
        AsyncMock(return_value="open"),
    )
    monkeypatch.setattr(
        main_module.tickets_repo, "replace_ticket_assets", AsyncMock(return_value=[])
    )

    # Mock _render_ticket_detail to avoid full DB dependency for error-path rendering
    monkeypatch.setattr(
        main_module,
        "_render_ticket_detail",
        AsyncMock(
            return_value=__import__("fastapi").responses.HTMLResponse(
                "error", status_code=status.HTTP_400_BAD_REQUEST
            )
        ),
    )
    monkeypatch.setattr(session_manager, "load_session", AsyncMock(return_value=active_session))

    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    try:
        with TestClient(app) as client:
            client.cookies.set("myportal_session_csrf", active_session.csrf_token)
            response = client.post(
                "/admin/tickets/135/details",
                data={
                    "status": "open",
                    "priority": "normal",
                    "companyId": "",  # No company
                    "assetIds": "20",  # Asset submitted without company
                    "returnUrl": "/admin/tickets/135",
                },
                headers={"X-CSRF-Token": "csrf-token"},
                follow_redirects=False,
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    # Assets should not be saved
    main_module.tickets_repo.replace_ticket_assets.assert_not_awaited()
