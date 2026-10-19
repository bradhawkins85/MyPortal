"""Patch policy management using the existing company-scoped RMM permissions."""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel, Field
from app.repositories import rmm_automation as agents_repo, rmm_patching as repo
from app.services import audit, rmm_patching as patching, rmm_automation, rmm_targeting
from app.repositories import rmm_targeting as targeting_repo
from .check_routes import TargetingPreviewRequest
from app.services.rmm_scripts import RunRequestError
from app.security.flash import flash_redirect
from .routes import _context, _assets_routes, get_current_rmm_agent

router = APIRouter(tags=["RMM patching"])
PATH = "/rmm/patching"


async def editable(policy_id, company_id, user):
    policy = await repo.get_policy(policy_id)
    if not policy or policy.get("company_id") not in {None, company_id}:
        raise HTTPException(404, "Policy not found")
    if policy.get("company_id") is None and not user.get("is_super_admin"):
        raise HTTPException(403, "Only super admins can change global policies")
    return policy


def form_values(policy):
    return {**patching.DEFAULTS, **policy, **policy.get("settings", {}),
            "scope": "global" if policy.get("company_id") is None else "company",
            "target_mode": "rules" if policy.get("target_rules") is not None else "all"}


async def render(request, context, editing=None, errors=None):
    user, company, company_id, can_edit = context
    from app.features.companies.handlers import _get_company_management_scope
    super_admin, _, memberships = await _get_company_management_scope(request, user)
    can_manage_sources = super_admin or bool(memberships.get(company_id, {}).get("is_admin"))
    policies = await repo.list_policies(company_id)
    for policy in policies:
        policy["can_edit"] = can_edit and (policy["company_id"] is not None or bool(user.get("is_super_admin")))
    from app.repositories import rmm_windows_updates as updates_repo
    from app.services import rmm_windows_updates

    blocks = [rmm_windows_updates.describe_block(rule, can_edit=can_edit, is_super_admin=bool(user.get("is_super_admin")))
              for rule in await updates_repo.list_company_blocks(company_id)]
    response = await _assets_routes()._main()._render_template("rmm/patching.html", request, user,
        extra={"title": "Patch management", "company": company, "policies": policies, "can_edit": can_edit,
               "can_manage_sources": can_manage_sources,
               "is_super_admin": bool(user.get("is_super_admin")), "editing": editing, "errors": errors or {},
               "blocks": blocks,
               "categories": sorted(patching.CATEGORIES)})
    if errors:
        response.status_code = 400
    return response


@router.get(PATH, response_class=HTMLResponse)
async def page(request: Request, policy: int | None = Query(None, ge=1), new: bool = False):
    context = await _context(request)
    if isinstance(context, RedirectResponse):
        return context
    user, _, company_id, can_edit = context
    editing = None
    if can_edit and policy:
        editing = form_values(await editable(policy, company_id, user))
    elif can_edit and new:
        editing = {**patching.DEFAULTS, "timezone": rmm_automation.default_timezone()}
    return await render(request, context, editing)


async def save(request, policy_id):
    context = await _context(request, write=True)
    if isinstance(context, RedirectResponse):
        return context
    user, _, company_id, _ = context
    if policy_id:
        await editable(policy_id, company_id, user)
    form = await request.form()
    payload = {**dict(form), "categories": form.getlist("categories")}
    try:
        values = patching.validate(payload, company_id, bool(user.get("is_super_admin")))
        saved_id = await repo.save(policy_id, {**values, "updated_by_user_id": user.get("id")})
    except RunRequestError as exc:
        posted = {**patching.DEFAULTS, **payload, "id": policy_id,
                  "is_enabled": patching.flag(payload.get("is_enabled")),
                  "install_missing": patching.flag(payload.get("install_missing"))}
        try:
            posted["target_rules"] = json.loads(payload.get("target_rules") or "null")
        except ValueError:
            posted["target_rules"] = None
        return await render(request, context, posted, exc.errors)
    await audit.record(action="rmm.patch.save", request=request, user_id=user.get("id"),
        entity_type="rmm_patch_policy", entity_id=saved_id, metadata=values)
    return flash_redirect(PATH, "Patch policy saved.", "success")


@router.post(PATH)
async def create(request: Request):
    return await save(request, None)


@router.post(PATH + "/{policy_id:int}")
async def update(policy_id: int, request: Request):
    return await save(request, policy_id)


@router.post(PATH + "/{policy_id:int}/delete")
async def delete(policy_id: int, request: Request):
    context = await _context(request, write=True)
    if isinstance(context, RedirectResponse):
        return context
    user, _, company_id, _ = context
    await editable(policy_id, company_id, user)
    await repo.delete(policy_id)
    await audit.record(action="rmm.patch.delete", request=request, user_id=user.get("id"),
                       entity_type="rmm_patch_policy", entity_id=policy_id)
    return flash_redirect(PATH, "Patch policy deleted.", "success")


@router.post(PATH + "/{policy_id:int}/run")
async def run(policy_id: int, request: Request):
    context = await _context(request, write=True)
    if isinstance(context, RedirectResponse):
        return context
    user, _, company_id, _ = context
    policy = await editable(policy_id, company_id, user)
    policies = await repo.list_policies(policy.get("company_id"))
    runs = []
    agents = await agents_repo.list_active_agents(company_id=policy.get("company_id"))
    applied_by_agent = await patching.effective_for_agents(policies, agents)
    for agent in agents:
        applied = applied_by_agent[int(agent["id"])]
        run_id = await patching.queue(agent, applied, {policy_id}, patching.utcnow() + timedelta(minutes=15), user.get("id"))
        if run_id:
            runs.append(run_id)
    await audit.record(action="rmm.patch.run", request=request, user_id=user.get("id"),
                       entity_type="rmm_patch_policy", entity_id=policy_id, metadata={"run_ids": runs})
    return flash_redirect(PATH, f"Queued patching on {len(runs)} devices. View results in Scripts run history.", "success")


@router.post("/api/rmm/patching/targeting/preview")
async def preview(request: Request, payload: TargetingPreviewRequest):
    user, _, company_id, _ = await _context(request, write=True, api=True)
    if payload.scope not in {"company", "global"}:
        raise HTTPException(422, "Choose this company or every company")
    if payload.scope == "global" and not user.get("is_super_admin"):
        raise HTTPException(403, "Only super admins can preview global policies")
    try:
        matches = rmm_targeting.compile_rules(payload.rules)
    except ValueError as exc:
        return JSONResponse({"detail": str(exc), "errors": {"target_rules": str(exc)}}, status_code=400)
    agents = [a for a in await agents_repo.list_active_agents(
        company_id=None if payload.scope == "global" else company_id) if str(a.get("os") or "").lower() == "windows"]
    contexts = await targeting_repo.contexts_for_agents(agents)
    matched = [a for a in agents if matches(contexts.get(int(a["id"])))]
    return {"matched_count": len(matched), "eligible_count": len(contexts), "truncated": len(matched) > 100,
            "agents": [{"id": a["id"], "asset_id": a.get("asset_id"), "hostname": a.get("hostname"),
                        "company_name": contexts[int(a["id"])]["company"].get("name")} for a in matched[:100]]}


@router.get('/api/rmm/chocolatey/packages')
async def packages(request: Request, q: str = Query('', max_length=255)):
    await _context(request, api=True)
    from app.services import chocolatey_catalog as catalog
    try:
        return {'packages': await catalog.query(q), 'updated_at': await catalog.last_updated()}
    except catalog.CatalogUnavailable as exc:
        raise HTTPException(503, str(exc)) from None


@router.get('/api/rmm/assets/{asset_id:int}/software/upgrade')
async def software_upgrade_info(asset_id: int, request: Request, name: str = Query(..., min_length=1, max_length=255)):
    _, _, company_id, can_run = await _context(request, api=True)
    from .inventory_routes import _company_agent
    from app.services import rmm_software_updates, chocolatey_catalog
    agent = await _company_agent(company_id, asset_id)
    try:
        app = await rmm_software_updates.upgrade_for(agent, name)
    except chocolatey_catalog.CatalogUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
    return {'application': app, 'can_run': can_run}


@router.post('/rmm/assets/{asset_id:int}/software/upgrade')
async def software_upgrade(asset_id: int, request: Request):
    context = await _context(request, write=True)
    if isinstance(context, RedirectResponse):
        return context
    user, _, company_id, _ = context
    from .inventory_routes import _company_agent
    from app.services import rmm_software_updates, chocolatey_catalog
    agent = await _company_agent(company_id, asset_id)
    form = await request.form()
    name = str(form.get('name') or '')
    if not 1 <= len(name) <= 255:
        raise HTTPException(422, 'Choose an installed application')
    try:
        run_id = await rmm_software_updates.queue(agent, name, user['id'])
    except RunRequestError as exc:
        return flash_redirect(f'/assets/{asset_id}#software', ' '.join(exc.errors.values()), 'error')
    except chocolatey_catalog.CatalogUnavailable as exc:
        return flash_redirect(f'/assets/{asset_id}#software', str(exc), 'error')
    await audit.record(action='rmm.software.upgrade', request=request, user_id=user['id'], entity_type='asset',
                       entity_id=asset_id, metadata={'name': name, 'run_id': run_id})
    return flash_redirect(f'/assets/{asset_id}#software', 'Upgrade queued. View results in Scripts run history.', 'success')


@router.post('/rmm/assets/{asset_id:int}/software/install')
async def software_install(asset_id: int, request: Request):
    """Install or update one Chocolatey package on this device without a script."""
    context = await _context(request, write=True)
    if isinstance(context, RedirectResponse):
        return context
    user, _, company_id, _ = context
    from .inventory_routes import _company_agent
    from app.services import rmm_software_updates
    agent = await _company_agent(company_id, asset_id)
    form = await request.form()
    try:
        ids = rmm_software_updates.package_ids([form.get('package_id')])
        run_id = await rmm_software_updates.queue_apps(agent, ids, 'install', user['id'])
    except RunRequestError as exc:
        return flash_redirect(f'/assets/{asset_id}#software', ' '.join(exc.errors.values()), 'error')
    await audit.record(action='rmm.software.install', request=request, user_id=user['id'], entity_type='asset',
                       entity_id=asset_id, metadata={'package_ids': ids, 'run_id': run_id})
    return flash_redirect(f'/assets/{asset_id}#software',
                          f'Installing {ids[0]}. View results in Scripts run history.', 'success')


class AppPushRequest(BaseModel):
    package_ids: list[str] = Field(min_length=1, max_length=20)
    mode: Literal['install', 'upgrade'] = 'install'
    all_devices: bool = False
    agent_ids: list[int] = Field(default_factory=list, max_length=1000)


@router.post('/api/rmm/chocolatey/push')
async def push_apps(request: Request, payload: AppPushRequest):
    """Install or update Chocolatey packages now on chosen Windows devices of this company."""
    user, _, company_id, _ = await _context(request, write=True, api=True)
    from app.services import rmm_software_updates
    try:
        ids = rmm_software_updates.package_ids(payload.package_ids)
    except RunRequestError as exc:
        return JSONResponse({'detail': ' '.join(exc.errors.values()), 'errors': exc.errors}, status_code=400)
    agents = [a for a in await agents_repo.list_active_agents(company_id=company_id)
              if str(a.get('os') or '').lower() == 'windows']
    if not payload.all_devices:
        chosen = set(payload.agent_ids)
        agents = [a for a in agents if int(a['id']) in chosen]
    if not agents:
        return JSONResponse({'detail': 'Choose at least one Windows device.',
                             'errors': {'agent_ids': 'Choose at least one Windows device.'}}, status_code=400)
    run_ids, not_needed, problems = [], 0, []
    for agent in agents:
        try:
            run_id = await rmm_software_updates.queue_apps(agent, ids, payload.mode, user['id'])
        except RunRequestError as exc:
            problems.append({'agent_id': agent['id'], 'hostname': agent.get('hostname') or agent.get('agent_uid'),
                             'detail': ' '.join(exc.errors.values())})
            continue
        if run_id:
            run_ids.append(run_id)
        else:
            not_needed += 1
    await audit.record(action='rmm.software.push', request=request, user_id=user['id'], entity_type='company',
                       entity_id=company_id, metadata={'package_ids': ids, 'mode': payload.mode, 'run_ids': run_ids})
    return {'queued': len(run_ids), 'run_ids': run_ids, 'not_needed': not_needed, 'problems': problems}


@router.get('/api/rmm/agent/chocolatey/sources')
async def agent_sources(agent: dict = Depends(get_current_rmm_agent)):
    from app.services import chocolatey_sources
    if str(agent.get('os') or '').lower() != 'windows':
        raise HTTPException(400, 'Chocolatey requires Windows')
    try:
        config = await chocolatey_sources.for_company(int(agent['company_id']), secrets=True)
    except ValueError:
        raise HTTPException(503, 'Chocolatey source configuration is invalid') from None
    # Reuse the same reconciliation implementation used before application jobs.
    script = chocolatey_sources.script() + '''
$ErrorActionPreference = 'Stop'
$mutex = New-Object Threading.Mutex($false, 'Global\\MyPortalPatchManagement')
if (-not $mutex.WaitOne(0)) { throw 'A patch operation is running; retry source reconciliation later.' }
try { $sources = $env:MYPORTAL_CHOCO_SOURCES | ConvertFrom-Json; Sync-ChocolateySources @($sources) }
finally { $mutex.ReleaseMutex(); $mutex.Dispose() }
'''
    return JSONResponse({'script': script, 'sources': config}, headers={'Cache-Control': 'no-store'})
