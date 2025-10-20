from datetime import datetime, timedelta, timezone

from app.services.automations import calculate_next_run


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
