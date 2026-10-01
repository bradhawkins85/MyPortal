from unittest.mock import AsyncMock

import pytest

from app.repositories import network_devices


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_bulk_update_is_noop_when_no_fields_requested(monkeypatch):
    execute = AsyncMock()
    monkeypatch.setattr(network_devices.db, "execute", execute)

    await network_devices.bulk_update_devices([1, 2], 42)

    execute.assert_not_awaited()


@pytest.mark.anyio
async def test_bulk_update_executes_parameterized_update_for_device_batch(monkeypatch):
    execute = AsyncMock()
    monkeypatch.setattr(network_devices.db, "execute", execute)

    await network_devices.bulk_update_devices(
        [11, "12"],
        42,
        state="Known",
        device_type_id=7,
        description="managed",
        update_description=True,
        agent_not_required=True,
    )

    execute.assert_awaited_once()
    sql = execute.await_args.args[0]
    assert sql.startswith("UPDATE network_devices SET ")
    assert "WHERE company_id=%s AND id IN (%s,%s)" in sql
    assert execute.await_args.args[1] == ("Known", 7, "managed", 1, 42, 11, 12)


@pytest.mark.anyio
async def test_bulk_update_rejects_non_numeric_device_ids(monkeypatch):
    execute = AsyncMock()
    monkeypatch.setattr(network_devices.db, "execute", execute)

    with pytest.raises(ValueError):
        await network_devices.bulk_update_devices(["11 OR 1=1"], 42, state="Known")

    execute.assert_not_awaited()
