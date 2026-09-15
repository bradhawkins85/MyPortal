"""Saved delivery addresses belonging to companies."""

from __future__ import annotations

from typing import Any

from app.core.database import db


async def list_for_company(company_id: int) -> list[dict[str, Any]]:
    return await db.fetch_all(
        """SELECT id, company_id, label, street, city, state, postcode, country
           FROM company_addresses WHERE company_id = %s ORDER BY label, id""",
        (company_id,),
    )


async def get_for_company(company_id: int, address_id: int) -> dict[str, Any] | None:
    return await db.fetch_one(
        """SELECT id, company_id, label, street, city, state, postcode, country
           FROM company_addresses WHERE company_id = %s AND id = %s""",
        (company_id, address_id),
    )


async def create(company_id: int, **fields: str | None) -> dict[str, Any]:
    address_id = await db.execute_returning_lastrowid(
        """INSERT INTO company_addresses
           (company_id, label, street, city, state, postcode, country)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (
            company_id, fields["label"], fields["street"], fields.get("city"),
            fields.get("state"), fields.get("postcode"), fields.get("country"),
        ),
    )
    return (await get_for_company(company_id, address_id)) or {}


async def update(company_id: int, address_id: int, **fields: str | None) -> dict[str, Any] | None:
    await db.execute(
        """UPDATE company_addresses SET label = %s, street = %s, city = %s,
           state = %s, postcode = %s, country = %s, updated_at = CURRENT_TIMESTAMP
           WHERE company_id = %s AND id = %s""",
        (
            fields["label"], fields["street"], fields.get("city"), fields.get("state"),
            fields.get("postcode"), fields.get("country"), company_id, address_id,
        ),
    )
    return await get_for_company(company_id, address_id)


async def delete(company_id: int, address_id: int) -> bool:
    if not await get_for_company(company_id, address_id):
        return False
    await db.execute(
        "DELETE FROM company_addresses WHERE company_id = %s AND id = %s",
        (company_id, address_id),
    )
    return True
