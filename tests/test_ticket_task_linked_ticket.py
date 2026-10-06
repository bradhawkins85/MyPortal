"""Tests for creating a linked (child) ticket from a ticket task.

Covers the new "create linked ticket" action on the ticket-detail task list:
  * the service layer creates a standalone ticket that references the main
    ticket (``tickets.parent_ticket_id``) and records it on the task
    (``ticket_tasks.linked_ticket_id``);
  * the action is idempotent (an already-linked task returns its existing
    ticket instead of creating a new one);
  * the HTTP route returns 404 for a missing ticket/task, 201 on create and
    200 on the idempotent path.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from starlette.responses import Response

from app.api.routes import tickets as ticket_routes
from app.repositories import ticket_tasks as ticket_tasks_repo
from app.repositories import tickets as tickets_repo
from app.services import tickets as tickets_service


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


MAIN_TICKET = {
    "id": 1,
    "subject": "Main ticket",
    "description": "Main description",
    "company_id": 10,
    "requester_id": 20,
    "assigned_user_id": 30,
    "status": "open",
    "priority": "high",
    "category": "general",
    "module_slug": None,
    "ticket_number": 1,
}

NEW_TICKET = {
    "id": 2,
    "ticket_number": 2,
    "subject": "Main ticket \u2014 Fix disk",
    "company_id": 10,
    "requester_id": 20,
    "parent_ticket_id": 1,
}

CURRENT_USER = {
    "id": 7,
    "email": "tech@example.com",
    "first_name": "Tech",
    "last_name": "One",
}


# ---------------------------------------------------------------------------
# Service layer
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_service_creates_linked_ticket_and_links_task(monkeypatch):
    """First call creates a standalone ticket, links it to the main ticket,
    records it on the task and drops a note on the main ticket."""
    create_ticket = AsyncMock(return_value=NEW_TICKET)
    set_ticket_parent = AsyncMock()
    set_task_linked_ticket = AsyncMock()
    create_reply = AsyncMock(return_value={"id": 999})
    get_ticket = AsyncMock(return_value=None)  # task not linked yet

    monkeypatch.setattr(tickets_service, "create_ticket", create_ticket)
    monkeypatch.setattr(tickets_repo, "set_ticket_parent", set_ticket_parent)
    monkeypatch.setattr(ticket_tasks_repo, "set_task_linked_ticket", set_task_linked_ticket)
    monkeypatch.setattr(tickets_repo, "create_reply", create_reply)
    monkeypatch.setattr(tickets_repo, "get_ticket", get_ticket)

    ticket, was_created = await tickets_service.create_linked_ticket_for_task(
        main_ticket=MAIN_TICKET,
        task={"id": 55, "task_name": "Fix disk", "linked_ticket_id": None},
        actor=CURRENT_USER,
    )

    assert was_created is True
    assert ticket["id"] == 2

    # The ticket is standalone: same company/requester, but unassigned.
    kwargs = create_ticket.call_args.kwargs
    assert kwargs["requester_id"] == 20
    assert kwargs["company_id"] == 10
    assert kwargs["assigned_user_id"] is None
    assert "Main ticket" in kwargs["subject"] and "Fix disk" in kwargs["subject"]
    assert kwargs["trigger_automations"] is False

    # Parent link and task link are both recorded.
    set_ticket_parent.assert_awaited_once_with(2, 1)
    set_task_linked_ticket.assert_awaited_once_with(55, 2)

    # An internal note is dropped on the main ticket.
    reply_kwargs = create_reply.call_args.kwargs
    assert reply_kwargs["ticket_id"] == 1
    assert reply_kwargs["is_internal"] is True


@pytest.mark.anyio
async def test_service_is_idempotent_when_task_already_linked(monkeypatch):
    """When the task already has a linked ticket it is returned and nothing
    new is created."""
    get_ticket = AsyncMock(return_value=dict(NEW_TICKET))
    create_ticket = AsyncMock()  # must NOT be called
    set_ticket_parent = AsyncMock()
    set_task_linked_ticket = AsyncMock()
    create_reply = AsyncMock()

    monkeypatch.setattr(tickets_service, "create_ticket", create_ticket)
    monkeypatch.setattr(tickets_repo, "set_ticket_parent", set_ticket_parent)
    monkeypatch.setattr(ticket_tasks_repo, "set_task_linked_ticket", set_task_linked_ticket)
    monkeypatch.setattr(tickets_repo, "create_reply", create_reply)
    monkeypatch.setattr(tickets_repo, "get_ticket", get_ticket)

    ticket, was_created = await tickets_service.create_linked_ticket_for_task(
        main_ticket=MAIN_TICKET,
        task={"id": 55, "task_name": "Fix disk", "linked_ticket_id": 2},
        actor=CURRENT_USER,
    )

    assert was_created is False
    assert ticket["id"] == 2
    create_ticket.assert_not_awaited()
    set_ticket_parent.assert_not_awaited()
    set_task_linked_ticket.assert_not_awaited()
    create_reply.assert_not_awaited()


# ---------------------------------------------------------------------------
# Route layer
# ---------------------------------------------------------------------------

def _patch_service(monkeypatch, ticket, was_created):
    async def fake_create(main_ticket, task, *, actor=None):
        return ticket, was_created

    monkeypatch.setattr(
        ticket_routes.tickets_service,
        "create_linked_ticket_for_task",
        fake_create,
    )
    monkeypatch.setattr(ticket_routes.audit_service, "record", AsyncMock())


@pytest.mark.anyio
async def test_route_returns_404_when_ticket_missing(monkeypatch):
    monkeypatch.setattr(ticket_routes.tickets_repo, "get_ticket", AsyncMock(return_value=None))

    with pytest.raises(Exception) as exc_info:
        await ticket_routes.create_task_linked_ticket(
            ticket_id=1,
            task_id=55,
            request=None,
            response=Response(),
            current_user=CURRENT_USER,
        )

    assert exc_info.value.status_code == 404


@pytest.mark.anyio
async def test_route_returns_404_when_task_not_on_ticket(monkeypatch):
    monkeypatch.setattr(
        ticket_routes.tickets_repo, "get_ticket", AsyncMock(return_value=MAIN_TICKET)
    )
    monkeypatch.setattr(
        ticket_routes.ticket_tasks_repo,
        "get_task",
        AsyncMock(return_value={"id": 55, "ticket_id": 999}),
    )

    with pytest.raises(Exception) as exc_info:
        await ticket_routes.create_task_linked_ticket(
            ticket_id=1,
            task_id=55,
            request=None,
            response=Response(),
            current_user=CURRENT_USER,
        )

    assert exc_info.value.status_code == 404


@pytest.mark.anyio
async def test_route_creates_linked_ticket_with_201(monkeypatch):
    monkeypatch.setattr(
        ticket_routes.tickets_repo, "get_ticket", AsyncMock(return_value=MAIN_TICKET)
    )
    monkeypatch.setattr(
        ticket_routes.ticket_tasks_repo,
        "get_task",
        AsyncMock(return_value={"id": 55, "ticket_id": 1, "linked_ticket_id": None}),
    )
    _patch_service(monkeypatch, NEW_TICKET, True)

    response = Response()
    payload = await ticket_routes.create_task_linked_ticket(
        ticket_id=1,
        task_id=55,
        request=None,
        response=response,
        current_user=CURRENT_USER,
    )

    assert response.status_code == 201
    assert payload.created is True
    assert payload.ticket_id == 2
    assert payload.ticket_number == "2"
    assert payload.admin_url == "/admin/tickets/2"


@pytest.mark.anyio
async def test_route_returns_existing_linked_ticket_with_200(monkeypatch):
    monkeypatch.setattr(
        ticket_routes.tickets_repo, "get_ticket", AsyncMock(return_value=MAIN_TICKET)
    )
    monkeypatch.setattr(
        ticket_routes.ticket_tasks_repo,
        "get_task",
        AsyncMock(return_value={"id": 55, "ticket_id": 1, "linked_ticket_id": 2}),
    )
    _patch_service(monkeypatch, NEW_TICKET, False)

    response = Response()
    payload = await ticket_routes.create_task_linked_ticket(
        ticket_id=1,
        task_id=55,
        request=None,
        response=response,
        current_user=CURRENT_USER,
    )

    assert response.status_code == 200
    assert payload.created is False
    assert payload.ticket_id == 2
    assert payload.admin_url == "/admin/tickets/2"
