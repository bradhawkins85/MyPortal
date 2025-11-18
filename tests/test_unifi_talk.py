"""Tests for Unifi Talk SFTP integration."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import unifi_talk


@pytest.mark.asyncio
async def test_download_recordings_basic():
    """Test basic SFTP download functionality."""
    # Mock SFTP client
    mock_sftp = MagicMock()
    mock_sftp.listdir.return_value = ["recording1.mp3", "recording2.mp3", "notes.txt"]
    mock_sftp.get = MagicMock()
    
    # Mock SSH client
    mock_ssh = MagicMock()
    mock_ssh.open_sftp.return_value = mock_sftp
    
    # Create temp directory for downloads
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("paramiko.SSHClient", return_value=mock_ssh):
            result = await unifi_talk.download_recordings_from_sftp(
                remote_host="192.168.1.100",
                remote_path="/recordings",
                username="test_user",
                password="test_pass",
                local_path=tmpdir,
                port=22,
            )
    
    # Verify results
    assert result["status"] == "ok"
    assert result["downloaded"] == 2  # Only MP3 files
    assert result["skipped"] == 0
    assert result["total_files"] == 2
    assert len(result["errors"]) == 0
    
    # Verify SSH connection was made
    mock_ssh.connect.assert_called_once()
    # Verify listdir was called
    mock_sftp.listdir.assert_called_once_with("/recordings")
    # Verify get was called twice (once for each MP3 file)
    assert mock_sftp.get.call_count == 2


@pytest.mark.asyncio
async def test_download_recordings_skip_existing():
    """Test that existing files are skipped."""
    # Mock SFTP client
    mock_sftp = MagicMock()
    mock_sftp.listdir.return_value = ["recording1.mp3"]
    mock_sftp.get = MagicMock()
    
    # Mock SSH client
    mock_ssh = MagicMock()
    mock_ssh.open_sftp.return_value = mock_sftp
    
    # Create temp directory with existing file
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create existing file
        existing_file = Path(tmpdir) / "recording1.mp3"
        existing_file.write_text("existing content")
        
        with patch("paramiko.SSHClient", return_value=mock_ssh):
            result = await unifi_talk.download_recordings_from_sftp(
                remote_host="192.168.1.100",
                remote_path="/recordings",
                username="test_user",
                password="test_pass",
                local_path=tmpdir,
                port=22,
            )
    
    # Verify results - file should be skipped
    assert result["status"] == "ok"
    assert result["downloaded"] == 0
    assert result["skipped"] == 1
    assert result["total_files"] == 1
    
    # Verify get was not called since file exists
    mock_sftp.get.assert_not_called()


@pytest.mark.asyncio
async def test_download_recordings_authentication_error():
    """Test handling of authentication errors."""
    import paramiko
    
    # Mock SSH client that raises AuthenticationException
    mock_ssh = MagicMock()
    mock_ssh.connect.side_effect = paramiko.AuthenticationException("Invalid credentials")
    
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("paramiko.SSHClient", return_value=mock_ssh):
            with pytest.raises(ValueError, match="Authentication failed"):
                await unifi_talk.download_recordings_from_sftp(
                    remote_host="192.168.1.100",
                    remote_path="/recordings",
                    username="bad_user",
                    password="bad_pass",
                    local_path=tmpdir,
                    port=22,
                )


@pytest.mark.asyncio
async def test_download_recordings_remote_path_not_found():
    """Test handling of non-existent remote path."""
    # Mock SFTP client that raises FileNotFoundError
    mock_sftp = MagicMock()
    mock_sftp.listdir.side_effect = FileNotFoundError("No such file or directory")
    
    # Mock SSH client
    mock_ssh = MagicMock()
    mock_ssh.open_sftp.return_value = mock_sftp
    
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("paramiko.SSHClient", return_value=mock_ssh):
            with pytest.raises(FileNotFoundError, match="Remote path does not exist"):
                await unifi_talk.download_recordings_from_sftp(
                    remote_host="192.168.1.100",
                    remote_path="/nonexistent",
                    username="test_user",
                    password="test_pass",
                    local_path=tmpdir,
                    port=22,
                )


@pytest.mark.asyncio
async def test_download_recordings_partial_failure():
    """Test handling of partial download failures."""
    # Mock SFTP client
    mock_sftp = MagicMock()
    mock_sftp.listdir.return_value = ["recording1.mp3", "recording2.mp3"]
    
    # First file succeeds, second file fails
    def mock_get(remote, local):
        if "recording2" in remote:
            raise Exception("Download failed")
    
    mock_sftp.get.side_effect = mock_get
    
    # Mock SSH client
    mock_ssh = MagicMock()
    mock_ssh.open_sftp.return_value = mock_sftp
    
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("paramiko.SSHClient", return_value=mock_ssh):
            result = await unifi_talk.download_recordings_from_sftp(
                remote_host="192.168.1.100",
                remote_path="/recordings",
                username="test_user",
                password="test_pass",
                local_path=tmpdir,
                port=22,
            )
    
    # Verify partial success
    assert result["status"] == "partial"
    assert result["downloaded"] == 1
    assert result["total_files"] == 2
    assert len(result["errors"]) == 1
    assert "recording2" in result["errors"][0]


@pytest.mark.asyncio
async def test_download_recordings_invalid_local_path():
    """Test handling of invalid local path."""
    with pytest.raises(ValueError, match="Invalid local path"):
        await unifi_talk.download_recordings_from_sftp(
            remote_host="192.168.1.100",
            remote_path="/recordings",
            username="test_user",
            password="test_pass",
            local_path="/dev/null/invalid",  # Invalid path
            port=22,
        )
