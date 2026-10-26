"""Storage for RMM findings (:mod:`app.services.rmm_findings`).

Analyses, findings and the software snapshots used to spot installs and
removals live in the main database. The read-only queries that gather signals
also reach the separate event log (:mod:`app.core.event_log_database`) and
stats (:mod:`app.core.stats_database`) databases.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from app.core.database import db
from app.core.event_log_database import ENTRIES_TABLE, MESSAGES_TABLE, event_log_db
from app.core.stats_database import SAMPLES_TABLE, stats_db

_FINDING_COLUMNS = (
    "id, fingerprint, finding_key, source, category, severity, title, summary, recommendation, evidence_json, "
    "confidence, company_ids_json, asset_ids_json, status, status_note, status_changed_at, "
    "status_changed_by_user_id, is_active, occurrences, first_seen_at, last_seen_at, last_run_id"
)
_RUN_COLUMNS = (
    "id, status, trigger_source, requested_by_user_id, started_at, finished_at, ai_status, ai_model, ai_error, "
    "error, finding_count, stats_json"
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _decode(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return default
    return decoded if isinstance(decoded, type(default)) else default


def _placeholders(count: int) -> str:
    return ", ".join(["%s"] * count)


def _finding_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    finding = dict(row)
    finding["evidence"] = _decode(finding.pop("evidence_json", None), [])
    finding["company_ids"] = [int(value) for value in _decode(finding.pop("company_ids_json", None), [])
                              if isinstance(value, int)]
    finding["asset_ids"] = [int(value) for value in _decode(finding.pop("asset_ids_json", None), [])
                            if isinstance(value, int)]
    finding["is_active"] = bool(finding.get("is_active"))
    return finding


def _run_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    run = dict(row)
    run["stats"] = _decode(run.pop("stats_json", None), {})
    return run


# --------------------------------------------------------------------------- #
# Analyses
# --------------------------------------------------------------------------- #


async def create_run(*, trigger_source: str, requested_by_user_id: int | None) -> int:
    return int(await db.execute_returning_lastrowid(
        "INSERT INTO rmm_finding_runs (status, trigger_source, requested_by_user_id, started_at, ai_status) "
        "VALUES ('running', %s, %s, %s, 'pending')",
        (trigger_source, requested_by_user_id, _utcnow()),
    ))


async def finish_run(
    run_id: int,
    *,
    status: str,
    ai_status: str,
    ai_model: str | None,
    ai_error: str | None,
    error: str | None,
    finding_count: int,
    stats: dict[str, Any],
) -> None:
    await db.execute(
        "UPDATE rmm_finding_runs SET status = %s, finished_at = %s, ai_status = %s, ai_model = %s, ai_error = %s, "
        "error = %s, finding_count = %s, stats_json = %s WHERE id = %s",
        (status, _utcnow(), ai_status, (ai_model or None) and ai_model[:255], (ai_error or None) and ai_error[:512],
         (error or None) and error[:512], int(finding_count), json.dumps(stats, default=str), int(run_id)),
    )


async def get_run(run_id: int) -> dict[str, Any] | None:
    return _run_row(await db.fetch_one(
        "SELECT " + _RUN_COLUMNS + " FROM rmm_finding_runs WHERE id = %s",  # nosec B608 - fixed column list
        (int(run_id),),
    ))


async def get_latest_run() -> dict[str, Any] | None:
    return _run_row(await db.fetch_one(
        "SELECT " + _RUN_COLUMNS + " FROM rmm_finding_runs ORDER BY started_at DESC, id DESC LIMIT 1",  # nosec B608
    ))


async def get_running_run() -> dict[str, Any] | None:
    return _run_row(await db.fetch_one(
        "SELECT " + _RUN_COLUMNS + " FROM rmm_finding_runs WHERE status = 'running' "  # nosec B608
        "ORDER BY started_at DESC, id DESC LIMIT 1",
    ))


async def fail_stale_runs(started_before: datetime) -> int:
    """Mark analyses still running since before ``started_before`` as failed
    (the instance running them stopped)."""

    return await db.execute_rowcount(
        "UPDATE rmm_finding_runs SET status = 'failed', finished_at = %s, "
        "error = 'The analysis stopped before it finished.' WHERE status = 'running' AND started_at < %s",
        (_utcnow(), started_before),
    )


async def list_runs(limit: int = 10) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT " + _RUN_COLUMNS + " FROM rmm_finding_runs ORDER BY started_at DESC, id DESC LIMIT %s",  # nosec B608
        (int(limit),),
    )
    return [_run_row(row) for row in rows]


# --------------------------------------------------------------------------- #
# Findings
# --------------------------------------------------------------------------- #


async def upsert_finding(finding: dict[str, Any], *, run_id: int) -> int:
    """Store a finding from this analysis; the same fingerprint updates the
    existing row and keeps the technician's review status."""

    now = _utcnow()
    values = (
        finding["source"], finding["category"], finding["severity"], finding["title"][:255],
        finding.get("summary") or "", finding.get("recommendation") or "",
        json.dumps(finding.get("evidence") or []), finding.get("confidence"),
        json.dumps(sorted(set(finding.get("company_ids") or []))),
        json.dumps(sorted(set(finding.get("asset_ids") or []))),
    )
    existing = await db.fetch_one(
        "SELECT id FROM rmm_findings WHERE fingerprint = %s", (finding["fingerprint"],)
    )
    if existing:
        await db.execute(
            "UPDATE rmm_findings SET source = %s, category = %s, severity = %s, title = %s, summary = %s, "
            "recommendation = %s, evidence_json = %s, confidence = %s, company_ids_json = %s, asset_ids_json = %s, "
            "is_active = 1, occurrences = occurrences + 1, last_seen_at = %s, last_run_id = %s WHERE id = %s",
            (*values, now, int(run_id), int(existing["id"])),
        )
        return int(existing["id"])
    return int(await db.execute_returning_lastrowid(
        "INSERT INTO rmm_findings (fingerprint, finding_key, source, category, severity, title, summary, "
        "recommendation, evidence_json, confidence, company_ids_json, asset_ids_json, status, is_active, "
        "occurrences, first_seen_at, last_seen_at, last_run_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'open', 1, 1, %s, %s, %s)",
        (finding["fingerprint"], finding["key"][:160], *values, now, now, int(run_id)),
    ))


async def deactivate_unseen(run_id: int, *, sources: Sequence[str], keep_key_prefixes: Sequence[str] = ()) -> int:
    """Findings from ``sources`` that the analysis ``run_id`` did not find
    again are no longer active, except keys starting with ``keep_key_prefixes``
    (their data could not be read this time)."""

    if not sources:
        return 0
    clauses = ["is_active = 1", "(last_run_id IS NULL OR last_run_id <> %s)",
               "source IN (" + _placeholders(len(sources)) + ")"]
    params: list[Any] = [int(run_id), *sources]
    for prefix in keep_key_prefixes:
        clauses.append("finding_key NOT LIKE %s")
        params.append(prefix + "%")
    # The clauses are fixed strings chosen above; every value is a bound parameter.
    return await db.execute_rowcount(
        "UPDATE rmm_findings SET is_active = 0 WHERE " + " AND ".join(clauses),  # nosec B608
        tuple(params),
    )


async def names_for(*, asset_ids: Sequence[int], company_ids: Sequence[int]) -> dict[str, dict[int, Any]]:
    """Asset (name, company) and company names for presenting findings."""

    assets: dict[int, Any] = {}
    companies: dict[int, Any] = {}
    if asset_ids:
        rows = await db.fetch_all(
            "SELECT id, name, company_id FROM assets WHERE id IN (" + _placeholders(len(asset_ids)) + ")",  # nosec B608
            tuple(int(asset_id) for asset_id in asset_ids),
        )
        assets = {int(row["id"]): {"name": row.get("name"), "company_id": row.get("company_id")} for row in rows}
    wanted = {int(company_id) for company_id in company_ids} | {
        int(asset["company_id"]) for asset in assets.values() if asset.get("company_id")
    }
    if wanted:
        rows = await db.fetch_all(
            "SELECT id, name FROM companies WHERE id IN (" + _placeholders(len(wanted)) + ")",  # nosec B608
            tuple(sorted(wanted)),
        )
        companies = {int(row["id"]): row.get("name") for row in rows}
    return {"assets": assets, "companies": companies}


async def list_findings(*, include_inactive: bool = False, limit: int = 500) -> list[dict[str, Any]]:
    where = "" if include_inactive else " WHERE is_active = 1"
    rows = await db.fetch_all(
        "SELECT " + _FINDING_COLUMNS + " FROM rmm_findings" + where  # nosec B608 - fixed strings only
        + " ORDER BY is_active DESC, last_seen_at DESC, id DESC LIMIT %s",
        (int(limit),),
    )
    return [_finding_row(row) for row in rows]


async def list_active_keys(source: str) -> list[dict[str, Any]]:
    return await db.fetch_all(
        "SELECT finding_key, title, category FROM rmm_findings WHERE is_active = 1 AND source = %s "
        "ORDER BY last_seen_at DESC LIMIT 50",
        (source,),
    )


async def get_finding(finding_id: int) -> dict[str, Any] | None:
    return _finding_row(await db.fetch_one(
        "SELECT " + _FINDING_COLUMNS + " FROM rmm_findings WHERE id = %s",  # nosec B608 - fixed column list
        (int(finding_id),),
    ))


async def set_status(finding_id: int, *, status: str, note: str | None, user_id: int | None) -> None:
    await db.execute(
        "UPDATE rmm_findings SET status = %s, status_note = %s, status_changed_at = %s, "
        "status_changed_by_user_id = %s WHERE id = %s",
        (status, (note or None) and note[:512], _utcnow(), user_id, int(finding_id)),
    )


async def prune(*, findings_before: datetime, runs_before: datetime, changes_before: datetime) -> None:
    await db.execute(
        "DELETE FROM rmm_findings WHERE is_active = 0 AND last_seen_at < %s", (findings_before,)
    )
    await db.execute("DELETE FROM rmm_finding_runs WHERE started_at < %s AND status <> 'running'", (runs_before,))
    await db.execute("DELETE FROM rmm_finding_software_changes WHERE detected_at < %s", (changes_before,))


# --------------------------------------------------------------------------- #
# Devices, software and checks (main database)
# --------------------------------------------------------------------------- #


async def list_devices() -> list[dict[str, Any]]:
    """Every active RMM agent with its company, asset and latest hardware snapshot."""

    return await db.fetch_all(
        "SELECT a.id AS agent_id, a.company_id, a.asset_id, a.hostname, a.os, a.last_seen_utc, "
        "c.name AS company_name, s.name AS asset_name, i.manufacturer, i.model, i.os_name, i.os_version, "
        "i.cpu_model, i.cpu_cores, i.ram_bytes, i.disk_total_bytes, i.system_disk_free_bytes, i.last_boot_at, "
        "i.collected_at "
        "FROM rmm_agents a "
        "LEFT JOIN rmm_device_inventory i ON i.agent_id = a.id "
        "LEFT JOIN companies c ON c.id = a.company_id "
        "LEFT JOIN assets s ON s.id = a.asset_id "
        "WHERE a.status = 'active'"
    )


async def list_software(agent_ids: Sequence[int]) -> dict[int, list[dict[str, Any]]]:
    if not agent_ids:
        return {}
    rows = await db.fetch_all(
        "SELECT agent_id, name, version, install_date FROM rmm_device_software "  # nosec B608 - placeholders only
        "WHERE agent_id IN (" + _placeholders(len(agent_ids)) + ")",
        tuple(int(agent_id) for agent_id in agent_ids),
    )
    software: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        software.setdefault(int(row["agent_id"]), []).append(row)
    return software


async def list_snapshots(agent_ids: Sequence[int]) -> dict[int, dict[str, str]]:
    """The software each agent had at the previous analysis: ``{name: version}``."""

    if not agent_ids:
        return {}
    rows = await db.fetch_all(
        "SELECT agent_id, items_json FROM rmm_finding_software_snapshots "  # nosec B608 - placeholders only
        "WHERE agent_id IN (" + _placeholders(len(agent_ids)) + ")",
        tuple(int(agent_id) for agent_id in agent_ids),
    )
    return {int(row["agent_id"]): _decode(row.get("items_json"), {}) for row in rows}


async def save_snapshot(agent_id: int, items: dict[str, str]) -> None:
    payload = json.dumps(items, sort_keys=True)
    now = _utcnow()
    existing = await db.fetch_one(
        "SELECT agent_id FROM rmm_finding_software_snapshots WHERE agent_id = %s", (int(agent_id),)
    )
    if existing:
        await db.execute(
            "UPDATE rmm_finding_software_snapshots SET items_json = %s, captured_at = %s WHERE agent_id = %s",
            (payload, now, int(agent_id)),
        )
        return
    await db.execute(
        "INSERT INTO rmm_finding_software_snapshots (agent_id, items_json, captured_at) VALUES (%s, %s, %s)",
        (int(agent_id), payload, now),
    )


async def insert_software_changes(changes: Iterable[dict[str, Any]]) -> None:
    for change in changes:
        await db.execute(
            "INSERT INTO rmm_finding_software_changes (agent_id, company_id, asset_id, name, change_type, "
            "old_version, new_version, detected_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (int(change["agent_id"]), change.get("company_id"), change.get("asset_id"), change["name"][:255],
             change["change_type"], (change.get("old_version") or None) and change["old_version"][:128],
             (change.get("new_version") or None) and change["new_version"][:128], change["detected_at"]),
        )


async def list_software_changes(since: datetime) -> list[dict[str, Any]]:
    return await db.fetch_all(
        "SELECT agent_id, company_id, asset_id, name, change_type, old_version, new_version, detected_at "
        "FROM rmm_finding_software_changes WHERE detected_at >= %s ORDER BY detected_at",
        (since,),
    )


async def failing_checks() -> list[dict[str, Any]]:
    """Checks with at least one failing device, with how many devices they cover."""

    return await db.fetch_all(
        "SELECT c.id, c.name, c.check_type, c.target, "
        "SUM(CASE WHEN s.status = 'failing' THEN 1 ELSE 0 END) AS failing, COUNT(*) AS devices "
        "FROM rmm_check_states s JOIN rmm_checks c ON c.id = s.check_id "
        "WHERE c.is_enabled = 1 GROUP BY c.id, c.name, c.check_type, c.target "
        "HAVING SUM(CASE WHEN s.status = 'failing' THEN 1 ELSE 0 END) > 0"
    )


async def list_failing_agents(check_id: int, limit: int = 25) -> list[int]:
    rows = await db.fetch_all(
        "SELECT agent_id FROM rmm_check_states WHERE check_id = %s AND status = 'failing' LIMIT %s",
        (int(check_id), int(limit)),
    )
    return [int(row["agent_id"]) for row in rows]


# --------------------------------------------------------------------------- #
# Event logs (event log database)
# --------------------------------------------------------------------------- #


async def event_counts(*, since: datetime, until: datetime) -> list[dict[str, Any]]:
    """Occurrences per log, source and event ID between ``since`` and ``until``
    (an entry can stand for several repeats, :mod:`app.core.event_log_database`)."""

    return await event_log_db.fetch_all(
        "SELECT log_name, COALESCE(provider, '') AS provider, event_id, MIN(level) AS level, "
        "SUM(repeat_count) AS total, "
        "COUNT(DISTINCT agent_id) AS devices, COUNT(DISTINCT company_id) AS companies "
        "FROM " + ENTRIES_TABLE + " WHERE time_created >= %s AND time_created < %s "  # nosec B608 - fixed table
        "GROUP BY log_name, COALESCE(provider, ''), event_id",
        (since, until),
    )


async def event_agent_counts(
    *, log_name: str, provider: str, event_id: int, baseline_start: datetime, current_start: datetime,
    until: datetime,
) -> list[dict[str, Any]]:
    """For one event: each device's occurrences in the current and baseline windows."""

    return await event_log_db.fetch_all(
        "SELECT agent_id, company_id, asset_id, "
        "SUM(CASE WHEN time_created >= %s THEN repeat_count ELSE 0 END) AS current_count, "
        "SUM(CASE WHEN time_created < %s THEN repeat_count ELSE 0 END) AS baseline_count, "
        "MIN(CASE WHEN time_created >= %s THEN time_created END) AS first_current "
        "FROM " + ENTRIES_TABLE + " WHERE time_created >= %s AND time_created < %s "  # nosec B608 - fixed table
        "AND log_name = %s AND COALESCE(provider, '') = %s AND event_id = %s "
        "GROUP BY agent_id, company_id, asset_id",
        (current_start, current_start, current_start, baseline_start, until, log_name, provider, int(event_id)),
    )


async def event_sample(*, log_name: str, provider: str, event_id: int, since: datetime) -> str:
    row = await event_log_db.fetch_all(
        "SELECT COALESCE(m.message, e.message) AS message FROM " + ENTRIES_TABLE + " e "  # nosec B608
        "LEFT JOIN " + MESSAGES_TABLE + " m ON m.id = e.message_id WHERE e.time_created >= %s "
        "AND e.log_name = %s AND COALESCE(e.provider, '') = %s AND e.event_id = %s "
        "ORDER BY e.time_created DESC LIMIT 1",
        (since, log_name, provider, int(event_id)),
    )
    return str((row[0].get("message") if row else "") or "")


async def security_events(*, event_ids: Sequence[int], since: datetime, limit: int) -> list[dict[str, Any]]:
    """Entries with the given event IDs since ``since``, newest first."""

    if not event_ids:
        return []
    return await event_log_db.fetch_all(
        "SELECT e.agent_id, e.company_id, e.asset_id, e.log_name, COALESCE(e.provider, '') AS provider, "
        "e.event_id, e.computer, COALESCE(m.message, e.message) AS message, e.repeat_count, e.time_created "
        "FROM " + ENTRIES_TABLE + " e LEFT JOIN " + MESSAGES_TABLE + " m "  # nosec B608 - fixed tables, placeholders
        "ON m.id = e.message_id WHERE e.time_created >= %s AND e.event_id IN (" + _placeholders(len(event_ids)) + ") "
        "ORDER BY e.time_created DESC LIMIT %s",
        (since, *(int(event_id) for event_id in event_ids), int(limit)),
    )


# --------------------------------------------------------------------------- #
# Device stats (stats database)
# --------------------------------------------------------------------------- #


async def stat_averages(*, metrics: Sequence[str], since: datetime) -> list[dict[str, Any]]:
    """Each device's average and peak for whole-device metrics (CPU, memory)."""

    if not metrics:
        return []
    return await stats_db.fetch_all(
        "SELECT agent_id, metric, AVG(value) AS average, MAX(value) AS peak, COUNT(*) AS samples "
        "FROM " + SAMPLES_TABLE + " WHERE sampled_at >= %s AND instance = '' "  # nosec B608 - fixed table
        "AND metric IN (" + _placeholders(len(metrics)) + ") GROUP BY agent_id, metric",
        (since, *metrics),
    )
