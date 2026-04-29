"""Repository for the MyPortal Tray App tables.

All access to ``tray_install_tokens``, ``tray_devices``, ``tray_menu_configs``
and ``tray_command_log`` flows through this module so callers never embed raw
SQL.  Functions accept and return plain ``dict`` objects, matching the style
used by other repositories such as :mod:`app.repositories.chat`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.database import db


# ---------------------------------------------------------------------------
# Install tokens
# ---------------------------------------------------------------------------


async def create_install_token(
    *,
    label: str,
    company_id: int | None,
    token_hash: str,
    token_prefix: str,
    created_by_user_id: int | None,
    expires_at: datetime | None = None,
) -> dict[str, Any]:
    if db.is_sqlite():
        await db.execute(
            """INSERT INTO tray_install_tokens
               (company_id, label, token_hash, token_prefix,
                created_by_user_id, expires_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (company_id, label, token_hash, token_prefix, created_by_user_id, expires_at),
        )
    else:
        await db.execute(
            """INSERT INTO tray_install_tokens
               (company_id, label, token_hash, token_prefix,
                created_by_user_id, expires_at)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (company_id, label, token_hash, token_prefix, created_by_user_id, expires_at),
        )
    row = await db.fetch_one(
        "SELECT * FROM tray_install_tokens WHERE token_hash = %s"
        if not db.is_sqlite()
        else "SELECT * FROM tray_install_tokens WHERE token_hash = ?",
        (token_hash,),
    )
    return dict(row) if row else {}


async def list_install_tokens(*, company_id: int | None = None) -> list[dict[str, Any]]:
    if company_id is None:
        rows = await db.fetch_all(
            "SELECT * FROM tray_install_tokens ORDER BY created_at DESC"
        )
    else:
        placeholder = "?" if db.is_sqlite() else "%s"
        rows = await db.fetch_all(
            f"SELECT * FROM tray_install_tokens WHERE company_id = {placeholder} "
            "ORDER BY created_at DESC",
            (company_id,),
        )
    return [dict(r) for r in rows]


async def get_install_token_by_hash(token_hash: str) -> dict[str, Any] | None:
    placeholder = "?" if db.is_sqlite() else "%s"
    row = await db.fetch_one(
        f"SELECT * FROM tray_install_tokens WHERE token_hash = {placeholder}",
        (token_hash,),
    )
    return dict(row) if row else None


async def mark_install_token_used(token_id: int) -> None:
    placeholder = "?" if db.is_sqlite() else "%s"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.execute(
        f"UPDATE tray_install_tokens SET last_used_at = {placeholder}, "
        f"use_count = use_count + 1 WHERE id = {placeholder}",
        (now, token_id),
    )


async def revoke_install_token(token_id: int) -> None:
    placeholder = "?" if db.is_sqlite() else "%s"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.execute(
        f"UPDATE tray_install_tokens SET revoked_at = {placeholder} "
        f"WHERE id = {placeholder}",
        (now, token_id),
    )


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------


async def create_device(
    *,
    company_id: int | None,
    asset_id: int | None,
    device_uid: str,
    enrolment_token_id: int | None,
    auth_token_hash: str,
    auth_token_prefix: str,
    os: str,
    os_version: str | None,
    hostname: str | None,
    serial_number: str | None,
    agent_version: str | None,
    console_user: str | None,
    status: str = "active",
) -> dict[str, Any]:
    placeholder = "?" if db.is_sqlite() else "%s"
    columns = (
        "company_id, asset_id, device_uid, enrolment_token_id, auth_token_hash, "
        "auth_token_prefix, os, os_version, hostname, serial_number, "
        "agent_version, console_user, status"
    )
    values = ", ".join([placeholder] * 13)
    await db.execute(
        f"INSERT INTO tray_devices ({columns}) VALUES ({values})",
        (
            company_id,
            asset_id,
            device_uid,
            enrolment_token_id,
            auth_token_hash,
            auth_token_prefix,
            os,
            os_version,
            hostname,
            serial_number,
            agent_version,
            console_user,
            status,
        ),
    )
    return await get_device_by_uid(device_uid) or {}


async def update_device_auth(
    device_id: int,
    *,
    auth_token_hash: str,
    auth_token_prefix: str,
) -> None:
    placeholder = "?" if db.is_sqlite() else "%s"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.execute(
        f"UPDATE tray_devices SET auth_token_hash = {placeholder}, "
        f"auth_token_prefix = {placeholder}, status = 'active', "
        f"updated_at = {placeholder} WHERE id = {placeholder}",
        (auth_token_hash, auth_token_prefix, now, device_id),
    )


async def get_device_by_uid(device_uid: str) -> dict[str, Any] | None:
    placeholder = "?" if db.is_sqlite() else "%s"
    row = await db.fetch_one(
        f"SELECT * FROM tray_devices WHERE device_uid = {placeholder}",
        (device_uid,),
    )
    return dict(row) if row else None


async def get_device_by_id(device_id: int) -> dict[str, Any] | None:
    placeholder = "?" if db.is_sqlite() else "%s"
    row = await db.fetch_one(
        f"SELECT * FROM tray_devices WHERE id = {placeholder}",
        (device_id,),
    )
    return dict(row) if row else None


async def get_device_by_auth_hash(auth_token_hash: str) -> dict[str, Any] | None:
    placeholder = "?" if db.is_sqlite() else "%s"
    row = await db.fetch_one(
        f"SELECT * FROM tray_devices WHERE auth_token_hash = {placeholder} "
        "AND status = 'active'",
        (auth_token_hash,),
    )
    return dict(row) if row else None


async def list_devices(
    *,
    company_id: int | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    placeholder = "?" if db.is_sqlite() else "%s"
    clauses: list[str] = ["1=1"]
    params: list[Any] = []
    if company_id is not None:
        clauses.append(f"company_id = {placeholder}")
        params.append(company_id)
    if status:
        clauses.append(f"status = {placeholder}")
        params.append(status)
    rows = await db.fetch_all(
        f"SELECT * FROM tray_devices WHERE {' AND '.join(clauses)} "
        "ORDER BY last_seen_utc DESC, created_at DESC",
        tuple(params),
    )
    return [dict(r) for r in rows]


async def update_device_heartbeat(
    device_id: int,
    *,
    console_user: str | None,
    last_ip: str | None,
    agent_version: str | None,
) -> None:
    placeholder = "?" if db.is_sqlite() else "%s"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.execute(
        f"UPDATE tray_devices SET last_seen_utc = {placeholder}, "
        f"console_user = COALESCE({placeholder}, console_user), "
        f"last_ip = COALESCE({placeholder}, last_ip), "
        f"agent_version = COALESCE({placeholder}, agent_version), "
        f"updated_at = {placeholder} WHERE id = {placeholder}",
        (now, console_user, last_ip, agent_version, now, device_id),
    )


async def link_device_to_asset(device_id: int, asset_id: int | None) -> None:
    placeholder = "?" if db.is_sqlite() else "%s"
    await db.execute(
        f"UPDATE tray_devices SET asset_id = {placeholder} WHERE id = {placeholder}",
        (asset_id, device_id),
    )


async def revoke_device(device_id: int) -> None:
    placeholder = "?" if db.is_sqlite() else "%s"
    await db.execute(
        f"UPDATE tray_devices SET status = 'revoked' WHERE id = {placeholder}",
        (device_id,),
    )


# ---------------------------------------------------------------------------
# Menu configs
# ---------------------------------------------------------------------------


async def create_menu_config(
    *,
    name: str,
    scope: str,
    scope_ref_id: int | None,
    payload_json: str,
    display_text: str | None,
    env_allowlist: str | None,
    branding_icon_url: str | None,
    enabled: bool,
    created_by_user_id: int | None,
) -> dict[str, Any]:
    placeholder = "?" if db.is_sqlite() else "%s"
    columns = (
        "name, scope, scope_ref_id, payload_json, display_text, env_allowlist, "
        "branding_icon_url, enabled, created_by_user_id, updated_by_user_id"
    )
    values = ", ".join([placeholder] * 10)
    await db.execute(
        f"INSERT INTO tray_menu_configs ({columns}) VALUES ({values})",
        (
            name,
            scope,
            scope_ref_id,
            payload_json,
            display_text,
            env_allowlist,
            branding_icon_url,
            1 if enabled else 0,
            created_by_user_id,
            created_by_user_id,
        ),
    )
    rows = await db.fetch_all(
        "SELECT * FROM tray_menu_configs ORDER BY id DESC LIMIT 1"
    )
    return dict(rows[0]) if rows else {}


async def list_menu_configs() -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT * FROM tray_menu_configs ORDER BY scope, name"
    )
    return [dict(r) for r in rows]


async def get_menu_config(config_id: int) -> dict[str, Any] | None:
    placeholder = "?" if db.is_sqlite() else "%s"
    row = await db.fetch_one(
        f"SELECT * FROM tray_menu_configs WHERE id = {placeholder}",
        (config_id,),
    )
    return dict(row) if row else None


async def update_menu_config(
    config_id: int,
    *,
    name: str | None = None,
    payload_json: str | None = None,
    display_text: str | None = None,
    env_allowlist: str | None = None,
    branding_icon_url: str | None = None,
    enabled: bool | None = None,
    updated_by_user_id: int | None = None,
) -> None:
    placeholder = "?" if db.is_sqlite() else "%s"
    sets: list[str] = []
    params: list[Any] = []
    if name is not None:
        sets.append(f"name = {placeholder}")
        params.append(name)
    if payload_json is not None:
        sets.append(f"payload_json = {placeholder}")
        params.append(payload_json)
        sets.append("version = version + 1")
    if display_text is not None:
        sets.append(f"display_text = {placeholder}")
        params.append(display_text)
    if env_allowlist is not None:
        sets.append(f"env_allowlist = {placeholder}")
        params.append(env_allowlist)
    if branding_icon_url is not None:
        sets.append(f"branding_icon_url = {placeholder}")
        params.append(branding_icon_url)
    if enabled is not None:
        sets.append(f"enabled = {placeholder}")
        params.append(1 if enabled else 0)
    if updated_by_user_id is not None:
        sets.append(f"updated_by_user_id = {placeholder}")
        params.append(updated_by_user_id)
    if not sets:
        return
    sets.append(f"updated_at = {placeholder}")
    params.append(datetime.now(timezone.utc).replace(tzinfo=None))
    params.append(config_id)
    await db.execute(
        f"UPDATE tray_menu_configs SET {', '.join(sets)} WHERE id = {placeholder}",
        tuple(params),
    )


async def delete_menu_config(config_id: int) -> None:
    placeholder = "?" if db.is_sqlite() else "%s"
    await db.execute(
        f"DELETE FROM tray_menu_configs WHERE id = {placeholder}",
        (config_id,),
    )


# ---------------------------------------------------------------------------
# Command log
# ---------------------------------------------------------------------------


async def log_command(
    *,
    device_id: int,
    command: str,
    payload_json: str | None,
    initiated_by_user_id: int | None,
    status: str = "queued",
) -> int:
    placeholder = "?" if db.is_sqlite() else "%s"
    last_id = await db.execute_returning_lastrowid(
        f"INSERT INTO tray_command_log (device_id, command, payload_json, "
        f"initiated_by_user_id, status) VALUES ({placeholder}, {placeholder}, "
        f"{placeholder}, {placeholder}, {placeholder})",
        (device_id, command, payload_json, initiated_by_user_id, status),
    )
    return int(last_id) if last_id else 0


async def mark_command_delivered(command_id: int, *, error: str | None = None) -> None:
    placeholder = "?" if db.is_sqlite() else "%s"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    new_status = "error" if error else "delivered"
    await db.execute(
        f"UPDATE tray_command_log SET status = {placeholder}, error = {placeholder}, "
        f"delivered_at = {placeholder} WHERE id = {placeholder}",
        (new_status, error, now, command_id),
    )
