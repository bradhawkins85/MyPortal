"""Shop handlers for the ``shop`` feature pack."""

from __future__ import annotations


def _main():
    from app import main as main_module

    return main_module


shop_page = _main().shop_page
shop_packages_page = _main().shop_packages_page
shop_product_detail_api = _main().shop_product_detail_api
admin_shop_product_search_api = _main().admin_shop_product_search_api
admin_shop_product_restrictions_api = _main().admin_shop_product_restrictions_api
admin_shop_product_detail_api = _main().admin_shop_product_detail_api
admin_shop_product_price_history_api = _main().admin_shop_product_price_history_api
admin_shop_page = _main().admin_shop_page
admin_shop_packages_page = _main().admin_shop_packages_page
admin_shop_package_detail = _main().admin_shop_package_detail
admin_shop_optional_accessories_page = _main().admin_shop_optional_accessories_page
admin_shop_categories_page = _main().admin_shop_categories_page
admin_shop_product_create_page = _main().admin_shop_product_create_page
admin_shop_subscription_categories_page = _main().admin_shop_subscription_categories_page
admin_create_shop_package = _main().admin_create_shop_package
admin_update_shop_package = _main().admin_update_shop_package
admin_archive_shop_package = _main().admin_archive_shop_package
admin_delete_shop_package = _main().admin_delete_shop_package
admin_add_package_item = _main().admin_add_package_item
admin_update_package_item = _main().admin_update_package_item
admin_add_package_item_alternate = _main().admin_add_package_item_alternate
admin_remove_package_item_alternate = _main().admin_remove_package_item_alternate
admin_remove_package_item = _main().admin_remove_package_item
admin_sync_optional_accessories = _main().admin_sync_optional_accessories
admin_import_optional_accessory = _main().admin_import_optional_accessory
admin_dismiss_optional_accessory = _main().admin_dismiss_optional_accessory
admin_bulk_dismiss_optional_accessories = _main().admin_bulk_dismiss_optional_accessories
admin_restore_optional_accessory = _main().admin_restore_optional_accessory
admin_create_shop_category = _main().admin_create_shop_category
admin_delete_shop_category = _main().admin_delete_shop_category
admin_update_shop_category = _main().admin_update_shop_category
admin_create_subscription_category = _main().admin_create_subscription_category
admin_delete_subscription_category = _main().admin_delete_subscription_category
admin_update_subscription_category = _main().admin_update_subscription_category
admin_import_shop_product = _main().admin_import_shop_product
admin_create_shop_product = _main().admin_create_shop_product
admin_update_shop_product = _main().admin_update_shop_product
admin_archive_shop_product = _main().admin_archive_shop_product
admin_unarchive_shop_product = _main().admin_unarchive_shop_product
admin_update_shop_product_visibility = _main().admin_update_shop_product_visibility
admin_delete_shop_product = _main().admin_delete_shop_product
admin_company_shop_items_api = _main().admin_company_shop_items_api
admin_update_company_shop_visibility = _main().admin_update_company_shop_visibility
