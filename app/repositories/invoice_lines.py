from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.core.database import db


def _normalise_line(row: dict[str, Any]) -> dict[str, Any]:
    line = dict(row)
    for field in ("id", "invoice_id"):
        if field in line and line[field] is not None:
            line[field] = int(line[field])
    for field in ("quantity", "unit_amount", "amount"):
        val = line.get(field)
        if val is not None:
            line[field] = Decimal(str(val))
    return line


async def list_invoice_lines(invoice_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT * FROM invoice_lines WHERE invoice_id = %s ORDER BY id",
        (invoice_id,),
    )
    return [_normalise_line(row) for row in rows]


async def create_invoice_line(
    *,
    invoice_id: int,
    description: str | None,
    quantity: Decimal,
    unit_amount: Decimal,
    amount: Decimal,
    product_code: str | None,
) -> dict[str, Any]:
    line_id = await db.execute_returning_lastrowid(
        """
        INSERT INTO invoice_lines (invoice_id, description, quantity, unit_amount, amount, product_code)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (invoice_id, description, quantity, unit_amount, amount, product_code),
    )
    if not line_id:
        raise RuntimeError("Failed to create invoice line")
    row = await db.fetch_one("SELECT * FROM invoice_lines WHERE id = %s", (line_id,))
    if not row:
        raise RuntimeError("Failed to retrieve created invoice line")
    return _normalise_line(row)


async def delete_invoice_lines(invoice_id: int) -> None:
    await db.execute("DELETE FROM invoice_lines WHERE invoice_id = %s", (invoice_id,))
