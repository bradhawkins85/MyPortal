"""Tests for M365 last sign-in date collection.

Covers:
- _sync_staff_assignments requests signInActivity in $select
- _sync_staff_assignments stores lastSignInDateTime when creating new staff
- _sync_staff_assignments updates m365_last_sign_in for existing staff
- get_all_users requests signInActivity in $select
- _import_from_m365 stores lastSignInDateTime via create_staff and update_staff
- parse_graph_datetime parses ISO 8601 strings correctly
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.services import m365 as m365_service
from app.services import staff_importer
from app.services.m365 import parse_graph_datetime


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# parse_graph_datetime helper
# ---------------------------------------------------------------------------


def test_parse_graph_datetime_with_z_suffix():
    """parse_graph_datetime handles 'Z' suffix (UTC) correctly."""
    result = parse_graph_datetime("2024-03-15T10:30:00Z")
    assert result == datetime(2024, 3, 15, 10, 30, 0)
    assert result.tzinfo is None  # stored as naive UTC


def test_parse_graph_datetime_with_offset():
    """parse_graph_datetime converts non-UTC offsets to UTC."""
    result = parse_graph_datetime("2024-03-15T20:30:00+10:00")
    assert result == datetime(2024, 3, 15, 10, 30, 0)
    assert result.tzinfo is None


def test_parse_graph_datetime_none():
    """parse_graph_datetime returns None for None input."""
    assert parse_graph_datetime(None) is None


def test_parse_graph_datetime_empty_string():
    """parse_graph_datetime returns None for empty string."""
    assert parse_graph_datetime("") is None


def test_parse_graph_datetime_invalid():
    """parse_graph_datetime returns None for invalid input."""
    assert parse_graph_datetime("not-a-date") is None


# ---------------------------------------------------------------------------
# _sync_staff_assignments includes signInActivity in $select
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_sync_staff_assignments_select_includes_sign_in_activity():
    """_sync_staff_assignments includes signInActivity in the $select parameter."""
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
    assert "signInActivity" in captured_urls[0]


# ---------------------------------------------------------------------------
# _sync_staff_assignments stores lastSignInDateTime on create
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_sync_staff_assignments_stores_last_sign_in_on_create():
    """_sync_staff_assignments passes m365_last_sign_in when creating a new staff record."""
    create_calls: list[dict] = []

    async def mock_graph_get(
        token: str,
        url: str,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return {
            "value": [
                {
                    "id": "u1",
                    "mail": "alice@example.com",
                    "givenName": "Alice",
                    "surname": "Smith",
                    "signInActivity": {"lastSignInDateTime": "2024-06-01T08:00:00Z"},
                },
            ]
        }

    async def mock_create_staff(**kwargs: Any) -> dict[str, Any]:
        create_calls.append(kwargs)
        return {"id": 1, "email": "alice@example.com"}

    with (
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
        patch.object(m365_service.staff_repo, "get_staff_by_company_and_email", AsyncMock(return_value=None)),
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
    assert create_calls[0]["m365_last_sign_in"] == datetime(2024, 6, 1, 8, 0, 0)


# ---------------------------------------------------------------------------
# _sync_staff_assignments updates lastSignInDateTime on existing staff
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_sync_staff_assignments_updates_last_sign_in_for_existing():
    """_sync_staff_assignments calls update_m365_last_sign_in for existing staff."""
    update_calls: list[tuple] = []

    async def mock_graph_get(
        token: str,
        url: str,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return {
            "value": [
                {
                    "id": "u1",
                    "mail": "alice@example.com",
                    "givenName": "Alice",
                    "surname": "Smith",
                    "signInActivity": {"lastSignInDateTime": "2024-06-15T09:30:00Z"},
                },
            ]
        }

    async def mock_update_m365_last_sign_in(staff_id: int, last_sign_in: Any) -> None:
        update_calls.append((staff_id, last_sign_in))

    with (
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
        patch.object(
            m365_service.staff_repo,
            "get_staff_by_company_and_email",
            AsyncMock(return_value={"id": 42, "email": "alice@example.com"}),
        ),
        patch.object(
            m365_service.staff_repo,
            "update_m365_last_sign_in",
            side_effect=mock_update_m365_last_sign_in,
        ),
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

    assert len(update_calls) == 1
    assert update_calls[0][0] == 42
    assert update_calls[0][1] == datetime(2024, 6, 15, 9, 30, 0)


@pytest.mark.anyio("asyncio")
async def test_sync_staff_assignments_no_update_when_sign_in_missing():
    """_sync_staff_assignments does NOT call update_m365_last_sign_in when
    signInActivity is absent from the Graph response."""
    update_calls: list[tuple] = []

    async def mock_graph_get(
        token: str,
        url: str,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return {
            "value": [
                {
                    "id": "u1",
                    "mail": "alice@example.com",
                    "givenName": "Alice",
                    "surname": "Smith",
                    # No signInActivity field
                },
            ]
        }

    async def mock_update_m365_last_sign_in(staff_id: int, last_sign_in: Any) -> None:
        update_calls.append((staff_id, last_sign_in))

    with (
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
        patch.object(
            m365_service.staff_repo,
            "get_staff_by_company_and_email",
            AsyncMock(return_value={"id": 42, "email": "alice@example.com"}),
        ),
        patch.object(
            m365_service.staff_repo,
            "update_m365_last_sign_in",
            side_effect=mock_update_m365_last_sign_in,
        ),
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

    assert len(update_calls) == 0


# ---------------------------------------------------------------------------
# get_all_users includes signInActivity in $select
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_get_all_users_select_includes_sign_in_activity():
    """get_all_users includes signInActivity in the $select parameter."""
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
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="fake-token")),
    ):
        await m365_service.get_all_users(company_id=1)

    assert len(captured_urls) >= 1
    assert "signInActivity" in captured_urls[0]


# ---------------------------------------------------------------------------
# _import_from_m365 stores lastSignInDateTime
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_import_from_m365_stores_last_sign_in_on_create():
    """_import_from_m365 passes m365_last_sign_in to create_staff."""
    create_calls: list[dict] = []

    async def mock_create_staff(**kwargs: Any) -> dict[str, Any]:
        create_calls.append(kwargs)
        staff = {
            "id": 1,
            "email": kwargs.get("email", ""),
            "first_name": kwargs.get("first_name", ""),
            "last_name": kwargs.get("last_name", ""),
        }
        return staff

    users = [
        {
            "id": "u1",
            "givenName": "Bob",
            "surname": "Jones",
            "displayName": "Bob Jones",
            "mail": "bob@example.com",
            "userPrincipalName": "bob@example.com",
            "mobilePhone": None,
            "businessPhones": [],
            "streetAddress": None,
            "city": None,
            "state": None,
            "postalCode": None,
            "country": None,
            "department": None,
            "jobTitle": None,
            "signInActivity": {"lastSignInDateTime": "2024-07-20T14:00:00Z"},
        }
    ]

    with (
        patch.object(m365_service, "get_all_users", AsyncMock(return_value=users)),
        patch.object(m365_service, "get_credentials", AsyncMock(return_value={"tenant_id": "t1"})),
        patch("app.services.staff_importer.staff_repo.list_staff", AsyncMock(return_value=[])),
        patch("app.services.staff_importer.staff_repo.create_staff", side_effect=mock_create_staff),
        patch("app.services.staff_importer.staff_repo.delete_m365_staff_not_in", AsyncMock(return_value=0)),
    ):
        await staff_importer._import_from_m365(company_id=1)

    assert len(create_calls) == 1
    assert create_calls[0]["m365_last_sign_in"] == datetime(2024, 7, 20, 14, 0, 0)


@pytest.mark.anyio("asyncio")
async def test_import_from_m365_stores_last_sign_in_on_update():
    """_import_from_m365 passes m365_last_sign_in to update_staff."""
    update_calls: list[tuple] = []

    async def mock_update_staff(staff_id: int, **kwargs: Any) -> dict[str, Any]:
        update_calls.append((staff_id, kwargs))
        return {"id": staff_id, "email": "carol@example.com"}

    existing = [
        {
            "id": 5,
            "email": "carol@example.com",
            "first_name": "Carol",
            "last_name": "White",
            "mobile_phone": None,
            "date_onboarded": None,
            "date_offboarded": None,
            "enabled": True,
            "street": None,
            "city": None,
            "state": None,
            "postcode": None,
            "country": None,
            "department": None,
            "job_title": None,
            "org_company": None,
            "manager_name": None,
            "account_action": None,
            "syncro_contact_id": None,
        }
    ]

    users = [
        {
            "id": "u2",
            "givenName": "Carol",
            "surname": "White",
            "displayName": "Carol White",
            "mail": "carol@example.com",
            "userPrincipalName": "carol@example.com",
            "mobilePhone": None,
            "businessPhones": [],
            "streetAddress": None,
            "city": None,
            "state": None,
            "postalCode": None,
            "country": None,
            "department": None,
            "jobTitle": None,
            "signInActivity": {"lastSignInDateTime": "2024-08-10T11:15:00Z"},
        }
    ]

    with (
        patch.object(m365_service, "get_all_users", AsyncMock(return_value=users)),
        patch("app.services.staff_importer.staff_repo.list_staff", AsyncMock(return_value=existing)),
        patch("app.services.staff_importer.staff_repo.update_staff", side_effect=mock_update_staff),
        patch("app.services.staff_importer.staff_repo.delete_m365_staff_not_in", AsyncMock(return_value=0)),
    ):
        await staff_importer._import_from_m365(company_id=1)

    assert len(update_calls) == 1
    assert update_calls[0][1]["m365_last_sign_in"] == datetime(2024, 8, 10, 11, 15, 0)
