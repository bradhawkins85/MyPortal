import pytest
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

from fastapi import status
from fastapi.testclient import TestClient

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


@pytest.fixture
def helpdesk_user() -> dict[str, Any]:
    now = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    return {
        "id": 5,
        "email": "helpdesk@example.com",
        "first_name": "Helpdesk",
        "last_name": "Tech",
        "mobile_phone": None,
        "company_id": None,
        "is_super_admin": False,
        "created_at": now,
        "updated_at": now,
    }


def test_admin_tickets_page_with_phone_search(monkeypatch, active_session, helpdesk_user):
    """Test that admin tickets page accepts phoneNumber query parameter and searches tickets"""
    
    # Mock session lookup
    async def fake_load_session(request):
        return active_session
    
    monkeypatch.setattr(session_manager, "load_session", fake_load_session)
    
    # Mock user lookup
    async def fake_get_user_by_id(user_id: int):
        if user_id == helpdesk_user["id"]:
            return helpdesk_user
        return None
    
    from app.repositories import users as user_repo
    monkeypatch.setattr(user_repo, "get_user_by_id", fake_get_user_by_id)
    
    # Mock helpdesk technician check
    async def fake_is_helpdesk_technician(user: dict, request):
        return user.get("id") == helpdesk_user["id"]
    
    monkeypatch.setattr(main_module, "_is_helpdesk_technician", fake_is_helpdesk_technician)
    
    # Mock ticket search by phone
    sample_tickets = [
        {
            "id": 1,
            "company_id": 10,
            "requester_id": 5,
            "assigned_user_id": None,
            "subject": "Test Ticket 1",
            "description": "First ticket",
            "status": "open",
            "priority": "normal",
            "category": None,
            "module_slug": None,
            "external_reference": None,
            "ai_summary": None,
            "ai_summary_status": None,
            "ai_summary_model": None,
            "ai_resolution_state": None,
            "ai_summary_updated_at": None,
            "ai_tags": [],
            "ai_tags_status": None,
            "ai_tags_model": None,
            "ai_tags_updated_at": None,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            "closed_at": None,
        }
    ]
    
    async def fake_list_tickets_by_phone(phone_number: str, limit: int = 100):
        return sample_tickets
    
    from app.repositories import tickets as tickets_repo
    monkeypatch.setattr(tickets_repo, "list_tickets_by_requester_phone", fake_list_tickets_by_phone)
    
    # Mock dashboard service
    from app.services import tickets as tickets_service
    from types import SimpleNamespace
    
    async def fake_load_dashboard_state(status_filter=None, module_filter=None, limit=200):
        return SimpleNamespace(
            tickets=[],
            total=0,
            status_counts={},
            available_statuses=[],
            status_definitions=[],
            modules=[],
            companies=[],
            technicians=[],
            company_lookup={},
            user_lookup={},
        )
    
    monkeypatch.setattr(tickets_service, "load_dashboard_state", fake_load_dashboard_state)
    
    # Mock labour types service
    from app.services import labour_types as labour_types_service
    
    async def fake_list_labour_types():
        return []
    
    monkeypatch.setattr(labour_types_service, "list_labour_types", fake_list_labour_types)
    
    # Make request with phone number
    client = TestClient(app)
    response = client.get(
        "/admin/tickets?phoneNumber=%2B61412345678",
        cookies={"session_token": active_session.session_token}
    )
    
    # Check response
    assert response.status_code == status.HTTP_200_OK
    assert b"Test Ticket 1" in response.content or b"Found 1 ticket(s)" in response.content


def test_admin_tickets_page_with_empty_phone_search(monkeypatch, active_session, helpdesk_user):
    """Test that admin tickets page handles empty phone search gracefully"""
    
    # Mock session lookup
    async def fake_load_session(request):
        return active_session
    
    monkeypatch.setattr(session_manager, "load_session", fake_load_session)
    
    # Mock user lookup
    async def fake_get_user_by_id(user_id: int):
        if user_id == helpdesk_user["id"]:
            return helpdesk_user
        return None
    
    from app.repositories import users as user_repo
    monkeypatch.setattr(user_repo, "get_user_by_id", fake_get_user_by_id)
    
    # Mock helpdesk technician check
    async def fake_is_helpdesk_technician(user: dict, request):
        return user.get("id") == helpdesk_user["id"]
    
    monkeypatch.setattr(main_module, "_is_helpdesk_technician", fake_is_helpdesk_technician)
    
    # Mock ticket search by phone - returns empty list
    async def fake_list_tickets_by_phone(phone_number: str, limit: int = 100):
        return []
    
    from app.repositories import tickets as tickets_repo
    monkeypatch.setattr(tickets_repo, "list_tickets_by_requester_phone", fake_list_tickets_by_phone)
    
    # Mock dashboard service
    from app.services import tickets as tickets_service
    from types import SimpleNamespace
    
    async def fake_load_dashboard_state(status_filter=None, module_filter=None, limit=200):
        return SimpleNamespace(
            tickets=[],
            total=0,
            status_counts={},
            available_statuses=[],
            status_definitions=[],
            modules=[],
            companies=[],
            technicians=[],
            company_lookup={},
            user_lookup={},
        )
    
    monkeypatch.setattr(tickets_service, "load_dashboard_state", fake_load_dashboard_state)
    
    # Mock labour types service
    from app.services import labour_types as labour_types_service
    
    async def fake_list_labour_types():
        return []
    
    monkeypatch.setattr(labour_types_service, "list_labour_types", fake_list_labour_types)
    
    # Make request with phone number
    client = TestClient(app)
    response = client.get(
        "/admin/tickets?phoneNumber=0412999999",
        cookies={"session_token": active_session.session_token}
    )
    
    # Check response - should still return 200 but show error message
    assert response.status_code == status.HTTP_200_OK
    assert b"No tickets found" in response.content


def test_admin_tickets_page_without_phone_search(monkeypatch, active_session, helpdesk_user):
    """Test that admin tickets page works normally without phone search parameter"""
    
    # Mock session lookup
    async def fake_load_session(request):
        return active_session
    
    monkeypatch.setattr(session_manager, "load_session", fake_load_session)
    
    # Mock user lookup
    async def fake_get_user_by_id(user_id: int):
        if user_id == helpdesk_user["id"]:
            return helpdesk_user
        return None
    
    from app.repositories import users as user_repo
    monkeypatch.setattr(user_repo, "get_user_by_id", fake_get_user_by_id)
    
    # Mock helpdesk technician check
    async def fake_is_helpdesk_technician(user: dict, request):
        return user.get("id") == helpdesk_user["id"]
    
    monkeypatch.setattr(main_module, "_is_helpdesk_technician", fake_is_helpdesk_technician)
    
    # Mock dashboard service
    from app.services import tickets as tickets_service
    from types import SimpleNamespace
    
    sample_tickets = [
        {
            "id": 1,
            "company_id": 10,
            "requester_id": 5,
            "assigned_user_id": None,
            "subject": "Test Ticket 1",
            "description": "First ticket",
            "status": "open",
            "priority": "normal",
            "category": None,
            "module_slug": None,
            "external_reference": None,
            "ai_summary": None,
            "ai_summary_status": None,
            "ai_summary_model": None,
            "ai_resolution_state": None,
            "ai_summary_updated_at": None,
            "ai_tags": [],
            "ai_tags_status": None,
            "ai_tags_model": None,
            "ai_tags_updated_at": None,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            "closed_at": None,
        }
    ]
    
    async def fake_load_dashboard_state(status_filter=None, module_filter=None, limit=200):
        return SimpleNamespace(
            tickets=sample_tickets,
            total=1,
            status_counts={"open": 1},
            available_statuses=["open"],
            status_definitions=[],
            modules=[],
            companies=[],
            technicians=[],
            company_lookup={},
            user_lookup={},
        )
    
    monkeypatch.setattr(tickets_service, "load_dashboard_state", fake_load_dashboard_state)
    
    # Mock labour types service
    from app.services import labour_types as labour_types_service
    
    async def fake_list_labour_types():
        return []
    
    monkeypatch.setattr(labour_types_service, "list_labour_types", fake_list_labour_types)
    
    # Make request without phone number
    client = TestClient(app)
    response = client.get(
        "/admin/tickets",
        cookies={"session_token": active_session.session_token}
    )
    
    # Check response
    assert response.status_code == status.HTTP_200_OK
    assert b"Ticketing workspace" in response.content
