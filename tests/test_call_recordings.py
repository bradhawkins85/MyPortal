"""Tests for call recordings functionality."""
from __future__ import annotations

import json
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
        
        mock_lookup.return_value = 10  # Only called once for phone_number
        mock_fetch_one.return_value = {
            "id": 1,
            "file_path": "/recordings/test.wav",
            "file_name": "test.wav",
            "phone_number": "+1234567890",
            "caller_staff_id": 10,
            "callee_staff_id": None,
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
            phone_number="+1234567890",
            call_date=datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
            duration_seconds=300,
        )
        
        assert result["id"] == 1
        assert result["file_name"] == "test.wav"
        assert result["phone_number"] == "+1234567890"
        assert result["caller_staff_id"] == 10
        assert mock_execute.called
        assert mock_lookup.call_count == 1


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
async def test_list_call_recordings_sorted_by_created_at():
    """Test that call recordings are sorted by created_at DESC (most recent first)."""
    from app.repositories import call_recordings as repo
    
    with patch.object(repo.db, "fetch_all", new_callable=AsyncMock) as mock_fetch_all:
        # Mock data with different created_at timestamps
        mock_fetch_all.return_value = [
            {
                "id": 3,
                "file_name": "newest.wav",
                "transcription_status": "pending",
                "linked_ticket_id": None,
                "call_date": datetime(2024, 1, 10, 10, 0, 0),
                "caller_staff_id": 10,
                "callee_staff_id": 20,
                "duration_seconds": 300,
                "created_at": datetime(2024, 1, 20, 15, 0, 0),  # Most recent
                "updated_at": datetime(2024, 1, 20, 15, 0, 0),
            },
            {
                "id": 2,
                "file_name": "middle.wav",
                "transcription_status": "pending",
                "linked_ticket_id": None,
                "call_date": datetime(2024, 1, 15, 10, 0, 0),
                "caller_staff_id": 10,
                "callee_staff_id": 20,
                "duration_seconds": 300,
                "created_at": datetime(2024, 1, 18, 12, 0, 0),  # Middle
                "updated_at": datetime(2024, 1, 18, 12, 0, 0),
            },
            {
                "id": 1,
                "file_name": "oldest.wav",
                "transcription_status": "pending",
                "linked_ticket_id": None,
                "call_date": datetime(2024, 1, 20, 10, 0, 0),  # Most recent call_date but oldest created_at
                "caller_staff_id": 10,
                "callee_staff_id": 20,
                "duration_seconds": 300,
                "created_at": datetime(2024, 1, 16, 9, 0, 0),  # Oldest
                "updated_at": datetime(2024, 1, 16, 9, 0, 0),
            }
        ]
        
        result = await repo.list_call_recordings(limit=10, offset=0)
        
        # Verify the function was called
        assert mock_fetch_all.called
        
        # Verify SQL contains ORDER BY created_at DESC
        call_args = mock_fetch_all.call_args
        sql_query = call_args[0][0]
        assert "ORDER BY cr.created_at DESC" in sql_query, "Query should order by created_at DESC"
        
        # Verify results are in expected order (newest created_at first)
        assert len(result) == 3
        assert result[0]["file_name"] == "newest.wav"
        assert result[1]["file_name"] == "middle.wav"
        assert result[2]["file_name"] == "oldest.wav"


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
async def test_transcribe_recording_with_file_upload():
    """Test transcribing a call recording with file upload to /asr endpoint."""
    from app.services import call_recordings as service
    from app.repositories import call_recordings as call_recordings_repo
    from app.repositories import integration_modules as modules_repo
    import tempfile
    import os
    
    # Create a temporary audio file for testing
    with tempfile.NamedTemporaryFile(mode='wb', suffix='.wav', delete=False) as tmp_file:
        tmp_file.write(b"fake audio data")
        temp_audio_path = tmp_file.name
    
    try:
        recording = {
            "id": 1,
            "file_path": temp_audio_path,
            "file_name": "test_call.wav",
            "transcription": None,
            "transcription_status": "pending",
        }
        
        with patch.object(call_recordings_repo, "get_call_recording_by_id", new_callable=AsyncMock) as mock_get, \
             patch.object(modules_repo, "get_module", new_callable=AsyncMock) as mock_get_module, \
             patch.object(call_recordings_repo, "update_call_recording", new_callable=AsyncMock) as mock_update, \
             patch("httpx.AsyncClient") as mock_client_class:
            
            # Mock the recording lookup
            mock_get.return_value = recording
            
            # Mock WhisperX module settings
            mock_get_module.return_value = {
                "slug": "whisperx",
                "enabled": True,
                "settings": {
                    "base_url": "http://localhost:9000",
                    "api_key": "test-api-key",
                    "language": "en",
                },
            }
            
            # Mock httpx client response
            mock_response = MagicMock()
            mock_response.json.return_value = {"text": "This is a transcribed text from the audio file."}
            mock_response.raise_for_status = MagicMock()
            
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client
            
            # Mock the update to return completed recording
            mock_update.return_value = {
                **recording,
                "transcription": "This is a transcribed text from the audio file.",
                "transcription_status": "completed",
            }
            
            # Call the transcribe function
            result = await service.transcribe_recording(1, force=False)
            
            # Verify the result
            assert result["transcription"] == "This is a transcribed text from the audio file."
            assert result["transcription_status"] == "completed"
            
            # Verify the API was called correctly
            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args
            
            # Verify endpoint is /asr
            assert call_args[0][0] == "http://localhost:9000/asr"
            
            # Verify files were passed
            assert "files" in call_args[1]
            
            # Verify authorization header
            assert "headers" in call_args[1]
            assert call_args[1]["headers"]["Authorization"] == "Bearer test-api-key"

    finally:
        # Clean up temp file
        if os.path.exists(temp_audio_path):
            os.unlink(temp_audio_path)


@pytest.mark.asyncio
async def test_transcribe_recording_invalid_json_response():
    """Test handling when transcription service returns invalid JSON."""
    from app.services import call_recordings as service
    from app.repositories import call_recordings as call_recordings_repo
    from app.repositories import integration_modules as modules_repo
    import tempfile
    import os

    with tempfile.NamedTemporaryFile(mode="wb", suffix=".wav", delete=False) as tmp_file:
        tmp_file.write(b"fake audio data")
        temp_audio_path = tmp_file.name

    try:
        recording = {
            "id": 1,
            "file_path": temp_audio_path,
            "file_name": "test_call.wav",
            "transcription": None,
            "transcription_status": "pending",
        }

        with patch.object(call_recordings_repo, "get_call_recording_by_id", new_callable=AsyncMock) as mock_get, \
             patch.object(modules_repo, "get_module", new_callable=AsyncMock) as mock_get_module, \
             patch.object(call_recordings_repo, "update_call_recording", new_callable=AsyncMock) as mock_update, \
             patch("httpx.AsyncClient") as mock_client_class:

            mock_get.return_value = recording

            mock_get_module.return_value = {
                "slug": "whisperx",
                "enabled": True,
                "settings": {
                    "base_url": "http://localhost:9000",
                },
            }

            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.json.side_effect = ValueError("Expecting value: line 1 column 1 (char 0)")
            mock_response.text = ""

            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            with pytest.raises(ValueError, match="Invalid response from transcription service"):
                await service.transcribe_recording(1, force=False)

            assert mock_client.post.await_count == 1

            assert mock_update.await_args_list[0].args[0] == 1
            assert mock_update.await_args_list[0].kwargs == {
                "transcription_status": "processing",
            }
            assert mock_update.await_args_list[1].args[0] == 1
            assert mock_update.await_args_list[1].kwargs == {
                "transcription_status": "failed",
            }

    finally:
        if os.path.exists(temp_audio_path):
            os.unlink(temp_audio_path)


@pytest.mark.asyncio
async def test_transcribe_recording_file_not_found():
    """Test transcription handling when audio file doesn't exist."""
    from app.services import call_recordings as service
    from app.repositories import call_recordings as call_recordings_repo
    from app.repositories import integration_modules as modules_repo
    
    recording = {
        "id": 1,
        "file_path": "/nonexistent/path/audio.wav",
        "file_name": "audio.wav",
        "transcription": None,
        "transcription_status": "pending",
    }
    
    with patch.object(call_recordings_repo, "get_call_recording_by_id", new_callable=AsyncMock) as mock_get, \
         patch.object(modules_repo, "get_module", new_callable=AsyncMock) as mock_get_module, \
         patch.object(call_recordings_repo, "update_call_recording", new_callable=AsyncMock) as mock_update:
        
        mock_get.return_value = recording
        
        mock_get_module.return_value = {
            "slug": "whisperx",
            "enabled": True,
            "settings": {
                "base_url": "http://localhost:9000",
            },
        }
        
        # Should raise ValueError for file not found
        with pytest.raises(ValueError, match="Audio file not found"):
            await service.transcribe_recording(1, force=False)
        
        # Verify status was updated to failed
        mock_update.assert_called_with(1, transcription_status="failed")


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
        "phoneNumber": "+1234567890",
        "callDate": datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
        "durationSeconds": 300,
    }

    schema = CallRecordingCreate.model_validate(data)

    assert schema.file_path == "/recordings/test.wav"
    assert schema.file_name == "test.wav"
    assert schema.phone_number == "+1234567890"
    assert schema.duration_seconds == 300


@pytest.mark.asyncio
async def test_sync_recordings_from_filesystem_creates_records(tmp_path):
    """The sync helper should create new call recording entries from metadata files."""
    from app.services import call_recordings as service
    from app.repositories import call_recordings as repo

    audio_path = tmp_path / "call1.wav"
    audio_path.write_bytes(b"fake audio")
    metadata = {
        "callDate": "2024-01-15T10:30:00Z",
        "phone_number": "+123456789",
        "duration_seconds": "300",
        "transcription": "Call transcript",
        "transcription_status": "completed",
    }
    (tmp_path / "call1.json").write_text(json.dumps(metadata), encoding="utf-8")

    with patch.object(repo, "get_call_recording_by_file_path", new_callable=AsyncMock) as mock_get, \
         patch.object(repo, "create_call_recording", new_callable=AsyncMock) as mock_create, \
         patch.object(repo, "update_call_recording", new_callable=AsyncMock) as mock_update:
        mock_get.return_value = None
        mock_create.return_value = {"id": 1}

        result = await service.sync_recordings_from_filesystem(str(tmp_path))

        assert result["created"] == 1
        assert result["updated"] == 0
        mock_update.assert_not_called()
        assert mock_create.await_count == 1

        call_kwargs = mock_create.await_args.kwargs
        assert call_kwargs["file_path"] == str(audio_path)
        assert call_kwargs["file_name"] == "call1.wav"
        assert call_kwargs["phone_number"] == "+123456789"
        assert call_kwargs["duration_seconds"] == 300
        assert call_kwargs["transcription"] == "Call transcript"
        assert call_kwargs["transcription_status"] == "completed"
        assert isinstance(call_kwargs["call_date"], datetime)


@pytest.mark.asyncio
async def test_sync_recordings_from_filesystem_updates_existing(tmp_path):
    """Existing recordings should be updated when transcripts are discovered."""
    from app.services import call_recordings as service
    from app.repositories import call_recordings as repo

    audio_path = tmp_path / "call2.wav"
    audio_path.write_bytes(b"fake audio")
    (tmp_path / "call2.txt").write_text("Fresh transcript", encoding="utf-8")

    existing = {"id": 42, "transcription": None, "transcription_status": "pending"}

    with patch.object(repo, "get_call_recording_by_file_path", new_callable=AsyncMock) as mock_get, \
         patch.object(repo, "create_call_recording", new_callable=AsyncMock) as mock_create, \
         patch.object(repo, "update_call_recording", new_callable=AsyncMock) as mock_update:
        mock_get.return_value = existing

        result = await service.sync_recordings_from_filesystem(str(tmp_path))

        assert result["updated"] == 1
        mock_create.assert_not_called()
        mock_update.assert_awaited_once_with(
            42,
            transcription="Fresh transcript",
            transcription_status="completed",
        )


@pytest.mark.asyncio
async def test_sync_recordings_from_filesystem_missing_path(tmp_path):
    """A missing recordings path should raise FileNotFoundError."""
    from app.services import call_recordings as service

    missing = tmp_path / "does-not-exist"
    with pytest.raises(FileNotFoundError):
        await service.sync_recordings_from_filesystem(str(missing))


@pytest.mark.asyncio
async def test_sync_recordings_preserves_processing_status(tmp_path):
    """Sync should not override 'processing' status when no new transcription is found."""
    from app.services import call_recordings as service
    from app.repositories import call_recordings as repo

    audio_path = tmp_path / "call3.wav"
    audio_path.write_bytes(b"fake audio")
    
    # Create metadata file with "pending" status
    metadata = {
        "transcription_status": "pending",
    }
    (tmp_path / "call3.json").write_text(json.dumps(metadata), encoding="utf-8")

    # Existing recording has "processing" status (transcription in progress)
    existing = {
        "id": 99,
        "transcription": None,
        "transcription_status": "processing",
    }

    with patch.object(repo, "get_call_recording_by_file_path", new_callable=AsyncMock) as mock_get, \
         patch.object(repo, "create_call_recording", new_callable=AsyncMock) as mock_create, \
         patch.object(repo, "update_call_recording", new_callable=AsyncMock) as mock_update:
        mock_get.return_value = existing

        result = await service.sync_recordings_from_filesystem(str(tmp_path))

        # Should skip the update entirely - no new transcription data
        assert result["skipped"] == 1
        assert result["updated"] == 0
        mock_create.assert_not_called()
        mock_update.assert_not_called()


@pytest.mark.asyncio
async def test_sync_recordings_preserves_completed_status(tmp_path):
    """Sync should not override 'completed' status when no new transcription is found."""
    from app.services import call_recordings as service
    from app.repositories import call_recordings as repo

    audio_path = tmp_path / "call4.wav"
    audio_path.write_bytes(b"fake audio")
    
    # Create metadata file with "pending" status
    metadata = {
        "transcription_status": "pending",
    }
    (tmp_path / "call4.json").write_text(json.dumps(metadata), encoding="utf-8")

    # Existing recording has "completed" status with transcription
    existing = {
        "id": 100,
        "transcription": "This is already transcribed text.",
        "transcription_status": "completed",
    }

    with patch.object(repo, "get_call_recording_by_file_path", new_callable=AsyncMock) as mock_get, \
         patch.object(repo, "create_call_recording", new_callable=AsyncMock) as mock_create, \
         patch.object(repo, "update_call_recording", new_callable=AsyncMock) as mock_update:
        mock_get.return_value = existing

        result = await service.sync_recordings_from_filesystem(str(tmp_path))

        # Should skip the update - existing transcription matches, no status change
        assert result["skipped"] == 1
        assert result["updated"] == 0
        mock_create.assert_not_called()
        mock_update.assert_not_called()


@pytest.mark.asyncio
async def test_sync_recordings_preserves_failed_status(tmp_path):
    """Sync should not override 'failed' status when no new transcription is found."""
    from app.services import call_recordings as service
    from app.repositories import call_recordings as repo

    audio_path = tmp_path / "call5.wav"
    audio_path.write_bytes(b"fake audio")
    
    # Create metadata file with "pending" status
    metadata = {
        "transcription_status": "pending",
    }
    (tmp_path / "call5.json").write_text(json.dumps(metadata), encoding="utf-8")

    # Existing recording has "failed" status (transcription failed)
    existing = {
        "id": 101,
        "transcription": None,
        "transcription_status": "failed",
    }

    with patch.object(repo, "get_call_recording_by_file_path", new_callable=AsyncMock) as mock_get, \
         patch.object(repo, "create_call_recording", new_callable=AsyncMock) as mock_create, \
         patch.object(repo, "update_call_recording", new_callable=AsyncMock) as mock_update:
        mock_get.return_value = existing

        result = await service.sync_recordings_from_filesystem(str(tmp_path))

        # Should skip the update - no new transcription data
        assert result["skipped"] == 1
        assert result["updated"] == 0
        mock_create.assert_not_called()
        mock_update.assert_not_called()


@pytest.mark.asyncio
async def test_extract_phone_from_title():
    """Test phone number extraction from recording title."""
    from app.services.call_recordings import _extract_phone_from_title
    
    # Test with + prefix
    title1 = "Recording +61439531124 0001 2025-10-13 14:56"
    phone1 = _extract_phone_from_title(title1)
    assert phone1 == "+61439531124"
    
    # Test without + prefix
    title2 = 'Recording in_memory_ring_flow:{"items":[{"id":"contact-24e3e112"}]} 61410553956 2025-10-24 15:43'
    phone2 = _extract_phone_from_title(title2)
    assert phone2 == "61410553956"
    
    # Test with no phone number
    title3 = "Recording without phone"
    phone3 = _extract_phone_from_title(title3)
    assert phone3 is None
    
    # Test with None
    phone4 = _extract_phone_from_title(None)
    assert phone4 is None


@pytest.mark.asyncio
async def test_extract_datetime_from_title():
    """Test date/time extraction from recording title."""
    from app.services.call_recordings import _extract_datetime_from_title
    
    # Test standard format
    title1 = "Recording +61439531124 0001 2025-10-13 14:56"
    date1 = _extract_datetime_from_title(title1)
    assert date1 is not None
    assert date1.year == 2025
    assert date1.month == 10
    assert date1.day == 13
    assert date1.hour == 14
    assert date1.minute == 56
    
    # Test with JSON in title
    title2 = 'Recording in_memory_ring_flow:{"items":[]} 61410553956 2025-10-24 15:43'
    date2 = _extract_datetime_from_title(title2)
    assert date2 is not None
    assert date2.year == 2025
    assert date2.month == 10
    assert date2.day == 24
    assert date2.hour == 15
    assert date2.minute == 43
    
    # Test with no date
    title3 = "Recording without date"
    date3 = _extract_datetime_from_title(title3)
    assert date3 is None
    
    # Test with None
    date4 = _extract_datetime_from_title(None)
    assert date4 is None


@pytest.mark.asyncio
async def test_sync_recordings_uses_title_extraction(tmp_path):
    """Test that sync uses phone number and date from MP3 title."""
    from app.services.call_recordings import _read_audio_title
    from app.services import call_recordings as service
    from app.repositories import call_recordings as repo
    
    # Create a test MP3 file (empty is fine for this test)
    audio_path = tmp_path / "test_call.mp3"
    audio_path.write_bytes(b"fake mp3 data")
    
    # Mock the title reading to return a sample title
    test_title = "Recording +61439531124 0001 2025-10-13 14:56"
    
    with patch.object(service, "_read_audio_title", return_value=test_title) as mock_read_title, \
         patch.object(repo, "get_call_recording_by_file_path", new_callable=AsyncMock) as mock_get, \
         patch.object(repo, "create_call_recording", new_callable=AsyncMock) as mock_create:
        
        mock_get.return_value = None  # No existing recording
        mock_create.return_value = {"id": 1}
        
        result = await service.sync_recordings_from_filesystem(str(tmp_path))
        
        assert result["created"] == 1
        assert mock_create.await_count == 1
        
        # Verify phone number was extracted from title
        call_kwargs = mock_create.await_args.kwargs
        assert call_kwargs["phone_number"] == "+61439531124"
        
        # Verify date was extracted from title
        assert call_kwargs["call_date"] is not None
        assert call_kwargs["call_date"].year == 2025
        assert call_kwargs["call_date"].month == 10
        assert call_kwargs["call_date"].day == 13


@pytest.mark.asyncio
async def test_create_call_recording_with_phone_number():
    """Test creating a call recording with phone_number field."""
    from app.repositories import call_recordings as repo
    
    with patch.object(repo.db, "execute", new_callable=AsyncMock) as mock_execute, \
         patch.object(repo.db, "fetch_one", new_callable=AsyncMock) as mock_fetch_one, \
         patch.object(repo, "_lookup_staff_by_phone", new_callable=AsyncMock) as mock_lookup:
        
        mock_lookup.return_value = 42  # staff_id
        mock_fetch_one.return_value = {
            "id": 1,
            "file_path": "/recordings/test.mp3",
            "file_name": "test.mp3",
            "phone_number": "+61439531124",
            "caller_staff_id": 42,
            "callee_staff_id": None,
            "call_date": datetime(2025, 10, 13, 14, 56, 0),
            "duration_seconds": 120,
            "transcription": None,
            "transcription_status": "pending",
            "linked_ticket_id": None,
            "created_at": datetime(2025, 10, 13, 15, 0, 0),
            "updated_at": datetime(2025, 10, 13, 15, 0, 0),
        }
        
        result = await repo.create_call_recording(
            file_path="/recordings/test.mp3",
            file_name="test.mp3",
            phone_number="+61439531124",
            call_date=datetime(2025, 10, 13, 14, 56, 0, tzinfo=timezone.utc),
            duration_seconds=120,
        )
        
        assert result["id"] == 1
        assert result["phone_number"] == "+61439531124"
        assert result["caller_staff_id"] == 42
        assert mock_execute.called
        assert mock_lookup.call_count == 1
