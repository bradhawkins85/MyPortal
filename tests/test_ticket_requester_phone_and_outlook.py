"""Tests for the ticket "Add Phone" and "Send To Outlook" requester actions."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import main
from app.services import user_m365_contacts


def _json_request(payload: dict | None = None) -> Request:
    body = json.dumps(payload or {}).encode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request({"type": "http", "method": "POST", "path": "/", "headers": []}, receive)


@pytest.fixture
def helpdesk(monkeypatch):
    monkeypatch.setattr(main, "_require_helpdesk_page", AsyncMock(return_value=({"id": 7}, None)))
    monkeypatch.setattr(main, "_feature_pack_available", lambda slug: True)
    record = AsyncMock()
    monkeypatch.setattr(main.audit_service, "record", record)
    return record


@pytest.mark.anyio
async def test_add_landline_updates_the_requester_staff_record(monkeypatch, helpdesk):
    monkeypatch.setattr(main.tickets_repo, "get_ticket", AsyncMock(return_value={"requester_staff_id": 42, "company_id": 3}))
    monkeypatch.setattr(main.staff_repo, "get_staff_by_id", AsyncMock(return_value={"id": 42, "landline_phone": None}))
    update = AsyncMock()
    monkeypatch.setattr(main.staff_repo, "update_landline_phone", update)

    response = await main.add_ticket_requester_phone(_json_request({"kind": "landline", "phone": "02 9876 5432"}), 9)

    assert response.status_code == 200
    assert json.loads(response.body)["display"] == "0298 765 432"
    update.assert_awaited_once_with(42, "02 9876 5432")
    assert helpdesk.await_args.kwargs["action"] == "ticket.requester.landline_phone_added"


@pytest.mark.anyio
async def test_add_company_phone_updates_the_linked_company(monkeypatch, helpdesk):
    monkeypatch.setattr(main.tickets_repo, "get_ticket", AsyncMock(return_value={"requester_staff_id": None, "company_id": 3}))
    monkeypatch.setattr(main.company_repo, "get_company_by_id", AsyncMock(return_value={"id": 3, "phone": ""}))
    update = AsyncMock()
    monkeypatch.setattr(main.company_repo, "update_company", update)

    response = await main.add_ticket_requester_phone(_json_request({"kind": "company", "phone": "1300 000 000"}), 9)

    assert response.status_code == 200
    update.assert_awaited_once_with(3, phone="1300 000 000")


@pytest.mark.anyio
async def test_add_phone_never_overwrites_an_existing_mobile(monkeypatch, helpdesk):
    monkeypatch.setattr(main.tickets_repo, "get_ticket", AsyncMock(return_value={"requester_staff_id": 42}))
    monkeypatch.setattr(main.staff_repo, "get_staff_by_id", AsyncMock(return_value={"id": 42, "mobile_phone": "0400 111 222"}))
    update = AsyncMock()
    monkeypatch.setattr(main.staff_repo, "update_mobile_phone", update)

    with pytest.raises(HTTPException) as exc_info:
        await main.add_ticket_requester_phone(_json_request({"kind": "mobile", "phone": "0400 999 999"}), 9)

    assert exc_info.value.status_code == 409
    update.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("payload", [{"kind": "fax", "phone": "0400 111 222"}, {"kind": "mobile", "phone": "abc"}])
async def test_add_phone_rejects_invalid_input(monkeypatch, helpdesk, payload):
    monkeypatch.setattr(main.tickets_repo, "get_ticket", AsyncMock(return_value={"requester_staff_id": 42}))

    with pytest.raises(HTTPException) as exc_info:
        await main.add_ticket_requester_phone(_json_request(payload), 9)

    assert exc_info.value.status_code == 422


@pytest.mark.anyio
async def test_send_to_outlook_uses_myportal_requester_details(monkeypatch, helpdesk):
    monkeypatch.setattr(main.tickets_repo, "get_ticket", AsyncMock(return_value={"requester_staff_id": 42, "company_id": 3}))
    monkeypatch.setattr(main.staff_repo, "get_staff_by_id", AsyncMock(return_value={
        "id": 42, "first_name": "Ada", "last_name": "Lovelace", "email": "ada@example.com",
        "mobile_phone": "0400 111 222", "landline_phone": "02 1111 2222", "job_title": "Engineer",
    }))
    monkeypatch.setattr(main.company_repo, "get_company_by_id", AsyncMock(return_value={"id": 3, "name": "Analytical", "phone": "1300 000 000"}))
    upsert = AsyncMock(return_value={"action": "created", "display_name": "Ada Lovelace"})
    monkeypatch.setattr(main.user_m365_contacts_service, "upsert_contact", upsert)

    response = await main.send_ticket_requester_to_outlook(_json_request(), 9)

    assert json.loads(response.body)["message"] == "Ada Lovelace added to your Outlook contacts."
    user_id, details = upsert.await_args.args
    assert user_id == 7
    assert details["company_phone"] == "1300 000 000"
    assert details["landline_phone"] == "02 1111 2222"


@pytest.mark.anyio
async def test_send_to_outlook_asks_read_only_connections_to_reconnect(monkeypatch, helpdesk):
    monkeypatch.setattr(user_m365_contacts.contacts_repo, "get_integration", AsyncMock(return_value={
        "oauth_scopes": "openid https://graph.microsoft.com/Contacts.Read",
    }))

    with pytest.raises(user_m365_contacts.ReconnectRequired):
        await user_m365_contacts.acquire_access_token(7, require_write=True)


def test_build_outlook_contact_omits_unknown_fields_and_dedupes_phones():
    contact = user_m365_contacts.build_outlook_contact({
        "first_name": "Ada", "last_name": "Lovelace", "email": "ada@example.com",
        "landline_phone": "02 1111 2222", "company_phone": "(02) 1111-2222",
    })

    assert contact == {
        "displayName": "Ada Lovelace", "givenName": "Ada", "surname": "Lovelace",
        "emailAddresses": [{"address": "ada@example.com", "name": "Ada Lovelace"}],
        "businessPhones": ["02 1111 2222"],
    }


@pytest.mark.anyio
async def test_upsert_contact_updates_existing_contact_matched_by_email(monkeypatch):
    monkeypatch.setattr(user_m365_contacts, "acquire_access_token", AsyncMock(return_value="token"))
    async_client = httpx.AsyncClient
    calls: list[tuple[str, str]] = []

    async def graph(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json={"value": [
                {"id": "other", "displayName": "Ada Lovelace", "emailAddresses": [{"address": "someone@example.com"}]},
                {"id": "abc", "displayName": "A. Lovelace", "emailAddresses": [{"address": "ADA@example.com"}]},
            ]})
        assert json.loads(request.content)["mobilePhone"] == "0400 111 222"
        return httpx.Response(200, json={"id": "abc"})

    monkeypatch.setattr(
        user_m365_contacts.httpx, "AsyncClient",
        lambda **kwargs: async_client(transport=httpx.MockTransport(graph), **kwargs),
    )

    result = await user_m365_contacts.upsert_contact(7, {
        "first_name": "Ada", "last_name": "Lovelace", "email": "ada@example.com", "mobile_phone": "0400 111 222",
    })

    assert result["action"] == "updated"
    assert calls[-1] == ("PATCH", "/v1.0/me/contacts/abc")


@pytest.mark.anyio
async def test_upsert_contact_creates_contact_when_missing(monkeypatch):
    monkeypatch.setattr(user_m365_contacts, "acquire_access_token", AsyncMock(return_value="token"))
    async_client = httpx.AsyncClient
    calls: list[str] = []

    async def graph(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json={"value": []})
        return httpx.Response(201, json={"id": "new"})

    monkeypatch.setattr(
        user_m365_contacts.httpx, "AsyncClient",
        lambda **kwargs: async_client(transport=httpx.MockTransport(graph), **kwargs),
    )

    result = await user_m365_contacts.upsert_contact(7, {"first_name": "Ada", "email": "ada@example.com"})

    assert result == {"action": "created", "display_name": "Ada"}
    assert calls == ["GET", "POST"]


def test_contacts_scope_requests_write_access():
    assert "Contacts.ReadWrite" in user_m365_contacts.CONTACTS_SCOPE


def test_ticket_template_hides_add_phone_when_mobile_and_company_are_shown():
    template = Path("app/templates/admin/ticket_detail.html").read_text()

    assert "not (ticket_requester_phone_display and ticket_company_phone_display)" in template
    assert "data-ticket-phone-add" in template
    assert "Send To Outlook" in template
    assert 'id="ticket-phone-modal"' in template


@pytest.mark.anyio
async def test_send_to_outlook_is_unavailable_when_component_is_off(monkeypatch, helpdesk):
    monkeypatch.setattr(main, "_feature_pack_available", lambda slug: False)
    get_ticket = AsyncMock()
    monkeypatch.setattr(main.tickets_repo, "get_ticket", get_ticket)

    with pytest.raises(HTTPException) as exc_info:
        await main.send_ticket_requester_to_outlook(_json_request(), 9)

    assert exc_info.value.status_code == 404
    get_ticket.assert_not_awaited()
