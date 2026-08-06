from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_require_staff_request_access_allows_company_admin(monkeypatch):
    from app.api.routes import staff

    monkeypatch.setattr(
        staff.membership_repo,
        "get_membership_by_company_user",
        AsyncMock(
            return_value={
                "status": "active",
                "combined_permissions": ["company.admin"],
            }
        ),
    )

    await staff._require_staff_request_access({"id": 42, "is_super_admin": False}, 3)


@pytest.mark.anyio
async def test_require_staff_request_access_allows_request_permission(monkeypatch):
    from app.api.routes import staff

    monkeypatch.setattr(
        staff.membership_repo,
        "get_membership_by_company_user",
        AsyncMock(
            return_value={
                "status": "active",
                "combined_permissions": [staff.STAFF_REQUEST_PERMISSION],
            }
        ),
    )

    await staff._require_staff_request_access({"id": 42, "is_super_admin": False}, 3)


@pytest.mark.anyio
async def test_require_staff_request_access_rejects_wrong_company_membership(monkeypatch):
    from app.api.routes import staff

    monkeypatch.setattr(
        staff.membership_repo,
        "get_membership_by_company_user",
        AsyncMock(return_value=None),
    )

    with pytest.raises(HTTPException) as exc:
        await staff._require_staff_request_access({"id": 42, "is_super_admin": False}, 7)

    assert exc.value.status_code == 403
    assert exc.value.detail == "Company membership required"


@pytest.mark.anyio
async def test_create_staff_request_forces_company_scope(monkeypatch):
    from app.api.routes import staff
    from app.schemas.staff import StaffRequestCreate

    monkeypatch.setattr(staff, "_ensure_company_exists", AsyncMock())
    monkeypatch.setattr(staff, "_require_staff_request_access", AsyncMock())
    monkeypatch.setattr(
        staff.staff_onboarding_workflow_service,
        "notify_staff_approval_requested",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(staff.audit_service, "log_action", AsyncMock())

    create_mock = AsyncMock(
        return_value={
            "id": 99,
            "company_id": 4,
            "first_name": "Casey",
            "last_name": "Jones",
            "email": "casey@example.com",
            "status": "pending",
            "custom_fields": {},
        }
    )
    monkeypatch.setattr(staff.staff_requests_repo, "create_request", create_mock)

    payload = StaffRequestCreate(
        firstName="Casey",
        lastName="Jones",
        email="casey@example.com",
        onboardingStatus="requested",
    )

    result = await staff.create_staff_request(
        company_id=4,
        payload=payload,
        _=None,
        current_user={"id": 7, "is_super_admin": False},
    )

    assert result.company_id == 4
    assert result.status == "pending"
    create_kwargs = create_mock.await_args.kwargs
    assert create_kwargs["company_id"] == 4
    assert create_kwargs["requested_by_user_id"] == 7


@pytest.mark.anyio
async def test_create_staff_request_blocks_group_mapped_custom_fields_for_non_admin(monkeypatch):
    from app.api.routes import staff
    from app.schemas.staff import StaffRequestCreate

    monkeypatch.setattr(staff, "_ensure_company_exists", AsyncMock())
    monkeypatch.setattr(staff, "_require_staff_request_access", AsyncMock())
    monkeypatch.setattr(
        staff.membership_repo,
        "get_membership_by_company_user",
        AsyncMock(return_value={"status": "active", "combined_permissions": [staff.STAFF_REQUEST_PERMISSION]}),
    )
    monkeypatch.setattr(
        staff.staff_workflow_repo,
        "get_company_workflow_policy",
        AsyncMock(return_value={"config": {"custom_field_group_mappings": {"entra_admin": ["group-admin"]}}}),
    )
    monkeypatch.setattr(
        staff.staff_onboarding_workflow_service,
        "notify_staff_approval_requested",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(staff.audit_service, "log_action", AsyncMock())

    create_mock = AsyncMock(
        return_value={
            "id": 100,
            "company_id": 4,
            "first_name": "Casey",
            "last_name": "Jones",
            "email": "casey@example.com",
            "status": "pending",
            "custom_fields": {},
        }
    )
    monkeypatch.setattr(staff.staff_requests_repo, "create_request", create_mock)

    payload = StaffRequestCreate(
        firstName="Casey",
        lastName="Jones",
        email="casey@example.com",
        customFields={"entra_admin": True, "location": "NYC"},
    )

    await staff.create_staff_request(
        company_id=4,
        payload=payload,
        _=None,
        current_user={"id": 7, "is_super_admin": False},
    )

    create_kwargs = create_mock.await_args.kwargs
    assert create_kwargs["custom_fields"] == {"location": "NYC"}


@pytest.mark.anyio
async def test_create_staff_request_allows_group_mapped_custom_fields_for_department_manager(monkeypatch):
    from app.api.routes import staff
    from app.schemas.staff import StaffRequestCreate

    monkeypatch.setattr(staff, "_ensure_company_exists", AsyncMock())
    monkeypatch.setattr(staff, "_require_staff_request_access", AsyncMock())
    monkeypatch.setattr(
        staff.membership_repo,
        "get_membership_by_company_user",
        AsyncMock(
            return_value={
                "status": "active",
                "combined_permissions": [staff.STAFF_REQUEST_PERMISSION],
                "staff_permission": 2,
            }
        ),
    )
    monkeypatch.setattr(
        staff.staff_onboarding_workflow_service,
        "notify_staff_approval_requested",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(staff.audit_service, "log_action", AsyncMock())

    create_mock = AsyncMock(
        return_value={
            "id": 100,
            "company_id": 4,
            "first_name": "Casey",
            "last_name": "Jones",
            "email": "casey@example.com",
            "status": "pending",
            "custom_fields": {},
        }
    )
    monkeypatch.setattr(staff.staff_requests_repo, "create_request", create_mock)

    payload = StaffRequestCreate(
        firstName="Casey",
        lastName="Jones",
        email="casey@example.com",
        customFields={"entra_admin": True, "location": "NYC"},
    )

    await staff.create_staff_request(
        company_id=4,
        payload=payload,
        _=None,
        current_user={"id": 7, "is_super_admin": False},
    )

    create_kwargs = create_mock.await_args.kwargs
    assert create_kwargs["custom_fields"] == {"entra_admin": True, "location": "NYC"}


@pytest.mark.anyio
async def test_create_staff_request_allows_api_key_for_selected_company(monkeypatch):
    from app.api.routes import staff
    from app.schemas.staff import StaffRequestCreate

    monkeypatch.setattr(staff, "_ensure_company_exists", AsyncMock())
    monkeypatch.setattr(
        staff.staff_onboarding_workflow_service,
        "notify_staff_approval_requested",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(staff.audit_service, "log_action", AsyncMock())
    monkeypatch.setattr(
        staff.staff_field_config_service,
        "load_effective_company_staff_fields",
        AsyncMock(
            return_value=[
                {"key": "first_name", "label": "First name", "type": "text", "required": True},
                {"key": "last_name", "label": "Last name", "type": "text", "required": True},
                {"key": "enabled", "label": "Enabled", "type": "checkbox", "required": False},
            ]
        ),
    )
    monkeypatch.setattr(
        staff.staff_custom_fields_repo,
        "list_field_definitions",
        AsyncMock(
            return_value=[
                {"name": "office_location", "field_type": "select", "options": [{"value": "sydney"}]}
            ]
        ),
    )
    create_mock = AsyncMock(
        return_value={
            "id": 101,
            "company_id": 9,
            "first_name": "API",
            "last_name": "User",
            "email": "api@example.com",
            "status": "pending",
            "custom_fields": {},
        }
    )
    monkeypatch.setattr(staff.staff_requests_repo, "create_request", create_mock)

    result = await staff.create_staff_request(
        payload=StaffRequestCreate(
            firstName="API",
            lastName="User",
            email="api@example.com",
            enabled=False,
            customFields={"office_location": "sydney"},
        ),
        company_id=9,
        _=None,
        current_user=None,
        api_key_record={"id": 12},
    )

    assert result.company_id == 9
    assert create_mock.await_args.kwargs["requested_by_user_id"] is None
    assert create_mock.await_args.kwargs["enabled"] is False
    assert create_mock.await_args.kwargs["custom_fields"] == {"office_location": "sydney"}


@pytest.mark.anyio
async def test_api_key_can_flag_staff_for_offboarding_approval(monkeypatch):
    from app.api.routes import staff
    from app.schemas.staff import StaffOffboardingRequestCreate

    record = {
        "id": 44,
        "company_id": 9,
        "first_name": "Alex",
        "last_name": "Smith",
        "email": "alex@example.com",
        "enabled": True,
        "is_ex_staff": False,
        "account_action": None,
        "onboarding_complete": True,
    }
    monkeypatch.setattr(
        staff.staff_repo, "get_staff_by_id", AsyncMock(return_value=record)
    )
    update_mock = AsyncMock(
        return_value={
            **record,
            "account_action": "Offboard Requested",
            "onboarding_status": "offboarding_awaiting_approval",
            "approval_status": "pending",
            "date_offboarded": "2026-08-10T02:00:00+00:00",
        }
    )
    monkeypatch.setattr(staff.staff_repo, "update_staff", update_mock)
    monkeypatch.setattr(
        staff.staff_onboarding_workflow_service,
        "notify_staff_approval_requested",
        AsyncMock(return_value=[2]),
    )
    monkeypatch.setattr(
        staff.staff_onboarding_workflow_service,
        "get_staff_workflow_status",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(staff.audit_service, "log_action", AsyncMock())

    result = await staff.request_staff_offboarding(
        staff_id=44,
        payload=StaffOffboardingRequestCreate(
            companyId=9,
            dateOffboarded="2026-08-10T12:00:00+10:00",
            offboardingType="resignation",
            notes="Final day",
        ),
        _=None,
        api_key_record={"id": 12},
    )

    assert result.approval_status == "pending"
    kwargs = update_mock.await_args.kwargs
    assert kwargs["account_action"] == "Offboard Requested"
    assert kwargs["onboarding_status"] == "offboarding_awaiting_approval"
    assert kwargs["date_offboarded"].isoformat() == "2026-08-10T02:00:00+00:00"
