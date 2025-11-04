import asyncio

from app.services import xero


def test_evaluate_qty_expression_with_static_number():
    result = xero._evaluate_qty_expression("5", {})
    assert result == 5.0


def test_evaluate_qty_expression_with_variable():
    context = {"active_agents": 10}
    result = xero._evaluate_qty_expression("{active_agents}", context)
    assert result == 10.0


def test_evaluate_qty_expression_with_invalid_input():
    result = xero._evaluate_qty_expression("invalid", {})
    assert result == 1.0


def test_evaluate_qty_expression_with_missing_variable():
    result = xero._evaluate_qty_expression("{missing_var}", {})
    assert result == 1.0


def test_build_recurring_invoice_items(monkeypatch):
    async def fake_list_items(company_id):
        return [
            {
                "id": 1,
                "company_id": company_id,
                "product_code": "LABOR-MANAGED",
                "description_template": "Managed services for {company_name}",
                "qty_expression": "1",
                "price_override": 100.00,
                "active": 1,
            },
            {
                "id": 2,
                "company_id": company_id,
                "product_code": "LABOR-ADHOC",
                "description_template": "Ad-hoc support",
                "qty_expression": "{active_agents}",
                "price_override": None,
                "active": 1,
            },
            {
                "id": 3,
                "company_id": company_id,
                "product_code": "INACTIVE-ITEM",
                "description_template": "Should not appear",
                "qty_expression": "1",
                "price_override": None,
                "active": 0,
            },
        ]

    monkeypatch.setattr(xero.recurring_items_repo, "list_company_recurring_invoice_items", fake_list_items)

    context = {"company_name": "Test Company", "active_agents": 5}
    result = asyncio.run(
        xero.build_recurring_invoice_items(
            company_id=1,
            tax_type="OUTPUT2",
            context=context,
        )
    )

    assert len(result) == 2
    assert result[0]["Description"] == "Managed services for Test Company"
    assert result[0]["Quantity"] == 1.0
    assert result[0]["ItemCode"] == "LABOR-MANAGED"
    assert result[0]["UnitAmount"] == 100.00
    assert result[0]["TaxType"] == "OUTPUT2"

    assert result[1]["Description"] == "Ad-hoc support"
    assert result[1]["Quantity"] == 5.0
    assert result[1]["ItemCode"] == "LABOR-ADHOC"
    assert "UnitAmount" not in result[1]
    assert result[1]["TaxType"] == "OUTPUT2"


def test_build_recurring_invoice_items_with_empty_list(monkeypatch):
    async def fake_list_items(company_id):
        return []

    monkeypatch.setattr(xero.recurring_items_repo, "list_company_recurring_invoice_items", fake_list_items)

    result = asyncio.run(
        xero.build_recurring_invoice_items(
            company_id=1,
            tax_type="OUTPUT2",
            context={},
        )
    )

    assert len(result) == 0
