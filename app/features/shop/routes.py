"""Shop routes for the ``shop`` feature pack."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse


router = APIRouter(tags=["Shop"])


def _main():
    from app import main as main_module

    return main_module


def _add(path: str, endpoint_name: str, methods: list[str], **kwargs) -> None:
    router.add_api_route(path, getattr(_main(), endpoint_name), methods=methods, **kwargs)


# Portal + shop API routes.
_add("/shop", "shop_page", ["GET"], response_class=HTMLResponse)
_add("/shop/packages", "shop_packages_page", ["GET"], response_class=HTMLResponse)
_add(
    "/api/shop/products/{product_id}",
    "shop_product_detail_api",
    ["GET"],
    response_class=JSONResponse,
)
_add(
    "/api/admin/shop/products/search",
    "admin_shop_product_search_api",
    ["GET"],
    response_class=JSONResponse,
)
_add(
    "/api/admin/shop/products/{product_id}/restrictions",
    "admin_shop_product_restrictions_api",
    ["GET"],
    response_class=JSONResponse,
)
_add(
    "/api/admin/shop/products/{product_id}",
    "admin_shop_product_detail_api",
    ["GET"],
    response_class=JSONResponse,
)
_add(
    "/api/admin/shop/products/{product_id}/price-history",
    "admin_shop_product_price_history_api",
    ["GET"],
    response_class=JSONResponse,
)

# Admin pages.
_add("/admin/shop", "admin_shop_page", ["GET"], response_class=HTMLResponse)
_add("/admin/shop/packages", "admin_shop_packages_page", ["GET"], response_class=HTMLResponse)
_add(
    "/admin/shop/packages/{package_id}",
    "admin_shop_package_detail",
    ["GET"],
    response_class=HTMLResponse,
)
_add(
    "/admin/shop/optional-accessories",
    "admin_shop_optional_accessories_page",
    ["GET"],
    response_class=HTMLResponse,
)
_add("/admin/shop/categories", "admin_shop_categories_page", ["GET"], response_class=HTMLResponse)
_add(
    "/admin/shop/products/new",
    "admin_shop_product_create_page",
    ["GET"],
    response_class=HTMLResponse,
)
_add(
    "/admin/shop/subscription-categories",
    "admin_shop_subscription_categories_page",
    ["GET"],
    response_class=HTMLResponse,
)

# Admin actions.
_add("/shop/admin/package", "admin_create_shop_package", ["POST"], status_code=303)
_add(
    "/shop/admin/package/{package_id}/update",
    "admin_update_shop_package",
    ["POST"],
    status_code=303,
)
_add(
    "/shop/admin/package/{package_id}/archive",
    "admin_archive_shop_package",
    ["POST"],
    status_code=303,
)
_add(
    "/shop/admin/package/{package_id}/delete",
    "admin_delete_shop_package",
    ["POST"],
    status_code=303,
)
_add(
    "/shop/admin/package/{package_id}/items/add",
    "admin_add_package_item",
    ["POST"],
    status_code=303,
)
_add(
    "/shop/admin/package/{package_id}/items/{product_id}/update",
    "admin_update_package_item",
    ["POST"],
    status_code=303,
)
_add(
    "/shop/admin/package/{package_id}/items/{product_id}/alternates/add",
    "admin_add_package_item_alternate",
    ["POST"],
    status_code=303,
)
_add(
    "/shop/admin/package/{package_id}/items/{product_id}/alternates/{alternate_product_id}/remove",
    "admin_remove_package_item_alternate",
    ["POST"],
    status_code=303,
)
_add(
    "/shop/admin/package/{package_id}/items/{product_id}/remove",
    "admin_remove_package_item",
    ["POST"],
    status_code=303,
)
_add(
    "/admin/shop/optional-accessories/sync",
    "admin_sync_optional_accessories",
    ["POST"],
    status_code=303,
)
_add(
    "/admin/shop/optional-accessories/{accessory_id}/import",
    "admin_import_optional_accessory",
    ["POST"],
    status_code=303,
)
_add(
    "/admin/shop/optional-accessories/{accessory_id}/dismiss",
    "admin_dismiss_optional_accessory",
    ["POST"],
    status_code=303,
)
_add(
    "/admin/shop/optional-accessories/bulk-dismiss",
    "admin_bulk_dismiss_optional_accessories",
    ["POST"],
    status_code=303,
)
_add(
    "/admin/shop/optional-accessories/{accessory_id}/restore",
    "admin_restore_optional_accessory",
    ["POST"],
    status_code=303,
)
_add("/shop/admin/category", "admin_create_shop_category", ["POST"], status_code=303)
_add(
    "/shop/admin/category/{category_id}/delete",
    "admin_delete_shop_category",
    ["POST"],
    status_code=303,
)
_add(
    "/shop/admin/category/{category_id}/update",
    "admin_update_shop_category",
    ["POST"],
    status_code=303,
)
_add(
    "/shop/admin/subscription-category",
    "admin_create_subscription_category",
    ["POST"],
    status_code=303,
)
_add(
    "/shop/admin/subscription-category/{category_id}/delete",
    "admin_delete_subscription_category",
    ["POST"],
    status_code=303,
)
_add(
    "/shop/admin/subscription-category/{category_id}/update",
    "admin_update_subscription_category",
    ["POST"],
    status_code=303,
)
_add("/shop/admin/product/import", "admin_import_shop_product", ["POST"], status_code=303)
_add("/shop/admin/product", "admin_create_shop_product", ["POST"], status_code=303)
_add(
    "/shop/admin/product/{product_id}",
    "admin_update_shop_product",
    ["POST"],
    status_code=303,
)
_add(
    "/shop/admin/product/{product_id}/archive",
    "admin_archive_shop_product",
    ["POST"],
    status_code=303,
)
_add(
    "/shop/admin/product/{product_id}/unarchive",
    "admin_unarchive_shop_product",
    ["POST"],
    status_code=303,
)
_add(
    "/shop/admin/product/{product_id}/visibility",
    "admin_update_shop_product_visibility",
    ["POST"],
    status_code=303,
)
_add(
    "/shop/admin/product/{product_id}/delete",
    "admin_delete_shop_product",
    ["POST"],
    status_code=303,
)


__all__ = ["router"]
