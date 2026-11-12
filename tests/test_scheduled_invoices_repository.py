"""Repository tests for scheduled invoice patching."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.core.database import db
from app.repositories import scheduled_invoices


def test_patch_scheduled_invoice_accepts_invoice_id_column(monkeypatch):
    """The ``invoice_id`` column must not collide with the record id argument."""
    execute = AsyncMock()
    monkeypatch.setattr(db, "execute", execute)
    monkeypatch.setattr(
        scheduled_invoices,
        "get_scheduled_invoice",
        AsyncMock(return_value={"id": 7, "status": "issued", "invoice_id": 376}),
    )

    result = asyncio.run(
        scheduled_invoices.patch_scheduled_invoice(
            7,
            status="issued",
            invoice_id=376,
            invoice_number="2026000376",
        )
    )

    sql, params = execute.await_args.args
    assert sql == (
        "UPDATE scheduled_invoices SET status = %s, invoice_id = %s, "
        "invoice_number = %s WHERE id = %s"
    )
    assert params == ("issued", 376, "2026000376", 7)
    assert result["invoice_id"] == 376
