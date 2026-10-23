"""Logs the AI agents need but cannot read, and the approved list built from them.

The AI troubleshooter (:mod:`app.services.ai_troubleshooter`) and the health
check (:mod:`app.services.ai_health_check`) only collect allowlisted event log
channels and log files in allowlisted folders. Whenever either asks for a log
it cannot have, the log is recorded here for a super admin to review on
``/admin/required-logs``:

* ``not_allowed`` - the source is not on the approved list, so it was never
  requested from the endpoint. Approving it adds the event log channel, or the
  folder holding the log file, to the approved list. Approved entries join the
  owner's ``TROUBLESHOOT_EVENT_LOGS_WINDOWS`` / ``TROUBLESHOOT_LOG_FOLDERS_*``
  settings for every later run, and the tray agent re-validates each one.
* ``unavailable`` - the source is allowed but the endpoint could not read it
  (channel disabled or missing, no matching file, access denied).

Recording is best-effort: a database problem never stops a troubleshooting
run or a health check.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Iterable, Mapping
from typing import Any

from app.core.logging import log_warning
from app.repositories import ai_required_logs as repo

REASON_NOT_ALLOWED = "not_allowed"
REASON_UNAVAILABLE = "unavailable"
REASONS = (REASON_NOT_ALLOWED, REASON_UNAVAILABLE)

FEATURE_TROUBLESHOOTER = "troubleshooter"
FEATURE_HEALTH_CHECK = "health_check"
FEATURE_LABELS = {FEATURE_TROUBLESHOOTER: "AI troubleshooter", FEATURE_HEALTH_CHECK: "Health check"}

STATUSES = ("open", "approved", "dismissed")

KIND_EVENT_LOG = "event_log"
KIND_FILE = "file"
KIND_MACOS = "macos_log"
KIND_LABELS = {KIND_EVENT_LOG: "Event log", KIND_FILE: "Log file", KIND_MACOS: "macOS log"}

APPROVE_EVENT_LOG = "event_log"
APPROVE_FOLDER = "folder"

MAX_RECORDS_PER_RUN = 16

# Windows event log channel names ("Application", "Microsoft-Windows-Ntfs/
# Operational"). The tray agent applies the same pattern (logsources.go).
_CHANNEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,150}(/[A-Za-z0-9][A-Za-z0-9 ._-]{0,60})?$")
_UNAVAILABLE_SECTION_RE = re.compile(
    r"^\[(?:(?:unavailable|refused): (?P<detail>.+)|(?P<missing>no matching log files found))\]$",
    re.DOTALL,
)

_APPROVED_TTL_SECONDS = 15.0
_approved: dict[str, dict[str, tuple[str, ...]]] = {}
_approved_loaded_at = 0.0


def valid_event_log_channel(name: str) -> bool:
    text = str(name or "")
    return bool(_CHANNEL_RE.match(text)) and text == text.strip() and "  " not in text


def source_kind(source: str) -> str:
    text = str(source or "").strip().lower()
    if text.startswith("file:"):
        return KIND_FILE
    if text.startswith("macos:"):
        return KIND_MACOS
    return KIND_EVENT_LOG


def source_key(platform: str, reason_code: str, source: str) -> str:
    # Windows names and paths are case-insensitive.
    normalised = source.strip().casefold() if platform != "macos" else source.strip()
    return hashlib.sha256(f"{platform}|{reason_code}|{normalised}".encode("utf-8")).hexdigest()


def looks_like_log_source(source: str, platform: str) -> bool:
    """True for something worth an admin's review: a channel name or a log path.

    Keeps free text the model sometimes writes ("Event Viewer", "check the
    logs") off the review list.
    """

    from app.services import ai_troubleshooter

    text = str(source or "").strip()
    kind = source_kind(text)
    if kind == KIND_MACOS:
        return bool(re.fullmatch(r"macos:[a-z0-9_-]{1,40}", text))
    if kind == KIND_FILE:
        path = text[len("file:"):].strip()
        windows = platform != "macos" and (len(path) > 2 and path[1] == ":" or "%" in path)
        candidate = ai_troubleshooter._expand_windows_variables(path) if windows else path
        if not windows and candidate.startswith("~/"):
            candidate = "/Users/*" + candidate[1:]
        segments = ai_troubleshooter._split_path(candidate, windows)
        return bool(segments) and len(segments) >= 2
    if platform == "macos" or not valid_event_log_channel(text):
        return False
    # Channels are either a classic log ("Application") or "Provider/Type".
    return "/" in text or " " not in text


def suggest_approval(source: str, platform: str) -> tuple[str | None, str | None]:
    """The approved-list entry that would let ``source`` be collected."""

    from app.services import ai_troubleshooter

    text = str(source or "").strip()
    kind = source_kind(text)
    if kind == KIND_EVENT_LOG:
        if platform != "macos" and valid_event_log_channel(text):
            return APPROVE_EVENT_LOG, text
        return None, None
    if kind != KIND_FILE:
        return None, None
    path = text[len("file:"):].strip()
    windows = platform != "macos" and (len(path) > 2 and path[1] == ":" or "%" in path)
    candidate = ai_troubleshooter._expand_windows_variables(path) if windows else path
    if not windows and candidate.startswith("~/"):
        candidate = "/Users/*" + candidate[1:]
    segments = ai_troubleshooter._split_path(candidate, windows)
    if not segments or len(segments) < 3:
        return APPROVE_FOLDER, None
    # The folder holding the file, cut back to the last folder without a wildcard.
    folder_segments = segments[:-1]
    while folder_segments and any(ch in folder_segments[-1] for ch in "*?") and folder_segments[-1] != "*":
        folder_segments = folder_segments[:-1]
    while folder_segments and folder_segments[-1] == "*":
        folder_segments = folder_segments[:-1]
    separator = "\\" if windows else "/"
    folder = separator.join(folder_segments)
    if not windows and not folder.startswith("/"):
        folder = "/" + folder
    if ai_troubleshooter._valid_extra_root(folder, windows):
        return APPROVE_FOLDER, folder
    return APPROVE_FOLDER, None


def validate_approval(kind: str | None, value: str, platform: str) -> str | None:
    """Return the cleaned approved-list value, or ``None`` when it is not allowed."""

    from app.services import ai_troubleshooter

    text = str(value or "").strip().strip('"').strip()
    if kind == APPROVE_EVENT_LOG:
        return text if platform != "macos" and valid_event_log_channel(text) else None
    if kind == APPROVE_FOLDER:
        windows = platform != "macos"
        if windows:
            text = text.replace("/", "\\").rstrip("\\")
        else:
            text = text.rstrip("/") or text
        return text if ai_troubleshooter._valid_extra_root(text, windows) else None
    return None


def unavailable_detail(body: str) -> str | None:
    """Why a collected section is empty, from the tray agent's placeholder text."""

    match = _UNAVAILABLE_SECTION_RE.match(str(body or "").strip())
    if not match:
        return None
    return (match.group("detail") or "No matching log files were found on the endpoint.").strip()[:1000]


# ---------------------------------------------------------------------------
# Approved list
# ---------------------------------------------------------------------------


async def refresh_approvals(*, force: bool = False) -> None:
    """Reload approved entries (cheap; cached for a few seconds per worker)."""

    global _approved, _approved_loaded_at
    if not force and time.monotonic() - _approved_loaded_at < _APPROVED_TTL_SECONDS:
        return
    try:
        rows = await repo.list_approved()
    except Exception as exc:  # noqa: BLE001 - keep the last known list
        log_warning("Could not load approved AI log sources", error=str(exc)[:300])
        return
    approved: dict[str, dict[str, list[str]]] = {}
    for row in rows:
        platform = str(row.get("platform") or "")
        kind = str(row.get("approve_kind") or "")
        value = validate_approval(kind, str(row.get("approve_value") or ""), platform)
        if not value:
            continue
        values = approved.setdefault(platform, {}).setdefault(kind, [])
        if value not in values:
            values.append(value)
    _approved = {
        platform: {kind: tuple(values) for kind, values in kinds.items()}
        for platform, kinds in approved.items()
    }
    _approved_loaded_at = time.monotonic()


def approved_values(platform: str, kind: str) -> list[str]:
    platforms = ("windows", "macos") if platform == "unknown" else (platform,)
    values: list[str] = []
    for name in platforms:
        for value in _approved.get(name, {}).get(kind, ()):
            if value not in values:
                values.append(value)
    return values


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


def _features(existing: str, feature: str) -> str:
    items = [item for item in str(existing or "").split(",") if item]
    if feature not in items:
        items.append(feature)
    return ",".join(items)


def _int_or_none(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


async def record(
    entries: Iterable[Mapping[str, Any]],
    *,
    feature: str,
    platform: str,
    company_id: Any = None,
    asset_id: Any = None,
    ticket_id: Any = None,
    health_check_id: Any = None,
) -> int:
    """Record logs that could not be read. Returns how many were recorded.

    Each entry has ``source``, ``reason_code`` (``not_allowed`` or
    ``unavailable``) and optional ``reason`` (why it was wanted) and
    ``detail`` (what the endpoint reported).
    """

    platform = platform if platform in {"windows", "macos"} else "unknown"
    context = {
        "company_id": _int_or_none(company_id),
        "asset_id": _int_or_none(asset_id),
        "ticket_id": _int_or_none(ticket_id),
        "health_check_id": _int_or_none(health_check_id),
    }
    recorded = 0
    seen: set[str] = set()
    for entry in entries:
        if recorded >= MAX_RECORDS_PER_RUN:
            break
        source = " ".join(str(entry.get("source") or "").split())[:512]
        reason_code = str(entry.get("reason_code") or "")
        if not source or reason_code not in REASONS:
            continue
        key = source_key(platform, reason_code, source)
        if key in seen:
            continue
        seen.add(key)
        reason = str(entry.get("reason") or "").strip() or None
        detail = str(entry.get("detail") or "").strip() or None
        try:
            existing = await repo.get_by_key(key)
            if existing:
                status = str(existing.get("status") or "open")
                # Asked for again after approval: the approval did not cover it.
                if status == "approved" and reason_code == REASON_NOT_ALLOWED:
                    status = "open"
                await repo.record_seen(
                    int(existing["id"]), reason=reason, detail=detail,
                    features=_features(str(existing.get("features") or ""), feature), status=status, **context,
                )
            else:
                approve_kind, approve_value = (
                    suggest_approval(source, platform) if reason_code == REASON_NOT_ALLOWED else (None, None)
                )
                await repo.insert(
                    source_key=key, platform=platform, kind=source_kind(source), source=source,
                    reason_code=reason_code, reason=reason, detail=detail, features=feature,
                    approve_kind=approve_kind, approve_value=approve_value, **context,
                )
            recorded += 1
        except Exception as exc:  # noqa: BLE001 - recording must never stop a run
            log_warning("Could not record a required AI log source", source=source[:200], error=str(exc)[:300])
    return recorded


__all__ = [
    "APPROVE_EVENT_LOG",
    "APPROVE_FOLDER",
    "FEATURE_HEALTH_CHECK",
    "FEATURE_TROUBLESHOOTER",
    "REASON_NOT_ALLOWED",
    "REASON_UNAVAILABLE",
    "approved_values",
    "looks_like_log_source",
    "record",
    "refresh_approvals",
    "source_kind",
    "suggest_approval",
    "unavailable_detail",
    "valid_event_log_channel",
    "validate_approval",
]
