"""Tests for sync_company_licenses retrieving subscription details.

Covers:
- expiry_date is populated from nextLifecycleDateTime in directory/subscriptions
- auto_renew is populated from autoRenew in directory/subscriptions
- contract_term is populated from subscriptionTermInfo.termDuration
- known termDuration values are mapped to human-readable labels
- fields are preserved when directory/subscriptions call fails
- _parse_subscription_date handles valid and invalid input
"""
from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.services import m365 as m365_service
from app.services.m365 import _parse_subscription_date, _TERM_DURATION_LABELS


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# _parse_subscription_date helper
# ---------------------------------------------------------------------------


def test_parse_subscription_date_valid_utc() -> None:
    result = _parse_subscription_date("2026-01-15T00:00:00Z")
    assert result == date(2026, 1, 15)


def test_parse_subscription_date_no_tz() -> None:
    result = _parse_subscription_date("2026-06-30T12:00:00")
    assert result == date(2026, 6, 30)


def test_parse_subscription_date_none() -> None:
    assert _parse_subscription_date(None) is None


def test_parse_subscription_date_empty_string() -> None:
    assert _parse_subscription_date("") is None


def test_parse_subscription_date_invalid() -> None:
    assert _parse_subscription_date("not-a-date") is None


# ---------------------------------------------------------------------------
# _TERM_DURATION_LABELS mappings
# ---------------------------------------------------------------------------


def test_term_duration_labels_known_values() -> None:
    assert _TERM_DURATION_LABELS["P1M"] == "Monthly"
    assert _TERM_DURATION_LABELS["P1Y"] == "Annual"
    assert _TERM_DURATION_LABELS["P2Y"] == "2-Year"
    assert _TERM_DURATION_LABELS["P3Y"] == "3-Year"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sku(part_number: str, sku_id: str, count: int = 5) -> dict[str, Any]:
    return {
        "skuPartNumber": part_number,
        "skuId": sku_id,
        "prepaidUnits": {"enabled": count},
    }


def _make_subscription(
    sku_id: str,
    next_lifecycle: str,
    auto_renew: bool,
    term_duration: str | None = None,
) -> dict[str, Any]:
    sub: dict[str, Any] = {
        "skuId": sku_id,
        "nextLifecycleDateTime": next_lifecycle,
        "autoRenew": auto_renew,
    }
    if term_duration is not None:
        sub["subscriptionTermInfo"] = {"termDuration": term_duration}
    return sub


def _make_license(
    license_id: int,
    platform: str,
    expiry_date: date | None = None,
    contract_term: str | None = "",
    auto_renew: bool | None = None,
) -> dict[str, Any]:
    return {
        "id": license_id,
        "company_id": 1,
        "name": platform,
        "platform": platform,
        "count": 5,
        "expiry_date": expiry_date,
        "contract_term": contract_term,
        "auto_renew": auto_renew,
    }


# ---------------------------------------------------------------------------
# expiry_date, auto_renew, and contract_term are populated from subscriptions
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_sync_populates_expiry_date_from_subscription():
    """nextLifecycleDateTime in directory/subscriptions should become expiry_date."""
    m365_skus = [_make_sku("SKU_A", "sku-id-a")]
    subscriptions = [_make_subscription("sku-id-a", "2027-03-01T00:00:00Z", True, "P1Y")]
    update_calls: list[dict[str, Any]] = []

    async def fake_graph_get(token, url):
        if "subscribedSkus" in url:
            return {"value": m365_skus}
        return {"value": subscriptions}

    async def capture_update(license_id, **kwargs):
        update_calls.append(kwargs)
        return _make_license(license_id, kwargs["platform"])

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_graph_get", side_effect=fake_graph_get),
        patch.object(m365_service.apps_repo, "get_app_by_vendor_sku", AsyncMock(return_value=None)),
        patch.object(m365_service.sku_friendly_repo, "get_friendly_name", AsyncMock(return_value=None)),
        patch.object(
            m365_service.license_repo,
            "get_license_by_company_and_sku",
            AsyncMock(return_value=_make_license(1, "SKU_A")),
        ),
        patch.object(m365_service.license_repo, "update_license", side_effect=capture_update),
        patch.object(m365_service, "_sync_staff_assignments", AsyncMock()),
        patch.object(m365_service.license_repo, "list_company_licenses", AsyncMock(return_value=[])),
        patch.object(m365_service, "log_info", lambda *a, **kw: None),
    ):
        await m365_service.sync_company_licenses(1)

    assert update_calls, "update_license should have been called"
    assert update_calls[0]["expiry_date"] == date(2027, 3, 1)
    assert update_calls[0]["auto_renew"] is True
    assert update_calls[0]["contract_term"] == "Annual"


# ---------------------------------------------------------------------------
# auto_renew=False is correctly stored
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_sync_populates_auto_renew_false():
    """autoRenew=False should be stored as auto_renew=False."""
    m365_skus = [_make_sku("SKU_B", "sku-id-b")]
    subscriptions = [_make_subscription("sku-id-b", "2027-06-01T00:00:00Z", False)]
    update_calls: list[dict[str, Any]] = []

    async def fake_graph_get(token, url):
        if "subscribedSkus" in url:
            return {"value": m365_skus}
        return {"value": subscriptions}

    async def capture_update(license_id, **kwargs):
        update_calls.append(kwargs)
        return _make_license(license_id, kwargs["platform"])

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_graph_get", side_effect=fake_graph_get),
        patch.object(m365_service.apps_repo, "get_app_by_vendor_sku", AsyncMock(return_value=None)),
        patch.object(m365_service.sku_friendly_repo, "get_friendly_name", AsyncMock(return_value=None)),
        patch.object(
            m365_service.license_repo,
            "get_license_by_company_and_sku",
            AsyncMock(return_value=_make_license(1, "SKU_B")),
        ),
        patch.object(m365_service.license_repo, "update_license", side_effect=capture_update),
        patch.object(m365_service, "_sync_staff_assignments", AsyncMock()),
        patch.object(m365_service.license_repo, "list_company_licenses", AsyncMock(return_value=[])),
        patch.object(m365_service, "log_info", lambda *a, **kw: None),
    ):
        await m365_service.sync_company_licenses(1)

    assert update_calls, "update_license should have been called"
    assert update_calls[0]["auto_renew"] is False


# ---------------------------------------------------------------------------
# Fields are preserved when directory/subscriptions call fails
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_sync_preserves_fields_when_subscriptions_call_fails():
    """When directory/subscriptions raises, existing expiry_date and contract_term are kept."""
    existing_expiry = date(2026, 12, 31)
    m365_skus = [_make_sku("SKU_C", "sku-id-c")]
    update_calls: list[dict[str, Any]] = []

    async def fake_graph_get(token, url):
        if "subscribedSkus" in url:
            return {"value": m365_skus}
        raise Exception("directory/subscriptions unavailable")

    async def capture_update(license_id, **kwargs):
        update_calls.append(kwargs)
        return _make_license(license_id, kwargs["platform"])

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_graph_get", side_effect=fake_graph_get),
        patch.object(m365_service.apps_repo, "get_app_by_vendor_sku", AsyncMock(return_value=None)),
        patch.object(m365_service.sku_friendly_repo, "get_friendly_name", AsyncMock(return_value=None)),
        patch.object(
            m365_service.license_repo,
            "get_license_by_company_and_sku",
            AsyncMock(return_value=_make_license(1, "SKU_C", expiry_date=existing_expiry, contract_term="Annual")),
        ),
        patch.object(m365_service.license_repo, "update_license", side_effect=capture_update),
        patch.object(m365_service, "_sync_staff_assignments", AsyncMock()),
        patch.object(m365_service.license_repo, "list_company_licenses", AsyncMock(return_value=[])),
        patch.object(m365_service, "log_info", lambda *a, **kw: None),
    ):
        await m365_service.sync_company_licenses(1)

    assert update_calls, "update_license should have been called even after subscription fetch failure"
    assert update_calls[0]["expiry_date"] == existing_expiry
    assert update_calls[0]["contract_term"] == "Annual"
    assert update_calls[0]["auto_renew"] is None


# ---------------------------------------------------------------------------
# New license created with subscription data
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_sync_creates_license_with_subscription_data():
    """New licenses should be created with expiry_date, auto_renew, contract_term from subscription."""
    m365_skus = [_make_sku("SKU_NEW", "sku-id-new")]
    subscriptions = [_make_subscription("sku-id-new", "2026-09-15T00:00:00Z", True, "P1M")]
    create_calls: list[dict[str, Any]] = []

    async def fake_graph_get(token, url):
        if "subscribedSkus" in url:
            return {"value": m365_skus}
        return {"value": subscriptions}

    async def capture_create(**kwargs):
        create_calls.append(kwargs)
        return _make_license(99, kwargs["platform"])

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_graph_get", side_effect=fake_graph_get),
        patch.object(m365_service.apps_repo, "get_app_by_vendor_sku", AsyncMock(return_value=None)),
        patch.object(m365_service.sku_friendly_repo, "get_friendly_name", AsyncMock(return_value=None)),
        patch.object(
            m365_service.license_repo,
            "get_license_by_company_and_sku",
            AsyncMock(return_value=None),  # not yet in DB
        ),
        patch.object(m365_service.license_repo, "create_license", side_effect=capture_create),
        patch.object(m365_service, "_sync_staff_assignments", AsyncMock()),
        patch.object(m365_service.license_repo, "list_company_licenses", AsyncMock(return_value=[])),
        patch.object(m365_service, "log_info", lambda *a, **kw: None),
    ):
        await m365_service.sync_company_licenses(1)

    assert create_calls, "create_license should have been called"
    assert create_calls[0]["expiry_date"] == date(2026, 9, 15)
    assert create_calls[0]["auto_renew"] is True
    assert create_calls[0]["contract_term"] == "Monthly"
