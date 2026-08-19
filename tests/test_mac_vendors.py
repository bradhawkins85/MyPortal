from unittest.mock import AsyncMock

import pytest

from app.repositories import network_devices
from app.services import mac_vendors
from app.services.scheduler import SchedulerService


def test_parse_ieee_oui_csv_normalizes_and_deduplicates_assignments():
    content = """Registry,Assignment,Organization Name
MA-L,AA-BB-CC, Example Inc.
MA-L,AABBCC,Example Updated
MA-L,invalid,Ignored
"""
    assert mac_vendors.parse_ieee_oui_csv(content) == [
        ("AABBCC", "Example Updated")
    ]


@pytest.mark.anyio
async def test_device_list_looks_up_vendor_when_table_is_loaded(monkeypatch):
    fetch_all = AsyncMock(return_value=[])
    monkeypatch.setattr(network_devices.db, "fetch_all", fetch_all)
    await network_devices.list_for_company(42)
    query, params = fetch_all.await_args.args
    assert "LEFT JOIN mac_vendors" in query
    assert "COALESCE(mv.vendor, nd.vendor) AS mac_vendor" in query
    assert params == (42,)


@pytest.mark.anyio
async def test_scheduler_dispatches_mac_vendor_update(monkeypatch):
    scheduler = SchedulerService()
    update = AsyncMock(return_value={"imported": 123})
    record = AsyncMock()
    monkeypatch.setattr(mac_vendors, "update_mac_vendors", update)
    monkeypatch.setattr(
        "app.services.scheduler.scheduled_tasks_repo.record_task_run", record
    )
    monkeypatch.setattr(
        "app.services.scheduler.scheduled_tasks_repo.has_run_since",
        AsyncMock(return_value=False),
    )

    class Lock:
        async def __aenter__(self):
            return True

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(
        "app.services.scheduler.db.acquire_lock", lambda *_args, **_kwargs: Lock()
    )
    await scheduler._run_task({"id": 9, "command": "update_mac_vendors"})
    update.assert_awaited_once_with()
    assert record.await_args.kwargs["status"] == "succeeded"
    assert '"imported": 123' in record.await_args.kwargs["details"]

