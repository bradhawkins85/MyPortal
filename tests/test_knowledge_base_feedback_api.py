from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import auth as auth_dependencies
from app.api.routes import knowledge_base as knowledge_base_routes
from app.core.database import db
from app.main import app, scheduler_service
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


@pytest.fixture
def active_session(monkeypatch) -> SessionData:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    session = SessionData(
        id=1,
        user_id=1,
        session_token="token",
        csrf_token="csrf-token",
        created_at=now,
        expires_at=now + timedelta(hours=1),
        last_seen_at=now,
        ip_address=None,
        user_agent=None,
        active_company_id=77,
        pending_totp_secret=None,
    )

    async def fake_load_session(request, *, allow_inactive=False):
        return session

    monkeypatch.setattr(session_manager, "load_session", fake_load_session)
    return session


def test_submit_feedback_creates_ticket(monkeypatch, active_session):
    user = {"id": 1, "email": "user@example.com", "company_id": 12, "is_super_admin": False}

    app.dependency_overrides[auth_dependencies.get_current_user] = lambda: user

    monkeypatch.setattr(
        knowledge_base_routes.kb_service,
        "build_access_context",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(
        knowledge_base_routes.kb_service,
        "get_article_by_slug_for_context",
        AsyncMock(return_value={"slug": "vpn-setup", "title": "VPN Setup"}),
    )
    monkeypatch.setattr(
        knowledge_base_routes.tickets_service,
        "resolve_status_or_default",
        AsyncMock(return_value="open"),
    )
    create_ticket = AsyncMock(return_value={"id": 321})
    monkeypatch.setattr(knowledge_base_routes.tickets_service, "create_ticket", create_ticket)
    audit_record = AsyncMock(return_value=None)
    monkeypatch.setattr(knowledge_base_routes.audit_service, "record", audit_record)

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/knowledge-base/articles/vpn-setup/feedback",
                json={"rating": "up", "feedback": "Great article"},
                headers={"X-CSRF-Token": active_session.csrf_token},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    assert response.json()["ticket_id"] == 321
    create_ticket.assert_awaited_once()
    create_call = create_ticket.await_args.kwargs
    assert create_call["requester_id"] == 1
    assert create_call["company_id"] == 77
    assert create_call["category"] == "knowledge_base_feedback"
    assert create_call["module_slug"] == "knowledge_base"
    assert "VPN Setup" in create_call["subject"]
    assert "Great article" in create_call["description"]
    audit_record.assert_awaited_once()


def test_submit_feedback_returns_not_found_for_inaccessible_article(monkeypatch, active_session):
    user = {"id": 1, "email": "user@example.com", "company_id": 12, "is_super_admin": False}
    app.dependency_overrides[auth_dependencies.get_current_user] = lambda: user

    monkeypatch.setattr(
        knowledge_base_routes.kb_service,
        "build_access_context",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(
        knowledge_base_routes.kb_service,
        "get_article_by_slug_for_context",
        AsyncMock(return_value=None),
    )
    create_ticket = AsyncMock(return_value={"id": 321})
    monkeypatch.setattr(knowledge_base_routes.tickets_service, "create_ticket", create_ticket)

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/knowledge-base/articles/unknown/feedback",
                json={"rating": "down", "feedback": "Needs work"},
                headers={"X-CSRF-Token": active_session.csrf_token},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["detail"] == "Article not found"
    create_ticket.assert_not_awaited()
