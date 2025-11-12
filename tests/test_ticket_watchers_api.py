"""Tests for ticket watcher add/remove API endpoints."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.api.dependencies import auth as auth_dependencies
from app.api.dependencies import database as database_dependencies
from app.core.database import db
from app import main as main_module
from app.main import (
    app,
    automations_service,
    change_log_service,
    modules_service,
    scheduler_service,
    tickets_repo,
    tickets_service,
)
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

    async def fake_sync_change_log_sources(*args, **kwargs):
        return None

    async def fake_ensure_default_modules(*args, **kwargs):
        return None

    async def fake_refresh_all_schedules(*args, **kwargs):
        return None

    monkeypatch.setattr(db, "connect", fake_connect)
    monkeypatch.setattr(db, "disconnect", fake_disconnect)
    monkeypatch.setattr(db, "run_migrations", fake_run_migrations)
    monkeypatch.setattr(scheduler_service, "start", fake_start)
    monkeypatch.setattr(scheduler_service, "stop", fake_stop)
    monkeypatch.setattr(change_log_service, "sync_change_log_sources", fake_sync_change_log_sources)
    monkeypatch.setattr(modules_service, "ensure_default_modules", fake_ensure_default_modules)
    monkeypatch.setattr(automations_service, "refresh_all_schedules", fake_refresh_all_schedules)


@pytest.fixture
def active_session(monkeypatch):
    now = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    session = SessionData(
        id=1,
        user_id=1,
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

    async def fake_load_session(request, *, allow_inactive=False):
        return session

    monkeypatch.setattr(session_manager, "load_session", fake_load_session)
    return session


def _override_dependencies(active_session):
    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    app.dependency_overrides[auth_dependencies.require_helpdesk_technician] = lambda: {
        "id": active_session.user_id,
        "email": "tech@example.com",
        "is_super_admin": False,
    }


def _reset_overrides():
    app.dependency_overrides.pop(database_dependencies.require_database, None)
    app.dependency_overrides.pop(auth_dependencies.require_helpdesk_technician, None)


def test_add_watcher_success(monkeypatch, active_session):
    """Test successfully adding a watcher to a ticket."""
    ticket_id = 123
    user_id = 5
    now = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    
    # Mock ticket exists
    async def mock_get_ticket(tid):
        return {
            "id": tid,
            "subject": "Test Ticket",
            "status": "open",
            "priority": "normal",
            "requester_id": 1,
            "company_id": 1,
            "created_at": now,
            "updated_at": now,
            "closed_at": None,
            "assigned_user_id": None,
            "category": None,
            "module_slug": None,
            "external_reference": None,
            "description": None,
            "ai_summary": None,
            "ai_summary_status": None,
            "ai_summary_model": None,
            "ai_resolution_state": None,
            "ai_summary_updated_at": None,
            "ai_tags": [],
            "ai_tags_status": None,
            "ai_tags_model": None,
            "ai_tags_updated_at": None,
        }
    
    # Mock watcher operations
    async def mock_add_watcher(tid, uid):
        pass
    
    async def mock_list_watchers(tid):
        return [
            {"id": 1, "ticket_id": tid, "user_id": user_id, "created_at": now}
        ]
    
    async def mock_list_replies(tid, include_internal=False):
        return []
    
    async def mock_emit_event(tid, **kwargs):
        pass
    
    # Mock has permission
    async def mock_has_permission(uid, key):
        return True
    
    from app.repositories import company_memberships as membership_repo
    
    monkeypatch.setattr(tickets_repo, "get_ticket", mock_get_ticket)
    monkeypatch.setattr(tickets_repo, "add_watcher", mock_add_watcher)
    monkeypatch.setattr(tickets_repo, "list_watchers", mock_list_watchers)
    monkeypatch.setattr(tickets_repo, "list_replies", mock_list_replies)
    monkeypatch.setattr(membership_repo, "user_has_permission", mock_has_permission)
    monkeypatch.setattr(tickets_service, "emit_ticket_updated_event", mock_emit_event)
    
    _override_dependencies(active_session)
    
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/tickets/{ticket_id}/watchers/{user_id}",
                headers={"X-CSRF-Token": active_session.csrf_token},
            )
        
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["id"] == ticket_id
        assert len(data["watchers"]) == 1
        assert data["watchers"][0]["user_id"] == user_id
    finally:
        _reset_overrides()


def test_add_watcher_ticket_not_found(monkeypatch, active_session):
    """Test adding a watcher to a non-existent ticket."""
    ticket_id = 999
    user_id = 5
    
    async def mock_get_ticket(tid):
        return None
    
    monkeypatch.setattr(tickets_repo, "get_ticket", mock_get_ticket)
    
    _override_dependencies(active_session)
    
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/tickets/{ticket_id}/watchers/{user_id}",
                headers={"X-CSRF-Token": active_session.csrf_token},
            )
        assert response.status_code == status.HTTP_404_NOT_FOUND
    finally:
        _reset_overrides()


def test_remove_watcher_success(monkeypatch, active_session):
    """Test successfully removing a watcher from a ticket."""
    ticket_id = 123
    user_id = 5
    
    async def mock_get_ticket(tid):
        return {
            "id": tid,
            "subject": "Test Ticket",
            "status": "open",
            "priority": "normal",
            "requester_id": 1,
            "company_id": 1,
        }
    
    async def mock_remove_watcher(tid, uid):
        pass
    
    async def mock_emit_event(tid, **kwargs):
        pass
    
    monkeypatch.setattr(tickets_repo, "get_ticket", mock_get_ticket)
    monkeypatch.setattr(tickets_repo, "remove_watcher", mock_remove_watcher)
    monkeypatch.setattr(tickets_service, "emit_ticket_updated_event", mock_emit_event)
    
    _override_dependencies(active_session)
    
    try:
        with TestClient(app) as client:
            response = client.delete(
                f"/api/tickets/{ticket_id}/watchers/{user_id}",
                headers={"X-CSRF-Token": active_session.csrf_token},
            )
        assert response.status_code == status.HTTP_204_NO_CONTENT
    finally:
        _reset_overrides()


def test_remove_watcher_ticket_not_found(monkeypatch, active_session):
    """Test removing a watcher from a non-existent ticket."""
    ticket_id = 999
    user_id = 5
    
    async def mock_get_ticket(tid):
        return None
    
    monkeypatch.setattr(tickets_repo, "get_ticket", mock_get_ticket)
    
    _override_dependencies(active_session)
    
    try:
        with TestClient(app) as client:
            response = client.delete(
                f"/api/tickets/{ticket_id}/watchers/{user_id}",
                headers={"X-CSRF-Token": active_session.csrf_token},
            )
        assert response.status_code == status.HTTP_404_NOT_FOUND
    finally:
        _reset_overrides()
