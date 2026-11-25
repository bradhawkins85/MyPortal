"""Presence checks shared by tray sessions, asset views and Wake-on-LAN."""

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

WEBSOCKET_PRESENCE_WINDOW_SECONDS = 90
HEARTBEAT_PRESENCE_WINDOW_SECONDS = 180


def as_utc(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def websocket_recently_seen(device: Mapping[str, Any], *, now: datetime | None = None) -> bool:
    """Return True when the device's WebSocket was active very recently."""

    seen = as_utc(device.get("ws_last_seen_utc"))
    if seen is None:
        return False
    current = now or datetime.now(timezone.utc)
    return (current - seen).total_seconds() <= WEBSOCKET_PRESENCE_WINDOW_SECONDS


def device_online(device: Mapping[str, Any], *, now: datetime | None = None) -> bool:
    """True when the tray device is live on some worker / by heartbeat."""

    current = now or datetime.now(timezone.utc)
    if websocket_recently_seen(device, now=current):
        return True
    last_seen = as_utc(device.get("last_seen_utc"))
    if (
        last_seen is not None
        and (current - last_seen).total_seconds()
        <= HEARTBEAT_PRESENCE_WINDOW_SECONDS
    ):
        return True
    return False
