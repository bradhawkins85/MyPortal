"""Tests for notification metadata serialization with datetime objects.

Regression tests ensuring that enriched ticket metadata containing datetime
objects does not prevent email delivery via the notification service.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.repositories.notifications import _serialise_metadata, _json_default
from app.services import notifications as notifications_service
from app.services import email as email_service


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# _serialise_metadata – datetime handling
# ---------------------------------------------------------------------------

def test_serialise_metadata_handles_datetime():
    """datetime objects in metadata are serialised to ISO format strings."""
    now = datetime(2026, 3, 9, 7, 0, 0, tzinfo=timezone.utc)
    metadata = {
        "ticket": {
            "id": 1,
            "subject": "Test",
            "created_at": now,
            "updated_at": now,
        }
    }
    result = _serialise_metadata(metadata)
    assert result is not None
    assert "2026-03-09T07:00:00+00:00" in result


def test_serialise_metadata_handles_none():
    """None metadata is serialised to None without error."""
    assert _serialise_metadata(None) is None


def test_serialise_metadata_handles_plain_dict():
    """Plain dicts with no special types serialise normally."""
    metadata = {"ticket": {"id": 1, "subject": "Hello", "number": "42"}}
    result = _serialise_metadata(metadata)
    assert result is not None
    assert '"subject": "Hello"' in result


def test_json_default_converts_datetime():
    """_json_default converts datetime to ISO string."""
    now = datetime(2026, 1, 15, 12, 30, 0, tzinfo=timezone.utc)
    result = _json_default(now)
    assert result == "2026-01-15T12:30:00+00:00"


def test_json_default_raises_for_unknown_types():
    """_json_default raises TypeError for types it cannot handle."""
    with pytest.raises(TypeError):
        _json_default(object())


# ---------------------------------------------------------------------------
# emit_notification – email is sent even when in-app storage fails
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_emit_notification_sends_email_when_create_notification_fails(monkeypatch):
    """Email delivery proceeds even if in-app notification storage raises an error."""
    from app.services import notification_event_settings

    async def fake_get_event_setting(event_type):
        return {
            "event_type": event_type,
            "display_name": "Ticket created",
            "message_template": "{{ message }}",
            "module_actions": [],
            "allow_channel_in_app": True,
            "allow_channel_email": True,
            "allow_channel_sms": False,
            "default_channel_in_app": True,
            "default_channel_email": True,
            "default_channel_sms": False,
        }

    async def fake_get_preference(user_id, event_type):
        return None

    async def failing_create_notification(**kwargs):
        raise RuntimeError("DB serialization error: Object of type datetime is not JSON serializable")

    async def fake_get_user_by_id(user_id):
        return {"id": user_id, "email": "user@example.com", "first_name": "Test", "last_name": "User"}

    email_sent = []

    async def fake_send_email(*, subject, recipients, html_body, text_body=None, **kwargs):
        email_sent.append({"subject": subject, "recipients": recipients})
        return True, None

    monkeypatch.setattr(notification_event_settings, "get_event_setting", fake_get_event_setting)
    monkeypatch.setattr(
        notifications_service.preferences_repo, "get_preference", fake_get_preference
    )
    monkeypatch.setattr(
        notifications_service.notifications_repo, "create_notification", failing_create_notification
    )
    monkeypatch.setattr(notifications_service.user_repo, "get_user_by_id", fake_get_user_by_id)
    monkeypatch.setattr(email_service, "send_email", fake_send_email)

    await notifications_service.emit_notification(
        event_type="tickets.created",
        user_id=5,
        message="Your ticket has been created",
        metadata={"ticket": {"id": 1, "subject": "Test"}},
    )

    assert len(email_sent) == 1
    assert email_sent[0]["recipients"] == ["user@example.com"]


@pytest.mark.anyio
async def test_emit_notification_sends_email_with_datetime_in_metadata(monkeypatch):
    """Email delivery works when ticket metadata contains datetime objects."""
    from app.services import notification_event_settings

    now = datetime(2026, 3, 9, 7, 0, 0, tzinfo=timezone.utc)

    async def fake_get_event_setting(event_type):
        return {
            "event_type": event_type,
            "display_name": "Ticket created",
            "message_template": "Ticket {{ ticket.ticket_number }} created",
            "module_actions": [],
            "allow_channel_in_app": True,
            "allow_channel_email": True,
            "allow_channel_sms": False,
            "default_channel_in_app": True,
            "default_channel_email": True,
            "default_channel_sms": False,
        }

    async def fake_get_preference(user_id, event_type):
        return None

    notifications_stored = []

    async def fake_create_notification(**kwargs):
        notifications_stored.append(kwargs)

    async def fake_get_user_by_id(user_id):
        return {"id": user_id, "email": "requester@example.com"}

    email_sent = []

    async def fake_send_email(*, subject, recipients, html_body, text_body=None, **kwargs):
        email_sent.append({"subject": subject, "recipients": recipients})
        return True, None

    monkeypatch.setattr(notification_event_settings, "get_event_setting", fake_get_event_setting)
    monkeypatch.setattr(
        notifications_service.preferences_repo, "get_preference", fake_get_preference
    )
    monkeypatch.setattr(
        notifications_service.notifications_repo, "create_notification", fake_create_notification
    )
    monkeypatch.setattr(notifications_service.user_repo, "get_user_by_id", fake_get_user_by_id)
    monkeypatch.setattr(email_service, "send_email", fake_send_email)

    enriched_ticket = {
        "id": 10,
        "ticket_number": "100",
        "subject": "Server offline",
        "requester_id": 5,
        "created_at": now,
        "updated_at": now,
        "closed_at": None,
    }

    await notifications_service.emit_notification(
        event_type="tickets.created",
        user_id=5,
        metadata={"ticket": enriched_ticket},
    )

    assert len(email_sent) == 1
    assert email_sent[0]["recipients"] == ["requester@example.com"]
    assert len(notifications_stored) == 1
