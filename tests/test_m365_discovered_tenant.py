"""Guided setup shows the discovered tenant by name before it is confirmed."""
from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlparse

import pytest
from jinja2 import Environment, FileSystemLoader
from starlette.requests import Request

import app.main as main_module
from app.main import app
from app.security.oauth_state import discovered_tenant_serializer
from app.services import m365 as m365_service

TENANT_ID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def async_client():
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


def _request(query: str) -> Request:
    return Request({"type": "http", "method": "GET", "query_string": query.encode(), "headers": []})


def _render_partial(discovered_tenant: dict[str, str]) -> str:
    environment = Environment(loader=FileSystemLoader("app/templates"), autoescape=True)
    return environment.get_template("m365/_discovered_tenant.html").render(
        discovered_tenant=discovered_tenant
    )


# ---------------------------------------------------------------------------
# Tenant name lookup
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_describe_signed_in_tenant_returns_name_and_default_domain(monkeypatch):
    graph_get = AsyncMock(return_value={"value": [{
        "id": TENANT_ID.upper(),
        "displayName": "  Contoso   Pty Ltd ",
        "verifiedDomains": [
            {"name": "contoso.onmicrosoft.com", "isInitial": True, "isDefault": False},
            {"name": "contoso.com.au", "isInitial": False, "isDefault": True},
        ],
    }]})
    monkeypatch.setattr(m365_service, "_graph_get", graph_get)

    result = await m365_service.describe_signed_in_tenant("token", TENANT_ID)

    assert result == {"name": "Contoso Pty Ltd", "domain": "contoso.com.au"}
    assert "/v1.0/organization?" in graph_get.await_args.args[1]


@pytest.mark.parametrize(
    "graph_get",
    [
        AsyncMock(return_value={"value": [{"id": "other-tenant", "displayName": "Fabrikam"}]}),
        AsyncMock(side_effect=m365_service.M365Error("Forbidden", http_status=403)),
        AsyncMock(side_effect=RuntimeError("network")),
    ],
)
@pytest.mark.anyio("asyncio")
async def test_describe_signed_in_tenant_is_best_effort(monkeypatch, graph_get):
    monkeypatch.setattr(m365_service, "_graph_get", graph_get)

    assert await m365_service.describe_signed_in_tenant("token", TENANT_ID) is None


# ---------------------------------------------------------------------------
# Signed hand-off to the configuration page
# ---------------------------------------------------------------------------


def _token(**overrides: Any) -> str:
    payload = {
        "company_id": 7, "tenant_id": TENANT_ID, "name": "Contoso Pty Ltd",
        "domain": "contoso.com.au", "account": "admin@contoso.com.au",
    }
    payload.update(overrides)
    return discovered_tenant_serializer.dumps(payload)


def test_discovered_tenant_token_is_read_for_its_company():
    result = main_module._m365_discovered_tenant(_request(f"m365_discovered={_token()}"), 7)

    assert result == {
        "tenant_id": TENANT_ID, "name": "Contoso Pty Ltd",
        "domain": "contoso.com.au", "account": "admin@contoso.com.au",
    }


def test_discovered_tenant_token_rejects_tampering_and_other_companies():
    token = _token()
    tampered = token[:-2] + ("AA" if not token.endswith("AA") else "BB")

    assert main_module._m365_discovered_tenant(_request(f"m365_discovered={tampered}"), 7) is None
    assert main_module._m365_discovered_tenant(_request(f"m365_discovered={token}"), 8) is None
    assert main_module._m365_discovered_tenant(_request("m365_discovered=not-a-token"), 7) is None


def test_discovered_tenant_token_expires(monkeypatch):
    with monkeypatch.context() as patched:
        patched.setattr(time, "time", lambda: 1_000_000.0)
        token = _token()

    assert main_module._m365_discovered_tenant(_request(f"m365_discovered={token}"), 7) is None


def test_legacy_tenant_id_parameter_still_shows_the_tenant_without_a_name():
    result = main_module._m365_discovered_tenant(
        _request(f"m365_discovered_tenant={TENANT_ID}"), 7
    )

    assert result == {"tenant_id": TENANT_ID, "name": "", "domain": "", "account": ""}


# ---------------------------------------------------------------------------
# Confirmation block
# ---------------------------------------------------------------------------


def test_confirmation_shows_the_tenant_name_instead_of_its_id():
    html = _render_partial({
        "tenant_id": TENANT_ID, "name": "Contoso Pty Ltd",
        "domain": "contoso.com.au", "account": "admin@contoso.com.au",
    })

    assert "Contoso Pty Ltd" in html
    assert "contoso.com.au" in html
    assert "Signed in as admin@contoso.com.au" in html
    assert f'href="/m365/provision?tenant_id={TENANT_ID}"' in html
    assert html.count(TENANT_ID) == 1  # only in the confirm link, not displayed


def test_confirmation_falls_back_to_the_tenant_id_when_no_name():
    html = _render_partial({"tenant_id": TENANT_ID, "name": "", "domain": "", "account": ""})

    assert "Tenant name not available" in html
    assert f"<code>{TENANT_ID}</code>" in html
    assert "Signed in as" not in html


def test_confirmation_escapes_tenant_details():
    html = _render_partial({
        "tenant_id": TENANT_ID, "name": "<script>alert(1)</script>",
        "domain": "", "account": "",
    })

    assert "<script>" not in html
    assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_discover_route_requests_user_read_for_the_tenant_name(async_client, monkeypatch):
    states: list[dict] = []

    async def fake_license_context(request):
        return {"id": 1, "is_super_admin": True}, None, {"id": 7}, 7, None

    async def fake_new_state(request, **context):
        states.append(context)
        return "discover-state"

    monkeypatch.setattr("app.main._load_license_context", fake_license_context)
    monkeypatch.setattr("app.main._new_m365_oauth_state", fake_new_state)
    monkeypatch.setattr(
        "app.main.m365_service.get_effective_pkce_client_id_for_company",
        AsyncMock(return_value="pkce-client"),
    )

    response = await async_client.get("/m365/discover", follow_redirects=False)

    assert response.status_code == 303
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["scope"] == [m365_service.DISCOVER_TENANT_SCOPE]
    assert "https://graph.microsoft.com/User.Read" in query["scope"][0]
    assert states[0]["scope"] == m365_service.DISCOVER_TENANT_SCOPE


class _TokenClient:
    calls: list[dict] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def post(self, url, data=None, **kwargs):
        _TokenClient.calls.append({"url": url, "data": data})
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"id_token": "id-token", "access_token": "graph-token"}
        return response


def _patch_discover_callback(monkeypatch, state_data: dict, profile: dict | None) -> AsyncMock:
    async def fake_consume_state(request, state):
        return state_data

    async def fake_authenticated_user(request):
        return {"id": 1, "is_super_admin": True}, None

    _TokenClient.calls = []
    describe = AsyncMock(return_value=profile)
    monkeypatch.setattr("app.main._consume_m365_oauth_state", fake_consume_state)
    monkeypatch.setattr("app.main._require_authenticated_user", fake_authenticated_user)
    monkeypatch.setattr("app.main.httpx.AsyncClient", _TokenClient)
    monkeypatch.setattr(
        "app.main.m365_service.validate_microsoft_id_token",
        AsyncMock(return_value={"tid": TENANT_ID, "preferred_username": "admin@contoso.com.au"}),
    )
    monkeypatch.setattr("app.main.m365_service.describe_signed_in_tenant", describe)
    return describe


def _discover_state(**extra: Any) -> dict[str, Any]:
    return {
        "company_id": 7, "flow": "discover", "code_verifier": "verifier",
        "client_id": "pkce-client", "redirect_uri": "https://portal.example/m365/callback",
        **extra,
    }


@pytest.mark.anyio("asyncio")
async def test_discover_callback_hands_the_tenant_name_to_the_confirmation(async_client, monkeypatch):
    describe = _patch_discover_callback(
        monkeypatch,
        _discover_state(scope=m365_service.DISCOVER_TENANT_SCOPE),
        {"name": "Contoso Pty Ltd", "domain": "contoso.com.au"},
    )

    response = await async_client.get("/m365/callback?code=c&state=s", follow_redirects=False)

    assert response.status_code == 303
    location = urlparse(response.headers["location"])
    assert location.path == "/m365"
    token = parse_qs(location.query)["m365_discovered"][0]
    assert discovered_tenant_serializer.loads(token) == {
        "company_id": 7, "tenant_id": TENANT_ID, "name": "Contoso Pty Ltd",
        "domain": "contoso.com.au", "account": "admin@contoso.com.au",
    }
    assert _TokenClient.calls[0]["data"]["scope"] == m365_service.DISCOVER_TENANT_SCOPE
    describe.assert_awaited_once_with("graph-token", TENANT_ID)


@pytest.mark.anyio("asyncio")
async def test_discover_callback_still_offers_the_tenant_when_the_name_is_unavailable(
    async_client, monkeypatch,
):
    _patch_discover_callback(
        monkeypatch, _discover_state(scope=m365_service.DISCOVER_TENANT_SCOPE), None,
    )

    response = await async_client.get("/m365/callback?code=c&state=s", follow_redirects=False)

    token = parse_qs(urlparse(response.headers["location"]).query)["m365_discovered"][0]
    data = discovered_tenant_serializer.loads(token)
    assert data["tenant_id"] == TENANT_ID
    assert data["name"] == ""


@pytest.mark.anyio("asyncio")
async def test_company_edit_discover_keeps_the_minimal_sign_in(async_client, monkeypatch):
    describe = _patch_discover_callback(
        monkeypatch, _discover_state(return_to="company_edit"), None,
    )

    response = await async_client.get("/m365/callback?code=c&state=s", follow_redirects=False)

    assert response.headers["location"] == (
        f"/admin/companies/7/edit?m365_discovered_tenant={TENANT_ID}"
    )
    assert _TokenClient.calls[0]["data"]["scope"] == m365_service.DISCOVER_SCOPE
    describe.assert_not_awaited()
