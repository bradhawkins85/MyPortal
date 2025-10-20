import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import status
from starlette.requests import Request

from app import main
from app.repositories import company_memberships as membership_repo
from app.schemas.memberships import MembershipUpdate


async def _dummy_receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


def _make_request(path: str = "/admin/companies/assignment/1/2/role") -> Request:
    scope = {"type": "http", "method": "POST", "path": path, "headers": []}
    return Request(scope, _dummy_receive)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_admin_update_membership_role_saves(monkeypatch):
    request = _make_request()

    form_mock = AsyncMock(return_value={"roleId": "3"})
    monkeypatch.setattr(request, "form", form_mock)

    current_user = {"id": 1, "is_super_admin": True}
    monkeypatch.setattr(
        main,
        "_require_authenticated_user",
        AsyncMock(return_value=(current_user, None)),
    )
    monkeypatch.setattr(
        main,
        "_ensure_company_permission",
        AsyncMock(return_value=None),
    )

    membership_record = {"id": 5, "role_id": 2}
    monkeypatch.setattr(
        main.membership_repo,
        "get_membership_by_company_user",
        AsyncMock(return_value=membership_record),
    )
    monkeypatch.setattr(
        main.role_repo,
        "get_role_by_id",
        AsyncMock(return_value={"id": 3}),
    )
    update_mock = AsyncMock(return_value={"id": 5, "role_id": 3})
    monkeypatch.setattr(main.membership_repo, "update_membership", update_mock)
    log_mock = AsyncMock()
    monkeypatch.setattr(main.audit_service, "log_action", log_mock)

    response = await main.admin_update_membership_role(1, 2, request)

    assert response.status_code == status.HTTP_200_OK
    update_mock.assert_awaited_once_with(5, role_id=3)
    log_mock.assert_awaited_once()


@pytest.mark.anyio("asyncio")
async def test_admin_update_company_permission_toggles(monkeypatch):
    request = _make_request("/admin/companies/assignment/1/2/permission")

    form_mock = AsyncMock(return_value={"field": "can_manage_licenses", "value": "1"})
    monkeypatch.setattr(request, "form", form_mock)

    current_user = {"id": 1, "is_super_admin": True}
    monkeypatch.setattr(
        main,
        "_require_authenticated_user",
        AsyncMock(return_value=(current_user, None)),
    )
    monkeypatch.setattr(
        main,
        "_ensure_company_permission",
        AsyncMock(return_value=None),
    )

    update_mock = AsyncMock()
    monkeypatch.setattr(main.user_company_repo, "update_permission", update_mock)

    response = await main.admin_update_company_permission(1, 2, request)

    assert response.status_code == status.HTTP_200_OK
    update_mock.assert_awaited_once_with(
        user_id=2,
        company_id=1,
        field="can_manage_licenses",
        value=True,
    )


def test_membership_update_accepts_camel_case_alias():
    update = MembershipUpdate.model_validate({"roleId": 9})
    assert update.role_id == 9
    assert update.model_dump(exclude_unset=True) == {"role_id": 9}


@pytest.mark.anyio("asyncio")
async def test_list_users_with_permission_filters_and_sorts(monkeypatch):
    rows = [
        {
            "user_id": 5,
            "email": "tech@example.com",
            "first_name": "Tech",
            "last_name": "User",
            "mobile_phone": None,
            "company_id": 3,
            "is_super_admin": 0,
            "permissions": json.dumps(["helpdesk.technician", "portal.access"]),
        },
        {
            "user_id": 8,
            "email": "other@example.com",
            "first_name": "Other",
            "last_name": "Person",
            "mobile_phone": None,
            "company_id": 2,
            "is_super_admin": 0,
            "permissions": json.dumps(["portal.access"]),
        },
    ]

    fetch_mock = AsyncMock(return_value=rows)
    monkeypatch.setattr(membership_repo.db, "fetch_all", fetch_mock)

    result = await membership_repo.list_users_with_permission("helpdesk.technician")

    fetch_mock.assert_awaited_once()
    assert result == [
        {
            "id": 5,
            "email": "tech@example.com",
            "first_name": "Tech",
            "last_name": "User",
            "mobile_phone": None,
            "company_id": 3,
            "is_super_admin": False,
        }
    ]


@pytest.mark.anyio("asyncio")
async def test_list_users_with_permission_includes_super_admin(monkeypatch):
    rows = [
        {
            "user_id": 9,
            "email": "admin@example.com",
            "first_name": "Admin",
            "last_name": "User",
            "mobile_phone": "12345",
            "company_id": 1,
            "is_super_admin": 1,
            "permissions": json.dumps(["portal.access"]),
        }
    ]

    fetch_mock = AsyncMock(return_value=rows)
    monkeypatch.setattr(membership_repo.db, "fetch_all", fetch_mock)

    result = await membership_repo.list_users_with_permission("helpdesk.technician")

    assert result == [
        {
            "id": 9,
            "email": "admin@example.com",
            "first_name": "Admin",
            "last_name": "User",
            "mobile_phone": "12345",
            "company_id": 1,
            "is_super_admin": True,
        }
    ]


@pytest.mark.anyio("asyncio")
async def test_user_has_permission_allows_super_admin(monkeypatch):
    list_mock = AsyncMock(return_value=[])
    monkeypatch.setattr(membership_repo, "list_memberships_for_user", list_mock)

    user_mock = AsyncMock(return_value={"id": 3, "is_super_admin": 1})
    monkeypatch.setattr(membership_repo.user_repo, "get_user_by_id", user_mock)

    result = await membership_repo.user_has_permission(3, "helpdesk.technician")

    assert result is True
    list_mock.assert_awaited_once_with(3, status="active")
    user_mock.assert_awaited_once_with(3)
