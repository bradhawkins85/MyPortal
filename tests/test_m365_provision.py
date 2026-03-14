"""Tests for the Microsoft 365 enterprise app provisioning service."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch
from typing import Any

from app.services import m365 as m365_service


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# Helpers
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
# Tests for _graph_post
# ---------------------------------------------------------------------------

@pytest.mark.anyio("asyncio")
async def test_graph_post_success():
    """_graph_post returns parsed JSON on 200/201."""
    from unittest.mock import MagicMock

    with patch("app.services.m365.httpx.AsyncClient") as mock_client_cls:
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "abc"}
        mock_client_cls.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=mock_response
        )

        result = await m365_service._graph_post(
            "token", "https://graph.microsoft.com/v1.0/applications", {"name": "test"}
        )

    assert result == {"id": "abc"}


@pytest.mark.anyio("asyncio")
async def test_graph_post_raises_on_error():
    """_graph_post raises M365Error on non-200/201 status."""
    from unittest.mock import MagicMock

    with patch("app.services.m365.httpx.AsyncClient") as mock_client_cls:
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = "Forbidden"
        mock_client_cls.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=mock_response
        )

        with pytest.raises(m365_service.M365Error, match="403"):
            await m365_service._graph_post(
                "token",
                "https://graph.microsoft.com/v1.0/applications",
                {},
            )


# ---------------------------------------------------------------------------
# Tests for provision_app_registration
# ---------------------------------------------------------------------------

@pytest.mark.anyio("asyncio")
async def test_provision_app_registration_success():
    """provision_app_registration returns a dict with client_id, client_secret, etc."""
    access_token = "delegated-access-token"

    call_order: list[str] = []

    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        call_order.append(url)
        if "/applications" in url and "addPassword" not in url and "owners" not in url:
            return _make_app_data("provisioned-client-id")
        if "/servicePrincipals" in url and "appRoleAssignments" not in url:
            return _make_sp_data("provisioned-sp-id")
        if "appRoleAssignments" in url:
            return _make_role_assignment()
        if "owners/$ref" in url:
            return {}  # 204 No Content → empty dict
        if "addPassword" in url:
            return _make_secret_data("provisioned-secret")
        return {}

    async def mock_graph_get(token: str, url: str) -> dict:
        if "servicePrincipals" in url:
            return _make_graph_sp_response("graph-sp-id")
        return {}

    with (
        patch.object(m365_service, "_graph_post", side_effect=mock_graph_post),
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
    ):
        result = await m365_service.provision_app_registration(
            access_token=access_token,
            display_name="MyPortal – Acme Corp",
        )

    assert result["client_id"] == "provisioned-client-id"
    assert result["client_secret"] == "provisioned-secret"
    assert result["app_object_id"] == "app-object-id"
    # Verify all required Graph calls were made
    assert any("/applications" in u and "addPassword" not in u for u in call_order), \
        "Should POST to /applications to create app registration"
    assert any("/servicePrincipals" in u and "appRoleAssignments" not in u for u in call_order), \
        "Should POST to /servicePrincipals to create service principal"
    assert sum(1 for u in call_order if "appRoleAssignments" in u) == len(
        m365_service._PROVISION_APP_ROLES
    ), "Should grant one role assignment per required role"
    assert any("addPassword" in u for u in call_order), \
        "Should POST to addPassword to create client secret"


@pytest.mark.anyio("asyncio")
async def test_provision_app_registration_missing_graph_sp():
    """provision_app_registration raises M365Error if Graph SP not found."""
    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        if "/applications" in url and "addPassword" not in url and "owners" not in url:
            return _make_app_data()
        if "/servicePrincipals" in url and "appRoleAssignments" not in url:
            return _make_sp_data()
        return {}

    async def mock_graph_get(token: str, url: str) -> dict:
        # Return empty list – Graph SP not found
        return {"value": []}

    with (
        patch.object(m365_service, "_graph_post", side_effect=mock_graph_post),
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
    ):
        with pytest.raises(m365_service.M365Error, match="Microsoft Graph service principal"):
            await m365_service.provision_app_registration(
                access_token="token",
                display_name="Test App",
            )


@pytest.mark.anyio("asyncio")
async def test_provision_app_registration_default_display_name():
    """provision_app_registration uses default display name when not specified."""
    captured_payloads: list[dict] = []

    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        captured_payloads.append({"url": url, "payload": payload})
        if "/applications" in url and "addPassword" not in url and "owners" not in url:
            return _make_app_data()
        if "/servicePrincipals" in url and "appRoleAssignments" not in url:
            return _make_sp_data()
        if "appRoleAssignments" in url:
            return _make_role_assignment()
        if "owners/$ref" in url:
            return {}
        if "addPassword" in url:
            return _make_secret_data()
        return {}

    async def mock_graph_get(token: str, url: str) -> dict:
        return _make_graph_sp_response()

    with (
        patch.object(m365_service, "_graph_post", side_effect=mock_graph_post),
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
    ):
        await m365_service.provision_app_registration(access_token="token")

    app_create = next(
        p for p in captured_payloads
        if "/applications" in p["url"] and "addPassword" not in p["url"]
    )
    assert app_create["payload"]["displayName"] == "MyPortal Integration"


@pytest.mark.anyio("asyncio")
async def test_provision_scope_constant():
    """PROVISION_SCOPE contains the required permissions."""
    assert "Application.ReadWrite.All" in m365_service.PROVISION_SCOPE
    assert "AppRoleAssignment.ReadWrite.All" in m365_service.PROVISION_SCOPE
    assert "offline_access" in m365_service.PROVISION_SCOPE


@pytest.mark.anyio("asyncio")
async def test_provision_app_roles_constant():
    """_PROVISION_APP_ROLES contains known Microsoft Graph permission GUIDs."""
    # User.Read.All
    assert "df021288-bdef-4463-88db-98f22de89214" in m365_service._PROVISION_APP_ROLES
    # Directory.Read.All
    assert "7ab1d382-f21e-4acd-a863-ba3e13f7da61" in m365_service._PROVISION_APP_ROLES


# ---------------------------------------------------------------------------
# Tests for extract_tenant_id_from_token
# ---------------------------------------------------------------------------

def _make_jwt(payload: dict) -> str:
    """Build a minimal JWT with the given payload (no real signature)."""
    import base64, json
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "RS256", "typ": "JWT"}).encode()
    ).rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(
        json.dumps(payload).encode()
    ).rstrip(b"=").decode()
    return f"{header}.{body}.fakesig"


def test_extract_tenant_id_success():
    """extract_tenant_id_from_token returns the tid claim from a valid JWT."""
    token = _make_jwt({"tid": "abc-def-1234", "sub": "user123"})
    result = m365_service.extract_tenant_id_from_token(token)
    assert result == "abc-def-1234"


def test_extract_tenant_id_strips_whitespace():
    """Whitespace around the tid value is stripped."""
    token = _make_jwt({"tid": "  spaced-tenant  ", "sub": "user"})
    result = m365_service.extract_tenant_id_from_token(token)
    assert result == "spaced-tenant"


def test_extract_tenant_id_missing_tid():
    """Raises M365Error when the tid claim is absent."""
    token = _make_jwt({"sub": "user123", "oid": "some-oid"})
    with pytest.raises(m365_service.M365Error, match="tid"):
        m365_service.extract_tenant_id_from_token(token)


def test_extract_tenant_id_malformed_jwt():
    """Raises M365Error for a string that is not a valid JWT."""
    with pytest.raises(m365_service.M365Error, match="[Mm]alformed|segment|decode"):
        m365_service.extract_tenant_id_from_token("not-a-jwt")


def test_extract_tenant_id_invalid_base64():
    """Raises M365Error when the payload segment is not valid base64."""
    with pytest.raises(m365_service.M365Error):
        m365_service.extract_tenant_id_from_token("header.!!!invalid!!!.sig")


def test_discover_scope_constant():
    """DISCOVER_SCOPE contains the expected OpenID scopes."""
    assert "openid" in m365_service.DISCOVER_SCOPE
    assert "profile" in m365_service.DISCOVER_SCOPE
