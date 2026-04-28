"""Tests for the per-recipient email delivery API endpoint."""

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.api.dependencies import auth as auth_dependencies
from app.core.database import db
from app.main import app, scheduler_service
from app.repositories import tickets as tickets_repo
from app.services import email_recipients


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
    monkeypatch.setattr(main_module.settings, "enable_csrf", False)


def _override_user(user):
    app.dependency_overrides[auth_dependencies.get_current_user] = lambda: user


def _clear_override():
    app.dependency_overrides.clear()


def test_endpoint_requires_authentication():
    with TestClient(app) as client:
        response = client.get(
            "/api/email-tracking/replies/123/recipients",
            headers={"Accept": "application/json"},
        )
    assert response.status_code == 401


def test_endpoint_returns_recipient_payload(monkeypatch):
    """A super admin can fetch recipients with computed status precedence."""
    _override_user({"id": 1, "email": "admin@example.com", "is_super_admin": True})

    async def fake_get_reply(reply_id):
        assert reply_id == 123
        return {"id": 123, "ticket_id": 456}

    async def fake_get_ticket(ticket_id):
        assert ticket_id == 456
        return {"id": 456, "requester_id": 999}

    async def fake_get_recipients(reply_id):
        return [
            {
                "id": 1,
                "recipient_email": "alice@example.com",
                "recipient_name": "Alice",
                "recipient_role": "to",
                "email_sent_at": None,
                "email_processed_at": None,
                "email_delivered_at": None,
                # Both opened and bounced — bounced must take precedence.
                "email_opened_at": "2026-04-28T05:00:00+00:00",
                "email_open_count": 2,
                "email_bounced_at": "2026-04-28T05:30:00+00:00",
                "email_rejected_at": None,
                "email_spam_at": None,
                "last_event_at": "2026-04-28T05:30:00+00:00",
                "last_event_type": "bounce",
                "last_event_detail": "550 mailbox full",
            },
            {
                "id": 2,
                "recipient_email": "bob@example.com",
                "recipient_name": None,
                "recipient_role": "cc",
                "email_sent_at": "2026-04-28T05:00:00+00:00",
                "email_processed_at": "2026-04-28T05:00:00+00:00",
                "email_delivered_at": "2026-04-28T05:01:00+00:00",
                "email_opened_at": None,
                "email_open_count": 0,
                "email_bounced_at": None,
                "email_rejected_at": None,
                "email_spam_at": None,
                "last_event_at": "2026-04-28T05:01:00+00:00",
                "last_event_type": "delivered",
                "last_event_detail": None,
            },
        ]

    monkeypatch.setattr(tickets_repo, "get_reply_by_id", fake_get_reply)
    monkeypatch.setattr(tickets_repo, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(email_recipients, "get_recipients_for_reply", fake_get_recipients)

    try:
        with TestClient(app) as client:
            response = client.get("/api/email-tracking/replies/123/recipients")
    finally:
        _clear_override()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reply_id"] == 123
    assert body["ticket_id"] == 456
    assert body["recipient_count"] == 2
    assert len(body["recipients"]) == 2

    by_email = {r["recipient_email"]: r for r in body["recipients"]}
    # Bounced must beat opened in the precedence order.
    assert by_email["alice@example.com"]["status"] == "bounced"
    assert by_email["alice@example.com"]["recipient_role"] == "to"
    assert by_email["alice@example.com"]["open_count"] == 2
    assert by_email["bob@example.com"]["status"] == "delivered"
    assert by_email["bob@example.com"]["recipient_role"] == "cc"


def test_endpoint_enforces_ticket_access(monkeypatch):
    """A non-helpdesk user who is not the requester or watcher must get 404."""
    user = {"id": 555, "email": "nope@example.com", "is_super_admin": False}
    _override_user(user)

    async def fake_get_reply(reply_id):
        return {"id": 123, "ticket_id": 456}

    async def fake_get_ticket(ticket_id):
        return {"id": 456, "requester_id": 999}

    async def fake_has_perm(uid, key):
        return False

    async def fake_is_watcher(ticket_id, user_id):
        return False

    monkeypatch.setattr(tickets_repo, "get_reply_by_id", fake_get_reply)
    monkeypatch.setattr(tickets_repo, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(tickets_repo, "is_ticket_watcher", fake_is_watcher)

    from app.repositories import company_memberships as membership_repo
    monkeypatch.setattr(membership_repo, "user_has_permission", fake_has_perm)

    try:
        with TestClient(app) as client:
            response = client.get("/api/email-tracking/replies/123/recipients")
    finally:
        _clear_override()

    assert response.status_code == 404


def test_endpoint_returns_404_for_unknown_reply(monkeypatch):
    _override_user({"id": 1, "email": "admin@example.com", "is_super_admin": True})

    async def fake_get_reply(reply_id):
        return None

    monkeypatch.setattr(tickets_repo, "get_reply_by_id", fake_get_reply)

    try:
        with TestClient(app) as client:
            response = client.get("/api/email-tracking/replies/999/recipients")
    finally:
        _clear_override()

    assert response.status_code == 404
