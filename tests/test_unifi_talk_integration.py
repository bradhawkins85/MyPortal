"""Integration test for Unifi Talk module with call recordings."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_unifi_talk_module_integration():
    """Test that the Unifi Talk module integrates with call_recordings service."""
    from app.services import modules
    
    # Mock the unifi_talk_service and call_recordings_service
    with patch("app.services.modules.unifi_talk_service") as mock_unifi, \
         patch("app.services.modules.call_recordings_service") as mock_recordings:
        
        # Setup mock download result
        mock_unifi.download_recordings_from_sftp = AsyncMock(return_value={
            "status": "ok",
            "downloaded": 2,
            "skipped": 1,
            "total_files": 3,
            "errors": [],
        })
        
        # Setup mock sync result
        mock_recordings.sync_recordings_from_filesystem = AsyncMock(return_value={
            "status": "ok",
            "created": 2,
            "updated": 0,
            "skipped": 0,
            "errors": [],
        })
        
        # Call the handler directly
        settings = {
            "remote_host": "192.168.1.100",
            "remote_path": "/volume1/.srv/unifi-talk/recordings",
            "username": "test_user",
            "password": "test_pass",
            "local_path": "/tmp/test_recordings",
            "port": 22,
        }
        
        result = await modules._invoke_unifi_talk(settings, {}, event_future=None)
        
        # Verify the result
        assert result["status"] == "ok"
        assert result["downloaded"] == 2
        assert result["skipped"] == 1
        assert result["total_files"] == 3
        assert result["sync_created"] == 2
        
        # Verify mocks were called with correct arguments
        mock_unifi.download_recordings_from_sftp.assert_called_once_with(
            remote_host="192.168.1.100",
            remote_path="/volume1/.srv/unifi-talk/recordings",
            username="test_user",
            password="test_pass",
            local_path="/tmp/test_recordings",
            port=22,
        )
        
        mock_recordings.sync_recordings_from_filesystem.assert_called_once_with(
            "/tmp/test_recordings"
        )


@pytest.mark.asyncio
async def test_unifi_talk_module_no_downloads():
    """Test when no new recordings are found."""
    from app.services import modules
    
    with patch("app.services.modules.unifi_talk_service") as mock_unifi, \
         patch("app.services.modules.call_recordings_service") as mock_recordings:
        
        mock_unifi.download_recordings_from_sftp = AsyncMock(return_value={
            "status": "ok",
            "downloaded": 0,
            "skipped": 5,
            "total_files": 5,
            "errors": [],
        })
        
        settings = {
            "remote_host": "192.168.1.100",
            "remote_path": "/recordings",
            "username": "user",
            "password": "pass",
            "local_path": "/tmp/test",
            "port": 22,
        }
        
        result = await modules._invoke_unifi_talk(settings, {}, event_future=None)
        
        assert result["status"] == "ok"
        assert result["downloaded"] == 0
        assert result["skipped"] == 5
        assert "message" in result
        assert "No new recordings" in result["message"]
        
        # Sync should NOT be called when nothing was downloaded
        mock_recordings.sync_recordings_from_filesystem.assert_not_called()


@pytest.mark.asyncio
async def test_unifi_talk_module_missing_config():
    """Test module validation with missing configuration."""
    from app.services import modules
    
    # Test missing remote_host
    result = await modules._invoke_unifi_talk(
        {"username": "user", "password": "pass"},
        {},
        event_future=None
    )
    assert result["status"] == "error"
    assert "Remote host" in result["error"]
    
    # Test missing username
    result = await modules._invoke_unifi_talk(
        {"remote_host": "192.168.1.100", "password": "pass"},
        {},
        event_future=None
    )
    assert result["status"] == "error"
    assert "Username" in result["error"]
    
    # Test missing password
    result = await modules._invoke_unifi_talk(
        {"remote_host": "192.168.1.100", "username": "user"},
        {},
        event_future=None
    )
    assert result["status"] == "error"
    assert "Password" in result["error"]


@pytest.mark.asyncio
async def test_unifi_talk_module_sftp_error():
    """Test module handles SFTP errors gracefully."""
    from app.services import modules
    
    with patch("app.services.modules.unifi_talk_service") as mock_unifi:
        mock_unifi.download_recordings_from_sftp = AsyncMock(
            side_effect=ValueError("Authentication failed: Invalid credentials")
        )
        
        settings = {
            "remote_host": "192.168.1.100",
            "remote_path": "/recordings",
            "username": "bad_user",
            "password": "bad_pass",
            "local_path": "/tmp/test",
            "port": 22,
        }
        
        result = await modules._invoke_unifi_talk(settings, {}, event_future=None)
        
        assert result["status"] == "error"
        assert "Authentication failed" in result["error"]
