from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

from app.api.dependencies.auth import get_current_session, get_current_user
from app.repositories import user_companies as user_company_repo
from app.repositories import websites as repo
from app.security.session import SessionData
from app.services import audit as audit_service
from app.services.website_monitoring import check_website, validate_public_url

router = APIRouter(prefix="/api/websites", tags=["Websites"])


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
    if not main_module._membership_menu_can(user, membership, "menu.assets"):
        raise HTTPException(status_code=403, detail="Website access required")
    return user, company_id, membership


def require_write(context):
    user, company_id, membership = context
    from app import main as main_module
    if not main_module._membership_menu_can(user, membership, "menu.assets", write=True):
        raise HTTPException(status_code=403, detail="Website write access required")
    return user, company_id


async def _validated(payload: WebsiteInput) -> dict:
    try:
        await validate_public_url(payload.url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return payload.model_dump()


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
