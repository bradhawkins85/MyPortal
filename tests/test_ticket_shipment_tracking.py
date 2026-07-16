from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.services import ticket_shipment_tracking as svc


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_startrack_essential_fields_extracts_only_selected_filters():
    html = """
    <span id="__c1_lblConsignmentNumber">UDWZ50125918</span>
    <span id="__c1_lblStatus">Ready for Pickup</span>
    <span id="__c1_lblETADate">16/07/2026</span>
    <span id="__c1_lblProofOfDelivery">Should not be included</span>
    """

    text = svc._extract_startrack_essential_fields(html)

    assert "__c1_lblConsignmentNumber: UDWZ50125918" in text
    assert "__c1_lblStatus: Ready for Pickup" in text
    assert "__c1_lblETADate: 16/07/2026" in text
    assert "ProofOfDelivery" not in text


@pytest.mark.anyio
async def test_startrack_fetch_sends_only_selected_filter_results(monkeypatch):
    provider = svc.StarTrackProviderAdapter()
    html = """
    <span id="__c1_lblConsignmentNumber">UDWZ50125918</span>
    <span id="__c1_lblStatus">In Transit</span>
    <span id="__c1_lblETADate">16/07/2026</span>
    <section>Large confusing page content Delivered signed by Wrong Person</section>
    """

    async def fake_fetch(url):
        return html

    monkeypatch.setattr(svc, "_fetch_with_retries", fake_fetch)

    raw = await provider.fetch("https://msto.startrack.com.au/track-trace/?id=UDWZ50125918")

    assert raw["text"] == (
        "__c1_lblConsignmentNumber: UDWZ50125918\n"
        "__c1_lblStatus: In Transit\n"
        "__c1_lblETADate: 16/07/2026"
    )
    assert raw["html"] == raw["text"]
    assert "Wrong Person" not in raw["text"]


def test_detect_provider_slug_startrack():
    provider = svc.detect_provider("https://www.startrack.com.au/track/ABC123")
    assert provider is not None
    assert provider.slug == "startrack"


def test_detect_provider_slug_unknown():
    assert svc.detect_provider("https://example.com/track/ABC123") is None


def test_validate_tracking_url_blocks_private_hosts():
    with pytest.raises(ValueError):
        svc.validate_tracking_url("http://127.0.0.1/track")


def test_watch_due_behavior():
    now = datetime.now(timezone.utc)
    due_watch = {"last_checked_at": now - timedelta(minutes=30), "poll_interval_seconds": 300}
    not_due_watch = {"last_checked_at": now - timedelta(seconds=30), "poll_interval_seconds": 300}

    assert svc._is_watch_due(due_watch, now_utc=now) is True
    assert svc._is_watch_due(not_due_watch, now_utc=now) is False


def test_meaningful_change_logic():
    previous = {
        "status": "In transit",
        "eta_date": "2026-07-20",
        "proof_of_delivery_date": None,
        "signatory": None,
        "items_in_transit": 1,
        "onboard_for_delivery": 0,
        "items_delivered": 0,
        "tracking_events": [{"occurred_at": "2026-07-15", "description": "Picked up"}],
    }
    current_same = dict(previous)
    current_changed = {**previous, "status": "Delivered", "items_delivered": 1}

    assert svc._has_meaningful_change(previous, current_same) is False
    assert svc._has_meaningful_change(previous, current_changed) is True
    assert svc._has_meaningful_change(None, current_changed) is True


def test_reply_template_contains_required_fields():
    snapshot = {
        "status": "In transit",
        "eta_date": "2026-07-20",
        "proof_of_delivery_date": None,
        "signatory": "J Smith",
        "items_in_transit": 2,
        "onboard_for_delivery": 1,
        "items_delivered": 0,
        "tracking_events": [{"occurred_at": "2026-07-15 10:00", "description": "Processed", "location": "Sydney"}],
    }
    watch = {"provider": "startrack", "consignment_id": "ABC123", "tracking_url": "https://www.startrack.com.au/track/ABC123"}
    body = svc._render_ticket_reply(snapshot, watch)

    assert "ETA" in body
    assert "POD date" in body
    assert "Status" in body
    assert "Items in transit" in body
    assert "Onboard for delivery" in body
    assert "Items delivered" in body
    assert "Signatory" in body


@pytest.mark.anyio
async def test_startrack_normalize_from_variant_text(monkeypatch):
    provider = svc.StarTrackProviderAdapter()

    async def fake_llm(**kwargs):
        return None

    monkeypatch.setattr(svc, "_extract_snapshot_with_llm", fake_llm)

    raw = {
        "url": "https://www.startrack.com.au/track/ABC123",
        "html": "<html><body></body></html>",
        "text": (
            "Status In transit ETA: 20/07/2026 2 in transit 1 onboard for delivery 0 delivered\n"
            "15/07/2026 09:30 - Processed at facility"
        ),
    }

    snapshot = await provider.normalize(raw)
    payload = snapshot.model_dump()

    assert payload["status"] == "In transit"
    assert payload["items_in_transit"] == 2
    assert payload["onboard_for_delivery"] == 1
    assert payload["items_delivered"] == 0
    assert payload["tracking_events"]


@pytest.mark.anyio
async def test_startrack_normalize_from_second_variant(monkeypatch):
    provider = svc.StarTrackProviderAdapter()

    async def fake_llm(**kwargs):
        return None

    monkeypatch.setattr(svc, "_extract_snapshot_with_llm", fake_llm)

    raw = {
        "url": "https://www.startrack.com.au/track/ZXCV987",
        "html": "<html><body></body></html>",
        "text": (
            "Delivered signed for by Casey Proof of delivery: 21/07/2026 0 in transit 0 onboard for delivery 1 delivered\n"
            "21/07/2026 13:10 - Delivered"
        ),
    }

    snapshot = await provider.normalize(raw)
    payload = snapshot.model_dump()

    assert payload["status"] == "Delivered"
    assert "Casey" in (payload["signatory"] or "")
    assert payload["items_delivered"] == 1


@pytest.mark.anyio
async def test_startrack_normalize_prefers_fallback_when_llm_is_incorrect(monkeypatch):
    provider = svc.StarTrackProviderAdapter()

    async def fake_llm(**kwargs):
        return svc.CanonicalShipmentSnapshot.model_validate(
            {
                "status": "Delivered",
                "eta_date": "2026-07-22",
                "proof_of_delivery_date": "2026-07-15",
                "signatory": "Wrong Person",
                "items_in_transit": 0,
                "onboard_for_delivery": 0,
                "items_delivered": 1,
                "tracking_events": [
                    {
                        "occurred_at": "2026-07-15 09:00",
                        "status": "Delivered",
                        "description": "Delivered",
                        "location": "Brisbane",
                    }
                ],
            }
        )

    monkeypatch.setattr(svc, "_extract_snapshot_with_llm", fake_llm)

    raw = {
        "url": "https://msto.startrack.com.au/track-trace/?id=UDWZ50125918",
        "html": "<html><body></body></html>",
        "text": (
            "Consignment : UDWZ50125918 "
            "Created Ready for Pick-up Picked Up In Transit Delivered "
            "Consignment Summary Type Despatch Service FIXED PRICE PREMIUM(FPP) "
            "Despatch Depot BRISBANE Despatch Date 15/07/2026 Delivery Depot BRISBANE "
            "ETA Date 16/07/2026 Proof of Delivery Quality Control Status Ready for Pickup Scan "
            "Depot Scan Date & Time"
        ),
    }

    snapshot = await provider.normalize(raw)
    payload = snapshot.model_dump()

    assert payload["status"] == "Ready for Pickup"
    assert payload["eta_date"] == "16/07/2026"
    assert payload["proof_of_delivery_date"] is None
    assert payload["signatory"] is None
    assert payload["items_in_transit"] == 0
    assert payload["onboard_for_delivery"] == 0
    assert payload["items_delivered"] == 0
    assert payload["tracking_events"] == []


@pytest.mark.anyio
async def test_process_due_shipment_watches_skips_not_due(monkeypatch):
    now = datetime.now(timezone.utc)
    due_watch = {
        "id": 1,
        "ticket_id": 10,
        "provider": "startrack",
        "tracking_url": "https://www.startrack.com.au/track/ABC123",
        "poll_interval_seconds": 60,
        "last_checked_at": now - timedelta(minutes=5),
        "last_snapshot": None,
        "last_snapshot_hash": None,
        "active": True,
        "public_comments_enabled": True,
    }
    not_due_watch = {
        "id": 2,
        "ticket_id": 11,
        "provider": "startrack",
        "tracking_url": "https://www.startrack.com.au/track/DEF456",
        "poll_interval_seconds": 600,
        "last_checked_at": now,
        "last_snapshot": None,
        "last_snapshot_hash": None,
        "active": True,
        "public_comments_enabled": True,
    }

    async def fake_list(limit=200):
        return [due_watch, not_due_watch]

    async def fake_get(watch_id):
        return due_watch if watch_id == 1 else not_due_watch

    monkeypatch.setattr(svc.shipment_watch_repo, "list_active_watches", fake_list)
    monkeypatch.setattr(svc.shipment_watch_repo, "get_watch_by_id", fake_get)

    updates: list[dict[str, Any]] = []

    async def fake_update(watch_id, **kwargs):
        updates.append({"watch_id": watch_id, **kwargs})

    monkeypatch.setattr(svc.shipment_watch_repo, "update_watch_check_state", fake_update)

    provider = svc.StarTrackProviderAdapter()

    async def fake_fetch(url: str):
        return {"url": url, "html": "", "text": "In transit 1 in transit 0 onboard for delivery 0 delivered", "consignment_id": "ABC123"}

    async def fake_normalize(raw):
        return svc.CanonicalShipmentSnapshot(
            status="In transit",
            eta_date="2026-07-20",
            proof_of_delivery_date=None,
            signatory=None,
            items_in_transit=1,
            onboard_for_delivery=0,
            items_delivered=0,
            tracking_events=[],
        )

    monkeypatch.setattr(provider, "fetch", fake_fetch)
    monkeypatch.setattr(provider, "normalize", fake_normalize)
    monkeypatch.setattr(svc, "detect_provider", lambda url: provider)

    @asynccontextmanager
    async def fake_lock(name, timeout=1):
        yield True

    monkeypatch.setattr(svc.db, "acquire_lock", fake_lock)
    async def fake_create_reply(**kwargs):
        return {"id": 100, **kwargs}

    async def fake_emit(*args, **kwargs):
        return None

    monkeypatch.setattr(svc.tickets_repo, "create_reply", fake_create_reply)
    monkeypatch.setattr(svc.tickets_service, "emit_ticket_replied_event", fake_emit)
    monkeypatch.setattr(svc.tickets_service, "emit_ticket_updated_event", fake_emit)

    result = await svc.process_due_shipment_watches(limit=10)

    assert result["checked"] == 1
    assert result["posted"] == 1
    assert any(entry["watch_id"] == 1 for entry in updates)
    assert all(entry["watch_id"] != 2 for entry in updates)


@pytest.mark.anyio
async def test_process_due_shipment_watches_skips_public_reply_when_disabled(monkeypatch):
    now = datetime.now(timezone.utc)
    due_watch = {
        "id": 1,
        "ticket_id": 10,
        "provider": "startrack",
        "tracking_url": "https://www.startrack.com.au/track/ABC123",
        "poll_interval_seconds": 60,
        "last_checked_at": now - timedelta(minutes=5),
        "last_snapshot": None,
        "last_snapshot_hash": None,
        "active": True,
        "public_comments_enabled": False,
    }

    async def fake_list(limit=200):
        return [due_watch]

    async def fake_get(watch_id):
        return due_watch

    updates: list[dict[str, Any]] = []

    async def fake_update(watch_id, **kwargs):
        updates.append({"watch_id": watch_id, **kwargs})

    monkeypatch.setattr(svc.shipment_watch_repo, "list_active_watches", fake_list)
    monkeypatch.setattr(svc.shipment_watch_repo, "get_watch_by_id", fake_get)
    monkeypatch.setattr(svc.shipment_watch_repo, "update_watch_check_state", fake_update)

    provider = svc.StarTrackProviderAdapter()

    async def fake_fetch(url: str):
        return {"url": url, "html": "", "text": "In transit 1 in transit 0 onboard for delivery 0 delivered", "consignment_id": "ABC123"}

    async def fake_normalize(raw):
        return svc.CanonicalShipmentSnapshot(
            status="In transit",
            eta_date="2026-07-20",
            proof_of_delivery_date=None,
            signatory=None,
            items_in_transit=1,
            onboard_for_delivery=0,
            items_delivered=0,
            tracking_events=[],
        )

    monkeypatch.setattr(provider, "fetch", fake_fetch)
    monkeypatch.setattr(provider, "normalize", fake_normalize)
    monkeypatch.setattr(svc, "detect_provider", lambda url: provider)

    @asynccontextmanager
    async def fake_lock(name, timeout=1):
        yield True

    monkeypatch.setattr(svc.db, "acquire_lock", fake_lock)
    create_reply_mock = AsyncMock(return_value={"id": 100})
    emit_replied_mock = AsyncMock(return_value=None)
    emit_updated_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(svc.tickets_repo, "create_reply", create_reply_mock)
    monkeypatch.setattr(svc.tickets_service, "emit_ticket_replied_event", emit_replied_mock)
    monkeypatch.setattr(svc.tickets_service, "emit_ticket_updated_event", emit_updated_mock)

    result = await svc.process_due_shipment_watches(limit=10)

    assert result["checked"] == 1
    assert result["changed"] == 1
    assert result["posted"] == 0
    create_reply_mock.assert_not_awaited()
    emit_replied_mock.assert_not_awaited()
    emit_updated_mock.assert_not_awaited()
    assert any(entry["watch_id"] == 1 for entry in updates)


@pytest.mark.anyio
async def test_llm_extraction_handles_trigger_errors(monkeypatch):
    async def fake_trigger(*args, **kwargs):
        raise RuntimeError("network error")

    monkeypatch.setattr(svc.modules_service, "trigger_module", fake_trigger)
    snapshot = await svc._extract_snapshot_with_llm(
        provider="startrack",
        tracking_url="https://www.startrack.com.au/track/ABC123",
        consignment_id="ABC123",
        text_excerpt="in transit",
        html_excerpt="<html></html>",
    )
    assert snapshot is None
