"""Regression tests for product image storage in the Shop feature pack."""

from app.features.shop import handlers
from app.services import file_storage


def test_shop_handlers_use_file_storage_service_for_product_images() -> None:
    """Image handling must not rely on removed exports from ``app.main``."""

    assert handlers.store_product_image is file_storage.store_product_image
    assert handlers.delete_stored_file is file_storage.delete_stored_file
    assert "store_product_image" not in handlers._main().__dict__
