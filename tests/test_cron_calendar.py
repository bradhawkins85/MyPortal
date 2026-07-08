from datetime import datetime, timezone

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "cron_calendar", Path("app/services/cron_calendar.py")
)
cron_calendar = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(cron_calendar)
build_calendar_events = cron_calendar.build_calendar_events
calculate_next_run = cron_calendar.calculate_next_run


def test_build_calendar_events_expands_cron_in_utc_order():
    tasks = [
        {
            "id": 1,
            "name": "Nightly sync",
            "command": "sync_staff",
            "cron": "0 0 * * *",
            "company_id": None,
            "company_name": "All companies",
            "active": True,
        },
        {
            "id": 2,
            "name": "Morning mail",
            "command": "sync_m365_mailboxes",
            "cron": "30 6 * * *",
            "company_id": 4,
            "company_name": "Example Co",
            "active": True,
        },
    ]

    events = build_calendar_events(
        tasks,
        start=datetime(2026, 7, 7, tzinfo=timezone.utc),
        end=datetime(2026, 7, 8, tzinfo=timezone.utc),
    )

    assert [event["title"] for event in events] == ["Morning mail"]
    assert events[0]["start"] == "2026-07-07T06:30:00+00:00"
    assert events[0]["company_name"] == "Example Co"


def test_build_calendar_events_skips_invalid_cron():
    events = build_calendar_events(
        [{"id": 1, "name": "Broken", "cron": "not a cron", "active": True}],
        start=datetime(2026, 7, 7, tzinfo=timezone.utc),
        end=datetime(2026, 7, 8, tzinfo=timezone.utc),
    )

    assert events == []


def test_build_calendar_events_uses_configured_cron_timezone():
    events = build_calendar_events(
        [
            {
                "id": 3,
                "name": "Evening Brisbane job",
                "command": "sync_staff",
                "cron": "1 18 * * *",
                "company_id": None,
                "company_name": "All companies",
                "active": True,
            }
        ],
        start=datetime(2026, 7, 7, tzinfo=timezone.utc),
        end=datetime(2026, 7, 8, tzinfo=timezone.utc),
        timezone_name="Australia/Brisbane",
    )

    assert len(events) == 1
    assert events[0]["start"] == "2026-07-07T08:01:00+00:00"


def test_build_calendar_events_falls_back_to_utc_for_invalid_timezone():
    events = build_calendar_events(
        [{"id": 4, "name": "UTC fallback", "cron": "1 18 * * *", "active": True}],
        start=datetime(2026, 7, 7, tzinfo=timezone.utc),
        end=datetime(2026, 7, 8, tzinfo=timezone.utc),
        timezone_name="Invalid/Timezone",
    )

    assert len(events) == 1
    assert events[0]["start"] == "2026-07-07T18:01:00+00:00"


def test_calculate_next_run_returns_utc_timestamp_for_configured_timezone():
    next_run = calculate_next_run(
        {"cron": "1 18 * * *"},
        reference=datetime(2026, 7, 7, tzinfo=timezone.utc),
        timezone_name="Australia/Brisbane",
    )

    assert next_run is not None
    assert next_run.isoformat() == "2026-07-07T08:01:00+00:00"


def test_calculate_next_run_returns_none_for_invalid_cron():
    assert calculate_next_run(
        {"cron": "not a cron"},
        reference=datetime(2026, 7, 7, tzinfo=timezone.utc),
    ) is None
