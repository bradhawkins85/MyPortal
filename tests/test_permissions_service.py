import asyncio

from app.services import permissions as permissions_service


def test_collect_role_permissions_filters_duplicates(monkeypatch):
    async def fake_list_memberships_for_user(user_id, *, status="active"):
        assert user_id == 5
        assert status == "active"
        return [
            {"permissions": ["helpdesk.technician", "portal.access"], "status": "active"},
            {"permissions": ["portal.access", "billing.manage"], "status": "active"},
        ]

    monkeypatch.setattr(
        permissions_service.membership_repo,
        "list_memberships_for_user",
        fake_list_memberships_for_user,
    )

    permissions = asyncio.run(permissions_service.collect_role_permissions(5))
    assert permissions == {"helpdesk.technician", "portal.access", "billing.manage"}


def test_user_has_role_permission_checks_super_admin(monkeypatch):
    async def fake_collect(user_id):  # pragma: no cover - should not run
        raise AssertionError("collect_role_permissions should not be called for super admins")

    monkeypatch.setattr(permissions_service, "collect_role_permissions", fake_collect)

    result = asyncio.run(
        permissions_service.user_has_role_permission(
            {"id": 1, "is_super_admin": True},
            "helpdesk.technician",
        )
    )
    assert result is True


def test_user_has_role_permission_denies_when_missing(monkeypatch):
    async def fake_collect(user_id):
        return {"portal.access"}

    monkeypatch.setattr(permissions_service, "collect_role_permissions", fake_collect)

    result = asyncio.run(
        permissions_service.user_has_role_permission(
            {"id": 2, "is_super_admin": False},
            "helpdesk.technician",
        )
    )
    assert result is False
