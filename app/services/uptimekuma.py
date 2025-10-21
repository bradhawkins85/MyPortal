from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Any, Mapping

from loguru import logger

from app.repositories import uptimekuma_alerts as alerts_repo
from app.schemas.uptimekuma import UptimeKumaAlertPayload
from app.services import modules as modules_service


class ModuleDisabledError(RuntimeError):
    """Raised when the Uptime Kuma integration module is disabled."""


class AuthenticationError(RuntimeError):
    """Raised when the provided webhook token is invalid."""


def _hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _verify_secret(token: str | None, expected_hash: str) -> bool:
    if not expected_hash:
        return True
    if not token:
        return False
    candidate = _hash_secret(token.strip())
    return hmac.compare_digest(candidate.lower(), expected_hash.lower())


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        return lowered in {"1", "true", "yes", "on"}
    return False


def _coerce_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return None


def _coerce_port(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _coerce_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        for parser in (datetime.fromisoformat,):
            try:
                dt = parser(text)
                break
            except ValueError:
                dt = None
        else:
            dt = None
        if dt is None:
            try:
                dt = datetime.fromtimestamp(float(text), tz=timezone.utc)
            except ValueError:
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    else:
        return None
    return dt.astimezone(timezone.utc)


def _choose_event_identifier(payload: UptimeKumaAlertPayload) -> str | None:
    candidates = [payload.uuid, payload.incident_id]
    extra = getattr(payload, "model_extra", {}) or {}
    for key in ("uuid", "incidentID", "incidentId", "id", "incident_id"):
        value = extra.get(key)
        if value:
            candidates.append(value)
    for candidate in candidates:
        if candidate:
            text = str(candidate).strip()
            if text:
                return text
    return None


def _normalise_status(value: str) -> str:
    return (value or "unknown").strip().lower()


async def _load_module_configuration() -> dict[str, Any]:
    module = await modules_service.get_module("uptimekuma", redact=False)
    if not module:
        logger.warning("Uptime Kuma module not found during alert ingestion")
        raise ModuleDisabledError("Uptime Kuma module is not configured")
    if not module.get("enabled"):
        raise ModuleDisabledError("Uptime Kuma module is disabled")
    settings = module.get("settings") or {}
    if not isinstance(settings, Mapping):
        settings = {}
    return dict(settings)


async def ingest_alert(
    *,
    payload: UptimeKumaAlertPayload,
    raw_payload: Mapping[str, Any],
    provided_secret: str | None,
    remote_addr: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    settings = await _load_module_configuration()
    secret_hash = str(settings.get("shared_secret_hash") or "").strip()
    if not _verify_secret(provided_secret, secret_hash):
        raise AuthenticationError("Invalid or missing webhook token")

    occurred_at = _coerce_datetime(payload.time)
    duration_seconds = _coerce_float(payload.duration)
    ping_ms = _coerce_float(payload.ping)

    record = await alerts_repo.create_alert(
        event_uuid=_choose_event_identifier(payload),
        monitor_id=payload.monitor_id,
        monitor_name=payload.monitor_name.strip() if payload.monitor_name else None,
        monitor_url=payload.monitor_url.strip() if payload.monitor_url else None,
        monitor_type=payload.monitor_type.strip() if payload.monitor_type else None,
        monitor_hostname=payload.monitor_hostname.strip() if payload.monitor_hostname else None,
        monitor_port=_coerce_port(payload.monitor_port),
        status=_normalise_status(payload.status),
        previous_status=_normalise_status(payload.previous_status) if payload.previous_status else None,
        importance=_coerce_bool(payload.importance),
        alert_type=payload.alert_type.strip() if payload.alert_type else None,
        reason=payload.reason.strip() if payload.reason else None,
        message=payload.message.strip() if payload.message else None,
        duration_seconds=duration_seconds,
        ping_ms=ping_ms,
        occurred_at=occurred_at,
        remote_addr=remote_addr,
        user_agent=user_agent,
        payload=raw_payload,
    )
    return record


async def list_alerts(
    *,
    status: str | None = None,
    monitor_id: int | None = None,
    importance: bool | None = None,
    search: str | None = None,
    sort_by: str = "received_at",
    sort_direction: str = "desc",
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    return await alerts_repo.list_alerts(
        status=status,
        monitor_id=monitor_id,
        importance=importance,
        search=search,
        sort_by=sort_by,
        sort_direction=sort_direction,
        limit=limit,
        offset=offset,
    )


async def get_alert(alert_id: int) -> dict[str, Any] | None:
    return await alerts_repo.get_alert(alert_id)
