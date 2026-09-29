"""Realtime refresh notifications for connected websocket clients."""
from __future__ import annotations

import asyncio
import json
import secrets
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable, Mapping

from fastapi import WebSocket
from redis.asyncio import Redis
from redis.asyncio.client import PubSub
from redis.exceptions import RedisError

from app.core.logging import log_warning

_CHAT_ROOM_TOPIC_PREFIX = "chat:room:"


@dataclass(slots=True, frozen=True)
class ConnectionAccess:
    """Identity attached to an authenticated realtime websocket connection.

    ``is_privileged`` connections (super admins and helpdesk technicians)
    receive every event.  Other connections only receive chat room events
    for rooms they created or participate in.
    """

    user_id: int | None
    is_privileged: bool = False


RoomAccessResolver = Callable[[int], Awaitable["set[int] | None"]]


async def _default_room_member_ids(room_id: int) -> set[int] | None:
    """Return the portal user ids allowed to see ``room_id`` chat events."""

    from app.repositories import chat as chat_repo

    room = await chat_repo.get_room(room_id)
    if not room:
        return None
    allowed: set[int] = set()
    creator = room.get("created_by_user_id")
    if creator is not None:
        try:
            allowed.add(int(creator))
        except (TypeError, ValueError):
            log_warning(
                "Ignoring invalid chat room creator id during realtime access resolution",
                room_id=room_id,
                creator=creator,
            )
    for participant in await chat_repo.get_participants(room_id):
        user_id = participant.get("user_id") if isinstance(participant, Mapping) else None
        if user_id is None or participant.get("left_at"):
            continue
        try:
            allowed.add(int(user_id))
        except (TypeError, ValueError):
            continue
    return allowed


def _chat_room_ids(payload: Mapping[str, Any]) -> set[int]:
    room_ids: set[int] = set()
    topics = payload.get("topics")
    if isinstance(topics, (list, tuple)):
        for topic in topics:
            if isinstance(topic, str) and topic.startswith(_CHAT_ROOM_TOPIC_PREFIX):
                suffix = topic[len(_CHAT_ROOM_TOPIC_PREFIX):]
                if suffix.isdigit():
                    room_ids.add(int(suffix))
    data = payload.get("data")
    if isinstance(data, Mapping) and (room_ids or "message" in data):
        raw = data.get("room_id")
        try:
            if raw is not None:
                room_ids.add(int(raw))
        except (TypeError, ValueError):
            # Ignore invalid room_id values during best-effort room id extraction.
            continue
    return room_ids


def _redacted_chat_payload(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    """Strip room-scoped topics and data for connections without room access."""

    topics = payload.get("topics")
    remaining = [
        topic
        for topic in (topics if isinstance(topics, (list, tuple)) else [])
        if isinstance(topic, str) and not topic.startswith(_CHAT_ROOM_TOPIC_PREFIX)
    ]
    if not remaining:
        return None
    redacted = {key: value for key, value in payload.items() if key not in {"data", "topics"}}
    redacted["topics"] = remaining
    return redacted


@dataclass(slots=True)
class BroadcastResult:
    """Summary of a broadcast operation."""

    attempted: int
    delivered: int
    dropped: int


class RefreshNotifier:
    """Track websocket connections and broadcast refresh instructions."""

    def __init__(self, *, room_access_resolver: RoomAccessResolver | None = None) -> None:
        self._connections: dict[WebSocket, ConnectionAccess | None] = {}
        self._room_access_resolver = room_access_resolver or _default_room_member_ids
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
            with suppress(Exception):
                await self._pubsub.unsubscribe(self._channel)
            with suppress(Exception):
                await self._pubsub.close()
            self._pubsub = None

    async def connect(
        self,
        websocket: WebSocket,
        *,
        access: ConnectionAccess | None = None,
    ) -> None:
        """Accept a websocket and track it for future broadcasts.

        ``access`` identifies the authenticated user so chat room events can
        be filtered per connection.  Connections without access information
        never receive chat room message bodies.
        """

        await websocket.accept()
        async with self._lock:
            self._connections[websocket] = access

    async def disconnect(self, websocket: WebSocket) -> None:
        """Stop tracking the supplied websocket connection."""

        async with self._lock:
            self._connections.pop(websocket, None)

    async def _resolve_room_members(self, room_ids: set[int]) -> dict[int, set[int]]:
        members: dict[int, set[int]] = {}
        for room_id in room_ids:
            try:
                allowed = await self._room_access_resolver(room_id)
            except Exception as exc:  # pragma: no cover - defensive logging
                log_warning("Failed to resolve chat room access for refresh", room_id=room_id, error=str(exc))
                allowed = None
            members[room_id] = allowed or set()
        return members

    async def _broadcast_payload(self, payload: Mapping[str, Any]) -> BroadcastResult:
        async with self._lock:
            targets = list(self._connections.items())
        if not targets:
            return BroadcastResult(attempted=0, delivered=0, dropped=0)
        room_ids = _chat_room_ids(payload)
        room_members: dict[int, set[int]] = {}
        redacted: dict[str, Any] | None = None
        if room_ids and any(not (access and access.is_privileged) for _, access in targets):
            room_members = await self._resolve_room_members(room_ids)
            redacted = _redacted_chat_payload(payload)
        attempted = 0
        delivered = 0
        dropped = 0
        for websocket, access in targets:
            outgoing: Mapping[str, Any] | None = payload
            if room_ids and not (access and access.is_privileged):
                user_id = access.user_id if access else None
                if user_id is None or not all(user_id in room_members.get(rid, set()) for rid in room_ids):
                    outgoing = redacted
            if outgoing is None:
                continue
            attempted += 1
            try:
                await websocket.send_json(outgoing)
                delivered += 1
            except Exception:
                dropped += 1
                async with self._lock:
                    self._connections.pop(websocket, None)
        return BroadcastResult(attempted=attempted, delivered=delivered, dropped=dropped)

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
