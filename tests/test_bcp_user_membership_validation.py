"""BCP roles, reviews and recovery owners only accept users from the plan's company."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, status

from app.api.routes import bcp
from app.repositories import user_companies as user_company_repo
from app.repositories import users as user_repo

PLAN_ID = 9
COMPANY_ID = 101
MEMBER_ID = 20
OUTSIDER_ID = 30
SUPER_ADMIN_ID = 40


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _patch(monkeypatch):
    monkeypatch.setattr(
        bcp, "_require_bcp_edit", AsyncMock(return_value=({"id": 7}, COMPANY_ID))
    )
    monkeypatch.setattr(
        bcp.bcp_repo, "get_plan_by_company", AsyncMock(return_value={"id": PLAN_ID, "company_id": COMPANY_ID})
    )
    own = {"id": 5, "plan_id": PLAN_ID, "role_id": 5}
    for getter in ("get_role_by_id", "get_review_item_by_id", "get_role_assignment_by_id"):
        monkeypatch.setattr(bcp.bcp_repo, getter, AsyncMock(return_value=own))

    users = {
        MEMBER_ID: {"id": MEMBER_ID, "is_super_admin": 0},
        OUTSIDER_ID: {"id": OUTSIDER_ID, "is_super_admin": 0},
        SUPER_ADMIN_ID: {"id": SUPER_ADMIN_ID, "is_super_admin": 1},
    }

    async def get_user(user_id):
        return users.get(user_id)

    async def get_user_company(user_id, company_id):
        return {"user_id": user_id} if (user_id, company_id) == (MEMBER_ID, COMPANY_ID) else None

    monkeypatch.setattr(user_repo, "get_user_by_id", get_user)
    monkeypatch.setattr(user_company_repo, "get_user_company", get_user_company)
    monkeypatch.setattr(bcp.audit, "record", AsyncMock())
    monkeypatch.setattr(bcp.audit, "record_create", AsyncMock())


def _review_kwargs(**overrides):
    kwargs = dict(
        review_date="2026-01-01", version_label=None, review_approval_status="Draft",
        reviewed_by_user_id=None, approved_by_user_id=None, reason=None, changes_made=None,
        approval_snapshot=None,
    )
    kwargs.update(overrides)
    return kwargs


def _assignment_kwargs(user_id):
    return dict(user_id=user_id, collaborator_role="Executor", is_alternate=False, contact_info=None)


CASES = [
    ("assign_user_to_role", "create_role_assignment", lambda uid: dict(role_id=5, **_assignment_kwargs(uid))),
    ("update_role_assignment_endpoint", "update_role_assignment",
     lambda uid: dict(assignment_id=5, **_assignment_kwargs(uid))),
    ("create_review_item_endpoint", "create_review_item", lambda uid: _review_kwargs(reviewed_by_user_id=uid)),
    ("create_review_item_endpoint", "create_review_item", lambda uid: _review_kwargs(approved_by_user_id=uid)),
    ("update_review_item_endpoint", "update_review_item",
     lambda uid: dict(review_id=5, **_review_kwargs(reviewed_by_user_id=uid))),
    ("update_review_item_endpoint", "update_review_item",
     lambda uid: dict(review_id=5, **_review_kwargs(approved_by_user_id=uid))),
]


@pytest.mark.anyio("asyncio")
@pytest.mark.parametrize("handler,mutator,build", CASES)
@pytest.mark.parametrize("user_id", [OUTSIDER_ID, 999])
async def test_non_member_user_is_rejected(monkeypatch, handler, mutator, build, user_id):
    mutate = AsyncMock(return_value={"id": 5})
    monkeypatch.setattr(bcp.bcp_repo, mutator, mutate)
    with pytest.raises(HTTPException) as exc:
        await getattr(bcp, handler)(MagicMock(), **build(user_id))
    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
    mutate.assert_not_awaited()


@pytest.mark.anyio("asyncio")
@pytest.mark.parametrize("handler,mutator,build", CASES)
@pytest.mark.parametrize("user_id", [MEMBER_ID, SUPER_ADMIN_ID])
async def test_member_or_super_admin_is_accepted(monkeypatch, handler, mutator, build, user_id):
    mutate = AsyncMock(return_value={"id": 5})
    monkeypatch.setattr(bcp.bcp_repo, mutator, mutate)
    response = await getattr(bcp, handler)(MagicMock(), **build(user_id))
    assert response.status_code in (302, 303)
    mutate.assert_awaited_once()


@pytest.mark.anyio("asyncio")
async def test_recovery_action_owner_must_be_member(monkeypatch):
    create = AsyncMock(return_value={"id": 5})
    monkeypatch.setattr(bcp.bcp_repo, "create_recovery_action", create)
    with pytest.raises(HTTPException) as exc:
        await bcp.create_recovery_action_endpoint(
            MagicMock(), action="Restore", resources=None, owner_id=OUTSIDER_ID, rto_hours=None,
            due_date=None, critical_activity_id=None, asset_ids=[],
        )
    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
    create.assert_not_awaited()
