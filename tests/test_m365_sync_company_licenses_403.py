"""Tests for 403 error handling in sync_company_licenses and sync_m365_data scheduler task.

Covers:
- sync_company_licenses raises an actionable M365Error on 403 from subscribedSkus
- sync_company_licenses auto-recovers when a delegated token is available and retry succeeds
- sync_company_licenses points to 'Authorise portal access' when no delegated token
- sync_company_licenses surfaces propagation-delay message when retry also fails with 403
- sync_m365_data task records licenses_sync_error and continues when sync_company_licenses fails
- sync_m365_data task records staff_sync_error and continues when import_m365_contacts_for_company fails
- sync_m365_data task still reports status=succeeded when license/staff sync errors occur
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import m365 as m365_service
from app.services.m365 import M365Error


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _make_task(company_id: int = 1) -> dict[str, Any]:
    return {
        "id": 99,
        "company_id": company_id,
        "command": "sync_m365_data",
    }


def _make_staff_summary() -> Any:
    s = MagicMock()
    s.created = 0
    s.updated = 0
    s.skipped = 0
    s.removed = 0
    s.total = 0
    return s


# ---------------------------------------------------------------------------
# sync_company_licenses – 403 raises actionable error
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_sync_company_licenses_403_no_delegated_token_raises_actionable_error():
    """When 403 and no delegated token, raises with 'Authorise portal access' guidance."""
    with (
        patch.object(
            m365_service,
            "acquire_access_token",
            AsyncMock(return_value="fake-token"),
        ),
        patch.object(
            m365_service,
            "_graph_get",
            AsyncMock(side_effect=M365Error("Microsoft Graph request failed (403)", http_status=403)),
        ),
        patch.object(
            m365_service,
            "acquire_delegated_token",
            AsyncMock(return_value=None),
        ),
    ):
        with pytest.raises(M365Error) as exc_info:
            await m365_service.sync_company_licenses(1)

    error_message = str(exc_info.value)
    assert "403" in error_message
    assert "Authorise portal access" in error_message


@pytest.mark.anyio("asyncio")
async def test_sync_company_licenses_403_with_delegated_token_retry_succeeds():
    """When 403 and delegated token available, auto-recover: re-grant permissions and retry."""
    call_count = 0

    async def fake_graph_get(token, url):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise M365Error("Microsoft Graph request failed (403)", http_status=403)
        # Retry succeeds
        return {"value": []}

    with (
        patch.object(
            m365_service,
            "acquire_access_token",
            AsyncMock(return_value="fake-token"),
        ),
        patch.object(m365_service, "_graph_get", side_effect=fake_graph_get),
        patch.object(
            m365_service,
            "acquire_delegated_token",
            AsyncMock(return_value="delegated-token"),
        ),
        patch.object(
            m365_service,
            "try_grant_missing_permissions",
            AsyncMock(return_value=True),
        ) as mock_grant,
        # Mock DB calls reached after successful SKU fetch
        patch("app.services.m365.license_repo.list_company_licenses", AsyncMock(return_value=[])),
    ):
        # Should NOT raise – retry succeeded
        await m365_service.sync_company_licenses(1)

    mock_grant.assert_awaited_once_with(1, access_token="delegated-token")
    assert call_count == 2, "Graph should have been called twice (first 403, then retry)"


@pytest.mark.anyio("asyncio")
async def test_sync_company_licenses_403_with_delegated_token_retry_also_403():
    """When retry also fails with 403, surface propagation-delay message."""
    with (
        patch.object(
            m365_service,
            "acquire_access_token",
            AsyncMock(return_value="fake-token"),
        ),
        patch.object(
            m365_service,
            "_graph_get",
            AsyncMock(side_effect=M365Error("Microsoft Graph request failed (403)", http_status=403)),
        ),
        patch.object(
            m365_service,
            "acquire_delegated_token",
            AsyncMock(return_value="delegated-token"),
        ),
        patch.object(
            m365_service,
            "try_grant_missing_permissions",
            AsyncMock(return_value=False),
        ),
    ):
        with pytest.raises(M365Error) as exc_info:
            await m365_service.sync_company_licenses(1)

    error_message = str(exc_info.value)
    assert "403" in error_message
    assert "propagation" in error_message.lower() or "wait" in error_message.lower()


@pytest.mark.anyio("asyncio")
async def test_sync_company_licenses_non_403_error_propagates_unchanged():
    """Non-403 errors from subscribedSkus propagate without modification."""
    original_message = "Microsoft Graph request failed (500)"
    with (
        patch.object(
            m365_service,
            "acquire_access_token",
            AsyncMock(return_value="fake-token"),
        ),
        patch.object(
            m365_service,
            "_graph_get",
            AsyncMock(side_effect=M365Error(original_message, http_status=500)),
        ),
    ):
        with pytest.raises(M365Error) as exc_info:
            await m365_service.sync_company_licenses(1)

    assert str(exc_info.value) == original_message


# ---------------------------------------------------------------------------
# sync_m365_data scheduler task – graceful 403 handling
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_sync_m365_data_continues_when_licenses_sync_fails():
    """If sync_company_licenses raises, the overall task still succeeds and records the error."""
    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()
    recorded_statuses: list[str] = []
    recorded_details: list[str] = []

    async def fake_record_run(task_id, *, status, started_at, finished_at, duration_ms, details=None):
        recorded_statuses.append(status)
        recorded_details.append(details or "")

    with (
        patch(
            "app.services.scheduler.m365_service.sync_company_licenses",
            AsyncMock(side_effect=M365Error(
                "License sync failed (403 Forbidden). Re-provision the enterprise app.",
                http_status=403,
            )),
        ),
        patch(
            "app.services.scheduler.staff_importer.import_m365_contacts_for_company",
            new_callable=AsyncMock,
            return_value=_make_staff_summary(),
        ),
        patch(
            "app.services.scheduler.m365_service.sync_mailboxes",
            new_callable=AsyncMock,
            return_value=0,
        ),
        patch("app.services.scheduler.scheduled_tasks_repo.record_task_run", side_effect=fake_record_run),
        patch("app.services.scheduler.db.acquire_lock") as mock_lock,
    ):
        mock_lock.return_value.__aenter__.return_value = True
        await scheduler._run_task(_make_task(company_id=1))

    assert recorded_statuses, "record_task_run should have been called"
    assert recorded_statuses[-1] == "succeeded", (
        f"Task should succeed even when sync_company_licenses fails; got {recorded_statuses[-1]!r}"
    )
    result = json.loads(recorded_details[-1])
    assert result.get("licenses_synced") is False
    assert "403" in str(result.get("licenses_sync_error") or "")


@pytest.mark.anyio("asyncio")
async def test_sync_m365_data_continues_when_staff_import_fails():
    """If import_m365_contacts_for_company raises, the overall task still succeeds."""
    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()
    recorded_statuses: list[str] = []
    recorded_details: list[str] = []

    async def fake_record_run(task_id, *, status, started_at, finished_at, duration_ms, details=None):
        recorded_statuses.append(status)
        recorded_details.append(details or "")

    with (
        patch("app.services.scheduler.m365_service.sync_company_licenses", new_callable=AsyncMock),
        patch(
            "app.services.scheduler.staff_importer.import_m365_contacts_for_company",
            AsyncMock(side_effect=Exception("Graph error during staff import")),
        ),
        patch(
            "app.services.scheduler.m365_service.sync_mailboxes",
            new_callable=AsyncMock,
            return_value=3,
        ),
        patch("app.services.scheduler.scheduled_tasks_repo.record_task_run", side_effect=fake_record_run),
        patch("app.services.scheduler.db.acquire_lock") as mock_lock,
    ):
        mock_lock.return_value.__aenter__.return_value = True
        await scheduler._run_task(_make_task(company_id=1))

    assert recorded_statuses[-1] == "succeeded", (
        f"Task should succeed even when staff import fails; got {recorded_statuses[-1]!r}"
    )
    result = json.loads(recorded_details[-1])
    assert result.get("licenses_synced") is True
    assert result.get("staff") is None
    assert "Graph error" in str(result.get("staff_sync_error") or "")
    assert result.get("mailboxes_synced") == 3


@pytest.mark.anyio("asyncio")
async def test_sync_m365_data_details_include_licenses_sync_error_field():
    """Task details always include licenses_sync_error (None on success)."""
    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()
    recorded_details: list[str] = []

    async def fake_record_run(task_id, *, status, started_at, finished_at, duration_ms, details=None):
        recorded_details.append(details or "")

    with (
        patch("app.services.scheduler.m365_service.sync_company_licenses", new_callable=AsyncMock),
        patch(
            "app.services.scheduler.staff_importer.import_m365_contacts_for_company",
            new_callable=AsyncMock,
            return_value=_make_staff_summary(),
        ),
        patch("app.services.scheduler.m365_service.sync_mailboxes", new_callable=AsyncMock, return_value=2),
        patch("app.services.scheduler.scheduled_tasks_repo.record_task_run", side_effect=fake_record_run),
        patch("app.services.scheduler.db.acquire_lock") as mock_lock,
    ):
        mock_lock.return_value.__aenter__.return_value = True
        await scheduler._run_task(_make_task(company_id=1))

    assert recorded_details, "record_task_run should have been called"
    result = json.loads(recorded_details[-1])
    assert result.get("licenses_synced") is True
    assert result.get("licenses_sync_error") is None
    assert result.get("staff_sync_error") is None
    assert result.get("mailboxes_synced") == 2
    assert result.get("mailbox_sync_error") is None
