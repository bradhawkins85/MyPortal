from __future__ import annotations

import pytest
from fastapi import HTTPException, status
from fastapi.responses import HTMLResponse
from starlette.datastructures import FormData

from app.api.routes import tickets as ticket_api
from app.features.tickets import admin_routes
from app.schemas.tickets import TicketReplyCreate
from app.services.tickets import reply_assignment_error


@pytest.mark.parametrize(
    ("ticket", "is_internal", "expected"),
    [
        (
            {"company_id": None, "requester_id": None},
            False,
            "Set a Company and Requester before sending a public reply.",
        ),
        (
            {"company_id": 4, "requester_id": None},
            False,
            "Set a Requester before sending a public reply.",
        ),
        (
            {"company_id": None, "requester_id": 7},
            False,
            "Set a Company before sending a public reply.",
        ),
        (
            {"company_id": None, "requester_id": None},
            True,
            "Set a Company before adding an internal note.",
        ),
        ({"company_id": 4, "requester_id": None}, True, None),
        ({"company_id": 4, "requester_id": 7}, False, None),
    ],
)
def test_reply_assignment_requirements(ticket, is_internal, expected):
    assert reply_assignment_error(ticket, is_internal=is_internal) == expected


@pytest.mark.anyio
async def test_api_rejects_public_reply_without_requester(monkeypatch):
    class Request:
        state = type("State", (), {"session": None})()

    monkeypatch.setattr(
        ticket_api.tickets_repo,
        "get_merged_target_ticket_id",
        lambda _ticket_id: _async(None),
    )
    monkeypatch.setattr(
        ticket_api.tickets_repo,
        "get_ticket",
        lambda _ticket_id: _async({"id": 12, "company_id": 3, "requester_id": None}),
    )

    with pytest.raises(HTTPException) as exc_info:
        await ticket_api.add_reply(
            12,
            TicketReplyCreate(body="Hello", is_internal=False),
            Request(),
            actor={"api_key": {"id": 1}, "user": None},
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Set a Requester before sending a public reply."


@pytest.mark.anyio
async def test_admin_rejects_internal_note_without_company(monkeypatch):
    class Request:
        async def form(self):
            return FormData([("body", "Internal"), ("isInternal", "true")])

    rendered: dict[str, object] = {}

    async def render_detail(*_args, **kwargs):
        rendered.update(kwargs)
        return HTMLResponse("invalid", status_code=kwargs["status_code"])

    monkeypatch.setattr(
        admin_routes._main(),
        "_require_helpdesk_page",
        lambda _request: _async(({"id": 9, "is_super_admin": True}, None)),
    )
    monkeypatch.setattr(admin_routes._main(), "_render_ticket_detail", render_detail)
    monkeypatch.setattr(
        admin_routes.tickets_repo,
        "get_ticket",
        lambda _ticket_id: _async({"id": 14, "company_id": None, "requester_id": None}),
    )

    response = await admin_routes.admin_create_ticket_reply(14, Request())

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert rendered["error_message"] == "Set a Company before adding an internal note."


async def _async(value):
    return value
