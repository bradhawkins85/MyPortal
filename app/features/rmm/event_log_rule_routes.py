"""Event log rules (:mod:`app.services.rmm_event_log_rules`).

Company scoped, ``menu.rmm_scripts``; changing rules needs write access, and
rules for every company need a super admin.

* ``GET /rmm/event-logs/rules`` the rules reaching the company's devices;
  ``?rule={id}`` edits one, ``?new=1`` (optionally ``&asset_id={id}``) adds one
* ``POST /rmm/event-logs/rules`` and ``/rmm/event-logs/rules/{id}`` save a rule
* ``POST /rmm/event-logs/rules/{id}/enabled`` and ``/delete``
* ``GET /rmm/event-logs/rules?run={id}`` asks to confirm running a drop rule
  now, with how many stored entries it matches; ``POST .../{id}/run`` deletes them
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.repositories import rmm as rmm_repo
from app.repositories import rmm_event_log_rules as rules_repo
from app.security.flash import flash_redirect
from app.services import audit as audit_service
from app.services import rmm_automation
from app.services import rmm_event_log_rules as event_log_rules
from app.services import rmm_event_logs as event_logs
from app.services import rmm_scripts

from .event_log_routes import PAGE_PATH, rule_summary
from .routes import _assets_routes, _context

router = APIRouter(tags=["RMM event log rules"])

RULES_PATH = PAGE_PATH + "/rules"
EMPTY_CONDITION_ROWS = 3


def _blank_rule(asset_id: int | None) -> dict[str, Any]:
    return {
        "id": None, "name": "", "scope": "asset" if asset_id else "company", "asset_id": asset_id,
        "action": "drop", "conditions": [], "script_id": None, "timeout_seconds": 600,
        "cooldown_minutes": event_log_rules.DEFAULT_COOLDOWN_MINUTES, "is_enabled": True,
        "schedule_mode": "delay", "delay_minutes": event_log_rules.DEFAULT_DELAY_MINUTES, "run_time": "02:00",
        "schedule_timezone": rmm_automation.default_timezone(),
    }


def _form_rule(rule: dict[str, Any]) -> dict[str, Any]:
    if rule.get("asset_id") is not None:
        scope = "asset"
    elif rule.get("company_id") is None and rule.get("id") is not None:
        scope = "global"
    else:
        scope = rule.get("scope") or "company"
    return {
        **rule, "scope": scope,
        "schedule_mode": rule.get("schedule_mode") or "delay",
        "delay_minutes": rule.get("delay_minutes") or event_log_rules.DEFAULT_DELAY_MINUTES,
        "run_time": rule.get("run_time") or "02:00",
        "schedule_timezone": rule.get("schedule_timezone") or rmm_automation.default_timezone(),
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
    run_now: dict[str, Any] | None = None,
):
    is_super_admin = bool(user.get("is_super_admin"))
    listed = await rules_repo.list_rules_for_company(company_id)
    pending = await rules_repo.list_pending_for_rules(
        [int(rule["id"]) for rule in listed if rule["action"] == "schedule_script"])
    rules = [
        {**rule_summary(rule), "can_edit": can_edit and event_log_rules.can_edit(
            rule, company_id=company_id, is_super_admin=is_super_admin),
         "pending": [{"run_at": item["run_at"].isoformat() if hasattr(item["run_at"], "isoformat") else item["run_at"]}
                     for item in pending.get(int(rule["id"]), [])]}
        for rule in listed
    ]
    script = None
    fields: list[dict[str, Any]] = []
    if editing and editing.get("script_id"):
        script = await rmm_repo.get_script(int(editing["script_id"]))
        stored = await rules_repo.get_rule(int(editing["id"]), with_secrets=True) if editing.get("id") else None
        if stored and stored.get("script_id") == editing.get("script_id"):
            fields = event_log_rules.script_values(stored, script)
    conditions = list((editing or {}).get("conditions") or [])
    conditions += [{"field": "event_id", "operator": "is", "value": ""}] * EMPTY_CONDITION_ROWS
    variables = [{"group": "Event", "label": label, "token": token} for label, token in event_log_rules.EVENT_VARIABLES]
    variables += await rmm_scripts.available_variables()
    response = await _assets_routes()._main()._render_template(
        "rmm/event_log_rules.html",
        request,
        user,
        extra={
            "title": "Event log rules",
            "company": company,
            "rules": rules,
            "editing": editing,
            "conditions": conditions[: event_log_rules.MAX_CONDITIONS],
            "errors": errors or {},
            "fields": event_log_rules.FIELDS,
            "operators": event_log_rules.OPERATORS,
            "field_operators": event_log_rules.FIELD_OPERATORS,
            "actions": event_log_rules.ACTIONS,
            "script_actions": event_log_rules.SCRIPT_ACTIONS,
            "schedule_modes": event_log_rules.SCHEDULE_MODES,
            "devices": [agent for agent in await rmm_repo.list_company_agents(company_id) if agent.get("asset_id")],
            "scripts": await rmm_repo.list_scripts(company_id=company_id),
            "script": script,
            "script_fields": fields,
            "variables": variables,
            "can_edit": can_edit,
            "is_super_admin": is_super_admin,
            "run_now": run_now,
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


async def _editable_rule(rule_id: int, user: dict[str, Any], company_id: int) -> dict[str, Any]:
    rule = await rules_repo.get_rule(rule_id)
    if not rule or (rule.get("company_id") is not None and int(rule["company_id"]) != int(company_id)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    if not event_log_rules.can_edit(rule, company_id=company_id, is_super_admin=bool(user.get("is_super_admin"))):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only super admins can change rules for every company")
    return rule


@router.get(RULES_PATH, response_class=HTMLResponse, summary="Event log rules")
async def rules_page(
    request: Request,
    rule: int | None = Query(default=None, ge=1),
    new: bool = Query(default=False),
    asset_id: int | None = Query(default=None, ge=1),
    run: int | None = Query(default=None, ge=1),
):
    context = await _load(request)
    if isinstance(context, RedirectResponse):
        return context
    user, company, company_id, can_edit = context
    editing = None
    if can_edit and rule is not None:
        editing = _form_rule(await _editable_rule(rule, user, company_id))
    elif can_edit and new:
        editing = _blank_rule(asset_id)
    run_now = None
    if can_edit and run is not None and rule is None and not new:
        to_run = await _editable_rule(run, user, company_id)
        if to_run["action"] != "drop":
            return flash_redirect(RULES_PATH, "Only drop rules can run now.", "error")
        available = await event_logs.enabled()
        run_now = {
            "rule": rule_summary(to_run),
            "available": available,
            "count": await event_log_rules.drop_stored(to_run, delete=False) if available else 0,
            "asset_id": asset_id,
        }
    return await _render(request, user=user, company=company, company_id=company_id, can_edit=can_edit,
                         editing=editing, run_now=run_now)


async def _save(request: Request, rule_id: int | None):
    context = await _load(request, write=True)
    if isinstance(context, RedirectResponse):
        return context
    user, company, company_id, can_edit = context
    if rule_id is not None:
        await _editable_rule(rule_id, user, company_id)
    form = await request.form()
    errors: dict[str, str] = {}
    conditions: list[dict[str, str]] = []
    try:
        conditions = event_log_rules.parse_conditions(
            form.getlist("condition_field"), form.getlist("condition_operator"), form.getlist("condition_value")
        )
        saved_id = await event_log_rules.save_rule(
            rule_id=rule_id, company_id=company_id, form=form, conditions=conditions,
            user_id=user.get("id"), is_super_admin=bool(user.get("is_super_admin")),
        )
    except event_log_rules.RuleError as exc:
        errors = exc.errors
    if errors:
        posted = [
            {"field": str(field), "operator": str(operator), "value": str(value)}
            for field, operator, value in zip(
                form.getlist("condition_field"), form.getlist("condition_operator"), form.getlist("condition_value"))
            if str(value).strip()
        ]
        try:
            script_id = int(str(form.get("script_id") or ""))
        except ValueError:
            script_id = None
        try:
            posted_asset = int(str(form.get("asset_id") or ""))
        except ValueError:
            posted_asset = None
        editing = {
            "id": rule_id, "name": str(form.get("name") or ""), "scope": str(form.get("scope") or "company"),
            "asset_id": posted_asset, "action": str(form.get("action") or ""), "conditions": posted,
            "script_id": script_id, "timeout_seconds": form.get("timeout_seconds") or 600,
            "cooldown_minutes": form.get("cooldown_minutes") or event_log_rules.DEFAULT_COOLDOWN_MINUTES,
            "is_enabled": bool(form.get("is_enabled")),
            "schedule_mode": str(form.get("schedule_mode") or "delay"),
            "delay_minutes": form.get("delay_minutes") or event_log_rules.DEFAULT_DELAY_MINUTES,
            "run_time": str(form.get("run_time") or ""),
            "schedule_timezone": str(form.get("schedule_timezone") or ""),
        }
        return await _render(request, user=user, company=company, company_id=company_id, can_edit=can_edit,
                             editing=editing, errors=errors, status_code=400)
    saved = await rules_repo.get_rule(saved_id)
    await audit_service.record(
        action="rmm.event_log_rule.save",
        request=request,
        user_id=user.get("id"),
        entity_type="rmm_event_log_rule",
        entity_id=saved_id,
        metadata={
            "name": saved["name"], "action": saved["action"], "company_id": saved.get("company_id"),
            "asset_id": saved.get("asset_id"), "script_id": saved.get("script_id"), "conditions": conditions,
        },
    )
    message = f"Saved {saved['name']}."
    if saved["action"] in event_log_rules.SCRIPT_ACTIONS and saved.get("script_id") and rule_id is None:
        message += " Fill in the script's values below if it needs any."
    return flash_redirect(f"{RULES_PATH}?rule={saved_id}", message, "success")


@router.post(RULES_PATH, response_class=HTMLResponse, summary="Add an event log rule")
async def create_rule(request: Request):
    return await _save(request, None)


@router.post(RULES_PATH + "/{rule_id:int}", response_class=HTMLResponse, summary="Update an event log rule")
async def update_rule(rule_id: int, request: Request):
    return await _save(request, rule_id)


@router.post(RULES_PATH + "/{rule_id:int}/enabled", response_class=HTMLResponse, summary="Turn an event log rule on or off")
async def set_rule_enabled(rule_id: int, request: Request):
    context = await _load(request, write=True)
    if isinstance(context, RedirectResponse):
        return context
    user, _company, company_id, _can_edit = context
    rule = await _editable_rule(rule_id, user, company_id)
    form = await request.form()
    enabled = str(form.get("enabled") or "") == "1"
    await rules_repo.set_rule_enabled(rule_id, enabled, user.get("id"))
    await audit_service.record(
        action="rmm.event_log_rule.enabled", request=request, user_id=user.get("id"),
        entity_type="rmm_event_log_rule", entity_id=rule_id, metadata={"enabled": enabled},
    )
    back = _asset_back(form, "event-log-rules") or RULES_PATH
    return flash_redirect(back, f"{rule['name']} turned {'on' if enabled else 'off'}.", "success")


def _asset_back(form: Any, anchor: str) -> str | None:
    value = str(form.get("asset_id") or "")
    return f"/assets/{int(value)}#{anchor}" if value.isdigit() else None


@router.post(RULES_PATH + "/{rule_id:int}/run", response_class=HTMLResponse, summary="Run a drop rule on stored events")
async def run_rule_now(rule_id: int, request: Request):
    context = await _load(request, write=True)
    if isinstance(context, RedirectResponse):
        return context
    user, _company, company_id, _can_edit = context
    rule = await _editable_rule(rule_id, user, company_id)
    form = await request.form()
    back = _asset_back(form, "event-log-rules") or RULES_PATH
    if rule["action"] != "drop":
        return flash_redirect(back, "Only drop rules can run now.", "error")
    if not await event_logs.enabled():
        return flash_redirect(back, "The event log database is not available, so nothing was deleted.", "error")
    deleted = await event_log_rules.drop_stored(rule, delete=True)
    await audit_service.record(
        action="rmm.event_log_rule.run", request=request, user_id=user.get("id"),
        entity_type="rmm_event_log_rule", entity_id=rule_id, metadata={"name": rule["name"], "deleted": deleted},
    )
    return flash_redirect(back, f"{rule['name']} deleted {deleted} stored event{'s' if deleted != 1 else ''}.", "success")


@router.post(RULES_PATH + "/{rule_id:int}/delete", response_class=HTMLResponse, summary="Delete an event log rule")
async def delete_rule(rule_id: int, request: Request):
    context = await _load(request, write=True)
    if isinstance(context, RedirectResponse):
        return context
    user, _company, company_id, _can_edit = context
    rule = await _editable_rule(rule_id, user, company_id)
    form = await request.form()
    await rules_repo.delete_rule(rule_id)
    await audit_service.record(
        action="rmm.event_log_rule.delete", request=request, user_id=user.get("id"),
        entity_type="rmm_event_log_rule", entity_id=rule_id, metadata={"name": rule["name"]},
    )
    return flash_redirect(_asset_back(form, "event-log-rules") or RULES_PATH, f"Deleted {rule['name']}.", "success")
