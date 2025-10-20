import pytest

from app.services import tickets as tickets_service


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_refresh_ticket_ai_summary_updates_summary(monkeypatch):
    captured = {}

    async def fake_get_ticket(ticket_id):
        return {
            "id": ticket_id,
            "subject": "Printer issue",
            "description": "The printer is jammed",
            "status": "open",
            "priority": "high",
            "requester_id": 7,
            "assigned_user_id": None,
        }

    async def fake_list_replies(ticket_id, include_internal=True):
        return [
            {
                "ticket_id": ticket_id,
                "author_id": 12,
                "body": "We replaced the toner and it works now.",
                "is_internal": False,
                "created_at": None,
            }
        ]

    async def fake_get_user(user_id):
        return {"id": user_id, "email": f"user{user_id}@example.test"}

    async def fake_trigger(slug, payload):
        assert slug == "ollama"
        assert "Printer issue" in payload.get("prompt", "")
        return {
            "status": "succeeded",
            "model": "llama3",
            "response": '{"summary": "Printer fixed after toner replacement.", "resolution": "Likely Resolved"}',
        }

    async def fake_update(ticket_id, **fields):
        captured.update(fields)

    monkeypatch.setattr(tickets_service.tickets_repo, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(tickets_service.tickets_repo, "list_replies", fake_list_replies)
    monkeypatch.setattr(tickets_service.tickets_repo, "update_ticket", fake_update)
    monkeypatch.setattr(tickets_service.user_repo, "get_user_by_id", fake_get_user)
    monkeypatch.setattr(tickets_service.modules_service, "trigger_module", fake_trigger)

    await tickets_service.refresh_ticket_ai_summary(5)

    assert captured["ai_summary"] == "Printer fixed after toner replacement."
    assert captured["ai_resolution_state"] == "likely_resolved"
    assert captured["ai_summary_status"] == "succeeded"
    assert captured["ai_summary_model"] == "llama3"
    assert captured["ai_summary_updated_at"] is not None


@pytest.mark.anyio
async def test_refresh_ticket_ai_summary_handles_missing_module(monkeypatch):
    captured = {}

    async def fake_get_ticket(ticket_id):
        return {"id": ticket_id, "subject": "Issue", "description": "", "status": "open", "priority": "normal"}

    async def fake_update(ticket_id, **fields):
        captured.update(fields)

    async def fake_list_replies(*args, **kwargs):
        return []

    async def fake_get_user(_):
        return None

    monkeypatch.setattr(tickets_service.tickets_repo, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(tickets_service.tickets_repo, "list_replies", fake_list_replies)
    monkeypatch.setattr(tickets_service.tickets_repo, "update_ticket", fake_update)
    monkeypatch.setattr(tickets_service.user_repo, "get_user_by_id", fake_get_user)

    async def fake_trigger(slug, payload):
        raise ValueError("module not configured")

    monkeypatch.setattr(tickets_service.modules_service, "trigger_module", fake_trigger)

    await tickets_service.refresh_ticket_ai_summary(9)

    assert captured["ai_summary_status"] == "skipped"
    assert captured["ai_summary"] is None
    assert captured["ai_resolution_state"] is None


@pytest.mark.anyio
async def test_refresh_ticket_ai_summary_handles_errors(monkeypatch):
    captured = {}

    async def fake_get_ticket(ticket_id):
        return {"id": ticket_id, "subject": "Issue", "description": "", "status": "open", "priority": "normal"}

    async def fake_update(ticket_id, **fields):
        captured.update(fields)

    async def fake_list_replies(*args, **kwargs):
        return []

    async def fake_get_user(_):
        return None

    monkeypatch.setattr(tickets_service.tickets_repo, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(tickets_service.tickets_repo, "list_replies", fake_list_replies)
    monkeypatch.setattr(tickets_service.tickets_repo, "update_ticket", fake_update)
    monkeypatch.setattr(tickets_service.user_repo, "get_user_by_id", fake_get_user)

    async def fake_trigger(slug, payload):
        raise RuntimeError("network error")

    monkeypatch.setattr(tickets_service.modules_service, "trigger_module", fake_trigger)

    await tickets_service.refresh_ticket_ai_summary(11)

    assert captured["ai_summary_status"] == "error"
    assert captured["ai_summary"] is None
    assert captured["ai_resolution_state"] is None
