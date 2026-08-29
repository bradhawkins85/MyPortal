import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.features.dmarc import routes


def test_dmarc_context_allows_read_role_and_blocks_management(monkeypatch):
    user = {"id": 7, "company_id": 42, "is_super_admin": False}
    main = SimpleNamespace(_require_authenticated_user=AsyncMock(return_value=(user, None)))
    monkeypatch.setattr(routes, "_main", lambda: main)
    monkeypatch.setattr(
        routes.memberships,
        "get_user_company",
        AsyncMock(return_value={"menu_permissions": {"menu.dmarc": "read"}}),
    )

    assert asyncio.run(routes._context(SimpleNamespace())) == (user, 42)
    with pytest.raises(HTTPException, match="DMARC permission required") as error:
        asyncio.run(routes._context(SimpleNamespace(), "dmarc.manage"))

    assert error.value.status_code == 403


def test_dmarc_context_allows_write_role_to_manage(monkeypatch):
    user = {"id": 7, "company_id": 42, "is_super_admin": False}
    main = SimpleNamespace(_require_authenticated_user=AsyncMock(return_value=(user, None)))
    monkeypatch.setattr(routes, "_main", lambda: main)
    monkeypatch.setattr(
        routes.memberships,
        "get_user_company",
        AsyncMock(return_value={"menu_permissions": {"menu.dmarc": "write"}}),
    )

    assert asyncio.run(routes._context(SimpleNamespace(), "dmarc.manage")) == (user, 42)
