from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app import main
from app.features.staff import handlers as staff_handlers
from app.services import staff_onboarding_workflows as workflow_service


class _DummyRequest:
    def __init__(self, form_data: dict[str, object]):
        self._form_data = form_data

    async def form(self):
        return self._form_data


_TECH = {
    "id": 1,
    "is_super_admin": False,
    "first_name": "Terry",
    "last_name": "Tech",
    "email": "tech@msp.example",
}

_MANAGER = {
    "id": 77,
    "company_id": 9,
    "first_name": "Morgan",
    "last_name": "Manager",
    "email": "morgan@acme.example",
    "enabled": 1,
    "is_ex_staff": 0,
}


def _patch_common(monkeypatch, *, is_technician: bool, staff_member=None, portal_user=None):
    monkeypatch.setattr(
        staff_handlers,
        "_load_staff_context",
        AsyncMock(return_value=(_TECH, {"role": "admin"}, {"id": 9}, 3, 9, None)),
    )
    monkeypatch.setattr(
        main, "_is_helpdesk_technician", AsyncMock(return_value=is_technician)
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
            {"first_name": "Alex", "last_name": "Rivera", "email": ""},
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
    monkeypatch.setattr(
        staff_handlers.staff_repo,
        "get_staff_by_id",
        AsyncMock(return_value=staff_member),
    )
    monkeypatch.setattr(
        staff_handlers.user_repo,
        "get_user_by_email",
        AsyncMock(return_value=portal_user),
    )
    create_request_mock = AsyncMock(return_value={"id": 42})
    monkeypatch.setattr(staff_handlers.staff_requests_repo, "create_request", create_request_mock)
    notify_mock = AsyncMock(return_value=[])
    monkeypatch.setattr(
        staff_handlers.staff_onboarding_workflow_service,
        "notify_staff_approval_requested",
        notify_mock,
    )
    audit_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(staff_handlers.audit_service, "log_action", audit_mock)
    return create_request_mock, notify_mock, audit_mock


@pytest.mark.asyncio
async def test_technician_can_request_on_behalf_of_staff_with_portal_login(monkeypatch):
    create_mock, notify_mock, audit_mock = _patch_common(
        monkeypatch,
        is_technician=True,
        staff_member=_MANAGER,
        portal_user={"id": 55, "email": "morgan@acme.example"},
    )
    request = _DummyRequest(
        {"first_name": "Alex", "last_name": "Rivera", "requested_on_behalf_of_staff_id": "77"}
    )

    response = await staff_handlers.create_staff_member(request)  # type: ignore[arg-type]

    assert response.status_code == 303
    kwargs = create_mock.await_args.kwargs
    assert kwargs["requested_by_user_id"] == 55
    assert kwargs["requested_by_name"] == "Morgan Manager"
    assert kwargs["requested_by_email"] == "morgan@acme.example"
    assert notify_mock.await_args.kwargs["requester_user_id"] == 55
    assert notify_mock.await_args.kwargs["requester_name"] == "Morgan Manager"
    audit_kwargs = audit_mock.await_args.kwargs
    assert audit_kwargs["user_id"] == 1
    assert audit_kwargs["metadata"]["requested_on_behalf_of_staff_id"] == 77


@pytest.mark.asyncio
async def test_on_behalf_of_staff_without_portal_login_keeps_email(monkeypatch):
    create_mock, _, _ = _patch_common(
        monkeypatch, is_technician=True, staff_member=_MANAGER, portal_user=None
    )
    request = _DummyRequest(
        {"first_name": "Alex", "last_name": "Rivera", "requested_on_behalf_of_staff_id": "77"}
    )

    await staff_handlers.create_staff_member(request)  # type: ignore[arg-type]

    kwargs = create_mock.await_args.kwargs
    assert kwargs["requested_by_user_id"] is None
    assert kwargs["requested_by_email"] == "morgan@acme.example"


@pytest.mark.asyncio
async def test_without_selection_technician_is_requester(monkeypatch):
    create_mock, _, _ = _patch_common(monkeypatch, is_technician=True)
    request = _DummyRequest({"first_name": "Alex", "last_name": "Rivera"})

    await staff_handlers.create_staff_member(request)  # type: ignore[arg-type]

    kwargs = create_mock.await_args.kwargs
    assert kwargs["requested_by_user_id"] == 1
    assert kwargs["requested_by_email"] == "tech@msp.example"


@pytest.mark.asyncio
async def test_non_technician_cannot_request_on_behalf_of(monkeypatch):
    create_mock, _, _ = _patch_common(
        monkeypatch, is_technician=False, staff_member=_MANAGER
    )
    request = _DummyRequest(
        {"first_name": "Alex", "last_name": "Rivera", "requested_on_behalf_of_staff_id": "77"}
    )

    with pytest.raises(HTTPException) as exc_info:
        await staff_handlers.create_staff_member(request)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 403
    create_mock.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "staff_member",
    [
        None,
        {**_MANAGER, "company_id": 10},
        {**_MANAGER, "enabled": 0},
        {**_MANAGER, "is_ex_staff": 1},
        {**_MANAGER, "email": ""},
    ],
)
async def test_on_behalf_of_rejects_invalid_staff(monkeypatch, staff_member):
    create_mock, _, _ = _patch_common(
        monkeypatch, is_technician=True, staff_member=staff_member
    )
    request = _DummyRequest(
        {"first_name": "Alex", "last_name": "Rivera", "requested_on_behalf_of_staff_id": "77"}
    )

    with pytest.raises(HTTPException) as exc_info:
        await staff_handlers.create_staff_member(request)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 400
    create_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_requestor_email_falls_back_to_stored_email(monkeypatch):
    get_user = AsyncMock(return_value=None)
    monkeypatch.setattr(workflow_service.user_repo, "get_user_by_id", get_user)

    assert (
        await workflow_service._resolve_requestor_email(
            {"requested_by_user_id": None, "requested_by_email": "Morgan@Acme.example"}
        )
        == "morgan@acme.example"
    )
    get_user.assert_not_awaited()


@pytest.mark.asyncio
async def test_requestor_email_prefers_portal_user_email(monkeypatch):
    monkeypatch.setattr(
        workflow_service.user_repo,
        "get_user_by_id",
        AsyncMock(return_value={"id": 55, "email": "current@acme.example"}),
    )

    assert (
        await workflow_service._resolve_requestor_email(
            {"requested_by_user_id": 55, "requested_by_email": "old@acme.example"}
        )
        == "current@acme.example"
    )
