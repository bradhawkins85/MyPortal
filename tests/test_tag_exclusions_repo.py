from typing import Any

import pytest

from app.repositories import tag_exclusions as tag_exclusions_repo


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_add_and_list_tag_exclusions(monkeypatch):
    """Test adding and listing tag exclusions."""
    stored_exclusions: list[dict[str, Any]] = []
    next_id = 1

    async def fake_add_exclusion(tag_slug: str, created_by: int | None = None):
        nonlocal next_id
        exclusion = {
            "id": next_id,
            "tag_slug": tag_slug,
            "created_at": None,
            "created_by": created_by,
        }
        stored_exclusions.append(exclusion)
        next_id += 1
        return exclusion

    async def fake_list_exclusions():
        return stored_exclusions.copy()

    async def fake_is_excluded(tag_slug: str):
        return any(e["tag_slug"] == tag_slug for e in stored_exclusions)

    monkeypatch.setattr(tag_exclusions_repo, "add_tag_exclusion", fake_add_exclusion)
    monkeypatch.setattr(tag_exclusions_repo, "list_tag_exclusions", fake_list_exclusions)
    monkeypatch.setattr(tag_exclusions_repo, "is_tag_excluded", fake_is_excluded)

    # Add an exclusion
    result = await tag_exclusions_repo.add_tag_exclusion("test-tag", 1)
    assert result is not None
    assert result["tag_slug"] == "test-tag"
    assert result["created_by"] == 1

    # List exclusions
    exclusions = await tag_exclusions_repo.list_tag_exclusions()
    assert len(exclusions) == 1
    assert exclusions[0]["tag_slug"] == "test-tag"

    # Check if excluded
    is_excluded = await tag_exclusions_repo.is_tag_excluded("test-tag")
    assert is_excluded is True

    is_excluded = await tag_exclusions_repo.is_tag_excluded("other-tag")
    assert is_excluded is False


@pytest.mark.anyio
async def test_delete_tag_exclusion(monkeypatch):
    """Test deleting tag exclusions."""
    stored_exclusions: list[dict[str, Any]] = [
        {"id": 1, "tag_slug": "test-tag", "created_at": None, "created_by": 1}
    ]

    async def fake_delete_exclusion(tag_slug: str):
        for i, exclusion in enumerate(stored_exclusions):
            if exclusion["tag_slug"] == tag_slug:
                stored_exclusions.pop(i)
                return True
        return False

    monkeypatch.setattr(tag_exclusions_repo, "delete_tag_exclusion", fake_delete_exclusion)

    # Delete existing exclusion
    result = await tag_exclusions_repo.delete_tag_exclusion("test-tag")
    assert result is True
    assert len(stored_exclusions) == 0

    # Try to delete non-existent exclusion
    result = await tag_exclusions_repo.delete_tag_exclusion("non-existent")
    assert result is False


@pytest.mark.anyio
async def test_get_excluded_tag_slugs(monkeypatch):
    """Test getting all excluded tag slugs as a set."""
    stored_exclusions: list[dict[str, Any]] = [
        {"id": 1, "tag_slug": "tag-one", "created_at": None, "created_by": 1},
        {"id": 2, "tag_slug": "tag-two", "created_at": None, "created_by": 1},
        {"id": 3, "tag_slug": "tag-three", "created_at": None, "created_by": 1},
    ]

    async def fake_get_slugs():
        return {e["tag_slug"] for e in stored_exclusions}

    monkeypatch.setattr(tag_exclusions_repo, "get_excluded_tag_slugs", fake_get_slugs)

    result = await tag_exclusions_repo.get_excluded_tag_slugs()
    assert result == {"tag-one", "tag-two", "tag-three"}
