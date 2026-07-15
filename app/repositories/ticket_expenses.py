"""Repository for ticket expenses data access."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.core.database import db


async def create_expense(ticket_id: int, description: str, amount: Decimal, created_by_user_id: int | None) -> dict[str, Any]:
    """Create a chargeable expense for a ticket."""
    expense_id = await db.execute_returning_lastrowid(
        """
        INSERT INTO ticket_expenses (ticket_id, description, amount, created_by_user_id)
        VALUES (?, ?, ?, ?)
        """,
        (ticket_id, description, str(amount), created_by_user_id),
    )
    return await get_expense(expense_id) or {}


async def get_expense(expense_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        """
        SELECT id, ticket_id, description, amount, created_by_user_id, billed_at, xero_invoice_number, created_at, updated_at
        FROM ticket_expenses
        WHERE id = ?
        """,
        (expense_id,),
    )
    return dict(row) if row else None


async def list_expenses(ticket_id: int, *, unbilled_only: bool = False) -> list[dict[str, Any]]:
    query = """
        SELECT id, ticket_id, description, amount, created_by_user_id, billed_at, xero_invoice_number, created_at, updated_at
        FROM ticket_expenses
        WHERE ticket_id = ?
    """
    params: list[Any] = [ticket_id]
    if unbilled_only:
        query += " AND billed_at IS NULL"
    query += " ORDER BY created_at ASC, id ASC"
    return [dict(row) for row in await db.fetch_all(query, tuple(params))]


async def delete_expense(expense_id: int, ticket_id: int) -> None:
    await db.execute("DELETE FROM ticket_expenses WHERE id = ? AND ticket_id = ? AND billed_at IS NULL", (expense_id, ticket_id))


async def mark_expenses_billed(expense_ids: list[int], *, invoice_number: str, billed_at: Any) -> None:
    if not expense_ids:
        return
    placeholders = ", ".join("?" for _ in expense_ids)
    await db.execute(
        f"UPDATE ticket_expenses SET billed_at = ?, xero_invoice_number = ? WHERE id IN ({placeholders})",
        (billed_at, invoice_number, *expense_ids),
    )


async def get_unbilled_total(ticket_id: int) -> Decimal:
    row = await db.fetch_one(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM ticket_expenses WHERE ticket_id = ? AND billed_at IS NULL",
        (ticket_id,),
    )
    return Decimal(str((row or {}).get("total") or 0))
