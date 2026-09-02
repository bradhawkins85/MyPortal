from __future__ import annotations
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from app.repositories import dmarc as repo
from app.repositories import user_companies as memberships
from app.security.menu_permissions import menu_has_access
from app.services import audit, dmarc

router = APIRouter(tags=["DMARC"])


@lru_cache(maxsize=1)
def _main():
    from app import main

    return main


async def _context_with_access(
    request: Request, permission: str = "dmarc.view"
) -> tuple[dict, int, bool]:
    user, redirect = await _main()._require_authenticated_user(request)
    if redirect:
        raise HTTPException(401, "Authentication required")
    company_id = user.get("company_id")
    if company_id is None:
        raise HTTPException(400, "Select a company")
    membership = await memberships.get_user_company(int(user["id"]), int(company_id))
    menu_permissions = (membership or {}).get("menu_permissions")
    requires_write = permission == "dmarc.manage"
    is_super_admin = bool(user.get("is_super_admin"))
    can_view = is_super_admin or menu_has_access(menu_permissions, "menu.dmarc")
    can_manage = is_super_admin or menu_has_access(
        menu_permissions, "menu.dmarc", write=True
    )
    if not can_view or (requires_write and not can_manage):
        raise HTTPException(403, "DMARC permission required")
    return user, int(company_id), can_manage


async def _context(request: Request, permission: str = "dmarc.view"):
    user, company_id, _ = await _context_with_access(request, permission)
    return user, company_id


def _range(start: datetime | None, end: datetime | None) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    end = end or now
    start = start or end - timedelta(days=30)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    if start >= end or end - start > timedelta(days=366):
        raise HTTPException(
            400, "Date range must be positive and no more than 366 days"
        )
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


@router.get("/dmarc", response_class=HTMLResponse)
async def page(
    request: Request,
    start: date | None = None,
    end: date | None = None,
    policy_domain: str | None = None,
):
    user, company_id, _ = await _context_with_access(request)
    now = datetime.now(timezone.utc)
    range_end = (
        datetime.combine(end + timedelta(days=1), time.min, timezone.utc)
        if end
        else now
    )
    range_start = (
        datetime.combine(start, time.min, timezone.utc)
        if start
        else range_end - timedelta(days=30)
    )
    range_start, range_end = _range(range_start, range_end)
    domains = await repo.policy_domains(company_id, range_start, range_end)
    available_domains = {str(item["domain"]) for item in domains}
    if policy_domain and policy_domain not in available_domains:
        raise HTTPException(400, "Unknown policy domain for the selected date range")
    metrics = await repo.overview(company_id, range_start, range_end, policy_domain)
    organizations = await repo.organization_summary(
        company_id, range_start, range_end, policy_domain
    )
    return await _main()._render_template(
        "dmarc/index.html",
        request,
        user,
        extra={
            "title": "DMARC reporting",
            "metrics": metrics,
            "organizations": organizations,
            "range_start": range_start,
            "range_end": range_end,
            "filter_start": start,
            "filter_end": end,
            "policy_domain": policy_domain,
            "policy_domains": domains,
        },
    )


@router.get("/api/dmarc/overview")
async def overview(
    request: Request,
    start: datetime | None = None,
    end: datetime | None = None,
    policy_domain: str | None = None,
):
    _, company_id = await _context(request)
    start, end = _range(start, end)
    return await repo.overview(company_id, start, end, policy_domain)


@router.get("/api/dmarc/organizations")
async def organizations(
    request: Request,
    start: datetime | None = None,
    end: datetime | None = None,
    policy_domain: str | None = None,
):
    _, company_id = await _context(request)
    start, end = _range(start, end)
    return {
        "items": await repo.organization_summary(company_id, start, end, policy_domain)
    }


@router.get("/api/dmarc/policies")
async def policies(
    request: Request, start: datetime | None = None, end: datetime | None = None
):
    _, company_id = await _context(request)
    start, end = _range(start, end)
    return {"items": await repo.policy_domains(company_id, start, end)}


@router.get("/api/dmarc/rua")
async def rua(request: Request):
    _, company_id = await _context(request, "dmarc.manage")
    addresses = await dmarc.company_reporting_addresses(company_id)
    if not addresses:
        raise HTTPException(
            409, "Configure an active Microsoft 365 DMARC reports mailbox first"
        )
    destinations = ",".join(f"mailto:{address}" for address in addresses)
    return {"rua": destinations, "ruf": destinations, "addresses": addresses}


@router.get("/api/dmarc/forensic-reports")
async def forensic_reports(
    request: Request,
    start: datetime | None = None,
    end: datetime | None = None,
    page: int = Query(1, ge=1, le=10000),
    per_page: int = Query(50, ge=1, le=250),
):
    _, company_id = await _context(request)
    start, end = _range(start, end)
    return {
        "items": await repo.list_forensic_reports(
            company_id,
            start=start,
            end=end,
            limit=per_page,
            offset=(page - 1) * per_page,
        ),
        "page": page,
        "per_page": per_page,
    }


@router.get("/api/dmarc/records")
async def records(
    request: Request,
    start: datetime | None = None,
    end: datetime | None = None,
    page: int = Query(1, ge=1, le=10000),
    per_page: int = Query(50, ge=1, le=250),
    domain: str | None = None,
    disposition: str | None = None,
):
    _, company_id = await _context(request)
    start, end = _range(start, end)
    return {
        "items": await repo.list_records(
            company_id,
            start=start,
            end=end,
            limit=per_page,
            offset=(page - 1) * per_page,
            domain=domain,
            disposition=disposition,
        ),
        "page": page,
        "per_page": per_page,
    }


@router.get("/api/dmarc/records/{record_id}")
async def record(request: Request, record_id: int):
    _, company_id = await _context(request)
    item = await repo.get_record(company_id, record_id)
    if not item:
        raise HTTPException(404, "Record not found")
    return item


@router.get("/admin/dmarc/quarantine")
async def quarantine(request: Request, page: int = Query(1, ge=1)):
    user, _ = await _context(request)
    if not user.get("is_super_admin"):
        raise HTTPException(403, "Super administrator required")
    return {"items": await repo.list_quarantine(limit=100, offset=(page - 1) * 100)}


@router.post("/admin/dmarc/companies/{company_id}/rotate-code")
async def rotate(request: Request, company_id: int):
    user, _ = await _context(request, "dmarc.manage")
    if not user.get("is_super_admin"):
        raise HTTPException(403, "Super administrator required")
    addresses = await dmarc.company_reporting_addresses(company_id)
    if not addresses:
        raise HTTPException(
            409, "Configure an active Microsoft 365 DMARC reports mailbox first"
        )
    await audit.record(
        action="dmarc.mailbox.read",
        request=request,
        user_id=int(user["id"]),
        entity_type="company",
        entity_id=company_id,
    )
    destinations = ",".join(f"mailto:{address}" for address in addresses)
    return {"rua": destinations, "ruf": destinations, "addresses": addresses}
