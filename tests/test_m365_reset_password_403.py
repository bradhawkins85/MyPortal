"""Tests for 403 error handling in the M365 reset-password flow.

Covers:
- _graph_patch extracts graph_error_code from 403 response bodies
- reset_user_password surfaces a clear actionable message on Authorization_RequestDenied (403)
- reset_user_password propagates other M365Error instances unchanged
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import m365 as m365_service
from app.services.m365 import M365Error, _graph_patch


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# _graph_patch – error body extraction
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_graph_patch_extracts_graph_error_code_on_403():
    """_graph_patch sets graph_error_code on M365Error when Graph returns 403."""
    error_body = {
        "error": {
            "code": "Authorization_RequestDenied",
            "message": "Insufficient privileges to complete the operation.",
        }
    }

    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.text = str(error_body)
    mock_response.json.return_value = error_body

    mock_client = MagicMock()
    mock_client.patch = AsyncMock(return_value=mock_response)
    mock_client_ctx = MagicMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.m365.httpx.AsyncClient", return_value=mock_client_ctx):
        with pytest.raises(M365Error) as exc_info:
            await _graph_patch(
                "fake-token",
                "https://graph.microsoft.com/v1.0/users/abc123",
                {"passwordProfile": {"password": "Test@12345"}},
            )

    exc = exc_info.value
    assert exc.http_status == 403
    assert exc.graph_error_code == "Authorization_RequestDenied"
    assert "Insufficient privileges" in str(exc)


@pytest.mark.anyio("asyncio")
async def test_graph_patch_includes_graph_error_message_in_exception_text():
    """_graph_patch appends the Graph error message to the exception text."""
    error_body = {
        "error": {
            "code": "Authorization_RequestDenied",
            "message": "Insufficient privileges to complete the operation.",
        }
    }

    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.text = str(error_body)
    mock_response.json.return_value = error_body

    mock_client = MagicMock()
    mock_client.patch = AsyncMock(return_value=mock_response)
    mock_client_ctx = MagicMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.m365.httpx.AsyncClient", return_value=mock_client_ctx):
        with pytest.raises(M365Error) as exc_info:
            await _graph_patch(
                "fake-token",
                "https://graph.microsoft.com/v1.0/users/abc123",
                {"passwordProfile": {"password": "Test@12345"}},
            )

    assert "Insufficient privileges" in str(exc_info.value)


@pytest.mark.anyio("asyncio")
async def test_graph_patch_handles_non_json_error_body_gracefully():
    """_graph_patch raises M365Error even when the error response body is not valid JSON."""
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"
    mock_response.json.side_effect = ValueError("not json")

    mock_client = MagicMock()
    mock_client.patch = AsyncMock(return_value=mock_response)
    mock_client_ctx = MagicMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.m365.httpx.AsyncClient", return_value=mock_client_ctx):
        with pytest.raises(M365Error) as exc_info:
            await _graph_patch(
                "fake-token",
                "https://graph.microsoft.com/v1.0/users/abc123",
                {"accountEnabled": False},
            )

    exc = exc_info.value
    assert exc.http_status == 500
    assert exc.graph_error_code is None


# ---------------------------------------------------------------------------
# reset_user_password – 403 Authorization_RequestDenied
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_reset_user_password_raises_actionable_403_on_authorization_request_denied():
    """reset_user_password raises a clear guidance message when Graph returns Authorization_RequestDenied."""
    with (
        patch.object(
            m365_service,
            "get_credentials",
            AsyncMock(return_value={"tenant_id": "t1", "client_id": "c1", "client_secret": "s1"}),
        ),
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(
            m365_service,
            "_lookup_user_by_email",
            AsyncMock(return_value={"id": "user-uuid-123"}),
        ),
        patch.object(m365_service, "_generate_m365_password", return_value="Fake@Password1"),
        patch.object(
            m365_service,
            "_graph_patch",
            AsyncMock(
                side_effect=M365Error(
                    "Microsoft Graph PATCH failed (403): Insufficient privileges to complete the operation.",
                    http_status=403,
                    graph_error_code="Authorization_RequestDenied",
                )
            ),
        ),
    ):
        with pytest.raises(M365Error) as exc_info:
            await m365_service.reset_user_password(1, "user@example.com")

    message = str(exc_info.value)
    assert "User.ReadWrite.All" in message
    assert exc_info.value.http_status == 403
    assert exc_info.value.graph_error_code == "Authorization_RequestDenied"


@pytest.mark.anyio("asyncio")
async def test_reset_user_password_propagates_other_m365_errors_unchanged():
    """reset_user_password lets non-403 M365Error exceptions propagate unchanged."""
    original_message = "Microsoft Graph PATCH failed (500)"
    with (
        patch.object(
            m365_service,
            "get_credentials",
            AsyncMock(return_value={"tenant_id": "t1", "client_id": "c1", "client_secret": "s1"}),
        ),
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(
            m365_service,
            "_lookup_user_by_email",
            AsyncMock(return_value={"id": "user-uuid-123"}),
        ),
        patch.object(m365_service, "_generate_m365_password", return_value="Fake@Password1"),
        patch.object(
            m365_service,
            "_graph_patch",
            AsyncMock(side_effect=M365Error(original_message, http_status=500)),
        ),
    ):
        with pytest.raises(M365Error) as exc_info:
            await m365_service.reset_user_password(1, "user@example.com")

    assert str(exc_info.value) == original_message


@pytest.mark.anyio("asyncio")
async def test_reset_user_password_propagates_non_authorization_denied_403():
    """reset_user_password propagates 403 errors that are NOT Authorization_RequestDenied unchanged."""
    original_message = "Microsoft Graph PATCH failed (403): Some other 403 error."
    with (
        patch.object(
            m365_service,
            "get_credentials",
            AsyncMock(return_value={"tenant_id": "t1", "client_id": "c1", "client_secret": "s1"}),
        ),
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(
            m365_service,
            "_lookup_user_by_email",
            AsyncMock(return_value={"id": "user-uuid-123"}),
        ),
        patch.object(m365_service, "_generate_m365_password", return_value="Fake@Password1"),
        patch.object(
            m365_service,
            "_graph_patch",
            AsyncMock(
                side_effect=M365Error(original_message, http_status=403, graph_error_code="SomeOtherCode")
            ),
        ),
    ):
        with pytest.raises(M365Error) as exc_info:
            await m365_service.reset_user_password(1, "user@example.com")

    assert str(exc_info.value) == original_message
