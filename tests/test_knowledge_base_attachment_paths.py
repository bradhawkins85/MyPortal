from __future__ import annotations

from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from app.api.routes import knowledge_base as knowledge_base_routes


def _pdf_upload(name: str = "guide.pdf", data: bytes = b"%PDF-1.7\n") -> UploadFile:
    headers = Headers({"content-type": "application/pdf"})
    return UploadFile(file=BytesIO(data), filename=name, headers=headers)


@pytest.mark.asyncio
async def test_upload_article_attachment_stores_file_under_private_upload_root(tmp_path: Path, monkeypatch):
    private_uploads = tmp_path / "private_uploads"
    monkeypatch.setattr(knowledge_base_routes, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(knowledge_base_routes, "_PRIVATE_UPLOADS_PATH", private_uploads)
    monkeypatch.setattr(
        knowledge_base_routes,
        "_KB_ATTACHMENTS_ROOT",
        private_uploads / "knowledge-base" / "attachments",
    )
    monkeypatch.setattr(
        knowledge_base_routes.kb_repo,
        "get_article_by_id",
        AsyncMock(return_value={"id": 7}),
    )

    captured: dict[str, object] = {}

    async def fake_create_attachment(article_id: int, **kwargs):
        captured.update(kwargs)
        return {"id": 11, "article_id": article_id, **kwargs}

    monkeypatch.setattr(knowledge_base_routes.kb_repo, "create_attachment", fake_create_attachment)

    response = await knowledge_base_routes.upload_article_attachment(
        article_id=7,
        file=_pdf_upload(),
        current_user={"id": 3},
    )

    assert response["article_id"] == 7
    storage_path = str(captured["storage_path"])
    assert storage_path.startswith("private_uploads/knowledge-base/attachments/7/")
    assert (tmp_path / storage_path).is_file()


@pytest.mark.asyncio
async def test_upload_article_attachment_rejects_symlink_escape(tmp_path: Path, monkeypatch):
    private_uploads = tmp_path / "private_uploads"
    attachments_root = private_uploads / "knowledge-base" / "attachments"
    outside = tmp_path / "outside"
    outside.mkdir()
    attachments_root.mkdir(parents=True)
    try:
        (attachments_root / "9").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Symlinks not available in this test environment: {exc}")

    monkeypatch.setattr(knowledge_base_routes, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(knowledge_base_routes, "_PRIVATE_UPLOADS_PATH", private_uploads)
    monkeypatch.setattr(knowledge_base_routes, "_KB_ATTACHMENTS_ROOT", attachments_root)
    monkeypatch.setattr(
        knowledge_base_routes.kb_repo,
        "get_article_by_id",
        AsyncMock(return_value={"id": 9}),
    )
    monkeypatch.setattr(
        knowledge_base_routes.kb_repo,
        "create_attachment",
        AsyncMock(side_effect=AssertionError("create_attachment should not be called for escaped paths")),
    )

    with pytest.raises(HTTPException) as exc:
        await knowledge_base_routes.upload_article_attachment(
            article_id=9,
            file=_pdf_upload(),
            current_user={"id": 3},
        )

    assert exc.value.status_code == 400


@pytest.mark.parametrize(
    "storage_path",
    [
        "../../etc/passwd",
        "/etc/passwd",
        "private_uploads/knowledge-base/attachments/5/../secret.txt",
        "private_uploads/knowledge-base/attachments/6/doc.pdf",
        "private_uploads/knowledge-base/attachments/5/",
    ],
)
def test_resolve_attachment_record_path_rejects_escaped_or_mismatched_paths(
    tmp_path: Path,
    monkeypatch,
    storage_path: str,
):
    private_uploads = tmp_path / "private_uploads"
    monkeypatch.setattr(knowledge_base_routes, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(knowledge_base_routes, "_PRIVATE_UPLOADS_PATH", private_uploads)
    monkeypatch.setattr(
        knowledge_base_routes,
        "_KB_ATTACHMENTS_ROOT",
        private_uploads / "knowledge-base" / "attachments",
    )

    assert knowledge_base_routes._resolve_attachment_record_path(5, storage_path) is None
