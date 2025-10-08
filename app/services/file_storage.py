from __future__ import annotations

import re
from pathlib import Path
from typing import Tuple
from uuid import uuid4

import aiofiles
from fastapi import HTTPException, UploadFile, status

_MAX_FILE_SIZE = 15 * 1024 * 1024  # 15 MB
_SAFE_FILENAME_PATTERN = re.compile(r"[^A-Za-z0-9._-]")
_ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_IMAGE_CONTENT_TYPE_MAP = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/pjpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


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


async def store_product_image(
    *,
    upload: UploadFile,
    uploads_root: Path,
    max_size: int = 5 * 1024 * 1024,
) -> tuple[str, Path]:
    """Persist a validated product image in the private uploads directory.

    Returns a tuple of (public_url, filesystem_path). The image is validated to
    ensure only common web image formats are stored and that oversized uploads
    are rejected before touching disk.
    """

    uploads_root.mkdir(parents=True, exist_ok=True)
    product_directory = uploads_root / "shop"
    product_directory.mkdir(parents=True, exist_ok=True)

    original_name = sanitize_filename(upload.filename or upload.content_type or "upload")
    suffix = Path(original_name).suffix.lower()
    content_type = (upload.content_type or "").lower()

    if suffix not in _ALLOWED_IMAGE_EXTENSIONS:
        suffix = _IMAGE_CONTENT_TYPE_MAP.get(content_type, suffix)
    if suffix not in _ALLOWED_IMAGE_EXTENSIONS:
        await upload.close()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported image type. Upload PNG, JPEG, GIF, or WebP files.",
        )

    stored_name = f"{uuid4().hex}{suffix}"
    destination = product_directory / stored_name

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
                        detail="Uploaded image exceeds the 5 MB limit.",
                    )
                await buffer.write(chunk)
    except Exception:
        if destination.exists():
            destination.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()

    public_url = f"/uploads/shop/{stored_name}"
    return public_url, destination
