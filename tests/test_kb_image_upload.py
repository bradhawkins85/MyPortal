"""Tests for knowledge base image upload functionality."""
from __future__ import annotations

import io
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers
from fastapi.testclient import TestClient

from app.services import file_storage
from app import main as app_main


@pytest.mark.asyncio
async def test_store_knowledge_base_image():
    """Test storing a knowledge base image."""
    # Create a mock upload file
    image_data = b"fake image data"
    
    # Create headers with content type
    headers = Headers({"content-type": "image/png"})
    
    upload = UploadFile(
        filename="test.png",
        file=io.BytesIO(image_data),
        headers=headers,
    )
    
    # Use a temporary directory for testing
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        uploads_root = Path(tmpdir)
        
        # Store the image
        image_url = await file_storage.store_knowledge_base_image(
            upload=upload,
            uploads_root=uploads_root,
        )
        
        # Check that the URL is correct
        assert image_url.startswith("/uploads/knowledge-base/")
        assert image_url.endswith(".png")
        
        # Check that the file was created
        kb_directory = uploads_root / "knowledge-base"
        assert kb_directory.exists()
        
        # Check that there's one file in the directory
        files = list(kb_directory.glob("*.png"))
        assert len(files) == 1
        
        # Check that the file has the correct content
        with open(files[0], "rb") as f:
            content = f.read()
            assert content == image_data


@pytest.mark.asyncio
async def test_store_knowledge_base_image_unsupported_type():
    """Test that uploading an unsupported file type raises an error."""
    from fastapi import HTTPException
    
    # Create a mock upload file with unsupported type
    headers = Headers({"content-type": "text/plain"})
    
    upload = UploadFile(
        filename="test.txt",
        file=io.BytesIO(b"not an image"),
        headers=headers,
    )
    
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        uploads_root = Path(tmpdir)
        
        # Store the image should raise an error
        with pytest.raises(HTTPException) as exc_info:
            await file_storage.store_knowledge_base_image(
                upload=upload,
                uploads_root=uploads_root,
            )
        
        assert exc_info.value.status_code == 400
        assert "Unsupported image type" in exc_info.value.detail


@pytest.mark.asyncio
async def test_store_knowledge_base_image_too_large():
    """Test that uploading a file that's too large raises an error."""
    from fastapi import HTTPException
    
    # Create a mock upload file that's too large
    large_data = b"x" * (6 * 1024 * 1024)  # 6 MB
    headers = Headers({"content-type": "image/png"})
    
    upload = UploadFile(
        filename="large.png",
        file=io.BytesIO(large_data),
        headers=headers,
    )
    
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        uploads_root = Path(tmpdir)

        # Store the image should raise an error
        with pytest.raises(HTTPException) as exc_info:
            await file_storage.store_knowledge_base_image(
                upload=upload,
                uploads_root=uploads_root,
            )

        assert exc_info.value.status_code == 413
        assert "exceeds the 5 MB limit" in exc_info.value.detail


def test_public_access_for_knowledge_base_images(tmp_path, monkeypatch):
    """Knowledge base images should load without authentication."""

    uploads_root = tmp_path / "knowledge-base"
    uploads_root.mkdir(parents=True)
    image_path = uploads_root / "example.png"
    image_bytes = b"kb image"
    image_path.write_bytes(image_bytes)

    auth_called = False
    sanitized_inputs: list[str] = []

    async def fake_require_authenticated_user(request):
        nonlocal auth_called
        auth_called = True
        return {}, None

    original_sanitizer = app_main._sanitize_upload_path

    def capture_sanitized_path(path: str):
        sanitized_inputs.append(path)
        return original_sanitizer(path)

    monkeypatch.setattr(app_main, "_private_uploads_path", tmp_path)
    monkeypatch.setattr(app_main, "_require_authenticated_user", fake_require_authenticated_user)
    monkeypatch.setattr(app_main, "_sanitize_upload_path", capture_sanitized_path)

    @asynccontextmanager
    async def noop_lifespan(app):
        yield

    monkeypatch.setattr(app_main.app.router, "lifespan_context", noop_lifespan)

    with TestClient(app_main.app) as client:
        response = client.get("/uploads/knowledge-base/example.png")

    assert response.status_code == 200
    assert response.content == image_bytes
    assert auth_called is False
    assert sanitized_inputs == ["knowledge-base/example.png"]
