from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.datastructures import FormData
from starlette.responses import HTMLResponse

import app.main as main_module
from app.features.assets import routes as assets_routes

TEMPLATE = (
    Path(__file__).resolve().parents[1] / "app/templates/devices/index.html"
).read_text(encoding="utf-8")


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


def test_discovered_devices_loads_shared_column_filters():
    assert 'name="interval_minutes" value="360"' in TEMPLATE
    assert 'data-table data-table-id="network-devices"' in TEMPLATE
    assert (
        'table_column_picker("network-devices", discovered_device_columns)' in TEMPLATE
    )
    assert 'data-column-key="hostname"' in TEMPLATE
    assert 'data-column-key="myportal-asset"' in TEMPLATE
    assert "static/js/tables.js" in TEMPLATE
    assert '<th class="table__actions">Actions</th>' in TEMPLATE
    assert 'name="agent_not_required" value="1"' in TEMPLATE
    assert "Agent not required" in TEMPLATE
    assert 'data-column-key="first-seen" data-sort="date">First seen</th>' in TEMPLATE
    assert 'data-column-key="last-seen" data-sort="date">Last seen</th>' in TEMPLATE
    assert "device.first_seen_at.strftime('%Y-%m-%d %H:%M')" in TEMPLATE
    assert "device.last_seen_at.strftime('%Y-%m-%d %H:%M')" in TEMPLATE
    assert 'page_header_overflow("network-device-actions", "Actions"' in TEMPLATE
    assert 'id="device-types-modal" hidden' in TEMPLATE
    assert (
        'data-device-types-close></div>\n  <section class="modal__dialog '
        'modal__dialog--three-quarters">'
    ) in TEMPLATE
    assert 'action="/devices/device-types"' in TEMPLATE
    assert "data-device-select-all" in TEMPLATE
    assert "data-device-bulk-open disabled" in TEMPLATE
    assert 'action="/devices/discovered-bulk-update"' in TEMPLATE
    assert 'action="/devices/discovered-purge"' in TEMPLATE
    assert "Purge out of scope" in TEMPLATE
    assert "data-device-bulk-ids" in TEMPLATE
    assert 'action="/devices/scanners/{{ scanner.id }}/scan"' in TEMPLATE
    assert ">Scan Now</button>" in TEMPLATE
    assert 'name="wan_cidrs"' in TEMPLATE
    assert 'name="local_cidrs"' in TEMPLATE


def test_scanner_scope_accepts_addresses_cidrs_and_commas():
    wan, local = assets_routes._scanner_scope_from_form(
        {"wan_cidrs": "203.0.113.10, 198.51.100.0/24", "local_cidrs": "192.168.7.12/24"}
    )
    assert wan == ["203.0.113.10/32", "198.51.100.0/24"]
    assert local == ["192.168.7.0/24"]


def test_scanner_scope_rejects_ipv6_local_range():
    with pytest.raises(HTTPException) as exc_info:
        assets_routes._scanner_scope_from_form({"local_cidrs": "2001:db8::/64"})
    assert exc_info.value.status_code == 422


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
        assets_routes.network_devices_repo,
        "list_for_company",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        assets_routes.network_devices_repo,
        "list_device_types",
        AsyncMock(return_value=[]),
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

    configure.assert_awaited_once_with(20, 42, True, 45, [], [])
    assert response.status_code == 303
    assert response.headers["location"] == "/devices"


@pytest.mark.anyio
async def test_add_scanner_defaults_to_six_hour_interval(monkeypatch):
    request = _request("/devices/scanners", "POST")
    request._form = {"device_id": "20"}
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

    configure.assert_awaited_once_with(20, 42, True, 360, [], [])
    assert response.status_code == 303


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


@pytest.mark.anyio
async def test_scan_now_sends_command_to_enabled_scanner(monkeypatch):
    request = _request("/devices/scanners/10/scan", "POST")
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
        AsyncMock(
            return_value=[
                {
                    "id": 10,
                    "device_uid": "scanner-uid",
                    "network_scanner_enabled": 1,
                }
            ]
        ),
    )
    send = AsyncMock(return_value=True)
    log_command = AsyncMock()
    monkeypatch.setattr(assets_routes.tray_service, "send_to_device", send)
    monkeypatch.setattr(assets_routes.tray_repo, "log_command", log_command)
    monkeypatch.setattr(
        main_module,
        "flash_redirect",
        lambda url, message, category: HTMLResponse(
            f"{url}|{message}|{category}", status_code=303
        ),
    )

    response = await assets_routes.scan_network_now(request, 10)

    send.assert_awaited_once_with("scanner-uid", {"type": "scan_network"})
    log_command.assert_awaited_once_with(
        device_id=10,
        command="scan_network",
        payload_json='{"type": "scan_network"}',
        initiated_by_user_id=7,
        status="delivered",
    )
    assert response.status_code == 303


@pytest.mark.anyio
async def test_scan_now_rejects_disabled_scanner(monkeypatch):
    request = _request("/devices/scanners/10/scan", "POST")
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
        AsyncMock(return_value=[{"id": 10, "network_scanner_enabled": 0}]),
    )

    with pytest.raises(HTTPException) as exc_info:
        await assets_routes.scan_network_now(request, 10)

    assert exc_info.value.status_code == 404


@pytest.mark.anyio
async def test_update_discovered_device_saves_admin_metadata(monkeypatch):
    request = _request("/devices/discovered/12", "POST")
    request._form = {
        "state": "unknown",
        "device_type_id": "3",
        "description": "Printer beside reception",
        "agent_not_required": "1",
    }
    monkeypatch.setattr(
        assets_routes,
        "_load_asset_context",
        AsyncMock(return_value=({"id": 7, "is_super_admin": True}, None, {}, 42, None)),
    )
    monkeypatch.setattr(
        assets_routes.network_devices_repo,
        "list_device_types",
        AsyncMock(return_value=[{"id": 3, "name": "Printer"}]),
    )
    update = AsyncMock()
    monkeypatch.setattr(assets_routes.network_devices_repo, "update_device", update)

    response = await assets_routes.update_network_device(request, 12)

    update.assert_awaited_once_with(
        12, 42, "Unknown", 3, "Printer beside reception", True
    )
    assert response.status_code == 303


@pytest.mark.anyio
async def test_update_discovered_device_clears_agent_not_required_when_unchecked(
    monkeypatch,
):
    request = _request("/devices/discovered/12", "POST")
    request._form = {"state": "known", "device_type_id": "", "description": ""}
    monkeypatch.setattr(
        assets_routes,
        "_load_asset_context",
        AsyncMock(return_value=({"id": 7, "is_super_admin": True}, None, {}, 42, None)),
    )
    monkeypatch.setattr(
        assets_routes.network_devices_repo,
        "list_device_types",
        AsyncMock(return_value=[]),
    )
    update = AsyncMock()
    monkeypatch.setattr(assets_routes.network_devices_repo, "update_device", update)

    response = await assets_routes.update_network_device(request, 12)

    update.assert_awaited_once_with(12, 42, "Known", None, None, False)
    assert response.status_code == 303


@pytest.mark.anyio
async def test_bulk_update_discovered_devices_applies_selected_action(monkeypatch):
    request = _request("/devices/discovered-bulk-update", "POST")
    request._form = FormData(
        [
            ("device_ids", "12"),
            ("device_ids", "18"),
            ("bulk_action", "state"),
            ("state", "known"),
        ]
    )
    monkeypatch.setattr(
        assets_routes,
        "_load_asset_context",
        AsyncMock(return_value=({"id": 7, "is_super_admin": True}, None, {}, 42, None)),
    )
    update = AsyncMock()
    monkeypatch.setattr(
        assets_routes.network_devices_repo, "bulk_update_devices", update
    )
    monkeypatch.setattr(
        main_module,
        "flash_redirect",
        lambda url, message, category: HTMLResponse(
            f"{url}|{message}|{category}", status_code=303
        ),
    )

    response = await assets_routes.bulk_update_network_devices(request)

    update.assert_awaited_once_with([12, 18], 42, state="Known")
    assert response.status_code == 303
    assert b"Updated 2 discovered devices" in response.body


@pytest.mark.anyio
async def test_bulk_update_discovered_devices_rejects_empty_selection(monkeypatch):
    request = _request("/devices/discovered-bulk-update", "POST")
    request._form = FormData([("bulk_action", "state"), ("state", "Known")])
    monkeypatch.setattr(
        assets_routes,
        "_load_asset_context",
        AsyncMock(return_value=({"id": 7, "is_super_admin": True}, None, {}, 42, None)),
    )

    with pytest.raises(HTTPException) as exc_info:
        await assets_routes.bulk_update_network_devices(request)

    assert exc_info.value.status_code == 422


@pytest.mark.anyio
async def test_purge_discovered_devices_uses_company_scope(monkeypatch):
    request = _request("/devices/discovered-purge", "POST")
    monkeypatch.setattr(
        assets_routes,
        "_load_asset_context",
        AsyncMock(return_value=({"id": 7, "is_super_admin": True}, None, {}, 42, None)),
    )
    purge = AsyncMock(return_value=2)
    monkeypatch.setattr(assets_routes.network_devices_repo, "purge_out_of_scope", purge)
    monkeypatch.setattr(
        main_module,
        "flash_redirect",
        lambda url, message, category: HTMLResponse(
            f"{url}|{message}|{category}", status_code=303
        ),
    )

    response = await assets_routes.purge_network_devices(request)

    purge.assert_awaited_once_with(42)
    assert response.status_code == 303
    assert b"Purged 2 out-of-scope discovered devices" in response.body
