"""Regression tests for non-inventory subscription orders."""

import asyncio
from contextlib import asynccontextmanager

from app.repositories import shop as shop_repo


def test_create_order_does_not_decrement_subscription_stock(monkeypatch):
    executed: list[tuple[str, tuple[object, ...]]] = []

    class Cursor:
        rowcount = 1

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def execute(self, query, params):
            executed.append((" ".join(query.split()), params))

        async def fetchone(self):
            return {"stock": 0, "subscription_category_id": 4}

    class Connection:
        def cursor(self, _cursor_type):
            return Cursor()

        async def begin(self):
            return None

        async def commit(self):
            return None

        async def rollback(self):
            return None

    @asynccontextmanager
    async def fake_acquire():
        yield Connection()

    monkeypatch.setattr(shop_repo.db, "acquire", fake_acquire)

    stock_change = asyncio.run(
        shop_repo.create_order(
            user_id=1,
            company_id=2,
            product_id=3,
            quantity=5,
            order_number="ORD123",
            status="pending",
            po_number=None,
        )
    )

    assert stock_change == (None, None)
    assert any("INSERT INTO shop_orders" in query for query, _params in executed)
    assert not any("UPDATE shop_products SET stock" in query for query, _params in executed)
