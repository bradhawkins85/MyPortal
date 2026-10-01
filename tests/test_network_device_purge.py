from unittest.mock import AsyncMock

import pytest

from app.repositories import network_devices


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_purge_checks_each_device_against_its_scanner_scope(monkeypatch):
    rows = [
        {
            "id": 1,
            "wan_ip": "203.0.113.8",
            "ip_address": "192.168.1.20",
            "network_scan_wan_cidrs": "203.0.113.0/24",
            "network_scan_local_cidrs": "192.168.1.0/24",
        },
        {
            "id": 2,
            "wan_ip": "198.51.100.8",
            "ip_address": "192.168.2.20",
            "network_scan_wan_cidrs": "203.0.113.0/24",
            "network_scan_local_cidrs": "192.168.1.0/24",
        },
        {
            "id": 3,
            "wan_ip": "198.51.100.8",
            "ip_address": "10.0.0.2",
            "network_scan_wan_cidrs": "",
            "network_scan_local_cidrs": "",
        },
        {
            "id": 4,
            "wan_ip": "203.0.113.9",
            "ip_address": "10.0.0.2",
            "network_scan_wan_cidrs": "203.0.113.0/24",
            "network_scan_local_cidrs": "",
        },
    ]
    monkeypatch.setattr(network_devices.db, "fetch_all", AsyncMock(return_value=rows))
    execute = AsyncMock()
    monkeypatch.setattr(network_devices.db, "execute", execute)

    count = await network_devices.purge_out_of_scope(42)

    assert count == 1
    execute.assert_awaited_once()
    assert execute.await_args.args[0].startswith("DELETE FROM network_devices")
    assert execute.await_args.args[1] == (42, 2)


@pytest.mark.anyio
async def test_purge_is_noop_when_no_cidrs_are_supplied(monkeypatch):
    monkeypatch.setattr(
        network_devices.db,
        "fetch_all",
        AsyncMock(
            return_value=[
                {
                    "id": 1,
                    "wan_ip": None,
                    "ip_address": "not-an-ip",
                    "network_scan_wan_cidrs": None,
                    "network_scan_local_cidrs": "",
                }
            ]
        ),
    )
    execute = AsyncMock()
    monkeypatch.setattr(network_devices.db, "execute", execute)

    assert await network_devices.purge_out_of_scope(42) == 0
    execute.assert_not_awaited()
