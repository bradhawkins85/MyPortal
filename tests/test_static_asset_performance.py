"""Static assets are hashed and compressed once per file version."""

from __future__ import annotations

import gzip
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from app.core import static_assets
from app.core.static_assets import CompressedStaticFiles, content_hash, release_files

CSS = ("body { color: #123456; margin: 0; }\n" * 200).encode()


def _client(directory: Path) -> TestClient:
    app = FastAPI()
    app.mount("/static", CompressedStaticFiles(directory=str(directory)), name="static")
    return TestClient(app)


def _bump_mtime(path: Path) -> None:
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))


def test_content_hash_is_cached_until_the_file_changes(tmp_path, monkeypatch):
    asset = tmp_path / "app.css"
    asset.write_bytes(b"one")
    first = content_hash(asset)

    reads: list[str] = []
    real_open = open

    def counting_open(path, *args, **kwargs):
        reads.append(str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", counting_open)
    assert content_hash(asset) == first
    assert reads == []

    asset.write_bytes(b"two")
    _bump_mtime(asset)
    assert content_hash(asset) not in ("", first)
    assert content_hash(tmp_path / "missing.css") == ""


def test_release_files_prunes_top_level_user_content(tmp_path):
    (tmp_path / "css").mkdir()
    (tmp_path / "css" / "app.css").write_text("a")
    (tmp_path / "uploads" / "deep").mkdir(parents=True)
    (tmp_path / "uploads" / "deep" / "file.txt").write_text("b")
    (tmp_path / "tray").mkdir()
    (tmp_path / "tray" / "myportal-tray.msi").write_bytes(b"c")
    (tmp_path / "css" / "tray").mkdir()
    (tmp_path / "css" / "tray" / "nested.css").write_text("d")

    found = [relative for relative, _, _ in release_files(tmp_path, skip_dirs=frozenset({"uploads", "tray"}))]

    assert found == ["css/app.css", "css/tray/nested.css"]


def test_release_manifest_skips_uploads_and_tray_installers(tmp_path, monkeypatch):
    import app.main as main_module

    static_root = tmp_path / "static"
    (static_root / "js").mkdir(parents=True)
    (static_root / "js" / "pwa.js").write_text("pwa")
    (static_root / "uploads").mkdir()
    (static_root / "uploads" / "legacy.pdf").write_bytes(b"pdf")
    (static_root / "tray").mkdir()
    (static_root / "tray" / "myportal-tray.msi").write_bytes(b"msi")
    templates_root = tmp_path / "templates"
    templates_root.mkdir()
    (templates_root / "base.html").write_text("<html></html>")

    monkeypatch.setattr(main_module.templates_config, "static_path", static_root)
    monkeypatch.setattr(main_module, "_RELEASE_HASH_ROOTS", (static_root, templates_root))

    manifest = main_module._release_manifest()

    assert set(manifest["content"]) == {"/static/js/pwa.js", "template:base.html"}
    assert manifest["assets"]["/static/js/pwa.js"] == content_hash(static_root / "js" / "pwa.js")

    (static_root / "tray" / "myportal-tray.msi").write_bytes(b"a refreshed installer")
    assert main_module._release_manifest()["release"] == manifest["release"]


def test_text_assets_are_served_gzip_compressed(tmp_path):
    (tmp_path / "app.css").write_bytes(CSS)
    client = _client(tmp_path)

    response = client.get("/static/app.css", headers={"Accept-Encoding": "gzip"})

    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    assert response.headers["vary"] == "Accept-Encoding"
    assert response.headers["etag"].startswith('W/"')
    assert "accept-ranges" not in response.headers
    assert int(response.headers["content-length"]) < len(CSS)
    # httpx decodes the body, so it must match the file byte for byte.
    assert response.content == CSS


def test_compressed_asset_revalidates_with_its_weak_etag(tmp_path):
    (tmp_path / "app.css").write_bytes(CSS)
    client = _client(tmp_path)
    etag = client.get("/static/app.css", headers={"Accept-Encoding": "gzip"}).headers["etag"]

    response = client.get(
        "/static/app.css", headers={"Accept-Encoding": "gzip", "If-None-Match": etag}
    )

    assert response.status_code == 304
    assert response.headers["vary"] == "Accept-Encoding"


def test_assets_are_uncompressed_without_gzip_or_for_ranges(tmp_path):
    (tmp_path / "app.css").write_bytes(CSS)
    client = _client(tmp_path)

    plain = client.get("/static/app.css", headers={"Accept-Encoding": "identity"})
    refused = client.get("/static/app.css", headers={"Accept-Encoding": "gzip;q=0"})
    ranged = client.get(
        "/static/app.css", headers={"Accept-Encoding": "gzip", "Range": "bytes=0-9"}
    )

    for response in (plain, refused):
        assert response.status_code == 200
        assert "content-encoding" not in response.headers
        assert response.headers["vary"] == "Accept-Encoding"
        assert response.content == CSS
    assert ranged.status_code == 206
    assert "content-encoding" not in ranged.headers
    assert ranged.content == CSS[:10]


def test_binary_and_tiny_files_are_not_compressed(tmp_path):
    (tmp_path / "logo.png").write_bytes(b"\x89PNG" + b"\x00" * 4096)
    (tmp_path / "tiny.js").write_bytes(b"let a = 1;")
    client = _client(tmp_path)

    png = client.get("/static/logo.png", headers={"Accept-Encoding": "gzip"})
    tiny = client.get("/static/tiny.js", headers={"Accept-Encoding": "gzip"})

    assert "content-encoding" not in png.headers
    assert "vary" not in png.headers
    assert "content-encoding" not in tiny.headers


def test_compressed_body_is_rebuilt_when_the_file_changes(tmp_path):
    asset = tmp_path / "app.js"
    asset.write_bytes(CSS)
    client = _client(tmp_path)
    first = client.get("/static/app.js", headers={"Accept-Encoding": "gzip"})

    updated = CSS.replace(b"#123456", b"#654321")
    asset.write_bytes(updated)
    _bump_mtime(asset)
    second = client.get("/static/app.js", headers={"Accept-Encoding": "gzip"})

    assert first.content == CSS
    assert second.content == updated
    assert second.headers["etag"] != first.headers["etag"]
    cached_body = static_assets._compressed_bodies[str(asset)][1]
    assert gzip.decompress(cached_body) == updated


def test_portal_middleware_stack_has_no_base_http_middleware():
    import app.main as main_module

    offenders = [
        middleware.cls
        for middleware in main_module.app.user_middleware
        if isinstance(middleware.cls, type) and issubclass(middleware.cls, BaseHTTPMiddleware)
    ]

    assert offenders == []
