"""RMM checks (:mod:`app.services.rmm_checks`).

Technician side (company scoped, ``menu.rmm_scripts``; changing checks needs
write access, and checks for every company need a super admin):

* ``GET /rmm/checks`` the checks reaching the company's devices; ``?check={id}``
  edits one, ``?new=1`` (optionally ``&asset_id={id}`` and ``&type={type}``) adds
  one, and ``?new=1&asset_id={id}&from={check}`` copies a check as the device's
  own, which replaces it on that device
* ``POST /rmm/checks`` and ``/rmm/checks/{id}`` save a check
* ``POST /rmm/checks/{id}/enabled`` and ``/delete``
* each asset page's Checks and Machine stats sections (:func:`asset_checks_context`)

Agent side (``Authorization: Bearer`` token):

* ``GET /api/rmm/agent/checks/config`` what to measure
* ``POST /api/rmm/agent/checks/report`` the measurements
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.repositories import rmm as rmm_repo
from app.repositories import rmm_checks as checks_repo
from app.repositories import rmm_automation as automation_repo
from app.repositories import rmm_targeting as targeting_repo
from app.security.flash import flash_redirect
from app.services import audit as audit_service
from app.services import rmm_automation, rmm_checks
from app.services import rmm_event_log_rules as event_log_rules
from app.services import rmm_scripts
from app.services import rmm_stats
from app.services import rmm_targeting

from .routes import _assets_routes, _context, _serialise, get_current_rmm_agent

router = APIRouter(tags=["RMM checks"])

PAGE_PATH = "/rmm/checks"

# The form's own fields for each type, with their defaults.
FORM_DEFAULTS: dict[str, Any] = {
    "disk_drive": "*", "disk_metric": "free_percent", "disk_operator": "below", "disk_threshold": "10",
    "ping_target": "", "ping_loss": "100", "ping_latency": "",
    "cpu_threshold": "80",
    "memory_metric": "free_percent", "memory_threshold": "10",
    "service_name": "", "service_expected": "running", "service_fix": "none",
    "network_interface": "", "network_direction": "total", "network_operator": "above", "network_threshold": "100",
    "script_expect": "exit_code", "script_value": "0", "script_interval": str(rmm_checks.DEFAULT_SCRIPT_INTERVAL),
}
DEFAULT_DURATIONS = {"cpu": 30, "memory": 15, "network": 15, "offline": 30}


def check_summary(check: dict[str, Any], *, counts: dict[str, int] | None = None) -> dict[str, Any]:
    """A check as the checks page and the asset page list it."""

    if check.get("asset_id") is not None:
        scope = f"Device: {check.get('asset_name') or 'Asset ' + str(check['asset_id'])}"
    elif check.get("company_id") is not None:
        scope = f"Company: {check.get('company_name') or check['company_id']}"
    else:
        scope = "Every company"
    action = rmm_checks.ACTIONS.get(check.get("action"), check.get("action"))
    if check.get("action") == "run_script":
        action = f"Run {check.get('script_name') or 'a removed script'}"
    elif check.get("action") == "schedule_script":
        action = (f"Run {check.get('script_name') or 'a removed script'} "
                  f"{event_log_rules.describe_schedule(check)}")
    return {
        **(_serialise(check) or {}),
        "type_label": rmm_checks.CHECK_TYPES.get(check.get("check_type"), check.get("check_type")),
        "scope_label": scope,
        "action_label": action,
        "description": rmm_checks.describe(check),
        "counts": counts or {},
    }


def form_values(check: dict[str, Any]) -> dict[str, Any]:
    """A saved check as the form's fields."""

    values = {**FORM_DEFAULTS}
    settings = check.get("settings") or {}
    kind = check.get("check_type")
    target = check.get("target") or ""
    if kind == "disk":
        values.update(disk_drive=target or "*", disk_metric=settings.get("metric"), disk_operator=settings.get("operator"),
                      disk_threshold=rmm_checks._round(float(settings.get("threshold") or 0)))
    elif kind == "ping":
        values.update(ping_target=target, ping_loss=settings.get("max_loss_percent") or 100,
                      ping_latency=settings.get("max_latency_ms") or "")
    elif kind == "cpu":
        values.update(cpu_threshold=rmm_checks._round(float(settings.get("threshold") or 0)))
    elif kind == "memory":
        values.update(memory_metric=settings.get("metric"),
                      memory_threshold=rmm_checks._round(float(settings.get("threshold") or 0)))
    elif kind == "service":
        values.update(service_name=target, service_expected=settings.get("expected"), service_fix=settings.get("fix"))
    elif kind == "network":
        values.update(network_interface=target, network_direction=settings.get("direction"),
                      network_operator=settings.get("operator"),
                      network_threshold=rmm_checks._round(float(settings.get("threshold_mbps") or 0)))
    elif kind == "script":
        values.update(script_expect=settings.get("expect"), script_value=settings.get("value"),
                      script_interval=settings.get("interval_minutes") or rmm_checks.DEFAULT_SCRIPT_INTERVAL)
    if check.get("asset_id") is not None:
        scope = "asset"
    elif check.get("company_id") is None and check.get("id") is not None:
        scope = "global"
    else:
        scope = check.get("scope") or "company"
    return {
        "id": check.get("id"), "name": check.get("name") or "", "scope": scope, "asset_id": check.get("asset_id"),
        "target_mode": "rules" if check.get("target_rules") is not None else "all",
        "target_rules": check.get("target_rules"),
        "check_type": kind, "duration_minutes": check.get("duration_minutes") or 0,
        "check_script_id": check.get("check_script_id"), "action": check.get("action") or "none",
        "script_id": check.get("script_id"), "timeout_seconds": check.get("timeout_seconds") or 600,
        "cooldown_minutes": check.get("cooldown_minutes") if check.get("cooldown_minutes") is not None
        else rmm_checks.DEFAULT_COOLDOWN_MINUTES,
        "schedule_mode": check.get("schedule_mode") or "delay",
        "delay_minutes": check.get("delay_minutes") or event_log_rules.DEFAULT_DELAY_MINUTES,
        "run_time": check.get("run_time") or "02:00",
        "schedule_timezone": check.get("schedule_timezone") or rmm_automation.default_timezone(),
        "is_enabled": bool(check.get("is_enabled")), **values,
    }


def _blank_check(asset_id: int | None, check_type: str | None) -> dict[str, Any]:
    kind = check_type if check_type in rmm_checks.CHECK_TYPES else "disk"
    return {
        **form_values({"check_type": kind, "is_enabled": True, "asset_id": asset_id,
                       "scope": "asset" if asset_id else "company"}),
        "duration_minutes": DEFAULT_DURATIONS.get(kind, 0),
        "target_mode": "rules", "target_rules": {"operator": "and", "children": []},
    }


def _posted(form: Any, check_id: int | None) -> dict[str, Any]:
    def number(name: str) -> int | None:
        try:
            return int(str(form.get(name) or ""))
        except ValueError:
            return None

    try:
        rules = json.loads(str(form.get("target_rules") or "null"))
    except (ValueError, TypeError, RecursionError):
        rules = {}
    # Keep ordinary invalid values editable, while bounding data embedded in
    # the error page and recursively rendered by the browser's rule editor.
    pending = [(rules, 0)]
    remaining = rmm_targeting.MAX_NODES * 8
    if not isinstance(rules, dict):
        rules = {}
        pending = []
    while pending:
        value, depth = pending.pop()
        remaining -= 1
        if remaining < 0 or depth > rmm_targeting.MAX_DEPTH * 2 + 4:
            rules = {}
            break
        if isinstance(value, dict):
            pending.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            pending.extend((item, depth + 1) for item in value)
        elif isinstance(value, str) and len(value) > rmm_targeting.MAX_VALUE_LENGTH:
            rules = {}
            break

    return {
        **{key: str(form.get(key) if form.get(key) is not None else default) for key, default in FORM_DEFAULTS.items()},
        "id": check_id, "name": str(form.get("name") or ""), "scope": str(form.get("scope") or "company"),
        "asset_id": number("asset_id"), "check_type": str(form.get("check_type") or ""),
        "target_mode": str(form.get("target_mode") or "all"), "target_rules": rules,
        "duration_minutes": form.get("duration_minutes") or 0, "check_script_id": number("check_script_id"),
        "action": str(form.get("action") or "none"), "script_id": number("script_id"),
        "timeout_seconds": form.get("timeout_seconds") or 600,
        "cooldown_minutes": form.get("cooldown_minutes") or rmm_checks.DEFAULT_COOLDOWN_MINUTES,
        "schedule_mode": str(form.get("schedule_mode") or "delay"),
        "delay_minutes": form.get("delay_minutes") or event_log_rules.DEFAULT_DELAY_MINUTES,
        "run_time": str(form.get("run_time") or ""),
        "schedule_timezone": str(form.get("schedule_timezone") or ""),
        "is_enabled": bool(form.get("is_enabled")),
    }


async def _render(
    request: Request,
    *,
    user: dict[str, Any],
    company: Any,
    company_id: int,
    can_edit: bool,
    editing: dict[str, Any] | None,
    errors: dict[str, str] | None = None,
    status_code: int = 200,
):
    is_super_admin = bool(user.get("is_super_admin"))
    checks = await checks_repo.list_checks_for_company(company_id)
    counts = await checks_repo.count_states(int(check["id"]) for check in checks)
    rows = []
    for check in checks:
        summary = check_summary(check, counts=counts.get(int(check["id"])))
        summary["can_edit"] = can_edit and rmm_checks.can_edit(check, company_id=company_id, is_super_admin=is_super_admin)
        summary["replaced"] = bool(rmm_checks.replaced_by(check, checks)) and check.get("asset_id") is None
        rows.append(summary)
    stored = await checks_repo.get_check(int(editing["id"]), with_secrets=True) if editing and editing.get("id") else None
    script = check_script = None
    fields: list[dict[str, Any]] = []
    check_fields: list[dict[str, Any]] = []
    if editing and editing.get("script_id"):
        script = await rmm_repo.get_script(int(editing["script_id"]))
        if stored and stored.get("script_id") == editing.get("script_id"):
            fields = rmm_checks.script_values(stored, script)
    if editing and editing.get("check_script_id"):
        check_script = await rmm_repo.get_script(int(editing["check_script_id"]))
        if stored and stored.get("check_script_id") == editing.get("check_script_id"):
            check_fields = rmm_checks.script_values(stored, check_script, own=True)
    variables = [{"group": "Check", "label": label, "token": token} for label, token in rmm_checks.CHECK_VARIABLES]
    variables += await rmm_scripts.available_variables()
    response = await _assets_routes()._main()._render_template(
        "rmm/checks.html",
        request,
        user,
        extra={
            "title": "Checks",
            "company": company,
            "checks": rows,
            "editing": editing,
            "errors": errors or {},
            "check_types": rmm_checks.CHECK_TYPES,
            "actions": rmm_checks.ACTIONS,
            "script_actions": rmm_checks.SCRIPT_ACTIONS,
            "schedule_modes": event_log_rules.SCHEDULE_MODES,
            "disk_metrics": rmm_checks.DISK_METRICS,
            "memory_metrics": rmm_checks.MEMORY_METRICS,
            "comparisons": rmm_checks.COMPARISONS,
            "network_directions": rmm_checks.NETWORK_DIRECTIONS,
            "service_states": rmm_checks.SERVICE_STATES,
            "service_fixes": rmm_checks.SERVICE_FIXES,
            "script_expectations": rmm_checks.SCRIPT_EXPECTATIONS,
            "statuses": rmm_checks.STATUSES,
            "devices": [agent for agent in await rmm_repo.list_company_agents(company_id) if agent.get("asset_id")],
            "scripts": await rmm_repo.list_scripts(company_id=company_id),
            "script": script,
            "script_fields": fields,
            "check_script": check_script,
            "check_script_fields": check_fields,
            "variables": variables,
            "can_edit": can_edit,
            "is_super_admin": is_super_admin,
            "targeting_fields": rmm_targeting.field_catalogue(),
        },
    )
    response.status_code = status_code
    return response


async def _load(request: Request, *, write: bool = False):
    context = await _context(request)
    if isinstance(context, RedirectResponse):
        return context
    user, company, company_id, can_edit = context
    if write and not can_edit:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission to run scripts is required")
    return user, company, company_id, can_edit


async def _editable_check(check_id: int, user: dict[str, Any], company_id: int) -> dict[str, Any]:
    check = await checks_repo.get_check(check_id)
    if not check or (check.get("company_id") is not None and int(check["company_id"]) != int(company_id)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Check not found")
    if not rmm_checks.can_edit(check, company_id=company_id, is_super_admin=bool(user.get("is_super_admin"))):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only super admins can change checks for every company")
    return check


class TargetingPreviewRequest(BaseModel):
    scope: str = Field(default="company", max_length=16)
    asset_id: int | None = Field(default=None, ge=1)
    rules: dict[str, Any]


@router.get("/api/rmm/checks/targeting/fields", summary="Check targeting fields and operators")
async def targeting_fields(request: Request):
    await _context(request, api=True)
    return {"fields": rmm_targeting.field_catalogue()}


@router.post("/api/rmm/checks/targeting/preview", summary="Preview devices matching check rules")
async def targeting_preview(request: Request, payload: TargetingPreviewRequest):
    user, _company, company_id, _can_edit = await _context(request, write=True, api=True)
    if payload.scope not in {"global", "company", "asset"}:
        raise HTTPException(status_code=422, detail="Choose a company or device scope")
    if payload.scope == "global" and not user.get("is_super_admin"):
        raise HTTPException(status_code=403, detail="Only super admins can preview checks for every company")
    if payload.scope == "asset":
        from app.repositories import assets as assets_repo
        asset = await assets_repo.get_asset_by_id(payload.asset_id) if payload.asset_id else None
        if not asset or int(asset.get("company_id") or 0) != int(company_id) or asset.get("archived_at"):
            raise HTTPException(status_code=404, detail="Device not found in this company")
    try:
        matches = rmm_targeting.compile_rules(payload.rules)
    except ValueError as exc:
        return JSONResponse({"detail": str(exc), "errors": {"target_rules": str(exc)}}, status_code=400)
    agents = await automation_repo.list_active_agents(company_id=None if payload.scope == "global" else company_id)
    if payload.scope == "asset":
        agents = [agent for agent in agents if agent.get("asset_id") == payload.asset_id]
    contexts = await targeting_repo.contexts_for_agents(agents)
    matched = [agent for agent in agents if int(agent["id"]) in contexts
               and matches(contexts[int(agent["id"])])]
    return {
        "matched_count": len(matched), "eligible_count": len(contexts), "truncated": len(matched) > 100,
        "agents": [{"id": agent["id"], "asset_id": agent.get("asset_id"), "hostname": agent.get("hostname"),
                    "company_name": contexts[int(agent["id"])]["company"].get("name")} for agent in matched[:100]],
    }


def _asset_back(form: Any) -> str | None:
    value = str(form.get("return_asset_id") or "")
    return f"/assets/{int(value)}#checks" if value.isdigit() else None


@router.get(PAGE_PATH, response_class=HTMLResponse, summary="RMM checks")
async def checks_page(
    request: Request,
    check: int | None = Query(default=None, ge=1),
    new: bool = Query(default=False),
    asset_id: int | None = Query(default=None, ge=1),
    check_type: str | None = Query(default=None, alias="type", max_length=16),
    copy_of: int | None = Query(default=None, alias="from", ge=1),
):
    context = await _load(request)
    if isinstance(context, RedirectResponse):
        return context
    user, company, company_id, can_edit = context
    editing = None
    if can_edit and check is not None:
        editing = form_values(await _editable_check(check, user, company_id))
    elif can_edit and new and copy_of is not None and asset_id is not None:
        # The device's own copy of a company or every-company check replaces it there.
        original = await checks_repo.get_check(copy_of)
        if not original or (original.get("company_id") is not None and int(original["company_id"]) != int(company_id)):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Check not found")
        editing = form_values({**original, "id": None, "asset_id": asset_id})
    elif can_edit and new:
        editing = _blank_check(asset_id, check_type)
    return await _render(request, user=user, company=company, company_id=company_id, can_edit=can_edit, editing=editing)


async def _save(request: Request, check_id: int | None):
    context = await _load(request, write=True)
    if isinstance(context, RedirectResponse):
        return context
    user, company, company_id, can_edit = context
    if check_id is not None:
        await _editable_check(check_id, user, company_id)
    form = await request.form()
    try:
        saved_id = await rmm_checks.save_check(
            check_id=check_id, company_id=company_id, form=form,
            user_id=user.get("id"), is_super_admin=bool(user.get("is_super_admin")),
        )
    except rmm_checks.CheckError as exc:
        return await _render(request, user=user, company=company, company_id=company_id, can_edit=can_edit,
                             editing=_posted(form, check_id), errors=exc.errors, status_code=400)
    saved = await checks_repo.get_check(saved_id)
    await audit_service.record(
        action="rmm.check.save",
        request=request,
        user_id=user.get("id"),
        entity_type="rmm_check",
        entity_id=saved_id,
        metadata={
            "name": saved["name"], "check_type": saved["check_type"], "target": saved["target"],
            "settings": saved["settings"], "action": saved["action"], "company_id": saved.get("company_id"),
            "asset_id": saved.get("asset_id"), "script_id": saved.get("script_id"),
            "check_script_id": saved.get("check_script_id"), "enabled": saved["is_enabled"],
        },
    )
    message = f"Saved {saved['name']}."
    new_script = (saved["action"] in rmm_checks.SCRIPT_ACTIONS and saved.get("script_id")) or saved.get("check_script_id")
    if new_script and check_id is None:
        message += " Fill in the script's values below if it needs any."
    return flash_redirect(f"{PAGE_PATH}?check={saved_id}#rmm-check-form", message, "success")


@router.post(PAGE_PATH, response_class=HTMLResponse, summary="Add an RMM check")
async def create_check(request: Request):
    return await _save(request, None)


@router.post(PAGE_PATH + "/{check_id:int}", response_class=HTMLResponse, summary="Update an RMM check")
async def update_check(check_id: int, request: Request):
    return await _save(request, check_id)


@router.post(PAGE_PATH + "/{check_id:int}/enabled", response_class=HTMLResponse, summary="Turn an RMM check on or off")
async def set_check_enabled(check_id: int, request: Request):
    context = await _load(request, write=True)
    if isinstance(context, RedirectResponse):
        return context
    user, _company, company_id, _can_edit = context
    check = await _editable_check(check_id, user, company_id)
    form = await request.form()
    enabled = str(form.get("enabled") or "") == "1"
    await checks_repo.set_check_enabled(check_id, enabled, user.get("id"))
    await audit_service.record(
        action="rmm.check.enabled", request=request, user_id=user.get("id"),
        entity_type="rmm_check", entity_id=check_id, metadata={"enabled": enabled},
    )
    return flash_redirect(_asset_back(form) or PAGE_PATH, f"{check['name']} turned {'on' if enabled else 'off'}.", "success")


@router.post(PAGE_PATH + "/{check_id:int}/delete", response_class=HTMLResponse, summary="Delete an RMM check")
async def delete_check(check_id: int, request: Request):
    context = await _load(request, write=True)
    if isinstance(context, RedirectResponse):
        return context
    user, _company, company_id, _can_edit = context
    check = await _editable_check(check_id, user, company_id)
    form = await request.form()
    await checks_repo.delete_check(check_id)
    await audit_service.record(
        action="rmm.check.delete", request=request, user_id=user.get("id"),
        entity_type="rmm_check", entity_id=check_id, metadata={"name": check["name"]},
    )
    return flash_redirect(_asset_back(form) or PAGE_PATH, f"Deleted {check['name']}.", "success")


async def asset_checks_context(company_id: int, asset_id: int, *, can_edit: bool, is_super_admin: bool,
                               stats_range: str | None = None) -> dict[str, Any]:
    """The asset page's Checks and Machine stats sections."""

    checks = await checks_repo.list_checks_for_device(company_id, asset_id)
    agent = await rmm_repo.get_asset_agent(asset_id)
    if agent and agent.get("company_id") is not None and int(agent["company_id"]) != int(company_id):
        agent = None
    if agent:
        matching = (await rmm_checks.targeted_checks_for_agents(checks, [agent]))[int(agent["id"])]
    else:
        matching = rmm_checks.effective_checks([check for check in checks if check.get("target_rules") is None], company_id, asset_id)
    applied = {int(check["id"]) for check in matching}
    states = await checks_repo.list_states_for_agent(int(agent["id"])) if agent else {}
    rows = []
    for check in checks:
        state = states.get(int(check["id"])) if int(check["id"]) in applied else None
        summary = check_summary(check)
        summary.update(
            can_edit=can_edit and rmm_checks.can_edit(check, company_id=company_id, is_super_admin=is_super_admin),
            applies=int(check["id"]) in applied,
            replaced=bool(check["is_enabled"]) and int(check["id"]) not in applied and check.get("target_rules") is None,
            filtered=check.get("target_rules") is not None and int(check["id"]) not in applied,
            state=_serialise(state) if state else None,
            status_label=rmm_checks.STATUSES.get((state or {}).get("status") or "pending"),
        )
        rows.append(summary)
    order = {"failing": 0, "warning": 1, "pending": 2, "passing": 3}
    rows.sort(key=lambda row: (not row["applies"], order.get((row["state"] or {}).get("status") or "pending", 2),
                               row["name"].casefold()))
    return {
        "checks": rows,
        "failing": sum(1 for row in rows if (row["state"] or {}).get("status") == "failing"),
        "has_agent": agent is not None,
        "can_edit": can_edit,
        "check_types": rmm_checks.CHECK_TYPES,
        "stats": await rmm_stats.asset_stats_context(asset_id, stats_range),
    }


# --------------------------------------------------------------------------- #
# Agent API
# --------------------------------------------------------------------------- #


@router.get("/api/rmm/agent/checks/config", summary="What the RMM agent measures for checks")
async def agent_checks_config(agent: dict = Depends(get_current_rmm_agent)):
    return await rmm_checks.agent_config(agent)


class AgentDisk(BaseModel):
    name: str = Field(max_length=255)
    total_bytes: float = Field(ge=0)
    free_bytes: float = Field(ge=0)


class AgentMemory(BaseModel):
    total_bytes: float = Field(ge=0)
    available_bytes: float = Field(ge=0)


class AgentInterface(BaseModel):
    name: str = Field(max_length=255)
    rx_bps: float = Field(default=0, ge=0)
    tx_bps: float = Field(default=0, ge=0)


class AgentPing(BaseModel):
    target: str = Field(max_length=255)
    sent: int = Field(ge=0, le=100)
    received: int = Field(ge=0, le=100)
    avg_ms: float | None = Field(default=None, ge=0)
    error: str | None = Field(default=None, max_length=1000)


class AgentService(BaseModel):
    name: str = Field(max_length=256)
    status: str = Field(max_length=32)
    action: str | None = Field(default=None, max_length=255)
    error: str | None = Field(default=None, max_length=1000)


class AgentCheckReport(BaseModel):
    cpu_percent: float | None = Field(default=None, ge=0, le=100)
    memory: AgentMemory | None = None
    disks: list[AgentDisk] | None = Field(default=None, max_length=64)
    network: list[AgentInterface] | None = Field(default=None, max_length=64)
    pings: list[AgentPing] = Field(default_factory=list, max_length=rmm_checks.MAX_PINGS)
    services: list[AgentService] = Field(default_factory=list, max_length=rmm_checks.MAX_SERVICES)


@router.post("/api/rmm/agent/checks/report", summary="Send the RMM agent's measurements for checks")
async def agent_checks_report(payload: AgentCheckReport, agent: dict = Depends(get_current_rmm_agent)):
    return await rmm_checks.ingest_report(agent, payload.model_dump())
