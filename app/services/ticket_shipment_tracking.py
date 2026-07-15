from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import re
import socket
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.database import db
from app.core.logging import log_error, log_info
from app.repositories import ticket_shipment_watches as shipment_watch_repo
from app.repositories import tickets as tickets_repo
from app.services import modules as modules_service
from app.services import tickets as tickets_service


_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]


class TrackingEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    occurred_at: str | None = None
    status: str | None = None
    description: str | None = None
    location: str | None = None


class CanonicalShipmentSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str | None = None
    eta_date: str | None = None
    proof_of_delivery_date: str | None = None
    signatory: str | None = None
    items_in_transit: int = Field(default=0, ge=0)
    onboard_for_delivery: int = Field(default=0, ge=0)
    items_delivered: int = Field(default=0, ge=0)
    tracking_events: list[TrackingEvent] = Field(default_factory=list)


class TicketShipmentWatchPayload(BaseModel):
    tracking_url: str = Field(min_length=1, max_length=500)
    poll_interval_seconds: int = Field(default=900, ge=60, le=86_400)
    active: bool = True


class TicketShipmentWatchResponse(BaseModel):
    id: int | None = None
    ticket_id: int | None = None
    tracking_url: str | None = None
    provider: str | None = None
    consignment_id: str | None = None
    poll_interval_seconds: int = 900
    active: bool = False
    last_checked_at: datetime | None = None
    last_posted_update_at: datetime | None = None


class ProviderAdapter(ABC):
    slug: str

    @abstractmethod
    def can_handle(self, url: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def fetch(self, url: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def normalize(self, raw: Mapping[str, Any]) -> CanonicalShipmentSnapshot:
        raise NotImplementedError


class _ProviderRateLimiter:
    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_call: dict[str, float] = {}

    async def wait(self, provider: str, min_interval_seconds: float) -> None:
        key = provider.strip().lower()
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            previous = self._last_call.get(key)
            now = time.monotonic()
            if previous is not None:
                elapsed = now - previous
                if elapsed < min_interval_seconds:
                    await asyncio.sleep(min_interval_seconds - elapsed)
            self._last_call[key] = time.monotonic()


_rate_limiter = _ProviderRateLimiter()


def _safe_iso(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _snapshot_payload(snapshot: CanonicalShipmentSnapshot | Mapping[str, Any] | None) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    if isinstance(snapshot, CanonicalShipmentSnapshot):
        payload = snapshot.model_dump()
    elif isinstance(snapshot, Mapping):
        try:
            payload = CanonicalShipmentSnapshot.model_validate(snapshot).model_dump()
        except ValidationError:
            return None
    else:
        return None

    events = payload.get("tracking_events") or []
    payload["tracking_events"] = sorted(
        events,
        key=lambda event: str((event or {}).get("occurred_at") or ""),
    )
    return payload


def _snapshot_hash(snapshot: CanonicalShipmentSnapshot | Mapping[str, Any]) -> str:
    payload = _snapshot_payload(snapshot) or {}
    packed = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(packed.encode("utf-8")).hexdigest()


def _extract_consignment_id(url: str, fallback_text: str = "") -> str | None:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for key in ("id", "consignment", "consignmentId", "tracking_number", "tracking"):
        values = query.get(key)
        if values:
            candidate = str(values[-1]).strip()
            if candidate:
                return candidate[:128]

    for pattern in (
        r"/(?:track|tracking)/([A-Za-z0-9-]{6,})",
        r"\b(?:consignment|tracking(?:\s*number)?)[:#\s]+([A-Za-z0-9-]{6,})",
    ):
        match = re.search(pattern, f"{url}\n{fallback_text}", flags=re.IGNORECASE)
        if match:
            return str(match.group(1)).strip()[:128]
    return None


def _extract_visible_text(html_text: str, *, limit: int = 14_000) -> str:
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html_text)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _extract_json_object(raw_text: str) -> dict[str, Any] | None:
    text = str(raw_text or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    fence_start = text.find("```")
    if fence_start != -1:
        fence_end = text.find("```", fence_start + 3)
        if fence_end != -1:
            fenced = text[fence_start + 3 : fence_end].strip()
            if fenced.lower().startswith("json"):
                fenced = fenced[4:].strip()
            start = fenced.find("{")
            end = fenced.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    parsed = json.loads(fenced[start : end + 1])
                    return parsed if isinstance(parsed, dict) else None
                except json.JSONDecodeError:
                    pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass
    return None


def _validate_tracking_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Tracking URL must use http or https")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("Tracking URL must include a hostname")

    try:
        resolved = socket.getaddrinfo(hostname, None, flags=socket.AI_NUMERICSERV)
    except socket.gaierror as exc:
        raise ValueError(f"Unable to resolve hostname: {hostname}") from exc

    for _family, _type, _proto, _canon, sockaddr in resolved:
        ip_raw = sockaddr[0]
        try:
            ip_obj = ipaddress.ip_address(ip_raw)
        except ValueError:
            continue
        if any(ip_obj in blocked for blocked in _BLOCKED_NETWORKS):
            raise ValueError("Tracking URL resolves to a blocked/private network")

    return url.strip()


async def _fetch_with_retries(url: str, *, timeout_seconds: float = 15.0, retries: int = 3) -> str:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
                response = await client.get(url, headers={"User-Agent": "MyPortal/1.0 (ticket-shipment-watch)"})
            response.raise_for_status()
            return response.text
        except (httpx.HTTPError, asyncio.TimeoutError, OSError, RuntimeError) as exc:
            last_error = exc
            if attempt >= retries:
                break
            await asyncio.sleep(min(0.5 * attempt, 2.0))
    raise RuntimeError(f"Failed to fetch tracking URL after retries: {last_error}")


class StarTrackProviderAdapter(ProviderAdapter):
    slug = "startrack"

    def can_handle(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return "startrack" in host

    async def fetch(self, url: str) -> dict[str, Any]:
        html_text = await _fetch_with_retries(url)
        return {
            "url": url,
            "html": html_text,
            "text": _extract_visible_text(html_text),
            "consignment_id": _extract_consignment_id(url, html_text),
        }

    async def normalize(self, raw: Mapping[str, Any]) -> CanonicalShipmentSnapshot:
        raw_text = str(raw.get("text") or "")
        html_text = str(raw.get("html") or "")

        fallback_payload: dict[str, Any] = {
            "status": _extract_status_fallback(raw_text),
            "eta_date": _extract_date_after_label(raw_text, "ETA"),
            "proof_of_delivery_date": _extract_date_after_label(raw_text, "Proof of delivery"),
            "signatory": _extract_signatory(raw_text),
            "items_in_transit": _extract_count(raw_text, "in transit"),
            "onboard_for_delivery": _extract_count(raw_text, "onboard for delivery"),
            "items_delivered": _extract_count(raw_text, "delivered"),
            "tracking_events": _extract_events(raw_text),
        }

        llm_snapshot = await _extract_snapshot_with_llm(
            provider=self.slug,
            tracking_url=str(raw.get("url") or ""),
            consignment_id=str(raw.get("consignment_id") or ""),
            text_excerpt=raw_text,
            html_excerpt=html_text[:12_000],
        )

        if llm_snapshot is None:
            return CanonicalShipmentSnapshot.model_validate(fallback_payload)

        merged = {
            **fallback_payload,
            **{key: value for key, value in llm_snapshot.model_dump().items() if value not in (None, "")},
        }
        return CanonicalShipmentSnapshot.model_validate(merged)


def _extract_status_fallback(text: str) -> str | None:
    lowered = text.lower()
    if re.search(r"status\s*[:\-]?\s*in\s+transit", lowered):
        return "In transit"
    if re.search(r"status\s*[:\-]?\s*delivered", lowered):
        return "Delivered"
    if re.search(r"status\s*[:\-]?\s*onboard\s+for\s+delivery", lowered):
        return "Onboard for delivery"

    in_transit = _extract_count(text, "in transit")
    delivered = _extract_count(text, "delivered")
    onboard = _extract_count(text, "onboard for delivery")
    if in_transit > 0:
        return "In transit"
    if delivered > 0:
        return "Delivered"
    if onboard > 0:
        return "Onboard for delivery"

    if re.search(r"\bdelivered\b", lowered):
        return "Delivered"
    if "in transit" in lowered:
        return "In transit"
    if "onboard" in lowered and "delivery" in lowered:
        return "Onboard for delivery"
    return None


def _extract_date_after_label(text: str, label: str) -> str | None:
    pattern = rf"{re.escape(label)}[^\n\r:]*[:\s]+([0-9]{{1,2}}[\/-][0-9]{{1,2}}[\/-][0-9]{{2,4}}|[A-Za-z]{{3,9}}\s+[0-9]{{1,2}},?\s+[0-9]{{4}})"
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return _safe_iso(match.group(1) if match else None)


def _extract_signatory(text: str) -> str | None:
    match = re.search(r"(?:signed\s+for\s+by|signatory)[:\s]+([^\n\r]{2,80})", text, flags=re.IGNORECASE)
    return _safe_iso(match.group(1) if match else None)


def _extract_count(text: str, label: str) -> int:
    match = re.search(rf"(\d+)\s+{re.escape(label)}", text, flags=re.IGNORECASE)
    if not match:
        return 0
    try:
        return max(0, int(match.group(1)))
    except (TypeError, ValueError):
        return 0


def _extract_events(text: str) -> list[dict[str, str | None]]:
    events: list[dict[str, str | None]] = []
    pattern = re.compile(
        r"([0-9]{1,2}[\/-][0-9]{1,2}[\/-][0-9]{2,4}(?:\s+[0-9]{1,2}:[0-9]{2}(?:\s*[APMapm]{2})?)?)\s+[-–]\s+([^\n\r]+)",
        flags=re.IGNORECASE,
    )
    for match in pattern.finditer(text):
        events.append(
            {
                "occurred_at": _safe_iso(match.group(1)),
                "description": _safe_iso(match.group(2)),
                "status": None,
                "location": None,
            }
        )
    return events[:40]


PROVIDERS: tuple[ProviderAdapter, ...] = (
    StarTrackProviderAdapter(),
)


def detect_provider(url: str) -> ProviderAdapter | None:
    for provider in PROVIDERS:
        if provider.can_handle(url):
            return provider
    return None


async def detect_provider_slug(url: str) -> str | None:
    provider = detect_provider(url)
    return provider.slug if provider else None


def validate_tracking_url(url: str) -> str:
    return _validate_tracking_url(url)


async def _extract_snapshot_with_llm(
    *,
    provider: str,
    tracking_url: str,
    consignment_id: str,
    text_excerpt: str,
    html_excerpt: str,
) -> CanonicalShipmentSnapshot | None:
    prompt = (
        "Extract shipping-tracking details into strict JSON."
        " Return only a JSON object with these keys exactly:"
        " status, eta_date, proof_of_delivery_date, signatory,"
        " items_in_transit, onboard_for_delivery, items_delivered, tracking_events."
        " tracking_events must be an array of objects with keys: occurred_at, status, description, location."
        " Use null for unknown text fields and 0 for unknown counts."
        " Do not include extra keys.\n\n"
        f"Provider: {provider}\n"
        f"Tracking URL: {tracking_url}\n"
        f"Consignment ID: {consignment_id}\n"
        f"Visible text excerpt:\n{text_excerpt[:10000]}\n\n"
        f"HTML excerpt:\n{html_excerpt[:6000]}\n"
    )

    try:
        result = await modules_service.trigger_module(
            "ollama",
            {"prompt": prompt, "format": "json"},
            background=False,
        )
    except ValueError:
        return None
    except Exception as exc:  # pragma: no cover
        log_error("Shipment watch LLM extraction failed", provider=provider, error=str(exc))
        return None

    if str(result.get("status") or "") != "succeeded":
        return None

    response_payload = result.get("response")
    response_text = None
    if isinstance(response_payload, Mapping):
        response_text = response_payload.get("response") or response_payload.get("message")
    else:
        response_text = response_payload

    extracted = _extract_json_object(str(response_text or ""))
    if not extracted:
        return None

    try:
        return CanonicalShipmentSnapshot.model_validate(extracted)
    except ValidationError:
        return None


def _is_watch_due(watch: Mapping[str, Any], *, now_utc: datetime | None = None) -> bool:
    now = now_utc or datetime.now(timezone.utc)
    last_checked = watch.get("last_checked_at")
    interval_seconds = int(watch.get("poll_interval_seconds") or 900)
    if not isinstance(last_checked, datetime):
        return True
    if last_checked.tzinfo is None:
        last_checked = last_checked.replace(tzinfo=timezone.utc)
    return (now - last_checked).total_seconds() >= max(60, interval_seconds)


def _has_meaningful_change(
    previous_snapshot: Mapping[str, Any] | None,
    current_snapshot: Mapping[str, Any],
) -> bool:
    if not previous_snapshot:
        return True
    keys = (
        "status",
        "eta_date",
        "proof_of_delivery_date",
        "signatory",
        "items_in_transit",
        "onboard_for_delivery",
        "items_delivered",
        "tracking_events",
    )
    previous = {key: previous_snapshot.get(key) for key in keys}
    current = {key: current_snapshot.get(key) for key in keys}
    return previous != current


def _render_ticket_reply(snapshot: Mapping[str, Any], watch: Mapping[str, Any]) -> str:
    events = snapshot.get("tracking_events") or []
    event_lines: list[str] = []
    for event in events[:8]:
        if not isinstance(event, Mapping):
            continue
        occurred_at = str(event.get("occurred_at") or "").strip() or "Unknown time"
        description = str(event.get("description") or event.get("status") or "Update").strip()
        location = str(event.get("location") or "").strip()
        detail = f"- {occurred_at}: {description}"
        if location:
            detail = f"{detail} ({location})"
        event_lines.append(detail)

    provider = str(watch.get("provider") or "Unknown provider").strip()
    consignment = str(watch.get("consignment_id") or "").strip() or "Not available"

    lines = [
        "Shipment tracking update",
        "",
        f"- Provider: {provider}",
        f"- Consignment: {consignment}",
        f"- Status: {snapshot.get('status') or 'Unknown'}",
        f"- ETA: {snapshot.get('eta_date') or 'Not available'}",
        f"- POD date: {snapshot.get('proof_of_delivery_date') or 'Not available'}",
        f"- Signatory: {snapshot.get('signatory') or 'Not available'}",
        f"- Items in transit: {snapshot.get('items_in_transit') or 0}",
        f"- Onboard for delivery: {snapshot.get('onboard_for_delivery') or 0}",
        f"- Items delivered: {snapshot.get('items_delivered') or 0}",
    ]

    if event_lines:
        lines.append("")
        lines.append("Recent tracking events:")
        lines.extend(event_lines)

    tracking_url = str(watch.get("tracking_url") or "").strip()
    if tracking_url:
        lines.append("")
        lines.append(f"Tracking URL: {tracking_url}")

    return "\n".join(lines)


async def upsert_watch(
    *,
    ticket_id: int,
    tracking_url: str,
    poll_interval_seconds: int,
    active: bool,
) -> dict[str, Any]:
    clean_url = _validate_tracking_url(str(tracking_url).strip())
    provider = detect_provider(clean_url)
    if provider is None:
        raise ValueError("Unsupported tracking provider URL")
    consignment_id = _extract_consignment_id(clean_url)

    watch = await shipment_watch_repo.upsert_watch(
        ticket_id=ticket_id,
        tracking_url=clean_url,
        provider=provider.slug,
        consignment_id=consignment_id,
        poll_interval_seconds=max(60, int(poll_interval_seconds)),
        active=bool(active),
    )
    return watch


async def get_watch_for_ticket(ticket_id: int) -> dict[str, Any] | None:
    return await shipment_watch_repo.get_watch_by_ticket(ticket_id)


async def set_watch_active(ticket_id: int, active: bool) -> dict[str, Any] | None:
    watch = await shipment_watch_repo.get_watch_by_ticket(ticket_id)
    if not watch:
        return None
    return await shipment_watch_repo.upsert_watch(
        ticket_id=ticket_id,
        tracking_url=str(watch.get("tracking_url") or ""),
        provider=str(watch.get("provider") or ""),
        consignment_id=watch.get("consignment_id"),
        poll_interval_seconds=int(watch.get("poll_interval_seconds") or 900),
        active=active,
    )


async def process_due_shipment_watches(*, limit: int = 200) -> dict[str, int]:
    watches = await shipment_watch_repo.list_active_watches(limit=limit)
    now = datetime.now(timezone.utc)

    checked = 0
    changed = 0
    posted = 0
    skipped = 0
    errors = 0

    for watch in watches:
        if not _is_watch_due(watch, now_utc=now):
            skipped += 1
            continue

        watch_id = watch.get("id")
        ticket_id = watch.get("ticket_id")
        if not isinstance(watch_id, int) or not isinstance(ticket_id, int):
            errors += 1
            continue

        lock_name = f"ticket_shipment_watch_{watch_id}"
        async with db.acquire_lock(lock_name, timeout=1) as lock_acquired:
            if not lock_acquired:
                skipped += 1
                continue

            try:
                refreshed = await shipment_watch_repo.get_watch_by_id(watch_id)
                if not refreshed or not bool(refreshed.get("active")):
                    skipped += 1
                    continue
                if not _is_watch_due(refreshed, now_utc=now):
                    skipped += 1
                    continue
                checked += 1

                provider = detect_provider(str(refreshed.get("tracking_url") or ""))
                if provider is None:
                    raise ValueError("No provider adapter matches tracking URL")

                await _rate_limiter.wait(provider.slug, min_interval_seconds=0.75)
                raw_payload = await provider.fetch(str(refreshed.get("tracking_url") or ""))
                snapshot = await provider.normalize(raw_payload)
                snapshot_payload = _snapshot_payload(snapshot) or {}
                current_hash = _snapshot_hash(snapshot_payload)
                previous_snapshot = refreshed.get("last_snapshot")
                previous_hash = str(refreshed.get("last_snapshot_hash") or "").strip() or None
                changed_now = _has_meaningful_change(previous_snapshot, snapshot_payload)

                await shipment_watch_repo.update_watch_check_state(
                    watch_id,
                    last_checked_at=now,
                    last_snapshot_hash=current_hash,
                    last_snapshot_json=snapshot_payload,
                )

                first_success = previous_hash is None
                should_post = changed_now or first_success
                if not should_post:
                    continue

                reply_external_ref = f"shipment-watch:{provider.slug}:{current_hash[:32]}"
                reply_body = _render_ticket_reply(snapshot_payload, refreshed)
                reply = await tickets_repo.create_reply(
                    ticket_id=ticket_id,
                    author_id=None,
                    body=reply_body,
                    is_internal=False,
                    external_reference=reply_external_ref[:128],
                )
                await shipment_watch_repo.update_watch_check_state(
                    watch_id,
                    last_checked_at=now,
                    last_posted_update_at=now,
                )
                await tickets_service.emit_ticket_replied_event(
                    ticket_id,
                    actor_type="system",
                    reply=reply,
                )
                await tickets_service.emit_ticket_updated_event(
                    ticket_id,
                    actor_type="system",
                    reply=reply,
                )
                changed += 1
                posted += 1
            except Exception as exc:  # noqa: BLE001
                errors += 1
                log_error(
                    "Ticket shipment watch processing failed",
                    watch_id=watch_id,
                    ticket_id=ticket_id,
                    provider=watch.get("provider"),
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                await shipment_watch_repo.update_watch_check_state(
                    watch_id,
                    last_checked_at=now,
                )

    if checked or errors:
        log_info(
            "Ticket shipment watch cycle completed",
            checked=checked,
            changed=changed,
            posted=posted,
            skipped=skipped,
            errors=errors,
        )

    return {
        "checked": checked,
        "changed": changed,
        "posted": posted,
        "skipped": skipped,
        "errors": errors,
    }


__all__ = [
    "CanonicalShipmentSnapshot",
    "TicketShipmentWatchPayload",
    "TicketShipmentWatchResponse",
    "detect_provider_slug",
    "validate_tracking_url",
    "get_watch_for_ticket",
    "process_due_shipment_watches",
    "upsert_watch",
    "_has_meaningful_change",
    "_is_watch_due",
    "_render_ticket_reply",
    "_validate_tracking_url",
]
