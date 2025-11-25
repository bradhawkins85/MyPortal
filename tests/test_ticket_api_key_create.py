"""Test that POST /api/tickets/ works with API key authentication."""
import pytest
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient

from app.main import app
from app.main import automations_service, change_log_service, modules_service, scheduler_service
from app.core.database import db
from app.security.session import SessionData, session_manager


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


def test_create_ticket_with_api_key_authentication(monkeypatch):
    """Test that POST /api/tickets/ works with API key authentication."""
    now = datetime.now(timezone.utc)
    
    from app.api.dependencies.api_keys import get_optional_api_key
    from app.api.dependencies.auth import get_optional_user
    from app.api.dependencies import database as database_deps
    
    # Create a mock API key record
    mock_api_key_record = {
        "id": 1,
        "name": "Test API Key",
        "key_value": "test-api-key",
        "is_active": True,
        "permissions": [],
        "ip_restrictions": [],
    }
    
    # Mock tickets service and repo
    from app.services import tickets as tickets_service
    from app.repositories import tickets as tickets_repo
    
    async def mock_create_ticket(**kwargs):
        return {
            "id": 999,
            "subject": kwargs.get("subject"),
            "description": kwargs.get("description"),
            "status": kwargs.get("status", "open"),
            "priority": kwargs.get("priority", "normal"),
            "requester_id": kwargs.get("requester_id"),
            "company_id": kwargs.get("company_id"),
            "assigned_user_id": kwargs.get("assigned_user_id"),
            "category": kwargs.get("category"),
            "module_slug": kwargs.get("module_slug"),
            "external_reference": kwargs.get("external_reference"),
            "closed_at": None,
            "xero_invoice_number": None,
            "ai_summary": None,
            "ai_tags": None,
            "created_at": now,
            "updated_at": now,
        }
    
    async def mock_get_ticket(ticket_id):
        return {
            "id": ticket_id,
            "subject": "Test Ticket via API Key",
            "description": "Test description",
            "status": "open",
            "priority": "normal",
            "requester_id": 123,
            "company_id": None,
            "assigned_user_id": None,
            "category": None,
            "module_slug": None,
            "external_reference": None,
            "closed_at": None,
            "xero_invoice_number": None,
            "ai_summary": None,
            "ai_tags": None,
            "created_at": now,
            "updated_at": now,
        }
    
    async def mock_list_replies(ticket_id, include_internal=False):
        return []
    
    async def mock_list_watchers(ticket_id):
        return []
    
    async def mock_add_watcher(ticket_id, user_id):
        return None
    
    async def mock_resolve_status(status):
        return status or "open"
    
    async def mock_validate_status(status):
        return status
    
    async def mock_refresh_ai(*args, **kwargs):
        pass
    
    monkeypatch.setattr(tickets_service, "create_ticket", mock_create_ticket)
    monkeypatch.setattr(tickets_service, "resolve_status_or_default", mock_resolve_status)
    monkeypatch.setattr(tickets_service, "validate_status_choice", mock_validate_status)
    monkeypatch.setattr(tickets_service, "refresh_ticket_ai_summary", mock_refresh_ai)
    monkeypatch.setattr(tickets_service, "refresh_ticket_ai_tags", mock_refresh_ai)
    monkeypatch.setattr(tickets_repo, "get_ticket", mock_get_ticket)
    monkeypatch.setattr(tickets_repo, "list_replies", mock_list_replies)
    monkeypatch.setattr(tickets_repo, "list_watchers", mock_list_watchers)
    monkeypatch.setattr(tickets_repo, "add_watcher", mock_add_watcher)
    
    from app.repositories import ticket_attachments as attachments_repo
    async def mock_list_attachments(ticket_id):
        return []
    monkeypatch.setattr(attachments_repo, "list_attachments", mock_list_attachments)
    
    # Override the dependencies using the actual imported functions
    app.dependency_overrides[database_deps.require_database] = lambda: None
    app.dependency_overrides[get_optional_api_key] = lambda: mock_api_key_record
    app.dependency_overrides[get_optional_user] = lambda: None
    
    try:
        client = TestClient(app)
        # Make POST request with API key
        response = client.post(
            "/api/tickets/",
            json={
                "subject": "Test Ticket via API Key",
                "description": "Test description",
                "requester_id": 123,  # Required when using API key
            },
            headers={"x-api-key": "test-api-key"}
        )
    finally:
        app.dependency_overrides.clear()
    
    assert response.status_code == 201, f"Expected 201 Created, got {response.status_code}: {response.json()}"
    
    data = response.json()
    assert "id" in data, "Response should contain ticket id"
    assert data["id"] == 999
    assert "items" not in data, "Response should NOT be a list"


def test_create_ticket_with_api_key_requires_requester_id(monkeypatch):
    """Test that POST /api/tickets/ with API key requires requester_id."""
    
    from app.api.dependencies.api_keys import get_optional_api_key
    from app.api.dependencies.auth import get_optional_user
    from app.api.dependencies import database as database_deps
    
    # Create a mock API key record
    mock_api_key_record = {
        "id": 1,
        "name": "Test API Key",
        "key_value": "test-api-key",
        "is_active": True,
        "permissions": [],
        "ip_restrictions": [],
    }
    
    # Override the dependencies using the actual imported functions
    app.dependency_overrides[database_deps.require_database] = lambda: None
    app.dependency_overrides[get_optional_api_key] = lambda: mock_api_key_record
    app.dependency_overrides[get_optional_user] = lambda: None
    
    try:
        client = TestClient(app)
        # Make POST request with API key but WITHOUT requester_id
        response = client.post(
            "/api/tickets/",
            json={
                "subject": "Test Ticket",
                "description": "Test description",
                # No requester_id provided
            },
            headers={"x-api-key": "test-api-key"}
        )
    finally:
        app.dependency_overrides.clear()
    
    assert response.status_code == 400, f"Expected 400 Bad Request, got {response.status_code}: {response.json()}"
    
    data = response.json()
    assert "requester_id is required" in data.get("detail", ""), f"Expected error about requester_id, got: {data}"


def test_create_ticket_with_session_still_works(monkeypatch):
    """Test that POST /api/tickets/ with session authentication still works."""
    client = TestClient(app)
    now = datetime.now(timezone.utc)
    
    # Mock session
    async def mock_load_session(request, *, allow_inactive=False):
        return SessionData(
            id=1,
            user_id=42,
            session_token="test_token",
            csrf_token="test_csrf",
            created_at=now,
            expires_at=now + timedelta(hours=12),
            last_seen_at=now,
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
    
    # Mock tickets service and repo
    from app.services import tickets as tickets_service
    from app.repositories import tickets as tickets_repo
    from app.api.routes import tickets as tickets_routes
    
    async def mock_has_helpdesk_permission(user):
        return False
    
    monkeypatch.setattr(tickets_routes, "_has_helpdesk_permission", mock_has_helpdesk_permission)
    
    async def mock_create_ticket(**kwargs):
        return {
            "id": 888,
            "subject": kwargs.get("subject"),
            "description": kwargs.get("description"),
            "status": kwargs.get("status", "open"),
            "priority": kwargs.get("priority", "normal"),
            "requester_id": kwargs.get("requester_id"),
            "company_id": kwargs.get("company_id"),
            "assigned_user_id": kwargs.get("assigned_user_id"),
            "category": kwargs.get("category"),
            "module_slug": kwargs.get("module_slug"),
            "external_reference": kwargs.get("external_reference"),
            "closed_at": None,
            "xero_invoice_number": None,
            "ai_summary": None,
            "ai_tags": None,
            "created_at": now,
            "updated_at": now,
        }
    
    async def mock_get_ticket(ticket_id):
        return {
            "id": ticket_id,
            "subject": "Test Ticket via Session",
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
            "created_at": now,
            "updated_at": now,
        }
    
    async def mock_list_replies(ticket_id, include_internal=False):
        return []
    
    async def mock_list_watchers(ticket_id):
        return []
    
    async def mock_add_watcher(ticket_id, user_id):
        return None
    
    async def mock_resolve_status(status):
        return status or "open"
    
    async def mock_refresh_ai(*args, **kwargs):
        pass
    
    monkeypatch.setattr(tickets_service, "create_ticket", mock_create_ticket)
    monkeypatch.setattr(tickets_service, "resolve_status_or_default", mock_resolve_status)
    monkeypatch.setattr(tickets_service, "refresh_ticket_ai_summary", mock_refresh_ai)
    monkeypatch.setattr(tickets_service, "refresh_ticket_ai_tags", mock_refresh_ai)
    monkeypatch.setattr(tickets_repo, "get_ticket", mock_get_ticket)
    monkeypatch.setattr(tickets_repo, "list_replies", mock_list_replies)
    monkeypatch.setattr(tickets_repo, "list_watchers", mock_list_watchers)
    monkeypatch.setattr(tickets_repo, "add_watcher", mock_add_watcher)
    
    from app.repositories import ticket_attachments as attachments_repo
    async def mock_list_attachments(ticket_id):
        return []
    monkeypatch.setattr(attachments_repo, "list_attachments", mock_list_attachments)
    
    # Make POST request with session (using Authorization header to bypass CSRF)
    response = client.post(
        "/api/tickets/",
        json={
            "subject": "Test Ticket via Session",
            "description": "Test description",
            # No requester_id needed - will use session user
        },
        headers={"Authorization": "Bearer test_token"},
        cookies={"myportal_session": "test_token"}
    )
    
    assert response.status_code == 201, f"Expected 201 Created, got {response.status_code}: {response.json()}"
    
    data = response.json()
    assert "id" in data, "Response should contain ticket id"
    assert data["id"] == 888
    assert data["requester_id"] == 42  # Session user ID
