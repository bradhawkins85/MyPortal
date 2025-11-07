"""Tests for subscription categories admin UI."""
import asyncio

from app.repositories import subscription_categories as sub_cat_repo


def test_list_subscription_categories(monkeypatch):
    """Test that list_categories returns all subscription categories."""
    
    async def fake_fetch_all(query, params=None):
        # Simulate database rows
        return [
            {
                "id": 1,
                "name": "Microsoft 365",
                "description": "Office and cloud productivity",
                "created_at": None,
                "updated_at": None,
            },
            {
                "id": 2,
                "name": "Security Software",
                "description": "Antivirus and security tools",
                "created_at": None,
                "updated_at": None,
            },
        ]
    
    monkeypatch.setattr(sub_cat_repo.db, "fetch_all", fake_fetch_all)
    
    categories = asyncio.run(sub_cat_repo.list_categories())
    
    # Should return all categories ordered by name
    assert len(categories) == 2
    assert categories[0]["name"] == "Microsoft 365"
    assert categories[0]["description"] == "Office and cloud productivity"
    assert categories[1]["name"] == "Security Software"
    assert categories[1]["description"] == "Antivirus and security tools"


def test_get_subscription_category(monkeypatch):
    """Test getting a single subscription category by ID."""
    
    async def fake_fetch_one(query, params):
        if params[0] == 5:
            return {
                "id": 5,
                "name": "Backup Solutions",
                "description": "Cloud backup services",
                "created_at": None,
                "updated_at": None,
            }
        return None
    
    monkeypatch.setattr(sub_cat_repo.db, "fetch_one", fake_fetch_one)
    
    category = asyncio.run(sub_cat_repo.get_category(5))
    
    assert category is not None
    assert category["id"] == 5
    assert category["name"] == "Backup Solutions"
    assert category["description"] == "Cloud backup services"
    
    # Test non-existent category
    not_found = asyncio.run(sub_cat_repo.get_category(999))
    assert not_found is None


def test_get_subscription_category_by_name(monkeypatch):
    """Test getting a subscription category by name."""
    
    async def fake_fetch_one(query, params):
        if params[0] == "Remote Monitoring":
            return {
                "id": 3,
                "name": "Remote Monitoring",
                "description": "RMM and monitoring tools",
                "created_at": None,
                "updated_at": None,
            }
        return None
    
    monkeypatch.setattr(sub_cat_repo.db, "fetch_one", fake_fetch_one)
    
    category = asyncio.run(sub_cat_repo.get_category_by_name("Remote Monitoring"))
    
    assert category is not None
    assert category["id"] == 3
    assert category["name"] == "Remote Monitoring"
    assert category["description"] == "RMM and monitoring tools"
    
    # Test non-existent category
    not_found = asyncio.run(sub_cat_repo.get_category_by_name("Non-existent"))
    assert not_found is None


def test_create_subscription_category(monkeypatch):
    """Test creating a subscription category."""
    
    created_params = {}
    
    async def fake_execute(query, params):
        created_params["query"] = query
        created_params["params"] = params
    
    async def fake_fetch_one(query, params):
        # Return the newly created category
        if params[0] == "Cloud Storage":
            return {
                "id": 10,
                "name": "Cloud Storage",
                "description": "Storage and file sharing",
                "created_at": None,
                "updated_at": None,
            }
        return None
    
    monkeypatch.setattr(sub_cat_repo.db, "execute", fake_execute)
    monkeypatch.setattr(sub_cat_repo.db, "fetch_one", fake_fetch_one)
    
    category = asyncio.run(
        sub_cat_repo.create_category("Cloud Storage", description="Storage and file sharing")
    )
    
    assert category is not None
    assert category["id"] == 10
    assert category["name"] == "Cloud Storage"
    assert category["description"] == "Storage and file sharing"
    assert created_params["params"] == ("Cloud Storage", "Storage and file sharing")


def test_update_subscription_category(monkeypatch):
    """Test updating a subscription category."""
    
    updated_params = {}
    
    async def fake_execute(query, params):
        updated_params["query"] = query
        updated_params["params"] = params
    
    monkeypatch.setattr(sub_cat_repo.db, "execute", fake_execute)
    
    asyncio.run(
        sub_cat_repo.update_category(
            7,
            name="Updated Name",
            description="Updated description",
        )
    )
    
    assert "name = %s" in updated_params["query"]
    assert "description = %s" in updated_params["query"]
    assert updated_params["params"] == ("Updated Name", "Updated description", 7)


def test_update_subscription_category_partial(monkeypatch):
    """Test updating only name without description."""
    
    updated_params = {}
    
    async def fake_execute(query, params):
        updated_params["query"] = query
        updated_params["params"] = params
    
    monkeypatch.setattr(sub_cat_repo.db, "execute", fake_execute)
    
    asyncio.run(
        sub_cat_repo.update_category(
            8,
            name="New Name",
        )
    )
    
    assert "name = %s" in updated_params["query"]
    assert "description" not in updated_params["query"]
    assert updated_params["params"] == ("New Name", 8)


def test_delete_subscription_category(monkeypatch):
    """Test deleting a subscription category."""
    
    deleted_params = {}
    
    async def fake_execute(query, params):
        deleted_params["query"] = query
        deleted_params["params"] = params
    
    monkeypatch.setattr(sub_cat_repo.db, "execute", fake_execute)
    
    asyncio.run(sub_cat_repo.delete_category(12))
    
    assert "DELETE FROM subscription_categories" in deleted_params["query"]
    assert deleted_params["params"] == (12,)
