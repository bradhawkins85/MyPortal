"""Tests for the Microsoft 365 enterprise app provisioning service."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch
from typing import Any

from app.services import m365 as m365_service
from tests.conftest import drain_provision_background_tasks


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
    return {
        "value": [
            {
                "id": graph_sp_id,
                "appRoles": [{"id": role_id} for role_id in m365_service._PROVISION_APP_ROLES],
            }
        ]
    }


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


@pytest.mark.anyio("asyncio")
async def test_graph_post_preserves_sanitized_detail_codes():
    """Graph detail codes remain available without retaining detail messages."""
    from unittest.mock import MagicMock

    with patch("app.services.m365.httpx.AsyncClient") as mock_client_cls:
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "redacted response"
        mock_response.json.return_value = {
            "error": {
                "code": "Request_BadRequest",
                "message": "redacted message",
                "details": [
                    {"code": "InvalidUpdate", "message": "tenant-sensitive detail"}
                ],
            }
        }
        mock_client_cls.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=mock_response
        )

        with pytest.raises(m365_service.M365Error) as raised:
            await m365_service._graph_post(
                "token", "https://graph.microsoft.com/v1.0/applications", {}
            )

    assert raised.value.graph_error_code == "Request_BadRequest"
    assert raised.value.graph_error_detail_codes == ("InvalidUpdate",)
    assert "tenant-sensitive detail" not in str(raised.value)


@pytest.mark.anyio("asyncio")
async def test_graph_post_treats_existing_app_role_assignment_as_success():
    """Graph's 400 duplicate-assignment variant is an idempotent success."""
    from unittest.mock import MagicMock

    with patch("app.services.m365.httpx.AsyncClient") as mock_client_cls:
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "sanitized duplicate response"
        mock_response.json.return_value = {
            "error": {
                "code": "Request_BadRequest",
                "message": "Permission being assigned already exists on the object",
                "details": [{"code": "InvalidUpdate"}],
            }
        }
        mock_client_cls.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=mock_response
        )

        result = await m365_service._graph_post(
            "token",
            "https://graph.microsoft.com/v1.0/servicePrincipals/resource-id/appRoleAssignedTo",
            {"principalId": "sp-id", "resourceId": "resource-id", "appRoleId": "role-id"},
        )

    assert result == {}


@pytest.mark.anyio("asyncio")
async def test_graph_post_does_not_hide_duplicate_message_on_unrelated_endpoint():
    """The duplicate special case must remain scoped to role assignments."""
    from unittest.mock import MagicMock

    with patch("app.services.m365.httpx.AsyncClient") as mock_client_cls:
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "sanitized duplicate response"
        mock_response.json.return_value = {
            "error": {
                "code": "Request_BadRequest",
                "message": "Permission being assigned already exists on the object",
                "details": [{"code": "InvalidUpdate"}],
            }
        }
        mock_client_cls.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=mock_response
        )

        with pytest.raises(m365_service.M365Error):
            await m365_service._graph_post(
                "token", "https://graph.microsoft.com/v1.0/applications", {}
            )


@pytest.mark.anyio("asyncio")
async def test_graph_patch_does_not_log_expected_error_status():
    """An anticipated PATCH 404 is left to the recovery flow to log safely."""
    from unittest.mock import MagicMock

    with (
        patch("app.services.m365.httpx.AsyncClient") as mock_client_cls,
        patch.object(m365_service, "log_error") as error_log,
    ):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "tenant-specific Graph response"
        mock_response.json.return_value = {
            "error": {"code": "Request_ResourceNotFound", "message": "not found"}
        }
        mock_client_cls.return_value.__aenter__.return_value.patch = AsyncMock(
            return_value=mock_response
        )

        with pytest.raises(m365_service.M365Error) as raised:
            await m365_service._graph_patch(
                "token",
                "https://graph.microsoft.com/v1.0/applications/missing-id",
                {},
                expected_error_statuses=frozenset({404}),
            )

    assert raised.value.http_status == 404
    assert raised.value.graph_error_code == "Request_ResourceNotFound"
    error_log.assert_not_called()


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
        if ("appRoleAssignments" in url or "appRoleAssignedTo" in url):
            return _make_role_assignment()
        if "owners/$ref" in url:
            return {}  # 204 No Content → empty dict
        if "addPassword" in url:
            return _make_secret_data("provisioned-secret")
        return {}

    async def mock_graph_get(token: str, url: str) -> dict:
        if "servicePrincipals" in url and m365_service._TEAMS_APP_ID in url:
            return {"value": [{"id": "graph-sp-id", "appRoles": [{"id": m365_service._TEAMS_MANAGE_AS_APP_ROLE}]}]}
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
        # Drain the background role-grant task while mocks are still active
        await drain_provision_background_tasks()

    assert result["client_id"] == "provisioned-client-id"
    assert result["client_secret"] == "provisioned-secret"
    assert result["app_object_id"] == "app-object-id"
    # Verify all required Graph calls were made
    assert any("/applications" in u and "addPassword" not in u for u in call_order), \
        "Should POST to /applications to create app registration"
    assert any("/servicePrincipals" in u and "appRoleAssignments" not in u for u in call_order), \
        "Should POST to /servicePrincipals to create service principal"
    assert sum(1 for u in call_order if ("appRoleAssignments" in u or "appRoleAssignedTo" in u)) == len(
        m365_service._PROVISION_APP_ROLES
    ) + 1, "Should grant one role assignment per required role plus Exchange.ManageAsApp"
    assert any(
        u.endswith("/servicePrincipals/graph-sp-id/appRoleAssignedTo")
        for u in call_order
    ), "Graph roles should be assigned through the stable resource service principal"
    assert not any(
        u.endswith("/servicePrincipals/provisioned-sp-id/appRoleAssignments")
        for u in call_order
    ), "Provisioning should not route role creation through the newly-created principal"
    assert any("addPassword" in u for u in call_order), \
        "Should POST to addPassword to create client secret"


@pytest.mark.anyio("asyncio")
async def test_grant_provisioned_roles_skips_existing_assignments_and_owner():
    """Reconfirming an existing tenant must not submit duplicate Graph writes."""
    graph_role = m365_service._PROVISION_APP_ROLES[0]
    graph_post = AsyncMock()

    async def mock_graph_get(token: str, url: str, **kwargs: Any) -> dict:
        if url.endswith("/appRoleAssignments?$select=resourceId,appRoleId"):
            return {
                "value": [
                    {"resourceId": "graph-sp-id", "appRoleId": graph_role},
                    {
                        "resourceId": "exchange-sp-id",
                        "appRoleId": m365_service._EXO_MANAGE_AS_APP_ROLE,
                    },
                ]
            }
        if "/owners?$select=id" in url:
            return {"value": [{"id": "sp-object-id"}]}
        if m365_service._EXO_APP_ID in url:
            return {"value": [{"id": "exchange-sp-id"}]}
        return {"value": []}

    with (
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
        patch.object(m365_service, "_graph_post", graph_post),
        patch.object(m365_service, "_ensure_directory_role_by_name", AsyncMock()),
        patch.object(m365_service, "_ensure_exchange_admin_role", AsyncMock()),
        patch.object(m365_service, "_ensure_teams_service_admin_role", AsyncMock()),
    ):
        await m365_service._grant_provisioned_roles(
            "token",
            "sp-object-id",
            "graph-sp-id",
            "app-object-id",
            valid_graph_roles=[graph_role],
        )

    graph_post.assert_not_awaited()


@pytest.mark.anyio("asyncio")
async def test_grant_provisioned_roles_assigns_owner_when_owner_preflight_fails():
    """A transient owner-list failure must not block first-time provisioning."""
    graph_post = AsyncMock(return_value={})

    async def mock_graph_get(token: str, url: str, **kwargs: Any) -> dict:
        if "/owners?$select=id" in url:
            raise m365_service.M365Error("owner lookup unavailable", http_status=503)
        if m365_service._EXO_APP_ID in url:
            return {"value": []}
        return {"value": []}

    with (
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
        patch.object(m365_service, "_graph_post", graph_post),
        patch.object(m365_service, "_ensure_directory_role_by_name", AsyncMock()),
        patch.object(m365_service, "_ensure_exchange_admin_role", AsyncMock()),
        patch.object(m365_service, "_ensure_teams_service_admin_role", AsyncMock()),
    ):
        await m365_service._grant_provisioned_roles(
            "token",
            "sp-object-id",
            "graph-sp-id",
            "app-object-id",
            valid_graph_roles=[],
        )

    graph_post.assert_awaited_once()
    assert graph_post.await_args.args[1].endswith("/applications/app-object-id/owners/$ref")


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
        if ("appRoleAssignments" in url or "appRoleAssignedTo" in url):
            return _make_role_assignment()
        if "owners/$ref" in url:
            return {}
        if "addPassword" in url:
            return _make_secret_data()
        return {}

    async def mock_graph_get(token: str, url: str, **kwargs: Any) -> dict:
        if "filter=displayName" in url:
            return {"value": []}
        return _make_graph_sp_response()

    with (
        patch.object(m365_service, "_graph_post", side_effect=mock_graph_post),
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
    ):
        await m365_service.provision_app_registration(access_token="token")
        # Drain background role-grant task while mocks are still active
        await drain_provision_background_tasks()

    app_create = next(
        p for p in captured_payloads
        if "/applications" in p["url"] and "addPassword" not in p["url"]
    )
    assert app_create["payload"]["displayName"] == "MyPortal Integration"


@pytest.mark.anyio("asyncio")
async def test_provision_app_registration_keeps_sharepoint_permission_when_lookup_omits_it():
    """Provisioning must request SharePointTenantSettings.Read.All even if Graph appRoles omits it."""
    captured_payloads: list[dict] = []
    sharepoint_role = m365_service._SHAREPOINT_TENANT_SETTINGS_ROLE
    graph_roles_without_sharepoint = [
        {"id": role_id}
        for role_id in m365_service._PROVISION_APP_ROLES
        if role_id != sharepoint_role
    ]

    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        captured_payloads.append({"url": url, "payload": payload})
        if "/applications" in url and "addPassword" not in url and "owners" not in url:
            return _make_app_data()
        if "/servicePrincipals" in url and "appRoleAssignments" not in url:
            return _make_sp_data()
        if ("appRoleAssignments" in url or "appRoleAssignedTo" in url):
            return _make_role_assignment()
        if "owners/$ref" in url:
            return {}
        if "addPassword" in url:
            return _make_secret_data()
        return {}

    async def mock_graph_get(token: str, url: str, **kwargs: Any) -> dict:
        if "filter=displayName" in url:
            return {"value": []}
        if m365_service._TEAMS_APP_ID in url:
            return {
                "value": [
                    {
                        "id": "teams-sp-id",
                        "appRoles": [{"id": m365_service._TEAMS_MANAGE_AS_APP_ROLE}],
                    }
                ]
            }
        return {"value": [{"id": "graph-sp-id", "appRoles": graph_roles_without_sharepoint}]}

    with (
        patch.object(m365_service, "_graph_post", side_effect=mock_graph_post),
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
    ):
        await m365_service.provision_app_registration(access_token="token")
        await drain_provision_background_tasks()

    app_create = next(
        p for p in captured_payloads
        if "/applications" in p["url"] and "addPassword" not in p["url"]
    )
    graph_access = next(
        access
        for access in app_create["payload"]["requiredResourceAccess"]
        if access["resourceAppId"] == m365_service._GRAPH_APP_ID
    )
    requested_graph_roles = {role["id"] for role in graph_access["resourceAccess"]}
    granted_graph_roles = {
        p["payload"]["appRoleId"]
        for p in captured_payloads
        if ("appRoleAssignments" in p["url"] or "appRoleAssignedTo" in p["url"])
        and p["payload"].get("resourceId") == "graph-sp-id"
    }

    assert sharepoint_role in requested_graph_roles
    assert sharepoint_role in granted_graph_roles


@pytest.mark.anyio("asyncio")
async def test_provision_app_registration_registers_redirect_uri():
    """provision_app_registration includes web.redirectUris when redirect_uri is provided."""
    captured_payloads: list[dict] = []

    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        captured_payloads.append({"url": url, "payload": payload})
        if "/applications" in url and "addPassword" not in url and "owners" not in url:
            return _make_app_data()
        if "/servicePrincipals" in url and "appRoleAssignments" not in url:
            return _make_sp_data()
        if ("appRoleAssignments" in url or "appRoleAssignedTo" in url):
            return _make_role_assignment()
        if "owners/$ref" in url:
            return {}
        if "addPassword" in url:
            return _make_secret_data()
        return {}

    async def mock_graph_get(token: str, url: str, **kwargs: Any) -> dict:
        if "filter=displayName" in url:
            return {"value": []}
        return _make_graph_sp_response()

    redirect_uri = "https://myportal.example.com/m365/callback"

    with (
        patch.object(m365_service, "_graph_post", side_effect=mock_graph_post),
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
    ):
        await m365_service.provision_app_registration(
            access_token="token",
            redirect_uri=redirect_uri,
        )
        # Drain background role-grant task while mocks are still active
        await drain_provision_background_tasks()

    app_create = next(
        p for p in captured_payloads
        if "/applications" in p["url"]
        and "addPassword" not in p["url"]
        and "owners" not in p["url"]
    )
    assert "web" in app_create["payload"], "App payload should include web section with redirectUris"
    assert app_create["payload"]["web"]["redirectUris"] == [redirect_uri]


@pytest.mark.anyio("asyncio")
async def test_provision_app_registration_no_redirect_uri_when_not_provided():
    """provision_app_registration omits web.redirectUris when redirect_uri is not provided."""
    captured_payloads: list[dict] = []

    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        captured_payloads.append({"url": url, "payload": payload})
        if "/applications" in url and "addPassword" not in url and "owners" not in url:
            return _make_app_data()
        if "/servicePrincipals" in url and "appRoleAssignments" not in url:
            return _make_sp_data()
        if ("appRoleAssignments" in url or "appRoleAssignedTo" in url):
            return _make_role_assignment()
        if "owners/$ref" in url:
            return {}
        if "addPassword" in url:
            return _make_secret_data()
        return {}

    async def mock_graph_get(token: str, url: str, **kwargs: Any) -> dict:
        if "filter=displayName" in url:
            return {"value": []}
        return _make_graph_sp_response()

    with (
        patch.object(m365_service, "_graph_post", side_effect=mock_graph_post),
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
    ):
        await m365_service.provision_app_registration(access_token="token")
        # Drain background role-grant task while mocks are still active
        await drain_provision_background_tasks()

    app_create = next(
        p for p in captured_payloads
        if "/applications" in p["url"]
        and "addPassword" not in p["url"]
        and "owners" not in p["url"]
    )
    assert "web" not in app_create["payload"], "App payload should not include web section when no redirect_uri"


@pytest.mark.anyio("asyncio")
async def test_provision_app_registration_replaces_deleted_pending_application():
    """A stale pending application identity must not make confirmation permanently fail."""
    posted_urls: list[str] = []

    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        posted_urls.append(url)
        if url.endswith("/applications"):
            return _make_app_data("replacement-client-id")
        if url.endswith("/servicePrincipals"):
            return _make_sp_data("replacement-sp-id")
        if "addPassword" in url:
            return _make_secret_data("replacement-secret")
        return {}

    async def mock_graph_get(token: str, url: str, **kwargs: Any) -> dict:
        if "/applications?" in url:
            return {"value": []}
        if m365_service._TEAMS_APP_ID in url:
            return {"value": []}
        return _make_graph_sp_response()

    with (
        patch.object(
            m365_service,
            "_get_sp_app_role_ids",
            AsyncMock(return_value=("graph-sp-id", set(m365_service._PROVISION_APP_ROLES))),
        ),
        patch.object(
            m365_service,
            "_build_required_resource_access",
            AsyncMock(return_value=([], [], [])),
        ),
        patch.object(
            m365_service,
            "_graph_patch",
            AsyncMock(
                side_effect=m365_service.M365Error(
                    "Microsoft Graph PATCH failed (404): Resource does not exist",
                    http_status=404,
                )
            ),
        ),
        patch.object(m365_service, "_graph_post", side_effect=mock_graph_post),
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
    ):
        result = await m365_service.provision_app_registration(
            access_token="token",
            app_object_id="93bcf873-a8bd-4061-b4d7-160b98ed8712",
            client_id="deleted-client-id",
            service_principal_object_id="deleted-sp-object-id",
        )
        await drain_provision_background_tasks()

    assert result["app_object_id"] == "app-object-id"
    assert result["client_id"] == "replacement-client-id"
    assert result["service_principal_object_id"] == "replacement-sp-id"
    assert any(url.endswith("/applications") for url in posted_urls)
    assert any(url.endswith("/servicePrincipals") for url in posted_urls)


@pytest.mark.anyio("asyncio")
async def test_provision_app_registration_recovers_stale_object_id_by_client_id():
    """A stale object ID must not duplicate an application that still exists."""
    recovered_id = "93bcf873-a8bd-4061-b4d7-160b98ed8713"
    graph_patch = AsyncMock(
        side_effect=[
            m365_service.M365Error(
                "Microsoft Graph PATCH failed (404): Resource does not exist",
                http_status=404,
            ),
            {},
        ]
    )

    async def mock_graph_get(token: str, url: str, **kwargs: Any) -> dict:
        if "/applications?" in url:
            return {"value": [{"id": recovered_id}]}
        return _make_graph_sp_response()

    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        if "addPassword" in url:
            return _make_secret_data("recovered-secret")
        raise AssertionError(f"Unexpected POST while recovering application: {url}")

    with (
        patch.object(
            m365_service,
            "_get_sp_app_role_ids",
            AsyncMock(return_value=("graph-sp-id", set(m365_service._PROVISION_APP_ROLES))),
        ),
        patch.object(m365_service, "_graph_patch", graph_patch),
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
        patch.object(m365_service, "_graph_post", side_effect=mock_graph_post),
        patch.object(m365_service.asyncio, "create_task", lambda coro, **kwargs: coro.close()),
    ):
        result = await m365_service.provision_app_registration(
            access_token="token",
            app_object_id="93bcf873-a8bd-4061-b4d7-160b98ed8712",
            client_id="existing-client-id",
            service_principal_object_id="existing-sp-id",
        )

    assert result["app_object_id"] == recovered_id
    assert result["client_id"] == "existing-client-id"
    assert result["service_principal_object_id"] == "existing-sp-id"
    assert graph_patch.await_count == 2
    assert graph_patch.await_args_list[1].args[1].endswith(recovered_id)


@pytest.mark.anyio("asyncio")
async def test_provision_app_registration_does_not_replace_on_non_404_patch_error():
    """Only a missing stored registration is safe to replace automatically."""
    with (
        patch.object(
            m365_service,
            "_get_sp_app_role_ids",
            AsyncMock(return_value=("graph-sp-id", set(m365_service._PROVISION_APP_ROLES))),
        ),
        patch.object(
            m365_service,
            "_build_required_resource_access",
            AsyncMock(return_value=([], [], [])),
        ),
        patch.object(
            m365_service,
            "_graph_patch",
            AsyncMock(
                side_effect=m365_service.M365Error(
                    "Microsoft Graph PATCH failed (403)", http_status=403
                )
            ),
        ),
        patch.object(m365_service, "_graph_post", AsyncMock()) as graph_post,
    ):
        with pytest.raises(m365_service.M365Error, match="403"):
            await m365_service.provision_app_registration(
                access_token="token",
                app_object_id="93bcf873-a8bd-4061-b4d7-160b98ed8712",
                client_id="existing-client-id",
            )

    graph_post.assert_not_awaited()


@pytest.mark.anyio("asyncio")
async def test_provision_scope_constant():
    """PROVISION_SCOPE contains the required Graph-qualified delegated permissions."""
    assert "https://graph.microsoft.com/Application.ReadWrite.All" in m365_service.PROVISION_SCOPE
    assert "https://graph.microsoft.com/AppRoleAssignment.ReadWrite.All" in m365_service.PROVISION_SCOPE
    assert "offline_access" in m365_service.PROVISION_SCOPE


# ---------------------------------------------------------------------------
# Tests for transient appRoleAssignment propagation retry
# ---------------------------------------------------------------------------

@pytest.mark.anyio("asyncio")
async def test_post_app_role_assignment_retries_on_transient_propagation_error(monkeypatch):
    """`_post_app_role_assignment_with_retry` retries on the well-known
    Microsoft Graph propagation error and succeeds on a later attempt."""
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(m365_service.asyncio, "sleep", fake_sleep)

    attempts = {"n": 0}

    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise m365_service.M365Error(
                "Microsoft Graph POST failed (400): Permission being assigned was not found on application",
                http_status=400,
                graph_error_code="Request_BadRequest",
            )
        return {"id": "assignment-id"}

    with patch.object(m365_service, "_graph_post", side_effect=mock_graph_post):
        result = await m365_service._post_app_role_assignment_with_retry(
            "token",
            "https://graph.microsoft.com/v1.0/servicePrincipals/sp-id/appRoleAssignments",
            {"principalId": "sp-id", "resourceId": "graph-sp-id", "appRoleId": "role-id"},
            initial_delay_seconds=0.0,
        )

    assert result == {"id": "assignment-id"}
    assert attempts["n"] == 3
    assert len(sleeps) == 2  # slept between attempts 1→2 and 2→3


@pytest.mark.anyio("asyncio")
async def test_post_app_role_assignment_retries_invalid_update_detail(monkeypatch):
    """Graph's detail-only InvalidUpdate propagation response is transient."""
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(m365_service.asyncio, "sleep", fake_sleep)
    graph_post = AsyncMock(
        side_effect=[
            m365_service.M365Error(
                "Microsoft Graph POST failed (400)",
                http_status=400,
                graph_error_code="Request_BadRequest",
                graph_error_detail_codes=("InvalidUpdate",),
            ),
            {"id": "assignment-id"},
        ]
    )

    with patch.object(m365_service, "_graph_post", graph_post):
        result = await m365_service._post_app_role_assignment_with_retry(
            "token",
            "https://graph.microsoft.com/v1.0/servicePrincipals/resource-id/appRoleAssignedTo",
            {"principalId": "new-sp-id", "resourceId": "resource-id", "appRoleId": "role-id"},
            initial_delay_seconds=0.0,
        )

    assert result == {"id": "assignment-id"}
    assert graph_post.await_count == 2
    assert sleeps == [0.0]


@pytest.mark.anyio("asyncio")
async def test_post_app_role_assignment_accepts_existing_assignment_without_retry(monkeypatch):
    """The wrapped 400 duplicate variant must not enter propagation retries."""
    async def fake_sleep(seconds: float) -> None:  # pragma: no cover
        raise AssertionError("should not sleep for an existing assignment")

    monkeypatch.setattr(m365_service.asyncio, "sleep", fake_sleep)
    graph_post = AsyncMock(
        side_effect=m365_service.M365Error(
            "Microsoft Graph POST failed (400): Permission being assigned already exists on the object",
            http_status=400,
            graph_error_code="Request_BadRequest",
            graph_error_detail_codes=("InvalidUpdate",),
        )
    )

    with patch.object(m365_service, "_graph_post", graph_post):
        result = await m365_service._post_app_role_assignment_with_retry(
            "token",
            "https://graph.microsoft.com/v1.0/servicePrincipals/resource-id/appRoleAssignedTo",
            {"principalId": "sp-id", "resourceId": "resource-id", "appRoleId": "role-id"},
        )

    assert result == {}
    graph_post.assert_awaited_once()


@pytest.mark.anyio("asyncio")
async def test_post_app_role_assignment_does_not_retry_on_409(monkeypatch):
    """Conflicts (already-assigned roles) must propagate immediately so the
    caller's existing 409 short-circuit can run."""
    async def fake_sleep(seconds: float) -> None:  # pragma: no cover
        raise AssertionError("should not sleep on 409")

    monkeypatch.setattr(m365_service.asyncio, "sleep", fake_sleep)

    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        raise m365_service.M365Error(
            "Microsoft Graph POST failed (409)", http_status=409
        )

    with patch.object(m365_service, "_graph_post", side_effect=mock_graph_post):
        with pytest.raises(m365_service.M365Error):
            await m365_service._post_app_role_assignment_with_retry(
                "token",
                "https://graph.microsoft.com/v1.0/servicePrincipals/sp-id/appRoleAssignments",
                {},
                initial_delay_seconds=0.0,
            )


@pytest.mark.anyio("asyncio")
async def test_post_app_role_assignment_default_budget_recovers_after_seven_failures(monkeypatch):
    """Default retry budget tolerates slow Graph SP propagation (>6 attempts).

    Regression test for the case where a tenant takes longer than the previous
    6-attempt / ~46 second budget to replicate a newly-created service
    principal, surfacing as repeated ``Permission being assigned was not found
    on application`` errors before the assignment finally succeeds.
    """
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(m365_service.asyncio, "sleep", fake_sleep)

    attempts = {"n": 0}

    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        attempts["n"] += 1
        if attempts["n"] < 8:
            raise m365_service.M365Error(
                "Microsoft Graph POST failed (400): Permission being assigned was not found on application",
                http_status=400,
                graph_error_code="Request_BadRequest",
            )
        return {"id": "assignment-id"}

    with patch.object(m365_service, "_graph_post", side_effect=mock_graph_post):
        result = await m365_service._post_app_role_assignment_with_retry(
            "token",
            "https://graph.microsoft.com/v1.0/servicePrincipals/sp-id/appRoleAssignments",
            {"principalId": "sp-id", "resourceId": "graph-sp-id", "appRoleId": "role-id"},
            initial_delay_seconds=0.0,
        )

    assert result == {"id": "assignment-id"}
    assert attempts["n"] == 8
    assert len(sleeps) == 7


@pytest.mark.anyio("asyncio")
async def test_post_app_role_assignment_gives_up_after_max_attempts(monkeypatch):
    async def fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(m365_service.asyncio, "sleep", fake_sleep)

    attempts = {"n": 0}

    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        attempts["n"] += 1
        raise m365_service.M365Error(
            "Microsoft Graph POST failed (400): Permission being assigned was not found on application",
            http_status=400,
            graph_error_code="Request_BadRequest",
        )

    with patch.object(m365_service, "_graph_post", side_effect=mock_graph_post):
        with pytest.raises(m365_service.M365Error, match="Permission being assigned"):
            await m365_service._post_app_role_assignment_with_retry(
                "token",
                "https://graph.microsoft.com/v1.0/servicePrincipals/sp-id/appRoleAssignments",
                {},
                max_attempts=3,
                initial_delay_seconds=0.0,
            )

    assert attempts["n"] == 3


@pytest.mark.anyio("asyncio")
async def test_provision_app_roles_constant():
    """_PROVISION_APP_ROLES contains known Microsoft Graph permission GUIDs."""
    # User.Read.All
    assert "df021288-bdef-4463-88db-98f22de89214" in m365_service._PROVISION_APP_ROLES
    # Directory.Read.All
    assert "7ab1d382-f21e-4acd-a863-ba3e13f7da61" in m365_service._PROVISION_APP_ROLES
    # User-PasswordProfile.ReadWrite.All
    assert "cc117bb9-00cf-4eb8-b580-ea2a878fe8f7" in m365_service._PROVISION_APP_ROLES
    # EduAssignments.Read.All must not be substituted for the password role.
    assert "4c37e1b6-35a1-43bf-926a-6f30f2cdf585" not in m365_service._PROVISION_APP_ROLES


def test_permission_contract_omits_unsupported_permissions():
    """Provisioning must not request permissions that Microsoft APIs do not expose."""
    permission_names = {
        permission.name for permission in m365_service.PERMISSION_CONTRACT
    }

    assert "SecuritySecureScore.Read.All" not in permission_names
    assert "LicenseManager.AccessAsUser" not in permission_names
    assert not any(
        permission.resource == "Microsoft Exchange Online Protection"
        and permission.name == "Exchange.ManageAsApp"
        for permission in m365_service.PERMISSION_CONTRACT
    )
    assert all(
        app["app_id"] != m365_service._SCC_APP_ID
        for app in m365_service.ENTERPRISE_APP_CATALOG
    )


# ---------------------------------------------------------------------------
# Tests for extract_tenant_id_from_token
# ---------------------------------------------------------------------------

def _make_jwt(payload: dict) -> str:
    """Build a minimal JWT with the given payload (no real signature)."""
    import base64
    import json
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
