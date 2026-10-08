"""Rack item image library: service guards, repository dedup/attach, and UI wiring.

Mirrors the conventions of ``test_asset_photos.py`` (service tests that monkeypatch
the upload root, no live DB) and ``test_rack_connections.py`` (repository tests that
monkeypatch ``db`` with ``AsyncMock``).
"""
import asyncio
import json
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from app.repositories import rack_item_images as repo
from app.services import rack_item_images as service


def _mock_db(monkeypatch, fetch_one=(), fetch_all=(), lastrowid=70):
    fetch_one_mock = AsyncMock(side_effect=list(fetch_one))
    fetch_all_mock = AsyncMock(side_effect=list(fetch_all))
    execute = AsyncMock()
    insert = AsyncMock(return_value=lastrowid)
    monkeypatch.setattr(repo.db, "fetch_one", fetch_one_mock)
    monkeypatch.setattr(repo.db, "fetch_all", fetch_all_mock)
    monkeypatch.setattr(repo.db, "execute", execute)
    monkeypatch.setattr(repo.db, "execute_returning_lastrowid", insert)
    return execute, insert


def test_migration_creates_library_and_attachment_tables():
    sql = (Path(__file__).resolve().parents[1] / "migrations/461_rack_item_images.sql").read_text()
    assert sql.startswith("-- phase: expand")
    assert "CREATE TABLE IF NOT EXISTS rack_item_images" in sql
    assert "CREATE TABLE IF NOT EXISTS rack_equipment_images" in sql
    assert "UNIQUE (company_id, item_type, kind, content_hash)" in sql
    # Deleting a library image must cascade to its attachments.
    assert "REFERENCES rack_item_images (id) ON DELETE CASCADE" in sql
    # Attaching is idempotent via a unique pair.
    assert "UNIQUE (equipment_id, image_id)" in sql
    # The deployment runs MariaDB, so the DDL must be MySQL dialect (the SQLite
    # fallback is produced by `_adapt_sql_for_sqlite`, never stored here).
    assert "AUTO_INCREMENT" in sql
    assert "AUTOINCREMENT" not in sql
    assert "ENGINE=InnoDB" in sql
    assert "CREATE INDEX IF NOT EXISTS" not in sql


def test_resolve_rack_image_rejects_traversal(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "_UPLOADS_ROOT", tmp_path)
    with pytest.raises(service.RackImageError) as error:
        service.resolve_rack_image("../../etc/passwd")
    assert error.value.status_code == 400


def test_shop_import_and_rack_resolution_support_symlinked_uploads(monkeypatch, tmp_path):
    shared = tmp_path / "shared"
    (shared / "shop").mkdir(parents=True)
    uploads = tmp_path / "release-uploads"
    uploads.symlink_to(shared, target_is_directory=True)
    monkeypatch.setattr(service, "_UPLOADS_ROOT", uploads)
    payload = b"shop image"
    (shared / "shop/widget.jpg").write_bytes(payload)

    assert service.read_shop_product_image("/uploads/shop/widget.jpg") == (payload, "widget.jpg", "image/jpeg")
    assert service.resolve_rack_image("shop/widget.jpg") == shared / "shop/widget.jpg"
    (shared / "shop/escape.jpg").symlink_to(tmp_path / "outside.jpg")
    with pytest.raises(service.RackImageError):
        service.read_shop_product_image("/uploads/shop/escape.jpg")
    with pytest.raises(service.RackImageError):
        service.resolve_rack_image("../outside.jpg")
    service.remove_rack_image("shop/widget.jpg", None)
    assert not (shared / "shop/widget.jpg").exists()


def test_read_shop_product_image_resolves_local_and_rejects_other(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "_UPLOADS_ROOT", tmp_path)
    image_dir = tmp_path / "shop"
    image_dir.mkdir()
    payload = b"\xff\xd8fake-jpeg-bytes"
    (image_dir / "widget.jpg").write_bytes(payload)

    resolved = service.read_shop_product_image("/uploads/shop/widget.jpg")
    assert resolved == (payload, "widget.jpg", "image/jpeg")

    assert service.read_shop_product_image("https://cdn.example.com/x.jpg") is None
    assert service.read_shop_product_image("/uploads/../secret/x.jpg") is None
    assert service.read_shop_product_image("/uploads/missing/x.jpg") is None


def test_prepare_bytes_rejects_non_image(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "_UPLOADS_ROOT", tmp_path)
    with pytest.raises(service.RackImageError) as error:
        asyncio.run(service.prepare_bytes_rack_image(b"<script>alert(1)</script>", "x.png", 1, "switch", "device"))
    assert error.value.status_code == 400


def test_prepare_bytes_rejects_tiny_image(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "_UPLOADS_ROOT", tmp_path)
    buffer = BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, "PNG")
    with pytest.raises(service.RackImageError) as error:
        asyncio.run(service.prepare_bytes_rack_image(buffer.getvalue(), "tiny.png", 1, "switch", "device"))
    assert error.value.status_code == 400


@pytest.mark.parametrize("image_format, suffix", [("PNG", "png"), ("JPEG", "jpg"),
                                                 ("JPEG", "jpeg"), ("GIF", "gif"), ("WEBP", "webp")])
def test_prepare_bytes_writes_original_and_thumbnail(monkeypatch, tmp_path, image_format, suffix):
    monkeypatch.setattr(service, "_UPLOADS_ROOT", tmp_path)
    buffer = BytesIO()
    Image.new("RGB", (400, 300), "blue").save(buffer, image_format)

    result = asyncio.run(service.prepare_bytes_rack_image(buffer.getvalue(), "big." + suffix, 1, "switch", "product"))

    assert set(result) == {"storage_name", "thumbnail_name", "content_type", "size_bytes", "content_hash"}
    assert result["content_type"] == "image/" + ("jpeg" if image_format == "JPEG" else suffix)
    assert len(result["content_hash"]) == 64
    assert (tmp_path / result["storage_name"]).is_file()
    assert result["thumbnail_name"] is not None and (tmp_path / result["thumbnail_name"]).is_file()
    with Image.open(tmp_path / result["thumbnail_name"]) as thumbnail:
        assert thumbnail.format == image_format
        assert thumbnail.size == (360, 270)


def test_repo_create_image_dedupes_by_hash(monkeypatch):
    _execute, insert = _mock_db(monkeypatch, fetch_one=[{"id": 55}])
    image_id, created = asyncio.run(repo.create_image(
        1, "switch", "device", storage_name="a/b.png", thumbnail_name=None,
        content_type="image/png", size_bytes=10, content_hash="abc"))
    assert (image_id, created) == (55, False)
    assert insert.await_count == 0  # no insert for an identical existing image


def test_repo_create_image_inserts_new(monkeypatch):
    _execute, insert = _mock_db(monkeypatch, fetch_one=[None], lastrowid=70)
    image_id, created = asyncio.run(repo.create_image(
        1, "switch", "product", storage_name="a/b.jpg", thumbnail_name="a/b.jpg-thumb",
        content_type="image/jpeg", size_bytes=20, content_hash="def",
        caption="Front", source_product_id=9, uploaded_by=3))
    assert (image_id, created) == (70, True)
    insert_sql, insert_params = insert.await_args.args
    assert "INSERT INTO rack_item_images" in insert_sql
    assert insert_params[:4] == (1, "switch", "product", "a/b.jpg")


def test_repo_attach_rejects_type_mismatch(monkeypatch):
    execute, _insert = _mock_db(monkeypatch, fetch_one=[{"id": 8, "item_type": "server"}, {"id": 9, "item_type": "switch"}])
    with pytest.raises(ValueError, match="does not match"):
        asyncio.run(repo.attach(1, 8, 9))
    assert execute.await_count == 0


def test_repo_list_library_groups_by_kind(monkeypatch):
    _mock_db(monkeypatch, fetch_all=[[
        {"id": 1, "kind": "device", "caption": "Front", "content_type": "image/png",
         "size_bytes": 5, "source_product_id": None, "created_at": "2026-01-01 00:00:00"},
        {"id": 2, "kind": "product", "caption": None, "content_type": "image/jpeg",
         "size_bytes": 6, "source_product_id": 4, "created_at": "2026-01-02 00:00:00"},
    ]])
    grouped = asyncio.run(repo.list_library(1, "switch"))
    assert [image["id"] for image in grouped["device"]] == [1]
    assert [image["id"] for image in grouped["product"]] == [2]
    assert grouped["device"][0]["url"] == "/api/infrastructure/rack-item-images/1"
    assert grouped["device"][0]["thumb_url"] == "/api/infrastructure/rack-item-images/1/thumb"


def test_template_and_js_wire_the_gallery():
    base = Path(__file__).resolve().parents[1]
    template = (base / "app/templates/infrastructure/racks.html").read_text()
    script = (base / "app/static/js/racks.js").read_text()

    for marker in ("data-rack-images", "data-image-kind", "data-image-attached",
                   "data-image-library", "data-image-upload", "data-image-import",
                   "data-image-status"):
        assert marker in template, marker
    for endpoint in ("rack-item-images/library", "rack-item-images/import-shop",
                     "rack-equipment/${encodeURIComponent(editingId)}/images",
                     "addEventListener('open', load)"):
        assert endpoint in script, endpoint


def _import_context(monkeypatch, company_id=1):
    from app.features.assets import routes

    monkeypatch.setattr(routes, "_rack_image_context", AsyncMock(return_value=({"id": 3}, company_id)))
    monkeypatch.setattr(routes.shop_repo, "get_product_by_id", AsyncMock(return_value={
        "id": 9, "image_url": "/uploads/shop/widget.png"}))
    monkeypatch.setattr(routes.audit_service, "record", AsyncMock())
    request = AsyncMock()
    request.json.return_value = {"product_id": 9, "item_type": "switch", "kind": "product"}
    return routes, request


def test_import_shop_image_once_then_reuses_files_across_clients(monkeypatch, tmp_path):
    routes, request = _import_context(monkeypatch)
    monkeypatch.setattr(service, "_UPLOADS_ROOT", tmp_path)
    (tmp_path / "shop").mkdir()
    buffer = BytesIO()
    Image.new("RGB", (400, 300), "blue").save(buffer, "PNG")
    (tmp_path / "shop/widget.png").write_bytes(buffer.getvalue())
    find = AsyncMock(return_value=None)
    insert = AsyncMock(return_value=(70, True))
    monkeypatch.setattr(repo, "find_shop_image", find)
    monkeypatch.setattr(repo, "create_image", insert)

    response = asyncio.run(routes.import_shop_rack_item_image(request))
    assert response.status_code == 201
    assert json.loads(response.body) == {"id": 70, "duplicate": False}
    metadata = {key: value for key, value in insert.await_args.kwargs.items()
                if key not in {"source_product_id", "uploaded_by"}}
    find.return_value = {"id": 70, "company_id": 1, "item_type": "switch", **metadata}
    files = set(tmp_path.rglob("*"))
    # Reuse must succeed without reading the shop image or preparing any files.
    monkeypatch.setattr(service, "read_shop_product_image", lambda *args: pytest.fail("Read duplicate shop image"))
    prepare = AsyncMock(side_effect=AssertionError("Prepared duplicate image"))
    monkeypatch.setattr(service, "prepare_bytes_rack_image", prepare)
    insert.reset_mock()
    response = asyncio.run(routes.import_shop_rack_item_image(request))
    assert response.status_code == 200
    assert json.loads(response.body) == {"id": 70, "duplicate": True}
    insert.assert_not_awaited()

    # Another client's library entry references the same original and thumbnail.
    monkeypatch.setattr(routes, "_rack_image_context", AsyncMock(return_value=({"id": 3}, 2)))
    insert.return_value = (71, True)
    response = asyncio.run(routes.import_shop_rack_item_image(request))
    assert response.status_code == 200
    assert json.loads(response.body) == {"id": 71, "duplicate": True}
    assert insert.await_args.args == (2, "switch", "product")
    assert insert.await_args.kwargs["storage_name"] == metadata["storage_name"]
    assert insert.await_args.kwargs["thumbnail_name"] == metadata["thumbnail_name"]
    assert set(tmp_path.rglob("*")) == files
    prepare.assert_not_awaited()
    routes.shop_repo.get_product_by_id.assert_awaited_with(9, include_archived=True, company_id=2)


def test_import_cannot_reuse_an_inaccessible_shop_product(monkeypatch):
    routes, request = _import_context(monkeypatch)
    routes.shop_repo.get_product_by_id.return_value = None
    find = AsyncMock()
    monkeypatch.setattr(repo, "find_shop_image", find)
    with pytest.raises(routes.HTTPException) as error:
        asyncio.run(routes.import_shop_rack_item_image(request))
    assert error.value.status_code == 422
    find.assert_not_awaited()


@pytest.mark.parametrize("referenced", [True, False])
def test_delete_preserves_files_referenced_by_other_clients(monkeypatch, tmp_path, referenced):
    routes, request = _import_context(monkeypatch)
    monkeypatch.setattr(service, "_UPLOADS_ROOT", tmp_path)
    (tmp_path / "image.png").write_bytes(b"original")
    (tmp_path / "image.png-thumb").write_bytes(b"thumbnail")
    monkeypatch.setattr(repo, "delete_image", AsyncMock(return_value={
        "storage_name": "image.png", "thumbnail_name": "image.png-thumb"}))
    monkeypatch.setattr(repo, "storage_is_referenced", AsyncMock(return_value=referenced))
    response = asyncio.run(routes.delete_rack_item_image(request, 70))
    assert response.status_code == 200
    assert (tmp_path / "image.png").exists() == referenced
    assert (tmp_path / "image.png-thumb").exists() == referenced


def test_repo_shop_lookup_prefers_company_and_type_without_exposing_captions(monkeypatch):
    _mock_db(monkeypatch, fetch_one=[{"id": 70}])
    assert asyncio.run(repo.find_shop_image(2, "switch", "product", 9)) == {"id": 70}
    query, params = repo.db.fetch_one.await_args.args
    assert "source_product_id=%s AND kind=%s" in query
    assert "ORDER BY (company_id=%s AND item_type=%s) DESC" in query
    assert "caption" not in query
    assert params == (9, "product", 2, "switch")
