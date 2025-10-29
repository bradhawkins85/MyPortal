"""Realtime refresh notifications for connected websocket clients."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from fastapi import WebSocket


@dataclass(slots=True)
class BroadcastResult:
    """Summary of a broadcast operation."""

    attempted: int
    delivered: int
    dropped: int


class RefreshNotifier:
    """Track websocket connections and broadcast refresh instructions."""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        """Accept a websocket and track it for future broadcasts."""

        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        """Stop tracking the supplied websocket connection."""

        async with self._lock:
            self._connections.discard(websocket)

    async def broadcast_refresh(
        self,
        *,
        reason: str | None = None,
        topics: Iterable[str] | None = None,
        data: Mapping[str, Any] | None = None,
    ) -> BroadcastResult:
        """Broadcast a refresh signal to all connected clients."""

        async with self._lock:
            # Snapshot the current connections so that we do not hold the
            # internal lock while sending messages.
            targets = list(self._connections)

        if not targets:
            return BroadcastResult(attempted=0, delivered=0, dropped=0)

        payload: dict[str, Any] = {
            "type": "refresh",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if reason:
            payload["reason"] = reason
        if topics:
            topic_list = []
            seen: set[str] = set()
            for topic in topics:
                if not isinstance(topic, str):
                    continue
                normalised = topic.strip()
                if not normalised:
                    continue
                lowered = normalised.lower()
                if lowered in seen:
                    continue
                seen.add(lowered)
                topic_list.append(lowered)
            if topic_list:
                payload["topics"] = topic_list
        if data:
            payload["data"] = dict(data)

        delivered = 0
        dropped = 0
        for websocket in targets:
            try:
                await websocket.send_json(payload)
                delivered += 1
            except Exception:
                dropped += 1
                # Stop tracking this websocket because it no longer accepts
                # messages.  We intentionally ignore errors here to keep the
                # broadcast resilient.
                async with self._lock:
                    self._connections.discard(websocket)

        return BroadcastResult(
            attempted=len(targets),
            delivered=delivered,
            dropped=dropped,
        )


refresh_notifier = RefreshNotifier()
