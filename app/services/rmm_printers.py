"""Installed printers on each RMM device.

A built-in PowerShell scan (``rmm_builtin/printers.ps1``) runs through the
device's RMM script runner as a ``printer_scan`` run, so no new RMM agent
version is needed: daily on connected Windows devices (:func:`tick`) and when a
technician presses **Scan now** on the asset page. It lists the device's
printers with ``Get-Printer`` (PrinterStatus, JobCount, and the port address
from ``Get-PrinterPort``), adds the
printer connections and default printer of every signed-in user, and prints
one ``##myportal-printers=`` line of gzip-compressed, base64-encoded JSON.
:func:`capture` takes that line out of the stored run output and :func:`ingest`
saves it. Only MyPortal's own scan runs can report printers.

Each printer keeps the date it was first and last seen. A printer a scan no
longer reports stays listed as no longer seen until it has been missing for
:data:`FORGET_AFTER`, then it is removed; a printer still installed is always
reported, however long ago it first appeared.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from app.core.logging import log_info, log_warning
from app.repositories import rmm as rmm_repo
from app.repositories import rmm_printers as repo
from app.security.encryption import encrypt_secret
from app.services import rmm_scripts
from app.services.rmm_scripts import RunRequestError
from app.services.rmm_windows_updates import decode as _decode

RUN_SOURCE = "printer_scan"
MARKER = "##myportal-printers="
SCAN_INTERVAL = timedelta(hours=24)
RETRY_INTERVAL = timedelta(hours=6)
SCANS_PER_TICK = 25
SCAN_TIMEOUT_SECONDS = 300
AUTO_SCAN_EXPIRY = timedelta(minutes=30)
MANUAL_SCAN_EXPIRY = timedelta(hours=24)
FORGET_AFTER = timedelta(days=365)
MAX_PRINTERS = 500
MAX_ERRORS = 20

ACTION_RUN_SOURCE = "printer_action"
ACTION_MARKER = "##myportal-printer-action="
ACTION_TIMEOUT_SECONDS = 180
ACTION_EXPIRY = timedelta(hours=24)
ACTIONS = ("rename", "remove")
MAX_NAME_LENGTH = 220
_ACTIVE_RUN_STATUSES = {"queued", "dispatched", "running"}

_BUILTIN = Path(__file__).parent / "rmm_builtin"

# Win32_Printer's numeric PrinterStatus (used where Get-Printer is missing).
_WMI_STATUS = {"1": "Other", "2": "Unknown", "3": "Idle", "4": "Printing", "5": "Warming up",
               "6": "Stopped printing", "7": "Offline"}
# Get-Printer's PrinterStatus names, in plain words.
_STATUS_LABELS = {
    "normal": "Ready", "paused": "Paused", "error": "Error", "pendingdeletion": "Pending deletion",
    "paperjam": "Paper jam", "paperout": "Out of paper", "manualfeed": "Manual feed", "paperproblem": "Paper problem",
    "offline": "Offline", "ioactive": "Busy", "busy": "Busy", "printing": "Printing", "outputbinfull": "Output bin full",
    "notavailable": "Not available", "waiting": "Waiting", "processing": "Processing", "initializing": "Initialising",
    "warmingup": "Warming up", "tonerlow": "Toner low", "notoner": "No toner", "pagepunt": "Page punt",
    "userintervention": "Needs attention", "outofmemory": "Out of memory", "dooropen": "Door open",
    "serverunknown": "Server unknown", "powersave": "Power save",
}
# Status pill variant for each state; anything else is neutral.
STATUS_VARIANTS = {
    "Ready": "success", "Idle": "success", "Printing": "info", "Processing": "info", "Busy": "info",
    "Warming up": "info", "Initialising": "info", "Power save": "neutral", "Waiting": "neutral",
    "Toner low": "warning", "Paused": "warning", "Manual feed": "warning", "Output bin full": "warning",
    "Pending deletion": "warning", "Offline": "danger", "Error": "danger", "Paper jam": "danger",
    "Out of paper": "danger", "Paper problem": "danger", "No toner": "danger", "Needs attention": "danger",
    "Door open": "danger", "Out of memory": "danger", "Not available": "danger", "Stopped printing": "danger",
}
_TYPE_LABELS = {"local": "Local", "connection": "Network connection"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def is_windows(agent: Mapping[str, Any] | None) -> bool:
    return bool(agent) and str(agent.get("os") or "").lower() == "windows"


def scan_script_content() -> str:
    return (_BUILTIN / "printers.ps1").read_text(encoding="utf-8")


def _builtin_script(content: str) -> dict[str, Any]:
    return {"id": None, "name": "Printer scan", "path": "builtin/printers.ps1", "language": "powershell",
            "content_sha256": hashlib.sha256(content.encode()).hexdigest()}


# --------------------------------------------------------------------------- #
# Reading a scan
# --------------------------------------------------------------------------- #


def take_marker(stdout: str, marker: str = MARKER) -> tuple[str, str | None]:
    """Remove the printer line from ``stdout``; return the rest and the encoded scan."""

    if marker not in stdout:
        return stdout, None
    encoded = None
    kept = []
    for line in stdout.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith(marker):
            encoded = stripped[len(marker):]
            continue
        kept.append(line)
    return "".join(kept), encoded


def decode(encoded: str) -> Any:
    return _decode(encoded)


def _text(value: Any, limit: int = 255) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    text = str(value).replace("\x00", "").strip()
    return text[:limit] or None


def _bool(value: Any) -> int:
    if isinstance(value, str):
        return 1 if value.strip().lower() in {"true", "1", "yes"} else 0
    return 1 if value is True or value == 1 else 0


def _names(value: Any) -> str | None:
    items = value if isinstance(value, list) else [value] if value else []
    names = list(dict.fromkeys(name for name in (_text(item, 128) for item in items) if name))
    return ", ".join(names)[:512] or None


def _count(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if 0 <= number <= 1_000_000 else None


def status_label(value: Any) -> str | None:
    text = _text(value, 64)
    if not text:
        return None
    if text in _WMI_STATUS:
        return _WMI_STATUS[text]
    return _STATUS_LABELS.get(text.replace(" ", "").replace(",", "").lower(), text)


def _time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00")[:32])
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed


def normalise(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("the scan is not a JSON object")
    raw = payload.get("printers")
    if isinstance(raw, Mapping):  # PowerShell 5 can collapse a one-item array
        raw = [raw]
    printers: list[dict[str, Any]] | None = None
    if isinstance(raw, list):
        printers = []
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            name = _text(item.get("name"))
            if not name or name.casefold() in seen:
                continue
            seen.add(name.casefold())
            printers.append({
                "name": name,
                "driver_name": _text(item.get("driver")),
                "port_name": _text(item.get("port")),
                "port_address": _text(item.get("port_address")),
                "printer_type": _text(item.get("type"), 32),
                "device_type": _text(item.get("device_type"), 32),
                "status": status_label(item.get("status")),
                "printer_status": _text(item.get("status"), 64),
                "job_count": _count(item.get("job_count")),
                "is_shared": _bool(item.get("shared")),
                "share_name": _text(item.get("share_name")),
                "is_published": _bool(item.get("published")),
                "location": _text(item.get("location")),
                "comment": _text(item.get("comment")),
                "computer_name": _text(item.get("computer_name")),
                "connected_users": _names(item.get("users")),
                "default_for": _names(item.get("default_for")),
            })
            if len(printers) >= MAX_PRINTERS:
                break
        printers.sort(key=lambda row: row["name"].casefold())
    errors = payload.get("errors")
    errors = errors if isinstance(errors, list) else [errors] if errors else []
    errors = [text for text in (_text(error, 500) for error in errors) if text][:MAX_ERRORS]
    # Nothing listed and the printer list itself failed: keep what is stored.
    if printers == [] and any(error.startswith("printers:") for error in errors):
        printers = None
    return {"scanned_at": _time(payload.get("scanned_at")), "printers": printers, "errors": errors}


async def ingest(agent: Mapping[str, Any], run: Mapping[str, Any], payload: Any) -> dict[str, Any]:
    state = normalise(payload)
    now = utcnow()
    # A device clock in the future must not make the scan look fresh forever.
    scanned_at = min(state["scanned_at"] or now, now)
    await repo.save_scan(dict(agent), run_id=run.get("id"), scanned_at=scanned_at, printers=state["printers"],
                         errors=state["errors"], forget_before=now - FORGET_AFTER)
    count = None if state["printers"] is None else len(state["printers"])
    log_info("RMM printers received", agent_id=agent.get("id"), run_id=run.get("id"), printers=count)
    return {"printers": count, "errors": len(state["errors"])}


async def capture(agent: Mapping[str, Any], run: Mapping[str, Any], stdout: str) -> str:
    """Save the printers a built-in scan reported, replacing its line in the
    output with a short note. Other runs' output is unchanged."""

    if run.get("run_source") != RUN_SOURCE:
        return stdout
    stdout, encoded = take_marker(stdout)
    if encoded is None:
        return stdout
    try:
        summary = await ingest(agent, run, decode(encoded))
        count = summary["printers"]
        note = ("MyPortal could not read the printer list." if count is None
                else f"MyPortal saved {count} printer{'s' if count != 1 else ''}.")
    except ValueError as exc:
        log_warning("RMM printer scan rejected", agent_id=agent.get("id"), run_id=run.get("id"), error=str(exc))
        note = f"MyPortal could not read the printer scan: {exc}."
    separator = "" if not stdout or stdout.endswith("\n") else "\n"
    return stdout + separator + note + "\n"


async def strip_live_output(agent: Mapping[str, Any], run_id: int, stdout: str) -> str:
    """Live output never shows the printer line of a built-in scan."""

    if MARKER not in stdout:
        return stdout
    run = await rmm_repo.get_agent_run(run_id, int(agent["id"]))
    if run and run.get("run_source") == RUN_SOURCE:
        return take_marker(stdout)[0]
    return stdout


# --------------------------------------------------------------------------- #
# Queueing scans
# --------------------------------------------------------------------------- #


async def queue_scan(agent: Mapping[str, Any] | None, *, user_id: int | None = None,
                     expires_in: timedelta = MANUAL_SCAN_EXPIRY) -> int | None:
    """Queue a printer scan, or return ``None`` when one is already waiting or running."""

    if not is_windows(agent) or agent.get("status") != "active":
        raise RunRequestError({"device": "Printer scans need an active RMM agent on Windows."})
    agent_id = int(agent["id"])
    if await repo.has_active_scan(agent_id, RUN_SOURCE):
        return None
    content = scan_script_content()
    script = _builtin_script(content)
    problem = rmm_scripts.device_problem(script, agent)
    if problem:
        raise RunRequestError({"device": problem})
    run_id = await rmm_repo.create_run(
        script=script, content=content, agent_id=agent_id, company_id=int(agent["company_id"]),
        asset_id=agent.get("asset_id"), requested_by_user_id=user_id, inputs=[],
        payload_encrypted=encrypt_secret(json.dumps({"env": {}})), timeout_seconds=SCAN_TIMEOUT_SECONDS,
        expires_at=utcnow() + expires_in, run_source=RUN_SOURCE,
    )
    await repo.mark_scan_requested(dict(agent), user_id)
    rmm_scripts.notify_agent(agent_id)
    return run_id


async def tick() -> None:
    """Scan connected Windows devices whose printer list is a day old."""

    now = utcnow()
    agents = await repo.list_agents_due(
        run_source=RUN_SOURCE, received_before=now - SCAN_INTERVAL, requested_before=now - RETRY_INTERVAL,
        seen_after=now - rmm_scripts.AGENT_CONNECTED_WINDOW, limit=SCANS_PER_TICK,
    )
    for agent in agents:
        try:
            await queue_scan(agent, expires_in=AUTO_SCAN_EXPIRY)
        except Exception as exc:  # noqa: BLE001 - one device must not stop the others
            log_warning("Could not queue a printer scan", agent_id=agent.get("id"), error=str(exc))


# --------------------------------------------------------------------------- #
# Display
# --------------------------------------------------------------------------- #


def _iso(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value.isoformat() if isinstance(value, datetime) else value for key, value in row.items()}


def _display(row: Mapping[str, Any]) -> dict[str, Any]:
    item = _iso(row)
    item["is_shared"] = bool(row.get("is_shared"))
    item["is_published"] = bool(row.get("is_published"))
    printer_type = str(row.get("printer_type") or "")
    if row.get("connected_users") and not printer_type:
        printer_type = "connection"
    item["type_label"] = _TYPE_LABELS.get(printer_type.lower(), printer_type or "Unknown")
    item["status_label"] = row.get("status") or "Unknown"
    item["status_variant"] = STATUS_VARIANTS.get(item["status_label"], "neutral")
    item["job_count"] = row.get("job_count")
    item["scope_label"] = "User connection" if row.get("connected_users") else "Device"
    item["is_installed"] = bool(row.get("is_installed", 1))
    item["presence_label"] = "Installed" if item["is_installed"] else "No longer seen"
    pending = row.get("pending_action") if row.get("pending_run_status") in _ACTIVE_RUN_STATUSES else None
    item["pending_action"] = pending
    item["pending_name"] = row.get("pending_name") if pending == "rename" else None
    item["pending_label"] = ({"rename": "Rename pending", "remove": "Removal pending"}.get(pending) if pending else None)
    item["is_connection"] = is_connection(row)
    item["can_rename"] = item["is_installed"] and not item["is_connection"] and not pending
    item["can_remove"] = not pending
    return item


async def asset_context(agent: Mapping[str, Any] | None, *, can_run: bool) -> dict[str, Any] | None:
    """The asset page's Printers section, or ``None`` for devices without an
    RMM agent on Windows."""

    if not is_windows(agent):
        return None
    agent_id = int(agent["id"])
    scan = await repo.get_scan(agent_id)
    received = scan.get("received_at") if scan else None
    rows = [_display(row) for row in await repo.list_printers(agent_id)] if received else []
    printers = [item for item in rows if item["is_installed"]]
    removed = [item for item in rows if not item["is_installed"]]
    return {
        "scan": _iso(scan) if received else None,
        "requested_at": _iso(scan).get("scan_requested_at") if scan else None,
        "printers": rows,
        "counts": {
            "total": len(printers),
            "removed": len(removed),
            "shared": sum(1 for item in printers if item["is_shared"]),
            "attention": sum(1 for item in printers if item["status_variant"] in ("danger", "warning")),
            "jobs": sum(item["job_count"] or 0 for item in printers),
        },
        "type_options": sorted({item["type_label"] for item in rows}),
        "status_options": sorted({item["status_label"] for item in rows}),
        "scan_pending": await repo.has_active_scan(agent_id, RUN_SOURCE),
        "can_run": bool(can_run) and agent.get("status") == "active",
    }


# --------------------------------------------------------------------------- #
# Renaming and removing printers
# --------------------------------------------------------------------------- #


def is_connection(row: Mapping[str, Any]) -> bool:
    """A print server connection (\\\\server\\printer), which Windows cannot rename."""

    return (str(row.get("name") or "").startswith("\\\\")
            or str(row.get("printer_type") or "").lower() == "connection")


def action_script_content() -> str:
    return (_BUILTIN / "printer_action.ps1").read_text(encoding="utf-8")


def clean_new_name(value: Any, current: str, others: list[str]) -> str:
    """The new name for a printer, or :class:`RunRequestError` explaining why it cannot be used."""

    name = " ".join(str(value or "").split())
    if not name:
        raise RunRequestError({"new_name": "Enter the printer's new name."})
    if len(name) > MAX_NAME_LENGTH:
        raise RunRequestError({"new_name": f"Printer names can be at most {MAX_NAME_LENGTH} characters."})
    if "\\" in name or "," in name or any(ord(char) < 32 for char in name):
        raise RunRequestError({"new_name": "Printer names cannot contain backslashes or commas."})
    if name == current:
        raise RunRequestError({"new_name": "That is already the printer's name."})
    if any(name.casefold() == other.casefold() for other in others if other != current):
        raise RunRequestError({"new_name": "Another printer on this device already has that name."})
    return name


async def queue_action(agent: Mapping[str, Any] | None, printer_id: int, action: str, *,
                       new_name: Any = None, user_id: int | None = None) -> dict[str, Any]:
    """Rename or remove a device's printer.

    Returns ``{"printer": name, "run_id": id}`` for a change queued on the
    device, or ``{"printer": name, "run_id": None}`` when a printer no longer
    seen was simply forgotten."""

    if action not in ACTIONS:
        raise RunRequestError({"action": "Choose to rename or remove the printer."})
    if not is_windows(agent):
        raise RunRequestError({"device": "Printer changes need an RMM agent on Windows."})
    agent_id = int(agent["id"])
    rows = await repo.list_printers(agent_id)
    printer = next((row for row in rows if int(row["id"]) == int(printer_id)), None)
    if printer is None:
        raise RunRequestError({"printer": "That printer is no longer listed for this device."})
    shown = _display(printer)
    if shown["pending_action"]:
        raise RunRequestError({"printer": "A change to this printer is already waiting for the device."})
    if action == "remove" and not shown["is_installed"]:
        await repo.delete_printer(agent_id, int(printer["id"]))
        return {"printer": printer["name"], "run_id": None}
    if action == "rename":
        if not shown["is_installed"]:
            raise RunRequestError({"printer": "Only printers still installed on the device can be renamed."})
        if shown["is_connection"]:
            raise RunRequestError({"printer": "Printer connections to a print server are named by the server "
                                              "and cannot be renamed on the device."})
        new_name = clean_new_name(new_name, printer["name"], [row["name"] for row in rows if row["is_installed"]])
    else:
        new_name = None
    if agent.get("status") != "active":
        raise RunRequestError({"device": "Printer changes need an active RMM agent."})
    content = action_script_content()
    script = {**_builtin_script(content), "name": "Printer " + action, "path": "builtin/printer_action.ps1"}
    problem = rmm_scripts.device_problem(script, agent)
    if problem:
        raise RunRequestError({"device": problem})
    request = {"action": action, "name": printer["name"], "new_name": new_name}
    run_id = await rmm_repo.create_run(
        script=script, content=content, agent_id=agent_id, company_id=int(agent["company_id"]),
        asset_id=agent.get("asset_id"), requested_by_user_id=user_id, inputs=[],
        payload_encrypted=encrypt_secret(json.dumps({"env": {"MYPORTAL_PRINTER_ACTION": json.dumps(request)}})),
        timeout_seconds=ACTION_TIMEOUT_SECONDS, expires_at=utcnow() + ACTION_EXPIRY, run_source=ACTION_RUN_SOURCE,
    )
    await repo.set_pending(agent_id, int(printer["id"]), action=action, new_name=new_name, run_id=run_id)
    rmm_scripts.notify_agent(agent_id)
    return {"printer": printer["name"], "new_name": new_name, "run_id": run_id}


async def capture_action(agent: Mapping[str, Any], run: Mapping[str, Any], stdout: str) -> str:
    """Apply a finished rename or removal to the stored printer list."""

    if run.get("run_source") != ACTION_RUN_SOURCE:
        return stdout
    stdout, encoded = take_marker(stdout, ACTION_MARKER)
    agent_id = int(agent["id"])
    printer = await repo.get_by_pending_run(agent_id, int(run["id"]))
    if printer is None:
        return stdout
    try:
        result = json.loads(encoded) if encoded else {}
    except ValueError:
        result = {}
    status = result.get("status") if isinstance(result, Mapping) else None
    if status == "removed" and printer["pending_action"] == "remove":
        await repo.delete_printer(agent_id, int(printer["id"]))
    elif status == "renamed" and printer["pending_action"] == "rename" and printer.get("pending_name"):
        await repo.rename_printer(agent_id, int(printer["id"]), printer["pending_name"])
    else:
        await repo.clear_pending(agent_id, int(printer["id"]))
    log_info("RMM printer action finished", agent_id=agent_id, run_id=run.get("id"),
             action=printer["pending_action"], status=status or "failed")
    return stdout


async def strip_live_action_output(agent: Mapping[str, Any], run_id: int, stdout: str) -> str:
    if ACTION_MARKER not in stdout:
        return stdout
    return take_marker(stdout, ACTION_MARKER)[0]


rmm_scripts.register_run_hooks("printers", live_output=strip_live_output, result=capture)
rmm_scripts.register_run_hooks("printer_actions", live_output=strip_live_action_output, result=capture_action)
