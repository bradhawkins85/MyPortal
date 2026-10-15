"""Windows Update status on each RMM device, and installing the updates a technician picks.

A built-in PowerShell scan (``rmm_builtin/windows_updates.ps1``) runs through
the device's RMM runner as an ``update_scan`` run: daily on connected Windows
devices (:func:`tick`), when a technician presses **Scan now** on the asset
page, and at the end of every patch job that installs Windows updates. It asks
the device's configured update service (Windows Update, Microsoft Update or
WSUS) which updates are still needed, reads the device's Windows Update
history and installed hotfixes, and prints one ``##myportal-windows-updates=``
line of gzip-compressed, base64-encoded JSON. :func:`capture` takes that line
out of the stored run output and :func:`ingest` saves it. Only MyPortal's own
scan and patch runs can report a status.

:func:`queue_install` queues a patch job (``run_source = 'patch'``, so it
shares the one-patch-job-per-device rule and run history of patch policies)
that installs just the chosen missing updates, with the restart the
technician picks. No new RMM agent version is needed.

Technicians can block updates for one device, one company or every company
(:func:`block_updates`, :func:`block_kb`). A block names a KB number, or the
update ID of an update without one. MyPortal never installs a blocked update:
chosen installs refuse it and every patch job skips it. Scan and patch jobs
receive the device's blocks in ``MYPORTAL_WU_BLOCKS`` when the device collects
them (:func:`blocks_env`), hide blocked updates from Windows Update so
automatic updates leave them alone, and show again the updates MyPortal hid
that are no longer blocked.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import zlib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from app.core.logging import log_info, log_warning
from app.repositories import rmm as rmm_repo
from app.repositories import rmm_patching as patch_repo
from app.repositories import rmm_windows_updates as repo
from app.security.encryption import encrypt_secret
from app.services import rmm_scripts
from app.services.rmm_inventory import format_bytes
from app.services.rmm_scripts import RunRequestError

RUN_SOURCE = "update_scan"
PATCH_RUN_SOURCE = "patch"
CAPTURE_SOURCES = frozenset({RUN_SOURCE, PATCH_RUN_SOURCE})
MARKER = "##myportal-windows-updates="

SCAN_INTERVAL = timedelta(hours=24)
# A scan that failed or expired is asked for again after this long, not on every tick.
RETRY_INTERVAL = timedelta(hours=6)
SCANS_PER_TICK = 25
SCAN_TIMEOUT_SECONDS = 1800
AUTO_SCAN_EXPIRY = timedelta(minutes=30)
MANUAL_SCAN_EXPIRY = timedelta(hours=24)
INSTALL_EXPIRY = timedelta(hours=1)
INSTALL_TIMEOUT_SECONDS = 7200
MAX_SELECTED = 100

MAX_ENCODED_CHARS = 4 * 1024 * 1024
MAX_DECODED_BYTES = 16 * 1024 * 1024
MAX_MISSING = 500
MAX_HISTORY = 1500
MAX_HOTFIXES = 1000

UPDATE_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_KB = re.compile(r"\bKB(\d{5,9})\b", re.IGNORECASE)
_KB_INPUT = re.compile(r"^\s*(?:KB)?\s*(\d{5,9})\s*$", re.IGNORECASE)
_HRESULT = re.compile(r"^0x[0-9A-F]{8}$")

OPERATIONS = {1: "install", 2: "uninstall"}
RESULTS = {0: "not_started", 1: "in_progress", 2: "succeeded", 3: "succeeded_with_errors", 4: "failed", 5: "aborted"}
SUCCEEDED = ("succeeded", "succeeded_with_errors")
RESULT_LABELS = {
    "not_started": "Not started", "in_progress": "In progress", "succeeded": "Succeeded",
    "succeeded_with_errors": "Succeeded with errors", "failed": "Failed", "aborted": "Cancelled", "unknown": "Unknown",
}
RESULT_STATUS = {"succeeded": "success", "succeeded_with_errors": "warning", "failed": "danger", "aborted": "warning"}
IMPORTANT_CLASSIFICATIONS = ("Security Updates", "Critical Updates")
IMPORTANT_SEVERITIES = ("Critical", "Important")
REBOOT_MODES = {
    "never": "Don't restart",
    "no_users": "Restart if needed, only when no one is signed in",
    "always": "Restart if needed, after warning signed-in users",
}
DEFAULT_REBOOT_DELAY_MINUTES = 15
BLOCK_SCOPES = {"device": "This device", "company": "This company", "global": "Every company"}
MAX_REASON = 500
# Windows limits one environment variable to 32,767 characters.
MAX_RELEASE = 500
_CLIENT_LABELS = {
    "myportal patch management": "MyPortal",
    "updateorchestrator": "Windows automatic updates",
    "automatic updates": "Windows automatic updates",
    "automaticupdates": "Windows automatic updates",
    "automaticupdatesservice": "Windows automatic updates",
}
_BUILTIN = Path(__file__).parent / "rmm_builtin"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def is_windows(agent: Mapping[str, Any] | None) -> bool:
    return bool(agent) and str(agent.get("os") or "").lower() == "windows"


# --------------------------------------------------------------------------- #
# Scripts
# --------------------------------------------------------------------------- #


def functions_script() -> str:
    """The scan functions, shared by the scan job and patch jobs."""

    return (_BUILTIN / "windows_updates.ps1").read_text(encoding="utf-8")


def scan_script_content() -> str:
    return (functions_script() + "\n$ErrorActionPreference = 'Stop'\nSend-MyPortalWindowsUpdateState\n"
            "if ($script:MyPortalUpdateScanErrors) { exit 1 }\n")


def _builtin_script(name: str, path: str, content: str) -> dict[str, Any]:
    return {"id": None, "name": name, "path": path, "language": "powershell",
            "content_sha256": hashlib.sha256(content.encode()).hexdigest()}


# --------------------------------------------------------------------------- #
# Reading a scan
# --------------------------------------------------------------------------- #


def take_marker(stdout: str) -> tuple[str, str | None]:
    """Remove the status line from ``stdout``; return the rest and the encoded status."""

    if MARKER not in stdout:
        return stdout, None
    encoded = None
    kept = []
    for line in stdout.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith(MARKER):
            encoded = stripped[len(MARKER):]
            continue
        kept.append(line)
    return "".join(kept), encoded


def decode(encoded: str) -> Any:
    if len(encoded) > MAX_ENCODED_CHARS:
        raise ValueError("the status is too large")
    try:
        compressed = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("the status is not valid base64") from None
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        raw = inflater.decompress(compressed, MAX_DECODED_BYTES)
    except zlib.error:
        raise ValueError("the status could not be decompressed") from None
    if inflater.unconsumed_tail:
        raise ValueError("the status is too large")
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ValueError("the status is not valid JSON") from None


def _text(value: Any, limit: int = 255) -> str | None:
    if value is None:
        return None
    text = str(value).replace("\x00", "").strip()
    return text[:limit] or None


def _int(value: Any, low: int = 0, high: int = 2 ** 62) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if low <= number <= high else None


def _time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _kb(*values: Any) -> str | None:
    found: list[str] = []
    for value in values:
        for number in _KB.findall(str(value or "")):
            if "KB" + number not in found:
                found.append("KB" + number)
    return ",".join(found)[:255] or None


def _update_id(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    return text if UPDATE_ID.fullmatch(text) else None


def normalise(payload: Any) -> dict[str, Any]:
    """Check and trim a device's status. Each list is ``None`` when the device
    could not read it, so the stored copy is kept rather than emptied."""

    if not isinstance(payload, Mapping) or payload.get("version") != 1:
        raise ValueError("the status format is not supported")
    errors = [str(error)[:500] for error in (payload.get("errors") or [])[:20] if str(error).strip()] \
        if isinstance(payload.get("errors"), list) else []
    missing = None
    if isinstance(payload.get("missing"), list):
        missing, seen = [], set()
        for item in payload["missing"][:MAX_MISSING]:
            if not isinstance(item, Mapping):
                continue
            update_id, title = _update_id(item.get("update_id")), _text(item.get("title"), 512)
            if not update_id or not title or update_id in seen:
                continue
            seen.add(update_id)
            missing.append({
                "update_id": update_id,
                "revision": _int(item.get("revision")),
                "title": title,
                "kb": _kb(item.get("kb"), title),
                "classification": _text(item.get("classification"), 64),
                "severity": _text(item.get("severity"), 32),
                "size_bytes": _int(item.get("size_bytes")),
                "released_on": _date(item.get("released")),
                "is_downloaded": item.get("downloaded") is True,
                "reboot_behavior": _int(item.get("reboot_behavior"), 0, 2),
                "is_interactive": item.get("interactive") is True,
                "is_optional": item.get("optional") is True,
                "is_hidden": item.get("hidden") is True,
            })
    history = None
    if isinstance(payload.get("history"), list):
        history = []
        for item in payload["history"][:MAX_HISTORY]:
            title = _text(item.get("title"), 512) if isinstance(item, Mapping) else None
            if not title:
                continue
            hresult = str(item.get("hresult") or "").upper().replace("0X", "0x", 1)
            history.append({
                "update_id": _update_id(item.get("update_id")),
                "title": title,
                "kb": _kb(title),
                "classification": _text(item.get("classification"), 64),
                "operation": OPERATIONS.get(_int(item.get("operation")), "other"),
                "result": RESULTS.get(_int(item.get("result")), "unknown"),
                "hresult": hresult if _HRESULT.fullmatch(hresult) and hresult != "0x00000000" else None,
                "occurred_at": _time(item.get("date")),
                "client_application": _text(item.get("client")),
            })
    hotfixes = None
    if isinstance(payload.get("hotfixes"), list):
        hotfixes = []
        for item in payload["hotfixes"][:MAX_HOTFIXES]:
            kb = _kb(item.get("kb")) if isinstance(item, Mapping) else None
            if not kb:
                continue
            description = _text(item.get("description"), 128)
            installed_on = _date(item.get("installed_on"))
            hotfixes.append({
                "update_id": None, "title": f"{description} ({kb})" if description else kb, "kb": kb,
                "classification": None, "operation": "install", "result": "succeeded", "hresult": None,
                "occurred_at": datetime(installed_on.year, installed_on.month, installed_on.day) if installed_on else None,
                "client_application": _text(item.get("installed_by")),
            })
    changes = {}
    for key in ("hid", "unhid"):
        values = payload.get(key) if isinstance(payload.get(key), list) else []
        changes[key] = list(dict.fromkeys(filter(None, (_update_id(value) for value in values[:MAX_MISSING]))))
    return {
        **changes,
        "scanned_at": _time(payload.get("scanned_at")),
        "source": _text(payload.get("source")),
        "reboot_pending": payload.get("reboot_pending") is True,
        "errors": errors,
        "missing": missing,
        "history": history,
        "hotfixes": hotfixes,
    }


async def ingest(agent: Mapping[str, Any], run: Mapping[str, Any], payload: Any) -> dict[str, Any]:
    state = normalise(payload)
    now = utcnow()
    # A device clock in the future must not make the scan look fresh forever.
    scanned_at = min(state["scanned_at"] or now, now)
    await repo.save_scan(
        dict(agent), run_id=run.get("id"), scanned_at=scanned_at, source=state["source"],
        reboot_pending=state["reboot_pending"], errors=state["errors"], missing=state["missing"],
        history=state["history"], hotfixes=state["hotfixes"],
    )
    present = {item["update_id"] for item in state["missing"]} if state["missing"] is not None else None
    await repo.save_hidden(int(agent["id"]), hid=state["hid"], unhid=state["unhid"], present=present)
    summary = {key: None if state[key] is None else len(state[key]) for key in ("missing", "history", "hotfixes")}
    log_info("RMM Windows Update status received", agent_id=agent.get("id"), run_id=run.get("id"), **summary,
             hid=len(state["hid"]), unhid=len(state["unhid"]))
    return {**summary, "errors": len(state["errors"]), "hid": len(state["hid"]), "unhid": len(state["unhid"])}


def _summary_note(summary: Mapping[str, Any]) -> str:
    parts = []
    if summary["missing"] is not None:
        parts.append(f"{summary['missing']} missing update{'s' if summary['missing'] != 1 else ''}")
    if summary["history"] is not None:
        parts.append(f"{summary['history']} history entr{'ies' if summary['history'] != 1 else 'y'}")
    if summary["hotfixes"] is not None:
        parts.append(f"{summary['hotfixes']} installed hotfix{'es' if summary['hotfixes'] != 1 else ''}")
    note = "MyPortal saved the Windows Update status: " + (", ".join(parts) or "nothing could be read") + "."
    if summary.get("hid") or summary.get("unhid"):
        note += f" Blocked updates hidden: {summary.get('hid', 0)}; unblocked updates shown again: {summary.get('unhid', 0)}."
    return note


async def capture(agent: Mapping[str, Any], run: Mapping[str, Any], stdout: str) -> str:
    """Save the status a built-in scan or patch run reported, replacing its
    line in the output with a short note. Other runs' output is unchanged."""

    if run.get("run_source") not in CAPTURE_SOURCES:
        return stdout
    stdout, encoded = take_marker(stdout)
    if encoded is None:
        return stdout
    try:
        note = _summary_note(await ingest(agent, run, decode(encoded)))
    except ValueError as exc:
        log_warning("RMM Windows Update status rejected", agent_id=agent.get("id"), run_id=run.get("id"), error=str(exc))
        note = f"MyPortal could not read the Windows Update status: {exc}."
    separator = "" if not stdout or stdout.endswith("\n") else "\n"
    return stdout + separator + note + "\n"


async def strip_live_output(agent: Mapping[str, Any], run_id: int, stdout: str) -> str:
    """Live output never shows the status line of a built-in run."""

    if MARKER not in stdout:
        return stdout
    run = await rmm_repo.get_agent_run(run_id, int(agent["id"]))
    if run and run.get("run_source") in CAPTURE_SOURCES:
        return take_marker(stdout)[0]
    return stdout


# --------------------------------------------------------------------------- #
# Queueing scans and installs
# --------------------------------------------------------------------------- #


async def queue_scan(agent: Mapping[str, Any], *, user_id: int | None = None,
                     expires_in: timedelta = MANUAL_SCAN_EXPIRY) -> int | None:
    """Queue a scan, or return ``None`` when one is already waiting or running."""

    if not is_windows(agent) or agent.get("status") != "active":
        raise RunRequestError({"device": "Windows Update scans need an active RMM agent on Windows."})
    agent_id = int(agent["id"])
    if await repo.has_active_scan(agent_id, RUN_SOURCE):
        return None
    content = scan_script_content()
    script = _builtin_script("Windows Update scan", "builtin/windows_updates.ps1", content)
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


def _reboot_settings(mode: Any, delay: Any) -> dict[str, Any]:
    errors: dict[str, str] = {}
    if mode not in REBOOT_MODES:
        errors["reboot_mode"] = "Choose whether the device may restart."
    try:
        minutes = int(str(delay if delay not in (None, "") else DEFAULT_REBOOT_DELAY_MINUTES))
        if not 5 <= minutes <= 120:
            raise ValueError
    except (TypeError, ValueError):
        errors["reboot_delay_minutes"] = "Enter a warning time from 5 to 120 minutes."
        minutes = DEFAULT_REBOOT_DELAY_MINUTES
    if errors:
        raise RunRequestError(errors)
    # Active hours do not apply: the technician chose to restart now.
    return {"reboot_mode": mode, "reboot_delay_minutes": minutes, "immediate": True,
            "active_start": 0, "active_end": 0}


async def queue_install(agent: Mapping[str, Any], update_ids: list[Any], *, reboot_mode: Any,
                        reboot_delay_minutes: Any, user_id: int | None) -> int:
    if not is_windows(agent) or agent.get("status") != "active":
        raise RunRequestError({"device": "Windows updates can only be installed through an active RMM agent on Windows."})
    wanted: list[str] = []
    for value in update_ids:
        update_id = str(value or "").strip().lower()
        if update_id and update_id not in wanted:
            wanted.append(update_id)
    if not wanted:
        raise RunRequestError({"updates": "Choose at least one missing update."})
    if len(wanted) > MAX_SELECTED:
        raise RunRequestError({"updates": f"Choose at most {MAX_SELECTED} updates at a time."})
    reboot = _reboot_settings(reboot_mode, reboot_delay_minutes)
    agent_id = int(agent["id"])
    missing = {str(row["update_id"]).lower(): row for row in await repo.list_missing(agent_id)}
    chosen = [missing.get(update_id) for update_id in wanted]
    if any(row is None for row in chosen):
        raise RunRequestError({"updates": "Some of the chosen updates are no longer missing on this device. "
                                          "Scan again and choose from the new list."})
    if any(row.get("is_interactive") for row in chosen):
        raise RunRequestError({"updates": "Updates that need someone at the device cannot be installed remotely."})
    rules = await repo.list_device_blocks(int(agent["company_id"]), agent.get("asset_id"))
    if any(blocked_by(row, rules) for row in chosen):
        raise RunRequestError({"updates": "Blocked updates cannot be installed. Unblock them first."})
    if await patch_repo.has_active_run(agent_id):
        raise RunRequestError({"updates": "A patch job is already waiting or running on this device."})
    from app.services import rmm_patching

    config = {"manual_windows": {"update_ids": wanted}, "windows": None, "apps": [], "reboot": reboot,
              "policies": [], "reboot_policy": None}
    content = rmm_patching.script_content()
    script = _builtin_script("Install Windows updates", "builtin/patch.ps1", content)
    problem = rmm_scripts.device_problem(script, agent)
    if problem:
        raise RunRequestError({"device": problem})
    restart = REBOOT_MODES[reboot["reboot_mode"]]
    if reboot["reboot_mode"] != "never":
        restart += f" ({reboot['reboot_delay_minutes']}-minute warning)"
    names = ", ".join(row.get("kb") or row["title"] for row in chosen)
    run_id = await rmm_repo.create_run(
        script=script, content=content, agent_id=agent_id, company_id=int(agent["company_id"]),
        asset_id=agent.get("asset_id"), requested_by_user_id=user_id,
        inputs=[{"key": "updates", "label": "Updates", "value": names[:2000]},
                {"key": "restart", "label": "Restart", "value": restart}],
        payload_encrypted=encrypt_secret(json.dumps({"env": {"MYPORTAL_PATCH_PLAN": json.dumps(config)}})),
        timeout_seconds=INSTALL_TIMEOUT_SECONDS, expires_at=utcnow() + INSTALL_EXPIRY, run_source=PATCH_RUN_SOURCE,
    )
    rmm_scripts.notify_agent(agent_id)
    return run_id


def install_is_current(agent: Mapping[str, Any], config: Mapping[str, Any]) -> bool:
    """A chosen-updates job still applies at dispatch on a Windows device; the
    device skips any update that has since been installed."""

    manual = config.get("manual_windows")
    return (is_windows(agent) and isinstance(manual, Mapping) and bool(manual.get("update_ids"))
            and all(_update_id(value) for value in manual["update_ids"])
            and not config.get("apps") and config.get("windows") is None)


async def tick() -> None:
    """Scan connected Windows devices whose status is a day old."""

    now = utcnow()
    agents = await repo.list_agents_due(
        run_source=RUN_SOURCE, received_before=now - SCAN_INTERVAL, requested_before=now - RETRY_INTERVAL,
        seen_after=now - rmm_scripts.AGENT_CONNECTED_WINDOW, limit=SCANS_PER_TICK,
    )
    for agent in agents:
        try:
            await queue_scan(agent, expires_in=AUTO_SCAN_EXPIRY)
        except Exception as exc:  # noqa: BLE001 - one device must not stop the others
            log_warning("Could not queue a Windows Update scan", agent_id=agent.get("id"), error=str(exc))


# --------------------------------------------------------------------------- #
# Blocking updates
# --------------------------------------------------------------------------- #


def block_scope(rule: Mapping[str, Any]) -> str:
    if rule.get("asset_id") is not None:
        return "device"
    return "company" if rule.get("company_id") is not None else "global"


def block_key(update: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """``(kb, update_id)`` for a new block: the KB number when the update has
    exactly one, which covers its other revisions and architectures; otherwise
    the update itself."""

    kbs = [kb for kb in str(update.get("kb") or "").split(",") if kb]
    return (kbs[0], None) if len(kbs) == 1 else (None, str(update["update_id"]).lower())


def rule_matches(rule: Mapping[str, Any], update: Mapping[str, Any]) -> bool:
    if rule.get("update_id"):
        return str(rule["update_id"]).lower() == str(update.get("update_id") or "").lower()
    return bool(rule.get("kb")) and str(rule["kb"]).upper() in str(update.get("kb") or "").upper().split(",")


def blocked_by(update: Mapping[str, Any], rules: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [rule for rule in rules if rule_matches(rule, update)]


def can_edit_block(rule: Mapping[str, Any], *, can_edit: bool, is_super_admin: bool) -> bool:
    """Company and device blocks need Scripts write access; blocks for every company need a super admin."""

    return bool(can_edit) and (block_scope(rule) != "global" or bool(is_super_admin))


async def blocks_env(agent: Mapping[str, Any]) -> dict[str, Any]:
    """What a scan or patch job needs at dispatch: the device's blocked KBs and
    update IDs, and the updates MyPortal hid that may be shown again (the
    device keeps hiding any that are still blocked)."""

    rules = await repo.list_device_blocks(int(agent["company_id"]), agent.get("asset_id"))
    return {
        "kbs": sorted({str(rule["kb"]).upper() for rule in rules if rule.get("kb")}),
        "update_ids": sorted({str(rule["update_id"]).lower() for rule in rules if rule.get("update_id")}),
        "release": (await repo.list_hidden(int(agent["id"])))[:MAX_RELEASE],
    }


def _scope_ids(scope: Any, *, company_id: int, asset_id: int | None, is_super_admin: bool) -> tuple[int | None, int | None]:
    if scope not in BLOCK_SCOPES or (scope == "device" and asset_id is None):
        raise RunRequestError({"scope": "Choose where to block the update."})
    if scope == "global" and not is_super_admin:
        raise RunRequestError({"scope": "Only super admins can block updates for every company."})
    if scope == "global":
        return None, None
    return int(company_id), (int(asset_id) if scope == "device" else None)


def _reason(value: Any) -> str | None:
    return _text(value, MAX_REASON)


async def block_updates(agent: Mapping[str, Any], update_ids: list[Any], *, scope: Any, reason: Any,
                        user_id: int | None, is_super_admin: bool) -> int:
    """Block chosen missing updates of a device; return how many new blocks were added."""

    wanted = list(dict.fromkeys(filter(None, (_update_id(value) for value in update_ids))))
    if not wanted:
        raise RunRequestError({"updates": "Choose at least one missing update."})
    if len(wanted) > MAX_SELECTED:
        raise RunRequestError({"updates": f"Choose at most {MAX_SELECTED} updates at a time."})
    company_id, asset_id = _scope_ids(scope, company_id=int(agent["company_id"]), asset_id=agent.get("asset_id"),
                                      is_super_admin=is_super_admin)
    missing = {str(row["update_id"]).lower(): row for row in await repo.list_missing(int(agent["id"]))}
    if any(update_id not in missing for update_id in wanted):
        raise RunRequestError({"updates": "Some of the chosen updates are no longer missing on this device. "
                                          "Scan again and choose from the new list."})
    added = 0
    for update_id in wanted:
        row = missing[update_id]
        kb, block_update_id = block_key(row)
        if await repo.find_block(company_id=company_id, asset_id=asset_id, kb=kb, update_id=block_update_id):
            continue
        await repo.create_block(company_id=company_id, asset_id=asset_id, kb=kb, update_id=block_update_id,
                                title=row["title"], reason=_reason(reason), user_id=user_id)
        added += 1
    return added


async def block_kb(company_id: int, kb: Any, *, scope: Any, reason: Any, user_id: int | None,
                   is_super_admin: bool) -> int | None:
    """Block a KB number for a company or every company before any device reports it.
    Returns the new block's ID, or ``None`` when it was already blocked there."""

    match = _KB_INPUT.match(str(kb or ""))
    if not match:
        raise RunRequestError({"kb": "Enter a KB number, such as KB5031356."})
    if scope not in ("company", "global"):
        raise RunRequestError({"scope": "Choose this company or every company."})
    company, _asset = _scope_ids(scope, company_id=company_id, asset_id=None, is_super_admin=is_super_admin)
    number = "KB" + match.group(1)
    if await repo.find_block(company_id=company, asset_id=None, kb=number, update_id=None):
        return None
    return await repo.create_block(company_id=company, asset_id=None, kb=number, update_id=None, title=number,
                                   reason=_reason(reason), user_id=user_id)


async def editable_block(block_id: int, company_id: int, *, can_edit: bool, is_super_admin: bool) -> dict[str, Any]:
    """A block this technician's company can see and they may remove."""

    rule = await repo.get_block(block_id)
    if not rule or rule.get("company_id") not in (None, int(company_id)):
        raise LookupError("block not found")
    if not can_edit_block(rule, can_edit=can_edit, is_super_admin=is_super_admin):
        raise PermissionError("Only super admins can unblock updates for every company.")
    return rule


def describe_block(rule: Mapping[str, Any], *, can_edit: bool, is_super_admin: bool) -> dict[str, Any]:
    item = _iso(rule)
    scope = block_scope(rule)
    item["scope"] = scope
    item["scope_label"] = {"device": rule.get("asset_name") or f"Asset {rule.get('asset_id')}",
                           "company": rule.get("company_name") or "This company",
                           "global": "Every company"}[scope]
    item["matches_label"] = rule.get("kb") or "This update only"
    item["can_unblock"] = can_edit_block(rule, can_edit=can_edit, is_super_admin=is_super_admin)
    return item


# --------------------------------------------------------------------------- #
# The asset page
# --------------------------------------------------------------------------- #


def _iso(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value.isoformat() if isinstance(value, (datetime, date)) else value for key, value in row.items()}


def _key(row: Mapping[str, Any]) -> str:
    return str(row.get("update_id") or row.get("kb") or row.get("title") or "").casefold()


def _client_label(value: Any) -> str:
    text = str(value or "").strip()
    return _CLIENT_LABELS.get(text.casefold(), text) or "—"


def _sort_time(row: Mapping[str, Any]) -> str:
    return str(row.get("occurred_at") or "")


def installed_updates(history: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Each update's latest successful install, unless it was uninstalled after
    that, plus hotfixes Windows Update history does not mention."""

    installs: dict[str, Mapping[str, Any]] = {}
    removals: dict[str, Mapping[str, Any]] = {}
    for row in sorted((r for r in history if r.get("source") == "history"), key=_sort_time, reverse=True):
        if row.get("result") not in SUCCEEDED:
            continue
        if row.get("operation") == "install":
            installs.setdefault(_key(row), row)
        elif row.get("operation") == "uninstall":
            removals.setdefault(_key(row), row)
    installed = [dict(row) for key, row in installs.items()
                 if not (key in removals and _sort_time(removals[key]) > _sort_time(row))]
    known = {kb for row in installed for kb in str(row.get("kb") or "").split(",") if kb}
    for row in history:
        if row.get("source") == "hotfix" and not set(str(row.get("kb") or "").split(",")) & known:
            installed.append(dict(row))
    return sorted(installed, key=_sort_time, reverse=True)


def _display(row: Mapping[str, Any]) -> dict[str, Any]:
    item = _iso(row)
    item["result_label"] = RESULT_LABELS.get(str(row.get("result")), "Unknown")
    item["result_status"] = RESULT_STATUS.get(str(row.get("result")), "neutral")
    item["client_label"] = _client_label(row.get("client_application"))
    item["installed_on"] = str(item.get("occurred_at") or "")[:10] if row.get("source") == "hotfix" else None
    return item


async def asset_context(agent: Mapping[str, Any] | None, *, can_run: bool,
                        is_super_admin: bool = False) -> dict[str, Any] | None:
    """The asset page's Windows updates section, or ``None`` for devices
    without an RMM agent on Windows."""

    if not is_windows(agent):
        return None
    agent_id = int(agent["id"])
    scan = await repo.get_scan(agent_id)
    received = scan.get("received_at") if scan else None
    missing = await repo.list_missing(agent_id) if received else []
    history = await repo.list_history(agent_id) if received else []
    can_edit = bool(can_run) and agent.get("status") == "active"
    rules = [describe_block(rule, can_edit=can_edit, is_super_admin=is_super_admin)
             for rule in await repo.list_device_blocks(int(agent["company_id"]), agent.get("asset_id"))]
    failures: dict[str, Mapping[str, Any]] = {}
    for row in sorted(history, key=_sort_time, reverse=True):
        if row.get("source") == "history" and row.get("operation") == "install" and row.get("result") == "failed":
            failures.setdefault(_key(row), row)
    missing_items, blocked_items = [], []
    for row in missing:
        item = _iso(row)
        failure = failures.get(_key(row))
        item["last_failure"] = _display(failure) if failure else None
        item["important"] = row.get("classification") in IMPORTANT_CLASSIFICATIONS or row.get("severity") in IMPORTANT_SEVERITIES
        item["size_label"] = format_bytes(row.get("size_bytes")) or "—"
        item["blocked_by"] = blocked_by(row, rules)
        (blocked_items if item["blocked_by"] else missing_items).append(item)
    installed = [_display(row) for row in installed_updates(history)]
    attempts = [_display(row) for row in sorted(history, key=_sort_time, reverse=True)
                if row.get("source") == "history"]
    month_ago = (utcnow() - timedelta(days=30)).isoformat()
    return {
        "scan": _iso(scan) if received else None,
        "requested_at": _iso(scan).get("scan_requested_at") if scan else None,
        "missing": missing_items,
        "blocked": blocked_items,
        "block_rules": rules,
        "installed": installed,
        "history": attempts,
        "counts": {
            "missing": len(missing_items),
            "blocked": len(blocked_items),
            "important": sum(1 for item in missing_items if item["important"]),
            "installed": len(installed),
            "failed_recently": sum(1 for item in attempts
                                   if item["result"] == "failed" and str(item.get("occurred_at") or "") >= month_ago),
        },
        "scan_pending": await repo.has_active_scan(agent_id, RUN_SOURCE),
        "install_pending": await patch_repo.has_active_run(agent_id),
        "can_run": can_edit,
        "block_scopes": {key: label for key, label in BLOCK_SCOPES.items() if key != "global" or is_super_admin},
        "reboot_modes": REBOOT_MODES,
        "default_reboot_delay": DEFAULT_REBOOT_DELAY_MINUTES,
        "max_selected": MAX_SELECTED,
    }
