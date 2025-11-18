import pytest
from unittest.mock import AsyncMock

from app.repositories import user_permissions as user_permissions_repo


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_add_user_permission(monkeypatch):
    """Test adding a permission to a user."""
    execute_mock = AsyncMock()
    monkeypatch.setattr(user_permissions_repo.db, "execute", execute_mock)
    
    get_mock = AsyncMock(return_value={
        "id": 1,
        "user_id": 5,
        "company_id": 10,
        "permission": "licenses.manage",
        "created_by": 1,
    })
    monkeypatch.setattr(user_permissions_repo, "get_user_permission", get_mock)
    
    result = await user_permissions_repo.add_user_permission(
        user_id=5,
        company_id=10,
        permission="licenses.manage",
        created_by=1,
    )
    
    assert result["user_id"] == 5
    assert result["company_id"] == 10
    assert result["permission"] == "licenses.manage"
    execute_mock.assert_awaited_once()


@pytest.mark.anyio("asyncio")
async def test_list_user_permissions(monkeypatch):
    """Test listing permissions for a user."""
    rows = [
        {"permission": "assets.manage"},
        {"permission": "licenses.manage"},
        {"permission": "shop.access"},
    ]
    fetch_mock = AsyncMock(return_value=rows)
    monkeypatch.setattr(user_permissions_repo.db, "fetch_all", fetch_mock)
    
    result = await user_permissions_repo.list_user_permissions(user_id=5, company_id=10)
    
    assert result == ["assets.manage", "licenses.manage", "shop.access"]
    fetch_mock.assert_awaited_once()


@pytest.mark.anyio("asyncio")
async def test_remove_user_permission(monkeypatch):
    """Test removing a permission from a user."""
    execute_mock = AsyncMock()
    monkeypatch.setattr(user_permissions_repo.db, "execute", execute_mock)
    
    await user_permissions_repo.remove_user_permission(
        user_id=5,
        company_id=10,
        permission="licenses.manage",
    )
    
    execute_mock.assert_awaited_once()
    args = execute_mock.call_args[0]
    assert "DELETE" in args[0]
    assert args[1] == (5, 10, "licenses.manage")


@pytest.mark.anyio("asyncio")
async def test_set_user_permissions_adds_new(monkeypatch):
    """Test setting permissions adds new ones."""
    # Mock current permissions (empty)
    list_mock = AsyncMock(return_value=[])
    monkeypatch.setattr(user_permissions_repo, "list_user_permissions", list_mock)
    
    add_mock = AsyncMock()
    monkeypatch.setattr(user_permissions_repo, "add_user_permission", add_mock)
    
    remove_mock = AsyncMock()
    monkeypatch.setattr(user_permissions_repo, "remove_user_permission", remove_mock)
    
    result = await user_permissions_repo.set_user_permissions(
        user_id=5,
        company_id=10,
        permissions=["licenses.manage", "shop.access"],
        created_by=1,
    )
    
    assert set(result) == {"licenses.manage", "shop.access"}
    assert add_mock.await_count == 2
    assert remove_mock.await_count == 0


@pytest.mark.anyio("asyncio")
async def test_set_user_permissions_removes_old(monkeypatch):
    """Test setting permissions removes ones no longer in list."""
    # Mock current permissions
    list_mock = AsyncMock(return_value=["assets.manage", "licenses.manage", "shop.access"])
    monkeypatch.setattr(user_permissions_repo, "list_user_permissions", list_mock)
    
    add_mock = AsyncMock()
    monkeypatch.setattr(user_permissions_repo, "add_user_permission", add_mock)
    
    remove_mock = AsyncMock()
    monkeypatch.setattr(user_permissions_repo, "remove_user_permission", remove_mock)
    
    result = await user_permissions_repo.set_user_permissions(
        user_id=5,
        company_id=10,
        permissions=["licenses.manage"],
        created_by=1,
    )
    
    assert result == ["licenses.manage"]
    assert add_mock.await_count == 0
    assert remove_mock.await_count == 2


@pytest.mark.anyio("asyncio")
async def test_clear_user_permissions(monkeypatch):
    """Test clearing all permissions for a user."""
    execute_mock = AsyncMock()
    monkeypatch.setattr(user_permissions_repo.db, "execute", execute_mock)
    
    await user_permissions_repo.clear_user_permissions(user_id=5, company_id=10)
    
    execute_mock.assert_awaited_once()
    args = execute_mock.call_args[0]
    assert "DELETE" in args[0]
    assert args[1] == (5, 10)


@pytest.mark.anyio("asyncio")
async def test_user_has_permission_with_user_permission(monkeypatch):
    """Test that user_has_permission checks user-specific permissions."""
    from app.repositories import company_memberships as membership_repo
    
    # Mock user (not super admin)
    user_mock = AsyncMock(return_value={"id": 5, "is_super_admin": False})
    monkeypatch.setattr(membership_repo.user_repo, "get_user_by_id", user_mock)
    
    # Mock memberships with no role permissions for this specific permission
    memberships = [
        {
            "company_id": 10,
            "permissions": ["portal.access"],
        }
    ]
    list_mock = AsyncMock(return_value=memberships)
    monkeypatch.setattr(membership_repo, "list_memberships_for_user", list_mock)
    
    # Mock user-specific permissions
    user_perms_mock = AsyncMock(return_value=["licenses.manage", "shop.access"])
    monkeypatch.setattr(
        membership_repo.user_permissions_repo,
        "list_user_permissions",
        user_perms_mock,
    )
    
    # Check for a permission that's only in user-specific permissions
    result = await membership_repo.user_has_permission(5, "licenses.manage")
    
    assert result is True
    user_perms_mock.assert_awaited_once_with(5, 10)


@pytest.mark.anyio("asyncio")
async def test_user_has_permission_cumulative(monkeypatch):
    """Test that permissions are cumulative (role + user)."""
    from app.repositories import company_memberships as membership_repo
    
    # Mock user (not super admin)
    user_mock = AsyncMock(return_value={"id": 5, "is_super_admin": False})
    monkeypatch.setattr(membership_repo.user_repo, "get_user_by_id", user_mock)
    
    # Mock memberships with some role permissions
    memberships = [
        {
            "company_id": 10,
            "permissions": ["portal.access", "forms.access"],
        }
    ]
    list_mock = AsyncMock(return_value=memberships)
    monkeypatch.setattr(membership_repo, "list_memberships_for_user", list_mock)
    
    # Mock user-specific permissions
    user_perms_mock = AsyncMock(return_value=["licenses.manage", "shop.access"])
    monkeypatch.setattr(
        membership_repo.user_permissions_repo,
        "list_user_permissions",
        user_perms_mock,
    )
    
    # Check for role permission
    result_role = await membership_repo.user_has_permission(5, "forms.access")
    assert result_role is True
    
    # Reset mocks for next call
    list_mock.reset_mock()
    user_perms_mock.reset_mock()
    
    # Check for user-specific permission
    result_user = await membership_repo.user_has_permission(5, "licenses.manage")
    assert result_user is True
