from types import SimpleNamespace

import anyio

from app import main
from app.services import role_switching


class _State:
    pass


def _request():
    return SimpleNamespace(state=_State())


def test_non_super_admin_cannot_activate_session_role(monkeypatch):
    async def unexpected_role_lookup(_role_id):  # pragma: no cover - assertion helper
        raise AssertionError("A non-Super Admin role selection must never be loaded")

    monkeypatch.setattr(role_switching.role_repo, "get_role_by_id", unexpected_role_lookup)
    user = {"id": 7, "is_super_admin": False}
    result = anyio.run(
        role_switching.apply_selected_role,
        _request(),
        user,
        SimpleNamespace(selected_role_id=12, active_company_id=4),
    )

    assert result is user
    assert result["is_super_admin"] is False


def test_selected_role_removes_super_admin_and_uses_only_role_permissions(monkeypatch):
    role = {
        "id": 12,
        "name": "Auditor",
        "permissions": {"menu.reports": "read", "menu.admin.roles": "none"},
        "legacy_permissions": ["reports.access"],
    }

    async def get_role(_role_id):
        return role

    monkeypatch.setattr(role_switching.role_repo, "get_role_by_id", get_role)
    request = _request()
    async def apply_and_check():
        result = await role_switching.apply_selected_role(
            request,
            {"id": 1, "email": "admin@example.com", "is_super_admin": True},
            SimpleNamespace(selected_role_id=12, active_company_id=4),
        )
        assert role_switching.effective_role_has_permission("reports.access") is True
        assert role_switching.effective_role_has_permission("admin.roles.manage") is False
        return result

    result = anyio.run(apply_and_check)

    assert result["is_super_admin"] is False
    assert result["role_switcher_allowed"] is True
    assert request.state.active_membership["menu_permissions"]["menu.reports"] == "read"


def test_role_switcher_markup_is_persistent_and_orders_super_admin_first():
    template = open("app/templates/base.html", encoding="utf-8").read()

    switcher = template.index('data-role-switcher')
    menu = template.index('<ul class="menu">')
    super_admin = template.index('>Super Admin mode</option>')
    roles_loop = template.index('{% for role in role_switcher_roles %}')
    assert switcher < menu
    assert super_admin < roles_loop
    assert "role_switcher_allowed" in template


def test_effective_company_membership_prefers_selected_role(monkeypatch):
    persisted = {"company_id": 4, "can_manage_assets": False}

    async def get_membership(_user_id, _company_id):
        return persisted

    monkeypatch.setattr(main.user_company_repo, "get_user_company", get_membership)
    request = _request()
    request.state.selected_role = {"id": 12}
    request.state.active_membership = {
        "company_id": 4,
        "can_manage_assets": True,
        "menu_permissions": {"menu.assets": "write"},
    }

    membership = anyio.run(
        main._get_effective_company_membership, request, 1, 4
    )

    assert membership is request.state.active_membership
    assert membership["menu_permissions"]["menu.assets"] == "write"


def test_effective_company_membership_uses_persisted_membership_without_role(monkeypatch):
    persisted = {"company_id": 4, "menu_permissions": {"menu.assets": "read"}}

    async def get_membership(_user_id, _company_id):
        return persisted

    monkeypatch.setattr(main.user_company_repo, "get_user_company", get_membership)
    request = _request()

    membership = anyio.run(
        main._get_effective_company_membership, request, 7, 4
    )

    assert membership is persisted
