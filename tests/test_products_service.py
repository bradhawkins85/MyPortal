from types import SimpleNamespace
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


@pytest.mark.anyio("asyncio")
async def test_update_stock_feed_persists_items(monkeypatch):
    xml_payload = """
        <rss>
          <channel>
            <item>
              <StockCode>ABC123</StockCode>
              <ProductName>Widget</ProductName>
              <ProductName2>Widget Plus</ProductName2>
              <RRP>12.50</RRP>
              <CategoryName>Widgets</CategoryName>
              <OnHandChanelNsw>1</OnHandChanelNsw>
              <OnHandChanelQld>2</OnHandChanelQld>
              <OnHandChanelVic>3</OnHandChanelVic>
              <OnHandChanelSa>4</OnHandChanelSa>
              <DBP>10.00</DBP>
              <Weight>0.5</Weight>
              <Length>10</Length>
              <Width>5</Width>
              <Height>3</Height>
              <pubDate>2024-05-01</pubDate>
              <WarrantyLength>12 months</WarrantyLength>
              <Manufacturer>Acme</Manufacturer>
              <ImageUrl>https://example.com/image.jpg</ImageUrl>
            </item>
          </channel>
        </rss>
    """

    requested: dict[str, str] = {}

    class DummyResponse:
        def __init__(self, text: str) -> None:
            self.text = text

        def raise_for_status(self) -> None:
            return None

    class DummyClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "DummyClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:  # type: ignore[override]
            return False

        async def get(self, url: str, follow_redirects: bool = True) -> DummyResponse:
            requested["url"] = url
            return DummyResponse(xml_payload)

    monkeypatch.setattr(products_service.httpx, "AsyncClient", DummyClient)
    monkeypatch.setattr(
        products_service,
        "get_settings",
        lambda: SimpleNamespace(stock_feed_url="https://example.com/feed.xml"),
    )

    mock_replace = AsyncMock()
    monkeypatch.setattr(products_service.stock_feed_repo, "replace_feed", mock_replace)

    count = await products_service.update_stock_feed()

    assert requested["url"] == "https://example.com/feed.xml"
    mock_replace.assert_awaited_once()
    items = mock_replace.await_args.args[0]
    assert len(items) == 1
    item = items[0]
    assert item["sku"] == "ABC123"
    assert item["on_hand_sa"] == 4
    assert item["pub_date"].isoformat() == "2024-05-01"
    assert count == 1


@pytest.mark.anyio("asyncio")
async def test_update_stock_feed_requires_config(monkeypatch):
    monkeypatch.setattr(
        products_service,
        "get_settings",
        lambda: SimpleNamespace(stock_feed_url=None),
    )

    with pytest.raises(ValueError):
        await products_service.update_stock_feed()
