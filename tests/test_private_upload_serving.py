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
    state = {"authenticated": True, "can_access_shop": True}

    async def fake_auth(request):
        if state["authenticated"]:
            user = {"id": 1, "company_id": 5}
            if state.get("super_admin"):
                user["is_super_admin"] = True
            return user, None
        return None, RedirectResponse("/login")

    async def fake_membership(request, user_id, company_id):
        return {"can_access_shop": state["can_access_shop"]}

    async def fake_product_ids(image_url):
        return list(state.get("product_ids", []))

    async def fake_excluded(company_id, product_ids):
        return list(state.get("excluded_product_ids", []))

    monkeypatch.setattr(app_main, "_require_authenticated_user", fake_auth)
    monkeypatch.setattr(app_main, "_get_effective_company_membership", fake_membership)
    monkeypatch.setattr(app_main.shop_repo, "get_product_ids_by_image_url", fake_product_ids)
    monkeypatch.setattr(app_main.shop_repo, "get_excluded_product_ids", fake_excluded)
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
async def test_shop_image_hidden_without_shop_permission(uploads):
    uploads["can_access_shop"] = False
    with pytest.raises(HTTPException) as exc:
        await app_main.serve_private_upload("shop/prod.png", request=None)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_shop_image_hidden_when_product_excluded_for_company(uploads):
    uploads["product_ids"] = [77]
    uploads["excluded_product_ids"] = [77]
    with pytest.raises(HTTPException) as exc:
        await app_main.serve_private_upload("shop/prod.png", request=None)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_super_admin_can_view_shop_image(uploads):
    uploads["super_admin"] = True
    response = await app_main.serve_private_upload("shop/prod.png", request=None)
    assert response.media_type == "image/png"


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
