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
   from the server's login token key. Both ``RUSTDESK_CFG`` and the MeshCentral
   address, user and login token key come from the .env file (see
   :func:`rustdesk_environment` / :func:`meshcentral_environment`).

Without a chosen script, RustDesk uses MyPortal's built-in one
(``rmm_builtin/rustdesk_activate*``): on Windows and macOS it installs RustDesk
when it is missing, then it applies ``RUSTDESK_CFG`` from the .env file,
starts the RustDesk service, sets a one-time password MyPortal generates and
reports the ID. A device without the RMM agent falls back to an
asset custom field holding its ID.

MeshCentral has a matching built-in (``rmm_builtin/meshcentral_activate*``).
MyPortal downloads the authenticated installer and delivers it through RMM
associated files, scoped to a device group named after the device's company, so the device lands in its company's
group rather than one shared group. The script reports the agent's node id
(``meshagent -nodeid``, as Tactical RMM reads it); once the run finishes MyPortal
asks the MeshCentral control channel (``app.services.meshcentral``) for that node
in the company's group, falling back to the node matching the device's host
name, and builds a signed-in link that jumps straight to it. The node id is
kept per asset (``rmm_meshcentral_nodes``): MeshCentral opens in a new tab as
soon as the technician clicks, at that device's page when its node id is known
and otherwise at the device list filtered to the asset's name.

Each session is a remote desktop, file transfer, terminal or (MeshCentral only)
registry session (``mode``), which only changes the launch link. The asset and
ticket pages open the link for the technician as soon as it is ready.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import time
from functools import lru_cache
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from urllib.parse import quote, urlencode, urlsplit

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.repositories import rmm as rmm_repo
from app.repositories import rmm_remote_control as remote_repo
from app.repositories import assets as assets_repo
from app.repositories import companies as companies_repo
from app.core.config import get_settings
from app.security.encryption import decrypt_secret, encrypt_secret
from app.services import rmm_scripts, rmm_script_files
from app.services import meshcentral

PROVIDERS = remote_repo.PROVIDERS
PROVIDER_LABELS = {"rustdesk": "RustDesk", "meshcentral": "MeshCentral"}
MODES = remote_repo.MODES
MODE_LABELS = {"remote": "Remote desktop", "remote_prompt": "Remote desktop (Prompt)", "file_transfer": "File transfer", "terminal": "Terminal", "registry": "Registry"}
# rustdesk://<action>/<id> per session type (RustDesk 1.4.1 or later for terminal).
_RUSTDESK_ACTIONS = {"remote": "connection/new", "remote_prompt": "connection/new", "file_transfer": "file-transfer", "terminal": "terminal"}
# MeshCentral's device view: 11 desktop, 12 terminal, 13 files, 9 registry.
_MESH_VIEWMODES = {"remote": "11", "file_transfer": "13", "terminal": "12", "registry": "9"}
_BUILTIN_DIR = Path(__file__).resolve().parent / "rmm_builtin"
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
    """Remote control cannot start, or the settings cannot be saved.

    ``message`` is written for technicians and safe to show them."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --------------------------------------------------------------------------- #
# Launch links
# --------------------------------------------------------------------------- #


def rustdesk_url(remote_id: str, password: str | None = None, mode: str = "remote") -> str:
    # RustDesk shows IDs in groups ("123 456 789").
    cleaned = re.sub(r"\s+", "", remote_id or "")
    if not _RUSTDESK_ID.match(cleaned):
        raise RemoteControlError("The RustDesk ID reported for this device is not valid.")
    url = f"rustdesk://{_RUSTDESK_ACTIONS.get(mode, _RUSTDESK_ACTIONS['remote'])}/{cleaned}"
    # Prompt sessions never pass a generated or script-provided password to the client.
    if password and mode != "remote_prompt":
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
                    *, mode: str = "remote", now: float | None = None, overview: bool = False,
                    search: str | None = None) -> str:
    """A signed-in MeshCentral link: to ``node_id`` in ``mode``'s view, or with
    ``overview`` to the device's general page; without a node, the device list
    filtered to ``search``."""

    params = {"login": meshcentral_login_token(key_hex, username, now=now)}
    if not node_id and search and search.strip():
        params["filter"] = search.strip()[:255]
    if node_id:
        node_id = node_id.strip()
        # MeshCentral's UI builds the node id as "node/<domain>/" + gotonode, so
        # gotonode must be the bare id. Accept the full "node//..." form too:
        # the id itself never contains "/" (its base64 uses "@" and "$"), so
        # dropping everything up to the last "/" is safe.
        if node_id.startswith("node/"):
            node_id = node_id.rsplit("/", 1)[-1]
        if not node_id or not _MESH_NODE_ID.match(node_id):
            raise RemoteControlError("The MeshCentral node ID reported for this device is not valid.")
        if overview:
            params.update({"viewmode": "10", "gotonode": node_id})
        else:
            params.update({"gotonode": node_id, "viewmode": _MESH_VIEWMODES.get(mode, "11"), "hide": "31"})
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
        "id_field": row.get("id_field") or "",
        "fields": _script_values(script, _stored_entries(row)),
        "builtin": row["provider"] in {"rustdesk", "meshcentral"} and not row.get("activation_script_id"),
        "updated_at": row.get("updated_at"),
    }


def rustdesk_environment() -> dict[str, bool]:
    """Whether RUSTDESK_CFG and the installer URLs are set (never their values)."""

    settings = get_settings()
    return {
        "cfg_set": bool((settings.rustdesk_cfg or "").strip()),
        "installer_set": bool((settings.rustdesk_installer_url or "").strip()),
        "macos_installer_set": bool((settings.rustdesk_macos_installer_url or "").strip()),
    }


def meshcentral_environment() -> dict[str, bool]:
    """Whether the MeshCentral .env settings are present (never their values)."""

    settings = get_settings()
    return {
        "server_set": bool((settings.meshcentral_server_url or "").strip()),
        "username_set": bool((settings.meshcentral_username or "").strip()),
        "token_key_set": bool((settings.meshcentral_login_token_key or "").strip()),
    }


def _meshcentral_config() -> tuple[str, str, str]:
    """The MeshCentral site, sign-in user and login token key from the .env file."""

    settings = get_settings()
    site = (settings.meshcentral_server_url or "").strip().rstrip("/")
    username = (settings.meshcentral_username or "").strip()
    key = re.sub(r"\s+", "", settings.meshcentral_login_token_key or "")
    missing = [
        name
        for name, value in (
            ("MESHCENTRAL_SERVER_URL", site),
            ("MESHCENTRAL_USERNAME", username),
            ("MESHCENTRAL_LOGIN_TOKEN_KEY", key),
        )
        if not value
    ]
    if missing:
        raise RemoteControlError(
            "MeshCentral is not configured. Set " + ", ".join(missing) + " in the .env file."
        )
    if not _HEX.match(key) or len(key) < 64 or len(key) % 2:
        raise RemoteControlError(
            "MESHCENTRAL_LOGIN_TOKEN_KEY is the long hex value printed by meshcentral --logintokenkey."
        )
    return site, username, key


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
    await remote_repo.save_provider(
        provider,
        is_enabled=enabled,
        activation_script_id=script_id,
        # MeshCentral's address, user and login token key live in the .env file
        # (see _meshcentral_config), so these provider columns stay empty.
        server_url=None,
        username=None,
        secret_encrypted=None,
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


@lru_cache(maxsize=None)
def _builtin_content(name: str) -> str:
    return (_BUILTIN_DIR / name).read_text(encoding="utf-8")


def builtin_rustdesk_script(agent: Mapping[str, Any]) -> dict[str, Any]:
    """MyPortal's own RustDesk activation script for the agent's operating system."""

    os_name = str(agent.get("os") or "windows").lower()
    windows = os_name in {"windows", ""}
    if windows:
        name = "rustdesk_activate.ps1"
    elif os_name in {"darwin", "macos"}:
        name = "rustdesk_activate_macos.sh"
    else:
        name = "rustdesk_activate.sh"
    content = _builtin_content(name)
    return {
        "id": None,
        "name": "RustDesk (built-in)",
        "path": f"builtin/{name}",
        "language": "powershell" if windows else "bash",
        "content": content,
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }


def builtin_meshcentral_script(agent: Mapping[str, Any]) -> dict[str, Any]:
    """MyPortal's own MeshCentral activation script for the agent's operating system."""

    os_name = str(agent.get("os") or "windows").lower()
    if os_name in {"windows", ""}:
        name, language = "meshcentral_activate.ps1", "powershell"
    elif os_name in {"darwin", "macos"}:
        name, language = "meshcentral_activate_macos.sh", "bash"
    else:
        name, language = "meshcentral_activate.sh", "bash"
    content = _builtin_content(name)
    return {
        "id": None,
        "name": "MeshCentral (built-in)",
        "path": f"builtin/{name}",
        "language": language,
        "content": content,
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }


async def _queue_builtin_rustdesk(agent: Mapping[str, Any], password: str, user_id: int | None) -> int:
    script = rmm_scripts.always_run(builtin_rustdesk_script(agent), agent)
    problem = rmm_scripts.device_problem(script, agent)
    if problem:
        raise RemoteControlError(problem)
    config = get_settings()
    env = {"MYPORTAL_RUSTDESK_PASSWORD": password}
    if (config.rustdesk_cfg or "").strip():
        env["RUSTDESK_CFG"] = config.rustdesk_cfg.strip()
    # Each script reads its own platform's installer override.
    installer_url = {
        "builtin/rustdesk_activate.ps1": config.rustdesk_installer_url,
        "builtin/rustdesk_activate_macos.sh": config.rustdesk_macos_installer_url,
    }.get(script["path"], "")
    if (installer_url or "").strip():
        env["RUSTDESK_INSTALLER_URL"] = installer_url.strip()
    return await rmm_repo.create_run(
        script=script,
        content=script["content"],
        agent_id=int(agent["id"]),
        company_id=int(agent["company_id"]),
        asset_id=int(agent["asset_id"]) if agent.get("asset_id") is not None else None,
        requested_by_user_id=user_id,
        inputs=[],
        payload_encrypted=encrypt_secret(json.dumps({"parameters": {}, "parameter_order": [], "env": env})),
        timeout_seconds=MAX_ACTIVATION_TIMEOUT_SECONDS,
        expires_at=_utcnow() + ACTIVATION_QUEUE_LIFETIME,
        run_source=RUN_SOURCE,
    )


async def _prepare_builtin_meshcentral(
    agent: Mapping[str, Any], company_id: int
) -> tuple[str, list[str]]:
    """The company's MeshCentral group id and the host name to match the node by.

    MeshCentral's agent installer is scoped to a device group, so the group is
    resolved (created if it does not exist) before the script runs; the group is
    named after the device's company so each company's devices sit in their own
    group. The node id is found later by matching the device's host name, because
    the agent has no command-line that prints it.
    """

    site, username, key = _meshcentral_config()
    token = meshcentral_login_token(key, username)
    company = await companies_repo.get_company_by_id(int(company_id))
    group_name = str((company or {}).get("name") or "").strip()
    if not group_name:
        raise RemoteControlError("This device has no company to make a MeshCentral group for.")
    try:
        group_id = await meshcentral.ensure_group(site, token, group_name)
    except meshcentral.MeshCentralError as exc:
        raise RemoteControlError(f"MeshCentral could not be set up for this device: {exc.message}") from exc
    hostname = str(agent.get("hostname") or "").strip()
    return group_id, [hostname] if hostname else []


async def _queue_builtin_meshcentral(
    agent: Mapping[str, Any], group_id: str, user_id: int | None
) -> int:
    """Download an authenticated installer and deliver it as an RMM run file."""

    script = rmm_scripts.always_run(builtin_meshcentral_script(agent), agent)
    problem = rmm_scripts.device_problem(script, agent)
    if problem:
        raise RemoteControlError(problem)
    if not agent.get("script_files_supported"):
        raise RemoteControlError("Update the RMM agent before installing MeshCentral through MyPortal.")
    os_name = str(agent.get("os") or "windows").lower()
    arch = str(agent.get("arch") or "").lower()
    arch = {"x86_64": "amd64", "x64": "amd64", "aarch64": "arm64",
            "x86": "386", "i386": "386", "i686": "386"}.get(arch, arch)
    windows = os_name == "windows"
    if os_name in {"darwin", "macos"}:
        arch_id = 10005
    else:
        arch_id = ({"amd64": 4, "arm64": 43, "386": 3} if windows else
                   {"amd64": 6, "arm64": 32, "386": 5}).get(arch)
        if arch_id is None:
            raise RemoteControlError("The device must report a supported architecture before installing MeshCentral.")
    site, username, key = _meshcentral_config()
    try:
        token = meshcentral_login_token(key, username)
        raw = await meshcentral.download_installer(site, token, group_id, arch_id)
        # Only Windows downloads embed the company's .msh in the executable.
        configuration = await meshcentral.download_settings(site, token, group_id) if not windows else None
    except meshcentral.MeshCentralError as exc:
        raise RemoteControlError(exc.message) from exc
    filename = "meshcentral-" + secrets.token_hex(16) + (".exe" if windows else "")
    destination = ("C:\\ProgramData\\MyPortal\\" if windows else "/var/tmp/") + filename
    installer = rmm_script_files.inline_file(raw, source_path="MeshCentral/" + filename, destination=destination)
    files = [installer]
    if configuration is not None:
        files.append(rmm_script_files.inline_file(
            configuration, source_path="MeshCentral/" + filename + ".msh", destination=destination + ".msh",
        ))
    env = {
        "MESH_GROUP_ID": group_id,
        "MESH_INSTALLER_PATH": destination,
    }
    return await rmm_repo.create_run(
        script=script,
        content=script["content"],
        agent_id=int(agent["id"]),
        company_id=int(agent["company_id"]),
        asset_id=int(agent["asset_id"]) if agent.get("asset_id") is not None else None,
        requested_by_user_id=user_id,
        inputs=[],
        payload_encrypted=encrypt_secret(json.dumps({"parameters": {}, "parameter_order": [], "env": env, "files": files})),
        timeout_seconds=MAX_ACTIVATION_TIMEOUT_SECONDS,
        expires_at=_utcnow() + ACTIVATION_QUEUE_LIFETIME,
        run_source=RUN_SOURCE,
    )


async def start(*, provider: str, company_id: int, asset_id: int, user_id: int | None,
                mode: str = "remote") -> dict[str, Any]:
    """Queue the activation script (or resolve straight away) and return the session."""

    if mode not in MODES:
        raise RemoteControlError("Choose a remote desktop, file transfer, terminal or registry session.")
    if mode == "remote_prompt" and provider != "rustdesk":
        raise RemoteControlError("Prompt sessions are only available for RustDesk.")
    if mode == "registry" and provider != "meshcentral":
        raise RemoteControlError("Registry sessions are only available for MeshCentral.")
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
    # Without a chosen script, RustDesk and MeshCentral use MyPortal's built-in
    # one on any device with the agent.
    builtin = provider == "rustdesk" and script is None and agent is not None
    builtin_mesh = provider == "meshcentral" and script is None and agent is not None
    if provider == "rustdesk" and script is None and agent is None and not settings.get("id_field"):
        raise RemoteControlError("The RMM agent is not installed on this device, so RustDesk cannot be switched on.")
    # Resolve the company's MeshCentral group up front so the session knows where
    # the device will land (the agent has no command that prints its node id).
    mesh_group_id: str | None = None
    mesh_hosts: list[str] = []
    if builtin_mesh:
        mesh_group_id, mesh_hosts = await _prepare_builtin_meshcentral(agent, company_id)
    session_values: dict[str, Any] = {}
    if builtin:
        # The built-in script sets this password, so MyPortal knows it up front.
        session_values["password"] = secrets.token_urlsafe(12)
    elif builtin_mesh:
        session_values["mesh_group_id"] = mesh_group_id
        session_values["mesh_host"] = ";".join(mesh_hosts)
    session_id = await remote_repo.create_session(
        provider=provider,
        mode=mode,
        company_id=company_id,
        asset_id=asset_id,
        agent_id=int(agent["id"]) if agent else None,
        requested_by_user_id=user_id,
        expires_at=_utcnow() + SESSION_LIFETIME,
        values_encrypted=encrypt_secret(json.dumps(session_values)) if session_values else None,
    )
    if builtin:
        try:
            run_id = await _queue_builtin_rustdesk(agent, session_values["password"], user_id)
        except RemoteControlError as exc:
            await remote_repo.finish_session(session_id, status="failed", error_message=exc.message)
            raise
        await remote_repo.set_session_run(session_id, run_id)
        rmm_scripts.notify_agent(int(agent["id"]))
    elif builtin_mesh:
        try:
            run_id = await _queue_builtin_meshcentral(agent, mesh_group_id, user_id)
        except RemoteControlError as exc:
            await remote_repo.finish_session(session_id, status="failed", error_message=exc.message)
            raise
        await remote_repo.set_session_run(session_id, run_id)
        rmm_scripts.notify_agent(int(agent["id"]))
    elif script is None:
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
            # Activation never waits behind other scripts running on the device.
            run_id = await rmm_scripts.queue_for_agent(
                script=rmm_scripts.always_run(script, agent),
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
    status = await session_status(session_id, company_id=company_id, user_id=user_id, is_super_admin=False)
    if status is not None and provider == "meshcentral":
        # Opened in a new tab on the click, so the device is in view while the script runs.
        status["open_url"] = await meshcentral_open_url(asset_id)
    return status


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
        # Keep what MyPortal already set (the built-in script's password).
        merged = {**_decrypt_values(session), **kept}
        await remote_repo.store_values(int(session["id"]), encrypt_secret(json.dumps(merged)))
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
        return rustdesk_url(remote_id, values.get("password"), session.get("mode") or "remote")
    # MeshCentral's address, user and login token key come from the .env file.
    site, username, key = _meshcentral_config()
    mode = session.get("mode") or "remote"
    group_id = values.get("mesh_group_id")
    if group_id:
        # Built-in: the agent was installed into the company's group, so ask
        # MeshCentral for the node matching this device's host name.
        # Like Tactical RMM, prefer the node id the device's own agent reported
        # (meshagent -nodeid); find_node only accepts it inside the company's group.
        hosts = [h.strip() for h in str(values.get("mesh_host") or "").split(";") if h.strip()]
        reported = values.get("node_id")
        node_id = None
        if hosts or reported:
            node_id = await meshcentral.find_node(
                site, meshcentral_login_token(key, username), group_id, hosts,
                node_id=reported, attempts=3, delay_seconds=2.0,
            )
        if not node_id:
            # The agent has not checked in yet (or could not be matched); open
            # MeshCentral signed in so the device shows up once it is online.
            return meshcentral_url(site, username, key, None, mode=mode)
        return await _remembered_mesh_url(session, site, username, key, node_id, mode)
    node_id = values.get("node_id") or values.get("id") or await _field_value(
        int(session["asset_id"]), settings.get("id_field")
    )
    if not node_id:
        return meshcentral_url(site, username, key, None, mode=mode)
    return await _remembered_mesh_url(session, site, username, key, node_id, mode)


async def _remembered_mesh_url(session: Mapping[str, Any], site: str, username: str, key: str,
                               node_id: str, mode: str) -> str:
    """The link to ``node_id``, kept for the asset so later actions open it straight away."""

    url = meshcentral_url(site, username, key, node_id, mode=mode)
    await remote_repo.save_mesh_node(int(session["asset_id"]), node_id)
    return url


async def meshcentral_open_url(asset_id: int) -> str:
    """Where MeshCentral opens while the activation script runs.

    The device's general page when its node id is known from an earlier
    session, otherwise the device list filtered to the asset's name."""

    site, username, key = _meshcentral_config()
    node_id = await remote_repo.get_mesh_node(asset_id)
    if node_id:
        try:
            return meshcentral_url(site, username, key, node_id, overview=True)
        except RemoteControlError:
            pass
    asset = await assets_repo.get_asset_by_id(asset_id)
    return meshcentral_url(site, username, key, None, search=str((asset or {}).get("name") or ""))


async def _resolve(session: Mapping[str, Any], settings: Mapping[str, Any], values: Mapping[str, str]) -> str | None:
    try:
        url = await _launch_url(session, settings, values)
    except RemoteControlError as exc:
        await remote_repo.finish_session(int(session["id"]), status="failed", error_message=exc.message)
        return None
    await remote_repo.finish_session(int(session["id"]), status="ready")
    return url


def _first_error_line(stderr: Any) -> str:
    """The first line of what the script wrote to stderr, such as a PowerShell ``throw``."""

    for line in str(stderr or "").splitlines():
        line = line.strip()
        if line:
            return line[:300]
    return ""


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
            reason = run.get("error_message") or _first_error_line(run.get("stderr")) or (
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
            await remote_repo.finish_session(session_id, status="failed", error_message=exc.message)
            session = await remote_repo.get_session(session_id) or session
    return {
        "id": session["id"],
        "provider": session["provider"],
        "mode": session.get("mode") or "remote",
        "label": PROVIDER_LABELS.get(session["provider"], session["provider"]),
        "mode_label": MODE_LABELS.get(session.get("mode") or "remote", ""),
        "status": session["status"],
        "run_id": session.get("run_id"),
        "run_status": run["status"] if run else None,
        "launch_url": launch_url if session["status"] == "ready" else None,
        "error": session.get("error_message"),
        "expires_at": session["expires_at"].isoformat() if hasattr(session["expires_at"], "isoformat") else session["expires_at"],
    }
