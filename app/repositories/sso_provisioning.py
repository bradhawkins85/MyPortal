"""Atomically provision an ordinary portal account for Microsoft sign-in."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import aiomysql
import aiosqlite

from app.core.database import db


@asynccontextmanager
async def _connection() -> AsyncIterator[Any]:
    if db.is_sqlite():
        # Use a dedicated connection so unrelated repository commits cannot
        # commit a partially provisioned account on the shared SQLite handle.
        async with aiosqlite.connect(str(db._get_sqlite_path())) as connection:
            connection.row_factory = aiosqlite.Row
            await connection.execute("PRAGMA foreign_keys = ON")
            yield connection
    else:
        async with db.acquire() as connection:
            yield connection


async def create_user_with_identity(
    *, email: str, password_hash: str, first_name: str | None, last_name: str | None,
    company_id: int, tenant_id: str, object_id: str, upn: str | None,
    identity_email: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Commit the account, Member access and identity together, or none of them."""
    sqlite = db.is_sqlite()
    async with _connection() as connection:
        if sqlite:
            await connection.execute("BEGIN IMMEDIATE")
        else:
            await connection.begin()
        try:
            async with connection.cursor(*(() if sqlite else (aiomysql.DictCursor,))) as cursor:
                async def execute(sql: str, params: tuple = ()) -> None:
                    await cursor.execute(sql.replace("%s", "?") if sqlite else sql, params)

                await execute("SELECT id FROM roles WHERE name = %s", ("Member",))
                role = await cursor.fetchone()
                if not role:
                    raise RuntimeError("The Member role is required for Microsoft sign-in provisioning")
                await execute(
                    "INSERT INTO users (email, password_hash, first_name, last_name, company_id,"
                    " is_super_admin, is_active, email_verified_at)"
                    " VALUES (%s, %s, %s, %s, %s, 0, 1, CURRENT_TIMESTAMP)",
                    (email, password_hash, first_name, last_name, company_id),
                )
                user_id = cursor.lastrowid
                await execute(
                    "INSERT INTO user_companies (user_id, company_id, is_admin) VALUES (%s, %s, 0)",
                    (user_id, company_id),
                )
                await execute(
                    "INSERT INTO company_memberships"
                    " (company_id, user_id, role_id, status, invited_at, joined_at)"
                    " VALUES (%s, %s, %s, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                    (company_id, user_id, role["id"]),
                )
                await execute(
                    "INSERT INTO user_sso_identities"
                    " (user_id, provider, tenant_id, object_id, upn, email, linked_via)"
                    " VALUES (%s, 'm365', %s, %s, %s, %s, 'auto')",
                    (user_id, tenant_id.lower(), object_id.lower(), upn, identity_email),
                )
                identity_id = cursor.lastrowid
                await execute("SELECT * FROM users WHERE id = %s", (user_id,))
                user = dict(await cursor.fetchone())
                await execute("SELECT * FROM user_sso_identities WHERE id = %s", (identity_id,))
                identity = dict(await cursor.fetchone())
            await connection.commit()
        except BaseException:
            await connection.rollback()
            raise
    return user, identity
