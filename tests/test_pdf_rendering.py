"""The PDF URL fetcher must not let rendered HTML reach local files or internal hosts."""

from __future__ import annotations

import socket

import pytest

from app.services import pdf_rendering


@pytest.fixture
def fetcher(monkeypatch, tmp_path):
    static_root = tmp_path / "static"
    (static_root / "images").mkdir(parents=True)
    (static_root / "images" / "logo.png").write_bytes(b"\x89PNG-logo")
    (static_root / "uploads").mkdir()
    (static_root / "uploads" / "doc.pdf").write_bytes(b"secret")
    monkeypatch.setattr(pdf_rendering, "_STATIC_ROOT", static_root)
    monkeypatch.setattr(pdf_rendering, "_portal_hostname", lambda: "portal.example.com")
    network_calls: list[str] = []

    def fake_network_fetch(self, url, headers=None):
        network_calls.append(url)
        return pdf_rendering.URLFetcherResponse(url, body=b"remote")

    monkeypatch.setattr(pdf_rendering.SafePdfUrlFetcher, "_fetch_remote", fake_network_fetch)
    instance = pdf_rendering.pdf_url_fetcher("http://attacker-host.test/")
    instance.network_calls = network_calls
    return instance


def _resolve_to(monkeypatch, address: str) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))],
    )


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/file",
        "gopher://127.0.0.1:6379/_INFO",
    ],
)
def test_non_http_schemes_are_refused(fetcher, url):
    with pytest.raises(ValueError):
        fetcher.fetch(url)
    assert fetcher.network_calls == []


@pytest.mark.parametrize(
    "url, address",
    [
        ("http://169.254.169.254/latest/meta-data/", "169.254.169.254"),
        ("http://127.0.0.1:6379/", "127.0.0.1"),
        ("http://intranet.example.com/logo.png", "10.0.0.5"),
        ("https://rebind.example.net/x.png", "192.168.1.10"),
    ],
)
def test_internal_destinations_are_refused(fetcher, monkeypatch, url, address):
    _resolve_to(monkeypatch, address)
    with pytest.raises(ValueError):
        fetcher.fetch(url)
    assert fetcher.network_calls == []


def test_request_host_header_does_not_unlock_internal_fetches(fetcher, monkeypatch):
    """base_url comes from the Host header, so it must not be trusted for private ranges."""
    _resolve_to(monkeypatch, "10.1.2.3")
    with pytest.raises(ValueError):
        fetcher.fetch("http://attacker-host.test/uploads/x.png")
    assert fetcher.network_calls == []


def test_public_images_are_fetched(fetcher, monkeypatch):
    _resolve_to(monkeypatch, "93.184.216.34")
    response = fetcher.fetch("https://cdn.supplier.example/product.jpg")
    assert response.read() == b"remote"
    assert fetcher.network_calls == ["https://cdn.supplier.example/product.jpg"]


def test_configured_portal_host_may_be_private(fetcher, monkeypatch):
    _resolve_to(monkeypatch, "10.0.0.20")
    fetcher.fetch("https://portal.example.com/uploads/knowledge-base/a.png")
    assert fetcher.network_calls == ["https://portal.example.com/uploads/knowledge-base/a.png"]


def test_configured_portal_host_still_blocks_loopback_and_metadata(fetcher, monkeypatch):
    _resolve_to(monkeypatch, "169.254.169.254")
    with pytest.raises(ValueError):
        fetcher.fetch("https://portal.example.com/x.png")


def test_same_origin_static_files_are_read_from_disk(fetcher):
    response = fetcher.fetch("http://attacker-host.test/static/images/logo.png")
    assert response.read() == b"\x89PNG-logo"
    assert fetcher.network_calls == []


@pytest.mark.parametrize(
    "path",
    ["/static/../../etc/passwd", "/static/%2e%2e/%2e%2e/etc/passwd", "/static/uploads/doc.pdf"],
)
def test_static_mapping_cannot_escape_or_read_uploads(fetcher, monkeypatch, path):
    _resolve_to(monkeypatch, "127.0.0.1")
    with pytest.raises(ValueError):
        fetcher.fetch("http://attacker-host.test" + path)
    assert fetcher.network_calls == []


def test_data_uris_are_allowed(fetcher):
    response = fetcher.fetch("data:image/png;base64,iVBORw0KGgo=")
    assert response.read().startswith(b"\x89PNG")


def test_redirects_are_not_followed():
    instance = pdf_rendering.pdf_url_fetcher(None)
    assert not any(
        handler.__class__.__name__ == "HTTPRedirectHandler" for handler in instance.handlers
    )


def test_every_weasyprint_call_site_uses_the_safe_fetcher():
    from pathlib import Path

    root = Path(pdf_rendering.__file__).resolve().parents[1]
    offenders = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if "HTML(string=" in line and "url_fetcher=" not in line:
                offenders.append(f"{path.relative_to(root)}:{line_number}")
    assert offenders == []
