import asyncio
from types import SimpleNamespace

from starlette.requests import Request

from app.api.routes import tray


def test_wan_ip_endpoint_returns_configured_agent_source(monkeypatch):
    monkeypatch.setattr(
        tray,
        "_settings",
        SimpleNamespace(
            wan_ip_source_url="https://whoami.example.test/",
            wan_ip_source_field="Cf-Connecting-Ip",
        ),
    )
    request = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1)})

    response = asyncio.run(tray.get_scanner_wan_ip(request, {}))

    assert response == {
        "source_url": "https://whoami.example.test/",
        "source_field": "Cf-Connecting-Ip",
    }


def test_wan_ip_endpoint_keeps_legacy_fallback(monkeypatch):
    monkeypatch.setattr(
        tray,
        "_settings",
        SimpleNamespace(
            wan_ip_source_url=None,
            wan_ip_source_field="X-Forwarded-For",
        ),
    )
    request = Request(
        {"type": "http", "headers": [], "client": ("203.0.113.42", 12345)}
    )

    assert asyncio.run(tray.get_scanner_wan_ip(request, {})) == {
        "wan_ip": "203.0.113.42"
    }
