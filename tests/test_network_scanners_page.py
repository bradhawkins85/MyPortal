from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import HTMLResponse

import app.main as main_module
from app.features.assets import routes as assets_routes


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _request(path: str = "/devices", method: str = "GET") -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": b"",
            "headers": [],
        }
    )


@pytest.mark.anyio
async def test_devices_page_separates_enabled_and_available_scanners(monkeypatch):
    scanner_assets = [
        {"id": 10, "asset_name": "Enabled PC", "network_scanner_enabled": 1},
        {"id": 20, "asset_name": "Available PC", "network_scanner_enabled": 0},
    ]
    monkeypatch.setattr(
        assets_routes,
        "_load_asset_context",
        AsyncMock(
            return_value=(
                {"id": 7, "is_super_admin": True},
                None,
                {"id": 42},
                42,
                None,
            )
        ),
    )
    monkeypatch.setattr(
        assets_routes.network_devices_repo,
        "list_scanners",
        AsyncMock(return_value=scanner_assets),
    )
    monkeypatch.setattr(
        assets_routes.network_devices_repo, "list_for_company", AsyncMock(return_value=[])
    )

    async def render(template, request, user, *, extra=None):
        assert template == "devices/index.html"
        assert extra["scanners"] == [scanner_assets[0]]
        assert extra["available_scanners"] == [scanner_assets[1]]
        return HTMLResponse("devices")

    monkeypatch.setattr(main_module, "_render_template", render)

    response = await assets_routes.network_devices_page(_request())

    assert response.status_code == 200


@pytest.mark.anyio
async def test_add_scanner_enables_selected_asset(monkeypatch):
    request = _request("/devices/scanners", "POST")
    request._form = {"device_id": "20", "interval_minutes": "45"}
    monkeypatch.setattr(
        assets_routes,
        "_load_asset_context",
        AsyncMock(
            return_value=(
                {"id": 7, "is_super_admin": True},
                None,
                {"id": 42},
                42,
                None,
            )
        ),
    )
    configure = AsyncMock()
    monkeypatch.setattr(
        assets_routes.network_devices_repo, "configure_scanner", configure
    )

    response = await assets_routes.add_network_scanner(request)

    configure.assert_awaited_once_with(20, 42, True, 45)
    assert response.status_code == 303
    assert response.headers["location"] == "/devices"


@pytest.mark.anyio
async def test_add_scanner_requires_asset_selection(monkeypatch):
    request = _request("/devices/scanners", "POST")
    request._form = {"device_id": "", "interval_minutes": "60"}
    monkeypatch.setattr(
        assets_routes,
        "_load_asset_context",
        AsyncMock(
            return_value=(
                {"id": 7, "is_super_admin": True},
                None,
                {"id": 42},
                42,
                None,
            )
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        await assets_routes.add_network_scanner(request)

    assert exc_info.value.status_code == 422
