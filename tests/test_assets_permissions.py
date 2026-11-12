from starlette.requests import Request
from starlette.responses import HTMLResponse
import pytest
from unittest.mock import AsyncMock

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

    async def fake_get_user_company(request, user_id, company_id):
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
    monkeypatch.setattr(main_module, "_get_effective_company_membership", fake_get_user_company)
    monkeypatch.setattr(assets_routes.company_repo, "get_company_by_id", fake_get_company_by_id)
    monkeypatch.setattr(assets_routes.asset_repo, "list_company_assets", fake_list_company_assets)
    monkeypatch.setattr(assets_routes.asset_custom_fields_repo, "list_field_definitions", fake_list_field_definitions)
    monkeypatch.setattr(assets_routes.asset_custom_fields_repo, "get_all_asset_field_values", fake_get_all_asset_field_values)
    monkeypatch.setattr(main_module, "_render_template", fake_render_template)

    response = await assets_routes.assets_page(_request())

    assert response.status_code == 200
    assert response.body == b"assets page"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("super_admin", "role_permissions", "legacy_admin", "expected_internal"),
    [
        (False, {"menu.assets": "read"}, False, False),
        (False, {"menu.assets": "write"}, False, False),
        (False, {"menu.assets": "write", "menu.tickets": "write"}, False, False),
        (False, {"menu.assets": "write", "menu.rmm_scripts": "write"}, False, False),
        (False, {"menu.assets": "write", "menu.admin.company": "read"}, False, False),
        (False, {"menu.assets": "read", "menu.admin.company": "write"}, False, True),
        (False, {"menu.assets": "write", "menu.admin.technician": "write"}, False, True),
        (False, {"menu.assets": "write"}, True, True),
        (True, {}, False, True),
    ],
)
async def test_internal_asset_columns_require_admin_or_technician(
    monkeypatch, super_admin, role_permissions, legacy_admin, expected_internal
):
    user = {"id": 7, "company_id": 42, "is_super_admin": super_admin}
    membership = {"company_id": 42, "menu_permissions": role_permissions, "is_admin": legacy_admin}
    record = {"id": 100, "company_id": 42, "name": "Laptop", "type": "workstation",
              "cpu_name": "Internal CPU", "operational_notes": "Private notes", "customer_visible": True}
    monkeypatch.setattr(assets_routes, "_load_asset_context", AsyncMock(return_value=(user, membership, {"id": 42}, 42, None)))
    monkeypatch.setattr(assets_routes.asset_repo, "list_company_assets", AsyncMock(return_value=[record]))
    monkeypatch.setattr(assets_routes, "_customer_visible_assets", AsyncMock(return_value=[record]))
    definitions = AsyncMock(return_value=[{"id": 1, "name": "private", "display_name": "Private field", "field_type": "text"}])
    values = AsyncMock(return_value={100: {1: "Secret custom value"}})
    devices = AsyncMock(return_value={100: {"device_uid": "private-device-id", "agent_version": "1.2.3"}})
    monkeypatch.setattr(assets_routes.asset_custom_fields_repo, "list_field_definitions", definitions)
    monkeypatch.setattr(assets_routes.asset_custom_fields_repo, "get_all_asset_field_values", values)
    monkeypatch.setattr(assets_routes.tray_repo, "list_active_devices_by_asset_ids", devices)
    monkeypatch.setattr(main_module, "_feature_pack_available", lambda _key: False)
    monkeypatch.setattr(main_module, "_is_helpdesk_technician", AsyncMock(return_value=True))
    renderer = AsyncMock(return_value=HTMLResponse("assets page"))
    monkeypatch.setattr(main_module, "_render_template", renderer)

    await assets_routes.assets_page(_request())

    extra = renderer.await_args.kwargs["extra"]
    keys = {column["key"] for column in extra["columns"]}
    asset = extra["assets"][0]
    assert ("cpu_name" in keys) is expected_internal
    assert ("tray_agent_synced" in keys) is expected_internal
    assert ("cf_1" in keys) is expected_internal
    if expected_internal:
        assert asset["cf_1"] == "Secret custom value"
        assert asset["cpu_name"] == "Internal CPU"
    else:
        assert keys <= set(assets_routes._CUSTOMER_ASSET_FIELDS)
        assert set(asset) == set(assets_routes._CUSTOMER_ASSET_FIELDS)
        definitions.assert_not_awaited()
        values.assert_not_awaited()
        devices.assert_not_awaited()


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
async def test_assets_page_omits_internal_columns_for_read_only_users(monkeypatch):
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
        keys = {column["key"] for column in extra["columns"]}
        assert keys <= set(assets_routes._CUSTOMER_ASSET_FIELDS)
        assert "tray_agent_synced" not in keys
        assert all(set(asset) == set(assets_routes._CUSTOMER_ASSET_FIELDS) for asset in extra["assets"])
        return HTMLResponse("assets page")

    monkeypatch.setattr(main_module, "_require_authenticated_user", fake_require_authenticated_user)
    monkeypatch.setattr(main_module, "_get_effective_company_membership", fake_get_user_company)
    monkeypatch.setattr(assets_routes.company_repo, "get_company_by_id", fake_get_company_by_id)
    monkeypatch.setattr(assets_routes.asset_repo, "list_company_assets", fake_list_company_assets)
    monkeypatch.setattr(assets_routes.asset_custom_fields_repo, "list_field_definitions", fake_list_field_definitions)
    monkeypatch.setattr(assets_routes.asset_custom_fields_repo, "get_all_asset_field_values", fake_get_all_asset_field_values)
    monkeypatch.setattr(assets_routes.tray_repo, "list_active_devices_by_asset_ids", fake_list_active_devices_by_asset_ids)
    monkeypatch.setattr(assets_routes, "_customer_visible_assets", AsyncMock(side_effect=lambda _membership, _company_id, rows: [row for row in rows if row.get("customer_visible")]))
    monkeypatch.setattr(main_module, "_render_template", fake_render_template)

    response = await assets_routes.assets_page(_request())

    assert response.status_code == 200
