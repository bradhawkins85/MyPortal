from decimal import Decimal

import pytest

from app.services import invoice_generator


@pytest.mark.anyio
async def test_generate_order_invoice_persists_locally_before_xero_sync(monkeypatch):
    calls: list[tuple[str, object]] = []

    async def fake_generate_invoice(company_id, **kwargs):
        calls.append(("generate", kwargs["recurring_line_items"]))
        return {"status": "succeeded", "invoice_id": 42, "invoice_number": "INV-42"}

    async def fake_get_company(company_id):
        return {"id": company_id, "xero_auto_send_product_invoices": 0}

    async def fake_sync_invoice(invoice_id, *, auto_send):
        calls.append(("sync", (invoice_id, auto_send)))
        return {"status": "succeeded"}

    monkeypatch.setattr(invoice_generator, "generate_invoice", fake_generate_invoice)
    monkeypatch.setattr(invoice_generator.company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(invoice_generator.xero_service, "sync_invoice", fake_sync_invoice)

    result = await invoice_generator.generate_order_invoice(
        order_number="ORD123",
        company_id=7,
        user_name="Ada Admin",
        order_items=[
            {
                "product_name": "Router",
                "product_sku": "RTR-1",
                "quantity": 2,
                "unit_price": Decimal("99.50"),
            }
        ],
        freight_amount=Decimal("12.00"),
    )

    assert [call[0] for call in calls] == ["generate", "sync"]
    assert calls[0][1][0] == {
        "Description": "Router",
        "Quantity": 2,
        "UnitAmount": 99.5,
        "ItemCode": "RTR-1",
    }
    assert calls[0][1][1]["Description"] == "Freight"
    assert calls[1] == ("sync", (42, False))
    assert result["order_number"] == "ORD123"
    assert result["xero_result"]["status"] == "succeeded"


@pytest.mark.anyio
async def test_generate_order_invoice_keeps_local_invoice_when_xero_fails(monkeypatch):
    async def fake_generate_invoice(company_id, **kwargs):
        return {"status": "succeeded", "invoice_id": 91}

    async def fake_get_company(company_id):
        return {"id": company_id, "xero_auto_send_product_invoices": 1}

    async def fake_sync_invoice(invoice_id, *, auto_send):
        return {"status": "failed", "error": "temporary Xero outage"}

    monkeypatch.setattr(invoice_generator, "generate_invoice", fake_generate_invoice)
    monkeypatch.setattr(invoice_generator.company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(invoice_generator.xero_service, "sync_invoice", fake_sync_invoice)

    result = await invoice_generator.generate_order_invoice(
        order_number="ORD456",
        company_id=7,
        order_items=[{"product_name": "Switch", "quantity": 1, "unit_price": 50}],
    )

    assert result["status"] == "succeeded"
    assert result["invoice_id"] == 91
    assert result["xero_result"]["status"] == "failed"
