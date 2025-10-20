import asyncio

import pytest
from fastapi import HTTPException

from app.api.dependencies import auth as auth_dependencies
from app.services import permissions as permissions_service


def test_require_helpdesk_technician_allows_when_authorised(monkeypatch):
    async def fake_has_permission(user, permission):
        assert permission == "helpdesk.technician"
        return True

    monkeypatch.setattr(
        permissions_service,
        "user_has_role_permission",
        fake_has_permission,
    )

    current_user = {"id": 7, "is_super_admin": False}
    result = asyncio.run(
        auth_dependencies.require_helpdesk_technician(current_user=current_user)
    )
    assert result is current_user


def test_require_helpdesk_technician_raises_when_unauthorised(monkeypatch):
    async def fake_has_permission(user, permission):
        return False

    monkeypatch.setattr(
        permissions_service,
        "user_has_role_permission",
        fake_has_permission,
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            auth_dependencies.require_helpdesk_technician(
                current_user={"id": 8, "is_super_admin": False}
            )
        )

    assert exc.value.status_code == 403
    assert "Helpdesk technician" in exc.value.detail
