from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.repositories import business_continuity_plans as bc_plans_repo


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _plan_factory(**overrides: Any) -> dict[str, Any]:
    """Factory to create test business continuity plan data."""
    now = datetime.now(timezone.utc)
    base = {
        "id": 1,
        "title": "Test Disaster Recovery Plan",
        "plan_type": "disaster_recovery",
        "content": "This is a test DR plan content.",
        "version": "1.0",
        "status": "active",
        "created_by": 1,
        "created_at": now,
        "updated_at": now,
        "last_reviewed_at": None,
        "last_reviewed_by": None,
    }
    base.update(overrides)
    return base


@pytest.mark.anyio("asyncio")
async def test_create_plan(monkeypatch):
    """Test creating a new business continuity plan."""
    plan_data = _plan_factory()
    
    async def mock_execute(query: str, params: tuple) -> int:
        return plan_data["id"]
    
    async def mock_fetch_one(query: str, params: tuple) -> dict[str, Any]:
        return plan_data
    
    monkeypatch.setattr("app.repositories.business_continuity_plans.db.execute", mock_execute)
    monkeypatch.setattr("app.repositories.business_continuity_plans.db.fetch_one", mock_fetch_one)
    
    result = await bc_plans_repo.create_plan(
        title=plan_data["title"],
        plan_type=plan_data["plan_type"],
        content=plan_data["content"],
        version=plan_data["version"],
        status=plan_data["status"],
        created_by=plan_data["created_by"],
    )
    
    assert result["id"] == plan_data["id"]
    assert result["title"] == plan_data["title"]
    assert result["plan_type"] == "disaster_recovery"


@pytest.mark.anyio("asyncio")
async def test_get_plan_by_id(monkeypatch):
    """Test retrieving a plan by ID."""
    plan_data = _plan_factory()
    
    async def mock_fetch_one(query: str, params: tuple) -> dict[str, Any]:
        return plan_data if params[0] == plan_data["id"] else None
    
    monkeypatch.setattr("app.repositories.business_continuity_plans.db.fetch_one", mock_fetch_one)
    
    result = await bc_plans_repo.get_plan_by_id(plan_data["id"])
    
    assert result is not None
    assert result["id"] == plan_data["id"]
    assert result["title"] == plan_data["title"]
    
    # Test non-existent plan
    result = await bc_plans_repo.get_plan_by_id(999)
    assert result is None


@pytest.mark.anyio("asyncio")
async def test_list_plans_with_filters(monkeypatch):
    """Test listing plans with various filters."""
    plans = [
        _plan_factory(id=1, plan_type="disaster_recovery", status="active"),
        _plan_factory(id=2, plan_type="incident_response", status="draft", title="IR Plan"),
        _plan_factory(id=3, plan_type="business_continuity", status="active", title="BC Plan"),
    ]
    
    async def mock_fetch_all(query: str, params: tuple) -> list[dict[str, Any]]:
        result = plans
        # Simple filtering based on params
        if params:
            if len(params) == 1:
                # Filter by plan_type or status
                if params[0] in ["disaster_recovery", "incident_response", "business_continuity"]:
                    result = [p for p in plans if p["plan_type"] == params[0]]
                elif params[0] in ["active", "draft", "archived"]:
                    result = [p for p in plans if p["status"] == params[0]]
        return result
    
    monkeypatch.setattr("app.repositories.business_continuity_plans.db.fetch_all", mock_fetch_all)
    
    # Test listing all plans
    result = await bc_plans_repo.list_plans()
    assert len(result) == 3
    
    # Test filtering by plan type
    result = await bc_plans_repo.list_plans(plan_type="disaster_recovery")
    assert len(result) == 1
    assert result[0]["plan_type"] == "disaster_recovery"
    
    # Test filtering by status
    result = await bc_plans_repo.list_plans(status="active")
    assert len(result) == 2
    assert all(p["status"] == "active" for p in result)


@pytest.mark.anyio("asyncio")
async def test_update_plan(monkeypatch):
    """Test updating a plan."""
    original_plan = _plan_factory()
    updated_title = "Updated DR Plan"
    
    async def mock_execute(query: str, params: tuple) -> None:
        pass
    
    updated_plan = original_plan.copy()
    updated_plan["title"] = updated_title
    
    async def mock_fetch_one(query: str, params: tuple) -> dict[str, Any]:
        return updated_plan
    
    monkeypatch.setattr("app.repositories.business_continuity_plans.db.execute", mock_execute)
    monkeypatch.setattr("app.repositories.business_continuity_plans.db.fetch_one", mock_fetch_one)
    
    result = await bc_plans_repo.update_plan(
        plan_id=original_plan["id"],
        title=updated_title,
    )
    
    assert result is not None
    assert result["title"] == updated_title


@pytest.mark.anyio("asyncio")
async def test_delete_plan(monkeypatch):
    """Test deleting a plan."""
    plan_id = 1
    
    async def mock_execute(query: str, params: tuple) -> None:
        pass
    
    monkeypatch.setattr("app.repositories.business_continuity_plans.db.execute", mock_execute)
    
    result = await bc_plans_repo.delete_plan(plan_id)
    assert result is True


@pytest.mark.anyio("asyncio")
async def test_add_plan_permission(monkeypatch):
    """Test adding a permission to a plan."""
    permission_data = {
        "id": 1,
        "plan_id": 1,
        "user_id": 5,
        "company_id": None,
        "permission_level": "read",
        "created_at": datetime.now(timezone.utc),
    }
    
    async def mock_execute(query: str, params: tuple) -> int:
        return permission_data["id"]
    
    async def mock_fetch_one(query: str, params: tuple) -> dict[str, Any]:
        return permission_data
    
    monkeypatch.setattr("app.repositories.business_continuity_plans.db.execute", mock_execute)
    monkeypatch.setattr("app.repositories.business_continuity_plans.db.fetch_one", mock_fetch_one)
    
    result = await bc_plans_repo.add_plan_permission(
        plan_id=permission_data["plan_id"],
        user_id=permission_data["user_id"],
        permission_level=permission_data["permission_level"],
    )
    
    assert result["id"] == permission_data["id"]
    assert result["user_id"] == permission_data["user_id"]
    assert result["permission_level"] == "read"


@pytest.mark.anyio("asyncio")
async def test_user_can_access_plan_super_admin(monkeypatch):
    """Test that super admins can access any plan."""
    plan_id = 1
    user_id = 1
    
    # Super admin should have access without checking permissions
    result = await bc_plans_repo.user_can_access_plan(plan_id, user_id, is_super_admin=True)
    assert result is True


@pytest.mark.anyio("asyncio")
async def test_user_can_access_plan_with_permission(monkeypatch):
    """Test that users with permissions can access plans."""
    plan_id = 1
    user_id = 5
    
    async def mock_fetch_one(query: str, params: tuple) -> dict[str, Any] | None:
        if "user_id" in query and params == (plan_id, user_id):
            return {"permission_level": "read"}
        return None
    
    monkeypatch.setattr("app.repositories.business_continuity_plans.db.fetch_one", mock_fetch_one)
    
    result = await bc_plans_repo.user_can_access_plan(plan_id, user_id, is_super_admin=False)
    assert result is True


@pytest.mark.anyio("asyncio")
async def test_user_cannot_access_plan_without_permission(monkeypatch):
    """Test that users without permissions cannot access plans."""
    plan_id = 1
    user_id = 999
    
    async def mock_fetch_one(query: str, params: tuple) -> None:
        return None
    
    monkeypatch.setattr("app.repositories.business_continuity_plans.db.fetch_one", mock_fetch_one)
    
    result = await bc_plans_repo.user_can_access_plan(plan_id, user_id, is_super_admin=False)
    assert result is False


@pytest.mark.anyio("asyncio")
async def test_user_can_edit_plan_with_edit_permission(monkeypatch):
    """Test that users with edit permissions can edit plans."""
    plan_id = 1
    user_id = 5
    
    async def mock_fetch_one(query: str, params: tuple) -> dict[str, Any] | None:
        if "user_id" in query and params == (plan_id, user_id):
            return {"permission_level": "edit"}
        return None
    
    monkeypatch.setattr("app.repositories.business_continuity_plans.db.fetch_one", mock_fetch_one)
    
    result = await bc_plans_repo.user_can_edit_plan(plan_id, user_id, is_super_admin=False)
    assert result is True


@pytest.mark.anyio("asyncio")
async def test_user_cannot_edit_plan_with_read_permission(monkeypatch):
    """Test that users with only read permissions cannot edit plans."""
    plan_id = 1
    user_id = 5
    
    async def mock_fetch_one(query: str, params: tuple) -> dict[str, Any] | None:
        if "user_id" in query and params == (plan_id, user_id):
            return {"permission_level": "read"}
        return None
    
    monkeypatch.setattr("app.repositories.business_continuity_plans.db.fetch_one", mock_fetch_one)
    
    result = await bc_plans_repo.user_can_edit_plan(plan_id, user_id, is_super_admin=False)
    assert result is False


@pytest.mark.anyio("asyncio")
async def test_list_plan_permissions(monkeypatch):
    """Test listing all permissions for a plan."""
    plan_id = 1
    permissions = [
        {
            "id": 1,
            "plan_id": plan_id,
            "user_id": 5,
            "company_id": None,
            "permission_level": "read",
            "created_at": datetime.now(timezone.utc),
        },
        {
            "id": 2,
            "plan_id": plan_id,
            "user_id": None,
            "company_id": 3,
            "permission_level": "edit",
            "created_at": datetime.now(timezone.utc),
        },
    ]
    
    async def mock_fetch_all(query: str, params: tuple) -> list[dict[str, Any]]:
        return permissions if params[0] == plan_id else []
    
    monkeypatch.setattr("app.repositories.business_continuity_plans.db.fetch_all", mock_fetch_all)
    
    result = await bc_plans_repo.list_plan_permissions(plan_id)
    assert len(result) == 2
    assert result[0]["user_id"] == 5
    assert result[1]["company_id"] == 3


@pytest.mark.anyio("asyncio")
async def test_update_plan_permission(monkeypatch):
    """Test updating a plan permission."""
    perm_id = 1
    new_level = "edit"
    
    async def mock_execute(query: str, params: tuple) -> None:
        pass
    
    async def mock_fetch_one(query: str, params: tuple) -> dict[str, Any]:
        return {
            "id": perm_id,
            "plan_id": 1,
            "user_id": 5,
            "company_id": None,
            "permission_level": new_level,
            "created_at": datetime.now(timezone.utc),
        }
    
    monkeypatch.setattr("app.repositories.business_continuity_plans.db.execute", mock_execute)
    monkeypatch.setattr("app.repositories.business_continuity_plans.db.fetch_one", mock_fetch_one)
    
    result = await bc_plans_repo.update_plan_permission(perm_id, new_level)
    assert result is not None
    assert result["permission_level"] == new_level


@pytest.mark.anyio("asyncio")
async def test_delete_plan_permission(monkeypatch):
    """Test deleting a plan permission."""
    perm_id = 1
    
    async def mock_execute(query: str, params: tuple) -> None:
        pass
    
    monkeypatch.setattr("app.repositories.business_continuity_plans.db.execute", mock_execute)
    
    result = await bc_plans_repo.delete_plan_permission(perm_id)
    assert result is True
