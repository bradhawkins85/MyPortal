from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.core.database import db


def _normalise(row: dict[str, Any]) -> dict[str, Any]:
    normalised = dict(row)
    for key in (
        "token_expires_at",
        "client_secret_expires_at",
        "admin_secret_expires_at",
        "created_at",
        "updated_at",
    ):
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


async def list_provisioned_company_ids() -> set[int]:
    """Return the set of company IDs that have M365 credentials configured."""
    rows = await db.fetch_all(
        "SELECT DISTINCT company_id FROM company_m365_credentials",
    )
    return {int(row["company_id"]) for row in rows}


async def upsert_mailbox(
    *,
    company_id: int,
    user_principal_name: str,
    display_name: str,
    mailbox_type: str,
    storage_used_bytes: int,
    archive_storage_used_bytes: int | None,
    has_archive: bool,
    forwarding_rule_count: int,
) -> None:
    """Insert or update a mailbox record for the given company."""
    await db.execute(
        """
        INSERT INTO m365_mailboxes (
            company_id, user_principal_name, display_name, mailbox_type,
            storage_used_bytes, archive_storage_used_bytes, has_archive,
            forwarding_rule_count, synced_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            display_name = VALUES(display_name),
            mailbox_type = VALUES(mailbox_type),
            storage_used_bytes = VALUES(storage_used_bytes),
            archive_storage_used_bytes = VALUES(archive_storage_used_bytes),
            has_archive = VALUES(has_archive),
            forwarding_rule_count = VALUES(forwarding_rule_count),
            synced_at = VALUES(synced_at)
        """,
        (
            company_id,
            user_principal_name,
            display_name,
            mailbox_type,
            storage_used_bytes,
            archive_storage_used_bytes,
            int(has_archive),
            forwarding_rule_count,
            datetime.utcnow(),
        ),
    )


async def delete_stale_mailboxes(company_id: int, current_upns: list[str]) -> None:
    """Remove mailbox rows for UPNs that are no longer present in the tenant."""
    if not current_upns:
        await db.execute(
            "DELETE FROM m365_mailboxes WHERE company_id = %s",
            (company_id,),
        )
        return
    placeholders = ", ".join(["%s"] * len(current_upns))
    await db.execute(
        f"DELETE FROM m365_mailboxes WHERE company_id = %s AND user_principal_name NOT IN ({placeholders})",
        (company_id, *current_upns),
    )


async def get_mailboxes(company_id: int, mailbox_type: str) -> list[dict]:
    """Return stored mailbox rows for the given company filtered by type."""
    rows = await db.fetch_all(
        """
        SELECT * FROM m365_mailboxes
        WHERE company_id = %s AND mailbox_type = %s
        ORDER BY display_name ASC
        """,
        (company_id, mailbox_type),
    )
    return [dict(row) for row in rows]


async def get_mailbox_synced_at(company_id: int) -> datetime | None:
    """Return the most recent synced_at timestamp for the company's mailboxes."""
    row = await db.fetch_one(
        "SELECT MAX(synced_at) AS synced_at FROM m365_mailboxes WHERE company_id = %s",
        (company_id,),
    )
    if row and row.get("synced_at"):
        value = row["synced_at"]
        return value if isinstance(value, datetime) else None
    return None


async def upsert_mailbox_member(
    *,
    company_id: int,
    mailbox_email: str,
    member_upn: str,
    member_display_name: str,
    synced_at: datetime,
) -> None:
    """Insert or update a mailbox-member row for the given company."""
    await db.execute(
        """
        INSERT INTO m365_mailbox_members (
            company_id, mailbox_email, member_upn, member_display_name, synced_at
        ) VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            member_display_name = VALUES(member_display_name),
            synced_at = VALUES(synced_at)
        """,
        (
            company_id,
            mailbox_email,
            member_upn,
            member_display_name,
            synced_at,
        ),
    )


async def delete_stale_mailbox_members(
    company_id: int, synced_before: datetime
) -> None:
    """Remove mailbox-member rows that were not touched in the current sync run.

    All rows for *company_id* whose ``synced_at`` timestamp is older than
    *synced_before* (the timestamp recorded at the start of the sync) are
    deleted.  Rows written during the current run have ``synced_at >=
    synced_before`` and are therefore retained.

    Using a timestamp comparison avoids building a large ``NOT IN (...)``
    clause that could degrade for tenants with many users and group memberships.
    """
    await db.execute(
        "DELETE FROM m365_mailbox_members WHERE company_id = %s AND synced_at < %s",
        (company_id, synced_before),
    )


async def get_mailbox_members(
    company_id: int, mailbox_email: str
) -> list[dict[str, Any]]:
    """Return synced member rows for the given mailbox email address."""
    rows = await db.fetch_all(
        """
        SELECT member_upn, member_display_name
        FROM m365_mailbox_members
        WHERE company_id = %s AND mailbox_email = %s
        ORDER BY member_display_name ASC
        """,
        (company_id, mailbox_email),
    )
    return [dict(row) for row in rows]


async def get_mailbox_members_by_local_part(
    company_id: int, local_part: str
) -> list[dict[str, Any]]:
    """Return synced member rows for mailbox emails sharing the given local part.

    This is a fallback when the exact ``mailbox_email`` lookup returns no results
    and the live Graph API call to resolve proxy addresses has failed.  It finds
    member rows stored under any email alias that shares the same local part (the
    portion before ``@``).  The ``local_part`` is escaped for SQL ``LIKE`` before
    use.
    """
    # Single-pass escape of SQL LIKE wildcards (\ % _) to prevent unintended
    # matching.  Each special character is prefixed with the ESCAPE character \.
    escaped = re.sub(r"([\\%_])", r"\\\1", local_part)
    pattern = escaped + "@%"
    rows = await db.fetch_all(
        """
        SELECT DISTINCT member_upn, member_display_name
        FROM m365_mailbox_members
        WHERE company_id = %s AND mailbox_email LIKE %s ESCAPE '\\\\'
        ORDER BY member_display_name ASC
        """,
        (company_id, pattern),
    )
    return [dict(row) for row in rows]


async def get_mailboxes_accessible_by_member(
    company_id: int, member_upn: str
) -> list[dict[str, Any]]:
    """Return mailboxes that the given member UPN has been granted access to.

    Queries ``m365_mailbox_members`` for all rows where ``member_upn`` matches,
    joining ``m365_mailboxes`` to retrieve the human-readable display name for
    each mailbox.  Falls back to the ``mailbox_email`` value when no matching
    row exists in ``m365_mailboxes`` (e.g. the mailbox was not captured in the
    usage report).

    :returns: A list of dicts with ``mailbox_email`` and ``display_name`` keys,
        ordered alphabetically by ``display_name``.
    """
    rows = await db.fetch_all(
        """
        SELECT
            mm.mailbox_email,
            COALESCE(mb.display_name, mm.mailbox_email) AS display_name
        FROM m365_mailbox_members mm
        LEFT JOIN m365_mailboxes mb
            ON mb.company_id = mm.company_id
           AND mb.user_principal_name = mm.mailbox_email
        WHERE mm.company_id = %s AND mm.member_upn = %s
        ORDER BY display_name ASC
        """,
        (company_id, member_upn),
    )
    return [dict(row) for row in rows]


async def get_admin_credentials(company_id: int) -> dict[str, Any] | None:
    """Retrieve the M365 admin app credentials for a specific company.

    Returns a dict with admin_client_id, admin_client_secret, admin_tenant_id,
    admin_app_object_id, admin_secret_key_id, admin_secret_expires_at, and
    pkce_client_id fields if configured, or None if no admin credentials exist.
    """
    row = await db.fetch_one(
        """
        SELECT admin_client_id, admin_client_secret, admin_tenant_id,
               admin_app_object_id, admin_secret_key_id, admin_secret_expires_at,
               pkce_client_id
        FROM company_m365_credentials
        WHERE company_id = %s
          AND admin_client_id IS NOT NULL
          AND admin_client_id != ''
        """,
        (company_id,),
    )
    if not row:
        return None
    result = dict(row)
    # Normalise datetime field
    if result.get("admin_secret_expires_at"):
        value = result["admin_secret_expires_at"]
        if isinstance(value, datetime):
            result["admin_secret_expires_at"] = value.replace(tzinfo=None)
    return result


async def upsert_admin_credentials(
    *,
    company_id: int,
    admin_client_id: str,
    admin_client_secret: str,
    admin_tenant_id: str | None = None,
    admin_app_object_id: str | None = None,
    admin_secret_key_id: str | None = None,
    admin_secret_expires_at: datetime | None = None,
    pkce_client_id: str | None = None,
) -> None:
    """Store or update M365 admin app credentials for a company.

    This upserts the admin credential columns in company_m365_credentials.
    If the company row doesn't exist yet, it requires at least a placeholder
    tenant_id/client_id for the customer credentials.
    """
    # Check if row exists first
    existing = await get_credentials(company_id)
    if existing:
        # Update existing row with admin credentials
        await db.execute(
            """
            UPDATE company_m365_credentials
            SET admin_client_id = %s,
                admin_client_secret = %s,
                admin_tenant_id = %s,
                admin_app_object_id = %s,
                admin_secret_key_id = %s,
                admin_secret_expires_at = %s,
                pkce_client_id = %s
            WHERE company_id = %s
            """,
            (
                admin_client_id,
                admin_client_secret,
                admin_tenant_id,
                admin_app_object_id,
                admin_secret_key_id,
                admin_secret_expires_at,
                pkce_client_id,
                company_id,
            ),
        )
    else:
        # Insert new row with admin credentials (using placeholder customer creds)
        await db.execute(
            """
            INSERT INTO company_m365_credentials (
                company_id, tenant_id, client_id, client_secret,
                admin_client_id, admin_client_secret, admin_tenant_id,
                admin_app_object_id, admin_secret_key_id, admin_secret_expires_at,
                pkce_client_id
            ) VALUES (%s, '', '', '', %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                company_id,
                admin_client_id,
                admin_client_secret,
                admin_tenant_id,
                admin_app_object_id,
                admin_secret_key_id,
                admin_secret_expires_at,
                pkce_client_id,
            ),
        )


async def delete_admin_credentials(company_id: int) -> None:
    """Clear the M365 admin app credentials for a company."""
    await db.execute(
        """
        UPDATE company_m365_credentials
        SET admin_client_id = NULL,
            admin_client_secret = NULL,
            admin_tenant_id = NULL,
            admin_app_object_id = NULL,
            admin_secret_key_id = NULL,
            admin_secret_expires_at = NULL,
            pkce_client_id = NULL
        WHERE company_id = %s
        """,
        (company_id,),
    )


async def update_pkce_client_id(company_id: int, pkce_client_id: str | None) -> None:
    """Update the PKCE client ID for a company."""
    await db.execute(
        """
        UPDATE company_m365_credentials
        SET pkce_client_id = %s
        WHERE company_id = %s
        """,
        (pkce_client_id, company_id),
    )
