"""What MyPortal's own agents say about a device when Tactical RMM is off.

Tactical RMM and Syncro write an asset's ``status``, ``last_sync`` and
``last_user``. Once Tactical RMM is disabled those values stop refreshing (its
agents drift to "overdue"), so anywhere an asset is shown the tray agent's
presence, check-in time and signed-in user take their place.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, MutableMapping
from datetime import datetime, timezone
from typing import Any

from app.core.logging import log_warning
from app.repositories import tray as tray_repo
from app.services.tray_presence import device_online


async def module_enabled(slug: str) -> bool:
    """Is the integration module turned on? Errors count as off."""
    from app.services import modules as modules_service

    try:
        module = await modules_service.get_module(slug, redact=True)
    except Exception:  # pragma: no cover - defensive
        return False
    return bool(module and module.get("enabled"))


def as_utc(value: Any) -> datetime | None:
    """Parse a stored timestamp (naive values are UTC)."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    if isinstance(value, str) and value.strip():
        text = value.strip().replace(" ", "T", 1)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return as_utc(datetime.fromisoformat(text))
        except ValueError:
            return None
    return None


def tray_last_seen(device: Mapping[str, Any]) -> datetime | None:
    """The latest time the tray agent was seen online."""
    seen = [
        parsed
        for parsed in (as_utc(device.get("last_seen_utc")), as_utc(device.get("ws_last_seen_utc")))
        if parsed
    ]
    return max(seen) if seen else None


def tray_console_user(device: Mapping[str, Any]) -> str:
    """The person the tray agent reports as signed in (or last signed in)."""
    console_user = str(device.get("console_user") or "").strip()
    # Older tray services reported their own SYSTEM identity, which is the
    # machine account (COMPUTERNAME$), not a person.
    if console_user.endswith("$") or console_user.upper() in {"SYSTEM", "ROOT"}:
        return ""
    return console_user


def tray_status(device: Mapping[str, Any]) -> str:
    """Online/offline as the tray agent's presence reports it."""
    return "online" if device_online(device) else "offline"


def status_without_tactical(
    stored_status: Any, tray_device: Mapping[str, Any] | None, *, syncro_enabled: bool
) -> str | None:
    """Status to show when Tactical RMM is off.

    Its last synced value (online/offline/overdue) never refreshes, so the tray
    agent decides; Syncro's value stays only while Syncro is still syncing.
    """
    if tray_device:
        return tray_status(tray_device)
    if not syncro_enabled:
        return None
    return str(stored_status or "").strip() or None


_GIB = 1024 ** 3


def rmm_inventory_fields(inventory: Mapping[str, Any]) -> dict[str, Any]:
    """Asset inventory values from a MyPortal RMM agent's hardware snapshot.

    Keys match the ``assets`` columns Tactical RMM used to fill; values the
    agent did not report are left out so stored values stay in place.
    """
    fields: dict[str, Any] = {}
    os_name = str(inventory.get("os_name") or "").strip()
    if os_name:
        fields["os_name"] = os_name
    cpu = str(inventory.get("cpu_model") or "").strip()
    if cpu:
        fields["cpu_name"] = cpu
    try:
        ram_bytes = int(inventory.get("ram_bytes") or 0)
    except (TypeError, ValueError):
        ram_bytes = 0
    if ram_bytes > 0:
        fields["ram_gb"] = round(ram_bytes / _GIB, 1)
    try:
        disk_bytes = int(inventory.get("disk_total_bytes") or 0)
    except (TypeError, ValueError):
        disk_bytes = 0
    if disk_bytes > 0:
        fields["hdd_size"] = f"{round(disk_bytes / _GIB)} GB"
    boot = as_utc(inventory.get("last_boot_at"))
    if boot:
        fields["boot_time"] = boot
    return fields


async def apply_tray_presence(
    assets: Iterable[MutableMapping[str, Any]],
    *,
    id_key: str = "id",
    fields: tuple[str, ...] = ("status", "last_sync", "last_user"),
) -> None:
    """Replace stale Tactical RMM values on ``assets`` in place when it is off.

    ``status`` follows the tray agent (see :func:`status_without_tactical`);
    ``last_sync`` becomes the tray agent's last check-in and ``last_user`` its
    signed-in user, when the device has a tray agent. Assets whose
    ``provenance`` is ``manual`` keep their technician-entered status. Does
    nothing while Tactical RMM is enabled, and leaves ``assets`` untouched if
    the tray agent lookup fails.
    """
    records = [asset for asset in assets if asset.get(id_key)]
    if not records or await module_enabled("tacticalrmm"):
        return
    syncro_enabled = await module_enabled("syncro") if "status" in fields else True
    try:
        devices = await tray_repo.list_active_devices_by_asset_ids(
            [int(a[id_key]) for a in records]
        )
    except Exception as exc:  # noqa: BLE001 - presence is a best-effort overlay
        log_warning("Tray presence lookup failed; keeping stored asset values", error=str(exc))
        return
    for asset in records:
        device = devices.get(int(asset[id_key]))
        if "status" in fields and asset.get("provenance") != "manual":
            asset["status"] = status_without_tactical(
                asset.get("status"), device, syncro_enabled=syncro_enabled
            )
        if not device:
            continue
        if "last_sync" in fields:
            seen = tray_last_seen(device)
            if seen:
                asset["last_sync"] = seen
        if "last_user" in fields:
            user = tray_console_user(device)
            if user:
                asset["last_user"] = user
