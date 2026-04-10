from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable

import aiomysql
import aiosqlite

from app.core.database import db


DEFAULT_WORKFLOW_KEY = "staff_onboarding_m365"
DEFAULT_OFFBOARDING_WORKFLOW_KEY = "staff_offboarding_m365"
DIRECTION_ONBOARDING = "onboarding"
DIRECTION_OFFBOARDING = "offboarding"


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json_default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable in workflow payload")

def _deserialise_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _normalise_execution(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    record = dict(row)
    for key in ("id", "company_id", "staff_id", "retries_used", "helpdesk_ticket_id"):
        if record.get(key) is not None:
            record[key] = int(record[key])
    if record.get("direction") is None:
        record["direction"] = DIRECTION_ONBOARDING
    return record


def _normalise_idempotency(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    record = dict(row)
    for key in ("id", "api_key_id", "company_id", "staff_id", "response_status"):
        if record.get(key) is not None:
            record[key] = int(record[key])
    response_payload_raw = record.get("response_payload_json")
    if isinstance(response_payload_raw, str) and response_payload_raw.strip():
        try:
            record["response_payload"] = json.loads(response_payload_raw)
        except json.JSONDecodeError:
            record["response_payload"] = {}
    else:
        record["response_payload"] = {}
    return record


def _normalise_policy(row: dict[str, Any] | None, *, default_workflow_key: str = DEFAULT_WORKFLOW_KEY) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "id": int(row["id"]) if row.get("id") is not None else None,
        "company_id": int(row["company_id"]),
        "direction": str(row.get("direction") or DIRECTION_ONBOARDING),
        "workflow_key": str(row.get("workflow_key") or default_workflow_key),
        "workflow_name": str(row.get("workflow_name") or "").strip() or None,
        "delay_type": str(row.get("delay_type") or "scheduled").strip().lower() or "scheduled",
        "sort_order": int(row.get("sort_order") or 0),
        "is_enabled": bool(int(row.get("is_enabled") or 0)),
        "max_retries": max(0, int(row.get("max_retries") or 0)),
        "config": _deserialise_json(row.get("config_json")),
    }


async def list_company_workflow_policies(
    company_id: int,
    *,
    direction: str = DIRECTION_ONBOARDING,
    enabled_only: bool = False,
) -> list[dict[str, Any]]:
    if enabled_only:
        rows = await db.fetch_all(
            """
            SELECT *
            FROM company_onboarding_workflow_policies
            WHERE company_id = %s AND direction = %s AND is_enabled = 1
            ORDER BY sort_order ASC, id ASC
            """,
            (company_id, direction),
        )
    else:
        rows = await db.fetch_all(
            """
            SELECT *
            FROM company_onboarding_workflow_policies
            WHERE company_id = %s AND direction = %s
            ORDER BY sort_order ASC, id ASC
            """,
            (company_id, direction),
        )
    default_key = DEFAULT_WORKFLOW_KEY if direction == DIRECTION_ONBOARDING else DEFAULT_OFFBOARDING_WORKFLOW_KEY
    return [_normalise_policy(dict(row), default_workflow_key=default_key) for row in rows if row]  # type: ignore[misc]


async def get_company_workflow_policy_by_id(
    policy_id: int,
    company_id: int,
) -> dict[str, Any] | None:
    row = await db.fetch_one(
        """
        SELECT *
        FROM company_onboarding_workflow_policies
        WHERE id = %s AND company_id = %s
        LIMIT 1
        """,
        (policy_id, company_id),
    )
    if not row:
        return None
    default_key = DEFAULT_WORKFLOW_KEY if str(row.get("direction") or "") == DIRECTION_ONBOARDING else DEFAULT_OFFBOARDING_WORKFLOW_KEY
    return _normalise_policy(dict(row), default_workflow_key=default_key)


async def get_company_workflow_policy_by_key(
    company_id: int,
    workflow_key: str,
    direction: str = DIRECTION_ONBOARDING,
) -> dict[str, Any] | None:
    row = await db.fetch_one(
        """
        SELECT *
        FROM company_onboarding_workflow_policies
        WHERE company_id = %s AND direction = %s AND workflow_key = %s
        LIMIT 1
        """,
        (company_id, direction, workflow_key),
    )
    if not row:
        return None
    default_key = DEFAULT_WORKFLOW_KEY if direction == DIRECTION_ONBOARDING else DEFAULT_OFFBOARDING_WORKFLOW_KEY
    return _normalise_policy(dict(row), default_workflow_key=default_key)


async def get_company_workflow_policy(
    company_id: int,
    *,
    default_workflow_key: str = DEFAULT_WORKFLOW_KEY,
    direction: str = DIRECTION_ONBOARDING,
) -> dict[str, Any]:
    """Return the first (primary) workflow policy for a company and direction.

    Falls back to a synthetic default policy when none is configured.
    This function is retained for backward compatibility with callers that
    expect a single policy dict.
    """
    company = await db.fetch_one(
        "SELECT company_onboarding_workflow_id FROM companies WHERE id = %s",
        (company_id,),
    )
    policy = await db.fetch_one(
        """
        SELECT *
        FROM company_onboarding_workflow_policies
        WHERE company_id = %s AND direction = %s
        ORDER BY sort_order ASC, id ASC
        LIMIT 1
        """,
        (company_id, direction),
    )
    # Fall back to the legacy company_onboarding_workflow_id field only for onboarding
    if direction == DIRECTION_ONBOARDING:
        workflow_key = str((company or {}).get("company_onboarding_workflow_id") or "").strip() or default_workflow_key
    else:
        workflow_key = default_workflow_key
    if not policy:
        return {
            "company_id": company_id,
            "workflow_key": workflow_key,
            "workflow_name": None,
            "delay_type": "scheduled",
            "sort_order": 0,
            "is_enabled": True,
            "max_retries": 2,
            "config": {},
        }
    normalised = _normalise_policy(dict(policy), default_workflow_key=default_workflow_key)
    if normalised and not normalised.get("workflow_key"):
        normalised["workflow_key"] = workflow_key
    return normalised or {
        "company_id": company_id,
        "workflow_key": workflow_key,
        "workflow_name": None,
        "delay_type": "scheduled",
        "sort_order": 0,
        "is_enabled": True,
        "max_retries": 2,
        "config": {},
    }


async def delete_company_workflow_policy(
    policy_id: int,
    company_id: int,
) -> bool:
    """Delete a workflow policy by ID. Returns True if a row was deleted."""
    row = await db.fetch_one(
        "SELECT id FROM company_onboarding_workflow_policies WHERE id = %s AND company_id = %s",
        (policy_id, company_id),
    )
    if not row:
        return False
    await db.execute(
        "DELETE FROM company_onboarding_workflow_policies WHERE id = %s AND company_id = %s",
        (policy_id, company_id),
    )
    return True


async def upsert_company_workflow_policy(
    *,
    company_id: int,
    workflow_key: str,
    is_enabled: bool,
    max_retries: int,
    config: dict[str, Any] | None,
    default_workflow_key: str = DEFAULT_WORKFLOW_KEY,
    direction: str = DIRECTION_ONBOARDING,
    delay_type: str = "scheduled",
    workflow_name: str | None = None,
    sort_order: int = 0,
) -> dict[str, Any]:
    clean_key = workflow_key.strip()
    if not clean_key:
        existing_policy = await get_company_workflow_policy(
            company_id,
            default_workflow_key=default_workflow_key,
            direction=direction,
        )
        clean_key = str(existing_policy.get("workflow_key") or "").strip() or default_workflow_key
    clean_delay_type = str(delay_type or "scheduled").strip().lower()
    if clean_delay_type not in ("scheduled", "immediate"):
        clean_delay_type = "scheduled"
    # Update the legacy company_onboarding_workflow_id field only for the primary (first) onboarding workflow
    if direction == DIRECTION_ONBOARDING:
        existing = await list_company_workflow_policies(company_id, direction=direction)
        is_first = not existing or (len(existing) == 1 and str(existing[0].get("workflow_key") or "") == clean_key)
        if is_first:
            await db.execute(
                "UPDATE companies SET company_onboarding_workflow_id = %s WHERE id = %s",
                (clean_key, company_id),
            )
    await db.execute(
        """
        INSERT INTO company_onboarding_workflow_policies
            (company_id, direction, workflow_key, workflow_name, delay_type, sort_order, is_enabled, max_retries, config_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            workflow_name = VALUES(workflow_name),
            delay_type = VALUES(delay_type),
            sort_order = VALUES(sort_order),
            is_enabled = VALUES(is_enabled),
            max_retries = VALUES(max_retries),
            config_json = VALUES(config_json)
        """,
        (
            company_id,
            direction,
            clean_key,
            workflow_name or None,
            clean_delay_type,
            max(0, int(sort_order)),
            1 if is_enabled else 0,
            max(0, int(max_retries)),
            json.dumps(config or {}, default=_json_default, ensure_ascii=False),
        ),
    )
    result = await get_company_workflow_policy_by_key(company_id, clean_key, direction)
    if result:
        return result
    return await get_company_workflow_policy(
        company_id,
        default_workflow_key=default_workflow_key,
        direction=direction,
    )


async def create_or_reset_execution(
    *,
    company_id: int,
    staff_id: int,
    workflow_key: str,
    direction: str = DIRECTION_ONBOARDING,
    scheduled_for_utc: datetime | None = None,
    requested_timezone: str | None = None,
) -> dict[str, Any]:
    now = _utc_now_naive()
    await db.execute(
        """
        INSERT INTO staff_onboarding_workflow_executions
            (company_id, staff_id, workflow_key, direction, state, current_step, retries_used, last_error, helpdesk_ticket_id, requested_at, scheduled_for_utc, requested_timezone, started_at, completed_at)
        VALUES (%s, %s, %s, %s, 'requested', NULL, 0, NULL, NULL, %s, %s, %s, NULL, NULL)
        """,
        (
            company_id,
            staff_id,
            workflow_key,
            direction,
            now,
            scheduled_for_utc,
            requested_timezone,
        ),
    )
    execution = await get_execution_by_staff_id(staff_id)
    if not execution:
        raise RuntimeError("Failed to create workflow execution")
    return execution


async def get_execution_by_id(execution_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        """
        SELECT *
        FROM staff_onboarding_workflow_executions
        WHERE id = %s
        LIMIT 1
        """,
        (execution_id,),
    )
    return _normalise_execution(row)


async def get_execution_by_staff_id(staff_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        """
        SELECT *
        FROM staff_onboarding_workflow_executions
        WHERE staff_id = %s
        ORDER BY requested_at DESC, id DESC
        LIMIT 1
        """,
        (staff_id,),
    )
    return _normalise_execution(row)


async def list_executions_for_staff_ids(staff_ids: Iterable[int]) -> dict[int, dict[str, Any]]:
    ids = [int(item) for item in staff_ids]
    if not ids:
        return {}
    ids_csv = ",".join(str(item) for item in ids)
    rows = await db.fetch_all(
        """
        SELECT *
        FROM staff_onboarding_workflow_executions
        WHERE FIND_IN_SET(staff_id, %s) > 0
        ORDER BY requested_at DESC, id DESC
        """,
        (ids_csv,),
    )
    mapped: dict[int, dict[str, Any]] = {}
    for row in rows:
        item = _normalise_execution(row)
        if item:
            sid = int(item["staff_id"])
            if sid not in mapped:
                mapped[sid] = item
    return mapped


async def list_execution_history_for_staff(
    staff_id: int,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return all historical executions for a staff member, newest first."""
    rows = await db.fetch_all(
        """
        SELECT *
        FROM staff_onboarding_workflow_executions
        WHERE staff_id = %s
        ORDER BY requested_at DESC, id DESC
        LIMIT %s
        """,
        (staff_id, int(limit)),
    )
    return [_normalise_execution(row) for row in rows if row]  # type: ignore[misc]


async def list_recent_executions_for_company(
    company_id: int,
    *,
    limit: int = 100,
    direction: str | None = None,
) -> list[dict[str, Any]]:
    """Return recent workflow executions for all staff in a company, newest first."""
    if direction:
        rows = await db.fetch_all(
            """
            SELECT *
            FROM staff_onboarding_workflow_executions
            WHERE company_id = %s AND direction = %s
            ORDER BY requested_at DESC, id DESC
            LIMIT %s
            """,
            (company_id, direction, int(limit)),
        )
    else:
        rows = await db.fetch_all(
            """
            SELECT *
            FROM staff_onboarding_workflow_executions
            WHERE company_id = %s
            ORDER BY requested_at DESC, id DESC
            LIMIT %s
            """,
            (company_id, int(limit)),
        )
    return [_normalise_execution(row) for row in rows if row]  # type: ignore[misc]


async def update_execution_state(
    execution_id: int,
    *,
    state: str,
    current_step: str | None = None,
    retries_used: int | None = None,
    last_error: str | None = None,
    helpdesk_ticket_id: int | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> None:
    await db.execute(
        """
        UPDATE staff_onboarding_workflow_executions
        SET
            state = %s,
            current_step = %s,
            retries_used = COALESCE(%s, retries_used),
            last_error = %s,
            helpdesk_ticket_id = COALESCE(%s, helpdesk_ticket_id),
            started_at = COALESCE(%s, started_at),
            completed_at = %s
        WHERE id = %s
        """,
        (
            state,
            current_step,
            retries_used,
            last_error,
            helpdesk_ticket_id,
            started_at,
            completed_at,
            execution_id,
        ),
    )


async def claim_next_due_approved_execution(*, now_utc: datetime | None = None) -> dict[str, Any] | None:
    due_at = now_utc or _utc_now_naive()
    due_states = ("approved", "offboarding_approved")
    if db.is_sqlite():
        row = await db.fetch_one(
            """
            SELECT *
            FROM staff_onboarding_workflow_executions
            WHERE state IN (?, ?)
              AND (scheduled_for_utc IS NULL OR scheduled_for_utc <= ?)
            ORDER BY COALESCE(scheduled_for_utc, requested_at) ASC, id ASC
            LIMIT 1
            """,
            (due_states[0], due_states[1], due_at),
        )
        if not row:
            return None
        await db.execute(
            """
            UPDATE staff_onboarding_workflow_executions
            SET state = 'requested', current_step = 'queued'
            WHERE id = ?
              AND state IN (?, ?)
            """,
            (row["id"], due_states[0], due_states[1]),
        )
        refreshed = await db.fetch_one(
            "SELECT * FROM staff_onboarding_workflow_executions WHERE id = ?",
            (row["id"],),
        )
        return _normalise_execution(refreshed)

    async with db.acquire() as conn:
        await conn.begin()
        try:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    """
                    SELECT *
                    FROM staff_onboarding_workflow_executions
                    WHERE state IN (%s, %s)
                      AND (scheduled_for_utc IS NULL OR scheduled_for_utc <= %s)
                    ORDER BY COALESCE(scheduled_for_utc, requested_at) ASC, id ASC
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                    """,
                    (due_states[0], due_states[1], due_at),
                )
                row = await cursor.fetchone()
                if not row:
                    await conn.commit()
                    return None
                await cursor.execute(
                    """
                    UPDATE staff_onboarding_workflow_executions
                    SET state = 'requested', current_step = 'queued'
                    WHERE id = %s
                    """,
                    (row["id"],),
                )
            await conn.commit()
            claimed = dict(row)
            claimed["state"] = "requested"
            claimed["current_step"] = "queued"
            return _normalise_execution(claimed)
        except Exception:
            await conn.rollback()
            raise


async def claim_next_paused_license_execution(
    *,
    now_utc: datetime | None = None,
    company_id: int | None = None,
) -> dict[str, Any] | None:
    paused_state = "paused_license_unavailable"
    resumed_at = now_utc or _utc_now_naive()
    if db.is_sqlite():
        where_company = "AND company_id = ?" if company_id is not None else ""
        query_params: list[Any] = [paused_state]
        if company_id is not None:
            query_params.append(int(company_id))
        row = await db.fetch_one(
            f"""
            SELECT *
            FROM staff_onboarding_workflow_executions
            WHERE state = ?
              {where_company}
            ORDER BY requested_at ASC, id ASC
            LIMIT 1
            """,
            tuple(query_params),
        )
        if not row:
            return None
        await db.execute(
            """
            UPDATE staff_onboarding_workflow_executions
            SET state = 'requested', current_step = 'resuming_after_capacity_change', last_error = NULL, started_at = ?
            WHERE id = ?
              AND state = ?
            """,
            (resumed_at, row["id"], paused_state),
        )
        refreshed = await db.fetch_one(
            "SELECT * FROM staff_onboarding_workflow_executions WHERE id = ?",
            (row["id"],),
        )
        return _normalise_execution(refreshed)

    async with db.acquire() as conn:
        await conn.begin()
        try:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                if company_id is not None:
                    await cursor.execute(
                        """
                        SELECT *
                        FROM staff_onboarding_workflow_executions
                        WHERE state = %s
                          AND company_id = %s
                        ORDER BY requested_at ASC, id ASC
                        LIMIT 1
                        FOR UPDATE SKIP LOCKED
                        """,
                        (paused_state, int(company_id)),
                    )
                else:
                    await cursor.execute(
                        """
                        SELECT *
                        FROM staff_onboarding_workflow_executions
                        WHERE state = %s
                        ORDER BY requested_at ASC, id ASC
                        LIMIT 1
                        FOR UPDATE SKIP LOCKED
                        """,
                        (paused_state,),
                    )
                row = await cursor.fetchone()
                if not row:
                    await conn.commit()
                    return None
                await cursor.execute(
                    """
                    UPDATE staff_onboarding_workflow_executions
                    SET state = 'requested', current_step = 'resuming_after_capacity_change', last_error = NULL, started_at = %s
                    WHERE id = %s
                    """,
                    (resumed_at, row["id"]),
                )
            await conn.commit()
            claimed = dict(row)
            claimed["state"] = "requested"
            claimed["current_step"] = "resuming_after_capacity_change"
            claimed["last_error"] = None
            claimed["started_at"] = resumed_at
            return _normalise_execution(claimed)
        except Exception:
            await conn.rollback()
            raise


async def append_step_log(
    *,
    execution_id: int,
    step_name: str,
    status: str,
    attempt: int,
    request_payload: dict[str, Any] | None = None,
    response_payload: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> None:
    await db.execute(
        """
        INSERT INTO staff_onboarding_workflow_step_logs
            (execution_id, step_name, status, attempt, request_payload, response_payload, error_message, started_at, completed_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            execution_id,
            step_name,
            status,
            attempt,
            json.dumps(request_payload or {}, default=_json_default, ensure_ascii=False),
            json.dumps(response_payload or {}, default=_json_default, ensure_ascii=False),
            error_message,
            _utc_now_naive(),
            _utc_now_naive(),
        ),
    )


async def list_step_logs_for_execution_ids(
    execution_ids: Iterable[int],
) -> dict[int, list[dict[str, Any]]]:
    ids = [int(item) for item in execution_ids]
    if not ids:
        return {}
    ids_csv = ",".join(str(item) for item in ids)
    rows = await db.fetch_all(
        """
        SELECT *
        FROM staff_onboarding_workflow_step_logs
        WHERE FIND_IN_SET(execution_id, %s) > 0
        ORDER BY started_at ASC, id ASC
        """,
        (ids_csv,),
    )
    mapped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        execution_id = int(row.get("execution_id") or 0)
        if execution_id <= 0:
            continue
        mapped.setdefault(execution_id, []).append(dict(row))
    return mapped


async def create_external_checkpoint(
    *,
    execution_id: int,
    company_id: int,
    staff_id: int,
    confirmation_token_hash: str,
) -> dict[str, Any]:
    await db.execute(
        """
        INSERT INTO staff_onboarding_external_checkpoints
            (execution_id, company_id, staff_id, confirmation_token_hash, status, created_at, updated_at)
        VALUES (%s, %s, %s, %s, 'pending', %s, %s)
        """,
        (
            execution_id,
            company_id,
            staff_id,
            confirmation_token_hash,
            _utc_now_naive(),
            _utc_now_naive(),
        ),
    )
    row = await db.fetch_one(
        """
        SELECT *
        FROM staff_onboarding_external_checkpoints
        WHERE execution_id = %s
        ORDER BY id DESC
        LIMIT 1
        """,
        (execution_id,),
    )
    return dict(row or {})


async def get_pending_external_checkpoint(
    *,
    execution_id: int,
    company_id: int,
    staff_id: int,
    confirmation_token_hash: str,
) -> dict[str, Any] | None:
    row = await db.fetch_one(
        """
        SELECT *
        FROM staff_onboarding_external_checkpoints
        WHERE execution_id = %s
          AND company_id = %s
          AND staff_id = %s
          AND confirmation_token_hash = %s
          AND status = 'pending'
        ORDER BY id DESC
        LIMIT 1
        """,
        (execution_id, company_id, staff_id, confirmation_token_hash),
    )
    return dict(row) if row else None


async def list_external_checkpoints_for_execution_ids(
    execution_ids: Iterable[int],
) -> dict[int, list[dict[str, Any]]]:
    ids = [int(item) for item in execution_ids]
    if not ids:
        return {}
    ids_csv = ",".join(str(item) for item in ids)
    rows = await db.fetch_all(
        """
        SELECT *
        FROM staff_onboarding_external_checkpoints
        WHERE FIND_IN_SET(execution_id, %s) > 0
        ORDER BY created_at ASC, id ASC
        """,
        (ids_csv,),
    )
    mapped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        execution_id = int(row.get("execution_id") or 0)
        if execution_id <= 0:
            continue
        mapped.setdefault(execution_id, []).append(dict(row))
    return mapped


async def confirm_external_checkpoint(
    checkpoint_id: int,
    *,
    source: str,
    callback_timestamp: datetime,
    proof_reference_id: str | None,
    payload_hash: str | None,
    callback_payload: dict[str, Any] | None,
    confirmed_by_api_key_id: int,
) -> None:
    await db.execute(
        """
        UPDATE staff_onboarding_external_checkpoints
        SET
            status = 'confirmed',
            source = %s,
            callback_timestamp = %s,
            proof_reference_id = %s,
            payload_hash = %s,
            callback_payload_json = %s,
            confirmed_by_api_key_id = %s,
            confirmed_at = %s,
            updated_at = %s
        WHERE id = %s
        """,
        (
            source,
            callback_timestamp,
            proof_reference_id,
            payload_hash,
            json.dumps(callback_payload or {}, default=_json_default, ensure_ascii=False),
            confirmed_by_api_key_id,
            _utc_now_naive(),
            _utc_now_naive(),
            checkpoint_id,
        ),
    )


async def try_create_external_confirmation_idempotency(
    *,
    api_key_id: int,
    idempotency_key: str,
    request_fingerprint: str,
    company_id: int,
    staff_id: int,
) -> bool:
    try:
        await db.execute(
            """
            INSERT INTO staff_onboarding_external_confirmation_idempotency
                (api_key_id, idempotency_key, request_fingerprint, company_id, staff_id, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                api_key_id,
                idempotency_key,
                request_fingerprint,
                company_id,
                staff_id,
                _utc_now_naive(),
                _utc_now_naive(),
            ),
        )
    except (aiomysql.IntegrityError, aiosqlite.IntegrityError):
        return False
    return True


async def get_external_confirmation_idempotency(
    *,
    api_key_id: int,
    idempotency_key: str,
) -> dict[str, Any] | None:
    row = await db.fetch_one(
        """
        SELECT *
        FROM staff_onboarding_external_confirmation_idempotency
        WHERE api_key_id = %s
          AND idempotency_key = %s
        LIMIT 1
        """,
        (api_key_id, idempotency_key),
    )
    return _normalise_idempotency(row)


async def finalize_external_confirmation_idempotency(
    *,
    api_key_id: int,
    idempotency_key: str,
    response_status: int,
    response_payload: dict[str, Any],
) -> None:
    await db.execute(
        """
        UPDATE staff_onboarding_external_confirmation_idempotency
        SET
            response_status = %s,
            response_payload_json = %s,
            updated_at = %s
        WHERE api_key_id = %s
          AND idempotency_key = %s
        """,
        (
            int(response_status),
            json.dumps(response_payload or {}, default=_json_default, ensure_ascii=False),
            _utc_now_naive(),
            api_key_id,
            idempotency_key,
        ),
    )


async def get_kid_friendly_words() -> list[str]:
    """Return all words from the kid-friendly word table.

    Words are stored exclusively in the database (migration 199) and are
    never returned by any API or UI route — the only access path is through
    this function.  The caller is expected to cache the result for the
    lifetime of the process so that this query is only executed once.
    """
    rows = await db.fetch_all(
        "SELECT word FROM workflow_kid_friendly_words ORDER BY id",
        (),
    )
    return [str(row["word"]) for row in rows if row.get("word")]
