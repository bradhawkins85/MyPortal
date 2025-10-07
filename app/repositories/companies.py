from __future__ import annotations

from typing import Any, List, Optional

from app.core.database import db


async def get_company_by_id(company_id: int) -> Optional[dict[str, Any]]:
    row = await db.fetch_one("SELECT * FROM companies WHERE id = %s", (company_id,))
    if row and "is_vip" in row:
        row["is_vip"] = int(row["is_vip"]) if row["is_vip"] is not None else None
    return row


async def list_companies() -> List[dict[str, Any]]:
    rows = await db.fetch_all("SELECT * FROM companies ORDER BY name")
    return [
        {**row, "is_vip": int(row["is_vip"]) if row.get("is_vip") is not None else None}
        for row in rows
    ]


async def create_company(**data: Any) -> dict[str, Any]:
    columns = ", ".join(data.keys())
    placeholders = ", ".join(["%s"] * len(data))
    await db.execute(
        f"INSERT INTO companies ({columns}) VALUES ({placeholders})",
        tuple(data.values()),
    )
    row = await db.fetch_one(
        "SELECT * FROM companies WHERE id = LAST_INSERT_ID()"
    )
    if not row:
        raise RuntimeError("Failed to create company")
    if "is_vip" in row:
        row["is_vip"] = int(row["is_vip"]) if row["is_vip"] is not None else None
    return row


async def update_company(company_id: int, **updates: Any) -> dict[str, Any]:
    if not updates:
        company = await get_company_by_id(company_id)
        if not company:
            raise ValueError("Company not found")
        return company

    columns = ", ".join(f"{column} = %s" for column in updates.keys())
    params = list(updates.values()) + [company_id]
    await db.execute(f"UPDATE companies SET {columns} WHERE id = %s", tuple(params))
    updated = await get_company_by_id(company_id)
    if not updated:
        raise ValueError("Company not found after update")
    return updated


async def delete_company(company_id: int) -> None:
    await db.execute("DELETE FROM companies WHERE id = %s", (company_id,))
