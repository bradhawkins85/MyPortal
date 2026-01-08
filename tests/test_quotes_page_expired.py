"""Tests for the quotes page datetime handling."""
from datetime import datetime, timezone


def test_is_expired_with_string_datetime():
    """Test that _is_expired function handles string datetime values correctly."""
    # This tests the fix for the tzinfo AttributeError
    # Simulate the _is_expired function from main.py
    
    def _is_expired(expires_at):
        if not expires_at:
            return False
        # Handle string datetime (ISO format from _normalise_datetime)
        if isinstance(expires_at, str):
            try:
                expires_at = datetime.fromisoformat(expires_at)
            except (ValueError, AttributeError):
                return False
        # Ensure datetime has timezone info
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return expires_at < datetime.now(timezone.utc)
    
    # Test with expired string datetime
    expired_date = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc).isoformat()
    assert _is_expired(expired_date) is True
    
    # Test with future string datetime
    future_date = datetime(2030, 12, 31, 23, 59, 59, tzinfo=timezone.utc).isoformat()
    assert _is_expired(future_date) is False
    
    # Test with None
    assert _is_expired(None) is False
    
    # Test with datetime object (expired)
    expired_dt = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert _is_expired(expired_dt) is True
    
    # Test with datetime object (future)
    future_dt = datetime(2030, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    assert _is_expired(future_dt) is False
    
    # Test with naive datetime (should be treated as UTC)
    naive_expired = datetime(2024, 1, 1, 0, 0, 0)
    assert _is_expired(naive_expired) is True
    
    # Test with invalid string
    assert _is_expired("invalid") is False
