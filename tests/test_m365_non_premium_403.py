"""Tests for Microsoft Graph non-premium tenant 403 handling.

Covers:
- _graph_get populates graph_error_code from the response body on error
- get_all_users retries without signInActivity on Authentication_RequestFromNonPremiumTenantOrB2CTenant 403
- get_all_users re-raises non-premium 403 errors with a different error code unchanged
- get_all_users re-raises 403 with a different graph_error_code unchanged
- _sync_staff_assignments retries without signInActivity on non-premium 403
- _sync_staff_assignments re-raises other 403 errors unchanged
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import m365 as m365_service
from app.services.m365 import M365Error, _NON_PREMIUM_ERROR_CODE, _graph_get


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# M365Error carries graph_error_code
# ---------------------------------------------------------------------------


def test_m365_error_carries_graph_error_code():
    """M365Error stores graph_error_code attribute."""
    err = M365Error("msg", http_status=403, graph_error_code="SomeCode")
    assert err.http_status == 403
    assert err.graph_error_code == "SomeCode"


def test_m365_error_graph_error_code_defaults_to_none():
    """M365Error.graph_error_code defaults to None when not provided."""
    err = M365Error("msg", http_status=403)
    assert err.graph_error_code is None


# ---------------------------------------------------------------------------
# _graph_get populates graph_error_code
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_graph_get_populates_graph_error_code_from_response():
    """_graph_get extracts error.code from the JSON error body and stores it in M365Error."""
    error_body = {
        "error": {
            "code": _NON_PREMIUM_ERROR_CODE,
            "message": "Tenant does not have premium license",
        }
    }

    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.text = json.dumps(error_body)
    mock_response.json.return_value = error_body

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client_ctx = MagicMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.m365.httpx.AsyncClient", return_value=mock_client_ctx):
        with pytest.raises(M365Error) as exc_info:
            await _graph_get("fake-token", "https://graph.microsoft.com/v1.0/users")

    assert exc_info.value.http_status == 403
    assert exc_info.value.graph_error_code == _NON_PREMIUM_ERROR_CODE


@pytest.mark.anyio("asyncio")
async def test_graph_get_graph_error_code_is_none_when_body_not_json():
    """_graph_get sets graph_error_code to None when the response body is not JSON."""
    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.text = "Forbidden"
    mock_response.json.side_effect = ValueError("no JSON")

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client_ctx = MagicMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.m365.httpx.AsyncClient", return_value=mock_client_ctx):
        with pytest.raises(M365Error) as exc_info:
            await _graph_get("fake-token", "https://graph.microsoft.com/v1.0/users")

    assert exc_info.value.http_status == 403
    assert exc_info.value.graph_error_code is None


# ---------------------------------------------------------------------------
# get_all_users – non-premium 403 retry
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_get_all_users_retries_without_sign_in_activity_on_non_premium_403():
    """get_all_users retries without signInActivity when non-premium 403 is received."""
    call_count = 0
    captured_urls: list[str] = []

    async def mock_graph_get(
        token: str,
        url: str,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        captured_urls.append(url)
        if call_count == 1:
            # First call (with signInActivity) fails with non-premium 403
            raise M365Error(
                "Microsoft Graph request failed (403)",
                http_status=403,
                graph_error_code=_NON_PREMIUM_ERROR_CODE,
            )
        # Retry (without signInActivity) succeeds
        return {"value": [{"id": "u1", "mail": "user@example.com", "accountEnabled": True}]}

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="fake-token")),
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
    ):
        users = await m365_service.get_all_users(company_id=1)

    assert call_count == 2
    assert "signInActivity" in captured_urls[0], "First call must include signInActivity"
    assert "signInActivity" not in captured_urls[1], "Retry must NOT include signInActivity"
    assert len(users) == 1
    assert users[0]["mail"] == "user@example.com"


@pytest.mark.anyio("asyncio")
async def test_get_all_users_reraises_403_with_different_error_code():
    """get_all_users re-raises 403 errors with a graph_error_code other than non-premium."""
    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="fake-token")),
        patch.object(
            m365_service,
            "_graph_get",
            AsyncMock(
                side_effect=M365Error(
                    "Microsoft Graph request failed (403)",
                    http_status=403,
                    graph_error_code="Authorization_RequestDenied",
                )
            ),
        ),
    ):
        with pytest.raises(M365Error) as exc_info:
            await m365_service.get_all_users(company_id=1)

    assert exc_info.value.http_status == 403
    assert exc_info.value.graph_error_code == "Authorization_RequestDenied"


@pytest.mark.anyio("asyncio")
async def test_get_all_users_reraises_403_with_no_error_code():
    """get_all_users re-raises a plain 403 (no graph_error_code) without retrying."""
    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="fake-token")),
        patch.object(
            m365_service,
            "_graph_get",
            AsyncMock(
                side_effect=M365Error(
                    "Microsoft Graph request failed (403)",
                    http_status=403,
                    graph_error_code=None,
                )
            ),
        ),
    ):
        with pytest.raises(M365Error) as exc_info:
            await m365_service.get_all_users(company_id=1)

    assert exc_info.value.http_status == 403
    assert exc_info.value.graph_error_code is None


@pytest.mark.anyio("asyncio")
async def test_get_all_users_non_premium_retry_follows_pagination():
    """After non-premium 403, the retry in get_all_users follows @odata.nextLink pages."""
    call_count = 0

    async def mock_graph_get(
        token: str,
        url: str,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise M365Error(
                "Microsoft Graph request failed (403)",
                http_status=403,
                graph_error_code=_NON_PREMIUM_ERROR_CODE,
            )
        if call_count == 2:
            return {
                "value": [{"id": "u1", "mail": "a@example.com", "accountEnabled": True}],
                "@odata.nextLink": "https://graph.microsoft.com/v1.0/users?$skiptoken=page2",
            }
        return {"value": [{"id": "u2", "mail": "b@example.com", "accountEnabled": True}]}

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="fake-token")),
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
    ):
        users = await m365_service.get_all_users(company_id=1)

    assert call_count == 3
    assert len(users) == 2


# ---------------------------------------------------------------------------
# _sync_staff_assignments – non-premium 403 retry
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_sync_staff_assignments_retries_without_sign_in_activity_on_non_premium_403():
    """_sync_staff_assignments retries without signInActivity when non-premium 403 is received."""
    call_count = 0
    captured_urls: list[str] = []

    async def mock_graph_get(
        token: str,
        url: str,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        captured_urls.append(url)
        if call_count == 1:
            raise M365Error(
                "Microsoft Graph request failed (403)",
                http_status=403,
                graph_error_code=_NON_PREMIUM_ERROR_CODE,
            )
        return {"value": []}

    with (
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
        patch.object(m365_service.license_repo, "list_staff_for_license", AsyncMock(return_value=[])),
        patch.object(m365_service.license_repo, "bulk_unlink_staff", AsyncMock()),
    ):
        # Should not raise
        await m365_service._sync_staff_assignments(
            company_id=1,
            license_id=10,
            access_token="fake-token",
            sku_id="84a661c4-e949-4bd2-a560-ed7766fcaf2b",
        )

    assert call_count == 2
    assert "signInActivity" in captured_urls[0], "First call must include signInActivity"
    assert "signInActivity" not in captured_urls[1], "Retry must NOT include signInActivity"


@pytest.mark.anyio("asyncio")
async def test_sync_staff_assignments_reraises_other_403_errors():
    """_sync_staff_assignments re-raises 403 errors that are not the non-premium error."""
    with (
        patch.object(
            m365_service,
            "_graph_get",
            AsyncMock(
                side_effect=M365Error(
                    "Microsoft Graph request failed (403)",
                    http_status=403,
                    graph_error_code="Authorization_RequestDenied",
                )
            ),
        ),
        patch.object(m365_service.license_repo, "list_staff_for_license", AsyncMock(return_value=[])),
        patch.object(m365_service.license_repo, "bulk_unlink_staff", AsyncMock()),
    ):
        with pytest.raises(M365Error) as exc_info:
            await m365_service._sync_staff_assignments(
                company_id=1,
                license_id=10,
                access_token="fake-token",
                sku_id="84a661c4-e949-4bd2-a560-ed7766fcaf2b",
            )

    assert exc_info.value.http_status == 403
    assert exc_info.value.graph_error_code == "Authorization_RequestDenied"


@pytest.mark.anyio("asyncio")
async def test_sync_staff_assignments_non_premium_retry_creates_staff_without_sign_in():
    """After non-premium retry, _sync_staff_assignments creates staff with m365_last_sign_in=None."""
    call_count = 0
    create_calls: list[dict] = []

    async def mock_graph_get(
        token: str,
        url: str,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise M365Error(
                "Microsoft Graph request failed (403)",
                http_status=403,
                graph_error_code=_NON_PREMIUM_ERROR_CODE,
            )
        return {
            "value": [
                {
                    "id": "u1",
                    "mail": "alice@example.com",
                    "givenName": "Alice",
                    "surname": "Smith",
                    "accountEnabled": True,
                }
            ]
        }

    async def mock_create_staff(**kwargs: Any) -> dict[str, Any]:
        create_calls.append(kwargs)
        return {"id": 1, "email": "alice@example.com"}

    with (
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
        patch.object(
            m365_service.staff_repo,
            "get_staff_by_company_and_email",
            AsyncMock(return_value=None),
        ),
        patch.object(m365_service.staff_repo, "create_staff", side_effect=mock_create_staff),
        patch.object(m365_service.license_repo, "link_staff_to_license", AsyncMock()),
        patch.object(m365_service.license_repo, "list_staff_for_license", AsyncMock(return_value=[])),
        patch.object(m365_service.license_repo, "bulk_unlink_staff", AsyncMock()),
    ):
        await m365_service._sync_staff_assignments(
            company_id=1,
            license_id=10,
            access_token="fake-token",
            sku_id="84a661c4-e949-4bd2-a560-ed7766fcaf2b",
        )

    assert len(create_calls) == 1
    # m365_last_sign_in must be explicitly None since signInActivity was not
    # available in the premium-free retry path
    assert create_calls[0]["m365_last_sign_in"] is None
