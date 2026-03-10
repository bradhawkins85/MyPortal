"""Tests ensuring that creating a ticket from a call recording triggers automations.

Previously ``create_ticket_from_recording`` called ``tickets_repo.create_ticket``
directly, bypassing the service layer.  This meant the ``tickets.created``
automation event was never fired.  The fix routes the call through
``tickets_service.create_ticket`` so the automation handler runs normally.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from app.services import call_recordings as call_recordings_service


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_create_ticket_from_recording_uses_service_layer(monkeypatch):
    """create_ticket_from_recording must call tickets_service.create_ticket,
    not tickets_repo.create_ticket, so the tickets.created automation fires."""
    from app.repositories import call_recordings as call_recordings_repo
    from app.repositories import tickets as tickets_repo
    from app.services import tickets as tickets_service

    recording = {
        "id": 1,
        "phone_number": "+61412345678",
        "caller_first_name": "Alice",
        "caller_last_name": "Smith",
        "callee_first_name": None,
        "callee_last_name": None,
        "call_date": datetime(2025, 3, 10, 8, 0, 0, tzinfo=timezone.utc),
        "duration_seconds": 180,
        "transcription": "Customer called about invoice #42. Resolved.",
    }

    async def fake_get_recording(recording_id: int) -> dict[str, Any]:
        assert recording_id == 1
        return recording

    async def fake_summarize(transcription: str) -> str:
        return "Invoice inquiry resolved"

    service_create_called: list[dict[str, Any]] = []

    async def fake_service_create_ticket(**kwargs: Any) -> dict[str, Any]:
        service_create_called.append(dict(kwargs))
        return {"id": 99, "ticket_number": "TKT-99", **kwargs}

    repo_create_called: list[Any] = []

    async def fake_repo_create_ticket(**kwargs: Any) -> dict[str, Any]:  # pragma: no cover
        repo_create_called.append(kwargs)
        return {"id": 999, **kwargs}

    async def fake_link_recording(recording_id: int, ticket_id: int) -> None:
        pass

    async def fake_create_reply(**kwargs: Any) -> dict[str, Any]:
        return {"id": 1, **kwargs}

    monkeypatch.setattr(call_recordings_repo, "get_call_recording_by_id", fake_get_recording)
    monkeypatch.setattr(call_recordings_service, "summarize_transcription", fake_summarize)
    monkeypatch.setattr(tickets_service, "create_ticket", fake_service_create_ticket)
    monkeypatch.setattr(tickets_repo, "create_ticket", fake_repo_create_ticket)
    monkeypatch.setattr(call_recordings_repo, "link_recording_to_ticket", fake_link_recording)
    monkeypatch.setattr(tickets_repo, "create_reply", fake_create_reply)

    result = await call_recordings_service.create_ticket_from_recording(
        recording_id=1,
        company_id=10,
        user_id=5,
    )

    # tickets_service.create_ticket must have been called (service layer)
    assert len(service_create_called) == 1, "tickets_service.create_ticket was not called"
    call_kwargs = service_create_called[0]
    assert call_kwargs["company_id"] == 10
    assert call_kwargs["requester_id"] == 5
    assert call_kwargs["module_slug"] == "call-recordings"

    # tickets_repo.create_ticket must NOT have been called directly (bypass guard)
    assert len(repo_create_called) == 0, (
        "tickets_repo.create_ticket was called directly, bypassing the service layer"
    )

    assert result["id"] == 99


@pytest.mark.anyio
async def test_create_ticket_from_recording_automation_event_fires(monkeypatch):
    """The tickets.created automation event must fire when a ticket is created from
    a call recording."""
    from app.repositories import call_recordings as call_recordings_repo
    from app.repositories import tickets as tickets_repo
    from app.services import automations as automations_service
    from app.services import tickets as tickets_service

    recording = {
        "id": 2,
        "phone_number": "+61498765432",
        "caller_first_name": None,
        "caller_last_name": None,
        "callee_first_name": "Bob",
        "callee_last_name": "Jones",
        "call_date": datetime(2025, 3, 11, 9, 30, 0, tzinfo=timezone.utc),
        "duration_seconds": 240,
        "transcription": "Support call about network outage. Ticket raised.",
    }

    async def fake_get_recording(recording_id: int) -> dict[str, Any]:
        return recording

    async def fake_summarize(transcription: str) -> str:
        return "Network outage ticket"

    # Track automation handle_event calls
    automation_events: list[str] = []

    async def fake_handle_event(event_name: str, context: Any) -> list[Any]:
        automation_events.append(event_name)
        return []

    # Minimal stubs for the tickets_service.create_ticket dependencies
    async def fake_repo_create_ticket(**kwargs: Any) -> dict[str, Any]:
        ticket_id = 77
        return {"id": ticket_id, **kwargs}

    async def fake_get_company(company_id: int) -> dict[str, Any] | None:
        return None

    async def fake_get_user(user_id: int) -> dict[str, Any] | None:
        return {"id": user_id, "email": "user@example.com", "first_name": "Bob", "last_name": "Jones"}

    async def fake_resolve_status(value: Any) -> str:
        return value or "open"

    async def fake_send_creation_email(enriched_ticket: Any, *, requester_email_fallback: Any = None) -> None:
        pass

    async def fake_link_recording(recording_id: int, ticket_id: int) -> None:
        pass

    async def fake_create_reply(**kwargs: Any) -> dict[str, Any]:
        return {"id": 1, **kwargs}

    monkeypatch.setattr(call_recordings_repo, "get_call_recording_by_id", fake_get_recording)
    monkeypatch.setattr(call_recordings_service, "summarize_transcription", fake_summarize)
    monkeypatch.setattr(automations_service, "handle_event", fake_handle_event)
    monkeypatch.setattr(tickets_repo, "create_ticket", fake_repo_create_ticket)
    monkeypatch.setattr(tickets_service.company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(tickets_service.user_repo, "get_user_by_id", fake_get_user)
    monkeypatch.setattr(tickets_service, "resolve_status_or_default", fake_resolve_status)
    monkeypatch.setattr(tickets_service, "_send_ticket_creation_email", fake_send_creation_email)
    monkeypatch.setattr(call_recordings_repo, "link_recording_to_ticket", fake_link_recording)
    monkeypatch.setattr(tickets_repo, "create_reply", fake_create_reply)

    await call_recordings_service.create_ticket_from_recording(
        recording_id=2,
        company_id=20,
        user_id=7,
    )

    assert "tickets.created" in automation_events, (
        "tickets.created automation event was not fired when creating a ticket from a recording"
    )
