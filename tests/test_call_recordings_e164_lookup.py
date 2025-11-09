"""Tests for E.164 phone number matching in lookup_staff_by_phone."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_lookup_staff_by_phone_e164_match():
    """Test staff lookup with E.164 normalized number."""
    from app.repositories import call_recordings as repo
    
    with patch.object(repo.db, "fetch_one", new_callable=AsyncMock) as mock_fetch_one:
        # First call returns match for E.164 format
        mock_fetch_one.return_value = {"id": 42}
        
        # Australian mobile with leading 0 should match E.164 format in DB
        result = await repo.lookup_staff_by_phone("0412345678")
        
        assert result == 42
        # Should try E.164 format first: +61412345678
        assert mock_fetch_one.call_count >= 1
        first_call_args = mock_fetch_one.call_args_list[0]
        assert "+61412345678" in str(first_call_args)


@pytest.mark.asyncio
async def test_lookup_staff_by_phone_e164_different_formats_same_staff():
    """Test that different formats of same number find the same staff member."""
    from app.repositories import call_recordings as repo
    
    test_formats = [
        "0412345678",           # Australian local format
        "+61412345678",         # E.164 format
        "61412345678",          # International without +
        "0412 345 678",         # With spaces
        "+61 412 345 678",      # E.164 with spaces
    ]
    
    for phone_format in test_formats:
        with patch.object(repo.db, "fetch_one", new_callable=AsyncMock) as mock_fetch_one:
            mock_fetch_one.return_value = {"id": 42}
            
            result = await repo.lookup_staff_by_phone(phone_format)
            
            assert result == 42, f"Failed to find staff for format: {phone_format}"


@pytest.mark.asyncio
async def test_lookup_staff_by_phone_e164_us_number():
    """Test staff lookup with US phone number."""
    from app.repositories import call_recordings as repo
    
    with patch.object(repo.db, "fetch_one", new_callable=AsyncMock) as mock_fetch_one:
        mock_fetch_one.return_value = {"id": 99}
        
        # US number should be normalized
        result = await repo.lookup_staff_by_phone("+14155551234")
        
        assert result == 99


@pytest.mark.asyncio
async def test_lookup_staff_by_phone_fallback_to_original():
    """Test that lookup falls back to original format if E.164 doesn't match."""
    from app.repositories import call_recordings as repo
    
    with patch.object(repo.db, "fetch_one", new_callable=AsyncMock) as mock_fetch_one:
        # First call (E.164) returns None, second call (original) returns match
        mock_fetch_one.side_effect = [None, {"id": 42}]
        
        result = await repo.lookup_staff_by_phone("+61412345678")
        
        assert result == 42
        assert mock_fetch_one.call_count == 2


@pytest.mark.asyncio
async def test_lookup_staff_by_phone_fallback_to_normalized():
    """Test that lookup falls back to normalized format if E.164 and original don't match."""
    from app.repositories import call_recordings as repo
    
    with patch.object(repo.db, "fetch_one", new_callable=AsyncMock) as mock_fetch_one:
        # First two calls return None, third call (normalized) returns match
        mock_fetch_one.side_effect = [None, None, {"id": 42}]
        
        result = await repo.lookup_staff_by_phone("+61 412 345 678")
        
        assert result == 42
        assert mock_fetch_one.call_count == 3


@pytest.mark.asyncio
async def test_lookup_staff_by_phone_fallback_to_partial():
    """Test that lookup falls back to partial match (last 10 digits)."""
    from app.repositories import call_recordings as repo
    
    with patch.object(repo.db, "fetch_one", new_callable=AsyncMock) as mock_fetch_one:
        # All exact matches return None, partial match returns result
        # Need 4 Nones: E.164, original, normalized, then partial match succeeds
        mock_fetch_one.side_effect = [None, None, None, {"id": 42}]
        
        # Use a number with spaces so normalized differs from original
        result = await repo.lookup_staff_by_phone("+61 412 345 678")
        
        assert result == 42
        assert mock_fetch_one.call_count == 4
        # Last call should use LIKE with last 10 digits
        last_call_args = mock_fetch_one.call_args_list[-1]
        assert "LIKE" in str(last_call_args)


@pytest.mark.asyncio
async def test_lookup_staff_by_phone_invalid_number_tries_fallbacks():
    """Test that invalid numbers still try fallback matching strategies."""
    from app.repositories import call_recordings as repo
    
    with patch.object(repo.db, "fetch_one", new_callable=AsyncMock) as mock_fetch_one:
        # All lookups return None
        mock_fetch_one.return_value = None
        
        result = await repo.lookup_staff_by_phone("invalid123")
        
        assert result is None
        # Should still try original and normalized formats
        assert mock_fetch_one.call_count >= 1


@pytest.mark.asyncio
async def test_lookup_staff_by_phone_no_match():
    """Test staff lookup with no matching phone number."""
    from app.repositories import call_recordings as repo
    
    with patch.object(repo.db, "fetch_one", new_callable=AsyncMock) as mock_fetch_one:
        mock_fetch_one.return_value = None
        
        result = await repo.lookup_staff_by_phone("+9999999999")
        
        assert result is None
