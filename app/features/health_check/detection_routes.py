"""Health check detections: what the automatic checks find, how to watch for it and the scripts that fix it.

Users with RMM scripts access see every detection with its suggested event
log monitors and scripts (:mod:`app.services.health_check_detections`), and
the scripts and tickets linked to it (:mod:`app.services.health_check_remediation`)
for the company they are working in, and the company's assets whose latest
health check reports it. Write access links a script, or a ticket for a
technician to investigate, for that company; super admins also link them for
every company.

* ``GET /health-checks/detections`` the list; ``?edit={key}&scope=company|global``
  opens the editor for one detection's script
* ``POST /health-checks/detections/{key}`` link a script and/or a ticket (or
  neither, to turn the every-company link off for the company)
* ``POST /health-checks/detections/{key}/remove`` remove a link
* ``GET /api/health-checks/detections``, ``PUT`` and ``DELETE
  /api/health-checks/detections/{key}/remediation`` (helpdesk technicians
  and super admins) the same as JSON
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from app.api.dependencies.auth import require_helpdesk_technician
from app.core.logging import log_warning
from app.repositories import ai_health_check_remediations as remediation_repo
from app.repositories import ai_health_checks as checks_repo
from app.repositories import companies as company_repo
from app.repositories import rmm as rmm_repo
from app.security.flash import flash_redirect
from app.services import ai_health_check as service
from app.services import audit as audit_service
from app.services import health_check_detections as detections
from app.services import health_check_remediation as remediation
from app.services import rmm_event_logs
from app.services import rmm_scripts

from . import routes as hc_routes

router = APIRouter(tags=["Health check detections"])

DETECTIONS_PATH = "/health-checks/detections"
EVENT_LOG_RULES_PATH = "/rmm/event-logs/rules"
_KEY_PATTERN = "^[a-z0-9_]{1,64}$"


def suggest_url(monitor_key: str, *, asset_id: int | None = None) -> str:
    """The event log rule form, filled in from a catalog monitor."""

    url = f"{EVENT_LOG_RULES_PATH}?new=1&suggest={monitor_key}"
    return url + (f"&asset_id={int(asset_id)}" if asset_id else "")


def _link_view(link: dict[str, Any] | None) -> dict[str, Any] | None:
    if not link:
        return None
    return {
        "id": link["id"],
        "script_id": link.get("script_id"),
        "script_name": link.get("script_name"),
        "script_path": link.get("script_path"),
        "script_active": bool(link.get("script_active")) and bool(link.get("script_id")),
        "is_enabled": bool(link.get("is_enabled")),
        "create_ticket": bool(link.get("create_ticket")),
        "timeout_seconds": link.get("timeout_seconds"),
        "run_count": int(link.get("run_count") or 0),
        "last_run_at": link.get("last_run_at"),
        "last_summary": link.get("last_summary"),
    }


async def _collected_logs(company_id: int | None) -> list[str]:
    try:
        return await rmm_event_logs.logs_for(company_id, None)
    except Exception as exc:  # noqa: BLE001 - fall back to the default logs
        log_warning("Could not read the company's collected event logs", company_id=company_id, error=str(exc)[:300])
        return rmm_event_logs.default_logs()


_SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}


def reporting_assets(checks: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """The assets whose latest completed health check reports each detection, by detection key."""

    found: dict[str, list[dict[str, Any]]] = {}
    for check in checks:
        report = check.get("report") if isinstance(check.get("report"), dict) else {}
        signals = [s for s in report.get("signals") or [] if isinstance(s, dict) and s.get("key")]
        by_key: dict[str, list[dict[str, Any]]] = {}
        for signal in signals:
            by_key.setdefault(str(signal["key"]), []).append(signal)
        for key, matched in by_key.items():
            worst = min(matched, key=lambda s: _SEVERITY_RANK.get(str(s.get("severity")), 9))
            found.setdefault(key, []).append({
                "asset_id": check.get("asset_id"),
                "asset_name": check.get("asset_name") or f"Asset {check.get('asset_id')}",
                "check_id": check.get("id"),
                "completed_at": check.get("completed_at"),
                "severity": worst.get("severity"),
                "titles": [str(s.get("title") or "") for s in matched if s.get("title")],
            })
    return found


async def _reporting_for(company_id: int | None) -> dict[str, list[dict[str, Any]]]:
    if company_id is None:
        return {}
    return reporting_assets(await checks_repo.latest_completed_for_company(int(company_id)))


def _detection_view(
    key: str, links: list[dict[str, Any]], company_id: int | None, collected: list[str],
    reporting: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    item = detections.detection(key) or {}
    matching = [link for link in links if link.get("detection_key") == key]
    global_link = next((link for link in matching if link.get("company_id") is None), None)
    company_link = next((link for link in matching if company_id is not None and link.get("company_id") is not None
                         and int(link["company_id"]) == int(company_id)), None)
    effective = company_link or global_link
    monitors = [detections.present_monitor(detections.monitor(name), collected) for name in item.get("monitors", [])]
    return {
        **item,
        "area_label": service.AREAS.get(item.get("area", ""), item.get("area", "")),
        "global_link": _link_view(global_link),
        "company_link": _link_view(company_link),
        "runs_script": bool(effective and effective.get("is_enabled") and effective.get("script_id")),
        "raises_ticket": bool(effective and effective.get("is_enabled") and effective.get("create_ticket")),
        "assets": (reporting or {}).get(key, []),
        "monitors": [{**m, "url": suggest_url(m["key"])} for m in monitors],
        "search": " ".join([key, item.get("label", ""), item.get("description", ""), item.get("remedy", ""),
                            *(m["name"] for m in monitors)]),
    }


def catalog_view(
    links: list[dict[str, Any]], company_id: int | None, collected: list[str],
    reporting: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Every detection with its links, monitors and the assets reporting it, grouped by area."""

    groups = []
    for area, label in service.AREAS.items():
        items = [_detection_view(key, links, company_id, collected, reporting)
                 for key, item in detections.DETECTIONS.items() if item["area"] == area]
        if items:
            groups.append({"key": area, "label": label, "items": items})
    return groups


async def _scripts_for(scope: str, company_id: int) -> list[dict[str, Any]]:
    if scope == "global":
        return [script for script in await rmm_repo.list_scripts() if script.get("company_id") is None]
    return await rmm_repo.list_scripts(company_id=company_id)


async def _editor(key: str, scope: str, company_id: int, *, posted: dict[str, Any] | None = None,
                  errors: dict[str, str] | None = None) -> dict[str, Any]:
    link = await remediation_repo.get_for_scope(key, None if scope == "global" else company_id, with_secrets=True)
    script_id = service._int_or_none((posted or {}).get("script_id") if posted is not None else (link or {}).get("script_id"))
    script = await rmm_repo.get_script(script_id) if script_id else None
    same_script = bool(link and script and link.get("script_id") == script.get("id"))
    return {
        "key": key,
        "detection": detections.detection(key),
        "scope": scope,
        "link": _link_view(link),
        "script_id": script_id,
        "script": script if same_script else None,
        "fields": remediation.script_values(link, script) if same_script else [],
        "timeout_seconds": (posted or {}).get("timeout_seconds") or (link or {}).get("timeout_seconds") or 600,
        "is_enabled": bool((posted or {}).get("is_enabled")) if posted is not None else (
            bool(link.get("is_enabled")) if link else True),
        "create_ticket": bool((posted or {}).get("create_ticket")) if posted is not None else (
            bool(link.get("create_ticket")) if link else False),
        "scripts": await _scripts_for(scope, company_id),
        "errors": errors or {},
    }


async def _render(request: Request, user: dict[str, Any], company_id: int, can_edit: bool, *,
                  editor: dict[str, Any] | None = None, status_code: int = 200):
    company = await company_repo.get_company_by_id(company_id)
    links = await remediation_repo.list_for_company(company_id)
    groups = catalog_view(links, company_id, await _collected_logs(company_id), await _reporting_for(company_id))
    variables = [{"group": "Health check", "label": label, "token": token}
                 for label, token in remediation.HEALTH_VARIABLES]
    if editor:
        variables += await rmm_scripts.available_variables()
    response = await hc_routes._main()._render_template(
        "health_checks/detections.html", request, user,
        extra={
            "title": "Health check detections",
            "company": company,
            "groups": groups,
            "counts": {
                "detections": len(detections.DETECTIONS),
                "with_script": sum(1 for group in groups for item in group["items"] if item["runs_script"]),
                "with_ticket": sum(1 for group in groups for item in group["items"] if item["raises_ticket"]),
                "reported": sum(1 for group in groups for item in group["items"] if item["assets"]),
                "with_remedy": sum(1 for item in detections.DETECTIONS.values() if item.get("remedy")),
                "with_monitors": sum(1 for item in detections.DETECTIONS.values() if item.get("monitors")),
            },
            "can_edit": can_edit,
            "is_super_admin": bool(user.get("is_super_admin")),
            "editor": editor,
            "variables": variables,
        },
    )
    response.status_code = status_code
    return response


@router.get(DETECTIONS_PATH, response_class=HTMLResponse, summary="Health check detections and their fixes")
async def detections_page(
    request: Request,
    edit: str | None = Query(default=None, pattern=_KEY_PATTERN),
    scope: str = Query(default="company", pattern="^(company|global)$"),
):
    user, company_id, can_edit, redirect = await hc_routes._rmm_access(request)
    if redirect:
        return redirect
    editor = None
    if edit and can_edit and edit in detections.DETECTIONS and (scope == "company" or user.get("is_super_admin")):
        editor = await _editor(edit, scope, int(company_id))
    return await _render(request, user, int(company_id), can_edit, editor=editor)


def _check_key(key: str) -> None:
    if key not in detections.DETECTIONS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Detection not found")


@router.post(DETECTIONS_PATH + "/{key}", response_class=HTMLResponse, summary="Link a script to a detection")
async def save_link(key: str, request: Request):
    user, company_id, _can_edit, redirect = await hc_routes._rmm_access(request, write=True)
    if redirect:
        return redirect
    _check_key(key)
    form = await request.form()
    scope = str(form.get("scope") or "company")
    if scope not in remediation.SCOPES:
        scope = "company"
    try:
        link_id = await remediation.save_link(
            detection_key=key, scope=scope, company_id=int(company_id), form=form, user_id=user.get("id"),
            is_super_admin=bool(user.get("is_super_admin")),
        )
    except remediation.RemediationError as exc:
        posted = {"script_id": str(form.get("script_id") or "").strip() or None,
                  "timeout_seconds": form.get("timeout_seconds"), "is_enabled": bool(form.get("is_enabled")),
                  "create_ticket": bool(form.get("create_ticket"))}
        if scope == "global" and not user.get("is_super_admin"):
            return flash_redirect(DETECTIONS_PATH, str(exc), "error")
        editor = await _editor(key, scope, int(company_id), posted=posted, errors=exc.errors)
        return await _render(request, user, int(company_id), True, editor=editor, status_code=400)
    saved = await remediation_repo.get(link_id)
    await audit_service.log_action(
        action="health_check.remediation.save", user_id=user.get("id"), entity_type="ai_health_check_remediation",
        entity_id=link_id, new_value={"detection": key, "company_id": saved.get("company_id"),
                                      "script_id": saved.get("script_id"), "is_enabled": saved.get("is_enabled"),
                                      "create_ticket": saved.get("create_ticket")},
        request=request,
    )
    label = detections.DETECTIONS[key]["label"]
    target = f"{DETECTIONS_PATH}#detection-{key}"
    if not saved.get("script_id") and saved.get("create_ticket"):
        message = f"Saved. A ticket is raised when a health check finds “{label}”."
    elif not saved.get("script_id"):
        message = f"No script runs for “{label}” in this company."
    else:
        message = f"Saved. {saved.get('script_name') or 'The script'} runs when a health check finds “{label}”."
        script = await rmm_repo.get_script(int(saved["script_id"]))
        if script and rmm_scripts.script_fields(script):
            message += " Check the script's values."
            target = f"{DETECTIONS_PATH}?edit={key}&scope={scope}#hc-remediation-modal"
    return flash_redirect(target, message, "success")


@router.post(DETECTIONS_PATH + "/{key}/remove", response_class=HTMLResponse, summary="Remove a detection's script")
async def remove_link(key: str, request: Request):
    user, company_id, _can_edit, redirect = await hc_routes._rmm_access(request, write=True)
    if redirect:
        return redirect
    _check_key(key)
    form = await request.form()
    scope = str(form.get("scope") or "company")
    if scope == "global" and not user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only super admins can change every company's scripts")
    link = await remediation_repo.get_for_scope(key, None if scope == "global" else int(company_id))
    if link:
        await remediation_repo.delete(int(link["id"]))
        await audit_service.log_action(
            action="health_check.remediation.remove", user_id=user.get("id"), entity_type="ai_health_check_remediation",
            entity_id=int(link["id"]), previous_value={"detection": key, "company_id": link.get("company_id"),
                                                       "script_id": link.get("script_id")},
            request=request,
        )
    return flash_redirect(f"{DETECTIONS_PATH}#detection-{key}", "Removed.", "success")


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #


@router.get("/api/health-checks/detections", summary="Health check detections, monitors and linked scripts")
async def api_detections(
    company_id: int | None = Query(default=None, gt=0, description="Include this company's own scripts"),
    current_user: dict = Depends(require_helpdesk_technician),
):
    links = await remediation_repo.list_for_company(company_id)
    groups = catalog_view(links, company_id, await _collected_logs(company_id), await _reporting_for(company_id))
    return JSONResponse({"detections": hc_routes._json([item for group in groups for item in group["items"]])})


class RemediationRequest(BaseModel):
    company_id: int | None = Field(default=None, gt=0, description="The company; omit for every company (super admins)")
    script_id: int | None = Field(default=None, gt=0, description="The script; omit to run none for the company")
    create_ticket: bool = Field(default=False, description="Raise a ticket, linked to the asset, for a technician")
    timeout_seconds: int | None = Field(default=None, ge=10, le=86400)
    is_enabled: bool = True
    values: dict[str, str] = Field(default_factory=dict, description="Script values by field key")


@router.put("/api/health-checks/detections/{key}/remediation", summary="Link a script to a detection")
async def api_save_link(
    key: str, payload: RemediationRequest, request: Request, current_user: dict = Depends(require_helpdesk_technician),
):
    _check_key(key)
    form: dict[str, Any] = {
        "script_id": payload.script_id or "", "timeout_seconds": payload.timeout_seconds or "",
        "is_enabled": "1" if payload.is_enabled else "",
        "create_ticket": "1" if payload.create_ticket else "",
        **{"entry:" + name: value for name, value in payload.values.items()},
    }
    scope = "company" if payload.company_id else "global"
    if payload.company_id and not await company_repo.get_company_by_id(payload.company_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    try:
        link_id = await remediation.save_link(
            detection_key=key, scope=scope, company_id=int(payload.company_id or 0), form=form,
            user_id=current_user.get("id"), is_super_admin=bool(current_user.get("is_super_admin")),
        )
    except remediation.RemediationError as exc:
        code = status.HTTP_403_FORBIDDEN if "scope" in exc.errors else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=code, detail=exc.errors) from exc
    saved = await remediation_repo.get(link_id)
    await audit_service.log_action(
        action="health_check.remediation.save", user_id=current_user.get("id"),
        entity_type="ai_health_check_remediation", entity_id=link_id,
        new_value={"detection": key, "company_id": saved.get("company_id"), "script_id": saved.get("script_id"),
                   "is_enabled": saved.get("is_enabled"), "create_ticket": saved.get("create_ticket")},
        request=request,
    )
    return JSONResponse({"remediation": hc_routes._json(_link_view(saved))})


@router.delete("/api/health-checks/detections/{key}/remediation", summary="Remove a detection's script")
async def api_remove_link(
    key: str,
    request: Request,
    company_id: int | None = Query(default=None, gt=0, description="The company; omit for every company"),
    current_user: dict = Depends(require_helpdesk_technician),
):
    _check_key(key)
    if company_id is None and not current_user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only super admins can change every company's scripts")
    link = await remediation_repo.get_for_scope(key, company_id)
    if not link:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No script is linked")
    await remediation_repo.delete(int(link["id"]))
    await audit_service.log_action(
        action="health_check.remediation.remove", user_id=current_user.get("id"),
        entity_type="ai_health_check_remediation", entity_id=int(link["id"]),
        previous_value={"detection": key, "company_id": link.get("company_id"), "script_id": link.get("script_id")},
        request=request,
    )
    return JSONResponse({"status": "removed"})


__all__ = ["DETECTIONS_PATH", "catalog_view", "reporting_assets", "router", "suggest_url"]
