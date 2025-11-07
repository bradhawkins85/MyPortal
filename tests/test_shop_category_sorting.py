"""Tests for alphabetical shop category sorting."""
import asyncio

from app.repositories import shop as shop_repo


def test_list_categories_sorts_alphabetically(monkeypatch):
    """Test that categories are sorted alphabetically, not by display_order."""
    
    async def fake_fetch_all(query, params=None):
        # Simulate database rows - intentionally out of alphabetical order
        # and with display_order that would change the order
        return [
            {"id": 1, "name": "Zebra Category", "parent_id": None, "display_order": 0},
            {"id": 2, "name": "Alpha Category", "parent_id": None, "display_order": 2},
            {"id": 3, "name": "Beta Category", "parent_id": None, "display_order": 1},
        ]
    
    monkeypatch.setattr(shop_repo.db, "fetch_all", fake_fetch_all)
    
    categories = asyncio.run(shop_repo.list_categories())
    
    # Should be sorted alphabetically, not by display_order
    assert len(categories) == 3
    assert categories[0]["name"] == "Alpha Category"
    assert categories[1]["name"] == "Beta Category"
    assert categories[2]["name"] == "Zebra Category"


def test_list_categories_sorts_children_alphabetically(monkeypatch):
    """Test that child categories are sorted alphabetically."""
    
    async def fake_fetch_all(query, params=None):
        # Parent and children intentionally out of alphabetical order
        return [
            {"id": 1, "name": "Electronics", "parent_id": None, "display_order": 0},
            {"id": 2, "name": "Tablets", "parent_id": 1, "display_order": 0},
            {"id": 3, "name": "Laptops", "parent_id": 1, "display_order": 2},
            {"id": 4, "name": "Monitors", "parent_id": 1, "display_order": 1},
        ]
    
    monkeypatch.setattr(shop_repo.db, "fetch_all", fake_fetch_all)
    
    categories = asyncio.run(shop_repo.list_categories())
    
    assert len(categories) == 1
    assert categories[0]["name"] == "Electronics"
    
    # Children should be alphabetically sorted
    children = categories[0]["children"]
    assert len(children) == 3
    assert children[0]["name"] == "Laptops"
    assert children[1]["name"] == "Monitors"
    assert children[2]["name"] == "Tablets"


def test_list_all_categories_flat_groups_children_under_parents(monkeypatch):
    """Test that flat category list shows children grouped under their parents."""
    
    async def fake_fetch_all(query, params=None):
        # Multiple parents with children
        return [
            {"id": 1, "name": "Zebra Parent", "parent_id": None, "display_order": 0},
            {"id": 2, "name": "Zebra Child 1", "parent_id": 1, "display_order": 0},
            {"id": 3, "name": "Zebra Child 2", "parent_id": 1, "display_order": 1},
            {"id": 4, "name": "Alpha Parent", "parent_id": None, "display_order": 1},
            {"id": 5, "name": "Alpha Child 1", "parent_id": 4, "display_order": 0},
            {"id": 6, "name": "Beta Parent", "parent_id": None, "display_order": 2},
        ]
    
    monkeypatch.setattr(shop_repo.db, "fetch_all", fake_fetch_all)
    
    categories = asyncio.run(shop_repo.list_all_categories_flat())
    
    # Should be ordered: parent (alphabetically), then its children (alphabetically), repeat
    # Alpha Parent, Alpha Child 1, Beta Parent, Zebra Parent, Zebra Child 1, Zebra Child 2
    assert len(categories) == 6
    
    assert categories[0]["name"] == "Alpha Parent"
    assert categories[0]["parent_id"] is None
    
    assert categories[1]["name"] == "Alpha Child 1"
    assert categories[1]["parent_id"] == 4
    
    assert categories[2]["name"] == "Beta Parent"
    assert categories[2]["parent_id"] is None
    
    assert categories[3]["name"] == "Zebra Parent"
    assert categories[3]["parent_id"] is None
    
    assert categories[4]["name"] == "Zebra Child 1"
    assert categories[4]["parent_id"] == 1
    
    assert categories[5]["name"] == "Zebra Child 2"
    assert categories[5]["parent_id"] == 1


def test_list_all_categories_flat_sorts_children_alphabetically(monkeypatch):
    """Test that children within same parent are sorted alphabetically."""
    
    async def fake_fetch_all(query, params=None):
        return [
            {"id": 1, "name": "Parent", "parent_id": None, "display_order": 0},
            {"id": 2, "name": "Zebra Child", "parent_id": 1, "display_order": 0},
            {"id": 3, "name": "Alpha Child", "parent_id": 1, "display_order": 2},
            {"id": 4, "name": "Beta Child", "parent_id": 1, "display_order": 1},
        ]
    
    monkeypatch.setattr(shop_repo.db, "fetch_all", fake_fetch_all)
    
    categories = asyncio.run(shop_repo.list_all_categories_flat())
    
    # Parent first, then children alphabetically
    assert len(categories) == 4
    assert categories[0]["name"] == "Parent"
    assert categories[1]["name"] == "Alpha Child"
    assert categories[2]["name"] == "Beta Child"
    assert categories[3]["name"] == "Zebra Child"
