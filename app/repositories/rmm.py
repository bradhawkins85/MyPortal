"""Storage for RMM scripts, enrolled RMM agents and script runs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable

from app.core.database import db

ACTIVE_RUN_STATUSES = ("queued", "dispatched", "running")
FINISHED_RUN_STATUSES = ("completed", "failed", "timed_out", "cancelled", "expired")

# Fixed SQL fragments naming what queued a run (manual, schedule or onboarding).
_RUN_SOURCE_COLUMNS = "r.run_source, r.schedule_id, r.onboarding_run_id, s.name AS schedule_name"
_RUN_SOURCE_JOINS = (
    "LEFT JOIN users u ON u.id = r.requested_by_user_id LEFT JOIN rmm_schedules s ON s.id = r.schedule_id"
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _decode_json(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _script_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    script = dict(row)
    script["parameters"] = _decode_json(script.pop("parameters_json", None), [])
    script["env_vars"] = _decode_json(script.pop("env_vars_json", None), [])
    script["files"] = _decode_json(script.pop("files_json", None), [])
    script["is_active"] = bool(script.get("is_active"))
    if "ai_summary_json" in script:
        script["ai_summary"] = _decode_json(script.pop("ai_summary_json"), None)
    return script


def _run_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    run = dict(row)
    if "inputs_json" in run:
        run["inputs"] = _decode_json(run.pop("inputs_json"), [])
    if "custom_values_json" in run:
        run["custom_values"] = _decode_json(run.pop("custom_values_json"), [])
    return run


# --------------------------------------------------------------------------- #
# Scripts
# --------------------------------------------------------------------------- #


async def list_scripts(*, company_id: int | None = None) -> list[dict[str, Any]]:
    """Active scripts; with ``company_id``, only those that company may run
    (Common scripts plus the scripts in its own folder)."""

    if company_id is None:
        rows = await db.fetch_all(
            "SELECT id, path, name, folder, language, description, content_sha256, source_sha, "
            "parameters_json, env_vars_json, default_timeout_seconds, company_id, is_active, synced_at, "
            "created_at, updated_at, files_json FROM rmm_scripts WHERE is_active = 1 ORDER BY folder, name"
        )
    else:
        rows = await db.fetch_all(
            "SELECT id, path, name, folder, language, description, content_sha256, source_sha, "
            "parameters_json, env_vars_json, default_timeout_seconds, company_id, is_active, synced_at, "
            "created_at, updated_at, files_json FROM rmm_scripts WHERE is_active = 1 "
            "AND (company_id IS NULL OR company_id = %s) ORDER BY folder, name",
            (company_id,),
        )
    return [_script_row(row) for row in rows or []]


def script_available_to(script: dict[str, Any] | None, company_id: int) -> bool:
    """True when ``script`` is active and may run on ``company_id``'s devices."""

    if not script or not script.get("is_active"):
        return False
    owner = script.get("company_id")
    return owner is None or int(owner) == int(company_id)


async def get_script(script_id: int, *, with_content: bool = False) -> dict[str, Any] | None:
    if with_content:
        row = await db.fetch_one(
            "SELECT id, path, name, folder, language, description, content_sha256, source_sha, "
            "parameters_json, env_vars_json, default_timeout_seconds, company_id, is_active, synced_at, "
            "created_at, updated_at, files_json, content, ai_summary_json, ai_summary_sha256, ai_summary_model, "
            "ai_summary_updated_at FROM rmm_scripts WHERE id = %s",
            (script_id,),
        )
    else:
        row = await db.fetch_one(
            "SELECT id, path, name, folder, language, description, content_sha256, source_sha, "
            "parameters_json, env_vars_json, default_timeout_seconds, company_id, is_active, synced_at, "
            "created_at, updated_at, files_json FROM rmm_scripts WHERE id = %s",
            (script_id,),
        )
    return _script_row(row)


async def script_source_index() -> dict[str, dict[str, Any]]:
    """Return ``{path: {id, source_sha, company_id, is_active}}`` for every stored script."""

    rows = await db.fetch_all("SELECT id, path, source_sha, company_id, is_active FROM rmm_scripts")
    return {str(row["path"]): dict(row) for row in rows or []}


async def upsert_script(
    *,
    path: str,
    name: str,
    folder: str,
    language: str,
    description: str,
    content: str,
    content_sha256: str,
    source_sha: str,
    parameters: list[dict[str, Any]],
    env_vars: list[dict[str, Any]],
    company_id: int | None = None,
) -> None:
    now = _utcnow()
    params = (
        name, folder, language, description, content, content_sha256, source_sha,
        json.dumps(parameters), json.dumps(env_vars), company_id, now, now,
    )
    existing = await db.fetch_one("SELECT id FROM rmm_scripts WHERE path = %s", (path,))
    if existing:
        await db.execute(
            "UPDATE rmm_scripts SET name = %s, folder = %s, language = %s, description = %s, content = %s, "
            "content_sha256 = %s, source_sha = %s, parameters_json = %s, env_vars_json = %s, company_id = %s, "
            "synced_at = %s, updated_at = %s, is_active = 1 WHERE id = %s",
            params + (existing["id"],),
        )
        return
    await db.execute(
        "INSERT INTO rmm_scripts (name, folder, language, description, content, content_sha256, source_sha, "
        "parameters_json, env_vars_json, company_id, synced_at, updated_at, path) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        params + (path,),
    )


async def touch_scripts(paths: Iterable[str]) -> None:
    """Mark unchanged scripts as seen in the latest sync."""

    now = _utcnow()
    for path in paths:
        await db.execute(
            "UPDATE rmm_scripts SET synced_at = %s, is_active = 1 WHERE path = %s", (now, path)
        )


async def deactivate_scripts(paths: Iterable[str]) -> None:
    """Hide scripts that are no longer in the repository; past runs keep their snapshot."""

    now = _utcnow()
    for path in paths:
        await db.execute(
            "UPDATE rmm_scripts SET is_active = 0, updated_at = %s WHERE path = %s", (now, path)
        )


async def set_script_timeout(script_id: int, timeout_seconds: int) -> None:
    await db.execute(
        "UPDATE rmm_scripts SET default_timeout_seconds = %s, updated_at = %s WHERE id = %s",
        (timeout_seconds, _utcnow(), script_id),
    )


async def set_script_files(script_id: int, files: list[dict[str, str]]) -> None:
    await db.execute(
        "UPDATE rmm_scripts SET files_json = %s, updated_at = %s WHERE id = %s",
        (json.dumps(files), _utcnow(), script_id),
    )


async def save_script_summary(
    script_id: int, summary: dict[str, Any], *, content_sha256: str, model: str | None
) -> None:
    """Store the AI summary of a script as it was at ``content_sha256``."""

    await db.execute(
        "UPDATE rmm_scripts SET ai_summary_json = %s, ai_summary_sha256 = %s, ai_summary_model = %s, "
        "ai_summary_updated_at = %s WHERE id = %s",
        (json.dumps(summary), content_sha256, model, _utcnow(), script_id),
    )


# --------------------------------------------------------------------------- #
# Agents
# --------------------------------------------------------------------------- #


async def get_agent_by_uid(agent_uid: str) -> dict[str, Any] | None:
    return await db.fetch_one("SELECT * FROM rmm_agents WHERE agent_uid = %s", (agent_uid,))


async def get_agent_by_token_hash(token_hash: str) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT * FROM rmm_agents WHERE auth_token_hash = %s AND status = 'active'", (token_hash,)
    )


async def get_agent(agent_id: int) -> dict[str, Any] | None:
    return await db.fetch_one("SELECT * FROM rmm_agents WHERE id = %s", (agent_id,))


async def create_agent(
    *,
    agent_uid: str,
    tray_device_id: int | None,
    company_id: int | None,
    asset_id: int | None,
    auth_token_hash: str,
    auth_token_prefix: str,
    details: dict[str, Any],
) -> int:
    now = _utcnow()
    return await db.execute_returning_lastrowid(
        "INSERT INTO rmm_agents (agent_uid, tray_device_id, company_id, asset_id, auth_token_hash, "
        "auth_token_prefix, hostname, os, os_version, arch, agent_version, shells, last_ip, last_seen_utc, "
        "status, created_at, updated_at, script_files_supported) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
        "'active', %s, %s, %s)",
        (
            agent_uid, tray_device_id, company_id, asset_id, auth_token_hash, auth_token_prefix,
            details.get("hostname"), details.get("os"), details.get("os_version"), details.get("arch"),
            details.get("agent_version"), details.get("shells"), details.get("last_ip"), now, now, now,
            bool(details.get("script_files_supported")),
        ),
    )


async def reenrol_agent(
    agent_id: int,
    *,
    tray_device_id: int | None,
    company_id: int | None,
    asset_id: int | None,
    auth_token_hash: str,
    auth_token_prefix: str,
    details: dict[str, Any],
) -> None:
    now = _utcnow()
    await db.execute(
        "UPDATE rmm_agents SET tray_device_id = %s, company_id = %s, asset_id = %s, auth_token_hash = %s, "
        "auth_token_prefix = %s, hostname = %s, os = %s, os_version = %s, arch = %s, agent_version = %s, "
        "shells = %s, last_ip = %s, last_seen_utc = %s, status = 'active', updated_at = %s, "
        "script_files_supported = %s WHERE id = %s",
        (
            tray_device_id, company_id, asset_id, auth_token_hash, auth_token_prefix,
            details.get("hostname"), details.get("os"), details.get("os_version"), details.get("arch"),
            details.get("agent_version"), details.get("shells"), details.get("last_ip"), now, now,
            bool(details.get("script_files_supported")), agent_id,
        ),
    )


async def record_agent_checkin(agent_id: int, details: dict[str, Any]) -> None:
    now = _utcnow()
    # Older agents and job polling omit thermal; preserve the last snapshot.
    # An explicit empty snapshot replaces old readings when sensors disappear.
    thermal = details.get("thermal")
    thermal_sql = ", thermal_json = %s" if thermal is not None else ""
    thermal_params = (json.dumps(thermal, default=lambda value: value.isoformat()),) if thermal is not None else ()
    await db.execute(
        "UPDATE rmm_agents SET last_seen_utc = %s, last_ip = COALESCE(%s, last_ip), "
        "agent_version = COALESCE(%s, agent_version), shells = COALESCE(%s, shells), "
        "hostname = COALESCE(%s, hostname), os_version = COALESCE(%s, os_version), updated_at = %s, "
        "script_files_supported = COALESCE(%s, script_files_supported) "
        + thermal_sql + " WHERE id = %s",
        (
            now, details.get("last_ip"), details.get("agent_version"), details.get("shells"),
            details.get("hostname"), details.get("os_version"), now, details.get("script_files_supported"),
        ) + thermal_params + (agent_id,),
    )


async def set_agent_status(agent_id: int, status: str) -> None:
    await db.execute(
        "UPDATE rmm_agents SET status = %s, updated_at = %s WHERE id = %s", (status, _utcnow(), agent_id)
    )


async def list_company_agents(company_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT g.id, g.agent_uid, g.asset_id, g.hostname, g.os, g.os_version, g.arch, g.agent_version, "
        "g.shells, g.last_seen_utc, g.status, a.name AS asset_name "
        "FROM rmm_agents g LEFT JOIN assets a ON a.id = g.asset_id "
        "WHERE g.company_id = %s AND g.status = 'active' ORDER BY COALESCE(a.name, g.hostname)",
        (company_id,),
    )
    return [dict(row) for row in rows or []]


async def list_company_asset_agents(company_id: int) -> dict[int, dict[str, Any]]:
    """Each of a company's assets with an active RMM agent, keyed by asset ID (latest check-in)."""

    rows = await db.fetch_all(
        "SELECT asset_id, last_seen_utc, agent_version FROM rmm_agents "
        "WHERE company_id = %s AND status = 'active' AND asset_id IS NOT NULL "
        "ORDER BY last_seen_utc DESC, id DESC",
        (company_id,),
    )
    agents: dict[int, dict[str, Any]] = {}
    for row in rows or []:
        agents.setdefault(int(row["asset_id"]), dict(row))
    return agents


async def get_asset_agent(asset_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT * FROM rmm_agents WHERE asset_id = %s AND status = 'active' "
        "ORDER BY last_seen_utc DESC LIMIT 1",
        (asset_id,),
    )

    if row:
        row = dict(row)
        row["thermal"] = _decode_json(row.pop("thermal_json", None), None)
    return row


# --------------------------------------------------------------------------- #
# Runs
# --------------------------------------------------------------------------- #


async def create_run(
    *,
    script: dict[str, Any],
    content: str,
    agent_id: int,
    company_id: int,
    asset_id: int | None,
    requested_by_user_id: int | None,
    inputs: list[dict[str, Any]],
    payload_encrypted: str,
    timeout_seconds: int,
    expires_at: datetime,
    run_source: str = "manual",
    schedule_id: int | None = None,
    onboarding_run_id: int | None = None,
) -> int:
    return await db.execute_returning_lastrowid(
        "INSERT INTO rmm_script_runs (script_id, script_name, script_path, language, script_content, "
        "content_sha256, agent_id, company_id, asset_id, requested_by_user_id, status, inputs_json, "
        "payload_encrypted, timeout_seconds, queued_at, expires_at, run_source, schedule_id, "
        "onboarding_run_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'queued', %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            script["id"], script["name"], script["path"], script["language"], content,
            script["content_sha256"], agent_id, company_id, asset_id, requested_by_user_id,
            json.dumps(inputs), payload_encrypted, timeout_seconds, _utcnow(), expires_at,
            run_source, schedule_id, onboarding_run_id,
        ),
    )


async def get_run(run_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT r.id, r.script_id, r.script_name, r.script_path, r.language, r.agent_id, "
        "r.company_id, r.asset_id, r.requested_by_user_id, r.status, r.timeout_seconds, "
        "r.exit_code, r.error_message, r.queued_at, r.dispatched_at, r.started_at, "
        "r.completed_at, r.expires_at, a.name AS asset_name, u.email AS requested_by_email, "
        f"{_RUN_SOURCE_COLUMNS}, "  # nosec B608 - fixed column list
        "r.inputs_json, r.stdout, r.stderr, r.custom_values_json, r.content_sha256 FROM "
        f"rmm_script_runs r LEFT JOIN assets a ON a.id = r.asset_id {_RUN_SOURCE_JOINS} "  # nosec B608 - fixed joins
        "WHERE r.id = %s",
        (run_id,),
    )
    return _run_row(row)


async def get_run_file_payload(run_id: int, agent_id: int) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT payload_encrypted, expires_at FROM rmm_script_runs WHERE id = %s AND agent_id = %s "
        "AND status IN ('dispatched', 'running') AND expires_at > %s",
        (run_id, agent_id, _utcnow()),
    )


async def list_runs(
    *, company_id: int, asset_id: int | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    capped = max(1, min(int(limit), 500))
    if asset_id is None:
        rows = await db.fetch_all(
            "SELECT r.id, r.script_id, r.script_name, r.script_path, r.language, r.agent_id, "
            "r.company_id, r.asset_id, r.requested_by_user_id, r.status, r.timeout_seconds, "
            "r.exit_code, r.error_message, r.queued_at, r.dispatched_at, r.started_at, "
            "r.completed_at, r.expires_at, a.name AS asset_name, u.email AS requested_by_email, "
            f"{_RUN_SOURCE_COLUMNS} FROM "  # nosec B608 - fixed column list
            f"rmm_script_runs r LEFT JOIN assets a ON a.id = r.asset_id {_RUN_SOURCE_JOINS} "  # nosec B608 - fixed joins
            "WHERE r.company_id = %s ORDER BY r.queued_at DESC, r.id DESC "
            "LIMIT %s",
            (company_id, capped),
        )
    else:
        rows = await db.fetch_all(
            "SELECT r.id, r.script_id, r.script_name, r.script_path, r.language, r.agent_id, "
            "r.company_id, r.asset_id, r.requested_by_user_id, r.status, r.timeout_seconds, "
            "r.exit_code, r.error_message, r.queued_at, r.dispatched_at, r.started_at, "
            "r.completed_at, r.expires_at, a.name AS asset_name, u.email AS requested_by_email, "
            f"{_RUN_SOURCE_COLUMNS} FROM "  # nosec B608 - fixed column list
            f"rmm_script_runs r LEFT JOIN assets a ON a.id = r.asset_id {_RUN_SOURCE_JOINS} "  # nosec B608 - fixed joins
            "WHERE r.company_id = %s AND r.asset_id = %s ORDER BY r.queued_at "
            "DESC, r.id DESC LIMIT %s",
            (company_id, asset_id, capped),
        )
    return [_run_row(row) for row in rows or []]


async def has_active_run(agent_id: int, script_id: int, *, run_source: str) -> bool:
    """True while ``agent_id`` has an unfinished run of ``script_id`` from ``run_source``."""

    row = await db.fetch_one(
        "SELECT id FROM rmm_script_runs WHERE agent_id = %s AND script_id = %s AND run_source = %s "
        "AND status IN ('queued', 'dispatched', 'running') LIMIT 1",
        (agent_id, script_id, run_source),
    )
    return bool(row)


async def next_jobs_for_agent(agent_id: int, *, limit: int = 5) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT id, script_name, script_path, language, script_content, content_sha256, payload_encrypted, "
        "timeout_seconds, run_source, expires_at FROM rmm_script_runs WHERE agent_id = %s AND status = 'queued' "
        "ORDER BY queued_at, id LIMIT %s",
        (agent_id, limit),
    )
    return [dict(row) for row in rows or []]


async def has_queued_jobs(agent_id: int) -> bool:
    row = await db.fetch_one(
        "SELECT id FROM rmm_script_runs WHERE agent_id = %s AND status = 'queued' LIMIT 1", (agent_id,)
    )
    return bool(row)


async def mark_dispatched(run_id: int, agent_id: int, *, expires_at: datetime) -> bool:
    """Claim a queued run for an agent. Returns ``False`` if another poll got it first."""

    claimed = await db.execute_rowcount(
        "UPDATE rmm_script_runs SET status = 'dispatched', dispatched_at = %s, expires_at = %s "
        "WHERE id = %s AND agent_id = %s AND status = 'queued'",
        (_utcnow(), expires_at, run_id, agent_id),
    )
    return claimed > 0


async def get_agent_run(run_id: int, agent_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT id, company_id, asset_id, status, timeout_seconds, onboarding_run_id, run_source FROM rmm_script_runs "
        "WHERE id = %s AND agent_id = %s",
        (run_id, agent_id),
    )
    return dict(row) if row else None


async def mark_started(run_id: int) -> None:
    await db.execute(
        "UPDATE rmm_script_runs SET status = 'running', started_at = %s "
        "WHERE id = %s AND status IN ('queued', 'dispatched')",
        (_utcnow(), run_id),
    )


async def update_run_output(run_id: int, agent_id: int, *, stdout: str, stderr: str) -> bool:
    """Replace a live snapshot only for the owning agent's executing run."""
    updated = await db.execute_rowcount(
        "UPDATE rmm_script_runs SET stdout = %s, stderr = %s "
        "WHERE id = %s AND agent_id = %s AND status IN ('dispatched', 'running')",
        (stdout, stderr, run_id, agent_id),
    )
    if updated:
        return True
    # MySQL reports zero changed rows for an identical retry.
    run = await get_agent_run(run_id, agent_id)
    return bool(run and run["status"] in ("dispatched", "running"))


async def finish_run(
    run_id: int,
    *,
    status: str,
    exit_code: int | None,
    stdout: str,
    stderr: str,
    custom_values: list[dict[str, Any]],
    error_message: str | None,
) -> None:
    """Store the result and clear the encrypted values the agent no longer needs."""

    await db.execute(
        "UPDATE rmm_script_runs SET status = %s, exit_code = %s, stdout = %s, stderr = %s, "
        "custom_values_json = %s, error_message = %s, completed_at = %s, payload_encrypted = NULL "
        "WHERE id = %s",
        (status, exit_code, stdout, stderr, json.dumps(custom_values), error_message, _utcnow(), run_id),
    )


async def cancel_run(run_id: int) -> bool:
    """Cancel a run the device has not collected yet."""

    cancelled = await db.execute_rowcount(
        "UPDATE rmm_script_runs SET status = 'cancelled', completed_at = %s, payload_encrypted = NULL "
        "WHERE id = %s AND status = 'queued'",
        (_utcnow(), run_id),
    )
    return cancelled > 0


async def expire_stale_runs(now: datetime | None = None) -> int:
    """Close runs whose agent never collected them, or never reported back."""

    now = now or _utcnow()
    return await db.execute_rowcount(
        "UPDATE rmm_script_runs SET "
        "error_message = CASE WHEN status = 'queued' "
        "THEN 'The device did not collect the script before it expired.' "
        "ELSE 'The device did not report a result in time.' END, "
        "status = CASE WHEN status = 'queued' THEN 'expired' ELSE 'timed_out' END, "
        "completed_at = %s, payload_encrypted = NULL "
        "WHERE status IN ('queued', 'dispatched', 'running') "
        "AND expires_at IS NOT NULL AND expires_at < %s",
        (now, now),
    )


# --------------------------------------------------------------------------- #
# Gitea accounts for MyPortal sign-in
# --------------------------------------------------------------------------- #


async def get_gitea_account(user_id: int) -> dict[str, Any] | None:
    return await db.fetch_one("SELECT * FROM rmm_gitea_accounts WHERE user_id = %s", (user_id,))


async def save_gitea_account(user_id: int, login: str, permission: str) -> None:
    now = _utcnow()
    if await get_gitea_account(user_id):
        await db.execute(
            "UPDATE rmm_gitea_accounts SET permission = %s, updated_at = %s WHERE user_id = %s",
            (permission, now, user_id),
        )
        return
    await db.execute(
        "INSERT INTO rmm_gitea_accounts (user_id, gitea_login, permission, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s)",
        (user_id, login, permission, now, now),
    )


async def list_gitea_accounts() -> list[dict[str, Any]]:
    return await db.fetch_all("SELECT * FROM rmm_gitea_accounts ORDER BY user_id")
