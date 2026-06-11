from starlette.requests import Request
from starlette.responses import HTMLResponse
import pytest

from app.features.assets import routes as assets_routes
import app.main as main_module


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/assets", "headers": []})


@pytest.mark.anyio
async def test_assets_page_allows_read_only_menu_permission(monkeypatch):
    user = {"id": 7, "company_id": 42, "is_super_admin": False}
    membership = {
        "company_id": 42,
        "can_manage_assets": False,
        "menu_permissions": {"menu.assets": "read"},
    }

    async def fake_require_authenticated_user(request):
        return user, None

    async def fake_get_user_company(user_id, company_id):
        return membership

    async def fake_get_company_by_id(company_id):
        return {"id": company_id, "name": "Acme"}

    async def fake_list_company_assets(company_id):
        return []

    async def fake_list_field_definitions():
        return []

    async def fake_get_all_asset_field_values(asset_ids):
        return {}

    async def fake_render_template(template, request, current_user, *, extra=None):
        assert template == "assets/index.html"
        assert extra["company"] == {"id": 42, "name": "Acme"}
        return HTMLResponse("assets page")

    monkeypatch.setattr(main_module, "_require_authenticated_user", fake_require_authenticated_user)
    monkeypatch.setattr(assets_routes.user_company_repo, "get_user_company", fake_get_user_company)
    monkeypatch.setattr(assets_routes.company_repo, "get_company_by_id", fake_get_company_by_id)
    monkeypatch.setattr(assets_routes.asset_repo, "list_company_assets", fake_list_company_assets)
    monkeypatch.setattr(assets_routes.asset_custom_fields_repo, "list_field_definitions", fake_list_field_definitions)
    monkeypatch.setattr(assets_routes.asset_custom_fields_repo, "get_all_asset_field_values", fake_get_all_asset_field_values)
    monkeypatch.setattr(main_module, "_render_template", fake_render_template)

    response = await assets_routes.assets_page(_request())

    assert response.status_code == 200
    assert response.body == b"assets page"


@pytest.mark.anyio
async def test_assets_page_redirects_when_menu_permission_is_none(monkeypatch):
    user = {"id": 7, "company_id": 42, "is_super_admin": False}
    membership = {
        "company_id": 42,
        "can_manage_assets": False,
        "menu_permissions": {"menu.assets": "none"},
    }

    async def fake_require_authenticated_user(request):
        return user, None

    async def fake_get_user_company(user_id, company_id):
        return membership

    monkeypatch.setattr(main_module, "_require_authenticated_user", fake_require_authenticated_user)
    monkeypatch.setattr(assets_routes.user_company_repo, "get_user_company", fake_get_user_company)

    response = await assets_routes.assets_page(_request())

    assert response.status_code == 303
    assert response.headers["location"] == "/"
