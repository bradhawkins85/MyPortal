"""Script result rules (:mod:`app.services.rmm_script_result_rules`).

Company scoped, ``menu.rmm_scripts``; changing rules needs write access, and
rules for every company need a super admin.

* ``GET /rmm/scripts/result-rules`` the rules reaching the company's runs;
  ``?rule={id}`` edits one, ``?new=1`` (optionally ``&script_id={id}``) adds one
* ``POST /rmm/scripts/result-rules`` and ``/rmm/scripts/result-rules/{id}`` save a rule
* ``POST /rmm/scripts/result-rules/{id}/enabled`` and ``/delete``
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.repositories import rmm as rmm_repo
from app.repositories import rmm_script_result_rules as rules_repo
from app.security.flash import flash_redirect
from app.services import audit as audit_service
from app.services import rmm_script_result_rules as result_rules
from app.services import rmm_scripts

from .routes import _assets_routes, _context

router = APIRouter(tags=["RMM script result rules"])

RULES_PATH = "/rmm/scripts/result-rules"
EMPTY_CONDITION_ROWS = 3


def _blank_rule(script_id: int | None) -> dict[str, Any]:
    return {
        "id": None, "name": "", "scope": "company", "script_id": script_id, "match_mode": "all",
        "conditions": [], "action": "create_ticket", "next_script_id": None, "timeout_seconds": 600,
        "pass_results": True, "ticket_mode": "update", "ticket_subject": "", "ticket_priority": "normal",
        "is_enabled": True,
    }


def _form_rule(rule: dict[str, Any]) -> dict[str, Any]:
    return {**rule, "scope": "global" if rule.get("company_id") is None else "company",
            "ticket_subject": rule.get("ticket_subject") or ""}


def _int_or_none(value: Any) -> int | None:
    try:
        return int(str(value or ""))
    except ValueError:
        return None


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
    rules = [
        {**result_rules.rule_summary(rule), "can_edit": can_edit and result_rules.can_edit(
            rule, company_id=company_id, is_super_admin=is_super_admin)}
        for rule in await rules_repo.list_rules_for_company(company_id)
    ]
    next_script = None
    fields: list[dict[str, Any]] = []
    if editing and editing.get("next_script_id"):
        next_script = await rmm_repo.get_script(int(editing["next_script_id"]))
        stored = await rules_repo.get_rule(int(editing["id"]), with_secrets=True) if editing.get("id") else None
        if stored and stored.get("next_script_id") == editing.get("next_script_id"):
            fields = result_rules.script_values(stored, next_script)
    conditions = list((editing or {}).get("conditions") or [])
    conditions += [{"field": "output", "name": "", "operator": "contains", "value": ""}] * EMPTY_CONDITION_ROWS
    variables = [{"group": "Result", "label": label, "token": token} for label, token in result_rules.RESULT_VARIABLES]
    variables += await rmm_scripts.available_variables()
    response = await _assets_routes()._main()._render_template(
        "rmm/script_result_rules.html",
        request,
        user,
        extra={
            "title": "Script result rules",
            "company": company,
            "rules": rules,
            "editing": editing,
            "conditions": conditions[: result_rules.MAX_CONDITIONS],
            "errors": errors or {},
            "fields": result_rules.FIELDS,
            "operators": result_rules.OPERATORS,
            "field_operators": result_rules.FIELD_OPERATORS,
            "actions": result_rules.ACTIONS,
            "match_modes": result_rules.MATCH_MODES,
            "ticket_modes": result_rules.TICKET_MODES,
            "ticket_priorities": result_rules.TICKET_PRIORITIES,
            "max_chain_depth": result_rules.MAX_CHAIN_DEPTH,
            "scripts": await rmm_repo.list_scripts(company_id=company_id),
            "next_script": next_script,
            "script_fields": fields,
            "variables": variables,
            "can_edit": can_edit,
            "is_super_admin": is_super_admin,
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
    if not result_rules.can_edit(rule, company_id=company_id, is_super_admin=bool(user.get("is_super_admin"))):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only super admins can change rules for every company")
    return rule


@router.get(RULES_PATH, response_class=HTMLResponse, summary="Script result rules")
async def rules_page(
    request: Request,
    rule: int | None = Query(default=None, ge=1),
    new: bool = Query(default=False),
    script_id: int | None = Query(default=None, ge=1),
):
    context = await _load(request)
    if isinstance(context, RedirectResponse):
        return context
    user, company, company_id, can_edit = context
    editing = None
    if can_edit and rule is not None:
        editing = _form_rule(await _editable_rule(rule, user, company_id))
    elif can_edit and new:
        editing = _blank_rule(script_id)
    return await _render(request, user=user, company=company, company_id=company_id, can_edit=can_edit,
                         editing=editing)


async def _save(request: Request, rule_id: int | None):
    context = await _load(request, write=True)
    if isinstance(context, RedirectResponse):
        return context
    user, company, company_id, can_edit = context
    if rule_id is not None:
        await _editable_rule(rule_id, user, company_id)
    form = await request.form()
    rows = (form.getlist("condition_field"), form.getlist("condition_name"), form.getlist("condition_operator"),
            form.getlist("condition_value"))
    errors: dict[str, str] = {}
    conditions: list[dict[str, str]] = []
    try:
        conditions = result_rules.parse_conditions(*rows)
        saved_id = await result_rules.save_rule(
            rule_id=rule_id, company_id=company_id, form=form, conditions=conditions,
            user_id=user.get("id"), is_super_admin=bool(user.get("is_super_admin")),
        )
    except result_rules.RuleError as exc:
        errors = exc.errors
    if errors:
        posted = [
            {"field": str(field), "name": str(name), "operator": str(operator), "value": str(value)}
            for field, name, operator, value in zip(*rows)
            if str(value).strip() or str(operator) in ("empty", "not_empty")
        ]
        editing = {
            "id": rule_id, "name": str(form.get("name") or ""), "scope": str(form.get("scope") or "company"),
            "script_id": _int_or_none(form.get("script_id")), "match_mode": str(form.get("match_mode") or "all"),
            "conditions": posted, "action": str(form.get("action") or ""),
            "next_script_id": _int_or_none(form.get("next_script_id")),
            "timeout_seconds": form.get("timeout_seconds") or 600,
            "pass_results": bool(form.get("pass_results")), "ticket_mode": str(form.get("ticket_mode") or "update"),
            "ticket_subject": str(form.get("ticket_subject") or ""),
            "ticket_priority": str(form.get("ticket_priority") or "normal"),
            "is_enabled": bool(form.get("is_enabled")),
        }
        return await _render(request, user=user, company=company, company_id=company_id, can_edit=can_edit,
                             editing=editing, errors=errors, status_code=400)
    saved = await rules_repo.get_rule(saved_id)
    await audit_service.record(
        action="rmm.script_result_rule.save",
        request=request,
        user_id=user.get("id"),
        entity_type="rmm_script_result_rule",
        entity_id=saved_id,
        metadata={
            "name": saved["name"], "action": saved["action"], "company_id": saved.get("company_id"),
            "script_id": saved.get("script_id"), "next_script_id": saved.get("next_script_id"),
            "match_mode": saved.get("match_mode"), "conditions": conditions,
        },
    )
    message = f"Saved {saved['name']}."
    if saved["action"] == "run_script" and saved.get("next_script_id") and rule_id is None:
        message += " Fill in the script's values below if it needs any."
    return flash_redirect(f"{RULES_PATH}?rule={saved_id}", message, "success")


@router.post(RULES_PATH, response_class=HTMLResponse, summary="Add a script result rule")
async def create_rule(request: Request):
    return await _save(request, None)


@router.post(RULES_PATH + "/{rule_id:int}", response_class=HTMLResponse, summary="Update a script result rule")
async def update_rule(rule_id: int, request: Request):
    return await _save(request, rule_id)


@router.post(RULES_PATH + "/{rule_id:int}/enabled", response_class=HTMLResponse,
             summary="Turn a script result rule on or off")
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
        action="rmm.script_result_rule.enabled", request=request, user_id=user.get("id"),
        entity_type="rmm_script_result_rule", entity_id=rule_id, metadata={"enabled": enabled},
    )
    return flash_redirect(RULES_PATH, f"{rule['name']} turned {'on' if enabled else 'off'}.", "success")


@router.post(RULES_PATH + "/{rule_id:int}/delete", response_class=HTMLResponse, summary="Delete a script result rule")
async def delete_rule(rule_id: int, request: Request):
    context = await _load(request, write=True)
    if isinstance(context, RedirectResponse):
        return context
    user, _company, company_id, _can_edit = context
    rule = await _editable_rule(rule_id, user, company_id)
    await rules_repo.delete_rule(rule_id)
    await audit_service.record(
        action="rmm.script_result_rule.delete", request=request, user_id=user.get("id"),
        entity_type="rmm_script_result_rule", entity_id=rule_id, metadata={"name": rule["name"]},
    )
    return flash_redirect(RULES_PATH, f"Deleted {rule['name']}.", "success")
