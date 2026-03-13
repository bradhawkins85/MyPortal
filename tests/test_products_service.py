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
    monkeypatch.setattr(
        products_service.shop_repo, "sync_pending_optional_accessories", AsyncMock(return_value=0)
    )
    monkeypatch.setattr(
        products_service.shop_repo, "list_pending_optional_accessories", AsyncMock(return_value=[])
    )

    await products_service.update_products_from_feed()

    # Only MAIN1 has opt_accessori, so only its cross-sells should be set
    mock_replace.assert_awaited_once_with(1, cross_sell_ids=[2, 3])
    products_service.shop_repo.get_product_ids_by_skus.assert_awaited_once_with(
        ["ACC1", "ACC2"]
    )


@pytest.mark.anyio("asyncio")
async def test_update_products_from_feed_upserts_only_existing_feed_items(monkeypatch):
    """Only products that already exist in the store are upserted from the stock feed."""
    feed_items = [
        _make_feed_item("NEW-SKU"),
        _make_feed_item("EXISTING-SKU"),
    ]

    monkeypatch.setattr(
        products_service.stock_feed_repo,
        "list_all_items",
        AsyncMock(return_value=feed_items),
    )

    async def fake_get_by_sku(sku, **kwargs):
        if sku == "EXISTING-SKU":
            return {"id": 1, "image_url": None}
        return None

    monkeypatch.setattr(products_service.shop_repo, "get_product_by_sku", fake_get_by_sku)
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
    monkeypatch.setattr(
        products_service.shop_repo, "sync_pending_optional_accessories", AsyncMock(return_value=0)
    )
    monkeypatch.setattr(
        products_service.shop_repo, "list_pending_optional_accessories", AsyncMock(return_value=[])
    )

    await products_service.update_products_from_feed()

    assert mock_upsert.await_count == 1


@pytest.mark.anyio("asyncio")
async def test_update_products_from_feed_cross_sells_resolved_for_existing_products_only(
    monkeypatch,
):
    """Cross-sells are only applied for products already present in the store."""
    feed_items = [
        _make_feed_item("MAIN1", opt_accessori="ACC1"),
        _make_feed_item("ACC1"),
    ]

    async def fake_get_by_sku(sku, **kwargs):
        if sku == "MAIN1":
            return {"id": 1, "image_url": None}
        return None

    monkeypatch.setattr(
        products_service.stock_feed_repo,
        "list_all_items",
        AsyncMock(return_value=feed_items),
    )
    monkeypatch.setattr(products_service.shop_repo, "get_product_by_sku", fake_get_by_sku)
    monkeypatch.setattr(products_service.shop_repo, "upsert_product_from_feed", AsyncMock())
    monkeypatch.setattr(
        products_service.shop_repo, "get_product_ids_by_skus", AsyncMock(return_value=[99])
    )
    mock_replace = AsyncMock()
    monkeypatch.setattr(
        products_service.shop_repo, "replace_product_recommendations", mock_replace
    )
    monkeypatch.setattr(
        products_service, "_get_or_create_category_hierarchy", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        products_service.shop_repo, "sync_pending_optional_accessories", AsyncMock(return_value=0)
    )
    monkeypatch.setattr(
        products_service.shop_repo, "list_pending_optional_accessories", AsyncMock(return_value=[])
    )

    await products_service.update_products_from_feed()

    mock_replace.assert_awaited_once_with(1, cross_sell_ids=[99])


@pytest.mark.anyio("asyncio")
async def test_update_products_from_feed_uses_vendor_sku_for_cross_sells(monkeypatch):
    """Cross-sell targets are resolved by vendor_sku when internal sku differs."""
    feed_items = [
        _make_feed_item("NHU-R5AC-LITE", opt_accessori="NHU-POE-24-12WG"),
    ]

    monkeypatch.setattr(
        products_service.stock_feed_repo,
        "list_all_items",
        AsyncMock(return_value=feed_items),
    )
    monkeypatch.setattr(
        products_service.shop_repo,
        "get_product_by_sku",
        AsyncMock(return_value={"id": 10, "image_url": None}),
    )
    monkeypatch.setattr(products_service.shop_repo, "upsert_product_from_feed", AsyncMock())
    monkeypatch.setattr(
        products_service.shop_repo,
        "get_product_ids_by_skus",
        AsyncMock(return_value=[55]),
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
    monkeypatch.setattr(
        products_service.shop_repo, "sync_pending_optional_accessories", AsyncMock(return_value=0)
    )
    monkeypatch.setattr(
        products_service.shop_repo, "list_pending_optional_accessories", AsyncMock(return_value=[])
    )

    await products_service.update_products_from_feed()

    mock_replace.assert_awaited_once_with(10, cross_sell_ids=[55])
    products_service.shop_repo.get_product_ids_by_skus.assert_awaited_once_with(
        ["NHU-POE-24-12WG"]
    )


@pytest.mark.anyio("asyncio")
async def test_update_products_from_feed_does_not_download_images_for_new_products(
    monkeypatch,
):
    """Images must not be downloaded for products that do not yet exist in the store."""
    feed_item = {**_make_feed_item("NEW-SKU"), "image_url": "https://example.com/img.jpg"}

    monkeypatch.setattr(
        products_service.stock_feed_repo,
        "list_all_items",
        AsyncMock(return_value=[feed_item]),
    )
    monkeypatch.setattr(
        products_service.shop_repo,
        "get_product_by_sku",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(products_service.shop_repo, "upsert_product_from_feed", AsyncMock())
    monkeypatch.setattr(products_service.shop_repo, "replace_product_recommendations", AsyncMock())
    monkeypatch.setattr(
        products_service, "_get_or_create_category_hierarchy", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        products_service.shop_repo, "sync_pending_optional_accessories", AsyncMock(return_value=0)
    )
    monkeypatch.setattr(
        products_service.shop_repo, "list_pending_optional_accessories", AsyncMock(return_value=[])
    )

    mock_download = AsyncMock(return_value="/uploads/shop/some.jpg")
    monkeypatch.setattr(products_service, "_download_product_image", mock_download)

    await products_service.update_products_from_feed()

    mock_download.assert_not_called()


@pytest.mark.anyio("asyncio")
async def test_update_products_from_feed_downloads_image_for_existing_product_without_image(
    monkeypatch,
):
    """Images should be downloaded for store products that do not yet have an image."""
    feed_item = {**_make_feed_item("EXISTING-SKU"), "image_url": "https://example.com/img.jpg"}

    monkeypatch.setattr(
        products_service.stock_feed_repo,
        "list_all_items",
        AsyncMock(return_value=[feed_item]),
    )
    monkeypatch.setattr(
        products_service.shop_repo,
        "get_product_by_sku",
        AsyncMock(return_value={"id": 7, "image_url": None}),
    )
    monkeypatch.setattr(products_service.shop_repo, "upsert_product_from_feed", AsyncMock())
    monkeypatch.setattr(products_service.shop_repo, "replace_product_recommendations", AsyncMock())
    monkeypatch.setattr(
        products_service, "_get_or_create_category_hierarchy", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        products_service.shop_repo, "sync_pending_optional_accessories", AsyncMock(return_value=0)
    )
    monkeypatch.setattr(
        products_service.shop_repo, "list_pending_optional_accessories", AsyncMock(return_value=[])
    )

    mock_download = AsyncMock(return_value="/uploads/shop/downloaded.jpg")
    monkeypatch.setattr(products_service, "_download_product_image", mock_download)

    await products_service.update_products_from_feed()

    mock_download.assert_awaited_once_with("https://example.com/img.jpg")


@pytest.mark.anyio("asyncio")
async def test_process_feed_item_skips_image_download_for_new_product_when_disabled(
    monkeypatch,
):
    """_process_feed_item with download_image_if_new=False must not download images for new products."""
    item = {**_make_feed_item("NEW-SKU"), "image_url": "https://example.com/img.jpg"}

    monkeypatch.setattr(products_service.shop_repo, "upsert_product_from_feed", AsyncMock())
    monkeypatch.setattr(
        products_service.shop_repo,
        "get_product_by_sku",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        products_service, "_get_or_create_category_hierarchy", AsyncMock(return_value=None)
    )

    mock_download = AsyncMock(return_value="/uploads/shop/some.jpg")
    monkeypatch.setattr(products_service, "_download_product_image", mock_download)

    result = await products_service._process_feed_item(
        item, None, download_image_if_new=False
    )

    assert result is True
    mock_download.assert_not_called()


@pytest.mark.anyio("asyncio")
async def test_process_feed_item_downloads_image_for_new_product_when_enabled(
    monkeypatch,
):
    """_process_feed_item with download_image_if_new=True (default) downloads images for new products."""
    item = {**_make_feed_item("NEW-SKU"), "image_url": "https://example.com/img.jpg"}

    monkeypatch.setattr(products_service.shop_repo, "upsert_product_from_feed", AsyncMock())
    monkeypatch.setattr(
        products_service.shop_repo,
        "get_product_by_sku",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        products_service, "_get_or_create_category_hierarchy", AsyncMock(return_value=None)
    )

    mock_download = AsyncMock(return_value="/uploads/shop/some.jpg")
    monkeypatch.setattr(products_service, "_download_product_image", mock_download)

    result = await products_service._process_feed_item(item, None)

    assert result is True
    mock_download.assert_awaited_once_with("https://example.com/img.jpg")


# ---------------------------------------------------------------------------
# _download_pending_accessory_images tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_download_pending_accessory_images_downloads_external_urls(monkeypatch):
    """External image URLs in the pending accessories table should be downloaded."""
    accessories = [
        {
            "id": 1,
            "sku": "ACC-001",
            "product_name": "Cable",
            "image_url": "https://vendor.com/cable.jpg",
        },
    ]
    monkeypatch.setattr(
        products_service.shop_repo,
        "list_pending_optional_accessories",
        AsyncMock(return_value=accessories),
    )
    mock_update = AsyncMock()
    monkeypatch.setattr(
        products_service.shop_repo,
        "update_pending_accessory_image_url",
        mock_update,
    )
    mock_download = AsyncMock(return_value="/uploads/shop/cable.jpg")
    monkeypatch.setattr(products_service, "_download_product_image", mock_download)

    await products_service._download_pending_accessory_images()

    mock_download.assert_awaited_once_with("https://vendor.com/cable.jpg")
    mock_update.assert_awaited_once_with("ACC-001", "/uploads/shop/cable.jpg")


@pytest.mark.anyio("asyncio")
async def test_download_pending_accessory_images_skips_local_paths(monkeypatch):
    """Pending accessories whose image_url already points to a local path are skipped."""
    accessories = [
        {
            "id": 2,
            "sku": "ACC-002",
            "product_name": "Adapter",
            "image_url": "/uploads/shop/adapter.jpg",
        },
    ]
    monkeypatch.setattr(
        products_service.shop_repo,
        "list_pending_optional_accessories",
        AsyncMock(return_value=accessories),
    )
    mock_update = AsyncMock()
    monkeypatch.setattr(
        products_service.shop_repo,
        "update_pending_accessory_image_url",
        mock_update,
    )
    mock_download = AsyncMock(return_value="/uploads/shop/adapter.jpg")
    monkeypatch.setattr(products_service, "_download_product_image", mock_download)

    await products_service._download_pending_accessory_images()

    mock_download.assert_not_called()
    mock_update.assert_not_called()


@pytest.mark.anyio("asyncio")
async def test_download_pending_accessory_images_skips_when_no_url(monkeypatch):
    """Pending accessories with no image URL are silently skipped."""
    accessories = [
        {"id": 3, "sku": "ACC-003", "product_name": "Hub", "image_url": None},
    ]
    monkeypatch.setattr(
        products_service.shop_repo,
        "list_pending_optional_accessories",
        AsyncMock(return_value=accessories),
    )
    mock_update = AsyncMock()
    monkeypatch.setattr(
        products_service.shop_repo,
        "update_pending_accessory_image_url",
        mock_update,
    )
    mock_download = AsyncMock()
    monkeypatch.setattr(products_service, "_download_product_image", mock_download)

    await products_service._download_pending_accessory_images()

    mock_download.assert_not_called()
    mock_update.assert_not_called()


@pytest.mark.anyio("asyncio")
async def test_download_pending_accessory_images_skips_update_when_download_fails(monkeypatch):
    """If the image download fails (returns None), the DB record is not updated."""
    accessories = [
        {
            "id": 4,
            "sku": "ACC-004",
            "product_name": "Dongle",
            "image_url": "https://vendor.com/dongle.jpg",
        },
    ]
    monkeypatch.setattr(
        products_service.shop_repo,
        "list_pending_optional_accessories",
        AsyncMock(return_value=accessories),
    )
    mock_update = AsyncMock()
    monkeypatch.setattr(
        products_service.shop_repo,
        "update_pending_accessory_image_url",
        mock_update,
    )
    monkeypatch.setattr(products_service, "_download_product_image", AsyncMock(return_value=None))

    await products_service._download_pending_accessory_images()

    mock_update.assert_not_called()


@pytest.mark.anyio("asyncio")
async def test_update_products_from_feed_downloads_pending_accessory_images(monkeypatch):
    """update_products_from_feed downloads images for pending accessories with external URLs."""
    feed_item = _make_feed_item("EXISTING-SKU")
    monkeypatch.setattr(
        products_service.stock_feed_repo,
        "list_all_items",
        AsyncMock(return_value=[feed_item]),
    )
    monkeypatch.setattr(
        products_service.shop_repo,
        "get_product_by_sku",
        AsyncMock(return_value={"id": 1, "image_url": None}),
    )
    monkeypatch.setattr(products_service.shop_repo, "upsert_product_from_feed", AsyncMock())
    monkeypatch.setattr(products_service.shop_repo, "replace_product_recommendations", AsyncMock())
    monkeypatch.setattr(
        products_service, "_get_or_create_category_hierarchy", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        products_service.shop_repo, "sync_pending_optional_accessories", AsyncMock(return_value=1)
    )
    pending = [
        {
            "id": 10,
            "sku": "PENDING-ACC",
            "product_name": "Pending Accessory",
            "image_url": "https://vendor.com/pending.jpg",
        }
    ]
    monkeypatch.setattr(
        products_service.shop_repo,
        "list_pending_optional_accessories",
        AsyncMock(return_value=pending),
    )
    mock_update = AsyncMock()
    monkeypatch.setattr(
        products_service.shop_repo,
        "update_pending_accessory_image_url",
        mock_update,
    )
    mock_download = AsyncMock(return_value="/uploads/shop/pending.jpg")
    monkeypatch.setattr(products_service, "_download_product_image", mock_download)

    await products_service.update_products_from_feed()

    mock_download.assert_awaited_once_with("https://vendor.com/pending.jpg")
    mock_update.assert_awaited_once_with("PENDING-ACC", "/uploads/shop/pending.jpg")
