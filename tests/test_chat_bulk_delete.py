from __future__ import annotations

import asyncio

from app.api.routes import chat as chat_routes


def test_bulk_delete_rooms_broadcasts_refresh_after_delete(monkeypatch):
    async def run_test():
        monkeypatch.setattr(chat_routes._settings, "matrix_enabled", True)
        rooms = [
            {"id": 2, "subject": "Second"},
            {"id": 5, "subject": "Fifth"},
        ]
        deleted_ids = []
        audit_payload = {}
        broadcasts = []

        async def fake_list_rooms_by_ids(room_ids):
            assert room_ids == [2, 5, 9]
            return rooms

        async def fake_delete_rooms(room_ids):
            deleted_ids.extend(room_ids)
            return len(room_ids)

        async def fake_log_action(**kwargs):
            audit_payload.update(kwargs)

        async def fake_broadcast_refresh(**kwargs):
            broadcasts.append(kwargs)

        monkeypatch.setattr(chat_routes.chat_repo, "list_rooms_by_ids", fake_list_rooms_by_ids)
        monkeypatch.setattr(chat_routes.chat_repo, "delete_rooms", fake_delete_rooms)
        monkeypatch.setattr(chat_routes.audit_service, "log_action", fake_log_action)
        monkeypatch.setattr(chat_routes.refresh_notifier, "broadcast_refresh", fake_broadcast_refresh)

        response = await chat_routes.bulk_delete_rooms(
            body=chat_routes.ChatRoomBulkDelete(room_ids=[5, 2, 2, 9]),
            current_user={"id": 7, "is_super_admin": True},
        )

        assert response.status_code == 200
        assert deleted_ids == [2, 5]
        assert audit_payload["action"] == "bulk_delete"
        assert audit_payload["previous_value"]["missing_room_ids"] == [9]
        assert broadcasts == [
            {
                "reason": "chat_rooms_deleted",
                "topics": ["chat:rooms"],
                "data": {"room_ids": [2, 5]},
            }
        ]

    asyncio.run(run_test())
