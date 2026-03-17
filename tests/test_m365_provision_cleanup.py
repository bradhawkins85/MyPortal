"""Tests for the 'remove old MyPortal apps before re-provisioning' behavior."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch
from typing import Any

from app.services import m365 as m365_service


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------

def _make_app_data(client_id: str = "new-client-id") -> dict[str, Any]:
    return {"id": "app-object-id", "appId": client_id}


def _make_sp_data(sp_id: str = "sp-object-id") -> dict[str, Any]:
    return {"id": sp_id}


def _make_graph_sp_response(graph_sp_id: str = "graph-sp-id") -> dict[str, Any]:
    return {"value": [{"id": graph_sp_id}]}


def _make_role_assignment() -> dict[str, Any]:
    return {"id": "assignment-id"}


def _make_secret_data(secret: str = "plain-text-secret") -> dict[str, Any]:
    return {"secretText": secret}


# ---------------------------------------------------------------------------
# Tests for _graph_delete
# ---------------------------------------------------------------------------

@pytest.mark.anyio("asyncio")
async def test_graph_delete_success():
    """_graph_delete does not raise on 204."""
    from unittest.mock import MagicMock

    with patch("app.services.m365.httpx.AsyncClient") as mock_client_cls:
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_client_cls.return_value.__aenter__.return_value.delete = AsyncMock(
            return_value=mock_response
        )
        # Should complete without raising
        await m365_service._graph_delete(
            "token", "https://graph.microsoft.com/v1.0/applications/abc"
        )


@pytest.mark.anyio("asyncio")
async def test_graph_delete_raises_on_error():
    """_graph_delete raises M365Error on non-200/204 status."""
    from unittest.mock import MagicMock

    with patch("app.services.m365.httpx.AsyncClient") as mock_client_cls:
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = "Forbidden"
        mock_client_cls.return_value.__aenter__.return_value.delete = AsyncMock(
            return_value=mock_response
        )
        with pytest.raises(m365_service.M365Error, match="403"):
            await m365_service._graph_delete(
                "token", "https://graph.microsoft.com/v1.0/applications/abc"
            )


# ---------------------------------------------------------------------------
# Tests for _delete_existing_apps_by_display_name
# ---------------------------------------------------------------------------

@pytest.mark.anyio("asyncio")
async def test_delete_existing_apps_deletes_found_apps():
    """_delete_existing_apps_by_display_name deletes all apps returned by search."""
    deleted_urls: list[str] = []

    async def mock_graph_get(token: str, url: str, **kwargs: Any) -> dict:
        return {
            "value": [
                {"id": "old-app-id-1", "appId": "old-client-1", "displayName": "MyPortal Integration"},
                {"id": "old-app-id-2", "appId": "old-client-2", "displayName": "MyPortal Integration"},
            ]
        }

    async def mock_graph_delete(token: str, url: str) -> None:
        deleted_urls.append(url)

    with (
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
        patch.object(m365_service, "_graph_delete", side_effect=mock_graph_delete),
    ):
        await m365_service._delete_existing_apps_by_display_name("token", "MyPortal Integration")

    assert "https://graph.microsoft.com/v1.0/applications/old-app-id-1" in deleted_urls
    assert "https://graph.microsoft.com/v1.0/applications/old-app-id-2" in deleted_urls
    assert len(deleted_urls) == 2


@pytest.mark.anyio("asyncio")
async def test_delete_existing_apps_no_op_when_none_found():
    """_delete_existing_apps_by_display_name does nothing when no apps exist."""
    deleted_urls: list[str] = []

    async def mock_graph_get(token: str, url: str, **kwargs: Any) -> dict:
        return {"value": []}

    async def mock_graph_delete(token: str, url: str) -> None:
        deleted_urls.append(url)

    with (
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
        patch.object(m365_service, "_graph_delete", side_effect=mock_graph_delete),
    ):
        await m365_service._delete_existing_apps_by_display_name("token", "MyPortal Integration")

    assert deleted_urls == []


@pytest.mark.anyio("asyncio")
async def test_delete_existing_apps_continues_on_search_failure():
    """_delete_existing_apps_by_display_name swallows search errors gracefully."""
    async def mock_graph_get(token: str, url: str, **kwargs: Any) -> dict:
        raise m365_service.M365Error("Graph GET failed (500)", http_status=500)

    deleted_urls: list[str] = []

    async def mock_graph_delete(token: str, url: str) -> None:
        deleted_urls.append(url)

    with (
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
        patch.object(m365_service, "_graph_delete", side_effect=mock_graph_delete),
    ):
        # Should not raise
        await m365_service._delete_existing_apps_by_display_name("token", "MyPortal Integration")

    assert deleted_urls == []


@pytest.mark.anyio("asyncio")
async def test_delete_existing_apps_continues_on_delete_failure():
    """_delete_existing_apps_by_display_name continues even if deleting one app fails."""
    deleted_urls: list[str] = []

    async def mock_graph_get(token: str, url: str, **kwargs: Any) -> dict:
        return {
            "value": [
                {"id": "app-1", "appId": "client-1", "displayName": "MyPortal Integration"},
                {"id": "app-2", "appId": "client-2", "displayName": "MyPortal Integration"},
            ]
        }

    async def mock_graph_delete(token: str, url: str) -> None:
        if "app-1" in url:
            raise m365_service.M365Error("DELETE failed (403)", http_status=403)
        deleted_urls.append(url)

    with (
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
        patch.object(m365_service, "_graph_delete", side_effect=mock_graph_delete),
    ):
        # Should not raise; should attempt both
        await m365_service._delete_existing_apps_by_display_name("token", "MyPortal Integration")

    assert "https://graph.microsoft.com/v1.0/applications/app-2" in deleted_urls


# ---------------------------------------------------------------------------
# Tests for provision_app_registration – cleanup integration
# ---------------------------------------------------------------------------

@pytest.mark.anyio("asyncio")
async def test_provision_app_registration_deletes_existing_before_creating():
    """provision_app_registration removes existing apps before creating a new one."""
    deleted_urls: list[str] = []
    get_calls: list[str] = []

    async def mock_graph_get(token: str, url: str, **kwargs: Any) -> dict:
        get_calls.append(url)
        if "filter=displayName" in url:
            # Return one pre-existing app with the same name
            return {
                "value": [
                    {"id": "stale-app-id", "appId": "stale-client-id", "displayName": "MyPortal Integration"}
                ]
            }
        if "servicePrincipals" in url and "appId eq" in url:
            return {"value": [{"id": "graph-sp-id"}]}
        return {"value": []}

    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        if "/applications" in url and "addPassword" not in url and "owners" not in url:
            return {"id": "new-app-object-id", "appId": "new-client-id"}
        if "/servicePrincipals" in url and "appRoleAssignments" not in url:
            return {"id": "new-sp-id"}
        if "appRoleAssignments" in url:
            return {"id": "role-assignment-id"}
        if "owners/$ref" in url:
            return {}
        if "addPassword" in url:
            return {"secretText": "new-secret"}
        return {}

    async def mock_graph_delete(token: str, url: str) -> None:
        deleted_urls.append(url)

    with (
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
        patch.object(m365_service, "_graph_post", side_effect=mock_graph_post),
        patch.object(m365_service, "_graph_delete", side_effect=mock_graph_delete),
    ):
        result = await m365_service.provision_app_registration(
            access_token="token",
            display_name="MyPortal Integration",
        )

    # Old app should have been deleted
    assert "https://graph.microsoft.com/v1.0/applications/stale-app-id" in deleted_urls
    # New app should have been created
    assert result["client_id"] == "new-client-id"
    assert result["app_object_id"] == "new-app-object-id"


@pytest.mark.anyio("asyncio")
async def test_provision_app_registration_succeeds_when_no_existing_app():
    """provision_app_registration works normally when no pre-existing app is found."""
    deleted_urls: list[str] = []

    async def mock_graph_get(token: str, url: str, **kwargs: Any) -> dict:
        if "filter=displayName" in url:
            return {"value": []}
        if "servicePrincipals" in url and "appId eq" in url:
            return {"value": [{"id": "graph-sp-id"}]}
        return {"value": []}

    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        if "/applications" in url and "addPassword" not in url and "owners" not in url:
            return {"id": "app-object-id", "appId": "client-id"}
        if "/servicePrincipals" in url and "appRoleAssignments" not in url:
            return {"id": "sp-id"}
        if "appRoleAssignments" in url:
            return {"id": "role-id"}
        if "owners/$ref" in url:
            return {}
        if "addPassword" in url:
            return {"secretText": "secret"}
        return {}

    async def mock_graph_delete(token: str, url: str) -> None:
        deleted_urls.append(url)

    with (
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
        patch.object(m365_service, "_graph_post", side_effect=mock_graph_post),
        patch.object(m365_service, "_graph_delete", side_effect=mock_graph_delete),
    ):
        result = await m365_service.provision_app_registration(
            access_token="token",
            display_name="MyPortal Integration",
        )

    assert deleted_urls == [], "No deletions should occur when no pre-existing app is found"
    assert result["client_id"] == "client-id"


# ---------------------------------------------------------------------------
# Tests for provision_csp_admin_app_registration – cleanup integration
# ---------------------------------------------------------------------------

@pytest.mark.anyio("asyncio")
async def test_provision_csp_admin_deletes_existing_before_creating():
    """provision_csp_admin_app_registration removes existing apps before creating."""
    deleted_urls: list[str] = []

    async def mock_graph_get(token: str, url: str, **kwargs: Any) -> dict:
        if "filter=displayName" in url:
            return {
                "value": [
                    {"id": "stale-csp-app-id", "appId": "stale-csp-client", "displayName": "MyPortal CSP Admin"}
                ]
            }
        if "servicePrincipals" in url and "appId eq" in url:
            return {"value": [{"id": "graph-sp-id"}]}
        return {"value": []}

    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        if "/applications" in url and "addPassword" not in url and "owners" not in url:
            return {"id": "new-csp-app-id", "appId": "new-csp-client"}
        if "/servicePrincipals" in url and "appRoleAssignments" not in url:
            return {"id": "new-csp-sp-id"}
        if "appRoleAssignments" in url:
            return {"id": "role-id"}
        if "owners/$ref" in url:
            return {}
        if "addPassword" in url:
            return {"secretText": "csp-secret"}
        return {}

    async def mock_graph_delete(token: str, url: str) -> None:
        deleted_urls.append(url)

    with (
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
        patch.object(m365_service, "_graph_post", side_effect=mock_graph_post),
        patch.object(m365_service, "_graph_delete", side_effect=mock_graph_delete),
    ):
        result = await m365_service.provision_csp_admin_app_registration(
            access_token="token",
            tenant_id="partner-tenant-id",
            display_name="MyPortal CSP Admin",
        )

    # Old CSP admin app should have been deleted
    assert "https://graph.microsoft.com/v1.0/applications/stale-csp-app-id" in deleted_urls
    # New app should have been created
    assert result["client_id"] == "new-csp-client"
    assert result["app_object_id"] == "new-csp-app-id"
