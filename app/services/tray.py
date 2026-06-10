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
from app.repositories import companies as companies_repo
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
    {"type": "label", "label": "Contact Support"},
    {"type": "label", "label": "Email: myportal@company.com.au"},
    {"type": "label", "label": "Phone: 0755000000"},
    {"type": "separator"},
    {"type": "link", "label": "Submit Ticket", "url": "https://www.google.com"},
    {"type": "separator"},
    {"type": "link", "label": "Knowledge Base", "url": "/kb"},
    {"type": "separator"},
    {"type": "open_chat", "label": "Chat"},
]


def _normalise_company_id_list(value: Any) -> list[int]:
    """Return positive company IDs from a menu-node condition field."""

    if value is None or value == "":
        return []
    raw_items: Iterable[Any]
    if isinstance(value, (list, tuple, set)):
        raw_items = value
    else:
        raw_items = str(value).split(",")

    ids: list[int] = []
    seen: set[int] = set()
    for item in raw_items:
        try:
            company_id = int(str(item).strip())
        except (TypeError, ValueError):
            continue
        if company_id <= 0 or company_id in seen:
            continue
        ids.append(company_id)
        seen.add(company_id)
    return ids


def _node_is_visible_for_company(node: dict[str, Any], company_id: int | None) -> bool:
    """Return whether a menu node passes company visibility conditions."""

    include_ids = _normalise_company_id_list(node.get("visible_company_ids"))
    exclude_ids = _normalise_company_id_list(node.get("hidden_company_ids"))
    if include_ids and company_id not in include_ids:
        return False
    if company_id is not None and company_id in exclude_ids:
        return False
    return True


def _filter_menu_nodes_for_company(
    nodes: list[dict[str, Any]],
    company_id: int | None,
) -> list[dict[str, Any]]:
    """Recursively remove menu nodes hidden for the device's assigned company."""

    filtered: list[dict[str, Any]] = []
    for raw_node in nodes:
        if not isinstance(raw_node, dict):
            continue
        if not _node_is_visible_for_company(raw_node, company_id):
            continue
        node = dict(raw_node)
        children = node.get("children")
        if isinstance(children, list):
            node["children"] = _filter_menu_nodes_for_company(children, company_id)
            node_type = str(node.get("type") or "").strip().lower()
            if node_type == "submenu" and not node["children"]:
                continue
        filtered.append(node)
    return filtered


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
            if not isinstance(payload, list):
                payload = _DEFAULT_MENU
        except (ValueError, TypeError):
            payload = _DEFAULT_MENU
        payload = _filter_menu_nodes_for_company(payload, company_id)
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


async def push_notification_to_company_devices(
    *,
    company_id: int | None,
    title: str,
    body: str,
    asset_ids: Iterable[int] | None = None,
    initiated_by_user_id: int | None = None,
) -> dict[str, int]:
    """Push a tray notification to active company devices.

    Returns a delivery summary ``{"targeted": n, "delivered": n, "queued": n}``.
    """

    if company_id is None:
        return {"targeted": 0, "delivered": 0, "queued": 0}
    company = await companies_repo.get_company_by_id(int(company_id))
    if not company or not company.get("tray_notifications_enabled"):
        return {"targeted": 0, "delivered": 0, "queued": 0}

    active_devices = await tray_repo.list_devices(company_id=int(company_id), status="active")
    allowed_asset_ids = {
        int(asset_id)
        for asset_id in (asset_ids or [])
        if isinstance(asset_id, int) or str(asset_id).isdigit()
    }
    target_devices = [
        device
        for device in active_devices
        if not allowed_asset_ids or int(device.get("asset_id") or 0) in allowed_asset_ids
    ]
    if not target_devices:
        return {"targeted": 0, "delivered": 0, "queued": 0}

    payload = {
        "type": "show_notification",
        "payload": {
            "title": str(title or "MyPortal").strip()[:200],
            "body": str(body or "").strip()[:1000],
        },
    }
    payload_json = json.dumps(payload)

    delivered_count = 0
    queued_count = 0
    for device in target_devices:
        device_uid = str(device.get("device_uid") or "").strip()
        if not device_uid:
            continue
        delivered = await send_to_device(device_uid, payload)
        await tray_repo.log_command(
            device_id=int(device["id"]),
            command="show_notification",
            payload_json=payload_json,
            initiated_by_user_id=initiated_by_user_id,
            status="delivered" if delivered else "queued",
        )
        if delivered:
            delivered_count += 1
        else:
            queued_count += 1

    return {
        "targeted": delivered_count + queued_count,
        "delivered": delivered_count,
        "queued": queued_count,
    }


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
