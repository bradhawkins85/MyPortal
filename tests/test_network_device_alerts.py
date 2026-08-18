from unittest.mock import AsyncMock

import pytest

from app.api.routes import tray as tray_routes
from app.schemas.tray import NetworkScanHost, NetworkScanRequest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _payload() -> NetworkScanRequest:
    return NetworkScanRequest(
        wan_ip="203.0.113.10",
        subnets=["192.168.50.0/24"],
        hosts=[
            NetworkScanHost(
                ip_address="192.168.50.20",
                mac_address="AA:BB:CC:DD:EE:FF",
                hostname="reception-printer",
                vendor="Example Vendor",
            )
        ],
    )


@pytest.mark.anyio
async def test_first_subnet_scan_never_creates_alert_ticket(monkeypatch):
    monkeypatch.setattr(
        tray_routes.network_devices_repo,
        "register_scanned_subnets",
        AsyncMock(return_value={"192.168.50.0/24"}),
    )
    monkeypatch.setattr(
        tray_routes.network_devices_repo,
        "upsert_scan",
        AsyncMock(
            return_value=[
                {
                    "id": 91,
                    "ip_address": "192.168.50.20",
                    "mac_address": "AA:BB:CC:DD:EE:FF",
                    "hostname": "reception-printer",
                    "vendor": "Example Vendor",
                    "matched_asset_id": None,
                }
            ]
        ),
    )
    monkeypatch.setattr(
        tray_routes.companies_repo,
        "get_company_by_id",
        AsyncMock(return_value={"network_device_ticket_alerts_enabled": 1}),
    )
    create_ticket = AsyncMock()
    monkeypatch.setattr(tray_routes.tickets_service, "create_ticket", create_ticket)

    response = await tray_routes.upload_network_scan(
        _payload(),
        {"id": 7, "company_id": 42, "network_scanner_enabled": 1},
    )

    assert response == {"accepted": 1}
    create_ticket.assert_not_awaited()


@pytest.mark.anyio
async def test_later_scan_creates_ticket_for_new_unmatched_device(monkeypatch):
    monkeypatch.setattr(
        tray_routes.network_devices_repo,
        "register_scanned_subnets",
        AsyncMock(return_value=set()),
    )
    monkeypatch.setattr(
        tray_routes.network_devices_repo,
        "upsert_scan",
        AsyncMock(
            return_value=[
                {
                    "id": 92,
                    "ip_address": "192.168.50.20",
                    "mac_address": "AA:BB:CC:DD:EE:FF",
                    "hostname": "reception-printer",
                    "vendor": "Example Vendor",
                    "matched_asset_id": None,
                }
            ]
        ),
    )
    monkeypatch.setattr(
        tray_routes.companies_repo,
        "get_company_by_id",
        AsyncMock(return_value={"network_device_ticket_alerts_enabled": 1}),
    )
    create_ticket = AsyncMock()
    monkeypatch.setattr(tray_routes.tickets_service, "create_ticket", create_ticket)

    await tray_routes.upload_network_scan(
        _payload(),
        {"id": 7, "company_id": 42, "network_scanner_enabled": 1},
    )

    create_ticket.assert_awaited_once()
    assert create_ticket.await_args.kwargs["company_id"] == 42
    assert create_ticket.await_args.kwargs["external_reference"] == "network-device:92"
