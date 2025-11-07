"""Test for recursive shop category rendering in templates."""
import asyncio

from app.repositories import shop as shop_repo


def test_list_categories_supports_deep_nesting(monkeypatch):
    """Test that list_categories returns deeply nested hierarchical structure."""
    
    async def fake_fetch_all(query, params=None):
        # Simulate database rows with 4 levels of nesting
        return [
            {"id": 1, "name": "Electronics", "parent_id": None, "display_order": 0},
            {"id": 2, "name": "Computers", "parent_id": 1, "display_order": 0},
            {"id": 3, "name": "Laptops", "parent_id": 2, "display_order": 0},
            {"id": 4, "name": "Gaming Laptops", "parent_id": 3, "display_order": 0},
            {"id": 5, "name": "Clothing", "parent_id": None, "display_order": 1},
        ]
    
    monkeypatch.setattr(shop_repo.db, "fetch_all", fake_fetch_all)
    
    categories = asyncio.run(shop_repo.list_categories())
    
    # Should return only top-level categories
    assert len(categories) == 2
    
    # Check 4-level deep nesting: Electronics > Computers > Laptops > Gaming Laptops
    electronics = categories[1]  # Second because alphabetically sorted
    assert electronics["name"] == "Electronics"
    assert len(electronics["children"]) == 1
    
    computers = electronics["children"][0]
    assert computers["name"] == "Computers"
    assert len(computers["children"]) == 1
    
    laptops = computers["children"][0]
    assert laptops["name"] == "Laptops"
    assert len(laptops["children"]) == 1
    
    gaming_laptops = laptops["children"][0]
    assert gaming_laptops["name"] == "Gaming Laptops"
    assert len(gaming_laptops["children"]) == 0


def test_list_categories_maintains_alphabetical_ordering_at_all_levels(monkeypatch):
    """Test that categories are alphabetically sorted at each level of the hierarchy."""
    
    async def fake_fetch_all(query, params=None):
        # Return categories in non-alphabetical order
        return [
            {"id": 1, "name": "Zebra", "parent_id": None, "display_order": 0},
            {"id": 2, "name": "Apple", "parent_id": None, "display_order": 0},
            {"id": 3, "name": "Zelda Games", "parent_id": 1, "display_order": 0},
            {"id": 4, "name": "Animal Toys", "parent_id": 1, "display_order": 0},
            {"id": 5, "name": "Books", "parent_id": 2, "display_order": 0},
        ]
    
    monkeypatch.setattr(shop_repo.db, "fetch_all", fake_fetch_all)
    
    categories = asyncio.run(shop_repo.list_categories())
    
    # Top level should be alphabetically sorted
    assert len(categories) == 2
    assert categories[0]["name"] == "Apple"
    assert categories[1]["name"] == "Zebra"
    
    # Zebra's children should be alphabetically sorted
    zebra_children = categories[1]["children"]
    assert len(zebra_children) == 2
    assert zebra_children[0]["name"] == "Animal Toys"
    assert zebra_children[1]["name"] == "Zelda Games"


def test_category_has_children_list(monkeypatch):
    """Test that each category has a children list, even if empty."""
    
    async def fake_fetch_all(query, params=None):
        return [
            {"id": 1, "name": "Electronics", "parent_id": None, "display_order": 0},
            {"id": 2, "name": "Clothing", "parent_id": None, "display_order": 0},
        ]
    
    monkeypatch.setattr(shop_repo.db, "fetch_all", fake_fetch_all)
    
    categories = asyncio.run(shop_repo.list_categories())
    
    # Both categories should have empty children lists
    assert "children" in categories[0]
    assert isinstance(categories[0]["children"], list)
    assert len(categories[0]["children"]) == 0
    
    assert "children" in categories[1]
    assert isinstance(categories[1]["children"], list)
    assert len(categories[1]["children"]) == 0


def test_mixed_depth_hierarchy(monkeypatch):
    """Test that categories at different depths can coexist."""
    
    async def fake_fetch_all(query, params=None):
        # Some branches go deeper than others
        return [
            {"id": 1, "name": "Electronics", "parent_id": None, "display_order": 0},
            {"id": 2, "name": "Computers", "parent_id": 1, "display_order": 0},
            {"id": 3, "name": "Laptops", "parent_id": 2, "display_order": 0},
            {"id": 4, "name": "Accessories", "parent_id": 1, "display_order": 0},  # Only 1 level deep
            {"id": 5, "name": "Clothing", "parent_id": None, "display_order": 0},  # No children at all
        ]
    
    monkeypatch.setattr(shop_repo.db, "fetch_all", fake_fetch_all)
    
    categories = asyncio.run(shop_repo.list_categories())
    
    assert len(categories) == 2
    
    # Electronics has 2 children at different depths
    electronics = categories[1]
    assert electronics["name"] == "Electronics"
    assert len(electronics["children"]) == 2
    
    # Accessories is only 1 level deep (child of Electronics)
    accessories = electronics["children"][0]
    assert accessories["name"] == "Accessories"
    assert len(accessories["children"]) == 0
    
    # Computers is 2 levels deep (child > grandchild)
    computers = electronics["children"][1]
    assert computers["name"] == "Computers"
    assert len(computers["children"]) == 1
    assert computers["children"][0]["name"] == "Laptops"
    
    # Clothing has no children
    clothing = categories[0]
    assert clothing["name"] == "Clothing"
    assert len(clothing["children"]) == 0
