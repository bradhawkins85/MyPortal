"""Tests for ticket attachments functionality."""
import io
import sys
from pathlib import Path
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

# Add project root to path for direct imports
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Direct imports to avoid loading the whole app
from app.repositories import ticket_attachments
from app.services import ticket_attachments as attachments_service


class _DummyDB:
    """Mock database for testing."""
    
    def __init__(self, rows=None, last_id=1):
        self.execute_sql = None
        self.execute_params = None
        self.fetch_sql = None
        self.fetch_params = None
        self.fetch_all_sql = None
        self.fetch_all_params = None
        self._rows = rows or []
        self._last_id = last_id
    
    async def execute(self, sql, params):
        self.execute_sql = sql.strip()
        self.execute_params = params
        return self._last_id
    
    async def fetch_one(self, sql, params):
        self.fetch_sql = sql.strip()
        self.fetch_params = params
        if self._rows:
            return self._rows[0]
        return None
    
    async def fetch_all(self, sql, params):
        self.fetch_all_sql = sql.strip()
        self.fetch_all_params = params
        return self._rows


# ==================== Repository Tests ====================


@pytest.mark.asyncio
async def test_create_attachment():
    """Test creating an attachment record."""
    mock_row = {
        "id": 42,
        "ticket_id": 1,
        "filename": "test.pdf",
        "original_filename": "document.pdf",
        "file_size": 1024,
        "mime_type": "application/pdf",
        "access_level": "closed",
        "uploaded_by_user_id": 10,
        "uploaded_at": datetime.now(timezone.utc),
    }
    
    mock_db = _DummyDB(rows=[mock_row], last_id=42)
    
    with patch("app.repositories.ticket_attachments.db", mock_db):
        result = await ticket_attachments.create_attachment(
            ticket_id=1,
            filename="test.pdf",
            original_filename="document.pdf",
            file_size=1024,
            mime_type="application/pdf",
            access_level="closed",
            uploaded_by_user_id=10,
        )
    
    assert result["id"] == 42
    assert result["filename"] == "test.pdf"
    assert "INSERT INTO ticket_attachments" in mock_db.execute_sql
    assert mock_db.execute_params[0] == 1  # ticket_id


@pytest.mark.asyncio
async def test_get_attachment():
    """Test retrieving an attachment by ID."""
    mock_row = {
        "id": 1,
        "ticket_id": 10,
        "filename": "file.jpg",
        "original_filename": "photo.jpg",
        "file_size": 2048,
        "mime_type": "image/jpeg",
        "access_level": "open",
        "uploaded_by_user_id": 5,
        "uploaded_at": datetime.now(timezone.utc),
    }
    
    mock_db = _DummyDB(rows=[mock_row])
    
    with patch("app.repositories.ticket_attachments.db", mock_db):
        result = await ticket_attachments.get_attachment(1)
    
    assert result is not None
    assert result["id"] == 1
    assert result["filename"] == "file.jpg"
    assert "SELECT" in mock_db.fetch_sql
    assert "FROM ticket_attachments" in mock_db.fetch_sql


@pytest.mark.asyncio
async def test_list_attachments():
    """Test listing attachments for a ticket."""
    mock_rows = [
        {
            "id": 1,
            "ticket_id": 5,
            "filename": "file1.pdf",
            "original_filename": "doc1.pdf",
            "file_size": 1000,
            "mime_type": "application/pdf",
            "access_level": "closed",
            "uploaded_by_user_id": 1,
            "uploaded_at": datetime.now(timezone.utc),
        },
        {
            "id": 2,
            "ticket_id": 5,
            "filename": "file2.png",
            "original_filename": "image.png",
            "file_size": 2000,
            "mime_type": "image/png",
            "access_level": "restricted",
            "uploaded_by_user_id": 2,
            "uploaded_at": datetime.now(timezone.utc),
        },
    ]
    
    mock_db = _DummyDB(rows=mock_rows)
    
    with patch("app.repositories.ticket_attachments.db", mock_db):
        result = await ticket_attachments.list_attachments(5)
    
    assert len(result) == 2
    assert result[0]["filename"] == "file1.pdf"
    assert result[1]["filename"] == "file2.png"
    assert mock_db.fetch_all_params[0] == 5  # ticket_id


@pytest.mark.asyncio
async def test_update_attachment():
    """Test updating attachment fields."""
    mock_db = _DummyDB()
    
    with patch("app.repositories.ticket_attachments.db", mock_db):
        await ticket_attachments.update_attachment(
            1,
            access_level="restricted"
        )
    
    assert "UPDATE ticket_attachments" in mock_db.execute_sql
    assert "access_level" in mock_db.execute_sql
    assert mock_db.execute_params[0] == "restricted"
    assert mock_db.execute_params[1] == 1  # attachment_id


@pytest.mark.asyncio
async def test_delete_attachment():
    """Test deleting an attachment."""
    mock_db = _DummyDB()
    
    with patch("app.repositories.ticket_attachments.db", mock_db):
        await ticket_attachments.delete_attachment(1)
    
    assert "DELETE FROM ticket_attachments" in mock_db.execute_sql
    assert mock_db.execute_params[0] == 1


@pytest.mark.asyncio
async def test_count_attachments():
    """Test counting attachments for a ticket."""
    mock_row = {"count": 3}
    mock_db = _DummyDB(rows=[mock_row])
    
    with patch("app.repositories.ticket_attachments.db", mock_db):
        result = await ticket_attachments.count_attachments(5)
    
    assert result == 3
    assert "COUNT(*)" in mock_db.fetch_sql
    assert mock_db.fetch_params[0] == 5  # ticket_id


# ==================== Service Tests ====================


def test_generate_secure_filename():
    """Test secure filename generation."""
    # Test with extension
    filename = attachments_service._generate_secure_filename("document.pdf")
    assert filename.endswith(".pdf")
    assert len(filename) > 10  # Should be random and long
    
    # Test without extension
    filename = attachments_service._generate_secure_filename("noextension")
    assert "." not in filename
    assert len(filename) > 10


def test_validate_file_upload_success():
    """Test successful file validation."""
    mock_file = MagicMock()
    mock_file.filename = "test.pdf"
    mock_file.content_type = "application/pdf"
    mock_file.size = 1000
    
    is_valid, error = attachments_service.validate_file_upload(mock_file)
    
    assert is_valid is True
    assert error is None


def test_validate_file_upload_no_filename():
    """Test validation fails with no filename."""
    mock_file = MagicMock()
    mock_file.filename = None
    
    is_valid, error = attachments_service.validate_file_upload(mock_file)
    
    assert is_valid is False
    assert "No filename" in error


def test_validate_file_upload_too_large():
    """Test validation fails with large file."""
    mock_file = MagicMock()
    mock_file.filename = "large.pdf"
    mock_file.content_type = "application/pdf"
    mock_file.size = 100 * 1024 * 1024  # 100 MB
    
    is_valid, error = attachments_service.validate_file_upload(mock_file)
    
    assert is_valid is False
    assert "exceeds maximum" in error


def test_validate_file_upload_invalid_mime_type():
    """Test validation fails with invalid MIME type."""
    mock_file = MagicMock()
    mock_file.filename = "evil.exe"
    mock_file.content_type = "application/x-executable"
    mock_file.size = 1000
    
    is_valid, error = attachments_service.validate_file_upload(mock_file)
    
    assert is_valid is False
    assert "not allowed" in error


@pytest.mark.asyncio
async def test_save_uploaded_file_success():
    """Test successful file upload."""
    mock_file = MagicMock()
    mock_file.filename = "test.pdf"
    mock_file.content_type = "application/pdf"
    mock_file.size = 1024
    mock_file.read = AsyncMock(return_value=b"test content")
    
    mock_attachment = {
        "id": 1,
        "ticket_id": 5,
        "filename": "random.pdf",
        "original_filename": "test.pdf",
        "file_size": 12,
        "mime_type": "application/pdf",
        "access_level": "closed",
        "uploaded_by_user_id": 10,
        "uploaded_at": datetime.now(timezone.utc),
    }
    
    with patch("app.services.ticket_attachments._get_upload_directory") as mock_dir, \
         patch("app.services.ticket_attachments.attachments_repo.create_attachment", return_value=mock_attachment), \
         patch("builtins.open", create=True) as mock_open:
        
        mock_dir.return_value = Path("/tmp/test")
        
        result = await attachments_service.save_uploaded_file(
            ticket_id=5,
            file=mock_file,
            access_level="closed",
            uploaded_by_user_id=10,
        )
    
    assert result["id"] == 1
    assert result["ticket_id"] == 5
    mock_file.read.assert_called_once()


@pytest.mark.asyncio
async def test_save_uploaded_file_validation_error():
    """Test file upload with validation error."""
    mock_file = MagicMock()
    mock_file.filename = None  # Invalid
    
    with pytest.raises(ValueError, match="No filename"):
        await attachments_service.save_uploaded_file(
            ticket_id=5,
            file=mock_file,
            access_level="closed",
            uploaded_by_user_id=10,
        )


def test_generate_open_access_token():
    """Test token generation for open access."""
    token = attachments_service.generate_open_access_token(42)
    
    assert token is not None
    assert isinstance(token, str)
    assert len(token) > 10


def test_verify_open_access_token_valid():
    """Test verifying a valid token."""
    # Generate a token
    attachment_id = 42
    token = attachments_service.generate_open_access_token(attachment_id)
    
    # Verify it
    verified_id = attachments_service.verify_open_access_token(token)
    
    assert verified_id == attachment_id


def test_verify_open_access_token_invalid():
    """Test verifying an invalid token."""
    verified_id = attachments_service.verify_open_access_token("invalid-token")
    
    assert verified_id is None


def test_get_attachment_file_path():
    """Test getting file path for an attachment."""
    path = attachments_service.get_attachment_file_path("test.pdf")
    
    assert isinstance(path, Path)
    assert str(path).endswith("test.pdf")
    assert "uploads" in str(path)
    assert "tickets" in str(path)


@pytest.mark.asyncio
async def test_delete_attachment_file():
    """Test deleting an attachment file."""
    mock_attachment = {
        "id": 1,
        "filename": "test.pdf",
    }
    
    with patch("app.services.ticket_attachments.attachments_repo.delete_attachment") as mock_delete, \
         patch("app.services.ticket_attachments._get_upload_directory") as mock_get_dir:
        
        # Create mock file path
        mock_file_path = MagicMock()
        mock_file_path.exists.return_value = True
        mock_file_path.unlink = MagicMock()
        
        # Mock upload directory
        mock_dir = MagicMock()
        mock_dir.__truediv__ = MagicMock(return_value=mock_file_path)  # For / operator
        mock_get_dir.return_value = mock_dir
        
        await attachments_service.delete_attachment_file(mock_attachment)
    
    mock_delete.assert_called_once_with(1)
    mock_file_path.exists.assert_called_once()
    mock_file_path.unlink.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
