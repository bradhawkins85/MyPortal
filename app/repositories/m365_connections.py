"""Persistence for gradual, per-company Microsoft 365 connection migration."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.database import db


async def get(connection_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one("SELECT * FROM m365_connections WHERE id = %s", (connection_id,))
    return dict(row) if row else None


async def get_active(company_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT * FROM m365_connections WHERE company_id = %s AND state = 'active' "
        "ORDER BY version DESC LIMIT 1",
        (company_id,),
    )
    return dict(row) if row else None


async def get_pending(company_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT * FROM m365_connections WHERE company_id = %s AND state = 'pending' "
        "ORDER BY version DESC LIMIT 1",
        (company_id,),
    )
    return dict(row) if row else None


async def ensure_legacy(company_id: int, credentials: dict[str, Any]) -> dict[str, Any]:
    """Represent an existing credential row without changing its ID or secrets."""
    active = await get_active(company_id)
    if active:
        await inventory_dependencies(
            int(active["id"]), company_id, str(credentials.get("tenant_id") or "")
        )
        return active
    await db.execute(
        """
        INSERT INTO m365_connections
          (company_id, version, mode, state, tenant_id, client_id, client_secret,
           app_object_id, client_secret_key_id, client_secret_expires_at,
           verification_tenant, verification_workload, verification_renewal,
           verified_at, activated_at)
        VALUES (%s, 1, 'legacy', 'active', %s, %s, %s, %s, %s, %s,
                1, 1, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON DUPLICATE KEY UPDATE company_id = VALUES(company_id)
        """,
        (
            company_id,
            credentials.get("tenant_id") or "",
            credentials.get("client_id") or "",
            credentials.get("client_secret") or "",
            credentials.get("app_object_id"),
            credentials.get("client_secret_key_id"),
            credentials.get("client_secret_expires_at"),
        ),
    )
    active = await get_active(company_id)
    if not active:
        raise RuntimeError("Failed to inventory legacy M365 connection")
    await inventory_dependencies(
        int(active["id"]), company_id, str(credentials.get("tenant_id") or "")
    )
    return active


async def inventory_dependencies(
    connection_id: int, company_id: int, tenant_id: str
) -> None:
    """Snapshot every known consumer before an old connection can be retired."""
    statements: tuple[tuple[str, tuple[Any, ...]], ...] = (
        (
            """INSERT IGNORE INTO m365_connection_dependencies
               (connection_id, dependency_type, dependency_key, details)
               SELECT %s, 'scheduled_job', CAST(id AS CHAR), command
               FROM scheduled_tasks WHERE company_id = %s""",
            (connection_id, company_id),
        ),
        (
            """INSERT IGNORE INTO m365_connection_dependencies
               (connection_id, dependency_type, dependency_key, details)
               SELECT %s, 'delegated_mailbox', CAST(id AS CHAR), user_principal_name
               FROM m365_mail_accounts WHERE company_id = %s""",
            (connection_id, company_id),
        ),
        (
            """INSERT IGNORE INTO m365_connection_dependencies
               (connection_id, dependency_type, dependency_key, details)
               SELECT %s, 'csp_mapping', CAST(id AS CHAR), csp_tenant_id
               FROM companies WHERE id = %s AND csp_tenant_id IS NOT NULL""",
            (connection_id, company_id),
        ),
        (
            """INSERT IGNORE INTO m365_connection_dependencies
               (connection_id, dependency_type, dependency_key, details)
               SELECT %s, 'personal_contact', CAST(user_id AS CHAR), account_email
               FROM user_m365_contact_integrations WHERE tenant_id = %s""",
            (connection_id, tenant_id),
        ),
        (
            """INSERT IGNORE INTO m365_connection_dependencies
               (connection_id, dependency_type, dependency_key, details)
               SELECT %s, 'company_bootstrap', CAST(company_id AS CHAR), admin_client_id
               FROM company_m365_credentials WHERE company_id = %s
                 AND admin_client_id IS NOT NULL AND admin_client_id != ''""",
            (connection_id, company_id),
        ),
    )
    for sql, params in statements:
        await db.execute(sql, params)


async def stage_candidate(
    *, company_id: int, tenant_id: str, client_id: str, client_secret: str,
    app_object_id: str | None, service_principal_object_id: str | None,
    client_secret_key_id: str | None, client_secret_expires_at: datetime | None,
) -> dict[str, Any]:
    """Idempotently stage credentials; never modifies the active connection."""
    await db.execute(
        """
        INSERT INTO m365_connections
          (company_id, version, mode, state, tenant_id, client_id, client_secret,
           app_object_id, service_principal_object_id, client_secret_key_id,
           client_secret_expires_at)
        SELECT %s, COALESCE(MAX(version), 0) + 1, 'managed', 'pending', %s, %s, %s,
               %s, %s, %s, %s
        FROM m365_connections WHERE company_id = %s
        ON DUPLICATE KEY UPDATE
          client_secret = VALUES(client_secret),
          app_object_id = VALUES(app_object_id),
          service_principal_object_id = VALUES(service_principal_object_id),
          client_secret_key_id = VALUES(client_secret_key_id),
          client_secret_expires_at = VALUES(client_secret_expires_at)
        """,
        (company_id, tenant_id, client_id, client_secret, app_object_id,
         service_principal_object_id, client_secret_key_id,
         client_secret_expires_at, company_id),
    )
    row = await db.fetch_one(
        "SELECT * FROM m365_connections WHERE company_id = %s AND tenant_id = %s "
        "AND client_id = %s",
        (company_id, tenant_id, client_id),
    )
    if not row:
        raise RuntimeError("Failed to stage M365 connection candidate")
    return dict(row)


async def record_verification(
    connection_id: int, *, tenant: bool, workload: bool, renewal: bool,
    error: str | None = None,
) -> None:
    verified_at = (
        datetime.now(timezone.utc).replace(tzinfo=None)
        if tenant and workload and renewal else None
    )
    await db.execute(
        """UPDATE m365_connections
           SET verification_tenant = %s, verification_workload = %s,
               verification_renewal = %s, verification_error = %s,
               verified_at = %s
           WHERE id = %s AND state = 'pending'""",
        (int(tenant), int(workload), int(renewal), error, verified_at, connection_id),
    )


async def activate(company_id: int, connection_id: int) -> dict[str, Any]:
    """Atomically switch the legacy compatibility row and active pointer."""
    candidate = await get(connection_id)
    if not candidate or int(candidate["company_id"]) != company_id:
        raise ValueError("M365 connection candidate not found")
    if candidate["state"] != "pending" or not all(
        candidate.get(key) for key in
        ("verification_tenant", "verification_workload", "verification_renewal")
    ):
        raise ValueError("M365 connection candidate has not passed verification")
    previous = await get_active(company_id)
    async with db.acquire_lock(f"m365_connection_cutover_{company_id}", timeout=10) as acquired:
        if not acquired:
            raise RuntimeError("M365 connection cutover is already in progress")
        # The compatibility credential is updated last. Existing callers therefore
        # continue to use the old secret if any preceding statement fails.
        try:
            await db.execute(
                "UPDATE m365_connections SET state = 'rollback', updated_at = CURRENT_TIMESTAMP "
                "WHERE company_id = %s AND state = 'active'",
                (company_id,),
            )
            await db.execute(
                "UPDATE m365_connections SET state = 'active', activated_at = CURRENT_TIMESTAMP, "
                "previous_connection_id = %s WHERE id = %s AND state = 'pending'",
                (previous.get("id") if previous else None, connection_id),
            )
            if previous:
                await db.execute(
                    "UPDATE m365_connection_dependencies SET connection_id = %s "
                    "WHERE connection_id = %s",
                    (connection_id, previous["id"]),
                )
            credential_values = (
                candidate["tenant_id"], candidate["client_id"], candidate["client_secret"],
                candidate.get("app_object_id"), candidate.get("client_secret_key_id"),
                candidate.get("client_secret_expires_at"), company_id,
            )
            if previous:
                await db.execute(
                    """UPDATE company_m365_credentials SET tenant_id = %s, client_id = %s,
                       client_secret = %s, app_object_id = %s, client_secret_key_id = %s,
                       client_secret_expires_at = %s WHERE company_id = %s""",
                    credential_values,
                )
            else:
                await db.execute(
                    """INSERT INTO company_m365_credentials
                       (tenant_id, client_id, client_secret, app_object_id,
                        client_secret_key_id, client_secret_expires_at, company_id)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    credential_values,
                )
        except Exception:
            # Preserve the old active pointer if any step before compatibility
            # credential replacement fails. Secrets are reused, not rebuilt.
            await db.execute(
                "UPDATE m365_connections SET state = 'pending' WHERE id = %s",
                (connection_id,),
            )
            if previous:
                await db.execute(
                    "UPDATE m365_connection_dependencies SET connection_id = %s "
                    "WHERE connection_id = %s",
                    (previous["id"], connection_id),
                )
                await db.execute(
                    "UPDATE m365_connections SET state = 'active' WHERE id = %s",
                    (previous["id"],),
                )
            raise
    active = await get_active(company_id)
    if not active:
        raise RuntimeError("M365 cutover did not produce an active connection")
    return active


async def rollback(company_id: int) -> dict[str, Any]:
    active = await get_active(company_id)
    if not active or not active.get("previous_connection_id"):
        raise ValueError("No previous M365 connection is available")
    previous = await get(int(active["previous_connection_id"]))
    if not previous or previous["state"] != "rollback":
        raise ValueError("Previous M365 connection is unavailable")
    await db.execute(
        "UPDATE m365_connections SET state = 'pending' WHERE id = %s", (active["id"],)
    )
    await db.execute(
        "UPDATE m365_connections SET state = 'active', activated_at = CURRENT_TIMESTAMP "
        "WHERE id = %s", (previous["id"],)
    )
    await db.execute(
        "UPDATE m365_connection_dependencies SET connection_id = %s "
        "WHERE connection_id = %s", (previous["id"], active["id"])
    )
    await db.execute(
        """UPDATE company_m365_credentials SET tenant_id = %s, client_id = %s,
           client_secret = %s, app_object_id = %s, client_secret_key_id = %s,
           client_secret_expires_at = %s WHERE company_id = %s""",
        (previous["tenant_id"], previous["client_id"], previous["client_secret"],
         previous.get("app_object_id"), previous.get("client_secret_key_id"),
         previous.get("client_secret_expires_at"), company_id),
    )
    return previous


async def dependency_inventory(connection_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT dependency_type, dependency_key, details "
        "FROM m365_connection_dependencies WHERE connection_id = %s "
        "ORDER BY dependency_type, dependency_key",
        (connection_id,),
    )
    return [dict(row) for row in rows]


async def retire(connection_id: int) -> None:
    connection = await get(connection_id)
    if not connection or connection["state"] != "rollback":
        raise ValueError("Only an inactive rollback connection may be retired")
    if await dependency_inventory(connection_id):
        raise ValueError("M365 connection still has dependent resources")
    await db.execute(
        "UPDATE m365_connections SET state = 'retired', retired_at = CURRENT_TIMESTAMP "
        "WHERE id = %s AND state = 'rollback'", (connection_id,),
    )
