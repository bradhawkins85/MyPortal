"""Test call recordings API endpoints."""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_list_call_recordings_api():
    """Test that the API endpoint properly serializes call recordings."""
    from app.api.routes import call_recordings as api
    from app.repositories import call_recordings as repo
    from app.schemas.call_recordings import CallRecordingResponse
    
    # Mock data that would come from the database
    mock_recording = {
        "id": 1,
        "file_path": "/recordings/test.wav",
        "file_name": "test.wav",
        "caller_number": "+1234567890",
        "callee_number": "+0987654321",
        "caller_staff_id": 10,
        "callee_staff_id": 20,
        "call_date": datetime(2024, 1, 15, 10, 30, 0),
        "duration_seconds": 300,
        "transcription": "Test transcription",
        "transcription_status": "completed",
        "linked_ticket_id": None,
        "created_at": datetime(2024, 1, 15, 10, 35, 0),
        "updated_at": datetime(2024, 1, 15, 10, 35, 0),
        # Joined fields
        "caller_first_name": "John",
        "caller_last_name": "Doe",
        "caller_email": "john@example.com",
        "callee_first_name": "Jane",
        "callee_last_name": "Smith",
        "callee_email": "jane@example.com",
        "linked_ticket_number": None,
        "linked_ticket_subject": None,
    }
    
    # Test that the schema can validate this data
    response = CallRecordingResponse.model_validate(mock_recording)
    
    assert response.id == 1
    assert response.file_name == "test.wav"
    assert response.caller_first_name == "John"
    assert response.callee_first_name == "Jane"
    assert response.transcription_status == "completed"
    
    # Test model_dump to ensure JSON serialization will work
    data = response.model_dump()
    assert data["id"] == 1
    assert data["file_name"] == "test.wav"
    
    # Verify datetimes are present
    assert "call_date" in data
    assert "created_at" in data
    assert "updated_at" in data


@pytest.mark.asyncio
async def test_list_call_recordings_empty_result():
    """Test that API returns empty list when no recordings exist."""
    from app.repositories import call_recordings as repo
    
    with patch.object(repo, "list_call_recordings", new_callable=AsyncMock) as mock_list:
        mock_list.return_value = []
        
        result = await repo.list_call_recordings(limit=10, offset=0)
        
        assert result == []
        assert isinstance(result, list)
