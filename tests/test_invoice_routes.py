
from datetime import datetime, timezone
from decimal import Decimal

from app.features.invoices.routes import _format_invoice_records


def test_format_invoice_records_uses_explicit_super_admin_flag():
    records = [
        {
            "id": 1,
            "invoice_number": "INV-1",
            "amount": Decimal("12.30"),
            "status": "draft",
            "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "due_date": None,
            "xero_invoice_id": "",
        }
    ]

    formatted_regular, _, _ = _format_invoice_records(records, is_super_admin=False)
    formatted_admin, _, _ = _format_invoice_records(records, is_super_admin=True)

    assert formatted_regular[0]["can_sync_to_xero"] is False
    assert formatted_admin[0]["can_sync_to_xero"] is True
