"""Tenant-safe persistence operations for voice monitoring."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.database import db


ENDPOINT_FIELDS = (
    "subscription_id", "destination_e164", "display_label", "enabled", "timezone",
    "schedule_cron", "interval_seconds", "timeout_seconds", "max_retries",
    "retry_delay_seconds", "expected_behavior", "transcription_enabled",
    "ticket_on_failure", "ticket_failure_threshold", "next_run_at",
)
TERMINAL_STATES = {"passed", "failed", "timed_out", "cancelled"}
ATTEMPT_STATES = {"queued", "dialing", "answered", *TERMINAL_STATES}
TRANSITIONS = {
    "queued": {"dialing", "cancelled"},
    "dialing": {"answered", "failed", "timed_out", "cancelled"},
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
