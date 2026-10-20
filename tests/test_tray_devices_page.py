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
    assert response.headers["location"] == "/admin/tray#devices"
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
    assert response.headers["location"] == "/admin/tray#devices"
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
    assert response.headers["location"] == "/admin/tray#devices"
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


@pytest.mark.anyio
async def test_tray_chat_popup_rejects_closed_explicit_room_without_creating_new_room(monkeypatch):
    import app.features.chat.routes as chat_routes
    import app.repositories.chat as chat_repo
    import app.repositories.companies as companies_repo
    import app.repositories.tray as tray_repo
    from app.services import matrix as matrix_service
    from fastapi import HTTPException

    monkeypatch.setattr(
        chat_routes,
        "get_settings",
        lambda: type("Settings", (), {"matrix_enabled": True, "environment": "development"})(),
    )
    monkeypatch.setattr(chat_routes.tray_service, "hash_token", lambda token: f"hash:{token}")
    monkeypatch.setattr(
        tray_repo,
        "get_chat_token_by_hash",
        AsyncMock(
            return_value={
                "id": 11,
                "device_id": 7,
                "room_id": 42,
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
    monkeypatch.setattr(
        chat_repo,
        "get_room",
        AsyncMock(return_value={"id": 42, "status": "closed", "tray_device_id": 7, "company_id": 3}),
    )
    get_open_mock = AsyncMock()
    create_matrix_mock = AsyncMock(return_value={"room_id": "!new:example"})
    create_room_mock = AsyncMock()
    monkeypatch.setattr(chat_repo, "get_open_room_by_device_id", get_open_mock)
    monkeypatch.setattr(matrix_service, "create_room", create_matrix_mock)
    monkeypatch.setattr(chat_repo, "create_room", create_room_mock)

    request = _make_request("/tray/chat", method="GET")
    with pytest.raises(HTTPException) as exc:
        await chat_routes.tray_chat_popup(request, None, token="abc", room=None)

    assert exc.value.status_code == 409
    assert exc.value.detail == "Chat room is closed"
    get_open_mock.assert_not_called()
    create_matrix_mock.assert_not_called()
    create_room_mock.assert_not_called()


@pytest.mark.anyio
async def test_issue_chat_token_rejects_closed_explicit_room(monkeypatch):
    import app.api.routes.tray as tray_routes
    import app.repositories.chat as chat_repo
    import app.repositories.companies as companies_repo
    import app.repositories.tray as tray_repo
    from fastapi import HTTPException

    class JsonRequest:
        async def json(self):
            return {"room_id": 42}

    monkeypatch.setattr(tray_routes._settings, "matrix_enabled", True)
    monkeypatch.setattr(companies_repo, "get_company_by_id", AsyncMock(return_value={"id": 3, "tray_chat_enabled": True}))
    monkeypatch.setattr(chat_repo, "get_room", AsyncMock(return_value={"id": 42, "status": "closed", "company_id": 3}))
    create_token_mock = AsyncMock()
    monkeypatch.setattr(tray_repo, "create_chat_token", create_token_mock)

    with pytest.raises(HTTPException) as exc:
        await tray_routes.issue_chat_token(
            JsonRequest(),
            {"id": 7, "device_uid": "dev-7", "company_id": 3, "status": "active"},
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == "Chat room is closed"
    create_token_mock.assert_not_called()


@pytest.mark.anyio
async def test_tray_bulk_update_outdated_devices_sends_tray_update_command(monkeypatch):
    import app.repositories.tray as tray_repo
    from app.services import tray as tray_service
    from app.services import tray_installer as tray_installer_service

    monkeypatch.setattr(
        main, "_require_super_admin_page", AsyncMock(return_value=({"id": 1}, None))
    )
    monkeypatch.setattr(
        tray_installer_service,
        "get_cached_latest_release_info",
        lambda: {"version": "2.0.0"},
    )
    monkeypatch.setattr(
        tray_repo,
        "list_devices",
        AsyncMock(
            return_value=[
                {"id": 1, "device_uid": "u1", "status": "active", "agent_version": "1.0.0"},
                {"id": 2, "device_uid": "u2", "status": "active", "agent_version": "2.0.0"},
                {"id": 3, "device_uid": "u3", "status": "offline", "agent_version": None},
                {"id": 4, "device_uid": "u4", "status": "revoked", "agent_version": "1.0.0"},
            ]
        ),
    )
    monkeypatch.setattr(tray_repo, "get_queued_commands_for_device", AsyncMock(return_value=[]))
    log_mock = AsyncMock(side_effect=[101, 102])
    monkeypatch.setattr(tray_repo, "log_command", log_mock)
    mark_mock = AsyncMock()
    monkeypatch.setattr(tray_repo, "mark_command_delivered", mark_mock)
    send_mock = AsyncMock(side_effect=[True, False])
    monkeypatch.setattr(tray_service, "send_to_device", send_mock)

    response = await main.admin_tray_bulk_update_outdated_devices(
        _make_request("/admin/tray/devices/bulk-update-outdated")
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/tray#devices"
    tray_repo.list_devices.assert_awaited_once_with()
    assert [call.kwargs["device_id"] for call in log_mock.await_args_list] == [1, 3]
    assert all(call.kwargs["command"] == "update" for call in log_mock.await_args_list)
    assert all(call.kwargs["status"] == "queued" for call in log_mock.await_args_list)
    sent = [call.args for call in send_mock.await_args_list]
    assert sent[0][0] == "u1"
    assert sent[0][1]["type"] == "update"
    assert sent[0][1]["command_id"] == 101
    assert sent[1][0] == "u3"
    # Only the live delivery is marked delivered; the offline one stays queued.
    mark_mock.assert_awaited_once_with(101)


@pytest.mark.anyio
async def test_tray_update_device_sends_tray_update_command(monkeypatch):
    import app.repositories.tray as tray_repo
    from app.services import tray as tray_service

    monkeypatch.setattr(
        main, "_require_super_admin_page", AsyncMock(return_value=({"id": 7}, None))
    )
    monkeypatch.setattr(
        tray_repo,
        "get_device_by_id",
        AsyncMock(return_value={"id": 5, "device_uid": "dev-5", "status": "active"}),
    )
    monkeypatch.setattr(tray_repo, "get_queued_commands_for_device", AsyncMock(return_value=[]))
    log_mock = AsyncMock(return_value=55)
    monkeypatch.setattr(tray_repo, "log_command", log_mock)
    mark_mock = AsyncMock()
    monkeypatch.setattr(tray_repo, "mark_command_delivered", mark_mock)
    send_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(tray_service, "send_to_device", send_mock)

    response = await main.admin_tray_update_device(5, _make_request("/admin/tray/devices/5/update"))

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/tray#devices"
    log_mock.assert_awaited_once()
    assert log_mock.await_args.kwargs["command"] == "update"
    assert log_mock.await_args.kwargs["initiated_by_user_id"] == 7
    send_mock.assert_awaited_once_with("dev-5", {"type": "update", "command_id": 55})
    mark_mock.assert_awaited_once_with(55)


@pytest.mark.anyio
async def test_tray_update_device_does_not_stack_queued_updates(monkeypatch):
    import app.repositories.tray as tray_repo
    from app.services import tray as tray_service

    monkeypatch.setattr(
        main, "_require_super_admin_page", AsyncMock(return_value=({"id": 7}, None))
    )
    monkeypatch.setattr(
        tray_repo,
        "get_device_by_id",
        AsyncMock(return_value={"id": 5, "device_uid": "dev-5", "status": "active"}),
    )
    monkeypatch.setattr(
        tray_repo,
        "get_queued_commands_for_device",
        AsyncMock(return_value=[{"id": 9, "command": "update"}]),
    )
    log_mock = AsyncMock()
    monkeypatch.setattr(tray_repo, "log_command", log_mock)
    send_mock = AsyncMock()
    monkeypatch.setattr(tray_service, "send_to_device", send_mock)

    response = await main.admin_tray_update_device(5, _make_request("/admin/tray/devices/5/update"))

    assert response.status_code == 303
    log_mock.assert_not_called()
    send_mock.assert_not_called()


@pytest.mark.anyio
async def test_tray_bulk_update_outdated_devices_requires_loaded_release(monkeypatch):
    import app.repositories.tray as tray_repo
    from app.services import tray_installer as tray_installer_service

    monkeypatch.setattr(
        main, "_require_super_admin_page", AsyncMock(return_value=({"id": 1}, None))
    )
    monkeypatch.setattr(
        tray_installer_service,
        "get_cached_latest_release_info",
        lambda: {"version": None},
    )
    list_mock = AsyncMock(return_value=[])
    monkeypatch.setattr(tray_repo, "list_devices", list_mock)

    response = await main.admin_tray_bulk_update_outdated_devices(
        _make_request("/admin/tray/devices/bulk-update-outdated")
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/tray#devices"
    list_mock.assert_not_called()
