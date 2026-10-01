from __future__ import annotations

import asyncio
import socket

import httpx
import pytest

from app.services import outbound_url_guard as guard
from app.services import service_status as service_status_svc
from app.services import webhook_monitor


def _fake_resolver(mapping):
    def _getaddrinfo(host, *args, **kwargs):
        ip = mapping[host]
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (ip, 0))]

    return _getaddrinfo


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "169.254.169.254",
        "0.0.0.0",
        "::1",
        "fe80::1",
        "::",
        "::ffff:127.0.0.1",
        "::ffff:169.254.169.254",
        "fd00:ec2::254",
    ],
)
def test_forbidden_addresses_always_blocked(ip):
    assert guard.is_forbidden_address(ip) is True
    assert guard.is_forbidden_address(ip, allow_private=False) is True


@pytest.mark.parametrize("ip", ["10.0.0.5", "192.168.1.10", "172.16.4.4"])
def test_private_addresses_allowed_only_when_permitted(ip):
    assert guard.is_forbidden_address(ip) is False
    assert guard.is_forbidden_address(ip, allow_private=False) is True


def test_public_address_allowed():
    assert guard.is_forbidden_address("93.184.216.34", allow_private=False) is False


def test_validate_outbound_url_rejects_metadata_hostname(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_resolver({"metadata.internal": "169.254.169.254"}))
    with pytest.raises(guard.UnsafeOutboundURLError):
        guard.validate_outbound_url("http://metadata.internal/latest/meta-data")


def test_validate_outbound_url_rejects_bad_scheme():
    with pytest.raises(guard.UnsafeOutboundURLError):
        guard.validate_outbound_url("file:///etc/passwd")


def _redirecting_transport(target: str):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.host == "public.example":
            return httpx.Response(302, headers={"Location": target})
        return httpx.Response(200, text="internal")

    return httpx.MockTransport(handler), calls


def test_redirect_to_loopback_is_blocked(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        _fake_resolver({"public.example": "93.184.216.34", "127.0.0.1": "127.0.0.1"}),
    )
    transport, calls = _redirecting_transport("http://127.0.0.1/admin")

    async def run():
        async with httpx.AsyncClient(
            transport=transport, follow_redirects=True, event_hooks=guard.redirect_guard_hooks()
        ) as client:
            await client.get("http://public.example/")

    with pytest.raises(guard.UnsafeOutboundURLError):
        asyncio.run(run())
    assert calls == ["http://public.example/"]


def test_redirect_to_lan_allowed_by_default(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        _fake_resolver({"public.example": "93.184.216.34", "192.168.1.20": "192.168.1.20"}),
    )
    transport, calls = _redirecting_transport("http://192.168.1.20/hook")

    async def run():
        async with httpx.AsyncClient(
            transport=transport, follow_redirects=True, event_hooks=guard.redirect_guard_hooks()
        ) as client:
            return await client.get("http://public.example/")

    response = asyncio.run(run())
    assert response.status_code == 200
    assert calls[-1] == "http://192.168.1.20/hook"


def test_webhook_monitor_client_uses_guard_hooks(monkeypatch):
    captured: dict = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            raise guard.UnsafeOutboundURLError("blocked")

    class Repo:
        async def record_attempt(self, **kwargs):
            captured["error"] = kwargs.get("error_message")

        async def mark_event_failed(self, *args, **kwargs):
            captured["failed"] = True

    monkeypatch.setattr(webhook_monitor.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(webhook_monitor, "webhook_repo", Repo())
    asyncio.run(
        webhook_monitor._attempt_event(
            {"id": 1, "attempt_count": 0, "max_attempts": 1, "target_url": "http://x.example/", "headers": {}}
        )
    )
    assert "request" in captured["event_hooks"]
    assert captured["failed"] is True


def test_service_status_lookup_client_uses_guard_hooks(monkeypatch):
    captured: dict = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, **kwargs):
            raise guard.UnsafeOutboundURLError("blocked")

    async def fake_get_service(service_id):
        return {
            "id": 1,
            "ai_lookup_enabled": True,
            "ai_lookup_url": "https://status.example/",
            "ai_lookup_prompt": "p",
        }

    monkeypatch.setattr(service_status_svc, "get_service", fake_get_service)
    monkeypatch.setattr(service_status_svc, "_validate_lookup_url", lambda url: url)
    monkeypatch.setattr(service_status_svc.httpx, "AsyncClient", FakeClient)
    result = asyncio.run(service_status_svc.run_ai_lookup_for_service(1))
    assert result["error"] == "URL fetch failed"
    assert "request" in captured["event_hooks"]
