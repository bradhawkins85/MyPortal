"""Monthly health checks: the groups of computers that get one, and when each is next due.

Users with RMM scripts access see the groups that reach the company they are
working in and its computers' next monthly checks
(:mod:`app.services.ai_health_check_schedule`). Write access manages the
company's groups; super admins also manage groups for every company. Each
group narrows its scope with the same advanced device rules as RMM checks,
previewed with ``POST /api/rmm/targeting/preview``.

* ``GET /health-checks/monthly`` the groups and upcoming checks;
  ``?group={id}`` or ``?new=1`` opens the group editor
* ``POST /health-checks/monthly/groups`` and ``/health-checks/monthly/groups/{id}``
  save a group; ``POST .../{id}/delete`` deletes one
* ``GET``/``POST /api/health-checks/monthly/groups`` and ``PUT``/``DELETE
  /api/health-checks/monthly/groups/{id}`` (helpdesk technicians and super
  admins) the same as JSON
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from app.api.dependencies.auth import require_helpdesk_technician
from app.core.logging import log_warning
from app.repositories import ai_health_check_groups as groups_repo
from app.repositories import ai_health_check_schedules as schedule_repo
from app.repositories import companies as company_repo
from app.security.flash import flash_redirect
from app.services import ai_health_check_schedule as schedule
from app.services import audit as audit_service

from . import routes as hc_routes

router = APIRouter(tags=["Monthly health checks"])

MONTHLY_PATH = "/health-checks/monthly"
GROUPS_PATH = MONTHLY_PATH + "/groups"
UPCOMING_LIMIT = 50


def _group_view(group: dict[str, Any], *, company_id: int | None, is_super_admin: bool, can_edit: bool) -> dict[str, Any]:
    return {
        **group,
        "scope": "global" if group.get("company_id") is None else "company",
        "scope_label": "Every company" if group.get("company_id") is None else (group.get("company_name") or "This company"),
        "rules_text": schedule.describe_rules(group.get("target_rules")),
        "has_rules": group.get("target_rules") is not None,
        "can_edit": can_edit and company_id is not None and schedule.can_edit_group(
            group, company_id=company_id, is_super_admin=is_super_admin),
    }


def _blank_group() -> dict[str, Any]:
    return {"id": None, "name": "", "scope": "company", "target_mode": "rules", "target_rules": None,
            "is_enabled": True, "is_default": False}


def _form_group(group: dict[str, Any]) -> dict[str, Any]:
    return {**group, "scope": "global" if group.get("company_id") is None else "company",
            "target_mode": "rules" if group.get("target_rules") is not None else "all"}


async def _resync() -> None:
    try:
        await schedule.resync()
    except Exception as exc:  # noqa: BLE001 - the next due check rechecks the groups anyway
        log_warning("Could not resync monthly health checks", error=str(exc)[:300])


async def _render(request: Request, user: dict[str, Any], company_id: int, can_edit: bool, *,
                  editing: dict[str, Any] | None = None, errors: dict[str, str] | None = None, status_code: int = 200):
    is_super_admin = bool(user.get("is_super_admin"))
    groups = [_group_view(group, company_id=company_id, is_super_admin=is_super_admin, can_edit=can_edit)
              for group in await groups_repo.list_for_company(company_id)]
    upcoming = await schedule_repo.list_for_company(company_id, limit=UPCOMING_LIMIT)
    response = await hc_routes._main()._render_template(
        "health_checks/monthly.html", request, user,
        extra={
            "title": "Monthly health checks",
            "company": await company_repo.get_company_by_id(company_id),
            "groups": groups,
            "upcoming": upcoming,
            "scheduled_count": await schedule_repo.count_for_company(company_id),
            "enabled_groups": sum(1 for group in groups if group["is_enabled"]),
            "monthly_enabled": schedule.enabled(),
            "can_edit": can_edit,
            "is_super_admin": is_super_admin,
            "editing": editing,
            "errors": errors or {},
        },
    )
    response.status_code = status_code
    return response


async def _editable(group_id: int, user: dict[str, Any], company_id: int) -> dict[str, Any]:
    group = await groups_repo.get(group_id)
    if not group or (group.get("company_id") is not None and int(group["company_id"]) != int(company_id)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    if not schedule.can_edit_group(group, company_id=company_id, is_super_admin=bool(user.get("is_super_admin"))):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only super admins can change groups for every company")
    return group


@router.get(MONTHLY_PATH, response_class=HTMLResponse, summary="Monthly health check groups and upcoming checks")
async def monthly_page(
    request: Request,
    group: int | None = Query(default=None, ge=1),
    new: bool = Query(default=False),
):
    user, company_id, can_edit, redirect = await hc_routes._rmm_access(request)
    if redirect:
        return redirect
    editing = None
    if can_edit and group is not None:
        editing = _form_group(await _editable(group, user, int(company_id)))
    elif can_edit and new:
        editing = _blank_group()
    return await _render(request, user, int(company_id), can_edit, editing=editing)


async def _save(request: Request, group_id: int | None):
    user, company_id, _can_edit, redirect = await hc_routes._rmm_access(request, write=True)
    if redirect:
        return redirect
    if group_id is not None:
        await _editable(group_id, user, int(company_id))
    form = await request.form()
    try:
        saved_id = await schedule.save_group(group_id=group_id, company_id=int(company_id), form=form,
                                             user_id=user.get("id"), is_super_admin=bool(user.get("is_super_admin")))
    except schedule.GroupError as exc:
        try:
            rules = json.loads(str(form.get("target_rules") or "null"))
        except (TypeError, ValueError, RecursionError):
            rules = None
        editing = {"id": group_id, "name": str(form.get("name") or ""), "scope": str(form.get("scope") or "company"),
                   "target_mode": str(form.get("target_mode") or "all"), "target_rules": rules,
                   "is_enabled": bool(form.get("is_enabled")), "is_default": False}
        return await _render(request, user, int(company_id), True, editing=editing, errors=exc.errors, status_code=400)
    saved = await groups_repo.get(saved_id)
    await audit_service.log_action(
        action="health_check.group.save", user_id=user.get("id"), entity_type="ai_health_check_group",
        entity_id=saved_id, new_value={"name": saved["name"], "company_id": saved.get("company_id"),
                                       "target_rules": saved.get("target_rules"), "is_enabled": saved["is_enabled"]},
        request=request,
    )
    await _resync()
    return flash_redirect(f"{MONTHLY_PATH}#group-{saved_id}",
                          f"Saved {saved['name']}. Matching computers are scheduled within a few minutes.", "success")


@router.post(GROUPS_PATH, response_class=HTMLResponse, summary="Add a monthly health check group")
async def create_group(request: Request):
    return await _save(request, None)


@router.post(GROUPS_PATH + "/{group_id:int}", response_class=HTMLResponse, summary="Update a monthly health check group")
async def update_group(group_id: int, request: Request):
    return await _save(request, group_id)


@router.post(GROUPS_PATH + "/{group_id:int}/delete", response_class=HTMLResponse,
             summary="Delete a monthly health check group")
async def delete_group(group_id: int, request: Request):
    user, company_id, _can_edit, redirect = await hc_routes._rmm_access(request, write=True)
    if redirect:
        return redirect
    group = await _editable(group_id, user, int(company_id))
    await groups_repo.delete(group_id)
    await audit_service.log_action(
        action="health_check.group.delete", user_id=user.get("id"), entity_type="ai_health_check_group",
        entity_id=group_id, previous_value={"name": group["name"], "company_id": group.get("company_id"),
                                            "target_rules": group.get("target_rules")},
        request=request,
    )
    await _resync()
    return flash_redirect(MONTHLY_PATH, f"Deleted {group['name']}.", "success")


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #


class GroupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    company_id: int | None = Field(default=None, gt=0, description="The company; omit for every company (super admins)")
    target_rules: dict[str, Any] | None = Field(
        default=None, description="Device rules as on RMM checks ({'operator': 'and'|'or', 'children': [...]}); "
                                  "omit for every computer in scope")
    is_enabled: bool = True


def _api_view(group: dict[str, Any]) -> dict[str, Any]:
    return hc_routes._json({**group, "rules_text": schedule.describe_rules(group.get("target_rules"))})


async def _api_save(group_id: int | None, payload: GroupRequest, request: Request, user: dict[str, Any]):
    if payload.company_id and not await company_repo.get_company_by_id(payload.company_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    if group_id is not None:
        existing = await groups_repo.get(group_id)
        if not existing:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        if existing.get("company_id") is None and not user.get("is_super_admin"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only super admins can change groups for every company")
    form = {
        "name": payload.name, "scope": "company" if payload.company_id else "global",
        "target_mode": "rules" if payload.target_rules is not None else "all",
        "target_rules": json.dumps(payload.target_rules) if payload.target_rules is not None else "",
        "is_enabled": "1" if payload.is_enabled else "",
    }
    try:
        saved_id = await schedule.save_group(group_id=group_id, company_id=int(payload.company_id or 0), form=form,
                                             user_id=user.get("id"), is_super_admin=bool(user.get("is_super_admin")))
    except schedule.GroupError as exc:
        code = status.HTTP_403_FORBIDDEN if "scope" in exc.errors else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=code, detail=exc.errors) from exc
    saved = await groups_repo.get(saved_id)
    await audit_service.log_action(
        action="health_check.group.save", user_id=user.get("id"), entity_type="ai_health_check_group",
        entity_id=saved_id, new_value={"name": saved["name"], "company_id": saved.get("company_id"),
                                       "target_rules": saved.get("target_rules"), "is_enabled": saved["is_enabled"]},
        request=request,
    )
    await _resync()
    return saved


@router.get("/api/health-checks/monthly/groups", summary="Monthly health check groups")
async def api_groups(
    company_id: int | None = Query(default=None, gt=0, description="Include this company's own groups"),
    current_user: dict = Depends(require_helpdesk_technician),
):
    return JSONResponse({"groups": [_api_view(group) for group in await groups_repo.list_for_company(company_id)]})


@router.post("/api/health-checks/monthly/groups", status_code=status.HTTP_201_CREATED,
             summary="Add a monthly health check group")
async def api_create_group(payload: GroupRequest, request: Request, current_user: dict = Depends(require_helpdesk_technician)):
    saved = await _api_save(None, payload, request, current_user)
    return JSONResponse({"group": _api_view(saved)}, status_code=status.HTTP_201_CREATED)


@router.put("/api/health-checks/monthly/groups/{group_id:int}", summary="Update a monthly health check group")
async def api_update_group(group_id: int, payload: GroupRequest, request: Request,
                           current_user: dict = Depends(require_helpdesk_technician)):
    return JSONResponse({"group": _api_view(await _api_save(group_id, payload, request, current_user))})


@router.delete("/api/health-checks/monthly/groups/{group_id:int}", summary="Delete a monthly health check group")
async def api_delete_group(group_id: int, request: Request, current_user: dict = Depends(require_helpdesk_technician)):
    group = await groups_repo.get(group_id)
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    if group.get("company_id") is None and not current_user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only super admins can change groups for every company")
    await groups_repo.delete(group_id)
    await audit_service.log_action(
        action="health_check.group.delete", user_id=current_user.get("id"), entity_type="ai_health_check_group",
        entity_id=group_id, previous_value={"name": group["name"], "company_id": group.get("company_id")},
        request=request,
    )
    await _resync()
    return JSONResponse({"status": "deleted"})


__all__ = ["MONTHLY_PATH", "router"]
