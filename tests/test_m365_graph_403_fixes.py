"""Tests for Microsoft Graph 403 error fixes.

Covers:
- _graph_get supports extra_headers (needed for ConsistencyLevel: eventual)
- _sync_staff_assignments sends ConsistencyLevel: eventual + $count=true

The test for _check_audit_log_enabled returning STATUS_UNKNOWN (not STATUS_FAIL)
on 403 is in tests/test_cis_benchmark.py::test_check_audit_log_enabled_unknown_on_403.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import m365 as m365_service
from app.services.m365 import M365Error, _graph_get


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# _graph_get extra_headers
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_graph_get_sends_extra_headers():
    """_graph_get forwards extra_headers to the HTTP request."""
    captured_headers: dict[str, str] = {}

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"value": []}

    async def mock_get(url: str, headers: dict[str, str]) -> Any:
        captured_headers.update(headers)
        return mock_response

    mock_client = MagicMock()
    mock_client.get = mock_get
    mock_client_ctx = MagicMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.m365.httpx.AsyncClient", return_value=mock_client_ctx):
        await _graph_get(
            "fake-token",
            "https://graph.microsoft.com/v1.0/test",
            extra_headers={"ConsistencyLevel": "eventual"},
        )

    assert captured_headers.get("ConsistencyLevel") == "eventual"
    assert captured_headers.get("Authorization") == "Bearer fake-token"


@pytest.mark.anyio("asyncio")
async def test_graph_get_without_extra_headers_still_works():
    """_graph_get works normally when no extra_headers are provided."""
    captured_headers: dict[str, str] = {}

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"result": "ok"}

    async def mock_get(url: str, headers: dict[str, str]) -> Any:
        captured_headers.update(headers)
        return mock_response

    mock_client = MagicMock()
    mock_client.get = mock_get
    mock_client_ctx = MagicMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.m365.httpx.AsyncClient", return_value=mock_client_ctx):
        result = await _graph_get("fake-token", "https://graph.microsoft.com/v1.0/test")

    assert result == {"result": "ok"}
    # Only the Authorization header should be present (no ConsistencyLevel)
    assert "ConsistencyLevel" not in captured_headers
    assert captured_headers.get("Authorization") == "Bearer fake-token"


# ---------------------------------------------------------------------------
# _sync_staff_assignments uses ConsistencyLevel: eventual
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_sync_staff_assignments_sends_consistency_level_header():
    """_sync_staff_assignments sends ConsistencyLevel: eventual for the
    advanced assignedLicenses filter query.

    Microsoft Graph requires ConsistencyLevel: eventual (plus $count=true) for
    $filter=assignedLicenses/any(...) – without it the API returns a 400 error.
    """
    captured_urls: list[str] = []
    captured_extra_headers: list[dict[str, str] | None] = []

    async def mock_graph_get(
        token: str,
        url: str,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        captured_urls.append(url)
        captured_extra_headers.append(extra_headers)
        return {"value": []}

    with (
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
        patch.object(m365_service.license_repo, "list_staff_for_license", AsyncMock(return_value=[])),
        patch.object(m365_service.license_repo, "bulk_unlink_staff", AsyncMock()),
    ):
        await m365_service._sync_staff_assignments(
            company_id=1,
            license_id=10,
            access_token="fake-token",
            sku_id="84a661c4-e949-4bd2-a560-ed7766fcaf2b",
        )

    assert len(captured_urls) == 1
    url = captured_urls[0]
    # Advanced query parameters must be present
    assert "$count=true" in url
    assert "assignedLicenses/any" in url

    # ConsistencyLevel header must be sent
    assert captured_extra_headers[0] is not None
    assert captured_extra_headers[0].get("ConsistencyLevel") == "eventual"


@pytest.mark.anyio("asyncio")
async def test_sync_staff_assignments_url_includes_count_param():
    """_sync_staff_assignments includes $count=true in the URL query string."""
    captured_urls: list[str] = []

    async def mock_graph_get(
        token: str,
        url: str,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        captured_urls.append(url)
        return {"value": []}

    with (
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
        patch.object(m365_service.license_repo, "list_staff_for_license", AsyncMock(return_value=[])),
        patch.object(m365_service.license_repo, "bulk_unlink_staff", AsyncMock()),
    ):
        await m365_service._sync_staff_assignments(
            company_id=1,
            license_id=10,
            access_token="fake-token",
            sku_id="84a661c4-e949-4bd2-a560-ed7766fcaf2b",
        )

    assert len(captured_urls) == 1
    assert "$count=true" in captured_urls[0]
