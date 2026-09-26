from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.api.dependencies.auth import get_current_session, get_current_user
from app.repositories import user_companies as user_company_repo
from app.repositories import websites as repo
from app.repositories import assets as assets_repo
from app.services import knowledge_base as knowledge_base_service
from app.security.session import SessionData
from app.services import audit as audit_service
from app.services.website_monitoring import check_website, validate_public_url

router = APIRouter(prefix="/api/websites", tags=["Websites"])
web_router = APIRouter(tags=["Websites"])


class WebsiteInput(BaseModel):
    name: str = Field(min_length=1, max_length=191)
    url: str = Field(min_length=1, max_length=2048)
    owner: str | None = Field(default=None, max_length=255)
    notes: str | None = Field(default=None, max_length=20000)
    monitor_availability: bool = True
    monitor_tls: bool = True
    collect_dns: bool = False
    collect_domain_expiry: bool = False
    asset_ids: list[int] = Field(default_factory=list, max_length=100)
    knowledge_base_article_ids: list[int] = Field(default_factory=list, max_length=100)

    @field_validator("name", "url")
    @classmethod
    def strip_required(cls, value: str) -> str:
        return value.strip()


async def access_context(user: dict = Depends(get_current_user),
                         session: SessionData = Depends(get_current_session)):
    company_id = session.active_company_id or user.get("company_id")
    if company_id is None:
        raise HTTPException(status_code=400, detail="An active company is required")
    company_id = int(company_id)
    membership = await user_company_repo.get_user_company(int(user["id"]), company_id)
    from app import main as main_module
    if not main_module._membership_menu_can(user, membership, "menu.websites"):
        raise HTTPException(status_code=403, detail="Website access required")
    return user, company_id, membership


def require_write(context):
    user, company_id, membership = context
    from app import main as main_module
    if not main_module._membership_menu_can(user, membership, "menu.websites", write=True):
        raise HTTPException(status_code=403, detail="Website write access required")
    return user, company_id


async def _validated(payload: WebsiteInput) -> dict:
    try:
        await validate_public_url(payload.url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return payload.model_dump()


async def _web_context(request: Request, *, write: bool = False):
    from app import main as main_module
    user, redirect = await main_module._require_authenticated_user(request)
    if redirect:
        return None, None, None, redirect
    company_id = user.get("company_id")
    if company_id is None:
        raise HTTPException(status_code=400, detail="An active company is required")
    company_id = int(company_id)
    membership = await main_module._get_effective_company_membership(request, user["id"], company_id)
    if not main_module._membership_menu_can(user, membership, "menu.websites", write=write):
        raise HTTPException(status_code=403, detail="Website access required")
    return user, company_id, membership, None


def _form_payload(form) -> WebsiteInput:
    def ids(name: str) -> list[int]:
        try:
            return [int(value) for value in form.getlist(name) if value]
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid linked record") from exc
    try:
        return WebsiteInput(
            name=form.get("name", ""), url=form.get("url", ""), owner=form.get("owner") or None,
            notes=form.get("notes") or None,
            monitor_availability="monitor_availability" in form, monitor_tls="monitor_tls" in form,
            collect_dns="collect_dns" in form, collect_domain_expiry="collect_domain_expiry" in form,
            asset_ids=ids("asset_ids"), knowledge_base_article_ids=ids("knowledge_base_article_ids"),
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="Website fields are invalid") from exc


async def _choices(user: dict, company_id: int):
    access = await knowledge_base_service.build_access_context(user)
    articles = await knowledge_base_service.list_articles_for_context(
        access, include_unpublished=bool(user.get("is_super_admin"))
    )
    return await assets_repo.list_company_assets(company_id), articles


@web_router.get("/websites", response_class=HTMLResponse)
async def websites_page(request: Request):
    from app import main as main_module
    user, company_id, membership, redirect = await _web_context(request)
    if redirect:
        return redirect
    rows = await repo.list_websites(company_id)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for row in rows:
        observed = row.get("last_success_at")
        failed = row.get("last_failure_at")
        row["display_state"] = "check_failed" if failed and (not observed or failed > observed) else (
            "unknown" if not observed else ("stale" if observed < now - timedelta(hours=24) else (
                "outage" if row.get("last_http_status") and int(row["last_http_status"]) >= 500 else "available")))
    state_filter = request.query_params.get("state", "")
    if state_filter in {"available", "outage", "check_failed", "stale", "unknown"}:
        rows = [row for row in rows if row["display_state"] == state_filter]
    can_write = main_module._membership_menu_can(user, membership, "menu.websites", write=True)
    return await main_module._render_template("websites/index.html", request, user, extra={
        "title": "Websites", "websites": rows, "can_write": can_write,
    })


@web_router.get("/websites/new", response_class=HTMLResponse)
@web_router.get("/websites/{website_id}/edit", response_class=HTMLResponse)
async def website_form_page(request: Request, website_id: int | None = None):
    from app import main as main_module
    user, company_id, _membership, redirect = await _web_context(request, write=True)
    if redirect:
        return redirect
    website = await repo.get_website(company_id, website_id) if website_id else None
    if website_id and not website:
        raise HTTPException(status_code=404, detail="Website not found")
    links = await repo.get_links(company_id, website_id) if website_id else {"assets": [], "articles": []}
    assets, articles = await _choices(user, company_id)
    return await main_module._render_template("websites/form.html", request, user, extra={
        "title": "Edit website" if website else "Create website", "website": website,
        "assets": assets, "articles": articles, "links": links,
    })


@web_router.post("/websites/save")
async def website_save(request: Request):
    user, company_id, _membership, redirect = await _web_context(request, write=True)
    if redirect:
        return redirect
    form = await request.form()
    payload = _form_payload(form)
    values = await _validated(payload)
    try:
        website_id = int(form["website_id"]) if form.get("website_id") else None
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Invalid website identifier") from exc
    if website_id:
        if not await repo.get_website(company_id, website_id):
            raise HTTPException(status_code=404, detail="Website not found")
        try:
            await repo.replace_links(company_id, website_id, payload.asset_ids, payload.knowledge_base_article_ids)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await repo.update_website(company_id, website_id, values)
        action = "website.update"
    else:
        website_id = await repo.create_website(company_id, values, int(user["id"]))
        try:
            await repo.replace_links(company_id, website_id, payload.asset_ids, payload.knowledge_base_article_ids)
        except ValueError as exc:
            await repo.delete_website(company_id, website_id)
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        action = "website.create"
    await audit_service.record(action=action, request=request, user_id=int(user["id"]), entity_type="website", entity_id=website_id)
    return RedirectResponse(f"/websites/{website_id}", status_code=303)


@web_router.get("/websites/{website_id}", response_class=HTMLResponse)
async def website_detail_page(request: Request, website_id: int):
    from app import main as main_module
    user, company_id, membership, redirect = await _web_context(request)
    if redirect:
        return redirect
    website = await repo.get_website(company_id, website_id)
    if not website:
        raise HTTPException(status_code=404, detail="Website not found")
    links = await repo.get_links(company_id, website_id)
    try:
        dns = json.loads(website.get("dns_facts_json") or "null")
    except (TypeError, json.JSONDecodeError):
        dns = None
    can_write = main_module._membership_menu_can(user, membership, "menu.websites", write=True)
    return await main_module._render_template("websites/detail.html", request, user, extra={
        "title": website["name"], "website": website, "links": links, "dns": dns,
        "jobs": await repo.list_check_jobs(company_id, website_id), "can_write": can_write,
    })


@web_router.post("/websites/{website_id}/check")
async def website_manual_check(request: Request, website_id: int):
    user, company_id, _membership, redirect = await _web_context(request, write=True)
    if redirect:
        return redirect
    website = await repo.get_website(company_id, website_id)
    if not website:
        raise HTTPException(status_code=404, detail="Website not found")
    result = await check_website(website)
    await audit_service.record(action="website.check", request=request, user_id=int(user["id"]), entity_type="website", entity_id=website_id, after={"ok": result["ok"]})
    outcome = "completed" if result["ok"] else "failed"
    return RedirectResponse(f"/websites/{website_id}?check={outcome}", status_code=303)


@router.get("", summary="List websites for the active company")
async def list_websites(context=Depends(access_context)):
    return await repo.list_websites(context[1])


@router.get("/checks/health", summary="Website check queue health")
async def check_health(context=Depends(access_context)):
    user, _, _ = context
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=403, detail="Administrator access required")
    return await repo.job_health()


@router.get("/{website_id}", summary="Get website monitoring facts")
async def get_website(website_id: int, context=Depends(access_context)):
    website = await repo.get_website(context[1], website_id)
    if not website:
        raise HTTPException(status_code=404, detail="Website not found")
    return website


@router.post("", status_code=status.HTTP_201_CREATED, summary="Document a website")
async def create_website(payload: WebsiteInput, request: Request, context=Depends(access_context)):
    user, company_id = require_write(context)
    values = await _validated(payload)
    website_id = await repo.create_website(company_id, values, int(user["id"]))
    try:
        await repo.replace_links(company_id, website_id, payload.asset_ids, payload.knowledge_base_article_ids)
    except ValueError as exc:
        await repo.delete_website(company_id, website_id)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    job_id = await repo.enqueue_check(website_id)
    await audit_service.record(action="website.create", request=request, user_id=int(user["id"]),
                               entity_type="website", entity_id=website_id,
                               after={"company_id": company_id, "name": payload.name, "url": payload.url})
    return {"id": website_id, "check_job_id": job_id}


@router.put("/{website_id}", summary="Update website documentation and monitoring")
async def update_website(website_id: int, payload: WebsiteInput, request: Request,
                         context=Depends(access_context)):
    user, company_id = require_write(context)
    if not await repo.get_website(company_id, website_id):
        raise HTTPException(status_code=404, detail="Website not found")
    values = await _validated(payload)
    try:
        await repo.replace_links(company_id, website_id, payload.asset_ids, payload.knowledge_base_article_ids)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await repo.update_website(company_id, website_id, values)
    await audit_service.record(action="website.update", request=request, user_id=int(user["id"]),
                               entity_type="website", entity_id=website_id,
                               after={"name": payload.name, "url": payload.url})
    return await repo.get_website(company_id, website_id)


@router.post("/{website_id}/checks", status_code=status.HTTP_202_ACCEPTED,
             summary="Queue a retryable website check")
async def queue_check(website_id: int, context=Depends(access_context)):
    require_write(context)
    if not await repo.get_website(context[1], website_id):
        raise HTTPException(status_code=404, detail="Website not found")
    return {"job_id": await repo.enqueue_check(website_id), "status": "pending"}


@router.post("/{website_id}/checks/run", summary="Run one bounded website check")
async def run_check(website_id: int, context=Depends(access_context)):
    require_write(context)
    website = await repo.get_website(context[1], website_id)
    if not website:
        raise HTTPException(status_code=404, detail="Website not found")
    return await check_website(website)


@router.delete("/{website_id}", status_code=status.HTTP_204_NO_CONTENT,
               summary="Delete website documentation")
async def delete_website(website_id: int, request: Request, context=Depends(access_context)):
    user, company_id = require_write(context)
    if not await repo.delete_website(company_id, website_id):
        raise HTTPException(status_code=404, detail="Website not found")
    await audit_service.record(action="website.delete", request=request, user_id=int(user["id"]),
                               entity_type="website", entity_id=website_id)
