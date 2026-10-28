"""Minimal client for a MeshCentral server's control channel.

MeshCentral manages its device groups (internally "meshes") and nodes over a
WebSocket at ``<site>/control.ashx?auth=<token>``. The token is the same
login-cookie a web sign-in uses, so MyPortal reuses the one built by
:func:`app.services.rmm_remote_control.meshcentral_login_token` and hands the
finished string in to the helpers here (kept separate so this module stays
independent of the remote control service).

Why it exists: MeshCentral's agent installers are built *per device group*
(``/meshagents?id=<arch>&meshid=<group>&installflags=2``). Installing with a
company's group therefore drops the device straight into that group instead of
a single shared group, and re-running the installer is how a device is moved to
its company's group. These helpers find-or-create that group and locate the
device's node id once it has checked in, so a session can build its sign-in
link.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Sequence

import httpx
import websockets

# How long a single control-channel round trip may take.
_REQUEST_TIMEOUT_SECONDS = 10
# Group/node lookups can miss a freshly installed agent that has not checked in
# yet, so find_node retries for roughly this long.
_NODE_WAIT_ATTEMPTS = 6
_NODE_WAIT_DELAY_SECONDS = 3.0


class MeshCentralError(Exception):
    """The control channel is unreachable, or the answer was not what we asked for."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


async def download_installer(server_url: str, login_token: str, group_id: str, arch_id: int) -> bytes:
    """Fetch a group-scoped installer with server-side authentication only."""
    return await _download_agent_file(
        server_url, login_token, "meshagents",
        {"id": arch_id, "meshid": group_id, "installflags": 2},
    )


async def download_settings(server_url: str, login_token: str, group_id: str) -> bytes:
    """Non-Windows meshagents downloads are bare binaries; deploy their .msh too."""
    raw = await _download_agent_file(server_url, login_token, "meshsettings", {"id": group_id})
    try:
        values = dict(line.split("=", 1) for line in raw.decode("utf-8-sig").splitlines() if "=" in line)
    except UnicodeError:
        raise MeshCentralError("MeshCentral returned an invalid agent configuration.") from None
    if not all(values.get(key) for key in ("MeshID", "ServerID", "MeshServer")):
        raise MeshCentralError("MeshCentral returned an incomplete agent configuration.")
    return raw


async def _download_agent_file(
    server_url: str, login_token: str, endpoint: str, params: dict[str, Any],
) -> bytes:
    from app.services.rmm_script_files import MAX_FILE_BYTES

    try:
        # Do not follow sign-in redirects or include HTTP exceptions in errors:
        # their request URLs contain the short-lived authentication token.
        async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
            # meshagents checks req.session.userid when lockagentdownload is on;
            # an auth query token alone does not establish that HTTP session.
            login = await client.get(server_url.rstrip("/") + "/", params={"login": login_token})
            if login.status_code not in (200, 302, 303) or not client.cookies:
                raise MeshCentralError("MyPortal could not sign in to MeshCentral to download the installer.")
            async with client.stream(
                "GET", server_url.rstrip("/") + "/" + endpoint, params=params,
            ) as response:
                if response.status_code != 200:
                    raise MeshCentralError(f"MeshCentral agent file download failed (HTTP {response.status_code}).")
                content_type = response.headers.get("content-type", "").lower()
                if any(value in content_type for value in ("text/html", "application/json")):
                    raise MeshCentralError("MeshCentral returned a sign-in page or error instead of an agent file.")
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_FILE_BYTES:
                        raise MeshCentralError("The MeshCentral agent file is larger than 50 MB.")
    except httpx.HTTPError:
        raise MeshCentralError("Could not download the MeshCentral agent file. Check MyPortal's connection to MeshCentral.") from None
    if not raw or raw.lstrip().lower().startswith((b"<", b"{")):
        raise MeshCentralError("MeshCentral did not return a valid agent file.")
    return bytes(raw)


def _control_url(server_url: str, login_token: str) -> str:
    url = (server_url or "").strip().rstrip("/")
    if not url:
        raise MeshCentralError("The MeshCentral address is missing.")
    if url.lower().startswith("https://"):
        url = "wss://" + url[len("https://"):]
    elif url.lower().startswith("http://"):
        url = "ws://" + url[len("http://"):]
    return url + "/control.ashx?auth=" + login_token


async def _request(
    server_url: str,
    login_token: str,
    action: str,
    payload: dict[str, Any] | None = None,
    *,
    response_action: str | None = None,
    timeout_seconds: float = _REQUEST_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """One request/response exchange on the control channel."""

    message = {"action": action, "responseid": "myportal"}
    if payload:
        message.update(payload)
    uri = _control_url(server_url, login_token)
    try:
        async with websockets.connect(uri, max_size=64 * 1024 * 1024) as websocket:
            await websocket.send(json.dumps(message))
            loop = asyncio.get_event_loop()
            deadline = loop.time() + timeout_seconds
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise MeshCentralError(f"Timed out waiting for MeshCentral to answer '{action}'.")
                raw = await asyncio.wait_for(websocket.recv(), timeout=remaining)
                data = json.loads(raw)
                if data.get("action") == (response_action or action):
                    return data
    except MeshCentralError:
        raise
    except OSError as exc:
        raise MeshCentralError(f"Could not reach the MeshCentral control channel: {exc}") from exc
    except ValueError as exc:
        raise MeshCentralError("The MeshCentral control channel sent an unreadable reply.") from exc


async def list_meshes(server_url: str, login_token: str) -> list[dict[str, Any]]:
    """Every device group the user can see (``_id`` looks like ``mesh//abc``)."""

    data = await _request(server_url, login_token, "meshes", response_action="meshes")
    meshes = data.get("meshes")
    return meshes if isinstance(meshes, list) else []


async def ensure_group(server_url: str, login_token: str, name: str) -> str:
    """Return the id (without the ``mesh//`` prefix) of a named group, creating it if needed."""

    name = (name or "").strip()
    if not name:
        raise MeshCentralError("A device group name is needed for MeshCentral.")
    for mesh in await list_meshes(server_url, login_token):
        if str(mesh.get("name", "")).lower() == name.lower():
            return str(mesh.get("_id", "")).removeprefix("mesh//")
    data = await _request(
        server_url, login_token, "createmesh", {"meshname": name, "meshtype": 2}, response_action="createmesh"
    )
    mesh_id = str(data.get("meshid") or "").removeprefix("mesh//")
    if not mesh_id:
        raise MeshCentralError(f"MeshCentral created the '{name}' group but did not return its id.")
    return mesh_id


async def find_node(
    server_url: str,
    login_token: str,
    group_id: str,
    names: Sequence[str],
    *,
    node_id: str | None = None,
    attempts: int = _NODE_WAIT_ATTEMPTS,
    delay_seconds: float = _NODE_WAIT_DELAY_SECONDS,
) -> str | None:
    """The node id (``node//...``) of a device in a group, matched by name.

    ``node_id`` is the id the device's own agent reported (``meshagent -nodeid``,
    bare or ``node//...``); it wins over a name match, but only when that node is
    in the group. A freshly installed agent takes a few seconds to check in, so
    the lookup is retried a handful of times. ``group_id`` is the id without the
    ``mesh//`` prefix; matches are verified against it so a same-named device (or
    a reported id) in another group is never picked.
    """

    wanted = {n.strip().lower() for n in names if n and n.strip()}
    reported = (node_id or "").strip().rsplit("/", 1)[-1]
    if not wanted and not reported:
        return None
    for attempt in range(attempts):
        if attempt:
            await asyncio.sleep(delay_seconds)
        try:
            data = await _request(
                server_url,
                login_token,
                "nodes",
                {"meshid": "mesh//" + group_id, "limit": 1000},
                response_action="nodes",
            )
        except MeshCentralError:
            return None
        nodes = data.get("nodes") if isinstance(data.get("nodes"), list) else []
        nodes = [node for node in nodes if str(node.get("mesh", "")).removeprefix("mesh//") == group_id]
        if reported:
            for node in nodes:
                if str(node.get("_id", "")).rsplit("/", 1)[-1] == reported:
                    return str(node.get("_id", ""))
        for node in nodes:
            # The node's display name defaults to the machine's hostname, which is
            # the same hostname the RMM agent reports; match either field.
            if str(node.get("name", "")).lower() in wanted or str(node.get("hostname", "")).lower() in wanted:
                return str(node.get("_id", ""))
    return None
