from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import Iterable, Mapping
from datetime import datetime, time, timedelta, timezone
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.datastructures import FormData

from app.api.routes import (
    audit_logs,
    auth,
    companies,
    memberships,
    notifications,
    ports,
    roles,
    staff as staff_api,
    users,
)
from app.core.config import get_settings, get_templates_config
from app.core.database import db
from app.core.logging import configure_logging, log_error, log_info
from app.repositories import audit_logs as audit_repo
from app.repositories import auth as auth_repo
from app.repositories import companies as company_repo
from app.repositories import company_memberships as membership_repo
from app.repositories import roles as role_repo
from app.repositories import shop as shop_repo
from app.repositories import staff as staff_repo
from app.repositories import user_companies as user_company_repo
from app.repositories import users as user_repo
from app.security.csrf import CSRFMiddleware
from app.security.rate_limiter import RateLimiterMiddleware, SimpleRateLimiter
from app.security.session import session_manager
from app.services.scheduler import scheduler_service
from app.services import staff_importer

configure_logging()
settings = get_settings()
templates_config = get_templates_config()
tags_metadata = [
    {"name": "Auth", "description": "Authentication, registration, and session management."},
    {"name": "Users", "description": "User administration, profile management, and self-service endpoints."},
    {"name": "Companies", "description": "Company catalogue and membership management."},
    {"name": "Roles", "description": "Role definitions and access controls."},
    {"name": "Memberships", "description": "Company membership workflows with approval tracking."},
    {
        "name": "Staff",
        "description": "Staff directory management, Syncro contact synchronisation, and verification workflows.",
    },
    {"name": "Audit Logs", "description": "Structured audit trail of privileged actions."},
    {"name": "Ports", "description": "Port catalogue, document storage, and pricing workflow APIs."},
    {"name": "Notifications", "description": "System-wide and user-specific notification feeds."},
]
app = FastAPI(
    title=settings.app_name,
    description=(
        "Customer portal API exposing authentication, company administration, port catalogue, "
        "and pricing workflow capabilities."
    ),
    docs_url=settings.swagger_ui_url,
    openapi_tags=tags_metadata,
)

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
app.include_router(ports.router)
app.include_router(notifications.router)
app.include_router(staff_api.router)
app.include_router(audit_logs.router)


async def _require_authenticated_user(request: Request) -> tuple[dict[str, Any] | None, RedirectResponse | None]:
    session = await session_manager.load_session(request)
    if not session:
        return None, RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    user = await user_repo.get_user_by_id(session.user_id)
    if not user:
        return None, RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    active_company_id = session.active_company_id
    if active_company_id is None:
        active_company_id = await _resolve_initial_company_id(user)
        if active_company_id is not None:
            await session_manager.set_active_company(session, active_company_id)
    if active_company_id is not None:
        user["company_id"] = active_company_id
    request.state.active_company_id = active_company_id
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


def _parse_input_datetime(value: str | None, *, assume_midnight: bool = False) -> datetime | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"]
        for fmt in formats:
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if assume_midnight and "T" not in text and " " not in text:
        parsed = datetime.combine(parsed.date(), time.min)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed


async def _resolve_initial_company_id(user: dict[str, Any]) -> int | None:
    raw_company = user.get("company_id")
    if raw_company is not None:
        try:
            return int(raw_company)
        except (TypeError, ValueError):
            pass

    companies = await user_company_repo.list_companies_for_user(user["id"])
    if companies:
        return int(companies[0].get("company_id"))
    return None


async def _build_base_context(
    request: Request,
    user: dict[str, Any],
    *,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    session = await session_manager.load_session(request)
    available_companies = getattr(request.state, "available_companies", None)
    if available_companies is None:
        available_companies = await user_company_repo.list_companies_for_user(user["id"])
        request.state.available_companies = available_companies
    active_company_id = getattr(request.state, "active_company_id", None)
    if active_company_id is None and session:
        active_company_id = session.active_company_id
        request.state.active_company_id = active_company_id
    active_company = None
    for company in available_companies:
        if company.get("company_id") == active_company_id:
            active_company = company
            break
    context: dict[str, Any] = {
        "request": request,
        "app_name": settings.app_name,
        "current_year": datetime.utcnow().year,
        "current_user": user,
        "available_companies": available_companies,
        "active_company": active_company,
        "active_company_id": active_company_id,
        "csrf_token": session.csrf_token if session else None,
    }
    if extra:
        context.update(extra)
    return context


async def _render_template(
    template_name: str,
    request: Request,
    user: dict[str, Any],
    *,
    extra: dict[str, Any] | None = None,
):
    context = await _build_base_context(request, user, extra=extra)
    return templates.TemplateResponse(template_name, context)


async def _load_staff_context(
    request: Request,
    *,
    require_admin: bool = False,
    require_super_admin: bool = False,
):
    user, redirect = await _require_authenticated_user(request)
    if redirect:
        return user, None, None, 0, None, redirect
    is_super_admin = bool(user.get("is_super_admin"))
    if require_super_admin and not is_super_admin:
        return user, None, None, 0, None, RedirectResponse(
            url="/", status_code=status.HTTP_303_SEE_OTHER
        )
    company_id_raw = user.get("company_id")
    if company_id_raw is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No company associated with the current user",
        )
    try:
        company_id = int(company_id_raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid company identifier")
    membership = await user_company_repo.get_user_company(user["id"], company_id)
    staff_permission = int(membership.get("staff_permission", 0)) if membership else 0
    if not is_super_admin and staff_permission <= 0:
        return user, membership, None, staff_permission, company_id, RedirectResponse(
            url="/", status_code=status.HTTP_303_SEE_OTHER
        )
    if require_admin and not (is_super_admin or (membership and membership.get("is_admin"))):
        return user, membership, None, staff_permission, company_id, RedirectResponse(
            url="/", status_code=status.HTTP_303_SEE_OTHER
        )
    company = await company_repo.get_company_by_id(company_id)
    return user, membership, company, staff_permission, company_id, None


@app.on_event("startup")
async def on_startup() -> None:
    await db.connect()
    await db.run_migrations()
    await scheduler_service.start()
    log_info("Application started", environment=settings.environment)


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await scheduler_service.stop()
    await db.disconnect()
    log_info("Application shutdown")


def _first_non_blank(keys: Iterable[str], *sources: Mapping[str, Any]) -> Any | None:
    for source in sources:
        if not source:
            continue
        for key in keys:
            if key not in source:
                continue
            value = source[key]
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            return value
    return None


async def _extract_switch_company_payload(request: Request) -> dict[str, Any]:
    raw_content_type = request.headers.get("content-type", "")
    content_type = raw_content_type.split(";", 1)[0].strip().lower()

    if content_type == "application/json":
        try:
            payload = await request.json()
        except (json.JSONDecodeError, ValueError):
            payload = None
        if isinstance(payload, dict):
            return payload
        return {}

    try:
        form_data: FormData | None = await request.form()
    except Exception:  # pragma: no cover - fallback when Starlette cannot parse the body
        form_data = None

    if not form_data:
        return {}

    data: dict[str, Any] = {}
    for key in form_data.keys():
        values = form_data.getlist(key)
        if values:
            data[key] = values[0]
    return data


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user, redirect = await _require_authenticated_user(request)
    if redirect:
        return redirect
    return await _render_template("dashboard.html", request, user)


@app.post("/switch-company", response_class=RedirectResponse)
async def switch_company(
    request: Request,
):
    user, redirect = await _require_authenticated_user(request)
    if redirect:
        return redirect
    session = await session_manager.load_session(request)
    if not session:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    body_data = await _extract_switch_company_payload(request)
    query_params = request.query_params

    company_id_raw = _first_non_blank(("companyId", "company_id"), body_data, query_params)
    if company_id_raw is not None:
        try:
            company_id = int(company_id_raw)
        except (TypeError, ValueError):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid company identifier")
    else:
        company_id = None

    if company_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="companyId is required")

    return_url_raw = _first_non_blank(("returnUrl", "return_url"), body_data, query_params)
    return_url: str | None = return_url_raw if isinstance(return_url_raw, str) else None

    companies = await user_company_repo.list_companies_for_user(user["id"])
    request.state.available_companies = companies

    if any(company.get("company_id") == company_id for company in companies):
        await session_manager.set_active_company(session, company_id)
        user["company_id"] = company_id
        request.state.active_company_id = company_id

    destination = "/"
    if return_url:
        candidate = return_url.strip()
        if candidate.startswith("/") and not candidate.startswith("//"):
            destination = candidate

    return RedirectResponse(url=destination, status_code=status.HTTP_303_SEE_OTHER)


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

    extra = {
        "title": "Shop",
        "categories": categories,
        "products": products,
        "current_category": category_id,
        "show_out_of_stock": show_out_of_stock,
        "search_term": search_term,
        "cart_error": cart_error,
    }
    return await _render_template("shop/index.html", request, user, extra=extra)


@app.get("/forms", response_class=HTMLResponse)
async def forms_page(request: Request):
    user, redirect = await _require_authenticated_user(request)
    if redirect:
        return redirect
    extra = {
        "title": "Forms",
        "forms": [],
        "opnform_base_url": settings.opnform_base_url,
    }
    return await _render_template("forms/index.html", request, user, extra=extra)


@app.get("/staff", response_class=HTMLResponse)
async def staff_page(
    request: Request,
    enabled: str = "",
    department: str = "",
):
    (
        user,
        membership,
        company,
        staff_permission,
        company_id,
        redirect,
    ) = await _load_staff_context(request)
    if redirect:
        return redirect

    is_super_admin = bool(user.get("is_super_admin"))
    is_admin = is_super_admin or bool(membership and membership.get("is_admin"))

    enabled_value = enabled.strip()
    enabled_filter: bool | None
    if enabled_value == "1":
        enabled_filter = True
    elif enabled_value == "0":
        enabled_filter = False
    else:
        enabled_filter = None

    department_filter = department.strip()

    staff_members: list[dict[str, Any]] = []
    departments: list[str] = []
    if company_id is not None:
        staff_members = await staff_repo.list_staff(company_id, enabled=enabled_filter)
        if not is_super_admin and staff_permission in (1, 2):
            user_email = (user.get("email") or "").lower()
            current_staff = next(
                (
                    member
                    for member in staff_members
                    if (member.get("email") or "").lower() == user_email
                ),
                None,
            )
            user_department = (current_staff or {}).get("department")
            if staff_permission == 1:
                if user_department:
                    staff_members = [
                        member
                        for member in staff_members
                        if member.get("department")
                        and member["department"].lower() == user_department.lower()
                    ]
                else:
                    staff_members = []
            else:  # staff_permission == 2
                if user_department:
                    staff_members = [
                        member
                        for member in staff_members
                        if (
                            member.get("department")
                            and member["department"].lower() == user_department.lower()
                        )
                        or not member.get("department")
                    ]
                else:
                    staff_members = [
                        member for member in staff_members if not member.get("department")
                    ]
        else:
            if department_filter:
                staff_members = [
                    member
                    for member in staff_members
                    if (member.get("department") or "") == department_filter
                ]
            departments = sorted(
                {
                    str(member.get("department"))
                    for member in staff_members
                    if member.get("department")
                }
            )

    extra = {
        "title": "Staff",
        "is_super_admin": is_super_admin,
        "is_admin": is_admin,
        "syncro_company_id": company.get("syncro_company_id") if company else None,
        "staff_permission": staff_permission,
        "departments": departments,
        "staff_members": staff_members,
        "enabled_filter": enabled_value,
        "department_filter": department_filter,
    }
    return await _render_template("staff/index.html", request, user, extra=extra)


@app.post("/staff", response_class=HTMLResponse)
async def create_staff_member(request: Request):
    (
        user,
        membership,
        company,
        staff_permission,
        company_id,
        redirect,
    ) = await _load_staff_context(request, require_admin=True)
    if redirect:
        return redirect
    if company_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No active company")

    form = await request.form()
    first_name = (form.get("firstName") or form.get("first_name") or "").strip()
    last_name = (form.get("lastName") or form.get("last_name") or "").strip()
    email = (form.get("email") or "").strip()
    if not first_name or not last_name or not email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="First name, last name, and email are required")

    mobile_phone = (form.get("mobilePhone") or form.get("mobile_phone") or "").strip() or None
    date_onboarded = _parse_input_datetime(form.get("dateOnboarded"), assume_midnight=True)
    date_offboarded = _parse_input_datetime(form.get("dateOffboarded"))
    enabled = str(form.get("enabled", "1")).lower() in {"1", "true", "on"}
    street = (form.get("street") or "").strip() or None
    city = (form.get("city") or "").strip() or None
    state_val = (form.get("state") or "").strip() or None
    postcode = (form.get("postcode") or "").strip() or None
    country = (form.get("country") or "").strip() or None
    department = (form.get("department") or "").strip() or None
    job_title = (form.get("jobTitle") or form.get("job_title") or "").strip() or None
    org_company = (form.get("company") or "").strip() or None
    manager_name = (form.get("managerName") or form.get("manager_name") or "").strip() or None

    await staff_repo.create_staff(
        company_id=company_id,
        first_name=first_name,
        last_name=last_name,
        email=email,
        mobile_phone=mobile_phone,
        date_onboarded=date_onboarded,
        date_offboarded=date_offboarded,
        enabled=enabled,
        street=street,
        city=city,
        state=state_val,
        postcode=postcode,
        country=country,
        department=department,
        job_title=job_title,
        org_company=org_company,
        manager_name=manager_name,
        account_action=None,
        syncro_contact_id=None,
    )

    return RedirectResponse(url="/staff", status_code=status.HTTP_303_SEE_OTHER)


@app.put("/staff/{staff_id}")
async def update_staff_member(staff_id: int, request: Request):
    (
        user,
        membership,
        company,
        staff_permission,
        company_id,
        redirect,
    ) = await _load_staff_context(request)
    if redirect:
        return redirect

    existing = await staff_repo.get_staff_by_id(staff_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff not found")

    is_super_admin = bool(user.get("is_super_admin"))
    if not is_super_admin and existing.get("company_id") != company_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    payload = await request.json()

    def get_value(*keys: str) -> Any:
        for key in keys:
            if key in payload:
                return payload[key]
        return None

    if is_super_admin:
        first_name = (get_value("firstName", "first_name") or existing.get("first_name") or "").strip()
        last_name = (get_value("lastName", "last_name") or existing.get("last_name") or "").strip()
        email = (get_value("email") or existing.get("email") or "").strip()
        mobile_phone = (get_value("mobilePhone", "mobile_phone") or existing.get("mobile_phone") or "").strip() or None
        date_onboarded = _parse_input_datetime(get_value("dateOnboarded", "date_onboarded"), assume_midnight=True) or _parse_input_datetime(existing.get("date_onboarded"))
        if existing.get("date_onboarded") and not get_value("dateOnboarded", "date_onboarded"):
            date_onboarded = _parse_input_datetime(existing.get("date_onboarded"))
        enabled = bool(get_value("enabled") if get_value("enabled") is not None else existing.get("enabled", True))
        street = get_value("street") or existing.get("street")
        city = get_value("city") or existing.get("city")
        state_val = get_value("state") or existing.get("state")
        postcode = get_value("postcode") or existing.get("postcode")
        country = get_value("country") or existing.get("country")
        department = get_value("department") or existing.get("department")
        job_title = get_value("jobTitle", "job_title") or existing.get("job_title")
        org_company = get_value("company", "org_company") or existing.get("org_company")
        manager_name = get_value("managerName", "manager_name") or existing.get("manager_name")
        account_action = get_value("accountAction", "account_action") or existing.get("account_action")
    else:
        first_name = existing.get("first_name") or ""
        last_name = existing.get("last_name") or ""
        email = existing.get("email") or ""
        mobile_phone = existing.get("mobile_phone")
        date_onboarded = _parse_input_datetime(existing.get("date_onboarded"))
        enabled = bool(existing.get("enabled", True))
        street = existing.get("street")
        city = existing.get("city")
        state_val = existing.get("state")
        postcode = existing.get("postcode")
        country = existing.get("country")
        department = existing.get("department")
        job_title = existing.get("job_title")
        org_company = existing.get("org_company")
        manager_name = existing.get("manager_name")
        account_action = existing.get("account_action")

    date_offboarded = _parse_input_datetime(get_value("dateOffboarded", "date_offboarded"))
    if date_offboarded is None and existing.get("date_offboarded"):
        date_offboarded = _parse_input_datetime(existing.get("date_offboarded"))

    updated = await staff_repo.update_staff(
        staff_id,
        company_id=existing.get("company_id") or company_id,
        first_name=first_name,
        last_name=last_name,
        email=email,
        mobile_phone=mobile_phone,
        date_onboarded=date_onboarded,
        date_offboarded=date_offboarded,
        enabled=enabled,
        street=street,
        city=city,
        state=state_val,
        postcode=postcode,
        country=country,
        department=department,
        job_title=job_title,
        org_company=org_company,
        manager_name=manager_name,
        account_action=account_action,
        syncro_contact_id=existing.get("syncro_contact_id"),
    )
    return JSONResponse({"success": True, "staff": updated})


@app.delete("/staff/{staff_id}")
async def delete_staff_member(staff_id: int, request: Request):
    (
        user,
        membership,
        company,
        staff_permission,
        company_id,
        redirect,
    ) = await _load_staff_context(request, require_super_admin=True)
    if redirect:
        return redirect
    existing = await staff_repo.get_staff_by_id(staff_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff not found")
    await staff_repo.delete_staff(staff_id)
    return JSONResponse({"success": True})


@app.post("/staff/enabled", response_class=HTMLResponse)
async def set_staff_enabled(request: Request):
    (
        user,
        membership,
        company,
        staff_permission,
        company_id,
        redirect,
    ) = await _load_staff_context(request)
    if redirect:
        return redirect
    form = await request.form()
    staff_id_raw = form.get("staffId")
    enabled_raw = form.get("enabled", "0")
    try:
        staff_id = int(staff_id_raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid staff identifier")
    enabled = str(enabled_raw).lower() in {"1", "true", "on"}
    existing = await staff_repo.get_staff_by_id(staff_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff not found")
    is_super_admin = bool(user.get("is_super_admin"))
    if not is_super_admin and existing.get("company_id") != company_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    await staff_repo.set_enabled(staff_id, enabled)
    return RedirectResponse(url="/staff", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/staff/{staff_id}/verify")
async def verify_staff_member(staff_id: int, request: Request):
    (
        user,
        membership,
        company,
        staff_permission,
        company_id,
        redirect,
    ) = await _load_staff_context(request, require_super_admin=True)
    if redirect:
        return redirect
    staff = await staff_repo.get_staff_by_id(staff_id)
    if not staff:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff not found")
    if not staff.get("mobile_phone"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No mobile phone for staff member")

    await staff_repo.purge_expired_verification_codes()
    code = f"{secrets.randbelow(900000) + 100000:06d}"
    admin_name = " ".join(filter(None, [user.get("first_name"), user.get("last_name")])).strip()
    await staff_repo.upsert_verification_code(
        staff_id,
        code=code,
        admin_name=admin_name or None,
    )

    status_code = None
    settings = get_settings()
    staff_company = await company_repo.get_company_by_id(staff.get("company_id"))
    if settings.verify_webhook_url:
        headers = {"Content-Type": "application/json"}
        if settings.verify_api_key:
            headers["Authorization"] = f"Bearer {settings.verify_api_key}"
        payload = {
            "mobilePhone": staff.get("mobile_phone"),
            "code": code,
            "adminName": admin_name,
            "companyName": staff_company.get("name") if staff_company else "",
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(str(settings.verify_webhook_url), json=payload, headers=headers)
            status_code = response.status_code
        except httpx.HTTPError as exc:
            log_error("Verify webhook failed", staff_id=staff_id, error=str(exc))

    return JSONResponse({
        "success": status_code == status.HTTP_202_ACCEPTED if status_code else True,
        "status": status_code,
        "code": code,
    })


@app.post("/staff/{staff_id}/invite")
async def invite_staff_member(staff_id: int, request: Request):
    (
        user,
        membership,
        company,
        staff_permission,
        company_id,
        redirect,
    ) = await _load_staff_context(request, require_admin=True)
    if redirect:
        return redirect
    staff = await staff_repo.get_staff_by_id(staff_id)
    if not staff:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff not found")
    if not staff.get("email"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No email for staff member")
    if not bool(user.get("is_super_admin")) and staff.get("company_id") != company_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    existing_user = await user_repo.get_user_by_email(staff["email"])
    if existing_user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User already exists")

    temp_password = secrets.token_urlsafe(12)
    created_user = await user_repo.create_user(
        email=staff["email"],
        password=temp_password,
        first_name=staff.get("first_name"),
        last_name=staff.get("last_name"),
        mobile_phone=staff.get("mobile_phone"),
        company_id=staff.get("company_id"),
    )
    await user_repo.update_user(created_user["id"], force_password_change=1)
    await user_company_repo.upsert_user_company(
        user_id=created_user["id"],
        company_id=staff.get("company_id"),
        can_manage_staff=False,
        staff_permission=0,
        is_admin=False,
    )
    token = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(hours=1)
    await auth_repo.create_password_reset_token(
        user_id=created_user["id"],
        token=token,
        expires_at=expires_at,
    )
    log_info(
        "Staff invitation generated",
        staff_id=staff_id,
        invited_user_id=created_user["id"],
    )
    return JSONResponse({"success": True})

@app.post("/admin/syncro/import-contacts")
async def import_syncro_contacts(request: Request):
    (
        user,
        membership,
        company,
        staff_permission,
        company_id,
        redirect,
    ) = await _load_staff_context(request, require_super_admin=True)
    if redirect:
        return redirect
    payload = await request.json()
    syncro_company_id = payload.get("syncroCompanyId") or payload.get("syncro_company_id")
    if not syncro_company_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="syncroCompanyId required")
    summary = await staff_importer.import_contacts_for_syncro_id(str(syncro_company_id))
    return JSONResponse({
        "success": True,
        "created": summary.created,
        "updated": summary.updated,
        "skipped": summary.skipped,
    })


@app.get("/admin/roles", response_class=HTMLResponse)
async def admin_roles(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    roles_list = await role_repo.list_roles()
    extra = {
        "title": "Role management",
        "roles": roles_list,
    }
    return await _render_template("admin/roles.html", request, current_user, extra=extra)


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
    extra = {
        "title": "Company memberships",
        "companies": companies,
        "selected_company": selected_company,
        "selected_company_id": effective_company_id,
        "memberships": memberships,
        "roles": roles_list,
        "users": users,
        "status_options": status_options,
    }
    return await _render_template("admin/memberships.html", request, current_user, extra=extra)


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
    extra = {
        "title": "Audit trail",
        "logs": logs,
        "filters": {
            "entity_type": entity_type or "",
            "entity_id": entity_id or "",
            "user_id": user_id or "",
            "limit": limit,
        },
    }
    return await _render_template("admin/audit_logs.html", request, current_user, extra=extra)


@app.get("/admin/forms", response_class=HTMLResponse)
async def admin_forms_page(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    extra = {
        "title": "Forms admin",
        "forms": [],
        "opnform_base_url": settings.opnform_base_url,
    }
    return await _render_template("admin/forms.html", request, current_user, extra=extra)


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

    extra = {
        "title": "Shop admin",
        "categories": categories,
        "products": products,
        "product_restrictions": restrictions_map,
        "all_companies": companies,
        "show_archived": show_archived,
    }
    return await _render_template("admin/shop.html", request, current_user, extra=extra)


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
