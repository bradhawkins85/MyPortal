"""Tests for hierarchical shop categories."""
import asyncio

from app.repositories import shop as shop_repo


def test_list_categories_returns_hierarchy(monkeypatch):
    """Test that list_categories returns hierarchical structure."""
    
    async def fake_fetch_all(query, params=None):
        # Simulate database rows
        return [
            {"id": 1, "name": "Electronics", "parent_id": None, "display_order": 0},
            {"id": 2, "name": "Computers", "parent_id": 1, "display_order": 0},
            {"id": 3, "name": "Laptops", "parent_id": 2, "display_order": 0},
            {"id": 4, "name": "Clothing", "parent_id": None, "display_order": 1},
            {"id": 5, "name": "Accessories", "parent_id": 1, "display_order": 1},
        ]
    
    monkeypatch.setattr(shop_repo.db, "fetch_all", fake_fetch_all)
    
    categories = asyncio.run(shop_repo.list_categories())
    
    # Should return only top-level categories
    assert len(categories) == 2
    assert categories[0]["name"] == "Electronics"
    assert categories[1]["name"] == "Clothing"
    
    # Electronics should have children
    assert len(categories[0]["children"]) == 2
    assert categories[0]["children"][0]["name"] == "Computers"
    assert categories[0]["children"][1]["name"] == "Accessories"
    
    # Computers should have children (nested)
    assert len(categories[0]["children"][0]["children"]) == 1
    assert categories[0]["children"][0]["children"][0]["name"] == "Laptops"
    
    # Clothing should have no children
    assert len(categories[1]["children"]) == 0


def test_list_all_categories_flat_returns_all(monkeypatch):
    """Test that list_all_categories_flat returns all categories in flat structure."""
    
    async def fake_fetch_all(query, params=None):
        return [
            {"id": 1, "name": "Electronics", "parent_id": None, "display_order": 0},
            {"id": 2, "name": "Computers", "parent_id": 1, "display_order": 0},
            {"id": 3, "name": "Clothing", "parent_id": None, "display_order": 1},
        ]
    
    monkeypatch.setattr(shop_repo.db, "fetch_all", fake_fetch_all)
    
    categories = asyncio.run(shop_repo.list_all_categories_flat())
    
    # Should return all categories in flat list
    assert len(categories) == 3
    assert categories[0]["name"] == "Electronics"
    assert categories[0]["parent_id"] is None
    assert categories[1]["name"] == "Computers"
    assert categories[1]["parent_id"] == 1
    assert categories[2]["name"] == "Clothing"
    assert categories[2]["parent_id"] is None


def test_create_category_with_parent(monkeypatch):
    """Test creating a category with a parent."""
    
    created_category = {}
    
    class FakeCursor:
        lastrowid = 42
        
        async def execute(self, query, params):
            created_category["query"] = query
            created_category["params"] = params
        
        async def __aenter__(self):
            return self
        
        async def __aexit__(self, *args):
            pass
    
    class FakeConnection:
        def cursor(self, cursor_class):
            return FakeCursor()
        
        async def __aenter__(self):
            return self
        
        async def __aexit__(self, *args):
            pass
    
    class FakeAcquire:
        async def __aenter__(self):
            return FakeConnection()
        
        async def __aexit__(self, *args):
            pass
    
    class FakeDB:
        def acquire(self):
            return FakeAcquire()
    
    monkeypatch.setattr(shop_repo, "db", FakeDB())
    
    category_id = asyncio.run(
        shop_repo.create_category("Laptops", parent_id=5, display_order=2)
    )
    
    assert category_id == 42
    assert created_category["params"] == ("Laptops", 5, 2)
    assert "parent_id" in created_category["query"]
    assert "display_order" in created_category["query"]


def test_get_category_includes_parent_info(monkeypatch):
    """Test that get_category returns parent_id and display_order."""
    
    async def fake_fetch_one(query, params):
        return {
            "id": 10, 
            "name": "Laptops", 
            "parent_id": 5,
            "display_order": 2,
        }
    
    monkeypatch.setattr(shop_repo.db, "fetch_one", fake_fetch_one)
    
    category = asyncio.run(shop_repo.get_category(10))
    
    assert category is not None
    assert category["id"] == 10
    assert category["name"] == "Laptops"
    assert category["parent_id"] == 5
    assert category["display_order"] == 2
