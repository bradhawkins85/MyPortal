from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable

from app.core.database import db


DEFAULT_WORKFLOW_KEY = "staff_onboarding_m365"


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
    return record


async def get_company_workflow_policy(company_id: int) -> dict[str, Any]:
    company = await db.fetch_one(
        "SELECT company_onboarding_workflow_id FROM companies WHERE id = %s",
        (company_id,),
    )
    policy = await db.fetch_one(
        """
        SELECT *
        FROM company_onboarding_workflow_policies
        WHERE company_id = %s
        """,
        (company_id,),
    )
    workflow_key = str((company or {}).get("company_onboarding_workflow_id") or "").strip() or DEFAULT_WORKFLOW_KEY
    if not policy:
        return {
            "company_id": company_id,
            "workflow_key": workflow_key,
            "is_enabled": True,
            "max_retries": 2,
            "config": {},
        }
    return {
        "company_id": int(policy["company_id"]),
        "workflow_key": str(policy.get("workflow_key") or workflow_key or DEFAULT_WORKFLOW_KEY),
        "is_enabled": bool(int(policy.get("is_enabled") or 0)),
        "max_retries": max(0, int(policy.get("max_retries") or 0)),
        "config": _deserialise_json(policy.get("config_json")),
    }


async def upsert_company_workflow_policy(
    *,
    company_id: int,
    workflow_key: str,
    is_enabled: bool,
    max_retries: int,
    config: dict[str, Any] | None,
) -> dict[str, Any]:
    clean_key = workflow_key.strip() or DEFAULT_WORKFLOW_KEY
    await db.execute(
        "UPDATE companies SET company_onboarding_workflow_id = %s WHERE id = %s",
        (clean_key, company_id),
    )
    await db.execute(
        """
        INSERT INTO company_onboarding_workflow_policies
            (company_id, workflow_key, is_enabled, max_retries, config_json)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            workflow_key = VALUES(workflow_key),
            is_enabled = VALUES(is_enabled),
            max_retries = VALUES(max_retries),
            config_json = VALUES(config_json)
        """,
        (
            company_id,
            clean_key,
            1 if is_enabled else 0,
            max(0, int(max_retries)),
            json.dumps(config or {}, ensure_ascii=False),
        ),
    )
    return await get_company_workflow_policy(company_id)


async def create_or_reset_execution(
    *,
    company_id: int,
    staff_id: int,
    workflow_key: str,
) -> dict[str, Any]:
    now = _utc_now_naive()
    await db.execute(
        """
        INSERT INTO staff_onboarding_workflow_executions
            (company_id, staff_id, workflow_key, state, current_step, retries_used, last_error, helpdesk_ticket_id, requested_at, started_at, completed_at)
        VALUES (%s, %s, %s, 'requested', NULL, 0, NULL, NULL, %s, NULL, NULL)
        ON DUPLICATE KEY UPDATE
            company_id = VALUES(company_id),
            workflow_key = VALUES(workflow_key),
            state = 'requested',
            current_step = NULL,
            retries_used = 0,
            last_error = NULL,
            helpdesk_ticket_id = NULL,
            requested_at = VALUES(requested_at),
            started_at = NULL,
            completed_at = NULL
        """,
        (company_id, staff_id, workflow_key, now),
    )
    execution = await get_execution_by_staff_id(staff_id)
    if not execution:
        raise RuntimeError("Failed to create workflow execution")
    await db.execute(
        "DELETE FROM staff_onboarding_workflow_step_logs WHERE execution_id = %s",
        (execution["id"],),
    )
    return execution


async def get_execution_by_staff_id(staff_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        """
        SELECT *
        FROM staff_onboarding_workflow_executions
        WHERE staff_id = %s
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
        """,
        (ids_csv,),
    )
    mapped: dict[int, dict[str, Any]] = {}
    for row in rows:
        item = _normalise_execution(row)
        if item:
            mapped[int(item["staff_id"])] = item
    return mapped


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
            json.dumps(request_payload or {}, ensure_ascii=False),
            json.dumps(response_payload or {}, ensure_ascii=False),
            error_message,
            _utc_now_naive(),
            _utc_now_naive(),
        ),
    )


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
            json.dumps(callback_payload or {}, ensure_ascii=False),
            confirmed_by_api_key_id,
            _utc_now_naive(),
            _utc_now_naive(),
            checkpoint_id,
        ),
    )
