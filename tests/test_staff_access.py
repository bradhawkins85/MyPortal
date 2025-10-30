from typing import Any
from unittest.mock import AsyncMock, call

import pytest

from app.services import staff_access as staff_access_service


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_apply_pending_access_for_user_assigns_permissions(monkeypatch):
    user = {"id": 5, "email": "user@example.com"}

    staff_rows = [
        {"id": 10, "company_id": 3},
        {"id": 11, "company_id": 4},
    ]

    assignments: dict[tuple[int, int], dict[str, Any]] = {
        (10, 3): {
            "staff_permission": 2,
            "can_manage_staff": True,
            "can_manage_licenses": True,
            "can_manage_assets": False,
            "can_manage_invoices": True,
            "can_manage_office_groups": False,
            "can_order_licenses": False,
            "can_access_shop": True,
            "can_access_cart": False,
            "can_access_orders": False,
            "can_access_forms": True,
            "is_admin": True,
            "role_id": 7,
        },
        (11, 4): {
            "staff_permission": 1,
            "can_manage_staff": False,
            "can_manage_licenses": False,
            "can_manage_assets": False,
            "can_manage_invoices": False,
            "can_manage_office_groups": False,
            "can_order_licenses": False,
            "can_access_shop": False,
            "can_access_cart": True,
            "can_access_orders": True,
            "can_access_forms": False,
            "is_admin": False,
            "role_id": None,
        },
    }

    async def fake_list_staff_by_email(email: str):
        assert email == "user@example.com"
        return staff_rows

    async def fake_get_assignment(*, staff_id: int, company_id: int):
        return assignments.get((staff_id, company_id))

    assign_mock = AsyncMock()
    delete_mock = AsyncMock()

    monkeypatch.setattr(
        staff_access_service.staff_repo,
        "list_staff_by_email",
        fake_list_staff_by_email,
    )
    monkeypatch.setattr(
        staff_access_service.pending_repo,
        "get_assignment",
        fake_get_assignment,
    )
    monkeypatch.setattr(
        staff_access_service.user_company_repo,
        "assign_user_to_company",
        assign_mock,
    )
    monkeypatch.setattr(
        staff_access_service.pending_repo,
        "delete_assignment",
        delete_mock,
    )

    membership_lookup: dict[tuple[int, int], dict[str, Any]] = {
        (3, 5): {"id": 22, "role_id": 5},
        (4, 5): {"id": 23, "role_id": 7},
    }

    async def fake_get_membership(company_id: int, user_id: int):
        return membership_lookup.get((company_id, user_id))

    update_membership_mock = AsyncMock()

    monkeypatch.setattr(
        staff_access_service.membership_repo,
        "get_membership_by_company_user",
        fake_get_membership,
    )
    monkeypatch.setattr(
        staff_access_service.membership_repo,
        "update_membership",
        update_membership_mock,
    )

    await staff_access_service.apply_pending_access_for_user(user)

    assert assign_mock.await_count == 2
    first_call = assign_mock.await_args_list[0]
    assert first_call.kwargs == {
        "user_id": 5,
        "company_id": 3,
        "staff_permission": 2,
        "can_manage_staff": True,
        "can_manage_licenses": True,
        "can_manage_assets": False,
        "can_manage_invoices": True,
        "can_manage_office_groups": False,
        "can_order_licenses": False,
        "can_access_shop": True,
        "can_access_cart": False,
        "can_access_orders": False,
        "can_access_forms": True,
        "is_admin": True,
    }
    second_call = assign_mock.await_args_list[1]
    assert second_call.kwargs == {
        "user_id": 5,
        "company_id": 4,
        "staff_permission": 1,
        "can_manage_staff": False,
        "can_manage_licenses": False,
        "can_manage_assets": False,
        "can_manage_invoices": False,
        "can_manage_office_groups": False,
        "can_order_licenses": False,
        "can_access_shop": False,
        "can_access_cart": True,
        "can_access_orders": True,
        "can_access_forms": False,
        "is_admin": False,
    }

    update_membership_mock.assert_awaited_once_with(22, role_id=7)
    assert delete_mock.await_args_list == [
        call(staff_id=10, company_id=3),
        call(staff_id=11, company_id=4),
    ]
