"""Tests for the m365 repository list_provisioned_company_ids function."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from app.repositories import m365 as m365_repo


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_list_provisioned_company_ids_returns_set_of_ids():
    """list_provisioned_company_ids returns a set of company IDs with credentials."""
    mock_rows = [
        {"company_id": 1},
        {"company_id": 2},
        {"company_id": 5},
    ]
    with patch.object(m365_repo.db, "fetch_all", new=AsyncMock(return_value=mock_rows)):
        result = await m365_repo.list_provisioned_company_ids()

    assert result == {1, 2, 5}


@pytest.mark.anyio("asyncio")
async def test_list_provisioned_company_ids_returns_empty_set_when_none():
    """list_provisioned_company_ids returns an empty set when there are no credentials."""
    with patch.object(m365_repo.db, "fetch_all", new=AsyncMock(return_value=[])):
        result = await m365_repo.list_provisioned_company_ids()

    assert result == set()


@pytest.mark.anyio("asyncio")
async def test_list_provisioned_company_ids_deduplicates():
    """list_provisioned_company_ids de-duplicates company IDs (DISTINCT query)."""
    mock_rows = [
        {"company_id": 3},
        {"company_id": 3},
    ]
    with patch.object(m365_repo.db, "fetch_all", new=AsyncMock(return_value=mock_rows)):
        result = await m365_repo.list_provisioned_company_ids()

    assert result == {3}
    assert len(result) == 1


@pytest.mark.anyio("asyncio")
async def test_list_provisioned_company_ids_coerces_to_int():
    """list_provisioned_company_ids coerces company_id values to int."""
    mock_rows = [{"company_id": "7"}, {"company_id": 8}]
    with patch.object(m365_repo.db, "fetch_all", new=AsyncMock(return_value=mock_rows)):
        result = await m365_repo.list_provisioned_company_ids()

    assert result == {7, 8}
    assert all(isinstance(cid, int) for cid in result)
