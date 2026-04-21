"""Tests for :mod:`app.security.client_ip`."""

from __future__ import annotations

from ipaddress import ip_network

from app.security.client_ip import get_client_ip


class _StubClient:
    def __init__(self, host: str) -> None:
        self.host = host


class _StubRequest:
    """Minimal duck-typed replacement for ``starlette.requests.Request``."""

    def __init__(self, peer: str | None, headers: dict[str, str] | None = None) -> None:
        self.client = _StubClient(peer) if peer else None
        self.headers = headers or {}


def test_xff_ignored_when_no_trusted_proxies():
    req = _StubRequest("198.51.100.5", {"x-forwarded-for": "1.2.3.4"})
    assert get_client_ip(req, trusted_networks=[]) == "198.51.100.5"  # type: ignore[arg-type]


def test_xff_honoured_when_peer_is_trusted_proxy():
    trusted = [ip_network("127.0.0.1")]
    req = _StubRequest("127.0.0.1", {"x-forwarded-for": "203.0.113.7, 10.0.0.1"})
    assert get_client_ip(req, trusted_networks=trusted) == "203.0.113.7"  # type: ignore[arg-type]


def test_xff_ignored_when_peer_not_in_trusted_cidr():
    trusted = [ip_network("10.0.0.0/8")]
    req = _StubRequest("198.51.100.5", {"x-forwarded-for": "1.2.3.4"})
    assert get_client_ip(req, trusted_networks=trusted) == "198.51.100.5"  # type: ignore[arg-type]


def test_x_real_ip_used_when_xff_missing_and_peer_trusted():
    trusted = [ip_network("10.0.0.0/8")]
    req = _StubRequest("10.1.2.3", {"x-real-ip": "198.51.100.9"})
    assert get_client_ip(req, trusted_networks=trusted) == "198.51.100.9"  # type: ignore[arg-type]


def test_returns_default_when_no_client():
    req = _StubRequest(None, {})
    assert get_client_ip(req, trusted_networks=[], default="fallback") == "fallback"  # type: ignore[arg-type]


def test_invalid_peer_ip_does_not_crash():
    """Non-IP peer strings (e.g. ``testclient``) must not crash and must
    not honour proxy headers for untrusted peers.
    """

    req = _StubRequest("testclient", {"x-forwarded-for": "1.2.3.4"})
    assert get_client_ip(req, trusted_networks=[ip_network("127.0.0.1")]) == "testclient"  # type: ignore[arg-type]
