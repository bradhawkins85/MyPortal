"""Tests for hierarchical sub-category creation during product import."""
import pytest
from unittest.mock import AsyncMock

from app.services import products as products_service


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_get_or_create_category_hierarchy_simple(monkeypatch):
    """Test creating a simple single-level category."""
    mock_list_flat = AsyncMock(return_value=[])
    mock_create = AsyncMock(return_value=42)
    
    monkeypatch.setattr(
        products_service.shop_repo,
        "list_all_categories_flat",
        mock_list_flat,
    )
    monkeypatch.setattr(
        products_service.shop_repo,
        "create_category",
        mock_create,
    )
    
    category_id = await products_service._get_or_create_category_hierarchy("Electronics")
    
    assert category_id == 42
    mock_list_flat.assert_awaited_once()
    mock_create.assert_awaited_once_with(name="Electronics", parent_id=None, display_order=0)



@pytest.mark.anyio("asyncio")
async def test_get_or_create_category_hierarchy_two_levels(monkeypatch):
    """Test creating a two-level category hierarchy."""
    create_calls = []
    
    async def mock_list_flat():
        # Return empty list - no existing categories
        return []
    
    async def mock_create(name, parent_id=None, display_order=0):
        category_id = len(create_calls) + 1
        create_calls.append({
            "name": name,
            "parent_id": parent_id,
            "display_order": display_order,
            "id": category_id,
        })
        return category_id
    
    monkeypatch.setattr(
        products_service.shop_repo,
        "list_all_categories_flat",
        mock_list_flat,
    )
    monkeypatch.setattr(
        products_service.shop_repo,
        "create_category",
        mock_create,
    )
    
    category_id = await products_service._get_or_create_category_hierarchy(
        "Electronics - Computers"
    )
    
    assert category_id == 2
    assert len(create_calls) == 2
    assert create_calls[0]["name"] == "Electronics"
    assert create_calls[0]["parent_id"] is None
    assert create_calls[1]["name"] == "Computers"
    assert create_calls[1]["parent_id"] == 1


@pytest.mark.anyio("asyncio")
async def test_get_or_create_category_hierarchy_three_levels(monkeypatch):
    """Test creating a three-level category hierarchy."""
    create_calls = []
    
    async def mock_list_flat():
        return []
    
    async def mock_create(name, parent_id=None, display_order=0):
        category_id = len(create_calls) + 1
        create_calls.append({
            "name": name,
            "parent_id": parent_id,
            "display_order": display_order,
            "id": category_id,
        })
        return category_id
    
    monkeypatch.setattr(
        products_service.shop_repo,
        "list_all_categories_flat",
        mock_list_flat,
    )
    monkeypatch.setattr(
        products_service.shop_repo,
        "create_category",
        mock_create,
    )
    
    category_id = await products_service._get_or_create_category_hierarchy(
        "Electronics - Computers - Laptops"
    )
    
    assert category_id == 3
    assert len(create_calls) == 3
    assert create_calls[0]["name"] == "Electronics"
    assert create_calls[0]["parent_id"] is None
    assert create_calls[1]["name"] == "Computers"
    assert create_calls[1]["parent_id"] == 1
    assert create_calls[2]["name"] == "Laptops"
    assert create_calls[2]["parent_id"] == 2


@pytest.mark.anyio("asyncio")
async def test_get_or_create_category_hierarchy_reuses_existing(monkeypatch):
    """Test that existing categories are reused correctly."""
    create_calls = []
    
    # Electronics already exists, Computers needs to be created
    existing_categories = [
        {"id": 10, "name": "Electronics", "parent_id": None, "display_order": 0}
    ]
    
    async def mock_list_flat():
        return existing_categories.copy()
    
    async def mock_create(name, parent_id=None, display_order=0):
        category_id = 20 + len(create_calls)
        create_calls.append({
            "name": name,
            "parent_id": parent_id,
            "display_order": display_order,
            "id": category_id,
        })
        # Simulate adding to existing list
        existing_categories.append({
            "id": category_id,
            "name": name,
            "parent_id": parent_id,
            "display_order": display_order,
        })
        return category_id
    
    monkeypatch.setattr(
        products_service.shop_repo,
        "list_all_categories_flat",
        mock_list_flat,
    )
    monkeypatch.setattr(
        products_service.shop_repo,
        "create_category",
        mock_create,
    )
    
    category_id = await products_service._get_or_create_category_hierarchy(
        "Electronics - Computers"
    )
    
    # Should reuse existing Electronics (id=10) and create Computers (id=20)
    assert category_id == 20
    assert len(create_calls) == 1
    assert create_calls[0]["name"] == "Computers"
    assert create_calls[0]["parent_id"] == 10


@pytest.mark.anyio("asyncio")
async def test_get_or_create_category_hierarchy_empty_string(monkeypatch):
    """Test that empty string returns None."""
    category_id = await products_service._get_or_create_category_hierarchy("")
    assert category_id is None


@pytest.mark.anyio("asyncio")
async def test_get_or_create_category_hierarchy_whitespace_only(monkeypatch):
    """Test that whitespace-only string returns None."""
    category_id = await products_service._get_or_create_category_hierarchy("   ")
    assert category_id is None


@pytest.mark.anyio("asyncio")
async def test_get_or_create_category_hierarchy_strips_whitespace(monkeypatch):
    """Test that category names are stripped of whitespace."""
    create_calls = []
    
    async def mock_list_flat():
        return []
    
    async def mock_create(name, parent_id=None, display_order=0):
        category_id = len(create_calls) + 1
        create_calls.append({
            "name": name,
            "parent_id": parent_id,
            "display_order": display_order,
            "id": category_id,
        })
        return category_id
    
    monkeypatch.setattr(
        products_service.shop_repo,
        "list_all_categories_flat",
        mock_list_flat,
    )
    monkeypatch.setattr(
        products_service.shop_repo,
        "create_category",
        mock_create,
    )
    
    category_id = await products_service._get_or_create_category_hierarchy(
        "  Electronics  -  Computers  -  Laptops  "
    )
    
    assert category_id == 3
    assert create_calls[0]["name"] == "Electronics"
    assert create_calls[1]["name"] == "Computers"
    assert create_calls[2]["name"] == "Laptops"



@pytest.mark.anyio("asyncio")
async def test_get_or_create_category_hierarchy_handles_duplicate_names_different_parents(monkeypatch):
    """Test handling categories with the same name but different parents."""
    create_calls = []
    
    # Existing structure:
    # - Accessories (id=1, parent=None)
    # - Electronics (id=2, parent=None)
    #   - Accessories (id=3, parent=2) <- different from id=1
    existing_categories = [
        {"id": 1, "name": "Accessories", "parent_id": None, "display_order": 0},
        {"id": 2, "name": "Electronics", "parent_id": None, "display_order": 0},
        {"id": 3, "name": "Accessories", "parent_id": 2, "display_order": 0},
    ]
    
    async def mock_list_flat():
        return existing_categories.copy()
    
    async def mock_create(name, parent_id=None, display_order=0):
        category_id = 10 + len(create_calls)
        create_calls.append({
            "name": name,
            "parent_id": parent_id,
            "display_order": display_order,
            "id": category_id,
        })
        existing_categories.append({
            "id": category_id,
            "name": name,
            "parent_id": parent_id,
            "display_order": display_order,
        })
        return category_id
    
    monkeypatch.setattr(
        products_service.shop_repo,
        "list_all_categories_flat",
        mock_list_flat,
    )
    monkeypatch.setattr(
        products_service.shop_repo,
        "create_category",
        mock_create,
    )
    
    # Should reuse Electronics (id=2) and its child Accessories (id=3)
    category_id = await products_service._get_or_create_category_hierarchy(
        "Electronics - Accessories"
    )
    
    assert category_id == 3
    # Should not create any new categories since they exist
    assert len(create_calls) == 0


@pytest.mark.anyio("asyncio")
async def test_import_product_with_subcategory(monkeypatch):
    """Test importing a product with a hierarchical category."""
    item = {
        "sku": "LAPTOP001",
        "product_name": "Gaming Laptop",
        "category_name": "Electronics - Computers - Laptops",
        "rrp": "1299.99",
    }
    existing_product = None
    
    mock_get_item = AsyncMock(return_value=item)
    mock_get_product = AsyncMock(return_value=existing_product)
    
    # Track the category hierarchy creation
    create_calls = []
    
    async def mock_list_flat():
        return []
    
    async def mock_create_category(name, parent_id=None, display_order=0):
        category_id = len(create_calls) + 1
        create_calls.append({
            "name": name,
            "parent_id": parent_id,
            "display_order": display_order,
            "id": category_id,
        })
        return category_id
    
    mock_upsert = AsyncMock()
    
    monkeypatch.setattr(
        products_service.stock_feed_repo,
        "get_item_by_sku",
        mock_get_item,
    )
    monkeypatch.setattr(
        products_service.shop_repo,
        "get_product_by_sku",
        mock_get_product,
    )
    monkeypatch.setattr(
        products_service.shop_repo,
        "list_all_categories_flat",
        mock_list_flat,
    )
    monkeypatch.setattr(
        products_service.shop_repo,
        "create_category",
        mock_create_category,
    )
    monkeypatch.setattr(
        products_service.shop_repo,
        "upsert_product_from_feed",
        mock_upsert,
    )
    
    result = await products_service.import_product_by_vendor_sku("LAPTOP001")
    
    assert result is True
    
    # Verify category hierarchy was created
    assert len(create_calls) == 3
    assert create_calls[0]["name"] == "Electronics"
    assert create_calls[0]["parent_id"] is None
    assert create_calls[1]["name"] == "Computers"
    assert create_calls[1]["parent_id"] == 1
    assert create_calls[2]["name"] == "Laptops"
    assert create_calls[2]["parent_id"] == 2
    
    # Verify product was created with correct category
    mock_upsert.assert_awaited_once()
    upsert_args = mock_upsert.await_args.kwargs
    assert upsert_args["category_id"] == 3  # Laptops category
