"""
File validation and security service for Business Continuity attachments.

Provides comprehensive validation including:
- File size limits
- File type/extension checking
- Executable file rejection
- Optional antivirus scanning integration
"""
from __future__ import annotations

import hashlib
import mimetypes
import re
import subprocess
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, UploadFile, status

from app.core.logging import log_info, log_warning

# File size limits
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB for BC attachments

# Allowed document types for BC plans
ALLOWED_EXTENSIONS = {
    # Documents
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".odt",
    ".ods",
    ".odp",
    # Text files
    ".txt",
    ".md",
    ".csv",
    ".rtf",
    # Images
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    # Archives (for bulk documentation)
    ".zip",
    ".tar",
    ".gz",
}

# Executable extensions that should always be rejected
EXECUTABLE_EXTENSIONS = {
    ".exe",
    ".dll",
    ".bat",
    ".cmd",
    ".com",
    ".msi",
    ".scr",
    ".vbs",
    ".js",
    ".jar",
    ".app",
    ".deb",
    ".rpm",
    ".sh",
    ".bash",
    ".ps1",
    ".psm1",
    ".psd1",
}

# MIME types for executables (additional check)
EXECUTABLE_MIME_TYPES = {
    "application/x-msdownload",
    "application/x-msdos-program",
    "application/x-executable",
    "application/x-sharedlib",
    "application/x-sh",
    "application/x-shellscript",
    "application/x-java-archive",
    "text/x-python",
    "text/x-script.python",
}

# Pattern to detect common filename sanitization bypasses
DANGEROUS_FILENAME_PATTERN = re.compile(r"\.\.|[<>:\"/\\|?*\x00-\x1f]")


def sanitize_filename(filename: str) -> str:
    """
    Sanitize filename to prevent path traversal and other attacks.
    
    Args:
        filename: Original filename from upload
        
    Returns:
        Sanitized filename safe for filesystem storage
    """
    # Get just the filename, no directory components
    name = Path(filename or "").name
    if not name:
        return "upload"
    
    # Remove dangerous characters
    cleaned = DANGEROUS_FILENAME_PATTERN.sub("_", name)
    
    # Avoid hidden files
    cleaned = cleaned.lstrip(".") or "upload"
    
    # Limit length
    return cleaned[:255]


def is_executable_file(filename: str, content_type: Optional[str] = None) -> bool:
    """
    Check if a file is an executable based on extension and MIME type.
    
    Args:
        filename: Filename to check
        content_type: Optional MIME type from upload
        
    Returns:
        True if file appears to be executable
    """
    # Check extension
    suffix = Path(filename).suffix.lower()
    if suffix in EXECUTABLE_EXTENSIONS:
        return True
    
    # Check MIME type if provided
    if content_type:
        mime_lower = content_type.lower()
        if any(exec_mime in mime_lower for exec_mime in EXECUTABLE_MIME_TYPES):
            return True
    
    # Additional check: guess MIME type from filename
    guessed_type, _ = mimetypes.guess_type(filename)
    if guessed_type:
        if any(exec_mime in guessed_type.lower() for exec_mime in EXECUTABLE_MIME_TYPES):
            return True
    
    return False


def is_allowed_file(filename: str) -> bool:
    """
    Check if a file extension is in the allowed list.
    
    Args:
        filename: Filename to check
        
    Returns:
        True if file extension is allowed
    """
    suffix = Path(filename).suffix.lower()
    return suffix in ALLOWED_EXTENSIONS


async def scan_file_with_av(file_path: Path) -> tuple[bool, Optional[str]]:
    """
    Scan a file with ClamAV antivirus if available.
    
    Args:
        file_path: Path to file to scan
        
    Returns:
        Tuple of (is_clean, threat_name)
        - is_clean: True if no threats found or AV not available
        - threat_name: Name of detected threat, or None if clean
    """
    try:
        # Check if clamdscan is available
        result = subprocess.run(
            ["which", "clamdscan"],
            capture_output=True,
            timeout=5,
        )
        
        if result.returncode != 0:
            # ClamAV not available, log and return clean
            log_info("ClamAV not available for file scanning")
            return True, None
        
        # Run clamdscan
        scan_result = subprocess.run(
            ["clamdscan", "--no-summary", str(file_path)],
            capture_output=True,
            timeout=30,
            text=True,
        )
        
        # clamdscan returns 0 for clean, 1 for infected, 2 for errors
        if scan_result.returncode == 0:
            log_info("File scan clean", file_path=str(file_path))
            return True, None
        elif scan_result.returncode == 1:
            # Extract threat name from output
            output = scan_result.stdout
            threat_match = re.search(r"FOUND:\s+(.+)", output)
            threat_name = threat_match.group(1) if threat_match else "Unknown threat"
            log_warning("File scan detected threat", file_path=str(file_path), threat=threat_name)
            return False, threat_name
        else:
            # Scan error, log and treat as clean (don't block uploads if AV has issues)
            log_warning("File scan error", file_path=str(file_path), error=scan_result.stderr)
            return True, None
            
    except subprocess.TimeoutExpired:
        log_warning("File scan timeout", file_path=str(file_path))
        return True, None  # Don't block on timeout
    except Exception as e:
        log_warning("File scan exception", file_path=str(file_path), error=str(e))
        return True, None  # Don't block on errors


async def validate_upload_file(
    upload: UploadFile,
    max_size: int = MAX_FILE_SIZE,
    allow_executables: bool = False,
    scan_with_av: bool = False,
) -> tuple[bytes, str, int]:
    """
    Validate an uploaded file for BC attachments.
    
    Performs comprehensive security validation:
    - Checks filename is provided
    - Validates file extension is allowed
    - Rejects executable files
    - Enforces file size limits
    - Optionally scans with antivirus
    
    Args:
        upload: FastAPI UploadFile object
        max_size: Maximum allowed file size in bytes
        allow_executables: Allow executable files (should always be False for BC)
        scan_with_av: Whether to scan file with antivirus if available
        
    Returns:
        Tuple of (file_content, sanitized_filename, file_size)
        
    Raises:
        HTTPException: If validation fails
    """
    # Check filename exists
    if not upload.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No filename provided",
        )
    
    # Sanitize filename
    sanitized_name = sanitize_filename(upload.filename)
    
    # Check for executable files
    if not allow_executables and is_executable_file(upload.filename, upload.content_type):
        log_warning(
            "Executable file upload rejected",
            filename=upload.filename,
            content_type=upload.content_type,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Executable files are not allowed for security reasons",
        )
    
    # Check file extension is allowed
    if not is_allowed_file(upload.filename):
        suffix = Path(upload.filename).suffix.lower()
        log_warning("Disallowed file type upload rejected", filename=upload.filename, extension=suffix)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type '{suffix}' is not allowed. Allowed types: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )
    
    # Read and validate file size
    content = await upload.read()
    file_size = len(content)
    
    if file_size > max_size:
        log_warning(
            "Oversized file upload rejected",
            filename=upload.filename,
            size=file_size,
            max_size=max_size,
        )
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File size ({file_size / (1024 * 1024):.1f} MB) exceeds maximum allowed ({max_size / (1024 * 1024):.0f} MB)",
        )
    
    # Optional: Scan with antivirus if enabled
    if scan_with_av:
        # Write to temporary file for scanning
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
            tmp_path = Path(tmp_file.name)
            tmp_file.write(content)
        
        try:
            is_clean, threat_name = await scan_file_with_av(tmp_path)
            if not is_clean:
                log_warning(
                    "Virus detected in upload",
                    filename=upload.filename,
                    threat=threat_name,
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"File rejected: virus or malware detected ({threat_name})",
                )
        finally:
            # Clean up temp file
            tmp_path.unlink(missing_ok=True)
    
    log_info(
        "File upload validated",
        filename=upload.filename,
        sanitized_name=sanitized_name,
        size=file_size,
    )
    
    return content, sanitized_name, file_size


def calculate_file_hash(content: bytes) -> str:
    """
    Calculate SHA256 hash of file content.
    
    Args:
        content: File content bytes
        
    Returns:
        Hex string of SHA256 hash
    """
    return hashlib.sha256(content).hexdigest()
