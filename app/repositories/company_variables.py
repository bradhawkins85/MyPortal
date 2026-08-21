"""Shared company-variable definitions and company-specific values."""

from __future__ import annotations

from typing import Any

from app.core.database import db


async def list_for_company(company_id: int) -> list[dict[str, Any]]:
    return await db.fetch_all(
        """
        SELECT d.id, d.name, COALESCE(v.value, '') AS value
        FROM company_variable_definitions d
        LEFT JOIN company_variable_values v
          ON v.variable_id = d.id AND v.company_id = %s
        ORDER BY d.name
        """,
        (company_id,),
    )


async def value_map(company_id: int) -> dict[str, str]:
    return {str(row["name"]): str(row.get("value") or "") for row in await list_for_company(company_id)}


async def create_definition(name: str) -> int:
    return await db.execute_returning_lastrowid(
        "INSERT INTO company_variable_definitions (name) VALUES (%s)", (name,)
    )


async def set_value(company_id: int, variable_id: int, value: str) -> None:
    if db.is_sqlite():
        sql = """
            INSERT INTO company_variable_values (company_id, variable_id, value)
            VALUES (%s, %s, %s)
            ON CONFLICT(company_id, variable_id) DO UPDATE SET value = excluded.value
        """
    else:
        sql = """
            INSERT INTO company_variable_values (company_id, variable_id, value)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE value = VALUES(value)
        """
    await db.execute(sql, (company_id, variable_id, value))
