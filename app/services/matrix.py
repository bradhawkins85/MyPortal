from __future__ import annotations

import asyncio
import html
import re
import uuid
from typing import Any

import httpx

from app.services.monitored_http import monitored_client

from app.core.config import get_settings

_settings = get_settings()

_DEFAULT_TIMEOUT = 30.0
_SYNC_TIMEOUT_MS = 30_000
_SAFE_MATRIX_PATH_RE = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/-]+$")

# Module-level shared client for connection pooling across Matrix API calls.
# Limits are intentionally conservative; the sync loop uses a longer timeout.
_client: httpx.AsyncClient | None = None


def _get_client(*, timeout: float = _DEFAULT_TIMEOUT) -> httpx.AsyncClient:
    """Return (or create) the shared async HTTP client."""
    global _client
    if _client is None or _client.is_closed:
        _client = monitored_client(httpx.AsyncClient, timeout=timeout)
    return _client


class MatrixError(RuntimeError):
    """Raised when Matrix homeserver responds with an error."""

    def __init__(self, errcode: str, error: str, status_code: int = 0) -> None:
        super().__init__(f"{errcode}: {error}")
        self.errcode = errcode
        self.error = error
        self.status_code = status_code


class MatrixConfigError(RuntimeError):
    """Raised when Matrix integration is not configured."""


def _base_url() -> str:
    url = (_settings.matrix_homeserver_url or "").rstrip("/")
    if not url:
        raise MatrixConfigError("MATRIX_HOMESERVER_URL is not configured")
    return url


def _bot_headers() -> dict[str, str]:
    token = _settings.matrix_bot_access_token or ""
    if not token:
        raise MatrixConfigError("MATRIX_BOT_ACCESS_TOKEN is not configured")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _admin_headers() -> dict[str, str]:
    token = _settings.matrix_admin_access_token or ""
    if not token:
        raise MatrixConfigError("MATRIX_ADMIN_ACCESS_TOKEN is not configured")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


async def _request(
    method: str,
    path: str,
    *,
    headers: dict[str, str],
    json: Any = None,
    params: dict[str, Any] | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
    max_retries: int = 3,
    monitor: bool = True,
) -> dict[str, Any]:
    if not _SAFE_MATRIX_PATH_RE.fullmatch(path):
        raise MatrixError("M_INVALID_PARAM", "Invalid Matrix API path")
    if not path.startswith("/_matrix/"):
        raise MatrixError("M_INVALID_PARAM", "Invalid Matrix API path")
    if any(segment in {".", ".."} for segment in path.split("/")):
        raise MatrixError("M_INVALID_PARAM", "Invalid Matrix API path")

    url = f"{_base_url()}{path}"
    for attempt in range(max_retries):
        try:
            client = _get_client(timeout=timeout)
            request_options: dict[str, Any] = {"timeout": timeout}
            if not monitor:
                request_options["extensions"] = {"myportal.skip_monitor": True}
            resp = await client.request(
                method, url, headers=headers, json=json, params=params, **request_options
            )
        except httpx.RequestError as exc:
            if attempt >= max_retries - 1:
                raise MatrixError("M_UNKNOWN", str(exc)) from exc
            await asyncio.sleep(2 ** attempt)
            continue
        if resp.status_code == 429:
            retry_after = float(resp.headers.get("Retry-After", 5))
            await asyncio.sleep(retry_after)
            continue
        try:
            data = resp.json()
        except Exception:
            data = {}
        if resp.is_error:
            errcode = data.get("errcode", "M_UNKNOWN")
            error = data.get("error", resp.text[:200])
            raise MatrixError(errcode, error, resp.status_code)
        return data
    return {}


async def whoami() -> dict[str, Any]:
    """Verify bot credentials and return identity info."""
    return await _request("GET", "/_matrix/client/v3/whoami", headers=_bot_headers())


async def create_room(
    *,
    name: str,
    topic: str = "",
    preset: str | None = None,
    invite_user_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Create a Matrix room and return {room_id, ...}."""
    body: dict[str, Any] = {
        "name": name,
        "preset": preset or _settings.matrix_default_room_preset,
        "visibility": "private",
    }
    if topic:
        body["topic"] = topic
    if invite_user_ids:
        body["invite"] = invite_user_ids
    return await _request("POST", "/_matrix/client/v3/createRoom", headers=_bot_headers(), json=body)


async def set_room_name(room_id: str, name: str) -> dict[str, Any]:
    """Set the Matrix room display name using the bot token."""
    return await _request(
        "PUT",
        f"/_matrix/client/v3/rooms/{room_id}/state/m.room.name/",
        headers=_bot_headers(),
        json={"name": name},
    )


async def invite_user(room_id: str, user_id: str) -> dict[str, Any]:
    """Invite a user to a room."""
    return await _request(
        "POST",
        f"/_matrix/client/v3/rooms/{room_id}/invite",
        headers=_bot_headers(),
        json={"user_id": user_id},
    )


async def join_room(room_id: str, *, access_token: str | None = None) -> dict[str, Any]:
    """Join a room (bot by default, or with a user access token)."""
    headers = _bot_headers() if not access_token else {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    return await _request("POST", f"/_matrix/client/v3/rooms/{room_id}/join", headers=headers, json={})


async def send_message(
    room_id: str,
    body: str,
    *,
    formatted_body: str | None = None,
    msgtype: str = "m.text",
    access_token: str | None = None,
    sender_display_name: str | None = None,
    transaction_id: str | None = None,
) -> dict[str, Any]:
    """Send a message to a room. Returns {event_id}.

    ``sender_display_name`` is used when a non-Matrix actor (for example a
    tray popup device) sends through the bot token. Matrix rooms display the
    bot as the sender, so prefixing both the plain-text and formatted payloads
    preserves the actual device/user attribution for Element and mobile apps.
    """
    # A stable transaction id lets callers safely retry an ambiguous Matrix
    # request: homeservers return the original event instead of creating one.
    txn_id = transaction_id or uuid.uuid4().hex
    headers = _bot_headers() if not access_token else {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    content_body = body
    content_formatted_body = formatted_body
    display_name = (sender_display_name or "").strip()
    if display_name:
        content_body = f"{display_name}: {body}"
        if content_formatted_body is None:
            content_formatted_body = (
                f"<strong>{html.escape(display_name)}</strong>: "
                f"{html.escape(body)}"
            )

    content: dict[str, Any] = {"msgtype": msgtype, "body": content_body}
    if content_formatted_body:
        content["format"] = "org.matrix.custom.html"
        content["formatted_body"] = content_formatted_body
    return await _request(
        "PUT",
        f"/_matrix/client/v3/rooms/{room_id}/send/m.room.message/{txn_id}",
        headers=headers,
        json=content,
    )


async def sync(
    *,
    since: str | None = None,
    timeout_ms: int = _SYNC_TIMEOUT_MS,
    filter_id: str | None = None,
) -> dict[str, Any]:
    """Long-poll sync. Returns raw sync response."""
    params: dict[str, Any] = {"timeout": timeout_ms}
    if since:
        params["since"] = since
    if filter_id:
        params["filter"] = filter_id
    return await _request(
        "GET",
        "/_matrix/client/v3/sync",
        headers=_bot_headers(),
        params=params,
        timeout=timeout_ms / 1000 + 10,
        # Sync is a persistent receive stream, not a webhook delivery. Failures
        # are reported by the sync loop in the application log.
        monitor=False,
    )


async def login(username: str, password: str) -> dict[str, Any]:
    """Login and return {access_token, device_id, user_id, ...}."""
    return await _request(
        "POST",
        "/_matrix/client/v3/login",
        headers={"Content-Type": "application/json"},
        json={
            "type": "m.login.password",
            "identifier": {"type": "m.id.user", "user": username},
            "password": password,
        },
    )


async def logout(access_token: str) -> None:
    """Logout a user session."""
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    try:
        await _request("POST", "/_matrix/client/v3/logout", headers=headers, json={})
    except MatrixError:
        return


async def set_display_name(user_id: str, display_name: str, *, access_token: str) -> None:
    """Set a user's display name."""
    try:
        await _request(
            "PUT",
            f"/_matrix/client/v3/profile/{user_id}/displayname",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json={"displayname": display_name},
        )
    except MatrixError:
        return


def sanitize_localpart(name: str) -> str:
    """Create a safe Matrix localpart from a display name."""
    clean = re.sub(r"[^a-z0-9._=-]", "", name.lower().replace(" ", "_"))
    clean = clean[:32] or "user"
    return clean


async def get_display_name(user_id: str) -> str | None:
    """Fetch a Matrix user's display name from their profile. Returns None on failure."""
    try:
        data = await _request(
            "GET",
            f"/_matrix/client/v3/profile/{user_id}/displayname",
            headers=_bot_headers(),
        )
        name = data.get("displayname")
        return name if name else None
    except (MatrixError, MatrixConfigError):
        return None


async def get_power_levels(room_id: str) -> dict[str, Any]:
    """Get the current power levels state for a room."""
    return await _request(
        "GET",
        f"/_matrix/client/v3/rooms/{room_id}/state/m.room.power_levels/",
        headers=_bot_headers(),
    )


async def set_user_power_level(
    room_id: str,
    user_id: str,
    power_level: int = 100,
) -> dict[str, Any]:
    """Set a user's power level in a room (100 = admin, 50 = moderator, 0 = member).

    Fetches the current power levels state, updates the target user's level,
    and writes the new state back.  Any exception is propagated to the caller.
    """
    current = await get_power_levels(room_id)
    users_map: dict[str, Any] = dict(current.get("users") or {})
    users_map[user_id] = power_level
    updated = dict(current)
    updated["users"] = users_map
    return await _request(
        "PUT",
        f"/_matrix/client/v3/rooms/{room_id}/state/m.room.power_levels/",
        headers=_bot_headers(),
        json=updated,
    )


# ---------------------------------------------------------------------------
# Media (photos, videos and file attachments)
# ---------------------------------------------------------------------------

_MXC_RE = re.compile(r"^mxc://([A-Za-z0-9.\-]+(?::[0-9]{1,5})?)/([A-Za-z0-9_\-]+)$")


def parse_mxc_uri(uri: str) -> tuple[str, str] | None:
    """Split ``mxc://server/media_id`` into its parts, or return None if invalid."""
    match = _MXC_RE.fullmatch(str(uri or "").strip())
    if not match:
        return None
    return match.group(1), match.group(2)


async def upload_media(data: bytes, *, content_type: str, filename: str) -> str:
    """Upload ``data`` to the homeserver media store and return its ``mxc://`` URI."""
    headers = {
        "Authorization": _bot_headers()["Authorization"],
        "Content-Type": content_type or "application/octet-stream",
    }
    client = _get_client(timeout=120.0)
    try:
        resp = await client.post(
            _base_url() + "/_matrix/media/v3/upload",
            headers=headers,
            params={"filename": filename},
            content=data,
            timeout=120.0,
            # Binary payloads are not useful in the webhook monitor log.
            extensions={"myportal.skip_monitor": True},
        )
    except httpx.RequestError as exc:
        raise MatrixError("M_UNKNOWN", str(exc)) from exc
    try:
        payload = resp.json()
    except Exception:
        payload = {}
    if resp.is_error:
        raise MatrixError(
            payload.get("errcode", "M_UNKNOWN"),
            payload.get("error", resp.text[:200]),
            resp.status_code,
        )
    content_uri = str(payload.get("content_uri") or "")
    if not parse_mxc_uri(content_uri):
        raise MatrixError("M_UNKNOWN", "Homeserver returned an invalid content URI")
    return content_uri


class MatrixMediaTooLarge(MatrixError):
    """Raised when a media download exceeds the caller's size limit."""


async def download_media(mxc_uri: str, *, max_bytes: int) -> tuple[bytes, str]:
    """Download media by ``mxc://`` URI as the bot; return ``(data, content_type)``.

    Uses the authenticated media endpoint first and falls back to the legacy
    unauthenticated one for homeservers that do not support it yet.
    """
    parts = parse_mxc_uri(mxc_uri)
    if not parts:
        raise MatrixError("M_INVALID_PARAM", "Invalid media URI")
    server_name, media_id = parts
    headers = {"Authorization": _bot_headers()["Authorization"]}
    client = _get_client(timeout=120.0)
    last_error: MatrixError | None = None
    for prefix in ("/_matrix/client/v1/media/download/", "/_matrix/media/v3/download/"):
        url = _base_url() + prefix + server_name + "/" + media_id
        try:
            async with client.stream(
                "GET",
                url,
                headers=headers,
                params={"allow_redirect": "true"},
                timeout=120.0,
                extensions={"myportal.skip_monitor": True},
            ) as resp:
                if resp.status_code in (400, 404, 405) and prefix.startswith("/_matrix/client/"):
                    last_error = MatrixError("M_UNRECOGNIZED", "Authenticated media unsupported", resp.status_code)
                    continue
                if resp.is_error:
                    raise MatrixError("M_UNKNOWN", "Media download failed", resp.status_code)
                declared = resp.headers.get("Content-Length")
                if declared and declared.isdigit() and int(declared) > max_bytes:
                    raise MatrixMediaTooLarge("M_TOO_LARGE", "Media is too large", 413)
                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise MatrixMediaTooLarge("M_TOO_LARGE", "Media is too large", 413)
                    chunks.append(chunk)
                content_type = resp.headers.get("Content-Type", "application/octet-stream")
                return b"".join(chunks), content_type
        except httpx.RequestError as exc:
            raise MatrixError("M_UNKNOWN", str(exc)) from exc
    raise last_error or MatrixError("M_NOT_FOUND", "Media not found", 404)


async def send_media_message(
    room_id: str,
    *,
    msgtype: str,
    filename: str,
    content_uri: str,
    info: dict[str, Any],
    transaction_id: str | None = None,
) -> dict[str, Any]:
    """Send an ``m.image``/``m.video``/``m.audio``/``m.file`` message. Returns {event_id}."""
    txn_id = transaction_id or uuid.uuid4().hex
    content: dict[str, Any] = {
        "msgtype": msgtype,
        "body": filename,
        "filename": filename,
        "url": content_uri,
        "info": info,
    }
    return await _request(
        "PUT",
        f"/_matrix/client/v3/rooms/{room_id}/send/m.room.message/{txn_id}",
        headers=_bot_headers(),
        json=content,
    )
