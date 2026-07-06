"""Service for handling ticket attachment file operations."""
from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any

import magic
from fastapi import UploadFile
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from app.core.config import get_settings
from app.core.logging import log_debug, log_error, log_info
from app.repositories import ticket_attachments as attachments_repo


# Maximum file size: 50 MB
MAX_FILE_SIZE = 50 * 1024 * 1024

# Allowed MIME types for attachments.
# SVG, HTML, and XML types are intentionally excluded: SVGs can contain embedded
# <script> tags and HTML/XML documents can trigger XSS when rendered inline.
ALLOWED_MIME_TYPES = {
    # Documents
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "text/plain",
    "text/csv",
    # Images (raster only — SVG excluded due to script-injection risk)
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    # Archives
    "application/zip",
    "application/x-7z-compressed",
    "application/x-tar",
    "application/gzip",
    # Other
    "application/json",
}

# Number of bytes to read from the start of a file for MIME sniffing.
_MAGIC_HEADER_BYTES = 2048


def _get_upload_directory() -> Path:
    """Get the base upload directory for ticket attachments.

    Files are stored under ``private_uploads/tickets/`` (outside the public
    ``/static`` tree) so that access-control checks in the download endpoint
    cannot be bypassed by guessing a direct URL.
    """
    base_dir = Path(__file__).resolve().parents[2] / "private_uploads" / "tickets"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


def _generate_secure_filename(original_filename: str) -> str:
    """Generate a secure filename using UUID."""
    # Extract extension from original filename
    extension = ""
    if "." in original_filename:
        extension = original_filename.rsplit(".", 1)[1].lower()
        # Limit extension length and sanitize
        extension = extension[:10]
        extension = "".join(c for c in extension if c.isalnum())
    
    # Generate random filename
    random_name = secrets.token_urlsafe(32)
    
    if extension:
        return f"{random_name}.{extension}"
    return random_name


def _sniff_mime_type(header_bytes: bytes) -> str | None:
    """Return the MIME type detected from the first bytes of a file.

    Uses ``python-magic`` (libmagic) to inspect the file *content* rather than
    trusting the client-supplied ``Content-Type`` header, which is trivially
    spoofable.
    """
    try:
        return magic.from_buffer(header_bytes, mime=True)
    except Exception as exc:
        log_error(f"MIME sniffing failed: {exc}")
        return None


def validate_file_upload(file: UploadFile, max_size: int = MAX_FILE_SIZE) -> tuple[bool, str | None]:
    """
    Validate an uploaded file.
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not file.filename:
        return False, "No filename provided"
    
    # Check file size (if available)
    if file.size and file.size > max_size:
        max_mb = max_size / (1024 * 1024)
        return False, f"File size exceeds maximum allowed size of {max_mb}MB"
    
    # Check MIME type against the client-supplied header as a quick pre-filter.
    # The actual bytes-based check is performed in save_uploaded_file after the
    # file has been read.
    if file.content_type and file.content_type.split(";")[0].strip() not in ALLOWED_MIME_TYPES:
        return False, f"File type '{file.content_type}' is not allowed"
    
    return True, None


async def save_uploaded_file(
    ticket_id: int,
    file: UploadFile,
    access_level: str,
    uploaded_by_user_id: int | None,
) -> dict[str, Any]:
    """
    Save an uploaded file and create database record.
    
    Args:
        ticket_id: The ticket ID to attach the file to
        file: The uploaded file
        access_level: Access level (open, closed, restricted)
        uploaded_by_user_id: User ID of uploader
    
    Returns:
        The created attachment record
    
    Raises:
        ValueError: If validation fails
        IOError: If file save fails
    """
    # Validate file
    is_valid, error = validate_file_upload(file)
    if not is_valid:
        raise ValueError(error or "Invalid file upload")
    
    # Generate secure filename
    secure_filename = _generate_secure_filename(file.filename or "upload")
    
    # Get upload directory
    upload_dir = _get_upload_directory()
    file_path = upload_dir / secure_filename
    
    # Save file
    try:
        contents = await file.read()
        file_size = len(contents)
        
        # Double check size after reading
        if file_size > MAX_FILE_SIZE:
            raise ValueError(f"File size {file_size} exceeds maximum")

        # Validate MIME type from actual file bytes (defeats spoofed Content-Type).
        actual_mime = _sniff_mime_type(contents[:_MAGIC_HEADER_BYTES])
        if actual_mime and actual_mime not in ALLOWED_MIME_TYPES:
            raise ValueError(f"File content type '{actual_mime}' is not allowed")
        
        with open(file_path, "wb") as f:
            f.write(contents)
        
        log_info(f"Saved file {secure_filename} ({file_size} bytes) for ticket {ticket_id}")
    except ValueError:
        raise
    except Exception as e:
        log_error(f"Failed to save file: {e}")
        # Clean up partial file if it exists
        if file_path.exists():
            file_path.unlink()
        raise IOError(f"Failed to save file: {e}") from e
    
    # Use the sniffed MIME type (more trustworthy than client-supplied header).
    resolved_mime = actual_mime or file.content_type

    # Create database record
    try:
        attachment = await attachments_repo.create_attachment(
            ticket_id=ticket_id,
            filename=secure_filename,
            original_filename=file.filename or "upload",
            file_size=file_size,
            mime_type=resolved_mime,
            access_level=access_level,
            uploaded_by_user_id=uploaded_by_user_id,
        )
        return attachment
    except Exception as e:
        log_error(f"Failed to create attachment record: {e}")
        # Clean up file if database insert fails
        if file_path.exists():
            file_path.unlink()
        raise


async def save_file_bytes(
    ticket_id: int,
    *,
    contents: bytes,
    original_filename: str,
    mime_type: str | None,
    access_level: str,
    uploaded_by_user_id: int | None,
) -> dict[str, Any]:
    """Save raw file bytes and create a ticket attachment record."""
    upload_name = original_filename or "upload"
    pseudo_file = type(
        "AttachmentUpload",
        (),
        {"filename": upload_name, "content_type": mime_type, "size": len(contents)},
    )()
    is_valid, error = validate_file_upload(pseudo_file)
    if not is_valid:
        raise ValueError(error or "Invalid file upload")
    file_size = len(contents)
    if file_size > MAX_FILE_SIZE:
        raise ValueError(f"File size {file_size} exceeds maximum")

    # Validate MIME type from actual file bytes.
    actual_mime = _sniff_mime_type(contents[:_MAGIC_HEADER_BYTES])
    if actual_mime and actual_mime not in ALLOWED_MIME_TYPES:
        raise ValueError(f"File content type '{actual_mime}' is not allowed")

    resolved_mime = actual_mime or mime_type

    secure_filename = _generate_secure_filename(upload_name)
    upload_dir = _get_upload_directory()
    file_path = upload_dir / secure_filename
    try:
        with open(file_path, "wb") as f:
            f.write(contents)
        log_info(f"Saved file {secure_filename} ({file_size} bytes) for ticket {ticket_id}")
    except Exception as e:
        log_error(f"Failed to save file: {e}")
        if file_path.exists():
            file_path.unlink()
        raise IOError(f"Failed to save file: {e}") from e

    try:
        return await attachments_repo.create_attachment(
            ticket_id=ticket_id,
            filename=secure_filename,
            original_filename=upload_name,
            file_size=file_size,
            mime_type=resolved_mime,
            access_level=access_level,
            uploaded_by_user_id=uploaded_by_user_id,
        )
    except Exception as e:
        log_error(f"Failed to create attachment record: {e}")
        if file_path.exists():
            file_path.unlink()
        raise


async def delete_attachment_file(attachment: dict[str, Any]) -> None:
    """Delete an attachment file and database record."""
    filename = attachment.get("filename")
    attachment_id = attachment.get("id")
    
    if not filename or not attachment_id:
        log_error("Invalid attachment data for deletion")
        return
    
    # Delete database record first
    try:
        await attachments_repo.delete_attachment(attachment_id)
    except Exception as e:
        log_error(f"Failed to delete attachment record {attachment_id}: {e}")
        raise
    
    # Delete file
    upload_dir = _get_upload_directory()
    file_path = upload_dir / filename
    
    if file_path.exists():
        try:
            file_path.unlink()
            log_info(f"Deleted file {filename}")
        except Exception as e:
            log_error(f"Failed to delete file {filename}: {e}")
            # Don't raise - file might already be gone, record is deleted


def get_attachment_file_path(filename: str) -> Path:
    """Get the full path to an attachment file."""
    upload_dir = _get_upload_directory()
    return upload_dir / filename


def generate_open_access_token(attachment_id: int, expires_in_seconds: int = 86400) -> str:
    """
    Generate a signed token for open access to an attachment.
    
    Args:
        attachment_id: The attachment ID
        expires_in_seconds: Token expiry time (default 24 hours)
    
    Returns:
        Signed token string
    """
    # Use the secret key from settings
    config = get_settings()
    secret_key = getattr(config, "secret_key", "change-me-in-production")
    serializer = URLSafeTimedSerializer(secret_key, salt="ticket-attachment")
    
    token = serializer.dumps({"attachment_id": attachment_id})
    return token


def verify_open_access_token(token: str, max_age: int = 86400) -> int | None:
    """
    Verify an open access token and return the attachment ID.
    
    Args:
        token: The signed token
        max_age: Maximum age in seconds (default 24 hours)
    
    Returns:
        Attachment ID if valid, None otherwise
    """
    config = get_settings()
    secret_key = getattr(config, "secret_key", "change-me-in-production")
    serializer = URLSafeTimedSerializer(secret_key, salt="ticket-attachment")
    
    try:
        data = serializer.loads(token, max_age=max_age)
        return data.get("attachment_id")
    except (BadSignature, SignatureExpired) as e:
        log_debug(f"Invalid or expired token: {e}")
        return None
