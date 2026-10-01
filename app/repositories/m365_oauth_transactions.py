"""Durable storage for short-lived Microsoft OAuth transactions."""

from __future__ import annotations

from datetime import datetime

from app.core.database import db


async def create(
    transaction_id: str, encrypted_payload: str, expires_at: datetime
) -> None:
    # Opportunistic cleanup keeps this deliberately small table bounded without
    # adding another scheduled task.
    await db.execute(
        "DELETE FROM m365_oauth_transactions WHERE expires_at <= CURRENT_TIMESTAMP"
    )
    await db.execute(
        """
        INSERT INTO m365_oauth_transactions
            (transaction_id, encrypted_payload, expires_at, created_at)
        VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
        """,
        (transaction_id, encrypted_payload, expires_at),
    )


async def consume(transaction_id: str) -> str | None:
    """Atomically claim a transaction and return its encrypted payload."""
    claimed = await db.execute_rowcount(
        """
        UPDATE m365_oauth_transactions
        SET consumed_at = CURRENT_TIMESTAMP
        WHERE transaction_id = %s
          AND consumed_at IS NULL
          AND expires_at > CURRENT_TIMESTAMP
        """,
        (transaction_id,),
    )
    if claimed != 1:
        return None
    row = await db.fetch_one(
        """
        SELECT encrypted_payload
        FROM m365_oauth_transactions
        WHERE transaction_id = %s
        """,
        (transaction_id,),
    )
    return str(row["encrypted_payload"]) if row else None
