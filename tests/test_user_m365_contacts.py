from unittest.mock import AsyncMock

import httpx
import pytest

from app.main import app
from app.services import user_m365_contacts


def test_match_contact_phones_requires_requester_name_match():
    contacts = [
        {"displayName": "Ada Lovelace", "mobilePhone": "0400 111 222", "businessPhones": ["02 1234 5678"]},
        {"displayName": "Grace Hopper", "mobilePhone": "0400 999 999"},
    ]
    assert user_m365_contacts.match_contact_phones("Ada Lovelace", contacts) == [
        {"name": "Ada Lovelace", "phone": "02 1234 5678"},
        {"name": "Ada Lovelace", "phone": "0400 111 222"},
    ]


def test_match_contact_phones_deduplicates_formatted_numbers():
    contacts = [{
        "displayName": "Ada Lovelace", "mobilePhone": "+61 400 111 222",
        "businessPhones": ["+61 (400) 111-222"], "homePhones": [],
    }]
    assert user_m365_contacts.match_contact_phones("Ada Lovelace", contacts) == [
        {"name": "Ada Lovelace", "phone": "+61 400 111 222"},
    ]


def test_contact_phone_lookup_accepts_post_requests():
    routes = [
        route for route in app.routes
        if getattr(route, "path", None) == "/api/profile/m365-contacts/phones"
    ]

    assert len(routes) == 1
    assert {"GET", "POST"}.issubset(routes[0].methods)


@pytest.mark.anyio
async def test_lookup_phones_reads_the_authenticated_technicians_contacts(monkeypatch):
    acquire_access_token = AsyncMock(return_value="technician-access-token")
    monkeypatch.setattr(user_m365_contacts, "acquire_access_token", acquire_access_token)
    async_client = httpx.AsyncClient

    async def graph_contacts(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer technician-access-token"
        return httpx.Response(200, json={"value": [{
            "displayName": "Ada Lovelace",
            "mobilePhone": "0400 111 222",
        }]})

    monkeypatch.setattr(
        user_m365_contacts.httpx,
        "AsyncClient",
        lambda **kwargs: async_client(transport=httpx.MockTransport(graph_contacts), **kwargs),
    )

    phones = await user_m365_contacts.lookup_phones(42, "Ada Lovelace")

    acquire_access_token.assert_awaited_once_with(42)
    assert phones == [{"name": "Ada Lovelace", "phone": "0400 111 222"}]


def test_tenant_id_uses_id_token_when_graph_access_token_is_opaque(monkeypatch):
    """The OAuth callback must not require Microsoft Graph access tokens to be JWTs."""
    seen: list[str] = []

    def extract(token: str) -> str:
        seen.append(token)
        if token == "signed-id-token":
            return "tenant-123"
        raise ValueError("opaque token")

    monkeypatch.setattr(user_m365_contacts.m365_service, "extract_tenant_id_from_token", extract)

    tenant_id = user_m365_contacts.tenant_id_from_token_response(
        {"access_token": "opaque-graph-token", "id_token": "signed-id-token"}
    )

    assert tenant_id == "tenant-123"
    assert seen == ["signed-id-token"]


def test_tenant_id_falls_back_to_access_token_for_older_responses(monkeypatch):
    monkeypatch.setattr(
        user_m365_contacts.m365_service,
        "extract_tenant_id_from_token",
        lambda token: "tenant-legacy" if token == "jwt-access-token" else "unexpected",
    )

    assert user_m365_contacts.tenant_id_from_token_response(
        {"access_token": "jwt-access-token"}
    ) == "tenant-legacy"
