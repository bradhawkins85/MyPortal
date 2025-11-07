"""Tests for shop category update functionality."""
import asyncio

from app.repositories import shop as shop_repo


def test_update_category_name(monkeypatch):
    """Test updating a category's name."""
    
    updated = {}
    
    class FakeCursor:
        rowcount = 1
        
        async def execute(self, query, params):
            updated["query"] = query
            updated["params"] = params
        
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
    
    result = asyncio.run(
        shop_repo.update_category(5, "Updated Category", parent_id=None, display_order=0)
    )
    
    assert result is True
    assert updated["params"] == ("Updated Category", None, 0, 5)
    assert "UPDATE shop_categories" in updated["query"]
    assert "name = %s" in updated["query"]
    assert "parent_id = %s" in updated["query"]
    assert "display_order = %s" in updated["query"]


def test_update_category_with_parent(monkeypatch):
    """Test updating a category to have a parent."""
    
    updated = {}
    
    class FakeCursor:
        rowcount = 1
        
        async def execute(self, query, params):
            updated["query"] = query
            updated["params"] = params
        
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
    
    result = asyncio.run(
        shop_repo.update_category(10, "Child Category", parent_id=5, display_order=2)
    )
    
    assert result is True
    assert updated["params"] == ("Child Category", 5, 2, 10)


def test_update_category_move_to_top_level(monkeypatch):
    """Test updating a category to remove its parent (make it top-level)."""
    
    updated = {}
    
    class FakeCursor:
        rowcount = 1
        
        async def execute(self, query, params):
            updated["query"] = query
            updated["params"] = params
        
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
    
    result = asyncio.run(
        shop_repo.update_category(10, "Top Level Category", parent_id=None, display_order=1)
    )
    
    assert result is True
    assert updated["params"] == ("Top Level Category", None, 1, 10)
    assert updated["params"][1] is None  # parent_id should be None


def test_update_category_not_found(monkeypatch):
    """Test updating a category that doesn't exist."""
    
    class FakeCursor:
        rowcount = 0
        
        async def execute(self, query, params):
            pass
        
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
    
    result = asyncio.run(
        shop_repo.update_category(999, "Nonexistent", parent_id=None, display_order=0)
    )
    
    assert result is False
