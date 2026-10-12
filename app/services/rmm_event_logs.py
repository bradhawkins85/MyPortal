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


class EventLogSettingsError(ValueError):
    pass


def level_label(level: Any) -> str:
    try:
        return LEVELS.get(int(level), "Information")
    except (TypeError, ValueError):
        return "Information"


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


async def logs_for(company_id: int | None, asset_id: int | None) -> list[str]:
    """Every log a device collects: the defaults, then its company's and its own extras."""

    company_logs = await repo.get_extra_logs(company_id=company_id) if company_id is not None else []
    asset_logs = await repo.get_extra_logs(asset_id=asset_id) if asset_id is not None else []
    return merge_logs(default_logs(), company_logs, asset_logs)


async def enabled() -> bool:
    return await event_log_db.ensure_connected()


async def agent_config(agent: dict[str, Any]) -> dict[str, Any]:
    """What an agent should collect. ``enabled`` is false without the event log database."""

    is_enabled = await enabled()
    return {
        "enabled": is_enabled,
        "logs": await logs_for(agent.get("company_id"), agent.get("asset_id")) if is_enabled else [],
        "interval_seconds": COLLECT_INTERVAL_SECONDS,
        "max_batch": MAX_BATCH,
        "initial_lookback_hours": INITIAL_LOOKBACK_HOURS,
    }


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


async def ingest(agent: dict[str, Any], entries: list[dict[str, Any]]) -> dict[str, int] | None:
    """Store a batch from an agent. Returns None when collection is off.

    Entries for logs the device is not set to collect are skipped, so an agent
    cannot fill the database with logs nobody chose.
    """

    if not await enabled():
        return None
    allowed = {name.casefold(): name for name in await logs_for(agent.get("company_id"), agent.get("asset_id"))}
    received_at = datetime.now(timezone.utc).replace(tzinfo=None)
    rows: list[dict[str, Any]] = []
    skipped = 0
    for entry in entries:
        log_name = allowed.get(str(entry.get("log_name") or "").casefold())
        if log_name is None:
            skipped += 1
            continue
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
    stored = await repo.insert_entries(rows) if rows else 0
    return {"received": len(entries), "stored": stored, "skipped": skipped, **rule_stats}


async def save_extra_logs(
    value: str | list[str] | None, *, company_id: int | None = None, asset_id: int | None = None,
    user_id: int | None = None,
) -> list[str]:
    logs = parse_log_names(value)
    await repo.save_extra_logs(logs, company_id=company_id, asset_id=asset_id, user_id=user_id)
    return logs


async def prune() -> int:
    """Delete entries older than ``EVENT_LOG_RETENTION_DAYS`` (0 keeps them all)."""

    days = get_settings().event_log_retention_days
    if days <= 0 or not await enabled():
        return 0
    before = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    removed = await repo.prune_entries(before)
    if removed:
        logger.info("Pruned {count} RMM event log entries older than {days} days", count=removed, days=days)
    return removed
