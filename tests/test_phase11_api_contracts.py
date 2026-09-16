from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.api.dependencies import auth as auth_dependencies
from app.api.dependencies import database as database_dependencies
from app.api.routes import auth as auth_routes
from app.api.routes import knowledge_base as knowledge_base_routes
from app.api.routes import tickets as tickets_routes
from app.api.routes import xero as xero_routes
from app.core.database import db
from app.main import app, automations_service, change_log_service, modules_service, scheduler_service
from app.schemas.auth import LoginResponse, RegistrationPendingResponse
from app.schemas.knowledge_base import KnowledgeBaseFeedbackCreateResponse
from app.schemas.tickets import TicketStatusListResponse
from app.schemas.xero import XeroTenantListResponse
from app.security.session import SessionData, session_manager
from app.services import tickets as tickets_service


@pytest.fixture(autouse=True)
def mock_startup(monkeypatch):
    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(main_module.settings, "enable_csrf", False)
    monkeypatch.setattr(db, "connect", _noop)
    monkeypatch.setattr(db, "disconnect", _noop)
    monkeypatch.setattr(db, "run_migrations", _noop)
    monkeypatch.setattr(scheduler_service, "start", _noop)
    monkeypatch.setattr(scheduler_service, "stop", _noop)
    monkeypatch.setattr(change_log_service, "sync_change_log_sources", _noop)
    monkeypatch.setattr(modules_service, "ensure_default_modules", _noop)
    monkeypatch.setattr(automations_service, "refresh_all_schedules", _noop)
    monkeypatch.setattr(main_module.refresh_notifier, "start", _noop)
    monkeypatch.setattr(main_module.refresh_notifier, "stop", _noop)
    monkeypatch.setattr(main_module.message_templates_service, "preload_cache", _noop)


@pytest.fixture
def active_session(monkeypatch) -> SessionData:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    session = SessionData(
        id=1,
        user_id=7,
        session_token="session-token",
        csrf_token="csrf-token",
        created_at=now,
        expires_at=now + timedelta(hours=1),
        last_seen_at=now,
        ip_address=None,
        user_agent=None,
        active_company_id=55,
        pending_totp_secret=None,
    )

    async def fake_load_session(request, *, allow_inactive=False):
        return session

    monkeypatch.setattr(session_manager, "load_session", fake_load_session)
    return session


def test_openapi_documents_phase11_contracts(active_session):
    with TestClient(app) as client:
        response = client.get("/internal/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    paths = schema["paths"]

    register = paths["/auth/register"]["post"]
    assert register["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/RegistrationRequest")
    assert register["responses"]["201"]["content"]["application/json"]["schema"]["$ref"].endswith("/LoginResponse")
    assert register["responses"]["202"]["content"]["application/json"]["schema"]["$ref"].endswith("/RegistrationPendingResponse")
    assert register["responses"]["409"]["content"]["application/json"]["schema"]["$ref"].endswith("/RegistrationConflictResponse")

    ticket_statuses = paths["/api/tickets/statuses"]
    assert set(ticket_statuses) >= {"get", "put"}
    assert ticket_statuses["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/TicketStatusListResponse")
    assert ticket_statuses["put"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/TicketStatusListResponse")

    feedback = paths["/api/knowledge-base/articles/{slug}/feedback"]["post"]
    assert feedback["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/KnowledgeBaseFeedbackCreateRequest")
    assert feedback["responses"]["201"]["content"]["application/json"]["schema"]["$ref"].endswith("/KnowledgeBaseFeedbackCreateResponse")

    xero_callback = paths["/api/integration-modules/xero/callback"]
    assert set(xero_callback) >= {"get", "post"}
    assert xero_callback["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/XeroCallbackResponse")
    assert xero_callback["post"]["responses"]["202"]["content"]["application/json"]["schema"]["$ref"].endswith("/XeroCallbackResponse")
    assert paths["/api/integration-modules/xero/tenants"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/XeroTenantListResponse")


def test_phase11_critical_endpoints_enforce_http_verbs(active_session):
    with TestClient(app) as client:
        assert client.put("/auth/register").status_code == 405
        assert client.patch("/api/tickets/statuses").status_code == 405
        assert client.put("/api/knowledge-base/articles/vpn-setup/feedback").status_code == 405
        assert client.delete("/api/integration-modules/xero/callback").status_code == 405
        assert client.post("/api/integration-modules/xero/tenants").status_code == 405


def test_registration_success_and_pending_flows_match_documented_models(monkeypatch, active_session):
    created_user = {
        "id": active_session.user_id,
        "email": "new.user@example.com",
        "first_name": "New",
        "last_name": "User",
        "mobile_phone": None,
        "company_id": None,
        "is_super_admin": True,
        "created_at": active_session.created_at,
        "updated_at": active_session.created_at,
    }

    async def fake_get_user_by_email(email: str):
        return None

    async def fake_create_user(**kwargs):
        return created_user

    async def fake_create_session(user_id, request, active_company_id=None):
        return active_session

    def fake_apply_session_cookies(response, session, request):
        return None

    monkeypatch.setattr(auth_routes.user_repo, "get_user_by_email", fake_get_user_by_email)
    monkeypatch.setattr(auth_routes.user_repo, "create_user", fake_create_user)
    monkeypatch.setattr(
        auth_routes.company_access,
        "first_accessible_company_id",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(auth_routes.session_manager, "create_session", fake_create_session)
    monkeypatch.setattr(auth_routes.session_manager, "apply_session_cookies", fake_apply_session_cookies)
    monkeypatch.setattr(
        auth_routes.staff_access_service,
        "apply_pending_access_for_user",
        AsyncMock(return_value=None),
    )
    app.dependency_overrides[database_dependencies.require_database] = lambda: None

    try:
        monkeypatch.setattr(auth_routes.user_repo, "count_users", AsyncMock(return_value=0))
        with TestClient(app) as client:
            first_user_response = client.post(
                "/auth/register",
                json={
                    "email": created_user["email"],
                    "password": "strong-password",
                    "first_name": created_user["first_name"],
                    "last_name": created_user["last_name"],
                },
            )

        assert first_user_response.status_code == 201
        LoginResponse.model_validate(first_user_response.json())

        pending_user = dict(created_user, id=99, is_super_admin=False, email="staff@example.com")
        monkeypatch.setattr(auth_routes.user_repo, "count_users", AsyncMock(return_value=4))
        monkeypatch.setattr(auth_routes.staff_repo, "list_staff_by_email", AsyncMock(return_value=[{"company_id": 12, "enabled": True}]))
        monkeypatch.setattr(auth_routes.user_repo, "create_user", AsyncMock(return_value=pending_user))
        monkeypatch.setattr(auth_routes.user_company_repo, "assign_user_to_company", AsyncMock(return_value=None))
        monkeypatch.setattr(auth_routes.user_repo, "update_user", AsyncMock(return_value=None))
        monkeypatch.setattr(auth_routes.auth_repo, "create_account_verification_token", AsyncMock(return_value=None))
        monkeypatch.setattr(auth_routes, "_send_signup_verification_email", AsyncMock(return_value=None))

        with TestClient(app) as client:
            pending_response = client.post(
                "/auth/register",
                json={
                    "email": pending_user["email"],
                    "password": "strong-password",
                    "first_name": "Staff",
                    "last_name": "User",
                },
            )

        assert pending_response.status_code == 202
        RegistrationPendingResponse.model_validate(pending_response.json())
    finally:
        app.dependency_overrides.clear()


def test_feedback_ticket_status_and_xero_tenant_flows_match_documented_models(monkeypatch, active_session):
    app.dependency_overrides[auth_dependencies.get_current_user] = lambda: {
        "id": active_session.user_id,
        "email": "user@example.com",
        "company_id": 12,
        "is_super_admin": False,
    }
    app.dependency_overrides[tickets_routes.require_helpdesk_technician] = lambda: {
        "id": active_session.user_id,
        "email": "tech@example.com",
        "is_super_admin": False,
    }

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
    monkeypatch.setattr(
        knowledge_base_routes.tickets_service,
        "create_ticket",
        AsyncMock(return_value={"id": 321}),
    )
    monkeypatch.setattr(knowledge_base_routes.audit_service, "record", AsyncMock(return_value=None))
    monkeypatch.setattr(
        tickets_service,
        "list_status_definitions",
        AsyncMock(
            return_value=[
                tickets_service.TicketStatusDefinition(
                    tech_status="open",
                    tech_label="Open",
                    public_status="Open",
                )
            ]
        ),
    )
    monkeypatch.setattr(
        xero_routes.modules_service,
        "get_module",
        AsyncMock(return_value={"enabled": True, "slug": "xero", "settings": {"tenant_id": "tenant-123"}}),
    )
    monkeypatch.setattr(
        xero_routes.modules_service,
        "get_xero_credentials",
        AsyncMock(
            return_value={
                "client_id": "client-id",
                "client_secret": "secret",
                "refresh_token": "refresh",
            }
        ),
    )
    monkeypatch.setattr(
        xero_routes.modules_service,
        "acquire_xero_access_token",
        AsyncMock(return_value="access-token"),
    )

    class MockResponse:
        def json(self):
            return [
                {
                    "tenantId": "tenant-123",
                    "tenantName": "Acme",
                    "tenantType": "ORGANISATION",
                    "createdDateUtc": "2026-01-01T00:00:00Z",
                }
            ]

        def raise_for_status(self):
            return None

    class MockAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, **kwargs):
            return MockResponse()

    monkeypatch.setattr(xero_routes.httpx, "AsyncClient", lambda timeout: MockAsyncClient())

    try:
        with TestClient(app) as client:
            feedback_response = client.post(
                "/api/knowledge-base/articles/vpn-setup/feedback",
                json={"rating": "up", "feedback": "Very helpful"},
                headers={"X-CSRF-Token": active_session.csrf_token},
            )
            ticket_statuses_response = client.get("/api/tickets/statuses")
            xero_tenants_response = client.get("/api/integration-modules/xero/tenants")

        assert feedback_response.status_code == 201
        KnowledgeBaseFeedbackCreateResponse.model_validate(feedback_response.json())

        assert ticket_statuses_response.status_code == 200
        TicketStatusListResponse.model_validate(ticket_statuses_response.json())

        assert xero_tenants_response.status_code == 200
        XeroTenantListResponse.model_validate(xero_tenants_response.json())
    finally:
        app.dependency_overrides.clear()
