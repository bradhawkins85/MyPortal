"""Test that /api/tickets/views endpoint is accessible and not matched by /{ticket_id}."""
from unittest.mock import AsyncMock
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.main import automations_service, change_log_service, modules_service, scheduler_service
from app.core.database import db
from app.security.session import SessionData, session_manager
from datetime import datetime, timedelta, timezone


@pytest.fixture(autouse=True)
def _mock_startup(monkeypatch):
    """Mock startup tasks."""
    async def fake_connect():
        return None

    async def fake_disconnect():
        return None

    async def fake_run_migrations():
        return None

    async def fake_sync_change_log_sources(*args, **kwargs):
        return None

    async def fake_ensure_default_modules(*args, **kwargs):
        return None

    async def fake_refresh_all_schedules(*args, **kwargs):
        return None

    async def fake_scheduler_start(*args, **kwargs):
        return None

    async def fake_scheduler_stop(*args, **kwargs):
        return None

    monkeypatch.setattr(db, "connect", fake_connect)
    monkeypatch.setattr(db, "disconnect", fake_disconnect)
    monkeypatch.setattr(db, "run_migrations", fake_run_migrations)
    monkeypatch.setattr(change_log_service, "sync_change_log_sources", fake_sync_change_log_sources)
    monkeypatch.setattr(modules_service, "ensure_default_modules", fake_ensure_default_modules)
    monkeypatch.setattr(automations_service, "refresh_all_schedules", fake_refresh_all_schedules)
    monkeypatch.setattr(scheduler_service, "start", fake_scheduler_start)
    monkeypatch.setattr(scheduler_service, "stop", fake_scheduler_stop)


def test_views_endpoint_not_matched_by_ticket_id_route(monkeypatch):
    """Test that /api/tickets/views is correctly matched and not treated as /{ticket_id}."""
    client = TestClient(app)
    
    # Mock session
    async def mock_load_session(request):
        return SessionData(
            id=1,
            user_id=42,
            session_token="test_token",
            csrf_token="test_csrf",
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=12),
            last_seen_at=datetime.now(timezone.utc),
            ip_address="127.0.0.1",
            user_agent="test",
            active_company_id=None,
        )
    
    monkeypatch.setattr(session_manager, "load_session", mock_load_session)
    
    # Mock the ticket_views_repo.list_views_for_user function
    from app.repositories import ticket_views as ticket_views_repo
    
    async def mock_list_views(user_id):
        # Should be called with user_id=42 from the session
        assert user_id == 42
        return []
    
    monkeypatch.setattr(ticket_views_repo, "list_views_for_user", mock_list_views)
    
    # Mock get_ticket to verify it's NOT called for /views
    from app.repositories import tickets as tickets_repo
    
    async def mock_get_ticket(ticket_id):
        # This should NEVER be called for the /views endpoint
        raise AssertionError(f"get_ticket should not be called for /views endpoint, but was called with ticket_id={ticket_id}")
    
    monkeypatch.setattr(tickets_repo, "get_ticket", mock_get_ticket)
    
    # Make the request
    response = client.get(
        "/api/tickets/views",
        cookies={"myportal_session": "test_token"}
    )
    
    # Should return 200 OK, not 422 Unprocessable Entity
    assert response.status_code == 200, f"Expected 200, got {response.status_code}. Response: {response.json()}"
    
    # Verify response structure
    data = response.json()
    assert "items" in data
    assert isinstance(data["items"], list)
    assert data["items"] == []


def test_ticket_id_route_still_works_for_numeric_ids(monkeypatch):
    """Verify that numeric ticket IDs still work after route reordering."""
    client = TestClient(app)
    
    # Mock session
    async def mock_load_session(request):
        return SessionData(
            id=1,
            user_id=42,
            session_token="test_token",
            csrf_token="test_csrf",
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=12),
            last_seen_at=datetime.now(timezone.utc),
            ip_address="127.0.0.1",
            user_agent="test",
            active_company_id=None,
        )
    
    monkeypatch.setattr(session_manager, "load_session", mock_load_session)
    
    # Mock user repository
    from app.repositories import users as user_repo
    
    async def mock_get_user_by_id(user_id):
        return {
            "id": user_id,
            "email": "test@example.com",
            "is_super_admin": False,
            "company_id": None,
        }
    
    monkeypatch.setattr(user_repo, "get_user_by_id", mock_get_user_by_id)
    
    # Mock tickets_repo.get_ticket
    from app.repositories import tickets as tickets_repo
    
    async def mock_get_ticket(ticket_id):
        if ticket_id == 123:
            return {
                "id": 123,
                "subject": "Test Ticket",
                "description": "Test description",
                "status": "open",
                "priority": "normal",
                "requester_id": 42,
                "company_id": None,
                "assigned_user_id": None,
                "category": None,
                "module_slug": None,
                "external_reference": None,
                "closed_at": None,
                "xero_invoice_number": None,
                "ai_summary": None,
                "ai_tags": None,
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
        return None
    
    async def mock_list_replies(ticket_id, include_internal=False):
        return []
    
    async def mock_list_watchers(ticket_id):
        return []
    
    async def mock_list_attachments(ticket_id):
        return []
    
    monkeypatch.setattr(tickets_repo, "get_ticket", mock_get_ticket)
    monkeypatch.setattr(tickets_repo, "list_replies", mock_list_replies)
    monkeypatch.setattr(tickets_repo, "list_watchers", mock_list_watchers)
    
    from app.repositories import ticket_attachments as attachments_repo
    monkeypatch.setattr(attachments_repo, "list_attachments", mock_list_attachments)
    
    # Request a ticket by numeric ID
    response = client.get(
        "/api/tickets/123",
        cookies={"myportal_session": "test_token"}
    )
    
    # Should return 200 OK
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    data = response.json()
    assert data["id"] == 123
    assert data["subject"] == "Test Ticket"
