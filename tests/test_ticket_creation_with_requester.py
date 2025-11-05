"""Test ticket creation with requester field."""
import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_create_ticket_with_staff_requester(monkeypatch):
    """Test that a ticket can be created with a staff member as requester."""
    from app.repositories import tickets as tickets_repo
    from app.services import tickets as tickets_service
    from app.services import automations as automations_service

    staff_id = 123
    company_id = 456

    async def fake_create_ticket(**kwargs):
        return {"id": 789, **kwargs}

    async def fake_get_company(cid):
        return {"id": cid, "name": "Test Company"} if cid == company_id else None

    async def fake_get_user(uid):
        return {"id": uid, "email": "john.doe@example.com"} if uid == staff_id else None

    async def fake_handle_event(event_name, context):
        return []

    async def fake_resolve_status(value):
        return value or "open"

    monkeypatch.setattr(tickets_repo, "create_ticket", fake_create_ticket)
    monkeypatch.setattr(automations_service, "handle_event", fake_handle_event)
    monkeypatch.setattr(tickets_service.company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(tickets_service.user_repo, "get_user_by_id", fake_get_user)
    monkeypatch.setattr(tickets_service, "resolve_status_or_default", fake_resolve_status)

    # Create a ticket with the staff member as requester
    ticket = await tickets_service.create_ticket(
        subject="Test ticket with staff requester",
        description="Testing requester field",
        requester_id=staff_id,
        company_id=company_id,
        assigned_user_id=None,
        priority="normal",
        status="open",
        category=None,
        module_slug=None,
        external_reference=None,
    )

    assert ticket is not None
    assert ticket["subject"] == "Test ticket with staff requester"
    assert ticket["requester_id"] == staff_id
    assert ticket["company_id"] == company_id

