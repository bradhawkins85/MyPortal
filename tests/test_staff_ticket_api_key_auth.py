"""Tests that staff and ticket CRUD endpoints accept API key authentication."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.main import automations_service, change_log_service, modules_service, scheduler_service
from app.core.database import db


@pytest.fixture(autouse=True)
def _mock_startup(monkeypatch):
    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(db, "connect", noop)
    monkeypatch.setattr(db, "disconnect", noop)
    monkeypatch.setattr(db, "run_migrations", noop)
    monkeypatch.setattr(change_log_service, "sync_change_log_sources", noop)
    monkeypatch.setattr(modules_service, "ensure_default_modules", noop)
    monkeypatch.setattr(automations_service, "refresh_all_schedules", noop)
    monkeypatch.setattr(scheduler_service, "start", noop)
    monkeypatch.setattr(scheduler_service, "stop", noop)


_MOCK_API_KEY_RECORD = {
    "id": 1,
    "description": "Test API Key",
    "is_enabled": True,
    "permissions": [],
    "ip_restrictions": [],
}

_MOCK_STAFF_RECORD = {
    "id": 42,
    "company_id": 10,
    "first_name": "Jane",
    "last_name": "Doe",
    "email": "jane@example.com",
    "enabled": True,
    "is_ex_staff": False,
    "mobile_phone": None,
    "date_onboarded": None,
    "date_offboarded": None,
    "street": None,
    "city": None,
    "state": None,
    "postcode": None,
    "country": None,
    "department": None,
    "job_title": None,
    "org_company": None,
    "manager_name": None,
    "account_action": None,
    "syncro_contact_id": None,
    "onboarding_status": "approved",
    "onboarding_complete": True,
    "onboarding_completed_at": None,
    "offboarding_status": None,
    "offboarding_complete": False,
    "offboarding_completed_at": None,
    "offboarding_requested_at": None,
    "offboarding_updated_at": None,
    "approval_status": "approved",
    "requested_by_user_id": None,
    "requested_at": None,
    "approved_by_user_id": None,
    "approved_at": None,
    "request_notes": None,
    "approval_notes": None,
    "created_at": None,
    "updated_at": None,
    "custom_fields": {},
    "workflow_status": None,
}


# ---------------------------------------------------------------------------
# Staff endpoints
# ---------------------------------------------------------------------------


def test_get_staff_list_with_api_key(monkeypatch):
    """GET /api/staff returns 200 when authenticated via x-api-key."""
    from app.api.dependencies import api_keys as api_key_dep
    from app.api.dependencies import database as database_dep
    from app.api.dependencies.auth import get_optional_user
    from app.api.routes import staff as staff_routes

    async def mock_list_staff(*args, **kwargs):
        return [_MOCK_STAFF_RECORD]

    async def mock_list_executions(*args, **kwargs):
        return {}

    monkeypatch.setattr(staff_routes.staff_repo, "list_staff", mock_list_staff)
    monkeypatch.setattr(
        staff_routes.staff_workflow_repo,
        "list_executions_for_staff_ids",
        mock_list_executions,
    )

    app.dependency_overrides[database_dep.require_database] = lambda: None
    app.dependency_overrides[api_key_dep.get_optional_api_key] = lambda: _MOCK_API_KEY_RECORD
    app.dependency_overrides[get_optional_user] = lambda: None
    try:
        client = TestClient(app)
        response = client.get(
            "/api/staff",
            params={"companyId": 10},
            headers={"x-api-key": "test-key"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200


def test_get_staff_by_id_with_api_key(monkeypatch):
    """GET /api/staff/{id} returns 200 when authenticated via x-api-key."""
    from app.api.dependencies import api_keys as api_key_dep
    from app.api.dependencies import database as database_dep
    from app.api.dependencies.auth import get_optional_user
    from app.api.routes import staff as staff_routes

    async def mock_get_staff_by_id(staff_id):
        return {**_MOCK_STAFF_RECORD, "id": staff_id}

    async def mock_get_workflow_status(staff_id):
        return None

    monkeypatch.setattr(staff_routes.staff_repo, "get_staff_by_id", mock_get_staff_by_id)
    monkeypatch.setattr(
        staff_routes.staff_onboarding_workflow_service,
        "get_staff_workflow_status",
        mock_get_workflow_status,
    )

    app.dependency_overrides[database_dep.require_database] = lambda: None
    app.dependency_overrides[api_key_dep.get_optional_api_key] = lambda: _MOCK_API_KEY_RECORD
    app.dependency_overrides[get_optional_user] = lambda: None
    try:
        client = TestClient(app)
        response = client.get(
            "/api/staff/42",
            headers={"x-api-key": "test-key"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200


def test_get_staff_by_id_requires_auth(monkeypatch):
    """GET /api/staff/{id} returns 401 when no auth is provided."""
    from app.api.dependencies import api_keys as api_key_dep
    from app.api.dependencies import database as database_dep
    from app.api.dependencies.auth import get_optional_user

    app.dependency_overrides[database_dep.require_database] = lambda: None
    app.dependency_overrides[api_key_dep.get_optional_api_key] = lambda: None
    app.dependency_overrides[get_optional_user] = lambda: None
    try:
        client = TestClient(app)
        response = client.get("/api/staff/42")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401


def test_delete_staff_with_api_key(monkeypatch):
    """DELETE /api/staff/{id} returns 204 when authenticated via x-api-key."""
    from app.api.dependencies import api_keys as api_key_dep
    from app.api.dependencies import database as database_dep
    from app.api.dependencies.auth import get_optional_user
    from app.api.routes import staff as staff_routes

    async def mock_get_staff_by_id(staff_id):
        return {**_MOCK_STAFF_RECORD, "id": staff_id}

    async def mock_delete_staff(staff_id):
        return None

    monkeypatch.setattr(staff_routes.staff_repo, "get_staff_by_id", mock_get_staff_by_id)
    monkeypatch.setattr(staff_routes.staff_repo, "delete_staff", mock_delete_staff)

    app.dependency_overrides[database_dep.require_database] = lambda: None
    app.dependency_overrides[api_key_dep.get_optional_api_key] = lambda: _MOCK_API_KEY_RECORD
    app.dependency_overrides[get_optional_user] = lambda: None
    try:
        client = TestClient(app)
        response = client.delete(
            "/api/staff/42",
            headers={"x-api-key": "test-key"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 204


# ---------------------------------------------------------------------------
# Ticket endpoints
# ---------------------------------------------------------------------------

_MOCK_TICKET_RECORD = {
    "id": 99,
    "subject": "Test ticket",
    "status": "open",
    "priority": "normal",
    "company_id": 10,
    "requester_id": 42,
    "assigned_user_id": None,
    "module_slug": None,
    "external_reference": None,
    "category": None,
    "created_at": "2025-01-01T00:00:00+00:00",
    "updated_at": "2025-01-01T00:00:00+00:00",
    "closed_at": None,
    "description": "<p>Test</p>",
    "ai_summary": None,
    "ai_tags": [],
}


def _setup_ticket_mocks(monkeypatch):
    """Patch ticket-related repositories used by _build_ticket_detail."""
    from app.api.routes import tickets as ticket_routes

    async def mock_get_ticket(ticket_id):
        return {**_MOCK_TICKET_RECORD, "id": ticket_id}

    async def mock_list_replies(ticket_id, *, include_internal=False):
        return []

    async def mock_list_split_replies(ticket_id):
        return []

    async def mock_list_watchers(ticket_id):
        return []

    async def mock_list_attachments(ticket_id, **kwargs):
        return []

    monkeypatch.setattr(ticket_routes.tickets_repo, "get_ticket", mock_get_ticket)
    monkeypatch.setattr(ticket_routes.tickets_repo, "list_replies", mock_list_replies)
    monkeypatch.setattr(
        ticket_routes.tickets_repo,
        "list_split_replies_for_original",
        mock_list_split_replies,
    )
    monkeypatch.setattr(ticket_routes.tickets_repo, "list_watchers", mock_list_watchers)
    monkeypatch.setattr(
        ticket_routes.attachments_repo, "list_attachments", mock_list_attachments
    )


def test_get_ticket_with_api_key(monkeypatch):
    """GET /api/tickets/{id} returns 200 when authenticated via x-api-key."""
    from app.api.dependencies import api_keys as api_key_dep
    from app.api.dependencies import database as database_dep
    from app.api.dependencies.auth import get_optional_user

    _setup_ticket_mocks(monkeypatch)

    app.dependency_overrides[database_dep.require_database] = lambda: None
    app.dependency_overrides[api_key_dep.get_optional_api_key] = lambda: _MOCK_API_KEY_RECORD
    app.dependency_overrides[get_optional_user] = lambda: None
    try:
        client = TestClient(app)
        response = client.get(
            "/api/tickets/99",
            headers={"x-api-key": "test-key"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200


def test_get_ticket_requires_auth(monkeypatch):
    """GET /api/tickets/{id} returns 401 when no auth is provided."""
    from app.api.dependencies import api_keys as api_key_dep
    from app.api.dependencies import database as database_dep
    from app.api.dependencies.auth import get_optional_user

    app.dependency_overrides[database_dep.require_database] = lambda: None
    app.dependency_overrides[api_key_dep.get_optional_api_key] = lambda: None
    app.dependency_overrides[get_optional_user] = lambda: None
    try:
        client = TestClient(app)
        response = client.get("/api/tickets/99")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401


def test_delete_ticket_with_api_key(monkeypatch):
    """DELETE /api/tickets/{id} returns 204 when authenticated via x-api-key."""
    from app.api.dependencies import api_keys as api_key_dep
    from app.api.dependencies import database as database_dep
    from app.api.dependencies.auth import get_optional_user
    from app.api.routes import tickets as ticket_routes

    async def mock_get_ticket(ticket_id):
        return {**_MOCK_TICKET_RECORD, "id": ticket_id}

    async def mock_delete_ticket(ticket_id):
        return None

    async def mock_broadcast(*args, **kwargs):
        return None

    monkeypatch.setattr(ticket_routes.tickets_repo, "get_ticket", mock_get_ticket)
    monkeypatch.setattr(ticket_routes.tickets_repo, "delete_ticket", mock_delete_ticket)
    monkeypatch.setattr(
        ticket_routes.tickets_service, "broadcast_ticket_event", mock_broadcast
    )

    app.dependency_overrides[database_dep.require_database] = lambda: None
    app.dependency_overrides[api_key_dep.get_optional_api_key] = lambda: _MOCK_API_KEY_RECORD
    app.dependency_overrides[get_optional_user] = lambda: None
    try:
        client = TestClient(app)
        response = client.delete(
            "/api/tickets/99",
            headers={"x-api-key": "test-key"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 204
