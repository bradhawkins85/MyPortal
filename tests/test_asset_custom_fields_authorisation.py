"""Regression tests: asset custom field API routes must be authorised."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.dependencies.auth import get_current_user, require_super_admin
from app.api.routes import asset_custom_fields as route
from app.schemas.asset_custom_fields import FieldValueSet


def _dependency_calls(path: str, method: str):
    for api_route in route.router.routes:
        if api_route.path == path and method in api_route.methods:
            return {dep.call for dep in api_route.dependant.dependencies}
    raise AssertionError(f"{method} {path} not found")


@pytest.mark.parametrize(
    "path, method",
    [
        ("/asset-custom-fields/definitions", "POST"),
        ("/asset-custom-fields/definitions/{definition_id}", "PUT"),
        ("/asset-custom-fields/definitions/{definition_id}", "DELETE"),
    ],
)
def test_definition_mutations_require_super_admin(path, method):
    assert require_super_admin in _dependency_calls(path, method)


@pytest.mark.parametrize(
    "path, method",
    [
        ("/asset-custom-fields/definitions", "GET"),
        ("/asset-custom-fields/definitions/{definition_id}", "GET"),
        ("/assets/{asset_id}/custom-fields", "GET"),
        ("/assets/{asset_id}/custom-fields", "POST"),
        ("/assets/{asset_id}/custom-fields/{field_definition_id}", "DELETE"),
    ],
)
def test_read_and_asset_routes_require_authentication(path, method):
    assert get_current_user in _dependency_calls(path, method)


@pytest.fixture
def asset_in_company_4(monkeypatch):
    monkeypatch.setattr(
        route.assets_repo,
        "get_asset_by_id",
        AsyncMock(return_value={"id": 12, "company_id": 4, "name": "Laptop"}),
    )
    monkeypatch.setattr(
        route.membership_repo, "user_has_permission", AsyncMock(return_value=False)
    )


@pytest.mark.asyncio
async def test_user_without_membership_cannot_read_values(monkeypatch, asset_in_company_4):
    monkeypatch.setattr(route.user_company_repo, "get_user_company", AsyncMock(return_value=None))
    values = AsyncMock(return_value=[])
    monkeypatch.setattr(route.custom_fields_repo, "get_asset_field_values", values)

    with pytest.raises(HTTPException) as exc:
        await route.get_asset_custom_fields(12, current_user={"id": 7, "is_super_admin": False})
    assert exc.value.status_code == 404
    values.assert_not_called()


@pytest.mark.asyncio
async def test_member_without_asset_write_cannot_set_values(monkeypatch, asset_in_company_4):
    monkeypatch.setattr(
        route.user_company_repo,
        "get_user_company",
        AsyncMock(return_value={"user_id": 7, "company_id": 4, "can_manage_assets": False}),
    )
    setter = AsyncMock()
    monkeypatch.setattr(route.custom_fields_repo, "set_asset_field_value", setter)

    with pytest.raises(HTTPException) as exc:
        await route.set_asset_custom_fields(
            12,
            [FieldValueSet(field_definition_id=1, value="x")],
            current_user={"id": 7, "is_super_admin": False},
        )
    assert exc.value.status_code == 404
    setter.assert_not_called()


@pytest.mark.asyncio
async def test_member_with_asset_management_can_read_values(monkeypatch, asset_in_company_4):
    get_membership = AsyncMock(
        return_value={"user_id": 7, "company_id": 4, "can_manage_assets": True}
    )
    monkeypatch.setattr(route.user_company_repo, "get_user_company", get_membership)
    monkeypatch.setattr(route.custom_fields_repo, "get_asset_field_values", AsyncMock(return_value=[]))

    assert await route.get_asset_custom_fields(12, current_user={"id": 7, "is_super_admin": False}) == []
    get_membership.assert_awaited_once_with(7, 4)


@pytest.mark.asyncio
async def test_helpdesk_technician_can_delete_value(monkeypatch, asset_in_company_4):
    monkeypatch.setattr(
        route.membership_repo, "user_has_permission", AsyncMock(return_value=True)
    )
    deleter = AsyncMock()
    monkeypatch.setattr(route.custom_fields_repo, "delete_asset_field_value", deleter)

    await route.delete_asset_custom_field(12, 3, current_user={"id": 9, "is_super_admin": False})
    deleter.assert_awaited_once_with(12, 3)
