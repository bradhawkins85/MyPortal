"""Persistence for per-user Microsoft 365 contact integrations."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.database import db


def _normalise(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    result = dict(row)
    expires = result.get("token_expires_at")
    if isinstance(expires, datetime) and expires.tzinfo is None:
        result["token_expires_at"] = expires.replace(tzinfo=timezone.utc)
    return result


async def get_integration(user_id: int) -> dict[str, Any] | None:
    return _normalise(await db.fetch_one(
        "SELECT * FROM user_m365_contact_integrations WHERE user_id = %s", (user_id,)
    ))


async def upsert_integration(
    user_id: int, *, tenant_id: str, account_email: str | None,
    refresh_token: str, access_token: str | None, token_expires_at: datetime | None,
    oauth_client_id: str | None = None, oauth_authority: str | None = None,
    oauth_account_id: str | None = None, oauth_scopes: str | None = None,
    oauth_connection_version: int | None = None, expected_revision: int | None = None,
) -> None:
    expires = token_expires_at.replace(tzinfo=None) if token_expires_at else None
    existing = await get_integration(user_id)
    if existing:
        revision_clause = "" if expected_revision is None else " AND token_revision = %s"
        params: list[Any] = [tenant_id, account_email, refresh_token, access_token, expires,
                             oauth_client_id, oauth_authority, oauth_account_id, oauth_scopes,
                             oauth_connection_version, user_id]
        if expected_revision is not None:
            params.append(expected_revision)
        await db.execute(
            """UPDATE user_m365_contact_integrations
               SET tenant_id = %s, account_email = %s, refresh_token = %s,
                   access_token = %s, token_expires_at = %s,
                   oauth_client_id = COALESCE(%s, oauth_client_id),
                   oauth_authority = COALESCE(%s, oauth_authority),
                   oauth_account_id = COALESCE(%s, oauth_account_id),
                   oauth_scopes = COALESCE(%s, oauth_scopes),
                   oauth_connection_version = COALESCE(%s, oauth_connection_version),
                   token_revision = token_revision + 1, updated_at = UTC_TIMESTAMP(6)
               WHERE user_id = %s""" + revision_clause,
            tuple(params),
        )
        return
    await db.execute(
        """INSERT INTO user_m365_contact_integrations
           (user_id, tenant_id, account_email, refresh_token, access_token, token_expires_at,
            oauth_client_id, oauth_authority, oauth_account_id, oauth_scopes, oauth_connection_version)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (user_id, tenant_id, account_email, refresh_token, access_token, expires,
         oauth_client_id, oauth_authority, oauth_account_id, oauth_scopes, oauth_connection_version),
    )


async def delete_integration(user_id: int) -> None:
    await db.execute("DELETE FROM user_m365_contact_integrations WHERE user_id = %s", (user_id,))
