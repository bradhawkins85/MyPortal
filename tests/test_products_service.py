from types import SimpleNamespace
from unittest.mock import AsyncMock, call

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


@pytest.mark.anyio("asyncio")
async def test_parse_feed_item_includes_opt_accessori():
    """OptAccessori in XML feed is parsed into the opt_accessori key."""
    xml_payload = """
        <rss>
          <channel>
            <item>
              <StockCode>MAIN1</StockCode>
              <ProductName>Main Product</ProductName>
              <OptAccessori>ACC1, ACC2, ACC3</OptAccessori>
            </item>
          </channel>
        </rss>
    """
    items = products_service._parse_stock_feed_xml(xml_payload)
    assert len(items) == 1
    assert items[0]["opt_accessori"] == "ACC1, ACC2, ACC3"


@pytest.mark.anyio("asyncio")
async def test_parse_feed_item_opt_accessori_absent():
    """When OptAccessori is absent from XML, opt_accessori key is None."""
    xml_payload = """
        <rss>
          <channel>
            <item>
              <StockCode>MAIN1</StockCode>
              <ProductName>Main Product</ProductName>
            </item>
          </channel>
        </rss>
    """
    items = products_service._parse_stock_feed_xml(xml_payload)
    assert len(items) == 1
    assert items[0]["opt_accessori"] is None


@pytest.mark.anyio("asyncio")
async def test_process_feed_item_links_cross_sells_from_opt_accessori(monkeypatch):
    """When opt_accessori is present, matching store products are linked as cross-sells."""
    item = {
        "sku": "MAIN1",
        "product_name": "Main Product",
        "product_name2": None,
        "rrp": None,
        "category_name": None,
        "on_hand_nsw": 0,
        "on_hand_qld": 0,
        "on_hand_vic": 0,
        "on_hand_sa": 0,
        "dbp": None,
        "weight": None,
        "length": None,
        "width": None,
        "height": None,
        "pub_date": None,
        "warranty_length": None,
        "manufacturer": None,
        "image_url": None,
        "opt_accessori": "ACC1, ACC2",
    }

    monkeypatch.setattr(products_service.shop_repo, "upsert_product_from_feed", AsyncMock())
    monkeypatch.setattr(
        products_service.shop_repo,
        "get_product_ids_by_skus",
        AsyncMock(return_value=[10, 11]),
    )
    monkeypatch.setattr(
        products_service.shop_repo,
        "get_product_by_sku",
        AsyncMock(return_value={"id": 5, "image_url": ""}),
    )
    mock_replace = AsyncMock()
    monkeypatch.setattr(
        products_service.shop_repo,
        "replace_product_recommendations",
        mock_replace,
    )
    monkeypatch.setattr(products_service, "_get_or_create_category_hierarchy", AsyncMock(return_value=None))

    result = await products_service._process_feed_item(item, None)

    assert result is True
    products_service.shop_repo.get_product_ids_by_skus.assert_awaited_once_with(
        ["ACC1", "ACC2"]
    )
    mock_replace.assert_awaited_once_with(5, cross_sell_ids=[10, 11])


@pytest.mark.anyio("asyncio")
async def test_process_feed_item_skips_cross_sells_when_opt_accessori_absent(monkeypatch):
    """When opt_accessori is absent, cross-sells are not updated."""
    item = {
        "sku": "MAIN1",
        "product_name": "Main Product",
        "product_name2": None,
        "rrp": None,
        "category_name": None,
        "on_hand_nsw": 0,
        "on_hand_qld": 0,
        "on_hand_vic": 0,
        "on_hand_sa": 0,
        "dbp": None,
        "weight": None,
        "length": None,
        "width": None,
        "height": None,
        "pub_date": None,
        "warranty_length": None,
        "manufacturer": None,
        "image_url": None,
        "opt_accessori": None,
    }

    monkeypatch.setattr(products_service.shop_repo, "upsert_product_from_feed", AsyncMock())
    monkeypatch.setattr(
        products_service.shop_repo,
        "get_product_by_sku",
        AsyncMock(return_value={"id": 5, "image_url": ""}),
    )
    mock_get_ids = AsyncMock(return_value=[])
    monkeypatch.setattr(products_service.shop_repo, "get_product_ids_by_skus", mock_get_ids)
    mock_replace = AsyncMock()
    monkeypatch.setattr(
        products_service.shop_repo,
        "replace_product_recommendations",
        mock_replace,
    )
    monkeypatch.setattr(products_service, "_get_or_create_category_hierarchy", AsyncMock(return_value=None))

    result = await products_service._process_feed_item(item, None)

    assert result is True
    mock_get_ids.assert_not_called()
    mock_replace.assert_not_called()


@pytest.mark.anyio("asyncio")
async def test_process_feed_item_empty_opt_accessori_preserves_cross_sells(monkeypatch):
    """When opt_accessori is an empty string, existing cross-sells are left untouched."""
    item = {
        "sku": "MAIN1",
        "product_name": "Main Product",
        "product_name2": None,
        "rrp": None,
        "category_name": None,
        "on_hand_nsw": 0,
        "on_hand_qld": 0,
        "on_hand_vic": 0,
        "on_hand_sa": 0,
        "dbp": None,
        "weight": None,
        "length": None,
        "width": None,
        "height": None,
        "pub_date": None,
        "warranty_length": None,
        "manufacturer": None,
        "image_url": None,
        "opt_accessori": "",
    }

    monkeypatch.setattr(products_service.shop_repo, "upsert_product_from_feed", AsyncMock())
    mock_get_ids = AsyncMock(return_value=[])
    monkeypatch.setattr(products_service.shop_repo, "get_product_ids_by_skus", mock_get_ids)
    monkeypatch.setattr(
        products_service.shop_repo,
        "get_product_by_sku",
        AsyncMock(return_value={"id": 5, "image_url": ""}),
    )
    mock_replace = AsyncMock()
    monkeypatch.setattr(
        products_service.shop_repo,
        "replace_product_recommendations",
        mock_replace,
    )
    monkeypatch.setattr(products_service, "_get_or_create_category_hierarchy", AsyncMock(return_value=None))

    result = await products_service._process_feed_item(item, None)

    assert result is True
    mock_get_ids.assert_not_called()
    mock_replace.assert_not_called()


@pytest.mark.anyio("asyncio")
async def test_update_stock_feed_persists_opt_accessori(monkeypatch):
    """opt_accessori from XML is included in the items passed to replace_feed."""
    xml_payload = """
        <rss>
          <channel>
            <item>
              <StockCode>ABC123</StockCode>
              <ProductName>Widget</ProductName>
              <OptAccessori>ACC1,ACC2</OptAccessori>
            </item>
          </channel>
        </rss>
    """

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

    assert count == 1
    items = mock_replace.await_args.args[0]
    assert items[0]["opt_accessori"] == "ACC1,ACC2"


def _make_feed_item(sku: str, opt_accessori: str | None = None) -> dict:
    return {
        "sku": sku,
        "product_name": f"Product {sku}",
        "product_name2": None,
        "rrp": None,
        "category_name": None,
        "on_hand_nsw": 0,
        "on_hand_qld": 0,
        "on_hand_vic": 0,
        "on_hand_sa": 0,
        "dbp": None,
        "weight": None,
        "length": None,
        "width": None,
        "height": None,
        "pub_date": None,
        "warranty_length": None,
        "manufacturer": None,
        "image_url": None,
        "opt_accessori": opt_accessori,
    }


@pytest.mark.anyio("asyncio")
async def test_update_products_from_feed_sets_cross_sells(monkeypatch):
    """Cross-sell associations from opt_accessori are applied during update_products_from_feed."""
    feed_items = [
        _make_feed_item("MAIN1", opt_accessori="ACC1,ACC2"),
        _make_feed_item("ACC1"),
        _make_feed_item("ACC2"),
    ]

    monkeypatch.setattr(
        products_service.stock_feed_repo,
        "list_all_items",
        AsyncMock(return_value=feed_items),
    )
    monkeypatch.setattr(
        products_service.shop_repo,
        "get_product_by_sku",
        AsyncMock(return_value={"id": 1, "image_url": None}),
    )
    monkeypatch.setattr(products_service.shop_repo, "upsert_product_from_feed", AsyncMock())
    monkeypatch.setattr(
        products_service.shop_repo,
        "get_product_ids_by_skus",
        AsyncMock(return_value=[2, 3]),
    )
    mock_replace = AsyncMock()
    monkeypatch.setattr(
        products_service.shop_repo,
        "replace_product_recommendations",
        mock_replace,
    )
    monkeypatch.setattr(
        products_service, "_get_or_create_category_hierarchy", AsyncMock(return_value=None)
    )

    await products_service.update_products_from_feed()

    # Only MAIN1 has opt_accessori, so only its cross-sells should be set
    mock_replace.assert_awaited_once_with(1, cross_sell_ids=[2, 3])
    products_service.shop_repo.get_product_ids_by_skus.assert_awaited_once_with(
        ["ACC1", "ACC2"]
    )


@pytest.mark.anyio("asyncio")
async def test_update_products_from_feed_upserts_all_feed_items(monkeypatch):
    """All items in the stock feed are upserted, not just those already in shop_products."""
    feed_items = [
        _make_feed_item("NEW-SKU"),
        _make_feed_item("EXISTING-SKU"),
    ]

    monkeypatch.setattr(
        products_service.stock_feed_repo,
        "list_all_items",
        AsyncMock(return_value=feed_items),
    )
    monkeypatch.setattr(
        products_service.shop_repo,
        "get_product_by_sku",
        AsyncMock(return_value=None),
    )
    mock_upsert = AsyncMock()
    monkeypatch.setattr(products_service.shop_repo, "upsert_product_from_feed", mock_upsert)
    monkeypatch.setattr(
        products_service.shop_repo,
        "replace_product_recommendations",
        AsyncMock(),
    )
    monkeypatch.setattr(
        products_service, "_get_or_create_category_hierarchy", AsyncMock(return_value=None)
    )

    await products_service.update_products_from_feed()

    # Both feed items should have been upserted (two-pass first loop)
    assert mock_upsert.await_count == 2


@pytest.mark.anyio("asyncio")
async def test_update_products_from_feed_cross_sells_resolved_after_all_upserts(
    monkeypatch,
):
    """Cross-sell target products are resolved after ALL feed items have been upserted.

    This ensures a product referenced in opt_accessori is available in shop_products
    even if it appears later in the feed than the product that references it.
    """
    # MAIN1 appears before ACC1 in the feed
    feed_items = [
        _make_feed_item("MAIN1", opt_accessori="ACC1"),
        _make_feed_item("ACC1"),
    ]

    upsert_call_order: list[str] = []

    async def fake_upsert(**kwargs):
        upsert_call_order.append(kwargs["sku"])

    ids_call_order: list[list[str]] = []

    async def fake_get_ids(skus):
        ids_call_order.append(list(skus))
        return [99]

    product_lookup: dict[str, dict] = {
        "MAIN1": {"id": 1, "image_url": None},
        "ACC1": {"id": 99, "image_url": None},
    }

    async def fake_get_by_sku(sku, **kwargs):
        return product_lookup.get(sku)

    monkeypatch.setattr(
        products_service.stock_feed_repo,
        "list_all_items",
        AsyncMock(return_value=feed_items),
    )
    monkeypatch.setattr(
        products_service.shop_repo, "get_product_by_sku", fake_get_by_sku
    )
    monkeypatch.setattr(products_service.shop_repo, "upsert_product_from_feed", fake_upsert)
    monkeypatch.setattr(
        products_service.shop_repo, "get_product_ids_by_skus", fake_get_ids
    )
    mock_replace = AsyncMock()
    monkeypatch.setattr(
        products_service.shop_repo, "replace_product_recommendations", mock_replace
    )
    monkeypatch.setattr(
        products_service, "_get_or_create_category_hierarchy", AsyncMock(return_value=None)
    )

    await products_service.update_products_from_feed()

    # Both products should be upserted in the first pass
    assert set(upsert_call_order) == {"MAIN1", "ACC1"}
    # Cross-sell lookup for ACC1 happens only after both are upserted
    assert ids_call_order == [["ACC1"]]
    # Cross-sell is set for MAIN1 pointing to ACC1 (id=99)
    mock_replace.assert_awaited_once_with(1, cross_sell_ids=[99])


@pytest.mark.anyio("asyncio")
async def test_update_products_from_feed_uses_vendor_sku_for_cross_sells(monkeypatch):
    """Cross-sells resolve even when the accessory product has a custom internal sku.

    opt_accessori contains the vendor's StockCode.  When a store product was created
    with a different internal sku but has vendor_sku equal to the StockCode, the
    get_product_ids_by_skus query must still find it (via vendor_sku).
    """
    feed_items = [
        _make_feed_item("NHU-R5AC-LITE", opt_accessori="NHU-POE-24-12WG"),
        _make_feed_item("NHU-POE-24-12WG"),
    ]

    monkeypatch.setattr(
        products_service.stock_feed_repo,
        "list_all_items",
        AsyncMock(return_value=feed_items),
    )
    # Simulate the accessory being found via vendor_sku lookup
    monkeypatch.setattr(
        products_service.shop_repo,
        "get_product_ids_by_skus",
        AsyncMock(return_value=[55]),
    )
    monkeypatch.setattr(
        products_service.shop_repo,
        "get_product_by_sku",
        AsyncMock(return_value={"id": 10, "image_url": None}),
    )
    monkeypatch.setattr(products_service.shop_repo, "upsert_product_from_feed", AsyncMock())
    mock_replace = AsyncMock()
    monkeypatch.setattr(
        products_service.shop_repo,
        "replace_product_recommendations",
        mock_replace,
    )
    monkeypatch.setattr(
        products_service, "_get_or_create_category_hierarchy", AsyncMock(return_value=None)
    )

    await products_service.update_products_from_feed()

    # Cross-sell should be set using whatever ID get_product_ids_by_skus returned
    mock_replace.assert_awaited_once_with(10, cross_sell_ids=[55])
    products_service.shop_repo.get_product_ids_by_skus.assert_awaited_once_with(
        ["NHU-POE-24-12WG"]
    )
