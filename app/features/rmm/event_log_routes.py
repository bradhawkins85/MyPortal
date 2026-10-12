"""RMM event logs (:mod:`app.services.rmm_event_logs`).

Technician side (company scoped, ``menu.rmm_scripts``; write access changes
which logs are collected):

* ``GET /rmm/event-logs`` the company's entries with filters, and the extra
  logs collected for the company and each device
* ``POST /rmm/event-logs/company`` and ``/rmm/event-logs/assets/{id}`` save
  extra logs (one per line)
* ``GET /api/rmm/event-logs`` the same entries as JSON
* each asset page's Event logs and Event log automations sections
  (:func:`asset_event_log_context`)

Event log rules live in :mod:`.event_log_rule_routes`.

Agent side (``Authorization: Bearer`` token):

* ``GET /api/rmm/agent/event-logs/config`` which logs to collect, or
  ``enabled: false`` without the event log database
* ``POST /api/rmm/agent/event-logs`` a batch of entries
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.event_log_database import event_log_db
from app.core.logging import log_warning
from app.repositories import rmm as rmm_repo
from app.repositories import rmm_event_logs as event_log_repo
from app.repositories import rmm_event_log_rules as rules_repo
from app.security.flash import flash_redirect
from app.services import audit as audit_service
from app.services import rmm_event_log_rules as event_log_rules
from app.services import rmm_event_logs as event_logs

from .routes import _assets_routes, _context, _serialise, get_current_rmm_agent

router = APIRouter(tags=["RMM event logs"])

PAGE_PATH = "/rmm/event-logs"
LEVEL_FILTERS = (("", "Any level"), ("3", "Warning or worse"), ("2", "Error or worse"), ("1", "Critical"))


def _entry(row: dict[str, Any]) -> dict[str, Any]:
    entry = _serialise(row) or {}
    entry["level_label"] = event_logs.level_label(row.get("level"))
    return entry


async def _filtered_entries(company_id: int, *, asset_id, log, level, event_id, q, limit) -> list[dict[str, Any]]:
    if not await event_logs.enabled():
        return []
    rows = await event_log_repo.list_entries(
        company_id=company_id,
        asset_id=asset_id,
        log_name=(log or "").strip() or None,
        max_level=level,
        event_id=event_id,
        search=(q or "").strip()[:200] or None,
        limit=limit,
    )
    return [_entry(row) for row in rows]


def _back_path(form: Any, asset_id: int | None) -> str:
    """The asset page when the form came from there, else the Event logs page."""

    if asset_id is not None and str(form.get("return_to") or "") == "asset":
        return f"/assets/{int(asset_id)}#event-logs"
    return PAGE_PATH


def rule_summary(rule: dict[str, Any]) -> dict[str, Any]:
    """A rule as the rules page and the asset page list it."""

    if rule.get("asset_id") is not None:
        scope = f"Device: {rule.get('asset_name') or 'Asset ' + str(rule['asset_id'])}"
    elif rule.get("company_id") is not None:
        scope = f"Company: {rule.get('company_name') or rule['company_id']}"
    else:
        scope = "Every company"
    action = event_log_rules.ACTIONS.get(rule.get("action"), rule.get("action"))
    if rule.get("action") == "run_script":
        action = f"Run {rule.get('script_name') or 'a removed script'}"
    return {
        **(_serialise(rule) or {}),
        "scope_label": scope,
        "action_label": action,
        "conditions_text": [event_log_rules.describe_condition(item) for item in rule.get("conditions") or []],
    }


async def asset_event_log_context(company_id: int, asset_id: int, *, can_edit: bool, is_super_admin: bool) -> dict[str, Any]:
    """The asset page's Event logs and Event log automations sections."""

    database = event_log_db.status()
    try:
        entries = await _filtered_entries(
            company_id, asset_id=asset_id, log=None, level=None, event_id=None, q=None, limit=25)
    except Exception:  # the asset page must not fail with the event log database
        log_warning("Could not read event log entries for the asset page", asset_id=asset_id)
        entries = []
    rules = [
        {**rule_summary(rule), "can_edit": can_edit and event_log_rules.can_edit(
            rule, company_id=company_id, is_super_admin=is_super_admin)}
        for rule in await rules_repo.list_rules_for_device(company_id, asset_id)
    ]
    return {
        "database": {**database, "error": None},
        "entries": entries,
        "default_logs": list(event_logs.DEFAULT_LOGS),
        "company_logs": await event_log_repo.get_extra_logs(company_id=company_id),
        "extra_logs": await event_log_repo.get_extra_logs(asset_id=asset_id),
        "rules": rules,
        "can_edit": can_edit,
    }


@router.get(PAGE_PATH, response_class=HTMLResponse, summary="Browse RMM event logs")
async def event_logs_page(
    request: Request,
    asset_id: int | None = Query(default=None, ge=1),
    log: str | None = Query(default=None, max_length=255),
    level: int | None = Query(default=None, ge=1, le=4),
    event_id: int | None = Query(default=None, ge=0),
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=200, ge=1, le=1000),
):
    context = await _context(request)
    if isinstance(context, RedirectResponse):
        return context
    user, company, company_id, can_edit = context
    database = event_log_db.status()
    agents = await rmm_repo.list_company_agents(company_id)
    asset_ids = [int(agent["asset_id"]) for agent in agents if agent.get("asset_id")]
    asset_logs = await event_log_repo.list_asset_extra_logs(asset_ids)
    company_logs = await event_log_repo.get_extra_logs(company_id=company_id)
    entries = await _filtered_entries(
        company_id, asset_id=asset_id, log=log, level=level, event_id=event_id, q=q, limit=limit
    )
    devices = [
        {
            **_serialise(agent),
            "extra_logs": asset_logs.get(int(agent["asset_id"]), []) if agent.get("asset_id") else [],
        }
        for agent in agents
    ]
    log_choices = event_logs.merge_logs(
        event_logs.DEFAULT_LOGS, company_logs, *(device["extra_logs"] for device in devices)
    )
    return await _assets_routes()._main()._render_template(
        "rmm/event_logs.html",
        request,
        user,
        extra={
            "title": "Event logs",
            "company": company,
            # Connection errors can name hosts, so only super admins see them.
            "database": {**database, "error": database["error"] if user.get("is_super_admin") else None},
            "default_logs": list(event_logs.DEFAULT_LOGS),
            "company_logs": company_logs,
            "devices": devices,
            "log_choices": log_choices,
            "level_filters": LEVEL_FILTERS,
            "entries": entries,
            "filters": {
                "asset_id": asset_id, "log": log or "", "level": str(level or ""),
                "event_id": event_id if event_id is not None else "", "q": q or "", "limit": limit,
            },
            "can_edit": can_edit,
            "can_manage_rules": can_edit,
            "retention_days": get_settings().event_log_retention_days,
        },
    )


async def _save(request: Request, *, asset_id: int | None = None):
    context = await _context(request)
    if isinstance(context, RedirectResponse):
        return context
    user, _company, company_id, can_edit = context
    if not can_edit:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission to run scripts is required")
    if asset_id is not None and not any(
        agent.get("asset_id") == asset_id for agent in await rmm_repo.list_company_agents(company_id)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    form = await request.form()
    back = _back_path(form, asset_id)
    try:
        logs = await event_logs.save_extra_logs(
            str(form.get("extra_logs") or ""),
            company_id=None if asset_id is not None else company_id,
            asset_id=asset_id,
            user_id=user.get("id"),
        )
    except event_logs.EventLogSettingsError as exc:
        return flash_redirect(back, str(exc), "error")
    await audit_service.record(
        action="rmm.event_logs.settings",
        request=request,
        user_id=user.get("id"),
        entity_type="asset" if asset_id is not None else "company",
        entity_id=asset_id if asset_id is not None else company_id,
        metadata={"extra_logs": logs},
    )
    target = "the device" if asset_id is not None else "every device in the company"
    return flash_redirect(back, f"Extra event logs saved for {target}.", "success")


@router.post(PAGE_PATH + "/company", response_class=HTMLResponse, summary="Save the company's extra event logs")
async def save_company_logs(request: Request):
    return await _save(request)


@router.post(PAGE_PATH + "/assets/{asset_id:int}", response_class=HTMLResponse, summary="Save a device's extra event logs")
async def save_asset_logs(asset_id: int, request: Request):
    return await _save(request, asset_id=asset_id)


@router.get("/api/rmm/event-logs", summary="List RMM event log entries for the current company")
async def list_event_logs_api(
    request: Request,
    asset_id: int | None = Query(default=None, ge=1),
    log: str | None = Query(default=None, max_length=255),
    level: int | None = Query(default=None, ge=1, le=4),
    event_id: int | None = Query(default=None, ge=0),
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=200, ge=1, le=1000),
):
    _user, _company, company_id, _can_edit = await _context(request, api=True)
    if not await event_logs.enabled():
        return JSONResponse(
            {"detail": "The event log database is not set up.", "entries": []},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    entries = await _filtered_entries(
        company_id, asset_id=asset_id, log=log, level=level, event_id=event_id, q=q, limit=limit
    )
    return {"entries": entries}


# --------------------------------------------------------------------------- #
# Agent API
# --------------------------------------------------------------------------- #


@router.get("/api/rmm/agent/event-logs/config", summary="Which event logs the RMM agent collects")
async def agent_event_log_config(agent: dict = Depends(get_current_rmm_agent)):
    return await event_logs.agent_config(agent)


class AgentEventLogEntry(BaseModel):
    log_name: str = Field(min_length=1, max_length=255)
    record_id: int = Field(ge=0)
    event_id: int = Field(ge=0, le=2_147_483_647)
    level: int = Field(default=4, ge=0, le=255)
    provider: str | None = Field(default=None, max_length=1024)
    computer: str | None = Field(default=None, max_length=1024)
    message: str | None = Field(default=None, max_length=65_536)
    time_created: datetime


class AgentEventLogBatch(BaseModel):
    entries: list[AgentEventLogEntry] = Field(default_factory=list, max_length=event_logs.MAX_BATCH)


@router.post("/api/rmm/agent/event-logs", summary="Send a batch of event log entries")
async def agent_event_logs(payload: AgentEventLogBatch, agent: dict = Depends(get_current_rmm_agent)):
    result = await event_logs.ingest(agent, [entry.model_dump() for entry in payload.entries])
    if result is None:
        return JSONResponse(
            {"detail": "Event log collection is off.", "enabled": False},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return result
