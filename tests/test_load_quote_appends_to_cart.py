"""Test that load quote to cart appends items instead of replacing them."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import status


@pytest.mark.asyncio
async def test_load_quote_to_cart_appends_items():
    """
    Test that load_quote_to_cart appends quote items to existing cart items
    and adds quantities for duplicate products.
    """
    from app.main import load_quote_to_cart
    from starlette.requests import Request
    
    # Mock the request
    mock_request = MagicMock(spec=Request)
    mock_request.url_for = MagicMock(return_value="/cart")
    
    # Mock session
    mock_session = MagicMock()
    mock_session.id = 123
    
    # Mock quote items - simulating 2 products in quote
    mock_quote_items = [
        {
            "product_id": 1,
            "quantity": 5,
            "price": "10.00",
            "product_name": "Product A",
            "sku": "SKU-A",
        },
        {
            "product_id": 2,
            "quantity": 3,
            "price": "20.00",
            "product_name": "Product B",
            "sku": "SKU-B",
        },
    ]
    
    # Mock product details
    mock_product = {
        "vendor_sku": "VENDOR-SKU",
        "description": "Test description",
        "image_url": "http://example.com/image.jpg",
    }
    
    # Mock existing cart item (Product A already in cart with quantity 2)
    mock_existing_item = {
        "product_id": 1,
        "quantity": 2,
        "unit_price": "10.00",
    }
    
    with patch("app.main._load_company_section_context") as mock_context, \
         patch("app.main.session_manager") as mock_session_manager, \
         patch("app.main.shop_repo") as mock_shop_repo, \
         patch("app.main.cart_repo") as mock_cart_repo, \
         patch("app.main.quote") as mock_quote:
        
        # Setup mocks
        mock_context.return_value = (
            {"id": 1},  # user
            {"id": 1},  # membership
            {"id": 1},  # company
            1,  # company_id
            None,  # redirect
        )
        mock_session_manager.load_session = AsyncMock(return_value=mock_session)
        mock_shop_repo.list_quote_items = AsyncMock(return_value=mock_quote_items)
        mock_shop_repo.get_product_by_id = AsyncMock(return_value=mock_product)
        
        # Mock cart_repo.get_item to return existing item for product 1, None for product 2
        async def mock_get_item(session_id, product_id):
            if product_id == 1:
                return mock_existing_item
            return None
        
        mock_cart_repo.get_item = AsyncMock(side_effect=mock_get_item)
        mock_cart_repo.upsert_item = AsyncMock()
        
        mock_quote.return_value = "success_message"
        
        # Call the function
        response = await load_quote_to_cart(mock_request, "QUOTE-123")
        
        # Assertions
        assert response.status_code == status.HTTP_303_SEE_OTHER
        assert "/cart" in response.headers["location"]
        
        # Verify cart_repo.clear_cart was NOT called
        assert not hasattr(mock_cart_repo, "clear_cart") or not mock_cart_repo.clear_cart.called
        
        # Verify get_item was called for each product
        assert mock_cart_repo.get_item.call_count == 2
        
        # Verify upsert_item was called twice
        assert mock_cart_repo.upsert_item.call_count == 2
        
        # Check that both products were added with correct quantities (order-independent)
        upsert_calls = {
            call[1]["product_id"]: call[1]["quantity"]
            for call in mock_cart_repo.upsert_item.call_args_list
        }
        
        # Product A should have combined quantity (2 existing + 5 from quote = 7)
        assert upsert_calls[1] == 7
        
        # Product B should have original quantity (no existing item)
        assert upsert_calls[2] == 3


@pytest.mark.asyncio
async def test_load_quote_to_cart_redirects_to_cart():
    """
    Test that load_quote_to_cart redirects to the cart page after loading.
    """
    from app.main import load_quote_to_cart
    from starlette.requests import Request
    
    # Mock the request
    mock_request = MagicMock(spec=Request)
    mock_request.url_for = MagicMock(return_value="/cart")
    
    # Mock session
    mock_session = MagicMock()
    mock_session.id = 123
    
    # Mock quote items
    mock_quote_items = [
        {
            "product_id": 1,
            "quantity": 5,
            "price": "10.00",
            "product_name": "Product A",
            "sku": "SKU-A",
        },
    ]
    
    # Mock product details
    mock_product = {
        "vendor_sku": "VENDOR-SKU",
        "description": "Test description",
        "image_url": "http://example.com/image.jpg",
    }
    
    with patch("app.main._load_company_section_context") as mock_context, \
         patch("app.main.session_manager") as mock_session_manager, \
         patch("app.main.shop_repo") as mock_shop_repo, \
         patch("app.main.cart_repo") as mock_cart_repo, \
         patch("app.main.quote") as mock_quote:
        
        # Setup mocks
        mock_context.return_value = (
            {"id": 1},  # user
            {"id": 1},  # membership
            {"id": 1},  # company
            1,  # company_id
            None,  # redirect
        )
        mock_session_manager.load_session = AsyncMock(return_value=mock_session)
        mock_shop_repo.list_quote_items = AsyncMock(return_value=mock_quote_items)
        mock_shop_repo.get_product_by_id = AsyncMock(return_value=mock_product)
        mock_cart_repo.get_item = AsyncMock(return_value=None)
        mock_cart_repo.upsert_item = AsyncMock()
        mock_quote.return_value = "success_message"
        
        # Call the function
        response = await load_quote_to_cart(mock_request, "QUOTE-123")
        
        # Assertions - verify redirect to cart page
        assert response.status_code == status.HTTP_303_SEE_OTHER
        assert "/cart" in response.headers["location"]
        assert "cartMessage=success_message" in response.headers["location"]
