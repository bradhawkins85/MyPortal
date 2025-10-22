import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.services import automations as automations_service
from app.services.automations import calculate_next_run


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_calculate_next_run_hourly_cadence():
    reference = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    automation = {"kind": "scheduled", "cadence": "hourly"}
    next_run = calculate_next_run(automation, reference=reference)
    assert next_run == reference + timedelta(hours=1)


def test_calculate_next_run_cron_expression():
    reference = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    automation = {"kind": "scheduled", "cron_expression": "*/15 * * * *"}
    next_run = calculate_next_run(automation, reference=reference)
    assert next_run == reference + timedelta(minutes=15)


def test_calculate_next_run_for_event_automation():
    reference = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    automation = {"kind": "event"}
    assert calculate_next_run(automation, reference=reference) is None


@pytest.mark.anyio
async def test_handle_event_executes_matching_automations(monkeypatch):
    contexts: list[tuple[int, dict[str, object] | None]] = []

    async def fake_list_event_automations(trigger_event: str, *, limit: int | None = None):
        assert trigger_event == "tickets.created"
        assert limit is None
        return [
            {"id": 1, "trigger_filters": {"match": {"ticket.status": "open"}}},
            {"id": 2, "trigger_filters": {"match": {"ticket.status": "closed"}}},
        ]

    async def fake_execute(automation, *, context=None):
        contexts.append((int(automation["id"]), context))
        now = datetime.now(timezone.utc)
        return {
            "status": "succeeded",
            "result": None,
            "error": None,
            "started_at": now,
            "finished_at": now,
            "next_run_at": None,
        }

    scheduled_tasks: list[tuple[int, asyncio.Task[dict[str, object]]]] = []

    def fake_schedule(coro, *, automation_id: int):
        task = asyncio.create_task(coro)
        scheduled_tasks.append((automation_id, task))
        return task

    monkeypatch.setattr(
        automations_service.automation_repo,
        "list_event_automations",
        fake_list_event_automations,
    )
    monkeypatch.setattr(
        automations_service,
        "_execute_automation",
        fake_execute,
    )
    monkeypatch.setattr(
        automations_service,
        "_schedule_background_execution",
        fake_schedule,
    )

    results = await automations_service.handle_event(
        "tickets.created",
        {"ticket": {"status": "open"}},
    )

    await asyncio.gather(*(task for _, task in scheduled_tasks))

    assert contexts == [(1, {"ticket": {"status": "open"}})]
    assert len(results) == 1
    assert results[0]["automation_id"] == 1
    assert results[0]["status"] == "queued"


@pytest.mark.anyio
async def test_handle_event_returns_empty_for_blank_event(monkeypatch):
    called = False

    async def fake_list_event_automations(*args, **kwargs):  # pragma: no cover - defensive
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(
        automations_service.automation_repo,
        "list_event_automations",
        fake_list_event_automations,
    )

    results = await automations_service.handle_event("", {"ticket": {}})

    assert results == []
    assert called is False


@pytest.mark.anyio
async def test_execute_automation_interpolates_context(monkeypatch):
    captured: list[tuple[str, dict[str, object]]] = []

    async def fake_trigger_module(module_slug, payload, *, background=False):
        assert background is False
        captured.append((module_slug, payload))
        return {"status": "ok"}

    async def fake_mark_started(*args, **kwargs):
        return None

    recorded_runs: list[dict[str, object]] = []

    async def fake_record_run(**kwargs):
        recorded_runs.append(kwargs)

    async def fake_set_last_error(*args, **kwargs):
        return None

    async def fake_set_next_run(*args, **kwargs):
        return None

    monkeypatch.setattr(
        automations_service.modules_service,
        "trigger_module",
        fake_trigger_module,
    )
    monkeypatch.setattr(
        automations_service.automation_repo,
        "mark_started",
        fake_mark_started,
    )
    monkeypatch.setattr(
        automations_service.automation_repo,
        "record_run",
        fake_record_run,
    )
    monkeypatch.setattr(
        automations_service.automation_repo,
        "set_last_error",
        fake_set_last_error,
    )
    monkeypatch.setattr(
        automations_service.automation_repo,
        "set_next_run",
        fake_set_next_run,
    )

    created_at = datetime(2025, 3, 1, 9, 30, tzinfo=timezone.utc)
    context = {
        "ticket": {
            "id": 321,
            "priority": "high",
            "requester": {"email": "alice@example.com"},
            "labels": ["critical", "vip"],
            "created_at": created_at,
        }
    }
    automation = {
        "id": 77,
        "kind": "event",
        "action_payload": {
            "actions": [
                {
                    "module": "smtp",
                    "payload": {
                        "subject": "Ticket #{{ticket.id}} assigned to {{ticket.requester.email}}",
                        "ticket_id": "{{ticket.id}}",
                        "metadata": {
                            "priority": "{{ticket.priority}}",
                            "first_tag": "{{ticket.labels.0}}",
                            "tags": ["{{ticket.labels.0}}", "{{ticket.labels.1}}"],
                            "timestamp": "{{ticket.created_at}}",
                        },
                    },
                }
            ]
        },
    }

    result = await automations_service._execute_automation(automation, context=context)

    assert result["status"] == "succeeded"
    assert recorded_runs and recorded_runs[0]["status"] == "succeeded"

    assert captured, "expected module to be invoked"
    module_slug, payload = captured[0]
    assert module_slug == "smtp"
    assert payload["subject"] == "Ticket #321 assigned to alice@example.com"
    assert payload["ticket_id"] == 321
    assert payload["metadata"]["priority"] == "high"
    assert payload["metadata"]["first_tag"] == "critical"
    assert payload["metadata"]["tags"] == ["critical", "vip"]
    assert payload["metadata"]["timestamp"] == created_at.isoformat()
    assert payload["context"] == context
