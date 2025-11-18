import asyncio

from app.services import shop as shop_service


def test_stock_notification_function_exists():
    """Test that the stock notification function exists and can be called"""
    # This is a minimal test to ensure the function exists
    assert hasattr(shop_service, 'maybe_send_stock_notification_by_id')

