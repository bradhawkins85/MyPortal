"""Tests for restricting staff and set-up pages to TRUSTED_IPS."""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from app.security import trusted_networks
from app.security.trusted_networks import (
    TrustedNetworkMiddleware,
    TrustedNetworkPolicy,
    is_trusted_network,
    normalise_path,
    path_matches,
)

PATHS = [
    "/admin/tickets",
    "/admin/tickets/12345",
    "/admin/system-updates/1",
    "/admin/profile",
    "/xero/connect",
    "/m365",
    "/m365/mailboxes/users",
    "/m365/discover",
    "/m365/connect",
    "/m365/callback",
    "/companies/7/m365-credentials",
    "/api/tickets",
]


def _client(policy: TrustedNetworkPolicy, ip: str, *, renderer=None) -> TestClient:
    app = FastAPI()

    async def endpoint(request: Request) -> dict[str, bool]:
        return {"trusted": is_trusted_network(request)}

    for path in PATHS:
        app.add_api_route(path, endpoint, methods=["GET", "POST"])

    app.add_middleware(TrustedNetworkMiddleware, policy=policy, html_renderer=renderer)
    return TestClient(app, client=(ip, 50000))


@pytest.fixture(autouse=True)
def _no_trusted_proxies(monkeypatch):
    monkeypatch.setattr(
        trusted_networks,
        "get_client_ip",
        lambda request, default=None: request.client.host if request.client else default,
    )


def test_unconfigured_allows_every_address():
    client = _client(TrustedNetworkPolicy(trusted_ips=""), "198.51.100.9")
    for path in ("/admin/tickets", "/xero/connect", "/m365/discover"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.json() == {"trusted": True}


@pytest.mark.parametrize(
    "path",
    ["/admin/tickets", "/admin/tickets/12345", "/xero/connect", "/m365/discover", "/m365/connect", "/companies/7/m365-credentials"],
)
def test_untrusted_address_refused_on_restricted_paths(path):
    client = _client(TrustedNetworkPolicy(trusted_ips="203.0.113.0/24"), "198.51.100.9")
    response = client.get(path)
    assert response.status_code == 403
    assert response.json()["detail"] == trusted_networks.BLOCKED_DETAIL
    assert client.post(path).status_code == 403


@pytest.mark.parametrize(
    "path",
    ["/admin/profile", "/m365", "/m365/mailboxes/users", "/m365/callback", "/api/tickets"],
)
def test_end_user_paths_stay_open_to_untrusted_addresses(path):
    client = _client(TrustedNetworkPolicy(trusted_ips="203.0.113.0/24"), "198.51.100.9")
    response = client.get(path)
    assert response.status_code == 200
    assert response.json() == {"trusted": False}


@pytest.mark.parametrize("ip", ["203.0.113.44", "10.0.0.5", "2001:db8::1", "::ffff:10.0.0.5"])
def test_trusted_addresses_reach_restricted_paths(ip):
    client = _client(TrustedNetworkPolicy(trusted_ips="203.0.113.0/24, 10.0.0.5,2001:db8::/48"), ip)
    response = client.get("/admin/tickets/12345")
    assert response.status_code == 200
    assert response.json() == {"trusted": True}


def test_path_tricks_do_not_bypass_restriction():
    client = _client(TrustedNetworkPolicy(trusted_ips="203.0.113.0/24"), "198.51.100.9")
    assert client.get("//admin//tickets").status_code in {403, 404}
    policy = TrustedNetworkPolicy(trusted_ips="203.0.113.0/24")
    assert policy.is_restricted("//admin//tickets/")
    assert policy.is_restricted("/admin/./tickets")
    assert policy.is_restricted("/m365/profile/../discover")
    assert not policy.is_restricted("/admin/ticketsx")


def test_extra_and_exempt_paths():
    policy = TrustedNetworkPolicy(
        trusted_ips="203.0.113.0/24",
        extra_paths="/api/tickets, m365/mailboxes",
        exempt_paths="/admin/tickets/12345",
    )
    assert policy.is_restricted("/api/tickets")
    assert policy.is_restricted("/m365/mailboxes/users")
    assert not policy.is_restricted("/admin/tickets/12345")
    assert policy.is_restricted("/admin/tickets/99")


def test_invalid_only_entries_fail_closed():
    policy = TrustedNetworkPolicy(trusted_ips="not-an-ip")
    assert policy.configured
    assert policy.invalid_entries == ["not-an-ip"]
    client = _client(policy, "203.0.113.4")
    assert client.get("/admin/tickets").status_code == 403
    assert client.get("/admin/profile").status_code == 200


def test_browser_requests_get_rendered_page():
    async def renderer(request: Request):
        return HTMLResponse("<h1>Not available from this network</h1>", status_code=403)

    client = _client(TrustedNetworkPolicy(trusted_ips="203.0.113.0/24"), "198.51.100.9", renderer=renderer)
    response = client.get("/xero/connect", headers={"accept": "text/html"})
    assert response.status_code == 403
    assert "Not available from this network" in response.text
    # Form posts and API calls still get JSON.
    assert client.post("/xero/connect", headers={"accept": "text/html"}).headers["content-type"].startswith("application/json")


def test_rule_matching_helpers():
    assert normalise_path("//admin///tickets/") == "/admin/tickets"
    assert path_matches("/companies/9/m365-credentials/test", "/companies/*/m365-credentials")
    assert not path_matches("/companies/m365-credentials", "/companies/*/m365-credentials")
    assert path_matches("/xero", "/xero")
    assert not path_matches("/xerox", "/xero")


def test_default_list_keeps_end_user_and_sign_in_paths_open():
    policy = TrustedNetworkPolicy(trusted_ips="203.0.113.0/24")
    for path in ("/m365/callback", "/admin/profile", "/admin/profile/ai-features", "/login", "/admin/marketing", "/api/integration-modules/xero/webhook"):
        assert not policy.is_restricted(path), path


def test_client_ip_uses_trusted_proxy_header(monkeypatch):
    from app.security.client_ip import get_client_ip
    from ipaddress import ip_network

    monkeypatch.setattr(
        trusted_networks,
        "get_client_ip",
        lambda request, default=None: get_client_ip(request, default=default, trusted_networks=[ip_network("127.0.0.1/32")]),
    )
    client = _client(TrustedNetworkPolicy(trusted_ips="203.0.113.0/24"), "127.0.0.1")
    assert client.get("/admin/tickets", headers={"x-forwarded-for": "203.0.113.8"}).status_code == 200
    assert client.get("/admin/tickets", headers={"x-forwarded-for": "198.51.100.9"}).status_code == 403
    # A spoofed header from an untrusted peer is ignored.
    spoofed = _client(TrustedNetworkPolicy(trusted_ips="203.0.113.0/24"), "198.51.100.9")
    assert spoofed.get("/admin/tickets", headers={"x-forwarded-for": "203.0.113.8"}).status_code == 403


@pytest.mark.parametrize(
    "raw",
    [
        '"203.0.113.10,192.168.1.0/24"',
        "'203.0.113.10, 192.168.1.0/24'",
        "203.0.113.10 192.168.1.0/24",
        "203.0.113.10;192.168.1.0/24",
        "203.0.113.10,192.168.1.0/24  # office WAN and LAN",
        "203.0.113.10,\n192.168.1.0/24",
    ],
)
def test_env_file_formatting_does_not_invalidate_entries(raw):
    policy = TrustedNetworkPolicy(trusted_ips=raw)
    assert policy.invalid_entries == []
    assert [str(network) for network in policy.networks] == ["203.0.113.10/32", "192.168.1.0/24"]
    assert policy.is_trusted_ip("203.0.113.10")
    assert policy.is_trusted_ip("192.168.1.77")


def test_refusal_reports_the_address_that_was_checked():
    async def renderer(request: Request):
        return HTMLResponse(f"from {trusted_networks.client_ip_seen(request)}", status_code=403)

    client = _client(TrustedNetworkPolicy(trusted_ips="203.0.113.0/24"), "198.51.100.9", renderer=renderer)
    assert client.get("/admin/tickets").json()["client_ip"] == "198.51.100.9"
    assert "from 198.51.100.9" in client.get("/admin/tickets", headers={"accept": "text/html"}).text


def test_refusal_log_names_the_client_ip():
    from loguru import logger

    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(str(message)), format="{message}")
    try:
        client = _client(TrustedNetworkPolicy(trusted_ips="203.0.113.0/24"), "198.51.100.9")
        client.get("/admin/system-updates/1")
    finally:
        logger.remove(sink_id)
    refusal = [m for m in messages if "Trusted network check refused" in m]
    assert refusal and "client_ip=198.51.100.9" in refusal[0]
