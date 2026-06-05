from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.responses import HTMLResponse
from starlette.requests import Request

from app.features.chat import routes as chat_routes
from app.security.session import SessionData


async def _dummy_receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


def _make_request(path: str = "/chat", query_string: str = "") -> Request:
    scope = {"type": "http", "method": "GET", "path": path, "headers": [], "query_string": query_string.encode()}
    return Request(scope, _dummy_receive)


def _session(user_id: int = 10) -> SessionData:
    now = datetime.now(timezone.utc)
    return SessionData(
        id=1,
        user_id=user_id,
        session_token="token",
        csrf_token="csrf",
        created_at=now,
        expires_at=now + timedelta(hours=1),
        last_seen_at=now,
        ip_address=None,
        user_agent=None,
    )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_chat_index_defaults_to_open_only(monkeypatch):
    request = _make_request()
    session = _session()

    monkeypatch.setattr(chat_routes, "get_settings", lambda: SimpleNamespace(matrix_enabled=True))
    monkeypatch.setattr(
        chat_routes.user_repo,
        "get_user_by_id",
        AsyncMock(return_value={"id": session.user_id, "company_id": 4, "is_super_admin": 0, "is_helpdesk_technician": 0}),
    )
    monkeypatch.setattr(
        chat_routes.user_company_repo,
        "get_user_company",
        AsyncMock(return_value={"can_access_chat": 1}),
    )
    list_rooms = AsyncMock(return_value=[])
    monkeypatch.setattr(chat_routes.chat_repo, "list_rooms", list_rooms)

    captured: dict[str, Any] = {}

    async def fake_render_template(template_name, request_obj, user_obj, *, extra):
        captured["template"] = template_name
        captured["extra"] = extra
        return HTMLResponse("OK")

    monkeypatch.setattr(chat_routes, "_main", lambda: SimpleNamespace(_render_template=fake_render_template))

    response = await chat_routes.chat_index(request, session=session)

    assert isinstance(response, HTMLResponse)
    list_rooms.assert_awaited_once()
    assert list_rooms.await_args.kwargs["status"] == "open"
    assert captured["template"] == "chat/index.html"
    assert captured["extra"]["show_closed_filter"] is False


@pytest.mark.anyio("asyncio")
async def test_chat_index_show_closed_filter_includes_closed(monkeypatch):
    request = _make_request()
    session = _session()

    monkeypatch.setattr(chat_routes, "get_settings", lambda: SimpleNamespace(matrix_enabled=True))
    monkeypatch.setattr(
        chat_routes.user_repo,
        "get_user_by_id",
        AsyncMock(return_value={"id": session.user_id, "company_id": 4, "is_super_admin": 0, "is_helpdesk_technician": 0}),
    )
    monkeypatch.setattr(
        chat_routes.user_company_repo,
        "get_user_company",
        AsyncMock(return_value={"can_access_chat": 1}),
    )
    list_rooms = AsyncMock(return_value=[])
    monkeypatch.setattr(chat_routes.chat_repo, "list_rooms", list_rooms)

    captured: dict[str, Any] = {}

    async def fake_render_template(template_name, request_obj, user_obj, *, extra):
        captured["template"] = template_name
        captured["extra"] = extra
        return HTMLResponse("OK")

    monkeypatch.setattr(chat_routes, "_main", lambda: SimpleNamespace(_render_template=fake_render_template))

    response = await chat_routes.chat_index(request, show_closed="1", session=session)

    assert isinstance(response, HTMLResponse)
    list_rooms.assert_awaited_once()
    assert list_rooms.await_args.kwargs["status"] is None
    assert captured["template"] == "chat/index.html"
    assert captured["extra"]["show_closed_filter"] is True
