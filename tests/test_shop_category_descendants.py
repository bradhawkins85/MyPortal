"""Tests for shop category descendants functionality."""
import asyncio

from app.repositories import shop as shop_repo


def test_get_category_descendants_includes_self_and_children(monkeypatch):
    """Test that get_category_descendants returns the category itself and all descendants."""
    
    async def fake_fetch_all(query, params=None):
        # Simulate database rows
        # Structure:
        # Electronics (1)
        #   - Computers (2)
        #     - Laptops (3)
        #   - Accessories (5)
        # Clothing (4)
        return [
            {"id": 1, "parent_id": None},
            {"id": 2, "parent_id": 1},
            {"id": 3, "parent_id": 2},
            {"id": 4, "parent_id": None},
            {"id": 5, "parent_id": 1},
        ]
    
    monkeypatch.setattr(shop_repo.db, "fetch_all", fake_fetch_all)
    
    # Test getting descendants of Electronics (should include 1, 2, 3, 5)
    descendants = asyncio.run(shop_repo.get_category_descendants(1))
    assert len(descendants) == 4
    assert set(descendants) == {1, 2, 3, 5}
    
    # Test getting descendants of Computers (should include 2, 3)
    descendants = asyncio.run(shop_repo.get_category_descendants(2))
    assert len(descendants) == 2
    assert set(descendants) == {2, 3}
    
    # Test getting descendants of Laptops (should include only 3)
    descendants = asyncio.run(shop_repo.get_category_descendants(3))
    assert len(descendants) == 1
    assert set(descendants) == {3}
    
    # Test getting descendants of Clothing (should include only 4)
    descendants = asyncio.run(shop_repo.get_category_descendants(4))
    assert len(descendants) == 1
    assert set(descendants) == {4}


def test_list_products_with_category_ids_filter(monkeypatch):
    """Test that list_products correctly filters by multiple category IDs."""
    
    fetch_count = 0
    
    async def fake_fetch_all(query, params=None):
        nonlocal fetch_count
        fetch_count += 1
        
        # First call is for products, second could be for features
        if fetch_count == 1:
            # Simulate products from different categories
            if params and len(params) > 1:  # Has category IDs
                # Return products from specified categories
                return [
                    {
                        "id": 1, 
                        "name": "Laptop", 
                        "category_id": 2, 
                        "sku": "LAP001", 
                        "vendor_sku": "V-LAP001",
                        "price": 999.99, 
                        "vip_price": None,
                        "cost": 800.00,
                        "stock": 10, 
                        "archived": 0, 
                        "category_name": "Computers",
                        "description": None,
                        "image_url": None,
                        "supplier_id": None,
                        "created_at": None,
                        "updated_at": None,
                    },
                    {
                        "id": 2, 
                        "name": "Desktop", 
                        "category_id": 3, 
                        "sku": "DES001", 
                        "vendor_sku": "V-DES001",
                        "price": 1299.99, 
                        "vip_price": None,
                        "cost": 1000.00,
                        "stock": 5, 
                        "archived": 0, 
                        "category_name": "Laptops",
                        "description": None,
                        "image_url": None,
                        "supplier_id": None,
                        "created_at": None,
                        "updated_at": None,
                    },
                ]
            return []
        # Subsequent calls for features - return empty
        return []
    
    monkeypatch.setattr(shop_repo.db, "fetch_all", fake_fetch_all)
    
    # Test filtering with multiple category IDs
    filters = shop_repo.ProductFilters(
        include_archived=False,
        category_ids=[2, 3]
    )
    
    products = asyncio.run(shop_repo.list_products(filters))
    
    # Should return products from both categories
    assert len(products) == 2
    # Products are sorted by name ASC
    product_names = [p["name"] for p in products]
    assert "Desktop" in product_names
    assert "Laptop" in product_names
