from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app import main


async def _dummy_receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


def _make_request(path: str, method: str = "POST") -> Request:
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": [],
        "scheme": "http",
        "server": ("testserver", 80),
    }
    return Request(scope, _dummy_receive)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_tray_device_delete_requires_revoked_status(monkeypatch):
    import app.repositories.tray as tray_repo

    monkeypatch.setattr(
        main, "_require_super_admin_page", AsyncMock(return_value=({"id": 1}, None))
    )
    monkeypatch.setattr(
        tray_repo, "get_device_by_id", AsyncMock(return_value={"id": 15, "status": "active"})
    )
    delete_mock = AsyncMock()
    monkeypatch.setattr(tray_repo, "delete_device", delete_mock)

    response = await main.admin_tray_delete_device(
        15, _make_request("/admin/tray/devices/15/delete")
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/tray/devices"
    delete_mock.assert_not_called()


@pytest.mark.anyio
async def test_tray_device_delete_allows_revoked_status(monkeypatch):
    import app.repositories.tray as tray_repo

    monkeypatch.setattr(
        main, "_require_super_admin_page", AsyncMock(return_value=({"id": 1}, None))
    )
    monkeypatch.setattr(
        tray_repo, "get_device_by_id", AsyncMock(return_value={"id": 15, "status": "revoked"})
    )
    delete_mock = AsyncMock()
    monkeypatch.setattr(tray_repo, "delete_device", delete_mock)

    response = await main.admin_tray_delete_device(
        15, _make_request("/admin/tray/devices/15/delete")
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/tray/devices"
    delete_mock.assert_awaited_once_with(15)


@pytest.mark.anyio
async def test_tray_bulk_delete_revoked_devices_calls_repo(monkeypatch):
    import app.repositories.tray as tray_repo

    monkeypatch.setattr(
        main, "_require_super_admin_page", AsyncMock(return_value=({"id": 1}, None))
    )
    delete_mock = AsyncMock(return_value=3)
    monkeypatch.setattr(tray_repo, "delete_revoked_devices", delete_mock)

    response = await main.admin_tray_bulk_delete_revoked_devices(
        _make_request("/admin/tray/devices/bulk-delete-revoked")
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/tray/devices"
    delete_mock.assert_awaited_once()
