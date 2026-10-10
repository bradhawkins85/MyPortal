"""RMM scripting pages and APIs.

Technician side (company scoped, ``menu.rmm_scripts``; write access runs scripts):

* ``GET /rmm/scripts`` script library, devices and run history
* ``GET /api/rmm/scripts`` and ``/api/rmm/scripts/{id}`` scripts and their fields
* ``POST /api/rmm/scripts/{id}/runs`` push a script to devices
* ``GET /api/rmm/runs`` / ``/api/rmm/runs/{id}``, ``POST /api/rmm/runs/{id}/cancel``
* ``POST /rmm/scripts/sync`` load scripts from Gitea (super admin)
* ``GET /api/rmm/gitea/identity`` who to sign in to Gitea as (asked by nginx
  for every ``/gitea/`` request; ``menu.rmm_script_editing``)

Schedules and onboarding live in :mod:`.automation_routes`.

Agent side (``Authorization: Bearer`` token):

* ``POST /api/rmm/agent/enrol`` with the tray device's token
* ``POST /api/rmm/agent/checkin``, ``GET /api/rmm/agent/jobs`` (long poll)
* ``POST /api/rmm/agent/runs/{id}/started`` and ``/result``
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from app.api.dependencies.auth import get_current_tray_device, get_optional_user
from app.repositories import rmm as rmm_repo
from app.security.client_ip import get_client_ip
from app.security.flash import flash_redirect
from app.services import audit as audit_service
from app.services import gitea
from app.services import gitea_sign_in
from app.services import rmm_automation
from app.services import rmm_remote_control
from app.services import rmm_script_parser as parser
from app.services import rmm_scripts

router = APIRouter(tags=["RMM scripts"])

PERMISSION = "menu.rmm_scripts"
_AGENT_PUBLIC_FIELDS = ("id", "hostname", "os", "os_version", "arch", "agent_version", "shells", "last_seen_utc", "status")


def _assets_routes():
    from app.features.assets import routes

    return routes


async def _context(request: Request, *, write: bool = False, api: bool = False):
    """Return ``(user, company, company_id, can_run)`` or a redirect / raise."""

    routes = _assets_routes()
    main_module = routes._main()
    user, membership, company, company_id, redirect = await routes._load_asset_context(request, PERMISSION)
    if redirect:
        if api:
            if user is None:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="RMM scripts access required")
        return redirect
    can_run = bool(user.get("is_super_admin")) or main_module._membership_menu_can(
        user, membership, PERMISSION, write=True
    )
    if write and not can_run:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission to run scripts is required")
    return user, company, company_id, can_run


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _serialise(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: _iso(value) for key, value in row.items()}


def _script_summary(script: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": script["id"],
        "name": script["name"],
        "path": script["path"],
        "folder": script.get("folder") or "",
        # Common scripts run anywhere; company scripts only on that company's devices.
        "scope": "company" if script.get("company_id") is not None else "common",
        "language": script["language"],
        "language_label": parser.LANGUAGE_LABELS.get(script["language"], script["language"]),
        "description": script.get("description") or "",
        "parameter_count": len(script.get("parameters") or []),
        "env_count": len(script.get("env_vars") or []),
        "default_timeout_seconds": script.get("default_timeout_seconds") or 600,
        "synced_at": _iso(script.get("synced_at")),
    }


def _group_scripts(scripts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for script in scripts:
        groups.setdefault(script.get("folder") or "", []).append(_script_summary(script))
    return [{"folder": folder, "scripts": groups[folder]} for folder in sorted(groups, key=str.casefold)]


async def asset_rmm_context(company_id: int, asset_id: int, *, can_run: bool) -> dict[str, Any]:
    """The asset page's Scripts card: the device's RMM agent and its recent runs."""

    await rmm_repo.expire_stale_runs()
    agent = await rmm_repo.get_asset_agent(asset_id)
    if agent and agent.get("company_id") is not None and int(agent["company_id"]) != int(company_id):
        agent = None
    return {
        "agent": _serialise({key: agent.get(key) for key in _AGENT_PUBLIC_FIELDS}) if agent else None,
        "runs": [_serialise(run) for run in await rmm_repo.list_runs(company_id=company_id, asset_id=asset_id, limit=10)],
        "can_run": can_run and agent is not None,
        # Remote control works without the agent when the device's ID is in a custom field.
        "remote_control": await rmm_remote_control.enabled_providers() if can_run else [],
    }


@router.get("/rmm/scripts", response_class=HTMLResponse, summary="Browse RMM scripts and their runs")
async def scripts_page(request: Request):
    context = await _context(request)
    if isinstance(context, RedirectResponse):
        return context
    user, company, company_id, can_run = context
    await rmm_repo.expire_stale_runs()
    scripts = await rmm_repo.list_scripts(company_id=company_id)
    agents = await rmm_repo.list_company_agents(company_id)
    runs = await rmm_repo.list_runs(company_id=company_id, limit=200)
    gitea_ready = True
    gitea_message = ""
    gitea_url = ""
    try:
        settings = await gitea.load_settings()
        # Only technicians who can sign in to Gitea get the link.
        if await gitea_sign_in.access_level(user) != "none":
            gitea_url = gitea.repository_url(settings)
    except gitea.GiteaError as exc:
        gitea_ready = False
        gitea_message = str(exc)
    summary = {
        "scripts": len(scripts),
        "agents": len(agents),
        "running": sum(1 for run in runs if run["status"] in rmm_repo.ACTIVE_RUN_STATUSES),
        "failed": sum(1 for run in runs if run["status"] in {"failed", "timed_out", "expired"}),
    }
    return await _assets_routes()._main()._render_template(
        "rmm/scripts.html",
        request,
        user,
        extra={
            "title": "Scripts",
            "company": company,
            "script_groups": _group_scripts(scripts),
            "agents": [_serialise(agent) for agent in agents],
            "runs": [_serialise(run) for run in runs],
            "summary": summary,
            "can_run": can_run,
            "can_sync": bool(user.get("is_super_admin")),
            "is_super_admin": bool(user.get("is_super_admin")),
            "gitea_ready": gitea_ready,
            "gitea_message": gitea_message,
            "gitea_url": gitea_url,
        },
    )


@router.post("/rmm/scripts/sync", response_class=HTMLResponse, summary="Load scripts from Gitea")
async def sync_scripts(request: Request):
    context = await _context(request)
    if isinstance(context, RedirectResponse):
        return context
    user = context[0]
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin privileges required")
    try:
        summary = await rmm_scripts.sync_from_gitea()
    except gitea.GiteaError as exc:
        return flash_redirect("/rmm/scripts", str(exc), "error")
    await audit_service.record(
        action="rmm.scripts.sync",
        request=request,
        user_id=user.get("id"),
        entity_type="rmm_script",
        metadata=summary.to_dict(),
    )
    return flash_redirect("/rmm/scripts", summary.message(), "warning" if summary.skipped else "success")


@router.get("/api/rmm/gitea/identity", include_in_schema=False)
async def gitea_identity(request: Request) -> Response:
    """Tell nginx who to sign in to Gitea as. Always 204: without the user
    headers Gitea shows its own sign-in page."""

    headers = {"Cache-Control": "no-store"}
    user = await get_optional_user(request)
    account = await gitea_sign_in.identity(user) if user else None
    if account:
        headers["X-MyPortal-Gitea-User"] = account.login
        if account.email:
            headers["X-MyPortal-Gitea-Email"] = account.email
        if account.full_name:
            headers["X-MyPortal-Gitea-Name"] = account.full_name
    return Response(status_code=204, headers=headers)


@router.get("/api/rmm/scripts", summary="List RMM scripts")
async def list_scripts_api(request: Request):
    _user, _company, company_id, _can_run = await _context(request, api=True)
    scripts = await rmm_repo.list_scripts(company_id=company_id)
    return {"scripts": [_script_summary(script) for script in scripts]}


@router.get("/api/rmm/scripts/{script_id:int}", summary="Get a script, its fields and the variables techs can use")
async def get_script_api(script_id: int, request: Request):
    _user, _company, company_id, can_run = await _context(request, api=True)
    script = await rmm_repo.get_script(script_id, with_content=True)
    if not rmm_repo.script_available_to(script, company_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Script not found")
    agents = await rmm_repo.list_company_agents(company_id)
    return {
        "script": {**_script_summary(script), "content": script.get("content") or ""},
        "fields": rmm_scripts.script_fields(script),
        "variables": await rmm_scripts.available_variables(),
        "agents": [_serialise(agent) for agent in agents],
        "source_url": await rmm_scripts.script_source_url(script),
        "can_run": can_run,
    }


class RunRequest(BaseModel):
    asset_ids: list[int] = Field(default_factory=list, max_length=rmm_scripts.MAX_TARGETS_PER_RUN)
    values: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int | None = None


@router.post("/api/rmm/scripts/{script_id:int}/runs", summary="Push a script to one or more devices")
async def run_script_api(script_id: int, payload: RunRequest, request: Request):
    user, _company, company_id, _can_run = await _context(request, write=True, api=True)
    try:
        result = await rmm_scripts.queue_runs(
            script_id=script_id,
            company_id=company_id,
            asset_ids=payload.asset_ids,
            submitted=payload.values,
            timeout_seconds=payload.timeout_seconds,
            requested_by_user_id=user.get("id"),
        )
    except rmm_scripts.RunRequestError as exc:
        return JSONResponse({"detail": "Check the highlighted fields.", "errors": exc.errors}, status_code=400)
    if result["queued"]:
        await audit_service.record(
            action="rmm.script.run",
            request=request,
            user_id=user.get("id"),
            entity_type="rmm_script",
            entity_id=script_id,
            metadata={
                "script": result["script"]["name"],
                "company_id": company_id,
                "runs": [item["run_id"] for item in result["queued"]],
                "assets": [item["asset_id"] for item in result["queued"]],
            },
        )
    return result


@router.get("/api/rmm/runs", summary="List script runs for the current company")
async def list_runs_api(request: Request, asset_id: int | None = Query(default=None), limit: int = Query(default=50, ge=1, le=500)):
    _user, _company, company_id, _can_run = await _context(request, api=True)
    await rmm_repo.expire_stale_runs()
    runs = await rmm_repo.list_runs(company_id=company_id, asset_id=asset_id, limit=limit)
    return {"runs": [_serialise(run) for run in runs]}


async def _company_run(run_id: int, company_id: int) -> dict[str, Any]:
    run = await rmm_repo.get_run(run_id)
    if not run or int(run["company_id"]) != int(company_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return run


@router.get("/api/rmm/runs/{run_id:int}", summary="Get a script run with its output")
async def get_run_api(run_id: int, request: Request):
    _user, _company, company_id, _can_run = await _context(request, api=True)
    return {"run": _serialise(await _company_run(run_id, company_id))}


@router.post("/api/rmm/runs/{run_id:int}/cancel", summary="Cancel a run the device has not collected yet")
async def cancel_run_api(run_id: int, request: Request):
    user, _company, company_id, _can_run = await _context(request, write=True, api=True)
    await _company_run(run_id, company_id)
    if not await rmm_repo.cancel_run(run_id):
        return JSONResponse({"detail": "The device has already collected this run."}, status_code=409)
    await rmm_automation.run_finished(run_id)
    await audit_service.record(
        action="rmm.script.cancel", request=request, user_id=user.get("id"), entity_type="rmm_script_run", entity_id=run_id,
    )
    return {"run": _serialise(await rmm_repo.get_run(run_id))}


# --------------------------------------------------------------------------- #
# Agent API
# --------------------------------------------------------------------------- #


def _bearer(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header.split(" ", 1)[1].strip()
    return ""


async def get_current_rmm_agent(request: Request) -> dict[str, Any]:
    agent = await rmm_scripts.authenticate_agent(_bearer(request))
    if not agent:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="RMM agent authentication failed")
    return agent


class AgentDetails(BaseModel):
    hostname: str | None = Field(default=None, max_length=255)
    os: str | None = Field(default=None, max_length=32)
    os_version: str | None = Field(default=None, max_length=128)
    arch: str | None = Field(default=None, max_length=32)
    agent_version: str | None = Field(default=None, max_length=32)
    shells: list[str] = Field(default_factory=list, max_length=16)


class AgentEnrolRequest(AgentDetails):
    agent_uid: str = Field(min_length=8, max_length=64)


@router.post("/api/rmm/agent/enrol", summary="Enrol the RMM agent installed with a tray device")
async def agent_enrol(payload: AgentEnrolRequest, request: Request, tray_device: dict = Depends(get_current_tray_device)):
    try:
        result = await rmm_scripts.enrol_agent(
            tray_device=tray_device,
            payload=payload.model_dump(),
            client_ip=get_client_ip(request, default=None),
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await audit_service.record(
        action="rmm.agent.enrol",
        request=request,
        entity_type="rmm_agent",
        entity_id=result["agent_id"],
        metadata={"tray_device_id": tray_device.get("id"), "company_id": tray_device.get("company_id")},
        actor="rmm-agent",
    )
    if result.pop("created", False):
        await rmm_automation.on_agent_enrolled(int(result["agent_id"]))
    return result


@router.post("/api/rmm/agent/checkin", summary="Report RMM agent details")
async def agent_checkin(payload: AgentDetails, request: Request, agent: dict = Depends(get_current_rmm_agent)):
    await rmm_scripts.checkin(agent, payload.model_dump(), get_client_ip(request, default=None))
    return {"ok": True, "poll_wait_seconds": rmm_scripts.POLL_WAIT_SECONDS}


@router.get("/api/rmm/agent/jobs", summary="Collect queued script runs (long poll)")
async def agent_jobs(
    wait: int = Query(default=rmm_scripts.POLL_WAIT_SECONDS, ge=0, le=rmm_scripts.POLL_WAIT_SECONDS),
    agent: dict = Depends(get_current_rmm_agent),
):
    await rmm_repo.record_agent_checkin(int(agent["id"]), {})
    return {"jobs": await rmm_scripts.wait_for_jobs(agent, wait_seconds=wait)}


@router.post("/api/rmm/agent/runs/{run_id:int}/started", summary="Mark a run as started")
async def agent_run_started(run_id: int, agent: dict = Depends(get_current_rmm_agent)):
    if not await rmm_scripts.mark_started(agent, run_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return {"ok": True}


class AgentCustomValue(BaseModel):
    scope: str = Field(max_length=16)
    name: str = Field(max_length=255)
    value: Any = None


class AgentResult(BaseModel):
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    error: str | None = Field(default=None, max_length=4000)
    custom_values: list[AgentCustomValue] = Field(default_factory=list, max_length=rmm_scripts.MAX_CUSTOM_VALUES)


@router.post("/api/rmm/agent/runs/{run_id:int}/result", summary="Report a run's exit code, output and custom values")
async def agent_run_result(run_id: int, payload: AgentResult, agent: dict = Depends(get_current_rmm_agent)):
    result = await rmm_scripts.record_result(agent, run_id, payload.model_dump())
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    await rmm_automation.run_finished(run_id)
    return result
