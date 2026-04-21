from __future__ import annotations

from typing import Any

from fastapi import Request

from app.core.logging import get_request_context, log_audit_event, log_error
from app.repositories import audit_logs as audit_repo
from app.services.audit_diff import diff as compute_diff
from app.services.audit_diff import redact


def _determine_event_type(action: str) -> str:
    """Determine the event type category based on the action prefix."""
    if action.startswith("bcp."):
        return "BCP ACTION"
    return "API OPERATION"


def _extract_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


def _extract_request_id(request: Request | None) -> str | None:
    """Pick up the X-Request-ID set by RequestLoggingMiddleware, if any."""

    if request is not None:
        candidate = getattr(request.state, "request_id", None)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
        header_value = request.headers.get("x-request-id")
        if header_value:
            stripped = header_value.strip()
            if stripped:
                return stripped
    # Fall back to context-bound request id from logging contextvars.
    return get_request_context().get("request_id")


async def log_action(
    *,
    action: str,
    user_id: int | None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    previous_value: Any = None,
    new_value: Any = None,
    metadata: dict[str, Any] | None = None,
    request: Request | None = None,
    api_key: str | None = None,
    request_id: str | None = None,
) -> None:
    ip_address = _extract_ip(request)
    resolved_request_id = request_id or _extract_request_id(request)

    # Log to database. Audit writes must never break the user request, so any
    # repository-level error is logged and swallowed here.
    try:
        await audit_repo.create_audit_log(
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            previous_value=previous_value,
            new_value=new_value,
            metadata=metadata,
            api_key=api_key,
            ip_address=ip_address,
            request_id=resolved_request_id,
        )
    except Exception as exc:  # pragma: no cover - defensive
        log_error(
            "Audit log database write failed",
            exc=exc,
            event="audit.db_write_failed",
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
        )

    # Log to disk for external tools (Fail2ban, SIEM platforms)
    event_type = _determine_event_type(action)
    extra_meta: dict[str, Any] = {}
    if api_key:
        extra_meta["api_key"] = api_key
    if resolved_request_id:
        extra_meta["request_id"] = resolved_request_id
    if metadata:
        # Include company_id if present in metadata for easier filtering
        if "company_id" in metadata:
            extra_meta["company_id"] = metadata["company_id"]

    log_audit_event(
        event_type,
        action,
        user_id=user_id,
        entity_type=entity_type,
        entity_id=entity_id,
        ip_address=ip_address,
        **extra_meta,
    )


async def record(
    *,
    action: str,
    request: Request | None = None,
    user_id: int | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    before: Any = None,
    after: Any = None,
    metadata: dict[str, Any] | None = None,
    api_key: str | None = None,
    sensitive_extra_keys: tuple[str, ...] = (),
) -> None:
    """Record an audit event with automatic field-level diff and redaction.

    This is the recommended helper for new audit calls. It accepts the full
    ``before`` / ``after`` snapshots of an entity (as a dict, Pydantic model,
    or any mapping-like object), computes a diff so only changed fields are
    stored, redacts well-known sensitive keys (passwords, tokens, secrets,
    API keys, ...), and pulls ``user_id``, ``request_id``, ``ip`` and
    ``api_key`` from the active request context when not supplied explicitly.

    For ticket replies, callers should pass ``after`` as a small descriptive
    dict (see :func:`app.services.audit_diff.summarise_reply_body`) rather
    than the reply body itself, and add ``"body"`` to ``sensitive_extra_keys``
    to ensure the body is never stored even if it leaks into ``metadata``.
    """

    previous_value, new_value = compute_diff(
        before, after, sensitive_extra_keys=sensitive_extra_keys
    )

    # Skip pure no-op updates (after == before) so the audit log isn't spammed
    # with rows that capture no information. Creations and deletions still go
    # through because at least one side will be non-None.
    if before is not None and after is not None and previous_value is None and new_value is None:
        return

    safe_metadata: dict[str, Any] | None
    if metadata is None:
        safe_metadata = None
    else:
        safe_metadata = redact(metadata, sensitive_extra_keys=sensitive_extra_keys)

    resolved_user_id = user_id
    if resolved_user_id is None:
        ctx_user_id = get_request_context().get("user_id")
        if isinstance(ctx_user_id, int):
            resolved_user_id = ctx_user_id

    await log_action(
        action=action,
        user_id=resolved_user_id,
        entity_type=entity_type,
        entity_id=entity_id,
        previous_value=previous_value,
        new_value=new_value,
        metadata=safe_metadata,
        request=request,
        api_key=api_key,
    )


async def record_create(
    *,
    action: str,
    after: Any,
    request: Request | None = None,
    user_id: int | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    metadata: dict[str, Any] | None = None,
    api_key: str | None = None,
    sensitive_extra_keys: tuple[str, ...] = (),
) -> None:
    """Record a creation event. Convenience wrapper around :func:`record`.

    Equivalent to calling ``record(..., before=None, after=after)`` but makes
    the intent explicit at the call site and removes the chance of accidentally
    passing a stale ``before`` snapshot.
    """

    await record(
        action=action,
        request=request,
        user_id=user_id,
        entity_type=entity_type,
        entity_id=entity_id,
        before=None,
        after=after,
        metadata=metadata,
        api_key=api_key,
        sensitive_extra_keys=sensitive_extra_keys,
    )


async def record_delete(
    *,
    action: str,
    before: Any,
    request: Request | None = None,
    user_id: int | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    metadata: dict[str, Any] | None = None,
    api_key: str | None = None,
    sensitive_extra_keys: tuple[str, ...] = (),
) -> None:
    """Record a deletion event. Convenience wrapper around :func:`record`.

    Equivalent to calling ``record(..., before=before, after=None)``.
    """

    await record(
        action=action,
        request=request,
        user_id=user_id,
        entity_type=entity_type,
        entity_id=entity_id,
        before=before,
        after=None,
        metadata=metadata,
        api_key=api_key,
        sensitive_extra_keys=sensitive_extra_keys,
    )
