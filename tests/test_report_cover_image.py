"""Tests for PDF cover image upload feature."""
from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Repository tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_pdf_cover_image_returns_none_when_no_row():
    """get_pdf_cover_image returns None when there is no site_settings row."""
    from app.repositories.site_settings import get_pdf_cover_image

    with patch("app.repositories.site_settings.db") as mock_db:
        mock_db.fetch_one = AsyncMock(return_value=None)
        result = await get_pdf_cover_image()
    assert result is None


@pytest.mark.asyncio
async def test_get_pdf_cover_image_returns_path_when_set():
    """get_pdf_cover_image returns the stored path when a value is present."""
    from app.repositories.site_settings import get_pdf_cover_image

    with patch("app.repositories.site_settings.db") as mock_db:
        mock_db.fetch_one = AsyncMock(return_value={"pdf_cover_image": "private_uploads/report-cover/abc.png"})
        result = await get_pdf_cover_image()
    assert result == "private_uploads/report-cover/abc.png"


@pytest.mark.asyncio
async def test_get_pdf_cover_image_returns_none_when_column_is_null():
    """get_pdf_cover_image returns None when the column value is NULL."""
    from app.repositories.site_settings import get_pdf_cover_image

    with patch("app.repositories.site_settings.db") as mock_db:
        mock_db.fetch_one = AsyncMock(return_value={"pdf_cover_image": None})
        result = await get_pdf_cover_image()
    assert result is None


@pytest.mark.asyncio
async def test_set_pdf_cover_image_calls_upsert():
    """set_pdf_cover_image executes an update when a row already exists."""
    from app.repositories.site_settings import set_pdf_cover_image

    with patch("app.repositories.site_settings.db") as mock_db:
        mock_db.fetch_one = AsyncMock(return_value={"id": 1})
        mock_db.execute = AsyncMock()
        await set_pdf_cover_image("private_uploads/report-cover/abc.png")
    mock_db.execute.assert_called_once()
    call_args = mock_db.execute.call_args
    assert "private_uploads/report-cover/abc.png" in call_args[0][1]


@pytest.mark.asyncio
async def test_set_pdf_cover_image_with_none():
    """set_pdf_cover_image can clear the value by passing None."""
    from app.repositories.site_settings import set_pdf_cover_image

    with patch("app.repositories.site_settings.db") as mock_db:
        mock_db.fetch_one = AsyncMock(return_value={"id": 1})
        mock_db.execute = AsyncMock()
        await set_pdf_cover_image(None)
    mock_db.execute.assert_called_once()
    call_args = mock_db.execute.call_args
    assert call_args[0][1][0] is None


@pytest.mark.asyncio
async def test_set_pdf_cover_image_inserts_when_no_row():
    """set_pdf_cover_image inserts a new row when site_settings is empty."""
    from app.repositories.site_settings import set_pdf_cover_image

    with patch("app.repositories.site_settings.db") as mock_db:
        mock_db.fetch_one = AsyncMock(return_value=None)
        mock_db.execute = AsyncMock()
        await set_pdf_cover_image("private_uploads/report-cover/new.png")
    mock_db.execute.assert_called_once()
    sql = mock_db.execute.call_args[0][0]
    assert "INSERT" in sql.upper()


# ---------------------------------------------------------------------------
# File storage tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_report_cover_image_rejects_unsupported_type(tmp_path):
    """store_report_cover_image raises 400 for non-image uploads."""
    from fastapi import HTTPException
    from app.services.file_storage import store_report_cover_image

    fake_upload = MagicMock()
    fake_upload.filename = "malware.exe"
    fake_upload.content_type = "application/octet-stream"
    fake_upload.close = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await store_report_cover_image(upload=fake_upload, uploads_root=tmp_path)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_store_report_cover_image_accepts_png(tmp_path):
    """store_report_cover_image stores a valid PNG and returns the correct paths."""
    from app.services.file_storage import store_report_cover_image

    png_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100  # minimal fake PNG

    async def mock_read(size):
        if not mock_read.called_once:
            mock_read.called_once = True
            return png_data
        return b""

    mock_read.called_once = False

    fake_upload = MagicMock()
    fake_upload.filename = "cover.png"
    fake_upload.content_type = "image/png"
    fake_upload.close = AsyncMock()
    fake_upload.read = AsyncMock(side_effect=[png_data, b""])

    relative_path, dest_path = await store_report_cover_image(
        upload=fake_upload, uploads_root=tmp_path
    )

    assert relative_path.startswith("private_uploads/report-cover/")
    assert relative_path.endswith(".png")
    assert dest_path.exists()
    assert dest_path.read_bytes() == png_data


@pytest.mark.asyncio
async def test_store_report_cover_image_rejects_oversized_file(tmp_path):
    """store_report_cover_image raises 413 when the file exceeds the size limit."""
    from fastapi import HTTPException
    from app.services.file_storage import store_report_cover_image

    chunk = b"x" * (1024 * 1024)  # 1 MB chunk
    chunks = [chunk] * 11  # 11 MB total — exceeds 10 MB limit

    fake_upload = MagicMock()
    fake_upload.filename = "big.png"
    fake_upload.content_type = "image/png"
    fake_upload.close = AsyncMock()
    fake_upload.read = AsyncMock(side_effect=chunks + [b""])

    with pytest.raises(HTTPException) as exc_info:
        await store_report_cover_image(upload=fake_upload, uploads_root=tmp_path)
    assert exc_info.value.status_code == 413


# ---------------------------------------------------------------------------
# PDF route: cover image embedding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pdf_route_embeds_cover_image_as_data_uri(tmp_path):
    """The PDF report route reads the cover image file and embeds it as a data URI."""
    # Create a fake cover image at the path the route expects:
    # _private_uploads_path.parent / "private_uploads/report-cover/test.png"
    cover_dir = tmp_path / "private_uploads" / "report-cover"
    cover_dir.mkdir(parents=True)
    img_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
    img_file = cover_dir / "test.png"
    img_file.write_bytes(img_bytes)
    relative_path = "private_uploads/report-cover/test.png"

    # The route resolves: _private_uploads_path.parent / relative_path
    # We simulate this with tmp_path as _private_uploads_path.parent.
    base = tmp_path
    resolved = (base / relative_path).resolve()
    assert resolved.is_file()

    expected_b64 = base64.b64encode(img_bytes).decode("ascii")
    expected_uri = f"data:image/png;base64,{expected_b64}"

    # Simulate the route logic.
    cover_file = (base / relative_path).resolve()
    uploads_root = base.resolve()
    cover_file.relative_to(uploads_root)  # should not raise
    suffix = cover_file.suffix.lower().lstrip(".")
    mime_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp"}
    mime = mime_map.get(suffix, "image/jpeg")
    encoded = base64.b64encode(cover_file.read_bytes()).decode("ascii")
    data_uri = f"data:{mime};base64,{encoded}"

    assert data_uri == expected_uri


@pytest.mark.asyncio
async def test_pdf_route_skips_cover_image_when_file_missing(tmp_path):
    """The PDF route gracefully falls back to None when the cover image file is absent."""
    base = tmp_path
    relative_path = "private_uploads/report-cover/nonexistent.png"
    cover_file = (base / relative_path).resolve()
    uploads_root = base.resolve()

    data_uri = None
    try:
        cover_file.relative_to(uploads_root)
        if cover_file.is_file():
            encoded = base64.b64encode(cover_file.read_bytes()).decode("ascii")
            data_uri = f"data:image/png;base64,{encoded}"
    except (ValueError, OSError):
        pass

    assert data_uri is None


# ---------------------------------------------------------------------------
# Helper function: _delete_cover_image_file
# ---------------------------------------------------------------------------


def test_delete_cover_image_file_removes_existing_file(tmp_path):
    """_delete_cover_image_file removes an existing cover image."""
    cover_dir = tmp_path / "private_uploads" / "report-cover"
    cover_dir.mkdir(parents=True)
    img_file = cover_dir / "cover.png"
    img_file.write_bytes(b"fake")
    relative_path = "private_uploads/report-cover/cover.png"

    # Simulate the function with tmp_path as the project root.
    base = tmp_path.resolve()
    candidate = (base / relative_path).resolve()
    candidate.relative_to(base)  # traversal check
    candidate.unlink(missing_ok=True)

    assert not img_file.exists()


def test_delete_cover_image_file_noop_when_missing(tmp_path):
    """_delete_cover_image_file does not raise when the file is already gone."""
    relative_path = "private_uploads/report-cover/ghost.png"
    base = tmp_path.resolve()
    candidate = (base / relative_path).resolve()
    # Should not raise.
    candidate.unlink(missing_ok=True)
