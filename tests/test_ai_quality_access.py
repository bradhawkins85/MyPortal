"""Who may rate an AI output."""
from __future__ import annotations

import pytest

from app.services import ai_quality_access as access

TICKET = {"id": 7, "requester_id": 20, "company_id": 2}


@pytest.fixture(autouse=True)
def tickets(monkeypatch):
    async def get_ticket(ticket_id):
        return TICKET if ticket_id == 7 else None

    async def is_ticket_watcher(ticket_id, user_id):
        return ticket_id == 7 and user_id == 30

    monkeypatch.setattr(access.tickets_repo, "get_ticket", get_ticket)
    monkeypatch.setattr(access.tickets_repo, "is_ticket_watcher", is_ticket_watcher)


def _ticket_output(subject_id="7", owner=99):
    return {"id": 1, "user_id": owner, "subject_type": "ticket", "subject_id": subject_id}


@pytest.mark.anyio
async def test_owner_can_rate_their_own_output():
    assert await access.can_rate({"id": 99}, {"id": 1, "user_id": 99, "subject_type": None}, is_technician=False)


@pytest.mark.anyio
async def test_technician_can_rate_ticket_output_they_did_not_trigger():
    assert await access.can_rate({"id": 5}, _ticket_output(), is_technician=True)


@pytest.mark.anyio
@pytest.mark.parametrize("user_id", [20, 30])
async def test_requester_and_watcher_can_rate(user_id):
    assert await access.can_rate({"id": user_id}, _ticket_output(), is_technician=False)


@pytest.mark.anyio
async def test_unrelated_customer_cannot_rate_ticket_output():
    assert not await access.can_rate({"id": 40}, _ticket_output(), is_technician=False)


@pytest.mark.anyio
async def test_customer_cannot_rate_missing_ticket():
    assert not await access.can_rate({"id": 20}, _ticket_output(subject_id="8"), is_technician=False)


@pytest.mark.anyio
async def test_non_ticket_subject_needs_technician():
    output = {"id": 1, "user_id": 99, "subject_type": "rmm_finding", "subject_id": "3"}
    assert not await access.can_rate({"id": 40}, output, is_technician=False)
    assert await access.can_rate({"id": 40}, output, is_technician=True)


@pytest.mark.anyio
async def test_super_admin_is_technician():
    assert await access.is_technician({"id": 1, "is_super_admin": True})


@pytest.mark.anyio
async def test_is_technician_uses_helpdesk_permission(monkeypatch):
    async def has_permission(user_id, key):
        return user_id == 5 and key == access.HELPDESK_PERMISSION_KEY

    monkeypatch.setattr(access.membership_repo, "user_has_permission", has_permission)
    assert await access.is_technician({"id": 5})
    assert not await access.is_technician({"id": 6})
    assert not await access.is_technician({"id": "not-a-number"})
