"""Shop handlers for the ``shop`` feature pack."""

from __future__ import annotations

from fastapi import Request


def _main():
    from app import main as main_module

    return main_module


async def shop_page(request: Request):
    return await _main().shop_page(request=request)


async def shop_packages_page(request: Request):
    return await _main().shop_packages_page(request=request)


async def shop_product_detail_api(product_id: int, request: Request):
    return await _main().shop_product_detail_api(product_id=product_id, request=request)


async def admin_shop_product_search_api(request: Request):
    return await _main().admin_shop_product_search_api(request=request)


async def admin_shop_product_restrictions_api(product_id: int, request: Request):
    return await _main().admin_shop_product_restrictions_api(product_id=product_id, request=request)


async def admin_shop_product_detail_api(product_id: int, request: Request):
    return await _main().admin_shop_product_detail_api(product_id=product_id, request=request)


async def admin_shop_product_price_history_api(product_id: int, request: Request):
    return await _main().admin_shop_product_price_history_api(product_id=product_id, request=request)


async def admin_shop_page(request: Request):
    return await _main().admin_shop_page(request=request)


async def admin_shop_packages_page(request: Request):
    return await _main().admin_shop_packages_page(request=request)


async def admin_shop_package_detail(package_id: int, request: Request):
    return await _main().admin_shop_package_detail(package_id=package_id, request=request)


async def admin_shop_optional_accessories_page(request: Request):
    return await _main().admin_shop_optional_accessories_page(request=request)


async def admin_shop_categories_page(request: Request):
    return await _main().admin_shop_categories_page(request=request)


async def admin_shop_product_create_page(request: Request):
    return await _main().admin_shop_product_create_page(request=request)


async def admin_shop_subscription_categories_page(request: Request):
    return await _main().admin_shop_subscription_categories_page(request=request)


async def admin_create_shop_package(request: Request):
    return await _main().admin_create_shop_package(request=request)


async def admin_update_shop_package(package_id: int, request: Request):
    return await _main().admin_update_shop_package(package_id=package_id, request=request)


async def admin_archive_shop_package(package_id: int, request: Request):
    return await _main().admin_archive_shop_package(package_id=package_id, request=request)


async def admin_delete_shop_package(package_id: int, request: Request):
    return await _main().admin_delete_shop_package(package_id=package_id, request=request)


async def admin_add_package_item(package_id: int, request: Request):
    return await _main().admin_add_package_item(package_id=package_id, request=request)


async def admin_update_package_item(package_id: int, product_id: int, request: Request):
    return await _main().admin_update_package_item(
        package_id=package_id,
        product_id=product_id,
        request=request,
    )


async def admin_add_package_item_alternate(package_id: int, product_id: int, request: Request):
    return await _main().admin_add_package_item_alternate(
        package_id=package_id,
        product_id=product_id,
        request=request,
    )


async def admin_remove_package_item_alternate(
    package_id: int,
    product_id: int,
    alternate_product_id: int,
    request: Request,
):
    return await _main().admin_remove_package_item_alternate(
        package_id=package_id,
        product_id=product_id,
        alternate_product_id=alternate_product_id,
        request=request,
    )


async def admin_remove_package_item(package_id: int, product_id: int, request: Request):
    return await _main().admin_remove_package_item(
        package_id=package_id,
        product_id=product_id,
        request=request,
    )


async def admin_sync_optional_accessories(request: Request):
    return await _main().admin_sync_optional_accessories(request=request)


async def admin_import_optional_accessory(accessory_id: int, request: Request):
    return await _main().admin_import_optional_accessory(accessory_id=accessory_id, request=request)


async def admin_dismiss_optional_accessory(accessory_id: int, request: Request):
    return await _main().admin_dismiss_optional_accessory(accessory_id=accessory_id, request=request)


async def admin_bulk_dismiss_optional_accessories(request: Request):
    return await _main().admin_bulk_dismiss_optional_accessories(request=request)


async def admin_restore_optional_accessory(accessory_id: int, request: Request):
    return await _main().admin_restore_optional_accessory(accessory_id=accessory_id, request=request)


async def admin_create_shop_category(request: Request):
    return await _main().admin_create_shop_category(request=request)


async def admin_delete_shop_category(category_id: int, request: Request):
    return await _main().admin_delete_shop_category(category_id=category_id, request=request)


async def admin_update_shop_category(category_id: int, request: Request):
    return await _main().admin_update_shop_category(category_id=category_id, request=request)


async def admin_create_subscription_category(request: Request):
    return await _main().admin_create_subscription_category(request=request)


async def admin_delete_subscription_category(category_id: int, request: Request):
    return await _main().admin_delete_subscription_category(category_id=category_id, request=request)


async def admin_update_subscription_category(category_id: int, request: Request):
    return await _main().admin_update_subscription_category(category_id=category_id, request=request)


async def admin_import_shop_product(request: Request):
    return await _main().admin_import_shop_product(request=request)


async def admin_create_shop_product(request: Request):
    return await _main().admin_create_shop_product(request=request)


async def admin_update_shop_product(product_id: int, request: Request):
    return await _main().admin_update_shop_product(product_id=product_id, request=request)


async def admin_archive_shop_product(product_id: int, request: Request):
    return await _main().admin_archive_shop_product(product_id=product_id, request=request)


async def admin_unarchive_shop_product(product_id: int, request: Request):
    return await _main().admin_unarchive_shop_product(product_id=product_id, request=request)


async def admin_update_shop_product_visibility(product_id: int, request: Request):
    return await _main().admin_update_shop_product_visibility(product_id=product_id, request=request)


async def admin_delete_shop_product(product_id: int, request: Request):
    return await _main().admin_delete_shop_product(product_id=product_id, request=request)


async def admin_company_shop_items_api(company_id: int, request: Request):
    return await _main().admin_company_shop_items_api(company_id=company_id, request=request)


async def admin_update_company_shop_visibility(company_id: int, request: Request):
    return await _main().admin_update_company_shop_visibility(company_id=company_id, request=request)
