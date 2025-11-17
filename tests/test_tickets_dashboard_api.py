from collections import Counter
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.api.routes.tickets import require_helpdesk_technician, require_super_admin
import app.main as main_module
from app.main import app
from app.main import automations_service, change_log_service, modules_service, scheduler_service
from app.core.database import db
from app.services import tickets as tickets_service
from app.security.session import SessionData, session_manager


@pytest.fixture(autouse=True)
def _mock_startup(monkeypatch):
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


def test_ticket_dashboard_endpoint(monkeypatch):
    client = TestClient(app)

    sample_state = tickets_service.TicketDashboardState(
        tickets=[
            {
                "id": 41,
                "subject": "Printer offline",
                "status": "open",
                "priority": "high",
                "company_id": 301,
                "assigned_user_id": 22,
                "module_slug": None,
                "requester_id": 12,
                "updated_at": datetime(2024, 5, 1, 9, 30, tzinfo=timezone.utc),
            }
        ],
        total=1,
        status_counts=Counter({"open": 1}),
        available_statuses=["open", "in_progress"],
        status_definitions=[
            tickets_service.TicketStatusDefinition(
                tech_status="open",
                tech_label="Open",
                public_status="Open",
            )
        ],
        modules=[],
        companies=[{"id": 301, "name": "Acme Corp"}],
        technicians=[{"id": 22, "email": "tech@example.com"}],
        company_lookup={301: {"id": 301, "name": "Acme Corp"}},
        user_lookup={22: {"id": 22, "email": "tech@example.com"}},
    )

    load_state_mock = AsyncMock(return_value=sample_state)
    monkeypatch.setattr(tickets_service, "load_dashboard_state", load_state_mock)

    app.dependency_overrides[require_helpdesk_technician] = lambda: {"id": 1, "is_super_admin": True}
    try:
        response = client.get("/api/tickets/dashboard", params={"status": "open", "module": "ops"})
    finally:
        app.dependency_overrides.pop(require_helpdesk_technician, None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["status_counts"] == {"open": 1}
    assert payload["filters"]["status"] == "open"
    assert payload["filters"]["module_slug"] == "ops"
    assert payload["items"][0]["company_name"] == "Acme Corp"
    assert payload["items"][0]["assigned_user_email"] == "tech@example.com"

    load_state_mock.assert_awaited_once()
    kwargs = load_state_mock.await_args.kwargs
    assert kwargs["status_filter"] == "open"
    assert kwargs["module_filter"] == "ops"


def test_list_ticket_statuses_endpoint(monkeypatch):
    client = TestClient(app)

    sample_statuses = [
        tickets_service.TicketStatusDefinition(
            tech_status="open",
            tech_label="Open",
            public_status="Open",
        ),
        tickets_service.TicketStatusDefinition(
            tech_status="waiting_on_customer",
            tech_label="Waiting on customer",
            public_status="Awaiting response",
        ),
    ]

    list_mock = AsyncMock(return_value=sample_statuses)
    monkeypatch.setattr(tickets_service, "list_status_definitions", list_mock)

    app.dependency_overrides[require_helpdesk_technician] = lambda: {"id": 7, "is_super_admin": False}
    try:
        response = client.get("/api/tickets/statuses")
    finally:
        app.dependency_overrides.pop(require_helpdesk_technician, None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["statuses"][0]["techStatus"] == "open"
    assert payload["statuses"][1]["publicStatus"] == "Awaiting response"
    list_mock.assert_awaited_once()


def test_replace_ticket_statuses_endpoint_handles_errors(monkeypatch):
    replace_mock = AsyncMock(side_effect=ValueError("Tech status values must be unique."))
    monkeypatch.setattr(tickets_service, "replace_ticket_statuses", replace_mock)

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

    app.dependency_overrides[require_super_admin] = lambda: {"id": 1, "is_super_admin": True}
    try:
        with TestClient(app) as client:
            client.cookies.set("myportal_session_csrf", session.csrf_token)
            response = client.put(
                "/api/tickets/statuses",
                json={
                    "statuses": [
                        {"techLabel": "Pending", "publicStatus": "Pending"},
                        {"techLabel": "Pending", "publicStatus": "Pending"},
                    ]
                },
                headers={"X-CSRF-Token": session.csrf_token},
            )
    finally:
        app.dependency_overrides.pop(require_super_admin, None)

    assert response.status_code == 400
    assert response.json()["detail"] == "Tech status values must be unique."
    replace_mock.assert_awaited_once()

def test_build_ticket_status_payloads_marks_new_default():
    payloads = main_module._build_ticket_status_payloads(
        ["Awaiting reply"],
        ["Awaiting reply"],
        [""],
        "awaiting_reply",
    )

    assert payloads
    assert payloads[0]["isDefault"] is True
