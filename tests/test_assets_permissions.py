from starlette.requests import Request
from starlette.responses import HTMLResponse
import pytest
from unittest.mock import AsyncMock

from app.features.assets import routes as assets_routes
import app.main as main_module


@pytest.fixture(autouse=True)
def no_hidden_asset_columns(monkeypatch):
    """No asset columns are hidden from end users unless a test says so."""
    monkeypatch.setattr(
        assets_routes.asset_column_restrictions_repo, "list_restricted_keys", AsyncMock(return_value=set())
    )


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
        "role_id": 3,
        "can_manage_assets": False,
        "menu_permissions": {"menu.assets": "read"},
    }

    async def fake_require_authenticated_user(request):
        return user, None

    async def fake_get_user_company(request, user_id, company_id):
        return membership

    async def fake_get_company_by_id(company_id):
        return {"id": company_id, "name": "Acme"}

    async def fake_list_company_assets(company_id):
        return [{"id": 100, "company_id": company_id, "name": "Workstation", "customer_visible": False}]

    async def fake_list_field_definitions():
        return []

    async def fake_get_all_asset_field_values(asset_ids):
        return {}

    async def fake_render_template(template, request, current_user, *, extra=None):
        assert template == "assets/index.html"
        assert extra["company"] == {"id": 42, "name": "Acme"}
        assert [asset["id"] for asset in extra["assets"]] == [100]
        assert extra["can_write_assets"] is False
        assert extra["can_wake"] is False
        assert extra["can_push_rmm_agent"] is False
        return HTMLResponse("assets page")

    monkeypatch.setattr(main_module, "_require_authenticated_user", fake_require_authenticated_user)
    monkeypatch.setattr(main_module, "_get_effective_company_membership", fake_get_user_company)
    monkeypatch.setattr(assets_routes.company_repo, "get_company_by_id", fake_get_company_by_id)
    monkeypatch.setattr(assets_routes.asset_repo, "list_company_assets", fake_list_company_assets)
    monkeypatch.setattr(assets_routes.asset_custom_fields_repo, "list_field_definitions", fake_list_field_definitions)
    monkeypatch.setattr(assets_routes.asset_custom_fields_repo, "get_all_asset_field_values", fake_get_all_asset_field_values)
    monkeypatch.setattr(assets_routes.tray_repo, "list_active_devices_by_asset_ids", AsyncMock(return_value={}))
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

    async def fake_get_user_company(request, user_id, company_id):
        return membership

    monkeypatch.setattr(main_module, "_require_authenticated_user", fake_require_authenticated_user)
    monkeypatch.setattr(main_module, "_get_effective_company_membership", fake_get_user_company)

    response = await assets_routes.assets_page(_request())

    assert response.status_code == 303
    assert response.headers["location"] == "/"


@pytest.mark.anyio
async def test_assets_page_adds_tray_agent_sync_column(monkeypatch):
    user = {"id": 7, "company_id": 42, "is_super_admin": False}
    membership = {
        "company_id": 42,
        "can_manage_assets": False,
        "menu_permissions": {"menu.assets": "read"},
    }

    async def fake_require_authenticated_user(request):
        return user, None

    async def fake_get_user_company(request, user_id, company_id):
        return membership

    async def fake_get_company_by_id(company_id):
        return {"id": company_id, "name": "Acme"}

    async def fake_list_company_assets(company_id):
        return [
            {
                "id": 100,
                "company_id": company_id,
                "name": "Workstation-100",
                "type": "workstation",
                "status": "active",
                "customer_visible": True,
            },
            {
                "id": 200,
                "company_id": company_id,
                "name": "Workstation-200",
                "type": "workstation",
                "status": "active",
                "customer_visible": True,
            },
        ]

    async def fake_list_field_definitions():
        return []

    async def fake_get_all_asset_field_values(asset_ids):
        return {}

    async def fake_list_active_devices_by_asset_ids(asset_ids):
        assert asset_ids == [100, 200]
        return {100: {"id": 1, "asset_id": 100, "device_uid": "tray-100", "hostname": "pc-100"}}

    async def fake_render_template(template, request, current_user, *, extra=None):
        assert template == "assets/index.html"
        column = next(column for column in extra["columns"] if column["key"] == "tray_agent_synced")
        assert column == {
            "key": "tray_agent_synced",
            "label": "TrayAgentID synced",
            "sort": "number",
            "field_type": "checkbox",
        }
        assets_by_id = {asset["id"]: asset for asset in extra["assets"]}
        assert assets_by_id[100]["tray_agent_synced"] is True
        assert assets_by_id[200]["tray_agent_synced"] is False
        # Read Only sees the same register as Read Write, agent versions included.
        keys = {column["key"] for column in extra["columns"]}
        assert "tray_agent_version" in keys
        assert "tray_agent_version" in assets_by_id[100]
        return HTMLResponse("assets page")

    monkeypatch.setattr(main_module, "_require_authenticated_user", fake_require_authenticated_user)
    monkeypatch.setattr(main_module, "_get_effective_company_membership", fake_get_user_company)
    monkeypatch.setattr(assets_routes.company_repo, "get_company_by_id", fake_get_company_by_id)
    monkeypatch.setattr(assets_routes.asset_repo, "list_company_assets", fake_list_company_assets)
    monkeypatch.setattr(assets_routes.asset_custom_fields_repo, "list_field_definitions", fake_list_field_definitions)
    monkeypatch.setattr(assets_routes.asset_custom_fields_repo, "get_all_asset_field_values", fake_get_all_asset_field_values)
    monkeypatch.setattr(assets_routes.tray_repo, "list_active_devices_by_asset_ids", fake_list_active_devices_by_asset_ids)
    monkeypatch.setattr(main_module, "_render_template", fake_render_template)

    response = await assets_routes.assets_page(_request())

    assert response.status_code == 200


@pytest.mark.anyio
async def test_read_only_assets_cannot_update_documentation(monkeypatch):
    membership = {"menu_permissions": {"menu.assets": "read"}}
    monkeypatch.setattr(assets_routes, "_load_asset_context", AsyncMock(
        return_value=({"id": 7}, membership, {"id": 42}, 42, None)
    ))
    update = AsyncMock()
    monkeypatch.setattr(assets_routes.asset_repo, "update_manual_inventory", update)

    with pytest.raises(assets_routes.HTTPException) as excinfo:
        await assets_routes.update_asset_documentation(_request(), 100)

    assert excinfo.value.status_code == 403
    update.assert_not_awaited()
