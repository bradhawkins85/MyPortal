import asyncio
from typing import Any

import pytest

from app.services import tickets as tickets_service


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _stub_emit_ticket_updates(monkeypatch):
    async def fake_emit_event(*args, **kwargs):
        return None

    monkeypatch.setattr(tickets_service, "emit_ticket_updated_event", fake_emit_event)


def _install_ticket_fakes(monkeypatch, *, response: Any, prompts: list[str], updates: list[dict[str, Any]]):
    async def fake_get_ticket(ticket_id):
        return {
            "id": ticket_id,
            "subject": "Printer issue in main office",
            "description": "The level 2 printer shows a paper jam error.",
            "status": "open",
            "priority": "high",
            "category": "Hardware",
            "module_slug": "support",
            "requester_id": 7,
            "assigned_user_id": 12,
        }

    async def fake_list_replies(ticket_id, include_internal=True):
        return [
            {"id": 1, "author_id": 7, "body": "Still jammed after a restart.", "is_internal": False},
            {"id": 2, "author_id": 12, "body": "Replaced the fuser unit.", "is_internal": True},
        ]

    async def fake_get_user(user_id):
        return {"id": user_id, "email": f"user{user_id}@example.test"}

    async def fake_trigger(slug, payload, *, background=True, on_complete=None):
        assert slug == "ollama"
        prompts.append(payload["prompt"])
        result = {"status": "succeeded", "model": "llama3", "response": response}
        if on_complete:
            await on_complete(result)
        return result

    async def fake_update(ticket_id, **fields):
        updates.append(fields)

    async def fake_get_excluded_tags():
        return set()

    monkeypatch.setattr(tickets_service.tickets_repo, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(tickets_service.tickets_repo, "list_replies", fake_list_replies)
    monkeypatch.setattr(tickets_service.tickets_repo, "update_ticket", fake_update)
    monkeypatch.setattr(tickets_service.user_repo, "get_user_by_id", fake_get_user)
    monkeypatch.setattr(tickets_service.modules_service, "trigger_module", fake_trigger)
    monkeypatch.setattr("app.services.tickets.get_all_excluded_tags", fake_get_excluded_tags)


@pytest.mark.anyio
async def test_refresh_ticket_ai_insights_stores_summary_and_tags_from_one_call(monkeypatch):
    prompts: list[str] = []
    updates: list[dict[str, Any]] = []
    _install_ticket_fakes(
        monkeypatch,
        response=(
            '```json\n{"summary": "Printer jam fixed by fuser swap.", '
            '"resolution": "Likely Resolved", "tags": ["Printer", "Paper Jam", "fuser"]}\n```'
        ),
        prompts=prompts,
        updates=updates,
    )

    await tickets_service.refresh_ticket_ai_insights(5)

    assert len(prompts) == 1
    assert '"tags"' in prompts[0] and '"summary"' in prompts[0]
    assert updates[0]["ai_summary_status"] == "queued"
    assert updates[0]["ai_tags_status"] == "queued"
    final = updates[-1]
    assert final["ai_summary"] == "Printer jam fixed by fuser swap."
    assert final["ai_resolution_state"] == "likely_resolved"
    assert final["ai_summary_status"] == "succeeded"
    assert final["ai_tags"] == ["printer", "paper-jam", "fuser"]
    assert final["ai_tags_status"] == "succeeded"
    assert final["ai_tags_model"] == "llama3"


@pytest.mark.anyio
async def test_insights_prompt_marks_only_customer_messages_as_tag_evidence(monkeypatch):
    prompts: list[str] = []
    _install_ticket_fakes(
        monkeypatch,
        response='{"summary": "s", "resolution": "Likely In Progress", "tags": []}',
        prompts=prompts,
        updates=[],
    )

    await tickets_service.refresh_ticket_ai_insights(5)

    prompt = prompts[0]
    assert "customer message by user7@example.test" in prompt
    assert "helpdesk internal note by user12@example.test" in prompt
    assert "never as tag evidence" in prompt


@pytest.mark.anyio
async def test_refresh_ticket_ai_insights_marks_both_skipped_without_module(monkeypatch):
    updates: list[dict[str, Any]] = []
    _install_ticket_fakes(monkeypatch, response="{}", prompts=[], updates=updates)

    async def missing_module(*args, **kwargs):
        raise ValueError("module not configured")

    monkeypatch.setattr(tickets_service.modules_service, "trigger_module", missing_module)

    await tickets_service.refresh_ticket_ai_insights(5)

    assert updates[-1]["ai_summary_status"] == "skipped"
    assert updates[-1]["ai_tags_status"] == "skipped"


@pytest.mark.anyio
async def test_schedule_ticket_ai_refresh_coalesces_bursts(monkeypatch):
    calls: list[int] = []

    async def fake_refresh(ticket_id):
        calls.append(ticket_id)

    monkeypatch.setattr(tickets_service, "refresh_ticket_ai_insights", fake_refresh)

    for _ in range(5):
        tickets_service.schedule_ticket_ai_refresh(9, delay=0.05)
        await asyncio.sleep(0.01)
    tickets_service.schedule_ticket_ai_refresh(10, delay=0.05)

    await asyncio.sleep(0.2)

    assert sorted(calls) == [9, 10]
    assert 9 not in tickets_service._pending_ai_refreshes


@pytest.mark.anyio
async def test_schedule_ticket_ai_refresh_uses_configured_delay(monkeypatch):
    calls: list[int] = []

    async def fake_refresh(ticket_id):
        calls.append(ticket_id)

    monkeypatch.setattr(tickets_service, "refresh_ticket_ai_insights", fake_refresh)
    monkeypatch.setattr(tickets_service, "_ai_refresh_debounce_seconds", lambda: 0.0)

    tickets_service.schedule_ticket_ai_refresh(11)
    await asyncio.sleep(0.05)

    assert calls == [11]


@pytest.mark.anyio
async def test_cancel_pending_ticket_ai_refresh_drops_scheduled_run(monkeypatch):
    calls: list[int] = []

    async def fake_refresh(ticket_id):
        calls.append(ticket_id)

    monkeypatch.setattr(tickets_service, "refresh_ticket_ai_insights", fake_refresh)

    tickets_service.schedule_ticket_ai_refresh(12, delay=0.05)
    tickets_service.cancel_pending_ticket_ai_refresh(12)
    await asyncio.sleep(0.1)

    assert calls == []


def test_schedule_ticket_ai_refresh_without_event_loop_is_noop():
    tickets_service.schedule_ticket_ai_refresh(13, delay=0)

    assert 13 not in tickets_service._pending_ai_refreshes
