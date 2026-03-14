"""Tests to ensure client secrets and other sensitive fields are never exposed
via the integration modules API routes (GET /{slug} and PUT /{slug}).
"""
from __future__ import annotations

import pytest
from unittest.mock import patch

from fastapi import HTTPException


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# Helpers – a selection of module slugs with sensitive settings
# ---------------------------------------------------------------------------

_SENSITIVE_MODULES = [
    (
        "xero",
        {
            "id": 1,
            "slug": "xero",
            "name": "Xero",
            "description": "Xero accounting integration",
            "icon": None,
            "enabled": True,
            "settings": {
                "client_id": "xero-client-id",
                "client_secret": "SUPER_SECRET_XERO",
                "refresh_token": "REFRESH_TOKEN_VALUE",
                "access_token": "ACCESS_TOKEN_VALUE",
                "tenant_id": "tenant-123",
            },
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
        },
    ),
    (
        "m365-admin",
        {
            "id": 2,
            "slug": "m365-admin",
            "name": "M365 Admin",
            "description": "Microsoft 365 administration",
            "icon": None,
            "enabled": True,
            "settings": {
                "client_id": "azure-client-id",
                "client_secret": "SUPER_SECRET_M365",
                "tenant_id": "tenant-abc",
            },
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
        },
    ),
    (
        "syncro",
        {
            "id": 3,
            "slug": "syncro",
            "name": "Syncro",
            "description": "Syncro RMM",
            "icon": None,
            "enabled": True,
            "settings": {"api_key": "SUPER_SECRET_SYNCRO_KEY"},
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
        },
    ),
    (
        "tacticalrmm",
        {
            "id": 4,
            "slug": "tacticalrmm",
            "name": "TacticalRMM",
            "description": "Tactical RMM integration",
            "icon": None,
            "enabled": False,
            "settings": {"api_key": "SUPER_SECRET_TACTICAL_KEY", "base_url": "https://rmm.example.com"},
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
        },
    ),
    (
        "ntfy",
        {
            "id": 5,
            "slug": "ntfy",
            "name": "Ntfy",
            "description": "Ntfy push notifications",
            "icon": None,
            "enabled": True,
            "settings": {"base_url": "https://ntfy.sh", "auth_token": "SUPER_SECRET_NTFY_TOKEN"},
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
        },
    ),
    (
        "unifi-talk",
        {
            "id": 6,
            "slug": "unifi-talk",
            "name": "Unifi Talk",
            "description": "Unifi Talk integration",
            "icon": None,
            "enabled": True,
            "settings": {
                "base_url": "https://unifi.example.com",
                "username": "admin",
                "password": "SUPER_SECRET_PASSWORD",
            },
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
        },
    ),
]

# The corresponding sensitive field for each slug
_SENSITIVE_FIELD = {
    "xero": ("client_secret", "refresh_token", "access_token"),
    "m365-admin": ("client_secret",),
    "syncro": ("api_key",),
    "tacticalrmm": ("api_key",),
    "ntfy": ("auth_token",),
    "unifi-talk": ("password",),
}


# ---------------------------------------------------------------------------
# GET /{slug} tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio("asyncio")
@pytest.mark.parametrize("slug,raw_module", _SENSITIVE_MODULES)
async def test_get_module_api_redacts_sensitive_fields(slug: str, raw_module: dict):
    """GET /api/integration-modules/{slug} must not return plaintext secrets."""
    from app.api.routes import modules as modules_route

    async def fake_get_module(module_slug: str, *, redact: bool = True):
        assert redact is True, "get_module must be called with redact=True"
        # Simulate what the service does: redact sensitive fields
        from app.services.modules import _redact_module_settings
        return _redact_module_settings(raw_module)

    with (
        patch.object(modules_route.modules_service, "get_module", side_effect=fake_get_module),
        patch("app.api.routes.modules.require_super_admin", return_value={"id": 1}),
    ):
        result = await modules_route.get_module(slug, current_user={"id": 1})

    response_settings = result.settings
    for field in _SENSITIVE_FIELD.get(slug, ()):
        value = response_settings.get(field)
        assert value != raw_module["settings"].get(field), (
            f"GET /{slug}: field '{field}' must be redacted in API response, got plaintext"
        )
        # If the field had a value it should now be masked (not empty and not the real value)
        if raw_module["settings"].get(field):
            assert value == "********", (
                f"GET /{slug}: field '{field}' should be masked as '********'"
            )


@pytest.mark.anyio("asyncio")
async def test_get_module_api_returns_404_for_missing_slug():
    """GET /api/integration-modules/{slug} returns 404 when module not found."""
    from app.api.routes import modules as modules_route

    async def fake_get_module(module_slug: str, *, redact: bool = True):
        return None

    with (
        patch.object(modules_route.modules_service, "get_module", side_effect=fake_get_module),
        patch("app.api.routes.modules.require_super_admin", return_value={"id": 1}),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await modules_route.get_module("nonexistent", current_user={"id": 1})

    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# PUT /{slug} tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio("asyncio")
@pytest.mark.parametrize("slug,raw_module", _SENSITIVE_MODULES)
async def test_put_module_api_redacts_sensitive_fields(slug: str, raw_module: dict):
    """PUT /api/integration-modules/{slug} must not return plaintext secrets."""
    from app.api.routes import modules as modules_route
    from app.schemas.integration_modules import IntegrationModuleUpdate

    async def fake_exists(module_slug: str):
        return raw_module

    async def fake_update_module(module_slug: str, *, enabled=None, settings=None):
        from app.services.modules import _redact_module_settings
        updated = dict(raw_module)
        if settings:
            updated["settings"] = {**raw_module["settings"], **settings}
        if enabled is not None:
            updated["enabled"] = enabled
        return _redact_module_settings(updated)

    payload = IntegrationModuleUpdate(enabled=True, settings={})

    with (
        patch.object(modules_route.module_repo, "get_module", side_effect=fake_exists),
        patch.object(modules_route.modules_service, "update_module", side_effect=fake_update_module),
        patch("app.api.routes.modules.require_super_admin", return_value={"id": 1}),
    ):
        result = await modules_route.update_module(slug, payload=payload, current_user={"id": 1})

    response_settings = result.settings
    for field in _SENSITIVE_FIELD.get(slug, ()):
        value = response_settings.get(field)
        assert value != raw_module["settings"].get(field), (
            f"PUT /{slug}: field '{field}' must be redacted in API response, got plaintext"
        )
        if raw_module["settings"].get(field):
            assert value == "********", (
                f"PUT /{slug}: field '{field}' should be masked as '********'"
            )


@pytest.mark.anyio("asyncio")
async def test_put_module_api_fallback_uses_service_not_repo():
    """PUT /{slug} fallback (when update_module returns None) must use modules_service.get_module."""
    from app.api.routes import modules as modules_route
    from app.schemas.integration_modules import IntegrationModuleUpdate

    raw_module = {
        "id": 10,
        "slug": "xero",
        "name": "Xero",
        "description": None,
        "icon": None,
        "enabled": True,
        "settings": {
            "client_id": "cid",
            "client_secret": "REAL_SECRET",
            "refresh_token": "REAL_REFRESH",
            "access_token": "REAL_ACCESS",
        },
        "created_at": "2024-01-01T00:00:00",
        "updated_at": "2024-01-01T00:00:00",
    }

    async def fake_exists(module_slug: str):
        return raw_module

    # Simulate update_module returning None (edge case)
    async def fake_update_returns_none(module_slug: str, *, enabled=None, settings=None):
        return None

    # The fallback must call modules_service.get_module (which redacts),
    # NOT module_repo.get_module (which does not)
    service_get_called = []

    async def fake_service_get_module(module_slug: str, *, redact: bool = True):
        service_get_called.append(module_slug)
        assert redact is True
        from app.services.modules import _redact_module_settings
        return _redact_module_settings(raw_module)

    payload = IntegrationModuleUpdate(enabled=True, settings={})

    with (
        patch.object(modules_route.module_repo, "get_module", side_effect=fake_exists),
        patch.object(modules_route.modules_service, "update_module", side_effect=fake_update_returns_none),
        patch.object(modules_route.modules_service, "get_module", side_effect=fake_service_get_module),
        patch("app.api.routes.modules.require_super_admin", return_value={"id": 1}),
    ):
        result = await modules_route.update_module("xero", payload=payload, current_user={"id": 1})

    assert len(service_get_called) == 1, "modules_service.get_module must be called in the fallback path"
    assert result.settings.get("client_secret") == "********"
    assert result.settings.get("refresh_token") == "********"
    assert result.settings.get("access_token") == "********"


@pytest.mark.anyio("asyncio")
async def test_put_module_api_returns_404_for_missing_slug():
    """PUT /api/integration-modules/{slug} returns 404 when module not found."""
    from app.api.routes import modules as modules_route
    from app.schemas.integration_modules import IntegrationModuleUpdate

    async def fake_exists(module_slug: str):
        return None

    payload = IntegrationModuleUpdate(enabled=True, settings={})

    with (
        patch.object(modules_route.module_repo, "get_module", side_effect=fake_exists),
        patch("app.api.routes.modules.require_super_admin", return_value={"id": 1}),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await modules_route.update_module("nonexistent", payload=payload, current_user={"id": 1})

    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Verify _redact_module_settings covers all known sensitive module slugs
# ---------------------------------------------------------------------------

def test_redact_covers_all_sensitive_module_slugs():
    """_redact_module_settings must mask secrets for every module that has them."""
    from app.services.modules import _redact_module_settings

    for slug, raw_module in _SENSITIVE_MODULES:
        result = _redact_module_settings(raw_module)
        for field in _SENSITIVE_FIELD.get(slug, ()):
            if raw_module["settings"].get(field):
                assert result["settings"].get(field) == "********", (
                    f"_redact_module_settings did not redact '{field}' for slug '{slug}'"
                )
