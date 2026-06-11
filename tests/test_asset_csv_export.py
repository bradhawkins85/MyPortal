from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.main as main_module
from app.features.assets import routes as assets_routes


@pytest.mark.parametrize(
    ("menu_level", "expected"),
    [
        ("read", False),
        ("write", True),
    ],
)
def test_assets_page_export_option_requires_write_menu_permission(monkeypatch, menu_level, expected):
    captured: dict[str, object] = {}
    user = {"id": 7, "company_id": 11, "is_super_admin": False}
    membership = {"menu_permissions": {"menu.assets": menu_level}}

    async def fake_require_authenticated_user(request):
        return user, None

    async def fake_get_user_company(user_id, company_id):
        assert user_id == 7
        assert company_id == 11
        return membership

    async def fake_get_company_by_id(company_id):
        assert company_id == 11
        return {"id": company_id, "name": "Example Co"}

    async def fake_list_company_assets(company_id):
        assert company_id == 11
        return []

    async def fake_list_field_definitions():
        return []

    async def fake_render_template(template_name, request, user_arg, *, extra):
        captured["template_name"] = template_name
        captured["extra"] = extra
        return SimpleNamespace(template_name=template_name, extra=extra)

    monkeypatch.setattr(main_module, "_require_authenticated_user", fake_require_authenticated_user)
    monkeypatch.setattr(main_module, "_render_template", fake_render_template)
    monkeypatch.setattr(assets_routes.user_company_repo, "get_user_company", fake_get_user_company)
    monkeypatch.setattr(assets_routes.company_repo, "get_company_by_id", fake_get_company_by_id)
    monkeypatch.setattr(assets_routes.asset_repo, "list_company_assets", fake_list_company_assets)
    monkeypatch.setattr(assets_routes.asset_custom_fields_repo, "list_field_definitions", fake_list_field_definitions)

    import asyncio

    response = asyncio.run(assets_routes.assets_page(SimpleNamespace()))

    assert response.extra["can_export_assets"] is expected
    assert captured["template_name"] == "assets/index.html"
