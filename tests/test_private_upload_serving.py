"""Regression tests for the ``/uploads`` private upload handler."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.responses import RedirectResponse

from app import main as app_main
from app.services import ticket_attachments


@pytest.fixture
def uploads(tmp_path: Path, monkeypatch):
    root = tmp_path / "private_uploads"
    for rel in (
        "shop/prod.png",
        "shop/evil.html",
        "knowledge-base/img.png",
        "knowledge-base/attachments/1/doc.pdf",
        "tickets/abc.html",
        "compliance/essential8/evidence.html",
        "compliance/smb1001/evidence.pdf",
    ):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"<script>alert(1)</script>")
    monkeypatch.setattr(app_main, "_private_uploads_path", root)
    state = {"authenticated": True}

    async def fake_auth(request):
        if state["authenticated"]:
            return {"id": 1}, None
        return None, RedirectResponse("/login")

    monkeypatch.setattr(app_main, "_require_authenticated_user", fake_auth)
    return state


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "tickets/abc.html",
        "knowledge-base/attachments/1/doc.pdf",
        "compliance/smb1001/evidence.pdf",
        "compliance/essential8/evidence.html",
        "shop/evil.html",
    ],
)
async def test_non_linked_or_active_content_paths_are_not_served(uploads, path):
    with pytest.raises(HTTPException) as exc:
        await app_main.serve_private_upload(path, request=None)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_images_served_inline_with_nosniff(uploads):
    response = await app_main.serve_private_upload("shop/prod.png", request=None)
    assert response.media_type == "image/png"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "content-disposition" not in response.headers


@pytest.mark.asyncio
async def test_only_kb_images_are_public(uploads):
    uploads["authenticated"] = False
    response = await app_main.serve_private_upload("knowledge-base/img.png", request=None)
    assert response.media_type == "image/png"
    redirect = await app_main.serve_private_upload("shop/prod.png", request=None)
    assert isinstance(redirect, RedirectResponse)


@pytest.mark.parametrize("name", ["x.html", "x.HTM", "x.svg", "x.xsl", "x.shtml", "x.js"])
def test_ticket_attachment_dangerous_extensions_are_neutralised(name):
    assert ticket_attachments._generate_secure_filename(name).endswith(".bin")


def test_ticket_attachment_safe_extension_kept():
    assert ticket_attachments._generate_secure_filename("report.pdf").endswith(".pdf")


def test_static_uploads_are_served_as_inert_downloads(tmp_path: Path):
    from starlette.applications import Starlette
    from starlette.routing import Mount
    from starlette.testclient import TestClient

    (tmp_path / "ports").mkdir()
    (tmp_path / "ports" / "evil.xsl").write_text("<xsl:stylesheet/>")
    static_app = Starlette(
        routes=[Mount("/static/uploads", app_main._DownloadOnlyStaticFiles(directory=str(tmp_path)))]
    )
    response = TestClient(static_app).get("/static/uploads/ports/evil.xsl")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"] == "attachment"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_static_uploads_mounted_before_generic_static():
    mounts = [getattr(route, "path", None) for route in app_main.app.routes]
    assert mounts.index("/static/uploads") < mounts.index("/static")
