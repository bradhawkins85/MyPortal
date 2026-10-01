"""Regression tests: chat room read/post/revoke routes enforce room access."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.routes import chat as chat_routes
from app.services import chat_access

ROOM = {
    "id": 42,
    "status": "open",
    "matrix_room_id": "!room:example.com",
    "created_by_user_id": 5,
    "company_id": 4,
}


@pytest.fixture
def chat_env(monkeypatch):
    monkeypatch.setattr(chat_routes._settings, "matrix_enabled", True)
    monkeypatch.setattr(chat_routes.chat_repo, "get_room", AsyncMock(return_value=dict(ROOM)))
    monkeypatch.setattr(chat_routes.chat_repo, "get_messages", AsyncMock(return_value=[]))
    monkeypatch.setattr(chat_routes.chat_repo, "get_participants", AsyncMock(return_value=[]))
    monkeypatch.setattr(chat_access.membership_repo, "user_has_permission", AsyncMock(return_value=False))


@pytest.mark.asyncio
async def test_non_participant_cannot_read_room(monkeypatch, chat_env):
    monkeypatch.setattr(chat_access.chat_repo, "get_participant", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as exc:
        await chat_routes.get_room(42, request=None, current_user={"id": 99, "company_id": 4})
    assert exc.value.status_code == 404
    chat_routes.chat_repo.get_messages.assert_not_called()


@pytest.mark.asyncio
async def test_non_participant_cannot_post_message(monkeypatch, chat_env):
    monkeypatch.setattr(chat_access.chat_repo, "get_participant", AsyncMock(return_value=None))
    send = AsyncMock()
    monkeypatch.setattr(chat_routes.matrix_service, "send_message", send)
    with pytest.raises(HTTPException) as exc:
        await chat_routes.send_message(
            42,
            request=None,
            body=chat_routes.ChatMessageCreate(body="hi"),
            current_user={"id": 99, "company_id": 4},
        )
    assert exc.value.status_code == 404
    send.assert_not_called()


@pytest.mark.asyncio
async def test_participant_can_read_room(monkeypatch, chat_env):
    monkeypatch.setattr(
        chat_access.chat_repo, "get_participant", AsyncMock(return_value={"room_id": 42, "user_id": 99})
    )
    response = await chat_routes.get_room(42, request=None, current_user={"id": 99})
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_creator_and_technician_can_read_room(monkeypatch, chat_env):
    monkeypatch.setattr(chat_access.chat_repo, "get_participant", AsyncMock(return_value=None))
    assert (await chat_routes.get_room(42, request=None, current_user={"id": 5})).status_code == 200
    monkeypatch.setattr(chat_access.membership_repo, "user_has_permission", AsyncMock(return_value=True))
    assert (await chat_routes.get_room(42, request=None, current_user={"id": 77})).status_code == 200


@pytest.mark.asyncio
async def test_non_staff_cannot_revoke_invite(monkeypatch, chat_env):
    get_invite = AsyncMock(return_value={"id": 1, "room_id": 42})
    update_invite = AsyncMock()
    monkeypatch.setattr(chat_routes.chat_repo, "get_invite", get_invite)
    monkeypatch.setattr(chat_routes.chat_repo, "update_invite", update_invite)
    with pytest.raises(HTTPException) as exc:
        await chat_routes.revoke_invite("tok", request=None, current_user={"id": 99})
    assert exc.value.status_code == 403
    update_invite.assert_not_called()
