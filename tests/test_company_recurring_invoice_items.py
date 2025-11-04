import asyncio

from app.repositories import company_recurring_invoice_items as recurring_items_repo


def test_list_company_recurring_invoice_items_builds_expected_query(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_fetch_all(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return [
            {
                "id": 1,
                "company_id": 5,
                "product_code": "LABOR-MANAGED",
                "description_template": "Monthly managed services",
                "qty_expression": "1",
                "price_override": None,
                "active": 1,
            }
        ]

    monkeypatch.setattr(recurring_items_repo.db, "fetch_all", fake_fetch_all)

    result = asyncio.run(recurring_items_repo.list_company_recurring_invoice_items(company_id=5))

    assert len(result) == 1
    assert "company_id = %s" in captured["sql"]
    assert captured["params"] == (5,)


def test_create_recurring_invoice_item_builds_expected_query(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_execute(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params

    async def fake_fetch_one(sql, params=None):
        return {
            "id": 1,
            "company_id": 5,
            "product_code": "LABOR-MANAGED",
            "description_template": "Monthly managed services",
            "qty_expression": "1",
            "price_override": None,
            "active": 1,
        }

    monkeypatch.setattr(recurring_items_repo.db, "execute", fake_execute)
    monkeypatch.setattr(recurring_items_repo.db, "fetch_one", fake_fetch_one)

    result = asyncio.run(
        recurring_items_repo.create_recurring_invoice_item(
            company_id=5,
            product_code="LABOR-MANAGED",
            description_template="Monthly managed services",
            qty_expression="1",
            price_override=None,
            active=True,
        )
    )

    assert result["id"] == 1
    assert result["company_id"] == 5
    assert "INSERT INTO company_recurring_invoice_items" in captured["sql"]
    assert captured["params"] == (5, "LABOR-MANAGED", "Monthly managed services", "1", None, 1)


def test_update_recurring_invoice_item_builds_expected_query(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_execute(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params

    async def fake_fetch_one(sql, params=None):
        return {
            "id": 1,
            "company_id": 5,
            "product_code": "LABOR-MANAGED-UPDATED",
            "description_template": "Updated description",
            "qty_expression": "2",
            "price_override": 100.00,
            "active": 1,
        }

    monkeypatch.setattr(recurring_items_repo.db, "execute", fake_execute)
    monkeypatch.setattr(recurring_items_repo.db, "fetch_one", fake_fetch_one)

    result = asyncio.run(
        recurring_items_repo.update_recurring_invoice_item(
            item_id=1,
            product_code="LABOR-MANAGED-UPDATED",
            description_template="Updated description",
            qty_expression="2",
            price_override=100.00,
        )
    )

    assert result["product_code"] == "LABOR-MANAGED-UPDATED"
    assert "UPDATE company_recurring_invoice_items SET" in captured["sql"]


def test_delete_recurring_invoice_item_builds_expected_query(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_execute(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params

    monkeypatch.setattr(recurring_items_repo.db, "execute", fake_execute)

    asyncio.run(recurring_items_repo.delete_recurring_invoice_item(item_id=1))

    assert "DELETE FROM company_recurring_invoice_items" in captured["sql"]
    assert captured["params"] == (1,)
