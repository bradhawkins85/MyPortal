from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from typing import Any

from fastapi import FastAPI, HTTPException, Request, status, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.api.routes import audit_logs, auth, companies, memberships, roles, users
from app.core.config import get_settings, get_templates_config
from app.core.database import db
from app.core.logging import configure_logging, log_error, log_info
from app.repositories import audit_logs as audit_repo
from app.repositories import companies as company_repo
from app.repositories import company_memberships as membership_repo
from app.repositories import roles as role_repo
from app.repositories import shop as shop_repo
from app.repositories import users as user_repo
from app.security.csrf import CSRFMiddleware
from app.security.rate_limiter import RateLimiterMiddleware, SimpleRateLimiter
from app.security.session import session_manager

configure_logging()
settings = get_settings()
templates_config = get_templates_config()
app = FastAPI(title=settings.app_name, docs_url=settings.swagger_ui_url)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[str(origin) for origin in settings.allowed_origins] or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

general_rate_limiter = SimpleRateLimiter(limit=300, window_seconds=900)
app.add_middleware(
    RateLimiterMiddleware,
    rate_limiter=general_rate_limiter,
    exempt_paths=("/docs", "/openapi.json", "/static"),
)

app.add_middleware(CSRFMiddleware)

templates = Jinja2Templates(directory=str(templates_config.template_path))
app.mount("/static", StaticFiles(directory=str(templates_config.static_path)), name="static")

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(companies.router)
app.include_router(roles.router)
app.include_router(memberships.router)
app.include_router(audit_logs.router)


async def _require_authenticated_user(request: Request) -> tuple[dict[str, Any] | None, RedirectResponse | None]:
    session = await session_manager.load_session(request)
    if not session:
        return None, RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    user = await user_repo.get_user_by_id(session.user_id)
    if not user:
        return None, RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    return user, None


async def _require_super_admin_page(request: Request) -> tuple[dict[str, Any] | None, RedirectResponse | None]:
    user, redirect = await _require_authenticated_user(request)
    if redirect:
        return None, redirect
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin privileges required")
    return user, None


def _to_iso(dt: Any) -> str | None:
    if not dt:
        return None
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt.isoformat()
    return str(dt)


@app.on_event("startup")
async def on_startup() -> None:
    await db.connect()
    await db.run_migrations()
    log_info("Application started", environment=settings.environment)


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await db.disconnect()
    log_info("Application shutdown")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user, redirect = await _require_authenticated_user(request)
    if redirect:
        return redirect
    context = {
        "request": request,
        "app_name": settings.app_name,
        "current_year": datetime.utcnow().year,
        "current_user": user,
    }
    return templates.TemplateResponse("dashboard.html", context)


@app.get("/shop", response_class=HTMLResponse)
async def shop_page(
    request: Request,
    category: int | None = None,
    show_out_of_stock: bool = Query(False, alias="showOutOfStock"),
    q: str | None = None,
    cart_error: str | None = None,
):
    user, redirect = await _require_authenticated_user(request)
    if redirect:
        return redirect
    search_term = (q or "").strip()
    effective_search = search_term or None

    company_id_raw = user.get("company_id")
    try:
        company_id = int(company_id_raw) if company_id_raw is not None else None
    except (TypeError, ValueError):
        company_id = None

    category_id = category if category and category > 0 else None

    filters = shop_repo.ProductFilters(
        include_archived=False,
        company_id=company_id,
        category_id=category_id,
        search_term=effective_search,
    )

    categories_task = asyncio.create_task(shop_repo.list_categories())
    products_task = asyncio.create_task(shop_repo.list_products(filters))
    company_task = (
        asyncio.create_task(company_repo.get_company_by_id(company_id))
        if company_id is not None
        else None
    )

    categories = await categories_task
    products = await products_task
    company = await company_task if company_task else None

    if not show_out_of_stock:
        products = [product for product in products if product.get("stock", 0) > 0]

    is_vip = bool(company and int(company.get("is_vip") or 0) == 1)
    if is_vip:
        for product in products:
            vip_price = product.get("vip_price")
            if vip_price is not None:
                product["price"] = vip_price

    context = {
        "request": request,
        "app_name": settings.app_name,
        "current_year": datetime.utcnow().year,
        "title": "Shop",
        "current_user": user,
        "categories": categories,
        "products": products,
        "current_category": category_id,
        "show_out_of_stock": show_out_of_stock,
        "search_term": search_term,
        "cart_error": cart_error,
    }
    return templates.TemplateResponse("shop/index.html", context)


@app.get("/forms", response_class=HTMLResponse)
async def forms_page(request: Request):
    user, redirect = await _require_authenticated_user(request)
    if redirect:
        return redirect
    context = {
        "request": request,
        "app_name": settings.app_name,
        "current_year": datetime.utcnow().year,
        "title": "Forms",
        "current_user": user,
        "forms": [],
        "opnform_base_url": settings.opnform_base_url,
    }
    return templates.TemplateResponse("forms/index.html", context)


@app.get("/staff", response_class=HTMLResponse)
async def staff_page(
    request: Request,
    enabled: str = "",
    department: str = "",
):
    user, redirect = await _require_authenticated_user(request)
    if redirect:
        return redirect
    context = {
        "request": request,
        "app_name": settings.app_name,
        "current_year": datetime.utcnow().year,
        "title": "Staff",
        "current_user": user,
        "is_super_admin": bool(user.get("is_super_admin")),
        "is_admin": bool(user.get("is_admin")),
        "syncro_company_id": user.get("syncro_company_id"),
        "staff_permission": user.get("staff_permission", 0),
        "departments": [],
        "staff_members": [],
        "enabled_filter": enabled,
        "department_filter": department,
    }
    return templates.TemplateResponse("staff/index.html", context)


@app.get("/admin/roles", response_class=HTMLResponse)
async def admin_roles(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    roles_list = await role_repo.list_roles()
    context = {
        "request": request,
        "app_name": settings.app_name,
        "current_year": datetime.utcnow().year,
        "title": "Role management",
        "roles": roles_list,
        "current_user": current_user,
    }
    return templates.TemplateResponse("admin/roles.html", context)


@app.get("/admin/memberships", response_class=HTMLResponse)
async def admin_memberships(request: Request, company_id: int | None = None):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    companies = await company_repo.list_companies()
    effective_company_id = company_id
    selected_company = None
    memberships: list[dict[str, Any]] = []
    if not effective_company_id and companies:
        effective_company_id = companies[0]["id"]
    if effective_company_id:
        selected_company = next((c for c in companies if c["id"] == effective_company_id), None)
        if not selected_company and companies:
            selected_company = companies[0]
            effective_company_id = selected_company["id"]
        if selected_company:
            memberships = await membership_repo.list_company_memberships(effective_company_id)
            for record in memberships:
                record["invited_at_iso"] = _to_iso(record.get("invited_at"))
                record["joined_at_iso"] = _to_iso(record.get("joined_at"))
                record["last_seen_at_iso"] = _to_iso(record.get("last_seen_at"))
    roles_list = await role_repo.list_roles()
    users = await user_repo.list_users()
    status_options = [
        {"value": "invited", "label": "Invited"},
        {"value": "active", "label": "Active"},
        {"value": "suspended", "label": "Suspended"},
    ]
    context = {
        "request": request,
        "app_name": settings.app_name,
        "current_year": datetime.utcnow().year,
        "title": "Company memberships",
        "current_user": current_user,
        "companies": companies,
        "selected_company": selected_company,
        "selected_company_id": effective_company_id,
        "memberships": memberships,
        "roles": roles_list,
        "users": users,
        "status_options": status_options,
    }
    return templates.TemplateResponse("admin/memberships.html", context)


@app.get("/admin/audit-logs", response_class=HTMLResponse)
async def admin_audit_logs(
    request: Request,
    entity_type: str | None = None,
    entity_id: int | None = None,
    user_id: int | None = None,
    limit: int = 100,
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    limit = max(1, min(limit, 500))
    logs = await audit_repo.list_audit_logs(
        entity_type=entity_type,
        entity_id=entity_id,
        user_id=user_id,
        limit=limit,
    )
    for log in logs:
        log["created_at_iso"] = _to_iso(log.get("created_at"))
    context = {
        "request": request,
        "app_name": settings.app_name,
        "current_year": datetime.utcnow().year,
        "title": "Audit trail",
        "current_user": current_user,
        "logs": logs,
        "filters": {
            "entity_type": entity_type or "",
            "entity_id": entity_id or "",
            "user_id": user_id or "",
            "limit": limit,
        },
    }
    return templates.TemplateResponse("admin/audit_logs.html", context)


@app.get("/admin/forms", response_class=HTMLResponse)
async def admin_forms_page(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    context = {
        "request": request,
        "app_name": settings.app_name,
        "current_year": datetime.utcnow().year,
        "title": "Forms admin",
        "current_user": current_user,
        "forms": [],
        "opnform_base_url": settings.opnform_base_url,
    }
    return templates.TemplateResponse("admin/forms.html", context)


@app.get("/admin/shop", response_class=HTMLResponse)
async def admin_shop_page(
    request: Request,
    show_archived: bool = Query(False, alias="showArchived"),
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    categories_task = asyncio.create_task(shop_repo.list_categories())
    products_task = asyncio.create_task(
        shop_repo.list_products(
            shop_repo.ProductFilters(include_archived=show_archived)
        )
    )
    restrictions_task = asyncio.create_task(shop_repo.list_product_restrictions())
    companies_task = asyncio.create_task(company_repo.list_companies())

    categories, products, restrictions, companies = await asyncio.gather(
        categories_task, products_task, restrictions_task, companies_task
    )

    restrictions_map: dict[int, list[dict[str, Any]]] = {}
    for restriction in restrictions:
        restrictions_map.setdefault(restriction["product_id"], []).append(restriction)

    context = {
        "request": request,
        "app_name": settings.app_name,
        "current_year": datetime.utcnow().year,
        "title": "Shop admin",
        "current_user": current_user,
        "categories": categories,
        "products": products,
        "product_restrictions": restrictions_map,
        "all_companies": companies,
        "show_archived": show_archived,
    }
    return templates.TemplateResponse("admin/shop.html", context)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    session = await session_manager.load_session(request)
    if session:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    try:
        user_count = await user_repo.count_users()
    except Exception as exc:  # pragma: no cover - defensive logging for startup issues
        log_error("Failed to determine user count during login", error=str(exc))
        user_count = 1

    if user_count == 0:
        return RedirectResponse(url="/register", status_code=status.HTTP_303_SEE_OTHER)

    context = {
        "request": request,
        "app_name": settings.app_name,
        "current_year": datetime.utcnow().year,
        "title": "Sign in",
    }
    return templates.TemplateResponse("auth/login.html", context)


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    session = await session_manager.load_session(request)
    if session:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    try:
        user_count = await user_repo.count_users()
    except Exception as exc:  # pragma: no cover - defensive logging for startup issues
        log_error("Failed to determine user count during registration", error=str(exc))
        user_count = 1

    if user_count > 0:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    context = {
        "request": request,
        "app_name": settings.app_name,
        "current_year": datetime.utcnow().year,
        "title": "Create super administrator",
    }
    return templates.TemplateResponse("auth/register.html", context)


@app.get("/health")
async def health_check():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}
