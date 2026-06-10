from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Mapping

from app.core.logging import log_error
from app.repositories import tray as tray_repo
from app.services import tray as tray_service


def _serialize(value: Any) -> Any:
    """Return a JSON-safe representation for tray websocket payloads."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    return value


def _is_from_tray_device(message: Mapping[str, Any], tray_device_id: int) -> bool:
    """Return True when a chat message originated from the linked tray device."""
    sender_matrix_id = str(message.get("sender_matrix_id") or "")
    return sender_matrix_id == f"@tray-device-{tray_device_id}:tray"


def _sender_name(message: Mapping[str, Any]) -> str:
    sender = str(message.get("sender_display_name") or "").strip()
    if sender:
        return sender
    sender = str(message.get("sender_matrix_id") or "").strip()
    return sender or "A technician"


async def notify_tray_device_of_chat_message(
    *,
    room: Mapping[str, Any],
    message: Mapping[str, Any],
) -> bool:
    """Push a native tray toast for a new incoming chat message.

    Tray chat windows poll the room while they are open.  When that window has
    been closed, the device still keeps its tray websocket connected; sending a
    separate ``chat_message`` command ensures the OS toast is displayed for each
    technician reply without echoing the client's own tray messages.
    """
    try:
        tray_device_id = int(room.get("tray_device_id") or 0)
    except (TypeError, ValueError):
        tray_device_id = 0
    if tray_device_id <= 0:
        return False
    if _is_from_tray_device(message, tray_device_id):
        return False

    device = await tray_repo.get_device_by_id(tray_device_id)
    device_uid = str((device or {}).get("device_uid") or "").strip()
    if not device_uid:
        return False

    room_id = int(room.get("id") or 0)
    payload = {
        "type": "chat_message",
        "room_id": room_id,
        "matrix_room_id": str(room.get("matrix_room_id") or ""),
        "subject": str(room.get("subject") or ""),
        "sender": _sender_name(message),
        "message": str(message.get("body") or ""),
        "message_id": message.get("id"),
        "matrix_event_id": str(message.get("matrix_event_id") or ""),
        "sent_at": message.get("sent_at"),
    }
    delivered = await tray_service.send_to_device(device_uid, _serialize(payload))
    try:
        await tray_repo.log_command(
            device_id=tray_device_id,
            command="chat_message",
            payload_json=json.dumps(_serialize({"room_id": room_id, "message_id": message.get("id")})),
            initiated_by_user_id=None,
            status="delivered" if delivered else "queued",
        )
    except Exception as exc:  # pragma: no cover - command logging must not block chat delivery
        log_error(
            "Failed to log tray chat message notification",
            room_id=room_id,
            device_id=tray_device_id,
            error=str(exc),
        )
    return delivered
