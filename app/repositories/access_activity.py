from __future__ import annotations

import ipaddress
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from urllib.parse import urlsplit

from app.core.database import db
from app.core.logging import log_warning


def _to_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
                try:
                    return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
    return None


def _name_or_email(row: Mapping[str, Any]) -> str:
    first = str(row.get("first_name") or "").strip()
    last = str(row.get("last_name") or "").strip()
    name = " ".join(part for part in (first, last) if part).strip()
    if name:
        return name
    email = str(row.get("email") or "").strip()
    return email or "Unknown"


def _parse_json_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, Mapping):
            return parsed
    return {}


def _normalise_ip(value: Any) -> str | None:
    text = str(value or "").strip().strip('"')
    if not text:
        return None
    if text.lower().startswith("for="):
        text = text[4:].strip().strip('"')
    if text.startswith("[") and "]" in text:
        text = text[1 : text.index("]")]
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        pass
    if text.count(":") == 1:
        host, port = text.rsplit(":", 1)
        if port.isdigit():
            try:
                return str(ipaddress.ip_address(host))
            except ValueError:
                return None
    return None


def _extract_ip_from_headers(headers: Mapping[str, Any]) -> str | None:
    for key in (
        "cf-connecting-ip",
        "true-client-ip",
        "x-forwarded-for",
        "forwarded",
        "x-real-ip",
        "x-client-ip",
    ):
        raw = next((value for header, value in headers.items() if str(header).lower() == key), None)
        if raw is None:
            continue
        if key in {"x-forwarded-for", "forwarded"}:
            for part in str(raw).split(","):
                candidate = _normalise_ip(part)
                if candidate:
                    return candidate
            continue
        candidate = _normalise_ip(raw)
        if candidate:
            return candidate
    return None


def _extract_ip_from_webhook_event(row: Mapping[str, Any]) -> str | None:
    metadata = _parse_json_mapping(row.get("metadata"))
    stored_ip = _normalise_ip(metadata.get("source_ip"))
    if stored_ip:
        return stored_ip
    headers = _parse_json_mapping(row.get("headers"))
    from_headers = _extract_ip_from_headers(headers)
    if from_headers:
        return from_headers
    source_url = str(row.get("source_url") or row.get("target_url") or "").strip()
    if not source_url:
        return None
    try:
        host = urlsplit(source_url).hostname
    except ValueError:
        host = None
    return _normalise_ip(host)


async def _safe_fetch_all(query: str, params: tuple[Any, ...], *, source: str) -> list[dict[str, Any]]:
    try:
        rows = await db.fetch_all(query, params)
    except Exception as exc:  # pragma: no cover - defensive for optional/legacy tables
        log_warning("Failed to load access activity source", source=source, error=str(exc))
        return []
    return [dict(row) for row in rows]


async def list_active_user_sessions(*, limit: int = 200) -> list[dict[str, Any]]:
    capped_limit = max(1, min(limit, 1000))
    now = datetime.utcnow()
    rows = await _safe_fetch_all(
        """
        SELECT
            s.id,
            s.user_id,
            s.created_at,
            s.last_seen_at,
            s.expires_at,
            s.ip_address,
            s.user_agent,
            u.email,
            u.first_name,
            u.last_name,
            c.name AS company_name
        FROM user_sessions AS s
        INNER JOIN users AS u ON u.id = s.user_id
        LEFT JOIN companies AS c ON c.id = u.company_id
        WHERE s.is_active = 1
          AND s.expires_at > %s
        ORDER BY s.last_seen_at DESC, s.id DESC
        LIMIT %s
        """,
        (now, capped_limit),
        source="user_sessions",
    )
    sessions: list[dict[str, Any]] = []
    for row in rows:
        sessions.append(
            {
                **row,
                "display_name": _name_or_email(row),
                "created_at": _to_utc(row.get("created_at")),
                "last_seen_at": _to_utc(row.get("last_seen_at")),
                "expires_at": _to_utc(row.get("expires_at")),
            }
        )
    return sessions


async def list_recent_connection_activity(
    *,
    limit: int = 400,
    lookback: timedelta = timedelta(days=7),
) -> list[dict[str, Any]]:
    capped_limit = max(1, min(limit, 2000))
    source_limit = max(50, min(capped_limit, 400))
    cutoff = datetime.utcnow() - lookback
    records: list[dict[str, Any]] = []

    user_rows = await _safe_fetch_all(
        """
        SELECT
            s.id,
            s.last_seen_at,
            s.ip_address,
            s.user_agent,
            u.email,
            u.first_name,
            u.last_name,
            c.name AS company_name
        FROM user_sessions AS s
        INNER JOIN users AS u ON u.id = s.user_id
        LEFT JOIN companies AS c ON c.id = u.company_id
        WHERE s.last_seen_at >= %s
        ORDER BY s.last_seen_at DESC, s.id DESC
        LIMIT %s
        """,
        (cutoff, source_limit),
        source="user_sessions_recent",
    )
    for row in user_rows:
        activity_at = _to_utc(row.get("last_seen_at"))
        if not activity_at:
            continue
        records.append(
            {
                "access_method": "Web session",
                "identity": _name_or_email(row),
                "source": "Portal UI",
                "source_ip": row.get("ip_address"),
                "details": row.get("company_name") or "No company",
                "activity_at": activity_at,
            }
        )

    api_rows = await _safe_fetch_all(
        """
        SELECT
            ak.id AS api_key_id,
            ak.description,
            aku.ip_address,
            aku.usage_count,
            aku.last_used_at
        FROM api_key_usage AS aku
        INNER JOIN api_keys AS ak ON ak.id = aku.api_key_id
        WHERE aku.last_used_at >= %s
        ORDER BY aku.last_used_at DESC, ak.id DESC
        LIMIT %s
        """,
        (cutoff, source_limit),
        source="api_key_usage",
    )
    for row in api_rows:
        activity_at = _to_utc(row.get("last_used_at"))
        if not activity_at:
            continue
        description = str(row.get("description") or "").strip()
        api_key_id = row.get("api_key_id")
        records.append(
            {
                "access_method": "API key",
                "identity": description or f"API key #{api_key_id}",
                "source": "REST API",
                "source_ip": row.get("ip_address"),
                "details": f"Requests from this IP: {int(row.get('usage_count') or 0)}",
                "activity_at": activity_at,
            }
        )

    webhook_rows = await _safe_fetch_all(
        """
        SELECT
            id,
            name,
            source_url,
            target_url,
            headers,
            metadata,
            status,
            COALESCE(updated_at, created_at) AS activity_at
        FROM webhook_events
        WHERE direction = 'incoming'
          AND COALESCE(updated_at, created_at) >= %s
        ORDER BY COALESCE(updated_at, created_at) DESC, id DESC
        LIMIT %s
        """,
        (cutoff, source_limit),
        source="incoming_webhooks",
    )
    for row in webhook_rows:
        activity_at = _to_utc(row.get("activity_at"))
        if not activity_at:
            continue
        source_url = str(row.get("source_url") or row.get("target_url") or "").strip()
        records.append(
            {
                "access_method": "Webhook",
                "identity": str(row.get("name") or "Incoming webhook"),
                "source": source_url or "Inbound endpoint",
                "source_ip": _extract_ip_from_webhook_event(row),
                "details": f"Status: {row.get('status') or 'unknown'}",
                "activity_at": activity_at,
            }
        )

    call_rows = await _safe_fetch_all(
        """
        SELECT
            id,
            event_name,
            remote_number,
            source_ip,
            user_agent,
            received_at
        FROM phone_call_events
        WHERE received_at >= %s
        ORDER BY received_at DESC, id DESC
        LIMIT %s
        """,
        (cutoff, source_limit),
        source="phone_call_events",
    )
    for row in call_rows:
        activity_at = _to_utc(row.get("received_at"))
        if not activity_at:
            continue
        remote = str(row.get("remote_number") or "").strip()
        details = f"Remote number: {remote}" if remote else "Phone ActionURL webhook"
        records.append(
            {
                "access_method": "Phone webhook",
                "identity": str(row.get("event_name") or "Call event"),
                "source": "Calls module",
                "source_ip": row.get("source_ip"),
                "details": details,
                "activity_at": activity_at,
            }
        )

    tray_rows = await _safe_fetch_all(
        """
        SELECT
            td.id,
            td.hostname,
            td.device_uid,
            td.console_user,
            td.last_ip,
            td.last_seen_utc,
            td.agent_version,
            c.name AS company_name
        FROM tray_devices AS td
        LEFT JOIN companies AS c ON c.id = td.company_id
        WHERE td.status = 'active'
          AND td.last_seen_utc IS NOT NULL
          AND td.last_seen_utc >= %s
        ORDER BY td.last_seen_utc DESC, td.id DESC
        LIMIT %s
        """,
        (cutoff, source_limit),
        source="tray_devices",
    )
    for row in tray_rows:
        activity_at = _to_utc(row.get("last_seen_utc"))
        if not activity_at:
            continue
        hostname = str(row.get("hostname") or "").strip()
        device_uid = str(row.get("device_uid") or "").strip()
        identity = hostname or device_uid or f"Tray device #{row.get('id')}"
        details_parts = [part for part in (row.get("company_name"), row.get("console_user")) if part]
        agent_version = str(row.get("agent_version") or "").strip()
        if agent_version:
            details_parts.append(f"Agent {agent_version}")
        records.append(
            {
                "access_method": "Tray app",
                "identity": identity,
                "source": "Tray agent",
                "source_ip": row.get("last_ip"),
                "details": " · ".join(str(part) for part in details_parts) or "Desktop tray connection",
                "activity_at": activity_at,
            }
        )

    min_time = datetime.min.replace(tzinfo=timezone.utc)
    records.sort(key=lambda entry: entry.get("activity_at") or min_time, reverse=True)
    return records[:capped_limit]
