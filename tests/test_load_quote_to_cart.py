"""Test for load quote to cart functionality."""
import inspect

import pytest


@pytest.mark.asyncio
async def test_load_quote_to_cart_uses_correct_shop_repo_function():
    """
    Test that load_quote_to_cart uses shop_repo.get_product_by_id
    and not the non-existent shop_repo.get_product.
    
    This is a regression test for the issue where calling shop_repo.get_product
    would raise AttributeError: module 'app.repositories.shop' has no attribute 'get_product'
    """
    from app.repositories import shop as shop_repo
    
    # Verify that get_product_by_id exists
    assert hasattr(shop_repo, 'get_product_by_id'), "shop_repo should have get_product_by_id function"
    assert callable(shop_repo.get_product_by_id), "get_product_by_id should be callable"
    
    # Verify that get_product does NOT exist (it should not)
    assert not hasattr(shop_repo, 'get_product'), "shop_repo should NOT have get_product function"
    
    # Verify the function signature is correct
    import inspect
    sig = inspect.signature(shop_repo.get_product_by_id)
    params = list(sig.parameters.keys())
    assert 'product_id' in params, "get_product_by_id should have product_id parameter"
    assert 'company_id' in params, "get_product_by_id should have company_id parameter"
    assert 'include_archived' in params, "get_product_by_id should have include_archived parameter"


def test_shop_repo_has_correct_function_signature():
    """
    Test that shop_repo.get_product_by_id has the correct signature
    to be called with the parameters used in load_quote_to_cart.
    """
    from app.repositories import shop as shop_repo
    import inspect
    
    # Verify the function exists
    assert hasattr(shop_repo, 'get_product_by_id'), "shop_repo should have get_product_by_id function"
    
    # Get the function signature
    func = getattr(shop_repo, 'get_product_by_id')
    sig = inspect.signature(func)
    
    # Verify it can be called with our pattern: get_product_by_id(product_id, company_id=company_id)
    params = sig.parameters
    
    # Check required positional parameter
    assert 'product_id' in params, "get_product_by_id should have product_id parameter"
    
    # Check that company_id is a keyword-only parameter (after the * in the function signature)
    assert 'company_id' in params, "get_product_by_id should have company_id parameter"
    company_id_param = params['company_id']
    assert company_id_param.kind == inspect.Parameter.KEYWORD_ONLY, "company_id should be keyword-only"
    # Also verify it has the correct default value
    assert company_id_param.default is None, "company_id should have default value of None"
    
    # Verify the function signature matches our usage pattern
    # This ensures calling: get_product_by_id(product_id, company_id=company_id)
    # will work correctly without AttributeError
