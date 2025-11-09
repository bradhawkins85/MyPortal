"""Tests for E.164 phone number display in call recordings."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.schemas.call_recordings import CallRecordingResponse


def test_call_recording_response_e164_formatting():
    """Test that CallRecordingResponse returns E.164 formatted phone number."""
    recording = CallRecordingResponse(
        id=1,
        file_path="/path/to/recording.mp3",
        file_name="recording.mp3",
        phone_number="0412345678",  # Australian mobile
        call_date=datetime.now(timezone.utc),
        transcription_status="completed",
        is_billable=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    
    # Original phone number should be preserved
    assert recording.phone_number == "0412345678"
    
    # E.164 formatted version should be available
    assert recording.phone_number_e164 == "+61412345678"


def test_call_recording_response_e164_with_spaces():
    """Test E.164 formatting with phone number containing spaces."""
    recording = CallRecordingResponse(
        id=2,
        file_path="/path/to/recording.mp3",
        file_name="recording.mp3",
        phone_number="0412 345 678",
        call_date=datetime.now(timezone.utc),
        transcription_status="completed",
        is_billable=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    
    assert recording.phone_number == "0412 345 678"
    assert recording.phone_number_e164 == "+61412345678"


def test_call_recording_response_e164_already_formatted():
    """Test that already E.164 formatted numbers are preserved."""
    recording = CallRecordingResponse(
        id=3,
        file_path="/path/to/recording.mp3",
        file_name="recording.mp3",
        phone_number="+61412345678",
        call_date=datetime.now(timezone.utc),
        transcription_status="completed",
        is_billable=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    
    assert recording.phone_number == "+61412345678"
    assert recording.phone_number_e164 == "+61412345678"


def test_call_recording_response_e164_us_number():
    """Test E.164 formatting with US phone number."""
    recording = CallRecordingResponse(
        id=4,
        file_path="/path/to/recording.mp3",
        file_name="recording.mp3",
        phone_number="+14155551234",
        call_date=datetime.now(timezone.utc),
        transcription_status="completed",
        is_billable=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    
    assert recording.phone_number == "+14155551234"
    assert recording.phone_number_e164 == "+14155551234"


def test_call_recording_response_e164_no_phone():
    """Test that None phone number is handled gracefully."""
    recording = CallRecordingResponse(
        id=5,
        file_path="/path/to/recording.mp3",
        file_name="recording.mp3",
        phone_number=None,
        call_date=datetime.now(timezone.utc),
        transcription_status="completed",
        is_billable=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    
    assert recording.phone_number is None
    assert recording.phone_number_e164 is None


def test_call_recording_response_e164_invalid_number():
    """Test that invalid phone numbers fall back to original."""
    recording = CallRecordingResponse(
        id=6,
        file_path="/path/to/recording.mp3",
        file_name="recording.mp3",
        phone_number="invalid",
        call_date=datetime.now(timezone.utc),
        transcription_status="completed",
        is_billable=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    
    # Invalid numbers should fall back to original
    assert recording.phone_number == "invalid"
    assert recording.phone_number_e164 == "invalid"


def test_call_recording_response_e164_with_staff_info():
    """Test E.164 formatting with complete staff information."""
    recording = CallRecordingResponse(
        id=7,
        file_path="/path/to/recording.mp3",
        file_name="recording.mp3",
        phone_number="0412345678",
        caller_staff_id=1,
        caller_first_name="John",
        caller_last_name="Doe",
        caller_email="john@example.com",
        call_date=datetime.now(timezone.utc),
        transcription_status="completed",
        is_billable=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    
    assert recording.phone_number == "0412345678"
    assert recording.phone_number_e164 == "+61412345678"
    assert recording.caller_first_name == "John"
    assert recording.caller_last_name == "Doe"


def test_call_recording_response_serialization_includes_e164():
    """Test that E.164 field is included in serialization."""
    recording = CallRecordingResponse(
        id=8,
        file_path="/path/to/recording.mp3",
        file_name="recording.mp3",
        phone_number="0412345678",
        call_date=datetime.now(timezone.utc),
        transcription_status="completed",
        is_billable=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    
    # Convert to dict (as would be done when returning from API)
    data = recording.model_dump()
    
    # Check both fields are present
    assert "phone_number" in data
    assert "phone_number_e164" in data
    assert data["phone_number"] == "0412345678"
    assert data["phone_number_e164"] == "+61412345678"


def test_call_recording_response_json_serialization():
    """Test that E.164 field is included in JSON serialization."""
    recording = CallRecordingResponse(
        id=9,
        file_path="/path/to/recording.mp3",
        file_name="recording.mp3",
        phone_number="0412 345 678",
        call_date=datetime.now(timezone.utc),
        transcription_status="completed",
        is_billable=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    
    # Convert to JSON string
    json_str = recording.model_dump_json()
    
    # Should contain both phone number fields
    assert '"phone_number":"0412 345 678"' in json_str or '"phone_number": "0412 345 678"' in json_str
    assert '"phone_number_e164":"+61412345678"' in json_str or '"phone_number_e164": "+61412345678"' in json_str
