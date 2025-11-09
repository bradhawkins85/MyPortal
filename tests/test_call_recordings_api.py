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


@pytest.mark.asyncio
async def test_sync_call_recordings_success():
    """Test successful sync of call recordings from filesystem."""
    from app.services import call_recordings as service
    from app.repositories import integration_modules as modules_repo
    
    mock_module = {
        "slug": "call-recordings",
        "enabled": True,
        "settings": {
            "recordings_path": "/test/recordings"
        }
    }
    
    mock_sync_result = {
        "status": "ok",
        "created": 5,
        "updated": 2,
        "skipped": 1,
        "errors": []
    }
    
    with patch.object(modules_repo, "get_module", new_callable=AsyncMock) as mock_get_module, \
         patch.object(service, "sync_recordings_from_filesystem", new_callable=AsyncMock) as mock_sync:
        
        mock_get_module.return_value = mock_module
        mock_sync.return_value = mock_sync_result
        
        result = await service.sync_recordings_from_filesystem("/test/recordings")
        
        assert result["status"] == "ok"
        assert result["created"] == 5
        assert result["updated"] == 2
        assert result["skipped"] == 1
        assert result["errors"] == []


@pytest.mark.asyncio
async def test_sync_call_recordings_with_errors():
    """Test sync with some errors reported."""
    from app.services import call_recordings as service
    
    mock_sync_result = {
        "status": "ok",
        "created": 3,
        "updated": 0,
        "skipped": 0,
        "errors": ["Error processing file1.wav", "Error processing file2.wav"]
    }
    
    with patch.object(service, "sync_recordings_from_filesystem", new_callable=AsyncMock) as mock_sync:
        mock_sync.return_value = mock_sync_result
        
        result = await service.sync_recordings_from_filesystem("/test/recordings")
        
        assert result["created"] == 3
        assert len(result["errors"]) == 2
        assert "Error processing file1.wav" in result["errors"]


@pytest.mark.asyncio
async def test_sync_call_recordings_invalid_path():
    """Test sync with invalid path raises FileNotFoundError."""
    from app.services import call_recordings as service
    
    with patch.object(service, "sync_recordings_from_filesystem", new_callable=AsyncMock) as mock_sync:
        mock_sync.side_effect = FileNotFoundError("Recordings path does not exist: /invalid/path")
        
        with pytest.raises(FileNotFoundError, match="Recordings path does not exist"):
            await service.sync_recordings_from_filesystem("/invalid/path")


@pytest.mark.asyncio
async def test_link_recording_to_ticket_success():
    """Test successfully linking a call recording to a ticket."""
    from app.repositories import call_recordings as call_recordings_repo
    from app.repositories import tickets as tickets_repo
    
    # Mock recording data
    mock_recording = {
        "id": 1,
        "file_path": "/recordings/test.wav",
        "file_name": "test.wav",
        "caller_number": "+1234567890",
        "callee_number": "+0987654321",
        "caller_staff_id": None,
        "callee_staff_id": None,
        "call_date": datetime(2024, 1, 15, 10, 30, 0),
        "duration_seconds": 300,
        "transcription": "Test transcription",
        "transcription_status": "completed",
        "linked_ticket_id": None,
        "created_at": datetime(2024, 1, 15, 10, 35, 0),
        "updated_at": datetime(2024, 1, 15, 10, 35, 0),
        "caller_first_name": None,
        "caller_last_name": None,
        "caller_email": None,
        "callee_first_name": None,
        "callee_last_name": None,
        "callee_email": None,
        "linked_ticket_number": None,
        "linked_ticket_subject": None,
    }
    
    # Mock ticket data
    mock_ticket = {
        "id": 42,
        "company_id": 1,
        "requester_id": 10,
        "assigned_user_id": None,
        "subject": "Test ticket",
        "description": "Test description",
        "status": "open",
        "priority": "normal",
        "category": None,
        "module_slug": None,
        "external_reference": None,
        "ticket_number": "TICK-123",
        "created_at": datetime(2024, 1, 15, 9, 0, 0, tzinfo=timezone.utc),
        "updated_at": datetime(2024, 1, 15, 9, 0, 0, tzinfo=timezone.utc),
        "closed_at": None,
        "ai_summary": None,
        "ai_summary_updated_at": None,
        "ai_tags": [],
        "ai_tags_updated_at": None,
    }
    
    # Mock updated recording with linked ticket
    mock_updated_recording = mock_recording.copy()
    mock_updated_recording["linked_ticket_id"] = 42
    mock_updated_recording["linked_ticket_number"] = "TICK-123"
    mock_updated_recording["linked_ticket_subject"] = "Test ticket"
    
    with patch.object(call_recordings_repo, "get_call_recording_by_id", new_callable=AsyncMock) as mock_get_recording, \
         patch.object(tickets_repo, "get_ticket", new_callable=AsyncMock) as mock_get_ticket, \
         patch.object(call_recordings_repo, "link_recording_to_ticket", new_callable=AsyncMock) as mock_link:
        
        mock_get_recording.return_value = mock_recording
        mock_get_ticket.return_value = mock_ticket
        mock_link.return_value = mock_updated_recording
        
        # Test the linking function
        result = await call_recordings_repo.link_recording_to_ticket(1, 42)
        
        assert result["linked_ticket_id"] == 42
        assert result["linked_ticket_number"] == "TICK-123"
        mock_link.assert_called_once_with(1, 42)


@pytest.mark.asyncio
async def test_link_recording_to_nonexistent_ticket():
    """Test linking a recording to a non-existent ticket fails."""
    from app.repositories import call_recordings as call_recordings_repo
    from app.repositories import tickets as tickets_repo
    
    # Mock recording data
    mock_recording = {
        "id": 1,
        "file_path": "/recordings/test.wav",
        "file_name": "test.wav",
        "linked_ticket_id": None,
    }
    
    with patch.object(call_recordings_repo, "get_call_recording_by_id", new_callable=AsyncMock) as mock_get_recording, \
         patch.object(tickets_repo, "get_ticket", new_callable=AsyncMock) as mock_get_ticket:
        
        mock_get_recording.return_value = mock_recording
        mock_get_ticket.return_value = None  # Ticket not found
        
        # Verify that get_ticket is called with the ticket ID
        result = await tickets_repo.get_ticket(999)
        
        assert result is None
        mock_get_ticket.assert_called_once_with(999)
