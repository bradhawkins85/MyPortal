from decimal import Decimal
import asyncio

from app.services import invoice_generator


def test_subscription_invoice_contains_only_selected_item_and_honours_auto_send(monkeypatch):
    captured = {}

    async def fake_generate(company_id, **kwargs):
        captured["company_id"] = company_id
        captured.update(kwargs)
        return {"status": "succeeded", "invoice_id": 71, "invoice_number": "INV-71"}

    async def fake_company(company_id):
        return {"id": company_id, "xero_auto_send_subscription_invoices": 0}

    async def fake_sync(invoice_id, auto_send=False):
        captured["sync"] = (invoice_id, auto_send)
        return {"status": "succeeded"}

    monkeypatch.setattr(invoice_generator, "generate_invoice", fake_generate)
    monkeypatch.setattr(invoice_generator.company_repo, "get_company_by_id", fake_company)
    monkeypatch.setattr(invoice_generator.xero_service, "sync_invoice", fake_sync)

    result = asyncio.run(invoice_generator.generate_subscription_invoice(
        9,
        recurring_item={
            "id": 4,
            "company_id": 9,
            "product_code": "SUB-1",
            "description_template": "Managed subscription",
        },
        quantity=3,
        unit_amount=Decimal("12.50"),
        coterm_end_date="2027-06-30",
    ))

    assert result["status"] == "succeeded"
    assert captured["include_recurring_items"] is False
    assert captured["include_ticket_items"] is False
    assert captured["recurring_line_items"] == [{
        "Description": "Managed subscription\nCo-Term Expiry: 2027-06-30",
        "Quantity": 3,
        "UnitAmount": 12.5,
        "ItemCode": "SUB-1",
        "MyPortalRecurringItemId": 4,
    }]
    assert captured["sync"] == (71, False)


def test_subscription_invoice_rejects_another_company_item():
    try:
        asyncio.run(invoice_generator.generate_subscription_invoice(
            9,
            recurring_item={"id": 4, "company_id": 10},
            quantity=1,
            unit_amount=Decimal("1.00"),
        ))
    except ValueError as exc:
        assert "does not belong" in str(exc)
    else:
        raise AssertionError("Expected company ownership validation")
