from unittest.mock import AsyncMock

import pytest

from app.services import products as products_service


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_import_product_by_vendor_sku_returns_false_when_missing(monkeypatch):
    mock_get_item = AsyncMock(return_value=None)
    monkeypatch.setattr(
        products_service.stock_feed_repo,
        "get_item_by_sku",
        mock_get_item,
    )

    mock_get_product = AsyncMock()
    monkeypatch.setattr(
        products_service.shop_repo,
        "get_product_by_sku",
        mock_get_product,
    )

    result = await products_service.import_product_by_vendor_sku("MISSING-SKU")

    assert result is False
    mock_get_item.assert_awaited_once_with("MISSING-SKU")
    mock_get_product.assert_not_called()


@pytest.mark.anyio("asyncio")
async def test_import_product_by_vendor_sku_processes_feed_item(monkeypatch):
    item = {"sku": "ABC123"}
    existing_product = {"id": 42}

    mock_get_item = AsyncMock(return_value=item)
    monkeypatch.setattr(
        products_service.stock_feed_repo,
        "get_item_by_sku",
        mock_get_item,
    )

    mock_get_product = AsyncMock(return_value=existing_product)
    monkeypatch.setattr(
        products_service.shop_repo,
        "get_product_by_sku",
        mock_get_product,
    )

    mock_process = AsyncMock(return_value=True)
    monkeypatch.setattr(products_service, "_process_feed_item", mock_process)

    result = await products_service.import_product_by_vendor_sku("  ABC123  ")

    assert result is True
    mock_get_item.assert_awaited_once_with("ABC123")
    mock_get_product.assert_awaited_once_with("ABC123", include_archived=True)
    mock_process.assert_awaited_once_with(item, existing_product)
