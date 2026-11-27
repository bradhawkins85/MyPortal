"""RMM maintenance windows and alert snoozes (:mod:`app.services.rmm_maintenance`).

Technician side (company scoped, ``menu.rmm_scripts``; changing windows needs
write access, and windows for every company need a super admin):

* ``GET /rmm/maintenance`` windows reaching the company: in maintenance now,
  upcoming, recurring and ended; ``?window={id}`` opens one in the editor
* ``POST /rmm/maintenance`` and ``/rmm/maintenance/{id}`` save a window
* ``POST /rmm/maintenance/{id}/end`` ends a one-off window now or switches a
  weekly one off; ``/delete`` removes it
* ``POST /rmm/maintenance/snooze`` snoozes a device's (or one check's) alerts
  from the asset page
* ``GET``/``POST /api/rmm/maintenance-windows``, ``GET``/``PUT``/``DELETE
  /api/rmm/maintenance-windows/{id}`` and ``GET /api/rmm/assets/{id}/maintenance``
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from starlette.datastructures import ImmutableMultiDict

from app.repositories import assets as assets_repo
from app.repositories import rmm as rmm_repo
from app.repositories import rmm_checks as checks_repo
from app.repositories import rmm_maintenance as maintenance_repo
from app.security.flash import flash_redirect
from app.services import audit as audit_service
from app.services import business_hours as business_hours_service
from app.services import rmm_maintenance

from .routes import _assets_routes, _context, _serialise

router = APIRouter(tags=["RMM maintenance windows"])

PAGE_PATH = "/rmm/maintenance"
API_PATH = "/api/rmm/maintenance-windows"


async def _load(request: Request, *, write: bool = False, api: bool = False, asset_id: int | None = None):
    context = await _context(request, api=api, asset_id=asset_id)
    if isinstance(context, RedirectResponse):
        return context
    user, company, company_id, can_edit = context
    if write and not can_edit:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission to change RMM settings is required")
    return user, company, company_id, can_edit


async def _window_for(window_id: int, user: dict[str, Any], company_id: int, *, edit: bool) -> dict[str, Any]:
    window = await maintenance_repo.get(window_id)
    if not window or (window.get("company_id") is not None and int(window["company_id"]) != int(company_id)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Maintenance window not found")
    if edit and not rmm_maintenance.can_edit(window, company_id=company_id,
                                             is_super_admin=bool(user.get("is_super_admin"))):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only super admins can change windows for every company")
    return window


async def _resolve_scope(form: Any, *, company_id: int, is_super_admin: bool) -> dict[str, Any]:
    """Company, device and check from the form, limited to what the technician may target."""

    errors: dict[str, str] = {}
    scope = str(form.get("scope") or "company")
    asset_id = check_id = None
    target_company: int | None = company_id
    if scope == "all":
        if not is_super_admin:
            errors["scope"] = "Only super admins can add windows for every company."
        target_company = None
    elif scope == "device":
        asset_id = rmm_maintenance._int_or_none(form.get("asset_id"))
        asset = await assets_repo.get_asset_by_id(asset_id) if asset_id else None
        if not asset or asset.get("company_id") is None or int(asset["company_id"]) != int(company_id):
            errors["asset_id"] = "Choose one of this company's devices."
            asset_id = None
    elif scope != "company":
        errors["scope"] = "Choose who the window covers."
    raw_check = rmm_maintenance._int_or_none(form.get("check_id"))
    if raw_check:
        check = await checks_repo.get_check(raw_check)
        reachable = check and (check.get("company_id") is None or int(check["company_id"]) == int(company_id))
        if not reachable:
            errors["check_id"] = "Choose one of this company's checks."
        else:
            check_id = raw_check
    if errors:
        raise rmm_maintenance.WindowError(errors)
    return {"company_id": target_company, "asset_id": asset_id, "check_id": check_id}


def _audit_payload(window_id: int, values: dict[str, Any]) -> dict[str, Any]:
    return {"id": window_id, **{key: (value.isoformat() if isinstance(value, datetime) else value)
                                for key, value in values.items()}}


async def _render(request: Request, *, user: dict[str, Any], company: Any, company_id: int, can_edit: bool,
                  editing: dict[str, Any] | None = None, errors: dict[str, str] | None = None,
                  status_code: int = 200):
    is_super_admin = bool(user.get("is_super_admin"))
    windows = await maintenance_repo.list_for_company(company_id)
    groups = rmm_maintenance.group_for_page(windows)
    for items in groups.values():
        for item in items:
            item["can_edit"] = can_edit and rmm_maintenance.can_edit(item, company_id=company_id,
                                                                     is_super_admin=is_super_admin)
    devices = [agent for agent in await rmm_repo.list_company_agents(company_id) if agent.get("asset_id")]
    checks = await checks_repo.list_checks_for_company(company_id)
    response = await _assets_routes()._main()._render_template(
        "rmm/maintenance.html",
        request,
        user,
        extra={
            "title": "Maintenance windows",
            "company": company,
            "groups": groups,
            "window_count": len(windows),
            "editing": editing,
            "errors": errors or {},
            "can_edit": can_edit,
            "is_super_admin": is_super_admin,
            "devices": devices,
            "checks": [{"id": check["id"], "name": check["name"]} for check in checks],
            "weekdays": rmm_maintenance.WEEKDAYS,
            "schedule_types": rmm_maintenance.SCHEDULE_TYPES,
            "timezones": business_hours_service.timezone_options(),
            "default_timezone": business_hours_service.default_timezone_name(),
        },
    )
    response.status_code = status_code
    return response


def _form_state(form: Any, window_id: int | None) -> dict[str, Any]:
    return {
        "id": window_id,
        "name": form.get("name") or "",
        "reason": form.get("reason") or "",
        "scope": form.get("scope") or "company",
        "asset_id": rmm_maintenance._int_or_none(form.get("asset_id")),
        "check_id": rmm_maintenance._int_or_none(form.get("check_id")),
        "schedule_type": form.get("schedule_type") or "once",
        "starts_local": form.get("starts_at") or "",
        "ends_local": form.get("ends_at") or "",
        "weekday_list": rmm_maintenance.parse_weekdays(form.getlist("weekdays")),
        "start_time": form.get("start_time") or "",
        "duration_minutes": form.get("duration_minutes") or "",
        "timezone": form.get("timezone") or "",
        "is_enabled": rmm_maintenance._enabled(form),
    }


def _editor_state(window: dict[str, Any]) -> dict[str, Any]:
    from zoneinfo import ZoneInfo

    zone = rmm_maintenance._zone(window.get("timezone"))

    def local(value: Any) -> str:
        utc = rmm_maintenance._naive_utc(value)
        if utc is None:
            return ""
        return utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(zone).strftime("%Y-%m-%dT%H:%M")

    scope = "device" if window.get("asset_id") else ("company" if window.get("company_id") else "all")
    return {
        "id": window["id"], "name": window["name"], "reason": window.get("reason") or "", "scope": scope,
        "asset_id": window.get("asset_id"), "check_id": window.get("check_id"),
        "schedule_type": window.get("schedule_type") or "once",
        "starts_local": local(window.get("starts_at")), "ends_local": local(window.get("ends_at")),
        "weekday_list": rmm_maintenance.parse_weekdays(window.get("weekdays")),
        "start_time": window.get("start_time") or "", "duration_minutes": window.get("duration_minutes") or "",
        "timezone": window.get("timezone") or "", "is_enabled": window.get("is_enabled", True),
    }


@router.get(PAGE_PATH, response_class=HTMLResponse, summary="RMM maintenance windows")
async def maintenance_page(request: Request, window: int | None = Query(default=None, ge=1),
                           new: bool = Query(default=False)):
    context = await _load(request)
    if isinstance(context, RedirectResponse):
        return context
    user, company, company_id, can_edit = context
    editing = None
    if can_edit and window is not None:
        editing = _editor_state(await _window_for(window, user, company_id, edit=True))
    elif can_edit and new:
        editing = {"id": None, "schedule_type": "once", "scope": "company", "is_enabled": True}
    return await _render(request, user=user, company=company, company_id=company_id, can_edit=can_edit,
                         editing=editing)


async def _save(request: Request, window_id: int | None):
    context = await _load(request, write=True)
    if isinstance(context, RedirectResponse):
        return context
    user, company, company_id, can_edit = context
    previous = await _window_for(window_id, user, company_id, edit=True) if window_id is not None else None
    form = await request.form()
    is_super_admin = bool(user.get("is_super_admin"))
    try:
        values = rmm_maintenance.parse_form(form if window_id is None else _with_id(form, window_id))
        values.update(await _resolve_scope(form, company_id=company_id, is_super_admin=is_super_admin))
    except rmm_maintenance.WindowError as exc:
        return await _render(request, user=user, company=company, company_id=company_id, can_edit=can_edit,
                             editing=_form_state(form, window_id), errors=exc.errors, status_code=400)
    if previous is not None:
        values["is_snooze"] = previous.get("is_snooze", False)
        await rmm_maintenance.update(window_id, values)
        saved_id = window_id
    else:
        saved_id = await rmm_maintenance.create(values, user_id=user.get("id"))
    await audit_service.record(
        action="rmm.maintenance.save", request=request, user_id=user.get("id"),
        entity_type="rmm_maintenance_window", entity_id=saved_id,
        metadata=_audit_payload(saved_id, values),
    )
    return flash_redirect(PAGE_PATH, f"Saved {values['name']}.", "success")


def _with_id(form: Any, window_id: int) -> Any:
    items = list(form.multi_items()) + [("id", str(window_id))]
    return ImmutableMultiDict(items)


@router.post(PAGE_PATH, response_class=HTMLResponse, summary="Add a maintenance window")
async def create_window(request: Request):
    return await _save(request, None)


@router.post(PAGE_PATH + "/{window_id:int}", response_class=HTMLResponse, summary="Update a maintenance window")
async def update_window(window_id: int, request: Request):
    return await _save(request, window_id)


def _back(form: Any) -> str:
    value = str(form.get("return_asset_id") or "")
    return f"/assets/{int(value)}#checks" if value.isdigit() else PAGE_PATH


@router.post(PAGE_PATH + "/{window_id:int}/end", response_class=HTMLResponse, summary="End a maintenance window now")
async def end_window(window_id: int, request: Request):
    context = await _load(request, write=True)
    if isinstance(context, RedirectResponse):
        return context
    user, _company, company_id, _can_edit = context
    window = await _window_for(window_id, user, company_id, edit=True)
    form = await request.form()
    await rmm_maintenance.end_now(window_id)
    await audit_service.record(action="rmm.maintenance.end", request=request, user_id=user.get("id"),
                               entity_type="rmm_maintenance_window", entity_id=window_id,
                               metadata={"name": window["name"]})
    verb = "Ended" if window.get("schedule_type") == "once" else "Switched off"
    return flash_redirect(_back(form), f"{verb} {window['name']}. Alerts are back on.", "success")


@router.post(PAGE_PATH + "/{window_id:int}/delete", response_class=HTMLResponse, summary="Delete a maintenance window")
async def delete_window(window_id: int, request: Request):
    context = await _load(request, write=True)
    if isinstance(context, RedirectResponse):
        return context
    user, _company, company_id, _can_edit = context
    window = await _window_for(window_id, user, company_id, edit=True)
    form = await request.form()
    await rmm_maintenance.delete(window_id)
    await audit_service.record(action="rmm.maintenance.delete", request=request, user_id=user.get("id"),
                               entity_type="rmm_maintenance_window", entity_id=window_id,
                               metadata={"name": window["name"]})
    return flash_redirect(_back(form), f"Deleted {window['name']}.", "success")


@router.post(PAGE_PATH + "/snooze", response_class=HTMLResponse, summary="Snooze a device's alerts")
async def snooze(request: Request):
    form = await request.form()
    asset_id = rmm_maintenance._int_or_none(form.get("asset_id"))
    if asset_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Choose a device")
    context = await _load(request, write=True, asset_id=asset_id)
    if isinstance(context, RedirectResponse):
        return context
    user, _company, company_id, _can_edit = context
    asset = await assets_repo.get_asset_by_id(asset_id)
    if not asset or asset.get("company_id") is None or int(asset["company_id"]) != int(company_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    check_id = rmm_maintenance._int_or_none(form.get("check_id"))
    check = await checks_repo.get_check(check_id) if check_id else None
    if check_id and (not check or (check.get("company_id") is not None and int(check["company_id"]) != int(company_id))):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Check not found")
    minutes = rmm_maintenance._int_or_none(form.get("minutes")) or 0
    device = asset.get("name") or f"Asset {asset_id}"
    name = f"Snoozed: {check['name']} on {device}" if check else f"Snoozed: {device}"
    back = f"/assets/{asset_id}#checks"
    try:
        values = rmm_maintenance.snooze_values(name=name, company_id=company_id, asset_id=asset_id,
                                               check_id=check_id, minutes=minutes, reason=form.get("reason"))
    except rmm_maintenance.WindowError as exc:
        return flash_redirect(back, "; ".join(exc.errors.values()), "error")
    window_id = await rmm_maintenance.create(values, user_id=user.get("id"))
    await audit_service.record(action="rmm.maintenance.snooze", request=request, user_id=user.get("id"),
                               entity_type="rmm_maintenance_window", entity_id=window_id,
                               metadata=_audit_payload(window_id, values))
    label = dict(rmm_maintenance.SNOOZE_CHOICES)[minutes]
    what = f"{check['name']} alerts" if check else "Alerts"
    return flash_redirect(back, f"{what} for {device} snoozed for {label}.", "success")


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #


class MaintenanceWindowIn(BaseModel):
    name: str = Field(min_length=1, max_length=rmm_maintenance.MAX_NAME_LENGTH)
    reason: str | None = Field(default=None, max_length=rmm_maintenance.MAX_REASON_LENGTH)
    scope: str = Field(default="company", description="all (super admins), company or device")
    asset_id: int | None = None
    check_id: int | None = Field(default=None, description="Limit the window to one check; omit for all checks")
    schedule_type: str = Field(default="once", description="once or weekly")
    starts_at: str | None = Field(default=None, description="One-off start, ISO 8601 (local to timezone if no offset)")
    ends_at: str | None = Field(default=None, description="One-off end, ISO 8601")
    weekdays: list[str] = Field(default_factory=list, description="Weekly: mon … sun")
    start_time: str | None = Field(default=None, description="Weekly: local start time, HH:MM")
    duration_minutes: int | None = Field(default=None, description="Weekly: length in minutes")
    timezone: str = "UTC"
    is_enabled: bool = True


def _api_form(payload: MaintenanceWindowIn, window_id: int | None = None) -> ImmutableMultiDict:
    items: list[tuple[str, str]] = []
    for key, value in payload.model_dump().items():
        if key == "weekdays":
            items.extend(("weekdays", str(day)) for day in value)
        elif key == "is_enabled":
            items.append((key, "1" if value else "0"))
        elif value is not None:
            items.append((key, str(value)))
    if window_id is not None:
        items.append(("id", str(window_id)))
    return ImmutableMultiDict(items)


def _api_window(window: dict[str, Any]) -> dict[str, Any]:
    summary = rmm_maintenance.summarise(window)
    keys = ("id", "name", "reason", "company_id", "asset_id", "check_id", "schedule_type", "starts_at", "ends_at",
            "weekday_list", "start_time", "duration_minutes", "timezone", "is_snooze", "is_enabled", "state",
            "active_until", "next_start", "schedule_label", "scope_label")
    return _serialise({key: summary.get(key) for key in keys}) or {}


@router.get(API_PATH, summary="List maintenance windows")
async def api_list_windows(request: Request) -> list[dict[str, Any]]:
    _user, _company, company_id, _can_edit = await _load(request, api=True)
    return [_api_window(window) for window in await maintenance_repo.list_for_company(company_id)]


@router.get(API_PATH + "/{window_id}", summary="Get a maintenance window")
async def api_get_window(window_id: int, request: Request) -> dict[str, Any]:
    user, _company, company_id, _can_edit = await _load(request, api=True)
    return _api_window(await _window_for(window_id, user, company_id, edit=False))


async def _api_save(request: Request, payload: MaintenanceWindowIn, window_id: int | None) -> dict[str, Any]:
    user, _company, company_id, _can_edit = await _load(request, write=True, api=True)
    previous = await _window_for(window_id, user, company_id, edit=True) if window_id is not None else None
    form = _api_form(payload, window_id)
    try:
        values = rmm_maintenance.parse_form(form)
        values.update(await _resolve_scope(form, company_id=company_id,
                                           is_super_admin=bool(user.get("is_super_admin"))))
    except rmm_maintenance.WindowError as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from exc
    if previous is not None:
        values["is_snooze"] = previous.get("is_snooze", False)
        await rmm_maintenance.update(window_id, values)
        saved_id = window_id
    else:
        saved_id = await rmm_maintenance.create(values, user_id=user.get("id"))
    await audit_service.record(action="rmm.maintenance.save", request=request, user_id=user.get("id"),
                               entity_type="rmm_maintenance_window", entity_id=saved_id,
                               metadata=_audit_payload(saved_id, values))
    return _api_window(await maintenance_repo.get(saved_id) or {})


@router.post(API_PATH, summary="Add a maintenance window", status_code=status.HTTP_201_CREATED)
async def api_create_window(payload: MaintenanceWindowIn, request: Request) -> dict[str, Any]:
    return await _api_save(request, payload, None)


@router.put(API_PATH + "/{window_id}", summary="Update a maintenance window")
async def api_update_window(window_id: int, payload: MaintenanceWindowIn, request: Request) -> dict[str, Any]:
    return await _api_save(request, payload, window_id)


@router.delete(API_PATH + "/{window_id}", summary="Delete a maintenance window", status_code=status.HTTP_204_NO_CONTENT)
async def api_delete_window(window_id: int, request: Request) -> None:
    user, _company, company_id, _can_edit = await _load(request, write=True, api=True)
    window = await _window_for(window_id, user, company_id, edit=True)
    await rmm_maintenance.delete(window_id)
    await audit_service.record(action="rmm.maintenance.delete", request=request, user_id=user.get("id"),
                               entity_type="rmm_maintenance_window", entity_id=window_id,
                               metadata={"name": window["name"]})


@router.get("/api/rmm/assets/{asset_id}/maintenance", summary="Whether a device is in maintenance now")
async def api_asset_maintenance(asset_id: int, request: Request) -> dict[str, Any]:
    _user, _company, company_id, _can_edit = await _load(request, api=True, asset_id=asset_id)
    found = await rmm_maintenance.coverage(company_id=company_id, asset_id=asset_id)
    return {
        "asset_id": asset_id,
        "in_maintenance": bool(found),
        "until": found.until.isoformat() if found.until else None,
        "windows": [_api_window(window) for window in found.windows],
    }


async def asset_maintenance_context(company_id: int, asset_id: int) -> dict[str, Any]:
    """The asset page's Checks section: windows holding the device's alerts now (all checks or one)."""

    windows = [window for window in await maintenance_repo.list_for_company(company_id)
               if rmm_maintenance.covers(window, company_id=company_id, asset_id=asset_id,
                                         check_id=window.get("check_id"))]
    active = [rmm_maintenance.summarise(window) for window in windows if rmm_maintenance.active_until(window)]
    return {
        "active": active,
        "snooze_choices": rmm_maintenance.SNOOZE_CHOICES,
    }


__all__ = ["router", "asset_maintenance_context"]
