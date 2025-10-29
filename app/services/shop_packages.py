from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Sequence

from app.repositories import shop as shop_repo


def _quantise_price(value: Decimal) -> Decimal:
    """Normalise prices to two decimal places for consistent display."""

    if value is None:
        return Decimal("0.00")
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _to_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _compute_package_metrics(
    items: Sequence[dict[str, Any]],
    *,
    is_vip: bool,
    restricted_product_ids: set[int],
) -> tuple[Decimal, int, bool]:
    running_total = Decimal("0")
    stock_levels: list[int] = []
    restricted = False

    for item in items:
        product_id = int(item.get("product_id") or 0)
        quantity = int(item.get("quantity") or 0)
        product_archived = bool(item.get("product_archived"))
        base_price = item.get("product_price")
        vip_price = item.get("product_vip_price")
        stock = int(item.get("product_stock") or 0)

        if product_archived:
            restricted = True
        if restricted_product_ids and product_id in restricted_product_ids:
            restricted = True

        if quantity < 0:
            quantity = 0

        price_source = vip_price if is_vip and vip_price is not None else base_price
        if price_source is not None:
            running_total += _to_decimal(price_source) * Decimal(quantity)

        if quantity <= 0:
            stock_levels.append(stock)
        else:
            stock_levels.append(stock // quantity)

    if not stock_levels:
        stock_level = 0
    else:
        stock_level = min(stock_levels)
        if stock_level < 0:
            stock_level = 0

    return _quantise_price(running_total), stock_level, restricted


async def load_admin_packages(*, include_archived: bool = False) -> list[dict[str, Any]]:
    filters = shop_repo.PackageFilters(include_archived=include_archived)
    packages = await shop_repo.list_packages(filters)
    package_ids = [package["id"] for package in packages]
    items_map = await shop_repo.list_package_items_for_packages(package_ids)

    enriched: list[dict[str, Any]] = []
    for package in packages:
        package_items = items_map.get(package["id"], [])
        price_total, stock_level, restricted = _compute_package_metrics(
            package_items,
            is_vip=False,
            restricted_product_ids=set(),
        )
        enriched.append(
            {
                **package,
                "items": package_items,
                "price_total": price_total,
                "stock_level": stock_level,
                "is_restricted": restricted,
                "active_item_count": sum(1 for item in package_items if not item.get("product_archived")),
            }
        )
    return enriched


async def load_company_packages(
    *,
    company_id: int | None,
    is_vip: bool,
) -> list[dict[str, Any]]:
    filters = shop_repo.PackageFilters(include_archived=False)
    packages = await shop_repo.list_packages(filters)
    package_ids = [package["id"] for package in packages]
    items_map = await shop_repo.list_package_items_for_packages(package_ids)

    product_ids: set[int] = set()
    for package_items in items_map.values():
        for item in package_items:
            product_ids.add(int(item.get("product_id") or 0))

    restricted_products: set[int] = set()
    if company_id is not None and product_ids:
        restricted_products = await shop_repo.get_restricted_product_ids(
            company_id=company_id,
            product_ids=product_ids,
        )

    visible_packages: list[dict[str, Any]] = []
    for package in packages:
        package_items = items_map.get(package["id"], [])
        price_total, stock_level, restricted = _compute_package_metrics(
            package_items,
            is_vip=is_vip,
            restricted_product_ids=restricted_products,
        )
        is_available = (
            not package.get("archived")
            and not restricted
            and stock_level > 0
            and bool(package_items)
        )
        visible_packages.append(
            {
                **package,
                "items": package_items,
                "price_total": price_total,
                "stock_level": stock_level,
                "is_restricted": restricted,
                "is_available": is_available,
                "active_item_count": sum(1 for item in package_items if not item.get("product_archived")),
            }
        )
    return visible_packages


async def get_package_detail(
    package_id: int,
    *,
    include_archived: bool = False,
) -> dict[str, Any] | None:
    package = await shop_repo.get_package(package_id, include_archived=include_archived)
    if not package:
        return None
    items = await shop_repo.list_package_items(package_id)
    price_total, stock_level, restricted = _compute_package_metrics(
        items,
        is_vip=False,
        restricted_product_ids=set(),
    )
    return {
        **package,
        "items": items,
        "price_total": price_total,
        "stock_level": stock_level,
        "is_restricted": restricted,
        "active_item_count": sum(1 for item in items if not item.get("product_archived")),
    }
