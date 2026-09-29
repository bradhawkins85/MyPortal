"""Handshake authentication for the ``/ws/refresh`` realtime websocket."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from fastapi.websockets import WebSocketDisconnect

from app import main as main_module
from app.security.session import SessionData


def _session(user_id: int = 7) -> SessionData:
    now = datetime.utcnow()
    return SessionData(
        id=1,
        user_id=user_id,
        session_token="tok",
        csrf_token="csrf",
        created_at=now,
        expires_at=now,
        last_seen_at=now,
        ip_address=None,
        user_agent=None,
    )


@pytest.fixture
def client() -> TestClient:
    return TestClient(main_module.app)


def test_refresh_socket_rejects_unauthenticated(client: TestClient) -> None:
    with patch.object(main_module.session_manager, "load_session", AsyncMock(return_value=None)):
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/refresh") as ws:
                ws.receive_text()
    assert exc_info.value.code == 4401


def test_refresh_socket_rejects_cross_origin(client: TestClient) -> None:
    load = AsyncMock(return_value=_session())
    with patch.object(main_module.session_manager, "load_session", load):
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(
                "/ws/refresh", headers={"Origin": "https://evil.example"}
            ) as ws:
                ws.receive_text()
    assert exc_info.value.code == 4403
    load.assert_not_called()


def test_refresh_socket_accepts_same_origin_session(client: TestClient) -> None:
    connect = AsyncMock()
    with patch.object(main_module.session_manager, "load_session", AsyncMock(return_value=_session())), patch.object(
        main_module.user_repo, "get_user_by_id", AsyncMock(return_value={"id": 7, "is_super_admin": False})
    ), patch.object(main_module, "_is_helpdesk_technician", AsyncMock(return_value=False)), patch.object(
        main_module.refresh_notifier, "connect", connect
    ), patch.object(main_module.refresh_notifier, "disconnect", AsyncMock()):
        async def _accept(websocket, *, access=None):
            await websocket.accept()

        connect.side_effect = _accept
        with client.websocket_connect("/ws/refresh", headers={"Origin": "http://testserver"}):
            pass
    access = connect.call_args.kwargs["access"]
    assert access.user_id == 7
    assert access.is_privileged is False
