"""Rack item image library: service guards, repository dedup/attach, and UI wiring.

Mirrors the conventions of ``test_asset_photos.py`` (service tests that monkeypatch
the upload root, no live DB) and ``test_rack_connections.py`` (repository tests that
monkeypatch ``db`` with ``AsyncMock``).
"""
import asyncio
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


def test_prepare_bytes_writes_original_and_thumbnail(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "_UPLOADS_ROOT", tmp_path)
    buffer = BytesIO()
    Image.new("RGB", (400, 300), "blue").save(buffer, "PNG")

    result = asyncio.run(service.prepare_bytes_rack_image(buffer.getvalue(), "big.png", 1, "switch", "product"))

    assert set(result) == {"storage_name", "thumbnail_name", "content_type", "size_bytes", "content_hash"}
    assert result["content_type"] == "image/png"
    assert len(result["content_hash"]) == 64
    assert (tmp_path / result["storage_name"]).is_file()
    assert result["thumbnail_name"] is not None and (tmp_path / result["thumbnail_name"]).is_file()


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