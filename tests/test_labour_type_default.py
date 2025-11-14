"""Tests for default labour type functionality."""
import pytest
from unittest.mock import AsyncMock, patch

from app.services import labour_types as labour_types_service


@pytest.mark.asyncio
async def test_replace_labour_types_requires_default():
    """Test that at least one labour type must be set as default."""
    definitions = [
        {"id": 1, "code": "REMOTE", "name": "Remote Support", "rate": 95.00, "is_default": False},
        {"id": 2, "code": "ONSITE", "name": "On-site Support", "rate": 150.00, "is_default": False},
    ]
    
    with pytest.raises(ValueError, match="At least one labour type must be set as default"):
        await labour_types_service.replace_labour_types(definitions)


@pytest.mark.asyncio
async def test_replace_labour_types_only_one_default():
    """Test that only one labour type can be set as default."""
    definitions = [
        {"id": 1, "code": "REMOTE", "name": "Remote Support", "rate": 95.00, "is_default": True},
        {"id": 2, "code": "ONSITE", "name": "On-site Support", "rate": 150.00, "is_default": True},
    ]
    
    with pytest.raises(ValueError, match="Only one labour type can be set as default"):
        await labour_types_service.replace_labour_types(definitions)


@pytest.mark.asyncio
async def test_replace_labour_types_with_default():
    """Test that labour types can be saved with one default."""
    definitions = [
        {"id": 1, "code": "REMOTE", "name": "Remote Support", "rate": 95.00, "is_default": True},
        {"id": 2, "code": "ONSITE", "name": "On-site Support", "rate": 150.00, "is_default": False},
    ]
    
    with patch("app.services.labour_types.labour_types_repo") as mock_repo:
        mock_repo.replace_labour_types = AsyncMock(return_value=[
            {"id": 1, "code": "REMOTE", "name": "Remote Support", "rate": 95.00, "is_default": True},
            {"id": 2, "code": "ONSITE", "name": "On-site Support", "rate": 150.00, "is_default": False},
        ])
        
        result = await labour_types_service.replace_labour_types(definitions)
        
        assert len(result) == 2
        # Find the default labour type
        default_type = next((lt for lt in result if lt.get("is_default")), None)
        assert default_type is not None
        assert default_type["code"] == "REMOTE"


@pytest.mark.asyncio
async def test_get_default_labour_type():
    """Test getting the default labour type."""
    with patch("app.services.labour_types.labour_types_repo") as mock_repo:
        mock_repo.get_default_labour_type = AsyncMock(return_value={
            "id": 1,
            "code": "REMOTE",
            "name": "Remote Support",
            "rate": 95.00,
            "is_default": True,
            "created_at": None,
            "updated_at": None,
        })
        
        result = await labour_types_service.get_default_labour_type()
        
        assert result is not None
        assert result["code"] == "REMOTE"
        assert result["is_default"] is True


@pytest.mark.asyncio
async def test_get_default_labour_type_none():
    """Test getting default labour type when none exists."""
    with patch("app.services.labour_types.labour_types_repo") as mock_repo:
        mock_repo.get_default_labour_type = AsyncMock(return_value=None)
        
        result = await labour_types_service.get_default_labour_type()
        
        assert result is None
