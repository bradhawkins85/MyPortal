from typing import Any

import pytest

from app.services import tickets as tickets_service


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_refresh_ticket_ai_summary_updates_summary(monkeypatch):
    updates: list[dict[str, Any]] = []

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

    async def fake_trigger(slug, payload, *, background=True, on_complete=None):
        assert slug == "ollama"
        assert "Printer issue" in payload.get("prompt", "")
        result = {
            "status": "succeeded",
            "model": "llama3",
            "response": '{"summary": "Printer fixed after toner replacement.", "resolution": "Likely Resolved"}',
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

    await tickets_service.refresh_ticket_ai_summary(5)

    assert len(updates) >= 2
    assert updates[0]["ai_summary_status"] == "queued"
    final_update = updates[-1]
    assert final_update["ai_summary"] == "Printer fixed after toner replacement."
    assert final_update["ai_resolution_state"] == "likely_resolved"
    assert final_update["ai_summary_status"] == "succeeded"
    assert final_update["ai_summary_model"] == "llama3"
    assert final_update["ai_summary_updated_at"] is not None


@pytest.mark.anyio
async def test_refresh_ticket_ai_summary_handles_missing_module(monkeypatch):
    updates: list[dict[str, Any]] = []

    async def fake_get_ticket(ticket_id):
        return {"id": ticket_id, "subject": "Issue", "description": "", "status": "open", "priority": "normal"}

    async def fake_update(ticket_id, **fields):
        updates.append(fields)

    async def fake_list_replies(*args, **kwargs):
        return []

    async def fake_get_user(_):
        return None

    monkeypatch.setattr(tickets_service.tickets_repo, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(tickets_service.tickets_repo, "list_replies", fake_list_replies)
    monkeypatch.setattr(tickets_service.tickets_repo, "update_ticket", fake_update)
    monkeypatch.setattr(tickets_service.user_repo, "get_user_by_id", fake_get_user)

    async def fake_trigger(slug, payload, *, background=True, on_complete=None):
        raise ValueError("module not configured")

    monkeypatch.setattr(tickets_service.modules_service, "trigger_module", fake_trigger)

    await tickets_service.refresh_ticket_ai_summary(9)

    assert updates
    assert updates[-1]["ai_summary_status"] == "skipped"
    assert updates[-1]["ai_summary"] is None
    assert updates[-1]["ai_resolution_state"] is None


@pytest.mark.anyio
async def test_refresh_ticket_ai_summary_handles_errors(monkeypatch):
    updates: list[dict[str, Any]] = []

    async def fake_get_ticket(ticket_id):
        return {"id": ticket_id, "subject": "Issue", "description": "", "status": "open", "priority": "normal"}

    async def fake_update(ticket_id, **fields):
        updates.append(fields)

    async def fake_list_replies(*args, **kwargs):
        return []

    async def fake_get_user(_):
        return None

    monkeypatch.setattr(tickets_service.tickets_repo, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(tickets_service.tickets_repo, "list_replies", fake_list_replies)
    monkeypatch.setattr(tickets_service.tickets_repo, "update_ticket", fake_update)
    monkeypatch.setattr(tickets_service.user_repo, "get_user_by_id", fake_get_user)

    async def fake_trigger(slug, payload, *, background=True, on_complete=None):
        raise RuntimeError("network error")

    monkeypatch.setattr(tickets_service.modules_service, "trigger_module", fake_trigger)

    await tickets_service.refresh_ticket_ai_summary(11)

    assert updates
    assert updates[-1]["ai_summary_status"] == "error"
    assert updates[-1]["ai_summary"] is None
    assert updates[-1]["ai_resolution_state"] is None


def test_extract_summary_fields_from_markdown_block():
    payload = "```json\n{\"summary\": \"Issue resolved\", \"resolution\": \"Likely Resolved\"}\n```"

    summary, resolution = tickets_service._extract_summary_fields(payload)

    assert summary == "Issue resolved"
    assert resolution == "Likely Resolved"


def test_extract_summary_fields_from_triple_quoted_block():
    payload = '"""json\n{"summary": "Still working", "resolution": "Likely In Progress"}\n"""'

    summary, resolution = tickets_service._extract_summary_fields(payload)

    assert summary == "Still working"
    assert resolution == "Likely In Progress"


def test_render_prompt_ignores_signatures_and_reply_markers():
    ticket = {
        "id": 1,
        "subject": "Email outage",
        "description": (
            "Users cannot send emails.\n"
            "--- Reply ABOVE THIS LINE to add a comment ---\n"
            "Thanks,\n"
            "Admin Team\n"
            "CONFIDENTIALITY NOTICE: This message may contain information"
        ),
        "status": "open",
        "priority": "high",
    }
    replies = [
        {
            "ticket_id": 1,
            "author_id": 4,
            "body": (
                "Investigating SMTP queue delay.\n"
                "Sent from my iPhone\n"
                "--- Reply ABOVE THIS LINE to add a comment ---\n"
                "Unauthorized viewing prohibited."
            ),
            "is_internal": False,
            "created_at": None,
        }
    ]

    prompt = tickets_service._render_prompt(ticket, replies, {})

    assert "Investigating SMTP queue delay." in prompt
    assert "Users cannot send emails." in prompt
    assert "Reply ABOVE THIS LINE" not in prompt
    assert "Sent from my iPhone" not in prompt
    assert "CONFIDENTIALITY NOTICE" not in prompt
    assert "Unauthorized viewing prohibited." not in prompt
