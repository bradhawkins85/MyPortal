"""Realtime refresh notifications for connected websocket clients."""
from __future__ import annotations

import asyncio
import json
import secrets
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from fastapi import WebSocket
from redis.asyncio import Redis
from redis.asyncio.client import PubSub
from redis.exceptions import RedisError

from app.core.logging import log_warning


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
        self._redis: Redis | None = None
        self._pubsub: PubSub | None = None
        self._listener_task: asyncio.Task[None] | None = None
        self._channel = "refresh:events"
        self._node_id = secrets.token_hex(8)

    async def start(self, *, redis_client: Redis | None = None) -> None:
        """Initialise Redis-backed pub/sub if a client is provided."""

        if redis_client is None or self._listener_task is not None:
            self._redis = redis_client
            return
        self._redis = redis_client
        self._pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
        await self._pubsub.subscribe(self._channel)
        self._listener_task = asyncio.create_task(self._listen_for_events())

    async def stop(self) -> None:
        """Stop listening for Redis events and release resources."""

        if self._listener_task is not None:
            self._listener_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._listener_task
            self._listener_task = None
        if self._pubsub is not None:
            try:
                await self._pubsub.unsubscribe(self._channel)
            except Exception:  # pragma: no cover - defensive cleanup
                pass
            try:
                await self._pubsub.close()
            except Exception:  # pragma: no cover - defensive cleanup
                pass
            self._pubsub = None

    async def connect(self, websocket: WebSocket) -> None:
        """Accept a websocket and track it for future broadcasts."""

        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        """Stop tracking the supplied websocket connection."""

        async with self._lock:
            self._connections.discard(websocket)

    async def _broadcast_payload(self, payload: Mapping[str, Any]) -> BroadcastResult:
        async with self._lock:
            targets = list(self._connections)
        if not targets:
            return BroadcastResult(attempted=0, delivered=0, dropped=0)
        delivered = 0
        dropped = 0
        for websocket in targets:
            try:
                await websocket.send_json(payload)
                delivered += 1
            except Exception:
                dropped += 1
                async with self._lock:
                    self._connections.discard(websocket)
        return BroadcastResult(attempted=len(targets), delivered=delivered, dropped=dropped)

    async def broadcast_refresh(
        self,
        *,
        reason: str | None = None,
        topics: Iterable[str] | None = None,
        data: Mapping[str, Any] | None = None,
    ) -> BroadcastResult:
        """Broadcast a refresh signal to all connected clients."""

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

        result = await self._broadcast_payload(payload)
        await self._publish_to_redis(payload)
        return result

    async def _publish_to_redis(self, payload: Mapping[str, Any]) -> None:
        if self._redis is None:
            return
        envelope = {"source": self._node_id, "payload": dict(payload)}
        try:
            await self._redis.publish(self._channel, json.dumps(envelope))
        except RedisError as exc:
            log_warning("Failed to publish refresh event to Redis", error=str(exc))

    async def _listen_for_events(self) -> None:
        if self._pubsub is None:
            return
        while True:
            try:
                message = await self._pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            except asyncio.CancelledError:
                break
            except Exception as exc:  # pragma: no cover - defensive logging
                log_warning("Redis refresh subscriber error", error=str(exc))
                await asyncio.sleep(1.0)
                continue
            if not message:
                await asyncio.sleep(0.05)
                continue
            data = message.get("data")
            if not data:
                continue
            if isinstance(data, bytes):
                try:
                    data = data.decode("utf-8")
                except UnicodeDecodeError:
                    continue
            try:
                envelope = json.loads(data)
            except (TypeError, ValueError):
                continue
            if not isinstance(envelope, dict):
                continue
            if envelope.get("source") == self._node_id:
                continue
            payload = envelope.get("payload")
            if isinstance(payload, dict):
                await self._broadcast_payload(payload)


refresh_notifier = RefreshNotifier()
