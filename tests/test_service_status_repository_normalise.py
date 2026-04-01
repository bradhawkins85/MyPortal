"""Tests for _normalise_service text field handling in service_status repository."""
from app.repositories.service_status import _normalise_service


def _base_row(**overrides):
    row = {
        "id": 1,
        "name": "My Service",
        "description": None,
        "status": "operational",
        "status_message": None,
        "display_order": 0,
        "is_active": 1,
        "tags": "",
        "created_at": None,
        "updated_at": None,
        "updated_by": None,
        "ai_lookup_enabled": 0,
        "ai_lookup_url": None,
        "ai_lookup_prompt": None,
        "ai_lookup_model_override": None,
        "ai_lookup_frequency_operational": 60,
        "ai_lookup_frequency_degraded": 15,
        "ai_lookup_frequency_partial_outage": 10,
        "ai_lookup_frequency_outage": 5,
        "ai_lookup_frequency_maintenance": 60,
        "ai_lookup_last_checked_at": None,
        "ai_lookup_last_status": None,
        "ai_lookup_last_message": None,
    }
    row.update(overrides)
    return row


def test_normalise_service_none_string_converted_to_none():
    """The literal string 'None' (any case) stored by the old template bug is converted to None."""
    row = _base_row(
        ai_lookup_url="None",
        ai_lookup_prompt="NONE",
        ai_lookup_model_override="none",
    )
    result = _normalise_service(row, {})
    assert result["ai_lookup_url"] is None
    assert result["ai_lookup_prompt"] is None
    assert result["ai_lookup_model_override"] is None


def test_normalise_service_empty_string_converted_to_none():
    """Empty strings for text fields are normalised to None."""
    row = _base_row(
        ai_lookup_url="",
        ai_lookup_prompt="   ",
        ai_lookup_model_override="",
    )
    result = _normalise_service(row, {})
    assert result["ai_lookup_url"] is None
    assert result["ai_lookup_prompt"] is None
    assert result["ai_lookup_model_override"] is None


def test_normalise_service_valid_text_preserved():
    """Valid non-empty strings are preserved with surrounding whitespace stripped."""
    row = _base_row(
        ai_lookup_url="  https://example.com/status  ",
        ai_lookup_prompt="  Is this OK?  ",
        ai_lookup_model_override="  llama3  ",
    )
    result = _normalise_service(row, {})
    assert result["ai_lookup_url"] == "https://example.com/status"
    assert result["ai_lookup_prompt"] == "Is this OK?"
    assert result["ai_lookup_model_override"] == "llama3"
