"""Validated storage and resolution of rack item images.

Images live in ``private_uploads/rack-item-images/<company>/<type>/<kind>/`` and
are served only through the access-controlled infrastructure routes. The service
is the on-disk half of the feature; the repository keeps the catalogue rows.
"""
from __future__ import annotations

import hashlib
import uuid
from io import BytesIO
from pathlib import Path

from fastapi import HTTPException, status, UploadFile

from app.services.file_storage import (
    _ALLOWED_IMAGE_EXTENSIONS,
    _IMAGE_CONTENT_TYPE_MAP,
    sanitize_filename,
)

MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB, matching shop product images
THUMBNAIL_WIDTH = 360
_MIN_EDGE = 16
_EXT_CONTENT_TYPE = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp",
}
# Private uploads root, resolved from the project location (tests monkeypatch this).
_UPLOADS_ROOT = Path(__file__).resolve().parents[2] / "private_uploads"
_IMAGE_FOLDER = "rack-item-images"


class RackImageError(HTTPException):
    """Raised for operator-facing image problems (bad type, oversize, invalid)."""


def _image_directory(company_id: int, item_type: str, kind: str) -> Path:
    return _UPLOADS_ROOT / _IMAGE_FOLDER / str(company_id) / item_type / kind


def _resolve_under_base(base: Path, candidate: Path) -> Path:
    """Resolve *candidate* and prove it stays inside *base* (path-traversal guard)."""
    try:
        base = base.resolve(strict=False)
        resolved = candidate.resolve(strict=False)
    except OSError as exc:  # pragma: no cover - defensive
        raise RackImageError(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid image path") from exc
    if not (resolved == base or base in resolved.parents):
        raise RackImageError(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid image path")
    return resolved


def resolve_rack_image(storage_name: str, variant: str = "full") -> Path:
    """Return the filesystem path for a stored image or its thumbnail."""
    name = Path(storage_name) if variant == "full" else Path(storage_name).with_name(Path(storage_name).name + "-thumb")
    return _resolve_under_base(_UPLOADS_ROOT, _UPLOADS_ROOT / name)


def remove_rack_image(storage_name: str, thumbnail_name: str | None) -> None:
    """Delete an image and its thumbnail from disk, ignoring missing files."""
    for value in (storage_name, thumbnail_name):
        if not value:
            continue
        try:
            _resolve_under_base(_UPLOADS_ROOT, _UPLOADS_ROOT / value).unlink(missing_ok=True)
        except RackImageError:  # pragma: no cover - defensive
            pass



async def prepare_rack_image(
    upload: UploadFile,
    company_id: int,
    item_type: str,
    kind: str,
) -> dict[str, object]:
    """Validate an upload, persist the original and a thumbnail, and return metadata.

    Returns ``storage_name`` (relative to the uploads root), ``thumbnail_name``,
    ``content_type``, ``size_bytes`` and a ``content_hash`` used to dedupe identical
    uploads across devices of the same type.
    """
    from PIL import Image, UnidentifiedImageError  # local import keeps the module import-light

    if kind not in {"device", "product"}:
        raise RackImageError(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid image kind")
    item_type = item_type.strip()
    if not item_type or len(item_type) > 20:
        raise RackImageError(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid item type")

    original_name = sanitize_filename(upload.filename or upload.content_type or "upload")
    suffix = Path(original_name).suffix.lower()
    content_type = (upload.content_type or "").lower()
    if suffix not in _ALLOWED_IMAGE_EXTENSIONS:
        suffix = _IMAGE_CONTENT_TYPE_MAP.get(content_type, suffix)
    if suffix not in _ALLOWED_IMAGE_EXTENSIONS:
        await upload.close()
        raise RackImageError(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported image type. Upload PNG, JPEG, GIF, or WebP files.",
        )

    # Read and size-cap the whole upload (capped at 5 MB, so holding it in memory is safe).
    chunks: list[bytes] = []
    total_size = 0
    try:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            total_size += len(chunk)
            if total_size > MAX_IMAGE_SIZE:
                raise RackImageError(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="Uploaded image exceeds the 5 MB limit.",
                )
            chunks.append(chunk)
    finally:
        await upload.close()
    payload = b"".join(chunks)

    try:
        image = Image.open(BytesIO(payload))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise RackImageError(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is not a valid image.",
        ) from exc

    width, height = image.size
    if width < _MIN_EDGE or height < _MIN_EDGE:
        raise RackImageError(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Image is too small. Upload an image at least 16 pixels wide and tall.",
        )

    target_content_type = content_type or _EXT_CONTENT_TYPE.get(suffix) or "image/jpeg"
    directory = _image_directory(company_id, item_type, kind)
    directory.mkdir(parents=True, exist_ok=True)
    original = directory / f"{uuid.uuid4().hex}{suffix}"
    original.write_bytes(payload)

    thumbnail_name: str | None = None
    if width > THUMBNAIL_WIDTH:
        thumbnail = image.copy()
        ratio = THUMBNAIL_WIDTH / width
        thumbnail.thumbnail((THUMBNAIL_WIDTH, max(1, round(height * ratio))))
        thumb_path = directory / (original.name + "-thumb")
        if suffix == ".jpg":
            thumbnail.convert("RGB").save(thumb_path, "JPEG", quality=85)
        else:
            thumbnail.save(thumb_path, format=Image.registered_extensions()[suffix])
        thumbnail_name = str(Path(directory).relative_to(_UPLOADS_ROOT) / thumb_path.name).replace("\\", "/")

    content_hash = hashlib.sha256(original.read_bytes()).hexdigest()
    storage_name = str(Path(directory).relative_to(_UPLOADS_ROOT) / original.name).replace("\\", "/")
    return {
        "storage_name": storage_name,
        "thumbnail_name": thumbnail_name,
        "content_type": target_content_type,
        "size_bytes": total_size,
        "content_hash": content_hash,
    }


async def prepare_bytes_rack_image(
    payload: bytes,
    filename: str,
    company_id: int,
    item_type: str,
    kind: str,
) -> dict[str, object]:
    """Validate in-memory image bytes, persist original + thumbnail, return metadata.

    Used by the shop import path, which reads an existing local image file rather than
    receiving a multipart upload. Returns the same shape as :func:`prepare_rack_image`.
    """
    from PIL import Image, UnidentifiedImageError  # local import keeps the module import-light

    if kind not in {"device", "product"}:
        raise RackImageError(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid image kind")
    item_type = item_type.strip()
    if not item_type or len(item_type) > 20:
        raise RackImageError(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid item type")
    if len(payload) > MAX_IMAGE_SIZE:
        raise RackImageError(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Image exceeds the 5 MB limit.")

    suffix = Path(sanitize_filename(filename)).suffix.lower()
    if suffix not in _ALLOWED_IMAGE_EXTENSIONS:
        raise RackImageError(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported image type.")

    try:
        image = Image.open(BytesIO(payload))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise RackImageError(status_code=status.HTTP_400_BAD_REQUEST, detail="Not a valid image.") from exc

    width, height = image.size
    if width < _MIN_EDGE or height < _MIN_EDGE:
        raise RackImageError(status_code=status.HTTP_400_BAD_REQUEST, detail="Image is too small.")

    directory = _image_directory(company_id, item_type, kind)
    directory.mkdir(parents=True, exist_ok=True)
    original = directory / f"{uuid.uuid4().hex}{suffix}"
    original.write_bytes(payload)

    thumbnail_name: str | None = None
    if width > THUMBNAIL_WIDTH:
        thumbnail = image.copy()
        ratio = THUMBNAIL_WIDTH / width
        thumbnail.thumbnail((THUMBNAIL_WIDTH, max(1, round(height * ratio))))
        thumb_path = directory / (original.name + "-thumb")
        if suffix == ".jpg":
            thumbnail.convert("RGB").save(thumb_path, "JPEG", quality=85)
        else:
            thumbnail.save(thumb_path, format=Image.registered_extensions()[suffix])
        thumbnail_name = str(Path(directory).relative_to(_UPLOADS_ROOT) / thumb_path.name).replace("\\", "/")

    content_hash = hashlib.sha256(payload).hexdigest()
    storage_name = str(Path(directory).relative_to(_UPLOADS_ROOT) / original.name).replace("\\", "/")
    return {
        "storage_name": storage_name,
        "thumbnail_name": thumbnail_name,
        "content_type": _EXT_CONTENT_TYPE.get(suffix) or "image/jpeg",
        "size_bytes": len(payload),
        "content_hash": content_hash,
    }


def read_shop_product_image(image_url: str) -> tuple[bytes, str, str] | None:
    """Return (bytes, filename, content_type) for a shop product's local image.

    Only resolves ``/uploads/...`` paths that point at files inside the uploads root.
    External or otherwise unresolvable URLs return ``None`` so the importer can report
    that a product image cannot be imported rather than fetching remote content.
    """
    if not image_url or not image_url.startswith("/uploads/"):
        return None
    name = image_url[len("/uploads/"):]
    if not name or name.startswith(("/", "\\")) or ".." in Path(name).parts:
        return None
    path = _resolve_under_base(_UPLOADS_ROOT, _UPLOADS_ROOT / name)
    if not path.is_file():
        return None
    payload = path.read_bytes()
    suffix = path.suffix.lower()
    if suffix not in _ALLOWED_IMAGE_EXTENSIONS:
        return None
    content_type = _EXT_CONTENT_TYPE.get(suffix) or "application/octet-stream"
    return payload, path.name, content_type
