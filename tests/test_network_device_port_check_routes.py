import json
from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.main as main_module
from app.features.assets import routes as assets_routes


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


DEVICE = {
    "id": 9,
    "ip_address": "192.168.1.20",
    "hostname": "nas",
    "scanner_tray_device_id": 3,
    "scanner_device_uid": "uid-3",
    "scanner_enabled": 1,
    "scanner_name": "Office PC",
}


def _request(method: str = "POST", *, json_accept: bool = True) -> Request:
    headers = [(b"accept", b"application/json")] if json_accept else []
    return Request(
        {
            "type": "http",
            "method": method,
            "path": "/devices/discovered/9/port-check",
            "query_string": b"",
            "headers": headers,
        }
    )


@pytest.fixture
def context(monkeypatch):
    monkeypatch.setattr(
        assets_routes,
        "_load_asset_context",
        AsyncMock(return_value=({"id": 7, "is_super_admin": True}, None, {"id": 42}, 42, None)),
    )
    repo = assets_routes.network_devices_repo
    mocks = {
        "get_device": AsyncMock(return_value=dict(DEVICE)),
        "create": AsyncMock(return_value=55),
        "set_command": AsyncMock(),
        "latest": AsyncMock(
            return_value={
                "id": 55,
                "status": "sent",
                "ports": "22,8000-8002",
                "open_ports": None,
                "checked_count": 0,
                "error": None,
                "requested_at": datetime(2026, 11, 23, 2, 0, 0),
                "completed_at": None,
            }
        ),
        "log_command": AsyncMock(return_value=77),
        "mark_delivered": AsyncMock(),
        "send": AsyncMock(return_value=True),
    }
    monkeypatch.setattr(repo, "get_device_for_port_check", mocks["get_device"])
    monkeypatch.setattr(repo, "create_port_check", mocks["create"])
    monkeypatch.setattr(repo, "set_port_check_command", mocks["set_command"])
    monkeypatch.setattr(repo, "get_latest_port_check", mocks["latest"])
    monkeypatch.setattr(assets_routes.tray_repo, "log_command", mocks["log_command"])
    monkeypatch.setattr(assets_routes.tray_repo, "mark_command_delivered", mocks["mark_delivered"])
    monkeypatch.setattr(assets_routes.tray_service, "send_to_device", mocks["send"])
    return mocks


@pytest.mark.anyio
async def test_port_check_sends_command_to_scanner(context):
    request = _request()
    request._form = {"ports": "8000-8002, 22"}

    response = await assets_routes.start_discovered_device_port_check(request, 9)

    context["create"].assert_awaited_once_with(
        company_id=42,
        network_device_id=9,
        scanner_tray_device_id=3,
        ip_address="192.168.1.20",
        ports="22,8000-8002",
        requested_by_user_id=7,
    )
    payload = context["send"].await_args.args[1]
    assert context["send"].await_args.args[0] == "uid-3"
    assert payload == {
        "type": "check_ports",
        "command_id": 77,
        "check_id": 55,
        "ip_address": "192.168.1.20",
        "ports": [22, 8000, 8001, 8002],
    }
    logged = json.loads(context["log_command"].await_args.kwargs["payload_json"])
    assert "command_id" not in logged
    assert {**logged, "command_id": 77} == payload
    context["mark_delivered"].assert_awaited_once_with(77)
    context["set_command"].assert_awaited_once_with(55, 77, "sent")
    body = json.loads(response.body)
    assert body["delivered"] is True
    assert body["check"]["ports"] == [22, 8000, 8001, 8002]
    assert body["check"]["requested_at"] == "2026-11-23T02:00:00Z"


@pytest.mark.anyio
async def test_port_check_is_queued_when_scanner_offline(context):
    context["send"].return_value = False
    request = _request()
    request._form = {"ports": "443"}

    response = await assets_routes.start_discovered_device_port_check(request, 9)

    context["set_command"].assert_awaited_once_with(55, 77, "queued")
    context["mark_delivered"].assert_not_awaited()
    assert context["log_command"].await_args.kwargs["status"] == "queued"
    assert json.loads(response.body)["delivered"] is False


@pytest.mark.anyio
async def test_port_check_rejects_bad_ports_without_creating(context):
    request = _request()
    request._form = {"ports": "100-50"}

    response = await assets_routes.start_discovered_device_port_check(request, 9)

    assert response.status_code == 422
    assert "backwards" in json.loads(response.body)["detail"]
    context["create"].assert_not_awaited()
    context["send"].assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("device", [None, {**DEVICE, "scanner_enabled": 0}])
async def test_port_check_requires_enabled_scanner_in_company(context, device):
    context["get_device"].return_value = device
    request = _request()
    request._form = {"ports": "443"}

    with pytest.raises(HTTPException) as exc_info:
        await assets_routes.start_discovered_device_port_check(request, 9)

    assert exc_info.value.status_code == 404
    context["get_device"].assert_awaited_once_with(42, 9)
    context["create"].assert_not_awaited()


@pytest.mark.anyio
async def test_port_check_requires_write_access(context, monkeypatch):
    membership = {"menu_permissions": {"menu.network_devices": "read"}}
    monkeypatch.setattr(
        assets_routes,
        "_load_asset_context",
        AsyncMock(return_value=({"id": 7, "is_super_admin": False}, membership, {"id": 42}, 42, None)),
    )
    request = _request()
    request._form = {"ports": "443"}

    with pytest.raises(HTTPException) as exc_info:
        await assets_routes.start_discovered_device_port_check(request, 9)

    assert exc_info.value.status_code == 403
    context["create"].assert_not_awaited()


@pytest.mark.anyio
async def test_port_check_without_js_redirects_with_flash(context, monkeypatch):
    calls = []

    def flash_redirect(url, message, kind):
        calls.append((url, message, kind))
        return "redirect"

    monkeypatch.setattr(main_module, "flash_redirect", flash_redirect)
    request = _request(json_accept=False)
    request._form = {"ports": "443"}

    assert await assets_routes.start_discovered_device_port_check(request, 9) == "redirect"
    assert calls[0][0] == "/devices#discovered-device-9"
    assert calls[0][2] == "success"


@pytest.mark.anyio
async def test_get_port_check_serialises_latest(context):
    context["latest"].return_value = {
        "id": 55,
        "status": "completed",
        "ports": "22,443",
        "open_ports": "443",
        "checked_count": 2,
        "error": None,
        "requested_at": datetime(2026, 11, 23, 2, 0, 0),
        "completed_at": datetime(2026, 11, 23, 2, 0, 5),
    }

    response = await assets_routes.get_discovered_device_port_check(_request("GET"), 9)

    check = json.loads(response.body)["check"]
    assert check["open_ports"] == [443]
    assert check["ports"] == [22, 443]
    assert check["completed_at"] == "2026-11-23T02:00:05Z"
    context["latest"].assert_awaited_once_with(42, 9)
