from unittest.mock import AsyncMock

import pytest

from app.repositories import network_devices


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_create_port_check_replaces_older_checks(monkeypatch):
    execute = AsyncMock()
    insert = AsyncMock(return_value=55)
    monkeypatch.setattr(network_devices.db, "execute", execute)
    monkeypatch.setattr(network_devices.db, "execute_returning_lastrowid", insert)

    check_id = await network_devices.create_port_check(
        company_id=42,
        network_device_id=9,
        scanner_tray_device_id=3,
        ip_address="192.168.1.20",
        ports="22,8000-8002",
        requested_by_user_id=7,
    )

    assert check_id == 55
    delete_sql, delete_params = execute.await_args.args
    assert delete_sql.startswith("DELETE FROM network_device_port_checks")
    assert delete_params == (42, 9)
    insert_sql, insert_params = insert.await_args.args
    assert insert_sql.startswith("INSERT INTO network_device_port_checks")
    assert insert_params == (42, 9, 3, "192.168.1.20", "22,8000-8002", 7)


@pytest.mark.anyio
async def test_get_port_check_for_scanner_filters_on_scanner(monkeypatch):
    fetch_one = AsyncMock(return_value=None)
    monkeypatch.setattr(network_devices.db, "fetch_one", fetch_one)

    assert await network_devices.get_port_check_for_scanner(55, 3) is None

    sql, params = fetch_one.await_args.args
    assert "id=%s AND scanner_tray_device_id=%s" in sql
    assert params == (55, 3)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "status"), [(None, "completed"), ("IP is out of range", "failed")]
)
async def test_complete_port_check_sets_status(monkeypatch, error, status):
    execute = AsyncMock()
    monkeypatch.setattr(network_devices.db, "execute", execute)

    await network_devices.complete_port_check(
        55, open_ports="22", checked_count=4, error=error
    )

    sql, params = execute.await_args.args
    assert sql.startswith("UPDATE network_device_port_checks SET")
    assert params[0] == status
    assert params[1:4] == ("22", 4, error)
    assert params[-1] == 55


@pytest.mark.anyio
async def test_latest_port_checks_by_device_keys_on_device(monkeypatch):
    fetch_all = AsyncMock(
        return_value=[{"id": 1, "network_device_id": 9}, {"id": 2, "network_device_id": 10}]
    )
    monkeypatch.setattr(network_devices.db, "fetch_all", fetch_all)

    result = await network_devices.latest_port_checks_by_device(42)

    assert set(result) == {9, 10}
    assert fetch_all.await_args.args[1] == (42,)
