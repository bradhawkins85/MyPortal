from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.repositories import access_activity


@pytest.mark.asyncio
async def test_list_session_audit_activity_filters_by_session_window(monkeypatch):
    session_row = {
        "id": 9,
        "user_id": 14,
        "ip_address": "203.0.113.9",
        "created_at": datetime(2026, 9, 15, 1, 0, tzinfo=timezone.utc),
        "last_seen_at": datetime(2026, 9, 15, 2, 0, tzinfo=timezone.utc),
        "expires_at": datetime(2026, 9, 15, 13, 0, tzinfo=timezone.utc),
    }
    captured: dict[str, object] = {}

    async def fake_get_active_user_session(session_id):
        assert session_id == 9
        return session_row

    async def fake_list_audit_logs(**kwargs):
        captured.update(kwargs)
        return [{"id": 1, "action": "ticket.update"}]

    monkeypatch.setattr(access_activity, "get_active_user_session", fake_get_active_user_session)
    monkeypatch.setattr(access_activity.audit_repo, "list_audit_logs", fake_list_audit_logs)

    rows = await access_activity.list_session_audit_activity(9)

    assert rows == [{"id": 1, "action": "ticket.update"}]
    assert captured["user_id"] == 14
    assert captured["ip_address"] == "203.0.113.9"
    assert captured["since"] == session_row["created_at"]
    assert captured["until"] == session_row["last_seen_at"]
