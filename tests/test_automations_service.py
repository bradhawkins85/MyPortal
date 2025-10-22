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

    results = await automations_service.handle_event(
        "tickets.created",
        {"ticket": {"status": "open"}},
    )

    assert contexts == [(1, {"ticket": {"status": "open"}})]
    assert len(results) == 1
    assert results[0]["automation_id"] == 1
    assert results[0]["status"] == "succeeded"


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
