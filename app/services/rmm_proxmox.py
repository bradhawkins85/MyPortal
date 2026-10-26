"""Proxmox VE hosts: what the RMM agent reports about them and the
**Proxmox VE** check type.

An RMM agent on a Proxmox VE host (detected by ``/etc/pve`` and ``pvesh``)
adds a ``proxmox`` section to its inventory snapshot (shown on the asset's
Hardware page) and, while the device has a Proxmox VE check, to its check
reports: the pve-manager version, cluster quorum and nodes, every virtual
machine and container in the cluster, this node's storage, ZFS pools, recent
backup jobs and, in inventory only, the subscription and pending updates.

:func:`normalise` cleans a reported section and :func:`evaluate` judges one
check against it. A check watches one :data:`CONDITIONS` entry; its target
names the guest (VMID or name), storage, ZFS pool or node, or ``*``/blank
for every one.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Mapping

CONDITIONS = {
    "guest": "A virtual machine or container is not running",
    "storage": "Storage is filling up",
    "zfs": "A ZFS pool is not healthy",
    "backup": "The latest backup job failed",
    "quorum": "The cluster has lost quorum",
    "node_offline": "A cluster node is offline",
}
# Conditions that name something; the others watch the whole host.
TARGETED = ("guest", "storage", "zfs", "node_offline")
RESULT_EXAMPLES = ("Not running", "Not found", "DEGRADED", "No quorum", "Offline", "Not a Proxmox VE host")
DEFAULT_STORAGE_PERCENT = 85.0

MAX_GUESTS = 1000
MAX_STORAGE = 100
MAX_POOLS = 50
MAX_BACKUPS = 10
MAX_NODES = 64
GB = 1024 ** 3


def _text(value: Any, limit: int = 255) -> str:
    return str(value if value is not None else "").strip()[:limit]


def _int(value: Any) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    return int(number) if math.isfinite(number) and number > 0 else 0


def _number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number > 0 else 0.0


def _items(value: Any, limit: int) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)][:limit]


def normalise(payload: Any) -> dict[str, Any] | None:
    """A reported ``proxmox`` section, cleaned and capped, or None."""

    if not isinstance(payload, Mapping):
        return None
    cluster = payload.get("cluster")
    pending = payload.get("pending_updates")
    return {
        "version": _text(payload.get("version"), 64),
        "node": _text(payload.get("node"), 128),
        "cluster": {
            "name": _text(cluster.get("name"), 128),
            "quorate": bool(cluster.get("quorate")),
            "nodes": _int(cluster.get("nodes")),
        } if isinstance(cluster, Mapping) else None,
        "nodes": [
            {"name": _text(item.get("name"), 128), "online": bool(item.get("online")), "local": bool(item.get("local"))}
            for item in _items(payload.get("nodes"), MAX_NODES)
        ],
        "guests": [
            {
                "vmid": _int(item.get("vmid")),
                "name": _text(item.get("name")),
                "type": "lxc" if item.get("type") == "lxc" else "qemu",
                "node": _text(item.get("node"), 128),
                "status": _text(item.get("status"), 32).lower(),
                "template": bool(item.get("template")),
                "cpus": _number(item.get("cpus")),
                "mem_bytes": _int(item.get("mem_bytes")),
                "max_mem_bytes": _int(item.get("max_mem_bytes")),
                "max_disk_bytes": _int(item.get("max_disk_bytes")),
                "uptime_seconds": _int(item.get("uptime_seconds")),
                "ha_state": _text(item.get("ha_state"), 32),
            }
            for item in _items(payload.get("guests"), MAX_GUESTS)
        ],
        "storage": [
            {
                "name": _text(item.get("name"), 128),
                "type": _text(item.get("type"), 32),
                "status": _text(item.get("status"), 32),
                "shared": bool(item.get("shared")),
                "content": _text(item.get("content")),
                "total_bytes": _int(item.get("total_bytes")),
                "used_bytes": _int(item.get("used_bytes")),
            }
            for item in _items(payload.get("storage"), MAX_STORAGE)
        ],
        "zfs_pools": [
            {
                "name": _text(item.get("name"), 128),
                "health": _text(item.get("health"), 32).upper(),
                "size_bytes": _int(item.get("size_bytes")),
                "alloc_bytes": _int(item.get("alloc_bytes")),
                "free_bytes": _int(item.get("free_bytes")),
            }
            for item in _items(payload.get("zfs_pools"), MAX_POOLS)
        ],
        "backups": [
            {
                "started_at": _text(item.get("started_at"), 40),
                "ended_at": _text(item.get("ended_at"), 40) or None,
                "guest": _text(item.get("guest"), 64),
                "status": _text(item.get("status"), 500),
                "ok": bool(item.get("ok")),
                "running": bool(item.get("running")),
            }
            for item in _items(payload.get("backups"), MAX_BACKUPS)
        ],
        "subscription": _text(payload.get("subscription"), 32),
        "pending_updates": _int(pending) if pending is not None else None,
        "errors": [_text(error, 500) for error in (payload.get("errors") or []) if _text(error)][:20],
    }


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #


def describe(condition: str, target: str, settings: Mapping[str, Any]) -> str:
    """When a Proxmox VE check fails, in words (without its duration)."""

    every = target in ("", "*")
    if condition == "guest":
        return f"Guest {target or '?'} is not running"
    if condition == "storage":
        limit = f"{float(settings.get('threshold') or DEFAULT_STORAGE_PERCENT):g}%"
        return f"{'Any storage' if every else 'Storage ' + target} is over {limit} full"
    if condition == "zfs":
        return f"{'A ZFS pool' if every else 'ZFS pool ' + target} is not ONLINE"
    if condition == "backup":
        return "The latest backup job on the node failed"
    if condition == "quorum":
        return "The Proxmox VE cluster has lost quorum"
    if condition == "node_offline":
        return f"{'A cluster node' if every else 'Node ' + target} is offline"
    return "Proxmox VE"


def _named(items: list[Mapping[str, Any]], target: str, key: str = "name") -> list[Mapping[str, Any]]:
    if target in ("", "*"):
        return items
    wanted = target.casefold()
    return [item for item in items if str(item.get(key) or "").casefold() == wanted]


def _guest_label(guest: Mapping[str, Any]) -> str:
    name = guest.get("name") or ""
    return f"{guest.get('vmid')}{' (' + name + ')' if name else ''}"


def _percent(used: float, total: float) -> float:
    return 100 * used / total if total else 0.0


def evaluate(condition: str, target: str, settings: Mapping[str, Any], report: Any) -> tuple[bool, str] | None:
    """Whether the report shows a problem, and the result in words; None when
    the agent sent no Proxmox VE section (an older agent)."""

    status = normalise(report)
    if status is None:
        return None
    if not status["version"] and not status["nodes"] and not status["guests"]:
        reason = status["errors"][0] if status["errors"] else "no Proxmox VE information"
        if "not a Proxmox VE host" in reason:
            return True, "Not a Proxmox VE host"
        return True, f"Unknown: {reason}"

    if condition == "guest":
        wanted = target.strip()
        guests = [guest for guest in status["guests"] if not guest["template"]]
        found = [guest for guest in guests
                 if str(guest["vmid"]) == wanted or guest["name"].casefold() == wanted.casefold()]
        if not found:
            return True, f"Not found: no virtual machine or container {wanted}"
        guest = found[0]
        where = f" on {guest['node']}" if guest["node"] else ""
        if guest["status"] != "running":
            return True, f"Not running: {_guest_label(guest)} is {guest['status'] or 'unknown'}{where}"
        return False, f"Running: {_guest_label(guest)}{where}"

    if condition == "storage":
        limit = float(settings.get("threshold") or DEFAULT_STORAGE_PERCENT)
        storage = [item for item in _named(status["storage"], target) if item["total_bytes"]]
        if not storage:
            return (True, f"Not found: storage {target}") if target not in ("", "*") else None
        rows = sorted(((_percent(item["used_bytes"], item["total_bytes"]), item) for item in storage),
                      key=lambda row: -row[0])

        def text(row: tuple[float, Mapping[str, Any]]) -> str:
            percent, item = row
            return f"{item['name']} {percent:.0f}% full ({item['used_bytes'] / GB:.0f} of {item['total_bytes'] / GB:.0f} GB)"

        bad = [row for row in rows if row[0] > limit]
        if bad:
            return True, "; ".join(text(row) for row in bad)
        return False, text(rows[0]) if len(rows) == 1 else f"Fullest: {text(rows[0])}"

    if condition == "zfs":
        pools = _named(status["zfs_pools"], target)
        if not pools:
            return (True, f"Not found: ZFS pool {target}") if target not in ("", "*") else (False, "No ZFS pools")
        bad = [pool for pool in pools if pool["health"] != "ONLINE"]
        if bad:
            return True, "; ".join(f"{pool['name']} {pool['health'] or 'UNKNOWN'}" for pool in bad)
        return False, ", ".join(f"{pool['name']} ONLINE" for pool in pools)

    if condition == "backup":
        finished = [backup for backup in status["backups"] if not backup["running"]]
        if not finished:
            return False, "No finished backup jobs on this node yet"
        latest = finished[0]
        when = latest["started_at"][:16].replace("T", " ")
        if latest["ok"]:
            return False, f"Last backup {when} UTC: {latest['status'] or 'OK'}"
        return True, f"Backup failed {when} UTC: {latest['status']}"

    if condition == "quorum":
        cluster = status["cluster"]
        if cluster is None:
            return False, "Standalone host (no cluster)"
        if not cluster["quorate"]:
            return True, f"No quorum in cluster {cluster['name']}"
        return False, f"Cluster {cluster['name']} is quorate ({cluster['nodes']} nodes)"

    if condition == "node_offline":
        nodes = _named(status["nodes"], target)
        if not nodes:
            return (True, f"Not found: node {target}") if target not in ("", "*") else None
        offline = [node["name"] for node in nodes if not node["online"]]
        if offline:
            return True, f"Offline: {', '.join(offline)}"
        return False, f"{len(nodes)} node{'s' if len(nodes) != 1 else ''} online"
    return None


def inventory_summary(status: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Counts for the asset's Hardware page."""

    if not status:
        return None
    guests = [guest for guest in status.get("guests") or [] if not guest.get("template")]
    local = [guest for guest in guests if not status.get("node") or guest.get("node") == status.get("node")]
    return {
        "guests": len(guests),
        "local_guests": len(local),
        "running": sum(1 for guest in local if guest.get("status") == "running"),
        "failed_backups": sum(1 for backup in status.get("backups") or [] if not backup.get("ok")),
        "unhealthy_pools": sum(1 for pool in status.get("zfs_pools") or [] if pool.get("health") != "ONLINE"),
        "storage_percent": {
            item.get("name"): round(_percent(item.get("used_bytes") or 0, item.get("total_bytes") or 0))
            for item in status.get("storage") or []
        },
    }


def format_uptime(seconds: Any) -> str:
    total = _int(seconds)
    if not total:
        return ""
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    return f"{days}d {hours}h" if days else f"{hours}h {rest // 60}m"


def format_time(value: Any) -> str:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return str(value or "")
