"""Service helpers for the MyPortal Tray App.

Responsibilities
----------------

* Generate / hash install tokens and per-device auth tokens.
* Resolve the effective tray menu config for a device (precedence:
  device > tag > company > global).
* Match enrolling devices to existing assets using serial / hostname
  heuristics borrowed from :mod:`app.services.asset_importer`.
* Dispatch server-initiated commands (``chat_open``, ``config_changed`` …)
  to a connected tray device over the realtime connection.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from app.core.config import get_settings
from app.core.logging import log_info, log_warning
from app.repositories import assets as assets_repo
from app.repositories import tray as tray_repo

_settings = get_settings()


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------


def generate_install_token() -> str:
    """Return a fresh install token (URL-safe, ~43 chars)."""

    return secrets.token_urlsafe(32)


def generate_auth_token() -> str:
    """Return a fresh per-device auth token (URL-safe, ~64 chars)."""

    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    """Return a deterministic hash for ``token``.

    The hash is salted with the application secret so a leaked database
    cannot be used to brute-force the token offline. SHA-256 is sufficient
    here because tokens are 256+ bits of entropy generated above; we are
    not protecting against weak passwords, only obscuring the value at
    rest. Using HMAC binds the hash to this MyPortal install.
    """

    import hmac

    key = (_settings.secret_key or "").encode("utf-8") or b"myportal-tray"
    digest = hmac.new(key, token.encode("utf-8"), hashlib.sha256).hexdigest()
    return digest


def token_prefix(token: str) -> str:
    return token[:8]


# ---------------------------------------------------------------------------
# Asset linking
# ---------------------------------------------------------------------------


async def find_matching_asset(
    *,
    company_id: int | None,
    serial_number: str | None,
    hostname: str | None,
) -> int | None:
    """Try to associate an enrolling device with an existing asset.

    Matching is intentionally conservative: we only auto-link when there is
    a single, unambiguous candidate.  Anything else is left for an admin to
    resolve manually from the Devices page.
    """

    if not (serial_number or hostname) or company_id is None:
        return None
    try:
        company_assets = await assets_repo.list_company_assets(int(company_id))
    except Exception:  # pragma: no cover - defensive
        return None

    candidates: list[dict[str, Any]] = []
    if serial_number:
        target = serial_number.strip().lower()
        candidates = [
            a
            for a in company_assets
            if str(a.get("serial_number") or "").strip().lower() == target
        ]
    if not candidates and hostname:
        target = hostname.strip().lower()
        candidates = [
            a
            for a in company_assets
            if str(a.get("name") or "").strip().lower() == target
        ]
    if len(candidates) == 1:
        try:
            return int(candidates[0].get("id"))
        except (TypeError, ValueError):
            return None
    return None


# ---------------------------------------------------------------------------
# Menu config resolution
# ---------------------------------------------------------------------------


_DEFAULT_MENU: list[dict[str, Any]] = [
    {"type": "label", "label": "MyPortal"},
    {"type": "separator"},
    {"type": "open_chat", "label": "Chat with helpdesk"},
    {"type": "separator"},
    {"type": "label", "label": "No menu configured"},
]


async def resolve_config_for_device(device: dict[str, Any]) -> dict[str, Any]:
    """Return the menu config payload + branding to send to ``device``.

    Precedence order: device-scoped > tag-scoped > company-scoped > global.
    Tag matching is a placeholder for now — wired in once asset tags are
    fully exposed in the repository layer.
    """

    configs = await tray_repo.list_menu_configs()
    enabled = [c for c in configs if c.get("enabled")]
    asset_id = device.get("asset_id")
    company_id = device.get("company_id")

    chosen: dict[str, Any] | None = None
    for scope in ("device", "tag", "company", "global"):
        for cfg in enabled:
            if cfg.get("scope") != scope:
                continue
            if scope == "device" and cfg.get("scope_ref_id") != asset_id:
                continue
            if scope == "company" and cfg.get("scope_ref_id") != company_id:
                continue
            chosen = cfg
            break
        if chosen:
            break

    payload: list[dict[str, Any]]
    display_text: str | None = None
    branding_icon_url: str | None = None
    env_allowlist: list[str] = []
    version = 0

    if chosen:
        try:
            payload = json.loads(chosen.get("payload_json") or "[]") or _DEFAULT_MENU
        except (ValueError, TypeError):
            payload = _DEFAULT_MENU
        display_text = chosen.get("display_text")
        branding_icon_url = chosen.get("branding_icon_url")
        raw_allow = chosen.get("env_allowlist") or ""
        env_allowlist = [
            v.strip()
            for v in str(raw_allow).split(",")
            if v.strip()
        ]
        version = int(chosen.get("version") or 1)
    else:
        payload = list(_DEFAULT_MENU)

    return {
        "version": version,
        "menu": payload,
        "display_text": display_text,
        "branding_icon_url": branding_icon_url,
        "env_allowlist": env_allowlist,
    }


def is_env_var_allowed(name: str, allowlist: Iterable[str]) -> bool:
    """Case-insensitive membership check used by the WS env-snapshot path."""

    target = name.strip().lower()
    return any(item.strip().lower() == target for item in allowlist if item)


# ---------------------------------------------------------------------------
# Device UID
# ---------------------------------------------------------------------------


def normalise_device_uid(value: str | None) -> str:
    """Return a clean device UID, generating one if not supplied."""

    if not value:
        return uuid.uuid4().hex
    cleaned = "".join(ch for ch in value if ch.isalnum() or ch in "-_")
    return cleaned[:64] or uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Command dispatch (consumed by app/main.py WS handler)
# ---------------------------------------------------------------------------


_active_connections: dict[str, Any] = {}


def register_connection(device_uid: str, websocket: Any) -> None:
    _active_connections[device_uid] = websocket


def unregister_connection(device_uid: str, websocket: Any) -> None:
    current = _active_connections.get(device_uid)
    if current is websocket:
        _active_connections.pop(device_uid, None)


def is_device_connected(device_uid: str) -> bool:
    return device_uid in _active_connections


async def send_to_device(
    device_uid: str,
    payload: dict[str, Any],
) -> bool:
    """Best-effort send to a connected tray device.

    Returns ``True`` when the message reached the local websocket. Cross-node
    fan-out (Redis pub/sub) is left for a follow-up — for the MVP we deliver
    only when the device's WS is on this app instance.
    """

    websocket = _active_connections.get(device_uid)
    if websocket is None:
        return False
    try:
        await websocket.send_json(payload)
        return True
    except Exception as exc:  # pragma: no cover - defensive
        log_warning("Tray websocket send failed", device_uid=device_uid, error=str(exc))
        unregister_connection(device_uid, websocket)
        return False


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


def technician_can_initiate(user: dict[str, Any], company: dict[str, Any] | None) -> bool:
    """Return True when ``user`` is allowed to push a chat to a device.

    The per-company toggle ``tray_chat_enabled`` is the primary gate; super
    admins can always initiate.
    """

    if user.get("is_super_admin"):
        return True
    if not company:
        return False
    if not company.get("tray_chat_enabled"):
        return False
    return bool(user.get("is_helpdesk_technician"))
