"""Tests for path traversal prevention."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.main import _resolve_private_upload, _private_uploads_path


@pytest.fixture
def temp_upload_dir(monkeypatch):
    """Create a temporary upload directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_path = Path(tmpdir) / "uploads"
        temp_path.mkdir(parents=True, exist_ok=True)
        
        # Create some test files
        (temp_path / "test.txt").write_text("test content")
        subdir = temp_path / "subdir"
        subdir.mkdir()
        (subdir / "nested.txt").write_text("nested content")
        
        # Monkey patch the private uploads path
        monkeypatch.setattr("app.main._private_uploads_path", temp_path)
        
        yield temp_path


def test_resolve_private_upload_normal_file(temp_upload_dir):
    """Test that normal file paths are resolved correctly."""
    result = _resolve_private_upload("test.txt")
    
    assert result.name == "test.txt"
    assert result.exists()
    assert result.read_text() == "test content"


def test_resolve_private_upload_nested_file(temp_upload_dir):
    """Test that nested file paths are resolved correctly."""
    result = _resolve_private_upload("subdir/nested.txt")
    
    assert result.name == "nested.txt"
    assert result.exists()
    assert result.read_text() == "nested content"


def test_resolve_private_upload_blocks_parent_directory_traversal(temp_upload_dir):
    """Test that .. path traversal is blocked."""
    with pytest.raises(HTTPException) as exc_info:
        _resolve_private_upload("../etc/passwd")
    
    assert exc_info.value.status_code == 404
    assert "File not found" in exc_info.value.detail


def test_resolve_private_upload_blocks_multiple_parent_traversal(temp_upload_dir):
    """Test that multiple .. attempts are blocked."""
    with pytest.raises(HTTPException) as exc_info:
        _resolve_private_upload("../../../../../../etc/passwd")
    
    assert exc_info.value.status_code == 404


def test_resolve_private_upload_blocks_absolute_paths(temp_upload_dir):
    """Test that absolute paths are blocked."""
    with pytest.raises(HTTPException) as exc_info:
        _resolve_private_upload("/etc/passwd")
    
    assert exc_info.value.status_code == 404
    assert "File not found" in exc_info.value.detail


def test_resolve_private_upload_blocks_windows_absolute_paths(temp_upload_dir):
    """Test that Windows absolute paths are blocked."""
    with pytest.raises(HTTPException) as exc_info:
        _resolve_private_upload("C:\\Windows\\System32\\config\\sam")
    
    assert exc_info.value.status_code == 404


def test_resolve_private_upload_normalizes_backslashes(temp_upload_dir):
    """Test that backslashes are normalized to forward slashes."""
    result = _resolve_private_upload("subdir\\nested.txt")
    
    assert result.name == "nested.txt"
    assert result.exists()


def test_resolve_private_upload_blocks_empty_path(temp_upload_dir):
    """Test that empty paths are rejected."""
    with pytest.raises(HTTPException) as exc_info:
        _resolve_private_upload("")
    
    assert exc_info.value.status_code == 404


def test_resolve_private_upload_blocks_null_bytes(temp_upload_dir):
    """Test that null byte injection is handled."""
    # Null bytes might be used to bypass extension checks
    with pytest.raises((HTTPException, ValueError)):
        _resolve_private_upload("test.txt\x00.jpg")


def test_resolve_private_upload_blocks_dot_segments(temp_upload_dir):
    """Test that . and .. segments are properly handled."""
    # Single dot should be ignored
    result = _resolve_private_upload("./test.txt")
    assert result.name == "test.txt"
    
    # Double dot in middle of path
    with pytest.raises((HTTPException, FileNotFoundError)):
        _resolve_private_upload("subdir/../../../etc/passwd")


def test_resolve_private_upload_rejects_nonexistent_files(temp_upload_dir):
    """Test that nonexistent files raise 404."""
    with pytest.raises(HTTPException) as exc_info:
        _resolve_private_upload("nonexistent.txt")
    
    assert exc_info.value.status_code == 404


def test_resolve_private_upload_rejects_directory_access(temp_upload_dir):
    """Test that directory access is blocked."""
    with pytest.raises(HTTPException) as exc_info:
        _resolve_private_upload("subdir")
    
    assert exc_info.value.status_code == 404


def test_resolve_private_upload_handles_url_encoding(temp_upload_dir):
    """Test that URL-encoded path traversal attempts are blocked."""
    # %2e%2e = ..
    # %2f = /
    with pytest.raises(HTTPException) as exc_info:
        _resolve_private_upload("%2e%2e%2f%2e%2e%2fetc%2fpasswd")
    
    # The path should not resolve to a file outside uploads
    assert exc_info.value.status_code == 404


def test_resolve_private_upload_prevents_symlink_escape(temp_upload_dir):
    """Test that symlinks pointing outside the upload dir are blocked."""
    # Create a symlink pointing outside
    symlink_path = temp_upload_dir / "escape_link"
    target_path = temp_upload_dir.parent / "outside.txt"
    target_path.write_text("outside content")
    
    try:
        symlink_path.symlink_to(target_path)
    except (OSError, NotImplementedError):
        # Symlinks might not be supported on all platforms
        pytest.skip("Symlinks not supported on this platform")
    
    # The resolve() call should detect that the symlink escapes the upload directory
    with pytest.raises(HTTPException) as exc_info:
        result = _resolve_private_upload("escape_link")
        # Even if we get a result, verify it's within the uploads directory
        assert result.is_relative_to(temp_upload_dir)
    
    # Cleanup
    symlink_path.unlink()
    target_path.unlink()


def test_resolve_private_upload_complex_traversal_attempts(temp_upload_dir):
    """Test various complex path traversal attempts."""
    malicious_paths = [
        "....//....//....//etc/passwd",
        "..\\..\\..\\..\\windows\\system32\\config\\sam",
        "subdir/../../etc/passwd",
        "./../.../../etc/passwd",
        "subdir/.././../etc/passwd",
    ]
    
    for malicious_path in malicious_paths:
        with pytest.raises((HTTPException, FileNotFoundError)):
            _resolve_private_upload(malicious_path)
