from __future__ import annotations

import re
from pathlib import Path
from typing import Tuple
from uuid import uuid4

import aiofiles
from fastapi import HTTPException, UploadFile, status

_MAX_FILE_SIZE = 15 * 1024 * 1024  # 15 MB
_SAFE_FILENAME_PATTERN = re.compile(r"[^A-Za-z0-9._-]")


def sanitize_filename(filename: str) -> str:
    """Return a filesystem-safe representation of *filename*.

    Directory components are stripped and any unsafe characters are replaced
    with underscores. A fallback name is returned if the input is empty.
    """

    name = Path(filename or "").name
    if not name:
        return "upload"
    cleaned = _SAFE_FILENAME_PATTERN.sub("_", name)
    # Avoid filenames starting with a dot to reduce accidental hidden files
    cleaned = cleaned.lstrip(".") or "upload"
    return cleaned[:255]


async def store_port_document(
    *,
    port_id: int,
    upload: UploadFile,
    uploads_root: Path,
    max_size: int = _MAX_FILE_SIZE,
) -> Tuple[str, int, str]:
    """Persist an uploaded document for a port.

    Returns a tuple of (relative_path, size, original_filename).
    """

    uploads_root.mkdir(parents=True, exist_ok=True)
    port_directory = uploads_root / "ports" / str(port_id)
    port_directory.mkdir(parents=True, exist_ok=True)

    original_name = sanitize_filename(upload.filename or upload.content_type or "upload")
    suffix = Path(original_name).suffix.lower()
    stored_name = f"{uuid4().hex}{suffix}"
    destination = port_directory / stored_name

    total_size = 0
    try:
        async with aiofiles.open(destination, "wb") as buffer:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > max_size:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="Uploaded file exceeds the 15 MB limit",
                    )
                await buffer.write(chunk)
    except Exception:
        if destination.exists():
            destination.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()

    relative_path = destination.relative_to(uploads_root.parent)
    return str(relative_path).replace("\\", "/"), total_size, original_name


def delete_stored_file(relative_path: str, uploads_root: Path) -> None:
    """Remove a previously stored file if it exists."""

    if not relative_path:
        return
    base_path = uploads_root.parent.resolve()
    candidate = (base_path / relative_path).resolve()
    if base_path not in candidate.parents and candidate != base_path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid file path")
    if candidate.exists():
        candidate.unlink()
