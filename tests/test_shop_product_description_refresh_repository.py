import pytest

from app.repositories import shop as shop_repo


@pytest.mark.anyio("asyncio")
async def test_list_product_description_refresh_ids_excludes_subscriptions(monkeypatch):
    captured = {}

    async def _fetch_all(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return [{"id": 8}, {"id": 13}]

    monkeypatch.setattr(shop_repo.db, "fetch_all", _fetch_all)

    product_ids = await shop_repo.list_product_description_refresh_ids()

    assert product_ids == [8, 13]
    assert "subscription_category_id IS NULL" in captured["sql"]
    assert "archived = 0" in captured["sql"]
    assert captured["params"] is None


@pytest.mark.anyio("asyncio")
async def test_list_product_description_refresh_ids_can_include_archived(monkeypatch):
    captured = {}

    async def _fetch_all(sql, params=None):
        captured["sql"] = sql
        return []

    monkeypatch.setattr(shop_repo.db, "fetch_all", _fetch_all)

    await shop_repo.list_product_description_refresh_ids(include_archived=True)

    assert "subscription_category_id IS NULL" in captured["sql"]
    assert "archived = 0" not in captured["sql"]
