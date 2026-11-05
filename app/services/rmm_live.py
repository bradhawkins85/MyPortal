"""RMM live tools: a technician's real-time terminal, task manager, service
manager, file browser, registry editor, disk usage scanner and CPU/memory
monitor, served by the RMM agent without RustDesk or MeshCentral.

A technician opens the Live tools page for a device, which asks
:func:`start_session` for a session. That stores an ``rmm_live_sessions`` row,
tells the agent's job poll to connect, and returns a one-time token for the
browser's WebSocket. The agent connects to ``/ws/rmm/agent/live/{id}`` and the
browser to ``/ws/rmm/live/{id}`` with its token; this module relays JSON frames
between the two sockets.

The browser and the agent may be connected to different Uvicorn workers, so a
:class:`Relay` carries frames between workers. With Redis configured it uses a
pub/sub channel per session; otherwise it falls back to the
``rmm_live_frames`` table, polled a few times a second. Within one worker the
two sockets share an in-process queue and neither Redis nor the database is
touched.

Nothing in a frame is persisted beyond this relay: terminal output, file
contents and registry values live only for the session. Only the fact that a
session happened, who started it and when, is audited.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from loguru import logger

from app.repositories import rmm_live as live_repo
from app.services.redis import get_redis_client
from app.services.tray import hash_token

# A session is held open this long from the last activity; the page keeps it
# alive while open and closes it when the technician leaves.
SESSION_TTL = timedelta(minutes=30)
# The agent is re-offered a still-unconnected session no more often than this.
REOFFER_AFTER = timedelta(seconds=10)
# How often each worker checks the database for agents with a session waiting,
# mirroring the job-queue watcher so idle polls stay cheap.
OFFER_WATCH_SECONDS = 2.0
# Database relay polling and sweep cadence (Redis deployments skip the poll).
DB_RELAY_POLL_SECONDS = 0.25
FRAME_SWEEP = timedelta(minutes=5)
MAX_FRAME_BYTES = 1 << 20

_CHANNEL_PREFIX = "myportal:rmm:live:"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _expired(session: dict[str, Any]) -> bool:
    """Whether a session's expiry has passed. SQLite returns the timestamp as
    a string, MySQL as a datetime; handle both."""

    expires_at = session.get("expires_at")
    if isinstance(expires_at, str):
        try:
            expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError:
            return False
    if isinstance(expires_at, datetime):
        if expires_at.tzinfo is not None:
            expires_at = expires_at.astimezone(timezone.utc).replace(tzinfo=None)
        return expires_at <= _utcnow()
    return False


# --------------------------------------------------------------------------- #
# Agent-side session offers (which live sessions the job poll should list)
# --------------------------------------------------------------------------- #

_offer_states: dict[int, bool] = {}
# Holds the running cross-worker offer watcher(s) so the task is not garbage
# collected and a second is not started on the same loop.
_offer_watchers: set[asyncio.Task] = set()


async def sessions_for_agent(agent: dict[str, Any]) -> list[dict[str, int]]:
    """Live sessions to connect to, for an agent's job poll. Older agents that
    do not advertise live tools support are never offered one."""

    if not agent.get("live_tools_supported"):
        return []
    ids = await live_repo.offer_requested(int(agent["id"]), reoffer_after=REOFFER_AFTER)
    return [{"id": session_id} for session_id in ids]


async def _watch_offers() -> None:
    while _offer_states:
        try:
            agents = await live_repo.agents_with_unoffered(reoffer_after=REOFFER_AFTER)
        except Exception as exc:  # noqa: BLE001 - keep watching
            logger.warning("RMM live offer check failed: {}", exc)
        else:
            for agent_id in list(_offer_states):
                if agent_id in agents:
                    rmm_repo_notify(agent_id)
        await asyncio.sleep(OFFER_WATCH_SECONDS)


def rmm_repo_notify(agent_id: int) -> None:
    from app.services import rmm_scripts

    rmm_scripts.notify_agent(agent_id)


def watch_agent_for_offers(agent_id: int) -> None:
    """While an agent long-polls, have this worker wake it when a live session
    is requested for it from elsewhere."""

    _offer_states[agent_id] = True
    loop = asyncio.get_running_loop()
    if not any(not task.done() and task.get_loop() is loop for task in _offer_watchers):
        task = loop.create_task(_watch_offers())
        _offer_watchers.add(task)
        task.add_done_callback(_offer_watchers.discard)


def unwatch_agent_for_offers(agent_id: int) -> None:
    _offer_states.pop(agent_id, None)


# --------------------------------------------------------------------------- #
# Relay between the browser socket and the agent socket
# --------------------------------------------------------------------------- #


class Relay:
    """Carries frames for one session between its two sockets.

    ``to_agent`` frames come from the browser; ``to_browser`` frames come from
    the agent. Each direction has exactly one consumer. To keep delivery
    single-path and duplicate-free whether the two sockets land on the same
    worker or different ones, a Redis deployment relays every frame through a
    per-direction pub/sub channel, and a deployment without Redis relays
    through the ``rmm_live_frames`` table (polled a few times a second). There
    is no separate in-process queue to get out of step with those.
    """

    def __init__(self, session_id: int):
        self.session_id = session_id
        self._redis = get_redis_client()

    @staticmethod
    def _channel(session_id: int, direction: str) -> str:
        return f"{_CHANNEL_PREFIX}{session_id}:{direction}"

    async def send(self, direction: str, frame: Any) -> None:
        """Deliver one frame toward ``to_agent`` or ``to_browser``."""

        data = frame if isinstance(frame, str) else json.dumps(frame)
        if len(data) > MAX_FRAME_BYTES:
            logger.warning("RMM live frame dropped: {} bytes", len(data))
            return
        channel = self._channel(self.session_id, direction)
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.publish(channel, data)
            return
        with contextlib.suppress(Exception):
            await live_repo.insert_frame(channel, data)

    async def receive(self, direction: str, handler: Callable[[str], Awaitable[bool]]) -> None:
        """Deliver frames travelling in ``direction`` to ``handler`` until it
        returns False or the task is cancelled."""

        if self._redis is not None:
            await self._receive_redis(direction, handler)
        else:
            await self._receive_db(direction, handler)

    async def _receive_redis(self, direction, handler) -> None:
        pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
        await pubsub.subscribe(self._channel(self.session_id, direction))
        try:
            while True:
                message = await pubsub.get_message(timeout=1.0)
                if message and message.get("type") == "message":
                    payload = message["data"]
                    if isinstance(payload, bytes):
                        payload = payload.decode("utf-8", "replace")
                    if not await handler(payload):
                        return
        finally:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe(self._channel(self.session_id, direction))
                await pubsub.aclose()

    async def _receive_db(self, direction, handler) -> None:
        channel = self._channel(self.session_id, direction)
        after_id = 0
        while True:
            try:
                frames = await live_repo.read_frames(channel, after_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("RMM live frame read failed: {}", exc)
                frames = []
            for frame in frames:
                after_id = int(frame["id"])
                if not await handler(frame["payload"]):
                    return
            if frames:
                with contextlib.suppress(Exception):
                    await live_repo.delete_frames(channel, after_id)
            else:
                await asyncio.sleep(DB_RELAY_POLL_SECONDS)

    async def close(self) -> None:
        if self._redis is None:
            with contextlib.suppress(Exception):
                await live_repo.delete_channels([
                    self._channel(self.session_id, "to_agent"),
                    self._channel(self.session_id, "to_browser"),
                ])


# --------------------------------------------------------------------------- #
# Session lifecycle
# --------------------------------------------------------------------------- #


@dataclass
class StartedSession:
    session_id: int
    browser_token: str
    expires_at: datetime


async def start_session(
    *, agent: dict[str, Any], company_id: int, asset_id: int, requested_by_user_id: int | None
) -> StartedSession:
    """Create a live tools session for ``agent`` and return the browser token.

    The agent must advertise live tools support; older agents cannot."""

    if not agent.get("live_tools_supported"):
        raise LiveToolsUnsupported(
            "Update the RMM agent on this device before using live tools."
        )
    token = secrets.token_urlsafe(32)
    expires_at = _utcnow() + SESSION_TTL
    session_id = await live_repo.create_session(
        company_id=company_id,
        asset_id=asset_id,
        agent_id=int(agent["id"]),
        requested_by_user_id=requested_by_user_id,
        browser_token_hash=hash_token(token),
        expires_at=expires_at,
    )
    # Wake the agent's long-poll on this worker; the offer watcher covers others.
    rmm_repo_notify(int(agent["id"]))
    logger.info("RMM live session {} requested for asset {}", session_id, asset_id)
    return StartedSession(session_id=session_id, browser_token=token, expires_at=expires_at)


class LiveToolsUnsupported(RuntimeError):
    """The device's RMM agent is too old for live tools."""


async def authorize_browser(session_id: int, token: str) -> dict[str, Any] | None:
    """The live session for a browser token, if it is open and unexpired."""

    session = await live_repo.get_session(session_id)
    if not session or session.get("status") not in live_repo.OPEN_STATUSES:
        return None
    if _expired(session):
        return None
    expected = str(session.get("browser_token_hash") or "")
    if not token or not secrets.compare_digest(hash_token(token), expected):
        return None
    return session


async def authorize_agent_session(session_id: int, agent: dict[str, Any]) -> dict[str, Any] | None:
    """The live session the agent may serve, if it belongs to this agent."""

    session = await live_repo.get_session(session_id)
    if not session or int(session.get("agent_id") or 0) != int(agent["id"]):
        return None
    if session.get("status") not in live_repo.OPEN_STATUSES:
        return None
    if _expired(session):
        return None
    return session


async def close_session(session_id: int, reason: str) -> None:
    if await live_repo.close_session(session_id, reason):
        logger.info("RMM live session {} closed: {}", session_id, reason)


async def expire_sessions() -> None:
    for session_id in await live_repo.expire_sessions():
        logger.info("RMM live session {} expired", session_id)


async def sweep() -> None:
    """Periodic cleanup: expire old sessions and drop stale relay frames."""

    with contextlib.suppress(Exception):
        await expire_sessions()
    with contextlib.suppress(Exception):
        await live_repo.sweep_frames(FRAME_SWEEP)
