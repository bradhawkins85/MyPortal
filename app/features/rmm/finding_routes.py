"""RMM findings (:mod:`app.services.rmm_findings`).

Findings look across every company, so the page and APIs are for super admins:

* ``GET /rmm/findings`` the findings from the latest analyses, the last
  analysis and its data sources; ``?view=all`` includes findings no longer
  detected
* ``POST /rmm/findings/run`` start an analysis now
* ``POST /rmm/findings/{id}/status`` acknowledge, dismiss or reopen a finding
* ``GET /api/rmm/findings``, ``GET /api/rmm/findings/runs/latest``,
  ``POST /api/rmm/findings/runs`` and ``PATCH /api/rmm/findings/{id}`` the
  same as JSON
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.repositories import rmm_findings as repo
from app.security.flash import flash_redirect
from app.services import audit as audit_service
from app.services import rmm_findings as findings_service

from .routes import _assets_routes

router = APIRouter(tags=["RMM findings"])

PAGE_PATH = "/rmm/findings"


async def _page_user(request: Request):
    """The super admin, or a redirect to sign in."""

    user, redirect = await _assets_routes()._main()._require_super_admin_page(request)
    return redirect or user


async def _api_user(request: Request) -> dict[str, Any]:
    user = await _page_user(request)
    if isinstance(user, RedirectResponse):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return user


async def _findings(*, include_inactive: bool) -> list[dict[str, Any]]:
    rows = await repo.list_findings(include_inactive=include_inactive)
    names = await repo.names_for(
        asset_ids=sorted({asset_id for row in rows for asset_id in row["asset_ids"]}),
        company_ids=sorted({company_id for row in rows for company_id in row["company_ids"]}),
    )
    findings = [findings_service.present(row, names) for row in rows]
    findings.sort(key=lambda f: (not f["is_active"], f["status"] == "dismissed", f["severity_rank"],
                                 -(f["confidence"] or 0), f["title"].lower()))
    return findings


def _counts(findings: list[dict[str, Any]]) -> dict[str, int]:
    counts = {severity: 0 for severity in findings_service.SEVERITIES}
    for finding in findings:
        if finding["is_active"] and finding["status"] != "dismissed":
            counts[finding["severity"]] = counts.get(finding["severity"], 0) + 1
    return counts


def _settings_summary() -> dict[str, Any]:
    settings = get_settings()
    llm = findings_service.build_llm_config()
    return {
        "enabled": bool(settings.findings_enabled),
        "ai_enabled": bool(settings.findings_ai_enabled),
        "interval_hours": int(settings.findings_interval_hours),
        "baseline_days": int(settings.findings_baseline_days),
        "model": llm.get("model") if settings.findings_ai_enabled else None,
    }


@router.get(PAGE_PATH, response_class=HTMLResponse, summary="Review platform-wide RMM findings")
async def findings_page(request: Request, view: str = Query(default="active", pattern="^(active|all)$")):
    user = await _page_user(request)
    if isinstance(user, RedirectResponse):
        return user
    findings = await _findings(include_inactive=view == "all")
    latest = findings_service.run_summary(await repo.get_latest_run())
    counts = _counts(findings)
    present_categories = {finding["category"] for finding in findings}
    return await _assets_routes()._main()._render_template(
        "rmm/findings.html",
        request,
        user,
        extra={
            "title": "Findings",
            "findings": findings,
            "view": view,
            "counts": counts,
            "open_total": sum(counts.values()),
            "latest_run": latest,
            "settings_summary": _settings_summary(),
            "categories": [(key, label) for key, label in findings_service.CATEGORIES.items() if key in present_categories],
            "severities": findings_service.SEVERITIES,
            "statuses": findings_service.STATUSES,
            "severity_variants": findings_service.SEVERITY_VARIANTS,
        },
    )


@router.post(f"{PAGE_PATH}/run", response_class=HTMLResponse, summary="Start an RMM findings analysis now")
async def run_now(request: Request):
    user = await _page_user(request)
    if isinstance(user, RedirectResponse):
        return user
    try:
        run_id = await findings_service.start_analysis(trigger_source="manual", user_id=user.get("id"))
    except findings_service.AnalysisRunning as exc:
        return flash_redirect(PAGE_PATH, str(exc), "warning")
    await audit_service.log_action(action="rmm.findings.run", user_id=user.get("id"), entity_type="rmm_finding_run",
                                   entity_id=run_id, request=request)
    return flash_redirect(PAGE_PATH, "Analysis started. Findings update here when it finishes.", "success")


async def _change_status(finding_id: int, new_status: str, note: str, user: dict[str, Any], request: Request) -> dict[str, Any]:
    if new_status not in findings_service.STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown status")
    finding = await repo.get_finding(finding_id)
    if not finding:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Finding not found")
    await repo.set_status(finding_id, status=new_status, note=note.strip()[:500] or None, user_id=user.get("id"))
    await audit_service.log_action(
        action="rmm.findings.status", user_id=user.get("id"), entity_type="rmm_finding", entity_id=finding_id,
        previous_value={"status": finding.get("status")}, new_value={"status": new_status}, request=request,
    )
    return finding


@router.post(f"{PAGE_PATH}/{{finding_id}}/status", response_class=HTMLResponse, summary="Acknowledge, dismiss or reopen a finding")
async def change_status(request: Request, finding_id: int):
    user = await _page_user(request)
    if isinstance(user, RedirectResponse):
        return user
    form = await request.form()
    new_status = str(form.get("status") or "")
    finding = await _change_status(finding_id, new_status, str(form.get("note") or ""), user, request)
    view = "all" if str(form.get("view") or "") == "all" else "active"
    message = {"open": "Finding reopened.", "acknowledged": "Finding acknowledged.", "dismissed": "Finding dismissed."}
    return flash_redirect(f"{PAGE_PATH}?view={view}#finding-{int(finding['id'])}", message[new_status], "success")


class StatusChange(BaseModel):
    status: str = Field(pattern="^(open|acknowledged|dismissed)$")
    note: str = Field(default="", max_length=500)


@router.get("/api/rmm/findings", summary="List RMM findings")
async def api_findings(request: Request, include_inactive: bool = Query(default=False)):
    await _api_user(request)
    findings = await _findings(include_inactive=include_inactive)
    return JSONResponse({"findings": findings, "counts": _counts(findings)})


@router.get("/api/rmm/findings/runs/latest", summary="The latest RMM findings analysis")
async def api_latest_run(request: Request):
    await _api_user(request)
    return JSONResponse({"run": findings_service.run_summary(await repo.get_latest_run())})


@router.post("/api/rmm/findings/runs", status_code=status.HTTP_202_ACCEPTED, summary="Start an RMM findings analysis")
async def api_start_run(request: Request):
    user = await _api_user(request)
    try:
        run_id = await findings_service.start_analysis(trigger_source="manual", user_id=user.get("id"))
    except findings_service.AnalysisRunning as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await audit_service.log_action(action="rmm.findings.run", user_id=user.get("id"), entity_type="rmm_finding_run",
                                   entity_id=run_id, request=request)
    return JSONResponse({"run": findings_service.run_summary(await repo.get_run(run_id))},
                        status_code=status.HTTP_202_ACCEPTED)


@router.patch("/api/rmm/findings/{finding_id}", summary="Acknowledge, dismiss or reopen a finding")
async def api_change_status(request: Request, finding_id: int, payload: StatusChange):
    user = await _api_user(request)
    await _change_status(finding_id, payload.status, payload.note, user, request)
    updated = await repo.get_finding(finding_id)
    names = await repo.names_for(asset_ids=updated["asset_ids"], company_ids=updated["company_ids"])
    return JSONResponse({"finding": json.loads(json.dumps(findings_service.present(updated, names), default=str))})
