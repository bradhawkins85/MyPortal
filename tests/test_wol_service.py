"""Unit tests for the Wake-on-LAN service orchestration.

The repository and tray-service layers are mocked so the tests exercise the
selection, dispatch and outcome-tracking logic without a live database.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import wol as wol_service


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _online_device(device_id: int, uid: str, ip: str) -> dict:
    return {
        "id": device_id,
        "device_uid": uid,
        "hostname": f"host-{device_id}",
        "asset_id": None,
        "last_ip": ip,
        "last_seen_utc": _now() - timedelta(seconds=10),  # within heartbeat window
        "ws_last_seen_utc": None,
    }


def _patch_repos(monkeypatch, *, active=None, scanners=None, matched_mac=None, subnet=None):
    """Monkeypatch every repository/service symbol the WoL service touches."""

    wol_repo = SimpleNamespace(
        list_active_devices=AsyncMock(return_value=active or []),
        list_subnet_scanner_devices=AsyncMock(return_value=scanners or []),
        get_matched_device_mac=AsyncMock(return_value=matched_mac),
        get_target_subnet_cidr=AsyncMock(return_value=subnet),
        create_attempt=AsyncMock(return_value=1234),
        mark_attempt_finished=AsyncMock(),
        mark_attempt_sent=AsyncMock(),
        list_pending_attempts=AsyncMock(return_value=[]),
        mark_attempt_awake=AsyncMock(),
        record_command_error=AsyncMock(),
    )
    tray_repo = SimpleNamespace(
        list_active_devices_by_asset_ids=AsyncMock(return_value={}),
        get_device_by_id=AsyncMock(return_value=None),
        log_command=AsyncMock(return_value=999),
        mark_command_delivered=AsyncMock(),
    )
    assets_repo = SimpleNamespace(
        get_asset_by_id=AsyncMock(
            return_value={"company_id": 7, "mac_address": "AA:BB:CC:DD:EE:FF"}
        )
    )
    send_to_device = AsyncMock(return_value=True)
    monkeypatch.setattr(wol_service, "wol_repo", wol_repo)
    monkeypatch.setattr(wol_service, "tray_repo", tray_repo)
    monkeypatch.setattr(wol_service, "assets_repo", assets_repo)
    monkeypatch.setattr(wol_service.tray_service, "send_to_device", send_to_device)
    return SimpleNamespace(
        wol_repo=wol_repo, tray_repo=tray_repo, assets_repo=assets_repo, send=send_to_device
    )


def test_parse_mac_list_extracts_canonical_macs():
    assert wol_service._parse_mac_list("aa-bb-cc-dd-ee-ff") == ["AA:BB:CC:DD:EE:FF"]
    assert wol_service._parse_mac_list("11:22:33:44:55:66, 77:88:99:aa:bb:cc") == [
        "11:22:33:44:55:66",
        "77:88:99:AA:BB:CC",
    ]
    assert wol_service._parse_mac_list("") == []
    assert wol_service._parse_mac_list(None) == []


def test_ip_in_subnet():
    assert wol_service._ip_in_subnet("10.0.0.5", "10.0.0.0/24")
    assert not wol_service._ip_in_subnet("192.168.1.5", "10.0.0.0/24")
    assert not wol_service._ip_in_subnet(None, "10.0.0.0/24")
    assert not wol_service._ip_in_subnet("10.0.0.5", "not-a-cidr")


@pytest.mark.anyio
async def test_find_candidates_filters_off_network_and_offline(monkeypatch):
    m = _patch_repos(monkeypatch)
    active = [_online_device(1, "uid-1", "10.0.0.10"), _online_device(2, "uid-2", "192.168.9.9")]
    stale = _online_device(3, "uid-3", "10.0.0.11")
    stale["last_seen_utc"] = _now() - timedelta(hours=5)  # offline
    m.wol_repo.list_active_devices.return_value = [active[0], active[1], stale]

    result = await wol_service._find_candidate_devices(
        company_id=7, exclude_device_id=99, subnet="10.0.0.0/24"
    )
    assert [d["id"] for d in result] == [1]


@pytest.mark.anyio
async def test_find_candidates_includes_scanners_on_subnet(monkeypatch):
    m = _patch_repos(monkeypatch)
    scanner = _online_device(50, "uid-scan", "10.0.0.254")
    m.wol_repo.list_subnet_scanner_devices.return_value = [scanner]
    m.wol_repo.list_active_devices.return_value = []

    result = await wol_service._find_candidate_devices(
        company_id=7, exclude_device_id=None, subnet="10.0.0.0/24"
    )
    assert [d["id"] for d in result] == [50]


@pytest.mark.anyio
async def test_resolve_target_from_asset(monkeypatch):
    m = _patch_repos(monkeypatch, subnet="10.0.0.0/24")
    m.tray_repo.list_active_devices_by_asset_ids.return_value = {
        42: _online_device(42, "uid-42", "10.0.0.5")
    }
    target = await wol_service._resolve_target(asset_id=42)
    assert target["company_id"] == 7
    assert target["asset_id"] == 42
    assert target["macs"] == ["AA:BB:CC:DD:EE:FF"]
    assert target["subnet"] == "10.0.0.0/24"


@pytest.mark.anyio
async def test_wake_no_mac(monkeypatch):
    m = _patch_repos(monkeypatch, subnet="10.0.0.0/24")
    m.assets_repo.get_asset_by_id.return_value = {"company_id": 7, "mac_address": None}
    m.wol_repo.get_matched_device_mac.return_value = None
    m.tray_repo.list_active_devices_by_asset_ids.return_value = {}

    result = await wol_service.wake_asset(42)
    assert result["status"] == "no_mac"
    m.wol_repo.create_attempt.assert_not_awaited()


@pytest.mark.anyio
async def test_wake_no_candidates(monkeypatch):
    m = _patch_repos(monkeypatch, subnet="10.0.0.0/24")
    m.tray_repo.list_active_devices_by_asset_ids.return_value = {}
    m.wol_repo.list_active_devices.return_value = []
    m.wol_repo.list_subnet_scanner_devices.return_value = []

    result = await wol_service.wake_asset(42)
    assert result["status"] == "no_candidates"
    m.wol_repo.mark_attempt_finished.assert_awaited_once()
    assert m.wol_repo.mark_attempt_finished.await_args.kwargs["status_value"] == "no_candidates"


@pytest.mark.anyio
async def test_wake_dispatched(monkeypatch):
    m = _patch_repos(monkeypatch, subnet="10.0.0.0/24")
    m.tray_repo.list_active_devices_by_asset_ids.return_value = {}
    m.wol_repo.list_active_devices.return_value = [_online_device(1, "uid-1", "10.0.0.10")]
    m.wol_repo.list_subnet_scanner_devices.return_value = []

    result = await wol_service.wake_asset(42)
    assert result["status"] == "dispatched"
    assert result["attempt_id"] == 1234
    assert result["delivered_count"] == 1
    assert result["delivered_device_ids"] == [1]
    m.send.assert_awaited()
    payload = m.send.await_args.args[1]
    assert payload["type"] == "wol"
    assert payload["attempt_id"] == 1234
    assert payload["target_mac"] == "AA:BB:CC:DD:EE:FF"
    m.wol_repo.mark_attempt_sent.assert_awaited_with(1234, 1)


@pytest.mark.anyio
async def test_wake_dispatch_uses_scanner_candidate(monkeypatch):
    m = _patch_repos(monkeypatch, subnet="10.0.0.0/24")
    m.tray_repo.list_active_devices_by_asset_ids.return_value = {}
    m.wol_repo.list_active_devices.return_value = []
    m.wol_repo.list_subnet_scanner_devices.return_value = [
        _online_device(50, "uid-scan", "10.0.0.254")
    ]

    result = await wol_service.wake_asset(42)
    assert result["status"] == "dispatched"
    assert result["delivered_device_ids"] == [50]


@pytest.mark.anyio
async def test_wake_send_failure_still_returns_dispatched(monkeypatch):
    m = _patch_repos(monkeypatch, subnet="10.0.0.0/24")
    m.tray_repo.list_active_devices_by_asset_ids.return_value = {}
    m.wol_repo.list_active_devices.return_value = [_online_device(1, "uid-1", "10.0.0.10")]
    m.wol_repo.list_subnet_scanner_devices.return_value = []
    m.send.return_value = False  # device not connected

    result = await wol_service.wake_asset(42)
    assert result["status"] == "dispatched"
    assert result["delivered_count"] == 0
    m.wol_repo.mark_attempt_sent.assert_not_awaited()


@pytest.mark.anyio
async def test_record_agent_sent_marks_and_records_error(monkeypatch):
    m = _patch_repos(monkeypatch)
    await wol_service.record_agent_sent(1234, 1, "boom")
    m.wol_repo.mark_attempt_sent.assert_awaited_with(1234, 1)
    m.wol_repo.record_command_error.assert_awaited_with(1234, "boom")

    await wol_service.record_agent_sent(1234, 1, None)
    # The error is only recorded when a non-None error is supplied.
    assert m.wol_repo.record_command_error.await_count == 1


@pytest.mark.anyio
async def test_check_pending_attempts_resolves_awake(monkeypatch):
    m = _patch_repos(monkeypatch)
    attempt = {
        "id": 55,
        "status": "awaiting",
        "check_deadline": _now() - timedelta(seconds=1),  # past
        "created_at": _now() - timedelta(seconds=600),
        "target_device_id": 42,
        "asset_id": 42,
    }
    m.wol_repo.list_pending_attempts.return_value = [attempt]
    m.tray_repo.get_device_by_id.return_value = _online_device(42, "uid-42", "10.0.0.5")

    resolved = await wol_service.check_pending_attempts()
    assert resolved == 1
    m.wol_repo.mark_attempt_awake.assert_awaited_once()
    m.wol_repo.mark_attempt_finished.assert_not_awaited()


@pytest.mark.anyio
async def test_check_pending_attempts_resolves_timeout(monkeypatch):
    m = _patch_repos(monkeypatch)
    attempt = {
        "id": 66,
        "status": "pending",
        "check_deadline": _now() - timedelta(seconds=1),  # past
        "created_at": _now() - timedelta(seconds=600),
        "target_device_id": None,
        "asset_id": None,
    }
    m.wol_repo.list_pending_attempts.return_value = [attempt]

    resolved = await wol_service.check_pending_attempts()
    assert resolved == 1
    m.wol_repo.mark_attempt_finished.assert_awaited_once()
    assert m.wol_repo.mark_attempt_finished.await_args.kwargs["status_value"] == "timeout"
    m.wol_repo.mark_attempt_awake.assert_not_awaited()


@pytest.mark.anyio
async def test_check_pending_attempts_skips_before_deadline(monkeypatch):
    m = _patch_repos(monkeypatch)
    attempt = {
        "id": 77,
        "status": "awaiting",
        "check_deadline": _now() + timedelta(minutes=5),  # future
        "created_at": _now(),
        "target_device_id": None,
        "asset_id": None,
    }
    m.wol_repo.list_pending_attempts.return_value = [attempt]

    resolved = await wol_service.check_pending_attempts()
    assert resolved == 0
    m.wol_repo.mark_attempt_awake.assert_not_awaited()
    m.wol_repo.mark_attempt_finished.assert_not_awaited()


