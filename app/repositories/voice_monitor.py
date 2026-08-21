"""Tenant-safe persistence operations for voice monitoring."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import secrets
from typing import Any

from app.core.database import db


ENDPOINT_FIELDS = (
    "subscription_id",
    "destination_e164",
    "display_label",
    "enabled",
    "timezone",
    "schedule_cron",
    "interval_seconds",
    "timeout_seconds",
    "max_retries",
    "retry_delay_seconds",
    "expected_behavior",
    "transcription_enabled",
    "ticket_on_failure",
    "ticket_failure_threshold",
    "next_run_at",
    "consent_granted",
    "recording_consent_granted",
    "consent_actor_id",
    "consent_at",
    "consent_policy_version",
    "consent_revoked_at",
    "quiet_hours_start",
    "quiet_hours_end",
    "caller_id_verified",
    "daily_attempt_limit",
    "monetary_cap_minor",
)
TERMINAL_STATES = {"passed", "failed", "timed_out", "cancelled", "exhausted"}
ATTEMPT_STATES = {
    "queued",
    "retry_wait",
    "dialing",
    "answered",
    "interrupted",
    *TERMINAL_STATES,
}
TRANSITIONS = {
    "queued": {"dialing", "cancelled", "exhausted"},
    "retry_wait": {"dialing", "cancelled", "exhausted"},
    "interrupted": {"retry_wait", "failed", "exhausted"},
    "dialing": {
        "answered",
        "failed",
        "timed_out",
        "cancelled",
        "retry_wait",
        "interrupted",
    },
    "answered": {"passed", "failed", "timed_out", "cancelled"},
}


async def _require_owned_subscription(
    company_id: int, subscription_id: str | None
) -> None:
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
    if values.get("consent_granted") and not values.get("consent_actor_id"):
        raise ValueError("consent actor is required")
    if values.get("consent_granted"):
        values["consent_at"] = values.get("consent_at") or datetime.now(timezone.utc).replace(tzinfo=None)
    if values.get("enabled", True):
        if values.get("subscription_id"):
            from app.services.voice_monitor_billing import assert_endpoint_capacity
            await assert_endpoint_capacity(values["subscription_id"], company_id=company_id)
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
    params: tuple[Any, ...] = (
        (company_id, enabled, limit, offset)
        if enabled is not None
        else (company_id, limit, offset)
    )
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
    current = await get_endpoint(company_id, endpoint_id)
    if values.get("consent_granted") and not values.get("consent_actor_id", (current or {}).get("consent_actor_id")):
        raise ValueError("consent actor is required")
    if values.get("consent_granted") and not (current or {}).get("consent_granted"):
        values["consent_at"] = datetime.now(timezone.utc).replace(tzinfo=None)
        values["consent_revoked_at"] = None
    if values.get("consent_granted") is False:
        values["consent_revoked_at"] = datetime.now(timezone.utc).replace(tzinfo=None)
        values["enabled"] = False
    if current and values.get("enabled", current.get("enabled")):
        subscription_id = values.get("subscription_id", current.get("subscription_id"))
        if subscription_id:
            from app.services.voice_monitor_billing import assert_endpoint_capacity
            await assert_endpoint_capacity(subscription_id, company_id=company_id,
                                           enabling_endpoint_id=endpoint_id)
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
    return bool(
        await db.execute_rowcount(
            "DELETE FROM voice_monitor_endpoints WHERE id = %s AND company_id = %s",
            (endpoint_id, company_id),
        )
    )


async def claim_due_work(
    *,
    worker_identity: str,
    limit: int = 25,
    now: datetime | None = None,
    lease_seconds: int = 300,
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
        claimed.append(
            {**dict(endpoint), "attempt_id": attempt_id, "lease_until": lease_until}
        )
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
        "SELECT e.id, e.company_id, e.subscription_id, e.next_run_at, e.interval_seconds, e.max_retries "
        "FROM voice_monitor_endpoints e JOIN subscriptions s ON s.id=e.subscription_id "
        "JOIN subscription_categories c ON c.id=s.subscription_category_id "
        "WHERE e.enabled=1 AND e.consent_granted=1 AND e.consent_revoked_at IS NULL "
        "AND e.caller_id_verified=1 AND e.next_run_at IS NOT NULL AND e.next_run_at <= %s "
        "AND LOWER(c.name)='voice monitor' AND LOWER(s.status)='active' "
        "AND %s BETWEEN s.start_date AND s.end_date "
        "AND NOT EXISTS (SELECT 1 FROM subscription_change_requests r WHERE r.subscription_id=s.id AND r.status='pending') "
        "ORDER BY e.next_run_at, e.id LIMIT %s",
        (now, now.date(), limit),
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
            (
                endpoint["id"],
                endpoint["company_id"],
                now,
                scheduled_for,
                now,
                key,
                key,
                int(endpoint.get("max_retries") or 0) + 1,
            ),
        )
        enqueued += int(bool(inserted))
        # Interval schedules are advanced here; cron schedules are recalculated by
        # configuration scheduling code and deliberately disabled until then.
        interval = endpoint.get("interval_seconds")
        next_run = (
            scheduled_for + timedelta(seconds=int(interval)) if interval else None
        )
        await db.execute_rowcount(
            "UPDATE voice_monitor_endpoints SET next_run_at = %s "
            "WHERE id = %s AND enabled = 1 AND next_run_at = %s",
            (next_run, endpoint["id"], scheduled_for),
        )
    return enqueued


async def claim_attempts(
    *,
    worker_identity: str,
    limit: int,
    lease_seconds: int,
    per_tenant: int,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Atomically CAS-claim available or abandoned attempts, fairly by tenant."""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    lease_until = now + timedelta(seconds=lease_seconds)
    candidates = await db.fetch_all(
        "SELECT a.*, e.destination_e164, e.timeout_seconds, e.expected_behavior, "
        "e.transcription_enabled, e.recording_consent_granted, e.retry_delay_seconds FROM voice_monitor_attempts a "
        "JOIN voice_monitor_endpoints e ON e.id = a.endpoint_id "
        "JOIN subscriptions s ON s.id=e.subscription_id "
        "JOIN subscription_categories c ON c.id=s.subscription_category_id "
        "WHERE a.completed_at IS NULL AND a.available_at <= %s "
        "AND e.enabled=1 AND e.consent_granted=1 AND e.consent_revoked_at IS NULL "
        "AND e.caller_id_verified=1 AND LOWER(c.name)='voice monitor' AND LOWER(s.status)='active' "
        "AND CURRENT_DATE BETWEEN s.start_date AND s.end_date "
        "AND NOT EXISTS (SELECT 1 FROM subscription_change_requests r WHERE r.subscription_id=s.id AND r.status='pending') "
        "AND a.delivery_count < a.max_deliveries AND (a.outcome_status IN ('queued','retry_wait') "
        "OR (a.outcome_status IN ('dialing','answered','interrupted') AND a.lease_until < %s)) "
        "ORDER BY a.available_at, a.company_id, a.id LIMIT %s",
        (now, now, limit * max(per_tenant, 1) * 2),
    )
    claimed, tenant_counts = [], {}
    for attempt in candidates:
        tenant = int(attempt["company_id"])
        if tenant_counts.get(tenant, 0) >= per_tenant or len(claimed) >= limit:
            continue
        old_owner, old_lease, old_status = (
            attempt.get("lease_owner"),
            attempt.get("lease_until"),
            attempt["outcome_status"],
        )
        changed = await db.execute_rowcount(
            "UPDATE voice_monitor_attempts SET outcome_status = 'dialing', lease_owner = %s, "
            "worker_identity = %s, lease_until = %s, heartbeat_at = %s, delivery_count = delivery_count + 1, "
            "started_at = COALESCE(started_at, %s) WHERE id = %s AND outcome_status = %s "
            "AND ((lease_owner IS NULL AND %s IS NULL) OR lease_owner = %s) "
            "AND ((lease_until IS NULL AND %s IS NULL) OR lease_until = %s)",
            (
                worker_identity,
                worker_identity,
                lease_until,
                now,
                now,
                attempt["id"],
                old_status,
                old_owner,
                old_owner,
                old_lease,
                old_lease,
            ),
        )
        if changed:
            attempt.update(
                outcome_status="dialing",
                lease_owner=worker_identity,
                lease_until=lease_until,
                delivery_count=int(attempt.get("delivery_count") or 0) + 1,
            )
            claimed.append(attempt)
            tenant_counts[tenant] = tenant_counts.get(tenant, 0) + 1
    return claimed


async def heartbeat_attempt(
    attempt_id: int,
    worker_identity: str,
    *,
    lease_seconds: int,
    now: datetime | None = None,
) -> bool:
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    return bool(
        await db.execute_rowcount(
            "UPDATE voice_monitor_attempts SET heartbeat_at = %s, lease_until = %s "
            "WHERE id = %s AND lease_owner = %s AND completed_at IS NULL",
            (now, now + timedelta(seconds=lease_seconds), attempt_id, worker_identity),
        )
    )


async def finish_delivery(
    attempt: dict[str, Any],
    worker_identity: str,
    *,
    status: str,
    failure_category: str | None = None,
    now: datetime | None = None,
) -> bool:
    """Persist a result or schedule bounded exponential retry."""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    deliveries = int(attempt["delivery_count"])
    maximum = int(attempt["max_deliveries"])
    retryable = (
        status in {"failed", "timed_out", "interrupted"} and deliveries < maximum
    )
    final_status = (
        "retry_wait"
        if retryable
        else ("exhausted" if status == "interrupted" else status)
    )
    delay = min(
        int(attempt.get("retry_delay_seconds") or 60) * (2 ** max(deliveries - 1, 0)),
        3600,
    )
    return bool(
        await db.execute_rowcount(
            "UPDATE voice_monitor_attempts SET outcome_status = %s, failure_category = %s, "
            "available_at = %s, completed_at = %s, lease_owner = NULL, lease_until = NULL, heartbeat_at = NULL "
            "WHERE id = %s AND lease_owner = %s AND completed_at IS NULL",
            (
                final_status,
                failure_category,
                now + timedelta(seconds=delay) if retryable else now,
                None if retryable else now,
                attempt["id"],
                worker_identity,
            ),
        )
    )


async def get_attempt(company_id: int, attempt_id: int) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT * FROM voice_monitor_attempts WHERE id = %s AND company_id = %s",
        (attempt_id, company_id),
    )


async def set_transcription_status(attempt_id: int, status: str,
                                   *, failure_code: str | None = None) -> None:
    """Update content processing independently from the operational outcome."""
    if status not in {"pending", "processing", "completed", "failed", "not_requested"}:
        raise ValueError("invalid transcription status")
    await db.execute_rowcount(
        "UPDATE voice_monitor_contents SET transcript_status = %s, transcription_failure_code = %s "
        "WHERE attempt_id = %s", (status, failure_code, attempt_id),
    )


async def initialize_content(attempt_id: int, company_id: int, *, media_reference: str | None,
                             transcription_requested: bool, retention_days: int = 30) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    verb = "INSERT OR IGNORE" if db.is_sqlite() else "INSERT IGNORE"
    await db.execute_rowcount(
        f"{verb} INTO voice_monitor_contents (attempt_id, company_id, media_reference, "
        "transcript_status, retain_until) VALUES (%s,%s,%s,%s,%s)",
        (attempt_id, company_id, media_reference,
         "pending" if transcription_requested else "not_requested",
         now + timedelta(days=max(0, retention_days))),
    )


async def store_transcript(attempt_id: int, company_id: int, transcript: str) -> str:
    """Persist transcript content separately; never place its text in attempt metadata."""
    reference = secrets.token_urlsafe(32)
    await db.execute_rowcount(
        "UPDATE voice_monitor_contents SET transcript_reference = %s, transcript_text = %s, "
        "transcript_status = 'completed', transcription_failure_code = NULL "
        "WHERE attempt_id = %s AND company_id = %s",
        (reference, transcript, attempt_id, company_id),
    )
    return reference


async def get_content(company_id: int, attempt_id: int) -> dict[str, Any] | None:
    """Tenant-authorized content retrieval."""
    return await db.fetch_one(
        "SELECT * FROM voice_monitor_contents WHERE attempt_id = %s AND company_id = %s",
        (attempt_id, company_id),
    )


async def delete_expired_content(*, now: datetime | None = None) -> int:
    """Retention job: erase content while leaving the attempt evidence intact."""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    return await db.execute_rowcount(
        "DELETE FROM voice_monitor_contents WHERE retain_until IS NOT NULL AND retain_until <= %s",
        (now,),
    )


async def record_worker_heartbeat(worker_identity: str, *, active_calls: int) -> None:
    queue = await db.fetch_one(
        "SELECT COUNT(*) count FROM voice_monitor_attempts WHERE completed_at IS NULL AND outcome_status IN ('queued','retry_wait')"
    ) or {}
    verb = "INSERT OR REPLACE" if db.is_sqlite() else "INSERT INTO"
    suffix = "" if db.is_sqlite() else " ON DUPLICATE KEY UPDATE heartbeat_at=VALUES(heartbeat_at),active_calls=VALUES(active_calls),queue_depth=VALUES(queue_depth)"
    await db.execute(
        f"{verb} voice_monitor_worker_heartbeats (worker_identity,heartbeat_at,active_calls,queue_depth) VALUES (%s,%s,%s,%s){suffix}",
        (worker_identity, datetime.now(timezone.utc).replace(tzinfo=None), active_calls, int(queue.get("count") or 0)),
    )


async def operational_metrics() -> dict[str, Any]:
    """Bounded operational counters; excludes destinations, media and transcripts."""
    return {
        "workers": await db.fetch_all("SELECT worker_identity,heartbeat_at,active_calls,queue_depth,provider_latency_ms FROM voice_monitor_worker_heartbeats ORDER BY worker_identity"),
        "outcomes": await db.fetch_all("SELECT outcome_status,COUNT(*) count FROM voice_monitor_attempts GROUP BY outcome_status"),
        "stuck_calls": (await db.fetch_one("SELECT COUNT(*) count FROM voice_monitor_attempts WHERE completed_at IS NULL AND outcome_status IN ('dialing','answered') AND lease_until<UTC_TIMESTAMP()") or {}).get("count", 0),
        "missing_callbacks": (await db.fetch_one("SELECT COUNT(*) count FROM voice_monitor_attempts WHERE provider_call_id IS NOT NULL AND final_callback_at IS NULL AND started_at<DATE_SUB(UTC_TIMESTAMP(),INTERVAL 10 MINUTE) AND outcome_status IN ('dialing','answered')") or {}).get("count", 0),
        "transcription_backlog": (await db.fetch_one("SELECT COUNT(*) count FROM voice_monitor_contents WHERE transcript_status IN ('pending','processing')") or {}).get("count", 0),
        "ticket_creation_failures": (await db.fetch_one("SELECT COUNT(*) count FROM voice_monitor_incidents WHERE ticket_claim_attempt_id IS NOT NULL AND ticket_id IS NULL") or {}).get("count", 0),
        "billing_unreconciled": (await db.fetch_one("SELECT COUNT(*) count FROM voice_monitor_attempts a LEFT JOIN voice_monitor_usage_ledger l ON l.attempt_id=a.id WHERE a.completed_at IS NOT NULL AND a.outcome_status IN ('passed','failed','timed_out','cancelled','exhausted') AND l.id IS NULL") or {}).get("count", 0),
    }


async def get_attempt_by_provider_call_id(
    company_id: int, provider_call_id: str
) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT * FROM voice_monitor_attempts WHERE provider_call_id = %s AND company_id = %s",
        (provider_call_id, company_id),
    )


async def list_attempts(
    company_id: int,
    *,
    endpoint_id: int | None = None,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
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
        "ORDER BY queued_at DESC, id DESC LIMIT %s OFFSET %s",
        tuple(params),
    )


async def count_attempts(company_id: int, *, endpoint_id: int | None = None) -> int:
    suffix = " AND endpoint_id = %s" if endpoint_id is not None else ""
    params = (company_id, endpoint_id) if endpoint_id is not None else (company_id,)
    row = await db.fetch_one(
        f"SELECT COUNT(*) AS count FROM voice_monitor_attempts WHERE company_id = %s{suffix}",
        params,
    )
    return int(row["count"]) if row else 0


async def get_preferences(company_id: int) -> dict[str, Any]:
    row = await db.fetch_one(
        "SELECT * FROM voice_monitor_preferences WHERE company_id = %s", (company_id,)
    )
    return row or {
        "company_id": company_id,
        "allow_test_calls": False,
        "recording_enabled": False,
        "notify_on_failure": True,
    }


async def set_preferences(
    company_id: int, user_id: int, values: dict[str, bool]
) -> dict[str, Any]:
    verb = "INSERT OR REPLACE" if db.is_sqlite() else "INSERT INTO"
    suffix = (
        ""
        if db.is_sqlite()
        else " ON DUPLICATE KEY UPDATE allow_test_calls=VALUES(allow_test_calls), recording_enabled=VALUES(recording_enabled), notify_on_failure=VALUES(notify_on_failure), updated_by=VALUES(updated_by)"
    )
    await db.execute(
        f"{verb} INTO voice_monitor_preferences (company_id, allow_test_calls, recording_enabled, notify_on_failure, updated_by) VALUES (%s,%s,%s,%s,%s){suffix}",
        (
            company_id,
            values["allow_test_calls"],
            values["recording_enabled"],
            values["notify_on_failure"],
            user_id,
        ),
    )
    return await get_preferences(company_id)


async def create_manual_attempt(
    company_id: int,
    endpoint_id: int,
    user_id: int,
    token: str,
    *,
    user_limit: int,
    company_limit: int,
) -> tuple[dict[str, Any], bool]:
    """Enqueue a call only when its endpoint has an active, tenant-owned subscription."""
    existing = await db.fetch_one(
        "SELECT * FROM voice_monitor_attempts WHERE company_id=%s AND initiated_by_user_id=%s AND request_idempotency_token=%s",
        (company_id, user_id, token),
    )
    if existing:
        return existing, False
    endpoint = await db.fetch_one("SELECT * FROM voice_monitor_endpoints WHERE id=%s AND company_id=%s AND enabled=1", (endpoint_id, company_id))
    if not endpoint:
        raise ValueError("number is not provisioned under an active subscription")
    from app.services.voice_monitor_billing import get_entitlement
    await get_entitlement(endpoint["subscription_id"], company_id=company_id)
    counts = (
        await db.fetch_one(
            "SELECT COUNT(*) AS company_count, SUM(CASE WHEN initiated_by_user_id=%s THEN 1 ELSE 0 END) AS user_count "
            "FROM voice_monitor_attempts WHERE company_id=%s AND initiated_by_user_id IS NOT NULL AND queued_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 1 HOUR)",
            (user_id, company_id),
        )
        or {}
    )
    if (
        int(counts.get("user_count") or 0) >= user_limit
        or int(counts.get("company_count") or 0) >= company_limit
    ):
        raise OverflowError("manual call rate limit exceeded")
    attempt_id = await db.execute_returning_lastrowid(
        "INSERT INTO voice_monitor_attempts (endpoint_id, company_id, initiated_by_user_id, request_idempotency_token, provider_idempotency_key) VALUES (%s,%s,%s,%s,%s)",
        (
            endpoint_id,
            company_id,
            user_id,
            token,
            hashlib.sha256(
                f"manual:{company_id}:{user_id}:{token}".encode()
            ).hexdigest(),
        ),
    )
    return (await get_attempt(company_id, attempt_id)) or {"id": attempt_id}, True


async def transition_attempt(
    company_id: int, attempt_id: int, from_status: str, to_status: str, **result: Any
) -> bool:
    """Atomically apply a valid state transition and immutable result fields."""
    if to_status not in ATTEMPT_STATES or to_status not in TRANSITIONS.get(
        from_status, set()
    ):
        raise ValueError(f"invalid attempt transition: {from_status} -> {to_status}")
    allowed = {
        "started_at",
        "answered_at",
        "completed_at",
        "provider_response_code",
        "provider_call_id",
        "failure_category",
        "failure_detail",
        "duration_seconds",
        "media_artifact_reference",
        "transcript_status",
        "transcript_text_reference",
        "retry_count",
    }
    fields = [field for field in allowed if field in result]
    if "failure_detail" in result and result["failure_detail"] is not None:
        # Persist a bounded, single-line diagnostic rather than raw provider
        # payloads; callers must never pass credentials in this field.
        result["failure_detail"] = " ".join(str(result["failure_detail"]).split())[
            :1000
        ]
    if to_status in TERMINAL_STATES and "completed_at" not in result:
        result["completed_at"] = datetime.now(timezone.utc).replace(tzinfo=None)
        fields.append("completed_at")
    assignments = ["outcome_status = %s", *(f"{field} = %s" for field in fields)]
    params = [
        to_status,
        *(result[field] for field in fields),
        attempt_id,
        company_id,
        from_status,
    ]
    return bool(
        await db.execute_rowcount(
            f"UPDATE voice_monitor_attempts SET {', '.join(assignments)} "
            "WHERE id = %s AND company_id = %s AND outcome_status = %s",
            tuple(params),
        )
    )


async def link_ticket_once(company_id: int, attempt_id: int, ticket_id: int) -> bool:
    """Atomically set the first ticket link, preventing duplicate ticket creation."""
    return bool(
        await db.execute_rowcount(
            "UPDATE voice_monitor_attempts SET created_ticket_id = %s "
            "WHERE id = %s AND company_id = %s AND created_ticket_id IS NULL",
            (ticket_id, attempt_id, company_id),
        )
    )


def _mask_destination(value: Any) -> str:
    """Mask a destination while retaining enough suffix digits for support."""
    raw = str(value or "")
    digits = "".join(character for character in raw if character.isdigit())
    return f"***{digits[-4:]}" if digits else "Unavailable"


async def get_report_summary(
    company_id: int, *, start: datetime, end: datetime
) -> dict[str, Any]:
    """Return tenant-scoped operational totals for a half-open report period."""
    row = await db.fetch_one(
        "SELECT COUNT(*) attempts, "
        "SUM(CASE WHEN a.outcome_status='passed' THEN 1 ELSE 0 END) successful_connections, "
        "SUM(CASE WHEN a.outcome_status IN ('failed','timed_out','exhausted') THEN 1 ELSE 0 END) failures, "
        "AVG(CASE WHEN a.answered_at IS NOT NULL THEN TIMESTAMPDIFF(MICROSECOND,a.started_at,a.answered_at)/1000000 END) avg_answer_seconds, "
        "AVG(a.duration_seconds) avg_duration_seconds "
        "FROM voice_monitor_attempts a WHERE a.company_id=%s AND a.queued_at >= %s AND a.queued_at < %s",
        (company_id, start, end),
    ) or {}
    attempts = int(row.get("attempts") or 0)
    successful = int(row.get("successful_connections") or 0)
    categories = await db.fetch_all(
        "SELECT COALESCE(failure_category,'unknown') category, COUNT(*) count "
        "FROM voice_monitor_attempts WHERE company_id=%s AND queued_at >= %s AND queued_at < %s "
        "AND outcome_status IN ('failed','timed_out','exhausted') "
        "GROUP BY COALESCE(failure_category,'unknown') ORDER BY count DESC, category",
        (company_id, start, end),
    )
    return {
        "period_start": start.isoformat(), "period_end": end.isoformat(),
        "attempts": attempts, "successful_connections": successful,
        "failures": int(row.get("failures") or 0),
        "availability_percentage": round(successful * 100 / attempts, 1) if attempts else 0.0,
        "failure_categories": [dict(item) for item in categories or []],
        "avg_answer_seconds": float(row["avg_answer_seconds"]) if row.get("avg_answer_seconds") is not None else None,
        "avg_duration_seconds": float(row["avg_duration_seconds"]) if row.get("avg_duration_seconds") is not None else None,
    }


async def get_report_detail(
    company_id: int, *, start: datetime, end: datetime
) -> dict[str, Any]:
    """Return safe detail rows; both attempt and endpoint ownership are enforced."""
    summary = await get_report_summary(company_id, start=start, end=end)
    rows = await db.fetch_all(
        "SELECT a.id,a.queued_at,a.answered_at,a.completed_at,a.outcome_status,a.failure_category,"
        "a.provider_response_code,a.provider_call_id,a.duration_seconds,a.retry_count,a.transcript_status,"
        "e.display_label,e.destination_e164 "
        "FROM voice_monitor_attempts a LEFT JOIN voice_monitor_endpoints e "
        "ON e.id=a.endpoint_id AND e.company_id=a.company_id "
        "WHERE a.company_id=%s AND a.queued_at >= %s AND a.queued_at < %s "
        "ORDER BY a.queued_at DESC,a.id DESC LIMIT 250",
        (company_id, start, end),
    )
    attempts = []
    affected: dict[tuple[Any, str], dict[str, Any]] = {}
    for source in rows or []:
        item = dict(source)
        item["masked_destination"] = _mask_destination(item.pop("destination_e164", None))
        # Reports expose correlation identifiers, never media, diagnostics, or transcript text.
        attempts.append(item)
        if item.get("outcome_status") in {"failed", "timed_out", "exhausted"}:
            key = (item.get("display_label"), item["masked_destination"])
            affected.setdefault(key, {"label": key[0], "masked_destination": key[1], "failures": 0})["failures"] += 1
    return {**summary, "attempt_rows": attempts, "recent_incidents": [a for a in attempts if a.get("outcome_status") in {"failed", "timed_out", "exhausted"}][:10], "affected_destinations": list(affected.values()), "packet_quality": None, "media_quality": None}


async def record_incident_failure(
    company_id: int, endpoint_id: int, attempt_id: int, threshold: int
) -> dict[str, Any] | None:
    """Increment failure state and atomically reserve threshold ticket creation."""
    verb = "INSERT OR IGNORE" if db.is_sqlite() else "INSERT IGNORE"
    await db.execute_rowcount(
        f"{verb} INTO voice_monitor_incidents (company_id,endpoint_id) VALUES (%s,%s)",
        (company_id, endpoint_id),
    )
    await db.execute_rowcount(
        "UPDATE voice_monitor_incidents SET consecutive_failures=consecutive_failures+1, "
        "ticket_claim_attempt_id=CASE WHEN ticket_id IS NULL AND ticket_claim_attempt_id IS NULL "
        "AND consecutive_failures+1 >= %s THEN %s ELSE ticket_claim_attempt_id END, recovered_at=NULL "
        "WHERE company_id=%s AND endpoint_id=%s",
        (max(1, threshold), attempt_id, company_id, endpoint_id),
    )
    return await db.fetch_one(
        "SELECT * FROM voice_monitor_incidents WHERE company_id=%s AND endpoint_id=%s",
        (company_id, endpoint_id),
    )


async def complete_incident_ticket_claim(
    company_id: int, endpoint_id: int, attempt_id: int, ticket_id: int
) -> bool:
    return bool(await db.execute_rowcount(
        "UPDATE voice_monitor_incidents SET ticket_id=%s,ticket_claim_attempt_id=NULL,opened_at=CURRENT_TIMESTAMP "
        "WHERE company_id=%s AND endpoint_id=%s AND ticket_id IS NULL AND ticket_claim_attempt_id=%s",
        (ticket_id, company_id, endpoint_id, attempt_id),
    ))


async def release_incident_ticket_claim(company_id: int, endpoint_id: int, attempt_id: int) -> None:
    await db.execute_rowcount(
        "UPDATE voice_monitor_incidents SET ticket_claim_attempt_id=NULL WHERE company_id=%s AND endpoint_id=%s AND ticket_claim_attempt_id=%s",
        (company_id, endpoint_id, attempt_id),
    )


async def recover_incident(company_id: int, endpoint_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT * FROM voice_monitor_incidents WHERE company_id=%s AND endpoint_id=%s",
        (company_id, endpoint_id),
    )
    await db.execute_rowcount(
        "UPDATE voice_monitor_incidents SET consecutive_failures=0,ticket_id=NULL,ticket_claim_attempt_id=NULL,recovered_at=CURRENT_TIMESTAMP "
        "WHERE company_id=%s AND endpoint_id=%s",
        (company_id, endpoint_id),
    )
    return row
