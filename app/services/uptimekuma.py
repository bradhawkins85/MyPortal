from __future__ import annotations

import hashlib
import hmac
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from loguru import logger

from app.repositories import service_status as service_status_repo
from app.repositories import uptimekuma_alerts as alerts_repo
from app.schemas.uptimekuma import UptimeKumaAlertPayload, UptimeKumaTag
from app.services import modules as modules_service


class ModuleDisabledError(RuntimeError):
    """Raised when the Uptime Kuma integration module is disabled."""


class AuthenticationError(RuntimeError):
    """Raised when the provided webhook token is invalid."""


# Maps Uptime Kuma alert statuses and Apprise notification types to
# service_status_services status values.
_ALERT_STATUS_TO_SERVICE_STATUS: dict[str, str] = {
    # Standard Uptime Kuma webhook statuses
    "up": "operational",
    "down": "outage",
    "pending": "degraded",
    "maintenance": "maintenance",
    # Apprise notification types
    "success": "operational",
    "failure": "outage",
    "warning": "degraded",
    "info": "maintenance",
}

# Pattern to strip status prefix from Uptime Kuma Apprise titles such as
# "[UP] My Service" or "[DOWN] My Service".
_TITLE_PREFIX_RE = re.compile(
    r"^\s*\[(?:UP|DOWN|PENDING|MAINTENANCE)[^\]]*\]\s*",
    re.IGNORECASE,
)

# Pattern to extract service name and status from UptimeKuma message bodies
# formatted as "[Service Name] [<optional emoji> Status] optional details",
# e.g. "[ESDS Website] [✅ Up] 200 - OK" or "[ESDS Website] [🔴 Down] Error".
_MESSAGE_SERVICE_STATUS_RE = re.compile(
    r"^\s*\[([^\]]+)\]\s*\[[^\]]*?(UP|DOWN|PENDING|MAINTENANCE)[^\]]*\]",
    re.IGNORECASE,
)

# The name of the Uptime Kuma tag whose value identifies the corresponding
# MyPortal service.  Tags with this name take priority over the monitor name
# when resolving which service to update.
_MYPORTAL_SERVICE_TAG_NAME = "MyPortal Service"


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


def _normalise_status(value: str | None) -> str:
    return (value or "unknown").strip().lower()


def _resolve_status(payload: UptimeKumaAlertPayload) -> str:
    """Return the best available status string from a payload.

    Prefers the explicit ``status`` field.  When absent (e.g. Apprise-only
    payloads from Uptime Kuma) the Apprise ``alert_type`` field (``type``) is
    used as a fallback so that ``success``/``failure``/``warning``/``info``
    values are preserved for downstream service-status mapping.  As a last
    resort the ``message`` body is parsed for an embedded status keyword such
    as ``[✅ Up]`` or ``[🔴 Down]``.
    """
    if payload.status:
        return _normalise_status(payload.status)
    if payload.alert_type:
        logger.debug(
            "No 'status' field in payload; falling back to alert_type",
            alert_type=payload.alert_type,
        )
        return _normalise_status(payload.alert_type)
    if payload.message:
        _, status = _parse_message_for_name_and_status(payload.message)
        if status:
            logger.debug(
                "No 'status' or 'alert_type' field; extracted status from message",
                status=status,
            )
            return _normalise_status(status)
    logger.warning("Payload contained neither 'status' nor 'alert_type'; defaulting to 'unknown'")
    return "unknown"


def _extract_monitor_name_from_title(title: str) -> str | None:
    """Extract the monitor name from an Uptime Kuma Apprise notification title.

    Uptime Kuma formats Apprise titles as ``[UP] Monitor Name`` or
    ``[DOWN] Monitor Name``.  This function strips any leading status
    prefix so only the bare name is returned.
    """
    stripped = _TITLE_PREFIX_RE.sub("", title).strip()
    return stripped or None


def _parse_message_for_name_and_status(message: str) -> tuple[str | None, str | None]:
    """Extract monitor name and status from a UptimeKuma message body.

    Handles the format ``[Service Name] [<optional emoji> Status] optional details``,
    for example::

        "[ESDS Website] [✅ Up] 200 - OK"
        "[ESDS Website] [🔴 Down] Some Error Message"

    Returns a ``(name, status)`` tuple.  Either element may be ``None`` when
    the message does not match the expected pattern.
    """
    m = _MESSAGE_SERVICE_STATUS_RE.match(message)
    if not m:
        return None, None
    name = m.group(1).strip() or None
    status = m.group(2).strip().lower() or None
    return name, status


def _resolve_monitor_name(payload: UptimeKumaAlertPayload) -> str | None:
    """Return the best available monitor name from a payload.

    Prefers the explicit ``monitor_name`` field.  Falls back to extracting
    the name from the Apprise ``title`` field, and finally attempts to
    parse the ``message`` body when both of the above are absent.
    """
    if payload.monitor_name:
        return payload.monitor_name.strip() or None
    if payload.title:
        return _extract_monitor_name_from_title(payload.title)
    if payload.message:
        name, _ = _parse_message_for_name_and_status(payload.message)
        return name
    return None


def _resolve_myportal_service_name_from_tags(tags: list[UptimeKumaTag] | None) -> str | None:
    """Return the MyPortal service name from Uptime Kuma tags.

    Searches the tags list for a tag whose ``name`` matches
    ``_MYPORTAL_SERVICE_TAG_NAME`` (case-insensitive) and returns its
    ``value``.  Returns ``None`` when no matching tag is found or the
    matched tag has no value.
    """
    if not tags:
        return None
    target = _MYPORTAL_SERVICE_TAG_NAME.lower()
    for tag in tags:
        tag_name = tag.name
        if tag_name and tag_name.strip().lower() == target:
            tag_value = tag.value
            return tag_value.strip() if tag_value else None
    return None


def _map_alert_status_to_service_status(alert_status: str, alert_type: str | None) -> str | None:
    """Map an Uptime Kuma alert status (or Apprise type) to a service status value.

    Returns ``None`` when no mapping can be determined.
    """
    normalised = (alert_status or "").strip().lower()
    service_status = _ALERT_STATUS_TO_SERVICE_STATUS.get(normalised)
    if service_status:
        return service_status
    if alert_type:
        normalised_type = alert_type.strip().lower()
        return _ALERT_STATUS_TO_SERVICE_STATUS.get(normalised_type)
    return None


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


async def _sync_service_status_from_alert(
    service_name: str,
    alert_status: str,
    alert_type: str | None,
    alert_message: str | None,
) -> bool:
    """Find a matching service by name and update its status.

    Returns ``True`` when a service was found and its status updated,
    ``False`` otherwise.
    """
    service_status = _map_alert_status_to_service_status(alert_status, alert_type)
    if not service_status:
        logger.debug(
            "No service status mapping for alert status",
            alert_status=alert_status,
            alert_type=alert_type,
        )
        return False

    service = await service_status_repo.find_service_by_name(service_name)
    if not service:
        logger.debug(
            "No matching service found for Uptime Kuma monitor",
            service_name=service_name,
        )
        return False

    service_id = service.get("id")
    updates: dict[str, Any] = {
        "status": service_status,
        "status_message": alert_message or None,
    }
    await service_status_repo.update_service(service_id, updates)
    logger.info(
        "Updated service status from Uptime Kuma alert",
        service_id=service_id,
        service_name=service.get("name"),
        old_status=service.get("status"),
        new_status=service_status,
    )
    return True


async def ingest_alert(
    *,
    payload: UptimeKumaAlertPayload,
    raw_payload: Mapping[str, Any],
    provided_secret: str | None,
    remote_addr: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    logger.debug(
        "Received Uptime Kuma alert",
        remote_addr=remote_addr,
        user_agent=user_agent,
        status=payload.status,
        alert_type=payload.alert_type,
        monitor_name=payload.monitor_name,
        title=payload.title,
        raw_keys=list(raw_payload.keys()) if raw_payload else [],
    )
    settings = await _load_module_configuration()
    secret_hash = str(settings.get("shared_secret_hash") or "").strip()
    if not _verify_secret(provided_secret, secret_hash):
        raise AuthenticationError("Invalid or missing webhook token")

    occurred_at = _coerce_datetime(payload.time)
    duration_seconds = _coerce_float(payload.duration)
    ping_ms = _coerce_float(payload.ping)

    monitor_name = _resolve_monitor_name(payload)
    resolved_status = _resolve_status(payload)

    # Determine which service name to use for the MyPortal service status sync.
    # Tags take priority: look for a tag named "MyPortal Service" and use its
    # value.  Fall back to the resolved monitor name when no such tag is present.
    service_name = _resolve_myportal_service_name_from_tags(payload.tags) or monitor_name

    record = await alerts_repo.create_alert(
        event_uuid=_choose_event_identifier(payload),
        monitor_id=payload.monitor_id,
        monitor_name=monitor_name,
        monitor_url=payload.monitor_url.strip() if payload.monitor_url else None,
        monitor_type=payload.monitor_type.strip() if payload.monitor_type else None,
        monitor_hostname=payload.monitor_hostname.strip() if payload.monitor_hostname else None,
        monitor_port=_coerce_port(payload.monitor_port),
        status=resolved_status,
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

    sync_enabled = _coerce_bool(settings.get("sync_service_status", True))
    service_status_updated = False
    if sync_enabled and service_name:
        try:
            service_status_updated = await _sync_service_status_from_alert(
                service_name=service_name,
                alert_status=resolved_status,
                alert_type=payload.alert_type,
                alert_message=payload.message.strip() if payload.message else None,
            )
        except Exception:
            logger.exception(
                "Failed to sync service status from Uptime Kuma alert",
                monitor_name=service_name,
            )

    record["service_status_updated"] = service_status_updated
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
