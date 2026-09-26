from io import BytesIO

import pytest
from fastapi import HTTPException, UploadFile
from PIL import Image

from app.services import asset_photos


@pytest.mark.anyio
async def test_prepare_reencodes_image_and_generates_safe_thumbnail(monkeypatch, tmp_path):
    monkeypatch.setattr(asset_photos, "ROOT", tmp_path)
    source = BytesIO()
    Image.new("RGB", (800, 600), "red").save(source, "PNG", pnginfo=None)
    upload = UploadFile(filename="../../photo.png", file=BytesIO(source.getvalue()))

    result = await asset_photos.prepare(upload, 2, 9)

    assert result["content_type"] == "image/jpeg"
    assert (tmp_path / "2" / "9" / result["storage_name"]).is_file()
    with Image.open(tmp_path / "2" / "9" / result["thumbnail_name"]) as thumbnail:
        assert max(thumbnail.size) <= 480
        assert thumbnail.format == "JPEG"


@pytest.mark.anyio
async def test_prepare_rejects_non_image(monkeypatch, tmp_path):
    monkeypatch.setattr(asset_photos, "ROOT", tmp_path)
    upload = UploadFile(filename="payload.jpg", file=BytesIO(b"<script>alert(1)</script>"))
    with pytest.raises(HTTPException) as error:
        await asset_photos.prepare(upload, 2, 9)
    assert error.value.status_code == 415
    assert not list(tmp_path.rglob("*"))


@pytest.mark.anyio
async def test_prepare_rejects_unapproved_decodable_format(monkeypatch, tmp_path):
    monkeypatch.setattr(asset_photos, "ROOT", tmp_path)
    source = BytesIO()
    Image.new("RGB", (10, 10)).save(source, "GIF")
    with pytest.raises(HTTPException) as error:
        await asset_photos.prepare(UploadFile(filename="image.gif", file=BytesIO(source.getvalue())), 2, 9)
    assert error.value.status_code == 415


def test_path_rejects_traversal(monkeypatch, tmp_path):
    monkeypatch.setattr(asset_photos, "ROOT", tmp_path)
    with pytest.raises(HTTPException):
        asset_photos.path(2, 9, "../secret")
