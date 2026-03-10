import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.services import automations as automations_service
from app.services import message_templates as message_templates_service
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
        assert limit is None
        if trigger_event == "tickets.created":
            return [
                {"id": 1, "trigger_filters": {"match": {"ticket.status": "open"}}},
                {"id": 2, "trigger_filters": {"match": {"ticket.status": "closed"}}},
            ]
        if trigger_event == "ticket.created":
            return []
        raise AssertionError(f"Unexpected trigger event: {trigger_event}")

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
async def test_handle_event_includes_legacy_alias_automations(monkeypatch):
    contexts: list[tuple[int, dict[str, object] | None]] = []
    requested_events: list[str] = []

    async def fake_list_event_automations(trigger_event: str, *, limit: int | None = None):
        assert limit is None
        requested_events.append(trigger_event)
        if trigger_event == "tickets.created":
            return [{"id": 10, "trigger_filters": None}]
        if trigger_event == "ticket.created":
            return [{"id": 20, "trigger_filters": None}]
        return []

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
    monkeypatch.setattr(automations_service, "_execute_automation", fake_execute)
    monkeypatch.setattr(automations_service, "_schedule_background_execution", fake_schedule)

    results = await automations_service.handle_event("tickets.created", {"ticket": {"id": 1}})

    await asyncio.gather(*(task for _, task in scheduled_tasks))

    assert requested_events == ["tickets.created", "ticket.created"]
    assert contexts == [(10, {"ticket": {"id": 1}}), (20, {"ticket": {"id": 1}})]
    assert [item["automation_id"] for item in results] == [10, 20]


@pytest.mark.anyio
async def test_handle_event_deduplicates_same_automation_from_aliases(monkeypatch):
    async def fake_list_event_automations(trigger_event: str, *, limit: int | None = None):
        if trigger_event in {"tickets.created", "ticket.created"}:
            return [{"id": 42, "trigger_filters": None}]
        return []

    async def fake_execute(automation, *, context=None):
        now = datetime.now(timezone.utc)
        return {
            "status": "succeeded",
            "result": None,
            "error": None,
            "started_at": now,
            "finished_at": now,
            "next_run_at": None,
        }

    scheduled_ids: list[int] = []

    def fake_schedule(coro, *, automation_id: int):
        scheduled_ids.append(automation_id)
        return asyncio.create_task(coro)

    monkeypatch.setattr(
        automations_service.automation_repo,
        "list_event_automations",
        fake_list_event_automations,
    )
    monkeypatch.setattr(automations_service, "_execute_automation", fake_execute)
    monkeypatch.setattr(automations_service, "_schedule_background_execution", fake_schedule)

    results = await automations_service.handle_event("tickets.created", {"ticket": {"id": 1}})

    assert scheduled_ids == [42]
    assert len(results) == 1
    assert results[0]["automation_id"] == 42


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


@pytest.mark.anyio
async def test_execute_automation_supports_constant_tokens(monkeypatch):
    captured: list[dict[str, object]] = []

    async def fake_trigger_module(module_slug, payload, *, background=False):
        assert background is False
        captured.append(payload)
        return {"status": "ok"}

    async def fake_mark_started(*args, **kwargs):
        return None

    async def fake_record_run(**kwargs):
        return None

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

    context = {"ticket": {"id": 41, "subject": "VPN outage", "priority": "high"}}
    automation = {
        "id": 91,
        "kind": "event",
        "action_module": "ntfy",
        "action_payload": {
            "message": "{{ TICKET_SUMMARY }}",
            "title": "{{ TICKET_PRIORITY }} priority",
        },
    }

    result = await automations_service._execute_automation(automation, context=context)

    assert result["status"] == "succeeded"
    assert captured
    payload = captured[0]
    assert payload["message"] == "VPN outage"
    assert payload["title"] == "high priority"


@pytest.mark.anyio
async def test_execute_automation_supports_message_templates(monkeypatch):
    captured: list[dict[str, object]] = []

    async def fake_trigger_module(module_slug, payload, *, background=False):
        captured.append(payload)
        return {"status": "ok"}

    async def fake_mark_started(*args, **kwargs):
        return None

    async def fake_record_run(**kwargs):
        return None

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

    template_records = [
        {
            "id": 1,
            "slug": "welcome_email",
            "name": "Welcome Email",
            "description": None,
            "content_type": "text/plain",
            "content": "Hello {{ticket.requester.email}} from {{ APP_NAME }}",
            "created_at": None,
            "updated_at": None,
        }
    ]

    monkeypatch.setattr(
        message_templates_service,
        "iter_templates",
        lambda: template_records,
    )

    context = {"ticket": {"id": 41, "requester": {"email": "alice@example.com"}}}
    automation = {
        "id": 63,
        "kind": "event",
        "action_module": "ntfy",
        "action_payload": {
            "title": "Welcome notification",
            "message": "{{ TEMPLATE_WELCOME_EMAIL }}",
        },
    }

    result = await automations_service._execute_automation(automation, context=context)

    assert result["status"] == "succeeded"
    assert captured
    payload = captured[0]
    assert payload["title"] == "Welcome notification"
    assert "alice@example.com" in payload["message"]
@pytest.mark.anyio
async def test_execute_automation_injects_system_variables(monkeypatch):
    from app.core.config import get_settings

    captured: list[dict[str, object]] = []

    async def fake_trigger_module(module_slug, payload, *, background=False):
        assert background is False
        captured.append(payload)
        return {"status": "ok"}

    async def fake_mark_started(*args, **kwargs):
        return None

    async def fake_record_run(**kwargs):
        return None

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

    settings = get_settings()

    automation = {
        "id": 105,
        "kind": "event",
        "action_module": "ntfy",
        "action_payload": {
            "message": "App {{ APP_NAME }} ({{ APP_ENVIRONMENT }})",
            "metadata": {
                "timestamp": "{{ NOW_UTC }}",
                "backend": "{{ APP_DATABASE_BACKEND }}",
            },
        },
    }

    result = await automations_service._execute_automation(automation)

    assert result["status"] == "succeeded"
    assert captured
    payload = captured[0]
    assert payload["message"].startswith(f"App {settings.app_name} (")
    assert payload["metadata"]["backend"] in {"mysql", "sqlite"}

    timestamp = payload["metadata"]["timestamp"]
    parsed = datetime.fromisoformat(timestamp)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timezone.utc.utcoffset(parsed)


@pytest.mark.anyio
async def test_execute_automation_marks_failure_when_action_fails(monkeypatch):
    captured_run: dict[str, Any] = {}
    captured_errors: list[tuple[int, str | None]] = []

    async def fake_trigger_module(module_slug, payload, *, background=False):
        assert module_slug == "smtp"
        assert background is False
        return {"status": "failed", "last_error": "SMTP service declined to send message"}

    async def fake_mark_started(*args, **kwargs):
        return None

    async def fake_record_run(**kwargs):
        captured_run.update(kwargs)

    async def fake_set_last_error(automation_id, message):
        captured_errors.append((automation_id, message))

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

    automation = {
        "id": 310,
        "kind": "event",
        "action_module": "smtp",
        "action_payload": {
            "actions": [
                {
                    "module": "smtp",
                    "payload": {"recipients": ["alerts@example.com"]},
                }
            ]
        },
    }

    result = await automations_service._execute_automation(automation)

    assert result["status"] == "failed"
    assert "SMTP service declined" in (result.get("error") or "")

    assert captured_run["status"] == "failed"
    assert "SMTP service declined" in (captured_run.get("error_message") or "")
    assert isinstance(captured_run.get("result_payload"), list)
    assert captured_run["result_payload"][0]["status"] == "failed"
    assert "SMTP service declined" in captured_run["result_payload"][0]["error"]

    assert captured_errors[-1] == (310, captured_run.get("error_message"))


@pytest.mark.anyio
async def test_execute_automation_runs_all_actions_of_same_type(monkeypatch):
    """Multiple actions of the same module type must all be triggered."""
    captured: list[tuple[str, dict]] = []

    async def fake_trigger_module(module_slug, payload, *, background=False):
        captured.append((module_slug, dict(payload)))
        return {"status": "succeeded"}

    async def fake_mark_started(*args, **kwargs):
        return None

    async def fake_record_run(**kwargs):
        return None

    async def fake_set_last_error(*args, **kwargs):
        return None

    async def fake_set_next_run(*args, **kwargs):
        return None

    monkeypatch.setattr(automations_service.modules_service, "trigger_module", fake_trigger_module)
    monkeypatch.setattr(automations_service.automation_repo, "mark_started", fake_mark_started)
    monkeypatch.setattr(automations_service.automation_repo, "record_run", fake_record_run)
    monkeypatch.setattr(automations_service.automation_repo, "set_last_error", fake_set_last_error)
    monkeypatch.setattr(automations_service.automation_repo, "set_next_run", fake_set_next_run)

    automation = {
        "id": 42,
        "kind": "event",
        "action_payload": {
            "actions": [
                {
                    "module": "smtp2go",
                    "payload": {
                        "recipients": ["admin@example.com"],
                        "subject": "Alert to admin",
                    },
                },
                {
                    "module": "smtp2go",
                    "payload": {
                        "recipients": ["user@example.com"],
                        "subject": "Alert to user",
                    },
                },
            ]
        },
    }

    result = await automations_service._execute_automation(automation)

    assert result["status"] == "succeeded"
    assert len(captured) == 2, f"Expected 2 smtp2go invocations, got {len(captured)}"
    assert captured[0][0] == "smtp2go"
    assert captured[1][0] == "smtp2go"
    assert captured[0][1].get("subject") == "Alert to admin"
    assert captured[1][1].get("subject") == "Alert to user"

    result_payload = result.get("result")
    assert isinstance(result_payload, list)
    assert len(result_payload) == 2
    assert result_payload[0]["status"] == "succeeded"
    assert result_payload[1]["status"] == "succeeded"


@pytest.mark.anyio
async def test_execute_automation_continues_after_first_action_failure(monkeypatch):
    """When the first of multiple same-type actions fails, subsequent actions must still run."""
    captured: list[tuple[str, dict]] = []
    captured_run: dict[str, Any] = {}

    async def fake_trigger_module(module_slug, payload, *, background=False):
        captured.append((module_slug, dict(payload)))
        # First call fails, second succeeds
        if len(captured) == 1:
            return {"status": "failed", "last_error": "Connection refused"}
        return {"status": "succeeded"}

    async def fake_mark_started(*args, **kwargs):
        return None

    async def fake_record_run(**kwargs):
        captured_run.update(kwargs)

    async def fake_set_last_error(*args, **kwargs):
        return None

    async def fake_set_next_run(*args, **kwargs):
        return None

    monkeypatch.setattr(automations_service.modules_service, "trigger_module", fake_trigger_module)
    monkeypatch.setattr(automations_service.automation_repo, "mark_started", fake_mark_started)
    monkeypatch.setattr(automations_service.automation_repo, "record_run", fake_record_run)
    monkeypatch.setattr(automations_service.automation_repo, "set_last_error", fake_set_last_error)
    monkeypatch.setattr(automations_service.automation_repo, "set_next_run", fake_set_next_run)

    automation = {
        "id": 55,
        "kind": "event",
        "action_payload": {
            "actions": [
                {
                    "module": "smtp2go",
                    "payload": {"recipients": ["admin@example.com"], "subject": "Alert 1"},
                },
                {
                    "module": "smtp2go",
                    "payload": {"recipients": ["user@example.com"], "subject": "Alert 2"},
                },
            ]
        },
    }

    result = await automations_service._execute_automation(automation)

    # Overall status is failed because first action failed
    assert result["status"] == "failed"
    # But BOTH actions must have been attempted
    assert len(captured) == 2, f"Expected 2 invocations, got {len(captured)}: second action was skipped"
    assert captured[0][1].get("subject") == "Alert 1"
    assert captured[1][1].get("subject") == "Alert 2"

    # Result payload records both actions
    assert isinstance(captured_run.get("result_payload"), list)
    assert len(captured_run["result_payload"]) == 2
    assert captured_run["result_payload"][0]["status"] == "failed"
    assert captured_run["result_payload"][1]["status"] == "succeeded"
