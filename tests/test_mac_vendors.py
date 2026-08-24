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
async def test_device_list_exposes_matching_type_recommendations(monkeypatch):
    fetch_all = AsyncMock(
        return_value=[{"id": 8, "recommended_device_type_ids": "2,5"}]
    )
    monkeypatch.setattr(network_devices.db, "fetch_all", fetch_all)

    devices = await network_devices.list_for_company(42)

    assert devices[0]["recommended_device_type_ids"] == {2, 5}
    query = fetch_all.await_args.args[0]
    assert "network_device_type_vendors" in query


@pytest.mark.anyio
async def test_auto_assignment_only_updates_untyped_matching_device(monkeypatch):
    execute = AsyncMock()
    monkeypatch.setattr(network_devices.db, "execute", execute)

    await network_devices._auto_assign_device_type(17)

    query, params = execute.await_args.args
    assert "nd.device_type_id IS NULL" in query
    assert "dt.auto_assign=1" in query
    assert "COALESCE(mv.vendor, nd.vendor)" in query
    assert params == (17,)


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
