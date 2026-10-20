"""RMM scripting pages and APIs.

Technician side (company scoped, ``menu.rmm_scripts``; write access runs scripts):

* ``GET /rmm/scripts`` script library, devices and run history; ``?script={id}``
  opens one script: its description, AI summary, inputs, runs and source
* ``GET /api/rmm/scripts`` and ``/api/rmm/scripts/{id}`` scripts and their fields
* ``POST /api/rmm/scripts/{id}/summary`` (re)generate a script's AI summary
* ``GET/PUT /api/rmm/scripts/{id}/files`` list/set associated Gitea files
* ``POST /api/rmm/scripts/{id}/runs`` push a script to devices
* ``POST /api/rmm/assets/{id}/tray-repair`` repair the device's tray agent
* ``GET /api/rmm/runs`` / ``/api/rmm/runs/{id}``, ``POST /api/rmm/runs/{id}/cancel``
* ``POST /rmm/scripts/sync`` load scripts from Gitea (super admin)
* ``GET /api/rmm/gitea/identity`` who to sign in to Gitea as (asked by nginx
  for every ``/gitea/`` request; ``menu.rmm_script_editing``)

Schedules and onboarding live in :mod:`.automation_routes`.

Agent side (``Authorization: Bearer`` token):

* ``POST /api/rmm/agent/enrol`` with the tray device's token
* ``POST /api/rmm/agent/checkin``, ``GET /api/rmm/agent/jobs`` (long poll)
* ``POST /api/rmm/agent/runs/{id}/started`` and ``/result``
* ``GET /api/rmm/agent/runs/{id}/files/{index}`` assigned immutable file downloads
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field, model_validator

from app.api.dependencies.auth import get_current_tray_device, get_optional_user
from app.repositories import rmm as rmm_repo
from app.security.client_ip import get_client_ip
from app.security.flash import flash_redirect
from app.services import audit as audit_service
from app.services import gitea
from app.services import gitea_sign_in
from app.services import rmm_agent_deploy
from app.services import rmm_automation
from app.services import rmm_checks
from app.services import rmm_commands
from app.services import rmm_remote_control
from app.services import rmm_script_parser as parser
from app.services import rmm_script_summary
from app.services import rmm_script_files
from app.services import rmm_scripts
from app.services import rmm_tray_repair
from app.services import tray as tray_service

router = APIRouter(tags=["RMM scripts"])

PERMISSION = "menu.rmm_scripts"
_AGENT_PUBLIC_FIELDS = ("id", "hostname", "os", "os_version", "arch", "agent_version", "shells", "last_seen_utc", "status", "thermal")


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
        "default_queue_timeout_seconds": script.get("default_queue_timeout_seconds") or 0,
        "platforms": script.get("platforms", rmm_repo.default_script_platforms(script.get("language"))),
        "platform_labels": [rmm_scripts.SCRIPT_PLATFORMS.get(item, item) for item in script.get("platforms", rmm_repo.default_script_platforms(script.get("language")))],
        "allow_parallel": bool(script.get("allow_parallel")),
        "execution_mode": rmm_repo.script_execution_mode(script),
        "synced_at": _iso(script.get("synced_at")),
        "files": script.get("files") or [],
    }


def _group_scripts(scripts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for script in scripts:
        groups.setdefault(script.get("folder") or "", []).append(_script_summary(script))
    return [{"folder": folder, "scripts": groups[folder]} for folder in sorted(groups, key=str.casefold)]


def _script_tree(groups: list[dict[str, Any]]) -> dict[str, Any]:
    """Nest the folder groups into a tree for the page's folder navigation:
    ``Common/disk`` becomes ``Common`` > ``disk``. Each node has its ``name``,
    full ``path``, sub-``folders``, own ``scripts`` and the ``count`` of every
    script beneath it."""

    root: dict[str, Any] = {"name": "", "path": "", "folders": {}, "scripts": [], "count": 0}
    for group in groups:
        node = root
        node["count"] += len(group["scripts"])
        path = ""
        for part in (part for part in str(group["folder"] or "").split("/") if part):
            path = f"{path}/{part}" if path else part
            node = node["folders"].setdefault(
                part, {"name": part, "path": path, "folders": {}, "scripts": [], "count": 0}
            )
            node["count"] += len(group["scripts"])
        node["scripts"].extend(group["scripts"])

    def finish(node: dict[str, Any]) -> dict[str, Any]:
        node["folders"] = [finish(node["folders"][name]) for name in sorted(node["folders"], key=str.casefold)]
        return node

    return finish(root)


async def asset_rmm_context(company_id: int, asset_id: int, *, can_run: bool) -> dict[str, Any]:
    """The asset page's Scripts card: the device's RMM agent and its recent runs."""

    await rmm_repo.expire_stale_runs()
    agent = await rmm_repo.get_asset_agent(asset_id)
    if agent and agent.get("company_id") is not None and int(agent["company_id"]) != int(company_id):
        agent = None
    # Without an agent, technicians can push one through the device's tray app.
    deploy = _serialise(await rmm_agent_deploy.asset_deploy_state(asset_id, company_id))
    return {
        "agent": _serialise({key: agent.get(key) for key in _AGENT_PUBLIC_FIELDS}) if agent else None,
        "deploy": deploy,
        "can_push_agent": bool(can_run and deploy and deploy.get("supported")),
        "install_history": [
            _serialise(attempt) for attempt in await rmm_agent_deploy.asset_install_history(asset_id, limit=10)
        ],
        "runs": [_serialise(run) for run in await rmm_repo.list_runs(company_id=company_id, asset_id=asset_id, limit=10)],
        "can_run": can_run and agent is not None,
        "agent_state": rmm_scripts.agent_state(agent),
        # RustDesk and MeshCentral are switched on through the agent, so they need one.
        "remote_control": await rmm_remote_control.enabled_providers() if can_run and agent else [],
        "command_languages": rmm_commands.languages_for(agent) if can_run and agent else [],
        "tray_repair": bool(can_run and rmm_tray_repair.supported(agent)),
    }


async def company_agent_states(company_id: int) -> dict[int, str]:
    """``connected`` or ``offline`` for each of a company's assets with an RMM agent.

    Assets missing from the result have no agent installed."""

    agents = await rmm_repo.list_company_asset_agents(company_id)
    return {asset_id: rmm_scripts.agent_state(agent) for asset_id, agent in agents.items()}


async def company_agent_details(company_id: int) -> dict[int, dict[str, Any]]:
    """State and reported version of each of a company's assets with an RMM agent."""

    agents = await rmm_repo.list_company_asset_agents(company_id)
    return {
        asset_id: {"state": rmm_scripts.agent_state(agent), "version": agent.get("agent_version")}
        for asset_id, agent in agents.items()
    }


async def _script_detail(script_id: int, company_id: int, runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Everything the Scripts page shows for one script, or None when the company cannot use it."""

    script = await rmm_repo.get_script(script_id, with_content=True)
    if not rmm_repo.script_available_to(script, company_id):
        return None
    return {
        **_script_summary(script),
        "content": script.get("content") or "",
        "content_sha256": script.get("content_sha256") or "",
        "line_count": len((script.get("content") or "").splitlines()),
        "fields": rmm_scripts.script_fields(script),
        "source_url": await rmm_scripts.script_source_url(script),
        "ai": rmm_script_summary.summary_state(script),
        "runs": [run for run in runs if run.get("script_id") == script["id"]][:25],
    }


@router.get("/rmm/scripts", response_class=HTMLResponse, summary="Browse RMM scripts and their runs")
async def scripts_page(request: Request, script: int | None = Query(default=None, ge=1)):
    context = await _context(request)
    if isinstance(context, RedirectResponse):
        return context
    user, company, company_id, can_run = context
    await rmm_repo.expire_stale_runs()
    scripts = await rmm_repo.list_scripts(company_id=company_id)
    agents = await rmm_repo.list_company_agents(company_id)
    runs = [_serialise(run) for run in await rmm_repo.list_runs(company_id=company_id, limit=200)]
    selected = await _script_detail(script, company_id, runs) if script else None
    groups = _group_scripts(scripts)
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
            "script_platforms": rmm_scripts.SCRIPT_PLATFORMS,
            "company": company,
            "script_groups": groups,
            "script_tree": _script_tree(groups),
            "agents": [_serialise(agent) for agent in agents],
            "runs": runs,
            "summary": summary,
            "selected_script": selected,
            "script_not_found": bool(script) and selected is None,
            "can_run": can_run,
            "can_sync": bool(user.get("is_super_admin")),
            "can_edit_files": await gitea_sign_in.access_level(user) == "write",
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


@router.get("/downloads", include_in_schema=False)
async def public_downloads() -> Response:
    """Public downloads are available without RMM permissions or sign-in."""
    try:
        settings = gitea.public_settings(await gitea.load_settings())
    except gitea.GiteaError as exc:
        raise HTTPException(status_code=503, detail="Public downloads are unavailable.") from exc
    return RedirectResponse(gitea.repository_url(settings), status_code=302, headers={"Cache-Control": "no-store"})


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
async def list_scripts_api(request: Request, asset_id: int | None = Query(default=None, ge=1)):
    _user, _company, company_id, _can_run = await _context(request, api=True)
    scripts = await rmm_repo.list_scripts(company_id=company_id)
    if asset_id is not None:
        agent = await rmm_repo.get_asset_agent(asset_id)
        if not agent or int(agent.get("company_id") or 0) != company_id:
            raise HTTPException(status_code=404, detail="Device not found")
        scripts = [script for script in scripts if not rmm_scripts.device_problem(script, agent)]
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
        "agents": [_serialise(agent) for agent in agents if not rmm_scripts.device_problem(script, agent)],
        "source_url": await rmm_scripts.script_source_url(script),
        "can_run": can_run,
    }


@router.post("/api/rmm/scripts/{script_id:int}/summary", summary="Generate the AI summary of a script")
async def summarise_script_api(script_id: int, request: Request):
    user, _company, company_id, _can_run = await _context(request, write=True, api=True)
    script = await rmm_repo.get_script(script_id, with_content=True)
    if not rmm_repo.script_available_to(script, company_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Script not found")
    try:
        state = await rmm_script_summary.generate_summary(script)
    except rmm_script_summary.SummaryUnavailable as exc:
        detail = rmm_script_summary.MESSAGES.get(exc.code, rmm_script_summary.MESSAGES["unusable"])
        return JSONResponse({"detail": detail}, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    await audit_service.record(
        action="rmm.script.summarise",
        request=request,
        user_id=user.get("id"),
        entity_type="rmm_script",
        entity_id=script_id,
        metadata={"script": script["name"], "model": state["model"]},
    )
    return state


def _policy_timeout(raw: Any) -> int:
    try:
        minutes = int(str(raw))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Enter whole minutes, or 0 for forever") from None
    if minutes < 0 or minutes > rmm_scripts.MAX_QUEUE_TIMEOUT_SECONDS // 60:
        raise HTTPException(status_code=400, detail="Choose a valid timeout in minutes")
    return minutes * 60


@router.post("/rmm/scripts/{script_id:int}/queue-timeout", summary="Save a script's offline wait timeout")
async def update_script_queue_timeout(script_id: int, request: Request):
    user, _company, company_id, _can_run = await _context(request, api=True)
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=403, detail="Super admin privileges required")
    script = await rmm_repo.get_script(script_id)
    if not rmm_repo.script_available_to(script, company_id):
        raise HTTPException(status_code=404, detail="Script not found")
    form = await request.form()
    seconds = _policy_timeout(form.get("queue_timeout_minutes"))
    await rmm_repo.set_script_queue_timeout(script_id, seconds)
    await audit_service.record(action="rmm.script.queue_timeout.update", request=request, user_id=user.get("id"),
                               entity_type="rmm_script", entity_id=script_id,
                               metadata={"queue_timeout_seconds": seconds})
    return flash_redirect(f"/rmm/scripts?script={script_id}", "Offline wait timeout saved.", "success")


@router.post("/rmm/scripts/{script_id:int}/platforms", summary="Save a script's allowed platforms")
async def update_script_platforms(script_id: int, request: Request):
    user, _company, company_id, _can_run = await _context(request, api=True)
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=403, detail="Super admin privileges required")
    script = await rmm_repo.get_script(script_id)
    if not rmm_repo.script_available_to(script, company_id):
        raise HTTPException(status_code=404, detail="Script not found")
    form = await request.form()
    platforms = list(dict.fromkeys(str(item) for item in form.getlist("platforms")))
    if any(item not in rmm_scripts.SCRIPT_PLATFORMS for item in platforms):
        raise HTTPException(status_code=400, detail="Choose valid platforms")
    await rmm_repo.set_script_platforms(script_id, platforms)
    # Tray menus hide rmm_script items by platform; refresh devices' cached menus.
    await tray_service.broadcast_config_changed()
    await audit_service.record(action="rmm.script.platforms.update", request=request, user_id=user.get("id"),
                               entity_type="rmm_script", entity_id=script_id, metadata={"platforms": platforms})
    return flash_redirect(f"/rmm/scripts?script={script_id}", "Script platforms saved.", "success")


class ScriptExecutionRequest(BaseModel):
    # Keep the existing boolean API for clients using the first two modes.
    allow_parallel: bool | None = Field(default=None, strict=True)
    execution_mode: Literal["sequential", "parallel", "run_anyway"] | None = None

    @model_validator(mode="after")
    def validate_mode(self):
        if self.execution_mode is None and self.allow_parallel is None:
            raise ValueError("Choose an execution mode")
        if self.execution_mode is not None and self.allow_parallel is not None:
            if self.allow_parallel != (self.execution_mode == "parallel"):
                raise ValueError("Conflicting execution settings")
        return self

    @property
    def resolved_mode(self) -> str:
        return self.execution_mode or ("parallel" if self.allow_parallel else "sequential")


async def _save_script_execution(request: Request, script_id: int, execution_mode: str) -> None:
    user, _company, company_id, _can_run = await _context(request, api=True)
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=403, detail="Super admin privileges required")
    script = await rmm_repo.get_script(script_id)
    if not rmm_repo.script_available_to(script, company_id):
        raise HTTPException(status_code=404, detail="Script not found")
    if execution_mode not in {"sequential", "parallel", "run_anyway"}:
        raise HTTPException(status_code=400, detail="Choose a valid execution mode")
    await rmm_repo.set_script_execution(script_id, execution_mode)
    await audit_service.record(
        action="rmm.script.execution.update", request=request, user_id=user.get("id"),
        entity_type="rmm_script", entity_id=script_id, metadata={"execution_mode": execution_mode},
    )


@router.put("/api/rmm/scripts/{script_id:int}/execution", summary="Set a script's execution mode")
async def update_script_execution_api(script_id: int, payload: ScriptExecutionRequest, request: Request):
    await _save_script_execution(request, script_id, payload.resolved_mode)
    return {"allow_parallel": payload.resolved_mode == "parallel", "execution_mode": payload.resolved_mode}


@router.post("/rmm/scripts/{script_id:int}/execution", summary="Save a script's execution mode")
async def update_script_execution(script_id: int, request: Request):
    form = await request.form()
    execution_mode = str(form.get("execution_mode") or ("parallel" if form.get("allow_parallel") == "on" else "sequential"))
    await _save_script_execution(request, script_id, execution_mode)
    return flash_redirect(f"/rmm/scripts?script={script_id}", "Script execution setting saved.", "success")


async def _editable_script_files(request: Request, script_id: int) -> tuple[dict, dict, int]:
    user, _company, company_id, _can_run = await _context(request, api=True)
    if await gitea_sign_in.access_level(user) != "write":
        raise HTTPException(status_code=403, detail="Script editing write access is required")
    script = await rmm_repo.get_script(script_id, with_content=True)
    if not rmm_repo.script_available_to(script, company_id):
        raise HTTPException(status_code=404, detail="Script not found")
    return user, script, company_id


class ScriptFileAssociation(BaseModel):
    source_path: str = Field(min_length=1, max_length=1024)
    destination: str = Field(min_length=1, max_length=1024)


class ScriptFilesRequest(BaseModel):
    files: list[ScriptFileAssociation] = Field(default_factory=list, max_length=rmm_script_files.MAX_FILES)


@router.get("/api/rmm/scripts/{script_id:int}/files", summary="List associated and available Gitea files")
async def script_files_api(script_id: int, request: Request):
    _user, script, company_id = await _editable_script_files(request, script_id)
    try:
        available, warning = await rmm_script_files.file_choices(script, company_id)
    except gitea.GiteaError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"files": script.get("files") or [], "available": available, "warning": warning}


async def _save_script_files(request: Request, user: dict, script: dict, files: list[dict]) -> list[dict]:
    validated = await rmm_script_files.validate_sources(script, files)
    await rmm_repo.set_script_files(int(script["id"]), validated)
    await audit_service.record(
        action="rmm.script.files.update", request=request, user_id=user.get("id"),
        entity_type="rmm_script", entity_id=script["id"], metadata={"files": validated},
    )
    return validated


@router.put("/api/rmm/scripts/{script_id:int}/files", summary="Set a script's Gitea files and endpoint paths")
async def update_script_files_api(script_id: int, payload: ScriptFilesRequest, request: Request):
    user, script, _company_id = await _editable_script_files(request, script_id)
    try:
        files = await _save_script_files(request, user, script, [item.model_dump() for item in payload.files])
    except rmm_script_files.ScriptFileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except gitea.GiteaError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"files": files}


@router.post("/rmm/scripts/{script_id:int}/files", response_class=HTMLResponse, summary="Associate a Gitea file with a script")
async def add_script_file(script_id: int, request: Request):
    user, script, _company_id = await _editable_script_files(request, script_id)
    form = await request.form()
    files = list(script.get("files") or [])
    association = {"source_path": str(form.get("source_path") or ""), "destination": str(form.get("destination") or "")}
    if form.get("index") not in (None, ""):
        try:
            index = int(str(form["index"]))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid file association") from exc
        if index < 0 or index >= len(files):
            raise HTTPException(status_code=404, detail="File association not found")
        files[index] = association
    else:
        files.append(association)
    try:
        await _save_script_files(request, user, script, files)
    except (rmm_script_files.ScriptFileError, gitea.GiteaError) as exc:
        if request.headers.get("Accept") == "application/json":
            return JSONResponse({"detail": str(exc)}, status_code=400)
        return flash_redirect(f"/rmm/scripts?script={script_id}", str(exc), "error")
    if request.headers.get("Accept") == "application/json":
        return JSONResponse({"ok": True})
    return flash_redirect(f"/rmm/scripts?script={script_id}", "File associated with the script.", "success")


@router.post("/rmm/scripts/{script_id:int}/files/{index:int}/remove", response_class=HTMLResponse, summary="Remove a script file association")
async def remove_script_file(script_id: int, index: int, request: Request):
    user, script, _company_id = await _editable_script_files(request, script_id)
    files = list(script.get("files") or [])
    if index < 0 or index >= len(files):
        raise HTTPException(status_code=404, detail="File association not found")
    files.pop(index)
    # Removing a file must still work when Gitea or another associated file is unavailable.
    await rmm_repo.set_script_files(script_id, files)
    await audit_service.record(
        action="rmm.script.files.update", request=request, user_id=user.get("id"),
        entity_type="rmm_script", entity_id=script_id, metadata={"files": files},
    )
    return flash_redirect(f"/rmm/scripts?script={script_id}", "File association removed.", "success")


class RunRequest(BaseModel):
    asset_ids: list[int] = Field(default_factory=list, max_length=rmm_scripts.MAX_TARGETS_PER_RUN)
    values: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int | None = None
    queue_timeout_seconds: int | None = Field(default=None, ge=0, le=rmm_scripts.MAX_QUEUE_TIMEOUT_SECONDS)
    run_as: Literal["system", "user"] = "system"


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
            queue_timeout_seconds=payload.queue_timeout_seconds,
            requested_by_user_id=user.get("id"),
            run_as=payload.run_as,
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
                "run_as": payload.run_as,
            },
        )
    return result


class CommandRequest(BaseModel):
    language: Literal["powershell", "cmd", "bash", "zsh"]
    command: str = Field(max_length=rmm_commands.MAX_COMMAND_LENGTH)
    run_as: Literal["system", "user"] = "system"
    timeout_seconds: int | None = None


@router.post("/api/rmm/assets/{asset_id:int}/commands", summary="Run an ad hoc command on a device")
async def run_command_api(asset_id: int, payload: CommandRequest, request: Request):
    user, _company, company_id, _can_run = await _context(request, write=True, api=True)
    agent = await rmm_repo.get_asset_agent(asset_id)
    if not agent or int(agent.get("company_id") or 0) != int(company_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No RMM agent is installed on this device.")
    try:
        run_id = await rmm_commands.queue_command(
            agent={**agent, "company_id": int(company_id), "asset_id": asset_id},
            language=payload.language,
            command=payload.command,
            run_as=payload.run_as,
            timeout_seconds=payload.timeout_seconds,
            requested_by_user_id=user.get("id"),
        )
    except rmm_scripts.RunRequestError as exc:
        return JSONResponse({"detail": "; ".join(exc.errors.values()), "errors": exc.errors}, status_code=400)
    await audit_service.record(
        action="rmm.command.run",
        request=request,
        user_id=user.get("id"),
        entity_type="asset",
        entity_id=asset_id,
        metadata={
            "company_id": company_id, "run_id": run_id, "language": payload.language, "run_as": payload.run_as,
            "command": payload.command[: rmm_commands.SHOWN_COMMAND_LENGTH],
        },
    )
    return {"run_id": run_id}


@router.post("/api/rmm/assets/{asset_id:int}/tray-repair", summary="Repair the tray agent on a device through its RMM agent")
async def tray_repair_api(asset_id: int, request: Request):
    user, _company, company_id, _can_run = await _context(request, write=True, api=True)
    agent = await rmm_repo.get_asset_agent(asset_id)
    if not agent or int(agent.get("company_id") or 0) != int(company_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No RMM agent is installed on this device.")
    try:
        run_id = await rmm_tray_repair.queue_repair(
            agent={**agent, "company_id": int(company_id), "asset_id": asset_id},
            requested_by_user_id=user.get("id"),
        )
    except rmm_scripts.RunRequestError as exc:
        return JSONResponse({"detail": "; ".join(exc.errors.values()), "errors": exc.errors}, status_code=400)
    await audit_service.record(
        action="rmm.tray_repair.run",
        request=request,
        user_id=user.get("id"),
        entity_type="asset",
        entity_id=asset_id,
        metadata={"company_id": company_id, "run_id": run_id},
    )
    return {"run_id": run_id}


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
    run = await _company_run(run_id, company_id)
    # Older agents and historical runs may contain redirected PowerShell XML.
    from app.services.rmm_output import readable_output

    for stream in ("stdout", "stderr"):
        run[stream] = readable_output(run.get(stream))
    return {"run": _serialise(run)}


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


class TemperatureSensor(BaseModel):
    id: str = Field(min_length=1, max_length=255)
    name: str = Field(max_length=255)
    hardware: str = Field(max_length=255)
    celsius: float = Field(ge=-100, le=250, allow_inf_nan=False)


class ThermalSnapshot(BaseModel):
    collected_at: datetime
    status: Literal["available", "unavailable", "unsupported", "error"]
    sensors: list[TemperatureSensor] = Field(default_factory=list, max_length=256)


class AgentDetails(BaseModel):
    hostname: str | None = Field(default=None, max_length=255)
    os: str | None = Field(default=None, max_length=32)
    os_version: str | None = Field(default=None, max_length=128)
    arch: str | None = Field(default=None, max_length=32)
    agent_version: str | None = Field(default=None, max_length=32)
    shells: list[str] = Field(default_factory=list, max_length=16)
    script_files_supported: bool | None = None
    run_anyway_supported: bool | None = None
    run_as_user_supported: bool | None = None
    thermal: ThermalSnapshot | None = None


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
    await rmm_scripts.heartbeat(agent)
    return {"jobs": await rmm_scripts.wait_for_jobs(agent, wait_seconds=wait)}


@router.post("/api/rmm/agent/runs/{run_id:int}/started", summary="Mark a run as started")
async def agent_run_started(run_id: int, agent: dict = Depends(get_current_rmm_agent)):
    if not await rmm_scripts.mark_started(agent, run_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return {"ok": True}


@router.get("/api/rmm/agent/runs/{run_id:int}/files/{index:int}", summary="Download a file assigned to this agent's active run")
async def agent_script_file(run_id: int, index: int, agent: dict = Depends(get_current_rmm_agent)):
    try:
        downloaded = await rmm_script_files.download_file(agent, run_id, index)
    except (rmm_script_files.ScriptFileError, gitea.GiteaError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if downloaded is None:
        raise HTTPException(status_code=404, detail="Run file not found")
    raw, _filename = downloaded
    return Response(raw, media_type="application/octet-stream", headers={"Cache-Control": "no-store"})


class AgentCustomValue(BaseModel):
    scope: str = Field(max_length=16)
    name: str = Field(max_length=255)
    value: Any = None


class AgentOutput(BaseModel):
    stdout: str = Field(default="", max_length=2_000_000)
    stderr: str = Field(default="", max_length=2_000_000)


@router.post("/api/rmm/agent/runs/{run_id:int}/output", summary="Report a live output snapshot for an executing run")
async def agent_run_output(run_id: int, payload: AgentOutput, agent: dict = Depends(get_current_rmm_agent)):
    if not await rmm_scripts.record_output(agent, run_id, payload.model_dump()):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Active run not found")
    return {"ok": True}


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
    await rmm_checks.run_finished(run_id)
    return result
