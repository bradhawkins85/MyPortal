from __future__ import annotations
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from app.repositories import dmarc as repo
from app.repositories import m365_mail_accounts as m365_mail_repo
from app.repositories import user_companies as memberships
from app.security.menu_permissions import menu_has_access
from app.services import audit, dmarc

router = APIRouter(tags=["DMARC"])

@lru_cache(maxsize=1)
def _main():
    from app import main
    return main

async def _context(request: Request, permission: str = "dmarc.view"):
    user, redirect = await _main()._require_authenticated_user(request)
    if redirect:
        raise HTTPException(401, "Authentication required")
    company_id = user.get("company_id")
    if company_id is None:
        raise HTTPException(400, "Select a company")
    membership = await memberships.get_user_company(int(user["id"]), int(company_id))
    menu_permissions = (membership or {}).get("menu_permissions")
    requires_write = permission == "dmarc.manage"
    if not user.get("is_super_admin") and not menu_has_access(
        menu_permissions, "menu.dmarc", write=requires_write
    ):
        raise HTTPException(403, "DMARC permission required")
    return user, int(company_id)

def _range(start: datetime | None, end: datetime | None) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    end = end or now
    start = start or end - timedelta(days=30)
    if start.tzinfo is None: start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None: end = end.replace(tzinfo=timezone.utc)
    if start >= end or end - start > timedelta(days=366):
        raise HTTPException(400, "Date range must be positive and no more than 366 days")
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)

@router.get("/dmarc", response_class=HTMLResponse)
async def page(request: Request):
    user, company_id = await _context(request)
    start, end = _range(None, None)
    metrics = await repo.overview(company_id, start, end)
    return await _main()._render_template("dmarc/index.html", request, user, extra={"title": "DMARC reporting", "metrics": metrics, "range_start": start, "range_end": end})

@router.get("/api/dmarc/overview")
async def overview(request: Request, start: datetime | None = None, end: datetime | None = None):
    _, company_id = await _context(request)
    start, end = _range(start, end)
    return await repo.overview(company_id, start, end)

@router.get("/api/dmarc/rua")
async def rua(request: Request):
    _, company_id = await _context(request, "dmarc.manage")
    address = await dmarc.company_reporting_address(company_id)
    if not address:
        raise HTTPException(409, "Configure an active Microsoft 365 DMARC reports mailbox first")
    return {"rua": f"mailto:{address}"}

@router.get("/api/dmarc/records")
async def records(request: Request, start: datetime | None = None, end: datetime | None = None,
                  page: int = Query(1, ge=1, le=10000), per_page: int = Query(50, ge=1, le=250),
                  domain: str | None = None, disposition: str | None = None):
    _, company_id = await _context(request); start, end = _range(start, end)
    return {"items": await repo.list_records(company_id, start=start, end=end, limit=per_page, offset=(page-1)*per_page, domain=domain, disposition=disposition), "page": page, "per_page": per_page}

@router.get("/api/dmarc/records/{record_id}")
async def record(request: Request, record_id: int):
    _, company_id = await _context(request)
    item = await repo.get_record(company_id, record_id)
    if not item: raise HTTPException(404, "Record not found")
    return item

@router.get("/admin/dmarc/quarantine")
async def quarantine(request: Request, page: int = Query(1, ge=1)):
    user, _ = await _context(request)
    if not user.get("is_super_admin"): raise HTTPException(403, "Super administrator required")
    return {"items": await repo.list_quarantine(limit=100, offset=(page-1)*100)}

@router.post("/admin/dmarc/companies/{company_id}/rotate-code")
async def rotate(request: Request, company_id: int, confirm: bool = False):
    user, _ = await _context(request, "dmarc.manage")
    if not user.get("is_super_admin"): raise HTTPException(403, "Super administrator required")
    if not confirm: raise HTTPException(400, "Explicit confirmation is required")
    mailbox = await m365_mail_repo.get_dmarc_account()
    if not mailbox or not mailbox.get("active"):
        raise HTTPException(409, "Configure an active Microsoft 365 DMARC reports mailbox first")
    mailbox_address = str(mailbox.get("user_principal_name") or "")
    reporting_domain = mailbox_address.partition("@")[2]
    code = dmarc.generate_company_code(); await repo.set_company_code(company_id, code)
    await audit.record(action="dmarc.code.rotate", request=request, user_id=int(user["id"]), entity_type="company", entity_id=company_id)
    return {"rua": f"mailto:{dmarc.reporting_address(code, reporting_domain)}"}
