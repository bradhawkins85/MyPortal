from datetime import date
from decimal import Decimal

import pytest

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
            "billable_statuses": ["open", "pending"],
            "line_item_description_template": "Ticket {ticket_id}: {ticket_subject}",
        }
    }
    payload = {
        "client_id": "new-id",
        "client_secret": "********",
        "default_hourly_rate": "175",
        "billable_statuses": "resolved, Closed",
        "line_item_description_template": " Ticket #{ticket_id} - {ticket_subject} ",
    }
    result = modules_service._coerce_settings("xero", payload, existing)
    assert result["client_secret"] == "super-secret"
    assert result["refresh_token"] == "refresh-token"
    assert result["client_id"] == "new-id"
    assert result["default_hourly_rate"] == "175.00"
    assert result["billable_statuses"] == ["resolved", "closed"]
    assert result["line_item_description_template"] == "Ticket #{ticket_id} - {ticket_subject}"


def test_coerce_settings_xero_includes_company_name():
    """Test that company_name field is properly handled in Xero settings."""
    existing = {
        "settings": {
            "client_id": "existing-id",
            "client_secret": "super-secret",
            "refresh_token": "refresh-token",
            "tenant_id": "existing-tenant",
            "company_name": "Old Company Name",
        }
    }
    payload = {
        "company_name": "New Company Name",
        "tenant_id": "new-tenant",
    }
    result = modules_service._coerce_settings("xero", payload, existing)
    assert result["company_name"] == "New Company Name"
    assert result["tenant_id"] == "new-tenant"
    # Verify secrets are preserved
    assert result["client_secret"] == "super-secret"
    assert result["refresh_token"] == "refresh-token"


@pytest.mark.anyio("asyncio")
async def test_build_ticket_invoices_groups_billable_minutes():
    async def fake_fetch_ticket(ticket_id: int):
        return {
            "id": ticket_id,
            "company_id": 1,
            "subject": f"Ticket {ticket_id}",
            "status": "resolved",
        }

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
async def test_build_ticket_invoices_respects_status_filters_and_templates():
    invoice_day = date(2024, 5, 1)

    async def fake_fetch_ticket(ticket_id: int):
        status = "resolved" if ticket_id == 1 else "open"
        return {
            "id": ticket_id,
            "company_id": 1,
            "subject": f"Ticket {ticket_id}",
            "status": status,
        }

    async def fake_fetch_replies(ticket_id: int):
        return [
            {
                "minutes_spent": 30,
                "is_billable": True,
                "labour_type_code": "REMOTE",
                "labour_type_name": "Remote",
            }
        ]

    async def fake_fetch_company(company_id: int):
        return {"id": company_id, "name": "Acme Corp", "xero_id": "abc-123"}

    existing_invoice = {
        "type": "ACCREC",
        "contact": {"Name": "Acme Corp"},
        "line_items": [
            {"Description": "Existing", "Quantity": 1.0, "UnitAmount": 100.0, "AccountCode": "400"}
        ],
        "line_amount_type": "Exclusive",
        "reference": "Support — Tickets 100",
        "context": {
            "company": {"id": 1, "name": "Acme Corp", "xero_id": "abc-123"},
            "tickets": [
                {
                    "id": 100,
                    "subject": "Earlier",
                    "billable_minutes": 60,
                    "status": "resolved",
                    "labour_groups": [],
                }
            ],
            "total_billable_minutes": 60,
            "invoice_date": invoice_day.isoformat(),
        },
    }
    invoice_map: dict[tuple[int, date], dict] = {(1, invoice_day): existing_invoice}

    invoices = await xero_service.build_ticket_invoices(
        [1, 2],
        hourly_rate=Decimal("150"),
        account_code="400",
        tax_type=None,
        line_amount_type="Exclusive",
        reference_prefix="Support",
        allowed_statuses=["resolved"],
        description_template="Ticket #{ticket_id} - {ticket_subject} - {labour_name}",
        invoice_date=invoice_day,
        existing_invoice_map=invoice_map,
        fetch_ticket=fake_fetch_ticket,
        fetch_replies=fake_fetch_replies,
        fetch_company=fake_fetch_company,
    )

    assert invoices == [existing_invoice]
    assert len(existing_invoice["line_items"]) == 2
    assert existing_invoice["line_items"][-1]["Description"] == "Ticket #1 - Ticket 1 - Remote"
    assert existing_invoice["context"]["total_billable_minutes"] == 90
    assert existing_invoice["context"]["tickets"][-1]["id"] == 1
    assert existing_invoice["context"]["invoice_date"] == invoice_day.isoformat()
    assert existing_invoice["reference"] == "Support — Tickets 100, 1"


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
