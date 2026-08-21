"""Tenant-safe persistence operations for voice monitoring."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from typing import Any

from app.core.database import db


ENDPOINT_FIELDS = (
    "subscription_id", "destination_e164", "display_label", "enabled", "timezone",
    "schedule_cron", "interval_seconds", "timeout_seconds", "max_retries",
    "retry_delay_seconds", "expected_behavior", "transcription_enabled",
    "ticket_on_failure", "ticket_failure_threshold", "next_run_at",
)
TERMINAL_STATES = {"passed", "failed", "timed_out", "cancelled", "exhausted"}
ATTEMPT_STATES = {"queued", "retry_wait", "dialing", "answered", "interrupted", *TERMINAL_STATES}
TRANSITIONS = {
    "queued": {"dialing", "cancelled", "exhausted"},
    "retry_wait": {"dialing", "cancelled", "exhausted"},
    "interrupted": {"retry_wait", "failed", "exhausted"},
    "dialing": {"answered", "failed", "timed_out", "cancelled", "retry_wait", "interrupted"},
    "answered": {"passed", "failed", "timed_out", "cancelled"},
}


async def _require_owned_subscription(company_id: int, subscription_id: str | None) -> None:
    if subscription_id is None:
        return
    owned = await db.fetch_one(
        "SELECT id FROM subscriptions WHERE id = %s AND customer_id = %s",
        (subscription_id, company_id),
    )
    if owned is None:
        raise ValueError("subscription does not belong to company")


async def create_endpoint(company_id: int, values: dict[str, Any]) -> dict[str, Any]:
    """Create an endpoint owned by ``company_id``."""
    await _require_owned_subscription(company_id, values.get("subscription_id"))
    columns = [field for field in ENDPOINT_FIELDS if field in values]
    endpoint_id = await db.execute_returning_lastrowid(
        f"INSERT INTO voice_monitor_endpoints (company_id, {', '.join(columns)}) "
        f"VALUES (%s, {', '.join(['%s'] * len(columns))})",
        (company_id, *(values[field] for field in columns)),
    )
    endpoint = await get_endpoint(company_id, endpoint_id)
    if endpoint is None:
        raise RuntimeError("created voice monitor endpoint could not be read")
    return endpoint


async def get_endpoint(company_id: int, endpoint_id: int) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT * FROM voice_monitor_endpoints WHERE id = %s AND company_id = %s",
        (endpoint_id, company_id),
    )


async def list_endpoints(
    company_id: int, *, enabled: bool | None = None, limit: int = 100, offset: int = 0
) -> list[dict[str, Any]]:
    condition = " AND enabled = %s" if enabled is not None else ""
    params: tuple[Any, ...] = (company_id, enabled, limit, offset) if enabled is not None else (company_id, limit, offset)
    return await db.fetch_all(
        f"SELECT * FROM voice_monitor_endpoints WHERE company_id = %s{condition} "
        "ORDER BY display_label, id LIMIT %s OFFSET %s",
        params,
    )


async def update_endpoint(
    company_id: int, endpoint_id: int, values: dict[str, Any]
) -> dict[str, Any] | None:
    if "subscription_id" in values:
        await _require_owned_subscription(company_id, values["subscription_id"])
    columns = [field for field in ENDPOINT_FIELDS if field in values]
    if columns:
        await db.execute(
            f"UPDATE voice_monitor_endpoints SET {', '.join(f'{field} = %s' for field in columns)} "
            "WHERE id = %s AND company_id = %s",
            (*(values[field] for field in columns), endpoint_id, company_id),
        )
    return await get_endpoint(company_id, endpoint_id)


async def delete_endpoint(company_id: int, endpoint_id: int) -> bool:
    """Delete configuration; the FK deliberately preserves attempt history."""
    return bool(await db.execute_rowcount(
        "DELETE FROM voice_monitor_endpoints WHERE id = %s AND company_id = %s",
        (endpoint_id, company_id),
    ))


async def claim_due_work(
    *, worker_identity: str, limit: int = 25, now: datetime | None = None, lease_seconds: int = 300
) -> list[dict[str, Any]]:
    """Claim due endpoints with a compare-and-swap lease and enqueue attempts.

    This is an internal cross-tenant worker operation. Customer reads remain
    company-scoped; the returned rows include the owning company explicitly.
    """
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    lease_until = now + timedelta(seconds=lease_seconds)
    candidates = await db.fetch_all(
        "SELECT * FROM voice_monitor_endpoints WHERE enabled = 1 "
        "AND next_run_at IS NOT NULL AND next_run_at <= %s ORDER BY next_run_at, id LIMIT %s",
        (now, limit),
    )
    claimed: list[dict[str, Any]] = []
    for endpoint in candidates:
        changed = await db.execute_rowcount(
            "UPDATE voice_monitor_endpoints SET next_run_at = %s "
            "WHERE id = %s AND enabled = 1 AND next_run_at = %s",
            (lease_until, endpoint["id"], endpoint["next_run_at"]),
        )
        if not changed:
            continue
        attempt_id = await db.execute_returning_lastrowid(
            "INSERT INTO voice_monitor_attempts (endpoint_id, company_id, queued_at, worker_identity) "
            "VALUES (%s, %s, %s, %s)",
            (endpoint["id"], endpoint["company_id"], now, worker_identity),
        )
        claimed.append({**dict(endpoint), "attempt_id": attempt_id, "lease_until": lease_until})
    return claimed


def dispatch_key(endpoint_id: int, scheduled_for: datetime) -> str:
    """Return the stable identity of one endpoint schedule occurrence."""
    stamp = scheduled_for.replace(tzinfo=None).isoformat(timespec="microseconds")
    return hashlib.sha256(f"voice-monitor:{endpoint_id}:{stamp}".encode()).hexdigest()


async def enqueue_due_attempts(*, limit: int = 100, now: datetime | None = None) -> int:
    """Lightweight dispatcher: durably enqueue each due occurrence exactly once.

    The unique dispatch key closes the crash window between inserting an attempt
    and advancing the endpoint.  A subsequent dispatcher pass can safely repeat
    either operation without producing another attempt.
    """
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    due = await db.fetch_all(
        "SELECT id, company_id, next_run_at, interval_seconds, max_retries "
        "FROM voice_monitor_endpoints WHERE enabled = 1 AND next_run_at IS NOT NULL "
        "AND next_run_at <= %s ORDER BY next_run_at, id LIMIT %s", (now, limit),
    )
    enqueued = 0
    for endpoint in due:
        scheduled_for = endpoint["next_run_at"]
        key = dispatch_key(int(endpoint["id"]), scheduled_for)
        insert_verb = "INSERT OR IGNORE" if db.is_sqlite() else "INSERT IGNORE"
        inserted = await db.execute_rowcount(
            f"{insert_verb} INTO voice_monitor_attempts "
            "(endpoint_id, company_id, queued_at, scheduled_for, available_at, dispatch_key, "
            "provider_idempotency_key, max_deliveries) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (endpoint["id"], endpoint["company_id"], now, scheduled_for, now, key, key,
             int(endpoint.get("max_retries") or 0) + 1),
        )
        enqueued += int(bool(inserted))
        # Interval schedules are advanced here; cron schedules are recalculated by
        # configuration scheduling code and deliberately disabled until then.
        interval = endpoint.get("interval_seconds")
        next_run = scheduled_for + timedelta(seconds=int(interval)) if interval else None
        await db.execute_rowcount(
            "UPDATE voice_monitor_endpoints SET next_run_at = %s "
            "WHERE id = %s AND enabled = 1 AND next_run_at = %s",
            (next_run, endpoint["id"], scheduled_for),
        )
    return enqueued


async def claim_attempts(
    *, worker_identity: str, limit: int, lease_seconds: int, per_tenant: int,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Atomically CAS-claim available or abandoned attempts, fairly by tenant."""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    lease_until = now + timedelta(seconds=lease_seconds)
    candidates = await db.fetch_all(
        "SELECT a.*, e.destination_e164, e.timeout_seconds, e.expected_behavior, "
        "e.transcription_enabled, e.retry_delay_seconds FROM voice_monitor_attempts a "
        "LEFT JOIN voice_monitor_endpoints e ON e.id = a.endpoint_id "
        "WHERE a.completed_at IS NULL AND a.available_at <= %s "
        "AND a.delivery_count < a.max_deliveries AND (a.outcome_status IN ('queued','retry_wait') "
        "OR (a.outcome_status IN ('dialing','answered','interrupted') AND a.lease_until < %s)) "
        "ORDER BY a.available_at, a.company_id, a.id LIMIT %s", (now, now, limit * max(per_tenant, 1) * 2),
    )
    claimed, tenant_counts = [], {}
    for attempt in candidates:
        tenant = int(attempt["company_id"])
        if tenant_counts.get(tenant, 0) >= per_tenant or len(claimed) >= limit:
            continue
        old_owner, old_lease, old_status = attempt.get("lease_owner"), attempt.get("lease_until"), attempt["outcome_status"]
        changed = await db.execute_rowcount(
            "UPDATE voice_monitor_attempts SET outcome_status = 'dialing', lease_owner = %s, "
            "worker_identity = %s, lease_until = %s, heartbeat_at = %s, delivery_count = delivery_count + 1, "
            "started_at = COALESCE(started_at, %s) WHERE id = %s AND outcome_status = %s "
            "AND ((lease_owner IS NULL AND %s IS NULL) OR lease_owner = %s) "
            "AND ((lease_until IS NULL AND %s IS NULL) OR lease_until = %s)",
            (worker_identity, worker_identity, lease_until, now, now, attempt["id"], old_status,
             old_owner, old_owner, old_lease, old_lease),
        )
        if changed:
            attempt.update(outcome_status="dialing", lease_owner=worker_identity,
                           lease_until=lease_until, delivery_count=int(attempt.get("delivery_count") or 0) + 1)
            claimed.append(attempt)
            tenant_counts[tenant] = tenant_counts.get(tenant, 0) + 1
    return claimed


async def heartbeat_attempt(attempt_id: int, worker_identity: str, *, lease_seconds: int,
                            now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    return bool(await db.execute_rowcount(
        "UPDATE voice_monitor_attempts SET heartbeat_at = %s, lease_until = %s "
        "WHERE id = %s AND lease_owner = %s AND completed_at IS NULL",
        (now, now + timedelta(seconds=lease_seconds), attempt_id, worker_identity),
    ))


async def finish_delivery(attempt: dict[str, Any], worker_identity: str, *, status: str,
                          failure_category: str | None = None, now: datetime | None = None) -> bool:
    """Persist a result or schedule bounded exponential retry."""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    deliveries = int(attempt["delivery_count"])
    maximum = int(attempt["max_deliveries"])
    retryable = status in {"failed", "timed_out", "interrupted"} and deliveries < maximum
    final_status = "retry_wait" if retryable else ("exhausted" if status == "interrupted" else status)
    delay = min(int(attempt.get("retry_delay_seconds") or 60) * (2 ** max(deliveries - 1, 0)), 3600)
    return bool(await db.execute_rowcount(
        "UPDATE voice_monitor_attempts SET outcome_status = %s, failure_category = %s, "
        "available_at = %s, completed_at = %s, lease_owner = NULL, lease_until = NULL, heartbeat_at = NULL "
        "WHERE id = %s AND lease_owner = %s AND completed_at IS NULL",
        (final_status, failure_category, now + timedelta(seconds=delay) if retryable else now,
         None if retryable else now, attempt["id"], worker_identity),
    ))


async def get_attempt(company_id: int, attempt_id: int) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT * FROM voice_monitor_attempts WHERE id = %s AND company_id = %s",
        (attempt_id, company_id),
    )


async def get_attempt_by_provider_call_id(company_id: int, provider_call_id: str) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT * FROM voice_monitor_attempts WHERE provider_call_id = %s AND company_id = %s",
        (provider_call_id, company_id),
    )


async def list_attempts(
    company_id: int, *, endpoint_id: int | None = None, status: str | None = None,
    limit: int = 100, offset: int = 0,
) -> list[dict[str, Any]]:
    conditions, params = ["company_id = %s"], [company_id]
    if endpoint_id is not None:
        conditions.append("endpoint_id = %s")
        params.append(endpoint_id)
    if status is not None:
        conditions.append("outcome_status = %s")
        params.append(status)
    params.extend((limit, offset))
    return await db.fetch_all(
        f"SELECT * FROM voice_monitor_attempts WHERE {' AND '.join(conditions)} "
        "ORDER BY queued_at DESC, id DESC LIMIT %s OFFSET %s", tuple(params),
    )


async def count_attempts(company_id: int, *, endpoint_id: int | None = None) -> int:
    suffix = " AND endpoint_id = %s" if endpoint_id is not None else ""
    params = (company_id, endpoint_id) if endpoint_id is not None else (company_id,)
    row = await db.fetch_one(
        f"SELECT COUNT(*) AS count FROM voice_monitor_attempts WHERE company_id = %s{suffix}", params
    )
    return int(row["count"]) if row else 0


async def transition_attempt(
    company_id: int, attempt_id: int, from_status: str, to_status: str, **result: Any
) -> bool:
    """Atomically apply a valid state transition and immutable result fields."""
    if to_status not in ATTEMPT_STATES or to_status not in TRANSITIONS.get(from_status, set()):
        raise ValueError(f"invalid attempt transition: {from_status} -> {to_status}")
    allowed = {
        "started_at", "answered_at", "completed_at", "provider_response_code",
        "provider_call_id", "failure_category", "failure_detail", "duration_seconds",
        "media_artifact_reference", "transcript_status", "transcript_text_reference", "retry_count",
    }
    fields = [field for field in allowed if field in result]
    if "failure_detail" in result and result["failure_detail"] is not None:
        # Persist a bounded, single-line diagnostic rather than raw provider
        # payloads; callers must never pass credentials in this field.
        result["failure_detail"] = " ".join(str(result["failure_detail"]).split())[:1000]
    if to_status in TERMINAL_STATES and "completed_at" not in result:
        result["completed_at"] = datetime.now(timezone.utc).replace(tzinfo=None)
        fields.append("completed_at")
    assignments = ["outcome_status = %s", *(f"{field} = %s" for field in fields)]
    params = [to_status, *(result[field] for field in fields), attempt_id, company_id, from_status]
    return bool(await db.execute_rowcount(
        f"UPDATE voice_monitor_attempts SET {', '.join(assignments)} "
        "WHERE id = %s AND company_id = %s AND outcome_status = %s",
        tuple(params),
    ))


async def link_ticket_once(company_id: int, attempt_id: int, ticket_id: int) -> bool:
    """Atomically set the first ticket link, preventing duplicate ticket creation."""
    return bool(await db.execute_rowcount(
        "UPDATE voice_monitor_attempts SET created_ticket_id = %s "
        "WHERE id = %s AND company_id = %s AND created_ticket_id IS NULL",
        (ticket_id, attempt_id, company_id),
    ))
