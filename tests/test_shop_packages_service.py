import asyncio
from decimal import Decimal

from app.services import shop_packages as packages_service


def test_load_admin_packages_computes_totals(monkeypatch):
    async def fake_list_packages(filters):
        return [
            {
                "id": 1,
                "name": "Starter bundle",
                "sku": "PKG-1",
                "archived": False,
                "product_count": 2,
            }
        ]

    async def fake_list_items(package_ids):
        return {
            1: [
                {
                    "product_id": 10,
                    "quantity": 2,
                    "product_price": Decimal("5.00"),
                    "product_vip_price": Decimal("4.50"),
                    "product_stock": 6,
                    "product_archived": False,
                },
                {
                    "product_id": 11,
                    "quantity": 1,
                    "product_price": Decimal("3.00"),
                    "product_vip_price": None,
                    "product_stock": 4,
                    "product_archived": False,
                },
            ]
        }

    monkeypatch.setattr(packages_service.shop_repo, "list_packages", fake_list_packages)
    monkeypatch.setattr(
        packages_service.shop_repo,
        "list_package_items_for_packages",
        fake_list_items,
    )

    packages = asyncio.run(packages_service.load_admin_packages())

    assert len(packages) == 1
    package = packages[0]
    assert package["price_total"] == Decimal("13.00")
    assert package["stock_level"] == 3
    assert package["is_restricted"] is False
    assert package["active_item_count"] == 2
    first_item = package["items"][0]
    assert first_item["resolved_product"]["product_id"] == 10
    assert first_item["is_substituted"] is False


def test_load_company_packages_marks_restricted(monkeypatch):
    async def fake_list_packages(filters):
        return [
            {
                "id": 2,
                "name": "VIP bundle",
                "sku": "PKG-2",
                "archived": False,
                "product_count": 1,
            }
        ]

    async def fake_list_items(package_ids):
        return {
            2: [
                {
                    "product_id": 20,
                    "quantity": 1,
                    "product_price": Decimal("9.99"),
                    "product_vip_price": Decimal("8.99"),
                    "product_stock": 5,
                    "product_archived": False,
                }
            ]
        }

    async def fake_get_restricted(*, company_id, product_ids):
        return {20}

    monkeypatch.setattr(packages_service.shop_repo, "list_packages", fake_list_packages)
    monkeypatch.setattr(
        packages_service.shop_repo,
        "list_package_items_for_packages",
        fake_list_items,
    )
    monkeypatch.setattr(
        packages_service.shop_repo,
        "get_restricted_product_ids",
        fake_get_restricted,
    )

    packages = asyncio.run(
        packages_service.load_company_packages(company_id=3, is_vip=True)
    )

    package = packages[0]
    assert package["is_restricted"] is True
    assert package["is_available"] is False
    assert package["stock_level"] == 5
    assert package["price_total"] == Decimal("8.99")
    assert package["items"][0]["resolved_is_restricted"] is True


def test_get_package_detail_returns_enriched(monkeypatch):
    async def fake_get_package(package_id, include_archived=False):
        return {
            "id": package_id,
            "name": "Ops kit",
            "sku": "PKG-OPS",
            "archived": False,
            "product_count": 1,
        }

    async def fake_list_items(package_id):
        return [
            {
                "product_id": 30,
                "quantity": 3,
                "product_price": Decimal("2.00"),
                "product_vip_price": None,
                "product_stock": 12,
                "product_archived": False,
            }
        ]

    monkeypatch.setattr(packages_service.shop_repo, "get_package", fake_get_package)
    monkeypatch.setattr(packages_service.shop_repo, "list_package_items", fake_list_items)

    package = asyncio.run(packages_service.get_package_detail(7))

    assert package is not None
    assert package["price_total"] == Decimal("6.00")
    assert package["stock_level"] == 4
    assert package["is_restricted"] is False
    assert package["active_item_count"] == 1
    assert package["items"][0]["resolved_product"]["product_id"] == 30


def test_load_company_packages_substitutes_when_primary_out_of_stock(monkeypatch):
    async def fake_list_packages(filters):
        return [
            {
                "id": 5,
                "name": "Pro kit",
                "sku": "PKG-5",
                "archived": False,
                "product_count": 1,
            }
        ]

    async def fake_list_items(package_ids):
        return {
            5: [
                {
                    "product_id": 100,
                    "quantity": 2,
                    "product_price": Decimal("5.00"),
                    "product_vip_price": Decimal("4.50"),
                    "product_stock": 0,
                    "product_archived": False,
                    "alternates": [
                        {
                            "product_id": 101,
                            "priority": 0,
                            "product_price": Decimal("6.00"),
                            "product_vip_price": Decimal("5.50"),
                            "product_stock": 10,
                            "product_archived": False,
                            "product_name": "Alt widget",
                            "product_sku": "ALT-101",
                            "product_vendor_sku": None,
                            "product_image_url": None,
                            "product_description": None,
                        }
                    ],
                }
            ]
        }

    async def fake_get_restricted(*, company_id, product_ids):
        return set()

    monkeypatch.setattr(packages_service.shop_repo, "list_packages", fake_list_packages)
    monkeypatch.setattr(
        packages_service.shop_repo,
        "list_package_items_for_packages",
        fake_list_items,
    )
    monkeypatch.setattr(
        packages_service.shop_repo,
        "get_restricted_product_ids",
        fake_get_restricted,
    )

    packages = asyncio.run(
        packages_service.load_company_packages(company_id=None, is_vip=False)
    )

    package = packages[0]
    assert package["is_available"] is True
    assert package["stock_level"] == 5
    assert package["price_total"] == Decimal("12.00")
    resolved_item = package["items"][0]
    assert resolved_item["resolved_product"]["product_id"] == 101
    assert resolved_item["is_substituted"] is True
