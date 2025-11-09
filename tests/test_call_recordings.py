"""Tests for call recordings functionality."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def sample_recording() -> dict[str, Any]:
    """Sample call recording data."""
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
        "linked_ticket_id": None,
        "created_at": datetime(2024, 1, 15, 10, 35, 0, tzinfo=timezone.utc),
        "updated_at": datetime(2024, 1, 15, 10, 35, 0, tzinfo=timezone.utc),
        "caller_first_name": "John",
        "caller_last_name": "Doe",
        "caller_email": "john@example.com",
        "callee_first_name": "Jane",
        "callee_last_name": "Smith",
        "callee_email": "jane@example.com",
        "linked_ticket_number": None,
        "linked_ticket_subject": None,
    }


@pytest.mark.asyncio
async def test_create_call_recording():
    """Test creating a call recording."""
    from app.repositories import call_recordings as repo
    
    with patch.object(repo.db, "execute", new_callable=AsyncMock) as mock_execute, \
         patch.object(repo.db, "fetch_one", new_callable=AsyncMock) as mock_fetch_one, \
         patch.object(repo, "_lookup_staff_by_phone", new_callable=AsyncMock) as mock_lookup:
        
        mock_lookup.side_effect = [10, 20]  # caller_staff_id, callee_staff_id
        mock_fetch_one.return_value = {
            "id": 1,
            "file_path": "/recordings/test.wav",
            "file_name": "test.wav",
            "caller_number": "+1234567890",
            "callee_number": "+0987654321",
            "caller_staff_id": 10,
            "callee_staff_id": 20,
            "call_date": datetime(2024, 1, 15, 10, 30, 0),
            "duration_seconds": 300,
            "transcription": None,
            "transcription_status": "pending",
            "linked_ticket_id": None,
            "created_at": datetime(2024, 1, 15, 10, 30, 0),
            "updated_at": datetime(2024, 1, 15, 10, 30, 0),
        }
        
        result = await repo.create_call_recording(
            file_path="/recordings/test.wav",
            file_name="test.wav",
            caller_number="+1234567890",
            callee_number="+0987654321",
            call_date=datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
            duration_seconds=300,
        )
        
        assert result["id"] == 1
        assert result["file_name"] == "test.wav"
        assert result["caller_staff_id"] == 10
        assert result["callee_staff_id"] == 20
        assert mock_execute.called
        assert mock_lookup.call_count == 2


@pytest.mark.asyncio
async def test_lookup_staff_by_phone_exact_match():
    """Test staff lookup with exact phone number match."""
    from app.repositories import call_recordings as repo
    
    with patch.object(repo.db, "fetch_one", new_callable=AsyncMock) as mock_fetch_one:
        mock_fetch_one.return_value = {"id": 42}
        
        result = await repo._lookup_staff_by_phone("+1234567890")
        
        assert result == 42
        assert mock_fetch_one.called


@pytest.mark.asyncio
async def test_lookup_staff_by_phone_no_match():
    """Test staff lookup with no matching phone number."""
    from app.repositories import call_recordings as repo
    
    with patch.object(repo.db, "fetch_one", new_callable=AsyncMock) as mock_fetch_one:
        mock_fetch_one.return_value = None
        
        result = await repo._lookup_staff_by_phone("+9999999999")
        
        assert result is None


@pytest.mark.asyncio
async def test_update_call_recording_transcription():
    """Test updating a call recording with transcription."""
    from app.repositories import call_recordings as repo
    
    with patch.object(repo.db, "execute", new_callable=AsyncMock) as mock_execute, \
         patch.object(repo, "get_call_recording_by_id", new_callable=AsyncMock) as mock_get:
        
        mock_get.return_value = {
            "id": 1,
            "transcription": "Updated transcription",
            "transcription_status": "completed",
            "linked_ticket_id": None,
        }
        
        result = await repo.update_call_recording(
            1,
            transcription="Updated transcription",
            transcription_status="completed"
        )
        
        assert result["id"] == 1
        assert result["transcription"] == "Updated transcription"
        assert result["transcription_status"] == "completed"
        assert mock_execute.called


@pytest.mark.asyncio
async def test_link_recording_to_ticket():
    """Test linking a call recording to a ticket."""
    from app.repositories import call_recordings as repo
    
    with patch.object(repo, "update_call_recording", new_callable=AsyncMock) as mock_update:
        mock_update.return_value = {
            "id": 1,
            "linked_ticket_id": 100,
        }
        
        result = await repo.link_recording_to_ticket(1, 100)
        
        assert result["linked_ticket_id"] == 100
        mock_update.assert_called_once_with(1, linked_ticket_id=100)


@pytest.mark.asyncio
async def test_list_call_recordings_with_filters():
    """Test listing call recordings with filters."""
    from app.repositories import call_recordings as repo
    
    with patch.object(repo.db, "fetch_all", new_callable=AsyncMock) as mock_fetch_all:
        mock_fetch_all.return_value = [
            {
                "id": 1,
                "file_name": "test1.wav",
                "transcription_status": "completed",
                "linked_ticket_id": None,
                "call_date": datetime(2024, 1, 15, 10, 30, 0),
                "caller_staff_id": 10,
                "callee_staff_id": 20,
                "duration_seconds": 300,
                "created_at": datetime(2024, 1, 15, 10, 35, 0),
                "updated_at": datetime(2024, 1, 15, 10, 35, 0),
            }
        ]
        
        result = await repo.list_call_recordings(
            limit=10,
            offset=0,
            search="test",
            transcription_status="completed",
        )
        
        assert len(result) == 1
        assert result[0]["file_name"] == "test1.wav"
        assert mock_fetch_all.called


@pytest.mark.asyncio
async def test_count_call_recordings():
    """Test counting call recordings."""
    from app.repositories import call_recordings as repo
    
    with patch.object(repo.db, "fetch_one", new_callable=AsyncMock) as mock_fetch_one:
        mock_fetch_one.return_value = {"count": 42}
        
        result = await repo.count_call_recordings()
        
        assert result == 42


@pytest.mark.asyncio
async def test_summarize_transcription():
    """Test summarizing a transcription using Ollama."""
    from app.services import call_recordings as service
    from app.repositories import integration_modules as modules_repo
    
    transcription = "This is a long test transcription about a customer issue."
    
    with patch.object(modules_repo, "get_module", new_callable=AsyncMock) as mock_get_module:
        # Mock Ollama module as disabled - should return truncated transcription
        mock_get_module.return_value = {
            "slug": "ollama",
            "enabled": False,
            "settings": {},
        }
        
        result = await service.summarize_transcription(transcription)
        
        # Should return original text when Ollama is not available
        assert result == transcription
        mock_get_module.assert_called_once_with("ollama")


@pytest.mark.asyncio
async def test_call_recording_response_schema():
    """Test CallRecordingResponse schema validation."""
    from app.schemas.call_recordings import CallRecordingResponse
    
    data = {
        "id": 1,
        "file_path": "/recordings/test.wav",
        "file_name": "test.wav",
        "caller_number": "+1234567890",
        "callee_number": "+0987654321",
        "call_date": datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
        "duration_seconds": 300,
        "transcription": "Test transcription",
        "transcription_status": "completed",
        "created_at": datetime(2024, 1, 15, 10, 35, 0, tzinfo=timezone.utc),
        "updated_at": datetime(2024, 1, 15, 10, 35, 0, tzinfo=timezone.utc),
    }
    
    response = CallRecordingResponse.model_validate(data)
    
    assert response.id == 1
    assert response.file_name == "test.wav"
    assert response.transcription_status == "completed"


@pytest.mark.asyncio
async def test_call_recording_create_schema():
    """Test CallRecordingCreate schema with aliases."""
    from app.schemas.call_recordings import CallRecordingCreate
    
    data = {
        "filePath": "/recordings/test.wav",
        "fileName": "test.wav",
        "callerNumber": "+1234567890",
        "calleeNumber": "+0987654321",
        "callDate": datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
        "durationSeconds": 300,
    }
    
    schema = CallRecordingCreate.model_validate(data)
    
    assert schema.file_path == "/recordings/test.wav"
    assert schema.file_name == "test.wav"
    assert schema.caller_number == "+1234567890"
    assert schema.duration_seconds == 300
