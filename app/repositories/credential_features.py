from __future__ import annotations

from app.core.database import db


async def is_enabled(company_id: int) -> bool:
    """Fail closed unless the credential vault is explicitly enabled for a company."""
    row = await db.fetch_one(
        "SELECT enabled FROM company_credential_features WHERE company_id = %s",
        (company_id,),
    )
    return bool(row and row.get("enabled"))
