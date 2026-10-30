"""Persistence for per-user single sign-on identity links."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.database import db

_DATETIME_FIELDS = ("created_at", "last_login_at")


def _normalise(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    result = dict(row)
    for field in _DATETIME_FIELDS:
        value = result.get(field)
        if isinstance(value, datetime) and value.tzinfo is None:
            result[field] = value.replace(tzinfo=timezone.utc)
    return result


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


async def get_by_subject(provider: str, tenant_id: str, object_id: str) -> dict[str, Any] | None:
    return _normalise(await db.fetch_one(
        "SELECT * FROM user_sso_identities"
        " WHERE provider = %s AND tenant_id = %s AND object_id = %s",
        (provider, tenant_id.lower(), object_id.lower()),
    ))


async def get_for_user(user_id: int, provider: str = "m365") -> dict[str, Any] | None:
    return _normalise(await db.fetch_one(
        "SELECT * FROM user_sso_identities WHERE user_id = %s AND provider = %s",
        (user_id, provider),
    ))


async def create(
    *, user_id: int, provider: str, tenant_id: str, object_id: str,
    upn: str | None, email: str | None, linked_via: str, linked_by_user_id: int | None,
) -> dict[str, Any]:
    tenant_id = tenant_id.lower()
    object_id = object_id.lower()
    await db.execute(
        """INSERT INTO user_sso_identities
           (user_id, provider, tenant_id, object_id, upn, email, linked_via, linked_by_user_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (user_id, provider, tenant_id, object_id, upn, email, linked_via, linked_by_user_id),
    )
    row = await get_by_subject(provider, tenant_id, object_id)
    return row or {}


async def replace_for_user(
    *, user_id: int, provider: str, tenant_id: str, object_id: str,
    upn: str | None, email: str | None, linked_via: str, linked_by_user_id: int | None,
) -> dict[str, Any]:
    await db.execute(
        "DELETE FROM user_sso_identities WHERE user_id = %s AND provider = %s",
        (user_id, provider),
    )
    return await create(
        user_id=user_id, provider=provider, tenant_id=tenant_id, object_id=object_id,
        upn=upn, email=email, linked_via=linked_via, linked_by_user_id=linked_by_user_id,
    )


async def delete_for_user(user_id: int, provider: str = "m365") -> dict[str, Any] | None:
    existing = await get_for_user(user_id, provider)
    if not existing:
        return None
    await db.execute(
        "DELETE FROM user_sso_identities WHERE user_id = %s AND provider = %s",
        (user_id, provider),
    )
    return existing


async def record_login(
    identity_id: int, *, upn: str | None, email: str | None, when: datetime,
) -> None:
    await db.execute(
        "UPDATE user_sso_identities SET upn = %s, email = %s, last_login_at = %s WHERE id = %s",
        (upn, email, _naive_utc(when), identity_id),
    )


async def list_for_users(user_ids: list[int], provider: str = "m365") -> dict[int, dict[str, Any]]:
    if not user_ids:
        return {}
    placeholders = ", ".join(["%s"] * len(user_ids))
    rows = await db.fetch_all(
        "SELECT * FROM user_sso_identities WHERE provider = %s AND user_id IN ("  # nosec B608
        + placeholders + ")",
        (provider, *user_ids),
    )
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        normalised = _normalise(row)
        if normalised:
            result[int(normalised["user_id"])] = normalised
    return result


async def count_for_company(company_id: int, provider: str = "m365") -> int:
    row = await db.fetch_one(
        """SELECT COUNT(DISTINCT i.id) AS count
           FROM user_sso_identities AS i
           INNER JOIN users AS u ON u.id = i.user_id
           WHERE i.provider = %s
             AND (u.company_id = %s
                  OR EXISTS (SELECT 1 FROM user_companies AS uc
                             WHERE uc.user_id = u.id AND uc.company_id = %s))""",
        (provider, company_id, company_id),
    )
    return int(row["count"]) if row and row.get("count") is not None else 0
