from datetime import datetime, timezone

from app.repositories.automations import _prepare_for_storage


def test_prepare_for_storage_preserves_naive_datetime():
    value = datetime(2025, 1, 1, 12, 0, 0)
    assert _prepare_for_storage(value) == value


def test_prepare_for_storage_converts_timezone_aware_to_utc_naive():
    aware = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    prepared = _prepare_for_storage(aware)
    assert prepared.tzinfo is None
    assert prepared == datetime(2025, 1, 1, 12, 0, 0)
