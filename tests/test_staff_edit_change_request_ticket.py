from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.features.staff import handlers


class JsonRequest(SimpleNamespace):
    async def json(self):
        return self.payload


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_staff_rw_edit_creates_ticket_instead_of_updating_staff(monkeypatch):
    existing = {
        "id": 55,
        "company_id": 9,
        "first_name": "Alex",
        "last_name": "Smith",
        "email": "alex@example.com",
        "mobile_phone": "0400000000",
        "enabled": True,
        "department": "Support",
        "job_title": "Analyst",
        "org_company": "Example Co",
        "manager_name": "Morgan",
        "street": "1 Old St",
        "city": "Sydney",
        "state": "NSW",
        "postcode": "2000",
        "country": "AU",
        "account_action": "Onboarded",
        "date_onboarded": None,
        "date_offboarded": None,
        "custom_fields": {"Desk": "A1"},
    }
    created_ticket = {"id": 901, "subject": "Staff change request: Alex Smith"}

    async def fake_load_context(request, **kwargs):
        assert kwargs == {"require_admin": True}
        return (
            {"id": 7, "company_id": 9, "email": "requester@example.com", "first_name": "Pat", "last_name": "Lee"},
            {"menu_permissions": {"menu.staff": "write"}, "staff_permission": 3},
            {"id": 9, "name": "Example Co"},
            3,
            9,
            None,
        )

    monkeypatch.setattr(handlers, "_load_staff_context", fake_load_context)
    monkeypatch.setattr(handlers.staff_repo, "get_staff_by_id", AsyncMock(return_value=existing))
    update_mock = AsyncMock()
    monkeypatch.setattr(handlers.staff_repo, "update_staff", update_mock)
    create_ticket_mock = AsyncMock(return_value=created_ticket)
    monkeypatch.setattr(handlers.tickets_service, "create_ticket", create_ticket_mock)
    monkeypatch.setattr(handlers.audit_service, "log_action", AsyncMock())

    response = await handlers.update_staff_member(
        55,
        JsonRequest(
            payload={
                "firstName": "Alexandra",
                "department": "Projects",
                "street": "2 New St",
                "customFields": {"Desk": "B2"},
            }
        ),
    )

    assert response.status_code == 200
    assert update_mock.await_count == 0
    create_kwargs = create_ticket_mock.await_args.kwargs
    assert create_kwargs["requester_id"] == 7
    assert create_kwargs["company_id"] == 9
    assert create_kwargs["module_slug"] == "staff"
    assert "Staff member being edited" in create_kwargs["description"]
    assert "Requested by" in create_kwargs["description"]
    assert "[Identity] First name: Alex -> Alexandra" in create_kwargs["description"]
    assert "[Employment / Organisation] Department: Support -> Projects" in create_kwargs["description"]
    assert "[Address] Street: 1 Old St -> 2 New St" in create_kwargs["description"]
    assert "[Custom fields] Desk: A1 -> B2" in create_kwargs["description"]


@pytest.mark.anyio
async def test_staff_rw_edit_rejects_changed_workflow_lifecycle_fields(monkeypatch):
    existing = {
        "id": 55,
        "company_id": 9,
        "first_name": "Alex",
        "last_name": "Smith",
        "email": "alex@example.com",
        "department": "Support",
        "account_action": "Onboarded",
        "date_onboarded": "2026-01-01T00:00:00",
        "date_offboarded": None,
        "custom_fields": {},
    }

    async def fake_load_context(request, **kwargs):
        return (
            {"id": 7, "company_id": 9, "email": "requester@example.com"},
            {"menu_permissions": {"menu.staff": "write"}, "staff_permission": 3},
            {"id": 9, "name": "Example Co"},
            3,
            9,
            None,
        )

    monkeypatch.setattr(handlers, "_load_staff_context", fake_load_context)
    monkeypatch.setattr(handlers.staff_repo, "get_staff_by_id", AsyncMock(return_value=existing))
    monkeypatch.setattr(handlers.tickets_service, "create_ticket", AsyncMock())

    with pytest.raises(HTTPException) as exc:
        await handlers.update_staff_member(
            55,
            JsonRequest(payload={"accountAction": "Offboard Requested", "firstName": "Alexandra"}),
        )

    assert exc.value.status_code == 400
    assert "Workflow & lifecycle fields cannot be changed" in exc.value.detail
    assert handlers.tickets_service.create_ticket.await_count == 0
