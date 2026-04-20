"""Shared helpers for MCP service implementations (ChatGPT, Ollama, ...)."""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Any, Mapping


# Sensitive ticket-record field names that should never be serialised to an
# external MCP client, even if they should not normally appear on a ticket row.
SENSITIVE_FIELDS: frozenset[str] = frozenset(
    {
        "password_hash",
        "password",
        "secret",
        "token",
        "api_key",
        "totp_secret",
        "encryption_key",
        "private_key",
        "client_secret",
        "webhook_secret",
        "auth_token",
        "shared_secret",
        "shared_secret_hash",
    }
)


def constant_time_compare(candidate: str, expected: str) -> bool:
    """Constant-time string comparison wrapper around :func:`hmac.compare_digest`."""

    return hmac.compare_digest(candidate, expected)


def hash_secret(secret: str) -> str:
    """SHA-256 hex digest of a secret, matching the modules-service convention."""

    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def serialise_datetime(value: Any) -> str | None:
    """Serialise a datetime (or arbitrary value) to an ISO-8601 UTC string."""

    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    if value is None:
        return None
    return str(value)


def filter_sensitive_fields(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy of *record* without any keys matching :data:`SENSITIVE_FIELDS`."""

    return {k: v for k, v in record.items() if k.lower() not in SENSITIVE_FIELDS}


def serialise_ticket(record: Mapping[str, Any]) -> dict[str, Any]:
    """Project a ticket record to a stable, MCP-safe payload."""

    return {
        "id": record.get("id"),
        "subject": record.get("subject"),
        "description": record.get("description"),
        "status": record.get("status"),
        "priority": record.get("priority"),
        "category": record.get("category"),
        "module_slug": record.get("module_slug"),
        "company_id": record.get("company_id"),
        "requester_id": record.get("requester_id"),
        "assigned_user_id": record.get("assigned_user_id"),
        "external_reference": record.get("external_reference"),
        "created_at": serialise_datetime(record.get("created_at")),
        "updated_at": serialise_datetime(record.get("updated_at")),
        "closed_at": serialise_datetime(record.get("closed_at")),
    }


def serialise_reply(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": record.get("id"),
        "ticket_id": record.get("ticket_id"),
        "author_id": record.get("author_id"),
        "body": record.get("body"),
        "is_internal": bool(record.get("is_internal")),
        "created_at": serialise_datetime(record.get("created_at")),
    }


def serialise_watcher(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": record.get("id"),
        "ticket_id": record.get("ticket_id"),
        "user_id": record.get("user_id"),
        "created_at": serialise_datetime(record.get("created_at")),
    }
