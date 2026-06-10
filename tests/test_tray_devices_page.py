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


@pytest.mark.anyio
async def test_start_device_chat_reuses_existing_open_tray_room(monkeypatch):
    import app.api.routes.tray as tray_routes
    import app.repositories.chat as chat_repo
    import app.repositories.companies as companies_repo
    import app.repositories.tray as tray_repo
    from app.schemas.tray import TrayChatStartRequest
    from app.services import audit as audit_service
    from app.services import matrix as matrix_service
    from app.services import tray as tray_service

    monkeypatch.setattr(
        tray_routes,
        "_settings",
        type(
            "Settings",
            (),
            {"matrix_enabled": True, "matrix_bot_user_id": "@bot:example"},
        )(),
    )
    monkeypatch.setattr(
        tray_repo,
        "get_device_by_uid",
        AsyncMock(
            return_value={
                "id": 7,
                "device_uid": "dev-7",
                "status": "active",
                "company_id": 3,
            }
        ),
    )
    monkeypatch.setattr(
        companies_repo, "get_company_by_id", AsyncMock(return_value={"id": 3})
    )
    monkeypatch.setattr(
        tray_service, "technician_can_initiate", lambda user, company: True
    )

    existing_room = {
        "id": 42,
        "matrix_room_id": "!existing:example",
        "status": "open",
        "company_id": 3,
        "tray_device_id": 7,
    }
    monkeypatch.setattr(
        chat_repo, "get_open_room_by_device_id", AsyncMock(return_value=existing_room)
    )
    create_matrix_mock = AsyncMock(return_value={"room_id": "!new:example"})
    create_room_mock = AsyncMock()
    attach_mock = AsyncMock()
    monkeypatch.setattr(matrix_service, "create_room", create_matrix_mock)
    monkeypatch.setattr(chat_repo, "create_room", create_room_mock)
    monkeypatch.setattr(tray_routes, "_attach_room_to_device", attach_mock)
    monkeypatch.setattr(tray_service, "send_to_device", AsyncMock(return_value=True))
    monkeypatch.setattr(tray_repo, "log_command", AsyncMock())
    monkeypatch.setattr(audit_service, "log_action", AsyncMock())

    response = await tray_routes.start_device_chat(
        "dev-7",
        TrayChatStartRequest(subject="Help me"),
        {"id": 99, "email": "tech@example.test", "display_name": "Tech"},
    )

    assert response.room_id == 42
    assert response.matrix_room_id == "!existing:example"
    assert response.delivered is True
    create_matrix_mock.assert_not_called()
    create_room_mock.assert_not_called()
    attach_mock.assert_not_called()


@pytest.mark.anyio
async def test_tray_chat_popup_reuses_existing_open_room_from_unbound_token(monkeypatch):
    import app.features.chat.routes as chat_routes
    import app.repositories.chat as chat_repo
    import app.repositories.companies as companies_repo
    import app.repositories.tray as tray_repo
    from app.services import matrix as matrix_service

    monkeypatch.setattr(
        chat_routes,
        "get_settings",
        lambda: type(
            "Settings", (), {"matrix_enabled": True, "environment": "development"}
        )(),
    )
    monkeypatch.setattr(
        chat_routes.tray_service, "hash_token", lambda token: f"hash:{token}"
    )
    monkeypatch.setattr(
        tray_repo,
        "get_chat_token_by_hash",
        AsyncMock(
            return_value={
                "id": 11,
                "device_id": 7,
                "room_id": None,
                "expires_at": None,
                "used_at": None,
            }
        ),
    )
    monkeypatch.setattr(
        tray_repo,
        "get_device_by_id",
        AsyncMock(
            return_value={
                "id": 7,
                "device_uid": "dev-7",
                "status": "active",
                "company_id": 3,
                "hostname": "PC-7",
            }
        ),
    )
    monkeypatch.setattr(
        companies_repo,
        "get_company_by_id",
        AsyncMock(return_value={"id": 3, "tray_chat_enabled": True}),
    )
    monkeypatch.setattr(tray_repo, "mark_chat_token_used", AsyncMock())

    existing_room = {
        "id": 42,
        "matrix_room_id": "!existing:example",
        "status": "open",
        "company_id": 3,
        "tray_device_id": 7,
        "subject": "Chat from PC-7",
    }
    monkeypatch.setattr(chat_repo, "get_room", AsyncMock())
    monkeypatch.setattr(
        chat_repo, "get_open_room_by_device_id", AsyncMock(return_value=existing_room)
    )
    create_matrix_mock = AsyncMock(return_value={"room_id": "!new:example"})
    create_room_mock = AsyncMock()
    monkeypatch.setattr(matrix_service, "create_room", create_matrix_mock)
    monkeypatch.setattr(chat_repo, "create_room", create_room_mock)
    monkeypatch.setattr(chat_repo, "get_messages", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        chat_routes, "_make_popup_session", lambda **kwargs: "cookie-value"
    )
    monkeypatch.setattr(
        chat_routes, "_render_popup", lambda **kwargs: f"room={kwargs['room_id']}"
    )

    request = _make_request("/tray/chat", method="GET")
    response = await chat_routes.tray_chat_popup(request, None, token="abc", room=None)

    assert response.status_code == 200
    assert response.body == b"room=42"
    create_matrix_mock.assert_not_called()
    create_room_mock.assert_not_called()
