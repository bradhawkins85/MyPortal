from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.routes import tray as tray_routes
from app.schemas.tray import PortCheckResultRequest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


DEVICE = {"id": 3, "company_id": 42}
CHECK = {"id": 55, "command_id": 77, "ports": "22,443,8000-8002", "status": "sent"}


@pytest.fixture
def repo(monkeypatch):
    mocks = {
        "get": AsyncMock(return_value=dict(CHECK)),
        "complete": AsyncMock(),
        "mark": AsyncMock(),
    }
    monkeypatch.setattr(
        tray_routes.network_devices_repo, "get_port_check_for_scanner", mocks["get"]
    )
    monkeypatch.setattr(tray_routes.network_devices_repo, "complete_port_check", mocks["complete"])
    monkeypatch.setattr(tray_routes.tray_repo, "mark_command_completed", mocks["mark"])
    return mocks


@pytest.mark.anyio
async def test_stores_open_ports_reported_by_scanner(repo):
    payload = PortCheckResultRequest(command_id=77, open_ports=[443, 22], checked_count=5)

    assert await tray_routes.report_port_check(55, payload, DEVICE) == {"status": "ok"}

    repo["get"].assert_awaited_once_with(55, 3)
    repo["complete"].assert_awaited_once_with(
        55, open_ports="22,443", checked_count=5, error=None
    )
    repo["mark"].assert_awaited_once_with(77, error=None)


@pytest.mark.anyio
async def test_records_scanner_failure(repo):
    payload = PortCheckResultRequest(command_id=77, error="Outside this scanner's ranges")

    await tray_routes.report_port_check(55, payload, DEVICE)

    repo["complete"].assert_awaited_once_with(
        55, open_ports="", checked_count=0, error="Outside this scanner's ranges"
    )
    repo["mark"].assert_awaited_once_with(77, error="Outside this scanner's ranges")


@pytest.mark.anyio
async def test_other_scanner_or_replaced_check_is_not_found(repo):
    repo["get"].return_value = None

    with pytest.raises(HTTPException) as exc_info:
        await tray_routes.report_port_check(
            55, PortCheckResultRequest(command_id=77), DEVICE
        )

    assert exc_info.value.status_code == 404
    repo["complete"].assert_not_awaited()


@pytest.mark.anyio
async def test_stale_command_id_is_not_found(repo):
    with pytest.raises(HTTPException) as exc_info:
        await tray_routes.report_port_check(
            55, PortCheckResultRequest(command_id=76), DEVICE
        )

    assert exc_info.value.status_code == 404
    repo["complete"].assert_not_awaited()


@pytest.mark.anyio
async def test_unrequested_open_port_is_rejected(repo):
    with pytest.raises(HTTPException) as exc_info:
        await tray_routes.report_port_check(
            55, PortCheckResultRequest(command_id=77, open_ports=[3306]), DEVICE
        )

    assert exc_info.value.status_code == 422
    repo["complete"].assert_not_awaited()


def test_payload_limits():
    with pytest.raises(ValidationError):
        PortCheckResultRequest(command_id=77, open_ports=list(range(1, 1026)))
    with pytest.raises(ValidationError):
        PortCheckResultRequest(command_id=77, error="x" * 501)
