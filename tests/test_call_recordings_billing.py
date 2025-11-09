"""Tests for call recordings billing functionality in tickets."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def sample_recording_with_billing() -> dict[str, Any]:
    """Sample call recording data with billing information."""
    return {
        "id": 1,
        "file_path": "/recordings/call_001.wav",
        "file_name": "call_001.wav",
        "caller_number": "+1234567890",
        "callee_number": "+0987654321",
        "caller_staff_id": 10,
        "callee_staff_id": 20,
        "call_date": datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
        "duration_seconds": 300,
        "transcription": "Hello, this is a test call transcription.",
        "transcription_status": "completed",
        "linked_ticket_id": 100,
        "minutes_spent": 30,
        "is_billable": True,
        "labour_type_id": 5,
        "created_at": datetime(2024, 1, 15, 10, 35, 0, tzinfo=timezone.utc),
        "updated_at": datetime(2024, 1, 15, 10, 35, 0, tzinfo=timezone.utc),
        "caller_first_name": "John",
        "caller_last_name": "Doe",
        "caller_email": "john@example.com",
        "callee_first_name": "Jane",
        "callee_last_name": "Smith",
        "callee_email": "jane@example.com",
        "linked_ticket_number": 12345,
        "linked_ticket_subject": "Test Ticket",
        "labour_type_name": "Remote Support",
        "labour_type_code": "RS",
    }


@pytest.mark.asyncio
async def test_list_ticket_call_recordings():
    """Test fetching call recordings linked to a ticket."""
    from app.repositories import call_recordings as repo
    
    ticket_id = 100
    expected_recordings = [
        {
            "id": 1,
            "file_name": "call_001.wav",
            "caller_number": "+1234567890",
            "callee_number": "+0987654321",
            "linked_ticket_id": ticket_id,
            "minutes_spent": 30,
            "is_billable": True,
            "labour_type_id": 5,
            "labour_type_name": "Remote Support",
            "labour_type_code": "RS",
        },
        {
            "id": 2,
            "file_name": "call_002.wav",
            "caller_number": "+1111111111",
            "callee_number": "+2222222222",
            "linked_ticket_id": ticket_id,
            "minutes_spent": 15,
            "is_billable": False,
            "labour_type_id": None,
            "labour_type_name": None,
            "labour_type_code": None,
        },
    ]
    
    with patch.object(repo.db, "fetch_all", new_callable=AsyncMock) as mock_fetch_all:
        mock_fetch_all.return_value = expected_recordings
        
        recordings = await repo.list_ticket_call_recordings(ticket_id)
        
        assert len(recordings) == 2
        assert recordings[0]["id"] == 1
        assert recordings[0]["minutes_spent"] == 30
        assert recordings[0]["is_billable"] is True
        assert recordings[0]["labour_type_name"] == "Remote Support"
        
        assert recordings[1]["id"] == 2
        assert recordings[1]["minutes_spent"] == 15
        assert recordings[1]["is_billable"] is False
        assert recordings[1]["labour_type_name"] is None
        
        # Verify the SQL query includes the labour type join
        call_args = mock_fetch_all.call_args
        sql = call_args[0][0]
        assert "LEFT JOIN ticket_labour_types" in sql
        assert "data-recording-id" not in sql  # Should not contain HTML


@pytest.mark.asyncio
async def test_update_call_recording_billing():
    """Test updating billing information for a call recording."""
    from app.repositories import call_recordings as repo
    
    recording_id = 1
    updated_recording = {
        "id": recording_id,
        "minutes_spent": 45,
        "is_billable": True,
        "labour_type_id": 7,
        "labour_type_name": "On-site Support",
        "labour_type_code": "OS",
    }
    
    with patch.object(repo.db, "execute", new_callable=AsyncMock) as mock_execute, \
         patch.object(repo, "get_call_recording_by_id", new_callable=AsyncMock) as mock_get:
        
        mock_get.return_value = updated_recording
        
        result = await repo.update_call_recording(
            recording_id,
            minutes_spent=45,
            is_billable=True,
            labour_type_id=7,
        )
        
        assert result["minutes_spent"] == 45
        assert result["is_billable"] is True
        assert result["labour_type_id"] == 7
        
        # Verify the update query was called
        mock_execute.assert_called_once()
        call_args = mock_execute.call_args
        sql = call_args[0][0]
        assert "UPDATE call_recordings SET" in sql
        assert "minutes_spent = %s" in sql
        assert "is_billable = %s" in sql
        assert "labour_type_id = %s" in sql


@pytest.mark.asyncio
async def test_call_recording_schema_validation():
    """Test that call recording schemas correctly validate billing fields."""
    from app.schemas.call_recordings import CallRecordingUpdate, CallRecordingResponse
    
    # Test update schema
    update_data = {
        "minutesSpent": 30,
        "isBillable": True,
        "labourTypeId": 5,
    }
    update_schema = CallRecordingUpdate(**update_data)
    assert update_schema.minutes_spent == 30
    assert update_schema.is_billable is True
    assert update_schema.labour_type_id == 5
    
    # Test response schema
    response_data = {
        "id": 1,
        "file_path": "/test.wav",
        "file_name": "test.wav",
        "call_date": datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
        "transcription_status": "completed",
        "created_at": datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
        "updated_at": datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
        "minutes_spent": 30,
        "is_billable": True,
        "labour_type_id": 5,
        "labour_type_name": "Remote Support",
        "labour_type_code": "RS",
    }
    response_schema = CallRecordingResponse(**response_data)
    assert response_schema.minutes_spent == 30
    assert response_schema.is_billable is True
    assert response_schema.labour_type_id == 5
    assert response_schema.labour_type_name == "Remote Support"
    assert response_schema.labour_type_code == "RS"
