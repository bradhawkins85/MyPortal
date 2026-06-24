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
import os
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from app.core.config import get_settings
from app.core.logging import log_error, log_info, log_warning
from app.repositories import assets as assets_repo
from app.repositories import companies as companies_repo
from app.repositories import site_settings as site_settings_repo
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
# Tactical RMM enrolment-token synchronisation
# ---------------------------------------------------------------------------


def trmm_client_token_field_name() -> str:
    """Return the TRMM client custom field name that stores tray install tokens."""

    return (
        os.getenv("TRMM_CLIENT_TOKEN_FIELD", "MyPortalToken").strip() or "MyPortalToken"
    )[:128]


async def ensure_company_install_token(
    *,
    company_id: int,
    created_by_user_id: int | None = None,
    label: str | None = None,
) -> tuple[dict[str, Any], str | None]:
    """Return an active company install-token row, creating one when needed.

    The raw token can only be returned when a new token is created. Existing
    token values are intentionally unrecoverable because only HMAC hashes are
    stored at rest.
    """

    tokens = await tray_repo.list_install_tokens(company_id=int(company_id))
    for token in tokens:
        if token.get("revoked_at"):
            continue
        expires_at = token.get("expires_at")
        if isinstance(expires_at, datetime):
            aware = expires_at.replace(tzinfo=expires_at.tzinfo or timezone.utc)
            if aware < datetime.now(timezone.utc):
                continue
        return token, None

    company = await companies_repo.get_company_by_id(int(company_id))
    raw_token = generate_install_token()
    record = await tray_repo.create_install_token(
        label=(label or f"{(company or {}).get('name') or 'Company'} TRMM tray token")[
            :150
        ],
        company_id=int(company_id),
        token_hash=hash_token(raw_token),
        token_prefix=token_prefix(raw_token),
        created_by_user_id=created_by_user_id,
    )
    return record, raw_token


def _extract_trmm_custom_field_id(
    client: dict[str, Any], field_name: str
) -> int | None:
    for field in client.get("custom_fields") or []:
        if not isinstance(field, dict):
            continue
        if str(field.get("name") or "").strip().casefold() != field_name.casefold():
            continue
        raw_id = field.get("field") or field.get("id") or field.get("pk")
        try:
            return int(raw_id)
        except (TypeError, ValueError):
            return None
    return None


def _coerce_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _iter_trmm_custom_field_definitions(response: Any) -> Iterable[dict[str, Any]]:
    if isinstance(response, list):
        for item in response:
            if isinstance(item, dict):
                yield item
        return
    if isinstance(response, dict):
        for key in ("results", "custom_fields", "items", "data"):
            value = response.get(key)
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        yield item
                return


def _extract_trmm_client_custom_field_definition_id(
    response: Any, field_name: str
) -> int | None:
    target_name = field_name.strip().casefold()
    for field in _iter_trmm_custom_field_definitions(response):
        name = str(field.get("name") or "").strip().casefold()
        model = str(field.get("model") or "").strip().casefold()
        if name != target_name or model != "client":
            continue
        field_id = _coerce_positive_int(
            field.get("id") or field.get("pk") or field.get("field")
        )
        if field_id is not None:
            return field_id
    return None


def _build_trmm_client_custom_field_payload(
    client: dict[str, Any], field_id: int, token: str
) -> list[dict[str, Any]]:
    """Return TRMM client custom-field values preserving unrelated fields.

    Tactical RMM client responses identify custom-field values by numeric
    definition IDs in the ``field`` property rather than by field name. When
    updating a single client field, include the existing field value ``id`` (if
    present) and keep the rest of the custom-field array intact so the PUT does
    not accidentally clear unrelated client custom fields.
    """

    payload: list[dict[str, Any]] = []
    found_target = False
    for existing in client.get("custom_fields") or []:
        if not isinstance(existing, dict):
            continue
        existing_field_id = _coerce_positive_int(existing.get("field"))
        if existing_field_id is None:
            continue
        item: dict[str, Any] = {
            "field": existing_field_id,
            "value": existing.get("value"),
        }
        value_id = _coerce_positive_int(existing.get("id") or existing.get("pk"))
        if value_id is not None:
            item["id"] = value_id
        if existing_field_id == field_id:
            item["value"] = token
            found_target = True
        payload.append(item)

    if not found_target:
        payload.append({"field": field_id, "value": token})
    return payload


async def update_trmm_client_token_field(
    *,
    trmm_client_id: str | int,
    token: str,
    field_name: str | None = None,
) -> dict[str, Any]:
    """Write a tray enrolment token into a Tactical RMM client custom field."""

    from app.services import tacticalrmm as tacticalrmm_service

    client_id = str(trmm_client_id or "").strip()
    if not client_id:
        raise ValueError("Tactical RMM client ID is required")
    custom_field_name = (field_name or trmm_client_token_field_name()).strip()
    if not custom_field_name:
        raise ValueError("Tactical RMM custom field name is required")

    client = await tacticalrmm_service._call_endpoint(
        f"/clients/{client_id}/", method="GET"
    )
    if not isinstance(client, dict):
        client = {}
    field_id = _extract_trmm_custom_field_id(client, custom_field_name)
    if field_id is None:
        definitions = await tacticalrmm_service._call_endpoint(
            "/core/customfields/", method="GET"
        )
        field_id = _extract_trmm_client_custom_field_definition_id(
            definitions, custom_field_name
        )
    if field_id is None:
        raise ValueError(
            f'Tactical RMM client custom field "{custom_field_name}" was not found'
        )

    custom_field_payload = _build_trmm_client_custom_field_payload(
        client, field_id, token
    )

    # Tactical RMM returns client custom-field values as ``{"field": <id>,
    # "value": ...}`` entries, often without the field name.  Send the same
    # value shape back and preserve unrelated fields so updating the tray token
    # does not clear other client-level custom fields.
    body = {"custom_fields": custom_field_payload}
    return await tacticalrmm_service._call_endpoint(
        f"/clients/{client_id}/", method="PUT", body=body
    )


async def sync_company_trmm_tray_token(
    *,
    company_id: int,
    created_by_user_id: int | None = None,
) -> dict[str, Any]:
    """Ensure a MyPortal tray token exists and publish it to TRMM for a company."""

    company = await companies_repo.get_company_by_id(int(company_id))
    if not company:
        return {
            "status": "skipped",
            "reason": "company_not_found",
            "company_id": company_id,
        }
    trmm_client_id = str(company.get("tacticalrmm_client_id") or "").strip()
    if not trmm_client_id:
        return {
            "status": "skipped",
            "reason": "missing_tacticalrmm_client_id",
            "company_id": company_id,
        }

    token_record, raw_token = await ensure_company_install_token(
        company_id=int(company_id), created_by_user_id=created_by_user_id
    )
    if raw_token is None:
        return {
            "status": "skipped",
            "reason": "existing_token_value_not_recoverable",
            "company_id": company_id,
            "token_id": token_record.get("id"),
        }

    await update_trmm_client_token_field(trmm_client_id=trmm_client_id, token=raw_token)
    log_info(
        "Published tray enrolment token to Tactical RMM client custom field",
        company_id=company_id,
        trmm_client_id=trmm_client_id,
        field=trmm_client_token_field_name(),
    )
    return {
        "status": "updated",
        "company_id": company_id,
        "token_id": token_record.get("id"),
    }


async def sync_all_company_trmm_tray_tokens(
    *, created_by_user_id: int | None = None
) -> dict[str, Any]:
    """Create missing tray enrolment tokens and publish new values to TRMM."""

    companies = await companies_repo.list_companies()
    summary: dict[str, Any] = {
        "processed": 0,
        "updated": 0,
        "skipped": [],
        "errors": [],
    }
    for company in companies:
        company_id = int(company.get("id") or 0)
        if company_id <= 0:
            continue
        summary["processed"] += 1
        try:
            result = await sync_company_trmm_tray_token(
                company_id=company_id, created_by_user_id=created_by_user_id
            )
            if result.get("status") == "updated":
                summary["updated"] += 1
            else:
                summary["skipped"].append(result)
        except Exception as exc:  # pragma: no cover - defensive integration logging
            log_error(
                "Failed to sync tray token to Tactical RMM",
                company_id=company_id,
                error=str(exc),
            )
            summary["errors"].append({"company_id": company_id, "error": str(exc)})
    return summary


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
    branding_display_name = await site_settings_repo.get_tray_icon_tooltip_name()
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
        env_allowlist = [v.strip() for v in str(raw_allow).split(",") if v.strip()]
        version = int(chosen.get("version") or 1)
    else:
        payload = list(_DEFAULT_MENU)

    return {
        "version": version,
        "menu": payload,
        "display_text": display_text,
        "branding_icon_url": branding_icon_url,
        "branding_display_name": branding_display_name,
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


async def deliver_queued_commands(device: dict[str, Any]) -> dict[str, int]:
    """Send queued commands to a freshly connected device.

    The admin device page logs commands as ``queued`` before attempting live
    websocket delivery.  If live delivery is impossible (for example the tray
    is offline or connected to another web worker), this reconnect drain is
    the reliable path that ensures the tray service eventually receives the
    command.
    """

    device_uid = str(device.get("device_uid") or "").strip()
    if not device_uid:
        return {"delivered": 0, "failed": 0}

    delivered = 0
    failed = 0
    commands = await tray_repo.get_queued_commands_for_device(int(device["id"]))
    for command in commands:
        payload: dict[str, Any]
        raw_payload = command.get("payload_json")
        if raw_payload:
            try:
                parsed = json.loads(raw_payload)
                payload = parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                payload = {}
        else:
            payload = {}
        payload.setdefault("type", command.get("command"))
        payload.setdefault("command_id", command.get("id"))

        if await send_to_device(device_uid, payload):
            await tray_repo.mark_command_delivered(int(command["id"]))
            delivered += 1
        else:
            failed += 1
            break

    if delivered or failed:
        log_info(
            "Tray queued command delivery complete",
            device_uid=device_uid,
            delivered=delivered,
            failed=failed,
        )
    return {"delivered": delivered, "failed": failed}


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

    active_devices = await tray_repo.list_devices(
        company_id=int(company_id), status="active"
    )
    allowed_asset_ids = {
        int(asset_id)
        for asset_id in (asset_ids or [])
        if isinstance(asset_id, int) or str(asset_id).isdigit()
    }
    target_devices = [
        device
        for device in active_devices
        if not allowed_asset_ids
        or int(device.get("asset_id") or 0) in allowed_asset_ids
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


def technician_can_initiate(
    user: dict[str, Any], company: dict[str, Any] | None
) -> bool:
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
