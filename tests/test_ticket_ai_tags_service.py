from typing import Any

import pytest

from app.services import tickets as tickets_service


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_refresh_ticket_ai_tags_updates_tags(monkeypatch):
    updates: list[dict[str, Any]] = []

    async def fake_get_ticket(ticket_id):
        return {
            "id": ticket_id,
            "subject": "Printer issue in main office",
            "description": "The printer on level 2 shows a paper jam error after toner replacement.",
            "status": "open",
            "priority": "high",
            "category": "Hardware",
            "module_slug": "support",
            "requester_id": 7,
            "assigned_user_id": None,
        }

    async def fake_list_replies(ticket_id, include_internal=True):
        return [
            {
                "ticket_id": ticket_id,
                "author_id": 12,
                "body": "Technician removed jammed paper and reloaded tray.",
                "is_internal": False,
                "created_at": None,
            }
        ]

    async def fake_get_user(user_id):
        return {"id": user_id, "email": f"user{user_id}@example.test"}

    async def fake_trigger(slug, payload, *, background=True, on_complete=None):
        assert slug == "ollama"
        assert "Printer issue" in payload.get("prompt", "")
        result = {
            "status": "succeeded",
            "model": "llama3",
            "response": '{"tags": ["Printer", "Paper Jam"]}',
        }
        if on_complete:
            await on_complete(result)
        return result

    async def fake_update(ticket_id, **fields):
        updates.append(fields)

    monkeypatch.setattr(tickets_service.tickets_repo, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(tickets_service.tickets_repo, "list_replies", fake_list_replies)
    monkeypatch.setattr(tickets_service.tickets_repo, "update_ticket", fake_update)
    monkeypatch.setattr(tickets_service.user_repo, "get_user_by_id", fake_get_user)
    monkeypatch.setattr(tickets_service.modules_service, "trigger_module", fake_trigger)

    await tickets_service.refresh_ticket_ai_tags(5)

    assert len(updates) >= 2
    assert updates[0]["ai_tags_status"] == "queued"
    final_update = updates[-1]
    assert final_update["ai_tags_status"] == "succeeded"
    assert final_update["ai_tags_model"] == "llama3"
    assert final_update["ai_tags_updated_at"] is not None
    assert isinstance(final_update["ai_tags"], list)
    assert 5 <= len(final_update["ai_tags"]) <= 10
    assert "printer" in final_update["ai_tags"]
    assert "paper-jam" in final_update["ai_tags"]


@pytest.mark.anyio
async def test_refresh_ticket_ai_tags_handles_missing_module(monkeypatch):
    updates: list[dict[str, Any]] = []

    async def fake_get_ticket(ticket_id):
        return {"id": ticket_id, "subject": "Issue", "description": "", "status": "open", "priority": "normal"}

    async def fake_list_replies(*args, **kwargs):
        return []

    async def fake_get_user(_):
        return None

    async def fake_update(ticket_id, **fields):
        updates.append(fields)

    async def fake_trigger(slug, payload, *, background=True, on_complete=None):
        raise ValueError("module not configured")

    monkeypatch.setattr(tickets_service.tickets_repo, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(tickets_service.tickets_repo, "list_replies", fake_list_replies)
    monkeypatch.setattr(tickets_service.tickets_repo, "update_ticket", fake_update)
    monkeypatch.setattr(tickets_service.user_repo, "get_user_by_id", fake_get_user)
    monkeypatch.setattr(tickets_service.modules_service, "trigger_module", fake_trigger)

    await tickets_service.refresh_ticket_ai_tags(9)

    assert updates
    assert updates[-1]["ai_tags_status"] == "skipped"
    assert updates[-1]["ai_tags"] is None
    assert updates[-1]["ai_tags_model"] is None


@pytest.mark.anyio
async def test_refresh_ticket_ai_tags_handles_errors(monkeypatch):
    updates: list[dict[str, Any]] = []

    async def fake_get_ticket(ticket_id):
        return {"id": ticket_id, "subject": "Issue", "description": "", "status": "open", "priority": "normal"}

    async def fake_list_replies(*args, **kwargs):
        return []

    async def fake_get_user(_):
        return None

    async def fake_update(ticket_id, **fields):
        updates.append(fields)

    async def fake_trigger(slug, payload, *, background=True, on_complete=None):
        raise RuntimeError("network error")

    monkeypatch.setattr(tickets_service.tickets_repo, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(tickets_service.tickets_repo, "list_replies", fake_list_replies)
    monkeypatch.setattr(tickets_service.tickets_repo, "update_ticket", fake_update)
    monkeypatch.setattr(tickets_service.user_repo, "get_user_by_id", fake_get_user)
    monkeypatch.setattr(tickets_service.modules_service, "trigger_module", fake_trigger)

    await tickets_service.refresh_ticket_ai_tags(11)

    assert updates
    assert updates[-1]["ai_tags_status"] == "error"
    assert updates[-1]["ai_tags"] is None
    assert updates[-1]["ai_tags_model"] is None
