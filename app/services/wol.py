"""Wake-on-LAN (WoL) orchestration.

When a technician clicks "Wake" on an offline device/asset, this module:

* resolves the target MAC address(es) and the subnet the target lives on,
* finds the *other* on-network tray agents that are online,
* broadcasts a WoL magic packet by dispatching a ``wol`` command to each of
  them, and
* records a ``wol_attempts`` row so the outcome (awake / timeout / failed) can
  be tracked and per-asset success statistics can be shown.

The actual UDP broadcast is performed by the Go tray agent that receives the
``wol`` command; the server only orchestrates and tracks.
"""

from __future__ import annotations

import ipaddress
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.logging import log_info, log_warning
from app.repositories import tray as tray_repo
from app.repositories import wol as wol_repo
from app.repositories import assets as assets_repo
from app.services import tray as tray_service

# How long to wait for the target to come back online after a WoL attempt.
WOL_WAKE_WINDOW_SECONDS = 120
# A device whose HTTP heartbeat is within this window is treated as live.
HEARTBEAT_PRESENCE_WINDOW_SECONDS = 180


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _parse_mac_list(raw: str | None) -> list[str]:
    """Extract every valid MAC address from a free-text field.

    ``assets.mac_address`` is free text that may hold several addresses
    (comma/space separated). Returns de-duplicated canonical ``AA:BB:...``
    values, preserving order.
    """

    if not raw:
        return []
    candidates = re.findall(
        r"([0-9A-Fa-f]{2})[:-]?([0-9A-Fa-f]{2})[:-]?"
        r"([0-9A-Fa-f]{2})[:-]?([0-9A-Fa-f]{2})[:-]?"
        r"([0-9A-Fa-f]{2})[:-]?([0-9A-Fa-f]{2})",
        raw.upper(),
    )
    seen: set[str] = set()
    result: list[str] = []
    for parts in candidates:
        mac = ":".join(parts)
        if mac not in seen:
            seen.add(mac)
            result.append(mac)
    return result


def _ip_in_subnet(ip_text: str | None, cidr: str | None) -> bool:
    if not ip_text or not cidr:
        return False
    try:
        ip = ipaddress.ip_address(ip_text.strip())
        network = ipaddress.ip_network(cidr.strip(), strict=False)
        return ip in network
    except (ValueError, TypeError):
        return False


def _device_online(device: dict[str, Any], *, now: datetime | None = None) -> bool:
    """True when the tray device is live on some worker / by heartbeat."""

    current = now or datetime.now(timezone.utc)
    ws_seen = tray_service._as_utc(device.get("ws_last_seen_utc"))
    if (
        ws_seen is not None
        and (current - ws_seen).total_seconds()
        <= tray_service.WEBSOCKET_PRESENCE_WINDOW_SECONDS
    ):
        return True
    last_seen = tray_service._as_utc(device.get("last_seen_utc"))
    if (
        last_seen is not None
        and (current - last_seen).total_seconds()
        <= HEARTBEAT_PRESENCE_WINDOW_SECONDS
    ):
        return True
    return False


def _cidr_from_ip(ip_text: str | None) -> str | None:
    """A /24 fallback subnet for a bare IP address."""

    if not ip_text:
        return None
    try:
        ip = ipaddress.ip_address(ip_text.strip())
        network = ipaddress.ip_network(f"{ip}/24", strict=False)
        return str(network)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# candidate selection
# ---------------------------------------------------------------------------


async def _find_candidate_devices(
    *,
    company_id: int,
    exclude_device_id: int | None,
    subnet: str | None,
) -> list[dict[str, Any]]:
    """On-network, online tray agents (excluding the target) that can broadcast."""

    now = datetime.now(timezone.utc)
    seen_ids: set[int] = set()
    candidates: list[dict[str, Any]] = []

    # 1. Active devices whose last IP falls in the target subnet (if known).
    for device in await wol_repo.list_active_devices(company_id, exclude_device_id):
        if subnet and not _ip_in_subnet(device.get("last_ip"), subnet):
            continue
        if device["id"] in seen_ids:
            continue
        if not _device_online(device, now=now):
            continue
        seen_ids.add(device["id"])
        candidates.append(device)

    # 2. Scanner agents registered for the target subnet (they sit on it).
    if subnet:
        for device in await wol_repo.list_subnet_scanner_devices(company_id, subnet):
            if device["id"] in seen_ids:
                continue
            if exclude_device_id and device["id"] == exclude_device_id:
                continue
            if not _device_online(device, now=now):
                continue
            seen_ids.add(device["id"])
            candidates.append(device)

    return candidates


async def _resolve_target(
    *,
    asset_id: int | None = None,
    device_id: int | None = None,
) -> dict[str, Any]:
    """Resolve company, target device, MAC(s) and subnet for a wake action."""

    target_device: dict[str, Any] | None = None
    if asset_id is not None:
        asset = await assets_repo.get_asset_by_id(int(asset_id))
        if not asset:
            raise ValueError("Asset not found")
        company_id = int(asset.get("company_id"))
        by_asset = await tray_repo.list_active_devices_by_asset_ids([int(asset_id)])
        target_device = by_asset.get(int(asset_id))
    elif device_id is not None:
        target_device = await tray_repo.get_device_by_id(int(device_id))
        if not target_device:
            raise ValueError("Device not found")
        company_id = int(target_device.get("company_id"))
        asset_id = target_device.get("asset_id")
    else:
        raise ValueError("Either asset_id or device_id is required")

    macs: list[str] = []
    if asset_id is not None:
        asset = await assets_repo.get_asset_by_id(int(asset_id))
        macs = _parse_mac_list(asset.get("mac_address") if asset else None)
    if not macs:
        matched = await wol_repo.get_matched_device_mac(int(asset_id or 0))
        macs = _parse_mac_list(matched)

    subnet = None
    if asset_id is not None:
        subnet = await wol_repo.get_target_subnet_cidr(int(asset_id))
    if not subnet and target_device and target_device.get("last_ip"):
        subnet = _cidr_from_ip(target_device.get("last_ip"))

    return {
        "company_id": company_id,
        "asset_id": asset_id,
        "target_device": target_device,
        "macs": macs,
        "subnet": subnet,
    }


# ---------------------------------------------------------------------------
# public entry points
# ---------------------------------------------------------------------------


async def _wake(
    *,
    asset_id: int | None = None,
    device_id: int | None = None,
    initiated_by_user_id: int | None = None,
) -> dict[str, Any]:
    target = await _resolve_target(asset_id=asset_id, device_id=device_id)
    company_id = target["company_id"]
    target_device = target.get("target_device")
    exclude_id = int(target_device["id"]) if target_device else None

    if not target["macs"]:
        return {
            "status": "no_mac",
            "error": "No MAC address is recorded for this asset",
            "attempt_id": None,
        }
    target_mac = target["macs"][0]  # one broadcast per agent is enough

    candidates = await _find_candidate_devices(
        company_id=company_id, exclude_device_id=exclude_id, subnet=target["subnet"]
    )
    now = datetime.now(timezone.utc)
    check_deadline = now.replace(tzinfo=None) + timedelta(seconds=WOL_WAKE_WINDOW_SECONDS)

    # Create the attempt row first so agents can report back against it.
    attempt_id = await wol_repo.create_attempt(
        company_id=company_id,
        asset_id=target.get("asset_id"),
        target_device_id=exclude_id,
        target_mac=target_mac,
        subnet=target["subnet"],
        trigger_type="manual",
        initiated_by_user_id=initiated_by_user_id,
        status="pending",
        check_deadline=check_deadline,
    )

    if not candidates:
        await wol_repo.mark_attempt_finished(
            attempt_id,
            status_value="no_candidates",
            error="No online agents were found on the target's network",
        )
        return {
            "status": "no_candidates",
            "error": "No online agents were found on the target's network",
            "attempt_id": attempt_id,
        }

    delivered_device_ids: list[int] = []
    errors: list[str] = []
    for candidate in candidates:
        try:
            command_id = await tray_repo.log_command(
                device_id=int(candidate["id"]),
                command="wol",
                payload_json=json.dumps(
                    {"attempt_id": attempt_id, "target_mac": target_mac,
                     "target_device_id": exclude_id}
                ),
                initiated_by_user_id=initiated_by_user_id,
                status="queued",
            )
            delivered = await tray_service.send_to_device(
                str(candidate["device_uid"]),
                {
                    "type": "wol",
                    "command_id": command_id,
                    "attempt_id": attempt_id,
                    "target_mac": target_mac,
                    "target_device_id": exclude_id,
                },
            )
            if delivered:
                delivered_device_ids.append(int(candidate["id"]))
                await tray_repo.mark_command_delivered(command_id)
        except Exception as exc:  # noqa: BLE001 - best-effort per agent
            log_warning("WoL dispatch failed", device_id=candidate.get("id"), error=str(exc))
            errors.append(f"device {candidate.get('id')}: {exc}")

    for device_id in delivered_device_ids:
        await wol_repo.mark_attempt_sent(attempt_id, device_id)

    log_info(
        "WoL attempt created",
        attempt_id=attempt_id,
        company_id=company_id,
        asset_id=target.get("asset_id"),
        candidates=len(candidates),
        delivered=len(delivered_device_ids),
    )
    return {
        "status": "dispatched",
        "attempt_id": attempt_id,
        "target_mac": target_mac,
        "subnet": target["subnet"],
        "candidate_count": len(candidates),
        "delivered_count": len(delivered_device_ids),
        "delivered_device_ids": delivered_device_ids,
        "error": "; ".join(errors) if errors else None,
    }


async def wake_asset(
    asset_id: int, initiated_by_user_id: int | None = None
) -> dict[str, Any]:
    return await _wake(asset_id=asset_id, initiated_by_user_id=initiated_by_user_id)


async def wake_device(
    device_id: int, initiated_by_user_id: int | None = None
) -> dict[str, Any]:
    return await _wake(device_id=device_id, initiated_by_user_id=initiated_by_user_id)


async def record_agent_sent(
    attempt_id: int, device_id: int, error: str | None = None
) -> None:
    """Record that a candidate agent broadcast the WoL packet (device callback)."""

    await wol_repo.mark_attempt_sent(attempt_id, device_id)
    if error:
        await wol_repo.record_command_error(attempt_id, error)


# ---------------------------------------------------------------------------
# outcome tracking
# ---------------------------------------------------------------------------


async def _target_is_online(target: dict[str, Any]) -> tuple[bool, str]:
    """Has the target asset/device come back online?"""

    target_device = target.get("target_device")
    if target_device and _device_online(target_device):
        return True, "agent_online"

    asset_id = target.get("asset_id")
    if asset_id:
        by_asset = await tray_repo.list_active_devices_by_asset_ids([int(asset_id)])
        for device in by_asset.values():
            if _device_online(device):
                return True, "agent_online"

        asset = await assets_repo.get_asset_by_id(int(asset_id))
        if asset:
            created = tray_service._as_utc(target.get("created_at"))
            for value in (
                tray_service._as_utc(asset.get("boot_time")),
                tray_service._as_utc(asset.get("last_sync")),
            ):
                if value is not None and created is not None and value >= created:
                    return True, "asset_sync"
    return False, ""


async def check_pending_attempts() -> int:
    """Resolve any pending/awaiting WoL attempts whose window has elapsed.

    Called periodically by the scheduler. For each attempt past its deadline we
    re-check whether the target is online (-> awake) or mark it timed out.
    Returns the number of attempts resolved.
    """

    resolved = 0
    for attempt in await wol_repo.list_pending_attempts():
        if attempt.get("status") not in {"pending", "awaiting"}:
            continue
        deadline = tray_service._as_utc(attempt.get("check_deadline"))
        if deadline is None:
            created = tray_service._as_utc(attempt.get("created_at"))
            if created is None:
                continue
            deadline = created + timedelta(seconds=WOL_WAKE_WINDOW_SECONDS)
        if datetime.now(timezone.utc) < deadline:
            continue

        target = {
            "asset_id": attempt.get("asset_id"),
            "created_at": attempt.get("created_at"),
            "target_device": None,
        }
        if attempt.get("target_device_id"):
            target["target_device"] = await tray_repo.get_device_by_id(
                int(attempt["target_device_id"])
            )
        online, via = await _target_is_online(target)
        if online:
            await wol_repo.mark_attempt_awake(attempt["id"], via or "unknown")
        else:
            await wol_repo.mark_attempt_finished(
                attempt["id"],
                status_value="timeout",
                error=f"Device did not come back online within {WOL_WAKE_WINDOW_SECONDS}s",
            )
        resolved += 1
        log_info("WoL attempt resolved", attempt_id=attempt["id"], online=online, via=via)
    return resolved



