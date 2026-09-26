from __future__ import annotations

from app.core.database import db

REQUIRED_TABLES = (
    "company_credential_features",
    "credentials",
    "credential_secret_versions",
    "credential_links",
    "audit_logs",
)


async def is_enabled(company_id: int) -> bool:
    """Fail closed unless the credential vault is explicitly enabled for a company."""
    row = await db.fetch_one(
        "SELECT enabled FROM company_credential_features WHERE company_id = %s",
        (company_id,),
    )
    return bool(row and row.get("enabled"))


async def get(company_id: int) -> dict | None:
    return await db.fetch_one(
        "SELECT enabled, enabled_at, enabled_by_user_id, updated_at "
        "FROM company_credential_features WHERE company_id = %s",
        (company_id,),
    )


async def missing_prerequisite_tables() -> list[str]:
    """Return logical prerequisite names, without leaking database errors."""
    missing: list[str] = []
    for table in REQUIRED_TABLES:
        try:
            # Names are from the constant allow-list above, never user input.
            await db.fetch_one(f"SELECT 1 AS ready FROM {table} WHERE 1 = 0")
        except Exception:
            missing.append(table)
    return missing


async def set_enabled(company_id: int, *, enabled: bool, actor_id: int) -> None:
    if await get(company_id) is None:
        await db.execute(
            "INSERT INTO company_credential_features "
            "(company_id, enabled, enabled_at, enabled_by_user_id, updated_at) "
            "VALUES (%s, %s, CASE WHEN %s = 1 THEN CURRENT_TIMESTAMP ELSE NULL END, %s, CURRENT_TIMESTAMP)",
            (company_id, int(enabled), int(enabled), actor_id),
        )
        return
    await db.execute(
        "UPDATE company_credential_features SET enabled = %s, "
        "enabled_at = CASE WHEN %s = 1 THEN CURRENT_TIMESTAMP ELSE enabled_at END, "
        "enabled_by_user_id = %s, updated_at = CURRENT_TIMESTAMP WHERE company_id = %s",
        (int(enabled), int(enabled), actor_id, company_id),
    )
