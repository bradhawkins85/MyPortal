"""Test case for plain text response from WhisperX."""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, mock_open
from pathlib import Path


@pytest.mark.asyncio
async def test_transcribe_recording_accepts_plain_text_response():
    """Test that transcription accepts plain text response from WhisperX with HTTP 200."""
    from app.services import call_recordings as service
    from app.repositories import call_recordings as repo
    from app.repositories import integration_modules as modules_repo
    from app.services import webhook_monitor
    
    # Mock recording data
    mock_recording = {
        "id": 1,
        "file_path": "/test/recordings/test.wav",
        "file_name": "test.wav",
        "caller_number": "+1234567890",
        "callee_number": "+0987654321",
        "call_date": datetime(2024, 1, 15, 10, 30, 0),
        "duration_seconds": 300,
        "transcription": None,
        "transcription_status": "pending",
    }
    
    # Mock WhisperX module settings
    mock_module = {
        "slug": "whisperx",
        "enabled": True,
        "settings": {
            "base_url": "http://whisperx.example.com",
            "api_key": "test-api-key",
            "language": "en",
        }
    }
    
    # Mock webhook event
    mock_webhook_event = {
        "id": 100,
        "name": "whisperx_transcription_1",
        "target_url": "http://whisperx.example.com/asr",
        "status": "pending",
    }
    
    # Mock successful WhisperX response with PLAIN TEXT (not JSON)
    plain_text_transcription = "This is a test transcription from WhisperX"
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = plain_text_transcription.encode('utf-8')
    mock_response.text = plain_text_transcription
    mock_response.headers = {"content-type": "text/plain"}
    # When .json() is called on plain text, it should raise ValueError
    mock_response.json.side_effect = ValueError("Expecting value: line 1 column 1 (char 0)")
    
    with patch.object(repo, "get_call_recording_by_id", new_callable=AsyncMock) as mock_get, \
         patch.object(modules_repo, "get_module", new_callable=AsyncMock) as mock_get_module, \
         patch.object(repo, "update_call_recording", new_callable=AsyncMock) as mock_update, \
         patch.object(webhook_monitor, "create_manual_event", new_callable=AsyncMock) as mock_create_webhook, \
         patch.object(webhook_monitor, "record_manual_success", new_callable=AsyncMock) as mock_webhook_success, \
         patch("builtins.open", mock_open(read_data=b"fake audio data")), \
         patch("pathlib.Path.stat") as mock_stat, \
         patch("httpx.AsyncClient") as mock_client:
        
        # Setup mocks
        mock_get.return_value = mock_recording
        mock_get_module.return_value = mock_module
        mock_update.return_value = {**mock_recording, "transcription": plain_text_transcription, "transcription_status": "completed"}
        mock_create_webhook.return_value = mock_webhook_event
        
        mock_stat_result = MagicMock()
        mock_stat_result.st_size = 12345
        mock_stat.return_value = mock_stat_result
        
        # Setup httpx client mock
        mock_client_instance = MagicMock()
        mock_client_instance.__aenter__.return_value = mock_client_instance
        mock_client_instance.__aexit__.return_value = None
        mock_client_instance.post = AsyncMock(return_value=mock_response)
        mock_client.return_value = mock_client_instance
        
        # Call the function - it should succeed with plain text response
        result = await service.transcribe_recording(1, force=False)
        
        # Verify recording was updated with the plain text transcription
        assert mock_update.call_count == 2  # Once for "processing", once for "completed"
        final_update_call = mock_update.call_args_list[1]
        assert final_update_call.args[0] == 1
        assert final_update_call.kwargs["transcription"] == plain_text_transcription
        assert final_update_call.kwargs["transcription_status"] == "completed"
        
        # Verify webhook success was recorded
        mock_webhook_success.assert_called_once()
        
        # Verify result
        assert result["transcription"] == plain_text_transcription
        assert result["transcription_status"] == "completed"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
