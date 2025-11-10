"""Test to verify the scheduler can handle the fixed return value from process_queued_transcriptions."""
import pytest
from unittest.mock import AsyncMock, patch
from app.services import call_recordings as call_recordings_service


@pytest.mark.asyncio
async def test_process_queued_transcriptions_returns_details_not_message():
    """Test that process_queued_transcriptions returns 'details' key instead of 'message' to avoid conflict with log_info."""
    
    # Mock the repository to return no recordings
    with patch.object(call_recordings_service, "call_recordings_repo") as mock_repo:
        mock_repo.list_call_recordings = AsyncMock(return_value=[])
        
        # Call the function
        result = await call_recordings_service.process_queued_transcriptions()
        
        # Verify the result structure
        assert result["status"] == "ok"
        assert result["processed"] == 0
        assert "details" in result, "Result should contain 'details' key"
        assert "message" not in result, "Result should NOT contain 'message' key (conflicts with log_info)"
        assert result["details"] == "No recordings to process"
        
        # Simulate what scheduler.py does - this should not raise TypeError
        from app.core.logging import log_info
        try:
            log_info("Transcription processed", **result)
            # If we get here, the fix works!
        except TypeError as e:
            pytest.fail(f"log_info raised TypeError when it shouldn't: {e}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
