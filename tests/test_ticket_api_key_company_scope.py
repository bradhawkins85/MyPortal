"""Regression tests: API keys must be restricted to their scoped companies on /api/tickets."""
import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from app.main import app
from app.main import automations_service, change_log_service, modules_service, scheduler_service
from app.core.database import db

from app.api.dependencies.api_keys import get_optional_api_key
from app.api.dependencies.auth import get_optional_user
from app.api.dependencies import database as database_deps
from app.repositories import tickets as tickets_repo
from app.services import tickets as tickets_service

JSON_HEADERS = {"Accept": "application/json", "x-api-key": "scoped-test-key"}


@pytest.fixture(autouse=True)
def _mock_startup(monkeypatch):
    """Mock startup tasks (mirrors tests/test_ticket_api_key_create.py)."""

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


def _api_key_record(company_ids):
    record = {
        "id": 7,
        "name": "Scoped Test Key",
        "key_value": "scoped-test-key",
        "is_active": True,
        "permissions": [],
        "ip_restrictions": [],
    }
    if company_ids is not None:
        record["allowed_company_ids"] = list(company_ids)
    return record


def _ticket_row(ticket_id: int, company_id):
    now = datetime.now(timezone.utc)
    return {
        "id": ticket_id,
        "subject": "Scoped ticket",
        "description": "desc",
        "status": "open",
        "priority": "normal",
        "requester_id": 123,
        "company_id": company_id,
        "assigned_user_id": None,
        "category": None,
        "module_slug": None,
        "external_reference": None,
        "merged_into_ticket_id": None,
        "closed_at": None,
        "xero_invoice_number": None,
        "ai_summary": None,
        "ai_tags": None,
        "created_at": now,
        "updated_at": now,
    }


def _override_actor(monkeypatch, key_record):
    app.dependency_overrides[database_deps.require_database] = lambda: None
    app.dependency_overrides[get_optional_api_key] = lambda: key_record
    app.dependency_overrides[get_optional_user] = lambda: None


def _detail_repo_mocks(monkeypatch, ticket):
    async def mock_get_ticket(ticket_id):
        return dict(ticket)

    async def mock_list_replies(ticket_id, include_internal=False):
        return []

    async def mock_list_watchers(ticket_id):
        return []

    async def mock_list_split_replies(ticket_id):
        return []

    monkeypatch.setattr(tickets_repo, "get_ticket", mock_get_ticket)
    monkeypatch.setattr(tickets_repo, "list_replies", mock_list_replies)
    monkeypatch.setattr(tickets_repo, "list_watchers", mock_list_watchers)
    monkeypatch.setattr(tickets_repo, "list_split_replies_for_original", mock_list_split_replies)

    from app.repositories import ticket_attachments as attachments_repo

    async def mock_list_attachments(ticket_id, *, access_levels=None):
        return []

    monkeypatch.setattr(attachments_repo, "list_attachments", mock_list_attachments)


def test_api_key_cannot_read_ticket_outside_its_company(monkeypatch):
    _override_actor(monkeypatch, _api_key_record([10]))
    _detail_repo_mocks(monkeypatch, _ticket_row(500, company_id=20))
    try:
        client = TestClient(app)
        response = client.get("/api/tickets/500", headers=JSON_HEADERS)
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 403, response.text
    assert "company" in response.json()["detail"].lower()


def test_api_key_can_read_ticket_within_its_company(monkeypatch):
    _override_actor(monkeypatch, _api_key_record([20]))
    _detail_repo_mocks(monkeypatch, _ticket_row(500, company_id=20))
    try:
        client = TestClient(app)
        response = client.get("/api/tickets/500", headers=JSON_HEADERS)
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200, response.text
    assert response.json()["id"] == 500


def test_unscoped_api_key_keeps_legacy_ticket_access(monkeypatch):
    _override_actor(monkeypatch, _api_key_record(None))
    _detail_repo_mocks(monkeypatch, _ticket_row(500, company_id=20))
    try:
        client = TestClient(app)
        response = client.get("/api/tickets/500", headers=JSON_HEADERS)
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200, response.text


def test_api_key_cannot_create_ticket_outside_its_company(monkeypatch):
    _override_actor(monkeypatch, _api_key_record([10]))
    try:
        client = TestClient(app)
        response = client.post(
            "/api/tickets/",
            json={
                "subject": "Cross-tenant ticket",
                "description": "desc",
                "requester_id": 123,
                "company_id": 20,
            },
            headers=JSON_HEADERS,
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 403, response.text
    assert "company" in response.json()["detail"].lower()


def test_scoped_api_key_create_requires_company_id(monkeypatch):
    created = {}

    async def mock_create_ticket(**kwargs):
        created.update(kwargs)
        return _ticket_row(999, kwargs.get("company_id"))

    monkeypatch.setattr(tickets_service, "create_ticket", mock_create_ticket)
    monkeypatch.setattr(tickets_service, "resolve_status_or_default", lambda status: status or "open")
    monkeypatch.setattr(tickets_service, "validate_status_choice", lambda status, *, allow_hidden: status)
    monkeypatch.setattr(tickets_service, "schedule_ticket_ai_refresh", lambda *a, **k: None)

    _override_actor(monkeypatch, _api_key_record([10, 20]))
    try:
        client = TestClient(app)
        response = client.post(
            "/api/tickets/",
            json={
                "subject": "No company",
                "description": "desc",
                "requester_id": 123,
            },
            headers=JSON_HEADERS,
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 400, response.text
    assert "company_id" in response.json()["detail"]
    assert not created, "ticket must not be created"


def test_api_key_list_defaults_to_single_scoped_company(monkeypatch):
    captured = {}

    async def mock_list_tickets(**kwargs):
        captured.update(kwargs)
        return []

    async def mock_count_tickets(**kwargs):
        return 0

    monkeypatch.setattr(tickets_repo, "list_tickets", mock_list_tickets)
    monkeypatch.setattr(tickets_repo, "count_tickets", mock_count_tickets)

    _override_actor(monkeypatch, _api_key_record([10]))
    try:
        client = TestClient(app)
        response = client.get("/api/tickets/", headers=JSON_HEADERS)
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200, response.text
    assert captured.get("company_id") == 10


def test_multi_company_api_key_list_requires_company_filter(monkeypatch):
    _override_actor(monkeypatch, _api_key_record([10, 20]))
    try:
        client = TestClient(app)
        response = client.get("/api/tickets/", headers=JSON_HEADERS)
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 400, response.text
    assert "company_id" in response.json()["detail"]


def test_api_key_cannot_move_ticket_to_other_company(monkeypatch):
    _override_actor(monkeypatch, _api_key_record([10]))
    _detail_repo_mocks(monkeypatch, _ticket_row(500, company_id=10))
    try:
        client = TestClient(app)
        response = client.put(
            "/api/tickets/500",
            json={"company_id": 20},
            headers=JSON_HEADERS,
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 403, response.text
    assert "company" in response.json()["detail"].lower()
