from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, status

from app.api.routes import bcp


@pytest.mark.asyncio
async def test_delete_distribution_entry_still_awaits_auth_before_delete(monkeypatch):
    request = MagicMock()
    require_edit = AsyncMock(return_value=({"id": 7}, 101))
    delete_entry = AsyncMock(return_value=True)

    monkeypatch.setattr(bcp, "_require_bcp_edit", require_edit)
    monkeypatch.setattr(bcp.bcp_repo, "delete_distribution_entry", delete_entry)

    response = await bcp.delete_distribution_entry(request, 55)

    require_edit.assert_awaited_once_with(request)
    delete_entry.assert_awaited_once_with(55)
    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/bcp"


@pytest.mark.asyncio
async def test_delete_distribution_entry_does_not_mutate_when_auth_fails(monkeypatch):
    request = MagicMock()
    delete_entry = AsyncMock()

    async def fail_auth(_request):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    monkeypatch.setattr(bcp, "_require_bcp_edit", fail_auth)
    monkeypatch.setattr(bcp.bcp_repo, "delete_distribution_entry", delete_entry)

    with pytest.raises(HTTPException) as exc_info:
        await bcp.delete_distribution_entry(request, 55)

    delete_entry.assert_not_awaited()
    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
