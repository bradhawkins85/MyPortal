from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock

import pytest
from starlette.datastructures import FormData

from app.services import automations as automations_service
from app.services import business_hours as bh
from app.services import slas as sla_service


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _clear_cache():
    bh.invalidate_cache()
    yield
    bh.invalidate_cache()


def _brisbane(closures: set[date] | None = None) -> bh.BusinessHoursSchedule:
    # Brisbane is UTC+10 with no daylight saving; weekdays 08:30-17:00.
    return bh.build_schedule(
        {"timezone": "Australia/Brisbane", "weekly_hours": bh.DEFAULT_WEEKLY_HOURS},
        closures or set(),
    )


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def test_is_open_respects_weekly_hours_and_timezone():
    schedule = _brisbane()
    assert bh.is_open(schedule, utc(2026, 9, 28, 23, 0))  # Tue 09:00 local
    assert not bh.is_open(schedule, utc(2026, 9, 29, 8, 0))  # Tue 18:00 local
    assert not bh.is_open(schedule, utc(2026, 10, 3, 1, 0))  # Saturday


def test_unconfigured_schedule_is_always_open():
    assert bh.is_open(None, utc(2026, 10, 3, 1, 0))
    assert bh.next_open(None, utc(2026, 10, 3, 1, 0)) == utc(2026, 10, 3, 1, 0)


def test_next_open_skips_closures():
    schedule = _brisbane({date(2026, 9, 30)})
    # Tue 18:00 local -> Wed closed -> Thu 08:30 local (Wed 22:30 UTC)
    assert bh.next_open(schedule, utc(2026, 9, 29, 8, 0)) == utc(2026, 9, 30, 22, 30)


def test_business_time_math():
    schedule = _brisbane()
    assert bh.business_seconds_between(
        schedule, utc(2026, 9, 28, 23, 0), utc(2026, 9, 29, 8, 0)
    ) == 8 * 3600
    # One business hour after Friday 16:30 local is Monday 09:00 local.
    assert bh.add_business_seconds(schedule, utc(2026, 10, 2, 6, 30), 3600) == utc(2026, 10, 4, 23, 0)


def test_daylight_saving_timezone():
    schedule = bh.build_schedule(
        {"timezone": "Australia/Sydney", "weekly_hours": bh.DEFAULT_WEEKLY_HOURS}
    )
    # Sydney moves to UTC+11 on 2026-10-04; Monday 08:30 local is 21:30 UTC Sunday.
    assert bh.next_open(schedule, utc(2026, 10, 3, 0, 0)) == utc(2026, 10, 4, 21, 30)


def test_parse_weekly_form_validates_and_supports_split_shifts():
    form = FormData(
        [
            ("day_mon_open", "1"),
            ("day_mon_start", "08:00"),
            ("day_mon_end", "12:00"),
            ("day_mon_start2", "13:00"),
            ("day_mon_end2", "00:00"),
            ("day_tue_start", "09:00"),
            ("day_tue_end", "17:00"),
        ]
    )
    weekly, error = bh.parse_weekly_form(form)
    assert error is None
    assert weekly["mon"] == [{"start": "08:00", "end": "12:00"}, {"start": "13:00", "end": "24:00"}]
    assert weekly["tue"] == []

    _, error = bh.parse_weekly_form(
        FormData([("day_wed_open", "1"), ("day_wed_start", "17:00"), ("day_wed_end", "09:00")])
    )
    assert error and "Wednesday" in error


@pytest.mark.anyio("asyncio")
async def test_automation_gate_uses_company_schedule(monkeypatch):
    closed = _brisbane()
    get_schedule = AsyncMock(return_value=closed)
    monkeypatch.setattr(bh, "get_schedule", get_schedule)
    automation = {"id": 1, "business_hours_mode": "pause", "business_hours_source": "company"}
    context = {"ticket": {"id": 5, "company_id": 7}}

    gate = await bh.automation_gate(automation, context, at=utc(2026, 9, 29, 8, 0))

    get_schedule.assert_awaited_once_with(7)
    assert gate["action"] == "pause"
    assert gate["run_after"] == utc(2026, 9, 29, 22, 30)
    assert await bh.automation_gate(automation, context, at=utc(2026, 9, 28, 23, 0)) is None
    assert await bh.automation_gate({"id": 1}, context) is None


@pytest.mark.anyio("asyncio")
async def test_handle_event_pauses_outside_business_hours(monkeypatch):
    automation = {
        "id": 3,
        "trigger_filters": None,
        "business_hours_mode": "pause",
        "business_hours_source": "company",
    }
    run_after = utc(2026, 9, 29, 22, 30)
    monkeypatch.setattr(
        automations_service.automation_repo,
        "list_event_automations",
        AsyncMock(side_effect=lambda key: [automation] if key == "tickets.created" else []),
    )
    monkeypatch.setattr(
        automations_service.business_hours_service,
        "automation_gate",
        AsyncMock(return_value={"action": "pause", "run_after": run_after, "company_id": 7}),
    )
    create_deferred = AsyncMock()
    monkeypatch.setattr(automations_service.business_hours_repo, "create_deferred_run", create_deferred)
    monkeypatch.setattr(automations_service, "_record_action_history", AsyncMock())
    execute = AsyncMock()
    monkeypatch.setattr(automations_service, "_execute_automation", execute)

    result = await automations_service.handle_event(
        "tickets.created", {"ticket": {"id": 9, "company_id": 7}}
    )

    assert result[0]["status"] == "deferred"
    execute.assert_not_called()
    kwargs = create_deferred.await_args.kwargs
    assert kwargs["automation_id"] == 3
    assert kwargs["ticket_id"] == 9
    assert kwargs["run_after"] == run_after


@pytest.mark.anyio("asyncio")
async def test_handle_event_skips_outside_business_hours(monkeypatch):
    automation = {"id": 4, "trigger_filters": None, "business_hours_mode": "skip"}
    monkeypatch.setattr(
        automations_service.automation_repo,
        "list_event_automations",
        AsyncMock(side_effect=lambda key: [automation] if key == "tickets.created" else []),
    )
    monkeypatch.setattr(
        automations_service.business_hours_service,
        "automation_gate",
        AsyncMock(return_value={"action": "skip", "run_after": None}),
    )
    create_deferred = AsyncMock()
    monkeypatch.setattr(automations_service.business_hours_repo, "create_deferred_run", create_deferred)
    monkeypatch.setattr(automations_service, "_record_action_history", AsyncMock())
    execute = AsyncMock()
    monkeypatch.setattr(automations_service, "_execute_automation", execute)

    result = await automations_service.handle_event("tickets.created", {"ticket": {"id": 9}})

    assert result == [{"automation_id": 4, "status": "skipped", "reason": "outside_business_hours"}]
    create_deferred.assert_not_called()
    execute.assert_not_called()


@pytest.mark.anyio("asyncio")
async def test_process_deferred_runs_executes_when_open(monkeypatch):
    repo = automations_service.business_hours_repo
    monkeypatch.setattr(
        repo,
        "list_due_deferred_runs",
        AsyncMock(return_value=[{"id": 11, "automation_id": 3, "context": {"ticket": {"id": 9}}}]),
    )
    monkeypatch.setattr(repo, "claim_deferred_run", AsyncMock(return_value=True))
    finish = AsyncMock()
    monkeypatch.setattr(repo, "finish_deferred_run", finish)
    monkeypatch.setattr(
        automations_service.automation_repo,
        "get_automation",
        AsyncMock(return_value={"id": 3, "status": "active", "business_hours_mode": "pause"}),
    )
    monkeypatch.setattr(
        automations_service.business_hours_service, "automation_gate", AsyncMock(return_value=None)
    )
    execute = AsyncMock(return_value={"status": "succeeded"})
    monkeypatch.setattr(automations_service, "_execute_automation", execute)

    assert await automations_service.process_deferred_runs() == 1
    assert execute.await_args.kwargs["context"] == {"ticket": {"id": 9}}
    finish.assert_awaited_once_with(11, status="completed", error_message=None)


@pytest.mark.anyio("asyncio")
async def test_process_deferred_runs_cancels_inactive_automation(monkeypatch):
    repo = automations_service.business_hours_repo
    monkeypatch.setattr(
        repo, "list_due_deferred_runs", AsyncMock(return_value=[{"id": 12, "automation_id": 3}])
    )
    monkeypatch.setattr(repo, "claim_deferred_run", AsyncMock(return_value=True))
    finish = AsyncMock()
    monkeypatch.setattr(repo, "finish_deferred_run", finish)
    monkeypatch.setattr(
        automations_service.automation_repo,
        "get_automation",
        AsyncMock(return_value={"id": 3, "status": "inactive"}),
    )
    execute = AsyncMock()
    monkeypatch.setattr(automations_service, "_execute_automation", execute)

    await automations_service.process_deferred_runs()

    execute.assert_not_called()
    assert finish.await_args.kwargs["status"] == "cancelled"


def test_sla_counts_only_business_hours():
    schedule = _brisbane()
    row = {
        "sla_id": 1,
        "sla_name": "Standard",
        "created_at": utc(2026, 10, 2, 6, 30),  # Friday 16:30 local
        "response_minutes": 60,
        "resolution_minutes": 480,
        "status": "open",
    }
    saturday = utc(2026, 10, 3, 1, 0)
    wall_clock = sla_service.calculate_status(row, now=saturday)
    business = sla_service.calculate_status(row, now=saturday, schedule=schedule)

    assert wall_clock["response_breached"] is True
    assert business["response_breached"] is False
    assert business["response_due_at"] == utc(2026, 10, 4, 23, 0)  # Monday 09:00 local
    assert business["paused"] is True
    assert business["paused_reason"] == "outside_business_hours"


@pytest.mark.parametrize(
    ("kind", "mode", "expected_mode"),
    [("event", "pause", "pause"), ("event", "skip", "skip"), ("scheduled", "pause", None), ("scheduled", "skip", "skip")],
)
def test_automation_form_business_hours_modes(kind, mode, expected_mode):
    from app.features.automations import handlers

    form = FormData(
        [
            ("name", "Customer SMS"),
            ("businessHoursMode", mode),
            ("businessHoursSource", "company"),
        ]
    )
    data, _, error, _ = handlers._parse_automation_form_submission(form, kind=kind)
    assert error is None
    assert data["business_hours_mode"] == expected_mode
    assert data["business_hours_source"] == ("company" if expected_mode else None)
