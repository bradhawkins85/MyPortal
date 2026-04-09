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
