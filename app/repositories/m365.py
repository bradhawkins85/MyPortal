from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.database import db


def _normalise(row: dict[str, Any]) -> dict[str, Any]:
    normalised = dict(row)
    for key in ("token_expires_at", "client_secret_expires_at", "created_at", "updated_at"):
        value = normalised.get(key)
        if isinstance(value, datetime):
            normalised[key] = value.replace(tzinfo=None)
    if "company_id" in normalised and normalised["company_id"] is not None:
        normalised["company_id"] = int(normalised["company_id"])
    return normalised


async def get_credentials(company_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT * FROM company_m365_credentials WHERE company_id = %s",
        (company_id,),
    )
    return _normalise(row) if row else None


async def upsert_credentials(
    *,
    company_id: int,
    tenant_id: str,
    client_id: str,
    client_secret: str,
    refresh_token: str | None = None,
    access_token: str | None = None,
    token_expires_at: datetime | None = None,
    app_object_id: str | None = None,
    client_secret_key_id: str | None = None,
    client_secret_expires_at: datetime | None = None,
) -> dict[str, Any]:
    await db.execute(
        """
        INSERT INTO company_m365_credentials (
            company_id, tenant_id, client_id, client_secret, refresh_token, access_token,
            token_expires_at, app_object_id, client_secret_key_id, client_secret_expires_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            tenant_id = VALUES(tenant_id),
            client_id = VALUES(client_id),
            client_secret = VALUES(client_secret),
            refresh_token = VALUES(refresh_token),
            access_token = VALUES(access_token),
            token_expires_at = VALUES(token_expires_at),
            app_object_id = VALUES(app_object_id),
            client_secret_key_id = VALUES(client_secret_key_id),
            client_secret_expires_at = VALUES(client_secret_expires_at)
        """,
        (
            company_id,
            tenant_id,
            client_id,
            client_secret,
            refresh_token,
            access_token,
            token_expires_at,
            app_object_id,
            client_secret_key_id,
            client_secret_expires_at,
        ),
    )
    credentials = await get_credentials(company_id)
    if not credentials:
        raise RuntimeError("Failed to persist Microsoft 365 credentials")
    return credentials


async def update_tokens(
    *,
    company_id: int,
    refresh_token: str | None,
    access_token: str | None,
    token_expires_at: datetime | None,
) -> dict[str, Any]:
    await db.execute(
        """
        UPDATE company_m365_credentials
        SET refresh_token = %s, access_token = %s, token_expires_at = %s
        WHERE company_id = %s
        """,
        (
            refresh_token,
            access_token,
            token_expires_at,
            company_id,
        ),
    )
    credentials = await get_credentials(company_id)
    if not credentials:
        raise RuntimeError("Credentials not found after token update")
    return credentials


async def update_client_secret(
    *,
    company_id: int,
    client_secret: str,
    key_id: str | None = None,
    expires_at: datetime | None = None,
) -> None:
    """Replace the stored client secret, key ID and expiry with new values."""
    await db.execute(
        """
        UPDATE company_m365_credentials
        SET client_secret = %s,
            client_secret_key_id = %s,
            client_secret_expires_at = %s
        WHERE company_id = %s
        """,
        (client_secret, key_id, expires_at, company_id),
    )


async def list_credentials_expiring_before(cutoff: datetime) -> list[dict[str, Any]]:
    """Return all credential rows whose client secret expires before *cutoff*."""
    rows = await db.fetch_all(
        """
        SELECT * FROM company_m365_credentials
        WHERE client_secret_expires_at IS NOT NULL
          AND client_secret_expires_at <= %s
        """,
        (cutoff,),
    )
    return [_normalise(row) for row in rows]


async def delete_credentials(company_id: int) -> None:
    await db.execute(
        "DELETE FROM company_m365_credentials WHERE company_id = %s",
        (company_id,),
    )
