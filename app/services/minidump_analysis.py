"""Windows blue screen (minidump) reports from tray devices.

The tray app decodes the kernel minidumps in ``%SystemRoot%\\Minidump`` on
the computer and uploads a small summary of each (the dump file never leaves
the computer). This module validates and stores those summaries
(:func:`ingest`). Everything a device sends is untrusted: summaries are
rebuilt from known keys only, with every value checked and bounded.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from app.repositories import asset_minidumps as repo
from app.services import minidump_stop_codes

MAX_DUMPS_PER_UPLOAD = 20
MAX_SUMMARY_BYTES = 64 * 1024
KEEP_PER_ASSET = 50

_MAX_DRIVERS = 512
_MAX_UNLOADED = 64
_MAX_STACK_DRIVERS = 64
_MAX_NAME = 64
_MAX_UPTIME_SECONDS = 10 * 365 * 24 * 3600
_HEX = re.compile(r"^0x[0-9a-f]{1,16}$")
_NAME_STRIP = re.compile(r"[^A-Za-z0-9._-]")
_ARCHITECTURES = {"x64", "arm64", "x86"}
_EARLIEST = datetime(2000, 1, 1)
_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _hex(value: Any) -> str:
    text = str(value).strip().lower() if isinstance(value, str) else ""
    return text if _HEX.match(text) else ""


def _name(value: Any) -> str:
    """A driver or file name reduced to its base name and to file-name characters."""

    if not isinstance(value, str):
        return ""
    base = re.split(r"[\\/]", value)[-1]
    return _NAME_STRIP.sub("", base)[:_MAX_NAME]


def _int(value: Any, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > maximum:
        return 0
    return value


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.replace(microsecond=0)


def _plausible_time(value: Any, now: datetime) -> str:
    parsed = _parse_time(value)
    if parsed is None or parsed < _EARLIEST or parsed > now + timedelta(days=1):
        return ""
    return parsed.strftime(_TIME_FORMAT)


def _address_ref(value: Any) -> dict[str, str] | None:
    if not isinstance(value, Mapping):
        return None
    return {"address": _hex(value.get("address")), "driver": _name(value.get("driver")),
            "offset": _hex(value.get("offset"))}


def _drivers(value: Any, now: datetime) -> list[dict[str, Any]]:
    drivers = []
    for item in _list(value)[:_MAX_DRIVERS]:
        if not isinstance(item, Mapping) or not _name(item.get("name")):
            continue
        drivers.append({
            "name": _name(item.get("name")), "base": _hex(item.get("base")),
            "size": _int(item.get("size"), 0xFFFFFFFF), "timestamp": _plausible_time(item.get("timestamp"), now),
            "checksum": _int(item.get("checksum"), 0xFFFFFFFF),
        })
    return drivers


def clean_summary(raw: Any, *, now: datetime) -> dict[str, Any] | None:
    """The validated summary of one reported dump, or ``None`` when it cannot be identified.

    Rebuilt from known keys only. A crash time before 2000 or more than a
    day ahead of ``now`` (an empty value in the dump, or a wrong clock) is
    dropped; the caller falls back to the file's time.
    """

    if not isinstance(raw, Mapping):
        return None
    file_name = _name(raw.get("file_name"))
    modified = _parse_time(raw.get("file_modified_at"))
    if not file_name or modified is None:
        return None
    code = raw.get("bugcheck_code")
    if isinstance(code, bool) or not isinstance(code, int) or code < 0 or code > 0xFFFFFFFF:
        code = None
    architecture = raw.get("architecture") if raw.get("architecture") in _ARCHITECTURES else "unknown"
    parameter_drivers = []
    for item in _list(raw.get("parameter_drivers"))[:4]:
        ref = _address_ref(item)
        if ref and isinstance(item.get("parameter"), int) and 1 <= item["parameter"] <= 4:
            parameter_drivers.append({"parameter": item["parameter"], **ref})
    stack_drivers = [
        {"driver": _name(item.get("driver")), "offset": _hex(item.get("offset"))}
        for item in _list(raw.get("stack_drivers"))[:_MAX_STACK_DRIVERS]
        if isinstance(item, Mapping) and _name(item.get("driver"))
    ]
    unloaded = [
        {"name": _name(item.get("name")), "start": _hex(item.get("start")), "end": _hex(item.get("end"))}
        for item in _list(raw.get("unloaded_drivers"))[:_MAX_UNLOADED]
        if isinstance(item, Mapping) and _name(item.get("name"))
    ]
    error = raw.get("error") if isinstance(raw.get("error"), str) else ""
    summary: dict[str, Any] = {
        "file_name": file_name,
        "file_size": _int(raw.get("file_size"), 1 << 40),
        "file_modified_at": modified.strftime(_TIME_FORMAT),
        "supported": raw.get("supported") is True,
        "error": re.sub(r"[^\x20-\x7e]", "", error)[:200],
        "architecture": architecture,
        "bugcheck_code": code,
        "bugcheck_parameters": [_hex(value) for value in _list(raw.get("bugcheck_parameters"))[:4]],
        "crashed_at": _plausible_time(raw.get("crashed_at"), now),
        "uptime_seconds": _int(raw.get("uptime_seconds"), _MAX_UPTIME_SECONDS),
        "windows_build": _int(raw.get("windows_build"), 10_000_000),
        "processor_count": _int(raw.get("processor_count"), 4096),
        "dump_type": _int(raw.get("dump_type"), 0xFFFF),
        "faulting_address": _address_ref(raw.get("faulting_address")),
        "parameter_drivers": parameter_drivers,
        "stack_drivers": stack_drivers,
        "suspect_driver": _name(raw.get("suspect_driver")),
        "drivers": _drivers(raw.get("drivers"), now),
        "unloaded_drivers": unloaded,
    }
    # The driver list is the only part that can be large; trim it to fit.
    while summary["drivers"] and len(json.dumps(summary)) > MAX_SUMMARY_BYTES:
        del summary["drivers"][-max(1, len(summary["drivers"]) // 10):]
    return summary


def _stored_time(value: str) -> datetime | None:
    return datetime.strptime(value, _TIME_FORMAT) if value else None


async def ingest(device: Mapping[str, Any], dumps: Any, *, now: datetime | None = None) -> dict[str, int]:
    """Store the dump summaries a tray device reported, on the device's own asset.

    A device with no asset in its company is accepted and ignored. Returns
    how many were received, stored, already known and rejected.
    """

    now = now or _utcnow()
    items = _list(dumps)[:MAX_DUMPS_PER_UPLOAD]
    counts = {"received": len(items), "stored": 0, "duplicates": 0, "rejected": 0}
    if not items:
        return counts
    asset = await repo.resolve_asset_for_device(device)
    device_id = device.get("id")
    if not asset or not isinstance(device_id, int):
        return counts
    for raw in items:
        summary = clean_summary(raw, now=now)
        if summary is None:
            counts["rejected"] += 1
            continue
        modified = _stored_time(summary["file_modified_at"])
        record = {
            "file_name": summary["file_name"],
            "file_size": summary["file_size"],
            "file_modified_at": modified,
            # No usable crash time in the dump: the file was written at the crash.
            "crashed_at": _stored_time(summary["crashed_at"]) or (modified if modified <= now + timedelta(days=1) else now),
            "bugcheck_code": summary["bugcheck_code"],
            "bugcheck_name": minidump_stop_codes.name(summary["bugcheck_code"]),
            "suspect_driver": summary["suspect_driver"],
            "supported": summary["supported"],
            "summary": summary,
        }
        stored = await repo.upsert_dump(
            company_id=int(asset["company_id"]), asset_id=int(asset["id"]), tray_device_id=device_id, record=record,
        )
        counts["stored" if stored else "duplicates"] += 1
    if counts["stored"]:
        await repo.prune(int(asset["id"]), keep=KEEP_PER_ASSET)
    return counts
