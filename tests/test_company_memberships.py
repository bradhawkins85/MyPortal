import json
from typing import Any
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import status
from starlette.requests import Request
from starlette.responses import HTMLResponse

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


@pytest.mark.anyio("asyncio")
async def test_admin_remove_pending_company_assignment(monkeypatch):
    request = _make_request("/admin/companies/assignment/5/9/pending/remove")

    current_user = {"id": 10, "is_super_admin": True}
    monkeypatch.setattr(
        main,
        "_require_super_admin_page",
        AsyncMock(return_value=(current_user, None)),
    )

    pending_record = {"staff_id": 9, "company_id": 5, "staff_permission": 2}
    get_mock = AsyncMock(return_value=pending_record)
    monkeypatch.setattr(
        main.pending_staff_access_repo,
        "get_assignment",
        get_mock,
    )

    delete_mock = AsyncMock()
    monkeypatch.setattr(
        main.pending_staff_access_repo,
        "delete_assignment",
        delete_mock,
    )

    log_mock = AsyncMock()
    monkeypatch.setattr(main.audit_service, "log_action", log_mock)

    response = await main.admin_remove_pending_company_assignment(5, 9, request)

    assert response.status_code == status.HTTP_200_OK
    get_mock.assert_awaited_once_with(staff_id=9, company_id=5)
    delete_mock.assert_awaited_once_with(staff_id=9, company_id=5)
    log_mock.assert_awaited_once()


@pytest.mark.anyio("asyncio")
async def test_admin_assign_user_to_company_preserves_existing_permissions(monkeypatch):
    request = _make_request("/admin/companies/assign")

    form_mock = AsyncMock(
        return_value={
            "userId": "7",
            "companyId": "4",
            "staffPermission": "2",
        }
    )
    monkeypatch.setattr(request, "form", form_mock)

    current_user = {"id": 1, "is_super_admin": True}
    monkeypatch.setattr(
        main,
        "_require_super_admin_page",
        AsyncMock(return_value=(current_user, None)),
    )

    monkeypatch.setattr(
        main.user_repo,
        "get_user_by_id",
        AsyncMock(return_value={"id": 7, "email": "user@example.com"}),
    )
    monkeypatch.setattr(
        main.company_repo,
        "get_company_by_id",
        AsyncMock(return_value={"id": 4, "name": "Example Co"}),
    )

    existing_assignment = {
        "can_access_shop": True,
        "can_access_cart": True,
        "can_access_orders": False,
        "can_access_forms": True,
        "can_manage_assets": True,
        "can_manage_licenses": False,
        "can_manage_invoices": True,
        "can_manage_office_groups": False,
        "can_manage_issues": False,
        "can_order_licenses": True,
        "is_admin": True,
        "can_manage_staff": True,
    }
    monkeypatch.setattr(
        main.user_company_repo,
        "get_user_company",
        AsyncMock(return_value=existing_assignment),
    )

    assign_mock = AsyncMock()
    monkeypatch.setattr(main.user_company_repo, "assign_user_to_company", assign_mock)

    response = await main.admin_assign_user_to_company(request)

    assert response.status_code == status.HTTP_303_SEE_OTHER
    location = response.headers.get("location")
    assert location is not None
    parsed = urlparse(location)
    assert parsed.path == "/admin/companies/4/edit"
    params = parse_qs(parsed.query)
    assert params.get("success") == [
        "Updated access for user@example.com at Example Co"
    ]
    assign_mock.assert_awaited_once()
    assert assign_mock.await_args.kwargs == {
        "user_id": 7,
        "company_id": 4,
        "staff_permission": 2,
        "can_manage_staff": True,
        "can_access_shop": True,
        "can_access_cart": True,
        "can_access_orders": False,
        "can_access_forms": True,
        "can_manage_assets": True,
        "can_manage_licenses": False,
        "can_manage_invoices": True,
        "can_manage_office_groups": False,
        "can_manage_issues": False,
        "can_order_licenses": True,
        "is_admin": True,
    }


@pytest.mark.anyio("asyncio")
async def test_admin_assign_user_to_company_prefers_source_company(monkeypatch):
    request = _make_request("/admin/companies/assign")

    form_mock = AsyncMock(
        return_value={
            "userId": "7",
            "companyId": "8",
            "sourceCompanyId": "4",
            "staffPermission": "1",
        }
    )
    monkeypatch.setattr(request, "form", form_mock)

    current_user = {"id": 1, "is_super_admin": True}
    monkeypatch.setattr(
        main,
        "_require_super_admin_page",
        AsyncMock(return_value=(current_user, None)),
    )

    user_mock = AsyncMock(return_value={"id": 7, "email": "user@example.com"})
    monkeypatch.setattr(main.user_repo, "get_user_by_id", user_mock)

    company_mock = AsyncMock(return_value={"id": 4, "name": "Example Co"})
    monkeypatch.setattr(main.company_repo, "get_company_by_id", company_mock)

    existing_assignment_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(
        main.user_company_repo,
        "get_user_company",
        existing_assignment_mock,
    )

    assign_mock = AsyncMock()
    monkeypatch.setattr(main.user_company_repo, "assign_user_to_company", assign_mock)

    response = await main.admin_assign_user_to_company(request)

    assert response.status_code == status.HTTP_303_SEE_OTHER
    location = response.headers.get("location")
    assert location is not None
    parsed = urlparse(location)
    assert parsed.path == "/admin/companies/4/edit"
    params = parse_qs(parsed.query)
    assert params.get("success") == [
        "Updated access for user@example.com at Example Co"
    ]

    assert existing_assignment_mock.await_args.args == (7, 4)
    assign_kwargs = assign_mock.await_args.kwargs
    assert assign_kwargs.get("company_id") == 4
    assert assign_kwargs.get("user_id") == 7


@pytest.mark.anyio("asyncio")
async def test_admin_assign_user_to_company_queues_pending_access(monkeypatch):
    request = _make_request("/admin/companies/assign")

    form_mock = AsyncMock(
        return_value={
            "userId": "staff:202",
            "companyId": "4",
            "staffPermission": "2",
            "can_manage_staff": "1",
            "can_access_shop": "1",
            "roleId": "3",
        }
    )
    monkeypatch.setattr(request, "form", form_mock)

    current_user = {"id": 1, "is_super_admin": True}
    monkeypatch.setattr(
        main,
        "_require_super_admin_page",
        AsyncMock(return_value=(current_user, None)),
    )

    staff_record = {
        "id": 202,
        "email": "pending@example.com",
        "company_id": 4,
    }
    monkeypatch.setattr(
        main.staff_repo,
        "get_staff_by_id",
        AsyncMock(return_value=staff_record),
    )
    monkeypatch.setattr(main.user_repo, "get_user_by_email", AsyncMock(return_value=None))
    monkeypatch.setattr(
        main.role_repo,
        "get_role_by_id",
        AsyncMock(return_value={"id": 3}),
    )

    upsert_mock = AsyncMock()
    monkeypatch.setattr(
        main.pending_staff_access_repo,
        "upsert_assignment",
        upsert_mock,
    )

    captured: dict[str, Any] = {}

    def fake_redirect(*, company_id, success=None, error=None):
        captured["company_id"] = company_id
        captured["success"] = success
        captured["error"] = error
        return HTMLResponse("ok")

    monkeypatch.setattr(main, "_company_edit_redirect", fake_redirect)

    response = await main.admin_assign_user_to_company(request)

    assert response.status_code == status.HTTP_200_OK
    upsert_mock.assert_awaited_once()
    assert upsert_mock.await_args.kwargs == {
        "staff_id": 202,
        "company_id": 4,
        "staff_permission": 2,
        "can_manage_staff": True,
        "can_manage_licenses": False,
        "can_manage_assets": False,
        "can_manage_invoices": False,
        "can_manage_office_groups": False,
        "can_manage_issues": False,
        "can_order_licenses": False,
        "can_access_shop": True,
        "can_access_cart": False,
        "can_access_orders": False,
        "can_access_forms": False,
        "is_admin": False,
        "role_id": 3,
    }
    assert captured.get("company_id") == 4
    assert captured.get("success") == (
        "Saved pending access for pending@example.com. Permissions will activate after sign-up."
    )
    assert captured.get("error") is None


@pytest.mark.anyio("asyncio")
async def test_admin_assign_user_to_company_uses_source_company_in_form_state(monkeypatch):
    request = _make_request("/admin/companies/assign")

    form_mock = AsyncMock(
        return_value={
            "userId": "7",
            "companyId": "8",
            "sourceCompanyId": "4",
            "staffPermission": "1",
        }
    )
    monkeypatch.setattr(request, "form", form_mock)

    current_user = {"id": 1, "is_super_admin": True}
    monkeypatch.setattr(
        main,
        "_require_super_admin_page",
        AsyncMock(return_value=(current_user, None)),
    )

    monkeypatch.setattr(main.user_repo, "get_user_by_id", AsyncMock(return_value=None))
    monkeypatch.setattr(
        main.company_repo,
        "get_company_by_id",
        AsyncMock(return_value={"id": 4, "name": "Example Co"}),
    )

    captured: dict[str, Any] = {}

    async def fake_render_company_edit_page(
        request_obj,
        user_obj,
        *,
        company_id,
        form_values=None,
        assign_form_values=None,
        success_message=None,
        error_message=None,
        status_code=status.HTTP_200_OK,
    ) -> HTMLResponse:
        captured["company_id"] = company_id
        captured["assign_form_values"] = assign_form_values
        captured["error_message"] = error_message
        captured["status_code"] = status_code
        return HTMLResponse("error", status_code=status_code)

    monkeypatch.setattr(main, "_render_company_edit_page", fake_render_company_edit_page)

    response = await main.admin_assign_user_to_company(request)

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert captured.get("company_id") == 4
    assign_form_values = captured.get("assign_form_values", {})
    assert assign_form_values.get("company_id") == 4
    assert assign_form_values.get("user_id") == 7


@pytest.mark.anyio("asyncio")
async def test_render_company_edit_page_includes_assign_form_data(monkeypatch):
    request = _make_request("/admin/companies/4/edit")
    current_user = {"id": 1, "is_super_admin": True}

    company_record = {
        "id": 4,
        "name": "Example Co",
        "email_domains": [],
        "syncro_company_id": None,
        "xero_id": None,
        "is_vip": 0,
    }
    monkeypatch.setattr(
        main.company_repo,
        "get_company_by_id",
        AsyncMock(return_value=company_record),
    )

    managed_companies = [
        {"id": 4, "name": "Example Co"},
        {"id": 8, "name": "Other Co"},
    ]
    monkeypatch.setattr(
        main,
        "_get_company_management_scope",
        AsyncMock(return_value=(True, managed_companies, {})),
    )
    monkeypatch.setattr(
        main.user_company_repo,
        "list_assignments",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        main.role_repo,
        "list_roles",
        AsyncMock(return_value=[{"id": 3, "name": "Manager"}]),
    )

    monkeypatch.setattr(
        main.pending_staff_access_repo,
        "list_assignments_for_company",
        AsyncMock(return_value=[]),
    )

    async def fake_list_staff_with_users(company_id: int) -> list[dict[str, Any]]:
        if company_id == 4:
            return [
                {
                    "staff_id": 101,
                    "first_name": "Alpha",
                    "last_name": "One",
                    "email": "alpha@example.com",
                    "enabled": True,
                    "user_id": 10,
                },
                {
                    "staff_id": 102,
                    "first_name": "Inactive",
                    "last_name": "Person",
                    "email": "inactive@example.com",
                    "enabled": False,
                    "user_id": None,
                },
            ]
        if company_id == 8:
            return [
                {
                    "staff_id": 203,
                    "first_name": "",
                    "last_name": "",
                    "email": "zeta@example.com",
                    "enabled": True,
                    "user_id": 12,
                },
                {
                    "staff_id": 202,
                    "first_name": "Beta",
                    "last_name": "Person",
                    "email": "beta@example.com",
                    "enabled": True,
                    "user_id": 11,
                },
            ]
        return []

    monkeypatch.setattr(main.staff_repo, "list_staff_with_users", fake_list_staff_with_users)

    captured: dict[str, Any] = {}

    async def fake_render_template(template_name, request_obj, user_obj, *, extra):
        captured["template"] = template_name
        captured["extra"] = extra
        return HTMLResponse("ok")

    monkeypatch.setattr(main, "_render_template", fake_render_template)

    assign_form_values = {
        "company_id": "8",
        "user_id": "11",
        "staff_permission": "3",
        "role_id": "3",
        "can_manage_staff": True,
        "can_access_shop": True,
    }

    response = await main._render_company_edit_page(
        request,
        current_user,
        company_id=4,
        assign_form_values=assign_form_values,
    )

    assert response.status_code == status.HTTP_200_OK
    assert captured.get("template") == "admin/company_edit.html"
    extra = captured.get("extra", {})
    assert extra.get("assign_form", {}).get("company_id") == 8
    assert extra.get("assign_form", {}).get("user_id") == 11
    assert extra.get("assign_form", {}).get("user_value") == "11"
    assert extra.get("assign_form", {}).get("staff_permission") == 3
    assert extra.get("assign_form", {}).get("permissions", {}).get("can_access_shop") is True
    assert extra.get("assign_user_options") == [
        {
            "value": "11",
            "label": "Beta Person (beta@example.com)",
            "email": "beta@example.com",
            "staff_id": 202,
            "user_id": 11,
            "has_user": True,
            "pending_access": False,
        },
        {
            "value": "12",
            "label": "zeta@example.com",
            "email": "zeta@example.com",
            "staff_id": 203,
            "user_id": 12,
            "has_user": True,
            "pending_access": False,
        },
    ]
    assert extra.get("company_user_options", {}).get(4) == [
        {
            "value": "10",
            "label": "Alpha One (alpha@example.com)",
            "email": "alpha@example.com",
            "staff_id": 101,
            "user_id": 10,
            "has_user": True,
            "pending_access": False,
        },
        {
            "value": "staff:102",
            "label": "Inactive Person (inactive@example.com) (inactive) – invite required",
            "email": "inactive@example.com",
            "staff_id": 102,
            "user_id": None,
            "has_user": False,
            "pending_access": False,
        },
    ]


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
