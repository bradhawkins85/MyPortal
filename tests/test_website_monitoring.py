from __future__ import annotations

import socket
import asyncio
from unittest.mock import AsyncMock

import pytest
import httpx

from app.services import website_monitoring as monitoring


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.8", "169.254.169.254", "::1"])
def test_rejects_non_public_resolution(monkeypatch, address):
    async def fake_getaddrinfo(*args, **kwargs):
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (address, 0))]

    async def run():
        monkeypatch.setattr(monitoring.asyncio.get_running_loop(), "getaddrinfo", fake_getaddrinfo)
        with pytest.raises(ValueError, match="non-public"):
            await monitoring.validate_public_url("https://example.test")
    asyncio.run(run())


def test_rejects_credentials_and_nonstandard_ports():
    async def run():
        with pytest.raises(ValueError, match="credentials"):
            await monitoring.validate_public_url("https://user:secret@example.com")
        with pytest.raises(ValueError, match="standard web ports"):
            await monitoring.validate_public_url("https://example.com:8443")
    asyncio.run(run())


def test_rejects_mixed_public_and_private_resolution(monkeypatch):
    async def fake_getaddrinfo(*args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 0)),
        ]

    async def run():
        monkeypatch.setattr(monitoring.asyncio.get_running_loop(), "getaddrinfo", fake_getaddrinfo)
        with pytest.raises(ValueError, match="non-public"):
            await monitoring.validate_public_url("https://example.test")
    asyncio.run(run())


def test_availability_pins_validated_addresses_across_dns_change_and_retries(monkeypatch):
    attempted = []

    class Stream:
        def __init__(self, url, kwargs):
            self.url = str(url)
            self.kwargs = kwargs

        async def __aenter__(self):
            attempted.append((self.url, self.kwargs))
            if "[2606:2800:220:1:248:1893:25c8:1946]" in self.url:
                raise httpx.ConnectError("IPv6 unavailable")
            return self

        async def __aexit__(self, *args):
            return None

        status_code = 204

        async def aiter_bytes(self):
            yield b""

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def stream(self, method, url, **kwargs):
            assert method == "GET"
            return Stream(url, kwargs)

    resolutions = 0

    # A rebinding lookup returns loopback after validation. The fetch must not
    # perform that second lookup.
    async def rebound(*args, **kwargs):
        nonlocal resolutions
        resolutions += 1
        if resolutions > 1:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]
        return [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "",
             ("2606:2800:220:1:248:1893:25c8:1946", 0, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
        ]

    monkeypatch.setattr(monitoring.httpx, "AsyncClient", Client)

    async def run():
        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", rebound)
        host, port, addresses = await monitoring.validate_public_url(
            "https://example.test/path?q=1"
        )
        return await monitoring.fetch_availability(
            "https://example.test/path?q=1", host, port, addresses
        )

    status = asyncio.run(run())

    assert status == 204
    assert resolutions == 1
    assert [item[0] for item in attempted] == [
        "https://[2606:2800:220:1:248:1893:25c8:1946]:443/path?q=1",
        "https://93.184.216.34:443/path?q=1",
    ]
    assert all(item[1]["headers"]["Host"] == "example.test" for item in attempted)
    assert all(item[1]["extensions"]["sni_hostname"] == "example.test" for item in attempted)


def test_availability_revalidates_each_connection_address():
    with pytest.raises(ValueError, match="connection address is not public"):
        asyncio.run(monitoring.fetch_availability(
            "http://example.test", "example.test", 80, ["127.0.0.1"]
        ))


def test_failed_check_records_failure_without_success(monkeypatch):
    calls = []

    async def reject(url):
        raise ValueError("blocked")

    async def failure(*args):
        calls.append(("failure", args))

    async def success(*args):
        calls.append(("success", args))

    monkeypatch.setattr(monitoring, "validate_public_url", reject)
    monkeypatch.setattr(monitoring.repo, "record_failure", failure)
    monkeypatch.setattr(monitoring.repo, "record_success", success)
    result = asyncio.run(monitoring.check_website({"id": 7, "url": "http://localhost"}))
    assert result["ok"] is False and result["retryable"] is True
    assert [item[0] for item in calls] == ["failure"]


def test_success_feeds_certificate_and_domain_expiries(monkeypatch):
    certificate = {"expires_at": "cert-date", "source": "tls-handshake"}
    domain = {"expires_at": "domain-date", "source": "rdap"}
    monkeypatch.setattr(monitoring, "validate_public_url", AsyncMock(return_value=("example.com", 443, ["93.184.216.34"])))
    monkeypatch.setattr(monitoring, "inspect_certificate", AsyncMock(return_value=certificate))
    monkeypatch.setattr(monitoring, "lookup_domain_expiry", AsyncMock(return_value=domain))
    monkeypatch.setattr(monitoring.repo, "record_success", AsyncMock())
    monkeypatch.setattr(monitoring.repo, "record_domain_expiry", AsyncMock())

    result = asyncio.run(monitoring.check_website({
        "id": 7, "url": "https://example.com", "monitor_tls": True,
        "monitor_availability": False, "collect_domain_expiry": True,
    }))

    assert result["ok"] is True
    assert result["certificate"] == certificate
    assert result["domain"] == domain
    monitoring.repo.record_domain_expiry.assert_awaited_once()

@pytest.mark.parametrize(("host", "expected"), [
    ("shop.example.com.au", "example.com.au"),
    ("www.example.co.uk", "example.co.uk"),
    ("example.com", "example.com"),
])
def test_registrable_domain_uses_public_suffix_list(host, expected):
    assert monitoring.registrable_domain(host) == expected


def test_dns_rrsets_ignore_answer_order_and_ttl(monkeypatch):
    from app.repositories import websites
    first = {"source": "recursive-dns", "coverage": "public lookup", "records": [
        {"name": "EXAMPLE.COM", "type": "a", "values": ["192.0.2.2", "192.0.2.1"], "ttl": 240}
    ]}
    second = {"source": "recursive-dns", "coverage": "public lookup", "records": [
        {"name": "example.com.", "type": "A", "values": ["192.0.2.1", "192.0.2.2"], "ttl": 120}
    ]}
    assert websites._rrsets(first) == websites._rrsets(second)


def test_dns_rrsets_detect_value_change():
    from app.repositories import websites
    before = {"records": [{"name": "example.com", "type": "A", "values": ["192.0.2.1"]}]}
    after = {"records": [{"name": "example.com", "type": "A", "values": ["192.0.2.2"]}]}
    assert websites._rrsets(before) != websites._rrsets(after)
