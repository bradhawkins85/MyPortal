from __future__ import annotations

from io import BytesIO
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status
from PIL import Image, ImageOps, UnidentifiedImageError

ROOT = Path(__file__).resolve().parents[2] / "private_uploads" / "asset-photos"
MAX_BYTES = 15 * 1024 * 1024
MAX_PIXELS = 40_000_000
FORMATS = {"JPEG": ("image/jpeg", ".jpg"), "PNG": ("image/png", ".png"), "WEBP": ("image/webp", ".webp")}


async def prepare(upload: UploadFile, company_id: int, asset_id: int) -> dict[str, object]:
    data = await upload.read(MAX_BYTES + 1)
    await upload.close()
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Photo exceeds the 15 MB limit.")
    try:
        with Image.open(BytesIO(data)) as source:
            if source.format not in FORMATS:
                raise HTTPException(status_code=415, detail="Unsupported image. Use JPEG, PNG, or WebP.")
            source.verify()
        with Image.open(BytesIO(data)) as source:
            if source.width * source.height > MAX_PIXELS:
                raise HTTPException(status_code=413, detail="Photo dimensions are too large.")
            image = ImageOps.exif_transpose(source).convert("RGB")
            original = image.copy()
            thumb = image.copy()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise HTTPException(status_code=415, detail="Unsupported or invalid image. Use JPEG, PNG, or WebP.")
    directory = ROOT / str(company_id) / str(asset_id)
    directory.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex
    original_path, thumb_path = directory / f"{token}.jpg", directory / f"{token}-thumb.jpg"
    original.thumbnail((2560, 2560))
    original.save(original_path, "JPEG", quality=88, optimize=True)
    thumb.thumbnail((480, 480))
    thumb.save(thumb_path, "JPEG", quality=82, optimize=True)
    return {"storage_name": token + ".jpg", "thumbnail_name": token + "-thumb.jpg", "content_type": "image/jpeg", "size_bytes": original_path.stat().st_size}


def path(company_id: int, asset_id: int, name: str) -> Path:
    if Path(name).name != name:
        raise HTTPException(status_code=404, detail="Photo not found")
    candidate = (ROOT / str(company_id) / str(asset_id) / name).resolve()
    if ROOT.resolve() not in candidate.parents:
        raise HTTPException(status_code=404, detail="Photo not found")
    return candidate


def remove(company_id: int, asset_id: int, *names: str) -> None:
    for name in names:
        path(company_id, asset_id, name).unlink(missing_ok=True)
