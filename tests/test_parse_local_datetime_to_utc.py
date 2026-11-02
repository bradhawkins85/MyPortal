"""Requested dates from the portal are local wall-clock times in the
requester's timezone and must be converted to UTC before they are stored."""

from datetime import datetime, timezone

from app import main


def test_naive_value_is_interpreted_in_requested_timezone():
    result = main._parse_local_datetime_to_utc(
        "2026-11-20T09:00", timezone_name="Australia/Brisbane"
    )

    assert result == datetime(2026, 11, 19, 23, 0, tzinfo=timezone.utc)


def test_explicit_offset_wins_over_requested_timezone():
    result = main._parse_local_datetime_to_utc(
        "2026-11-20T09:00+10:00", timezone_name="America/New_York"
    )

    assert result == datetime(2026, 11, 19, 23, 0, tzinfo=timezone.utc)


def test_value_without_timezone_is_treated_as_utc():
    result = main._parse_local_datetime_to_utc("2026-11-20T09:00", timezone_name=None)

    assert result == datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)


def test_unknown_timezone_falls_back_to_utc():
    result = main._parse_local_datetime_to_utc(
        "2026-11-20T09:00", timezone_name="Not/AZone"
    )

    assert result == datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)


def test_date_only_value_is_local_midnight():
    result = main._parse_local_datetime_to_utc(
        "2026-11-20", timezone_name="Australia/Brisbane", assume_midnight=True
    )

    assert result == datetime(2026, 11, 19, 14, 0, tzinfo=timezone.utc)


def test_blank_value_returns_none():
    assert main._parse_local_datetime_to_utc("  ", timezone_name="UTC") is None
