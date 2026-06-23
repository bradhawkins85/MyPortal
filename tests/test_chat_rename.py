from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.api.routes import chat as chat_routes


def test_rename_room_allows_technician_and_updates_subject(monkeypatch):
    async def run_test():
        monkeypatch.setattr(chat_routes._settings, "matrix_enabled", True)
        room = {
            "id": 42,
            "subject": "Old subject",
            "matrix_room_id": "!room:example.com",
            "updated_at": datetime.now(timezone.utc).replace(tzinfo=None),
        }
        updated_fields = {}
        audit_payload = {}
        matrix_names = []
        broadcasts = []

        async def fake_get_room(room_id: int):
            assert room_id == 42
            return {**room, **updated_fields}

        async def fake_update_room(room_id: int, **fields):
            assert room_id == 42
            updated_fields.update(fields)

        async def fake_set_room_name(room_id: str, name: str):
            matrix_names.append((room_id, name))
            return {}

        async def fake_log_action(**kwargs):
            audit_payload.update(kwargs)

        async def fake_broadcast_refresh(**kwargs):
            broadcasts.append(kwargs)

        monkeypatch.setattr(chat_routes.chat_repo, "get_room", fake_get_room)
        monkeypatch.setattr(chat_routes.chat_repo, "update_room", fake_update_room)
        monkeypatch.setattr(chat_routes.matrix_service, "set_room_name", fake_set_room_name)
        monkeypatch.setattr(chat_routes.audit_service, "log_action", fake_log_action)
        monkeypatch.setattr(chat_routes.refresh_notifier, "broadcast_refresh", fake_broadcast_refresh)

        response = await chat_routes.rename_room(
            42,
            request=None,
            body=chat_routes.ChatRoomRename(subject="  New subject  "),
            current_user={"id": 7, "is_helpdesk_technician": True},
        )

        assert response.status_code == 200
        assert updated_fields["subject"] == "New subject"
        assert matrix_names == [("!room:example.com", "New subject")]
        assert audit_payload["action"] == "rename"
        assert audit_payload["old_value"] == {"subject": "Old subject"}
        assert audit_payload["new_value"] == {"subject": "New subject"}
        assert broadcasts[0]["topics"] == ["chat:room:42", "chat:rooms"]

    asyncio.run(run_test())


def test_rename_room_rejects_non_staff(monkeypatch):
    async def run_test():
        monkeypatch.setattr(chat_routes._settings, "matrix_enabled", True)

        with pytest.raises(HTTPException) as exc:
            await chat_routes.rename_room(
                42,
                request=None,
                body=chat_routes.ChatRoomRename(subject="New subject"),
                current_user={"id": 8, "is_helpdesk_technician": False, "is_super_admin": False},
            )

        assert exc.value.status_code == 403

    asyncio.run(run_test())
