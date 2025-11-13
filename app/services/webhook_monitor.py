from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

import httpx

from app.core.logging import log_error, log_info
from app.repositories import webhook_events as webhook_repo

_MAX_BACKOFF_SECONDS = 3600
_SENSITIVE_HEADERS = {
    "authorization",
    "proxy-authorization",
    "x-api-key",
    "x-auth-token",
    "api-key",
    "x-access-token",
}
_SENSITIVE_RESPONSE_HEADERS = {"set-cookie", "set-cookie2"}


def _truncate(value: str | None, *, limit: int = 4000) -> str | None:
    if value is None:
        return None
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def _redact_headers(headers: Mapping[str, Any] | None, *, sensitive: set[str]) -> dict[str, Any] | None:
    if not headers:
        return None
    result: dict[str, Any] = {}
    for key, value in headers.items():
        lower_key = str(key).lower()
        if lower_key in sensitive:
            result[str(key)] = "***REDACTED***"
        else:
            result[str(key)] = str(value)
    return result


def _prepare_request_body(payload: Any) -> Any:
    if payload is None:
        return None
    if isinstance(payload, bytes):
        return _truncate(payload.decode(errors="replace"))
    if isinstance(payload, str):
        return _truncate(payload)
    return payload


async def enqueue_event(
    *,
    name: str,
    target_url: str,
    payload: Any = None,
    headers: dict[str, str] | None = None,
    max_attempts: int = 3,
    backoff_seconds: int = 300,
    attempt_immediately: bool = True,
) -> dict[str, Any]:
    event = await webhook_repo.create_event(
        name=name,
        target_url=target_url,
        headers=headers,
        payload=payload,
        max_attempts=max_attempts,
        backoff_seconds=backoff_seconds,
    )
    if attempt_immediately and event.get("id"):
        event_id = int(event["id"])
        await webhook_repo.mark_in_progress(event_id)
        await _attempt_event(event)
        refreshed = await webhook_repo.get_event(event_id)
        if refreshed:
            return refreshed
    return event


async def create_manual_event(
    *,
    name: str,
    target_url: str,
    payload: Any = None,
    headers: dict[str, str] | None = None,
    max_attempts: int = 1,
    backoff_seconds: int = 0,
) -> dict[str, Any]:
    """Create a webhook event handled outside the automatic dispatcher.

    Manual events are marked ``in_progress`` immediately so the background
    webhook monitor does not attempt delivery before the caller records a
    success or failure outcome.  This mirrors the behaviour of
    :func:`enqueue_event` without triggering outbound HTTP retries.
    """

    event = await webhook_repo.create_event(
        name=name,
        target_url=target_url,
        headers=headers,
        payload=payload,
        max_attempts=max(1, max_attempts),
        backoff_seconds=max(0, backoff_seconds),
    )
    if not event or event.get("id") is None:
        return event
    event_id = int(event["id"])
    await webhook_repo.mark_in_progress(event_id)
    refreshed = await webhook_repo.get_event(event_id)
    return refreshed or event


async def record_manual_success(
    event_id: int,
    *,
    attempt_number: int,
    response_status: int | None,
    response_body: str | None,
    request_headers: Mapping[str, Any] | None = None,
    request_body: Any = None,
    response_headers: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist a successful delivery outcome for an externally handled event."""

    await webhook_repo.record_attempt(
        event_id=event_id,
        attempt_number=attempt_number,
        status="succeeded",
        response_status=response_status,
        response_body=response_body,
        error_message=None,
        request_headers=_redact_headers(request_headers, sensitive=_SENSITIVE_HEADERS),
        request_body=_prepare_request_body(request_body),
        response_headers=_redact_headers(response_headers, sensitive=_SENSITIVE_RESPONSE_HEADERS),
    )
    await webhook_repo.mark_event_completed(
        event_id,
        attempt_number=attempt_number,
        response_status=response_status,
        response_body=response_body,
    )
    refreshed = await webhook_repo.get_event(event_id)
    return refreshed or {"id": event_id, "status": "succeeded"}


async def record_manual_failure(
    event_id: int,
    *,
    attempt_number: int,
    status: str,
    error_message: str | None,
    response_status: int | None,
    response_body: str | None,
    request_headers: Mapping[str, Any] | None = None,
    request_body: Any = None,
    response_headers: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist a failed delivery outcome for an externally handled event."""

    await webhook_repo.record_attempt(
        event_id=event_id,
        attempt_number=attempt_number,
        status=status,
        response_status=response_status,
        response_body=response_body,
        error_message=error_message,
        request_headers=_redact_headers(request_headers, sensitive=_SENSITIVE_HEADERS),
        request_body=_prepare_request_body(request_body),
        response_headers=_redact_headers(response_headers, sensitive=_SENSITIVE_RESPONSE_HEADERS),
    )
    await webhook_repo.mark_event_failed(
        event_id,
        attempt_number=attempt_number,
        error_message=error_message,
        response_status=response_status,
        response_body=response_body,
    )
    refreshed = await webhook_repo.get_event(event_id)
    return refreshed or {"id": event_id, "status": "failed", "last_error": error_message}


async def process_pending_events(limit: int = 10) -> None:
    events = await webhook_repo.list_due_events(limit=limit)
    for event in events:
        try:
            await webhook_repo.mark_in_progress(int(event["id"]))
            await _attempt_event(event)
        except Exception as exc:  # pragma: no cover - defensive logging
            log_error("Failed to process webhook event", event_id=event.get("id"), error=str(exc))


async def purge_completed_events(*, retention: timedelta = timedelta(hours=24)) -> int:
    if retention.total_seconds() <= 0:
        return 0
    cutoff = datetime.now(timezone.utc) - retention
    deleted = await webhook_repo.delete_succeeded_before(cutoff)
    if deleted:
        log_info("Purged delivered webhook events", count=deleted)
    return deleted


async def fail_stalled_events(*, timeout_seconds: int = 600) -> int:
    """Mark webhook events that have been in_progress for too long as failed.
    
    Args:
        timeout_seconds: Maximum time a webhook can be in_progress (default: 600 = 10 minutes)
    
    Returns:
        Number of events marked as failed
    """
    events = await webhook_repo.list_stalled_events(timeout_seconds=timeout_seconds)
    if not events:
        return 0
    
    failed_count = 0
    for event in events:
        event_id = int(event["id"])
        attempt_number = int(event.get("attempt_count") or 0) + 1
        error_message = f"Webhook task timed out after {timeout_seconds} seconds"
        
        try:
            await webhook_repo.record_attempt(
                event_id=event_id,
                attempt_number=attempt_number,
                status="timeout",
                response_status=None,
                response_body=None,
                error_message=error_message,
            )
            await webhook_repo.mark_event_failed(
                event_id=event_id,
                attempt_number=attempt_number,
                error_message=error_message,
                response_status=None,
                response_body=None,
            )
            log_error(
                "Webhook event timed out",
                event_id=event_id,
                timeout_seconds=timeout_seconds,
                name=event.get("name"),
            )
            failed_count += 1
        except Exception as exc:  # pragma: no cover - defensive logging
            log_error(
                "Failed to mark stalled webhook event as failed",
                event_id=event_id,
                error=str(exc),
            )
    
    if failed_count:
        log_info("Failed stalled webhook events", count=failed_count)
    
    return failed_count


async def force_retry(event_id: int) -> dict[str, Any] | None:
    await webhook_repo.force_retry(event_id)
    event = await webhook_repo.get_event(event_id)
    if not event:
        return None
    await _attempt_event(event)
    return await webhook_repo.get_event(event_id)


async def _attempt_event(event: dict[str, Any]) -> None:
    event_id = int(event["id"])
    attempt = int(event.get("attempt_count") or 0) + 1
    max_attempts = int(event.get("max_attempts") or 1)
    backoff_seconds = int(event.get("backoff_seconds") or 300)
    headers = {str(key): str(value) for key, value in (event.get("headers") or {}).items()}
    safe_headers = _redact_headers(headers, sensitive=_SENSITIVE_HEADERS)
    payload = event.get("payload")
    request_body = _prepare_request_body(payload)
    log_info("Delivering webhook", event_id=event_id, attempt=attempt, url=event.get("target_url"))
    response_status: int | None = None
    response_body: str | None = None
    response_headers: dict[str, Any] | None = None
    error_message: str | None = None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                str(event["target_url"]),
                json=payload,
                headers=headers,
            )
        response_status = response.status_code
        response_body = _truncate(response.text)
        response_headers = _redact_headers(response.headers, sensitive=_SENSITIVE_RESPONSE_HEADERS)
        success = 200 <= response.status_code < 300
        status_text = "succeeded" if success else "failed"
        await webhook_repo.record_attempt(
            event_id=event_id,
            attempt_number=attempt,
            status=status_text,
            response_status=response_status,
            response_body=response_body,
            error_message=None if success else f"HTTP {response.status_code}",
            request_headers=safe_headers,
            request_body=request_body,
            response_headers=response_headers,
        )
        if success:
            await webhook_repo.mark_event_completed(
                event_id,
                attempt_number=attempt,
                response_status=response_status,
                response_body=response_body,
            )
            log_info("Webhook delivered", event_id=event_id, status=response_status)
            return
        error_message = f"Unexpected status {response.status_code}"
    except Exception as exc:  # pragma: no cover - network safety
        error_message = str(exc)
        await webhook_repo.record_attempt(
            event_id=event_id,
            attempt_number=attempt,
            status="error",
            response_status=response_status,
            response_body=response_body,
            error_message=error_message,
            request_headers=safe_headers,
            request_body=request_body,
            response_headers=response_headers,
        )

    if attempt >= max_attempts:
        await webhook_repo.mark_event_failed(
            event_id,
            attempt_number=attempt,
            error_message=error_message,
            response_status=response_status,
            response_body=response_body,
        )
        log_error("Webhook delivery failed", event_id=event_id, error=error_message)
        return

    next_attempt = _calculate_next_attempt(backoff_seconds, attempt)
    await webhook_repo.schedule_retry(
        event_id,
        attempt_number=attempt,
        next_attempt_at=next_attempt,
        error_message=error_message,
        response_status=response_status,
        response_body=response_body,
    )
    log_info(
        "Webhook scheduled for retry",
        event_id=event_id,
        attempt=attempt,
        next_attempt=next_attempt.isoformat(),
        reason=error_message,
    )


def _calculate_next_attempt(backoff_seconds: int, attempt: int) -> datetime:
    delay = min(backoff_seconds * (2 ** (attempt - 1)), _MAX_BACKOFF_SECONDS)
    return datetime.now(timezone.utc) + timedelta(seconds=delay)
