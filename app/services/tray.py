"""Service helpers for the MyPortal Tray App.

Responsibilities
----------------

* Generate / hash install tokens and per-device auth tokens.
* Resolve the effective tray menu config for a device (precedence:
  device > tag > company > global), then filter its items by company and
  advanced device rules.
* Match enrolling devices to existing assets using serial / hostname
  heuristics borrowed from :mod:`app.services.asset_importer`.
* Dispatch server-initiated commands (``chat_open``, ``config_changed`` …)
  to a connected tray device over the realtime connection.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import os
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable
from weakref import WeakValueDictionary

from app.core.ai_identity import TROUBLESHOOT_AGENT_EMAIL, get_agent_name, personalise_agent_text
from app.core.config import get_settings
from app.core.logging import log_error, log_info, log_warning
from app.repositories import assets as assets_repo
from app.repositories import companies as companies_repo
from app.repositories import m365_signatures as m365_signatures_repo
from app.repositories import site_settings as site_settings_repo
from app.repositories import tray as tray_repo
from app.services import ai_consent
from app.services import service_status as service_status_service

_settings = get_settings()

SERVICE_STATUS_URL_VARIABLE = "{{service_status_url}}"
_setup_ticket_locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()


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
    company_assets = await assets_repo.list_company_assets(int(company_id))

    candidates: list[dict[str, Any]] = []
    company_assets = [a for a in company_assets if not a.get("archived_at")]
    if serial_number and not assets_repo.is_placeholder_serial(serial_number):
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


async def ensure_device_asset(device: dict[str, Any]) -> int | None:
    """Give a company-owned active device a canonical asset and repair RMM links."""
    if device.get("company_id") is None or device.get("status") != "active":
        return device.get("asset_id")
    company_id = int(device["company_id"])
    asset_id = device.get("asset_id")
    asset = await assets_repo.get_asset_by_id(int(asset_id)) if asset_id else None
    if not asset or int(asset["company_id"]) != company_id:
        asset_id = await tray_repo.get_device_rmm_asset(int(device["id"]), company_id)
        if not asset_id:
            asset_id = await find_matching_asset(
                company_id=company_id, serial_number=device.get("serial_number"),
                hostname=device.get("hostname"),
            )
        if not asset_id:
            asset_id = await assets_repo.create_tray_asset(device)
        await tray_repo.link_device_to_asset(int(device["id"]), int(asset_id))
        device["asset_id"] = int(asset_id)
    await tray_repo.link_device_rmm_assets(int(device["id"]), company_id, int(asset_id))
    return int(asset_id)


# Retain in-flight work after the HTTP response, and share it between check-ins.
_device_provision_tasks: dict[int, asyncio.Task] = {}
PROVISION_CHECKIN_WAIT_SECONDS = 1.0
PROVISION_TIMEOUT_SECONDS = 120.0


async def provision_device_for_checkin(device: dict[str, Any]) -> int | None:
    """Provision without holding enrolment credentials or liveness behind slow work.

    Quick provisioning still returns the asset in the enrolment response. Slow
    work continues after the response; failed work is retried at the next check-in.
    Device/asset identities and the setup-ticket lock also protect across workers.
    """
    if device.get("company_id") is None or device.get("status") != "active":
        return device.get("asset_id")
    device_id = int(device["id"])
    task = _device_provision_tasks.get(device_id)
    if task is None:
        async def provision() -> int | None:
            try:
                async with asyncio.timeout(PROVISION_TIMEOUT_SECONDS):
                    asset_id = await ensure_device_asset(device)
                    await ensure_device_setup_ticket(device)
                    return asset_id
            except Exception as exc:
                log_error(
                    "Could not provision tray device; retrying on next check-in",
                    device_uid=device.get("device_uid"),
                    error=f"{type(exc).__name__}: {exc}",
                )
                return device.get("asset_id")
            finally:
                _device_provision_tasks.pop(device_id, None)

        task = asyncio.create_task(provision(), name=f"tray-provision:{device_id}")
        _device_provision_tasks[device_id] = task
    try:
        asset_id = await asyncio.wait_for(
            asyncio.shield(task), timeout=PROVISION_CHECKIN_WAIT_SECONDS,
        )
        if asset_id:
            device["asset_id"] = asset_id
    except TimeoutError:
        # The slow check-in is retried on the next poll; the provisioning
        # task keeps running in the background.
        pass
    return device.get("asset_id")


async def reconcile_device_assets() -> None:
    """Backfill existing devices at startup, retrying failures on the next run."""
    for device in await tray_repo.list_devices(status="active"):
        try:
            await ensure_device_asset(device)
        except Exception as exc:
            log_error("Could not reconcile tray device asset", device_uid=device.get("device_uid"), error=str(exc))


async def ensure_device_setup_ticket(device: dict[str, Any]) -> None:
    """Create one setup ticket per new enrolment, retrying partial failures.

    The migration marks old devices processed. A disabled company's first
    enrolment is also marked processed, so enabling later does not backfill it.
    Ticket failures must never prevent the agent from enrolling or checking in.
    """
    if device.get("setup_ticket_processed", 1) or device.get("status") != "active":
        return
    from app.repositories import tickets as tickets_repo
    from app.services import tickets as tickets_service

    device_id = int(device["id"])
    lock = _setup_ticket_locks.setdefault(device_id, asyncio.Lock())
    try:
        # Local locking also serialises SQLite coroutines; the database lock
        # coordinates MySQL workers. Always re-read after acquiring both.
        async with lock, tray_repo.db.acquire_lock(f"tray-setup-ticket:{device_id}") as acquired:
            if not acquired:
                return
            current = await tray_repo.get_device_by_id(device_id)
            if not current or current.get("status") != "active":
                return
            if current.get("setup_ticket_processed", 1):
                device["setup_ticket_processed"] = 1
                return
            company_id = current.get("company_id")
            if company_id is None:
                return
            company = await companies_repo.get_company_by_id(int(company_id))
            if not company:
                return
            if company.get("tray_setup_tickets_enabled", True):
                asset_id = await ensure_device_asset(current)
                if not asset_id:
                    return
                reference = f"tray-setup:{device_id}"
                # If creation succeeded but linking/marking failed, reuse the
                # same ticket on the next check-in, including closed tickets.
                ticket = await tickets_repo.get_ticket_by_external_reference(reference)
                if ticket and int(ticket["company_id"]) != int(company_id):
                    raise ValueError("Setup ticket belongs to another company")
                if not ticket:
                    label = current.get("hostname") or current["device_uid"]
                    ticket = await tickets_service.create_ticket(
                        subject=f"New device setup required: {label}"[:255],
                        description=(
                            "A new tray agent has checked in for the first time. "
                            "Perform the required setup of this device.\n\n"
                            f"Device: {label}\n"
                            f"Serial number: {current.get('serial_number') or 'Not reported'}\n"
                            f"Operating system: {current.get('os') or 'Not reported'} "
                            f"{current.get('os_version') or ''}\n"
                            f"Console user: {current.get('console_user') or 'Not reported'}\n"
                        ),
                        requester_id=None, company_id=int(company_id),
                        assigned_user_id=None, priority="normal", status="open",
                        category=None, module_slug="tray",
                        external_reference=reference, trigger_automations=True,
                    )
                await tickets_repo.replace_ticket_assets(int(ticket["id"]), [int(asset_id)])
            await tray_repo.mark_setup_ticket_processed(device_id)
            device["setup_ticket_processed"] = 1
    except Exception as exc:
        log_error("Could not create tray device setup ticket", device_id=device_id, error=str(exc))


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


def _collect_rmm_script_ids(nodes: list[dict[str, Any]]) -> set[int]:
    ids: set[int] = set()
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if str(node.get("type") or "").strip().lower() == "rmm_script":
            try:
                ids.add(int(node.get("script_id") or 0))
            except (TypeError, ValueError):
                # A malformed script_id is ignored; the remaining nodes are still scanned.
                pass
        children = node.get("children")
        if isinstance(children, list):
            ids |= _collect_rmm_script_ids(children)
    return ids


def _platform_from_os_name(value: Any) -> str:
    """Map a free-form OS name ("macOS 14.5", "Microsoft Windows 11 Pro") to a
    script platform key, or ``""`` when it is not recognised."""

    from app.services import rmm_scripts

    name = str(value or "").strip().lower()
    if not name:
        return ""
    exact = rmm_scripts.normalise_platform(name)
    if exact in rmm_scripts.SCRIPT_PLATFORMS:
        return exact
    for needle, platform in (
        ("windows", "windows"),
        ("macos", "darwin"),
        ("mac os", "darwin"),
        ("os x", "darwin"),
        ("darwin", "darwin"),
        ("freebsd", "freebsd"),
        ("openbsd", "openbsd"),
        ("netbsd", "netbsd"),
        ("linux", "linux"),
        ("ubuntu", "linux"),
        ("debian", "linux"),
    ):
        if needle in name:
            return platform
    return ""


async def _device_platform(device: dict[str, Any]) -> str:
    """The script platform of ``device``'s computer, or ``""`` when unknown.

    Older tray enrolments did not record an OS, so fall back to the linked
    asset's RMM agent (the one that actually runs the script) and then the
    asset's OS name rather than showing every item.
    """

    platform = _platform_from_os_name(device.get("os"))
    if platform:
        return platform
    asset_id = device.get("asset_id")
    if not asset_id:
        return ""
    try:
        from app.repositories import rmm as rmm_repo

        agent = await rmm_repo.get_asset_agent(int(asset_id))
    except Exception:  # noqa: BLE001 - RMM feature pack may be absent
        agent = None
    if agent:
        platform = _platform_from_os_name(agent.get("os"))
        if platform:
            return platform
    asset = await assets_repo.get_asset_by_id(int(asset_id))
    return _platform_from_os_name((asset or {}).get("os_name"))


async def _filter_rmm_scripts_for_platform(
    nodes: list[dict[str, Any]], device: dict[str, Any]
) -> list[dict[str, Any]]:
    """Drop ``rmm_script`` items whose script is not enabled for the device's OS.

    A Windows-only script is hidden from a Mac's tray menu (and vice versa).
    Devices whose platform cannot be determined keep every item; the run
    endpoint still refuses a script the device's platform cannot run.
    """

    from app.repositories import rmm as rmm_repo
    from app.services import rmm_scripts

    platform = await _device_platform(device)
    script_ids = _collect_rmm_script_ids(nodes)
    if not platform or not script_ids:
        return nodes
    allowed: set[int] = set()
    for script_id in script_ids:
        script = await rmm_repo.get_script(script_id) if script_id else None
        if script and rmm_scripts.platform_allowed(script, {"os": platform}):
            allowed.add(script_id)

    def keep(node_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
        kept: list[dict[str, Any]] = []
        for raw_node in node_list:
            if not isinstance(raw_node, dict):
                continue
            node = dict(raw_node)
            node_type = str(node.get("type") or "").strip().lower()
            if node_type == "rmm_script":
                try:
                    script_id = int(node.get("script_id") or 0)
                except (TypeError, ValueError):
                    script_id = 0
                if script_id not in allowed:
                    continue
            children = node.get("children")
            if isinstance(children, list):
                node["children"] = keep(children)
                if node_type == "submenu" and not node["children"]:
                    continue
            kept.append(node)
        return kept

    return keep(nodes)


async def _expand_menu_url_variables(
    nodes: list[dict[str, Any]], company_id: int | None
) -> list[dict[str, Any]]:
    """Resolve server-owned link variables while retaining legacy link nodes.

    The tray clients already know how to open ``link`` nodes, so resolving the
    URL here makes Service Status available without requiring an agent update.
    """

    company = None
    if company_id is not None:
        company = await companies_repo.get_company_by_id(int(company_id))
    status_url = None
    portal_base_url = str(_settings.portal_url or "").strip().rstrip("/")
    if company and int(company.get("archived") or 0) != 1 and portal_base_url:
        token = service_status_service.build_public_status_token(
            int(company_id),
            seed=service_status_service.public_status_token_seed(company),
        )
        status_url = (
            f"{portal_base_url}/service-status/public/{int(company_id)}/{token}"
        )

    def expand(node_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
        expanded: list[dict[str, Any]] = []
        for raw_node in node_list:
            node = dict(raw_node)
            if node.get("type") == "link" and node.get("url") == SERVICE_STATUS_URL_VARIABLE:
                if status_url is None:
                    continue
                node["url"] = status_url
            children = node.get("children")
            if isinstance(children, list):
                node["children"] = expand(children)
                if node.get("type") == "submenu" and not node["children"]:
                    continue
            expanded.append(node)
        return expanded

    return expand(nodes)


def _node_rules(node: dict[str, Any]) -> Any:
    """The rule tree on a menu node: ``None`` when it has none, ``False`` when
    it is unreadable (so the item is hidden rather than shown to everyone)."""

    raw = node.get("target_rules")
    if raw is None or raw == "" or raw == {}:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return False
    return raw if isinstance(raw, dict) else False


def normalise_menu_rules(nodes: list[Any], path: str = "") -> None:
    """Validate and normalise every item's ``target_rules`` in place.

    Raises ``ValueError`` naming the item when a rule tree is invalid. Empty
    rules are removed so the item shows on every device again.
    """

    from app.services import rmm_targeting

    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        where = f"{path}{index + 1}"
        raw = node.get("target_rules")
        if raw is None or raw == "" or raw == {}:
            node.pop("target_rules", None)
        else:
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except (ValueError, TypeError):
                    raise ValueError(f"Menu item {where}: device rules are not valid JSON") from None
            label = str(node.get("label") or node.get("type") or "")
            try:
                node["target_rules"] = rmm_targeting.validate_rules(raw)
            except ValueError as exc:
                raise ValueError(f"Menu item {where} ({label}): {exc}") from None
        children = node.get("children")
        if isinstance(children, list):
            normalise_menu_rules(children, f"{where}.")


def _menu_has_rules(nodes: list[dict[str, Any]]) -> bool:
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if _node_rules(node) is not None:
            return True
        children = node.get("children")
        if isinstance(children, list) and _menu_has_rules(children):
            return True
    return False


def _filter_menu_nodes_for_rules(
    nodes: list[dict[str, Any]], context: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Drop items whose advanced device rules do not match ``context``.

    Items without rules are always kept. An item with rules is hidden when the
    device's inventory is unknown (``context`` is ``None``) or its rules are
    invalid. Submenus emptied by filtering are removed, and ``target_rules`` is
    never sent to the device.
    """

    from app.services import rmm_targeting

    filtered: list[dict[str, Any]] = []
    for raw_node in nodes:
        if not isinstance(raw_node, dict):
            continue
        rules = _node_rules(raw_node)
        if rules is not None and not (
            rules and context is not None and rmm_targeting.matches_rules(rules, context)
        ):
            continue
        node = {key: value for key, value in raw_node.items() if key != "target_rules"}
        children = node.get("children")
        if isinstance(children, list):
            node["children"] = _filter_menu_nodes_for_rules(children, context)
            if str(node.get("type") or "").strip().lower() == "submenu" and not node["children"]:
                continue
        filtered.append(node)
    return filtered


def config_in_scope(cfg: dict[str, Any], device: dict[str, Any]) -> bool:
    scope = cfg.get("scope")
    if scope == "device":
        return cfg.get("scope_ref_id") == device.get("asset_id")
    if scope == "company":
        return cfg.get("scope_ref_id") == device.get("company_id")
    return scope in ("tag", "global")


async def device_rule_context(device: dict[str, Any]) -> dict[str, Any] | None:
    """The advanced-rule inventory context for a tray device.

    Uses the linked asset's RMM agent inventory (software, hardware, network)
    when there is one, otherwise the asset, company, tags and custom fields
    with the tray device's own hostname/OS/IP. ``None`` when the device has no
    company, so items with device rules are hidden from it.
    """

    from app.repositories import rmm_targeting as targeting_repo

    company_id = device.get("company_id")
    asset_id = device.get("asset_id")
    if not company_id:
        return None
    if asset_id:
        try:
            from app.repositories import rmm as rmm_repo

            agent = await rmm_repo.get_asset_agent(int(asset_id))
        except Exception:  # noqa: BLE001 - RMM feature pack may be absent
            agent = None
        if agent and int(agent.get("company_id") or 0) == int(company_id):
            try:
                contexts = await targeting_repo.contexts_for_agents([agent])
            except Exception as exc:  # noqa: BLE001 - fall back to asset data
                log_warning("Tray menu rule context failed", device_id=device.get("id"), error=str(exc))
                contexts = {}
            context = contexts.get(int(agent["id"]))
            if context is not None:
                return context
    try:
        return await targeting_repo.context_for_asset(
            company_id, asset_id, agent={
                "hostname": device.get("hostname"),
                "os": device.get("os"),
                "os_version": device.get("os_version"),
                "last_ip": device.get("last_ip"),
            },
        )
    except Exception as exc:  # noqa: BLE001 - never block the menu on inventory
        log_warning("Tray menu rule context failed", device_id=device.get("id"), error=str(exc))
        return None


async def select_config_for_device(
    configs: list[dict[str, Any]], device: dict[str, Any]
) -> dict[str, Any] | None:
    """Pick the enabled menu config for ``device``: device > tag > company > global."""

    enabled = [c for c in configs if c.get("enabled") and config_in_scope(c, device)]
    for scope in ("device", "tag", "company", "global"):
        for cfg in enabled:
            if cfg.get("scope") == scope:
                return cfg
    return None


SIGNATURE_REFRESH_NODE_TYPE = "refresh_signatures"
SIGNATURE_REFRESH_LABEL = "Refresh Email Signatures"


async def _signature_refresh_available(company_id: Any) -> bool:
    """True when the company syncs Classic Outlook signatures from a published template."""

    if not company_id:
        return False
    company = await companies_repo.get_company_by_id(int(company_id))
    if not company or not company.get("classic_outlook_signatures_enabled"):
        return False
    templates = await m365_signatures_repo.list_templates(int(company_id))
    return any(str(template.get("status") or "") == "published" for template in templates)


def _apply_signature_refresh_item(
    nodes: list[dict[str, Any]], available: bool
) -> list[dict[str, Any]]:
    """Show "Refresh Email Signatures" only to companies with signature templates.

    Unavailable companies have every ``refresh_signatures`` node removed. For
    available companies a menu that does not place the item itself gets one
    added before a trailing *Quit* item, or at the end.
    """

    def _strip(items: list[Any]) -> tuple[list[Any], bool]:
        kept: list[Any] = []
        found = False
        for node in items:
            if not isinstance(node, dict):
                kept.append(node)
                continue
            if str(node.get("type") or "").strip().lower() == SIGNATURE_REFRESH_NODE_TYPE:
                found = True
                if available:
                    kept.append(node)
                continue
            children = node.get("children")
            if isinstance(children, list):
                node = dict(node)
                node["children"], child_found = _strip(children)
                found = found or child_found
            kept.append(node)
        return kept, found

    result, found = _strip(list(nodes))
    if available and not found:
        item = {"type": SIGNATURE_REFRESH_NODE_TYPE, "label": SIGNATURE_REFRESH_LABEL}
        position = len(result)
        if result and isinstance(result[-1], dict) and str(result[-1].get("type") or "").lower() == "quit":
            position -= 1
        result.insert(position, item)
    return result


async def resolve_config_for_device(device: dict[str, Any]) -> dict[str, Any]:
    """Return the menu config payload + branding to send to ``device``.

    Precedence order: device-scoped > tag-scoped > company-scoped > global.
    Each device asks for its own menu (by its device UID / auth token), so
    menu items with advanced device rules are filtered per asset.
    Tag matching is a placeholder for now — wired in once asset tags are
    fully exposed in the repository layer.
    """

    configs = await tray_repo.list_menu_configs()
    company_id = device.get("company_id")
    chosen = await select_config_for_device(configs, device)

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
        if _menu_has_rules(payload):
            payload = _filter_menu_nodes_for_rules(payload, await device_rule_context(device))
        display_text = chosen.get("display_text")
        branding_icon_url = chosen.get("branding_icon_url")
        raw_allow = chosen.get("env_allowlist") or ""
        env_allowlist = [v.strip() for v in str(raw_allow).split(",") if v.strip()]
        version = int(chosen.get("version") or 1)
    else:
        payload = list(_DEFAULT_MENU)

    payload = await _expand_menu_url_variables(payload, company_id)
    payload = await _filter_rmm_scripts_for_platform(payload, device)
    payload = _apply_signature_refresh_item(
        payload, await _signature_refresh_available(company_id)
    )

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


# A device whose WebSocket touched ``ws_last_seen_utc`` within this window is
# treated as live on *some* app worker (it re-drains queued commands on its
# next keep-alive, so a queued command is picked up within ~20 seconds).
WEBSOCKET_PRESENCE_WINDOW_SECONDS = 90


async def mark_websocket_alive(device: dict[str, Any]) -> None:
    """Best-effort record that ``device`` holds a live WebSocket."""

    if not device.get("asset_id") or not device.get("setup_ticket_processed", 1):
        await provision_device_for_checkin(device)
    try:
        await tray_repo.touch_device_websocket(int(device["id"]))
    except Exception as exc:  # noqa: BLE001 - presence is diagnostic only
        log_warning(
            "Failed to record tray WebSocket presence",
            device_uid=device.get("device_uid"),
            error=str(exc),
        )


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def websocket_recently_seen(device: dict[str, Any], *, now: datetime | None = None) -> bool:
    """Return True when the device's WebSocket was active very recently."""

    seen = _as_utc(device.get("ws_last_seen_utc"))
    if seen is None:
        return False
    current = now or datetime.now(timezone.utc)
    return (current - seen).total_seconds() <= WEBSOCKET_PRESENCE_WINDOW_SECONDS


# How often an open device WebSocket re-checks the command log for commands
# that were queued by another worker (see ``/ws/tray/{device_uid}``).
QUEUED_COMMAND_DRAIN_INTERVAL_SECONDS = 15.0


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


CONFIG_CHANGED_MESSAGE: dict[str, Any] = {"type": "config_changed"}


async def broadcast_config_changed() -> int:
    """Tell every connected tray device to re-fetch its menu config.

    Devices cache the resolved menu and only re-fetch it on start-up, on a
    manual refresh or on ``config_changed``. Anything that changes what
    :func:`resolve_config_for_device` returns (menu edits, a script's allowed
    platforms) must call this or devices keep showing the stale menu.
    Returns how many devices were notified on this app instance.
    """

    sent = 0
    for device_uid in list(_active_connections):
        if await send_to_device(device_uid, dict(CONFIG_CHANGED_MESSAGE)):
            sent += 1
    return sent


# Key in a troubleshoot command payload holding state only the server reads.
SERVER_CONTEXT_KEY = "server_context"


TROUBLESHOOT_AI_OPT_OUT_DETAIL = (
    "The ticket's requester has opted out of AI processing, so the AI "
    "troubleshooter cannot run on this ticket."
)


async def _troubleshoot_still_allowed(payload: dict[str, Any]) -> bool:
    """Re-check AI consent for a queued troubleshoot command (fails closed)."""

    ticket_id = payload.get("ticket_id")
    if ticket_id in (None, ""):
        return False
    return await ai_consent.is_ai_allowed_for_ticket_id(ticket_id)


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
        # Server-side troubleshooter state stays on the server.
        payload.pop(SERVER_CONTEXT_KEY, None)

        if command.get("command") == "troubleshoot" and not await _troubleshoot_still_allowed(payload):
            # The requester opted out of AI after the command was queued:
            # never hand the ticket-derived prompt to the device.
            await tray_repo.mark_command_completed(
                int(command["id"]), error=TROUBLESHOOT_AI_OPT_OUT_DETAIL
            )
            log_info(
                "Dropped queued troubleshoot command: requester opted out of AI",
                command_id=command.get("id"),
                device_uid=device_uid,
            )
            continue

        if command.get("command") == "install_rmm_agent":
            # Older tray apps ignore the RMM agent install, so it waits until
            # the device reports a tray version that handles it.
            from app.services import rmm_agent_deploy

            if not await rmm_agent_deploy.ready_for_install(int(device["id"])):
                continue

        if command.get("command") == "health_check":
            # Older tray apps ignore the health check too, so it waits until
            # the device reports a tray version that runs it.
            from app.services import ai_health_check

            if not await ai_health_check.ready_for_device(int(device["id"])):
                continue

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


async def dispatch_device_event(device_id: int, event: str, *, initiated_by_user_id: int | None = None) -> bool:
    """Persist a wakeup before live delivery, including across app workers."""
    if event not in {"defender_commands", "checkin_now"}:
        raise ValueError("Unsupported device event")
    device = await tray_repo.get_device_by_id(device_id)
    if not device or device.get("status") != "active":
        return False
    queued = await tray_repo.get_queued_commands_for_device(device_id)
    if any(command.get("command") == event for command in queued):
        return False
    command_id = await tray_repo.log_command(
        device_id=device_id, command=event, payload_json="{}",
        initiated_by_user_id=initiated_by_user_id, status="queued",
    )
    delivered = await send_to_device(str(device["device_uid"]), {"type": event, "command_id": command_id})
    if delivered:
        await tray_repo.mark_command_delivered(command_id)
    return delivered


async def dispatch_update_command(
    device: dict[str, Any],
    *,
    initiated_by_user_id: int | None,
    extra_payload: dict[str, Any] | None = None,
) -> tuple[int | None, bool]:
    """Ask a tray device to update itself through its built-in updater.

    The tray service handles an ``update`` WebSocket message by checking
    ``/api/tray/version`` immediately (even when automatic updates are off)
    and installing a newer release. The command is logged as ``queued`` so an
    offline device receives it on its next connection through
    :func:`deliver_queued_commands`; a live device gets it straight away.

    A device with an ``update`` command already waiting is not given another,
    so repeated clicks do not stack installs. Returns ``(command_id,
    delivered)``; ``command_id`` is ``None`` when an update was already queued.
    """

    device_id = int(device["id"])
    queued = await tray_repo.get_queued_commands_for_device(device_id)
    if any(command.get("command") == "update" for command in queued):
        return None, False

    payload = dict(extra_payload or {})
    command_id = await tray_repo.log_command(
        device_id=device_id,
        command="update",
        payload_json=json.dumps(payload),
        initiated_by_user_id=initiated_by_user_id,
        status="queued",
    )
    device_uid = str(device.get("device_uid") or "").strip()
    delivered = bool(device_uid) and await send_to_device(
        device_uid, {**payload, "type": "update", "command_id": command_id}
    )
    if delivered:
        await tray_repo.mark_command_delivered(command_id)
    log_info(
        "Dispatched tray update command",
        command_id=command_id,
        device_uid=device_uid,
        delivered=delivered,
    )
    return command_id, delivered


def build_troubleshoot_llm_config(model_override: str | None = None) -> dict[str, str]:
    """Resolve the LLM endpoint / model / key for the AI troubleshooting agent.

    Returns a dict with ``base_url``, ``model`` and ``api_key``. The agent calls
    the OpenAI-compatible ``{base_url}/v1/chat/completions`` endpoint (which
    Ollama also serves), so the same config works for both Ollama and
    OpenAI-compatible providers.

    Dedicated troubleshooter settings (``TROUBLESHOOT_LLM_*``) are preferred so
    the troubleshooter can run against a different model / endpoint / key than
    the Matrix-bot AI waiting assistant. When none of the troubleshooter
    settings are configured, the Matrix-bot AI Ollama settings
    (``MATRIXBOT_AI_OLLAMA_*``) are used as a fallback so a single LLM can serve
    both. A per-run ``model_override`` always wins for the model.
    """

    troubleshoot_base_url = str(_settings.troubleshoot_llm_base_url or "").strip()
    troubleshoot_model = str(_settings.troubleshoot_llm_model or "").strip()
    troubleshoot_api_key = str(_settings.troubleshoot_llm_api_key or "").strip()

    if troubleshoot_base_url or troubleshoot_model or troubleshoot_api_key:
        # A dedicated troubleshooter LLM is configured; use it exclusively so
        # settings from the two assistants are never mixed together.
        base_url, model, api_key = (
            troubleshoot_base_url,
            troubleshoot_model,
            troubleshoot_api_key,
        )
    else:
        # No troubleshooter settings; fall back to the Matrix-bot AI settings.
        base_url = str(_settings.matrixbot_ai_ollama_url or "").strip()
        model = str(_settings.matrixbot_ai_ollama_model or "").strip()
        api_key = str(_settings.matrixbot_ai_ollama_api_key or "").strip()

    base_url = base_url.rstrip("/")
    if not base_url:
        # Match the local default used by the Matrix-bot AI assistant.
        base_url = "http://127.0.0.1:11434"
    model = (model_override or "").strip() or model or "llama3"
    return {"base_url": base_url, "model": model, "api_key": api_key}


async def dispatch_troubleshoot_command(
    *,
    device_id: int,
    device_uid: str,
    payload: dict[str, Any],
    initiated_by_user_id: int | None,
) -> tuple[int, bool]:
    """Log a ``troubleshoot`` tray command and deliver it to the device.

    The command is recorded in the command log (``status="queued"``) so it
    survives the device being offline, then we attempt live delivery over the
    device's WebSocket. When live delivery succeeds the row is marked
    ``delivered``; otherwise it stays ``queued`` and the reconnect drain
    (see :func:`deliver_queued_commands`) delivers it on the device's next
    (re)connection.

    ``payload`` must NOT contain ``type`` or ``command_id`` — both delivery
    paths inject them (the reconnect drain via ``setdefault``; the live path
    below), keeping the two paths byte-for-byte consistent for the device.

    A ``server_context`` entry is stored with the command for the server's own
    later use (the multi-stage troubleshooter's plan) and is never sent to the
    device by either delivery path.

    Returns ``(command_id, delivered)`` where ``delivered`` is True only when
    the message reached the device's local WebSocket right now.
    """

    payload_json = json.dumps(payload)
    command_id = await tray_repo.log_command(
        device_id=device_id,
        command="troubleshoot",
        payload_json=payload_json,
        initiated_by_user_id=initiated_by_user_id,
        status="queued",
    )
    live_payload = {
        "type": "troubleshoot",
        "command_id": command_id,
        **{key: value for key, value in payload.items() if key != SERVER_CONTEXT_KEY},
    }
    delivered = await send_to_device(device_uid, live_payload)
    if delivered:
        await tray_repo.mark_command_delivered(command_id)
    log_info(
        "Dispatched tray troubleshoot command",
        command_id=command_id,
        device_uid=device_uid,
        delivered=delivered,
    )
    return command_id, delivered


def is_troubleshoot_agent_reply(reply: dict[str, Any]) -> bool:
    """True for notes the AI troubleshooter wrote (shown as the "AI" type)."""

    return (
        reply.get("author_id") is None
        and str(reply.get("author_email") or "").strip().lower() == TROUBLESHOOT_AGENT_EMAIL
    )


async def add_troubleshoot_note(ticket_id: int, body_html: str) -> dict[str, Any] | None:
    """Store an internal (staff-only) troubleshooter status note on a ticket.

    Notes are authored by the configured AI support pseudo-user and a ticket
    refresh is broadcast so an open ticket view picks the note up live.
    """

    # Imported lazily: the ticket modules import this service.
    from app.repositories import tickets as tickets_repo
    from app.services import tickets as tickets_service
    from app.services.sanitization import sanitize_rich_text

    reply = await tickets_repo.create_reply(
        ticket_id=ticket_id,
        author_id=None,
        body=sanitize_rich_text(personalise_agent_text(body_html, rich_text=True)).html,
        is_internal=True,
        author_email=TROUBLESHOOT_AGENT_EMAIL,
        author_display_name=get_agent_name(),
    )
    try:
        await tickets_service.broadcast_ticket_event(action="reply", ticket_id=ticket_id)
    except Exception as exc:  # noqa: BLE001 - realtime refresh is best-effort
        log_warning(
            "Failed to broadcast troubleshooter note",
            ticket_id=ticket_id,
            error=str(exc),
        )
    return reply


def troubleshoot_requester_label(requested_by: dict[str, Any] | None) -> str:
    """Name the technician who started a troubleshooter run."""

    requester = requested_by or {}
    requester_label = str(
        requester.get("display_name")
        or " ".join(
            part
            for part in (
                str(requester.get("first_name") or "").strip(),
                str(requester.get("last_name") or "").strip(),
            )
            if part
        )
        or requester.get("email")
        or "a technician"
    ).strip()
    return requester_label


def troubleshoot_delivery_html(*, delivered: bool, device: dict[str, Any]) -> str:
    """Explain on the ticket whether a troubleshoot command reached the device."""

    if delivered:
        delivery_html = (
            "<p>Sent to the device. Progress updates will be posted here as "
            "the agent runs.</p>"
        )
    elif websocket_recently_seen(device):
        delivery_html = (
            "<p>The device is connected to another MyPortal worker process, so "
            "the request is queued and will be picked up within about 30 "
            "seconds. Progress updates will be posted here as the agent "
            "runs.</p>"
        )
    else:
        last_live = _as_utc(device.get("ws_last_seen_utc"))
        last_live_text = (
            f"last live connection {last_live.strftime('%Y-%m-%d %H:%M UTC')}"
            if last_live
            else "no live connection has been recorded"
        )
        delivery_html = (
            "<p>The request is queued, but the device has no live connection to "
            f"MyPortal ({html.escape(last_live_text)}). It will start as soon as "
            "the device's live connection is established.</p>"
            "<p><em>If the device still shows as online, its heartbeat is "
            "reaching the server but its WebSocket is not - check that the "
            "reverse proxy forwards WebSocket upgrades for <code>/ws/</code> "
            "paths.</em></p>"
        )
    return delivery_html


async def add_troubleshoot_requested_note(
    *,
    ticket_id: int,
    command_id: int,
    delivered: bool,
    device: dict[str, Any],
    model: str,
    requested_by: dict[str, Any] | None,
    target_label: str | None = None,
) -> None:
    """Record on the ticket that a technician started the troubleshooter.

    Best effort: the command has already been dispatched, so a failure to
    write the note is logged rather than surfaced to the caller.
    """

    requester_label = troubleshoot_requester_label(requested_by)
    device_label = str(device.get("hostname") or device.get("device_uid") or "device").strip()
    target = (target_label or "").strip()
    target_html = f"{html.escape(target)} ({html.escape(device_label)})" if target else html.escape(device_label)
    delivery_html = troubleshoot_delivery_html(delivered=delivered, device=device)
    body_html = (
        f"<p><strong>{html.escape(get_agent_name())}</strong> - troubleshooting requested by "
        f"{html.escape(requester_label)} for {target_html} "
        f"(command #{int(command_id)}, model {html.escape(model or 'default')}).</p>"
        + delivery_html
    )
    try:
        await add_troubleshoot_note(ticket_id, body_html)
    except Exception as exc:  # noqa: BLE001 - the command is already dispatched
        log_error(
            "Failed to record troubleshooter request note",
            ticket_id=ticket_id,
            command_id=command_id,
            error=str(exc),
        )


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
    if not company or not company.get("tray_notifications_enabled", True):
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
    if not company.get("tray_chat_enabled", True):
        return False
    return bool(user.get("is_helpdesk_technician"))
