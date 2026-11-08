from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app import main
from app.features.staff import handlers as staff_handlers


class _DummyRequest:
    def __init__(self, form_data: dict[str, object]):
        self._form_data = form_data

    async def form(self):
        return self._form_data


_TECH = {"id": 1, "is_super_admin": False, "email": "tech@msp.example"}


def _patch(monkeypatch, *, is_technician: bool, existing_staff=None):
    monkeypatch.setattr(
        staff_handlers,
        "_load_staff_context",
        AsyncMock(return_value=(_TECH, {"role": "admin"}, {"id": 9}, 3, 9, None)),
    )
    monkeypatch.setattr(
        main, "_has_admin_technician_access", AsyncMock(return_value=is_technician)
    )
    monkeypatch.setattr(
        staff_handlers.staff_field_config_service,
        "load_effective_company_staff_fields",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        staff_handlers.staff_field_config_service,
        "validate_staff_form_values",
        lambda submitted, field_config: (
            {
                "first_name": "Alex",
                "last_name": "Rivera",
                "email": "alex@acme.example",
                "mobile_phone": "",
            },
            [],
        ),
    )
    monkeypatch.setattr(
        staff_handlers.staff_custom_fields_repo,
        "list_field_definitions",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        staff_handlers, "_get_current_user_staff_job_title", AsyncMock(return_value=None)
    )
    mocks = {
        "list_by_email": AsyncMock(return_value=existing_staff or []),
        "create_staff": AsyncMock(return_value={"id": 501, "company_id": 9}),
        "create_request": AsyncMock(return_value={"id": 42}),
        "notify": AsyncMock(return_value=[]),
        "enqueue": AsyncMock(return_value=None),
        "audit": AsyncMock(return_value=None),
    }
    monkeypatch.setattr(staff_handlers.staff_repo, "list_staff_by_email", mocks["list_by_email"])
    monkeypatch.setattr(staff_handlers.staff_repo, "create_staff", mocks["create_staff"])
    monkeypatch.setattr(staff_handlers.staff_requests_repo, "create_request", mocks["create_request"])
    monkeypatch.setattr(
        staff_handlers.staff_onboarding_workflow_service,
        "notify_staff_approval_requested",
        mocks["notify"],
    )
    monkeypatch.setattr(
        staff_handlers.staff_onboarding_workflow_service,
        "enqueue_staff_onboarding_workflow",
        mocks["enqueue"],
    )
    monkeypatch.setattr(staff_handlers.audit_service, "log_action", mocks["audit"])
    return mocks


@pytest.mark.asyncio
async def test_skip_onboarding_creates_staff_directly(monkeypatch):
    mocks = _patch(monkeypatch, is_technician=True)
    request = _DummyRequest(
        {"first_name": "Alex", "last_name": "Rivera", "skip_onboarding_workflow": "1"}
    )

    response = await staff_handlers.create_staff_member(request)  # type: ignore[arg-type]

    assert response.status_code == 303
    kwargs = mocks["create_staff"].await_args.kwargs
    assert kwargs["company_id"] == 9
    assert kwargs["email"] == "alex@acme.example"
    assert kwargs["mobile_phone"] is None
    assert kwargs["onboarding_status"] == "completed"
    assert kwargs["onboarding_complete"] is True
    assert kwargs["approval_status"] == "approved"
    mocks["create_request"].assert_not_awaited()
    mocks["notify"].assert_not_awaited()
    mocks["enqueue"].assert_not_awaited()
    assert mocks["audit"].await_args.kwargs["action"] == "staff.created_without_onboarding"


@pytest.mark.asyncio
async def test_without_skip_creates_onboarding_request(monkeypatch):
    mocks = _patch(monkeypatch, is_technician=True)
    request = _DummyRequest({"first_name": "Alex", "last_name": "Rivera"})

    await staff_handlers.create_staff_member(request)  # type: ignore[arg-type]

    mocks["create_request"].assert_awaited_once()
    mocks["create_staff"].assert_not_awaited()


@pytest.mark.asyncio
async def test_non_technician_cannot_skip_onboarding(monkeypatch):
    mocks = _patch(monkeypatch, is_technician=False)
    request = _DummyRequest(
        {"first_name": "Alex", "last_name": "Rivera", "skip_onboarding_workflow": "on"}
    )

    with pytest.raises(HTTPException) as exc:
        await staff_handlers.create_staff_member(request)  # type: ignore[arg-type]

    assert exc.value.status_code == 403
    mocks["create_staff"].assert_not_awaited()
    mocks["create_request"].assert_not_awaited()


@pytest.mark.asyncio
async def test_skip_onboarding_rejects_duplicate_email_in_company(monkeypatch):
    mocks = _patch(
        monkeypatch,
        is_technician=True,
        existing_staff=[{"id": 7, "company_id": 9, "email": "alex@acme.example"}],
    )
    request = _DummyRequest(
        {"first_name": "Alex", "last_name": "Rivera", "skip_onboarding_workflow": "1"}
    )

    with pytest.raises(HTTPException) as exc:
        await staff_handlers.create_staff_member(request)  # type: ignore[arg-type]

    assert exc.value.status_code == 400
    mocks["create_staff"].assert_not_awaited()


def test_add_staff_form_offers_skip_onboarding_toggle():
    template = Path("app/templates/staff/index.html").read_text()
    assert 'name="skip_onboarding_workflow"' in template
    assert "Skip onboarding workflow" in template


def test_migration_shows_email_and_makes_mobile_optional():
    sql = Path(
        "migrations/545_staff_intake_email_visible_mobile_optional.sql"
    ).read_text()
    assert "-- phase: data" in sql
    assert "SET default_visible = 1" in sql
    assert "field_key = 'email'" in sql
    assert "SET default_required = 0" in sql
    assert "field_key = 'mobile_phone'" in sql
