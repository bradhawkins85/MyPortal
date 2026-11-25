"""Where an RMM script is linked: the raw rows behind the Scripts page's *Usage* section.

Every query reads one place that can run a script (schedules, onboarding,
checks, event log rules, script result rules, ticket automations, tray menus,
health check fixes and remote control activation). Places that keep their
actions as JSON (checks, event log rules, automations, tray menus) are
returned whole so :mod:`app.services.rmm_script_usage` can look inside them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.database import db


async def list_schedules(script_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT s.id, s.name, s.company_id, s.target_mode, s.asset_ids_json, s.cron, s.is_enabled, "
        "s.next_run_at, s.run_once_at, s.last_run_at, c.name AS company_name "
        "FROM rmm_schedules s LEFT JOIN companies c ON c.id = s.company_id "
        "WHERE s.script_id = %s ORDER BY s.name, s.id",
        (script_id,),
    )
    return [dict(row) for row in rows or []]


async def list_onboarding_steps(script_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT o.id, o.company_id, o.position, c.name AS company_name "
        "FROM rmm_onboarding_steps o LEFT JOIN companies c ON c.id = o.company_id "
        "WHERE o.script_id = %s ORDER BY c.name, o.position, o.id",
        (script_id,),
    )
    return [dict(row) for row in rows or []]


async def list_checks(script_id: int) -> list[dict[str, Any]]:
    """Checks that test with or act on ``script_id``, plus every check with listed actions."""

    rows = await db.fetch_all(
        "SELECT c.id, c.name, c.company_id, c.asset_id, c.is_enabled, c.action, c.script_id, c.check_script_id, "
        "c.actions_json, a.name AS asset_name, COALESCE(c.company_id, a.company_id) AS owner_company_id, "
        "co.name AS company_name, ac.name AS asset_company_name "
        "FROM rmm_checks c LEFT JOIN assets a ON a.id = c.asset_id "
        "LEFT JOIN companies co ON co.id = c.company_id LEFT JOIN companies ac ON ac.id = a.company_id "
        "WHERE c.script_id = %s OR c.check_script_id = %s OR c.actions_json IS NOT NULL ORDER BY c.name, c.id",
        (script_id, script_id),
    )
    return [dict(row) for row in rows or []]


async def list_event_log_rules(script_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT r.id, r.name, r.company_id, r.asset_id, r.is_enabled, r.action, r.script_id, r.actions_json, "
        "r.last_matched_at, a.name AS asset_name, COALESCE(r.company_id, a.company_id) AS owner_company_id, "
        "c.name AS company_name, ac.name AS asset_company_name "
        "FROM rmm_event_log_rules r LEFT JOIN assets a ON a.id = r.asset_id "
        "LEFT JOIN companies c ON c.id = r.company_id LEFT JOIN companies ac ON ac.id = a.company_id "
        "WHERE r.script_id = %s OR r.actions_json IS NOT NULL ORDER BY r.name, r.id",
        (script_id,),
    )
    return [dict(row) for row in rows or []]


async def list_result_rules(script_id: int) -> list[dict[str, Any]]:
    """Script result rules that run ``script_id`` after another script finishes."""

    rows = await db.fetch_all(
        "SELECT r.id, r.name, r.company_id, r.script_id, r.is_enabled, r.last_matched_at, "
        "s.name AS watched_script_name, c.name AS company_name "
        "FROM rmm_script_result_rules r LEFT JOIN rmm_scripts s ON s.id = r.script_id "
        "LEFT JOIN companies c ON c.id = r.company_id "
        "WHERE r.next_script_id = %s AND r.action = %s ORDER BY r.name, r.id",
        (script_id, "run_script"),
    )
    return [dict(row) for row in rows or []]


async def list_health_check_fixes(script_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT r.id, r.detection_key, r.company_id, r.is_enabled, r.last_run_at, c.name AS company_name "
        "FROM ai_health_check_remediations r LEFT JOIN companies c ON c.id = r.company_id "
        "WHERE r.script_id = %s ORDER BY r.detection_key, r.id",
        (script_id,),
    )
    return [dict(row) for row in rows or []]


async def list_remote_control_providers(script_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT provider, is_enabled FROM rmm_remote_control_providers WHERE activation_script_id = %s "
        "ORDER BY provider",
        (script_id,),
    )
    return [dict(row) for row in rows or []]


async def list_script_automations() -> list[dict[str, Any]]:
    """Automations whose action, or one of whose listed actions, may be the RMM script action."""

    rows = await db.fetch_all(
        "SELECT id, name, kind, status, trigger_event, action_module, action_payload FROM automations "
        "WHERE action_module = %s OR action_payload LIKE %s ORDER BY name, id",
        ("rmm-script", "%rmm-script%"),
    )
    return [dict(row) for row in rows or []]


async def list_tray_menus() -> list[dict[str, Any]]:
    """Tray menus that may hold a script item (their items are checked by the service)."""

    rows = await db.fetch_all(
        "SELECT t.id, t.name, t.scope, t.scope_ref_id, t.enabled, t.payload_json, "
        "CASE WHEN t.scope = 'company' THEN t.scope_ref_id WHEN t.scope = 'device' THEN a.company_id END "
        "AS owner_company_id, c.name AS company_name, a.name AS asset_name "
        "FROM tray_menu_configs t "
        "LEFT JOIN assets a ON t.scope = 'device' AND a.id = t.scope_ref_id "
        "LEFT JOIN companies c ON c.id = CASE WHEN t.scope = 'company' THEN t.scope_ref_id "
        "WHEN t.scope = 'device' THEN a.company_id END "
        "WHERE t.payload_json LIKE %s ORDER BY t.name, t.id",
        ("%rmm_script%",),
    )
    return [dict(row) for row in rows or []]


async def run_activity(script_id: int, *, company_id: int | None, since: datetime) -> dict[str, Any]:
    """How often ``script_id`` ran since ``since`` and when it last ran, for one company or all."""

    if company_id is None:
        row = await db.fetch_one(
            "SELECT MAX(queued_at) AS last_run_at, "
            "SUM(CASE WHEN queued_at >= %s THEN 1 ELSE 0 END) AS recent_runs "
            "FROM rmm_script_runs WHERE script_id = %s",
            (since, script_id),
        )
    else:
        row = await db.fetch_one(
            "SELECT MAX(queued_at) AS last_run_at, "
            "SUM(CASE WHEN queued_at >= %s THEN 1 ELSE 0 END) AS recent_runs "
            "FROM rmm_script_runs WHERE script_id = %s AND company_id = %s",
            (since, script_id, company_id),
        )
    row = dict(row or {})
    return {"last_run_at": row.get("last_run_at"), "recent_runs": int(row.get("recent_runs") or 0)}
