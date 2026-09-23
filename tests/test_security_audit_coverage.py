"""Focused regression coverage for security-sensitive audit events."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from app.api.routes import modules as module_routes
from app.schemas.integration_modules import IntegrationModuleUpdate
from app.services.audit_diff import REDACTED, diff


def test_module_setting_diff_names_changes_and_redacts_credentials() -> None:
    before, after = diff(
        module_routes._audit_snapshot(
            {"enabled": False, "settings": {"base_url": "old", "api_key": "old-secret"}}
        ),
        module_routes._audit_snapshot(
            {"enabled": True, "settings": {"base_url": "new", "api_key": "new-secret"}}
        ),
    )

    assert before == {
        "enabled": False,
        "setting.api_key": REDACTED,
        "setting.base_url": "old",
    }
    assert after == {
        "enabled": True,
        "setting.api_key": REDACTED,
        "setting.base_url": "new",
    }
    assert "old-secret" not in repr((before, after))
    assert "new-secret" not in repr((before, after))


@pytest.mark.anyio("asyncio")
async def test_module_update_attributes_actor_and_records_only_diff(monkeypatch) -> None:
    now = datetime(2026, 9, 23)
    existing = {
        "id": 7, "slug": "example", "name": "Example", "description": None,
        "icon": None, "enabled": False, "settings": {"url": "old"},
        "created_at": now, "updated_at": now,
    }
    updated = {**existing, "enabled": True, "settings": {"url": "new"}}
    monkeypatch.setattr(module_routes.module_repo, "get_module", AsyncMock(return_value=existing))
    availability = type("Availability", (), {"module_available": lambda self, slug: True})()
    monkeypatch.setattr(module_routes, "get_component_availability", lambda: availability)
    monkeypatch.setattr(module_routes.modules_service, "update_module", AsyncMock(return_value=updated))
    audit_record = AsyncMock()
    monkeypatch.setattr(module_routes.audit_service, "record", audit_record)

    await module_routes.update_module(
        "example",
        IntegrationModuleUpdate(enabled=True, settings={"url": "new"}),
        current_user={"id": 41},
    )

    call = audit_record.await_args.kwargs
    assert call["action"] == "integration.module.configure"
    assert call["user_id"] == 41
    assert call["metadata"] == {"module": "example"}
    assert call["before"] == {"enabled": False, "setting.url": "old"}
    assert call["after"] == {"enabled": True, "setting.url": "new"}
