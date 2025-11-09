"""Test to verify call-recordings module can be triggered."""
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_trigger_call_recordings_module():
    """Test that triggering call-recordings module doesn't raise 'No handler registered' error."""
    from app.services import modules
    from app.repositories import integration_modules as module_repo
    
    # Mock the module repository to return call-recordings module
    async def fake_get_module(slug):
        if slug == "call-recordings":
            return {
                "slug": "call-recordings",
                "name": "Call Recordings",
                "enabled": True,
                "settings": {
                    "recordings_path": "/test/path/recordings",
                },
            }
        return None
    
    with patch.object(module_repo, "get_module", new_callable=AsyncMock) as mock_get, \
         patch.object(modules.call_recordings_service, "sync_recordings_from_filesystem", new_callable=AsyncMock) as mock_sync:
        mock_get.side_effect = fake_get_module
        mock_sync.return_value = {
            "status": "ok",
            "created": 2,
            "updated": 1,
            "skipped": 0,
            "errors": [],
        }

        result = await modules.trigger_module("call-recordings", {}, background=False)

        assert result is not None
        assert result.get("status") == "ok"
        assert result.get("created") == 2
        assert result.get("recordings_path") == "/test/path/recordings"
        mock_sync.assert_awaited_once_with("/test/path/recordings")


@pytest.mark.asyncio
async def test_call_recordings_not_in_trigger_actions():
    """Test that call-recordings is excluded from trigger action modules."""
    from app.services import modules
    from app.repositories import integration_modules as module_repo
    
    # Mock the module repository
    async def fake_list_modules():
        return [
            {"slug": "call-recordings", "name": "Call Recordings", "enabled": True, "settings": {}},
            {"slug": "smtp", "name": "Send Email", "enabled": True, "settings": {}},
            {"slug": "create-ticket", "name": "Create Ticket", "enabled": True, "settings": {}},
        ]
    
    with patch.object(module_repo, "list_modules", new_callable=AsyncMock) as mock_list:
        mock_list.side_effect = fake_list_modules
        
        result = await modules.list_trigger_action_modules()
        result_slugs = {module["slug"] for module in result}
        
        # call-recordings should be excluded
        assert "call-recordings" not in result_slugs
        # These should be included
        assert "smtp" in result_slugs
        assert "create-ticket" in result_slugs
