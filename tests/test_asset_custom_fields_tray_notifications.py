from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.api.routes import asset_custom_fields as route
from app.schemas.asset_custom_fields import FieldValueSet


@pytest.mark.asyncio
async def test_set_asset_custom_fields_can_send_tray_notification(monkeypatch):
    monkeypatch.setattr(
        route.custom_fields_repo,
        "get_field_definition",
        AsyncMock(return_value={"id": 1, "field_type": "text"}),
    )
    monkeypatch.setattr(
        route.custom_fields_repo,
        "set_asset_field_value",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        route.assets_repo,
        "get_asset_by_id",
        AsyncMock(return_value={"id": 12, "company_id": 4, "name": "Laptop 12"}),
    )
    from app.services import tray as tray_service

    notify_mock = AsyncMock(return_value={"targeted": 1, "delivered": 1, "queued": 0})
    monkeypatch.setattr(tray_service, "push_notification_to_company_devices", notify_mock)

    response = await route.set_asset_custom_fields(
        12,
        [FieldValueSet(field_definition_id=1, value="updated")],
        send_tray_notification=True,
    )

    assert response == {"message": "Custom fields updated successfully"}
    notify_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_asset_custom_fields_skips_tray_notification_when_disabled(monkeypatch):
    monkeypatch.setattr(
        route.custom_fields_repo,
        "get_field_definition",
        AsyncMock(return_value={"id": 1, "field_type": "text"}),
    )
    monkeypatch.setattr(
        route.custom_fields_repo,
        "set_asset_field_value",
        AsyncMock(return_value=None),
    )
    from app.services import tray as tray_service

    notify_mock = AsyncMock(return_value={"targeted": 0, "delivered": 0, "queued": 0})
    monkeypatch.setattr(tray_service, "push_notification_to_company_devices", notify_mock)

    response = await route.set_asset_custom_fields(
        12,
        [FieldValueSet(field_definition_id=1, value="updated")],
        send_tray_notification=False,
    )

    assert response == {"message": "Custom fields updated successfully"}
    notify_mock.assert_not_called()
