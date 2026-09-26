from __future__ import annotations

import hashlib
import hmac
from datetime import datetime
from typing import Any

from app.core.database import db
from app.repositories import vault

_COLUMNS = "id, credential_id, credential_version, company_id, staff_id, grantor_user_id, recipient_user_id, recipient_email, reason, expires_at, revoked_at, consumed_at"


def digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


async def create(
    *,
    credential_id: int,
    company_id: int,
    staff_id: int,
    grantor_user_id: int,
    recipient_user_id: int | None,
    recipient_email: str | None,
    reason: str,
    expires_at: datetime,
    token: str | None,
    verification_code: str | None,
) -> dict[str, Any] | None:
    credential = await db.fetch_one(
        "SELECT c.current_version FROM credentials c JOIN credential_links l ON l.credential_id = c.id AND l.company_id = c.company_id AND l.target_type = 'staff' AND l.target_id = %s WHERE c.id = %s AND c.company_id = %s AND c.revoked_at IS NULL AND c.archived_at IS NULL",
        (staff_id, credential_id, company_id),
    )
    if credential is None:
        return None
    if recipient_user_id is not None:
        recipient = await db.fetch_one(
            "SELECT u.id FROM users u JOIN user_companies uc ON uc.user_id = u.id WHERE u.id = %s AND uc.company_id = %s AND u.is_active = 1 AND u.email_verified_at IS NOT NULL",
            (recipient_user_id, company_id),
        )
        if recipient is None:
            return None
    grant_id = await db.execute_returning_lastrowid(
        "INSERT INTO credential_grants (credential_id, credential_version, company_id, staff_id, grantor_user_id, recipient_user_id, recipient_email, reason, expires_at, token_hash, verification_hash) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            credential_id,
            credential["current_version"],
            company_id,
            staff_id,
            grantor_user_id,
            recipient_user_id,
            recipient_email,
            reason,
            expires_at,
            digest(token) if token else None,
            (
                digest(token + ":" + verification_code)
                if token and verification_code
                else None
            ),
        ),
    )
    return await get(grant_id)


async def get(grant_id: int) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT " + _COLUMNS + " FROM credential_grants WHERE id = %s", (grant_id,)
    )


async def revoke(
    grant_id: int, company_id: int, grantor_user_id: int
) -> dict[str, Any] | None:
    changed = await db.execute_rowcount(
        "UPDATE credential_grants SET revoked_at = CURRENT_TIMESTAMP WHERE id = %s AND company_id = %s AND grantor_user_id = %s AND revoked_at IS NULL AND consumed_at IS NULL",
        (grant_id, company_id, grantor_user_id),
    )
    return await get(grant_id) if changed else None


async def reveal_named(
    grant_id: int, user_id: int, company_id: int
) -> tuple[dict[str, Any], str] | None:
    # Consuming is the authorization linearization point. Exactly one competing
    # request can win, and a revoke that wins first prevents any decryption.
    changed = await db.execute_rowcount(
        "UPDATE credential_grants SET opened_at = COALESCE(opened_at, CURRENT_TIMESTAMP), revealed_at = CURRENT_TIMESTAMP, consumed_at = CURRENT_TIMESTAMP WHERE id = %s AND company_id = %s AND recipient_user_id = %s AND revoked_at IS NULL AND consumed_at IS NULL AND expires_at > CURRENT_TIMESTAMP",
        (grant_id, company_id, user_id),
    )
    if changed != 1:
        return None
    row = await get(grant_id)
    if row is None:
        return None
    revealed = await vault.reveal(
        company_id, int(row["credential_id"]), int(row["credential_version"])
    )
    return (row, revealed[1]) if revealed else None


async def verify_external(token: str, code: str) -> int | None:
    row = await db.fetch_one(
        "SELECT g.id, g.verification_hash FROM credential_grants g "
        "JOIN company_credential_features f ON f.company_id = g.company_id AND f.enabled = 1 "
        "WHERE g.token_hash = %s AND g.recipient_user_id IS NULL AND g.verified_at IS NULL "
        "AND g.revoked_at IS NULL AND g.consumed_at IS NULL AND g.expires_at > CURRENT_TIMESTAMP",
        (digest(token),),
    )
    if row is None or not hmac.compare_digest(
        bytes(row["verification_hash"]), digest(token + ":" + code)
    ):
        return None
    changed = await db.execute_rowcount(
        "UPDATE credential_grants SET verified_at = CURRENT_TIMESTAMP, opened_at = COALESCE(opened_at, CURRENT_TIMESTAMP) WHERE id = %s AND verified_at IS NULL AND revoked_at IS NULL AND consumed_at IS NULL AND expires_at > CURRENT_TIMESTAMP AND EXISTS (SELECT 1 FROM company_credential_features f WHERE f.company_id = credential_grants.company_id AND f.enabled = 1)",
        (row["id"],),
    )
    return int(row["id"]) if changed else None


async def consume_external(token: str) -> tuple[dict[str, Any], str] | None:
    token_hash = digest(token)
    # The conditional state transition is the one-time gate. Only its winner may decrypt.
    changed = await db.execute_rowcount(
        "UPDATE credential_grants SET consumed_at = CURRENT_TIMESTAMP, revealed_at = CURRENT_TIMESTAMP WHERE token_hash = %s AND recipient_user_id IS NULL AND verified_at IS NOT NULL AND revoked_at IS NULL AND consumed_at IS NULL AND expires_at > CURRENT_TIMESTAMP AND EXISTS (SELECT 1 FROM company_credential_features f WHERE f.company_id = credential_grants.company_id AND f.enabled = 1)",
        (token_hash,),
    )
    if changed != 1:
        return None
    row = await db.fetch_one(
        "SELECT " + _COLUMNS + " FROM credential_grants WHERE token_hash = %s",
        (token_hash,),
    )
    if row is None:
        return None
    revealed = await vault.reveal(
        int(row["company_id"]),
        int(row["credential_id"]),
        int(row["credential_version"]),
    )
    return (row, revealed[1]) if revealed else None


async def claim_expiry(
    *, grant_id: int | None = None, user_id: int | None = None, token: str | None = None
) -> dict[str, Any] | None:
    """Claim the single expiry audit event without changing client-visible errors."""
    if token is not None:
        where = "token_hash = %s AND recipient_user_id IS NULL"
        params: tuple[Any, ...] = (digest(token),)
    elif grant_id is not None and user_id is not None:
        where = "id = %s AND recipient_user_id = %s"
        params = (grant_id, user_id)
    else:
        return None
    changed = await db.execute_rowcount(
        "UPDATE credential_grants SET expired_audited_at = CURRENT_TIMESTAMP WHERE "
        + where
        + " AND expired_audited_at IS NULL AND expires_at <= CURRENT_TIMESTAMP",
        params,
    )
    if changed != 1:
        return None
    return await db.fetch_one(
        "SELECT " + _COLUMNS + " FROM credential_grants WHERE " + where, params
    )
