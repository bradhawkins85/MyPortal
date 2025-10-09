from __future__ import annotations

import asyncio
import json
import secrets
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode

import aiomysql
import httpx
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.openapi.docs import get_swagger_ui_html
from itsdangerous import BadSignature, URLSafeSerializer
from starlette.datastructures import FormData

from app.api.routes import (
    api_keys,
    audit_logs,
    auth,
    companies,
    licenses as licenses_api,
    forms as forms_api,
    invoices as invoices_api,
    memberships,
    m365 as m365_api,
    notifications,
    ports,
    scheduler as scheduler_api,
    roles,
    staff as staff_api,
    users,
)
from app.core.config import get_settings, get_templates_config
from app.core.database import db
from app.core.logging import configure_logging, log_error, log_info
from app.repositories import audit_logs as audit_repo
from app.repositories import api_keys as api_key_repo
from app.repositories import auth as auth_repo
from app.repositories import companies as company_repo
from app.repositories import company_memberships as membership_repo
from app.repositories import invoices as invoice_repo
from app.repositories import licenses as license_repo
from app.repositories import forms as forms_repo
from app.repositories import m365 as m365_repo
from app.repositories import roles as role_repo
from app.repositories import shop as shop_repo
from app.repositories import cart as cart_repo
from app.repositories import scheduled_tasks as scheduled_tasks_repo
from app.repositories import staff as staff_repo
from app.repositories import webhook_events as webhook_events_repo
from app.repositories import user_companies as user_company_repo
from app.repositories import users as user_repo
from app.security.csrf import CSRFMiddleware
from app.security.encryption import encrypt_secret
from app.security.rate_limiter import RateLimiterMiddleware, SimpleRateLimiter
from app.security.session import SessionData, session_manager
from app.api.dependencies.auth import get_current_session
from app.services.scheduler import scheduler_service
from app.security.api_keys import mask_api_key
from app.services import audit as audit_service
from app.services import m365 as m365_service
from app.services import products as products_service
from app.services import shop as shop_service
from app.services import staff_importer
from app.services import template_variables
from app.services import webhook_monitor
from app.services.opnform import (
    OpnformValidationError,
    extract_allowed_host,
    normalize_opnform_embed_code,
    normalize_opnform_form_url,
)
from app.services.file_storage import delete_stored_file, store_product_image

configure_logging()
settings = get_settings()
templates_config = get_templates_config()
oauth_state_serializer = URLSafeSerializer(settings.secret_key, salt="m365-oauth")
OPNFORM_ALLOWED_HOST = extract_allowed_host(
    str(settings.opnform_base_url) if settings.opnform_base_url else None
)


def _opnform_base_url() -> str | None:
    if settings.opnform_base_url:
        base = str(settings.opnform_base_url)
        return base if base.endswith("/") else f"{base}/"
    return "/myforms/"
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
    {
        "name": "API Keys",
        "description": "Super-admin management of API credentials with usage telemetry.",
    },
    {"name": "Ports", "description": "Port catalogue, document storage, and pricing workflow APIs."},
    {"name": "Invoices", "description": "Invoice catalogue, status tracking, and reconciliation APIs."},
    {"name": "Licenses", "description": "Software license catalogue, assignments, and ordering workflows."},
    {
        "name": "Forms",
        "description": "OpnForm publishing, company assignments, and secure embedding endpoints.",
    },
    {"name": "Office365", "description": "Microsoft 365 credential management and synchronisation APIs."},
    {"name": "Notifications", "description": "System-wide and user-specific notification feeds."},
    {"name": "Shop", "description": "Product catalogue management and visibility controls."},
]
app = FastAPI(
    title=settings.app_name,
    description=(
        "Customer portal API exposing authentication, company administration, port catalogue, "
        "and pricing workflow capabilities."
    ),
    docs_url=None,
    openapi_url=None,
    openapi_tags=tags_metadata,
)

SWAGGER_UI_PATH = settings.swagger_ui_url or "/docs"
PROTECTED_OPENAPI_PATH = "/internal/openapi.json"

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
    exempt_paths=(SWAGGER_UI_PATH, PROTECTED_OPENAPI_PATH, "/static"),
)

app.add_middleware(CSRFMiddleware)

templates = Jinja2Templates(directory=str(templates_config.template_path))

# Ensure document uploads remain web-accessible with the same paths used by the
# legacy Node.js implementation.  Product images continue to live in the
# private ``/uploads`` directory which requires authentication before access.
_uploads_path = templates_config.static_path / "uploads"
_uploads_path.mkdir(parents=True, exist_ok=True)

_private_uploads_path = Path(__file__).resolve().parent.parent / "private_uploads"
_private_uploads_path.mkdir(parents=True, exist_ok=True)
try:
    _private_uploads_path.chmod(0o700)
except OSError:
    # The filesystem may not support chmod (e.g. on Windows).  Continue with
    # the secure default provided by ``mkdir``.
    pass


def _resolve_private_upload(file_path: str) -> Path:
    """Resolve ``/uploads`` paths to the secured private uploads directory.

    Supports legacy nested directory structures while preventing path traversal
    outside the uploads root.
    """

    if not file_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    normalised = file_path.replace("\\", "/")
    raw_path = PurePosixPath(normalised)

    if raw_path.is_absolute():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    sanitized_parts: list[str] = []
    for segment in raw_path.parts:
        if segment in {"", "."}:
            continue
        if segment == "..":
            if sanitized_parts:
                sanitized_parts.pop()
            continue
        sanitized_parts.append(segment)
    if not sanitized_parts:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    candidate = (_private_uploads_path.joinpath(*sanitized_parts)).resolve()
    uploads_root = _private_uploads_path.resolve()

    try:
        candidate.relative_to(uploads_root)
    except ValueError as exc:  # pragma: no cover - defensive guard
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found") from exc

    if not candidate.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    return candidate


app.mount("/static", StaticFiles(directory=str(templates_config.static_path)), name="static")


@app.get(PROTECTED_OPENAPI_PATH, include_in_schema=False)
async def authenticated_openapi_schema(
    _: SessionData = Depends(get_current_session),
) -> JSONResponse:
    """Return the OpenAPI schema for authenticated users only."""

    return JSONResponse(app.openapi())


@app.get(SWAGGER_UI_PATH, include_in_schema=False)
async def authenticated_swagger_ui(request: Request) -> Response:
    """Render the Swagger UI after verifying the user session."""

    session = await session_manager.load_session(request)
    if not session:
        next_target = quote(SWAGGER_UI_PATH, safe="/")
        login_url = f"/login?next={next_target}"
        redirect = RedirectResponse(url=login_url, status_code=status.HTTP_303_SEE_OTHER)
        return redirect

    return get_swagger_ui_html(
        openapi_url=PROTECTED_OPENAPI_PATH,
        title=f"{settings.app_name} API Docs",
        oauth2_redirect_url=None,
    )

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(companies.router)
app.include_router(licenses_api.router)
app.include_router(forms_api.router)
app.include_router(roles.router)
app.include_router(memberships.router)
app.include_router(m365_api.router)
app.include_router(ports.router)
app.include_router(notifications.router)
app.include_router(staff_api.router)
app.include_router(invoices_api.router)
app.include_router(audit_logs.router)
app.include_router(api_keys.router)
app.include_router(scheduler_api.router)


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


@app.get("/uploads/{file_path:path}", response_class=FileResponse, include_in_schema=False)
async def serve_private_upload(file_path: str, request: Request):
    """Serve product images stored in the legacy private uploads directory."""

    _, redirect = await _require_authenticated_user(request)
    if redirect:
        return redirect
    resolved_path = _resolve_private_upload(file_path)
    headers = {"Cache-Control": "public, max-age=86400"}
    return FileResponse(resolved_path, headers=headers)


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


def _serialise_for_json(value: Any) -> Any:
    if isinstance(value, datetime):
        return _to_iso(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, Decimal):
        if value == value.to_integral():
            return int(value)
        return float(value)
    if isinstance(value, Mapping):
        return {key: _serialise_for_json(item) for key, item in value.items()}
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray)):
        return [_serialise_for_json(item) for item in value]
    return value


def _serialise_mapping(record: Mapping[str, Any]) -> dict[str, Any]:
    return {key: _serialise_for_json(value) for key, value in record.items()}


def _parse_input_date(value: str | None) -> date | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


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


def _parse_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return default
    return text in {"1", "true", "t", "yes", "y", "on"}


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
    membership = None
    if active_company_id is not None:
        membership = await user_company_repo.get_user_company(user["id"], int(active_company_id))
        request.state.active_membership = membership

    context: dict[str, Any] = {
        "request": request,
        "app_name": settings.app_name,
        "current_year": datetime.utcnow().year,
        "current_user": user,
        "available_companies": available_companies,
        "active_company": active_company,
        "active_company_id": active_company_id,
        "active_membership": membership,
        "csrf_token": session.csrf_token if session else None,
    }
    if extra:
        context.update(extra)

    cart_summary = {"item_count": 0, "total_quantity": 0, "subtotal": Decimal("0")}
    if session:
        try:
            cart_summary = await cart_repo.summarise_cart(session.id)
        except Exception as exc:  # pragma: no cover - defensive logging
            log_error("Failed to summarise cart", error=str(exc))
    context["cart_summary"] = cart_summary
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


_API_KEY_ORDER_CHOICES: list[tuple[str, str]] = [
    ("created_at", "Creation date"),
    ("last_used_at", "Last activity"),
    ("expiry_date", "Expiry date"),
    ("usage_count", "Usage count"),
    ("description", "Description"),
]
_API_KEY_ORDER_COLUMNS = {choice[0] for choice in _API_KEY_ORDER_CHOICES}
_API_KEY_DIRECTION_CHOICES: list[tuple[str, str]] = [
    ("desc", "Descending"),
    ("asc", "Ascending"),
]


def _normalise_api_key_order(order_by: str | None) -> str:
    if not order_by:
        return "created_at"
    if order_by in _API_KEY_ORDER_COLUMNS:
        return order_by
    return "created_at"


def _normalise_direction(direction: str | None) -> str:
    if not direction:
        return "desc"
    return "asc" if direction.lower() == "asc" else "desc"


def _extract_api_key_filters(data: Mapping[str, Any]) -> dict[str, Any]:
    search = (str(data.get("search", "")).strip() or None) if data else None
    include_expired = _parse_bool(data.get("include_expired"))
    order_by = _normalise_api_key_order(str(data.get("order_by", "")))
    order_direction = _normalise_direction(str(data.get("order_direction", "")))
    service_filter = (str(data.get("service_filter", "")).strip() or None)
    correlation_search = (str(data.get("correlation_search", "")).strip() or None)
    return {
        "search": search,
        "include_expired": include_expired,
        "order_by": order_by,
        "order_direction": order_direction,
        "service_filter": service_filter,
        "correlation_search": correlation_search,
    }


def _format_correlation_label(raw_key: str) -> str:
    prefix, _, value = raw_key.partition(":")
    safe_value = (value or "").strip()
    if prefix == "api_key":
        preview = safe_value[-4:] if safe_value else "••••"
        return f"API key fingerprint …{preview}"
    if prefix == "api_key_meta":
        preview = safe_value[-4:] if safe_value else "••••"
        return f"Metadata API key …{preview}"
    if prefix == "ip":
        return f"Source IP {safe_value or 'unknown'}"
    if prefix.endswith("_id"):
        label = prefix.replace("_", " ").title()
        return f"{label} #{safe_value or '?'}"
    if prefix:
        label = prefix.replace("_", " ").title()
        return f"{label} {safe_value}".strip()
    return safe_value or "Correlation"


def _format_entity_label(log: Mapping[str, Any]) -> str:
    entity_type = str(log.get("entity_type") or "system").strip()
    entity_id = log.get("entity_id")
    if entity_id is not None:
        return f"{entity_type} #{entity_id}"
    return entity_type


def _derive_correlation_keys(log: Mapping[str, Any]) -> list[str]:
    keys: list[str] = []
    entity_type = log.get("entity_type")
    entity_id = log.get("entity_id")
    if entity_type and entity_id is not None:
        keys.append(f"{entity_type}:{entity_id}")
    metadata = log.get("metadata")
    if isinstance(metadata, Mapping):
        for candidate in ("api_key_id", "company_id", "webhook_event_id", "task_id", "user_id"):
            value = metadata.get(candidate)
            if value in (None, "", [], {}):
                continue
            keys.append(f"{candidate}:{value}")
        if metadata.get("source_ip"):
            keys.append(f"ip:{metadata['source_ip']}")
        if metadata.get("api_key"):
            keys.append(f"api_key_meta:{metadata['api_key']}")
    if log.get("api_key"):
        keys.append(f"api_key:{log['api_key']}")
    if log.get("ip_address"):
        keys.append(f"ip:{log['ip_address']}")
    # Deduplicate while preserving order
    seen: set[str] = set()
    unique_keys: list[str] = []
    for key in keys:
        if key not in seen:
            unique_keys.append(key)
            seen.add(key)
    return unique_keys


def _extract_audit_service(action: Any) -> str:
    if not action:
        return "system"
    text = str(action)
    return text.split(".", 1)[0]


def _prepare_api_key_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    today = date.today()
    prepared: list[dict[str, Any]] = []
    active_count = 0
    for row in rows:
        expiry = row.get("expiry_date")
        is_expired = bool(expiry and isinstance(expiry, date) and expiry < today)
        if not is_expired:
            active_count += 1
        usage_entries: list[dict[str, Any]] = []
        for entry in row.get("usage", []) or []:
            usage_entries.append(
                {
                    "ip_address": entry.get("ip_address"),
                    "usage_count": entry.get("usage_count", 0),
                    "last_used_iso": _to_iso(entry.get("last_used_at")),
                }
            )
        expiry_iso = None
        if isinstance(expiry, date):
            expiry_iso = datetime.combine(expiry, time.min, tzinfo=timezone.utc).isoformat()
        prepared.append(
            {
                "id": row["id"],
                "description": row.get("description"),
                "key_preview": mask_api_key(row.get("key_prefix")),
                "created_iso": _to_iso(row.get("created_at")),
                "expiry_date": expiry.isoformat() if isinstance(expiry, date) else None,
                "expiry_iso": expiry_iso,
                "last_used_iso": _to_iso(row.get("last_used_at")),
                "last_seen_iso": _to_iso(row.get("last_seen_at")),
                "usage_count": row.get("usage_count", 0),
                "is_expired": is_expired,
                "usage": usage_entries,
            }
        )
    stats = {
        "total": len(prepared),
        "active": active_count,
        "expired": len(prepared) - active_count,
    }
    return prepared, stats


def _build_audit_correlations(
    logs: list[dict[str, Any]],
    *,
    service_filter: str | None = None,
    text_query: str | None = None,
    limit: int = 25,
) -> tuple[list[dict[str, Any]], list[str]]:
    services: set[str] = set()
    groups: dict[str, list[dict[str, Any]]] = {}
    for log in logs:
        service = _extract_audit_service(log.get("action"))
        services.add(service)
        for key in _derive_correlation_keys(log):
            groups.setdefault(key, []).append(log)
    correlations: list[dict[str, Any]] = []
    text = text_query.lower().strip() if text_query else None
    for key, items in groups.items():
        if len(items) < 2:
            continue
        item_services = sorted({_extract_audit_service(item.get("action")) for item in items})
        if service_filter and service_filter not in item_services:
            continue
        label = _format_correlation_label(key)
        if text and text not in label.lower():
            continue
        sorted_items = sorted(
            items,
            key=lambda entry: entry.get("created_at") or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        latest = sorted_items[0]
        events: list[dict[str, Any]] = []
        for entry in sorted_items[:8]:
            events.append(
                {
                    "id": entry.get("id"),
                    "action": entry.get("action"),
                    "service": _extract_audit_service(entry.get("action")),
                    "created_at_iso": _to_iso(entry.get("created_at")),
                    "entity_label": _format_entity_label(entry),
                    "user_email": entry.get("user_email"),
                    "user_id": entry.get("user_id"),
                    "ip_address": entry.get("ip_address"),
                    "metadata": _serialise_for_json(entry.get("metadata")),
                }
            )
        correlations.append(
            {
                "key": key,
                "label": label,
                "services": item_services,
                "event_count": len(items),
                "latest_iso": _to_iso(latest.get("created_at")),
                "events": events,
            }
        )
    correlations.sort(
        key=lambda item: item.get("latest_iso") or "",
        reverse=True,
    )
    return correlations[:limit], sorted(services)


async def _render_api_keys_dashboard(
    request: Request,
    current_user: dict[str, Any],
    *,
    search: str | None,
    include_expired: bool,
    order_by: str,
    order_direction: str,
    service_filter: str | None,
    correlation_search: str | None,
    status_message: str | None = None,
    errors: list[str] | None = None,
    new_api_key: dict[str, Any] | None = None,
):
    rows = await api_key_repo.list_api_keys_with_usage(
        search=search,
        include_expired=include_expired,
        order_by=order_by,
        order_direction=order_direction,
    )
    prepared_keys, stats = _prepare_api_key_rows(rows)
    logs = await audit_repo.list_audit_logs(limit=250)
    correlations, service_names = _build_audit_correlations(
        logs,
        service_filter=service_filter,
        text_query=correlation_search,
    )
    filter_state = {
        "search": search or "",
        "include_expired": "1" if include_expired else "0",
        "order_by": order_by,
        "order_direction": order_direction,
        "service_filter": service_filter or "",
        "correlation_search": correlation_search or "",
    }
    filters = {
        "search": search or "",
        "include_expired": include_expired,
        "order_by": order_by,
        "order_direction": order_direction,
        "service_filter": service_filter or "",
        "correlation_search": correlation_search or "",
    }
    order_options = [
        {"value": value, "label": label}
        for value, label in _API_KEY_ORDER_CHOICES
    ]
    direction_options = [
        {"value": value, "label": label}
        for value, label in _API_KEY_DIRECTION_CHOICES
    ]
    service_options = [
        {"value": value, "label": value.replace("_", " ").title()}
        for value in service_names
    ]
    extra = {
        "title": "API credentials",
        "api_keys": prepared_keys,
        "api_key_stats": stats,
        "filters": filters,
        "filter_state": filter_state,
        "order_options": order_options,
        "direction_options": direction_options,
        "service_options": service_options,
        "correlations": correlations,
        "status_message": status_message,
        "errors": errors or [],
        "new_api_key": new_api_key,
    }
    return await _render_template("admin/api_keys.html", request, current_user, extra=extra)
async def _load_license_context(
    request: Request,
    *,
    require_manage: bool = True,
    require_order: bool = False,
):
    user, redirect = await _require_authenticated_user(request)
    if redirect:
        return user, None, None, None, redirect
    is_super_admin = bool(user.get("is_super_admin"))
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
    can_manage = bool(membership and membership.get("can_manage_licenses"))
    can_order = bool(membership and membership.get("can_order_licenses"))
    if require_manage and not (is_super_admin or can_manage):
        return (
            user,
            membership,
            None,
            company_id,
            RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER),
        )
    if require_order and not (is_super_admin or can_order):
        return (
            user,
            membership,
            None,
            company_id,
            RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER),
        )
    company = await company_repo.get_company_by_id(company_id)
    return user, membership, company, company_id, None


async def _load_invoice_context(request: Request):
    user, redirect = await _require_authenticated_user(request)
    if redirect:
        return user, None, None, None, redirect
    is_super_admin = bool(user.get("is_super_admin"))
    company_id_raw = user.get("company_id")
    if company_id_raw is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No company associated with the current user",
        )
    try:
        company_id = int(company_id_raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid company identifier") from exc
    membership = await user_company_repo.get_user_company(user["id"], company_id)
    can_manage = bool(membership and membership.get("can_manage_invoices"))
    if not (is_super_admin or can_manage):
        return (
            user,
            membership,
            None,
            company_id,
            RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER),
        )
    company = await company_repo.get_company_by_id(company_id)
    return user, membership, company, company_id, None


async def _send_license_webhook(
    *,
    action: str,
    company_id: int,
    license_id: int,
    quantity: int,
) -> None:
    if not settings.licenses_webhook_url or not settings.licenses_webhook_api_key:
        return
    payload = {
        "companyId": company_id,
        "licenseId": license_id,
        "quantity": quantity,
        "action": action,
    }
    headers = {
        "x-api-key": settings.licenses_webhook_api_key,
        "Content-Type": "application/json",
    }
    try:
        await webhook_monitor.enqueue_event(
            name="license-change",
            target_url=str(settings.licenses_webhook_url),
            payload=payload,
            headers=headers,
            max_attempts=5,
            backoff_seconds=300,
            attempt_immediately=True,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to enqueue license webhook", error=str(exc))


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


async def _load_shop_context(
    request: Request,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None, int | None, RedirectResponse | None]:
    user, redirect = await _require_authenticated_user(request)
    if redirect:
        return user, None, None, None, redirect

    is_super_admin = bool(user and user.get("is_super_admin"))
    company_id_raw = user.get("company_id") if user else None
    if company_id_raw is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No company associated with the current user",
        )
    try:
        company_id = int(company_id_raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid company identifier") from exc

    membership = await user_company_repo.get_user_company(user["id"], company_id)
    can_access_shop = bool(membership and membership.get("can_access_shop"))
    if not (is_super_admin or can_access_shop):
        return (
            user,
            membership,
            None,
            company_id,
            RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER),
        )

    company = await company_repo.get_company_by_id(company_id)
    return user, membership, company, company_id, None


def _companies_redirect(
    *,
    company_id: int | None = None,
    success: str | None = None,
    error: str | None = None,
    extra: dict[str, str] | None = None,
) -> RedirectResponse:
    params: dict[str, str] = {}
    if company_id is not None:
        params["company_id"] = str(company_id)
    if success:
        params["success"] = success.strip()[:200]
    if error:
        params["error"] = error.strip()[:200]
    if extra:
        for key, value in extra.items():
            if value is None:
                continue
            params[key] = value
    query = urlencode(params)
    url = "/admin/companies"
    if query:
        url = f"{url}?{query}"
    return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)


_COMPANY_PERMISSION_COLUMNS: list[dict[str, str]] = [
    {"field": "can_manage_licenses", "label": "Licenses"},
    {"field": "can_manage_office_groups", "label": "Office groups"},
    {"field": "can_manage_assets", "label": "Assets"},
    {"field": "can_manage_invoices", "label": "Invoices"},
    {"field": "can_order_licenses", "label": "Order licenses"},
    {"field": "can_access_shop", "label": "Shop"},
    {"field": "is_admin", "label": "Company admin"},
]

_STAFF_PERMISSION_OPTIONS: list[dict[str, Any]] = [
    {"value": 0, "label": "No staff access"},
    {"value": 1, "label": "Department viewer"},
    {"value": 2, "label": "Department manager"},
    {"value": 3, "label": "Full staff manager"},
]


def _sanitize_message(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:200]


async def _get_company_management_scope(
    request: Request,
    user: dict[str, Any],
) -> tuple[bool, list[dict[str, Any]], dict[int, dict[str, Any]]]:
    is_super_admin = bool(user.get("is_super_admin"))
    if is_super_admin:
        companies = await company_repo.list_companies()
        companies.sort(key=lambda item: (item.get("name") or "").lower())
        return True, companies, {}

    memberships = await user_company_repo.list_companies_for_user(user["id"])
    membership_lookup: dict[int, dict[str, Any]] = {}
    for record in memberships:
        raw_company_id = record.get("company_id")
        try:
            company_id = int(raw_company_id)
        except (TypeError, ValueError):
            continue
        staff_permission = int(record.get("staff_permission") or 0)
        if (
            bool(record.get("is_admin"))
            or bool(record.get("can_manage_staff"))
            or staff_permission > 0
        ):
            membership_lookup[company_id] = record

    if not membership_lookup:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
        )

    companies: list[dict[str, Any]] = []
    for company_id in sorted(membership_lookup.keys()):
        company = await company_repo.get_company_by_id(company_id)
        if company:
            companies.append(company)

    if not companies:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
        )

    return False, companies, membership_lookup


async def _render_companies_dashboard(
    request: Request,
    user: dict[str, Any],
    *,
    selected_company_id: int | None = None,
    success_message: str | None = None,
    error_message: str | None = None,
    temporary_password: str | None = None,
    invited_email: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    is_super_admin, managed_companies, membership_lookup = await _get_company_management_scope(
        request, user
    )

    ordered_company_ids: list[int] = [
        int(company["id"]) for company in managed_companies if company.get("id") is not None
    ]

    effective_company_id = selected_company_id
    if effective_company_id is not None and effective_company_id not in ordered_company_ids:
        effective_company_id = ordered_company_ids[0] if ordered_company_ids else None

    if effective_company_id is None and not is_super_admin and ordered_company_ids:
        active_company_raw = getattr(request.state, "active_company_id", None)
        try:
            active_company_candidate = int(active_company_raw)
        except (TypeError, ValueError):
            active_company_candidate = None
        if active_company_candidate in ordered_company_ids:
            effective_company_id = active_company_candidate
        else:
            effective_company_id = ordered_company_ids[0]

    if is_super_admin and effective_company_id is None:
        assignments = await user_company_repo.list_assignments()
    elif effective_company_id is not None:
        assignments = await user_company_repo.list_assignments(effective_company_id)
    else:
        assignments = []

    role_rows = await role_repo.list_roles()
    role_options: list[dict[str, Any]] = []
    for record in role_rows:
        role_id = record.get("id")
        name = (record.get("name") or "").strip()
        if role_id is None or not name:
            continue
        role_options.append(
            {
                "id": int(role_id),
                "name": name,
                "description": (record.get("description") or "").strip(),
                "is_system": bool(record.get("is_system")),
            }
        )

    user_options: list[dict[str, Any]] = []
    if is_super_admin:
        raw_users = await user_repo.list_users()
        for record in raw_users:
            user_id = record.get("id")
            email = (record.get("email") or "").strip()
            if user_id is None or not email:
                continue
            user_options.append({"id": user_id, "email": email})

    extra = {
        "title": "Company administration",
        "managed_companies": managed_companies,
        "selected_company_id": effective_company_id,
        "assignments": assignments,
        "permission_columns": _COMPANY_PERMISSION_COLUMNS,
        "staff_permission_options": _STAFF_PERMISSION_OPTIONS,
        "role_options": role_options,
        "is_super_admin": is_super_admin,
        "success_message": success_message,
        "error_message": error_message,
        "temporary_password": temporary_password,
        "invited_email": invited_email,
        "user_options": user_options,
    }

    response = await _render_template("admin/companies.html", request, user, extra=extra)
    response.status_code = status_code
    return response


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


def _parse_multipart_fallback(text_body: str) -> dict[str, str]:
    """Parse rudimentary multipart form payloads when the content type is wrong."""

    boundary: str | None = None
    for line in text_body.splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        if candidate.startswith("--") and "content-disposition" not in candidate.lower():
            boundary = candidate
            break

    if not boundary:
        return {}

    parts = text_body.split(boundary)
    parsed: dict[str, str] = {}

    for part in parts:
        chunk = part.strip()
        if not chunk or chunk == "--":
            continue
        if chunk.startswith("--") and not chunk.strip("-"):
            continue

        # Remove any leading CRLF left after splitting.
        while chunk.startswith("\r\n"):
            chunk = chunk[2:]

        headers_section, separator, value_section = chunk.partition("\r\n\r\n")
        if not separator:
            continue

        field_name: str | None = None
        for header_line in headers_section.splitlines():
            header = header_line.strip()
            if not header.lower().startswith("content-disposition"):
                continue
            for attribute in header.split(";"):
                attribute = attribute.strip()
                if attribute.lower().startswith("name="):
                    field_name = attribute.split("=", 1)[1].strip().strip('"')
                    break
            if field_name:
                break

        if not field_name:
            continue

        value = value_section.rstrip()
        if value.endswith("--"):
            value = value[:-2].rstrip()

        parsed[field_name] = value

    return parsed


async def _extract_switch_company_payload(request: Request) -> dict[str, Any]:
    raw_content_type = request.headers.get("content-type", "")
    content_type = raw_content_type.split(";", 1)[0].strip().lower()

    data: dict[str, Any] = {}

    if content_type == "application/json":
        try:
            payload = await request.json()
        except (json.JSONDecodeError, ValueError, RuntimeError, UnicodeDecodeError):
            payload = None
        if isinstance(payload, dict):
            return payload
        # Fall back to parsing the raw body below so mislabelled JSON requests
        # (for example, form submissions with an incorrect content type) are
        # still handled gracefully.

    should_attempt_form = content_type in {
        "application/x-www-form-urlencoded",
        "multipart/form-data",
    }

    cached_form: FormData | None = getattr(request, "_form", None)
    form_data: FormData | None
    if cached_form is not None:
        form_data = cached_form
    elif should_attempt_form:
        try:
            form_data = await request.form()
        except Exception:  # pragma: no cover - fallback when Starlette cannot parse the body
            form_data = None
    else:
        form_data = None

    if form_data is not None:
        keys = list(form_data.keys())
        if keys:
            for key in keys:
                values = form_data.getlist(key)
                if values:
                    data[key] = values[0]
            return data

    body_bytes: bytes | None = getattr(request, "_body", None)

    if body_bytes is None:
        try:
            body_bytes = await request.body()
        except RuntimeError:
            body_bytes = getattr(request, "_body", None)

    if not body_bytes:
        return data

    charset = getattr(request, "charset", None) or "utf-8"
    try:
        text_body = body_bytes.decode(charset, errors="replace")
    except LookupError:  # pragma: no cover - unsupported encodings
        text_body = body_bytes.decode("utf-8", errors="replace")

    lower_body = text_body.lower()

    if "content-disposition:" in lower_body and "form-data" in lower_body:
        parsed = _parse_multipart_fallback(text_body)
        if parsed:
            for key, value in parsed.items():
                if key not in data:
                    data[key] = value
            if data:
                return data

    if "=" in text_body or "&" in text_body:
        for key, value in parse_qsl(text_body, keep_blank_values=True):
            if key not in data:
                data[key] = value

        if data:
            return data

    try:
        payload = json.loads(text_body)
    except (json.JSONDecodeError, ValueError):
        return data

    if isinstance(payload, dict):
        return payload

    return data


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user, redirect = await _require_authenticated_user(request)
    if redirect:
        return redirect
    return await _render_template("dashboard.html", request, user)


@app.get("/licenses", response_class=HTMLResponse)
async def licenses_page(request: Request):
    user, membership, company, company_id, redirect = await _load_license_context(request)
    if redirect:
        return redirect
    records = await license_repo.list_company_licenses(company_id)
    formatted: list[dict[str, Any]] = []
    for record in records:
        expiry_value = record.get("expiry_date")
        if isinstance(expiry_value, datetime):
            expiry_display = expiry_value.strftime("%Y-%m-%d")
        elif isinstance(expiry_value, date):
            expiry_display = expiry_value.strftime("%Y-%m-%d")
        elif expiry_value:
            expiry_display = str(expiry_value)
        else:
            expiry_display = ""
        formatted.append(record | {"expiry_display": expiry_display})
    is_super_admin = bool(user.get("is_super_admin"))
    can_order = bool(is_super_admin or (membership and membership.get("can_order_licenses")))
    can_manage = bool(is_super_admin or (membership and membership.get("can_manage_licenses")))
    credentials = await m365_repo.get_credentials(company_id)
    extra = {
        "title": "Licenses",
        "licenses": formatted,
        "company": company,
        "can_order_licenses": can_order,
        "can_manage_licenses": can_manage,
        "webhook_enabled": bool(settings.licenses_webhook_url and settings.licenses_webhook_api_key),
        "has_m365_credentials": bool(credentials),
    }
    return await _render_template("licenses/index.html", request, user, extra=extra)


@app.get("/invoices", response_class=HTMLResponse)
async def invoices_page(request: Request):
    user, membership, company, company_id, redirect = await _load_invoice_context(request)
    if redirect:
        return redirect
    records = await invoice_repo.list_company_invoices(company_id)
    status_class_map = {
        "paid": "status--active",
        "sent": "status--invited",
        "pending": "status--invited",
        "issued": "status--invited",
        "draft": "status--invited",
        "overdue": "status--suspended",
        "past due": "status--suspended",
        "void": "status--invited",
        "cancelled": "status--invited",
    }
    total_amount = Decimal("0.00")
    paid_count = 0
    today = datetime.now(timezone.utc).date()
    formatted: list[dict[str, Any]] = []
    for record in records:
        amount_value = record.get("amount")
        amount_decimal = amount_value if isinstance(amount_value, Decimal) else Decimal(str(amount_value or "0"))
        amount_decimal = amount_decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total_amount += amount_decimal
        status_text_raw = (record.get("status") or "").strip()
        status_slug = status_text_raw.lower()
        if status_slug == "paid":
            paid_count += 1
        status_class = status_class_map.get(status_slug, "status--invited" if status_slug else "")
        due_value = record.get("due_date")
        if isinstance(due_value, datetime):
            due_value = due_value.date()
        due_display = None
        due_iso = ""
        is_overdue = False
        if isinstance(due_value, date):
            due_display = due_value.strftime("%d %b %Y")
            due_iso = datetime.combine(due_value, time.min, tzinfo=timezone.utc).isoformat()
            is_overdue = bool(status_slug not in {"paid", "void", "cancelled"} and due_value < today)
        formatted.append(
            record
            | {
                "amount": amount_decimal,
                "amount_display": f"${amount_decimal:,.2f}",
                "due_display": due_display,
                "due_iso": due_iso,
                "due_sort": due_iso,
                "status_display": status_text_raw.title() if status_text_raw else "—",
                "status_class": status_class,
                "status_slug": status_slug,
                "is_overdue": is_overdue,
            }
        )
    unpaid_count = max(len(records) - paid_count, 0)
    total_amount_display = f"${total_amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}"
    status_options = sorted({invoice["status_slug"] for invoice in formatted if invoice["status_slug"]})
    extra = {
        "title": "Invoices",
        "invoices": formatted,
        "company": company,
        "has_invoices": bool(formatted),
        "total_amount_display": total_amount_display,
        "paid_count": paid_count,
        "unpaid_count": unpaid_count,
        "status_options": status_options,
        "can_delete_invoices": bool(user.get("is_super_admin")),
    }
    return await _render_template("invoices/index.html", request, user, extra=extra)


@app.get("/licenses/{license_id}/allocated", response_class=JSONResponse)
async def license_allocations(request: Request, license_id: int):
    user, membership, _, company_id, redirect = await _load_license_context(request)
    if redirect:
        return redirect
    record = await license_repo.get_license_by_id(license_id)
    if not record or int(record.get("company_id", 0)) != company_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="License not found")
    members = await license_repo.list_staff_for_license(license_id)
    return JSONResponse(members)


@app.post("/licenses/{license_id}/order", response_class=JSONResponse)
async def order_license(request: Request, license_id: int):
    user, membership, _, company_id, redirect = await _load_license_context(
        request,
        require_manage=False,
        require_order=True,
    )
    if redirect:
        return redirect
    record = await license_repo.get_license_by_id(license_id)
    if not record or int(record.get("company_id", 0)) != company_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="License not found")
    try:
        payload = await request.json()
    except Exception as exc:  # pragma: no cover - defensive parsing
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payload") from exc
    quantity = int(payload.get("quantity", 0) or 0)
    if quantity <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Quantity must be greater than zero")
    await _send_license_webhook(
        action="order",
        company_id=company_id,
        license_id=license_id,
        quantity=quantity,
    )
    log_info(
        "License order submitted",
        company_id=company_id,
        license_id=license_id,
        quantity=quantity,
        user_id=user.get("id"),
    )
    return JSONResponse({"success": True})


@app.post("/licenses/{license_id}/remove", response_class=JSONResponse)
async def remove_license(request: Request, license_id: int):
    user, membership, _, company_id, redirect = await _load_license_context(
        request,
        require_manage=False,
        require_order=True,
    )
    if redirect:
        return redirect
    record = await license_repo.get_license_by_id(license_id)
    if not record or int(record.get("company_id", 0)) != company_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="License not found")
    try:
        payload = await request.json()
    except Exception as exc:  # pragma: no cover - defensive parsing
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payload") from exc
    quantity = int(payload.get("quantity", 0) or 0)
    if quantity <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Quantity must be greater than zero")
    await _send_license_webhook(
        action="remove",
        company_id=company_id,
        license_id=license_id,
        quantity=quantity,
    )
    log_info(
        "License removal requested",
        company_id=company_id,
        license_id=license_id,
        quantity=quantity,
        user_id=user.get("id"),
    )
    return JSONResponse({"success": True})


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


@app.get("/m365", response_class=HTMLResponse)
async def m365_page(request: Request, error: str | None = None):
    user, membership, company, company_id, redirect = await _load_license_context(request)
    if redirect:
        return redirect
    credentials = await m365_service.get_credentials(company_id)
    credential_view = None
    if credentials:
        expires = credentials.get("token_expires_at")
        if isinstance(expires, datetime):
            expires_display = expires.replace(tzinfo=timezone.utc).isoformat()
        elif expires:
            expires_display = str(expires)
        else:
            expires_display = None
        credential_view = {
            "tenant_id": credentials.get("tenant_id"),
            "client_id": credentials.get("client_id"),
            "token_expires_at": expires_display,
        }
    extra = {
        "title": "Office 365",
        "company": company,
        "credential": credential_view,
        "error": error,
        "is_super_admin": bool(user.get("is_super_admin")),
        "has_credentials": bool(credentials),
        "admin_credentials_configured": bool(
            settings.m365_admin_client_id and settings.m365_admin_client_secret
        ),
    }
    return await _render_template("m365/index.html", request, user, extra=extra)


@app.post("/m365/credentials", response_class=RedirectResponse)
async def save_m365_credentials(
    request: Request,
    tenant_id: str = Form(..., alias="tenantId"),
    client_id: str = Form(..., alias="clientId"),
    client_secret: str = Form(..., alias="clientSecret"),
):
    user, membership, _, company_id, redirect = await _load_license_context(request)
    if redirect:
        return redirect
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin privileges required")
    await m365_service.upsert_credentials(
        company_id=company_id,
        tenant_id=tenant_id.strip(),
        client_id=client_id.strip(),
        client_secret=client_secret.strip(),
    )
    log_info("Microsoft 365 credentials updated", company_id=company_id, user_id=user.get("id"))
    return RedirectResponse(url="/m365", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/m365/credentials/delete", response_class=RedirectResponse)
async def delete_m365_credentials(request: Request):
    user, membership, _, company_id, redirect = await _load_license_context(request)
    if redirect:
        return redirect
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin privileges required")
    await m365_service.delete_credentials(company_id)
    log_info("Microsoft 365 credentials deleted", company_id=company_id, user_id=user.get("id"))
    return RedirectResponse(url="/m365", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/m365/sync", response_class=JSONResponse)
async def sync_m365(request: Request):
    user, membership, _, company_id, redirect = await _load_license_context(request)
    if redirect:
        return redirect
    try:
        await m365_service.sync_company_licenses(company_id)
    except m365_service.M365Error as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    log_info("Microsoft 365 license sync triggered", company_id=company_id, user_id=user.get("id"))
    return JSONResponse({"success": True})


@app.get("/m365/connect")
async def m365_connect(request: Request):
    user, membership, _, company_id, redirect = await _load_license_context(request)
    if redirect:
        return redirect
    credentials = await m365_service.get_credentials(company_id)
    if not credentials:
        return RedirectResponse(url="/m365", status_code=status.HTTP_303_SEE_OTHER)
    redirect_uri = str(request.url_for("m365_callback"))
    state = oauth_state_serializer.dumps({
        "company_id": company_id,
        "user_id": user.get("id"),
    })
    params = {
        "client_id": credentials["client_id"],
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": "offline_access https://graph.microsoft.com/.default User.Read.All Directory.Read.All",
        "state": state,
        "prompt": "consent",
    }
    authorize_url = (
        f"https://login.microsoftonline.com/{credentials['tenant_id']}/oauth2/v2.0/authorize"
        f"?{urlencode(params)}"
    )
    return RedirectResponse(url=authorize_url, status_code=status.HTTP_303_SEE_OTHER)


@app.get("/m365/callback", name="m365_callback")
async def m365_callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
    if error:
        message = request.query_params.get("error_description", error)
        encoded = urlencode({"error": message})
        return RedirectResponse(url=f"/m365?{encoded}", status_code=status.HTTP_303_SEE_OTHER)
    if not code or not state:
        return RedirectResponse(url="/m365?error=invalid+response", status_code=status.HTTP_303_SEE_OTHER)
    try:
        state_data = oauth_state_serializer.loads(state)
    except BadSignature:
        return RedirectResponse(url="/m365?error=invalid+state", status_code=status.HTTP_303_SEE_OTHER)
    company_id = int(state_data.get("company_id", 0))
    credentials = await m365_service.get_credentials(company_id)
    if not credentials:
        return RedirectResponse(url="/m365?error=missing+credentials", status_code=status.HTTP_303_SEE_OTHER)
    token_endpoint = f"https://login.microsoftonline.com/{credentials['tenant_id']}/oauth2/v2.0/token"
    redirect_uri = str(request.url_for("m365_callback"))
    data = {
        "client_id": credentials["client_id"],
        "client_secret": credentials.get("client_secret") or "",
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "scope": "offline_access https://graph.microsoft.com/.default User.Read.All Directory.Read.All",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(token_endpoint, data=data)
    if response.status_code != 200:
        log_error(
            "Microsoft 365 authorization failed",
            status=response.status_code,
            body=response.text,
        )
        return RedirectResponse(url="/m365?error=authorization+failed", status_code=status.HTTP_303_SEE_OTHER)
    payload = response.json()
    refresh_token = payload.get("refresh_token")
    access_token = payload.get("access_token")
    expires_in = payload.get("expires_in")
    expires_at = None
    if isinstance(expires_in, (int, float)):
        expires_at = datetime.utcnow() + timedelta(seconds=float(expires_in))
    await m365_repo.update_tokens(
        company_id=company_id,
        refresh_token=encrypt_secret(refresh_token) if refresh_token else None,
        access_token=encrypt_secret(access_token) if access_token else None,
        token_expires_at=expires_at.replace(tzinfo=None) if expires_at else None,
    )
    log_info("Microsoft 365 OAuth callback processed", company_id=company_id)
    return RedirectResponse(url="/m365", status_code=status.HTTP_303_SEE_OTHER)


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

    def _product_has_price(product: Mapping[str, Any]) -> bool:
        raw_price = product.get("price")
        if raw_price is None:
            return False
        try:
            return Decimal(str(raw_price)) > 0
        except (InvalidOperation, TypeError, ValueError):
            return False

    products = [product for product in products if _product_has_price(product)]

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


@app.post("/cart/add", response_class=RedirectResponse, include_in_schema=False)
async def add_to_cart(request: Request) -> RedirectResponse:
    user, membership, company, company_id, redirect = await _load_shop_context(request)
    if redirect:
        return redirect

    session = await session_manager.load_session(request)
    if not session:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    form = await request.form()
    product_id_raw = form.get("productId")
    quantity_raw = form.get("quantity")

    try:
        product_id = int(product_id_raw)
    except (TypeError, ValueError):
        return RedirectResponse(url=request.url_for("shop_page"), status_code=status.HTTP_303_SEE_OTHER)

    try:
        requested_quantity = int(quantity_raw) if quantity_raw is not None else 1
    except (TypeError, ValueError):
        requested_quantity = 1
    if requested_quantity <= 0:
        requested_quantity = 1

    product = await shop_repo.get_product_by_id(
        product_id,
        company_id=company_id,
    )
    if not product:
        return RedirectResponse(url=request.url_for("shop_page"), status_code=status.HTTP_303_SEE_OTHER)

    available_stock = int(product.get("stock") or 0)
    existing = await cart_repo.get_item(session.id, product_id)
    existing_quantity = existing.get("quantity") if existing else 0
    if available_stock <= 0 or existing_quantity + requested_quantity > available_stock:
        remaining = max(available_stock - existing_quantity, 0)
        message = quote(f"Cannot add item. Only {remaining} left in stock.")
        return RedirectResponse(
            url=f"{request.url_for('shop_page')}?cart_error={message}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    is_vip = bool(company and int(company.get("is_vip") or 0) == 1)
    price_source = product.get("price")
    if is_vip and product.get("vip_price") is not None:
        price_source = product.get("vip_price")
    unit_price = Decimal(str(price_source or 0))
    new_quantity = existing_quantity + requested_quantity

    await cart_repo.upsert_item(
        session_id=session.id,
        product_id=product_id,
        quantity=new_quantity,
        unit_price=unit_price,
        name=str(product.get("name") or ""),
        sku=str(product.get("sku") or ""),
        vendor_sku=product.get("vendor_sku"),
        description=product.get("description"),
        image_url=product.get("image_url"),
    )

    return RedirectResponse(url=request.url_for("shop_page"), status_code=status.HTTP_303_SEE_OTHER)


@app.get("/cart", response_class=HTMLResponse, name="cart_page")
async def view_cart(
    request: Request,
    order_message: str | None = Query(None, alias="orderMessage"),
):
    user, membership, company, company_id, redirect = await _load_shop_context(request)
    if redirect:
        return redirect

    session = await session_manager.load_session(request)
    if not session:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    items = await cart_repo.list_items(session.id)
    cart_items: list[dict[str, Any]] = []
    total = Decimal("0")
    for item in items:
        unit_price = item.get("unit_price")
        if not isinstance(unit_price, Decimal):
            unit_price = Decimal(str(unit_price or 0))
        quantity = int(item.get("quantity") or 0)
        line_total = unit_price * quantity
        total += line_total
        hydrated = dict(item)
        hydrated["unit_price"] = unit_price
        hydrated["line_total"] = line_total
        cart_items.append(hydrated)

    extra = {
        "title": "Cart",
        "cart_items": cart_items,
        "cart_total": total,
        "order_message": order_message,
    }
    return await _render_template("shop/cart.html", request, user, extra=extra)


def _normalise_status_badge(label: str) -> str:
    lowered = label.lower()
    if any(token in lowered for token in ("cancel", "decline", "failed")):
        return "badge--danger"
    if any(token in lowered for token in ("ship", "delivered", "complete", "fulfilled")):
        return "badge--success"
    if any(token in lowered for token in ("pending", "processing", "backorder", "hold")):
        return "badge--warning"
    return "badge--muted"


def _summarise_orders(
    orders: list[dict[str, Any]],
    *,
    attribute: str,
) -> list[dict[str, Any]]:
    total = len(orders)
    if total == 0:
        return []
    counter = Counter(order.get(attribute, "") or "Unknown" for order in orders)
    summary: list[dict[str, Any]] = []
    for label, count in sorted(counter.items(), key=lambda item: (-item[1], item[0].lower())):
        percentage = round((count / total) * 100, 1)
        summary.append({"label": label, "count": count, "percentage": percentage})
    return summary


@app.get("/orders", response_class=HTMLResponse)
async def orders_page(
    request: Request,
    status_filter: str | None = Query(None, alias="status"),
    shipping_filter: str | None = Query(None, alias="shippingStatus"),
):
    user, membership, company, company_id, redirect = await _load_shop_context(request)
    if redirect:
        return redirect

    orders_raw = await shop_repo.list_order_summaries(company_id)

    def _label(value: str | None) -> str:
        text = (value or "").strip()
        return text if text else "Pending"

    enriched_orders: list[dict[str, Any]] = []
    for order in orders_raw:
        label = _label(order.get("status"))
        shipping_label = _label(order.get("shipping_status"))
        record = dict(order)
        record["status_label"] = label
        record["shipping_status_label"] = shipping_label
        record["status_value"] = label.lower()
        record["shipping_status_value"] = shipping_label.lower()
        record["status_badge"] = _normalise_status_badge(label)
        record["shipping_badge"] = _normalise_status_badge(shipping_label)
        record["order_date_iso"] = order.get("order_date")
        record["eta_iso"] = order.get("eta")
        enriched_orders.append(record)

    status_options = sorted(
        { (order["status_value"], order["status_label"]) for order in enriched_orders },
        key=lambda item: item[1].lower(),
    )
    shipping_options = sorted(
        { (order["shipping_status_value"], order["shipping_status_label"]) for order in enriched_orders },
        key=lambda item: item[1].lower(),
    )

    status_option_map = {value: label for value, label in status_options}
    shipping_option_map = {value: label for value, label in shipping_options}

    status_key = (status_filter or "").strip().lower() or None
    if status_key not in status_option_map:
        status_key = None
    shipping_key = (shipping_filter or "").strip().lower() or None
    if shipping_key not in shipping_option_map:
        shipping_key = None

    filtered_orders = [
        order
        for order in enriched_orders
        if (status_key is None or order["status_value"] == status_key)
        and (shipping_key is None or order["shipping_status_value"] == shipping_key)
    ]

    total_orders = len(enriched_orders)
    visible_orders = len(filtered_orders)

    status_summary = _summarise_orders(filtered_orders, attribute="status_label")
    shipping_summary = _summarise_orders(filtered_orders, attribute="shipping_status_label")

    extra = {
        "title": "Orders",
        "orders": filtered_orders,
        "status_options": [
            {"value": value, "label": label} for value, label in status_options
        ],
        "shipping_options": [
            {"value": value, "label": label} for value, label in shipping_options
        ],
        "status_filter": status_key,
        "shipping_filter": shipping_key,
        "status_summary": status_summary,
        "shipping_summary": shipping_summary,
        "orders_total": visible_orders,
        "orders_total_all": total_orders,
        "filters_active": bool(status_key or shipping_key),
    }
    return await _render_template("shop/orders.html", request, user, extra=extra)


@app.post("/cart/remove", response_class=RedirectResponse, name="cart_remove_items", include_in_schema=False)
async def remove_cart_items(request: Request) -> RedirectResponse:
    user, membership, company, company_id, redirect = await _load_shop_context(request)
    if redirect:
        return redirect

    session = await session_manager.load_session(request)
    if not session:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    form = await request.form()
    product_ids: list[int] = []
    if isinstance(form, FormData):
        raw_values = form.getlist("remove")
    else:
        raw_value = form.get("remove")
        raw_values = raw_value if isinstance(raw_value, list) else [raw_value] if raw_value is not None else []
    for value in raw_values:
        try:
            product_ids.append(int(value))
        except (TypeError, ValueError):
            continue
    await cart_repo.remove_items(session.id, product_ids)
    return RedirectResponse(url=request.url_for("cart_page"), status_code=status.HTTP_303_SEE_OTHER)


@app.post("/cart/place-order", response_class=RedirectResponse, name="cart_place_order", include_in_schema=False)
async def place_order(request: Request) -> RedirectResponse:
    user, membership, company, company_id, redirect = await _load_shop_context(request)
    if redirect:
        return redirect

    session = await session_manager.load_session(request)
    if not session:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    if company_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No active company selected")

    form = await request.form()
    po_number_raw = form.get("poNumber")
    po_number = (str(po_number_raw).strip() or None) if po_number_raw is not None else None
    if po_number and len(po_number) > 100:
        po_number = po_number[:100]

    items = await cart_repo.list_items(session.id)
    if not items:
        return RedirectResponse(url=request.url_for("cart_page"), status_code=status.HTTP_303_SEE_OTHER)

    order_number = "ORD" + "".join(secrets.choice("0123456789") for _ in range(12))

    if settings.shop_webhook_url and settings.shop_webhook_api_key:
        try:
            await webhook_monitor.enqueue_event(
                name="shop-order",
                target_url=str(settings.shop_webhook_url),
                payload={
                    "cart": [
                        {
                            "productId": item.get("product_id"),
                            "quantity": item.get("quantity"),
                            "price": float(item.get("unit_price", 0)),
                            "name": item.get("product_name"),
                            "sku": item.get("product_sku"),
                            "vendorSku": item.get("product_vendor_sku"),
                        }
                        for item in items
                    ],
                    "poNumber": po_number,
                    "orderNumber": order_number,
                    "companyId": company_id,
                },
                headers={
                    "x-api-key": settings.shop_webhook_api_key,
                    "Content-Type": "application/json",
                },
                max_attempts=5,
                backoff_seconds=300,
                attempt_immediately=True,
            )
        except Exception as exc:  # pragma: no cover - webhook safety
            log_error("Failed to enqueue shop webhook", error=str(exc))

    for item in items:
        try:
            previous_stock, new_stock = await shop_repo.create_order(
                user_id=int(user["id"]),
                company_id=company_id,
                product_id=int(item.get("product_id")),
                quantity=int(item.get("quantity")),
                order_number=order_number,
                status="pending",
                po_number=po_number,
            )
        except ValueError as exc:
            message = quote(str(exc))
            return RedirectResponse(
                url=f"{request.url_for('cart_page')}?orderMessage={message}",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        await shop_service.maybe_send_discord_stock_notification_by_id(
            int(item.get("product_id")),
            previous_stock,
            new_stock,
        )

    await cart_repo.clear_cart(session.id)
    success = quote("Your order is being processed.")
    return RedirectResponse(
        url=f"{request.url_for('cart_page')}?orderMessage={success}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.get("/myforms", response_class=HTMLResponse)
async def forms_page(request: Request):
    user, redirect = await _require_authenticated_user(request)
    if redirect:
        return redirect

    active_company_id = getattr(request.state, "active_company_id", None)
    active_company = None
    if active_company_id is not None:
        try:
            active_company = await company_repo.get_company_by_id(int(active_company_id))
        except (TypeError, ValueError):
            active_company = None

    portal_base = str(settings.portal_url) if settings.portal_url else str(request.base_url).rstrip("/")
    login_url = str(request.url_for("login_page"))

    replacements = template_variables.build_template_replacement_map(
        template_variables.TemplateContext(
            user=template_variables.TemplateContextUser(
                id=user.get("id"),
                email=user.get("email"),
                first_name=user.get("first_name"),
                last_name=user.get("last_name"),
            ),
            company=template_variables.TemplateContextCompany(
                id=int(active_company_id) if active_company_id is not None else None,
                name=(active_company or {}).get("name"),
                syncro_customer_id=(active_company or {}).get("syncro_company_id"),
            ),
            portal=template_variables.TemplateContextPortal(
                base_url=portal_base,
                login_url=login_url,
            ),
        )
    )

    raw_forms = await forms_repo.list_forms_for_user(user["id"])
    hydrated_forms: list[dict[str, Any]] = []
    for form in raw_forms:
        iframe_url = template_variables.apply_template_variables(form.get("url", ""), replacements)
        embed_html: str | None = None
        embed_code = form.get("embed_code")
        if embed_code:
            try:
                normalized = normalize_opnform_embed_code(embed_code, allowed_host=OPNFORM_ALLOWED_HOST)
                embed_html = template_variables.apply_template_variables(
                    normalized.sanitized_embed_code,
                    replacements,
                )
                iframe_url = template_variables.apply_template_variables(
                    normalized.form_url,
                    replacements,
                )
            except OpnformValidationError as exc:
                log_error(
                    "Failed to normalise OpnForm embed for rendering",
                    form_id=form.get("id"),
                    error=str(exc),
                )
        hydrated_forms.append(
            {
                "id": form.get("id"),
                "name": form.get("name"),
                "description": form.get("description"),
                "iframe_url": iframe_url,
                "embed_html": embed_html,
            }
        )

    extra = {
        "title": "Forms",
        "forms": hydrated_forms,
        "opnform_base_url": _opnform_base_url(),
    }
    return await _render_template("forms/index.html", request, user, extra=extra)


@app.get("/forms", include_in_schema=False)
async def legacy_forms_redirect() -> RedirectResponse:
    """Maintain compatibility for legacy bookmarks under /forms."""
    return RedirectResponse(url="/myforms", status_code=status.HTTP_308_PERMANENT_REDIRECT)


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

    settings = get_settings()
    staff_company = await company_repo.get_company_by_id(staff.get("company_id"))
    event_record: dict[str, Any] | None = None
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
            event_record = await webhook_monitor.enqueue_event(
                name="staff-verification",
                target_url=str(settings.verify_webhook_url),
                payload=payload,
                headers=headers,
                max_attempts=5,
                backoff_seconds=180,
                attempt_immediately=True,
            )
        except Exception as exc:
            log_error("Verify webhook failed", staff_id=staff_id, error=str(exc))

    status_code = int(event_record.get("response_status")) if event_record and event_record.get("response_status") is not None else None
    success = True
    if event_record:
        success = event_record.get("status") == "succeeded"

    return JSONResponse({
        "success": success,
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

@app.get("/admin/companies", response_class=HTMLResponse)
async def admin_companies_page(
    request: Request,
    company_id: int | None = Query(default=None),
    success: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    current_user, redirect = await _require_authenticated_user(request)
    if redirect:
        return redirect
    return await _render_companies_dashboard(
        request,
        current_user,
        selected_company_id=company_id,
        success_message=_sanitize_message(success),
        error_message=_sanitize_message(error),
    )


@app.post("/admin/companies", response_class=HTMLResponse)
async def admin_create_company(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    name = str(form.get("name", "")).strip()
    syncro_company_id = (str(form.get("syncroCompanyId", "")).strip() or None)
    xero_id = (str(form.get("xeroId", "")).strip() or None)
    is_vip = _parse_bool(form.get("isVip"))
    if not name:
        response = await _render_companies_dashboard(
            request,
            current_user,
            error_message="Enter a company name.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
        return response
    payload: dict[str, Any] = {"name": name, "is_vip": 1 if is_vip else 0}
    if syncro_company_id:
        payload["syncro_company_id"] = syncro_company_id
    if xero_id:
        payload["xero_id"] = xero_id
    try:
        created = await company_repo.create_company(**payload)
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to create company", error=str(exc))
        response = await _render_companies_dashboard(
            request,
            current_user,
            error_message="Unable to create company. Please try again.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
        return response
    return _companies_redirect(
        company_id=created.get("id"),
        success=f"Company {created.get('name')} created.",
    )


@app.post("/admin/companies/{company_id}", response_class=HTMLResponse)
async def admin_update_company(company_id: int, request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    name = str(form.get("name", "")).strip()
    syncro_company_id = (str(form.get("syncroCompanyId", "")).strip() or None)
    xero_id = (str(form.get("xeroId", "")).strip() or None)
    is_vip = _parse_bool(form.get("isVip"))
    if not name:
        response = await _render_companies_dashboard(
            request,
            current_user,
            selected_company_id=company_id,
            error_message="Enter a company name.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
        return response
    existing = await company_repo.get_company_by_id(company_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    updates: dict[str, Any] = {
        "name": name,
        "is_vip": 1 if is_vip else 0,
        "syncro_company_id": syncro_company_id,
        "xero_id": xero_id,
    }
    try:
        await company_repo.update_company(company_id, **updates)
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to update company", company_id=company_id, error=str(exc))
        response = await _render_companies_dashboard(
            request,
            current_user,
            selected_company_id=company_id,
            error_message="Unable to update company. Please try again.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
        return response
    return _companies_redirect(
        company_id=company_id,
        success=f"Company {name} updated.",
    )


async def _ensure_company_permission(
    request: Request,
    user: dict[str, Any],
    company_id: int,
    *,
    require_admin: bool = False,
    require_staff_manager: bool = False,
) -> None:
    is_super_admin, _, membership_lookup = await _get_company_management_scope(request, user)
    if is_super_admin:
        return
    membership = membership_lookup.get(company_id)
    if not membership:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    staff_permission = int(membership.get("staff_permission") or 0)
    if require_admin and not bool(membership.get("is_admin")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    if require_staff_manager and staff_permission < 3 and not bool(membership.get("can_manage_staff")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")


@app.post("/admin/companies/users/create", response_class=HTMLResponse)
async def admin_create_company_user(request: Request):
    current_user, redirect = await _require_authenticated_user(request)
    if redirect:
        return redirect
    form = await request.form()
    company_id_raw = form.get("companyId")
    email = str(form.get("email", "")).strip().lower()
    password = str(form.get("password", ""))
    first_name = (str(form.get("firstName", "")).strip() or None)
    last_name = (str(form.get("lastName", "")).strip() or None)
    try:
        company_id = int(company_id_raw)
    except (TypeError, ValueError):
        response = await _render_companies_dashboard(
            request,
            current_user,
            error_message="Select a company.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
        return response
    await _ensure_company_permission(
        request,
        current_user,
        company_id,
        require_admin=True,
        require_staff_manager=True,
    )
    if not email:
        response = await _render_companies_dashboard(
            request,
            current_user,
            selected_company_id=company_id,
            error_message="Enter an email address.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
        return response
    if len(password) < 8:
        response = await _render_companies_dashboard(
            request,
            current_user,
            selected_company_id=company_id,
            error_message="Enter a password of at least 8 characters.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
        return response
    existing_user = await user_repo.get_user_by_email(email)
    if existing_user:
        response = await _render_companies_dashboard(
            request,
            current_user,
            selected_company_id=company_id,
            error_message="A user with that email already exists.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
        return response
    created_user = await user_repo.create_user(
        email=email,
        password=password,
        first_name=first_name,
        last_name=last_name,
        company_id=company_id,
    )
    await user_repo.update_user(created_user["id"], force_password_change=1)
    await user_company_repo.assign_user_to_company(
        user_id=created_user["id"],
        company_id=company_id,
    )
    return _companies_redirect(
        company_id=company_id,
        success=f"User {email} created.",
    )


@app.post("/admin/companies/users/invite", response_class=HTMLResponse)
async def admin_invite_company_user(request: Request):
    current_user, redirect = await _require_authenticated_user(request)
    if redirect:
        return redirect
    form = await request.form()
    company_id_raw = form.get("companyId")
    email = str(form.get("email", "")).strip().lower()
    first_name = (str(form.get("firstName", "")).strip() or None)
    last_name = (str(form.get("lastName", "")).strip() or None)
    try:
        company_id = int(company_id_raw)
    except (TypeError, ValueError):
        response = await _render_companies_dashboard(
            request,
            current_user,
            error_message="Select a company.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
        return response
    await _ensure_company_permission(
        request,
        current_user,
        company_id,
        require_admin=True,
        require_staff_manager=True,
    )
    if not email:
        response = await _render_companies_dashboard(
            request,
            current_user,
            selected_company_id=company_id,
            error_message="Enter an email address.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
        return response
    existing_user = await user_repo.get_user_by_email(email)
    if existing_user:
        response = await _render_companies_dashboard(
            request,
            current_user,
            selected_company_id=company_id,
            error_message="A user with that email already exists.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
        return response
    temporary_password = secrets.token_urlsafe(12)
    created_user = await user_repo.create_user(
        email=email,
        password=temporary_password,
        first_name=first_name,
        last_name=last_name,
        company_id=company_id,
    )
    await user_repo.update_user(created_user["id"], force_password_change=1)
    await user_company_repo.assign_user_to_company(
        user_id=created_user["id"],
        company_id=company_id,
    )
    return await _render_companies_dashboard(
        request,
        current_user,
        selected_company_id=company_id,
        success_message=f"Invitation generated for {email}.",
        temporary_password=temporary_password,
        invited_email=email,
    )


@app.post("/admin/companies/assign", response_class=HTMLResponse)
async def admin_assign_user_to_company(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    user_id_raw = form.get("userId")
    company_id_raw = form.get("companyId")
    try:
        user_id = int(user_id_raw)
        company_id = int(company_id_raw)
    except (TypeError, ValueError):
        response = await _render_companies_dashboard(
            request,
            current_user,
            error_message="Select both a user and a company.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
        return response
    user_record = await user_repo.get_user_by_id(user_id)
    company_record = await company_repo.get_company_by_id(company_id)
    if not user_record or not company_record:
        response = await _render_companies_dashboard(
            request,
            current_user,
            error_message="User or company not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )
        return response
    await user_company_repo.assign_user_to_company(
        user_id=user_id,
        company_id=company_id,
    )
    return _companies_redirect(
        company_id=company_id,
        success=f"User {user_record.get('email')} assigned to {company_record.get('name')}.",
    )


@app.post("/admin/companies/assignment/{company_id}/{user_id}/permission")
async def admin_update_company_permission(company_id: int, user_id: int, request: Request):
    current_user, redirect = await _require_authenticated_user(request)
    if redirect:
        return redirect
    await _ensure_company_permission(
        request,
        current_user,
        company_id,
        require_admin=True,
    )
    form = await request.form()
    field = str(form.get("field", "")).strip()
    value = _parse_bool(form.get("value"))
    try:
        await user_company_repo.update_permission(
            user_id=user_id,
            company_id=company_id,
            field=field,
            value=value,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return JSONResponse({"success": True})


@app.post("/admin/companies/assignment/{company_id}/{user_id}/staff-permission")
async def admin_update_staff_permission(company_id: int, user_id: int, request: Request):
    current_user, redirect = await _require_authenticated_user(request)
    if redirect:
        return redirect
    await _ensure_company_permission(
        request,
        current_user,
        company_id,
        require_staff_manager=True,
    )
    form = await request.form()
    permission_raw = form.get("permission")
    try:
        permission_value = int(permission_raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid permission value")
    await user_company_repo.update_staff_permission(
        user_id=user_id,
        company_id=company_id,
        permission=permission_value,
    )
    return JSONResponse({"success": True})


@app.post("/admin/companies/assignment/{company_id}/{user_id}/role")
async def admin_update_membership_role(company_id: int, user_id: int, request: Request):
    current_user, redirect = await _require_authenticated_user(request)
    if redirect:
        return redirect
    await _ensure_company_permission(
        request,
        current_user,
        company_id,
        require_admin=True,
    )
    form = await request.form()
    role_raw = form.get("roleId") or form.get("role_id")
    try:
        role_id = int(role_raw) if role_raw is not None else None
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid role selection")
    if role_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Select a role for the membership")

    membership = await membership_repo.get_membership_by_company_user(company_id, user_id)
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
    membership_id = membership.get("id")
    if membership_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership identifier missing")

    existing_role_id = membership.get("role_id")
    if existing_role_id == role_id:
        return JSONResponse({"success": True})

    role_record = await role_repo.get_role_by_id(role_id)
    if not role_record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")

    updated = await membership_repo.update_membership(int(membership_id), role_id=role_id)

    await audit_service.log_action(
        action="membership.role_changed",
        user_id=current_user.get("id"),
        entity_type="company_membership",
        entity_id=int(membership_id),
        previous_value={"role_id": existing_role_id},
        new_value={"role_id": role_id},
        request=request,
    )

    return JSONResponse(
        {
            "success": True,
            "role_id": role_id,
            "role_name": updated.get("role_name"),
        }
    )


@app.post("/admin/companies/assignment/{company_id}/{user_id}/remove")
async def admin_remove_company_assignment(company_id: int, user_id: int, request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    await user_company_repo.remove_assignment(user_id=user_id, company_id=company_id)
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


@app.get("/admin/api-keys", response_class=HTMLResponse)
async def admin_api_keys_page(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    filters = _extract_api_key_filters(request.query_params)
    return await _render_api_keys_dashboard(request, current_user, **filters)


@app.post("/admin/api-keys", response_class=HTMLResponse)
async def admin_create_api_key_page(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    filters = _extract_api_key_filters(form)
    description = (str(form.get("description", "")).strip() or None)
    expiry_raw = form.get("expiry_date")
    expiry_date = _parse_input_date(expiry_raw) if expiry_raw else None
    errors: list[str] = []
    if expiry_raw and expiry_date is None:
        errors.append("Enter an expiry date in YYYY-MM-DD format.")
    if errors:
        return await _render_api_keys_dashboard(
            request,
            current_user,
            **filters,
            status_message=None,
            errors=errors,
        )
    try:
        raw_key, row = await api_key_repo.create_api_key(
            description=description,
            expiry_date=expiry_date,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to create API key from admin form", error=str(exc))
        errors.append("Unable to create API key. Please try again.")
        return await _render_api_keys_dashboard(
            request,
            current_user,
            **filters,
            status_message=None,
            errors=errors,
        )
    await audit_service.log_action(
        action="api_keys.create",
        user_id=current_user.get("id"),
        entity_type="api_key",
        entity_id=row["id"],
        new_value={
            "description": description,
            "expiry_date": expiry_date.isoformat() if isinstance(expiry_date, date) else None,
        },
        request=request,
    )
    new_api_key = {
        "id": row["id"],
        "value": raw_key,
        "key_preview": mask_api_key(row.get("key_prefix")),
        "description": row.get("description"),
        "expiry_iso": (
            datetime.combine(row.get("expiry_date"), time.min, tzinfo=timezone.utc).isoformat()
            if row.get("expiry_date")
            else None
        ),
    }
    status_message = "New API key created. Store the value securely; it will not be shown again."
    return await _render_api_keys_dashboard(
        request,
        current_user,
        **filters,
        status_message=status_message,
        errors=None,
        new_api_key=new_api_key,
    )


@app.post("/admin/api-keys/rotate", response_class=HTMLResponse)
async def admin_rotate_api_key_page(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    filters = _extract_api_key_filters(form)
    errors: list[str] = []
    api_key_id_raw = form.get("api_key_id")
    try:
        api_key_id = int(api_key_id_raw)
    except (TypeError, ValueError):
        errors.append("Invalid API key identifier supplied for rotation.")
        return await _render_api_keys_dashboard(
            request,
            current_user,
            **filters,
            status_message=None,
            errors=errors,
        )
    description = str(form.get("description", "")).strip() or None
    expiry_raw = form.get("expiry_date")
    expiry_date = _parse_input_date(expiry_raw) if expiry_raw else None
    if expiry_raw and expiry_date is None:
        errors.append("Enter a valid expiry date in YYYY-MM-DD format.")
    retire_previous = _parse_bool(form.get("retire_previous"), default=True)
    if errors:
        return await _render_api_keys_dashboard(
            request,
            current_user,
            **filters,
            status_message=None,
            errors=errors,
        )
    existing = await api_key_repo.get_api_key_with_usage(api_key_id)
    if not existing:
        errors.append("The selected API key could not be found. It may have been deleted.")
        return await _render_api_keys_dashboard(
            request,
            current_user,
            **filters,
            status_message=None,
            errors=errors,
        )
    final_description = description if description is not None else existing.get("description")
    final_expiry = expiry_date if expiry_date is not None else existing.get("expiry_date")
    try:
        raw_key, row = await api_key_repo.create_api_key(
            description=final_description,
            expiry_date=final_expiry,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to rotate API key from admin form", api_key_id=api_key_id, error=str(exc))
        errors.append("Unable to rotate API key. Please try again.")
        return await _render_api_keys_dashboard(
            request,
            current_user,
            **filters,
            status_message=None,
            errors=errors,
        )
    metadata = {
        "rotated_from": api_key_id,
        "retired_previous": retire_previous,
    }
    await audit_service.log_action(
        action="api_keys.rotate",
        user_id=current_user.get("id"),
        entity_type="api_key",
        entity_id=row["id"],
        previous_value=None,
        new_value={
            "description": final_description,
            "expiry_date": final_expiry.isoformat() if isinstance(final_expiry, date) else None,
        },
        metadata=metadata,
        request=request,
    )
    if retire_previous:
        retirement_date = date.today()
        await api_key_repo.update_api_key_expiry(api_key_id, retirement_date)
        await audit_service.log_action(
            action="api_keys.retire",
            user_id=current_user.get("id"),
            entity_type="api_key",
            entity_id=api_key_id,
            previous_value={
                "description": existing.get("description"),
                "expiry_date": existing.get("expiry_date").isoformat()
                if isinstance(existing.get("expiry_date"), date)
                else None,
                "key_preview": mask_api_key(existing.get("key_prefix")),
            },
            new_value={"expiry_date": retirement_date.isoformat()},
            metadata={"rotated_to": row["id"]},
            request=request,
        )
    new_api_key = {
        "id": row["id"],
        "value": raw_key,
        "key_preview": mask_api_key(row.get("key_prefix")),
        "description": row.get("description"),
        "expiry_iso": (
            datetime.combine(row.get("expiry_date"), time.min, tzinfo=timezone.utc).isoformat()
            if row.get("expiry_date")
            else None
        ),
        "rotated_from": api_key_id,
    }
    status_message = (
        "API key rotated. Copy the replacement key below and distribute it to integrated services."
    )
    return await _render_api_keys_dashboard(
        request,
        current_user,
        **filters,
        status_message=status_message,
        errors=None,
        new_api_key=new_api_key,
    )


@app.post("/admin/api-keys/delete", response_class=HTMLResponse)
async def admin_delete_api_key_page(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    filters = _extract_api_key_filters(form)
    errors: list[str] = []
    api_key_id_raw = form.get("api_key_id")
    try:
        api_key_id = int(api_key_id_raw)
    except (TypeError, ValueError):
        errors.append("Invalid API key identifier supplied for deletion.")
        return await _render_api_keys_dashboard(
            request,
            current_user,
            **filters,
            status_message=None,
            errors=errors,
        )
    existing = await api_key_repo.get_api_key_with_usage(api_key_id)
    if not existing:
        errors.append("API key not found or already deleted.")
        return await _render_api_keys_dashboard(
            request,
            current_user,
            **filters,
            status_message=None,
            errors=errors,
        )
    await api_key_repo.delete_api_key(api_key_id)
    await audit_service.log_action(
        action="api_keys.delete",
        user_id=current_user.get("id"),
        entity_type="api_key",
        entity_id=api_key_id,
        previous_value={
            "description": existing.get("description"),
            "expiry_date": existing.get("expiry_date").isoformat()
            if isinstance(existing.get("expiry_date"), date)
            else None,
            "key_preview": mask_api_key(existing.get("key_prefix")),
        },
        request=request,
    )
    status_message = f"API key {mask_api_key(existing.get('key_prefix'))} has been revoked."
    return await _render_api_keys_dashboard(
        request,
        current_user,
        **filters,
        status_message=status_message,
        errors=None,
        new_api_key=None,
    )


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


@app.get("/admin/automation", response_class=HTMLResponse)
async def admin_automation(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    tasks = await scheduled_tasks_repo.list_tasks()
    prepared_tasks: list[dict[str, Any]] = []
    for task in tasks:
        serialised_task = _serialise_mapping(task)
        serialised_task["last_run_iso"] = _to_iso(task.get("last_run_at"))
        prepared_tasks.append(serialised_task)
    command_options = [
        {"value": "sync_staff", "label": "Sync staff directory"},
        {"value": "sync_o365", "label": "Sync Microsoft 365 licenses"},
    ]
    existing_commands = {task.get("command") for task in tasks if task.get("command")}
    for command in sorted(existing_commands):
        if command and command not in {option["value"] for option in command_options}:
            command_options.append({"value": str(command), "label": str(command)})
    extra = {
        "title": "Automation & monitoring",
        "tasks": prepared_tasks,
        "command_options": command_options,
    }
    return await _render_template("admin/automation.html", request, current_user, extra=extra)


@app.get("/admin/webhooks", response_class=HTMLResponse)
async def admin_webhooks(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    events = await webhook_events_repo.list_events(limit=100)
    prepared_events: list[dict[str, Any]] = []
    for event in events:
        serialised_event = _serialise_mapping(event)
        serialised_event["created_iso"] = _to_iso(event.get("created_at"))
        serialised_event["updated_iso"] = _to_iso(event.get("updated_at"))
        serialised_event["next_attempt_iso"] = _to_iso(event.get("next_attempt_at"))
        prepared_events.append(serialised_event)
    extra = {
        "title": "Webhook delivery queue",
        "events": prepared_events,
    }
    return await _render_template("admin/webhooks.html", request, current_user, extra=extra)

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
    forms = await forms_repo.list_forms()
    extra = {
        "title": "Forms admin",
        "forms": forms,
        "opnform_base_url": _opnform_base_url(),
    }
    return await _render_template("admin/forms.html", request, current_user, extra=extra)


@app.post("/myforms/admin")
async def admin_create_form(
    request: Request,
    name: str = Form(...),
    url: str = Form(...),
    description: str = Form(""),
):
    _, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    cleaned_name = name.strip()
    if not cleaned_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Form name cannot be empty")
    try:
        normalized_url = normalize_opnform_form_url(url, allowed_host=OPNFORM_ALLOWED_HOST)
    except OpnformValidationError as exc:
        log_error("Invalid OpnForm URL supplied", error=str(exc))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    await forms_repo.create_form(
        name=cleaned_name,
        url=normalized_url,
        embed_code=None,
        description=description.strip() or None,
    )
    return RedirectResponse(url="/admin/forms", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/myforms/admin/edit")
async def admin_edit_form(
    request: Request,
    id: str = Form(...),
    name: str = Form(...),
    url: str = Form(...),
    description: str = Form(""),
):
    _, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    try:
        form_id = int(id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid form identifier")
    existing = await forms_repo.get_form(form_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form not found")
    cleaned_name = name.strip()
    if not cleaned_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Form name cannot be empty")
    try:
        normalized_url = normalize_opnform_form_url(url, allowed_host=OPNFORM_ALLOWED_HOST)
    except OpnformValidationError as exc:
        log_error("Invalid OpnForm URL during update", form_id=form_id, error=str(exc))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    await forms_repo.update_form(
        form_id,
        name=cleaned_name,
        url=normalized_url,
        embed_code=existing.get("embed_code"),
        description=description.strip() or None,
    )
    return RedirectResponse(url="/admin/forms", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/myforms/admin/delete")
async def admin_delete_form(
    request: Request,
    id: str = Form(...),
):
    _, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    try:
        form_id = int(id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid form identifier")
    existing = await forms_repo.get_form(form_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form not found")
    await forms_repo.delete_form(form_id)
    return RedirectResponse(url="/admin/forms", status_code=status.HTTP_303_SEE_OTHER)


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


@app.post(
    "/shop/admin/product/import",
    status_code=status.HTTP_303_SEE_OTHER,
    summary="Import a shop product from the stock feed",
    tags=["Shop"],
)
async def admin_import_shop_product(
    request: Request,
    vendor_sku: str = Form(...),
):
    """Import a single product by vendor SKU using the stock feed."""

    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    cleaned_vendor_sku = vendor_sku.strip()
    if not cleaned_vendor_sku:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vendor SKU cannot be empty",
        )

    await products_service.import_product_by_vendor_sku(cleaned_vendor_sku)

    return RedirectResponse(url="/admin/shop", status_code=status.HTTP_303_SEE_OTHER)


@app.post(
    "/shop/admin/product",
    status_code=status.HTTP_303_SEE_OTHER,
    summary="Create a shop product",
    tags=["Shop"],
)
async def admin_create_shop_product(
    request: Request,
    name: str = Form(...),
    sku: str = Form(...),
    vendor_sku: str = Form(...),
    price: str = Form(...),
    stock: str = Form(...),
    vip_price: str | None = Form(default=None),
    category_id: str | None = Form(default=None),
    image: UploadFile | None = File(default=None),
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    cleaned_name = name.strip()
    if not cleaned_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Product name cannot be empty")

    cleaned_sku = sku.strip()
    if not cleaned_sku:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="SKU cannot be empty")

    cleaned_vendor_sku = vendor_sku.strip()
    if not cleaned_vendor_sku:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Vendor SKU cannot be empty")

    try:
        price_decimal = Decimal(price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (TypeError, InvalidOperation):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Price must be a valid number")
    if price_decimal < 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Price must be at least zero")

    try:
        stock_int = int(stock)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Stock must be a whole number")
    if stock_int < 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Stock must be at least zero")

    vip_decimal: Decimal | None = None
    if vip_price not in (None, ""):
        try:
            vip_decimal = Decimal(vip_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except (TypeError, InvalidOperation):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="VIP price must be a valid number")
        if vip_decimal < 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="VIP price must be at least zero")

    category_value: int | None = None
    if category_id:
        try:
            category_value = int(category_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid category selection")
        category = await shop_repo.get_category(category_value)
        if not category:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Selected category does not exist")

    image_url: str | None = None
    stored_path: Path | None = None
    if image is not None:
        if image.filename:
            image_url, stored_path = await store_product_image(
                upload=image,
                uploads_root=_private_uploads_path,
                max_size=5 * 1024 * 1024,
            )
        else:
            await image.close()

    try:
        product = await shop_repo.create_product(
            name=cleaned_name,
            sku=cleaned_sku,
            vendor_sku=cleaned_vendor_sku,
            price=price_decimal,
            stock=stock_int,
            vip_price=vip_decimal,
            category_id=category_value,
            image_url=image_url,
        )
    except aiomysql.IntegrityError as exc:
        if stored_path:
            stored_path.unlink(missing_ok=True)
        if exc.args and exc.args[0] == 1062:
            detail = "A product with that SKU or vendor SKU already exists."
        else:
            detail = "Unable to create product."
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc
    except Exception:
        if stored_path:
            stored_path.unlink(missing_ok=True)
        raise

    log_info(
        "Shop product created",
        product_id=product["id"],
        sku=product["sku"],
        vendor_sku=product["vendor_sku"],
        created_by=current_user["id"] if current_user else None,
    )
    return RedirectResponse(url="/admin/shop", status_code=status.HTTP_303_SEE_OTHER)


@app.post(
    "/shop/admin/product/{product_id}/delete",
    status_code=status.HTTP_303_SEE_OTHER,
    summary="Delete a shop product",
    tags=["Shop"],
)
async def admin_delete_shop_product(request: Request, product_id: int):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    product = await shop_repo.get_product_by_id(product_id)
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    deleted = await shop_repo.delete_product(product_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    image_url = product.get("image_url")
    if image_url:
        try:
            delete_stored_file(image_url, _private_uploads_path)
        except HTTPException as exc:
            log_error(
                "Failed to remove deleted product image",
                product_id=product_id,
                error=str(exc),
            )
        except OSError as exc:
            log_error(
                "Failed to remove deleted product image",
                product_id=product_id,
                error=str(exc),
            )

    log_info(
        "Shop product deleted",
        product_id=product_id,
        deleted_by=current_user.get("id") if current_user else None,
    )
    return RedirectResponse(url="/admin/shop", status_code=status.HTTP_303_SEE_OTHER)


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
