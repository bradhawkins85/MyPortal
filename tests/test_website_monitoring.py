from __future__ import annotations

import socket
import asyncio
from unittest.mock import AsyncMock

import pytest

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
