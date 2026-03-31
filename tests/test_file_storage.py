from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile, status
from starlette.datastructures import Headers

from app.services.file_storage import (
    delete_stored_file,
    sanitize_filename,
    store_port_document,
    store_product_image,
)


def test_sanitize_filename_replaces_unsafe_characters():
    assert sanitize_filename("../dangerous/../file?.pdf") == "file_.pdf"


def test_sanitize_filename_handles_empty_values():
    assert sanitize_filename("") == "upload"


def test_delete_stored_file_rejects_outside_paths(tmp_path: Path):
    uploads_root = tmp_path / "static" / "uploads"
    uploads_root.mkdir(parents=True)
    with pytest.raises(HTTPException):
        delete_stored_file("../etc/passwd", uploads_root)


def _make_upload(data: bytes, filename: str, content_type: str) -> UploadFile:
    headers = Headers({"content-type": content_type})
    return UploadFile(file=BytesIO(data), filename=filename, headers=headers)


def test_store_product_image_persists_file(tmp_path: Path):
    uploads_root = tmp_path / "private_uploads"
    uploads_root.mkdir(parents=True)
    data = b"fake image data"
    upload = _make_upload(data, "test.png", "image/png")

    public_url, stored_path = asyncio.run(
        store_product_image(
            upload=upload,
            uploads_root=uploads_root,
            max_size=1024 * 1024,
        )
    )

    assert public_url.startswith("/uploads/shop/")
    assert stored_path.exists()
    assert stored_path.read_bytes() == data


def test_store_product_image_rejects_invalid_type(tmp_path: Path):
    uploads_root = tmp_path / "private_uploads"
    uploads_root.mkdir(parents=True)
    upload = _make_upload(b"data", "malicious.txt", "text/plain")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(store_product_image(upload=upload, uploads_root=uploads_root))
    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST


def test_store_product_image_enforces_size_limit(tmp_path: Path):
    uploads_root = tmp_path / "private_uploads"
    uploads_root.mkdir(parents=True)
    upload = _make_upload(b"a" * (1024 * 1024), "large.png", "image/png")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(store_product_image(upload=upload, uploads_root=uploads_root, max_size=1024))
    assert exc.value.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE


def test_store_port_document_rejects_html_type(tmp_path: Path):
    uploads_root = tmp_path / "static" / "uploads"
    upload = _make_upload(b"<script>alert(1)</script>", "payload.html", "text/html")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(store_port_document(port_id=1, upload=upload, uploads_root=uploads_root))

    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST


def test_store_port_document_persists_pdf(tmp_path: Path):
    uploads_root = tmp_path / "static" / "uploads"
    data = b"%PDF-1.7\n"
    upload = _make_upload(data, "manifest.pdf", "application/pdf")

    relative_path, size, original_name = asyncio.run(
        store_port_document(port_id=5, upload=upload, uploads_root=uploads_root)
    )

    assert relative_path.startswith("uploads/ports/5/")
    assert relative_path.endswith(".pdf")
    stored_path = uploads_root.parent / relative_path
    assert stored_path.exists()
    assert stored_path.read_bytes() == data
    assert size == len(data)
    assert original_name == "manifest.pdf"
