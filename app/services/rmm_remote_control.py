"""Remote control through RustDesk and MeshCentral, switched on by an RMM script.

Both tools are already installed on most devices but stay off until needed.
When a technician clicks Connect on an asset:

1. :func:`start` queues the provider's activation script on the device's RMM
   agent (``run_source = 'remote_control'``) and records a session. The script
   gets the values set on the Remote control page, which can be variables such
   as ``{{company.variables.RustDeskKey}}``.
2. The script installs the tool where it is missing, otherwise resets its
   access password, and prints what MyPortal needs to connect::

       ##myportal[session.id]=123456789          (RustDesk ID)
       ##myportal[session.password]=one-time-pw  (RustDesk, optional)
       ##myportal[session.node_id]=node//abc...  (MeshCentral)

   or writes ``[{"scope": "session", "name": "id", "value": "..."}]`` to
   ``$MYPORTAL_RESULT_FILE``. :func:`capture` keeps those values encrypted on
   the session; run history shows them masked and the output has them blanked.
3. :func:`session_status` builds the launch link once the run finishes: a
   ``rustdesk://`` link, or a MeshCentral address carrying a login token made
   from the server's login token key (``meshcentral --logintokenkey``).

Without an activation script, the device's ID comes from an asset custom
field. A MeshCentral session without a node ID opens MeshCentral signed in.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from urllib.parse import quote, urlencode, urlsplit

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.repositories import rmm as rmm_repo
from app.repositories import rmm_remote_control as remote_repo
from app.security.encryption import decrypt_secret, encrypt_secret
from app.services import rmm_scripts

PROVIDERS = remote_repo.PROVIDERS
PROVIDER_LABELS = {"rustdesk": "RustDesk", "meshcentral": "MeshCentral"}
# How long a session's link (and any one-time password) stays available.
SESSION_LIFETIME = timedelta(minutes=30)
# An offline device should fail fast rather than switch remote control on hours later.
ACTIVATION_QUEUE_LIFETIME = timedelta(minutes=10)
MAX_ACTIVATION_TIMEOUT_SECONDS = 300
RUN_SOURCE = "remote_control"
SESSION_VALUE_NAMES = {"id", "password", "node_id"}

_SESSION_MARKER = re.compile(r"^(\s*##myportal\[session\.([^\]]+)\]=)(.*?)\s*$")
_RUSTDESK_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MESH_NODE_ID = re.compile(r"^[A-Za-z0-9@$/_+=.-]{1,256}$")
_HEX = re.compile(r"^[0-9a-fA-F]+$")


class RemoteControlError(ValueError):
    """Remote control cannot start, or the settings cannot be saved."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --------------------------------------------------------------------------- #
# Launch links
# --------------------------------------------------------------------------- #


def rustdesk_url(remote_id: str, password: str | None = None) -> str:
    # RustDesk shows IDs in groups ("123 456 789").
    cleaned = re.sub(r"\s+", "", remote_id or "")
    if not _RUSTDESK_ID.match(cleaned):
        raise RemoteControlError("The RustDesk ID reported for this device is not valid.")
    url = f"rustdesk://connection/new/{cleaned}"
    if password:
        url += "?password=" + quote(password, safe="")
    return url


def _mesh_user(username: str) -> str:
    username = username.strip()
    # A full MeshCentral user id (user/<domain>/<name>) is used as is.
    return username.lower() if username.startswith("user/") else "user//" + username.lower()


def meshcentral_login_token(key_hex: str, username: str, *, now: float | None = None) -> str:
    """A MeshCentral login token, as MeshCentral's own ``encodeCookie`` makes it."""

    key = bytes.fromhex(key_hex.strip())[:32]
    message = json.dumps(
        {"a": 3, "u": _mesh_user(username), "time": int(now if now is not None else time.time())},
        separators=(",", ":"),
    ).encode("utf-8")
    iv = os.urandom(12)
    sealed = AESGCM(key).encrypt(iv, message, None)
    # AESGCM appends the 16-byte tag; MeshCentral expects iv + tag + ciphertext.
    ciphertext, tag = sealed[:-16], sealed[-16:]
    return base64.b64encode(iv + tag + ciphertext, altchars=b"@$").decode("ascii")


def meshcentral_url(server_url: str, username: str, key_hex: str, node_id: str | None = None,
                    *, now: float | None = None) -> str:
    params = {"login": meshcentral_login_token(key_hex, username, now=now)}
    if node_id:
        node_id = node_id.strip()
        if not _MESH_NODE_ID.match(node_id):
            raise RemoteControlError("The MeshCentral node ID reported for this device is not valid.")
        params.update({"gotonode": node_id, "viewmode": "11", "hide": "31"})
    return server_url.rstrip("/") + "/?" + urlencode(params, safe="@$/", quote_via=quote)


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #


def _stored_entries(row: Mapping[str, Any]) -> dict[str, str]:
    if not row.get("entries_encrypted"):
        return {}
    try:
        return remote_repo.decode_json(decrypt_secret(str(row["entries_encrypted"]), allow_plaintext=False))
    except Exception:  # noqa: BLE001 - unreadable values are entered again
        return {}


def _script_values(script: Mapping[str, Any] | None, entries: Mapping[str, str]) -> list[dict[str, Any]]:
    """The activation script's fields with their saved values (secrets never sent back)."""

    if not script:
        return []
    fields = []
    for field_def in rmm_scripts.script_fields(script):
        value = entries.get(field_def["key"], "")
        secret = bool(field_def["sensitive"]) and not rmm_scripts._TOKEN.search(value)
        fields.append({**field_def, "value": "" if secret else value, "value_set": bool(value)})
    return fields


def _public_provider(row: Mapping[str, Any], script: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "provider": row["provider"],
        "label": PROVIDER_LABELS[row["provider"]],
        "is_enabled": bool(row.get("is_enabled")),
        "activation_script_id": row.get("activation_script_id"),
        "activation_script_name": row.get("activation_script_name"),
        "activation_script_active": row.get("activation_script_active"),
        "server_url": row.get("server_url") or "",
        "username": row.get("username") or "",
        "secret_set": bool(row.get("secret_encrypted")),
        "id_field": row.get("id_field") or "",
        "fields": _script_values(script, _stored_entries(row)),
        "updated_at": row.get("updated_at"),
    }


async def load_settings() -> list[dict[str, Any]]:
    providers = await remote_repo.list_providers()
    public = []
    for name in PROVIDERS:
        row = providers[name]
        script = await rmm_repo.get_script(int(row["activation_script_id"])) if row.get("activation_script_id") else None
        public.append(_public_provider(row, script))
    return public


async def enabled_providers() -> list[dict[str, str]]:
    providers = await remote_repo.list_providers()
    return [
        {"provider": name, "label": PROVIDER_LABELS[name]}
        for name in PROVIDERS
        if providers[name]["is_enabled"]
    ]


def _clean_server_url(value: str) -> str:
    value = (value or "").strip().rstrip("/")
    if not value:
        return ""
    parts = urlsplit(value)
    if parts.scheme not in {"https", "http"} or not parts.hostname or parts.query or parts.fragment:
        raise RemoteControlError("Enter the MeshCentral address, for example https://mesh.example.com.")
    return value


async def save_settings(provider: str, form: Mapping[str, Any], *, user_id: int | None) -> None:
    if provider not in PROVIDERS:
        raise RemoteControlError("Unknown remote control provider.")
    current = (await remote_repo.list_providers())[provider]
    enabled = str(form.get("is_enabled") or "").lower() in {"1", "on", "true", "yes"}
    script_id: int | None = None
    entries: dict[str, str] = {}
    raw_script = str(form.get("activation_script_id") or "").strip()
    if raw_script:
        try:
            script_id = int(raw_script)
        except ValueError as exc:
            raise RemoteControlError("Choose the activation script from the list.") from exc
        script = await rmm_repo.get_script(script_id)
        if not script or not script.get("is_active", 1):
            raise RemoteControlError("The chosen activation script no longer exists. Sync scripts from Gitea and try again.")
        # A newly chosen script's fields are shown after saving, so only the
        # current script's values are read from the form.
        stored = _stored_entries(current) if current.get("activation_script_id") == script_id else {}
        for field_def in rmm_scripts.script_fields(script):
            key = field_def["key"]
            posted = form.get("entry:" + key)
            text = "" if posted is None else str(posted)[:65535]
            if not text.strip() and field_def["sensitive"] and stored.get(key):
                text = stored[key]  # a blank secret keeps the saved one
            if text.strip():
                entries[key] = text
    id_field = str(form.get("id_field") or "").strip()[:255] or None
    server_url = username = secret = None
    if provider == "meshcentral":
        server_url = _clean_server_url(str(form.get("server_url") or "")) or None
        username = str(form.get("username") or "").strip()[:255] or None
        key = re.sub(r"\s+", "", str(form.get("secret") or ""))
        if key:
            if not _HEX.match(key) or len(key) < 64 or len(key) % 2:
                raise RemoteControlError(
                    "The login token key is the long hex value printed by meshcentral --logintokenkey."
                )
            secret = encrypt_secret(key)
        if enabled and not (server_url and username and (secret or current.get("secret_encrypted"))):
            raise RemoteControlError("MeshCentral needs its address, a user name and the login token key.")
    elif enabled and not (script_id or id_field):
        raise RemoteControlError("RustDesk needs an activation script or the custom field holding each device's ID.")
    await remote_repo.save_provider(
        provider,
        is_enabled=enabled,
        activation_script_id=script_id,
        server_url=server_url,
        username=username,
        secret_encrypted=secret,
        id_field=id_field,
        entries_encrypted=encrypt_secret(json.dumps(entries)) if entries else None,
        user_id=user_id,
    )


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #


async def _provider(provider: str) -> dict[str, Any]:
    if provider not in PROVIDERS:
        raise RemoteControlError("Unknown remote control provider.")
    settings = (await remote_repo.list_providers())[provider]
    if not settings["is_enabled"]:
        raise RemoteControlError(f"{PROVIDER_LABELS[provider]} remote control is turned off.")
    return settings


async def start(*, provider: str, company_id: int, asset_id: int, user_id: int | None) -> dict[str, Any]:
    """Queue the activation script (or resolve straight away) and return the session."""

    settings = await _provider(provider)
    label = PROVIDER_LABELS[provider]
    agent = await rmm_repo.get_asset_agent(asset_id)
    if agent and agent.get("company_id") is not None and int(agent["company_id"]) != int(company_id):
        agent = None
    script = None
    if settings.get("activation_script_id"):
        script = await rmm_repo.get_script(int(settings["activation_script_id"]), with_content=True)
        if not script or not script.get("is_active", 1) or not rmm_repo.script_available_to(script, company_id):
            raise RemoteControlError(f"The {label} activation script is not available for this company.")
        if not agent:
            raise RemoteControlError(f"The RMM agent is not installed on this device, so {label} cannot be switched on.")
    session_id = await remote_repo.create_session(
        provider=provider,
        company_id=company_id,
        asset_id=asset_id,
        agent_id=int(agent["id"]) if agent else None,
        requested_by_user_id=user_id,
        expires_at=_utcnow() + SESSION_LIFETIME,
    )
    if script is None:
        await _resolve(await remote_repo.get_session(session_id), settings, {})
    else:
        try:
            entries = rmm_scripts.validate_entries(script, _stored_entries(settings))
        except rmm_scripts.RunRequestError as exc:
            await remote_repo.finish_session(session_id, status="failed", error_message="Activation script needs values.")
            raise RemoteControlError(
                f"The {label} activation script has required values that are not set. Fill them in on the Remote control page."
            ) from exc
        timeout = min(
            MAX_ACTIVATION_TIMEOUT_SECONDS,
            rmm_scripts.clamp_timeout(script.get("default_timeout_seconds"), MAX_ACTIVATION_TIMEOUT_SECONDS),
        )
        try:
            run_id = await rmm_scripts.queue_for_agent(
                script=script,
                agent=agent,
                entries=entries,
                inputs=rmm_scripts.stored_inputs(script, entries),
                timeout_seconds=timeout,
                requested_by_user_id=user_id,
                run_source=RUN_SOURCE,
                expires_at=_utcnow() + ACTIVATION_QUEUE_LIFETIME,
                notify=False,
            )
        except rmm_scripts.RunRequestError as exc:
            message = next(iter(exc.errors.values()), "The activation script cannot run on this device.")
            await remote_repo.finish_session(session_id, status="failed", error_message=message)
            raise RemoteControlError(message) from exc
        await remote_repo.set_session_run(session_id, run_id)
        rmm_scripts.notify_agent(int(agent["id"]))
    return await session_status(session_id, company_id=company_id, user_id=user_id, is_super_admin=False)


def take_session_markers(stdout: str) -> tuple[str, dict[str, str]]:
    """Pull ``##myportal[session.x]=value`` lines out of ``stdout`` and blank their values."""

    values: dict[str, str] = {}
    if "##myportal[session." not in stdout:
        return stdout, values
    lines = []
    for line in stdout.splitlines(keepends=True):
        match = _SESSION_MARKER.match(line.rstrip("\r\n"))
        if match:
            values[match.group(2).strip().lower()] = match.group(3)
            ending = line[len(line.rstrip("\r\n")):]
            line = match.group(1) + rmm_scripts.SENSITIVE_MASK + ending
        lines.append(line)
    return "".join(lines), values


async def capture(run: Mapping[str, Any], values: Mapping[str, str]) -> list[dict[str, Any]]:
    """Keep a remote control run's session values; return them masked for run history."""

    session = None
    if run.get("run_source") == RUN_SOURCE:
        session = await remote_repo.get_session_for_run(int(run["id"]))
    kept = {
        name.lower(): str(value)[:1024]
        for name, value in values.items()
        if name.lower() in SESSION_VALUE_NAMES and str(value).strip()
    }
    if session is not None and kept:
        await remote_repo.store_values(int(session["id"]), encrypt_secret(json.dumps(kept)))
    results = []
    for name in values:
        if session is None:
            message, applied = "Only remote control activation scripts can return session values.", False
        elif name.lower() not in SESSION_VALUE_NAMES:
            message, applied = "Not a remote control value (use id, password or node_id).", False
        else:
            message, applied = "Used for remote control", True
        results.append({"scope": "session", "name": name, "value": rmm_scripts.SENSITIVE_MASK,
                        "applied": applied, "message": message})
    return results


def _decrypt_values(session: Mapping[str, Any]) -> dict[str, str]:
    if not session.get("values_encrypted"):
        return {}
    try:
        return remote_repo.decode_json(decrypt_secret(str(session["values_encrypted"]), allow_plaintext=False))
    except Exception:  # noqa: BLE001 - a key rotation leaves the session unusable, not broken
        return {}


async def _field_value(asset_id: int, field_name: str | None) -> str:
    if not field_name:
        return ""
    lowered = field_name.strip().lower()
    for name, value in (await rmm_scripts._asset_custom_values(asset_id)).items():
        if name.lower() == lowered:
            return str(value or "").strip()
    return ""


async def _launch_url(session: Mapping[str, Any], settings: Mapping[str, Any], values: Mapping[str, str]) -> str:
    provider = session["provider"]
    if provider == "rustdesk":
        remote_id = values.get("id") or await _field_value(int(session["asset_id"]), settings.get("id_field"))
        if not remote_id:
            raise RemoteControlError(
                "No RustDesk ID for this device. Have the activation script print ##myportal[session.id]=<id>"
                + (f", or fill in the {settings['id_field']} field." if settings.get("id_field") else ".")
            )
        return rustdesk_url(remote_id, values.get("password"))
    node_id = values.get("node_id") or values.get("id") or await _field_value(
        int(session["asset_id"]), settings.get("id_field")
    )
    if not (settings.get("server_url") and settings.get("username") and settings.get("secret_encrypted")):
        raise RemoteControlError("MeshCentral is missing its address, user name or login token key.")
    key = decrypt_secret(str(settings["secret_encrypted"]), allow_plaintext=False)
    return meshcentral_url(str(settings["server_url"]), str(settings["username"]), key, node_id or None)


async def _resolve(session: Mapping[str, Any], settings: Mapping[str, Any], values: Mapping[str, str]) -> str | None:
    try:
        url = await _launch_url(session, settings, values)
    except RemoteControlError as exc:
        await remote_repo.finish_session(int(session["id"]), status="failed", error_message=str(exc))
        return None
    await remote_repo.finish_session(int(session["id"]), status="ready")
    return url


async def session_status(session_id: int, *, company_id: int, user_id: int | None, is_super_admin: bool) -> dict[str, Any] | None:
    """The session as the asset page sees it, or ``None`` when it is not this technician's."""

    await remote_repo.expire_sessions()
    session = await remote_repo.get_session(session_id)
    if not session or int(session["company_id"]) != int(company_id):
        return None
    if not is_super_admin and session.get("requested_by_user_id") != user_id:
        return None
    settings = (await remote_repo.list_providers())[session["provider"]]
    run = None
    launch_url = None
    if session["status"] == "activating" and session.get("run_id"):
        await rmm_repo.expire_stale_runs()
        run = await rmm_repo.get_run(int(session["run_id"]))
        if run is None:
            await remote_repo.finish_session(session_id, status="failed", error_message="The activation run was removed.")
        elif run["status"] == "completed":
            launch_url = await _resolve(session, settings, _decrypt_values(session))
        elif run["status"] not in rmm_repo.ACTIVE_RUN_STATUSES:
            reason = run.get("error_message") or (
                f"exit code {run['exit_code']}" if run.get("exit_code") is not None else run["status"]
            )
            await remote_repo.finish_session(
                session_id, status="failed", error_message=f"The activation script did not finish: {reason}"
            )
        session = await remote_repo.get_session(session_id) or session
    elif session["status"] == "ready":
        try:
            launch_url = await _launch_url(session, settings, _decrypt_values(session))
        except RemoteControlError as exc:
            await remote_repo.finish_session(session_id, status="failed", error_message=str(exc))
            session = await remote_repo.get_session(session_id) or session
    return {
        "id": session["id"],
        "provider": session["provider"],
        "label": PROVIDER_LABELS.get(session["provider"], session["provider"]),
        "status": session["status"],
        "run_id": session.get("run_id"),
        "run_status": run["status"] if run else None,
        "launch_url": launch_url if session["status"] == "ready" else None,
        "error": session.get("error_message"),
        "expires_at": session["expires_at"].isoformat() if hasattr(session["expires_at"], "isoformat") else session["expires_at"],
    }
