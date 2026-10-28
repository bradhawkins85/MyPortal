"""Storage for remote control settings and the sessions technicians start."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.database import db

PROVIDERS = ("rustdesk", "meshcentral")
MODES = ("remote", "remote_prompt", "file_transfer", "terminal", "registry")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def list_providers() -> dict[str, dict[str, Any]]:
    """Every provider's settings, with defaults for those never saved."""

    rows = await db.fetch_all(
        "SELECT p.provider, p.is_enabled, p.activation_script_id, p.server_url, p.username, "
        "p.secret_encrypted, p.id_field, p.entries_encrypted, p.updated_at, s.name AS activation_script_name, "
        "s.is_active AS activation_script_active, s.company_id AS activation_script_company_id "
        "FROM rmm_remote_control_providers p LEFT JOIN rmm_scripts s ON s.id = p.activation_script_id"
    )
    stored = {str(row["provider"]): dict(row) for row in rows}
    providers: dict[str, dict[str, Any]] = {}
    for provider in PROVIDERS:
        row = stored.get(provider) or {"provider": provider, "is_enabled": 0}
        row["is_enabled"] = bool(row.get("is_enabled"))
        providers[provider] = row
    return providers


async def save_provider(
    provider: str,
    *,
    is_enabled: bool,
    activation_script_id: int | None,
    server_url: str | None,
    username: str | None,
    secret_encrypted: str | None,
    id_field: str | None,
    entries_encrypted: str | None,
    user_id: int | None,
) -> None:
    """Insert or update a provider's settings. ``secret_encrypted`` None keeps the stored secret."""

    existing = await db.fetch_one(
        "SELECT secret_encrypted FROM rmm_remote_control_providers WHERE provider = %s", (provider,)
    )
    if existing is not None:
        secret = secret_encrypted if secret_encrypted is not None else existing.get("secret_encrypted")
        await db.execute(
            "UPDATE rmm_remote_control_providers SET is_enabled = %s, activation_script_id = %s, server_url = %s, "
            "username = %s, secret_encrypted = %s, id_field = %s, entries_encrypted = %s, updated_by_user_id = %s, "
            "updated_at = %s WHERE provider = %s",
            (1 if is_enabled else 0, activation_script_id, server_url, username, secret, id_field, entries_encrypted,
             user_id, _utcnow(), provider),
        )
        return
    await db.execute(
        "INSERT INTO rmm_remote_control_providers (provider, is_enabled, activation_script_id, server_url, "
        "username, secret_encrypted, id_field, entries_encrypted, updated_by_user_id, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (provider, 1 if is_enabled else 0, activation_script_id, server_url, username, secret_encrypted, id_field,
         entries_encrypted, user_id, _utcnow()),
    )


async def create_session(
    *,
    provider: str,
    company_id: int,
    asset_id: int,
    agent_id: int | None,
    requested_by_user_id: int | None,
    expires_at: datetime,
    mode: str = "remote",
    values_encrypted: str | None = None,
) -> int:
    return await db.execute_returning_lastrowid(
        "INSERT INTO rmm_remote_sessions (provider, mode, company_id, asset_id, agent_id, requested_by_user_id, "
        "status, values_encrypted, created_at, expires_at) VALUES (%s, %s, %s, %s, %s, %s, 'activating', %s, %s, %s)",
        (provider, mode, company_id, asset_id, agent_id, requested_by_user_id, values_encrypted, _utcnow(),
         expires_at),
    )


async def set_session_run(session_id: int, run_id: int) -> None:
    await db.execute("UPDATE rmm_remote_sessions SET run_id = %s WHERE id = %s", (run_id, session_id))


async def get_session(session_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT id, provider, mode, company_id, asset_id, agent_id, run_id, requested_by_user_id, status, "
        "values_encrypted, error_message, created_at, ready_at, expires_at FROM rmm_remote_sessions WHERE id = %s",
        (session_id,),
    )
    return dict(row) if row else None


async def get_session_for_run(run_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT id, status, values_encrypted FROM rmm_remote_sessions WHERE run_id = %s", (run_id,)
    )
    return dict(row) if row else None


async def store_values(session_id: int, values_encrypted: str) -> None:
    await db.execute(
        "UPDATE rmm_remote_sessions SET values_encrypted = %s WHERE id = %s AND status = 'activating'",
        (values_encrypted, session_id),
    )


async def finish_session(session_id: int, *, status: str, error_message: str | None = None) -> None:
    """Mark a session ready, failed or expired. Failed and expired sessions forget their values."""

    if status == "ready":
        await db.execute(
            "UPDATE rmm_remote_sessions SET status = 'ready', ready_at = %s, error_message = NULL WHERE id = %s",
            (_utcnow(), session_id),
        )
        return
    await db.execute(
        "UPDATE rmm_remote_sessions SET status = %s, error_message = %s, values_encrypted = NULL WHERE id = %s",
        (status, (error_message or "")[:1024] or None, session_id),
    )


async def expire_sessions(now: datetime | None = None) -> int:
    """Forget the values of sessions past their expiry."""

    return await db.execute_rowcount(
        "UPDATE rmm_remote_sessions SET status = 'expired', values_encrypted = NULL "
        "WHERE expires_at < %s AND status <> 'expired'",
        (now or _utcnow(),),
    )


def decode_json(value: Any) -> dict[str, str]:
    if not value:
        return {}
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return {str(key): str(item) for key, item in decoded.items()} if isinstance(decoded, dict) else {}


async def get_mesh_node(asset_id: int) -> str | None:
    """The MeshCentral node id last found for ``asset_id``."""

    row = await db.fetch_one("SELECT node_id FROM rmm_meshcentral_nodes WHERE asset_id = %s", (asset_id,))
    return str(row["node_id"]) if row and row.get("node_id") else None


async def save_mesh_node(asset_id: int, node_id: str) -> None:
    query = "INSERT INTO rmm_meshcentral_nodes (asset_id, node_id, updated_at) VALUES (%s, %s, %s)"
    query += (
        " ON CONFLICT(asset_id) DO UPDATE SET node_id = excluded.node_id, updated_at = excluded.updated_at"
        if db.is_sqlite()
        else " ON DUPLICATE KEY UPDATE node_id = VALUES(node_id), updated_at = VALUES(updated_at)"
    )
    await db.execute(query, (asset_id, node_id[:256], _utcnow()))
