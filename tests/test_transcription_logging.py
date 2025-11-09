"""Test transcription logging and webhook integration."""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, mock_open
from pathlib import Path


@pytest.mark.asyncio
async def test_transcribe_recording_with_logging_and_webhook():
    """Test that transcription logs details and creates webhook events."""
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
    
    # Mock successful WhisperX response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b'{"text": "This is a test transcription"}'
    mock_response.text = '{"text": "This is a test transcription"}'
    mock_response.headers = {"content-type": "application/json"}
    mock_response.json.return_value = {"text": "This is a test transcription"}
    
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
        mock_update.return_value = {**mock_recording, "transcription": "This is a test transcription", "transcription_status": "completed"}
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
        
        # Call the function
        result = await service.transcribe_recording(1, force=False)
        
        # Verify recording was fetched
        mock_get.assert_called_once_with(1)
        
        # Verify module settings were fetched
        mock_get_module.assert_called_once_with("whisperx")
        
        # Verify webhook event was created
        mock_create_webhook.assert_called_once()
        webhook_call_args = mock_create_webhook.call_args
        assert webhook_call_args.kwargs["name"] == "whisperx_transcription_1"
        assert webhook_call_args.kwargs["target_url"] == "http://whisperx.example.com/asr"
        assert webhook_call_args.kwargs["payload"]["recording_id"] == 1
        assert webhook_call_args.kwargs["payload"]["file_name"] == "test.wav"
        
        # Verify HTTP request was made
        mock_client_instance.post.assert_called_once()
        post_call_args = mock_client_instance.post.call_args
        assert post_call_args.args[0] == "http://whisperx.example.com/asr"
        
        # Verify webhook success was recorded
        mock_webhook_success.assert_called_once()
        success_call_args = mock_webhook_success.call_args
        assert success_call_args.args[0] == 100  # event_id is first positional arg
        assert success_call_args.kwargs["attempt_number"] == 1
        assert success_call_args.kwargs["response_status"] == 200
        
        # Verify recording was updated with transcription
        assert mock_update.call_count == 2  # Once for "processing", once for "completed"
        final_update_call = mock_update.call_args_list[1]
        assert final_update_call.args[0] == 1
        assert final_update_call.kwargs["transcription"] == "This is a test transcription"
        assert final_update_call.kwargs["transcription_status"] == "completed"
        
        # Verify result
        assert result["transcription"] == "This is a test transcription"
        assert result["transcription_status"] == "completed"


@pytest.mark.asyncio
async def test_transcribe_recording_http_error_creates_webhook_failure():
    """Test that HTTP errors are logged and recorded as webhook failures."""
    from app.services import call_recordings as service
    from app.repositories import call_recordings as repo
    from app.repositories import integration_modules as modules_repo
    from app.services import webhook_monitor
    import httpx
    
    # Mock recording data
    mock_recording = {
        "id": 2,
        "file_path": "/test/recordings/test2.wav",
        "file_name": "test2.wav",
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
        }
    }
    
    # Mock webhook event
    mock_webhook_event = {
        "id": 101,
        "name": "whisperx_transcription_2",
        "target_url": "http://whisperx.example.com/asr",
        "status": "pending",
    }
    
    # Mock error response
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"
    mock_response.headers = {"content-type": "text/plain"}
    
    with patch.object(repo, "get_call_recording_by_id", new_callable=AsyncMock) as mock_get, \
         patch.object(modules_repo, "get_module", new_callable=AsyncMock) as mock_get_module, \
         patch.object(repo, "update_call_recording", new_callable=AsyncMock) as mock_update, \
         patch.object(webhook_monitor, "create_manual_event", new_callable=AsyncMock) as mock_create_webhook, \
         patch.object(webhook_monitor, "record_manual_failure", new_callable=AsyncMock) as mock_webhook_failure, \
         patch("builtins.open", mock_open(read_data=b"fake audio data")), \
         patch("pathlib.Path.stat") as mock_stat, \
         patch("httpx.AsyncClient") as mock_client:
        
        # Setup mocks
        mock_get.return_value = mock_recording
        mock_get_module.return_value = mock_module
        mock_create_webhook.return_value = mock_webhook_event
        
        mock_stat_result = MagicMock()
        mock_stat_result.st_size = 12345
        mock_stat.return_value = mock_stat_result
        
        # Setup httpx client mock to raise HTTPStatusError
        mock_client_instance = MagicMock()
        mock_client_instance.__aenter__.return_value = mock_client_instance
        mock_client_instance.__aexit__.return_value = None
        
        # Create the error with response attribute
        error = httpx.HTTPStatusError("Server error", request=MagicMock(), response=mock_response)
        mock_client_instance.post = AsyncMock(side_effect=error)
        mock_client.return_value = mock_client_instance
        
        # Call the function and expect it to raise ValueError
        with pytest.raises(ValueError, match="Failed to transcribe recording"):
            await service.transcribe_recording(2, force=False)
        
        # Verify webhook failure was recorded
        mock_webhook_failure.assert_called_once()
        failure_call_args = mock_webhook_failure.call_args
        assert failure_call_args.args[0] == 101  # event_id is first positional arg
        assert failure_call_args.kwargs["attempt_number"] == 1
        assert failure_call_args.kwargs["status"] == "failed"
        assert failure_call_args.kwargs["response_status"] == 500
        assert "Internal Server Error" in failure_call_args.kwargs["response_body"]
        
        # Verify recording was marked as failed
        final_update_call = mock_update.call_args_list[-1]
        assert final_update_call.kwargs["transcription_status"] == "failed"


@pytest.mark.asyncio
async def test_transcribe_recording_file_not_found_creates_webhook_failure():
    """Test that file not found errors are logged and recorded as webhook failures."""
    from app.services import call_recordings as service
    from app.repositories import call_recordings as repo
    from app.repositories import integration_modules as modules_repo
    from app.services import webhook_monitor
    
    # Mock recording data
    mock_recording = {
        "id": 3,
        "file_path": "/test/recordings/missing.wav",
        "file_name": "missing.wav",
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
        }
    }
    
    # Mock webhook event
    mock_webhook_event = {
        "id": 102,
        "name": "whisperx_transcription_3",
        "target_url": "http://whisperx.example.com/asr",
        "status": "pending",
    }
    
    with patch.object(repo, "get_call_recording_by_id", new_callable=AsyncMock) as mock_get, \
         patch.object(modules_repo, "get_module", new_callable=AsyncMock) as mock_get_module, \
         patch.object(repo, "update_call_recording", new_callable=AsyncMock) as mock_update, \
         patch.object(webhook_monitor, "create_manual_event", new_callable=AsyncMock) as mock_create_webhook, \
         patch.object(webhook_monitor, "record_manual_failure", new_callable=AsyncMock) as mock_webhook_failure, \
         patch("builtins.open", side_effect=FileNotFoundError("File not found")):
        
        # Setup mocks
        mock_get.return_value = mock_recording
        mock_get_module.return_value = mock_module
        mock_create_webhook.return_value = mock_webhook_event
        
        # Call the function and expect it to raise ValueError
        with pytest.raises(ValueError, match="Audio file not found"):
            await service.transcribe_recording(3, force=False)
        
        # Verify webhook failure was recorded
        mock_webhook_failure.assert_called_once()
        failure_call_args = mock_webhook_failure.call_args
        assert failure_call_args.args[0] == 102  # event_id is first positional arg
        assert failure_call_args.kwargs["attempt_number"] == 1
        assert failure_call_args.kwargs["status"] == "error"
        assert "Audio file not found" in failure_call_args.kwargs["error_message"]
        
        # Verify recording was marked as failed
        final_update_call = mock_update.call_args_list[-1]
        assert final_update_call.kwargs["transcription_status"] == "failed"
