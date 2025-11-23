"""Tests for one-time scheduling functionality in automations."""
import pytest
from datetime import datetime, timedelta, timezone

from app.services.automations import calculate_next_run


def test_calculate_next_run_one_time_future():
    """Test one-time scheduling with a future scheduled time."""
    reference = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    scheduled_time = datetime(2025, 1, 5, 14, 30, tzinfo=timezone.utc)
    automation = {
        "kind": "scheduled",
        "run_once": True,
        "scheduled_time": scheduled_time,
        "last_run_at": None,
    }
    next_run = calculate_next_run(automation, reference=reference)
    assert next_run == scheduled_time


def test_calculate_next_run_one_time_past_not_run():
    """Test one-time scheduling with a past scheduled time that hasn't run yet."""
    reference = datetime(2025, 1, 5, 12, 0, tzinfo=timezone.utc)
    scheduled_time = datetime(2025, 1, 1, 14, 30, tzinfo=timezone.utc)
    automation = {
        "kind": "scheduled",
        "run_once": True,
        "scheduled_time": scheduled_time,
        "last_run_at": None,
    }
    next_run = calculate_next_run(automation, reference=reference)
    # Should return the scheduled time even if in the past, so it runs ASAP
    assert next_run == scheduled_time


def test_calculate_next_run_one_time_already_run():
    """Test one-time scheduling that has already been executed."""
    reference = datetime(2025, 1, 5, 12, 0, tzinfo=timezone.utc)
    scheduled_time = datetime(2025, 1, 1, 14, 30, tzinfo=timezone.utc)
    last_run = datetime(2025, 1, 1, 14, 30, 5, tzinfo=timezone.utc)
    automation = {
        "kind": "scheduled",
        "run_once": True,
        "scheduled_time": scheduled_time,
        "last_run_at": last_run,
    }
    next_run = calculate_next_run(automation, reference=reference)
    # Should not reschedule if already run
    assert next_run is None


def test_calculate_next_run_one_time_no_scheduled_time():
    """Test one-time scheduling without a scheduled time."""
    reference = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    automation = {
        "kind": "scheduled",
        "run_once": True,
        "scheduled_time": None,
        "last_run_at": None,
    }
    next_run = calculate_next_run(automation, reference=reference)
    # Should return None if no scheduled time is set
    assert next_run is None


def test_calculate_next_run_one_time_naive_datetime():
    """Test one-time scheduling with a naive datetime (no timezone)."""
    reference = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    scheduled_time = datetime(2025, 1, 5, 14, 30)  # No timezone
    automation = {
        "kind": "scheduled",
        "run_once": True,
        "scheduled_time": scheduled_time,
        "last_run_at": None,
    }
    next_run = calculate_next_run(automation, reference=reference)
    # Should handle naive datetime by treating as UTC
    expected = scheduled_time.replace(tzinfo=timezone.utc)
    assert next_run == expected


def test_calculate_next_run_recurring_not_affected():
    """Test that recurring schedules still work when run_once is False."""
    reference = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    automation = {
        "kind": "scheduled",
        "cadence": "hourly",
        "run_once": False,
    }
    next_run = calculate_next_run(automation, reference=reference)
    assert next_run == reference + timedelta(hours=1)


def test_calculate_next_run_event_automation_with_run_once():
    """Test that event automations ignore run_once flag."""
    reference = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    scheduled_time = datetime(2025, 1, 5, 14, 30, tzinfo=timezone.utc)
    automation = {
        "kind": "event",
        "run_once": True,
        "scheduled_time": scheduled_time,
    }
    next_run = calculate_next_run(automation, reference=reference)
    # Event automations should always return None
    assert next_run is None
