"""Tests for Microsoft 365 client credential automatic renewal."""
from __future__ import annotations

import pytest
from datetime import date, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import m365 as m365_service
from tests.conftest import drain_provision_background_tasks

APP_OBJECT_ID = "11111111-1111-1111-1111-111111111111"
SERVICE_PRINCIPAL_ID = "22222222-2222-2222-2222-222222222222"
GRAPH_SP_ID = "33333333-3333-3333-3333-333333333333"
OLD_KEY_ID = "44444444-4444-4444-4444-444444444444"
NEW_KEY_ID = "55555555-5555-5555-5555-555555555555"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_provision_mocks(
    client_id: str = "prov-client-id",
    sp_id: str = SERVICE_PRINCIPAL_ID,
    app_obj_id: str = APP_OBJECT_ID,
    secret: str = "plain-secret",
    key_id: str = NEW_KEY_ID,
) -> tuple[Any, Any]:
    """Return (mock_graph_post, mock_graph_get) for a successful provision flow."""
    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        if "/applications" in url and "addPassword" not in url and "owners" not in url:
            return {"id": app_obj_id, "appId": client_id}
        if "/servicePrincipals" in url and "appRoleAssignments" not in url:
            return {"id": sp_id}
        if "appRoleAssignments" in url:
            return {"id": "assignment-id"}
        if "owners/$ref" in url:
            return {}  # 204 No Content → empty dict
        if "addPassword" in url:
            return {"secretText": secret, "keyId": key_id}
        return {}

    async def mock_graph_get(token: str, url: str) -> dict:
        if "servicePrincipals" in url:
            return {"value": [{"id": GRAPH_SP_ID}]}
        return {}

    return mock_graph_post, mock_graph_get


# ---------------------------------------------------------------------------
# Tests: provision_app_registration – new return shape & configurable lifetime
# ---------------------------------------------------------------------------

@pytest.mark.anyio("asyncio")
async def test_provision_returns_dict_with_required_keys():
    """provision_app_registration returns a dict with all required fields."""
    mock_post, mock_get = _make_provision_mocks()

    with (
        patch.object(m365_service, "_graph_post", side_effect=mock_post),
        patch.object(m365_service, "_graph_get", side_effect=mock_get),
    ):
        result = await m365_service.provision_app_registration(access_token="tok")

    assert isinstance(result, dict)
    assert result["client_id"] == "prov-client-id"
    assert result["client_secret"] == "plain-secret"
    assert result["app_object_id"] == APP_OBJECT_ID
    assert result["client_secret_key_id"] == NEW_KEY_ID
    assert isinstance(result["client_secret_expires_at"], datetime)


@pytest.mark.anyio("asyncio")
async def test_provision_uses_configurable_lifetime():
    """provision_app_registration uses M365_CLIENT_SECRET_LIFETIME_DAYS from config."""
    captured_payloads: list[dict] = []

    async def mock_post(token: str, url: str, payload: dict) -> dict:
        captured_payloads.append({"url": url, "payload": payload})
        if "/applications" in url and "addPassword" not in url and "owners" not in url:
            return {"id": APP_OBJECT_ID, "appId": "cid"}
        if "/servicePrincipals" in url and "appRoleAssignments" not in url:
            return {"id": SERVICE_PRINCIPAL_ID}
        if "appRoleAssignments" in url:
            return {"id": "x"}
        if "owners/$ref" in url:
            return {}
        if "addPassword" in url:
            return {"secretText": "s", "keyId": "k"}
        return {}

    async def mock_get(token: str, url: str) -> dict:
        return {"value": [{"id": GRAPH_SP_ID}]}

    mock_settings = MagicMock()
    mock_settings.m365_client_secret_lifetime_days = 365  # 1 year instead of default 2

    with (
        patch.object(m365_service, "_graph_post", side_effect=mock_post),
        patch.object(m365_service, "_graph_get", side_effect=mock_get),
        patch("app.services.m365.get_settings", return_value=mock_settings),
    ):
        result = await m365_service.provision_app_registration(access_token="tok")
        await drain_provision_background_tasks()

    # The secret expiry passed to addPassword should be ~365 days from today
    add_password_call = next(
        p for p in captured_payloads if "addPassword" in p["url"]
    )
    end_dt_str: str = add_password_call["payload"]["passwordCredential"]["endDateTime"]
    end_date = date.fromisoformat(end_dt_str.split("T")[0])
    expected_date = date.today() + timedelta(days=365)
    assert end_date == expected_date

    # client_secret_expires_at should also reflect the 365-day lifetime
    assert result["client_secret_expires_at"].year == expected_date.year
    assert result["client_secret_expires_at"].month == expected_date.month
    assert result["client_secret_expires_at"].day == expected_date.day


@pytest.mark.anyio("asyncio")
async def test_provision_adds_sp_as_owner():
    """provision_app_registration POSTs to owners/$ref to add SP as owner."""
    owner_calls: list[dict] = []

    async def mock_post(token: str, url: str, payload: dict) -> dict:
        if "owners/$ref" in url:
            owner_calls.append({"url": url, "payload": payload})
            return {}
        if "/applications" in url and "addPassword" not in url:
            return {"id": APP_OBJECT_ID, "appId": "cid"}
        if "/servicePrincipals" in url and "appRoleAssignments" not in url:
            return {"id": SERVICE_PRINCIPAL_ID}
        if "appRoleAssignments" in url:
            return {"id": "x"}
        if "addPassword" in url:
            return {"secretText": "s", "keyId": "k"}
        return {}

    async def mock_get(token: str, url: str) -> dict:
        return {"value": [{"id": GRAPH_SP_ID}]}

    with (
        patch.object(m365_service, "_graph_post", side_effect=mock_post),
        patch.object(m365_service, "_graph_get", side_effect=mock_get),
    ):
        await m365_service.provision_app_registration(access_token="tok")
        await drain_provision_background_tasks()

    assert len(owner_calls) == 1
    owner_payload = owner_calls[0]["payload"]
    assert "@odata.id" in owner_payload
    assert SERVICE_PRINCIPAL_ID in owner_payload["@odata.id"]


@pytest.mark.anyio("asyncio")
async def test_provision_app_roles_includes_self_renewal_permission():
    """_PROVISION_APP_ROLES includes Application.ReadWrite.OwnedBy for self-renewal."""
    # Application.ReadWrite.OwnedBy GUID
    assert "18a4783c-866b-4cc7-a460-3d5e5662c884" in m365_service._PROVISION_APP_ROLES


# ---------------------------------------------------------------------------
# Tests: _graph_post handles 204 No Content
# ---------------------------------------------------------------------------

@pytest.mark.anyio("asyncio")
async def test_graph_post_handles_204_no_content():
    """_graph_post returns empty dict for 204 No Content (e.g. owners/$ref)."""
    with patch("app.services.m365.httpx.AsyncClient") as mock_client_cls:
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_client_cls.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=mock_response
        )

        result = await m365_service._graph_post(
            "token",
            "https://graph.microsoft.com/v1.0/applications/x/owners/$ref",
            {"@odata.id": "https://graph.microsoft.com/v1.0/directoryObjects/y"},
        )

    assert result == {}


# ---------------------------------------------------------------------------
# Tests: renew_admin_client_secret
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_renew_admin_client_secret_backfills_missing_app_object_id():
    """Renewal recovers and persists a missing admin app object ID from Graph."""
    stored_creds = {
        "tenant_id": "tenant-1",
        "client_id": "admin-client-id",
        "client_secret": "old-secret",
        "app_object_id": None,
        "client_secret_key_id": OLD_KEY_ID,
        "client_secret_expires_at": datetime.utcnow() + timedelta(days=4),
        "pkce_client_id": "pkce-client-id",
    }
    persisted: list[dict[str, Any]] = []
    posted_calls: list[dict[str, Any]] = []

    async def mock_get(token: str, url: str) -> dict:
        if "applications?$filter=appId eq" in url:
            return {"value": [{"id": APP_OBJECT_ID}]}
        return {"value": []}

    async def mock_post(token: str, url: str, payload: dict) -> dict:
        posted_calls.append({"url": url, "payload": payload})
        if "addPassword" in url:
            return {"secretText": "new-secret", "keyId": NEW_KEY_ID}
        if "removePassword" in url:
            return {}
        return {}

    exchange_mock = AsyncMock(return_value=("old-token", None, None))
    mock_settings = MagicMock()
    mock_settings.m365_client_secret_lifetime_days = 730

    with (
        patch.object(
            m365_service, "get_admin_m365_credentials", AsyncMock(return_value=stored_creds)
        ),
        patch.object(m365_service, "_exchange_token", exchange_mock),
        patch.object(m365_service, "_graph_get", side_effect=mock_get),
        patch.object(m365_service, "_graph_post", side_effect=mock_post),
        patch.object(
            m365_service,
            "update_admin_m365_credentials",
            side_effect=lambda **kwargs: persisted.append(kwargs) or None,
        ),
        patch("app.services.m365.get_settings", return_value=mock_settings),
    ):
        result = await m365_service.renew_admin_client_secret()

    assert result["key_id"] == NEW_KEY_ID
    assert len(persisted) == 3
    assert persisted[0]["app_object_id"] == APP_OBJECT_ID
    assert persisted[0]["client_secret"] == "old-secret"
    assert persisted[1]["app_object_id"] == APP_OBJECT_ID
    assert persisted[1]["client_secret"] == "new-secret"
    assert persisted[2]["app_object_id"] == APP_OBJECT_ID
    assert persisted[2]["client_secret"] == "new-secret"
    add_call = next(c for c in posted_calls if "addPassword" in c["url"])
    assert APP_OBJECT_ID in add_call["url"]
    exchange_mock.assert_awaited_once()


@pytest.mark.anyio("asyncio")
async def test_renew_admin_client_secret_validation_failure_restores_previous_secret():
    """Validation failure restores the previous admin credential and removes the new secret."""
    stored_creds = {
        "tenant_id": "tenant-1",
        "client_id": "admin-client-id",
        "client_secret": "old-secret",
        "app_object_id": APP_OBJECT_ID,
        "client_secret_key_id": OLD_KEY_ID,
        "client_secret_expires_at": datetime.utcnow() + timedelta(days=4),
        "pkce_client_id": "pkce-client-id",
    }
    posted_calls: list[dict[str, Any]] = []
    persisted: list[dict[str, Any]] = []

    async def mock_post(token: str, url: str, payload: dict) -> dict:
        posted_calls.append({"url": url, "payload": payload})
        if "addPassword" in url:
            return {"secretText": "new-secret", "keyId": NEW_KEY_ID}
        if "removePassword" in url:
            return {}
        return {}

    exchange_mock = AsyncMock(
        side_effect=[
            ("old-token", None, None),
            m365_service.M365Error("new secret rejected"),
        ]
    )
    mock_settings = MagicMock()
    mock_settings.m365_client_secret_lifetime_days = 730

    with (
        patch.object(
            m365_service, "get_admin_m365_credentials", AsyncMock(return_value=stored_creds)
        ),
        patch.object(m365_service, "_exchange_token", exchange_mock),
        patch.object(m365_service, "_graph_post", side_effect=mock_post),
        patch.object(
            m365_service,
            "update_admin_m365_credentials",
            side_effect=lambda **kwargs: persisted.append(kwargs) or None,
        ),
        patch("app.services.m365.get_settings", return_value=mock_settings),
    ):
        with pytest.raises(
            m365_service.M365Error,
            match="previous credential restored",
        ):
            await m365_service.renew_admin_client_secret()

    assert len(persisted) == 2
    assert persisted[0]["client_secret"] == "new-secret"
    assert persisted[0]["client_secret_key_id"] == NEW_KEY_ID
    assert persisted[1]["client_secret"] == "old-secret"
    assert persisted[1]["client_secret_key_id"] == OLD_KEY_ID
    remove_call = next(c for c in posted_calls if "removePassword" in c["url"])
    assert remove_call["payload"]["keyId"] == NEW_KEY_ID
    assert exchange_mock.await_count == 2


# ---------------------------------------------------------------------------
# Tests: renew_client_secret
# ---------------------------------------------------------------------------

@pytest.mark.anyio("asyncio")
async def test_renew_client_secret_success():
    """renew_client_secret creates a new secret and revokes the old one."""
    company_id = 42
    stored_creds = {
        "company_id": company_id,
        "tenant_id": "tenant-1",
        "client_id": "app-client-id",
        "client_secret": "encrypted-old-secret",
        "app_object_id": APP_OBJECT_ID,
        "client_secret_key_id": OLD_KEY_ID,
        "client_secret_expires_at": datetime.utcnow() + timedelta(days=5),
    }

    posted_calls: list[dict] = []

    async def mock_post(token: str, url: str, payload: dict) -> dict:
        posted_calls.append({"url": url, "payload": payload})
        if "addPassword" in url:
            return {"secretText": "new-secret", "keyId": NEW_KEY_ID}
        if "removePassword" in url:
            return {}
        return {}

    mock_update = AsyncMock()
    mock_settings = MagicMock()
    mock_settings.m365_client_secret_lifetime_days = 730
    mock_settings.m365_client_secret_renewal_days = 14

    with (
        patch.object(m365_service, "get_credentials", AsyncMock(return_value=stored_creds)),
        patch.object(m365_service, "_exchange_token", AsyncMock(return_value=("fresh-token", None, None))),
        patch.object(m365_service, "_graph_post", side_effect=mock_post),
        patch.object(m365_service.m365_repo, "update_client_secret", mock_update),
        patch("app.services.m365.get_settings", return_value=mock_settings),
    ):
        await m365_service.renew_client_secret(company_id)

    # addPassword was called
    add_pw = next(c for c in posted_calls if "addPassword" in c["url"])
    assert APP_OBJECT_ID in add_pw["url"]

    # DB was updated with encrypted new secret
    mock_update.assert_awaited_once()
    call_kwargs = mock_update.call_args.kwargs
    assert call_kwargs["company_id"] == company_id
    assert call_kwargs["key_id"] == NEW_KEY_ID

    # Old key was revoked
    remove_pw = next(c for c in posted_calls if "removePassword" in c["url"])
    assert remove_pw["payload"]["keyId"] == OLD_KEY_ID


@pytest.mark.anyio("asyncio")
async def test_renew_client_secret_no_credentials_raises():
    """renew_client_secret raises M365Error when no credentials are found."""
    with patch.object(m365_service, "get_credentials", AsyncMock(return_value=None)):
        with pytest.raises(m365_service.M365Error, match="No M365 credentials"):
            await m365_service.renew_client_secret(99)


@pytest.mark.anyio("asyncio")
async def test_renew_client_secret_no_app_object_id_raises():
    """renew_client_secret raises M365Error when app_object_id is not stored."""
    creds = {
        "company_id": 1,
        "tenant_id": "t",
        "client_id": "c",
        "client_secret": "enc",
        "app_object_id": None,
        "client_secret_key_id": None,
        "client_secret_expires_at": None,
    }
    with patch.object(m365_service, "get_credentials", AsyncMock(return_value=creds)):
        with pytest.raises(m365_service.M365Error, match="re-provisioning"):
            await m365_service.renew_client_secret(1)


@pytest.mark.anyio("asyncio")
async def test_renew_client_secret_revoke_failure_is_nonfatal():
    """A failure to revoke the old key is logged but does not raise an exception."""
    company_id = 7
    stored_creds = {
        "company_id": company_id,
        "tenant_id": "tenant",
        "client_id": "cid",
        "client_secret": "enc",
        "app_object_id": APP_OBJECT_ID,
        "client_secret_key_id": "old-key",
        "client_secret_expires_at": datetime.utcnow() + timedelta(days=3),
    }
    posted_calls: list[str] = []

    async def mock_post(token: str, url: str, payload: dict) -> dict:
        posted_calls.append(url)
        if "addPassword" in url:
            return {"secretText": "new", "keyId": "new-key"}
        if "removePassword" in url:
            raise m365_service.M365Error("removePassword failed")
        return {}

    mock_settings = MagicMock()
    mock_settings.m365_client_secret_lifetime_days = 730

    with (
        patch.object(m365_service, "get_credentials", AsyncMock(return_value=stored_creds)),
        patch.object(m365_service, "_exchange_token", AsyncMock(return_value=("tok", None, None))),
        patch.object(m365_service, "_graph_post", side_effect=mock_post),
        patch.object(m365_service.m365_repo, "update_client_secret", AsyncMock()),
        patch("app.services.m365.get_settings", return_value=mock_settings),
    ):
        # Should NOT raise even though removePassword failed
        await m365_service.renew_client_secret(company_id)

    # Verify removePassword was attempted
    assert any("removePassword" in u for u in posted_calls)


@pytest.mark.anyio("asyncio")
async def test_renew_client_secret_no_old_key_id_skips_revoke():
    """When no old key_id is stored, revocation is skipped gracefully."""
    company_id = 5
    stored_creds = {
        "company_id": company_id,
        "tenant_id": "tenant",
        "client_id": "cid",
        "client_secret": "enc",
        "app_object_id": APP_OBJECT_ID,
        "client_secret_key_id": None,  # no old key_id
        "client_secret_expires_at": datetime.utcnow() + timedelta(days=2),
    }
    posted_calls: list[str] = []

    async def mock_post(token: str, url: str, payload: dict) -> dict:
        posted_calls.append(url)
        if "addPassword" in url:
            return {"secretText": "new", "keyId": "new-key"}
        return {}

    mock_settings = MagicMock()
    mock_settings.m365_client_secret_lifetime_days = 730

    with (
        patch.object(m365_service, "get_credentials", AsyncMock(return_value=stored_creds)),
        patch.object(m365_service, "_exchange_token", AsyncMock(return_value=("tok", None, None))),
        patch.object(m365_service, "_graph_post", side_effect=mock_post),
        patch.object(m365_service.m365_repo, "update_client_secret", AsyncMock()),
        patch("app.services.m365.get_settings", return_value=mock_settings),
    ):
        await m365_service.renew_client_secret(company_id)

    # removePassword should not have been called
    assert not any("removePassword" in u for u in posted_calls)


# ---------------------------------------------------------------------------
# Tests: renew_expiring_client_secrets
# ---------------------------------------------------------------------------

@pytest.mark.anyio("asyncio")
async def test_renew_expiring_skips_without_app_object_id():
    """renew_expiring_client_secrets skips entries without app_object_id."""
    expiring_creds = [
        {
            "company_id": 1,
            "app_object_id": None,  # no object ID - cannot auto-renew
            "client_secret_expires_at": datetime.utcnow() + timedelta(days=3),
        }
    ]
    mock_settings = MagicMock()
    mock_settings.m365_client_secret_renewal_days = 30

    with (
        patch.object(
            m365_service.m365_repo,
            "list_credentials_expiring_before",
            AsyncMock(return_value=expiring_creds),
        ),
        patch.object(
            m365_service, "get_admin_m365_credentials", AsyncMock(return_value=None)
        ),
        patch.object(
            m365_service.m365_repo,
            "list_provisioned_company_ids",
            AsyncMock(return_value=set()),
        ),
        patch("app.services.m365.get_settings", return_value=mock_settings),
    ):
        result = await m365_service.renew_expiring_client_secrets()

    assert result["skipped"] == 1
    assert result["renewed"] == 0
    assert result["failed"] == 0


@pytest.mark.anyio("asyncio")
async def test_renew_expiring_counts_renewed_and_failed():
    """renew_expiring_client_secrets counts renewed and failed correctly."""
    expiring_creds = [
        {"company_id": 10, "app_object_id": "obj-10"},
        {"company_id": 11, "app_object_id": "obj-11"},
    ]

    async def fake_renew(company_id: int) -> None:
        if company_id == 11:
            raise m365_service.M365Error("Graph API error")

    mock_settings = MagicMock()
    mock_settings.m365_client_secret_renewal_days = 30

    with (
        patch.object(
            m365_service.m365_repo,
            "list_credentials_expiring_before",
            AsyncMock(return_value=expiring_creds),
        ),
        patch.object(m365_service, "renew_client_secret", side_effect=fake_renew),
        patch.object(
            m365_service, "get_admin_m365_credentials", AsyncMock(return_value=None)
        ),
        patch.object(
            m365_service.m365_repo,
            "list_provisioned_company_ids",
            AsyncMock(return_value=set()),
        ),
        patch("app.services.m365.get_settings", return_value=mock_settings),
    ):
        result = await m365_service.renew_expiring_client_secrets()

    assert result["renewed"] == 1
    assert result["failed"] == 1
    assert result["skipped"] == 0


@pytest.mark.anyio("asyncio")
async def test_renew_expiring_uses_configurable_renewal_window():
    """renew_expiring_client_secrets uses M365_CLIENT_SECRET_RENEWAL_DAYS for cutoff."""
    captured_cutoffs: list[datetime] = []

    async def fake_list_expiring(cutoff: datetime) -> list:
        captured_cutoffs.append(cutoff)
        return []

    mock_settings = MagicMock()
    mock_settings.m365_client_secret_renewal_days = 30  # custom: 30 days

    with (
        patch.object(
            m365_service.m365_repo,
            "list_credentials_expiring_before",
            side_effect=fake_list_expiring,
        ),
        patch.object(
            m365_service, "get_admin_m365_credentials", AsyncMock(return_value=None)
        ),
        patch.object(
            m365_service.m365_repo,
            "list_provisioned_company_ids",
            AsyncMock(return_value=set()),
        ),
        patch("app.services.m365.get_settings", return_value=mock_settings),
    ):
        await m365_service.renew_expiring_client_secrets()

    assert len(captured_cutoffs) == 1
    # The cutoff should be approximately now + 30 days
    expected = datetime.utcnow() + timedelta(days=30)
    delta = abs((captured_cutoffs[0] - expected).total_seconds())
    assert delta < 5  # within 5 seconds of expected


@pytest.mark.anyio("asyncio")
async def test_renew_expiring_empty_list_returns_zeros():
    """renew_expiring_client_secrets returns zero counts when nothing is expiring."""
    mock_settings = MagicMock()
    mock_settings.m365_client_secret_renewal_days = 30

    with (
        patch.object(
            m365_service.m365_repo,
            "list_credentials_expiring_before",
            AsyncMock(return_value=[]),
        ),
        patch.object(
            m365_service, "get_admin_m365_credentials", AsyncMock(return_value=None)
        ),
        patch.object(
            m365_service.m365_repo,
            "list_provisioned_company_ids",
            AsyncMock(return_value=set()),
        ),
        patch("app.services.m365.get_settings", return_value=mock_settings),
    ):
        result = await m365_service.renew_expiring_client_secrets()

    assert result == {"renewed": 0, "skipped": 0, "failed": 0}


@pytest.mark.anyio("asyncio")
async def test_renew_expiring_renews_global_admin_credentials():
    """renew_expiring_client_secrets renews expiring global admin credentials."""
    mock_settings = MagicMock()
    mock_settings.m365_client_secret_renewal_days = 30
    global_admin_creds = {
        "client_secret_expires_at": datetime.utcnow() + timedelta(days=3),
        "app_object_id": "global-app-obj",
    }
    mock_renew_admin = AsyncMock()

    with (
        patch.object(
            m365_service.m365_repo,
            "list_credentials_expiring_before",
            AsyncMock(return_value=[]),
        ),
        patch.object(
            m365_service, "get_admin_m365_credentials", AsyncMock(return_value=global_admin_creds)
        ),
        patch.object(
            m365_service.m365_repo,
            "list_provisioned_company_ids",
            AsyncMock(return_value=set()),
        ),
        patch.object(m365_service, "renew_admin_client_secret", mock_renew_admin),
        patch("app.services.m365.get_settings", return_value=mock_settings),
    ):
        result = await m365_service.renew_expiring_client_secrets()

    mock_renew_admin.assert_awaited_once()
    assert result == {"renewed": 1, "skipped": 0, "failed": 0}


@pytest.mark.anyio("asyncio")
async def test_renew_expiring_renews_company_admin_credentials():
    """renew_expiring_client_secrets renews expiring per-company admin credentials."""
    mock_settings = MagicMock()
    mock_settings.m365_client_secret_renewal_days = 30
    mock_renew_admin = AsyncMock()

    async def fake_company_admin(company_id: int) -> dict[str, Any] | None:
        if company_id == 21:
            return {
                "client_secret_expires_at": datetime.utcnow() + timedelta(days=2),
                "app_object_id": "company-app-obj",
            }
        if company_id == 22:
            return {
                "client_secret_expires_at": datetime.utcnow() + timedelta(days=90),
                "app_object_id": "company-app-obj-later",
            }
        return None

    with (
        patch.object(
            m365_service.m365_repo,
            "list_credentials_expiring_before",
            AsyncMock(return_value=[]),
        ),
        patch.object(
            m365_service, "get_admin_m365_credentials", AsyncMock(return_value=None)
        ),
        patch.object(
            m365_service.m365_repo,
            "list_provisioned_company_ids",
            AsyncMock(return_value={21, 22}),
        ),
        patch.object(
            m365_service, "get_company_admin_credentials", side_effect=fake_company_admin
        ),
        patch.object(m365_service, "renew_admin_client_secret", mock_renew_admin),
        patch("app.services.m365.get_settings", return_value=mock_settings),
    ):
        result = await m365_service.renew_expiring_client_secrets()

    mock_renew_admin.assert_awaited_once_with(21)
    assert result == {"renewed": 1, "skipped": 0, "failed": 0}


@pytest.mark.anyio("asyncio")
async def test_renew_expiring_global_admin_reprovision_counts_skipped():
    """Global admin reprovision-required errors are counted as skipped."""
    mock_settings = MagicMock()
    mock_settings.m365_client_secret_renewal_days = 30

    with (
        patch.object(
            m365_service.m365_repo,
            "list_credentials_expiring_before",
            AsyncMock(return_value=[]),
        ),
        patch.object(
            m365_service,
            "get_admin_m365_credentials",
            AsyncMock(
                return_value={
                    "client_secret_expires_at": datetime.utcnow() + timedelta(days=1),
                }
            ),
        ),
        patch.object(
            m365_service.m365_repo,
            "list_provisioned_company_ids",
            AsyncMock(return_value=set()),
        ),
        patch.object(
            m365_service,
            "renew_admin_client_secret",
            AsyncMock(side_effect=m365_service.M365ReprovisionRequiredError("re-provisioning")),
        ),
        patch("app.services.m365.get_settings", return_value=mock_settings),
    ):
        result = await m365_service.renew_expiring_client_secrets()

    assert result == {"renewed": 0, "skipped": 1, "failed": 0}


@pytest.mark.anyio("asyncio")
async def test_renew_expiring_company_admin_error_counts_failed():
    """Per-company admin renewal errors are counted as failed."""
    mock_settings = MagicMock()
    mock_settings.m365_client_secret_renewal_days = 30

    with (
        patch.object(
            m365_service.m365_repo,
            "list_credentials_expiring_before",
            AsyncMock(return_value=[]),
        ),
        patch.object(
            m365_service, "get_admin_m365_credentials", AsyncMock(return_value=None)
        ),
        patch.object(
            m365_service.m365_repo,
            "list_provisioned_company_ids",
            AsyncMock(return_value={33}),
        ),
        patch.object(
            m365_service,
            "get_company_admin_credentials",
            AsyncMock(
                return_value={
                    "client_secret_expires_at": datetime.utcnow() + timedelta(days=1),
                    "app_object_id": "company-app-obj",
                }
            ),
        ),
        patch.object(
            m365_service,
            "renew_admin_client_secret",
            AsyncMock(side_effect=m365_service.M365Error("Graph failure")),
        ),
        patch("app.services.m365.get_settings", return_value=mock_settings),
    ):
        result = await m365_service.renew_expiring_client_secrets()

    assert result == {"renewed": 0, "skipped": 0, "failed": 1}


@pytest.mark.anyio("asyncio")
async def test_test_connectivity_returns_org_details():
    """test_connectivity returns organization details when Graph call succeeds."""
    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="token")),
        patch.object(
            m365_service,
            "_graph_get",
            AsyncMock(return_value={"value": [{"id": "org-1", "displayName": "Contoso"}]}),
        ),
    ):
        result = await m365_service.test_connectivity(3)

    assert result["graph_access"] is True
    assert result["organization_id"] == "org-1"
    assert result["organization_name"] == "Contoso"
