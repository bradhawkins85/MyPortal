import asyncio
from unittest.mock import AsyncMock

import pytest

from app.services import tickets as tickets_service


def _ticket(**overrides):
    ticket = {
        "id": 9,
        "subject": "Printer offline",
        "description": "Printer is offline",
        "status": "resolved",
        "resolution_steps": "<ul><li>Technician-curated step</li></ul>",
        "resolution_steps_source": "manually_edited",
    }
    ticket.update(overrides)
    return ticket


def _patch(monkeypatch, ticket, trigger):
    update = AsyncMock()
    monkeypatch.setattr(tickets_service.tickets_repo, "get_ticket", AsyncMock(return_value=ticket))
    monkeypatch.setattr(tickets_service.tickets_repo, "list_replies", AsyncMock(return_value=[]))
    monkeypatch.setattr(tickets_service.tickets_repo, "update_ticket", update)
    monkeypatch.setattr(tickets_service.modules_service, "trigger_module", trigger)
    return update


def test_automatic_refresh_preserves_technician_edited_steps(monkeypatch):
    trigger = AsyncMock()
    update = _patch(monkeypatch, _ticket(), trigger)

    asyncio.run(tickets_service.refresh_ticket_resolution_steps(9))

    trigger.assert_not_awaited()
    update.assert_not_awaited()


def test_forced_refresh_regenerates_technician_edited_steps(monkeypatch):
    trigger = AsyncMock(return_value={"status": "queued"})
    _patch(monkeypatch, _ticket(), trigger)

    asyncio.run(tickets_service.refresh_ticket_resolution_steps(9, force=True))

    trigger.assert_awaited_once()


@pytest.mark.parametrize("source", [None, "ai_generated"])
def test_automatic_refresh_still_generates_ai_steps(monkeypatch, source):
    trigger = AsyncMock(return_value={"status": "queued"})
    _patch(monkeypatch, _ticket(resolution_steps_source=source), trigger)

    asyncio.run(tickets_service.refresh_ticket_resolution_steps(9))

    trigger.assert_awaited_once()


def test_unconfigured_module_marks_resolution_steps_skipped(monkeypatch):
    trigger = AsyncMock(side_effect=ValueError("Module ollama is not configured"))
    update = _patch(monkeypatch, _ticket(resolution_steps_source=None), trigger)

    asyncio.run(tickets_service.refresh_ticket_resolution_steps(9))

    assert update.await_args_list[-1].kwargs["resolution_steps_status"] == "skipped"
