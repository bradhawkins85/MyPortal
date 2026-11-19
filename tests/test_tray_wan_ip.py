import asyncio
from types import SimpleNamespace

from starlette.requests import Request

from app.api.routes import tray


def test_wan_ip_endpoint_returns_configured_agent_source(monkeypatch):
    monkeypatch.setattr(
        tray,
        "_settings",
        SimpleNamespace(
            wan_ip_source_url="https://whoami.example.test/",
            wan_ip_source_field="Cf-Connecting-Ip",
        ),
    )
    request = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1)})

    response = asyncio.run(tray.get_scanner_wan_ip(request, {}))

    assert response == {
        "source_url": "https://whoami.example.test/",
        "source_field": "Cf-Connecting-Ip",
    }


def test_wan_ip_endpoint_keeps_legacy_fallback(monkeypatch):
    monkeypatch.setattr(
        tray,
        "_settings",
        SimpleNamespace(
            wan_ip_source_url=None,
            wan_ip_source_field="X-Forwarded-For",
        ),
    )
    request = Request(
        {"type": "http", "headers": [], "client": ("203.0.113.42", 12345)}
    )

    assert asyncio.run(tray.get_scanner_wan_ip(request, {})) == {
        "wan_ip": "203.0.113.42"
    }


# --- Tray heartbeat WAN IPv4/IPv6 -------------------------------------------

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.schemas.tray import TrayHeartbeatRequest


def _heartbeat(monkeypatch, payload, *, client=("127.0.0.1", 1), asset_id=42):
    update_heartbeat = AsyncMock()
    record = AsyncMock()
    monkeypatch.setattr(tray.tray_repo, "update_device_heartbeat", update_heartbeat)
    monkeypatch.setattr(tray.assets_repo, "update_asset_wan_ips", record)
    device = {"id": 7, "asset_id": asset_id, "setup_ticket_processed": 1}
    request = Request({"type": "http", "headers": [], "client": client})
    response = asyncio.run(tray.heartbeat(payload, request, device))
    assert response.status_code == 200
    return record


def test_heartbeat_stores_both_reported_wan_addresses(monkeypatch):
    record = _heartbeat(
        monkeypatch,
        TrayHeartbeatRequest(wan_ipv4="203.0.113.5", wan_ipv6="2001:DB8::5"),
    )
    record.assert_awaited_once_with(
        42,
        wan_ipv4="203.0.113.5",
        wan_ipv6="2001:db8::5",
        wan_city=None,
        wan_country=None,
        wan_region_code=None,
    )


def test_heartbeat_clears_family_the_agent_could_not_resolve(monkeypatch):
    record = _heartbeat(monkeypatch, TrayHeartbeatRequest(wan_ipv4="203.0.113.5"))
    record.assert_awaited_once_with(
        42,
        wan_ipv4="203.0.113.5",
        wan_ipv6=None,
        wan_city=None,
        wan_country=None,
        wan_region_code=None,
    )


def test_heartbeat_stores_whoami_cloudflare_location(monkeypatch):
    record = _heartbeat(
        monkeypatch,
        TrayHeartbeatRequest(
            wan_ipv4="203.0.113.5",
            wan_city=" Brisbane\x07 ",
            wan_country="AU",
            wan_region_code="QLD" * 10,
        ),
    )
    record.assert_awaited_once_with(
        42,
        wan_ipv4="203.0.113.5",
        wan_ipv6=None,
        wan_city="Brisbane",
        wan_country="AU",
        wan_region_code=("QLD" * 10)[:16],
    )


def test_heartbeat_rejects_wrong_family_and_junk(monkeypatch):
    record = _heartbeat(
        monkeypatch,
        TrayHeartbeatRequest(wan_ipv4="2001:db8::5", wan_ipv6="not-an-ip"),
        client=("10.0.0.2", 1),
    )
    record.assert_not_awaited()


def test_heartbeat_falls_back_to_observed_public_address(monkeypatch):
    record = _heartbeat(monkeypatch, TrayHeartbeatRequest(), client=("8.8.8.8", 1))
    record.assert_awaited_once_with(
        42, wan_ipv4="8.8.8.8", wan_ipv6=None, replace_both=False
    )


def test_heartbeat_ignores_private_observed_address(monkeypatch):
    record = _heartbeat(monkeypatch, TrayHeartbeatRequest(), client=("192.168.1.20", 1))
    record.assert_not_awaited()


def test_heartbeat_survives_wan_storage_failure(monkeypatch):
    monkeypatch.setattr(tray.tray_repo, "update_device_heartbeat", AsyncMock())
    monkeypatch.setattr(
        tray.assets_repo,
        "update_asset_wan_ips",
        AsyncMock(side_effect=RuntimeError("column missing")),
    )
    request = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1)})
    payload = TrayHeartbeatRequest(wan_ipv4="203.0.113.5")
    device = {"id": 7, "asset_id": 42, "setup_ticket_processed": 1}
    assert asyncio.run(tray.heartbeat(payload, request, device)).status_code == 200


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("whoami.mydomain.com", "https://whoami.mydomain.com/"),
        ("http://whoami.lan:8080", "http://whoami.lan:8080/"),
        ("", None),
    ],
)
def test_wan_ip_source_url_accepts_bare_host(monkeypatch, raw, expected):
    monkeypatch.setenv("WAN_IP_SOURCE_URL", raw)
    settings = Settings()
    assert (str(settings.wan_ip_source_url) if settings.wan_ip_source_url else None) == expected


def test_asset_wan_ip_migration_is_idempotent():
    sql = (Path(__file__).parent.parent / "migrations" / "566_asset_wan_ips.sql").read_text()
    assert "-- phase: expand" in sql
    for column in (
        "wan_ipv4", "wan_ipv6", "wan_ip_updated_at", "wan_city", "wan_country", "wan_region_code"
    ):
        assert f"ADD COLUMN IF NOT EXISTS {column}" in sql


def test_asset_table_offers_wan_columns():
    from app.features.assets import routes as asset_routes

    keys = [column["key"] for column in asset_routes._ASSET_TABLE_COLUMNS]
    assert {"wan_ipv4", "wan_ipv6", "wan_city", "wan_region_code", "wan_country"} <= set(keys)
