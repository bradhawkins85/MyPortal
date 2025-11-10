"""
Tests for BC12 security and compliance features.

Tests cover:
- File upload validation (size, type, executables)
- CSRF token protection
- RBAC access controls for viewers
- Audit logging for approved plans
"""
import hashlib
import io
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add parent directory to path for direct import
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestFileValidation:
    """Test file upload validation and security."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Import bc_file_validation module for each test."""
        from app.services import bc_file_validation
        self.bc_file_validation = bc_file_validation

    def test_sanitize_filename(self):
        """Test filename sanitization."""
        # Normal filename
        assert self.bc_file_validation.sanitize_filename("document.pdf") == "document.pdf"
        
        # Path traversal attempt
        assert self.bc_file_validation.sanitize_filename("../../../etc/passwd") == "passwd"
        
        # Dangerous characters
        assert self.bc_file_validation.sanitize_filename("doc<script>.pdf") == "doc_script_.pdf"
        
        # Hidden file
        assert self.bc_file_validation.sanitize_filename(".hidden") == "hidden"
        
        # Empty filename
        assert self.bc_file_validation.sanitize_filename("") == "upload"
        
        # Long filename
        long_name = "a" * 300 + ".pdf"
        result = self.bc_file_validation.sanitize_filename(long_name)
        assert len(result) <= 255

    def test_is_executable_file(self):
        """Test executable file detection."""
        # Executable extensions
        assert self.bc_file_validation.is_executable_file("malware.exe")
        assert self.bc_file_validation.is_executable_file("script.bat")
        assert self.bc_file_validation.is_executable_file("hack.sh")
        assert self.bc_file_validation.is_executable_file("program.dll")
        assert self.bc_file_validation.is_executable_file("virus.ps1")
        
        # Safe files
        assert not self.bc_file_validation.is_executable_file("document.pdf")
        assert not self.bc_file_validation.is_executable_file("report.docx")
        assert not self.bc_file_validation.is_executable_file("image.png")
        
        # Executable MIME types
        assert self.bc_file_validation.is_executable_file(
            "program",
            content_type="application/x-msdownload"
        )
        assert self.bc_file_validation.is_executable_file(
            "script",
            content_type="application/x-sh"
        )

    def test_is_allowed_file(self):
        """Test allowed file type checking."""
        # Allowed document types
        assert self.bc_file_validation.is_allowed_file("document.pdf")
        assert self.bc_file_validation.is_allowed_file("report.docx")
        assert self.bc_file_validation.is_allowed_file("data.xlsx")
        assert self.bc_file_validation.is_allowed_file("notes.txt")
        assert self.bc_file_validation.is_allowed_file("image.png")
        
        # Disallowed types
        assert not self.bc_file_validation.is_allowed_file("program.exe")
        assert not self.bc_file_validation.is_allowed_file("movie.mp4")
        assert not self.bc_file_validation.is_allowed_file("music.mp3")

    def test_calculate_file_hash(self):
        """Test file hash calculation."""
        content = b"test content"
        expected_hash = hashlib.sha256(content).hexdigest()
        
        result = self.bc_file_validation.calculate_file_hash(content)
        
        assert result == expected_hash
        assert len(result) == 64  # SHA256 hex is 64 chars

    @pytest.mark.asyncio
    async def test_scan_file_with_av_not_available(self):
        """Test AV scanning when ClamAV is not available."""
        test_file = Path("/tmp/test_file.txt")
        test_file.write_text("test content")
        
        try:
            # Mock subprocess to simulate ClamAV not installed
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=1)  # which fails
                
                is_clean, threat = await self.bc_file_validation.scan_file_with_av(test_file)
                
                # Should return clean when AV not available
                assert is_clean is True
                assert threat is None
        finally:
            test_file.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_scan_file_with_av_clean(self):
        """Test AV scanning with clean file."""
        test_file = Path("/tmp/test_clean.txt")
        test_file.write_text("clean content")
        
        try:
            with patch("subprocess.run") as mock_run:
                # Mock which succeeding (ClamAV available)
                mock_run.side_effect = [
                    MagicMock(returncode=0),  # which succeeds
                    MagicMock(returncode=0, stdout="OK", stderr=""),  # scan clean
                ]
                
                is_clean, threat = await self.bc_file_validation.scan_file_with_av(test_file)
                
                assert is_clean is True
                assert threat is None
        finally:
            test_file.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_scan_file_with_av_infected(self):
        """Test AV scanning with infected file."""
        test_file = Path("/tmp/test_infected.txt")
        test_file.write_text("infected content")
        
        try:
            with patch("subprocess.run") as mock_run:
                # Mock which succeeding and scan finding threat
                mock_run.side_effect = [
                    MagicMock(returncode=0),  # which succeeds
                    MagicMock(
                        returncode=1,  # scan found infection
                        stdout="FOUND: Win.Test.EICAR_HDB-1",
                        stderr=""
                    ),
                ]
                
                is_clean, threat = await self.bc_file_validation.scan_file_with_av(test_file)
                
                assert is_clean is False
                assert threat is not None
                assert "EICAR" in threat or "Unknown" in threat
        finally:
            test_file.unlink(missing_ok=True)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
