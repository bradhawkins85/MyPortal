from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app import main


async def _dummy_receive() -> dict[str, object]:
    return {"type": "http.request", "body": b"", "more_body": False}


def _make_request(path: str = "/shop") -> Request:
    scope = {"type": "http", "method": "GET", "path": path, "headers": []}
    return Request(scope, _dummy_receive)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_load_company_section_denies_without_permission(monkeypatch):
    request = _make_request()
    user = {"id": 9, "company_id": 5, "is_super_admin": False}
    monkeypatch.setattr(main, "_require_authenticated_user", AsyncMock(return_value=(user, None)))
    monkeypatch.setattr(
        main.user_company_repo,
        "get_user_company",
        AsyncMock(return_value={"can_access_cart": 0}),
    )

    result = await main._load_company_section_context(request, permission_field="can_access_cart")

    assert result[-1] is not None


@pytest.mark.anyio("asyncio")
async def test_load_company_section_allows_with_permission(monkeypatch):
    request = _make_request()
    user = {"id": 11, "company_id": 7, "is_super_admin": False}
    membership = {"can_access_orders": 1}
    company = {"id": 7, "name": "Example"}

    monkeypatch.setattr(main, "_require_authenticated_user", AsyncMock(return_value=(user, None)))
    monkeypatch.setattr(main.user_company_repo, "get_user_company", AsyncMock(return_value=membership))
    monkeypatch.setattr(main.company_repo, "get_company_by_id", AsyncMock(return_value=company))

    loaded_user, loaded_membership, loaded_company, company_id, redirect = await main._load_company_section_context(
        request,
        permission_field="can_access_orders",
    )

    assert redirect is None
    assert loaded_user == user
    assert loaded_membership == membership
    assert loaded_company == company
    assert company_id == 7


@pytest.mark.anyio("asyncio")
async def test_load_company_section_super_admin_without_company(monkeypatch):
    request = _make_request()
    user = {"id": 3, "company_id": None, "is_super_admin": True}

    monkeypatch.setattr(main, "_require_authenticated_user", AsyncMock(return_value=(user, None)))

    loaded_user, membership, company, company_id, redirect = await main._load_company_section_context(
        request,
        permission_field="can_access_forms",
        allow_super_admin_without_company=True,
    )

    assert redirect is None
    assert loaded_user == user
    assert membership is None
    assert company is None
    assert company_id is None
