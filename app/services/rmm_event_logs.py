"""Windows event logs collected by the RMM agent.

Every agent sends the logs in ``EVENT_LOG_DEFAULT_LOGS`` (:func:`default_logs`;
Application and System when it is empty). Technicians add more logs for a whole
company or one device; an agent collects the defaults plus both lists. Entries are stored in the separate event log
database (``EVENT_LOG_DB_*``, :mod:`app.core.event_log_database`); without it
agents are told collection is off and nothing is stored.

The agent keeps its own place in each log (the last record ID sent) and sends
batches of at most :data:`MAX_BATCH` entries. A record already stored is
skipped, so resending a batch is harmless. Event log rules
(:mod:`app.services.rmm_event_log_rules`) can drop entries before they are
stored or run a script when one arrives.

Storage is kept small three ways:

* **Least severe level.** ``EVENT_LOG_MIN_LEVEL``, or a company's or device's
  own choice (:func:`collection_for`), says how much detail is kept; agents
  leave out anything less severe and :func:`ingest` drops what older agents
  still send. "Log always" events (level 0, such as Security audits) are
  always kept.
* **One copy of each message.** Message text is stored once however many
  devices log it (:func:`app.repositories.rmm_event_logs.message_ids`).
* **Repeats.** An event a device logs again within
  ``EVENT_LOG_REPEAT_WINDOW_MINUTES`` of its first occurrence (same log,
  event ID, level, source, computer and message) is folded into that entry,
  which counts its occurrences (:func:`_collapse`). Rules still see every
  occurrence.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger

from app.core.config import get_settings
from app.core.event_log_database import event_log_db
from app.repositories import rmm_event_logs as repo
from app.services import rmm_event_log_rules

# Collected when EVENT_LOG_DEFAULT_LOGS is empty or holds no valid name.
DEFAULT_LOGS = ("Application", "System")
MAX_EXTRA_LOGS = 25
MAX_BATCH = 500
MAX_MESSAGE_LENGTH = 32_000
COLLECT_INTERVAL_SECONDS = 60
INITIAL_LOOKBACK_HOURS = 24

# Windows channel names: "Security", "Windows PowerShell",
# "Microsoft-Windows-TaskScheduler/Operational", "Microsoft-Windows-Kernel-PnP/Configuration".
_LOG_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.%/-]{0,254}$")

LEVELS = {1: "Critical", 2: "Error", 3: "Warning", 4: "Information", 5: "Verbose"}
VERBOSE = 5
# What each least severe level keeps, for the settings forms.
MAX_LEVEL_CHOICES = (
    (5, "Everything (verbose)"),
    (4, "Information or worse"),
    (3, "Warning or worse"),
    (2, "Error or worse"),
    (1, "Critical only"),
)
# Message text is kept this much longer than entries, because a message's
# last use is only recorded once a day (see prune_messages).
MESSAGE_PRUNE_GRACE = timedelta(days=2)


class EventLogSettingsError(ValueError):
    pass


def level_label(level: Any) -> str:
    try:
        return LEVELS.get(int(level), "Information")
    except (TypeError, ValueError):
        return "Information"


def parse_max_level(value: Any) -> int | None:
    """A least severe level from a number (1-5) or a level name; None when blank or unknown."""

    text = str(value if value is not None else "").strip().casefold()
    if not text:
        return None
    if text.isdigit():
        level = int(text)
        return level if level in LEVELS else None
    names = {label.casefold(): level for level, label in LEVELS.items()}
    names.update({"info": 4, "warn": 3, "everything": 5, "all": 5})
    return names.get(text)


def default_max_level() -> int:
    """The least severe level kept unless a company or device chooses, from ``EVENT_LOG_MIN_LEVEL``."""

    raw = get_settings().event_log_min_level
    level = parse_max_level(raw)
    if level is None:
        if str(raw or "").strip():
            logger.warning("Ignoring invalid EVENT_LOG_MIN_LEVEL {value!r}", value=str(raw)[:40])
        return VERBOSE
    return level


def keeps_level(level: Any, max_level: int) -> bool:
    """Whether an entry at ``level`` is kept when ``max_level`` is the least severe kept."""

    try:
        level = int(level)
    except (TypeError, ValueError):
        level = 4
    return max_level >= VERBOSE or level == 0 or level <= max_level


def valid_log_name(name: str) -> bool:
    return bool(_LOG_NAME.match(name)) and "//" not in name and ".." not in name


def default_logs() -> list[str]:
    """The logs every agent sends, from ``EVENT_LOG_DEFAULT_LOGS``.

    Names are separated by ``;`` or ``,`` (neither can be in a log name).
    Invalid names are skipped with a warning so one typo never stops
    collection; with no valid name the built-in :data:`DEFAULT_LOGS` apply.
    """

    raw = str(get_settings().event_log_default_logs or "")
    logs: list[str] = []
    for item in re.split(r"[;,\r\n]", raw):
        name = item.strip()
        if not name:
            continue
        if not valid_log_name(name):
            logger.warning("Ignoring invalid EVENT_LOG_DEFAULT_LOGS entry {name!r}", name=name[:80])
            continue
        logs.append(name)
    logs = merge_logs(logs)[:MAX_EXTRA_LOGS]
    return logs or list(DEFAULT_LOGS)


def parse_log_names(value: str | list[str] | None) -> list[str]:
    """Clean a list of extra logs (one per line when given as text).

    Blank lines, duplicates and the default logs (:func:`default_logs`) are dropped; an invalid name
    raises :class:`EventLogSettingsError`.
    """

    items = value.splitlines() if isinstance(value, str) else list(value or [])
    logs: list[str] = []
    seen = {name.casefold() for name in default_logs()}
    for item in items:
        name = str(item).strip()
        if not name:
            continue
        if not valid_log_name(name):
            raise EventLogSettingsError(f"“{name[:80]}” is not a Windows event log name.")
        if name.casefold() in seen:
            continue
        seen.add(name.casefold())
        logs.append(name)
    if len(logs) > MAX_EXTRA_LOGS:
        raise EventLogSettingsError(f"Choose at most {MAX_EXTRA_LOGS} extra logs.")
    return logs


def merge_logs(*lists: list[str] | tuple[str, ...]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for names in lists:
        for name in names:
            if name.casefold() not in seen:
                seen.add(name.casefold())
                merged.append(name)
    return merged


async def collection_for(company_id: int | None, asset_id: int | None) -> dict[str, Any]:
    """What a device collects: every log (the defaults, then its company's and
    its own extras) and the least severe level kept (its own choice, else its
    company's, else ``EVENT_LOG_MIN_LEVEL``)."""

    empty = {"extra_logs": [], "max_level": None}
    company = await repo.get_settings(company_id=company_id) if company_id is not None else empty
    asset = await repo.get_settings(asset_id=asset_id) if asset_id is not None else empty
    max_level = next(
        (level for level in (asset["max_level"], company["max_level"]) if level in LEVELS), default_max_level()
    )
    return {"logs": merge_logs(default_logs(), company["extra_logs"], asset["extra_logs"]), "max_level": max_level}


async def logs_for(company_id: int | None, asset_id: int | None) -> list[str]:
    """Every log a device collects: the defaults, then its company's and its own extras."""

    return (await collection_for(company_id, asset_id))["logs"]


async def enabled() -> bool:
    return await event_log_db.ensure_connected()


async def agent_config(agent: dict[str, Any]) -> dict[str, Any]:
    """What an agent should collect. ``enabled`` is false without the event log database."""

    is_enabled = await enabled()
    collection = (
        await collection_for(agent.get("company_id"), agent.get("asset_id"))
        if is_enabled else {"logs": [], "max_level": default_max_level()}
    )
    return {
        "enabled": is_enabled,
        "logs": collection["logs"],
        "max_level": collection["max_level"],
        "interval_seconds": COLLECT_INTERVAL_SECONDS,
        "max_batch": MAX_BATCH,
        "initial_lookback_hours": INITIAL_LOOKBACK_HOURS,
    }


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _as_naive(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    return _naive_utc(value) if isinstance(value, datetime) else None


def _signature(row: dict[str, Any]) -> tuple:
    """What makes two occurrences the same event on one device."""

    return (
        row.get("company_id"), row.get("asset_id"), row["log_name"], int(row["event_id"]), int(row["level"]),
        row.get("provider"), row.get("computer"), row.get("message_id"),
    )


async def _collapse(
    agent: dict[str, Any], rows: list[dict[str, Any]], *, window: timedelta, received_at: datetime
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fold repeats of an event into one entry.

    Returns the entries to insert and the stored entries to add repeats to.
    An occurrence joins the latest entry for the same event when it comes
    after that entry's last record and within ``window`` of its first
    occurrence; otherwise it starts a new entry. An occurrence a stored entry
    already counts (a resent batch) is left out.
    """

    # Agents send each log in record order; a log's records only repeat
    # within that log.
    rows = sorted(rows, key=lambda row: (row["log_name"], row["record_id"]))
    open_entries: dict[tuple, dict[str, Any]] = {}
    counted: dict[tuple, list[tuple[int, int, datetime, datetime]]] = {}
    if window > timedelta(0) and rows:
        stored = await repo.recent_repeats(
            agent_id=int(agent["id"]),
            message_ids={row["message_id"] for row in rows if row.get("message_id") is not None},
            since=min(row["time_created"] for row in rows) - window,
        )
        for entry in stored:
            first = _as_naive(entry.get("time_created"))
            if first is None:
                continue
            entry = {
                **entry, "time_created": first,
                "last_time_created": _as_naive(entry.get("last_time_created")) or first,
                "last_record_id": int(entry.get("last_record_id") or entry["record_id"]),
                "stored": True, "added": 0,
            }
            key = _signature(entry)
            open_entries[key] = entry  # oldest first, so the latest stays open
            counted.setdefault(key, []).append(
                (int(entry["record_id"]), entry["last_record_id"], first, entry["last_time_created"])
            )
    inserts: list[dict[str, Any]] = []
    for row in rows:
        key = _signature(row)
        record_id, created = int(row["record_id"]), row["time_created"]
        if any(
            first_id <= record_id <= last_id and first_time <= created <= last_time
            for first_id, last_id, first_time, last_time in counted.get(key, ())
        ):
            continue
        entry = open_entries.get(key)
        if (
            entry is not None and window > timedelta(0) and record_id > entry["last_record_id"]
            and entry["time_created"] <= created <= entry["time_created"] + window
        ):
            entry["repeat_count"] = int(entry.get("repeat_count") or 1) + 1
            entry["last_time_created"] = max(entry["last_time_created"], created)
            entry["last_record_id"] = record_id
            entry["added"] += 1
            continue
        entry = {**row, "repeat_count": 1, "last_time_created": created, "last_record_id": record_id, "added": 0}
        open_entries[key] = entry
        inserts.append(entry)
    for entry in inserts:
        if entry["repeat_count"] == 1:
            # A single occurrence needs no range.
            entry["last_time_created"] = entry["last_record_id"] = None
    updates = [
        {**entry, "received_at": received_at}
        for entry in open_entries.values() if entry.get("stored") and entry["added"]
    ]
    return inserts, updates


async def ingest(agent: dict[str, Any], entries: list[dict[str, Any]]) -> dict[str, int] | None:
    """Store a batch from an agent. Returns None when collection is off.

    Entries for logs the device is not set to collect are skipped, so an agent
    cannot fill the database with logs nobody chose; entries less severe than
    the device keeps (:func:`collection_for`) are dropped as ``below_level``.
    ``stored`` counts the occurrences kept, ``repeats`` those of them folded
    into an earlier entry for the same event.
    """

    if not await enabled():
        return None
    collection = await collection_for(agent.get("company_id"), agent.get("asset_id"))
    allowed = {name.casefold(): name for name in collection["logs"]}
    received_at = datetime.now(timezone.utc).replace(tzinfo=None)
    rows: list[dict[str, Any]] = []
    skipped = below_level = 0
    seen: set[tuple[str, int]] = set()
    for entry in entries:
        log_name = allowed.get(str(entry.get("log_name") or "").casefold())
        if log_name is None:
            skipped += 1
            continue
        if not keeps_level(entry.get("level", 4), collection["max_level"]):
            below_level += 1
            continue
        if (log_name, int(entry["record_id"])) in seen:
            continue  # the same record twice in one batch
        seen.add((log_name, int(entry["record_id"])))
        rows.append({
            "company_id": agent.get("company_id"),
            "asset_id": agent.get("asset_id"),
            "agent_id": int(agent["id"]),
            "log_name": log_name,
            "record_id": int(entry["record_id"]),
            "event_id": int(entry["event_id"]),
            "level": int(entry.get("level", 4)),
            "provider": (entry.get("provider") or "")[:255] or None,
            "computer": (entry.get("computer") or "")[:255] or None,
            "message": (entry.get("message") or "")[:MAX_MESSAGE_LENGTH] or None,
            "time_created": _naive_utc(entry["time_created"]),
            "received_at": received_at,
        })
    # Event log rules drop entries before they are stored, and queue scripts.
    rows, rule_stats = await rmm_event_log_rules.apply(agent, rows)
    stored = repeats = 0
    if rows:
        ids = await repo.message_ids((row["message"] for row in rows if row["message"]), seen_at=received_at)
        for row in rows:
            message = row.pop("message")
            row["message_id"] = ids.get(message) if message else None
        window = timedelta(minutes=get_settings().event_log_repeat_window_minutes)
        inserts, updates = await _collapse(agent, rows, window=window, received_at=received_at)
        inserted = await repo.insert_entries(inserts) if inserts else 0
        # A first record already stored (a batch resent long after) is skipped
        # with its repeats; how many repeats it had is then unknown.
        stored = sum(entry["repeat_count"] for entry in inserts) if inserted == len(inserts) else inserted
        if updates:
            await repo.add_repeats(updates)
            stored += sum(update["added"] for update in updates)
        repeats = stored - inserted
    return {
        "received": len(entries), "stored": stored, "repeats": repeats, "skipped": skipped,
        "below_level": below_level, **rule_stats,
    }


async def save_extra_logs(
    value: str | list[str] | None, *, company_id: int | None = None, asset_id: int | None = None,
    user_id: int | None = None,
) -> list[str]:
    logs = parse_log_names(value)
    await repo.save_extra_logs(logs, company_id=company_id, asset_id=asset_id, user_id=user_id)
    return logs


async def save_max_level(
    value: Any, *, company_id: int | None = None, asset_id: int | None = None, user_id: int | None = None,
) -> int | None:
    """Save a company's or device's least severe level; blank uses the one above it."""

    level = parse_max_level(value)
    if level is None and str(value if value is not None else "").strip():
        raise EventLogSettingsError("Choose a level from the list.")
    await repo.save_max_level(level, company_id=company_id, asset_id=asset_id, user_id=user_id)
    return level


async def prune() -> int:
    """Delete entries older than ``EVENT_LOG_RETENTION_DAYS`` (0 keeps them all)
    and message text no entry uses any more, then move the text of entries
    stored before de-duplication into the messages table."""

    if not await enabled():
        return 0
    days = get_settings().event_log_retention_days
    removed = 0
    if days > 0:
        before = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        removed = await repo.prune_entries(before)
        if removed:
            logger.info("Pruned {count} RMM event log entries older than {days} days", count=removed, days=days)
        messages = await repo.prune_messages(before - MESSAGE_PRUNE_GRACE)
        if messages:
            logger.info("Pruned {count} RMM event log messages no longer used", count=messages)
    moved = await repo.compact_legacy_messages()
    if moved:
        logger.info("Moved the messages of {count} RMM event log entries into shared storage", count=moved)
    return removed
