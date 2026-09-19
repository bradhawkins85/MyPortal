import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.routes import defender
from app.security.menu_permissions import MENU_PERMISSION_MAP, normalize_menu_permissions


ADMIN_PERMISSION_KEYS = {
    "menu.admin.users",
    "menu.admin.sessions",
    "menu.admin.benchmarking",
    "menu.admin.rag",
    "menu.admin.cron_calendar",
    "menu.admin.forms",
    "menu.admin.tag_exclusions",
    "menu.admin.tray",
}


def test_new_admin_permissions_are_catalogued_and_default_to_no_access():
    normalized = normalize_menu_permissions({})

    assert ADMIN_PERMISSION_KEYS <= MENU_PERMISSION_MAP.keys()
    assert all(normalized[key] == "none" for key in ADMIN_PERMISSION_KEYS)
    assert all(MENU_PERMISSION_MAP[key].admin_only for key in ADMIN_PERMISSION_KEYS)


def test_defender_context_requires_read_permission(monkeypatch):
    request = object()

    async def require_authenticated_user(_request):
        return {"company_id": 42, "menu_access": {"menu.defender": "none"}}, None

    monkeypatch.setattr(defender, "_main", lambda: SimpleNamespace(
        _require_authenticated_user=require_authenticated_user,
        _menu_can=lambda permissions, key, write=False: False,
    ))

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(defender._portal_context(request))

    assert exc_info.value.status_code == 403
