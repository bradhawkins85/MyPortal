"""Shop routes for the ``shop`` feature pack."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

from . import handlers

router = APIRouter(tags=["Shop"])


def _add(path: str, endpoint, methods: list[str], **kwargs) -> None:
    router.add_api_route(path, endpoint, methods=methods, **kwargs)


# Portal + shop API routes.
_add("/shop", handlers.shop_page, ["GET"], response_class=HTMLResponse)
_add("/shop/packages", handlers.shop_packages_page, ["GET"], response_class=HTMLResponse)
_add(
    "/api/shop/products/{product_id}",
    handlers.shop_product_detail_api,
    ["GET"],
    response_class=JSONResponse,
)
_add(
    "/api/admin/shop/products/search",
    handlers.admin_shop_product_search_api,
    ["GET"],
    response_class=JSONResponse,
)
_add(
    "/api/admin/shop/products/{product_id}/restrictions",
    handlers.admin_shop_product_restrictions_api,
    ["GET"],
    response_class=JSONResponse,
)
_add(
    "/api/admin/shop/products/{product_id}",
    handlers.admin_shop_product_detail_api,
    ["GET"],
    response_class=JSONResponse,
)
_add(
    "/api/admin/shop/products/{product_id}/price-history",
    handlers.admin_shop_product_price_history_api,
    ["GET"],
    response_class=JSONResponse,
)

# Admin pages.
_add("/admin/shop", handlers.admin_shop_page, ["GET"], response_class=HTMLResponse)
_add("/admin/shop/packages", handlers.admin_shop_packages_page, ["GET"], response_class=HTMLResponse)
_add(
    "/admin/shop/packages/{package_id}",
    handlers.admin_shop_package_detail,
    ["GET"],
    response_class=HTMLResponse,
)
_add(
    "/admin/shop/optional-accessories",
    handlers.admin_shop_optional_accessories_page,
    ["GET"],
    response_class=HTMLResponse,
)
_add("/admin/shop/categories", handlers.admin_shop_categories_page, ["GET"], response_class=HTMLResponse)
_add(
    "/admin/shop/products/new",
    handlers.admin_shop_product_create_page,
    ["GET"],
    response_class=HTMLResponse,
)
_add(
    "/admin/shop/subscription-categories",
    handlers.admin_shop_subscription_categories_page,
    ["GET"],
    response_class=HTMLResponse,
)
_add(
    "/admin/shop/freight-rules",
    handlers.admin_shop_freight_rules_page,
    ["GET"],
    response_class=HTMLResponse,
)

# Admin actions.
_add("/shop/admin/package", handlers.admin_create_shop_package, ["POST"], status_code=303)
_add(
    "/shop/admin/package/{package_id}/update",
    handlers.admin_update_shop_package,
    ["POST"],
    status_code=303,
)
_add(
    "/shop/admin/package/{package_id}/archive",
    handlers.admin_archive_shop_package,
    ["POST"],
    status_code=303,
)
_add(
    "/shop/admin/package/{package_id}/delete",
    handlers.admin_delete_shop_package,
    ["POST"],
    status_code=303,
)
_add(
    "/shop/admin/package/{package_id}/items/add",
    handlers.admin_add_package_item,
    ["POST"],
    status_code=303,
)
_add(
    "/shop/admin/package/{package_id}/items/{product_id}/update",
    handlers.admin_update_package_item,
    ["POST"],
    status_code=303,
)
_add(
    "/shop/admin/package/{package_id}/items/{product_id}/alternates/add",
    handlers.admin_add_package_item_alternate,
    ["POST"],
    status_code=303,
)
_add(
    "/shop/admin/package/{package_id}/items/{product_id}/alternates/{alternate_product_id}/remove",
    handlers.admin_remove_package_item_alternate,
    ["POST"],
    status_code=303,
)
_add(
    "/shop/admin/package/{package_id}/items/{product_id}/remove",
    handlers.admin_remove_package_item,
    ["POST"],
    status_code=303,
)
_add(
    "/admin/shop/optional-accessories/sync",
    handlers.admin_sync_optional_accessories,
    ["POST"],
    status_code=303,
)
_add(
    "/admin/shop/optional-accessories/{accessory_id}/import",
    handlers.admin_import_optional_accessory,
    ["POST"],
    status_code=303,
)
_add(
    "/admin/shop/optional-accessories/{accessory_id}/dismiss",
    handlers.admin_dismiss_optional_accessory,
    ["POST"],
    status_code=303,
)
_add(
    "/admin/shop/optional-accessories/bulk-dismiss",
    handlers.admin_bulk_dismiss_optional_accessories,
    ["POST"],
    status_code=303,
)
_add(
    "/admin/shop/optional-accessories/{accessory_id}/restore",
    handlers.admin_restore_optional_accessory,
    ["POST"],
    status_code=303,
)
_add("/shop/admin/category", handlers.admin_create_shop_category, ["POST"], status_code=303)
_add(
    "/shop/admin/category/{category_id}/delete",
    handlers.admin_delete_shop_category,
    ["POST"],
    status_code=303,
)
_add(
    "/shop/admin/category/{category_id}/update",
    handlers.admin_update_shop_category,
    ["POST"],
    status_code=303,
)
_add(
    "/shop/admin/subscription-category",
    handlers.admin_create_subscription_category,
    ["POST"],
    status_code=303,
)
_add(
    "/shop/admin/subscription-category/{category_id}/delete",
    handlers.admin_delete_subscription_category,
    ["POST"],
    status_code=303,
)
_add(
    "/shop/admin/subscription-category/{category_id}/update",
    handlers.admin_update_subscription_category,
    ["POST"],
    status_code=303,
)
_add(
    "/admin/shop/freight-rules",
    handlers.admin_create_shop_freight_rule,
    ["POST"],
    status_code=303,
)
_add(
    "/admin/shop/freight-rules/{rule_id}",
    handlers.admin_update_shop_freight_rule,
    ["POST"],
    status_code=303,
)
_add(
    "/admin/shop/freight-rules/{rule_id}/delete",
    handlers.admin_delete_shop_freight_rule,
    ["POST"],
    status_code=303,
)
_add("/shop/admin/product/import", handlers.admin_import_shop_product, ["POST"], status_code=303)
_add("/shop/admin/product", handlers.admin_create_shop_product, ["POST"], status_code=303)
_add(
    "/shop/admin/product/{product_id}",
    handlers.admin_update_shop_product,
    ["POST"],
    status_code=303,
)
_add(
    "/shop/admin/product/{product_id}/refresh-description",
    handlers.admin_refresh_shop_product_description,
    ["POST"],
    status_code=303,
    summary="Refresh a shop product description and comparison features",
    tags=["Shop"],
)

_add(
    "/shop/admin/product/{product_id}/archive",
    handlers.admin_archive_shop_product,
    ["POST"],
    status_code=303,
)
_add(
    "/shop/admin/product/{product_id}/unarchive",
    handlers.admin_unarchive_shop_product,
    ["POST"],
    status_code=303,
)
_add(
    "/shop/admin/product/{product_id}/visibility",
    handlers.admin_update_shop_product_visibility,
    ["POST"],
    status_code=303,
)
_add(
    "/shop/admin/product/{product_id}/delete",
    handlers.admin_delete_shop_product,
    ["POST"],
    status_code=303,
)
_add(
    "/api/admin/companies/{company_id}/shop-items",
    handlers.admin_company_shop_items_api,
    ["GET"],
    response_class=JSONResponse,
)
_add(
    "/admin/companies/{company_id}/shop-visibility",
    handlers.admin_update_company_shop_visibility,
    ["POST"],
    status_code=303,
    summary="Update shop item visibility for a company",
    tags=["Shop"],
)


__all__ = ["router"]
