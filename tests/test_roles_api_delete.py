"""Deleting a role that company members still hold is refused with a clear message."""
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.routes import roles as roles_routes


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_delete_role_in_use_returns_conflict(monkeypatch):
    delete = AsyncMock()
    monkeypatch.setattr(roles_routes.role_repo, "get_role_by_id", AsyncMock(return_value={"id": 7, "is_system": False}))
    monkeypatch.setattr(roles_routes.role_repo, "count_members_by_role", AsyncMock(return_value={7: 3}))
    monkeypatch.setattr(roles_routes.role_repo, "delete_role", delete)

    with pytest.raises(HTTPException) as excinfo:
        await roles_routes.delete_role(7, request=None, _=None, current_user={"id": 1})

    assert excinfo.value.status_code == 409
    assert "3 company members" in excinfo.value.detail
    delete.assert_not_awaited()


@pytest.mark.anyio
async def test_delete_unassigned_role_still_deletes(monkeypatch):
    delete = AsyncMock()
    monkeypatch.setattr(roles_routes.role_repo, "get_role_by_id", AsyncMock(return_value={"id": 8, "is_system": False}))
    monkeypatch.setattr(roles_routes.role_repo, "count_members_by_role", AsyncMock(return_value={7: 3}))
    monkeypatch.setattr(roles_routes.role_repo, "delete_role", delete)
    monkeypatch.setattr(roles_routes.audit_service, "record_delete", AsyncMock())

    await roles_routes.delete_role(8, request=None, _=None, current_user={"id": 1})

    delete.assert_awaited_once_with(8)
