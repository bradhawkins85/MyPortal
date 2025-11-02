import pytest
from decimal import Decimal

from app.services import modules as modules_service
from app.services import xero as xero_service


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_coerce_settings_xero_preserves_secrets():
    existing = {
        "settings": {
            "client_id": "existing-id",
            "client_secret": "super-secret",
            "refresh_token": "refresh-token",
            "default_hourly_rate": "120.00",
        }
    }
    payload = {
        "client_id": "new-id",
        "client_secret": "********",
        "default_hourly_rate": "175",
    }
    result = modules_service._coerce_settings("xero", payload, existing)
    assert result["client_secret"] == "super-secret"
    assert result["refresh_token"] == "refresh-token"
    assert result["client_id"] == "new-id"
    assert result["default_hourly_rate"] == "175.00"


@pytest.mark.anyio("asyncio")
async def test_build_ticket_invoices_groups_billable_minutes():
    async def fake_fetch_ticket(ticket_id: int):
        return {"id": ticket_id, "company_id": 1, "subject": f"Ticket {ticket_id}"}

    async def fake_fetch_replies(ticket_id: int):
        if ticket_id == 1:
            return [
                {"minutes_spent": 30, "is_billable": True},
                {"minutes_spent": 15, "is_billable": False},
            ]
        return [
            {"minutes_spent": 45, "is_billable": False},
            {"minutes_spent": 10, "is_billable": False},
        ]

    async def fake_fetch_company(company_id: int):
        return {"id": company_id, "name": "Acme Corp", "xero_id": "abc-123"}

    invoices = await xero_service.build_ticket_invoices(
        [1, "2"],
        hourly_rate=Decimal("150"),
        account_code="400",
        tax_type="OUTPUT",
        line_amount_type="Exclusive",
        reference_prefix="Support",
        fetch_ticket=fake_fetch_ticket,
        fetch_replies=fake_fetch_replies,
        fetch_company=fake_fetch_company,
    )

    assert len(invoices) == 1
    invoice = invoices[0]
    assert invoice["context"]["total_billable_minutes"] == 30
    assert invoice["line_items"][0]["UnitAmount"] == 150.0
    assert invoice["line_items"][0]["Quantity"] == 0.5


@pytest.mark.anyio("asyncio")
async def test_build_order_invoice_returns_payload_with_context():
    async def fake_fetch_summary(order_number: str, company_id: int):
        return {"order_number": order_number, "status": "placed"}

    async def fake_fetch_items(order_number: str, company_id: int):
        return [
            {
                "quantity": 2,
                "price": Decimal("19.99"),
                "product_name": "Widget",
                "sku": "WID-1",
            }
        ]

    async def fake_fetch_company(company_id: int):
        return {"id": company_id, "name": "Acme Corp", "xero_id": "xyz-789"}

    invoice = await xero_service.build_order_invoice(
        "SO-100",
        1,
        account_code="400",
        tax_type=None,
        line_amount_type="Exclusive",
        fetch_summary=fake_fetch_summary,
        fetch_items=fake_fetch_items,
        fetch_company=fake_fetch_company,
    )

    assert invoice is not None
    assert invoice["line_items"][0]["Quantity"] == 2
    assert invoice["context"]["order"]["order_number"] == "SO-100"
    assert invoice["context"]["company"]["xero_id"] == "xyz-789"
