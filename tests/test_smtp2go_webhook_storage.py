"""Tests for storing SMTP2Go webhook events without ticket reply linkage."""

from datetime import datetime

import pytest


@pytest.mark.asyncio
async def test_process_webhook_event_records_without_ticket_reply(monkeypatch):
    """Ensure webhook events are saved even when no ticket reply matches."""
    from app.services import smtp2go

    class DummyDB:
        def __init__(self):
            self.fetch_one_calls = []
            self.execute_calls = []

        async def fetch_one(self, query, params):
            self.fetch_one_calls.append({"query": query, "params": params})
            return None  # Simulate missing ticket reply

        async def execute(self, query, params):
            self.execute_calls.append({"query": query, "params": params})
            return len(self.execute_calls)

    db_stub = DummyDB()
    monkeypatch.setattr(smtp2go, "db", db_stub)

    event_data = {
        "email_id": "missing-message-id",
        "event": "delivered",
        "recipient": "user@example.com",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "user_agent": "pytest",
        "ip": "203.0.113.10",
    }

    result = await smtp2go.process_webhook_event("delivered", event_data)

    # Should create an email_tracking_events row with the fallback tracking ID
    assert result is not None
    assert result["tracking_id"] == "missing-message-id"
    assert len(db_stub.execute_calls) == 1

    insert_params = db_stub.execute_calls[0]["params"]
    assert insert_params["tracking_id"] == "missing-message-id"
    assert insert_params["event_type"] == "delivered"
    assert insert_params["smtp2go_data"] is not None


@pytest.mark.asyncio
async def test_process_webhook_event_uses_message_id_when_email_id_missing(monkeypatch):
    """Ensure webhook processing uses Message-Id when email_id is absent."""
    from app.services import smtp2go

    class DummyDB:
        def __init__(self):
            self.fetch_one_calls = []
            self.execute_calls = []

        async def fetch_one(self, query, params):
            self.fetch_one_calls.append({"query": query, "params": params})
            return None  # No matching ticket reply

        async def execute(self, query, params):
            self.execute_calls.append({"query": query, "params": params})
            return len(self.execute_calls)

    db_stub = DummyDB()
    monkeypatch.setattr(smtp2go, "db", db_stub)

    event_data = {
        "Message-Id": "<mail.1392590358.22776@example.org>",
        "event": "bounce",
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }

    result = await smtp2go.process_webhook_event("bounce", event_data)

    assert result is not None
    assert result["tracking_id"] == "<mail.1392590358.22776@example.org>"
    assert db_stub.execute_calls, "Expected insert to be executed"

