from unittest.mock import AsyncMock

import asyncio

from app.repositories import infrastructure


def test_network_match_uses_most_specific_ipv4_and_ipv6_subnets():
    networks = [
        {"id": 1, "cidr": "10.0.0.0/8"},
        {"id": 2, "cidr": "10.2.0.0/16"},
        {"id": 3, "cidr": "2001:db8::/32"},
        {"id": 4, "cidr": "2001:db8:42::/48"},
    ]
    assert infrastructure.match_address_network("10.2.4.5", networks)[0]["id"] == 2
    assert infrastructure.match_address_network("2001:db8:42::9", networks)[0]["id"] == 4


def test_network_match_reports_invalid_missing_and_ambiguous_addresses():
    assert infrastructure.match_address_network("not-an-ip", [])[1] == "Invalid IP address"
    assert "No company network" in infrastructure.match_address_network("192.0.2.1", [])[1]
    duplicate_best = [
        {"id": 1, "cidr": "192.0.2.0/24"},
        {"id": 2, "cidr": "192.0.2.0/24"},
    ]
    assert "equally specific" in infrastructure.match_address_network("192.0.2.3", duplicate_best)[1]


def test_preview_defaults_to_device_ip_and_requires_explicit_wan(monkeypatch):
    fetch = AsyncMock(return_value=[{"id": 7, "name": "LAN", "cidr": "10.0.0.0/24"},
                                        {"id": 8, "name": "WAN", "cidr": "203.0.113.0/24"}])
    monkeypatch.setattr(infrastructure.db, "fetch_all", fetch)
    device = {"id": 4, "ip_address": "10.0.0.5", "wan_ip": "203.0.113.2", "matched_asset_id": None}
    local = asyncio.run(infrastructure.preview_discovered_addresses(3, [device]))
    wan = asyncio.run(infrastructure.preview_discovered_addresses(3, [device], use_wan=True))
    assert (local[0]["candidate"], local[0]["network"]["id"]) == ("10.0.0.5", 7)
    assert (wan[0]["candidate"], wan[0]["network"]["id"]) == ("203.0.113.2", 8)


def test_import_is_idempotent_and_refuses_another_source(monkeypatch):
    preview = AsyncMock(return_value=[{
        "device": {"id": 4}, "candidate": "10.0.0.5", "network": {"id": 7},
        "reason": None, "source_url": "/devices#discovered-device-4", "asset_url": "/assets/9",
    }])
    monkeypatch.setattr(infrastructure, "preview_discovered_addresses", preview)
    monkeypatch.setattr(infrastructure.db, "fetch_one", AsyncMock(return_value={
        "id": 12, "asset_id": 9, "source_network_device_id": 4, "notes": None, "dns_count": 0,
    }))
    insert = AsyncMock()
    monkeypatch.setattr(infrastructure.db, "execute_returning_lastrowid", insert)
    result = asyncio.run(infrastructure.import_discovered_address(
        3, {"id": 4, "matched_asset_id": 9, "ip_address": "10.0.0.5"}
    ))
    assert result["status"] == "updated"
    insert.assert_not_awaited()

    infrastructure.db.fetch_one.return_value["source_network_device_id"] = 5
    result = asyncio.run(infrastructure.import_discovered_address(
        3, {"id": 4, "matched_asset_id": 9, "ip_address": "10.0.0.5"}
    ))
    assert result["status"] == "conflict"
