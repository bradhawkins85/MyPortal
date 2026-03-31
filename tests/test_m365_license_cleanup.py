"""Tests for license cleanup during M365 sync.

Verifies that sync_company_licenses removes:
- licenses whose SKU is no longer returned by the M365 subscribedSkus endpoint
- licenses whose expiry_date has already passed

And that it retains:
- licenses whose SKU was returned by M365
- licenses with a future expiry_date
- licenses with no expiry_date set
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.services import m365 as m365_service


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _make_sku(part_number: str, sku_id: str, count: int = 5) -> dict[str, Any]:
    return {
        "skuPartNumber": part_number,
        "skuId": sku_id,
        "prepaidUnits": {"enabled": count},
    }


def _make_license(
    license_id: int,
    platform: str,
    expiry_date: date | None = None,
) -> dict[str, Any]:
    return {
        "id": license_id,
        "company_id": 1,
        "name": platform,
        "platform": platform,
        "count": 5,
        "expiry_date": expiry_date,
        "contract_term": "",
    }


def _make_m365_app(vendor_sku: str) -> dict[str, Any]:
    return {"id": 10, "vendor_sku": vendor_sku, "license_sku_id": "graph-guid"}


# ---------------------------------------------------------------------------
# Stale license (SKU no longer in M365 tenant) is removed
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_sync_removes_license_not_in_tenant():
    """A license in the DB whose platform SKU is not returned by M365
    subscribedSkus should be deleted during sync."""
    m365_skus = [_make_sku("ACTIVE_SKU", "sku-id-active")]
    db_licenses = [
        _make_license(1, "ACTIVE_SKU"),
        _make_license(2, "OLD_SKU"),  # no longer in tenant
    ]

    deleted_ids: list[int] = []

    async def _mock_delete(license_id: int) -> None:
        deleted_ids.append(license_id)

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(
            m365_service,
            "_graph_get",
            AsyncMock(return_value={"value": m365_skus}),
        ),
        patch.object(m365_service.apps_repo, "get_app_by_vendor_sku", AsyncMock(side_effect=_make_m365_app)),
        patch.object(
            m365_service.license_repo,
            "get_license_by_company_and_sku",
            AsyncMock(return_value=_make_license(1, "ACTIVE_SKU")),
        ),
        patch.object(m365_service.license_repo, "update_license", AsyncMock(return_value=_make_license(1, "ACTIVE_SKU"))),
        patch.object(m365_service, "_sync_staff_assignments", AsyncMock()),
        patch.object(
            m365_service.license_repo,
            "list_company_licenses",
            AsyncMock(return_value=db_licenses),
        ),
        patch.object(m365_service.license_repo, "delete_license", side_effect=_mock_delete),
        patch.object(m365_service, "log_info", lambda *a, **kw: None),
    ):
        await m365_service.sync_company_licenses(1)

    assert 2 in deleted_ids, "Stale license (OLD_SKU) should have been deleted"
    assert 1 not in deleted_ids, "Active license (ACTIVE_SKU) should not have been deleted"


# ---------------------------------------------------------------------------
# Expired license is removed
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_sync_removes_expired_license():
    """A license whose expiry_date is in the past should be deleted even if
    its SKU is still returned by M365."""
    yesterday = date.today() - timedelta(days=1)
    m365_skus = [_make_sku("SKU_A", "sku-id-a")]
    db_licenses = [
        _make_license(1, "SKU_A", expiry_date=yesterday),  # expired
    ]

    deleted_ids: list[int] = []

    async def _mock_delete(license_id: int) -> None:
        deleted_ids.append(license_id)

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(
            m365_service,
            "_graph_get",
            AsyncMock(return_value={"value": m365_skus}),
        ),
        patch.object(m365_service.apps_repo, "get_app_by_vendor_sku", AsyncMock(side_effect=_make_m365_app)),
        patch.object(
            m365_service.license_repo,
            "get_license_by_company_and_sku",
            AsyncMock(return_value=_make_license(1, "SKU_A")),
        ),
        patch.object(m365_service.license_repo, "update_license", AsyncMock(return_value=_make_license(1, "SKU_A"))),
        patch.object(m365_service, "_sync_staff_assignments", AsyncMock()),
        patch.object(
            m365_service.license_repo,
            "list_company_licenses",
            AsyncMock(return_value=db_licenses),
        ),
        patch.object(m365_service.license_repo, "delete_license", side_effect=_mock_delete),
        patch.object(m365_service, "log_info", lambda *a, **kw: None),
    ):
        await m365_service.sync_company_licenses(1)

    assert 1 in deleted_ids, "Expired license should have been deleted"


# ---------------------------------------------------------------------------
# Current license with future expiry is kept
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_sync_retains_license_with_future_expiry():
    """A license with a future expiry_date that is still in M365 should not
    be deleted."""
    tomorrow = date.today() + timedelta(days=1)
    m365_skus = [_make_sku("SKU_A", "sku-id-a")]
    db_licenses = [
        _make_license(1, "SKU_A", expiry_date=tomorrow),
    ]

    deleted_ids: list[int] = []

    async def _mock_delete(license_id: int) -> None:
        deleted_ids.append(license_id)

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(
            m365_service,
            "_graph_get",
            AsyncMock(return_value={"value": m365_skus}),
        ),
        patch.object(m365_service.apps_repo, "get_app_by_vendor_sku", AsyncMock(side_effect=_make_m365_app)),
        patch.object(
            m365_service.license_repo,
            "get_license_by_company_and_sku",
            AsyncMock(return_value=_make_license(1, "SKU_A")),
        ),
        patch.object(m365_service.license_repo, "update_license", AsyncMock(return_value=_make_license(1, "SKU_A"))),
        patch.object(m365_service, "_sync_staff_assignments", AsyncMock()),
        patch.object(
            m365_service.license_repo,
            "list_company_licenses",
            AsyncMock(return_value=db_licenses),
        ),
        patch.object(m365_service.license_repo, "delete_license", side_effect=_mock_delete),
        patch.object(m365_service, "log_info", lambda *a, **kw: None),
    ):
        await m365_service.sync_company_licenses(1)

    assert 1 not in deleted_ids, "License with future expiry should not be deleted"


# ---------------------------------------------------------------------------
# License with no expiry_date and present in M365 is kept
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_sync_retains_license_with_no_expiry():
    """A license with no expiry_date whose SKU is still in M365 must not be
    deleted."""
    m365_skus = [_make_sku("SKU_A", "sku-id-a")]
    db_licenses = [
        _make_license(1, "SKU_A", expiry_date=None),
    ]

    deleted_ids: list[int] = []

    async def _mock_delete(license_id: int) -> None:
        deleted_ids.append(license_id)

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(
            m365_service,
            "_graph_get",
            AsyncMock(return_value={"value": m365_skus}),
        ),
        patch.object(m365_service.apps_repo, "get_app_by_vendor_sku", AsyncMock(side_effect=_make_m365_app)),
        patch.object(
            m365_service.license_repo,
            "get_license_by_company_and_sku",
            AsyncMock(return_value=_make_license(1, "SKU_A")),
        ),
        patch.object(m365_service.license_repo, "update_license", AsyncMock(return_value=_make_license(1, "SKU_A"))),
        patch.object(m365_service, "_sync_staff_assignments", AsyncMock()),
        patch.object(
            m365_service.license_repo,
            "list_company_licenses",
            AsyncMock(return_value=db_licenses),
        ),
        patch.object(m365_service.license_repo, "delete_license", side_effect=_mock_delete),
        patch.object(m365_service, "log_info", lambda *a, **kw: None),
    ):
        await m365_service.sync_company_licenses(1)

    assert 1 not in deleted_ids, "License with no expiry should not be deleted if SKU still in M365"


# ---------------------------------------------------------------------------
# Non-M365/manual license is retained
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_sync_does_not_remove_non_m365_license():
    """Licenses not mapped to an M365 app must not be touched by M365 cleanup."""
    m365_skus = [_make_sku("M365_SKU", "sku-id-a")]
    db_licenses = [
        _make_license(1, "M365_SKU"),
        _make_license(2, "MANUAL_LICENSE"),
    ]

    deleted_ids: list[int] = []

    async def _mock_delete(license_id: int) -> None:
        deleted_ids.append(license_id)

    async def _mock_get_app(vendor_sku: str) -> dict[str, Any] | None:
        if vendor_sku == "M365_SKU":
            return {"id": 10, "vendor_sku": "M365_SKU", "license_sku_id": "graph-guid"}
        return None

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(
            m365_service,
            "_graph_get",
            AsyncMock(return_value={"value": m365_skus}),
        ),
        patch.object(m365_service.apps_repo, "get_app_by_vendor_sku", side_effect=_mock_get_app),
        patch.object(
            m365_service.license_repo,
            "get_license_by_company_and_sku",
            AsyncMock(return_value=_make_license(1, "M365_SKU")),
        ),
        patch.object(m365_service.license_repo, "update_license", AsyncMock(return_value=_make_license(1, "M365_SKU"))),
        patch.object(m365_service, "_sync_staff_assignments", AsyncMock()),
        patch.object(
            m365_service.license_repo,
            "list_company_licenses",
            AsyncMock(return_value=db_licenses),
        ),
        patch.object(m365_service.license_repo, "delete_license", side_effect=_mock_delete),
        patch.object(m365_service, "log_info", lambda *a, **kw: None),
    ):
        await m365_service.sync_company_licenses(1)

    assert 2 not in deleted_ids, "Manual/non-M365 license should not be deleted"


# ---------------------------------------------------------------------------
# Removal of stale license is logged
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_sync_logs_stale_license_removal():
    """When a stale license is removed, a log message must be emitted with
    company_id, license_id, and platform."""
    m365_skus: list[dict[str, Any]] = []  # no SKUs returned – all existing are stale
    db_licenses = [_make_license(7, "GONE_SKU")]

    logged_calls: list[tuple[str, dict[str, Any]]] = []

    def _capture(msg: str, **kwargs: Any) -> None:
        logged_calls.append((msg, kwargs))

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(
            m365_service,
            "_graph_get",
            AsyncMock(return_value={"value": m365_skus}),
        ),
        patch.object(m365_service.apps_repo, "get_app_by_vendor_sku", AsyncMock(side_effect=_make_m365_app)),
        patch.object(
            m365_service.license_repo,
            "list_company_licenses",
            AsyncMock(return_value=db_licenses),
        ),
        patch.object(m365_service.license_repo, "delete_license", AsyncMock()),
        patch.object(m365_service, "log_info", side_effect=_capture),
    ):
        await m365_service.sync_company_licenses(1)

    removal_logs = [
        (msg, kw)
        for msg, kw in logged_calls
        if msg == "M365 removed stale or expired license"
    ]
    assert removal_logs, "Expected a 'M365 removed stale or expired license' log"
    _, kw = removal_logs[0]
    assert kw.get("company_id") == 1
    assert kw.get("license_id") == 7
    assert kw.get("platform") == "GONE_SKU"
    assert kw.get("reason") == "not_in_tenant"
