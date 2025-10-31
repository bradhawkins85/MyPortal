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


def _build_product_candidate(
    source: dict[str, Any],
    *,
    substitution_type: str,
    priority: int,
) -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "product_id": int(source.get("product_id") or 0),
        "product_name": source.get("product_name"),
        "product_sku": source.get("product_sku"),
        "product_vendor_sku": source.get("product_vendor_sku"),
        "product_image_url": source.get("product_image_url"),
        "product_description": source.get("product_description"),
        "product_archived": bool(source.get("product_archived")),
        "priority": priority,
        "substitution_type": substitution_type,
    }
    price = source.get("product_price")
    candidate["product_price"] = _to_decimal(price)
    vip_price = source.get("product_vip_price")
    candidate["product_vip_price"] = None if vip_price is None else _to_decimal(vip_price)
    stock_value = source.get("product_stock")
    try:
        candidate["product_stock"] = int(stock_value)
    except (TypeError, ValueError):
        candidate["product_stock"] = 0
    alternate_id = source.get("id")
    candidate["alternate_id"] = int(alternate_id) if alternate_id is not None else 0
    return candidate


def _prepare_package_items(
    items: Sequence[dict[str, Any]],
    *,
    restricted_product_ids: set[int],
) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for item in items:
        quantity = int(item.get("quantity") or 0)
        if quantity < 0:
            quantity = 0
        primary_candidate = _build_product_candidate(
            item,
            substitution_type="primary",
            priority=-1,
        )
        alternates = sorted(
            item.get("alternates") or [],
            key=lambda alternate: (
                int(alternate.get("priority") or 0),
                str(alternate.get("product_name") or "").lower(),
            ),
        )
        candidates = [primary_candidate]
        for alternate in alternates:
            candidates.append(
                _build_product_candidate(
                    alternate,
                    substitution_type="alternate",
                    priority=int(alternate.get("priority") or 0),
                )
            )

        fallback = primary_candidate
        selected = None
        for candidate in candidates:
            product_id = int(candidate.get("product_id") or 0)
            archived = bool(candidate.get("product_archived"))
            restricted = product_id in restricted_product_ids
            stock = int(candidate.get("product_stock") or 0)
            if archived or restricted:
                continue
            if quantity <= 0 or stock >= quantity:
                selected = candidate
                break
        if selected is None:
            selected = fallback

        resolved_product_id = int(selected.get("product_id") or 0)
        resolved_archived = bool(selected.get("product_archived"))
        resolved_stock = int(selected.get("product_stock") or 0)
        resolved_restricted = (
            resolved_archived or resolved_product_id in restricted_product_ids
        )
        if quantity <= 0:
            available_per_package = resolved_stock
        else:
            available_per_package = resolved_stock // quantity
        if available_per_package < 0:
            available_per_package = 0

        prepared_item = dict(item)
        prepared_item["quantity"] = quantity
        prepared_item["primary_product"] = primary_candidate
        prepared_item["resolved_product"] = selected
        prepared_item["resolved_product_id"] = resolved_product_id
        prepared_item["resolved_product_source"] = selected.get(
            "substitution_type", "primary"
        )
        prepared_item["resolved_is_restricted"] = resolved_restricted
        prepared_item["available_stock_for_quantity"] = available_per_package
        prepared_item["is_substituted"] = (
            prepared_item["resolved_product_source"] != "primary"
        )
        prepared.append(prepared_item)
    return prepared


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
        quantity = int(item.get("quantity") or 0)
        resolved = item.get("resolved_product") or {}
        if quantity < 0:
            quantity = 0
        resolved_product_id = int(resolved.get("product_id") or 0)
        resolved_archived = bool(resolved.get("product_archived"))
        resolved_restricted = bool(item.get("resolved_is_restricted"))
        base_price = resolved.get("product_price")
        vip_price = resolved.get("product_vip_price")
        stock = int(resolved.get("product_stock") or 0)

        if (
            resolved_archived
            or resolved_restricted
            or (restricted_product_ids and resolved_product_id in restricted_product_ids)
        ):
            restricted = True

        price_source = vip_price if is_vip and vip_price is not None else base_price
        if price_source is not None:
            running_total += _to_decimal(price_source) * Decimal(quantity)

        if quantity <= 0:
            stock_levels.append(stock)
        elif quantity > 0:
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
        prepared_items = _prepare_package_items(
            package_items,
            restricted_product_ids=set(),
        )
        price_total, stock_level, restricted = _compute_package_metrics(
            prepared_items,
            is_vip=False,
            restricted_product_ids=set(),
        )
        enriched.append(
            {
                **package,
                "items": prepared_items,
                "price_total": price_total,
                "stock_level": stock_level,
                "is_restricted": restricted,
                "active_item_count": sum(
                    1
                    for item in prepared_items
                    if item.get("resolved_product")
                    and not bool(
                        (item.get("resolved_product") or {}).get("product_archived")
                    )
                ),
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
            for alternate in item.get("alternates") or []:
                product_ids.add(int(alternate.get("product_id") or 0))

    restricted_products: set[int] = set()
    if company_id is not None and product_ids:
        restricted_products = await shop_repo.get_restricted_product_ids(
            company_id=company_id,
            product_ids=product_ids,
        )

    visible_packages: list[dict[str, Any]] = []
    for package in packages:
        package_items = items_map.get(package["id"], [])
        prepared_items = _prepare_package_items(
            package_items,
            restricted_product_ids=restricted_products,
        )
        price_total, stock_level, restricted = _compute_package_metrics(
            prepared_items,
            is_vip=is_vip,
            restricted_product_ids=restricted_products,
        )
        valid_resolutions = all(
            int(item.get("resolved_product_id") or 0) > 0
            or int(item.get("quantity") or 0) == 0
            for item in prepared_items
        )
        is_available = (
            not package.get("archived")
            and not restricted
            and stock_level > 0
            and bool(prepared_items)
            and valid_resolutions
        )
        visible_packages.append(
            {
                **package,
                "items": prepared_items,
                "price_total": price_total,
                "stock_level": stock_level,
                "is_restricted": restricted,
                "is_available": is_available,
                "active_item_count": sum(
                    1
                    for item in prepared_items
                    if item.get("resolved_product")
                    and not bool(
                        (item.get("resolved_product") or {}).get("product_archived")
                    )
                ),
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
    prepared_items = _prepare_package_items(
        items,
        restricted_product_ids=set(),
    )
    price_total, stock_level, restricted = _compute_package_metrics(
        prepared_items,
        is_vip=False,
        restricted_product_ids=set(),
    )
    return {
        **package,
        "items": prepared_items,
        "price_total": price_total,
        "stock_level": stock_level,
        "is_restricted": restricted,
        "active_item_count": sum(
            1
            for item in prepared_items
            if item.get("resolved_product")
            and not bool((item.get("resolved_product") or {}).get("product_archived"))
        ),
    }
