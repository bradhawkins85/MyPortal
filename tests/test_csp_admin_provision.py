"""Tests for CSP/Lighthouse admin app auto-provisioning and renewal."""
from __future__ import annotations

import pytest
from datetime import date, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import m365 as m365_service


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_csp_admin_provision_mocks(
    client_id: str = "csp-admin-client-id",
    sp_id: str = "csp-admin-sp-id",
    app_obj_id: str = "csp-admin-app-obj-id",
    secret: str = "csp-admin-plain-secret",
    key_id: str = "csp-admin-key-id",
) -> tuple[Any, Any]:
    """Return (mock_graph_post, mock_graph_get) for a successful CSP admin provision flow."""

    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        if "/applications" in url and "addPassword" not in url and "owners" not in url:
            return {"id": app_obj_id, "appId": client_id}
        if "/servicePrincipals" in url and "appRoleAssignments" not in url:
            return {"id": sp_id}
        if "appRoleAssignments" in url:
            return {"id": "assignment-id"}
        if "owners/$ref" in url:
            return {}
        if "addPassword" in url:
            return {"secretText": secret, "keyId": key_id}
        return {}

    async def mock_graph_get(token: str, url: str) -> dict:
        if "servicePrincipals" in url:
            return {"value": [{"id": "graph-sp-id"}]}
        return {}

    return mock_graph_post, mock_graph_get


# ---------------------------------------------------------------------------
# Tests: provision_csp_admin_app_registration
# ---------------------------------------------------------------------------

@pytest.mark.anyio("asyncio")
async def test_csp_admin_provision_returns_required_keys():
    """provision_csp_admin_app_registration returns a dict with all required fields."""
    mock_post, mock_get = _make_csp_admin_provision_mocks()

    with (
        patch.object(m365_service, "_graph_post", side_effect=mock_post),
        patch.object(m365_service, "_graph_get", side_effect=mock_get),
    ):
        result = await m365_service.provision_csp_admin_app_registration(
            access_token="tok",
            tenant_id="partner-tenant-id",
        )

    assert isinstance(result, dict)
    assert result["client_id"] == "csp-admin-client-id"
    assert result["client_secret"] == "csp-admin-plain-secret"
    assert result["app_object_id"] == "csp-admin-app-obj-id"
    assert result["client_secret_key_id"] == "csp-admin-key-id"
    assert result["tenant_id"] == "partner-tenant-id"
    assert isinstance(result["client_secret_expires_at"], datetime)


@pytest.mark.anyio("asyncio")
async def test_csp_admin_provision_creates_app_with_correct_permissions():
    """provision_csp_admin_app_registration creates the app with Directory.Read.All + Application.ReadWrite.OwnedBy."""
    captured_payloads: list[dict] = []
    mock_post, mock_get = _make_csp_admin_provision_mocks()

    async def capturing_post(token: str, url: str, payload: dict) -> dict:
        captured_payloads.append({"url": url, "payload": payload})
        return await mock_post(token, url, payload)

    with (
        patch.object(m365_service, "_graph_post", side_effect=capturing_post),
        patch.object(m365_service, "_graph_get", side_effect=mock_get),
    ):
        await m365_service.provision_csp_admin_app_registration(
            access_token="tok",
            tenant_id="partner-tenant-id",
        )

    # Find the app creation call
    app_creation = next(
        c for c in captured_payloads
        if "/applications" in c["url"]
        and "addPassword" not in c["url"]
        and "owners" not in c["url"]
    )
    resource_access_ids = [
        entry["id"]
        for ra in app_creation["payload"].get("requiredResourceAccess", [])
        for entry in ra.get("resourceAccess", [])
    ]
    # Directory.Read.All delegated (Scope)
    assert m365_service._DIRECTORY_READ_ALL_SCOPE_ID in resource_access_ids
    # Application.ReadWrite.OwnedBy (Role)
    assert "18a4783c-866b-4cc7-a460-3d5e5662c884" in resource_access_ids


@pytest.mark.anyio("asyncio")
async def test_csp_admin_provision_grants_consent_for_app_roles():
    """provision_csp_admin_app_registration grants admin consent for CSP admin roles."""
    consent_calls: list[str] = []
    mock_post, mock_get = _make_csp_admin_provision_mocks()

    async def tracking_post(token: str, url: str, payload: dict) -> dict:
        if "appRoleAssignments" in url:
            consent_calls.append(payload.get("appRoleId", ""))
        return await mock_post(token, url, payload)

    with (
        patch.object(m365_service, "_graph_post", side_effect=tracking_post),
        patch.object(m365_service, "_graph_get", side_effect=mock_get),
    ):
        await m365_service.provision_csp_admin_app_registration(
            access_token="tok",
            tenant_id="partner-tenant-id",
        )

    # Both _CSP_ADMIN_APP_ROLES should be consented
    for role_id in m365_service._CSP_ADMIN_APP_ROLES:
        assert role_id in consent_calls, f"Missing consent for role {role_id}"


@pytest.mark.anyio("asyncio")
async def test_csp_admin_provision_adds_sp_as_owner():
    """provision_csp_admin_app_registration adds the SP as an owner for self-renewal."""
    owner_calls: list[str] = []
    mock_post, mock_get = _make_csp_admin_provision_mocks()

    async def tracking_post(token: str, url: str, payload: dict) -> dict:
        if "owners/$ref" in url:
            owner_calls.append(url)
        return await mock_post(token, url, payload)

    with (
        patch.object(m365_service, "_graph_post", side_effect=tracking_post),
        patch.object(m365_service, "_graph_get", side_effect=mock_get),
    ):
        await m365_service.provision_csp_admin_app_registration(
            access_token="tok",
            tenant_id="partner-tenant-id",
        )

    assert len(owner_calls) == 1


@pytest.mark.anyio("asyncio")
async def test_csp_admin_provision_owner_failure_is_nonfatal():
    """provision_csp_admin_app_registration continues even if the owner-add step fails."""
    mock_post, mock_get = _make_csp_admin_provision_mocks()

    async def failing_owner_post(token: str, url: str, payload: dict) -> dict:
        if "owners/$ref" in url:
            raise m365_service.M365Error("Forbidden")
        return await mock_post(token, url, payload)

    with (
        patch.object(m365_service, "_graph_post", side_effect=failing_owner_post),
        patch.object(m365_service, "_graph_get", side_effect=mock_get),
    ):
        result = await m365_service.provision_csp_admin_app_registration(
            access_token="tok",
            tenant_id="partner-tenant-id",
        )

    # Should still return credentials even though owner-add failed
    assert result["client_id"] == "csp-admin-client-id"
    assert result["client_secret"] == "csp-admin-plain-secret"


@pytest.mark.anyio("asyncio")
async def test_csp_admin_provision_uses_configurable_lifetime():
    """provision_csp_admin_app_registration uses M365_CLIENT_SECRET_LIFETIME_DAYS."""
    captured: list[dict] = []
    mock_post, mock_get = _make_csp_admin_provision_mocks()

    async def capturing_post(token: str, url: str, payload: dict) -> dict:
        captured.append({"url": url, "payload": payload})
        return await mock_post(token, url, payload)

    mock_settings = MagicMock()
    mock_settings.m365_client_secret_lifetime_days = 365

    with (
        patch.object(m365_service, "_graph_post", side_effect=capturing_post),
        patch.object(m365_service, "_graph_get", side_effect=mock_get),
        patch("app.services.m365.get_settings", return_value=mock_settings),
    ):
        result = await m365_service.provision_csp_admin_app_registration(
            access_token="tok",
            tenant_id="partner-tenant-id",
        )

    password_call = next(c for c in captured if "addPassword" in c["url"])
    end_dt = password_call["payload"]["passwordCredential"]["endDateTime"]
    expected_expiry = date.today() + timedelta(days=365)
    assert end_dt.startswith(expected_expiry.isoformat())

    # client_secret_expires_at should reflect the 365-day lifetime
    expires_at: datetime = result["client_secret_expires_at"]
    assert (expires_at.date() - date.today()).days == 365


@pytest.mark.anyio("asyncio")
async def test_csp_admin_provision_raises_if_graph_sp_not_found():
    """provision_csp_admin_app_registration raises M365Error if Graph SP missing."""
    mock_post, _ = _make_csp_admin_provision_mocks()

    async def mock_get_no_graph(token: str, url: str) -> dict:
        return {"value": []}  # No Microsoft Graph SP found

    with (
        patch.object(m365_service, "_graph_post", side_effect=mock_post),
        patch.object(m365_service, "_graph_get", side_effect=mock_get_no_graph),
    ):
        with pytest.raises(m365_service.M365Error, match="Microsoft Graph service principal"):
            await m365_service.provision_csp_admin_app_registration(
                access_token="tok",
                tenant_id="partner-tenant-id",
            )


# ---------------------------------------------------------------------------
# Tests: get_admin_m365_credentials
# ---------------------------------------------------------------------------

@pytest.mark.anyio("asyncio")
async def test_get_admin_credentials_returns_none_when_no_module():
    """get_admin_m365_credentials returns None when module does not exist."""
    with patch.object(m365_service.modules_repo, "get_module", AsyncMock(return_value=None)):
        result = await m365_service.get_admin_m365_credentials()
    assert result is None


@pytest.mark.anyio("asyncio")
async def test_get_admin_credentials_returns_none_when_empty_settings():
    """get_admin_m365_credentials returns None when client_id/secret are empty."""
    mock_module = {"settings": {"client_id": "", "client_secret": ""}}
    with patch.object(m365_service.modules_repo, "get_module", AsyncMock(return_value=mock_module)):
        result = await m365_service.get_admin_m365_credentials()
    assert result is None


@pytest.mark.anyio("asyncio")
async def test_get_admin_credentials_returns_plaintext_secret():
    """get_admin_m365_credentials returns plaintext secrets unchanged."""
    mock_module = {
        "settings": {
            "client_id": "my-client-id",
            "client_secret": "plaintext-secret",
            "tenant_id": "my-tenant",
            "app_object_id": "my-app-obj",
        }
    }
    with patch.object(m365_service.modules_repo, "get_module", AsyncMock(return_value=mock_module)):
        result = await m365_service.get_admin_m365_credentials()

    assert result is not None
    assert result["client_id"] == "my-client-id"
    assert result["client_secret"] == "plaintext-secret"
    assert result["tenant_id"] == "my-tenant"
    assert result["app_object_id"] == "my-app-obj"


@pytest.mark.anyio("asyncio")
async def test_get_admin_credentials_decrypts_encrypted_secret():
    """get_admin_m365_credentials decrypts auto-provisioned encrypted secrets."""
    from app.security.encryption import encrypt_secret

    plain = "super-secret-value"
    encrypted = encrypt_secret(plain)
    mock_module = {
        "settings": {
            "client_id": "my-client-id",
            "client_secret": encrypted,
        }
    }
    with patch.object(m365_service.modules_repo, "get_module", AsyncMock(return_value=mock_module)):
        result = await m365_service.get_admin_m365_credentials()

    assert result is not None
    assert result["client_secret"] == plain


# ---------------------------------------------------------------------------
# Tests: update_admin_m365_credentials
# ---------------------------------------------------------------------------

@pytest.mark.anyio("asyncio")
async def test_update_admin_credentials_encrypts_secret():
    """update_admin_m365_credentials encrypts the client_secret before storage."""
    from app.security.encryption import decrypt_secret

    captured_settings: list[dict] = []

    async def mock_get_module(slug: str) -> dict:
        return {"settings": {"client_id": "", "client_secret": ""}}

    async def mock_update_module(slug: str, *, enabled=None, settings=None, **kwargs):
        if settings:
            captured_settings.append(settings)
        return None

    with (
        patch.object(m365_service.modules_repo, "get_module", side_effect=mock_get_module),
        patch.object(m365_service.modules_repo, "update_module", side_effect=mock_update_module),
    ):
        await m365_service.update_admin_m365_credentials(
            client_id="new-client-id",
            client_secret="new-plain-secret",
            tenant_id="partner-tid",
            app_object_id="app-obj-id",
            client_secret_key_id="key-id-123",
            client_secret_expires_at=datetime(2028, 1, 1),
        )

    assert len(captured_settings) == 1
    stored = captured_settings[0]
    assert stored["client_id"] == "new-client-id"
    # Secret should be stored encrypted
    assert stored["client_secret"] != "new-plain-secret"
    assert decrypt_secret(stored["client_secret"]) == "new-plain-secret"
    assert stored["tenant_id"] == "partner-tid"
    assert stored["app_object_id"] == "app-obj-id"
    assert stored["client_secret_key_id"] == "key-id-123"
    assert stored["client_secret_expires_at"] == "2028-01-01T00:00:00"


# ---------------------------------------------------------------------------
# Tests: renew_admin_client_secret
# ---------------------------------------------------------------------------

@pytest.mark.anyio("asyncio")
async def test_renew_admin_client_secret_success():
    """renew_admin_client_secret renews the secret and revokes the old one."""
    from app.security.encryption import encrypt_secret

    stored_creds: list[dict] = []
    revoked_key_ids: list[str] = []

    mock_creds = {
        "client_id": "admin-client-id",
        "client_secret": "admin-secret",
        "tenant_id": "partner-tenant",
        "app_object_id": "admin-app-obj-id",
        "client_secret_key_id": "old-key-id",
        "client_secret_expires_at": "2026-01-01T00:00:00",
    }

    async def mock_get_creds() -> dict:
        return mock_creds

    async def mock_update_creds(**kwargs) -> None:
        stored_creds.append(kwargs)

    async def mock_exchange_token(*, tenant_id, client_id, client_secret, refresh_token):
        return "new-access-token", None, None

    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        if "addPassword" in url:
            return {"secretText": "brand-new-secret", "keyId": "new-key-id"}
        if "removePassword" in url:
            revoked_key_ids.append(payload.get("keyId", ""))
            return {}
        return {}

    mock_settings = MagicMock()
    mock_settings.m365_client_secret_lifetime_days = 730

    with (
        patch.object(m365_service, "get_admin_m365_credentials", side_effect=mock_get_creds),
        patch.object(m365_service, "update_admin_m365_credentials", side_effect=mock_update_creds),
        patch.object(m365_service, "_exchange_token", side_effect=mock_exchange_token),
        patch.object(m365_service, "_graph_post", side_effect=mock_graph_post),
        patch("app.services.m365.get_settings", return_value=mock_settings),
    ):
        await m365_service.renew_admin_client_secret()

    # New credentials should have been stored
    assert len(stored_creds) == 1
    assert stored_creds[0]["client_secret"] == "brand-new-secret"
    assert stored_creds[0]["client_secret_key_id"] == "new-key-id"

    # Old key should have been revoked
    assert "old-key-id" in revoked_key_ids


@pytest.mark.anyio("asyncio")
async def test_renew_admin_client_secret_raises_without_creds():
    """renew_admin_client_secret raises M365Error when no credentials are stored."""
    with patch.object(m365_service, "get_admin_m365_credentials", AsyncMock(return_value=None)):
        with pytest.raises(m365_service.M365Error, match="No M365 admin credentials"):
            await m365_service.renew_admin_client_secret()


@pytest.mark.anyio("asyncio")
async def test_renew_admin_client_secret_raises_without_tenant_id():
    """renew_admin_client_secret raises M365Error when tenant_id is not stored."""
    mock_creds = {
        "client_id": "cid",
        "client_secret": "sec",
        "tenant_id": None,
        "app_object_id": "app-obj",
        "client_secret_key_id": "key",
    }
    with patch.object(m365_service, "get_admin_m365_credentials", AsyncMock(return_value=mock_creds)):
        with pytest.raises(m365_service.M365Error, match="tenant ID not stored"):
            await m365_service.renew_admin_client_secret()


@pytest.mark.anyio("asyncio")
async def test_renew_admin_client_secret_raises_without_app_object_id():
    """renew_admin_client_secret raises M365Error when app_object_id is not stored."""
    mock_creds = {
        "client_id": "cid",
        "client_secret": "sec",
        "tenant_id": "tenant-id",
        "app_object_id": None,
        "client_secret_key_id": "key",
    }
    with patch.object(m365_service, "get_admin_m365_credentials", AsyncMock(return_value=mock_creds)):
        with pytest.raises(m365_service.M365Error, match="App object ID not stored"):
            await m365_service.renew_admin_client_secret()


@pytest.mark.anyio("asyncio")
async def test_renew_admin_client_secret_revoke_failure_is_nonfatal():
    """renew_admin_client_secret continues when old key revocation fails."""
    stored: list[dict] = []
    mock_creds = {
        "client_id": "cid",
        "client_secret": "sec",
        "tenant_id": "tenant-id",
        "app_object_id": "app-obj",
        "client_secret_key_id": "old-key",
    }

    async def mock_update(**kwargs) -> None:
        stored.append(kwargs)

    async def mock_exchange_token(**kwargs):
        return "access-tok", None, None

    async def mock_graph_post(token, url, payload):
        if "addPassword" in url:
            return {"secretText": "new-secret", "keyId": "new-key"}
        if "removePassword" in url:
            raise m365_service.M365Error("Revoke failed")
        return {}

    mock_settings = MagicMock()
    mock_settings.m365_client_secret_lifetime_days = 730

    with (
        patch.object(m365_service, "get_admin_m365_credentials", AsyncMock(return_value=mock_creds)),
        patch.object(m365_service, "update_admin_m365_credentials", side_effect=mock_update),
        patch.object(m365_service, "_exchange_token", side_effect=mock_exchange_token),
        patch.object(m365_service, "_graph_post", side_effect=mock_graph_post),
        patch("app.services.m365.get_settings", return_value=mock_settings),
    ):
        # Should not raise even though revoke fails
        await m365_service.renew_admin_client_secret()

    assert len(stored) == 1  # New secret was still stored


# ---------------------------------------------------------------------------
# Tests: renew_expiring_client_secrets – admin credential extension
# ---------------------------------------------------------------------------

@pytest.mark.anyio("asyncio")
async def test_renew_expiring_also_renews_admin_credential():
    """renew_expiring_client_secrets renews the admin credential when expiring soon."""
    admin_renewed: list[bool] = []

    async def mock_get_admin_creds():
        return {
            "client_id": "admin-cid",
            "client_secret": "admin-sec",
            "tenant_id": "admin-tenant",
            "app_object_id": "admin-app-obj",
            "client_secret_key_id": "admin-key",
            "client_secret_expires_at": "2026-01-01T00:00:00",  # Already past
        }

    async def mock_renew_admin():
        admin_renewed.append(True)

    mock_settings = MagicMock()
    mock_settings.m365_client_secret_renewal_days = 14

    with (
        patch.object(m365_service.m365_repo, "list_credentials_expiring_before", AsyncMock(return_value=[])),
        patch.object(m365_service, "get_admin_m365_credentials", side_effect=mock_get_admin_creds),
        patch.object(m365_service, "renew_admin_client_secret", side_effect=mock_renew_admin),
        patch("app.services.m365.get_settings", return_value=mock_settings),
    ):
        result = await m365_service.renew_expiring_client_secrets()

    assert result["renewed"] == 1
    assert result["failed"] == 0
    assert admin_renewed == [True]


@pytest.mark.anyio("asyncio")
async def test_renew_expiring_skips_admin_without_app_object_id():
    """renew_expiring_client_secrets skips admin renewal when app_object_id is absent."""
    admin_renewed: list[bool] = []

    async def mock_get_admin_creds():
        return {
            "client_id": "admin-cid",
            "client_secret": "admin-sec",
            "tenant_id": "admin-tenant",
            "app_object_id": None,  # No app_object_id → skip
            "client_secret_key_id": None,
            "client_secret_expires_at": "2025-01-01T00:00:00",
        }

    async def mock_renew_admin():
        admin_renewed.append(True)

    mock_settings = MagicMock()
    mock_settings.m365_client_secret_renewal_days = 14

    with (
        patch.object(m365_service.m365_repo, "list_credentials_expiring_before", AsyncMock(return_value=[])),
        patch.object(m365_service, "get_admin_m365_credentials", side_effect=mock_get_admin_creds),
        patch.object(m365_service, "renew_admin_client_secret", side_effect=mock_renew_admin),
        patch("app.services.m365.get_settings", return_value=mock_settings),
    ):
        result = await m365_service.renew_expiring_client_secrets()

    # Admin credential without app_object_id is not renewed
    assert result["renewed"] == 0
    assert admin_renewed == []


@pytest.mark.anyio("asyncio")
async def test_renew_expiring_does_not_renew_admin_when_not_expiring():
    """renew_expiring_client_secrets does not renew admin credential that is not expiring."""
    admin_renewed: list[bool] = []

    async def mock_get_admin_creds():
        # Expiry is far in the future
        far_future = (datetime.utcnow() + timedelta(days=365)).isoformat()
        return {
            "client_id": "admin-cid",
            "client_secret": "admin-sec",
            "tenant_id": "admin-tenant",
            "app_object_id": "admin-app-obj",
            "client_secret_key_id": "admin-key",
            "client_secret_expires_at": far_future,
        }

    async def mock_renew_admin():
        admin_renewed.append(True)

    mock_settings = MagicMock()
    mock_settings.m365_client_secret_renewal_days = 14

    with (
        patch.object(m365_service.m365_repo, "list_credentials_expiring_before", AsyncMock(return_value=[])),
        patch.object(m365_service, "get_admin_m365_credentials", side_effect=mock_get_admin_creds),
        patch.object(m365_service, "renew_admin_client_secret", side_effect=mock_renew_admin),
        patch("app.services.m365.get_settings", return_value=mock_settings),
    ):
        result = await m365_service.renew_expiring_client_secrets()

    assert result["renewed"] == 0
    assert admin_renewed == []


@pytest.mark.anyio("asyncio")
async def test_renew_expiring_handles_admin_renewal_error():
    """renew_expiring_client_secrets counts admin renewal failures in 'failed'."""
    async def mock_get_admin_creds():
        return {
            "client_id": "admin-cid",
            "client_secret": "admin-sec",
            "tenant_id": "admin-tenant",
            "app_object_id": "admin-app-obj",
            "client_secret_key_id": "admin-key",
            "client_secret_expires_at": "2024-01-01T00:00:00",
        }

    async def mock_renew_admin():
        raise m365_service.M365Error("Graph API error")

    mock_settings = MagicMock()
    mock_settings.m365_client_secret_renewal_days = 14

    with (
        patch.object(m365_service.m365_repo, "list_credentials_expiring_before", AsyncMock(return_value=[])),
        patch.object(m365_service, "get_admin_m365_credentials", side_effect=mock_get_admin_creds),
        patch.object(m365_service, "renew_admin_client_secret", side_effect=mock_renew_admin),
        patch("app.services.m365.get_settings", return_value=mock_settings),
    ):
        result = await m365_service.renew_expiring_client_secrets()

    assert result["renewed"] == 0
    assert result["failed"] == 1


# ---------------------------------------------------------------------------
# Tests: scope and role constants
# ---------------------------------------------------------------------------

def test_csp_admin_app_roles_contains_directory_read_all():
    """_CSP_ADMIN_APP_ROLES includes Directory.Read.All application permission."""
    assert "7ab1d382-f21e-4acd-a863-ba3e13f7da61" in m365_service._CSP_ADMIN_APP_ROLES


def test_csp_admin_app_roles_contains_app_readwrite_ownedby():
    """_CSP_ADMIN_APP_ROLES includes Application.ReadWrite.OwnedBy for self-renewal."""
    assert "18a4783c-866b-4cc7-a460-3d5e5662c884" in m365_service._CSP_ADMIN_APP_ROLES


def test_directory_read_all_scope_id_is_correct():
    """_DIRECTORY_READ_ALL_SCOPE_ID matches the known delegated scope for Directory.Read.All."""
    assert m365_service._DIRECTORY_READ_ALL_SCOPE_ID == "06da0dbc-49e2-44d2-8312-53f166ab848a"
