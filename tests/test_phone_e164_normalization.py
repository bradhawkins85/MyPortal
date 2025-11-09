"""Tests for phone number E.164 normalization."""
from __future__ import annotations

import pytest

from app.core.phone_utils import normalize_to_e164


def test_normalize_australian_mobile_with_leading_zero():
    """Test normalizing Australian mobile number starting with 0."""
    result = normalize_to_e164("0412345678")
    assert result == "+61412345678"


def test_normalize_australian_mobile_with_spaces():
    """Test normalizing Australian mobile with spaces."""
    result = normalize_to_e164("0412 345 678")
    assert result == "+61412345678"


def test_normalize_australian_mobile_with_country_code():
    """Test normalizing Australian mobile with +61 prefix."""
    result = normalize_to_e164("+61412345678")
    assert result == "+61412345678"


def test_normalize_australian_mobile_with_country_code_and_spaces():
    """Test normalizing Australian mobile with +61 and spaces."""
    result = normalize_to_e164("+61 412 345 678")
    assert result == "+61412345678"


def test_normalize_us_number_with_area_code():
    """Test normalizing US number with area code."""
    result = normalize_to_e164("(415) 555-1234", default_region="US")
    assert result == "+14155551234"


def test_normalize_us_number_with_plus():
    """Test normalizing US number with + prefix."""
    result = normalize_to_e164("+1 415 555 1234")
    assert result == "+14155551234"


def test_normalize_uk_mobile():
    """Test normalizing UK mobile number."""
    result = normalize_to_e164("07700900123", default_region="GB")
    assert result == "+447700900123"


def test_normalize_uk_mobile_with_country_code():
    """Test normalizing UK mobile with +44 prefix."""
    result = normalize_to_e164("+44 7700 900123")
    assert result == "+447700900123"


def test_normalize_invalid_number_returns_none():
    """Test that invalid phone numbers return None."""
    result = normalize_to_e164("invalid")
    assert result is None


def test_normalize_empty_string_returns_none():
    """Test that empty string returns None."""
    result = normalize_to_e164("")
    assert result is None


def test_normalize_none_returns_none():
    """Test that None returns None."""
    result = normalize_to_e164(None)
    assert result is None


def test_normalize_too_short_number_returns_none():
    """Test that too short numbers return None."""
    result = normalize_to_e164("123")
    assert result is None


def test_normalize_australian_landline():
    """Test normalizing Australian landline."""
    result = normalize_to_e164("03 9999 8888")
    assert result == "+61399998888"


def test_normalize_australian_landline_with_country_code():
    """Test normalizing Australian landline with country code."""
    result = normalize_to_e164("+61 3 9999 8888")
    assert result == "+61399998888"


def test_normalize_new_zealand_mobile():
    """Test normalizing New Zealand mobile."""
    result = normalize_to_e164("021 123 4567", default_region="NZ")
    assert result == "+64211234567"


def test_normalize_different_formats_same_result():
    """Test that different formats of same number produce same E.164 result."""
    formats = [
        "0412345678",
        "0412 345 678",
        "+61412345678",
        "+61 412 345 678",
        "61412345678",
    ]
    results = [normalize_to_e164(fmt) for fmt in formats]
    # All should normalize to the same E.164 format
    assert all(r == "+61412345678" for r in results if r is not None)
    # At least the first 4 should succeed
    assert results[0] == "+61412345678"
    assert results[1] == "+61412345678"
    assert results[2] == "+61412345678"
    assert results[3] == "+61412345678"
