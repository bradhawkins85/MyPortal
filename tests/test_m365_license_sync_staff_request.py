"""Licence sync must adopt a pending new staff request instead of duplicating it."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.services import m365 as m365_service


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _graph_payload() -> dict[str, Any]:
    return {
        "value": [
            {
                "id": "u1",
                "givenName": "Chandra",
                "surname": "Perera",
                "mail": "Chandra.Perera@example.com",
                "accountEnabled": True,
            }
        ]
    }


async def _run_sync(pending: dict[str, Any] | None) -> dict[str, AsyncMock]:
    mocks = {
        "get_by_email": AsyncMock(return_value=None),
        "get_pending": AsyncMock(return_value=pending),
        "set_email": AsyncMock(),
        "create": AsyncMock(return_value={"id": 900}),
        "link": AsyncMock(),
    }
    with (
        patch.object(m365_service, "_graph_get", AsyncMock(return_value=_graph_payload())),
        patch.object(m365_service.staff_repo, "get_staff_by_company_and_email", mocks["get_by_email"]),
        patch.object(m365_service.staff_repo, "get_pending_staff_request_by_name", mocks["get_pending"]),
        patch.object(m365_service.staff_repo, "set_staff_email", mocks["set_email"]),
        patch.object(m365_service.staff_repo, "create_staff", mocks["create"]),
        patch.object(m365_service.license_repo, "link_staff_to_license", mocks["link"]),
        patch.object(m365_service.license_repo, "list_staff_for_license", AsyncMock(return_value=[])),
        patch.object(m365_service.license_repo, "bulk_unlink_staff", AsyncMock()),
    ):
        await m365_service._sync_staff_assignments(
            company_id=1, license_id=10, access_token="t", sku_id="sku"
        )
    return mocks


@pytest.mark.anyio
async def test_licence_sync_adopts_pending_staff_request():
    mocks = await _run_sync({"id": 7, "first_name": "Chandra", "last_name": "Perera", "email": ""})

    mocks["get_pending"].assert_awaited_once_with(1, "Chandra", "Perera")
    mocks["set_email"].assert_awaited_once_with(7, "chandra.perera@example.com")
    mocks["create"].assert_not_awaited()
    mocks["link"].assert_awaited_once_with(7, 10)


@pytest.mark.anyio
async def test_licence_sync_creates_staff_when_no_pending_request():
    mocks = await _run_sync(None)

    mocks["set_email"].assert_not_awaited()
    mocks["create"].assert_awaited_once()
    assert mocks["create"].await_args.kwargs["source"] == "m365"
    mocks["link"].assert_awaited_once_with(900, 10)
