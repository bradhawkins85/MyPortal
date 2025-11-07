from __future__ import annotations

from dataclasses import dataclass
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Sequence

import aiomysql

from app.core.database import db


@dataclass(slots=True)
class ProductFilters:
    include_archived: bool = False
    company_id: int | None = None
    category_id: int | None = None
    category_ids: list[int] | None = None
    search_term: str | None = None


@dataclass(slots=True)
class PackageFilters:
    include_archived: bool = False
    search_term: str | None = None


async def list_categories() -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT id, name, parent_id, display_order 
        FROM shop_categories 
        ORDER BY name
        """
    )
    categories = [
        {
            "id": int(row["id"]), 
            "name": row["name"],
            "parent_id": _coerce_optional_int(row.get("parent_id")),
            "display_order": _coerce_int(row.get("display_order"), default=0),
        }
        for row in rows
    ]
    
    # Build hierarchical structure
    parent_map: dict[int | None, list[dict[str, Any]]] = {}
    for category in categories:
        parent_id = category.get("parent_id")
        parent_map.setdefault(parent_id, []).append(category)
    
    # Sort all children alphabetically by name
    for children_list in parent_map.values():
        children_list.sort(key=lambda c: c["name"].lower())
    
    # Attach children to parents
    for category in categories:
        category_id = category["id"]
        category["children"] = parent_map.get(category_id, [])
    
    # Return only top-level categories (those without parents), already sorted alphabetically
    return parent_map.get(None, [])


async def list_all_categories_flat() -> list[dict[str, Any]]:
    """List all categories in a flat structure for admin purposes.
    
    Returns categories ordered alphabetically with children grouped under their parents.
    Parent categories are sorted alphabetically, and each parent's children are also
    sorted alphabetically and appear immediately after their parent. This handles
    all levels of nesting (grandchildren, great-grandchildren, etc.).
    """
    rows = await db.fetch_all(
        """
        SELECT id, name, parent_id, display_order 
        FROM shop_categories 
        ORDER BY name
        """
    )
    categories = [
        {
            "id": int(row["id"]), 
            "name": row["name"],
            "parent_id": _coerce_optional_int(row.get("parent_id")),
            "display_order": _coerce_int(row.get("display_order"), default=0),
        }
        for row in rows
    ]
    
    # Build parent-child map
    parent_map: dict[int | None, list[dict[str, Any]]] = {}
    for category in categories:
        parent_id = category.get("parent_id")
        parent_map.setdefault(parent_id, []).append(category)
    
    # Sort all groups alphabetically by name
    for children_list in parent_map.values():
        children_list.sort(key=lambda c: c["name"].lower())
    
    # Recursively build flat list: parent followed by all its descendants
    def add_category_and_descendants(cat_id: int, result: list[dict[str, Any]]) -> None:
        """Add a category and all its descendants to the result list."""
        for child in parent_map.get(cat_id, []):
            result.append(child)
            # Recursively add this child's children
            add_category_and_descendants(child["id"], result)
    
    result: list[dict[str, Any]] = []
    # Start with top-level categories (no parent)
    for parent in parent_map.get(None, []):
        result.append(parent)
        # Add all descendants of this parent
        add_category_and_descendants(parent["id"], result)
    
    return result


async def get_category_descendants(category_id: int) -> list[int]:
    """Get all descendant category IDs for a given category.
    
    Returns a list of category IDs including the category itself and all its descendants
    (children, grandchildren, etc.).
    """
    rows = await db.fetch_all(
        """
        SELECT id, parent_id 
        FROM shop_categories
        """
    )
    
    # Build parent-child map
    parent_map: dict[int, list[int]] = {}
    for row in rows:
        parent_id = _coerce_optional_int(row.get("parent_id"))
        child_id = int(row["id"])
        if parent_id is not None:
            parent_map.setdefault(parent_id, []).append(child_id)
    
    # Recursively collect all descendants
    def collect_descendants(cat_id: int, result: set[int]) -> None:
        """Recursively add all descendants to the result set."""
        for child_id in parent_map.get(cat_id, []):
            result.add(child_id)
            collect_descendants(child_id, result)
    
    descendants: set[int] = {category_id}
    collect_descendants(category_id, descendants)
    
    return list(descendants)


async def list_products(filters: ProductFilters) -> list[dict[str, Any]]:
    query_parts: list[str] = [
        "SELECT",
        "    p.*,",
        "    c.name AS category_name",
        "FROM shop_products AS p",
        "LEFT JOIN shop_categories AS c ON c.id = p.category_id",
    ]
    params: list[Any] = []

    if filters.company_id is not None:
        query_parts.append(
            "LEFT JOIN shop_product_exclusions AS e "
            "ON e.product_id = p.id AND e.company_id = %s"
        )
        params.append(filters.company_id)

    conditions: list[str] = []
    if not filters.include_archived:
        conditions.append("p.archived = 0")
    if filters.company_id is not None:
        conditions.append("e.product_id IS NULL")
    if filters.category_id is not None:
        conditions.append("p.category_id = %s")
        params.append(filters.category_id)
    elif filters.category_ids is not None and filters.category_ids:
        placeholders = ", ".join(["%s"] * len(filters.category_ids))
        conditions.append(f"p.category_id IN ({placeholders})")
        params.extend(filters.category_ids)
    if filters.search_term:
        like = f"%{filters.search_term}%"
        conditions.append(
            "(p.name LIKE %s OR p.sku LIKE %s OR p.vendor_sku LIKE %s)"
        )
        params.extend([like, like, like])

    if conditions:
        query_parts.append("WHERE " + " AND ".join(conditions))

    query_parts.append("ORDER BY p.name ASC")
    sql = " ".join(query_parts)

    rows = await db.fetch_all(sql, tuple(params) if params else None)
    products = [_normalise_product(row) for row in rows]
    products = await _attach_features_to_products(products)
    await _populate_product_recommendations(products)
    return products


async def list_all_products(include_archived: bool = False) -> list[dict[str, Any]]:
    filters = ProductFilters(include_archived=include_archived)
    return await list_products(filters)


async def list_product_features(product_id: int) -> list[dict[str, Any]]:
    features = await list_features_for_products([product_id])
    return features.get(int(product_id), [])


async def list_features_for_products(
    product_ids: Iterable[int],
) -> dict[int, list[dict[str, Any]]]:
    identifiers = sorted({int(pid) for pid in product_ids if int(pid) > 0})
    if not identifiers:
        return {}

    placeholders = ", ".join(["%s"] * len(identifiers))
    sql = f"""
        SELECT
            id,
            product_id,
            feature_name,
            feature_value,
            position
        FROM shop_product_features
        WHERE product_id IN ({placeholders})
        ORDER BY product_id ASC, position ASC, id ASC
    """
    rows = await db.fetch_all(sql, tuple(identifiers))

    features_map: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        feature = _normalise_feature(row)
        product_id = feature["product_id"]
        features_map.setdefault(product_id, []).append(feature)
    return features_map


async def list_products_by_ids(
    product_ids: Sequence[int],
    *,
    include_archived: bool = False,
    company_id: int | None = None,
) -> list[dict[str, Any]]:
    identifiers = sorted({int(pid) for pid in product_ids if int(pid) > 0})
    if not identifiers:
        return []

    placeholders = ", ".join(["%s"] * len(identifiers))
    query_parts: list[str] = [
        "SELECT",
        "    p.*,",
        "    c.name AS category_name",
        "FROM shop_products AS p",
        "LEFT JOIN shop_categories AS c ON c.id = p.category_id",
    ]
    params: list[Any] = []
    if company_id is not None:
        query_parts.append(
            "LEFT JOIN shop_product_exclusions AS e "
            "ON e.product_id = p.id AND e.company_id = %s"
        )
        params.append(company_id)

    conditions: list[str] = [f"p.id IN ({placeholders})"]
    if not include_archived:
        conditions.append("p.archived = 0")
    if company_id is not None:
        conditions.append("e.product_id IS NULL")

    query_parts.append("WHERE " + " AND ".join(conditions))
    query_parts.append("ORDER BY p.name ASC")

    params.extend(identifiers)
    rows = await db.fetch_all(" ".join(query_parts), tuple(params))
    products = [_normalise_product(row) for row in rows]
    products = await _attach_features_to_products(products)
    await _populate_product_recommendations(products)
    return products


async def list_packages(filters: PackageFilters) -> list[dict[str, Any]]:
    query = [
        "SELECT",
        "    pkg.id,",
        "    pkg.sku,",
        "    pkg.name,",
        "    pkg.description,",
        "    pkg.archived,",
        "    pkg.created_at,",
        "    pkg.updated_at,",
        "    COUNT(items.id) AS product_count",
        "FROM shop_packages AS pkg",
        "LEFT JOIN shop_package_items AS items ON items.package_id = pkg.id",
    ]
    params: list[Any] = []
    conditions: list[str] = []
    if not filters.include_archived:
        conditions.append("pkg.archived = 0")
    if filters.search_term:
        search = f"%{filters.search_term.strip()}%"
        if search.strip("%"):
            conditions.append("(pkg.name LIKE %s OR pkg.sku LIKE %s OR pkg.description LIKE %s)")
            params.extend([search, search, search])
    if conditions:
        query.append("WHERE " + " AND ".join(conditions))
    query.append("GROUP BY pkg.id")
    query.append("ORDER BY pkg.name ASC")
    sql = "\n".join(query)
    rows = await db.fetch_all(sql, tuple(params) if params else None)
    return [_normalise_package(row) for row in rows]


async def get_package(package_id: int, *, include_archived: bool = False) -> dict[str, Any] | None:
    sql = [
        "SELECT",
        "    pkg.id,",
        "    pkg.sku,",
        "    pkg.name,",
        "    pkg.description,",
        "    pkg.archived,",
        "    pkg.created_at,",
        "    pkg.updated_at,",
        "    COUNT(items.id) AS product_count",
        "FROM shop_packages AS pkg",
        "LEFT JOIN shop_package_items AS items ON items.package_id = pkg.id",
        "WHERE pkg.id = %s",
    ]
    params: list[Any] = [package_id]
    if not include_archived:
        sql.append("AND pkg.archived = 0")
    sql.append("GROUP BY pkg.id")
    row = await db.fetch_one("\n".join(sql), tuple(params))
    return _normalise_package(row) if row else None


async def get_package_by_sku(sku: str) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT id, sku, name, description, archived, created_at, updated_at FROM shop_packages WHERE sku = %s",
        (sku,),
    )
    if not row:
        return None
    row["product_count"] = 0
    return _normalise_package(row)


async def create_package(*, sku: str, name: str, description: str | None = None) -> int:
    async with db.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                """
                INSERT INTO shop_packages (sku, name, description)
                VALUES (%s, %s, %s)
                """,
                (sku, name, description),
            )
            package_id = int(cursor.lastrowid)
    return package_id


async def update_package(
    package_id: int,
    *,
    sku: str,
    name: str,
    description: str | None,
) -> bool:
    async with db.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                """
                UPDATE shop_packages
                SET sku = %s,
                    name = %s,
                    description = %s
                WHERE id = %s
                """,
                (sku, name, description, package_id),
            )
            return cursor.rowcount > 0


async def set_package_archived(package_id: int, *, archived: bool) -> bool:
    async with db.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                "UPDATE shop_packages SET archived = %s WHERE id = %s",
                (1 if archived else 0, package_id),
            )
            return cursor.rowcount > 0


async def delete_package(package_id: int) -> bool:
    async with db.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                "DELETE FROM shop_packages WHERE id = %s",
                (package_id,),
            )
            return cursor.rowcount > 0


async def list_package_items(package_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT
            items.id AS item_id,
            items.package_id,
            items.product_id,
            items.quantity,
            products.name AS product_name,
            products.sku AS product_sku,
            products.vendor_sku AS product_vendor_sku,
            products.price AS product_price,
            products.vip_price AS product_vip_price,
            products.stock AS product_stock,
            products.archived AS product_archived,
            products.image_url AS product_image_url,
            products.description AS product_description
        FROM shop_package_items AS items
        INNER JOIN shop_products AS products ON products.id = items.product_id
        WHERE items.package_id = %s
        ORDER BY products.name ASC
        """,
        (package_id,),
    )
    items = [_normalise_package_item(row) for row in rows]
    await _attach_package_item_alternates(items)
    return items


async def list_package_items_for_packages(
    package_ids: Sequence[int],
) -> dict[int, list[dict[str, Any]]]:
    identifiers = [int(identifier) for identifier in package_ids if int(identifier) > 0]
    if not identifiers:
        return {}
    placeholders = ", ".join(["%s"] * len(identifiers))
    rows = await db.fetch_all(
        f"""
        SELECT
            items.id AS item_id,
            items.package_id,
            items.product_id,
            items.quantity,
            products.name AS product_name,
            products.sku AS product_sku,
            products.vendor_sku AS product_vendor_sku,
            products.price AS product_price,
            products.vip_price AS product_vip_price,
            products.stock AS product_stock,
            products.archived AS product_archived,
            products.image_url AS product_image_url,
            products.description AS product_description
        FROM shop_package_items AS items
        INNER JOIN shop_products AS products ON products.id = items.product_id
        WHERE items.package_id IN ({placeholders})
        ORDER BY items.package_id ASC, products.name ASC
        """,
        tuple(identifiers),
    )
    items = [_normalise_package_item(row) for row in rows]
    await _attach_package_item_alternates(items)
    grouped: dict[int, list[dict[str, Any]]] = {}
    for item in items:
        package_id = _coerce_int(item.get("package_id"))
        grouped.setdefault(package_id, []).append(item)
    return grouped


async def _attach_package_item_alternates(items: list[dict[str, Any]]) -> None:
    identifiers: list[int] = []
    for item in items:
        item_id = _coerce_int(item.get("id"), default=0)
        if item_id > 0:
            identifiers.append(item_id)
        else:
            item.setdefault("alternates", [])
    if not identifiers:
        return
    alternates_map = await list_package_item_alternates_for_items(identifiers)
    for item in items:
        item_id = _coerce_int(item.get("id"), default=0)
        item["alternates"] = alternates_map.get(item_id, [])


async def list_package_item_alternates_for_items(
    item_ids: Sequence[int],
) -> dict[int, list[dict[str, Any]]]:
    identifiers = [int(identifier) for identifier in item_ids if int(identifier) > 0]
    if not identifiers:
        return {}
    placeholders = ", ".join(["%s"] * len(identifiers))
    rows = await db.fetch_all(
        f"""
        SELECT
            alternates.id AS alternate_id,
            alternates.package_item_id,
            alternates.alternate_product_id,
            alternates.priority,
            products.name AS product_name,
            products.sku AS product_sku,
            products.vendor_sku AS product_vendor_sku,
            products.price AS product_price,
            products.vip_price AS product_vip_price,
            products.stock AS product_stock,
            products.archived AS product_archived,
            products.image_url AS product_image_url,
            products.description AS product_description
        FROM shop_package_item_alternates AS alternates
        INNER JOIN shop_products AS products ON products.id = alternates.alternate_product_id
        WHERE alternates.package_item_id IN ({placeholders})
        ORDER BY alternates.package_item_id ASC, alternates.priority ASC, products.name ASC
        """,
        tuple(identifiers),
    )
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        package_item_id = _coerce_int(row.get("package_item_id"))
        grouped.setdefault(package_item_id, []).append(
            _normalise_package_item_alternate(row)
        )
    return grouped


async def upsert_package_item(
    *,
    package_id: int,
    product_id: int,
    quantity: int,
) -> None:
    async with db.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                """
                INSERT INTO shop_package_items (package_id, product_id, quantity)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    quantity = VALUES(quantity),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (package_id, product_id, quantity),
            )


async def remove_package_item(package_id: int, product_id: int) -> None:
    await db.execute(
        "DELETE FROM shop_package_items WHERE package_id = %s AND product_id = %s",
        (package_id, product_id),
    )


async def upsert_package_item_alternate(
    *,
    package_id: int,
    product_id: int,
    alternate_product_id: int,
    priority: int,
) -> bool:
    row = await db.fetch_one(
        """
        SELECT id
        FROM shop_package_items
        WHERE package_id = %s AND product_id = %s
        LIMIT 1
        """,
        (package_id, product_id),
    )
    if not row:
        return False
    item_id = _coerce_int(row.get("id"), default=0)
    if item_id <= 0:
        return False
    await db.execute(
        """
        INSERT INTO shop_package_item_alternates (package_item_id, alternate_product_id, priority)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE
            priority = VALUES(priority),
            updated_at = CURRENT_TIMESTAMP
        """,
        (item_id, alternate_product_id, priority),
    )
    return True


async def remove_package_item_alternate(
    package_id: int, product_id: int, alternate_product_id: int
) -> bool:
    async with db.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                DELETE alternates
                FROM shop_package_item_alternates AS alternates
                INNER JOIN shop_package_items AS items ON items.id = alternates.package_item_id
                WHERE items.package_id = %s
                  AND items.product_id = %s
                  AND alternates.alternate_product_id = %s
                """,
                (package_id, product_id, alternate_product_id),
            )
            return cursor.rowcount > 0


async def get_restricted_product_ids(
    *,
    company_id: int,
    product_ids: Iterable[int],
) -> set[int]:
    identifiers = sorted({int(pid) for pid in product_ids if int(pid) > 0})
    if not identifiers:
        return set()
    placeholders = ", ".join(["%s"] * len(identifiers))
    rows = await db.fetch_all(
        f"""
        SELECT product_id
        FROM shop_product_exclusions
        WHERE company_id = %s AND product_id IN ({placeholders})
        """,
        tuple([company_id, *identifiers]),
    )
    return {_coerce_int(row.get("product_id")) for row in rows if row.get("product_id") is not None}


async def list_product_restrictions() -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT e.product_id, e.company_id, c.name AS company_name
        FROM shop_product_exclusions AS e
        INNER JOIN companies AS c ON c.id = e.company_id
        ORDER BY c.name
        """
    )
    restrictions: list[dict[str, Any]] = []
    for row in rows:
        restrictions.append(
            {
                "product_id": int(row["product_id"]),
                "company_id": int(row["company_id"]),
                "company_name": row["company_name"],
            }
        )
    return restrictions


async def get_category(category_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT id, name, parent_id, display_order FROM shop_categories WHERE id = %s",
        (category_id,),
    )
    if not row:
        return None
    return {
        "id": int(row["id"]), 
        "name": row["name"],
        "parent_id": _coerce_optional_int(row.get("parent_id")),
        "display_order": _coerce_int(row.get("display_order"), default=0),
    }


async def get_product_by_id(
    product_id: int,
    *,
    include_archived: bool = False,
    company_id: int | None = None,
) -> dict[str, Any] | None:
    query = [
        "SELECT",
        "    p.*,",
        "    c.name AS category_name",
        "FROM shop_products AS p",
        "LEFT JOIN shop_categories AS c ON c.id = p.category_id",
    ]
    params: list[Any] = []
    if company_id is not None:
        query.append(
            "LEFT JOIN shop_product_exclusions AS e ON e.product_id = p.id AND e.company_id = %s"
        )
        params.append(company_id)
    query.append("WHERE p.id = %s")
    params.append(product_id)
    if not include_archived:
        query.append("AND p.archived = 0")
    if company_id is not None:
        query.append("AND e.product_id IS NULL")
    sql = " ".join(query)
    row = await db.fetch_one(sql, tuple(params))
    if not row:
        return None
    product = _normalise_product(row)
    features = await list_product_features(product_id)
    product["features"] = features
    await _populate_product_recommendations([product])
    return product


async def get_product_by_sku(
    sku: str,
    *,
    include_archived: bool = False,
) -> dict[str, Any] | None:
    sql = [
        "SELECT",
        "    p.*,",
        "    c.name AS category_name",
        "FROM shop_products AS p",
        "LEFT JOIN shop_categories AS c ON c.id = p.category_id",
        "WHERE p.sku = %s",
    ]
    params: list[Any] = [sku]
    if not include_archived:
        sql.append("AND p.archived = 0")
    sql.append("LIMIT 1")
    row = await db.fetch_one(" ".join(sql), tuple(params))
    if not row:
        return None
    product = _normalise_product(row)
    if product["id"]:
        features = await list_product_features(product["id"])
        product["features"] = features
    else:
        product["features"] = []
    await _populate_product_recommendations([product])
    return product


async def create_product(
    *,
    name: str,
    sku: str,
    vendor_sku: str,
    price: Decimal,
    stock: int,
    description: str | None = None,
    vip_price: Decimal | None = None,
    category_id: int | None = None,
    image_url: str | None = None,
    cross_sell_product_ids: Iterable[int] | None = None,
    upsell_product_ids: Iterable[int] | None = None,
) -> dict[str, Any]:
    async with db.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                """
                INSERT INTO shop_products
                    (name, sku, vendor_sku, description, image_url, price, vip_price, stock, category_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    name,
                    sku,
                    vendor_sku,
                    description,
                    image_url,
                    price,
                    vip_price,
                    stock,
                    category_id,
                ),
            )
            product_id = int(cursor.lastrowid)
    await replace_product_recommendations(
        product_id,
        cross_sell_ids=cross_sell_product_ids,
        upsell_ids=upsell_product_ids,
    )
    product = await get_product_by_id(product_id)
    if not product:
        raise RuntimeError("Failed to create product")
    return product


async def replace_product_features(
    product_id: int,
    features: Sequence[dict[str, Any]],
) -> None:
    ordered: list[tuple[int, str, str, int]] = []
    for index, feature in enumerate(features):
        name_value = feature.get("name")
        value_value = feature.get("value")
        position_value = feature.get("position")
        try:
            position = int(position_value)
        except (TypeError, ValueError):
            position = index
        name = "" if name_value is None else str(name_value)
        value = "" if value_value is None else str(value_value)
        ordered.append((product_id, name, value, position))

    async with db.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await conn.begin()
            try:
                await cursor.execute(
                    "DELETE FROM shop_product_features WHERE product_id = %s",
                    (product_id,),
                )
                if ordered:
                    await cursor.executemany(
                        """
                        INSERT INTO shop_product_features
                            (product_id, feature_name, feature_value, position)
                        VALUES (%s, %s, %s, %s)
                        """,
                        ordered,
                    )
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise


async def delete_product(product_id: int) -> bool:
    async with db.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                "DELETE FROM shop_products WHERE id = %s",
                (product_id,),
            )
            return cursor.rowcount > 0


async def create_order(
    *,
    user_id: int,
    company_id: int,
    product_id: int,
    quantity: int,
    order_number: str,
    status: str,
    po_number: str | None,
) -> tuple[int | None, int | None]:
    async with db.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await conn.begin()
            try:
                await cursor.execute(
                    "SELECT stock FROM shop_products WHERE id = %s FOR UPDATE",
                    (product_id,),
                )
                row = await cursor.fetchone()
                previous_stock: int | None = None
                new_stock: int | None = None
                if row and row.get("stock") is not None:
                    previous_stock = int(row["stock"])
                    if previous_stock < quantity:
                        raise ValueError("Insufficient stock available for this product")
                    new_stock = previous_stock - quantity
                await cursor.execute(
                    """
                    INSERT INTO shop_orders (
                        user_id,
                        company_id,
                        product_id,
                        quantity,
                        order_number,
                        status,
                        notes,
                        po_number
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        user_id,
                        company_id,
                        product_id,
                        quantity,
                        order_number,
                        status,
                        None,
                        po_number,
                    ),
                )
                await cursor.execute(
                    "UPDATE shop_products SET stock = stock - %s WHERE id = %s",
                    (quantity, product_id),
                )
                await conn.commit()
                return previous_stock, new_stock
            except Exception:
                await conn.rollback()
                raise


async def list_order_summaries(company_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT
            order_number,
            company_id,
            MAX(order_date) AS order_date,
            MAX(status) AS status,
            MAX(shipping_status) AS shipping_status,
            MAX(notes) AS notes,
            MAX(po_number) AS po_number,
            MAX(consignment_id) AS consignment_id,
            MAX(eta) AS eta
        FROM shop_orders
        WHERE company_id = %s
        GROUP BY order_number, company_id
        ORDER BY order_date DESC
        """,
        (company_id,),
    )
    return [_normalise_order_summary(row) for row in rows]


async def get_order_summary(order_number: str, company_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        """
        SELECT
            order_number,
            company_id,
            MAX(order_date) AS order_date,
            MAX(status) AS status,
            MAX(shipping_status) AS shipping_status,
            MAX(notes) AS notes,
            MAX(po_number) AS po_number,
            MAX(consignment_id) AS consignment_id,
            MAX(eta) AS eta
        FROM shop_orders
        WHERE order_number = %s AND company_id = %s
        GROUP BY order_number, company_id
        """,
        (order_number, company_id),
    )
    if not row:
        return None
    return _normalise_order_summary(row)


async def update_order(
    order_number: str,
    company_id: int,
    **updates: Any,
) -> dict[str, Any] | None:
    existing = await get_order_summary(order_number, company_id)
    if not existing:
        return None

    if not updates:
        return existing

    allowed_fields = {
        "status",
        "shipping_status",
        "notes",
        "po_number",
        "consignment_id",
        "eta",
    }
    updates = {key: value for key, value in updates.items() if key in allowed_fields}
    if not updates:
        return existing

    if "eta" in updates:
        updates["eta"] = _ensure_naive_utc(updates["eta"])

    if updates:
        set_clause = ", ".join(f"{column} = %s" for column in updates)
        params: list[Any] = list(updates.values())
        params.extend([order_number, company_id])
        await db.execute(
            f"UPDATE shop_orders SET {set_clause} WHERE order_number = %s AND company_id = %s",
            tuple(params),
        )

    return await get_order_summary(order_number, company_id)


async def delete_order(order_number: str, company_id: int) -> None:
    await db.execute(
        "DELETE FROM shop_orders WHERE order_number = %s AND company_id = %s",
        (order_number, company_id),
    )


async def list_order_items(order_number: str, company_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT
            o.*, 
            p.name AS product_name,
            p.sku,
            p.description,
            p.image_url,
            p.stock,
            p.stock_nsw,
            p.stock_qld,
            p.stock_vic,
            p.stock_sa,
            IF(c.is_vip = 1 AND p.vip_price IS NOT NULL, p.vip_price, p.price) AS price
        FROM shop_orders AS o
        INNER JOIN shop_products AS p ON p.id = o.product_id
        INNER JOIN companies AS c ON c.id = o.company_id
        WHERE o.order_number = %s AND o.company_id = %s
        ORDER BY o.id ASC
        """,
        (order_number, company_id),
    )
    return [_normalise_order_item(row) for row in rows]


async def get_category_by_name(name: str) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT id, name, parent_id, display_order FROM shop_categories WHERE name = %s",
        (name,),
    )
    if not row:
        return None
    return {
        "id": int(row["id"]), 
        "name": row["name"],
        "parent_id": _coerce_optional_int(row.get("parent_id")),
        "display_order": _coerce_int(row.get("display_order"), default=0),
    }


async def create_category(name: str, parent_id: int | None = None, display_order: int = 0) -> int:
    async with db.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                "INSERT INTO shop_categories (name, parent_id, display_order) VALUES (%s, %s, %s)",
                (name, parent_id, display_order),
            )
            category_id = int(cursor.lastrowid)
    return category_id


async def update_category(category_id: int, name: str, parent_id: int | None = None, display_order: int = 0) -> bool:
    async with db.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                "UPDATE shop_categories SET name = %s, parent_id = %s, display_order = %s WHERE id = %s",
                (name, parent_id, display_order, category_id),
            )
            return cursor.rowcount > 0


async def delete_category(category_id: int) -> bool:
    async with db.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                "DELETE FROM shop_categories WHERE id = %s",
                (category_id,),
            )
            return cursor.rowcount > 0


async def update_product(
    product_id: int,
    *,
    name: str,
    sku: str,
    vendor_sku: str,
    description: str | None,
    price: Decimal,
    stock: int,
    vip_price: Decimal | None,
    category_id: int | None,
    image_url: str | None,
    cross_sell_product_ids: Iterable[int] | None,
    upsell_product_ids: Iterable[int] | None,
) -> dict[str, Any] | None:
    async with db.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                """
                UPDATE shop_products
                SET
                    name = %s,
                    sku = %s,
                    vendor_sku = %s,
                    description = %s,
                    image_url = %s,
                    price = %s,
                    vip_price = %s,
                    stock = %s,
                    category_id = %s
                WHERE id = %s
                """,
                (
                    name,
                    sku,
                    vendor_sku,
                    description,
                    image_url,
                    price,
                    vip_price,
                    stock,
                    category_id,
                    product_id,
                ),
            )
    await replace_product_recommendations(
        product_id,
        cross_sell_ids=cross_sell_product_ids,
        upsell_ids=upsell_product_ids,
    )
    return await get_product_by_id(product_id, include_archived=True)


async def set_product_archived(product_id: int, *, archived: bool) -> bool:
    async with db.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                "UPDATE shop_products SET archived = %s WHERE id = %s",
                (1 if archived else 0, product_id),
            )
            return cursor.rowcount > 0


async def replace_product_recommendations(
    product_id: int,
    *,
    cross_sell_ids: Iterable[int] | None = None,
    upsell_ids: Iterable[int] | None = None,
) -> None:
    cross_ids = sorted({int(pid) for pid in (cross_sell_ids or []) if int(pid) > 0 and int(pid) != product_id})
    upsell_ids_clean = sorted({int(pid) for pid in (upsell_ids or []) if int(pid) > 0 and int(pid) != product_id})

    async with db.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await conn.begin()
            try:
                await cursor.execute(
                    "DELETE FROM shop_product_cross_sells WHERE product_id = %s",
                    (product_id,),
                )
                if cross_ids:
                    await cursor.executemany(
                        "INSERT INTO shop_product_cross_sells (product_id, related_product_id) VALUES (%s, %s)",
                        [(product_id, related_id) for related_id in cross_ids],
                    )

                await cursor.execute(
                    "DELETE FROM shop_product_upsells WHERE product_id = %s",
                    (product_id,),
                )
                if upsell_ids_clean:
                    await cursor.executemany(
                        "INSERT INTO shop_product_upsells (product_id, related_product_id) VALUES (%s, %s)",
                        [(product_id, related_id) for related_id in upsell_ids_clean],
                    )
            except Exception:
                await conn.rollback()
                raise
            else:
                await conn.commit()


async def _populate_product_recommendations(products: list[dict[str, Any]]) -> None:
    product_ids = [
        _coerce_int(product.get("id"), default=0)
        for product in products
        if product.get("id") is not None
    ]
    identifiers = [pid for pid in product_ids if pid > 0]
    if not identifiers:
        for product in products:
            product.setdefault("cross_sell_products", [])
            product.setdefault("cross_sell_product_ids", [])
            product.setdefault("upsell_products", [])
            product.setdefault("upsell_product_ids", [])
        return

    cross_map = await _fetch_recommendation_map("shop_product_cross_sells", identifiers)
    upsell_map = await _fetch_recommendation_map("shop_product_upsells", identifiers)

    for product in products:
        product_id = _coerce_int(product.get("id"), default=0)
        cross_entries = cross_map.get(product_id, [])
        upsell_entries = upsell_map.get(product_id, [])
        product["cross_sell_products"] = cross_entries
        product["cross_sell_product_ids"] = [entry["id"] for entry in cross_entries]
        product["upsell_products"] = upsell_entries
        product["upsell_product_ids"] = [entry["id"] for entry in upsell_entries]


async def _fetch_recommendation_map(
    table_name: str, product_ids: Sequence[int]
) -> dict[int, list[dict[str, Any]]]:
    ids = sorted({int(pid) for pid in product_ids if int(pid) > 0})
    if not ids:
        return {}

    placeholders = ", ".join(["%s"] * len(ids))
    rows = await db.fetch_all(
        f"""
        SELECT
            rel.product_id,
            rel.related_product_id,
            p.name,
            p.sku,
            p.archived,
            p.category_id,
            c.name AS category_name
        FROM {table_name} AS rel
        JOIN shop_products AS p ON p.id = rel.related_product_id
        LEFT JOIN shop_categories AS c ON c.id = p.category_id
        WHERE rel.product_id IN ({placeholders})
        ORDER BY p.name ASC
        """,
        tuple(ids),
    )

    mapping: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        product_id = _coerce_int(row.get("product_id"), default=0)
        related_id = _coerce_int(row.get("related_product_id"), default=0)
        if product_id <= 0 or related_id <= 0:
            continue
        entry = {
            "id": related_id,
            "name": row.get("name"),
            "sku": row.get("sku"),
            "category_id": _coerce_optional_int(row.get("category_id")),
            "category_name": row.get("category_name"),
            "archived": bool(_coerce_int(row.get("archived"), default=0)),
        }
        mapping[product_id].append(entry)
    return mapping


async def replace_product_exclusions(
    product_id: int,
    excluded_company_ids: Iterable[int],
) -> None:
    ids = sorted({int(company_id) for company_id in excluded_company_ids})
    async with db.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await conn.begin()
            try:
                await cursor.execute(
                    "DELETE FROM shop_product_exclusions WHERE product_id = %s",
                    (product_id,),
                )
                if ids:
                    values = [(product_id, company_id) for company_id in ids]
                    await cursor.executemany(
                        "INSERT INTO shop_product_exclusions (product_id, company_id) VALUES (%s, %s)",
                        values,
                    )
            except Exception:
                await conn.rollback()
                raise
            else:
                await conn.commit()


async def upsert_product_from_feed(
    *,
    name: str,
    sku: str,
    vendor_sku: str,
    description: str | None,
    image_url: str | None,
    price: Decimal,
    vip_price: Decimal,
    stock: int,
    category_id: int | None,
    stock_nsw: int,
    stock_qld: int,
    stock_vic: int,
    stock_sa: int,
    buy_price: Decimal | None,
    weight: Decimal | None,
    length: Decimal | None,
    width: Decimal | None,
    height: Decimal | None,
    stock_at: date | None,
    warranty_length: str | None,
    manufacturer: str | None,
) -> None:
    await db.execute(
        """
        INSERT INTO shop_products
            (name, sku, vendor_sku, description, image_url, price, vip_price, stock,
             category_id, stock_nsw, stock_qld, stock_vic, stock_sa, buy_price,
             weight, length, width, height, stock_at, warranty_length, manufacturer)
        VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            name = VALUES(name),
            sku = VALUES(sku),
            description = VALUES(description),
            image_url = IFNULL(VALUES(image_url), image_url),
            price = VALUES(price),
            vip_price = VALUES(vip_price),
            stock = VALUES(stock),
            category_id = VALUES(category_id),
            stock_nsw = VALUES(stock_nsw),
            stock_qld = VALUES(stock_qld),
            stock_vic = VALUES(stock_vic),
            stock_sa = VALUES(stock_sa),
            buy_price = VALUES(buy_price),
            weight = VALUES(weight),
            length = VALUES(length),
            width = VALUES(width),
            height = VALUES(height),
            stock_at = VALUES(stock_at),
            warranty_length = VALUES(warranty_length),
            manufacturer = VALUES(manufacturer)
        """,
        (
            name,
            sku,
            vendor_sku,
            description,
            image_url,
            price,
            vip_price,
            stock,
            category_id,
            stock_nsw,
            stock_qld,
            stock_vic,
            stock_sa,
            buy_price,
            weight,
            length,
            width,
            height,
            stock_at,
            warranty_length,
            manufacturer,
        ),
    )


async def _attach_features_to_products(
    products: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if not products:
        return products

    identifiers = sorted(
        {int(product.get("id") or 0) for product in products if int(product.get("id") or 0) > 0}
    )
    features_map: dict[int, list[dict[str, Any]]] = {}
    if identifiers:
        features_map = await list_features_for_products(identifiers)

    for product in products:
        product_id = int(product.get("id") or 0)
        if product_id > 0:
            product["features"] = features_map.get(product_id, [])
        else:
            product["features"] = []
    return products


def _normalise_feature(row: dict[str, Any]) -> dict[str, Any]:
    record = dict(row)
    record["id"] = _coerce_int(row.get("id"))
    record["product_id"] = _coerce_int(row.get("product_id"))
    name_value = row.get("feature_name")
    value_value = row.get("feature_value")
    record["name"] = "" if name_value is None else str(name_value)
    record["value"] = "" if value_value is None else str(value_value)
    record["position"] = _coerce_int(row.get("position"), default=0)
    return record


def _normalise_product(row: dict[str, Any]) -> dict[str, Any]:
    normalised = dict(row)
    normalised["id"] = _coerce_int(row.get("id"))
    normalised["category_id"] = _coerce_optional_int(row.get("category_id"))
    normalised["price"] = _coerce_decimal(row.get("price"), default=0.0)
    normalised["vip_price"] = _coerce_optional_decimal(row.get("vip_price"))
    normalised["buy_price"] = _coerce_optional_decimal(row.get("buy_price"))
    normalised["weight"] = _coerce_optional_decimal(row.get("weight"))
    normalised["length"] = _coerce_optional_decimal(row.get("length"))
    normalised["width"] = _coerce_optional_decimal(row.get("width"))
    normalised["height"] = _coerce_optional_decimal(row.get("height"))
    normalised["stock"] = _coerce_int(row.get("stock"), default=0)
    normalised["stock_nsw"] = _coerce_int(row.get("stock_nsw"), default=0)
    normalised["stock_qld"] = _coerce_int(row.get("stock_qld"), default=0)
    normalised["stock_vic"] = _coerce_int(row.get("stock_vic"), default=0)
    normalised["stock_sa"] = _coerce_int(row.get("stock_sa"), default=0)
    normalised["archived"] = bool(_coerce_int(row.get("archived"), default=0))
    stock_at = row.get("stock_at")
    if isinstance(stock_at, (datetime, date)):
        normalised["stock_at"] = stock_at.isoformat()
    elif stock_at is not None:
        normalised["stock_at"] = str(stock_at)
    normalised.setdefault("cross_sell_products", [])
    normalised.setdefault("cross_sell_product_ids", [])
    normalised.setdefault("upsell_products", [])
    normalised.setdefault("upsell_product_ids", [])
    return normalised


def _normalise_package(row: dict[str, Any]) -> dict[str, Any]:
    record = dict(row)
    record["id"] = _coerce_int(row.get("id"))
    record["archived"] = bool(_coerce_int(row.get("archived"), default=0))
    record["product_count"] = _coerce_int(row.get("product_count"), default=0)
    record["created_at"] = _normalise_datetime(row.get("created_at"))
    record["updated_at"] = _normalise_datetime(row.get("updated_at"))
    return record


def _normalise_package_item(row: dict[str, Any]) -> dict[str, Any]:
    item = {
        "id": _coerce_int(row.get("item_id"), default=0),
        "package_id": _coerce_int(row.get("package_id")),
        "product_id": _coerce_int(row.get("product_id")),
        "quantity": max(_coerce_int(row.get("quantity"), default=1), 0),
        "product_name": row.get("product_name"),
        "product_sku": row.get("product_sku"),
        "product_vendor_sku": row.get("product_vendor_sku"),
        "product_archived": bool(_coerce_int(row.get("product_archived"), default=0)),
        "product_image_url": row.get("product_image_url"),
        "product_description": row.get("product_description"),
    }
    price = row.get("product_price")
    if isinstance(price, Decimal):
        item["product_price"] = price
    elif price is None:
        item["product_price"] = Decimal("0")
    else:
        item["product_price"] = Decimal(str(price))
    vip_price = row.get("product_vip_price")
    if isinstance(vip_price, Decimal):
        item["product_vip_price"] = vip_price
    elif vip_price is None:
        item["product_vip_price"] = None
    else:
        item["product_vip_price"] = Decimal(str(vip_price))
    stock = row.get("product_stock")
    if isinstance(stock, Decimal):
        item["product_stock"] = int(stock)
    elif stock is None:
        item["product_stock"] = 0
    else:
        item["product_stock"] = int(stock)
    item["alternates"] = []
    return item


def _normalise_package_item_alternate(row: dict[str, Any]) -> dict[str, Any]:
    alternate = {
        "id": _coerce_int(row.get("alternate_id"), default=0),
        "package_item_id": _coerce_int(row.get("package_item_id")),
        "product_id": _coerce_int(row.get("alternate_product_id")),
        "priority": _coerce_int(row.get("priority"), default=0),
        "product_name": row.get("product_name"),
        "product_sku": row.get("product_sku"),
        "product_vendor_sku": row.get("product_vendor_sku"),
        "product_archived": bool(_coerce_int(row.get("product_archived"), default=0)),
        "product_image_url": row.get("product_image_url"),
        "product_description": row.get("product_description"),
    }
    price = row.get("product_price")
    if isinstance(price, Decimal):
        alternate["product_price"] = price
    elif price is None:
        alternate["product_price"] = Decimal("0")
    else:
        alternate["product_price"] = Decimal(str(price))
    vip_price = row.get("product_vip_price")
    if isinstance(vip_price, Decimal):
        alternate["product_vip_price"] = vip_price
    elif vip_price is None:
        alternate["product_vip_price"] = None
    else:
        alternate["product_vip_price"] = Decimal(str(vip_price))
    stock = row.get("product_stock")
    if isinstance(stock, Decimal):
        alternate["product_stock"] = int(stock)
    elif stock is None:
        alternate["product_stock"] = 0
    else:
        alternate["product_stock"] = int(stock)
    return alternate


def _coerce_decimal(value: Any, *, default: float | None = None) -> float:
    if value is None:
        if default is None:
            raise ValueError("Decimal value is required")
        return default
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    return float(Decimal(str(value)))


def _coerce_optional_decimal(value: Any) -> float | None:
    if value is None:
        return None
    return _coerce_decimal(value)


def _coerce_int(value: Any, *, default: int | None = None) -> int:
    if value is None:
        if default is None:
            raise ValueError("Integer value is required")
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        return int(value)
    return int(float(value))


def _coerce_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return _coerce_int(value)


def _normalise_datetime(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        base = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return base.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        combined = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
        return combined.isoformat()
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat()


def _ensure_naive_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc).replace(tzinfo=None)
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    if isinstance(value, date):
        combined = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
        return combined.replace(tzinfo=None)
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("Invalid datetime value for eta") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.replace(tzinfo=None)


def _normalise_order_summary(row: dict[str, Any]) -> dict[str, Any]:
    summary = dict(row)
    summary["order_number"] = str(row.get("order_number") or "").strip()
    summary["company_id"] = _coerce_optional_int(row.get("company_id"))
    summary["status"] = str(row.get("status") or "").strip()
    summary["shipping_status"] = str(row.get("shipping_status") or "").strip()
    summary["notes"] = row.get("notes")
    summary["po_number"] = row.get("po_number")
    summary["consignment_id"] = row.get("consignment_id")
    summary["order_date"] = _normalise_datetime(row.get("order_date"))
    summary["eta"] = _normalise_datetime(row.get("eta"))
    return summary


def _normalise_order_item(row: dict[str, Any]) -> dict[str, Any]:
    normalised = dict(row)
    normalised["id"] = _coerce_optional_int(row.get("id"))
    normalised["company_id"] = _coerce_optional_int(row.get("company_id"))
    normalised["user_id"] = _coerce_optional_int(row.get("user_id"))
    normalised["product_id"] = _coerce_optional_int(row.get("product_id"))
    normalised["quantity"] = _coerce_int(row.get("quantity"), default=0)
    price = row.get("price")
    if isinstance(price, Decimal):
        normalised["price"] = price
    elif price is None:
        normalised["price"] = Decimal("0")
    else:
        normalised["price"] = Decimal(str(price))
    normalised["product_name"] = row.get("product_name")
    normalised["sku"] = row.get("sku")
    normalised["description"] = row.get("description")
    normalised["image_url"] = row.get("image_url")
    normalised["status"] = str(row.get("status") or "").strip()
    normalised["shipping_status"] = str(row.get("shipping_status") or "").strip()
    normalised["notes"] = row.get("notes")
    normalised["po_number"] = row.get("po_number")
    normalised["consignment_id"] = row.get("consignment_id")
    normalised["order_number"] = str(row.get("order_number") or "").strip()
    normalised["order_date"] = _normalise_datetime(row.get("order_date"))
    normalised["eta"] = _normalise_datetime(row.get("eta"))
    normalised["stock"] = _coerce_optional_int(row.get("stock"))
    normalised["stock_nsw"] = _coerce_optional_int(row.get("stock_nsw"))
    normalised["stock_qld"] = _coerce_optional_int(row.get("stock_qld"))
    normalised["stock_vic"] = _coerce_optional_int(row.get("stock_vic"))
    normalised["stock_sa"] = _coerce_optional_int(row.get("stock_sa"))
    return normalised
