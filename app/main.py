from __future__ import annotations

import asyncio
import json
import math
import secrets
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, time, timedelta, timezone
from ipaddress import ip_network
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from html import escape
from pathlib import Path, PurePosixPath
from typing import Any, cast
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
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.params import Form as FormField
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.openapi.docs import get_swagger_ui_html
from itsdangerous import BadSignature, URLSafeSerializer
from pydantic import ValidationError
from starlette.datastructures import FormData, URL

from app.api.routes import (
    agent,
    api_keys,
    asset_custom_fields,
    audit_logs,
    auth,
    automations as automations_api,
    bc5,
    bc11,
    bcp,
    business_continuity_plans as bc_plans_api,
    call_recordings as call_recordings_api,
    companies,
    essential8 as essential8_api,
    forms as forms_api,
    invoices as invoices_api,
    issues as issues_api,
    knowledge_base as knowledge_base_api,
    licenses as licenses_api,
    memberships,
    m365 as m365_api,
    message_templates as message_templates_api,
    mcp as mcp_api,
    imap as imap_api,
    modules as modules_api,
    notifications,
    orders as orders_api,
    ports,
    scheduler as scheduler_api,
    roles,
    staff as staff_api,
    subscriptions as subscriptions_api,
    tag_exclusions,
    tickets as tickets_api,
    users,
    system,
    uptimekuma,
    xero,
)
from uuid import uuid4

from app.core.config import get_settings, get_templates_config
from app.core.database import db
from app.core.logging import configure_logging, log_error, log_info
from loguru import logger
from app.repositories import audit_logs as audit_repo
from app.repositories import api_keys as api_key_repo
from app.repositories import auth as auth_repo
from app.repositories import assets as assets_repo
from app.repositories import billing_contacts as billing_contacts_repo
from app.repositories import business_continuity_plans as bc_plans_repo
from app.repositories import companies as company_repo
from app.repositories import company_memberships as membership_repo
from app.repositories import company_recurring_invoice_items as recurring_items_repo
from app.repositories import change_log as change_log_repo
from app.repositories import assets as asset_repo
from app.repositories import invoices as invoice_repo
from app.repositories import licenses as license_repo
from app.repositories import forms as forms_repo
from app.repositories import knowledge_base as knowledge_base_repo
from app.repositories import m365 as m365_repo
from app.core.notifications import DEFAULT_NOTIFICATION_EVENT_TYPES, merge_event_types
from app.repositories import notifications as notifications_repo
from app.repositories import notification_preferences as notification_preferences_repo
from app.repositories import roles as role_repo
from app.repositories import shop as shop_repo
from app.repositories import cart as cart_repo
from app.repositories import scheduled_tasks as scheduled_tasks_repo
from app.repositories import subscription_categories as subscription_categories_repo
from app.repositories import subscriptions as subscriptions_repo
from app.repositories import staff as staff_repo
from app.repositories import pending_staff_access as pending_staff_access_repo
from app.repositories import tickets as tickets_repo
from app.repositories import ticket_views as ticket_views_repo
from app.repositories import automations as automation_repo
from app.repositories import integration_modules as integration_modules_repo
from app.repositories import webhook_events as webhook_events_repo
from app.repositories import user_companies as user_company_repo
from app.repositories import users as user_repo
from app.repositories import issues as issues_repo
from app.schemas.api_keys import ALLOWED_API_KEY_HTTP_METHODS
from app.schemas.tickets import SyncroTicketImportRequest
from app.security.cache_control import CacheControlMiddleware
from app.security.csrf import CSRFMiddleware
from app.security.encryption import encrypt_secret
from app.security.rate_limiter import (
    EndpointRateLimiter,
    EndpointRateLimiterMiddleware,
    RateLimiterMiddleware,
    SimpleRateLimiter,
)
from app.security.request_logger import RequestLoggingMiddleware
from app.security.security_headers import SecurityHeadersMiddleware
from app.security.session import SessionData, session_manager
from app.api.dependencies.auth import get_current_session
from app.services.scheduler import scheduler_service
from app.security.api_keys import mask_api_key
from app.services import audit as audit_service
from app.services import background as background_tasks
from app.services import automations as automations_service
from app.services import change_log as change_log_service
from app.services import company_domains
from app.services import company_access
from app.services import email as email_service
from app.services import imap as imap_service
from app.services import knowledge_base as knowledge_base_service
from app.services import m365 as m365_service
from app.services import modules as modules_service
from app.services import notification_event_settings as event_settings_service
from app.services import message_templates as message_templates_service
from app.services import products as products_service
from app.services import shop as shop_service
from app.services import shop_packages as shop_packages_service
from app.services import staff_importer
from app.services import staff_access as staff_access_service
from app.services import company_importer
from app.services import labour_types as labour_types_service
from app.services import subscription_shop_integration
from app.services import ticket_importer
from app.services import tickets as tickets_service
from app.services import template_variables
from app.services import webhook_monitor
from app.services import xero as xero_service
from app.services import issues as issues_service
from app.services import impersonation as impersonation_service
from app.services.realtime import refresh_notifier
from app.services.sanitization import sanitize_rich_text
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
xero_oauth_state_serializer = URLSafeSerializer(settings.secret_key, salt="xero-oauth")
PWA_THEME_COLOR = "#0f172a"
PWA_BACKGROUND_COLOR = "#0f172a"
SHOP_LOW_STOCK_THRESHOLD = 5
_PWA_SERVICE_WORKER_PATH = templates_config.static_path / "service-worker.js"
_PWA_ICON_SOURCES = [
    {
        "src": "/static/logo.svg",
        "sizes": "192x192",
        "type": "image/svg+xml",
        "purpose": "any",
    },
    {
        "src": "/static/logo.svg",
        "sizes": "512x512",
        "type": "image/svg+xml",
        "purpose": "any",
    },
    {
        "src": "/static/logo.svg",
        "sizes": "any",
        "type": "image/svg+xml",
        "purpose": "any maskable",
    },
]
OPNFORM_ALLOWED_HOST = extract_allowed_host(
    str(settings.opnform_base_url) if settings.opnform_base_url else None
)


def _opnform_base_url() -> str | None:
    if settings.opnform_base_url:
        base = str(settings.opnform_base_url)
        return base if base.endswith("/") else f"{base}/"
    return "/myforms/"


def _build_xero_redirect_uri() -> str:
    """Build Xero OAuth redirect URI using PORTAL_URL setting.
    
    This ensures the redirect_uri matches what's configured in the Xero OAuth app,
    preventing 'Invalid redirect_uri' errors when the app is behind a proxy or
    using a different hostname than the incoming request.
    """
    if settings.portal_url:
        base = str(settings.portal_url).rstrip("/")
        return f"{base}/xero/callback"
    # Fallback to relative path if PORTAL_URL not set
    # This maintains backward compatibility but may cause issues
    return "/xero/callback"


def _serialise_for_json(value: Any) -> Any:
    """Convert mappings and sequences to JSON-safe primitives for templates."""

    if isinstance(value, datetime):
        target = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return target.astimezone(timezone.utc).isoformat()
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {key: _serialise_for_json(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_serialise_for_json(item) for item in value]
    return value
tags_metadata = [
    {
        "name": "API Keys",
        "description": "Super-admin management of API credentials with usage telemetry.",
    },
    {
        "name": "Assets",
        "description": "Device inventory, warranty status, and Syncro asset synchronisation endpoints.",
    },
    {"name": "Audit Logs", "description": "Structured audit trail of privileged actions."},
    {"name": "Auth", "description": "Authentication, registration, and session management."},
    {
        "name": "Automations",
        "description": "Workflow automations combining scheduling, event triggers, and module actions.",
    },
    {
        "name": "Business Continuity (BC5)",
        "description": "Comprehensive BC planning API with templates, plans, versions, workflows, attachments, and exports. Implements RBAC with viewer, editor, approver, and admin roles.",
    },
    {
        "name": "ChatGPT MCP",
        "description": "Expose secure Model Context Protocol tooling for ChatGPT ticket triage and updates.",
    },
    {"name": "Companies", "description": "Company catalogue and membership management."},
    {
        "name": "Forms",
        "description": "OpnForm publishing, company assignments, and secure embedding endpoints.",
    },
    {
        "name": "Integration Modules",
        "description": "Manage external module credentials for Ollama, SMTP, TacticalRMM, ntfy, and ChatGPT MCP.",
    },
    {"name": "Invoices", "description": "Invoice catalogue, status tracking, and reconciliation APIs."},
    {
        "name": "Knowledge Base",
        "description": "Permission-scoped articles with Ollama-assisted semantic search.",
    },
    {
        "name": "Agent",
        "description": "AI-assisted portal agent powered by the Ollama module with permission-aware context.",
    },
    {
        "name": "Licenses",
        "description": "Software license catalogue, assignments, and ordering workflows.",
    },
    {
        "name": "Memberships",
        "description": "Company membership workflows with approval tracking.",
    },
    {
        "name": "Message Templates",
        "description": "Reusable email and message bodies for automations and integrations.",
    },
    {"name": "Notifications", "description": "System-wide and user-specific notification feeds."},
    {"name": "Office365", "description": "Microsoft 365 credential management and synchronisation APIs."},
    {"name": "Ports", "description": "Port catalogue, document storage, and pricing workflow APIs."},
    {"name": "Roles", "description": "Role definitions and access controls."},
    {"name": "Shop", "description": "Product catalogue management and visibility controls."},
    {
        "name": "Shop Packages",
        "description": "Pre-built bundles of products designed to simplify repeat ordering workflows.",
    },
    {
        "name": "Staff",
        "description": "Staff directory management, Syncro contact synchronisation, and verification workflows.",
    },
    {
        "name": "System",
        "description": "Administrative system controls and realtime refresh notifications.",
    },
    {
        "name": "Tickets",
        "description": "Ticketing workspace with replies, watchers, and module-aligned categorisation.",
    },
    {
        "name": "Users",
        "description": "User administration, profile management, and self-service endpoints.",
    },
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


@app.on_event("startup")
async def _load_message_template_cache() -> None:
    try:
        await message_templates_service.refresh_cache()
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.error("Failed to preload message templates", error=str(exc))

SWAGGER_UI_PATH = settings.swagger_ui_url or "/docs"
PROTECTED_OPENAPI_PATH = "/internal/openapi.json"

app.add_middleware(
    CORSMiddleware,
    allow_origins=[str(origin) for origin in settings.allowed_origins] or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add security headers middleware
app.add_middleware(
    SecurityHeadersMiddleware,
    exempt_paths=("/static",),
)

# Add request logging middleware
app.add_middleware(
    RequestLoggingMiddleware,
    exempt_paths=("/static", "/health", "/manifest.webmanifest", "/service-worker.js"),
)

# Configure endpoint-specific rate limits per security requirements
endpoint_limiter = EndpointRateLimiter()

# Login: 5 attempts per 15 minutes per IP
endpoint_limiter.add_limit("/api/auth/login", "POST", limit=5, window_seconds=900)

# Password reset: 3 requests per hour per email
def _password_reset_key(request: Request) -> str:
    """Generate rate limit key based on email from form data."""
    try:
        # Try to get email from query params or form
        email = request.query_params.get("email")
        if not email:
            # For POST requests, we'd need to read the body but that's already
            # consumed by the time middleware runs. Use IP as fallback.
            client_ip = request.headers.get("x-forwarded-for")
            if client_ip:
                return client_ip.split(",")[0].strip()
            client = request.client
            return client.host if client else "anonymous"
        return f"reset:{email.lower()}"
    except Exception:
        # Fallback to IP-based limiting
        client_ip = request.headers.get("x-forwarded-for")
        if client_ip:
            return client_ip.split(",")[0].strip()
        client = request.client
        return client.host if client else "anonymous"

endpoint_limiter.add_limit("/api/auth/password/forgot", "POST", limit=3, window_seconds=3600, key_func=_password_reset_key)
endpoint_limiter.add_limit("/auth/password/forgot", "POST", limit=3, window_seconds=3600, key_func=_password_reset_key)

# File upload: 10 files per hour per user
def _user_upload_key(request: Request) -> str:
    """Generate rate limit key based on user ID from session."""
    from app.security.session import session_manager
    # Note: This is synchronous approximation - actual implementation
    # would need to be async. For now, use IP-based limiting.
    client_ip = request.headers.get("x-forwarded-for")
    if client_ip:
        return f"upload:{client_ip.split(',')[0].strip()}"
    client = request.client
    return f"upload:{client.host if client else 'anonymous'}"

# Apply to common upload endpoints
upload_paths = [
    "/api/tickets/attachments",
    "/api/shop/products/image",
    "/api/business-continuity/attachments",
]
for path in upload_paths:
    endpoint_limiter.add_limit(path, "POST", limit=10, window_seconds=3600, key_func=_user_upload_key)

# API calls: 100 requests per minute per user (applied via general rate limiter)

app.add_middleware(
    EndpointRateLimiterMiddleware,
    endpoint_limiter=endpoint_limiter,
    exempt_paths=(SWAGGER_UI_PATH, PROTECTED_OPENAPI_PATH, "/static"),
)

general_rate_limiter = SimpleRateLimiter(limit=100, window_seconds=60)
app.add_middleware(
    RateLimiterMiddleware,
    rate_limiter=general_rate_limiter,
    exempt_paths=(SWAGGER_UI_PATH, PROTECTED_OPENAPI_PATH, "/static", "/health"),
)

app.add_middleware(
    CacheControlMiddleware,
    exempt_paths=("/static",),
)

app.add_middleware(CSRFMiddleware)

templates = Jinja2Templates(directory=str(templates_config.template_path))

# Ensure document uploads remain web-accessible using the same paths as the
# previous portal stack.  Product images continue to live in the
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


@app.get("/manifest.webmanifest", include_in_schema=False)
async def pwa_manifest() -> JSONResponse:
    """Expose the Progressive Web App manifest for installable clients."""

    manifest = {
        "name": settings.app_name,
        "short_name": settings.app_name[:30],
        "id": "/",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "display_override": ["standalone", "minimal-ui"],
        "background_color": PWA_BACKGROUND_COLOR,
        "theme_color": PWA_THEME_COLOR,
        "description": f"{settings.app_name} portal with offline support.",
        "lang": "en",
        "dir": "ltr",
        "icons": _PWA_ICON_SOURCES,
        "shortcuts": [
            {
                "name": "Dashboard",
                "url": "/",
                "icons": [_PWA_ICON_SOURCES[0]],
            }
        ],
        "categories": ["productivity", "business"],
    }
    response = JSONResponse(manifest, media_type="application/manifest+json")
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@app.get("/service-worker.js", include_in_schema=False)
async def pwa_service_worker() -> FileResponse:
    """Serve the static service worker with strict caching headers."""

    if not _PWA_SERVICE_WORKER_PATH.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    response = FileResponse(
        _PWA_SERVICE_WORKER_PATH,
        media_type="application/javascript",
        filename="service-worker.js",
    )
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Service-Worker-Allowed"] = "/"
    return response



app.mount("/static", StaticFiles(directory=str(templates_config.static_path)), name="static")


@app.websocket("/ws/refresh")
async def refresh_updates(websocket: WebSocket) -> None:
    """Maintain a websocket connection for realtime refresh notifications."""

    await refresh_notifier.connect(websocket)
    try:
        while True:
            # Keep the connection open and consume incoming messages so we
            # detect client disconnects promptly.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await refresh_notifier.disconnect(websocket)


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
app.include_router(agent.router)
app.include_router(users.router)
app.include_router(call_recordings_api.router)
app.include_router(companies.router)
app.include_router(essential8_api.router)
app.include_router(licenses_api.router)
app.include_router(forms_api.router)
app.include_router(knowledge_base_api.router)
app.include_router(bc_plans_api.router)
app.include_router(bc5.router)
app.include_router(bc11.router)
app.include_router(bcp.router)
app.include_router(roles.router)
app.include_router(memberships.router)
app.include_router(m365_api.router)
app.include_router(message_templates_api.router)
app.include_router(ports.router)
app.include_router(notifications.router)
app.include_router(orders_api.router)
app.include_router(staff_api.router)
app.include_router(invoices_api.router)
app.include_router(issues_api.router)
app.include_router(subscriptions_api.router)
app.include_router(audit_logs.router)
app.include_router(api_keys.router)
app.include_router(scheduler_api.router)
app.include_router(tickets_api.router)
app.include_router(automations_api.router)
app.include_router(modules_api.router)
app.include_router(imap_api.router)
app.include_router(mcp_api.router)
app.include_router(system.router)
app.include_router(uptimekuma.router)
app.include_router(xero.router)
app.include_router(asset_custom_fields.router)
app.include_router(tag_exclusions.router)

HELPDESK_PERMISSION_KEY = tickets_service.HELPDESK_PERMISSION_KEY
ISSUE_TRACKER_PERMISSION_KEY = issues_service.ISSUE_TRACKER_PERMISSION_KEY



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


async def _is_helpdesk_technician(user: Mapping[str, Any], request: Request | None = None) -> bool:
    if user.get("is_super_admin"):
        if request is not None:
            request.state.is_helpdesk_technician = True
        return True
    if request is not None:
        cached = getattr(request.state, "is_helpdesk_technician", None)
        if cached is not None:
            return bool(cached)
    user_id = user.get("id")
    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError):
        result = False
    else:
        try:
            result = await membership_repo.user_has_permission(
                user_id_int, HELPDESK_PERMISSION_KEY
            )
            if not result:
                result = await membership_repo.user_has_permission(
                    user_id_int, "helpdesk.technician"
                )
        except Exception as exc:  # pragma: no cover - defensive fallback for tests without DB
            log_error("Failed to determine helpdesk technician role", error=str(exc))
            result = False
    if request is not None:
        request.state.is_helpdesk_technician = bool(result)
    return bool(result)


async def _has_issue_tracker_access(user: Mapping[str, Any], request: Request | None = None) -> bool:
    if user.get("is_super_admin"):
        if request is not None:
            request.state.has_issue_tracker_access = True
        return True
    if request is not None:
        cached = getattr(request.state, "has_issue_tracker_access", None)
        if cached is not None:
            return bool(cached)
    user_id = user.get("id")
    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError):
        result = False
    else:
        try:
            result = await membership_repo.user_has_permission(
                user_id_int, ISSUE_TRACKER_PERMISSION_KEY
            )
        except Exception as exc:  # pragma: no cover - defensive fallback for tests without DB
            log_error("Failed to determine issue tracker access", error=str(exc))
            result = False
        if not result:
            try:
                assignments = await user_company_repo.list_companies_for_user(user_id_int)
            except Exception as exc:  # pragma: no cover - defensive fallback for tests without DB
                log_error(
                    "Failed to evaluate direct issue tracker access",
                    error=str(exc),
                )
                assignments = []
            result = any(bool(assignment.get("can_manage_issues")) for assignment in assignments)
    if request is not None:
        request.state.has_issue_tracker_access = bool(result)
    return bool(result)


async def _require_helpdesk_page(request: Request) -> tuple[dict[str, Any] | None, RedirectResponse | None]:
    user, redirect = await _require_authenticated_user(request)
    if redirect:
        return None, redirect
    has_access = await _is_helpdesk_technician(user, request)
    if not has_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Helpdesk technician privileges required",
        )
    return user, None


async def _require_issue_tracker_access(
    request: Request,
) -> tuple[dict[str, Any] | None, RedirectResponse | None]:
    user, redirect = await _require_authenticated_user(request)
    if redirect:
        return None, redirect
    has_access = await _has_issue_tracker_access(user, request)
    if not has_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Issue tracker access required",
        )
    return user, None


async def _require_administration_access(
    request: Request,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, RedirectResponse | None]:
    user, redirect = await _require_authenticated_user(request)
    if redirect:
        return None, None, redirect

    membership = getattr(request.state, "active_membership", None)
    if membership is None:
        active_company_id = getattr(request.state, "active_company_id", None)
        if active_company_id is not None:
            try:
                membership = await user_company_repo.get_user_company(user["id"], int(active_company_id))
            except Exception:  # pragma: no cover - defensive protection against membership lookup failures
                membership = None
            request.state.active_membership = membership

    is_company_admin = bool(membership and membership.get("is_admin"))
    if not (user.get("is_super_admin") or is_company_admin):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator access required")

    return user, membership, None


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


def _prepare_notification_metadata(metadata: Any) -> list[dict[str, str]]:
    if metadata is None:
        return []

    serialised = _serialise_for_json(metadata)

    if isinstance(serialised, Mapping):
        items: list[dict[str, str]] = []
        for key in sorted(serialised.keys(), key=lambda item: str(item)):
            value = serialised[key]
            if isinstance(value, Mapping) or (
                isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray))
            ):
                value_text = json.dumps(value, ensure_ascii=False)
            else:
                value_text = "" if value is None else str(value)
            items.append({"key": str(key), "value": value_text})
        return items

    if isinstance(serialised, Iterable) and not isinstance(serialised, (str, bytes, bytearray)):
        value_text = json.dumps(serialised, ensure_ascii=False)
        return [{"key": "items", "value": value_text}]

    return [{"key": "value", "value": "" if serialised is None else str(serialised)}]


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


def _parse_int_in_range(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError, AttributeError):
        return default
    if parsed < minimum:
        return minimum
    if parsed > maximum:
        return maximum
    return parsed


def _validate_subscription_commitment_and_payment(
    subscription_category_id: int | None,
    commitment_type: str | None,
    payment_frequency: str | None,
) -> tuple[str | None, str | None]:
    """Validate subscription commitment type and payment frequency.
    
    Returns:
        Tuple of (commitment_value, payment_frequency_value)
        
    Raises:
        HTTPException: If validation fails
    """
    if not subscription_category_id:
        return None, None
        
    if not commitment_type or commitment_type not in ("monthly", "annual"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Commitment type must be 'monthly' or 'annual' for subscription products"
        )
    
    if not payment_frequency or payment_frequency not in ("monthly", "annual"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payment frequency must be 'monthly' or 'annual' for subscription products"
        )
    
    # Validate business rule: Monthly commitment can only have monthly payment
    if commitment_type == "monthly" and payment_frequency != "monthly":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Monthly commitment can only have monthly payment"
        )
    
    return commitment_type, payment_frequency


def _parse_staff_selection(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered.startswith("staff:"):
        candidate = text.split(":", 1)[1].strip()
    elif lowered.startswith("s-"):
        candidate = text[2:].strip()
    else:
        return None
    try:
        return int(candidate)
    except ValueError:
        return None


def _request_prefers_json(request: Request) -> bool:
    accept = (request.headers.get("accept") or "").lower()
    if "application/json" in accept:
        return True
    requested_with = (request.headers.get("x-requested-with") or "").lower()
    if requested_with == "xmlhttprequest":
        return True
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        return True
    return False


async def _resolve_initial_company_id(user: dict[str, Any]) -> int | None:
    return await company_access.first_accessible_company_id(user)


async def _build_base_context(
    request: Request,
    user: dict[str, Any],
    *,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    session = await session_manager.load_session(request)
    impersonator_user = None
    impersonation_started_at = None
    is_impersonating = False
    if session and session.impersonator_user_id is not None:
        is_impersonating = True
        impersonation_started_at = session.impersonation_started_at
        cached_impersonator = getattr(request.state, "impersonator_profile", None)
        if cached_impersonator and int(cached_impersonator.get("id", 0)) == session.impersonator_user_id:
            impersonator_user = cached_impersonator
        else:
            try:
                impersonator_user = await user_repo.get_user_by_id(session.impersonator_user_id)
            except Exception as exc:  # pragma: no cover - defensive logging
                log_error("Failed to load impersonator user context", error=str(exc))
                impersonator_user = None
            else:
                request.state.impersonator_profile = impersonator_user
    available_companies = getattr(request.state, "available_companies", None)
    if available_companies is None:
        available_companies = await company_access.list_accessible_companies(user)
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

    membership_data = membership or {}
    is_super_admin = bool(user.get("is_super_admin"))
    staff_permission_level = int(membership_data.get("staff_permission") or 0)
    is_helpdesk_technician = await _is_helpdesk_technician(user, request)
    has_issue_tracker_access = await _has_issue_tracker_access(user, request)

    def _has_permission(flag: str) -> bool:
        return bool(membership_data.get(flag))
    
    # Check BCP permissions
    can_view_bcp = is_super_admin
    can_edit_bcp = is_super_admin
    if not is_super_admin and active_company_id is not None:
        user_id = user.get("id")
        if user_id:
            try:
                can_view_bcp = await membership_repo.user_has_permission(int(user_id), "bcp:view")
                can_edit_bcp = await membership_repo.user_has_permission(int(user_id), "bcp:edit")
            except Exception as exc:  # pragma: no cover - defensive fallback
                log_error("Failed to check BCP permissions", error=str(exc))
                can_view_bcp = False
                can_edit_bcp = False

    permission_flags = {
        "can_access_shop": is_super_admin or _has_permission("can_access_shop"),
        "can_access_cart": is_super_admin or _has_permission("can_access_cart"),
        "can_access_orders": is_super_admin or _has_permission("can_access_orders"),
        "can_access_forms": is_super_admin or _has_permission("can_access_forms"),
        "can_manage_assets": is_super_admin or _has_permission("can_manage_assets"),
        "can_manage_licenses": is_super_admin or _has_permission("can_manage_licenses"),
        "can_manage_invoices": is_super_admin or _has_permission("can_manage_invoices"),
        "can_manage_staff": (
            is_super_admin
            or _has_permission("can_manage_staff")
            or staff_permission_level > 0
        ),
        "can_manage_issues": has_issue_tracker_access,
        "can_view_compliance": is_super_admin or (active_company_id is not None),
        "can_view_bcp": can_view_bcp,
        "can_edit_bcp": can_edit_bcp,
    }

    module_lookup = getattr(request.state, "module_lookup", None)
    if module_lookup is None:
        try:
            module_list = await modules_service.list_modules()
        except Exception as exc:  # pragma: no cover - defensive logging
            log_error("Failed to load integration modules for context", error=str(exc))
            module_list = []
        module_lookup = {module.get("slug"): module for module in module_list if module.get("slug")}
        request.state.module_lookup = module_lookup

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
        "staff_permission": staff_permission_level,
        "is_super_admin": is_super_admin,
        "is_helpdesk_technician": is_helpdesk_technician,
        "is_company_admin": is_super_admin or bool(membership_data.get("is_admin")),
        "integration_modules": module_lookup,
        "syncro_module_enabled": bool((module_lookup or {}).get("syncro", {}).get("enabled")),
        "enable_auto_refresh": bool(settings.enable_auto_refresh),
        "is_impersonating": is_impersonating,
        "impersonator_user": impersonator_user,
        "impersonation_started_at": impersonation_started_at,
        "has_issue_tracker_access": has_issue_tracker_access,
        "can_access_tickets": True,
    }
    context.update(permission_flags)
    if extra:
        context.update(extra)

    cart_summary = {"item_count": 0, "total_quantity": 0, "subtotal": Decimal("0")}
    if session:
        try:
            cart_summary = await cart_repo.summarise_cart(session.id)
        except Exception as exc:  # pragma: no cover - defensive logging
            log_error("Failed to summarise cart", error=str(exc))
    context["cart_summary"] = cart_summary
    if "notification_unread_count" not in context:
        unread_count = 0
        user_id = user.get("id")
        if user_id is not None:
            try:
                unread_count = await notifications_repo.count_notifications(
                    user_id=int(user_id),
                    read_state="unread",
                )
            except Exception as exc:  # pragma: no cover - defensive logging
                log_error("Failed to count unread notifications", error=str(exc))
        context["notification_unread_count"] = unread_count
    return context


async def _load_syncro_module() -> dict[str, Any] | None:
    try:
        return await modules_service.get_module("syncro", redact=False)
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to load Syncro module configuration", error=str(exc))
        return None


def _describe_syncro_module(module: dict[str, Any] | None) -> dict[str, Any]:
    settings_payload = (module or {}).get("settings") or {}
    base_url = str(settings_payload.get("base_url") or "").strip()
    api_key_present = bool(str(settings_payload.get("api_key") or "").strip())
    env_base_url = str(settings.syncro_webhook_url or "").strip()
    env_api_key = str(settings.syncro_api_key or "").strip()
    effective_base_url = (base_url or env_base_url).rstrip("/")
    rate_limit = _parse_int_in_range(
        settings_payload.get("rate_limit_per_minute"),
        default=180,
        minimum=1,
        maximum=600,
    )
    return {
        "enabled": bool(module and module.get("enabled")),
        "base_url": base_url,
        "effective_base_url": effective_base_url,
        "has_api_key": api_key_present or bool(env_api_key),
        "rate_limit_per_minute": rate_limit,
    }


async def _build_public_context(
    request: Request,
    *,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context: dict[str, Any] = {
        "request": request,
        "app_name": settings.app_name,
        "current_year": datetime.utcnow().year,
        "current_user": None,
        "available_companies": [],
        "active_company": None,
        "active_company_id": None,
        "active_membership": None,
        "csrf_token": None,
        "staff_permission": 0,
        "is_super_admin": False,
        "is_helpdesk_technician": False,
        "is_company_admin": False,
        "can_access_shop": False,
        "can_access_cart": False,
        "can_access_orders": False,
        "can_access_forms": False,
        "can_manage_assets": False,
        "can_manage_licenses": False,
        "can_manage_invoices": False,
        "can_manage_staff": False,
        "can_view_compliance": False,
        "cart_summary": {"item_count": 0, "total_quantity": 0, "subtotal": Decimal("0")},
        "notification_unread_count": 0,
        "enable_auto_refresh": bool(settings.enable_auto_refresh),
    }
    if extra:
        context.update(extra)
    return context


async def _build_portal_context(
    request: Request,
    user: dict[str, Any] | None,
    *,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if user:
        return await _build_base_context(request, user, extra=extra)
    return await _build_public_context(request, extra=extra)


async def _get_optional_user(
    request: Request,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    session = await session_manager.load_session(request)
    if not session:
        return None, None
    request.state.session = session
    user = await user_repo.get_user_by_id(session.user_id)
    if not user:
        return None, None
    request.state.active_company_id = session.active_company_id
    membership = None
    if session.active_company_id is not None:
        try:
            membership = await user_company_repo.get_user_company(user["id"], int(session.active_company_id))
        except Exception:  # pragma: no cover - defensive
            membership = None
        if membership is not None:
            request.state.active_membership = membership
    return user, membership


async def _build_consolidated_overview(
    request: Request, user: dict[str, Any]
) -> dict[str, Any]:
    session = await session_manager.load_session(request)
    available_companies = getattr(request.state, "available_companies", None)
    if available_companies is None:
        available_companies = await company_access.list_accessible_companies(user)
        request.state.available_companies = available_companies

    active_company_id = getattr(request.state, "active_company_id", None)
    if active_company_id is None and session:
        active_company_id = session.active_company_id
        request.state.active_company_id = active_company_id

    is_super_admin = bool(user.get("is_super_admin"))

    def _format_int(value: int | None) -> str:
        if value is None:
            return "0"
        return f"{int(value):,}"

    def _format_currency(amount: Decimal | None) -> str:
        if amount is None:
            amount = Decimal("0")
        if not isinstance(amount, Decimal):
            try:
                amount = Decimal(str(amount))
            except (InvalidOperation, ValueError):
                amount = Decimal("0")
        try:
            quantized = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except (InvalidOperation, ValueError):
            quantized = Decimal("0.00")
        return f"${quantized:,.2f}"

    cards: list[dict[str, Any]] = []

    total_companies = len(available_companies)
    total_users: int | None = None
    if is_super_admin:
        total_companies = await company_repo.count_companies()
        total_users = await user_repo.count_users()

    cards.append(
        {
            "label": "Companies" if is_super_admin else "My companies",
            "value": total_companies,
            "formatted": _format_int(total_companies),
            "description": (
                "Organisations across the portal"
                if is_super_admin
                else "Companies you can access"
            ),
        }
    )

    if total_users is not None:
        cards.append(
            {
                "label": "Portal users",
                "value": total_users,
                "formatted": _format_int(total_users),
                "description": "Registered accounts",
            }
        )

    unread_notifications = 0
    user_id = user.get("id")
    if user_id is not None:
        try:
            unread_notifications = await notifications_repo.count_notifications(
                user_id=int(user_id),
                read_state="unread",
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            log_error("Failed to load consolidated notifications count", error=str(exc))

    cards.append(
        {
            "label": "Unread alerts",
            "value": unread_notifications,
            "formatted": _format_int(unread_notifications),
            "description": "Notifications awaiting review",
        }
    )

    pending_webhooks = await webhook_events_repo.count_events_by_status("pending")
    failing_webhooks = await webhook_events_repo.count_events_by_status("failed")
    in_progress_webhooks = await webhook_events_repo.count_events_by_status("in_progress")

    webhook_notes: list[str] = []
    if failing_webhooks:
        webhook_notes.append(f"{_format_int(failing_webhooks)} failing")
    if in_progress_webhooks:
        webhook_notes.append(f"{_format_int(in_progress_webhooks)} in progress")
    if not webhook_notes and pending_webhooks:
        webhook_notes.append("Queued for retry")
    if not webhook_notes:
        webhook_notes.append("All clear")

    cards.append(
        {
            "label": "Webhook queue",
            "value": pending_webhooks,
            "formatted": _format_int(pending_webhooks),
            "description": ", ".join(webhook_notes),
        }
    )

    company_snapshot: dict[str, Any] | None = None
    if active_company_id:
        assets = await asset_repo.list_company_assets(active_company_id)
        asset_status_counts = Counter(
            (str(asset.get("status") or "Unspecified").strip() or "Unspecified")
            for asset in assets
        )

        staff_total = await staff_repo.count_staff(active_company_id)
        staff_enabled = await staff_repo.count_staff(active_company_id, enabled=True)

        licenses = await license_repo.list_company_licenses(active_company_id)
        invoices = await invoice_repo.list_company_invoices(active_company_id)

        def _safe_int(value: Any) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return 0

        def _is_countable(license_record: Mapping[str, Any]) -> bool:
            return _safe_int(license_record.get("count")) < 10000

        counted_licenses = [lic for lic in licenses if _is_countable(lic)]
        excluded_licenses = [lic for lic in licenses if not _is_countable(lic)]

        license_capacity = sum(_safe_int(lic.get("count")) for lic in counted_licenses)
        license_allocated = sum(_safe_int(lic.get("allocated")) for lic in counted_licenses)
        excluded_allocated = sum(_safe_int(lic.get("allocated")) for lic in excluded_licenses)
        license_available = max(license_capacity - license_allocated, 0)
        license_utilisation = (
            round((license_allocated / license_capacity) * 100)
            if license_capacity
            else 0
        )

        license_meta_parts = [f"{_format_int(license_allocated)} allocated"]
        if excluded_licenses:
            excluded_count = len(excluded_licenses)
            excluded_label = "license" if excluded_count == 1 else "licenses"
            note = f"Excludes {excluded_count} high-capacity {excluded_label}"
            if excluded_allocated:
                note += f" ({_format_int(excluded_allocated)} assigned)"
            license_meta_parts.append(note)
        license_meta = "; ".join(license_meta_parts)

        invoice_status_counts = Counter(
            (str(invoice.get("status") or "Unspecified").strip() or "Unspecified")
            for invoice in invoices
        )
        today = datetime.now(timezone.utc).date()
        open_amount = Decimal("0")
        overdue_count = 0
        for invoice in invoices:
            amount = invoice.get("amount")
            status = str(invoice.get("status") or "").strip().lower()
            if isinstance(amount, Decimal) and status not in {"paid", "void", "cancelled"}:
                open_amount += amount
            due_date = invoice.get("due_date")
            if (
                due_date
                and status not in {"paid", "void", "cancelled"}
                and due_date < today
            ):
                overdue_count += 1

        company_name = None
        for company in available_companies:
            if company.get("company_id") == active_company_id:
                company_name = company.get("company_name") or (
                    f"Company #{active_company_id}"
                )
                break
        if not company_name:
            company_name = f"Company #{active_company_id}"

        def _format_status_items(counter: Counter[str]) -> list[dict[str, Any]]:
            items: list[dict[str, Any]] = []
            for label, count in counter.most_common():
                if not label:
                    label = "Unspecified"
                display_label = label.title() if label.islower() else label
                items.append(
                    {
                        "label": display_label,
                        "value": count,
                        "formatted": _format_int(count),
                    }
                )
            if not items:
                items.append(
                    {
                        "label": "No records",
                        "value": 0,
                        "formatted": "0",
                    }
                )
            return items

        company_snapshot = {
            "name": company_name,
            "metrics": [
                {
                    "label": "Staff",
                    "value": staff_total,
                    "formatted": _format_int(staff_total),
                    "meta": (
                        f"{_format_int(staff_enabled)} active"
                        if staff_enabled
                        else "No active staff"
                    ),
                },
                {
                    "label": "Assets",
                    "value": len(assets),
                    "formatted": _format_int(len(assets)),
                    "meta": (
                        f"{_format_int(asset_status_counts.get('In use', 0))} in use"
                        if assets
                        else "No assets yet"
                    ),
                },
                {
                    "label": "Licenses",
                    "value": license_capacity,
                    "formatted": _format_int(license_capacity),
                    "meta": license_meta,
                },
            ],
            "asset_status": _format_status_items(asset_status_counts),
            "invoice_status": _format_status_items(invoice_status_counts),
            "licenses": {
                "total": license_capacity,
                "allocated": license_allocated,
                "available": license_available,
                "utilisation": license_utilisation,
                "formatted": {
                    "total": _format_int(license_capacity),
                    "allocated": _format_int(license_allocated),
                    "available": _format_int(license_available),
                    "utilisation": f"{license_utilisation}%",
                },
            },
            "financial": {
                "open_amount": open_amount,
                "open_formatted": _format_currency(open_amount),
                "overdue": overdue_count,
                "overdue_formatted": _format_int(overdue_count),
            },
        }

    return {
        "cards": cards,
        "webhooks": {
            "pending": pending_webhooks,
            "failed": failing_webhooks,
            "in_progress": in_progress_webhooks,
            "formatted": {
                "pending": _format_int(pending_webhooks),
                "failed": _format_int(failing_webhooks),
                "in_progress": _format_int(in_progress_webhooks),
            },
        },
        "company": company_snapshot,
        "unread_notifications": unread_notifications,
    }


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

_NOTIFICATION_SORT_CHOICES: list[tuple[str, str]] = [
    ("created_at", "Created date"),
    ("event_type", "Event type"),
    ("read_at", "Read date"),
]

_NOTIFICATION_ORDER_CHOICES: list[tuple[str, str]] = [
    ("desc", "Newest first"),
    ("asc", "Oldest first"),
]

_NOTIFICATION_READ_OPTIONS: list[tuple[str, str]] = [
    ("all", "All notifications"),
    ("unread", "Unread only"),
    ("read", "Read only"),
]

_NOTIFICATION_PAGE_SIZES: list[int] = [10, 25, 50, 100]

_MESSAGE_TEMPLATE_CONTENT_TYPES: tuple[tuple[str, str], ...] = (
    ("text/plain", "Plain text"),
    ("text/html", "HTML"),
)

_ASSET_TABLE_COLUMNS: list[dict[str, str]] = [
    {"key": "name", "label": "Name", "sort": "string", "priority": "essential"},
    {"key": "type", "label": "Type", "sort": "string"},
    {"key": "serial_number", "label": "Serial number", "sort": "string"},
    {"key": "status", "label": "Status", "sort": "string", "priority": "essential"},
    {"key": "os_name", "label": "OS name", "sort": "string"},
    {"key": "cpu_name", "label": "CPU", "sort": "string"},
    {"key": "ram_gb", "label": "RAM (GB)", "sort": "number"},
    {"key": "hdd_size", "label": "Storage", "sort": "string"},
    {"key": "last_sync", "label": "Last sync", "sort": "date", "priority": "essential"},
    {"key": "motherboard_manufacturer", "label": "Motherboard", "sort": "string"},
    {"key": "form_factor", "label": "Form factor", "sort": "string"},
    {"key": "last_user", "label": "Last user", "sort": "string", "priority": "essential"},
    {"key": "approx_age", "label": "Approx age", "sort": "number"},
    {"key": "performance_score", "label": "Performance score", "sort": "number"},
    {"key": "warranty_status", "label": "Warranty status", "sort": "string"},
    {"key": "warranty_end_date", "label": "Warranty end", "sort": "date"},
]


_PORTAL_STATUS_BADGE_MAP: dict[str, str] = {
    "open": "badge--warning",
    "in_progress": "badge--warning",
    "pending": "badge--warning",
    "resolved": "badge--success",
    "closed": "badge--muted",
}


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


def _parse_permission_lines(value: str | None) -> tuple[list[dict[str, Any]], list[str]]:
    if value is None:
        return [], []
    entries: dict[str, set[str]] = {}
    errors: list[str] = []
    allowed_methods = ", ".join(sorted(ALLOWED_API_KEY_HTTP_METHODS))
    for index, raw_line in enumerate(str(value).splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        slash_index = line.find("/")
        if slash_index <= 0:
            errors.append(f"Line {index}: Enter values as 'METHOD /path'.")
            continue
        method_part = line[:slash_index].strip()
        path = line[slash_index:].strip()
        if not path.startswith("/"):
            errors.append(f"Line {index}: Paths must start with '/'.")
            continue
        raw_methods = [
            token.strip().upper()
            for token in method_part.replace(",", " ").split()
        ]
        methods = [token for token in raw_methods if token]
        if not methods:
            errors.append(f"Line {index}: Provide at least one HTTP method.")
            continue
        invalid = [token for token in methods if token not in ALLOWED_API_KEY_HTTP_METHODS]
        if invalid:
            errors.append(
                f"Line {index}: Unsupported method(s) {', '.join(invalid)}. Allowed methods: {allowed_methods}."
            )
            continue
        entries.setdefault(path, set()).update(methods)
    parsed = [
        {"path": path, "methods": sorted(methods)}
        for path, methods in sorted(entries.items(), key=lambda item: item[0])
    ]
    return parsed, errors


def _parse_ip_restriction_lines(value: str | None) -> tuple[list[str], list[str]]:
    if value is None:
        return [], []
    entries: set[str] = set()
    errors: list[str] = []
    for index, raw_line in enumerate(str(value).splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            network = ip_network(line, strict=False)
        except ValueError:
            errors.append(f"Line {index}: Enter a valid IP address or CIDR range.")
            continue
        entries.add(network.with_prefixlen)
    ordered = sorted(entries)
    return ordered, errors


def _format_api_key_permissions(
    permissions: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], str, str]:
    display_entries: list[dict[str, Any]] = []
    if permissions:
        for entry in permissions:
            path = str(entry.get("path", "")).strip()
            methods = sorted(
                {str(method).strip().upper() for method in entry.get("methods", []) if str(method).strip()}
            )
            if not path or not methods:
                continue
            display_entries.append({"path": path, "methods": methods})
    display_entries.sort(key=lambda item: item["path"])
    permissions_text = "\n".join(
        f"{', '.join(entry['methods'])} {entry['path']}" for entry in display_entries
    )
    if display_entries:
        summary_parts = [
            f"{', '.join(entry['methods'])} {entry['path']}" for entry in display_entries[:2]
        ]
        remaining = len(display_entries) - 2
        if remaining > 0:
            summary_parts.append(f"+{remaining} more")
        access_summary = ", ".join(summary_parts)
    else:
        access_summary = "All endpoints"
    return display_entries, permissions_text, access_summary


def _format_api_key_ip_restrictions(
    restrictions: list[dict[str, Any]] | list[str] | None,
) -> tuple[list[dict[str, str]], str, str]:
    display_entries: list[dict[str, str]] = []
    if restrictions:
        for entry in restrictions:
            if isinstance(entry, Mapping):
                raw_value = entry.get("cidr")
            else:
                raw_value = entry
            value = str(raw_value or "").strip()
            if not value:
                continue
            try:
                network = ip_network(value, strict=False)
            except ValueError:
                continue
            canonical = network.with_prefixlen
            if network.prefixlen == network.max_prefixlen:
                label = network.network_address.compressed
            else:
                label = canonical
            display_entries.append({"cidr": canonical, "label": label})
    display_entries.sort(key=lambda item: item["label"])
    text_value = "\n".join(item["label"] for item in display_entries)
    if display_entries:
        summary_parts = [item["label"] for item in display_entries[:2]]
        remaining = len(display_entries) - 2
        if remaining > 0:
            summary_parts.append(f"+{remaining} more")
        summary = ", ".join(summary_parts)
    else:
        summary = "Any IP address"
    return display_entries, text_value, summary


def _prepare_api_key_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    today = date.today()
    prepared: list[dict[str, Any]] = []
    active_count = 0
    disabled_count = 0
    expired_count = 0
    for row in rows:
        expiry = row.get("expiry_date")
        is_expired = bool(expiry and isinstance(expiry, date) and expiry < today)
        is_enabled = bool(row.get("is_enabled", True))
        if is_expired:
            expired_count += 1
        elif is_enabled:
            active_count += 1
        else:
            disabled_count += 1
        usage_entries: list[dict[str, Any]] = []
        for entry in row.get("usage", []) or []:
            usage_entries.append(
                {
                    "ip_address": entry.get("ip_address"),
                    "usage_count": entry.get("usage_count", 0),
                    "last_used_iso": _to_iso(entry.get("last_used_at")),
                }
            )
        display_permissions, permissions_text, endpoint_summary = _format_api_key_permissions(
            row.get("permissions")
        )
        display_ip_restrictions, ip_text, ip_summary = _format_api_key_ip_restrictions(
            row.get("ip_restrictions")
        )
        if endpoint_summary and ip_summary:
            access_summary = f"{endpoint_summary} • {ip_summary}"
        elif endpoint_summary:
            access_summary = endpoint_summary
        else:
            access_summary = ip_summary
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
                "permissions": display_permissions,
                "permissions_text": permissions_text,
                "ip_restrictions": display_ip_restrictions,
                "ip_restrictions_text": ip_text,
                "ip_summary": ip_summary,
                "endpoint_summary": endpoint_summary,
                "access_summary": access_summary,
                "is_restricted": bool(display_permissions or display_ip_restrictions),
                "is_enabled": is_enabled,
            }
        )
    stats = {
        "total": len(prepared),
        "active": active_count,
        "expired": expired_count,
        "disabled": disabled_count,
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
        "allowed_methods": sorted(ALLOWED_API_KEY_HTTP_METHODS),
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


async def _load_subscription_context(request: Request):
    """Load context for subscription-related pages.
    
    Requires user to have both can_manage_licenses AND can_access_cart permissions.
    """
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
    has_license = bool(membership and membership.get("can_manage_licenses"))
    has_cart = bool(membership and membership.get("can_access_cart"))
    
    # Require both licenses and cart permissions (or super admin)
    if not (is_super_admin or (has_license and has_cart)):
        return (
            user,
            membership,
            None,
            company_id,
            RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER),
        )
    
    company = await company_repo.get_company_by_id(company_id)
    return user, membership, company, company_id, None


async def _load_asset_context(
    request: Request,
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
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid company identifier") from exc

    membership = await user_company_repo.get_user_company(user["id"], company_id)
    can_manage_assets = bool(membership and membership.get("can_manage_assets"))
    if not (is_super_admin or can_manage_assets):
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


async def _load_company_section_context(
    request: Request,
    *,
    permission_field: str,
    allow_super_admin_without_company: bool = False,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None, int | None, RedirectResponse | None]:
    user, redirect = await _require_authenticated_user(request)
    if redirect:
        return user, None, None, None, redirect

    is_super_admin = bool(user and user.get("is_super_admin"))
    company_id_raw = user.get("company_id") if user else None
    if company_id_raw is None:
        if is_super_admin and allow_super_admin_without_company:
            return user, None, None, None, None
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No company associated with the current user",
        )
    try:
        company_id = int(company_id_raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid company identifier") from exc

    membership = await user_company_repo.get_user_company(user["id"], company_id)
    has_permission = bool(membership and membership.get(permission_field))
    if not (is_super_admin or has_permission):
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


def _company_edit_redirect(
    *,
    company_id: int,
    success: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    params: dict[str, str] = {}
    if success:
        params["success"] = success.strip()[:200]
    if error:
        params["error"] = error.strip()[:200]
    query = urlencode(params)
    url = f"/admin/companies/{company_id}/edit"
    if query:
        url = f"{url}?{query}"
    return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)


_COMPANY_PERMISSION_COLUMNS: list[dict[str, str]] = [
    {"field": "can_access_shop", "label": "Shop"},
    {"field": "can_access_cart", "label": "Cart"},
    {"field": "can_access_orders", "label": "Orders"},
    {"field": "can_access_forms", "label": "Forms"},
    {"field": "can_manage_assets", "label": "Assets"},
    {"field": "can_manage_licenses", "label": "Licenses"},
    {"field": "can_manage_invoices", "label": "Invoices"},
    {"field": "can_manage_office_groups", "label": "Office groups"},
    {"field": "can_manage_issues", "label": "Issue tracker"},
    {"field": "can_order_licenses", "label": "Order licenses"},
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


async def _validate_recommendation_product_ids(
    raw_ids: Sequence[int | str] | None,
    *,
    category_id: int | None,
    field_label: str,
    disallow_product_id: int | None = None,
) -> list[int]:
    values: list[int] = []
    for raw in raw_ids or []:
        if raw in (None, ""):
            continue
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid {field_label.lower()} selection submitted",
            )
        if value <= 0:
            continue
        values.append(value)

    unique_ids = sorted(set(values))
    if not unique_ids:
        return []

    if category_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_label} products require selecting a category first",
        )

    candidates = await shop_repo.list_products_by_ids(unique_ids, include_archived=False)
    found_ids = {int(candidate.get("id") or 0) for candidate in candidates}
    missing = [str(value) for value in unique_ids if value not in found_ids]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_label} selection is no longer available",
        )

    validated: list[int] = []
    for candidate in candidates:
        candidate_id = int(candidate.get("id") or 0)
        if disallow_product_id is not None and candidate_id == disallow_product_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{field_label} products cannot include the item being edited",
            )
        candidate_category = candidate.get("category_id")
        if candidate_category != category_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{field_label} products must match the selected category",
            )
        if bool(candidate.get("archived")):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{field_label} selection is archived and cannot be used",
            )
        validated.append(candidate_id)

    return sorted(validated)


async def _resolve_related_product_id_by_sku(sku: str | None) -> int | None:
    """Look up a related product identifier from a SKU value."""

    if sku in (None, ""):
        return None

    candidate = str(sku).strip()
    if not candidate:
        return None

    product = await shop_repo.get_product_by_sku(candidate, include_archived=True)
    if not product:
        return None

    try:
        product_id = int(product.get("id") or 0)
    except (TypeError, ValueError):
        return None

    return product_id or None


def _normalise_related_product_inputs(raw: Any) -> list[int | str]:
    """Normalise related product identifiers from mixed FastAPI form inputs."""

    if isinstance(raw, FormField):
        raw = raw.default

    if raw in (None, ""):
        return []

    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        return list(raw)

    return [raw]


async def _render_impersonation_dashboard(
    request: Request,
    user: dict[str, Any],
    *,
    error_message: str | None = None,
    success_message: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    try:
        candidates = await impersonation_service.list_impersonatable_users()
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to enumerate impersonation candidates", error=str(exc))
        candidates = []
        if error_message is None:
            error_message = "Unable to load impersonation candidates."
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR

    context = await _build_base_context(
        request,
        user,
        extra={
            "title": "User impersonation",
            "impersonation_candidates": candidates,
            "impersonation_error": error_message,
            "impersonation_success": success_message,
        },
    )
    return templates.TemplateResponse(
        "admin/impersonation.html",
        context,
        status_code=status_code,
    )


async def _get_company_management_scope(
    request: Request,
    user: dict[str, Any],
    include_archived: bool = False,
) -> tuple[bool, list[dict[str, Any]], dict[int, dict[str, Any]]]:
    is_super_admin = bool(user.get("is_super_admin"))
    if is_super_admin:
        companies = await company_repo.list_companies(include_archived=include_archived)
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
            # Filter archived companies for non-super admins unless explicitly requested
            if include_archived or not company.get("archived"):
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
    include_archived: bool = False,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    is_super_admin, managed_companies, membership_lookup = await _get_company_management_scope(
        request, user, include_archived=include_archived
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
        "show_archived": include_archived,
    }

    response = await _render_template("admin/companies.html", request, user, extra=extra)
    response.status_code = status_code
    return response


async def _render_company_edit_page(
    request: Request,
    user: dict[str, Any],
    *,
    company_id: int,
    form_values: Mapping[str, Any] | None = None,
    assign_form_values: Mapping[str, Any] | None = None,
    success_message: str | None = None,
    error_message: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    company_record = await company_repo.get_company_by_id(company_id)
    if not company_record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")

    is_super_admin, managed_companies, _ = await _get_company_management_scope(request, user)

    assignments: list[dict[str, Any]] = []
    role_options: list[dict[str, Any]] = []
    company_user_options: dict[int, list[dict[str, Any]]] = {}
    if is_super_admin:
        assignments = await user_company_repo.list_assignments(company_id)
        for entry in assignments:
            entry["is_pending"] = False
            entry["pending_requires_account"] = False

        role_rows = await role_repo.list_roles()
        role_lookup: dict[int, str] = {}
        for record in role_rows:
            role_id = record.get("id")
            name = (record.get("name") or "").strip()
            if role_id is None or not name:
                continue
            try:
                role_id_int = int(role_id)
            except (TypeError, ValueError):
                continue
            role_lookup[role_id_int] = name
            role_options.append(
                {
                    "id": role_id_int,
                    "name": name,
                    "description": (record.get("description") or "").strip(),
                    "is_system": bool(record.get("is_system")),
                }
            )

        staff_directory: dict[int, list[dict[str, Any]]] = {}
        pending_assignments_map: dict[int, list[dict[str, Any]]] = {}
        for managed in managed_companies:
            raw_id = managed.get("id")
            try:
                managed_company_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            staff_rows = await staff_repo.list_staff_with_users(managed_company_id)
            staff_directory[managed_company_id] = staff_rows
            pending_assignments = await pending_staff_access_repo.list_assignments_for_company(
                managed_company_id
            )
            pending_assignments_map[managed_company_id] = pending_assignments
            pending_lookup = {
                entry.get("staff_id"): entry
                for entry in pending_assignments
                if entry.get("staff_id") is not None
            }
            options: list[dict[str, Any]] = []
            for row in staff_rows:
                staff_id = row.get("staff_id")
                email = (row.get("email") or "").strip()
                if staff_id is None or not email:
                    continue
                first_name = (row.get("first_name") or "").strip()
                last_name = (row.get("last_name") or "").strip()
                name_parts = [part for part in (first_name, last_name) if part]
                has_name = bool(name_parts)
                label: str
                if has_name and email:
                    label = f"{' '.join(name_parts)} ({email})"
                elif has_name:
                    label = " ".join(name_parts)
                else:
                    label = email
                user_id_value = row.get("user_id")
                has_user = user_id_value is not None
                option_value: str
                if has_user:
                    try:
                        numeric_user_id = int(user_id_value)
                    except (TypeError, ValueError):
                        numeric_user_id = None
                else:
                    numeric_user_id = None
                if numeric_user_id is not None:
                    option_value = str(numeric_user_id)
                else:
                    option_value = f"staff:{int(staff_id)}"
                if not row.get("enabled", True):
                    label = f"{label} (inactive)"
                pending_assignment = pending_lookup.get(int(staff_id))
                if numeric_user_id is None:
                    if pending_assignment:
                        label = f"{label} – access pending sign-up"
                    else:
                        label = f"{label} – invite required"
                options.append(
                    {
                        "value": option_value,
                        "label": label,
                        "email": email,
                        "staff_id": int(staff_id),
                        "user_id": numeric_user_id,
                        "has_user": numeric_user_id is not None,
                        "pending_access": bool(pending_assignment),
                    }
                )
            options.sort(key=lambda item: item.get("label", "").lower())
            company_user_options[managed_company_id] = options

        permission_label_lookup: dict[int, str] = {}
        for option in _STAFF_PERMISSION_OPTIONS:
            value = option.get("value")
            label = option.get("label")
            if value is None or label is None:
                continue
            try:
                permission_label_lookup[int(value)] = str(label)
            except (TypeError, ValueError):
                continue

        pending_entries = pending_assignments_map.get(company_id)
        if pending_entries is None:
            pending_entries = await pending_staff_access_repo.list_assignments_for_company(
                company_id
            )
        staff_rows_current = staff_directory.get(company_id)
        if staff_rows_current is None:
            staff_rows_current = await staff_repo.list_staff_with_users(company_id)
        staff_lookup: dict[int, dict[str, Any]] = {}
        for staff_entry in staff_rows_current:
            staff_id = staff_entry.get("staff_id")
            if staff_id is None:
                continue
            try:
                staff_lookup[int(staff_id)] = staff_entry
            except (TypeError, ValueError):
                continue

        existing_user_ids: set[int] = set()
        for assignment in assignments:
            user_id = assignment.get("user_id")
            if user_id is None:
                continue
            try:
                existing_user_ids.add(int(user_id))
            except (TypeError, ValueError):
                continue

        for pending_entry in pending_entries or []:
            staff_id_raw = pending_entry.get("staff_id")
            if staff_id_raw is None:
                continue
            try:
                staff_id_int = int(staff_id_raw)
            except (TypeError, ValueError):
                continue

            staff_info = staff_lookup.get(staff_id_int, {})
            user_id_value: int | None = None
            if staff_info.get("user_id") is not None:
                try:
                    user_id_value = int(staff_info.get("user_id"))
                except (TypeError, ValueError):
                    user_id_value = None
            if user_id_value is not None and user_id_value in existing_user_ids:
                continue

            email = (staff_info.get("email") or "").strip()
            if not email:
                email = f"Staff #{staff_id_int}"
            first_name = (staff_info.get("first_name") or "").strip()
            last_name = (staff_info.get("last_name") or "").strip()

            role_id_raw = pending_entry.get("role_id")
            role_id_value: int | None = None
            if role_id_raw is not None:
                try:
                    role_id_value = int(role_id_raw)
                except (TypeError, ValueError):
                    role_id_value = None

            try:
                staff_permission_value = int(pending_entry.get("staff_permission") or 0)
            except (TypeError, ValueError):
                staff_permission_value = 0

            pending_record: dict[str, Any] = {
                "company_id": pending_entry.get("company_id") or company_id,
                "user_id": user_id_value,
                "staff_id": staff_id_int,
                "email": email,
                "first_name": first_name,
                "last_name": last_name,
                "membership_id": None,
                "membership_role_id": role_id_value,
                "membership_role_name": role_lookup.get(role_id_value) if role_id_value is not None else None,
                "staff_permission": staff_permission_value,
                "staff_permission_label": permission_label_lookup.get(
                    staff_permission_value, permission_label_lookup.get(0, "No staff access")
                ),
                "can_manage_staff": bool(pending_entry.get("can_manage_staff", False)),
                "is_pending": True,
                "pending_requires_account": user_id_value is None,
            }

            for column in _COMPANY_PERMISSION_COLUMNS:
                field = column.get("field")
                if not field:
                    continue
                pending_record[field] = bool(pending_entry.get(field, False))

            assignments.append(pending_record)

        assignments.sort(
            key=lambda item: (
                (item.get("email") or "").lower(),
                1 if item.get("is_pending") else 0,
                item.get("user_id") or 0,
            )
        )

    def _string_value(key: str, default: str) -> str:
        if not form_values or key not in form_values:
            return default
        value = form_values.get(key)
        return str(value) if value is not None else ""

    def _bool_value(key: str, default: bool) -> bool:
        if not form_values or key not in form_values:
            return default
        value = form_values.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    default_email_domains = ", ".join(company_record.get("email_domains") or [])
    form_data = {
        "name": _string_value("name", (company_record.get("name") or "").strip()),
        "syncro_company_id": _string_value(
            "syncro_company_id", (company_record.get("syncro_company_id") or "").strip()
        ),
        "tacticalrmm_client_id": _string_value(
            "tacticalrmm_client_id",
            (company_record.get("tacticalrmm_client_id") or "").strip(),
        ),
        "xero_id": _string_value("xero_id", (company_record.get("xero_id") or "").strip()),
        "email_domains": _string_value("email_domains", default_email_domains),
        "is_vip": _bool_value("is_vip", bool(company_record.get("is_vip"))),
    }

    form_email_text = form_data.get("email_domains", "")
    if form_values and "email_domains" in form_values:
        preview_domains = [
            domain.strip()
            for domain in form_email_text.replace("\n", ",").split(",")
            if domain.strip()
        ]
    else:
        preview_domains = list(company_record.get("email_domains") or [])

    assign_values = assign_form_values or {}

    def _assign_int(key: str, default: int | None = None) -> int | None:
        if key not in assign_values:
            return default
        value = assign_values.get(key)
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            try:
                return int(value)
            except (TypeError, ValueError):
                return default
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return default
            try:
                return int(text)
            except ValueError:
                return default
        return default

    def _assign_bool(key: str, default: bool = False) -> bool:
        if key not in assign_values:
            return default
        value = assign_values.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            candidate = value.strip().lower()
            if not candidate:
                return False
            return candidate in {"1", "true", "yes", "on"}
        return default

    assign_company_id = _assign_int("company_id", company_id) or company_id
    raw_assign_user_value: str = ""
    if "user_value" in assign_values:
        value = assign_values.get("user_value")
        raw_assign_user_value = str(value).strip() if value is not None else ""
    elif "user_id" in assign_values:
        value = assign_values.get("user_id")
        raw_assign_user_value = str(value).strip() if value is not None else ""
    assign_user_value = raw_assign_user_value
    assign_user_id: int | None = None
    if assign_user_value:
        try:
            assign_user_id = int(assign_user_value)
        except ValueError:
            assign_user_id = None
    assign_role_id = _assign_int("role_id")
    assign_staff_permission = _assign_int("staff_permission", 0) or 0
    if assign_staff_permission < 0:
        assign_staff_permission = 0
    if assign_staff_permission > 3:
        assign_staff_permission = 3
    assign_can_manage_staff = _assign_bool("can_manage_staff", False)
    assign_permissions: dict[str, bool] = {}
    for column in _COMPANY_PERMISSION_COLUMNS:
        field = column.get("field")
        if not field:
            continue
        assign_permissions[field] = _assign_bool(field, False)

    assign_user_options = company_user_options.get(assign_company_id, []) if is_super_admin else []

    company_automation_tasks: list[dict[str, Any]] = []
    automation_command_options: list[dict[str, str]] = []
    automation_company_options: list[dict[str, str]] = []

    if is_super_admin:
        automation_command_options = [
            {"value": "sync_staff", "label": "Sync staff directory"},
            {"value": "sync_o365", "label": "Sync Microsoft 365 licenses"},
            {"value": "sync_to_xero", "label": "Sync to Xero"},
            {"value": "sync_to_xero_auto_send", "label": "Sync to Xero (Auto Send)"},
            {"value": "create_scheduled_ticket", "label": "Create scheduled ticket"},
            {"value": "sync_recordings", "label": "Sync call recordings"},
            {"value": "queue_transcriptions", "label": "Queue transcriptions"},
            {"value": "process_transcription", "label": "Process transcription"},
        ]
        default_command_values = {option["value"] for option in automation_command_options}

        try:
            tasks = await scheduled_tasks_repo.list_tasks()
        except Exception:  # pragma: no cover - fallback to keep page rendering
            tasks = []

        existing_commands: set[str] = set()
        for task in tasks:
            command_value = task.get("command")
            if command_value:
                existing_commands.add(str(command_value))

            raw_company_id = task.get("company_id")
            try:
                task_company_id = int(raw_company_id) if raw_company_id is not None else None
            except (TypeError, ValueError):
                task_company_id = None

            if task_company_id != company_id:
                continue

            serialised_task = _serialise_mapping(task)
            serialised_task["last_run_iso"] = _to_iso(task.get("last_run_at"))
            serialised_task["company_name"] = (company_record.get("name") or "").strip() or f"Company #{company_id}"
            company_automation_tasks.append(serialised_task)

        for command in sorted(existing_commands):
            if command and command not in default_command_values:
                automation_command_options.append({"value": command, "label": command})

        automation_company_options = [
            {
                "value": str(company_id),
                "label": (company_record.get("name") or "").strip() or f"Company #{company_id}",
            }
        ]

        company_automation_tasks.sort(key=lambda item: (item.get("name") or "").lower())

    # Fetch recurring invoice items for the company
    recurring_invoice_items = []
    if is_super_admin:
        try:
            items = await recurring_items_repo.list_company_recurring_invoice_items(
                company_id
            )
        except RuntimeError as exc:  # pragma: no cover - defensive guard for tests
            if "Database pool not initialised" in str(exc):
                items = []
            else:
                raise
        for item in items:
            recurring_invoice_items.append(_serialise_mapping(item))

    # Fetch billing contacts for the company
    billing_contacts = []
    company_staff = []
    if is_super_admin:
        try:
            billing_contacts = await billing_contacts_repo.list_billing_contacts_for_company(
                company_id
            )
        except RuntimeError as exc:  # pragma: no cover - defensive guard for tests
            if "Database pool not initialised" in str(exc):
                billing_contacts = []
            else:
                raise
        # Get all staff for this company for the dropdown
        try:
            company_staff = await staff_repo.list_staff(company_id)
        except RuntimeError as exc:  # pragma: no cover - defensive guard for tests
            if "Database pool not initialised" in str(exc):
                company_staff = []
            else:
                raise

    assign_form = {
        "company_id": assign_company_id,
        "user_id": assign_user_id,
        "user_value": assign_user_value,
        "role_id": assign_role_id,
        "staff_permission": assign_staff_permission,
        "can_manage_staff": assign_can_manage_staff,
        "permissions": assign_permissions,
    }

    extra = {
        "title": f"Edit {company_record.get('name') or 'company'}",
        "company": company_record,
        "form_data": form_data,
        "managed_companies": managed_companies,
        "is_super_admin": is_super_admin,
        "assignments": assignments,
        "permission_columns": _COMPANY_PERMISSION_COLUMNS,
        "staff_permission_options": _STAFF_PERMISSION_OPTIONS,
        "role_options": role_options,
        "success_message": success_message,
        "error_message": error_message,
        "email_domain_preview": preview_domains,
        "assign_form": assign_form,
        "company_user_options": company_user_options,
        "assign_user_options": assign_user_options,
        "company_automation_tasks": company_automation_tasks,
        "automation_command_options": automation_command_options,
        "automation_company_options": automation_company_options,
        "recurring_invoice_items": recurring_invoice_items,
        "billing_contacts": billing_contacts,
        "company_staff": company_staff,
    }

    response = await _render_template("admin/company_edit.html", request, user, extra=extra)
    response.status_code = status_code
    return response


@app.on_event("startup")
async def on_startup() -> None:
    try:
        await scheduler_service.run_system_update()
    except Exception as exc:
        log_error("Startup system update failed", error=str(exc))
    await db.connect()
    await db.run_migrations()
    async def _bootstrap_default_bcp_template() -> None:
        from app.services.bcp_template import bootstrap_default_template

        await bootstrap_default_template()

    startup_tasks = [
        ("sync_change_log_sources", change_log_service.sync_change_log_sources()),
        ("ensure_default_modules", modules_service.ensure_default_modules()),
        ("refresh_all_schedules", automations_service.refresh_all_schedules()),
        ("bootstrap_default_bcp_template", _bootstrap_default_bcp_template()),
    ]

    results = await asyncio.gather(
        *(task for _, task in startup_tasks), return_exceptions=True
    )

    for (name, _), result in zip(startup_tasks, results):
        if isinstance(result, Exception):
            if name == "bootstrap_default_bcp_template":
                log_error(
                    "Failed to bootstrap default BCP template", error=str(result)
                )
            else:
                log_error(
                    "Startup task failed", task=name, error=str(result)
                )
        elif name == "bootstrap_default_bcp_template":
            log_info("BCP default template bootstrapped")

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
    overview = await _build_consolidated_overview(request, user)
    
    # Check if Ollama module is enabled for the Agent section
    ollama_module = await modules_service.get_module("ollama", redact=False)
    ollama_enabled = ollama_module.get("enabled", False) if ollama_module else False
    
    return await _render_template(
        "dashboard.html",
        request,
        user,
        extra={
            "title": "Consolidated overview",
            "overview": overview,
            "notification_unread_count": overview.get("unread_notifications", 0),
            "ollama_enabled": ollama_enabled,
        },
    )


@app.get("/assets", response_class=HTMLResponse, tags=["Assets"])
async def assets_page(request: Request):
    user, _membership, company, company_id, redirect = await _load_asset_context(request)
    if redirect:
        return redirect

    rows = await asset_repo.list_company_assets(company_id)

    def _clean_text(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return str(value)
        text = str(value).strip()
        return text or None

    def _format_number(value: Any) -> tuple[str | None, str]:
        if value is None:
            return None, ""
        if isinstance(value, str) and not value.strip():
            return None, ""
        try:
            decimal_value = Decimal(str(value))
        except (InvalidOperation, ValueError):
            text = _clean_text(value)
            return text, text or ""
        display = format(decimal_value.normalize(), "f")
        if "." in display:
            display = display.rstrip("0").rstrip(".")
        return display or "0", str(decimal_value)

    def _parse_iso(value: str | None) -> datetime | None:
        if not value:
            return None
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            parsed = parsed.astimezone(timezone.utc)
        return parsed

    prepared: list[dict[str, Any]] = []
    today = datetime.now(timezone.utc).date()
    recent_threshold = datetime.now(timezone.utc) - timedelta(days=30)
    recent_sync = 0
    expired_warranty = 0
    active_warranty = 0

    for row in rows:
        name = _clean_text(row.get("name")) or "Asset"
        record: dict[str, Any] = {
            "id": row.get("id"),
            "name": name,
            "type": _clean_text(row.get("type")),
            "serial_number": _clean_text(row.get("serial_number")),
            "status": _clean_text(row.get("status")),
            "os_name": _clean_text(row.get("os_name")),
            "cpu_name": _clean_text(row.get("cpu_name")),
            "hdd_size": _clean_text(row.get("hdd_size")),
            "motherboard_manufacturer": _clean_text(row.get("motherboard_manufacturer")),
            "form_factor": _clean_text(row.get("form_factor")),
            "last_user": _clean_text(row.get("last_user")),
            "warranty_status": _clean_text(row.get("warranty_status")),
            "syncro_asset_id": _clean_text(row.get("syncro_asset_id")),
            "tactical_asset_id": _clean_text(row.get("tactical_asset_id")),
        }

        ram_display, ram_sort = _format_number(row.get("ram_gb"))
        approx_display, approx_sort = _format_number(row.get("approx_age"))
        performance_display, performance_sort = _format_number(row.get("performance_score"))
        record["ram_gb"] = ram_display
        record["ram_gb_sort"] = ram_sort
        record["approx_age"] = approx_display
        record["approx_age_sort"] = approx_sort
        record["performance_score"] = performance_display
        record["performance_score_sort"] = performance_sort

        last_sync_iso = _to_iso(row.get("last_sync"))
        record["last_sync"] = last_sync_iso
        record["last_sync_iso"] = last_sync_iso
        record["last_sync_sort"] = last_sync_iso or ""

        if last_sync_iso:
            parsed_last_sync = _parse_iso(last_sync_iso)
            if parsed_last_sync and parsed_last_sync >= recent_threshold:
                recent_sync += 1

        warranty_value = row.get("warranty_end_date")
        warranty_display: str | None
        warranty_sort = ""
        warranty_iso: str | None = None
        if isinstance(warranty_value, datetime):
            warranty_date = warranty_value.astimezone(timezone.utc).date()
            warranty_display = warranty_date.isoformat()
            warranty_iso = warranty_display
            warranty_sort = warranty_display
        elif isinstance(warranty_value, date):
            warranty_display = warranty_value.isoformat()
            warranty_iso = warranty_display
            warranty_sort = warranty_display
        else:
            warranty_display = _clean_text(warranty_value)
            if warranty_display:
                warranty_sort = warranty_display

        if warranty_iso:
            try:
                warranty_date_obj = date.fromisoformat(warranty_iso)
            except ValueError:
                warranty_date_obj = None
            if warranty_date_obj:
                if warranty_date_obj < today:
                    expired_warranty += 1
                else:
                    active_warranty += 1

        record["warranty_end_date"] = warranty_display
        record["warranty_end_sort"] = warranty_sort
        record["warranty_end_iso"] = warranty_iso

        prepared.append(record)

    stats = {
        "total": len(prepared),
        "recent_sync": recent_sync,
        "expired_warranty": expired_warranty,
        "active_warranty": active_warranty,
    }

    extra = {
        "title": "Assets",
        "assets": prepared,
        "columns": _ASSET_TABLE_COLUMNS,
        "company": company,
        "stats": stats,
        "has_assets": bool(prepared),
        "is_super_admin": bool(user.get("is_super_admin")),
    }
    return await _render_template("assets/index.html", request, user, extra=extra)


@app.get("/assets/settings", response_class=HTMLResponse, tags=["Assets"])
async def assets_settings_page(request: Request):
    user, _membership, _, _, redirect = await _load_asset_context(request)
    if redirect:
        return redirect
    
    # Only super admins can access settings
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin privileges required")
    
    extra = {
        "title": "Asset Custom Fields Settings",
        "is_super_admin": True,
    }
    return await _render_template("assets/settings.html", request, user, extra=extra)


@app.delete("/assets/{asset_id}", response_class=JSONResponse, tags=["Assets"])
async def delete_asset(request: Request, asset_id: int):
    user, _membership, _, company_id, redirect = await _load_asset_context(request)
    if redirect:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Asset management access denied")
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin privileges required")

    record = await asset_repo.get_asset_by_id(asset_id)
    if not record or int(record.get("company_id", 0) or 0) != company_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")

    await asset_repo.delete_asset(asset_id)
    log_info(
        "Asset deleted",
        asset_id=asset_id,
        company_id=company_id,
        user_id=user.get("id"),
    )
    return JSONResponse({"success": True})


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


@app.get("/compliance", response_class=HTMLResponse)
async def compliance_page(request: Request):
    """Essential 8 compliance tracking page."""
    from app.repositories import essential8 as essential8_repo
    
    user, membership, company, company_id, redirect = await _load_license_context(request, require_manage=False)
    if redirect:
        return redirect
    
    # Get compliance records
    compliance_records = await essential8_repo.list_company_compliance(company_id)
    
    # Get compliance summary
    summary = await essential8_repo.get_company_compliance_summary(company_id)
    
    is_super_admin = bool(user.get("is_super_admin"))
    
    extra = {
        "title": "Essential 8 Compliance",
        "compliance_records": compliance_records,
        "summary": summary,
        "company": company,
        "is_super_admin": is_super_admin,
    }
    return await _render_template("compliance/index.html", request, user, extra=extra)


@app.get("/compliance/control/{control_id}", response_class=HTMLResponse)
async def compliance_control_requirements_page(request: Request, control_id: int):
    """Essential 8 control requirements page."""
    from app.repositories import essential8 as essential8_repo
    
    user, membership, company, company_id, redirect = await _load_license_context(request, require_manage=False)
    if redirect:
        return redirect
    
    # Get control with requirements
    control_data = await essential8_repo.get_control_with_requirements(
        control_id=control_id,
        company_id=company_id,
    )
    
    if not control_data:
        raise HTTPException(status_code=404, detail="Control not found")
    
    # Build a map of requirement compliance for easier template access
    requirement_compliance_map = {}
    for rc in control_data.get("requirement_compliance", []):
        requirement_compliance_map[rc["requirement_id"]] = rc
    
    is_super_admin = bool(user.get("is_super_admin"))
    
    extra = {
        "title": f"{control_data['control']['name']} - Requirements",
        "control": control_data["control"],
        "requirements_ml1": control_data["requirements_ml1"],
        "requirements_ml2": control_data["requirements_ml2"],
        "requirements_ml3": control_data["requirements_ml3"],
        "company_compliance": control_data.get("company_compliance"),
        "requirement_compliance_map": requirement_compliance_map,
        "company": company,
        "is_super_admin": is_super_admin,
    }
    return await _render_template("compliance/control_requirements.html", request, user, extra=extra)


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


@app.get("/subscriptions", response_class=HTMLResponse)
async def subscriptions_page(request: Request):
    """Display active subscriptions for the current company."""
    user, membership, company, company_id, redirect = await _load_subscription_context(request)
    if redirect:
        return redirect
    
    # Fetch subscriptions for this company
    subscriptions = await subscriptions_repo.list_subscriptions(
        customer_id=company_id,
        limit=500,
    )
    
    # Format dates for display
    formatted: list[dict[str, Any]] = []
    for sub in subscriptions:
        formatted_sub = dict(sub)
        # Format dates
        if isinstance(sub.get("start_date"), datetime):
            formatted_sub["start_date"] = sub["start_date"].strftime("%Y-%m-%d")
        elif isinstance(sub.get("start_date"), date):
            formatted_sub["start_date"] = sub["start_date"].strftime("%Y-%m-%d")
        
        if isinstance(sub.get("end_date"), datetime):
            formatted_sub["end_date"] = sub["end_date"].strftime("%Y-%m-%d")
        elif isinstance(sub.get("end_date"), date):
            formatted_sub["end_date"] = sub["end_date"].strftime("%Y-%m-%d")
        
        # Get contract term from the subscription if available
        # For now, we'll leave it empty as the subscriptions table doesn't have this field
        formatted_sub["contract_term"] = ""
        
        formatted.append(formatted_sub)
    
    is_super_admin = bool(user.get("is_super_admin"))
    can_request_changes = bool(
        is_super_admin or (membership and membership.get("can_manage_licenses") and membership.get("can_access_cart"))
    )
    
    extra = {
        "title": "Subscriptions",
        "subscriptions": formatted,
        "company": company,
        "can_request_changes": can_request_changes,
    }
    return await _render_template("subscriptions/index.html", request, user, extra=extra)


@app.post("/subscriptions/{subscription_id}/request-change", response_class=JSONResponse)
async def request_subscription_change(request: Request, subscription_id: str):
    """Request a quantity change for a subscription."""
    user, membership, _, company_id, redirect = await _load_subscription_context(request)
    if redirect:
        return redirect
    
    # Verify subscription exists and belongs to this company
    subscription = await subscriptions_repo.get_subscription(subscription_id)
    if not subscription or int(subscription.get("customer_id", 0)) != company_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found")
    
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payload") from exc
    
    new_quantity = payload.get("quantity")
    if new_quantity is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Quantity is required")
    
    try:
        new_quantity = int(new_quantity)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Quantity must be a valid integer")
    
    if new_quantity < 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Quantity cannot be negative")
    
    # Check if the quantity is actually changing
    current_quantity = subscription.get("quantity", 0)
    if new_quantity == current_quantity:
        return JSONResponse({
            "success": False,
            "message": "No change requested - the new quantity is the same as the current quantity",
        })
    
    reason = payload.get("reason")
    
    # Log the change request for audit purposes
    log_info(
        "Subscription change requested",
        subscription_id=subscription_id,
        company_id=company_id,
        current_quantity=subscription.get("quantity"),
        requested_quantity=new_quantity,
        reason=reason,
        user_id=user.get("id"),
    )
    
    # Create a ticket for the subscription change request
    product_name = subscription.get("product_name") or "Unknown Product"
    
    # Build ticket subject
    subject = f"Subscription Change Request - {product_name}"
    
    # Build detailed description
    description_parts = [
        f"A subscription change has been requested.",
        "",
        "**Subscription Details:**",
        f"- Product: {product_name}",
        f"- Subscription ID: {subscription_id}",
        f"- Current Quantity: {current_quantity}",
        f"- Requested Quantity: {new_quantity}",
    ]
    
    if reason:
        description_parts.extend([
            "",
            "**Reason for Change:**",
            reason,
        ])
    
    description = "\n".join(description_parts)
    
    # Create the ticket against the requester
    user_id = user.get("id")
    try:
        ticket = await tickets_service.create_ticket(
            subject=subject,
            description=description,
            requester_id=user_id,
            company_id=company_id,
            assigned_user_id=None,
            priority="normal",
            status="open",
            category="subscription",
            module_slug=None,
            external_reference=f"subscription:{subscription_id}",
            trigger_automations=True,
        )
        
        ticket_id = ticket.get("id")
        ticket_number = ticket.get("ticket_number")
        
        log_info(
            "Ticket created for subscription change request",
            ticket_id=ticket_id,
            ticket_number=ticket_number,
            subscription_id=subscription_id,
        )
        
        return JSONResponse({
            "success": True,
            "message": "Change request submitted and ticket created",
            "ticket_id": ticket_id,
            "ticket_number": ticket_number,
        })
    except Exception as exc:
        log_error(
            "Failed to create ticket for subscription change request",
            subscription_id=subscription_id,
            error=str(exc),
        )
        # Still return success to the user, but log the error
        return JSONResponse({
            "success": True,
            "message": "Change request submitted",
        })


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

    companies = await company_access.list_accessible_companies(user)
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


@app.get("/xero/connect")
async def xero_connect(request: Request):
    """Initiate Xero OAuth2 authorization flow."""
    session_data = await session_manager.load_session(request)
    if not session_data:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    
    # Check if user is super admin
    user = await user_repo.get_user_by_id(session_data.user_id)
    if not user or not user.get("is_super_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only super administrators can configure Xero integration"
        )
    
    credentials = await modules_service.get_xero_credentials()
    if not credentials:
        return RedirectResponse(url="/admin/modules", status_code=status.HTTP_303_SEE_OTHER)
    
    client_id = credentials.get("client_id", "").strip()
    if not client_id:
        return RedirectResponse(url="/admin/modules?error=missing+client+id", status_code=status.HTTP_303_SEE_OTHER)
    
    # Generate state token to prevent CSRF
    state = xero_oauth_state_serializer.dumps({
        "user_id": session_data.user_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    
    # Build authorization URL using PORTAL_URL to ensure it matches Xero app settings
    redirect_uri = _build_xero_redirect_uri()
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "offline_access accounting.transactions accounting.contacts",
        "state": state,
    }
    authorize_url = f"https://login.xero.com/identity/connect/authorize?{urlencode(params)}"
    
    log_info("Initiating Xero OAuth flow", user_id=session_data.user_id, redirect_uri=redirect_uri)
    return RedirectResponse(url=authorize_url, status_code=status.HTTP_303_SEE_OTHER)


@app.get("/xero/callback", name="xero_callback")
async def xero_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    """Handle Xero OAuth2 callback and exchange code for tokens."""
    if error:
        # Sanitize error message to prevent URL redirection attacks
        message = request.query_params.get("error_description", error)
        # Only use alphanumeric, spaces, and basic punctuation
        safe_message = "".join(c if c.isalnum() or c in " .-_" else "" for c in str(message)[:200])
        encoded = urlencode({"error": safe_message or "authorization_error"})
        return RedirectResponse(url=f"/admin/modules?{encoded}", status_code=status.HTTP_303_SEE_OTHER)
    
    if not code or not state:
        return RedirectResponse(
            url="/admin/modules?error=invalid+response",
            status_code=status.HTTP_303_SEE_OTHER
        )
    
    # Verify state token
    try:
        state_data = xero_oauth_state_serializer.loads(state)
    except BadSignature:
        return RedirectResponse(
            url="/admin/modules?error=invalid+state",
            status_code=status.HTTP_303_SEE_OTHER
        )
    
    credentials = await modules_service.get_xero_credentials()
    if not credentials:
        return RedirectResponse(
            url="/admin/modules?error=missing+credentials",
            status_code=status.HTTP_303_SEE_OTHER
        )
    
    client_id = credentials.get("client_id", "").strip()
    client_secret = credentials.get("client_secret", "").strip()
    
    if not (client_id and client_secret):
        return RedirectResponse(
            url="/admin/modules?error=incomplete+credentials",
            status_code=status.HTTP_303_SEE_OTHER
        )
    
    # Exchange authorization code for tokens
    token_url = "https://identity.xero.com/connect/token"
    redirect_uri = _build_xero_redirect_uri()
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                token_url,
                data=data,
                auth=(client_id, client_secret),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        log_error(
            "Xero authorization failed",
            status=exc.response.status_code if exc.response else None,
            body=exc.response.text if exc.response else None,
        )
        return RedirectResponse(
            url="/admin/modules?error=authorization+failed",
            status_code=status.HTTP_303_SEE_OTHER
        )
    except Exception as exc:
        log_error("Xero authorization error", error=str(exc))
        return RedirectResponse(
            url="/admin/modules?error=connection+failed",
            status_code=status.HTTP_303_SEE_OTHER
        )
    
    payload = response.json()
    access_token = payload.get("access_token")
    refresh_token = payload.get("refresh_token")
    expires_in = payload.get("expires_in")
    
    if not (access_token and refresh_token):
        log_error("Xero token response missing tokens", payload_keys=list(payload.keys()))
        return RedirectResponse(
            url="/admin/modules?error=invalid+token+response",
            status_code=status.HTTP_303_SEE_OTHER
        )
    
    # Calculate token expiry
    expires_at = None
    if isinstance(expires_in, (int, float)):
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=float(expires_in))
    
    # Fetch tenant connections to get tenant_id
    tenant_id = None
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            connections_response = await client.get(
                "https://api.xero.com/connections",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
            )
        connections_response.raise_for_status()
        connections = connections_response.json()
        
        # Get the first tenant_id if we have connections
        if connections and len(connections) > 0:
            tenant_id = connections[0].get("tenantId")
            if tenant_id:
                log_info("Discovered Xero tenant_id", tenant_id=tenant_id)
    except Exception as exc:
        # Don't fail the entire flow if we can't fetch connections
        log_error("Failed to fetch Xero connections", error=str(exc))
    
    # Store tokens and tenant_id (tokens will be encrypted by update_xero_tokens)
    await modules_service.update_xero_tokens(
        access_token=access_token,
        refresh_token=refresh_token,
        token_expires_at=expires_at,
        tenant_id=tenant_id,
    )
    
    log_info("Xero OAuth callback processed successfully", user_id=state_data.get("user_id"))
    return RedirectResponse(url="/admin/modules?success=xero+authorized", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/shop", response_class=HTMLResponse)
async def shop_page(
    request: Request,
    category: str | None = Query(None),
    show_out_of_stock: bool = Query(False, alias="showOutOfStock"),
    q: str | None = None,
    cart_error: str | None = None,
):
    (
        user,
        _membership,
        company,
        company_id,
        redirect,
    ) = await _load_company_section_context(
        request,
        permission_field="can_access_shop",
    )
    if redirect:
        return redirect
    search_term = (q or "").strip()
    effective_search = search_term or None
    search_term_lower = effective_search.lower() if effective_search else None

    category_param = category.strip() if isinstance(category, str) and category.strip() else None
    show_packages = False
    category_id: int | None = None
    if category_param:
        if category_param.lower() == "packages":
            show_packages = True
        else:
            try:
                parsed_category = int(category_param)
            except (TypeError, ValueError):
                parsed_category = None
            if parsed_category is not None and parsed_category > 0:
                category_id = parsed_category

    is_vip = bool(company and int(company.get("is_vip") or 0) == 1)

    categories_task = asyncio.create_task(shop_repo.list_categories())

    products: list[dict[str, Any]]
    if show_packages:
        packages = await shop_packages_service.load_company_packages(
            company_id=company_id,
            is_vip=is_vip,
        )

        def _package_matches_search(package: Mapping[str, Any]) -> bool:
            if not search_term_lower:
                return True
            candidate_fields: list[str] = [
                str(package.get("name") or ""),
                str(package.get("sku") or ""),
            ]
            for item in package.get("items") or []:
                candidate_fields.append(str(item.get("product_name") or ""))
                candidate_fields.append(str(item.get("product_sku") or ""))
            return any(search_term_lower in field.lower() for field in candidate_fields if field)

        products = []
        for package in packages:
            if package.get("archived"):
                continue
            if package.get("is_restricted"):
                continue
            items = package.get("items") or []
            if not items:
                continue
            if not _package_matches_search(package):
                continue

            stock_level = int(package.get("stock_level") or 0)
            if not show_out_of_stock and stock_level <= 0:
                continue

            price_total = package.get("price_total")
            try:
                price_value = Decimal(str(price_total))
            except (InvalidOperation, TypeError, ValueError):
                continue
            if price_value <= 0:
                continue
            products.append(
                {
                    "id": package.get("id"),
                    "name": package.get("name"),
                    "sku": package.get("sku"),
                    "price": price_value,
                    "stock": stock_level,
                    "is_package": True,
                    "items": items,
                    "product_count": int(package.get("product_count") or 0),
                }
            )

        products.sort(key=lambda entry: str(entry.get("name") or "").lower())
    else:
        # Get category IDs to filter by (including descendants)
        category_ids = None
        if category_id is not None:
            category_ids = await shop_repo.get_category_descendants(category_id)
        
        filters = shop_repo.ProductFilters(
            include_archived=False,
            company_id=company_id,
            category_ids=category_ids,
            search_term=effective_search,
        )

        products_task = asyncio.create_task(shop_repo.list_products(filters))
        products = await products_task

        if not show_out_of_stock:
            products = [product for product in products if product.get("stock", 0) > 0]

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

    products = cast(list[dict[str, Any]], _serialise_for_json(products))
    if category_id is None:
        secrets.SystemRandom().shuffle(products)

    categories = await categories_task

    # Get active subscription product IDs for the customer
    active_subscription_product_ids = await subscriptions_repo.get_active_subscription_product_ids(company_id)

    extra = {
        "title": "Shop",
        "categories": categories,
        "products": products,
        "current_category": "packages" if show_packages else category_id,
        "show_packages": show_packages,
        "show_out_of_stock": show_out_of_stock,
        "search_term": search_term,
        "cart_error": cart_error,
        "low_stock_threshold": SHOP_LOW_STOCK_THRESHOLD,
        "active_subscription_product_ids": active_subscription_product_ids,
    }
    return await _render_template("shop/index.html", request, user, extra=extra)


@app.get(
    "/shop/packages",
    response_class=HTMLResponse,
    summary="View available product packages",
    tags=["Shop Packages"],
)
async def shop_packages_page(
    request: Request,
    cart_error: str | None = None,
):
    (
        user,
        _membership,
        company,
        company_id,
        redirect,
    ) = await _load_company_section_context(
        request,
        permission_field="can_access_shop",
    )
    if redirect:
        return redirect

    is_vip = bool(company and int(company.get("is_vip") or 0) == 1)
    packages = await shop_packages_service.load_company_packages(
        company_id=company_id,
        is_vip=is_vip,
    )

    packages_json = cast(list[dict[str, Any]], _serialise_for_json(packages))

    extra = {
        "title": "Shop packages",
        "packages": packages,
        "packages_json": packages_json,
        "cart_error": cart_error,
        "low_stock_threshold": SHOP_LOW_STOCK_THRESHOLD,
    }
    return await _render_template("shop/packages.html", request, user, extra=extra)


@app.post("/cart/add", response_class=RedirectResponse, include_in_schema=False)
async def add_to_cart(request: Request) -> RedirectResponse:
    (
        user,
        membership,
        company,
        company_id,
        redirect,
    ) = await _load_company_section_context(
        request,
        permission_field="can_access_cart",
    )
    if redirect:
        return redirect

    session = await session_manager.load_session(request)
    if not session:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    form = await request.form()
    product_id_raw = form.get("productId")
    quantity_raw = form.get("quantity")
    upgrade_from_values: Sequence[str] = []
    if isinstance(form, FormData):
        upgrade_from_values = form.getlist("upgradeFrom")
    else:
        upgrade_raw = form.get("upgradeFrom")
        if upgrade_raw is not None:
            upgrade_from_values = [upgrade_raw]
    upgrade_source_candidates: list[int] = []
    seen_upgrade_sources: set[int] = set()
    for raw_value in upgrade_from_values:
        try:
            resolved = int(str(raw_value))
        except (TypeError, ValueError):
            continue
        if resolved <= 0 or resolved in seen_upgrade_sources:
            continue
        upgrade_source_candidates.append(resolved)
        seen_upgrade_sources.add(resolved)

    try:
        product_id = int(product_id_raw)
    except (TypeError, ValueError):
        message = quote("Invalid product selection.")
        return RedirectResponse(
            url=f"{request.url_for('cart_page')}?cartError={message}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

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
        message = quote("Product is unavailable.")
        return RedirectResponse(
            url=f"{request.url_for('cart_page')}?cartError={message}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # Check if this is a subscription product that the user already owns
    subscription_category_id = product.get("subscription_category_id")
    if subscription_category_id is not None:
        active_subscription_product_ids = await subscriptions_repo.get_active_subscription_product_ids(company_id)
        if product_id in active_subscription_product_ids:
            message = quote("You already have an active subscription for this product. Please manage your subscription from the Subscriptions page.")
            return RedirectResponse(
                url=f"{request.url_for('shop_page')}?cart_error={message}",
                status_code=status.HTTP_303_SEE_OTHER,
            )

    existing = await cart_repo.get_item(session.id, product_id)
    existing_quantity = existing.get("quantity") if existing else 0

    selected_upgrade_source: int | None = None
    if upgrade_source_candidates:
        source_products = await shop_repo.list_products_by_ids(
            upgrade_source_candidates,
            company_id=company_id,
        )
        valid_upgrade_sources: dict[int, set[int]] = {}
        for source_product in source_products:
            try:
                source_id = int(source_product.get("id") or 0)
            except (TypeError, ValueError):
                continue
            if source_id <= 0:
                continue
            related_ids: set[int] = set()
            for related_id in source_product.get("upsell_product_ids") or []:
                try:
                    resolved_related = int(related_id)
                except (TypeError, ValueError):
                    continue
                if resolved_related > 0:
                    related_ids.add(resolved_related)
            if related_ids:
                valid_upgrade_sources[source_id] = related_ids

        if valid_upgrade_sources:
            for candidate in upgrade_source_candidates:
                related = valid_upgrade_sources.get(candidate)
                if not related or product_id not in related:
                    continue
                existing_source = await cart_repo.get_item(session.id, candidate)
                if not existing_source:
                    continue
                selected_upgrade_source = candidate
                break

    available_stock = int(product.get("stock") or 0)
    if available_stock <= 0 or existing_quantity + requested_quantity > available_stock:
        remaining = max(available_stock - existing_quantity, 0)
        message = quote(f"Cannot add item. Only {remaining} left in stock.")
        return RedirectResponse(
            url=f"{request.url_for('cart_page')}?cartError={message}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    is_vip = bool(company and int(company.get("is_vip") or 0) == 1)
    price_source = product.get("price")
    if is_vip and product.get("vip_price") is not None:
        price_source = product.get("vip_price")
    unit_price = Decimal(str(price_source or 0))
    new_quantity = existing_quantity + requested_quantity

    if selected_upgrade_source is not None:
        await cart_repo.remove_items(session.id, [selected_upgrade_source])

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

    cart_url = request.url_for("cart_page")
    if selected_upgrade_source is not None:
        cart_message = quote("Upgrade applied.")
    else:
        cart_message = quote("Item added to cart.")
    return RedirectResponse(
        url=f"{cart_url}?cartMessage={cart_message}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/cart/add-package", response_class=RedirectResponse, include_in_schema=False)
async def add_package_to_cart(request: Request) -> RedirectResponse:
    (
        user,
        _membership,
        company,
        company_id,
        redirect,
    ) = await _load_company_section_context(
        request,
        permission_field="can_access_cart",
    )
    if redirect:
        return redirect

    session = await session_manager.load_session(request)
    if not session:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    form = await request.form()
    package_id_raw = form.get("packageId")
    quantity_raw = form.get("quantity")

    try:
        package_id = int(package_id_raw)
    except (TypeError, ValueError):
        return RedirectResponse(url=request.url_for("shop_packages_page"), status_code=status.HTTP_303_SEE_OTHER)

    try:
        requested_quantity = int(quantity_raw) if quantity_raw is not None else 1
    except (TypeError, ValueError):
        requested_quantity = 1
    if requested_quantity <= 0:
        requested_quantity = 1

    is_vip = bool(company and int(company.get("is_vip") or 0) == 1)
    packages = await shop_packages_service.load_company_packages(
        company_id=company_id,
        is_vip=is_vip,
    )
    selected_package = next((pkg for pkg in packages if int(pkg.get("id") or 0) == package_id), None)
    if not selected_package or not selected_package.get("is_available"):
        message = quote("Selected package is not currently available.")
        return RedirectResponse(
            url=f"{request.url_for('shop_packages_page')}?cart_error={message}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    items = list(selected_package.get("items") or [])
    if not items:
        message = quote("Package does not contain any products.")
        return RedirectResponse(
            url=f"{request.url_for('shop_packages_page')}?cart_error={message}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # Get active subscription product IDs to check for subscription conflicts
    active_subscription_product_ids = await subscriptions_repo.get_active_subscription_product_ids(company_id)

    cart_updates: list[tuple[int, int, Decimal, dict[str, Any]]] = []
    for item in items:
        resolved = item.get("resolved_product") or {}
        product_id = int(resolved.get("product_id") or 0)
        if product_id <= 0:
            message = quote("One or more products in the package are unavailable.")
            return RedirectResponse(
                url=f"{request.url_for('shop_packages_page')}?cart_error={message}",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        quantity_per_package = int(item.get("quantity") or 0)
        if quantity_per_package <= 0:
            continue
        required_quantity = quantity_per_package * requested_quantity
        product = await shop_repo.get_product_by_id(
            product_id,
            company_id=company_id,
        )
        if not product:
            message = quote("One or more products in the package are unavailable.")
            return RedirectResponse(
                url=f"{request.url_for('shop_packages_page')}?cart_error={message}",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        
        # Check if this is a subscription product that the user already owns
        subscription_category_id = product.get("subscription_category_id")
        if subscription_category_id is not None and product_id in active_subscription_product_ids:
            product_name = str(product.get("name") or "a product")
            message = quote(
                f"Cannot add package. You already have an active subscription for {product_name}. Please manage your subscription from the Subscriptions page."
            )
            return RedirectResponse(
                url=f"{request.url_for('shop_packages_page')}?cart_error={message}",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        
        stock_available = int(product.get("stock") or 0)
        existing_item = await cart_repo.get_item(session.id, product_id)
        existing_quantity = existing_item.get("quantity") if existing_item else 0
        if stock_available <= 0 or existing_quantity + required_quantity > stock_available:
            remaining = max(stock_available - existing_quantity, 0)
            product_name = str(product.get("name") or "the product")
            message = quote(
                f"Cannot add package. {product_name} has only {remaining} left in stock."
            )
            return RedirectResponse(
                url=f"{request.url_for('shop_packages_page')}?cart_error={message}",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        price_source = product.get("price")
        if is_vip and product.get("vip_price") is not None:
            price_source = product.get("vip_price")
        unit_price = Decimal(str(price_source or 0))
        new_quantity = existing_quantity + required_quantity
        cart_updates.append(
            (
                product_id,
                new_quantity,
                unit_price,
                product,
            )
        )

    for product_id, new_quantity, unit_price, product in cart_updates:
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

    log_info(
        "Shop package added to cart",
        package_id=package_id,
        quantity=requested_quantity,
        added_by=user.get("id") if user else None,
    )

    return RedirectResponse(
        url=f"{request.url_for('shop_page')}?category=packages",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.get("/cart", response_class=HTMLResponse, name="cart_page")
async def view_cart(
    request: Request,
    order_message: str | None = Query(None, alias="orderMessage"),
    cart_error: str | None = Query(None, alias="cartError"),
    cart_message: str | None = Query(None, alias="cartMessage"),
):
    (
        user,
        membership,
        company,
        company_id,
        redirect,
    ) = await _load_company_section_context(
        request,
        permission_field="can_access_cart",
    )
    if redirect:
        return redirect

    session = await session_manager.load_session(request)
    if not session:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    items = await cart_repo.list_items(session.id)
    cart_items: list[dict[str, Any]] = []
    total = Decimal("0")
    cart_items_payload: list[dict[str, Any]] = []
    cart_product_ids: list[int] = []
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
        hydrated["available_stock"] = 0
        cart_items.append(hydrated)
        product_identifier = hydrated.get("product_id")
        try:
            resolved_product_id = int(product_identifier)
        except (TypeError, ValueError):
            resolved_product_id = 0
        if resolved_product_id > 0:
            cart_product_ids.append(resolved_product_id)
        cart_items_payload.append(
            {
                "product_id": hydrated.get("product_id"),
                "name": hydrated.get("product_name"),
                "sku": hydrated.get("product_sku"),
                "vendor_sku": hydrated.get("product_vendor_sku"),
                "description": hydrated.get("product_description"),
                "image_url": hydrated.get("product_image_url"),
                "unit_price": f"{unit_price:.2f}",
                "quantity": quantity,
                "line_total": f"{line_total:.2f}",
            }
        )

    recommendations: dict[str, list[dict[str, Any]]] = {"cross_sell": [], "upsell": []}
    if cart_product_ids:
        base_products = await shop_repo.list_products_by_ids(
            cart_product_ids,
            company_id=company_id,
        )
        product_lookup: dict[int, dict[str, Any]] = {}
        for product in base_products:
            try:
                product_lookup[int(product.get("id") or 0)] = product
            except (TypeError, ValueError):
                continue

        for item in cart_items:
            try:
                product_id = int(item.get("product_id") or 0)
            except (TypeError, ValueError):
                product_id = 0
            product = product_lookup.get(product_id)
            available_stock = 0
            if product:
                try:
                    available_stock = int(product.get("stock") or 0)
                except (TypeError, ValueError):
                    available_stock = 0
            item["available_stock"] = available_stock

        cart_product_id_set = {pid for pid in cart_product_ids}
        cross_sell_targets: dict[int, set[str]] = {}
        upsell_targets: dict[int, dict[str, set[str] | set[int]]] = {}
        for product in base_products:
            base_id = int(product.get("id") or 0)
            base_name = str(product.get("name") or "")
            for entry in product.get("cross_sell_products", []) or []:
                target_id = int(entry.get("id") or 0)
                if (
                    target_id > 0
                    and target_id not in cart_product_id_set
                    and target_id != base_id
                ):
                    cross_sell_targets.setdefault(target_id, set()).add(base_name)
            for entry in product.get("upsell_products", []) or []:
                target_id = int(entry.get("id") or 0)
                if (
                    target_id > 0
                    and target_id not in cart_product_id_set
                    and target_id != base_id
                ):
                    bucket = upsell_targets.setdefault(
                        target_id,
                        {"names": set(), "ids": set()},
                    )
                    bucket["names"].add(base_name)
                    bucket["ids"].add(base_id)

        target_ids = sorted(set(cross_sell_targets) | set(upsell_targets))
        if target_ids:
            related_products = await shop_repo.list_products_by_ids(
                target_ids,
                company_id=company_id,
            )
            related_map = {int(prod.get("id") or 0): prod for prod in related_products}
            is_vip = bool(company and int(company.get("is_vip") or 0) == 1)

            def _prepare_recommendation(
                *,
                target_id: int,
                source_names: Iterable[str],
                source_ids: Iterable[int] | None = None,
                kind: str,
            ) -> None:
                product = related_map.get(target_id)
                if not product:
                    return
                try:
                    stock_level = int(product.get("stock") or 0)
                except (TypeError, ValueError):
                    stock_level = 0
                if stock_level <= 0:
                    return
                price_source = product.get("price")
                if is_vip and product.get("vip_price") is not None:
                    price_source = product.get("vip_price")
                try:
                    price_value = Decimal(str(price_source or 0)).quantize(
                        Decimal("0.01"),
                        rounding=ROUND_HALF_UP,
                    )
                except (InvalidOperation, TypeError, ValueError):
                    price_value = Decimal("0.00")
                entry = {
                    "product_id": target_id,
                    "name": product.get("name"),
                    "sku": product.get("sku"),
                    "image_url": product.get("image_url"),
                    "price": price_value,
                    "source_names": sorted({name for name in source_names if name}),
                    "source_product_ids": sorted(
                        {
                            int(pid)
                            for pid in (source_ids or [])
                            if isinstance(pid, int) and pid > 0
                        }
                    ),
                    "kind": kind,
                }
                recommendations.setdefault(kind, []).append(entry)

            for target_id, names in cross_sell_targets.items():
                _prepare_recommendation(
                    target_id=target_id,
                    source_names=names,
                    source_ids=None,
                    kind="cross_sell",
                )
            for target_id, payload in upsell_targets.items():
                _prepare_recommendation(
                    target_id=target_id,
                    source_names=payload.get("names", set()),
                    source_ids=payload.get("ids", set()),
                    kind="upsell",
                )

            for bucket in recommendations.values():
                bucket.sort(key=lambda item: str(item.get("name") or "").lower())

    extra = {
        "title": "Cart",
        "cart_items": cart_items,
        "cart_total": total,
        "order_message": order_message,
        "cart_error": cart_error,
        "cart_message": cart_message,
        "cart_items_payload": cart_items_payload,
        "cart_recommendations": recommendations,
        "low_stock_threshold": SHOP_LOW_STOCK_THRESHOLD,
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
    (
        user,
        membership,
        company,
        company_id,
        redirect,
    ) = await _load_company_section_context(
        request,
        permission_field="can_access_orders",
    )
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


@app.post(
    "/cart/update",
    response_class=RedirectResponse,
    name="cart_update_items",
    include_in_schema=False,
)
async def update_cart_items(request: Request) -> RedirectResponse:
    (
        user,
        membership,
        company,
        company_id,
        redirect,
    ) = await _load_company_section_context(
        request,
        permission_field="can_access_cart",
    )
    if redirect:
        return redirect

    session = await session_manager.load_session(request)
    if not session:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    form = await request.form()
    updates: dict[int, int] = {}
    removals: set[int] = set()
    invalid_entries = False
    stock_conflicts: set[int] = set()
    max_quantity = 9999

    if isinstance(form, FormData):
        items = form.multi_items()
    else:
        items = form.items()

    for key, raw_value in items:
        if not isinstance(key, str) or not key.startswith("quantity_"):
            continue
        suffix = key[len("quantity_") :]
        try:
            product_id = int(suffix)
        except (TypeError, ValueError):
            invalid_entries = True
            continue

        value: str | None
        if isinstance(raw_value, (list, tuple)):
            value = str(raw_value[0]).strip() if raw_value else None
        else:
            value = str(raw_value).strip() if raw_value is not None else None

        try:
            quantity = int(value) if value is not None else None
        except (TypeError, ValueError):
            invalid_entries = True
            continue

        if quantity is None:
            invalid_entries = True
            continue

        if quantity <= 0:
            removals.add(product_id)
            continue

        if quantity > max_quantity:
            quantity = max_quantity
            invalid_entries = True

        updates[product_id] = quantity

    updated_count = 0
    for product_id, desired_quantity in updates.items():
        if product_id in removals:
            continue
        existing = await cart_repo.get_item(session.id, product_id)
        if not existing:
            invalid_entries = True
            removals.add(product_id)
            continue

        current_quantity = int(existing.get("quantity") or 0)
        if current_quantity == desired_quantity:
            continue

        product = await shop_repo.get_product_by_id(
            product_id,
            company_id=company_id,
        )
        if not product:
            invalid_entries = True
            removals.add(product_id)
            continue

        raw_stock = product.get("stock")
        try:
            available_stock = int(raw_stock)
        except (TypeError, ValueError):
            available_stock = 0

        if available_stock <= 0:
            stock_conflicts.add(product_id)
            removals.add(product_id)
            invalid_entries = True
            continue

        if desired_quantity > available_stock:
            stock_conflicts.add(product_id)
            invalid_entries = True
            continue

        await cart_repo.update_item_quantity(
            session_id=session.id,
            product_id=product_id,
            quantity=desired_quantity,
        )
        updated_count += 1

    removed_count = 0
    if removals:
        await cart_repo.remove_items(session.id, removals)
        removed_count = len(removals)

    url = URL(str(request.url_for("cart_page")))
    params: dict[str, str] = {}

    if updated_count:
        fragments = ["Quantities updated"]
        if removed_count:
            fragments.append("items removed")
        params["cartMessage"] = ", ".join(fragments) + "."
    elif removed_count and not stock_conflicts:
        params["cartMessage"] = "Items removed."

    if stock_conflicts:
        params["cartError"] = "Unable to increase some quantities due to limited stock."
    elif invalid_entries:
        if "cartMessage" in params:
            params["cartError"] = "Some quantities were adjusted."
        else:
            params["cartError"] = "No changes were applied. Please review the quantities entered."

    redirect_url = url.include_query_params(**params) if params else url
    return RedirectResponse(
        url=str(redirect_url),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/cart/remove", response_class=RedirectResponse, name="cart_remove_items", include_in_schema=False)
async def remove_cart_items(request: Request) -> RedirectResponse:
    (
        user,
        membership,
        company,
        company_id,
        redirect,
    ) = await _load_company_section_context(
        request,
        permission_field="can_access_cart",
    )
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
    (
        user,
        membership,
        company,
        company_id,
        redirect,
    ) = await _load_company_section_context(
        request,
        permission_field="can_access_cart",
    )
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
    
    # Create subscriptions for any subscription products in the order
    try:
        await subscription_shop_integration.create_subscriptions_from_order(
            order_number=order_number,
            company_id=company_id,
            user_id=int(user["id"]),
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error(
            "Failed to create subscriptions from order",
            order_number=order_number,
            company_id=company_id,
            error=str(exc),
        )
        # Don't fail the order, just log the error
    
    # Send order to Xero for invoicing
    try:
        user_record = await user_repo.get_user_by_id(int(user["id"]))
        user_name = None
        if user_record:
            first_name = user_record.get("first_name", "").strip()
            last_name = user_record.get("last_name", "").strip()
            if first_name or last_name:
                user_name = f"{first_name} {last_name}".strip()
            elif user_record.get("email"):
                user_name = str(user_record["email"])
        
        await xero_service.send_order_to_xero(
            order_number=order_number,
            company_id=company_id,
            user_name=user_name,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error(
            "Failed to send order to Xero",
            order_number=order_number,
            company_id=company_id,
            error=str(exc),
        )
        # Don't fail the order, just log the error
    
    success = quote("Your order is being processed.")
    return RedirectResponse(
        url=f"{request.url_for('cart_page')}?orderMessage={success}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.get("/notifications", response_class=HTMLResponse)
async def notifications_dashboard(request: Request):
    user, redirect = await _require_authenticated_user(request)
    if redirect:
        return redirect

    params = request.query_params
    search_term = (params.get("q") or "").strip()
    read_state = (params.get("read_state") or "all").lower()
    valid_read_states = {option[0] for option in _NOTIFICATION_READ_OPTIONS}
    if read_state not in valid_read_states:
        read_state = "all"

    sort_by = (params.get("sort_by") or "created_at").lower()
    valid_sort_columns = {option[0] for option in _NOTIFICATION_SORT_CHOICES}
    if sort_by not in valid_sort_columns:
        sort_by = "created_at"

    sort_order = (params.get("sort_order") or "desc").lower()
    if sort_order not in {"asc", "desc"}:
        sort_order = "desc"

    event_type_filter = (params.get("event_type") or "").strip()
    created_from_raw = (params.get("created_from") or "").strip()
    created_to_raw = (params.get("created_to") or "").strip()

    page_size = _parse_int_in_range(params.get("page_size"), default=25, minimum=5, maximum=100)
    if _NOTIFICATION_PAGE_SIZES:
        page_size = min(_NOTIFICATION_PAGE_SIZES, key=lambda size: abs(size - page_size))
    page = _parse_int_in_range(params.get("page"), default=1, minimum=1, maximum=1000)

    created_from_dt = _parse_input_datetime(created_from_raw)
    created_to_dt = None
    created_to_candidate = _parse_input_datetime(created_to_raw, assume_midnight=True)
    if created_to_candidate:
        if created_to_raw and all(separator not in created_to_raw for separator in ("T", " ")):
            created_to_dt = created_to_candidate + timedelta(days=1)
        else:
            created_to_dt = created_to_candidate

    search_filter = search_term or None
    event_filters = [event_type_filter] if event_type_filter else None
    repo_read_state = read_state if read_state in {"unread", "read"} else None

    try:
        user_id = int(user.get("id"))
    except (TypeError, ValueError):
        user_id = None

    total_count = await notifications_repo.count_notifications(
        user_id=user_id,
        read_state=repo_read_state,
        event_types=event_filters,
        search=search_filter,
        created_from=created_from_dt,
        created_to=created_to_dt,
    )

    total_pages = max(1, math.ceil(total_count / page_size)) if page_size else 1
    if page > total_pages:
        page = total_pages

    offset = (page - 1) * page_size

    records = await notifications_repo.list_notifications(
        user_id=user_id,
        read_state=repo_read_state,
        event_types=event_filters,
        search=search_filter,
        created_from=created_from_dt,
        created_to=created_to_dt,
        sort_by=sort_by,
        sort_direction=sort_order,
        limit=page_size,
        offset=offset,
    )

    filtered_unread_count = await notifications_repo.count_notifications(
        user_id=user_id,
        read_state="unread",
        event_types=event_filters,
        search=search_filter,
        created_from=created_from_dt,
        created_to=created_to_dt,
    )

    global_unread_count = await notifications_repo.count_notifications(
        user_id=user_id,
        read_state="unread",
    )

    prepared_notifications: list[dict[str, Any]] = []
    for record in records:
        metadata_items = _prepare_notification_metadata(record.get("metadata"))
        created_iso = _to_iso(record.get("created_at")) or ""
        read_iso = _to_iso(record.get("read_at")) or ""
        is_unread = record.get("read_at") is None
        prepared_notifications.append(
            {
                "id": record.get("id"),
                "event_type": record.get("event_type"),
                "message": record.get("message"),
                "metadata_items": metadata_items,
                "created_iso": created_iso,
                "read_iso": read_iso,
                "is_unread": is_unread,
                "status_label": "Unread" if is_unread else "Read",
                "status_class": "status status--unread" if is_unread else "status status--read",
                "metadata_json": json.dumps(
                    _serialise_for_json(record.get("metadata")),
                    ensure_ascii=False,
                    indent=2,
                )
                if record.get("metadata") is not None
                else None,
            }
        )

    pagination = {
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "total_count": total_count,
        "start": offset + 1 if total_count else 0,
        "end": offset + len(prepared_notifications),
        "has_previous": page > 1,
        "has_next": page < total_pages,
        "previous_url": str(request.url.include_query_params(page=page - 1)) if page > 1 else None,
        "next_url": str(request.url.include_query_params(page=page + 1)) if page < total_pages else None,
    }

    filters = {
        "query": search_term,
        "read_state": read_state,
        "event_type": event_type_filter,
        "created_from": created_from_raw,
        "created_to": created_to_raw,
        "sort_by": sort_by,
        "sort_order": sort_order,
        "page_size": page_size,
        "page": page,
    }

    active_filters = any(
        [
            bool(search_term),
            read_state != "all",
            bool(event_type_filter),
            bool(created_from_raw),
            bool(created_to_raw),
        ]
    )

    event_type_options = await notifications_repo.list_event_types(user_id=user_id)

    extra = {
        "title": "Notifications",
        "notifications": prepared_notifications,
        "filters": filters,
        "filters_active": active_filters,
        "sort_options": _NOTIFICATION_SORT_CHOICES,
        "order_options": _NOTIFICATION_ORDER_CHOICES,
        "read_options": _NOTIFICATION_READ_OPTIONS,
        "event_type_options": event_type_options,
        "pagination": pagination,
        "total_count": total_count,
        "filtered_unread_count": filtered_unread_count,
        "page_size_options": _NOTIFICATION_PAGE_SIZES,
        "notification_unread_count": global_unread_count,
    }

    return await _render_template("notifications/index.html", request, user, extra=extra)


@app.get("/tickets", response_class=HTMLResponse)
async def portal_tickets_page(request: Request):
    user, redirect = await _require_authenticated_user(request)
    if redirect:
        return redirect

    params = request.query_params
    status_filter = (params.get("status") or "").strip() or None
    search_term = (params.get("q") or "").strip() or None
    success_message = params.get("success")
    error_message = params.get("error")

    # If no explicit filters are provided, try to load the default view
    if not status_filter and not search_term:
        try:
            user_id = int(user.get("id"))
            default_view = await ticket_views_repo.get_default_view(user_id)
            if default_view:
                filters = default_view.get("filters") or {}
                # Apply status filter from default view
                if filters.get("status"):
                    status_list = filters["status"]
                    if isinstance(status_list, list) and status_list:
                        status_filter = ",".join(str(s) for s in status_list)
                # Apply search filter from default view
                if filters.get("search"):
                    search_term = str(filters["search"])
        except (TypeError, ValueError, RuntimeError):
            # If we can't load the default view, just continue without it
            pass

    return await _render_portal_tickets_page(
        request,
        user,
        status_filter=status_filter,
        search_term=search_term,
        success_message=success_message,
        error_message=error_message,
    )


@app.post("/tickets", response_class=HTMLResponse)
async def portal_create_ticket(request: Request):
    user, redirect = await _require_authenticated_user(request)
    if redirect:
        return redirect

    params = request.query_params
    status_filter = (params.get("status") or "").strip() or None
    search_term = (params.get("q") or "").strip() or None

    form = await request.form()
    subject = str(form.get("subject") or "").strip()
    description = str(form.get("description") or "").strip()
    form_values = {"subject": subject, "description": description}

    if not subject:
        return await _render_portal_tickets_page(
            request,
            user,
            status_filter=status_filter,
            search_term=search_term,
            error_message="Provide a subject for your ticket.",
            form_values=form_values,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    sanitized_description = sanitize_rich_text(description)
    description_payload = sanitized_description.html

    try:
        requester_id = int(user.get("id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied") from None

    company_raw = user.get("company_id")
    try:
        company_id = int(company_raw) if company_raw is not None else None
    except (TypeError, ValueError):
        company_id = None
    if company_id is None:
        return await _render_portal_tickets_page(
            request,
            user,
            status_filter=status_filter,
            search_term=search_term,
            error_message="Select a company before creating a ticket.",
            form_values=form_values,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        status_value = await tickets_service.resolve_status_or_default("open")
        ticket = await tickets_service.create_ticket(
            subject=subject,
            description=description_payload,
            requester_id=requester_id,
            company_id=company_id,
            assigned_user_id=None,
            priority="normal",
            status=status_value,
            category=None,
            module_slug=None,
            external_reference=None,
            trigger_automations=True,
            initial_reply_author_id=requester_id,
        )
        await tickets_repo.add_watcher(ticket["id"], requester_id)
        try:
            await tickets_service.refresh_ticket_ai_summary(ticket["id"])
        except RuntimeError:
            pass
        await tickets_service.refresh_ticket_ai_tags(ticket["id"])
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to create portal ticket", error=str(exc))
        return await _render_portal_tickets_page(
            request,
            user,
            status_filter=status_filter,
            search_term=search_term,
            error_message="We couldn't create your ticket right now. Please try again.",
            form_values=form_values,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    message = quote("Ticket created.")
    destination = f"/tickets/{ticket['id']}?success={message}"
    return RedirectResponse(url=destination, status_code=status.HTTP_303_SEE_OTHER)


@app.get("/tickets/{ticket_id}", response_class=HTMLResponse)
async def portal_ticket_detail(request: Request, ticket_id: int):
    user, redirect = await _require_authenticated_user(request)
    if redirect:
        return redirect

    params = request.query_params
    success_message = params.get("success")
    error_message = params.get("error")

    return await _render_portal_ticket_detail(
        request,
        user,
        ticket_id=ticket_id,
        success_message=success_message,
        error_message=error_message,
    )


@app.post("/tickets/{ticket_id}/replies", response_class=HTMLResponse)
async def portal_ticket_reply(request: Request, ticket_id: int):
    user, redirect = await _require_authenticated_user(request)
    if redirect:
        return redirect

    try:
        user_id = int(user.get("id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied") from None

    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    has_helpdesk_access = await _is_helpdesk_technician(user, request)
    is_super_admin = bool(user.get("is_super_admin"))
    is_requester = ticket.get("requester_id") == user_id
    if not (has_helpdesk_access or is_super_admin or is_requester):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    form = await request.form()
    body = str(form.get("body") or "").strip()
    sanitized_body = sanitize_rich_text(body)
    if not sanitized_body.has_rich_content:
        return await _render_portal_ticket_detail(
            request,
            user,
            ticket_id=ticket_id,
            reply_error="Reply message cannot be empty.",
            reply_body=body,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        await tickets_repo.create_reply(
            ticket_id=ticket_id,
            author_id=user_id,
            body=sanitized_body.html,
            is_internal=False,
            minutes_spent=None,
            is_billable=False,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to create portal ticket reply", ticket_id=ticket_id, error=str(exc))
        return await _render_portal_ticket_detail(
            request,
            user,
            ticket_id=ticket_id,
            error_message="We couldn't post your reply. Please try again.",
            reply_body=body,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    try:
        await tickets_service.refresh_ticket_ai_summary(ticket_id)
    except RuntimeError:
        pass
    await tickets_service.refresh_ticket_ai_tags(ticket_id)
    actor_type = "technician" if has_helpdesk_access or is_super_admin else "requester"
    await tickets_service.emit_ticket_updated_event(
        ticket_id,
        actor_type=actor_type,
        actor=user,
    )
    await tickets_service.broadcast_ticket_event(action="reply", ticket_id=ticket_id)

    message = quote("Reply posted.")
    return RedirectResponse(
        url=f"/tickets/{ticket_id}?success={message}",
        status_code=status.HTTP_303_SEE_OTHER,
    )



@app.get("/knowledge-base", response_class=HTMLResponse, tags=["Knowledge Base"])
async def knowledge_base_index(request: Request, article: str | None = Query(None, alias="slug")):
    if article:
        target = f"/knowledge-base/articles/{quote(article, safe='')}"
        return RedirectResponse(url=target, status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    user, _ = await _get_optional_user(request)
    access_context = await knowledge_base_service.build_access_context(user)
    include_unpublished = bool(user and user.get("is_super_admin"))
    articles = await knowledge_base_service.list_articles_for_context(
        access_context,
        include_unpublished=include_unpublished,
    )
    extra_context = {
        "title": "Knowledge base",
        "kb_articles": articles,
        "kb_is_super_admin": bool(user and user.get("is_super_admin")),
    }
    context = await _build_portal_context(request, user, extra=extra_context)
    return templates.TemplateResponse("knowledge_base/index.html", context)


@app.get("/knowledge-base/articles/{slug}", response_class=HTMLResponse, tags=["Knowledge Base"])
async def knowledge_base_article(request: Request, slug: str):
    user, _ = await _get_optional_user(request)
    access_context = await knowledge_base_service.build_access_context(user)
    include_unpublished = bool(user and user.get("is_super_admin"))
    article = await knowledge_base_service.get_article_by_slug_for_context(
        slug,
        access_context,
        include_unpublished=include_unpublished,
        include_permissions=include_unpublished,
    )
    if not article:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")
    extra_context = {
        "title": article.get("title") or "Knowledge base",
        "kb_article": article,
        "kb_is_super_admin": bool(user and user.get("is_super_admin")),
    }
    context = await _build_portal_context(request, user, extra=extra_context)
    return templates.TemplateResponse("knowledge_base/article.html", context)


@app.get("/notifications/settings", response_class=HTMLResponse)
async def notification_settings_page(request: Request):
    user, redirect = await _require_authenticated_user(request)
    if redirect:
        return redirect

    try:
        user_id = int(user.get("id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User session invalid")

    stored_preferences = await notification_preferences_repo.list_preferences(user_id)
    is_super_admin = bool(user.get("is_super_admin"))
    base_settings = await event_settings_service.list_event_settings(include_hidden=True)
    settings_map = {item["event_type"]: item for item in base_settings}

    event_types = merge_event_types(
        settings_map.keys(),
        [preference.get("event_type") for preference in stored_preferences],
        await notifications_repo.list_event_types(user_id=user_id),
    )

    mapped = {pref.get("event_type"): pref for pref in stored_preferences if pref.get("event_type")}
    preferences: list[dict[str, Any]] = []
    for event_type in event_types:
        setting = settings_map.get(event_type)
        if not setting:
            setting = await event_settings_service.get_event_setting(event_type)
            settings_map[event_type] = setting
        if not is_super_admin and not bool(setting.get("is_user_visible", True)):
            continue
        pref = mapped.get(event_type) or {}
        allow_in_app = bool(setting.get("allow_channel_in_app", True))
        allow_email = bool(setting.get("allow_channel_email", False))
        allow_sms = bool(setting.get("allow_channel_sms", False))
        channel_in_app = bool(pref.get("channel_in_app", setting.get("default_channel_in_app", True))) and allow_in_app
        channel_email = bool(pref.get("channel_email", setting.get("default_channel_email", False))) and allow_email
        channel_sms = bool(pref.get("channel_sms", setting.get("default_channel_sms", False))) and allow_sms
        description_value = setting.get("description")
        if isinstance(description_value, str):
            description_value = description_value.strip() or None
        preferences.append(
            {
                "event_type": event_type,
                "display_name": str(setting.get("display_name") or event_type),
                "description": description_value,
                "channel_in_app": channel_in_app,
                "channel_email": channel_email,
                "channel_sms": channel_sms,
                "allow_channel_in_app": allow_in_app,
                "allow_channel_email": allow_email,
                "allow_channel_sms": allow_sms,
                "default_channel_in_app": bool(setting.get("default_channel_in_app", True)),
                "default_channel_email": bool(setting.get("default_channel_email", False)),
                "default_channel_sms": bool(setting.get("default_channel_sms", False)),
                "is_user_visible": bool(setting.get("is_user_visible", True)),
            }
        )

    modules = await modules_service.list_modules()
    modules_payload = _serialise_for_json(modules)
    event_settings_payload = [
        {
            "event_type": item.get("event_type"),
            "display_name": item.get("display_name"),
            "description": item.get("description"),
            "message_template": item.get("message_template"),
            "is_user_visible": bool(item.get("is_user_visible", True)),
            "allow_channel_in_app": bool(item.get("allow_channel_in_app", True)),
            "allow_channel_email": bool(item.get("allow_channel_email", False)),
            "allow_channel_sms": bool(item.get("allow_channel_sms", False)),
            "default_channel_in_app": bool(item.get("default_channel_in_app", True)),
            "default_channel_email": bool(item.get("default_channel_email", False)),
            "default_channel_sms": bool(item.get("default_channel_sms", False)),
            "module_actions": _serialise_for_json(item.get("module_actions") or []),
        }
        for item in base_settings
    ]

    if is_super_admin:
        menu_events = [
            {
                "event_type": item.get("event_type"),
                "display_name": item.get("display_name") or item.get("event_type"),
                "description": item.get("description"),
                "is_user_visible": bool(item.get("is_user_visible", True)),
            }
            for item in event_settings_payload
        ]
    else:
        menu_events = [
            {
                "event_type": item.get("event_type"),
                "display_name": item.get("display_name") or item.get("event_type"),
                "description": item.get("description"),
                "is_user_visible": bool(item.get("is_user_visible", True)),
            }
            for item in preferences
        ]

    extra = {
        "title": "Notification settings",
        "preferences": preferences,
        "preferences_endpoint": "/api/notifications/preferences",
        "channel_descriptions": {
            "channel_in_app": "Store notifications in the in-app feed",
            "channel_email": "Email the notification to your primary address",
            "channel_sms": "Send a text message to your mobile number",
        },
        "default_event_types": set(DEFAULT_NOTIFICATION_EVENT_TYPES),
        "is_super_admin": is_super_admin,
        "event_settings": event_settings_payload,
        "event_settings_endpoint": "/api/notifications/events/settings",
        "modules": modules_payload,
        "event_menu": menu_events,
    }

    return await _render_template("notifications/settings.html", request, user, extra=extra)


@app.get("/myforms", response_class=HTMLResponse)
async def forms_page(request: Request):
    (
        user,
        _membership,
        company,
        company_id,
        redirect,
    ) = await _load_company_section_context(
        request,
        permission_field="can_access_forms",
        allow_super_admin_without_company=True,
    )
    if redirect:
        return redirect

    active_company_id = company_id
    active_company = company
    if active_company is None:
        fallback_company_id = getattr(request.state, "active_company_id", None)
        if fallback_company_id is not None:
            try:
                active_company = await company_repo.get_company_by_id(int(fallback_company_id))
                active_company_id = int(fallback_company_id)
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

    staff_company = await company_repo.get_company_by_id(staff.get("company_id"))
    company_name = staff_company.get("name") if staff_company else ""
    
    # Construct SMS message
    message_parts = [f"Your verification code is: {code}"]
    if admin_name:
        message_parts.append(f"Requested by: {admin_name}")
    if company_name:
        message_parts.append(f"Company: {company_name}")
    message = " | ".join(message_parts)
    
    # Send SMS via SMS Gateway module
    result: dict[str, Any] = {}
    try:
        result = await modules_service.trigger_module(
            "sms-gateway",
            {
                "message": message,
                "phoneNumbers": [staff.get("mobile_phone")],
            },
            background=False,
        )
    except Exception as exc:
        log_error("SMS Gateway module failed", staff_id=staff_id, error=str(exc))
        result = {"status": "error", "error": str(exc)}

    status_code = int(result.get("response_status")) if result.get("response_status") is not None else None
    # Accept both "succeeded" and "queued" as success states
    # (background=False should always return "succeeded", but we check "queued" defensively)
    success = result.get("status") in {"succeeded", "queued"}

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
    await staff_access_service.apply_pending_access_for_user(created_user)
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
    base_url = str(settings.portal_url).rstrip("/") if settings.portal_url else None
    reset_path = f"/reset-password?token={token}"
    reset_link = f"{base_url}{reset_path}" if base_url else reset_path
    inviter_email = user.get("email") or settings.smtp_user or None
    staff_name = staff.get("first_name") or staff.get("last_name") or "there"
    company_name = (company or {}).get("name") if company else None
    company_phrase = f" for {company_name}" if company_name else ""
    company_phrase_html = f" for {escape(company_name)}" if company_name else ""
    text_body = (
        f"Hello {staff_name},\n\n"
        f"You've been invited to access {settings.app_name}{company_phrase}. "
        f"Use the link below to set your password and activate your account:\n\n"
        f"{reset_link}\n\n"
        "The link expires in one hour. If you were not expecting this invitation you can ignore this email."
    )
    html_body = (
        f"<p>Hello {escape(staff_name)},</p>"
        f"<p>You've been invited to access {escape(settings.app_name)}{company_phrase_html}.</p>"
        f"<p><a href=\"{escape(reset_link)}\">Set your password and activate your account</a></p>"
        "<p>The link expires in one hour. If you were not expecting this invitation you can ignore this email.</p>"
    )
    try:
        sent, event_metadata = await email_service.send_email(
            subject=f"You're invited to {settings.app_name}",
            recipients=[staff["email"]],
            text_body=text_body,
            html_body=html_body,
            reply_to=inviter_email,
        )
        if not sent:
            log_info(
                "Staff invitation email skipped due to SMTP configuration",
                staff_id=staff_id,
                invited_user_id=created_user["id"],
                event_id=(event_metadata or {}).get("id") if isinstance(event_metadata, dict) else None,
            )
        else:
            log_info(
                "Staff invitation email sent",
                staff_id=staff_id,
                invited_user_id=created_user["id"],
                event_id=(event_metadata or {}).get("id") if isinstance(event_metadata, dict) else None,
            )
    except email_service.EmailDispatchError as exc:  # pragma: no cover - logged for diagnostics
        log_error(
            "Failed to send staff invitation email",
            staff_id=staff_id,
            invited_user_id=created_user["id"],
            error=str(exc),
            event_id=(event_metadata or {}).get("id") if "event_metadata" in locals() and isinstance(event_metadata, dict) else None,
        )

    log_info(
        "Staff invitation generated",
        staff_id=staff_id,
        invited_user_id=created_user["id"],
    )
    return JSONResponse({"success": True})

@app.get("/admin/profile", response_class=HTMLResponse)
async def admin_profile_page(request: Request):
    user, membership, redirect = await _require_administration_access(request)
    if redirect:
        return redirect

    try:
        devices = await auth_repo.get_totp_authenticators(user["id"])
    except Exception:  # pragma: no cover - defensive logging for profile rendering
        devices = []

    totp_devices: list[dict[str, Any]] = []
    for device in devices:
        identifier = device.get("id")
        if identifier is None:
            continue
        name = device.get("name") or "Authenticator"
        try:
            identifier = int(identifier)
        except (TypeError, ValueError):
            continue
        totp_devices.append({"id": identifier, "name": name})

    totp_devices.sort(key=lambda entry: entry["name"].lower())

    context = await _build_base_context(
        request,
        user,
        extra={
            "title": "My profile",
            "profile_membership": membership,
            "profile_totp_devices": totp_devices,
        },
    )
    return templates.TemplateResponse("admin/profile.html", context)


@app.get("/admin/impersonation", response_class=HTMLResponse)
async def admin_impersonation_page(
    request: Request,
    success: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    return await _render_impersonation_dashboard(
        request,
        current_user,
        success_message=_sanitize_message(success),
        error_message=_sanitize_message(error),
    )


@app.post("/admin/impersonation", response_class=HTMLResponse)
async def admin_impersonation_start(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    form = await request.form()
    target_value = form.get("userId") or form.get("user_id")
    try:
        target_user_id = int(str(target_value))
    except (TypeError, ValueError):
        return await _render_impersonation_dashboard(
            request,
            current_user,
            error_message="Select a valid user to impersonate.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    actor_session = getattr(request.state, "session", None)
    if actor_session is None:
        actor_session = await session_manager.load_session(request)
    if actor_session is None:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    try:
        _, impersonated_session = await impersonation_service.start_impersonation(
            request=request,
            actor_user=current_user,
            actor_session=actor_session,
            target_user_id=target_user_id,
        )
    except impersonation_service.SelfImpersonationError:
        error_message = "You cannot impersonate your own account."
        status_code = status.HTTP_400_BAD_REQUEST
    except impersonation_service.AlreadyImpersonatingError:
        error_message = "An impersonation session is already active."
        status_code = status.HTTP_409_CONFLICT
    except impersonation_service.NotImpersonatableError as exc:
        error_message = str(exc)
        status_code = status.HTTP_403_FORBIDDEN
    else:
        response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
        session_manager.apply_session_cookies(response, impersonated_session)
        request.state.session = impersonated_session
        request.state.active_company_id = impersonated_session.active_company_id
        request.state.impersonator_user_id = impersonated_session.impersonator_user_id
        request.state.impersonator_session_id = impersonated_session.impersonator_session_id
        return response

    return await _render_impersonation_dashboard(
        request,
        current_user,
        error_message=_sanitize_message(error_message),
        status_code=status_code,
    )


@app.get("/admin/companies", response_class=HTMLResponse)
async def admin_companies_page(
    request: Request,
    company_id: int | None = Query(default=None),
    success: str | None = Query(default=None),
    error: str | None = Query(default=None),
    show_archived: bool = Query(default=False),
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
        include_archived=show_archived,
    )


@app.get("/admin/companies/{company_id}/edit", response_class=HTMLResponse)
async def admin_company_edit_page(
    company_id: int,
    request: Request,
    success: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    return await _render_company_edit_page(
        request,
        current_user,
        company_id=company_id,
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
    tactical_client_id = (str(form.get("tacticalClientId", "")).strip() or None)
    xero_id = (str(form.get("xeroId", "")).strip() or None)
    is_vip = _parse_bool(form.get("isVip"))
    raw_email_domains = form.get("emailDomains")
    try:
        email_domains = company_domains.parse_email_domain_text(
            str(raw_email_domains) if raw_email_domains is not None else ""
        )
    except company_domains.EmailDomainError as exc:
        response = await _render_companies_dashboard(
            request,
            current_user,
            error_message=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
        return response
    if not name:
        response = await _render_companies_dashboard(
            request,
            current_user,
            error_message="Enter a company name.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
        return response
    payload: dict[str, Any] = {
        "name": name,
        "is_vip": 1 if is_vip else 0,
        "email_domains": email_domains,
    }
    if syncro_company_id:
        payload["syncro_company_id"] = syncro_company_id
    if xero_id:
        payload["xero_id"] = xero_id
    if tactical_client_id:
        payload["tacticalrmm_client_id"] = tactical_client_id
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


@app.post("/admin/companies/assign", response_class=HTMLResponse)
async def admin_assign_user_to_company(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    form_keys = set(form.keys())
    user_id_raw = form.get("userId") or form.get("user_id")
    company_id_raw = form.get("companyId") or form.get("company_id")
    source_company_raw = form.get("sourceCompanyId") or form.get("source_company_id")
    role_raw = form.get("roleId") or form.get("role_id")
    staff_permission_raw = form.get("staffPermission") or form.get("staff_permission")

    assign_form_state: dict[str, Any] = {
        "company_id": source_company_raw or company_id_raw,
        "user_value": user_id_raw,
        "user_id": None,
        "role_id": role_raw,
        "staff_permission": staff_permission_raw,
        "can_manage_staff": "can_manage_staff" in form_keys,
    }
    for column in _COMPANY_PERMISSION_COLUMNS:
        field = column.get("field")
        if field:
            assign_form_state[field] = field in form_keys

    resolved_company_id: int | None = None
    for raw_value in (source_company_raw, company_id_raw):
        if raw_value is None:
            continue
        try:
            resolved_company_id = int(raw_value)
            break
        except (TypeError, ValueError):
            continue

    async def _assign_error(message: str, status_code: int) -> HTMLResponse | RedirectResponse:
        if resolved_company_id is None:
            return _companies_redirect(error=message)
        return await _render_company_edit_page(
            request,
            current_user,
            company_id=resolved_company_id,
            assign_form_values=assign_form_state,
            error_message=message,
            status_code=status_code,
        )

    if resolved_company_id is None:
        return await _assign_error(
            "Select both a user and a company.", status.HTTP_400_BAD_REQUEST
        )

    company_id = resolved_company_id
    assign_form_state["company_id"] = company_id

    user_identifier = (user_id_raw or "").strip()
    parsed_user_id: int | None = None
    user_record: dict[str, Any] | None = None
    staff_record: dict[str, Any] | None = None
    existing_assignment: dict[str, Any] | None = None
    staff_selection_id = _parse_staff_selection(user_identifier)
    if staff_selection_id is not None:
        staff_record = await staff_repo.get_staff_by_id(staff_selection_id)
        if not staff_record:
            return await _assign_error(
                "Selected staff member could not be found.",
                status.HTTP_404_NOT_FOUND,
            )
        email = (staff_record.get("email") or "").strip()
        if not email:
            return await _assign_error(
                "Selected staff member does not have an email address.",
                status.HTTP_400_BAD_REQUEST,
            )
        staff_company_raw = staff_record.get("company_id")
        try:
            staff_company_id = int(staff_company_raw)
        except (TypeError, ValueError):
            staff_company_id = None
        if staff_company_id is not None and staff_company_id != company_id:
            return await _assign_error(
                "Selected staff member belongs to a different company.",
                status.HTTP_400_BAD_REQUEST,
            )

        user_record = await user_repo.get_user_by_email(email)
        if user_record and int(user_record.get("company_id") or 0) == company_id:
            parsed_user_id = int(user_record.get("id"))
            existing_assignment = await user_company_repo.get_user_company(
                parsed_user_id, company_id
            )
        else:
            try:
                staff_permission = (
                    int(staff_permission_raw)
                    if staff_permission_raw is not None
                    else 0
                )
            except (TypeError, ValueError):
                return await _assign_error(
                    "Select a valid staff permission level.",
                    status.HTTP_400_BAD_REQUEST,
                )
            if staff_permission < 0:
                staff_permission = 0
            if staff_permission > 3:
                staff_permission = 3

            permission_values: dict[str, bool] = {}
            for column in _COMPANY_PERMISSION_COLUMNS:
                field = column.get("field")
                if not field:
                    continue
                permission_values[field] = (
                    _parse_bool(form.get(field)) if field in form_keys else False
                )

            if "can_manage_staff" in form_keys:
                can_manage_staff_value = _parse_bool(form.get("can_manage_staff"))
            else:
                can_manage_staff_value = False

            role_id_value: int | None = None
            if role_raw:
                try:
                    role_id_value = int(role_raw)
                except (TypeError, ValueError):
                    return await _assign_error(
                        "Select a valid role for the membership.",
                        status.HTTP_400_BAD_REQUEST,
                    )
                role_record = await role_repo.get_role_by_id(role_id_value)
                if not role_record:
                    return await _assign_error(
                        "Selected role could not be found.",
                        status.HTTP_404_NOT_FOUND,
                    )

            await pending_staff_access_repo.upsert_assignment(
                staff_id=int(staff_record.get("id")),
                company_id=company_id,
                staff_permission=staff_permission,
                can_manage_staff=can_manage_staff_value,
                can_manage_licenses=permission_values.get("can_manage_licenses", False),
                can_manage_assets=permission_values.get("can_manage_assets", False),
                can_manage_invoices=permission_values.get("can_manage_invoices", False),
                can_manage_office_groups=permission_values.get(
                    "can_manage_office_groups", False
                ),
                can_manage_issues=permission_values.get("can_manage_issues", False),
                can_order_licenses=permission_values.get("can_order_licenses", False),
                can_access_shop=permission_values.get("can_access_shop", False),
                can_access_cart=permission_values.get("can_access_cart", False),
                can_access_orders=permission_values.get("can_access_orders", False),
                can_access_forms=permission_values.get("can_access_forms", False),
                is_admin=permission_values.get("is_admin", False),
                role_id=role_id_value,
            )

            success_message = (
                f"Saved pending access for {email}. Permissions will activate after sign-up."
            )
            return _company_edit_redirect(
                company_id=company_id,
                success=success_message,
            )
    else:
        try:
            parsed_user_id = int(user_identifier)
        except (TypeError, ValueError):
            return await _assign_error(
                "Select both a user and a company.", status.HTTP_400_BAD_REQUEST
            )

    assign_form_state["user_id"] = parsed_user_id
    if not assign_form_state.get("user_value"):
        assign_form_state["user_value"] = user_identifier

    if user_record is None and parsed_user_id is not None:
        user_record = await user_repo.get_user_by_id(parsed_user_id)
    company_record = await company_repo.get_company_by_id(company_id)
    if not user_record or not company_record:
        return await _assign_error(
            "User or company not found.", status.HTTP_404_NOT_FOUND
        )

    if existing_assignment is None and parsed_user_id is not None:
        existing_assignment = await user_company_repo.get_user_company(
            parsed_user_id, company_id
        )

    try:
        staff_permission = (
            int(staff_permission_raw) if staff_permission_raw is not None else 0
        )
    except (TypeError, ValueError):
        return await _assign_error(
            "Select a valid staff permission level.", status.HTTP_400_BAD_REQUEST
        )
    if staff_permission < 0:
        staff_permission = 0
    if staff_permission > 3:
        staff_permission = 3
    assign_form_state["staff_permission"] = staff_permission

    permission_values: dict[str, bool] = {}
    for column in _COMPANY_PERMISSION_COLUMNS:
        field = column.get("field")
        if not field:
            continue
        if field in form_keys:
            permission_values[field] = _parse_bool(form.get(field))
        elif existing_assignment is not None:
            permission_values[field] = bool(existing_assignment.get(field, False))
        else:
            permission_values[field] = False
        assign_form_state[field] = permission_values[field]

    if "can_manage_staff" in form_keys:
        can_manage_staff = _parse_bool(form.get("can_manage_staff"))
    elif existing_assignment is not None:
        can_manage_staff = bool(existing_assignment.get("can_manage_staff", False))
    else:
        can_manage_staff = False
    assign_form_state["can_manage_staff"] = can_manage_staff

    assign_kwargs: dict[str, Any] = {
        "user_id": parsed_user_id,
        "company_id": company_id,
        "staff_permission": staff_permission,
        "can_manage_staff": can_manage_staff,
    }
    for field, value in permission_values.items():
        assign_kwargs[field] = value

    await user_company_repo.assign_user_to_company(**assign_kwargs)

    if staff_record and staff_record.get("id") is not None:
        try:
            staff_id_int = int(staff_record.get("id"))
        except (TypeError, ValueError):
            staff_id_int = None
        if staff_id_int is not None:
            await pending_staff_access_repo.delete_assignment(
                staff_id=staff_id_int, company_id=company_id
            )

    if role_raw:
        try:
            role_id = int(role_raw)
        except (TypeError, ValueError):
            return await _assign_error(
                "Select a valid role for the membership.",
                status.HTTP_400_BAD_REQUEST,
            )
        assign_form_state["role_id"] = role_id
        role_record = await role_repo.get_role_by_id(role_id)
        if not role_record:
            return await _assign_error(
                "Selected role could not be found.",
                status.HTTP_404_NOT_FOUND,
            )
        membership = await membership_repo.get_membership_by_company_user(
            company_id, parsed_user_id
        )
        if membership:
            membership_id = membership.get("id")
            if membership_id is not None and membership.get("role_id") != role_id:
                await membership_repo.update_membership(int(membership_id), role_id=role_id)

    return _company_edit_redirect(
        company_id=company_id,
        success=(
            f"Updated access for {user_record.get('email')} at {company_record.get('name')}"
        ),
    )


@app.post("/admin/companies/{company_id}", response_class=HTMLResponse)
async def admin_update_company(company_id: int, request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    name = str(form.get("name", "")).strip()
    syncro_company_raw = str(form.get("syncroCompanyId", "")).strip()
    tactical_client_raw = str(form.get("tacticalClientId", "")).strip()
    xero_id_raw = str(form.get("xeroId", "")).strip()
    is_vip = _parse_bool(form.get("isVip"))
    raw_email_domains = form.get("emailDomains")
    email_domains_text = str(raw_email_domains) if raw_email_domains is not None else ""
    form_values = {
        "name": name,
        "syncro_company_id": syncro_company_raw,
        "tacticalrmm_client_id": tactical_client_raw,
        "xero_id": xero_id_raw,
        "email_domains": email_domains_text,
        "is_vip": is_vip,
    }
    existing = await company_repo.get_company_by_id(company_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    try:
        email_domains = company_domains.parse_email_domain_text(email_domains_text)
    except company_domains.EmailDomainError as exc:
        return await _render_company_edit_page(
            request,
            current_user,
            company_id=company_id,
            form_values=form_values,
            error_message=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if not name:
        return await _render_company_edit_page(
            request,
            current_user,
            company_id=company_id,
            form_values=form_values,
            error_message="Enter a company name.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    syncro_company_id = syncro_company_raw or None
    tactical_client_id = tactical_client_raw or None
    xero_id = xero_id_raw or None
    updates: dict[str, Any] = {
        "name": name,
        "is_vip": 1 if is_vip else 0,
        "syncro_company_id": syncro_company_id,
        "tacticalrmm_client_id": tactical_client_id,
        "xero_id": xero_id,
        "email_domains": email_domains,
    }
    try:
        await company_repo.update_company(company_id, **updates)
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to update company", company_id=company_id, error=str(exc))
        return await _render_company_edit_page(
            request,
            current_user,
            company_id=company_id,
            form_values=form_values,
            error_message="Unable to update company. Please try again.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return _company_edit_redirect(
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
    await staff_access_service.apply_pending_access_for_user(created_user)
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
    await staff_access_service.apply_pending_access_for_user(created_user)
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


@app.post("/admin/companies/assignment/{company_id}/{staff_id}/pending/remove")
async def admin_remove_pending_company_assignment(
    company_id: int, staff_id: int, request: Request
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    pending_assignment = await pending_staff_access_repo.get_assignment(
        staff_id=staff_id, company_id=company_id
    )
    if not pending_assignment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pending staff access not found",
        )

    await pending_staff_access_repo.delete_assignment(
        staff_id=staff_id, company_id=company_id
    )

    await audit_service.log_action(
        action="pending_staff_access.removed",
        user_id=current_user.get("id"),
        entity_type="pending_staff_access",
        entity_id=staff_id,
        previous_value=pending_assignment,
        new_value=None,
        request=request,
    )

    return JSONResponse({"success": True})


@app.post("/admin/companies/assignment/{company_id}/{user_id}/remove")
async def admin_remove_company_assignment(company_id: int, user_id: int, request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    await user_company_repo.remove_assignment(user_id=user_id, company_id=company_id)
    return JSONResponse({"success": True})


@app.post("/admin/companies/{company_id}/billing-contacts/add")
async def admin_add_billing_contact(company_id: int, request: Request):
    """Add a staff member as a billing contact for a company."""
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    
    payload = await request.json()
    staff_id = payload.get("staff_id") or payload.get("staffId")
    
    if not staff_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="staff_id required")
    
    try:
        staff_id_int = int(staff_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid staff_id")
    
    # Verify company exists
    company = await company_repo.get_company_by_id(company_id)
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    
    # Verify staff exists and belongs to the company
    staff = await staff_repo.get_staff_by_id(staff_id_int)
    if not staff:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Staff member not found"
        )
    if staff.get("company_id") != company_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Staff member must belong to the company"
        )
    
    contact = await billing_contacts_repo.add_billing_contact(company_id, staff_id_int)
    return JSONResponse({
        "success": True,
        "contact": {
            "staff_id": contact.get("staff_id"),
            "email": contact.get("email"),
            "first_name": contact.get("first_name"),
            "last_name": contact.get("last_name"),
        },
    })


@app.post("/admin/companies/{company_id}/billing-contacts/{staff_id}/remove")
async def admin_remove_billing_contact(company_id: int, staff_id: int, request: Request):
    """Remove a staff member as a billing contact for a company."""
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    
    await billing_contacts_repo.remove_billing_contact(company_id, staff_id)
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
    module = await _load_syncro_module()
    if not module or not module.get("enabled"):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Syncro module is disabled")
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


@app.post("/admin/syncro/import-companies")
async def import_syncro_companies(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    module = await _load_syncro_module()
    if not module or not module.get("enabled"):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Syncro module is disabled")
    log_info(
        "Syncro company import admin request received",
        user_id=current_user.get("id"),
        request_path=str(request.url),
    )
    task_id = uuid4().hex

    def _on_success(summary: company_importer.CompanyImportSummary) -> None:
        summary_data = summary.as_dict()
        log_info(
            "Syncro company import background task completed",
            task_id=task_id,
            fetched=summary_data.get("fetched", 0),
            created=summary_data.get("created", 0),
            updated=summary_data.get("updated", 0),
            skipped=summary_data.get("skipped", 0),
        )

    async def _on_error(exc: Exception) -> None:
        log_error(
            "Syncro company import background task failed",
            task_id=task_id,
            error=str(exc),
        )

    background_tasks.queue_background_task(
        lambda: company_importer.import_all_companies(),
        task_id=task_id,
        description="syncro-company-import",
        on_complete=_on_success,
        on_error=_on_error,
    )

    log_info(
        "Syncro company import queued",
        task_id=task_id,
        user_id=current_user.get("id"),
        request_path=str(request.url),
    )

    if _request_prefers_json(request):
        return JSONResponse(
            {
                "status": "queued",
                "taskId": task_id,
                "message": "Syncro company import queued.",
            },
            status_code=status.HTTP_202_ACCEPTED,
        )
    message = f"Syncro company import queued. Task ID: {task_id[:8]}"
    redirect_url = str(request.url_for("admin_modules_page"))
    if message:
        redirect_url = f"{redirect_url}?{urlencode({'success': message})}"
    redirect_url = f"{redirect_url}#module-syncro"
    return RedirectResponse(redirect_url, status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/syncro/import-tickets")
async def import_syncro_tickets(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    module = await _load_syncro_module()
    if not module or not module.get("enabled"):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Syncro module is disabled")
    payload = await request.json()
    try:
        import_request = SyncroTicketImportRequest.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.errors()) from exc
    log_info(
        "Syncro ticket import admin request received",
        user_id=current_user.get("id"),
        mode=import_request.mode.value,
        ticket_id=import_request.ticket_id,
        start_id=import_request.start_id,
        end_id=import_request.end_id,
        request_path=str(request.url),
    )
    try:
        summary = await ticket_importer.import_from_request(
            mode=import_request.mode.value,
            ticket_id=import_request.ticket_id,
            start_id=import_request.start_id,
            end_id=import_request.end_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return JSONResponse(summary.as_dict())


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
    permissions_raw = form.get("permissions")
    permissions_text = str(permissions_raw).strip() if permissions_raw is not None else ""
    parsed_permissions, permission_errors = _parse_permission_lines(permissions_text)
    allowed_ips_raw = form.get("allowed_ips")
    allowed_ips_text = str(allowed_ips_raw).strip() if allowed_ips_raw is not None else ""
    parsed_ip_restrictions, ip_errors = _parse_ip_restriction_lines(allowed_ips_text)
    errors: list[str] = []
    if expiry_raw and expiry_date is None:
        errors.append("Enter an expiry date in YYYY-MM-DD format.")
    errors.extend(permission_errors)
    errors.extend(ip_errors)
    is_enabled = True
    if "is_enabled_present" in form:
        is_enabled = str(form.get("is_enabled")) == "1"
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
            permissions=parsed_permissions,
            ip_restrictions=parsed_ip_restrictions,
            is_enabled=is_enabled,
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
            "permissions": parsed_permissions,
            "allowed_ips": parsed_ip_restrictions,
            "is_enabled": is_enabled,
        },
        request=request,
    )
    display_permissions, permissions_text_value, endpoint_summary = _format_api_key_permissions(
        row.get("permissions")
    )
    display_ip_restrictions, allowed_ips_text_value, ip_summary = _format_api_key_ip_restrictions(
        row.get("ip_restrictions")
    )
    if endpoint_summary and ip_summary:
        access_summary = f"{endpoint_summary} • {ip_summary}"
    elif endpoint_summary:
        access_summary = endpoint_summary
    else:
        access_summary = ip_summary
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
        "permissions": display_permissions,
        "permissions_text": permissions_text_value,
        "ip_restrictions": display_ip_restrictions,
        "ip_restrictions_text": allowed_ips_text_value,
        "access_summary": access_summary,
        "ip_summary": ip_summary,
        "endpoint_summary": endpoint_summary,
        "is_enabled": bool(row.get("is_enabled", True)),
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


@app.post("/admin/api-keys/update", response_class=HTMLResponse)
async def admin_update_api_key_page(request: Request):
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
        errors.append("Invalid API key identifier supplied for update.")
        return await _render_api_keys_dashboard(
            request,
            current_user,
            **filters,
            status_message=None,
            errors=errors,
        )

    existing = await api_key_repo.get_api_key_with_usage(api_key_id)
    if not existing:
        errors.append("API key not found or no longer available.")
        return await _render_api_keys_dashboard(
            request,
            current_user,
            **filters,
            status_message=None,
            errors=errors,
        )

    description_raw = form.get("description")
    description_text = str(description_raw).strip() if description_raw is not None else ""
    new_description = description_text or None

    expiry_raw = form.get("expiry_date")
    expiry_date = _parse_input_date(expiry_raw) if expiry_raw else None
    if expiry_raw and expiry_date is None:
        errors.append("Enter a valid expiry date in YYYY-MM-DD format.")

    permissions_raw = form.get("permissions")
    permissions_text = str(permissions_raw).strip() if permissions_raw is not None else ""
    parsed_permissions, permission_errors = _parse_permission_lines(permissions_text)
    errors.extend(permission_errors)

    is_enabled_argument: bool | None = None
    if "is_enabled_present" in form:
        is_enabled_argument = str(form.get("is_enabled")) == "1"

    if errors:
        return await _render_api_keys_dashboard(
            request,
            current_user,
            **filters,
            status_message=None,
            errors=errors,
        )

    update_kwargs: dict[str, Any] = {
        "description": new_description,
        "expiry_date": expiry_date,
        "permissions": parsed_permissions,
    }
    if is_enabled_argument is not None:
        update_kwargs["is_enabled"] = is_enabled_argument

    try:
        updated = await api_key_repo.update_api_key(
            api_key_id,
            **update_kwargs,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error(
            "Failed to update API key from admin form",
            api_key_id=api_key_id,
            error=str(exc),
        )
        errors.append("Unable to save API key changes. Please try again.")
        return await _render_api_keys_dashboard(
            request,
            current_user,
            **filters,
            status_message=None,
            errors=errors,
        )

    await audit_service.log_action(
        action="api_keys.update",
        user_id=current_user.get("id"),
        entity_type="api_key",
        entity_id=api_key_id,
        previous_value={
            "description": existing.get("description"),
            "expiry_date": existing.get("expiry_date").isoformat()
            if isinstance(existing.get("expiry_date"), date)
            else None,
            "permissions": existing.get("permissions", []),
            "is_enabled": bool(existing.get("is_enabled", True)),
        },
        new_value={
            "description": updated.get("description"),
            "expiry_date": updated.get("expiry_date").isoformat()
            if isinstance(updated.get("expiry_date"), date)
            else None,
            "permissions": updated.get("permissions", []),
            "is_enabled": bool(updated.get("is_enabled", True)),
        },
        request=request,
    )

    status_message = "API key changes saved."
    return await _render_api_keys_dashboard(
        request,
        current_user,
        **filters,
        status_message=status_message,
        errors=None,
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
    permissions_raw = form.get("permissions")
    permissions_text = str(permissions_raw).strip() if permissions_raw is not None else ""
    parsed_permissions, permission_errors = _parse_permission_lines(permissions_text)
    allowed_ips_raw = form.get("allowed_ips")
    allowed_ips_text = str(allowed_ips_raw).strip() if allowed_ips_raw is not None else ""
    parsed_ip_restrictions, ip_errors = _parse_ip_restriction_lines(allowed_ips_text)
    retire_previous = _parse_bool(form.get("retire_previous"), default=True)
    errors.extend(permission_errors)
    errors.extend(ip_errors)
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
    permissions = parsed_permissions if permissions_raw is not None else existing.get("permissions", [])
    ip_restrictions = (
        parsed_ip_restrictions
        if allowed_ips_raw is not None
        else [entry.get("cidr") for entry in existing.get("ip_restrictions", [])]
    )
    try:
        raw_key, row = await api_key_repo.create_api_key(
            description=final_description,
            expiry_date=final_expiry,
            permissions=permissions,
            ip_restrictions=ip_restrictions,
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
            "permissions": permissions,
            "allowed_ips": ip_restrictions,
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
    display_permissions, permissions_text_value, endpoint_summary = _format_api_key_permissions(
        row.get("permissions")
    )
    display_ip_restrictions, allowed_ips_text_value, ip_summary = _format_api_key_ip_restrictions(
        row.get("ip_restrictions")
    )
    if endpoint_summary and ip_summary:
        access_summary = f"{endpoint_summary} • {ip_summary}"
    elif endpoint_summary:
        access_summary = endpoint_summary
    else:
        access_summary = ip_summary
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
        "permissions": display_permissions,
        "permissions_text": permissions_text_value,
        "ip_restrictions": display_ip_restrictions,
        "ip_restrictions_text": allowed_ips_text_value,
        "access_summary": access_summary,
        "ip_summary": ip_summary,
        "endpoint_summary": endpoint_summary,
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


@app.get("/admin/message-templates", response_class=HTMLResponse)
async def admin_message_templates(
    request: Request,
    search: str | None = Query(default=None),
    content_type: str | None = Query(default=None),
    success: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    search_value = (search or "").strip()
    if len(search_value) > 120:
        search_value = search_value[:120]
    content_type_value = (content_type or "").strip().lower()
    if content_type_value not in {value for value, _ in _MESSAGE_TEMPLATE_CONTENT_TYPES}:
        content_type_value = ""

    try:
        template_records = await message_templates_service.list_templates(
            search=search_value or None,
            content_type=content_type_value or None,
            limit=500,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to load message templates", error=str(exc))
        template_records = []

    templates_payload: list[dict[str, Any]] = []
    for record in template_records:
        prepared = dict(record)
        prepared["created_at_iso"] = _to_iso(record.get("created_at"))
        prepared["updated_at_iso"] = _to_iso(record.get("updated_at"))
        templates_payload.append(prepared)

    content_type_options = [
        {"value": value, "label": label}
        for value, label in _MESSAGE_TEMPLATE_CONTENT_TYPES
    ]

    extra = {
        "title": "Message templates",
        "templates": templates_payload,
        "filters": {
            "search": search_value,
            "content_type": content_type_value or "",
        },
        "content_type_options": content_type_options,
        "success_message": _sanitize_message(success),
        "error_message": _sanitize_message(error),
    }

    return await _render_template("admin/message_templates.html", request, current_user, extra=extra)


@app.get("/admin/message-templates/new", response_class=HTMLResponse)
async def admin_message_templates_new(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    content_type_options = [
        {"value": value, "label": label}
        for value, label in _MESSAGE_TEMPLATE_CONTENT_TYPES
    ]

    extra = {
        "title": "New message template",
        "page_title": "New message template",
        "form_heading": "New template",
        "submit_label": "Save template",
        "show_reset_button": True,
        "template": {},
        "content_type_options": content_type_options,
        "default_content_type": "text/plain",
    }

    return await _render_template("admin/message_template_form.html", request, current_user, extra=extra)


@app.get("/admin/message-templates/{template_id}/edit", response_class=HTMLResponse)
async def admin_message_templates_edit(request: Request, template_id: int):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    template_record = await message_templates_service.get_template(template_id)
    if not template_record:
        message = "Template not found."
        encoded = urlencode({"error": message})
        return RedirectResponse(url=f"/admin/message-templates?{encoded}", status_code=status.HTTP_303_SEE_OTHER)

    content_type_options = [
        {"value": value, "label": label}
        for value, label in _MESSAGE_TEMPLATE_CONTENT_TYPES
    ]

    extra = {
        "title": f"Edit {template_record.get('name') or 'template'}",
        "page_title": "Edit message template",
        "form_heading": "Edit template",
        "submit_label": "Update template",
        "show_reset_button": False,
        "template": template_record,
        "content_type_options": content_type_options,
        "default_content_type": template_record.get("content_type") or "text/plain",
    }

    return await _render_template("admin/message_template_form.html", request, current_user, extra=extra)


@app.get("/admin/automation", response_class=HTMLResponse)
async def admin_automation(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    tasks = await scheduled_tasks_repo.list_tasks()
    companies = await company_repo.list_companies()

    company_lookup: dict[int, str] = {}
    for company in companies:
        try:
            company_id = int(company.get("id")) if company.get("id") is not None else None
        except (TypeError, ValueError):
            company_id = None
        if company_id is None:
            continue
        company_lookup[company_id] = str(company.get("name") or f"Company #{company_id}")

    prepared_tasks: list[dict[str, Any]] = []
    global_tasks: list[dict[str, Any]] = []
    missing_company_ids: set[int] = set()
    for task in tasks:
        serialised_task = _serialise_mapping(task)
        serialised_task["last_run_iso"] = _to_iso(task.get("last_run_at"))
        company_id = task.get("company_id")
        company_name: str
        if company_id is None:
            company_name = "All companies"
        else:
            try:
                company_key = int(company_id)
            except (TypeError, ValueError):
                company_key = None
            if company_key is None:
                company_name = "All companies"
            else:
                company_name = company_lookup.get(company_key)
                if not company_name:
                    company_name = f"Company #{company_key}"
                    missing_company_ids.add(company_key)
        serialised_task["company_name"] = company_name
        prepared_tasks.append(serialised_task)
        if company_id is None or company_name.lower() == "all companies":
            global_tasks.append(serialised_task)
    command_options = [
        {"value": "sync_staff", "label": "Sync staff directory"},
        {"value": "sync_o365", "label": "Sync Microsoft 365 licenses"},
        {"value": "sync_to_xero", "label": "Sync to Xero"},
        {"value": "sync_to_xero_auto_send", "label": "Sync to Xero (Auto Send)"},
        {"value": "create_scheduled_ticket", "label": "Create scheduled ticket"},
        {"value": "sync_recordings", "label": "Sync call recordings"},
        {"value": "queue_transcriptions", "label": "Queue transcriptions"},
        {"value": "process_transcription", "label": "Process transcription"},
    ]
    existing_commands = {task.get("command") for task in tasks if task.get("command")}
    for command in sorted(existing_commands):
        if command and command not in {option["value"] for option in command_options}:
            command_options.append({"value": str(command), "label": str(command)})
    company_options = [
        {"value": "", "label": "All companies"},
    ]
    for company_id, company_name in sorted(company_lookup.items(), key=lambda item: item[1].lower()):
        company_options.append({"value": str(company_id), "label": company_name})
    for company_id in sorted(missing_company_ids):
        company_options.append({"value": str(company_id), "label": f"Company #{company_id}"})

    extra = {
        "title": "System Automation",
        "tasks": prepared_tasks,
        "global_tasks": global_tasks,
        "command_options": command_options,
        "company_options": company_options,
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


@app.get("/admin/change-log", response_class=HTMLResponse)
async def admin_change_log(
    request: Request,
    change_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    limit = max(1, min(limit, 500))
    available_types = await change_log_repo.list_change_types()

    selected_type: str | None = None
    raw_change_type = (change_type or "").strip()
    if raw_change_type:
        lowered = raw_change_type.lower()
        for candidate in available_types:
            if candidate.lower() == lowered:
                selected_type = candidate
                break

    entries = await change_log_repo.list_change_log_entries(
        change_type=selected_type,
        limit=limit,
    )
    for entry in entries:
        entry["occurred_at_iso"] = _to_iso(entry.get("occurred_at_utc"))

    extra = {
        "title": "Change log",
        "change_entries": entries,
        "change_types": available_types,
        "filters": {
            "change_type": raw_change_type,
            "selected_change_type": selected_type.lower() if selected_type else "",
            "limit": limit,
        },
    }
    return await _render_template("admin/change_log.html", request, current_user, extra=extra)


@app.get("/admin/forms", response_class=HTMLResponse)
async def admin_forms_page(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    forms_task = asyncio.create_task(forms_repo.list_forms())
    companies_task = asyncio.create_task(company_repo.list_companies())
    assignments_task = asyncio.create_task(user_company_repo.list_assignments())
    permissions_task = asyncio.create_task(forms_repo.list_permission_entries())

    forms, companies, assignments, permission_entries = await asyncio.gather(
        forms_task,
        companies_task,
        assignments_task,
        permissions_task,
    )

    company_lookup: dict[int, dict[str, Any]] = {}
    for company in companies:
        company_id = int(company.get("id")) if company.get("id") is not None else None
        if company_id is None:
            continue
        company_lookup[company_id] = {
            "id": company_id,
            "name": company.get("name", "Unnamed company"),
            "users": [],
        }

    seen_assignments: set[tuple[int, int]] = set()
    for record in assignments:
        company_id = record.get("company_id")
        user_id = record.get("user_id")
        if company_id is None or user_id is None:
            continue
        company_entry = company_lookup.get(int(company_id))
        if not company_entry:
            continue
        key = (int(company_id), int(user_id))
        if key in seen_assignments:
            continue
        seen_assignments.add(key)

        first_name = (record.get("first_name") or "").strip()
        last_name = (record.get("last_name") or "").strip()
        full_name_parts = [part for part in (first_name, last_name) if part]
        full_name = " ".join(full_name_parts)
        email = (record.get("email") or "").strip()
        label: str
        if full_name and email:
            label = f"{full_name} ({email})"
        elif full_name:
            label = full_name
        elif email:
            label = email
        else:
            label = f"User {user_id}"

        company_entry["users"].append(
            {
                "id": int(user_id),
                "label": label,
                "email": email,
                "name": full_name,
            }
        )

    for company in company_lookup.values():
        company["users"].sort(key=lambda item: item.get("label", "").lower())

    company_user_options = sorted(company_lookup.values(), key=lambda item: item.get("name", ""))

    permissions_map: dict[int, dict[int, set[int]]] = {}
    for entry in permission_entries:
        form_id = entry.get("form_id")
        company_id = entry.get("company_id")
        user_id = entry.get("user_id")
        if form_id is None or company_id is None or user_id is None:
            continue
        form_map = permissions_map.setdefault(int(form_id), {})
        user_set = form_map.setdefault(int(company_id), set())
        user_set.add(int(user_id))

    permissions_json: dict[str, dict[str, list[int]]] = {}
    for form_id, company_map in permissions_map.items():
        json_companies: dict[str, list[int]] = {}
        for company_id, user_ids in company_map.items():
            json_companies[str(company_id)] = sorted(user_ids)
        permissions_json[str(form_id)] = json_companies

    form_assignment_summary: dict[int, dict[str, int]] = {}
    for form in forms:
        form_id = form.get("id")
        if form_id is None:
            continue
        company_map = permissions_map.get(int(form_id), {})
        company_count = 0
        user_count = 0
        for users in company_map.values():
            if users:
                company_count += 1
                user_count += len(users)
        form_assignment_summary[int(form_id)] = {
            "companies": company_count,
            "users": user_count,
        }

    extra = {
        "title": "Forms admin",
        "forms": forms,
        "opnform_base_url": _opnform_base_url(),
        "company_user_options": company_user_options,
        "form_permissions_map": permissions_json,
        "form_assignment_summary": form_assignment_summary,
    }
    return await _render_template("admin/forms.html", request, current_user, extra=extra)


@app.get("/admin/tag-exclusions", response_class=HTMLResponse)
async def admin_tag_exclusions_page(
    request: Request,
    success: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    
    context = await _build_base_context(
        request,
        current_user,
        extra={
            "success_message": _sanitize_message(success),
            "error_message": _sanitize_message(error),
        },
    )
    return templates.TemplateResponse(
        "admin/tag_exclusions.html",
        context,
    )


@app.get("/admin/call-recordings", response_class=HTMLResponse)
async def admin_call_recordings_page(request: Request):
    """Admin page for viewing call recordings and transcriptions (super admin only)."""
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    
    context = await _build_base_context(
        request,
        current_user,
        extra={
            "title": "Call Recordings & Transcriptions",
        },
    )
    return templates.TemplateResponse(
        "admin/call_recordings.html",
        context,
    )


@app.get("/admin/knowledge-base", response_class=HTMLResponse)
async def admin_knowledge_base_page(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    access_context = await knowledge_base_service.build_access_context(current_user)
    articles = await knowledge_base_service.list_articles_for_context(
        access_context,
        include_unpublished=True,
        include_permissions=True,
    )
    serialised_articles = jsonable_encoder(articles)
    extra = {
        "title": "Knowledge base admin",
        "kb_articles": serialised_articles,
    }
    return await _render_template("admin/knowledge_base.html", request, current_user, extra=extra)


async def _prepare_kb_editor_options() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    users_task = asyncio.create_task(user_repo.list_users())
    companies_task = asyncio.create_task(company_repo.list_companies())
    users, companies = await asyncio.gather(users_task, companies_task)

    user_options: list[dict[str, Any]] = []
    for user in users:
        user_id = user.get("id")
        if user_id is None:
            continue
        try:
            user_id_int = int(user_id)
        except (TypeError, ValueError):
            continue
        first_name = (user.get("first_name") or "").strip()
        last_name = (user.get("last_name") or "").strip()
        name_parts = [part for part in (first_name, last_name) if part]
        full_name = " ".join(name_parts)
        email = (user.get("email") or "").strip()
        if full_name and email:
            label = f"{full_name} ({email})"
        elif full_name:
            label = full_name
        elif email:
            label = email
        else:
            label = f"User {user_id_int}"
        user_options.append({"id": user_id_int, "label": label})

    user_options.sort(key=lambda item: item.get("label", "").lower())

    company_options: list[dict[str, Any]] = []
    for company in companies:
        company_id = company.get("id")
        if company_id is None:
            continue
        try:
            company_id_int = int(company_id)
        except (TypeError, ValueError):
            continue
        name = (company.get("name") or "").strip()
        if not name:
            name = f"Company {company_id_int}"
        company_options.append({"id": company_id_int, "name": name})

    company_options.sort(key=lambda item: item.get("name", "").lower())
    return user_options, company_options


@app.get("/admin/knowledge-base/new", response_class=HTMLResponse)
async def admin_new_knowledge_base_article_page(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    user_options, company_options = await _prepare_kb_editor_options()
    extra = {
        "title": "New knowledge base article",
        "kb_initial_article": None,
        "kb_user_options": user_options,
        "kb_company_options": company_options,
        "kb_form_mode": "create",
        "kb_catalogue_payload": [],
    }
    return await _render_template("admin/knowledge_base_editor.html", request, current_user, extra=extra)


@app.get("/admin/knowledge-base/articles/{slug}", response_class=HTMLResponse)
async def admin_edit_knowledge_base_article_page(request: Request, slug: str):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    access_context = await knowledge_base_service.build_access_context(current_user)
    article = await knowledge_base_service.get_article_by_slug_for_context(
        slug,
        access_context,
        include_unpublished=True,
        include_permissions=True,
    )
    if not article:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")

    user_options, company_options = await _prepare_kb_editor_options()
    serialised_article = jsonable_encoder(article)
    extra = {
        "title": f"Edit knowledge base article · {article.get('title') or article.get('slug')}",
        "kb_initial_article": serialised_article,
        "kb_user_options": user_options,
        "kb_company_options": company_options,
        "kb_form_mode": "edit",
        "kb_catalogue_payload": [{"slug": serialised_article.get("slug")}],
    }
    return await _render_template("admin/knowledge_base_editor.html", request, current_user, extra=extra)


@app.get("/admin/business-continuity-plans", response_class=HTMLResponse)
async def admin_business_continuity_plans_page(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    plans = await bc_plans_repo.list_plans()
    
    # Add ISO datetime strings for client-side rendering
    for plan in plans:
        if plan.get("updated_at"):
            plan["updated_at_iso"] = plan["updated_at"].isoformat() if isinstance(plan["updated_at"], datetime) else str(plan["updated_at"])
        if plan.get("last_reviewed_at"):
            plan["last_reviewed_at_iso"] = plan["last_reviewed_at"].isoformat() if isinstance(plan["last_reviewed_at"], datetime) else str(plan["last_reviewed_at"])
    
    serialised_plans = jsonable_encoder(plans)
    extra = {
        "title": "Business Continuity Plans",
        "bc_plans": serialised_plans,
    }
    return await _render_template("admin/business_continuity_plans.html", request, current_user, extra=extra)


@app.get("/admin/business-continuity-plans/new", response_class=HTMLResponse)
async def admin_new_business_continuity_plan_page(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    users = await user_repo.list_users()
    companies = await company_repo.list_companies()
    
    extra = {
        "title": "New Business Continuity Plan",
        "plan": None,
        "users": jsonable_encoder(users),
        "companies": jsonable_encoder(companies),
    }
    return await _render_template("admin/business_continuity_plan_editor.html", request, current_user, extra=extra)


@app.get("/admin/business-continuity-plans/{plan_id}", response_class=HTMLResponse)
async def admin_edit_business_continuity_plan_page(request: Request, plan_id: int):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    plan = await bc_plans_repo.get_plan_by_id(plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")

    users = await user_repo.list_users()
    companies = await company_repo.list_companies()
    
    serialised_plan = jsonable_encoder(plan)
    extra = {
        "title": f"Edit Plan · {plan.get('title') or f'Plan #{plan_id}'}",
        "plan": serialised_plan,
        "users": jsonable_encoder(users),
        "companies": jsonable_encoder(companies),
    }
    return await _render_template("admin/business_continuity_plan_editor.html", request, current_user, extra=extra)


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


@app.get(
    "/admin/shop/packages",
    response_class=HTMLResponse,
    summary="Manage shop packages",
    tags=["Shop Packages"],
)
async def admin_shop_packages_page(
    request: Request,
    show_archived: bool = Query(False, alias="showArchived"),
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    packages = await shop_packages_service.load_admin_packages(
        include_archived=show_archived,
    )

    extra = {
        "title": "Package admin",
        "packages": packages,
        "show_archived": show_archived,
    }
    return await _render_template("admin/shop_packages.html", request, current_user, extra=extra)


@app.get(
    "/admin/shop/packages/{package_id}",
    response_class=HTMLResponse,
    summary="View shop package detail",
    tags=["Shop Packages"],
)
async def admin_shop_package_detail(request: Request, package_id: int):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    package = await shop_packages_service.get_package_detail(package_id, include_archived=True)
    if not package:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Package not found")

    products = await shop_repo.list_all_products(include_archived=False)

    extra = {
        "title": f"Manage package: {package['name']}",
        "package": package,
        "products": products,
    }
    return await _render_template("admin/shop_package_detail.html", request, current_user, extra=extra)


@app.post(
    "/shop/admin/package",
    status_code=status.HTTP_303_SEE_OTHER,
    summary="Create a shop package",
    tags=["Shop Packages"],
)
async def admin_create_shop_package(
    request: Request,
    name: str = Form(...),
    sku: str = Form(...),
    description: str | None = Form(default=None),
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    cleaned_name = name.strip()
    cleaned_sku = sku.strip()
    cleaned_description = description.strip() if description and description.strip() else None
    if not cleaned_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Package name cannot be empty")
    if not cleaned_sku:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Package SKU cannot be empty")

    try:
        package_id = await shop_repo.create_package(
            sku=cleaned_sku,
            name=cleaned_name,
            description=cleaned_description,
        )
    except aiomysql.IntegrityError as exc:
        if exc.args and exc.args[0] == 1062:
            detail = "A package with that SKU already exists."
        else:
            detail = "Unable to create package."
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc

    log_info(
        "Shop package created",
        package_id=package_id,
        sku=cleaned_sku,
        name=cleaned_name,
        created_by=current_user["id"] if current_user else None,
    )
    return RedirectResponse(url="/admin/shop/packages", status_code=status.HTTP_303_SEE_OTHER)


@app.post(
    "/shop/admin/package/{package_id}/update",
    status_code=status.HTTP_303_SEE_OTHER,
    summary="Update a shop package",
    tags=["Shop Packages"],
)
async def admin_update_shop_package(
    request: Request,
    package_id: int,
    name: str = Form(...),
    sku: str = Form(...),
    description: str | None = Form(default=None),
    archived: str | None = Form(default=None),
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    cleaned_name = name.strip()
    cleaned_sku = sku.strip()
    cleaned_description = description.strip() if description and description.strip() else None
    if not cleaned_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Package name cannot be empty")
    if not cleaned_sku:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Package SKU cannot be empty")

    try:
        updated = await shop_repo.update_package(
            package_id,
            sku=cleaned_sku,
            name=cleaned_name,
            description=cleaned_description,
        )
    except aiomysql.IntegrityError as exc:
        if exc.args and exc.args[0] == 1062:
            detail = "A package with that SKU already exists."
        else:
            detail = "Unable to update package."
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc

    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Package not found")

    archived_flag = bool(archived and archived != "0")
    await shop_repo.set_package_archived(package_id, archived=archived_flag)

    log_info(
        "Shop package updated",
        package_id=package_id,
        sku=cleaned_sku,
        archived=archived_flag,
        updated_by=current_user["id"] if current_user else None,
    )
    return RedirectResponse(
        url=f"/admin/shop/packages/{package_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post(
    "/shop/admin/package/{package_id}/archive",
    status_code=status.HTTP_303_SEE_OTHER,
    summary="Archive or restore a shop package",
    tags=["Shop Packages"],
)
async def admin_archive_shop_package(
    request: Request,
    package_id: int,
    archived: str = Form(...),
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    archived_flag = bool(archived and archived != "0")
    package = await shop_repo.get_package(package_id, include_archived=True)
    if not package:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Package not found")

    await shop_repo.set_package_archived(package_id, archived=archived_flag)

    log_info(
        "Shop package archived" if archived_flag else "Shop package restored",
        package_id=package_id,
        updated_by=current_user["id"] if current_user else None,
    )
    return RedirectResponse(url="/admin/shop/packages", status_code=status.HTTP_303_SEE_OTHER)


@app.post(
    "/shop/admin/package/{package_id}/delete",
    status_code=status.HTTP_303_SEE_OTHER,
    summary="Delete a shop package",
    tags=["Shop Packages"],
)
async def admin_delete_shop_package(request: Request, package_id: int):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    package = await shop_repo.get_package(package_id, include_archived=True)
    if not package:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Package not found")

    deleted = await shop_repo.delete_package(package_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Package not found")

    log_info(
        "Shop package deleted",
        package_id=package_id,
        deleted_by=current_user["id"] if current_user else None,
    )
    return RedirectResponse(url="/admin/shop/packages", status_code=status.HTTP_303_SEE_OTHER)


@app.post(
    "/shop/admin/package/{package_id}/items/add",
    status_code=status.HTTP_303_SEE_OTHER,
    summary="Add a product to a shop package",
    tags=["Shop Packages"],
)
async def admin_add_package_item(
    request: Request,
    package_id: int,
    product_id: str = Form(...),
    quantity: str = Form(...),
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    try:
        product_identifier = int(product_id)
        quantity_value = int(quantity)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid product or quantity")

    if quantity_value <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Quantity must be at least 1")

    product = await shop_repo.get_product_by_id(product_identifier, include_archived=True)
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    await shop_repo.upsert_package_item(
        package_id=package_id,
        product_id=product_identifier,
        quantity=quantity_value,
    )

    log_info(
        "Shop package item added",
        package_id=package_id,
        product_id=product_identifier,
        quantity=quantity_value,
        added_by=current_user["id"] if current_user else None,
    )
    return RedirectResponse(
        url=f"/admin/shop/packages/{package_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post(
    "/shop/admin/package/{package_id}/items/{product_id}/update",
    status_code=status.HTTP_303_SEE_OTHER,
    summary="Update package item quantity",
    tags=["Shop Packages"],
)
async def admin_update_package_item(
    request: Request,
    package_id: int,
    product_id: int,
    quantity: str = Form(...),
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    try:
        quantity_value = int(quantity)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid quantity")

    if quantity_value <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Quantity must be at least 1")

    await shop_repo.upsert_package_item(
        package_id=package_id,
        product_id=product_id,
        quantity=quantity_value,
    )

    log_info(
        "Shop package item updated",
        package_id=package_id,
        product_id=product_id,
        quantity=quantity_value,
        updated_by=current_user["id"] if current_user else None,
    )
    return RedirectResponse(
        url=f"/admin/shop/packages/{package_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post(
    "/shop/admin/package/{package_id}/items/{product_id}/alternates/add",
    status_code=status.HTTP_303_SEE_OTHER,
    summary="Add an alternate product for a package item",
    tags=["Shop Packages"],
)
async def admin_add_package_item_alternate(
    request: Request,
    package_id: int,
    product_id: int,
    alternate_product_id: str = Form(...),
    priority: str = Form("0"),
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    try:
        alternate_id = int(alternate_product_id)
        primary_id = int(product_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid product selection")

    if primary_id == alternate_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Alternate product must differ from the primary product",
        )

    try:
        priority_value = int(priority) if priority is not None else 0
    except (TypeError, ValueError):
        priority_value = 0

    alternate_product = await shop_repo.get_product_by_id(
        alternate_id,
        include_archived=True,
    )
    if not alternate_product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alternate product not found")

    success = await shop_repo.upsert_package_item_alternate(
        package_id=package_id,
        product_id=primary_id,
        alternate_product_id=alternate_id,
        priority=priority_value,
    )
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Package item not found")

    log_info(
        "Shop package alternate assigned",
        package_id=package_id,
        product_id=primary_id,
        alternate_product_id=alternate_id,
        priority=priority_value,
        added_by=current_user["id"] if current_user else None,
    )

    return RedirectResponse(
        url=f"/admin/shop/packages/{package_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post(
    "/shop/admin/package/{package_id}/items/{product_id}/alternates/{alternate_product_id}/remove",
    status_code=status.HTTP_303_SEE_OTHER,
    summary="Remove an alternate product from a package item",
    tags=["Shop Packages"],
)
async def admin_remove_package_item_alternate(
    request: Request,
    package_id: int,
    product_id: int,
    alternate_product_id: int,
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    removed = await shop_repo.remove_package_item_alternate(
        package_id,
        product_id,
        alternate_product_id,
    )
    if not removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alternate product not found")

    log_info(
        "Shop package alternate removed",
        package_id=package_id,
        product_id=product_id,
        alternate_product_id=alternate_product_id,
        removed_by=current_user["id"] if current_user else None,
    )

    return RedirectResponse(
        url=f"/admin/shop/packages/{package_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post(
    "/shop/admin/package/{package_id}/items/{product_id}/remove",
    status_code=status.HTTP_303_SEE_OTHER,
    summary="Remove product from package",
    tags=["Shop Packages"],
)
async def admin_remove_package_item(
    request: Request,
    package_id: int,
    product_id: int,
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    await shop_repo.remove_package_item(package_id, product_id)

    log_info(
        "Shop package item removed",
        package_id=package_id,
        product_id=product_id,
        removed_by=current_user["id"] if current_user else None,
    )
    return RedirectResponse(
        url=f"/admin/shop/packages/{package_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.get("/admin/shop", response_class=HTMLResponse)
async def admin_shop_page(
    request: Request,
    show_archived: bool = Query(False, alias="showArchived"),
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    categories_task = asyncio.create_task(shop_repo.list_all_categories_flat())
    products_task = asyncio.create_task(
        shop_repo.list_products(
            shop_repo.ProductFilters(include_archived=show_archived)
        )
    )
    restrictions_task = asyncio.create_task(shop_repo.list_product_restrictions())
    companies_task = asyncio.create_task(company_repo.list_companies())
    subscription_categories_task = asyncio.create_task(subscription_categories_repo.list_categories())

    categories, products, restrictions, companies, subscription_categories = await asyncio.gather(
        categories_task, products_task, restrictions_task, companies_task, subscription_categories_task
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
        "subscription_categories": subscription_categories,
    }
    return await _render_template("admin/shop.html", request, current_user, extra=extra)


@app.get("/admin/shop/categories", response_class=HTMLResponse)
async def admin_shop_categories_page(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    categories = await shop_repo.list_all_categories_flat()

    extra = {
        "title": "Product categories",
        "categories": categories,
    }
    return await _render_template("admin/shop_categories.html", request, current_user, extra=extra)


@app.get("/admin/shop/products/new", response_class=HTMLResponse)
async def admin_shop_product_create_page(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    categories_task = asyncio.create_task(shop_repo.list_all_categories_flat())
    products_task = asyncio.create_task(
        shop_repo.list_products(shop_repo.ProductFilters(include_archived=False))
    )
    restrictions_task = asyncio.create_task(shop_repo.list_product_restrictions())
    subscription_categories_task = asyncio.create_task(subscription_categories_repo.list_categories())

    categories, products, restrictions, subscription_categories = await asyncio.gather(
        categories_task, products_task, restrictions_task, subscription_categories_task
    )

    restrictions_map: dict[int, list[dict[str, Any]]] = {}
    for restriction in restrictions:
        restrictions_map.setdefault(restriction["product_id"], []).append(restriction)

    extra = {
        "title": "Add product",
        "categories": categories,
        "products": products,
        "product_restrictions": restrictions_map,
        "subscription_categories": subscription_categories,
    }
    return await _render_template(
        "admin/shop_product_create.html", request, current_user, extra=extra
    )


@app.post(
    "/shop/admin/category",
    status_code=status.HTTP_303_SEE_OTHER,
    summary="Create a shop category",
    tags=["Shop"],
)
async def admin_create_shop_category(
    request: Request,
    name: str = Form(...),
    parent_id: str = Form(""),
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    cleaned_name = name.strip()
    if not cleaned_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Category name cannot be empty")

    # Convert empty string to None for parent_id
    parsed_parent_id: int | None = None
    if parent_id and parent_id.strip():
        try:
            parsed_parent_id = int(parent_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid parent category")

    try:
        category_id = await shop_repo.create_category(cleaned_name, parent_id=parsed_parent_id)
    except aiomysql.IntegrityError as exc:
        if exc.args and exc.args[0] == 1062:
            detail = "A category with that name already exists."
        else:
            detail = "Unable to create category."
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc

    log_info(
        "Shop category created",
        category_id=category_id,
        name=cleaned_name,
        parent_id=parsed_parent_id,
        created_by=current_user["id"] if current_user else None,
    )
    return RedirectResponse(url="/admin/shop/categories", status_code=status.HTTP_303_SEE_OTHER)


@app.post(
    "/shop/admin/category/{category_id}/delete",
    status_code=status.HTTP_303_SEE_OTHER,
    summary="Delete a shop category",
    tags=["Shop"],
)
async def admin_delete_shop_category(request: Request, category_id: int):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    category = await shop_repo.get_category(category_id)
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")

    deleted = await shop_repo.delete_category(category_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")

    log_info(
        "Shop category deleted",
        category_id=category_id,
        deleted_by=current_user["id"] if current_user else None,
    )
    return RedirectResponse(url="/admin/shop/categories", status_code=status.HTTP_303_SEE_OTHER)


@app.post(
    "/shop/admin/category/{category_id}/update",
    status_code=status.HTTP_303_SEE_OTHER,
    summary="Update a shop category",
    tags=["Shop"],
)
async def admin_update_shop_category(
    request: Request,
    category_id: int,
    name: str = Form(...),
    parent_id: str = Form(""),
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    category = await shop_repo.get_category(category_id)
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")

    cleaned_name = name.strip()
    if not cleaned_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Category name cannot be empty")

    # Convert empty string to None for parent_id
    parsed_parent_id: int | None = None
    if parent_id and parent_id.strip():
        try:
            parsed_parent_id = int(parent_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid parent category")

    # Prevent setting itself as parent or creating circular reference
    if parsed_parent_id == category_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A category cannot be its own parent")
    
    # Check if the new parent is a descendant of the category (which would create a circular reference)
    if parsed_parent_id is not None:
        all_categories = await shop_repo.list_all_categories_flat()
        
        # Build a map of category children
        children_map: dict[int, list[int]] = {}
        for cat in all_categories:
            parent = cat.get("parent_id")
            if parent is not None:
                children_map.setdefault(parent, []).append(cat["id"])
        
        def get_all_descendants(cat_id: int, visited: set[int]) -> set[int]:
            """Get all descendants of a category."""
            # Prevent infinite loops by skipping already-visited nodes
            if cat_id in visited:
                return set()
            
            visited.add(cat_id)
            descendants = set()
            
            for child_id in children_map.get(cat_id, []):
                descendants.add(child_id)
                descendants.update(get_all_descendants(child_id, visited))
            
            return descendants
        
        # Check if the new parent is in the descendants of the current category
        descendants = get_all_descendants(category_id, set())
        if parsed_parent_id in descendants:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot move a category into one of its own descendants"
            )

    try:
        updated = await shop_repo.update_category(
            category_id,
            cleaned_name,
            parent_id=parsed_parent_id,
            display_order=category.get("display_order", 0),
        )
        if not updated:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
    except aiomysql.IntegrityError as exc:
        if exc.args and exc.args[0] == 1062:
            detail = "A category with that name already exists."
        else:
            detail = "Unable to update category."
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc

    log_info(
        "Shop category updated",
        category_id=category_id,
        name=cleaned_name,
        parent_id=parsed_parent_id,
        updated_by=current_user["id"] if current_user else None,
    )
    return RedirectResponse(url="/admin/shop/categories", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/admin/shop/subscription-categories", response_class=HTMLResponse)
async def admin_shop_subscription_categories_page(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    categories = await subscription_categories_repo.list_categories()

    extra = {
        "title": "Subscription categories",
        "categories": categories,
    }
    return await _render_template("admin/shop_subscription_categories.html", request, current_user, extra=extra)


@app.post(
    "/shop/admin/subscription-category",
    status_code=status.HTTP_303_SEE_OTHER,
    summary="Create a subscription category",
    tags=["Shop"],
)
async def admin_create_subscription_category(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    cleaned_name = name.strip()
    if not cleaned_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Category name cannot be empty")

    cleaned_description = description.strip() if description else None

    try:
        await subscription_categories_repo.create_category(cleaned_name, description=cleaned_description)
    except aiomysql.IntegrityError as exc:
        if exc.args and exc.args[0] == 1062:
            detail = "A subscription category with that name already exists."
        else:
            detail = "Unable to create subscription category."
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc

    log_info(
        "Subscription category created",
        name=cleaned_name,
        description=cleaned_description,
        created_by=current_user["id"] if current_user else None,
    )
    return RedirectResponse(url="/admin/shop/subscription-categories", status_code=status.HTTP_303_SEE_OTHER)


@app.post(
    "/shop/admin/subscription-category/{category_id}/delete",
    status_code=status.HTTP_303_SEE_OTHER,
    summary="Delete a subscription category",
    tags=["Shop"],
)
async def admin_delete_subscription_category(request: Request, category_id: int):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    category = await subscription_categories_repo.get_category(category_id)
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription category not found")

    await subscription_categories_repo.delete_category(category_id)

    log_info(
        "Subscription category deleted",
        category_id=category_id,
        deleted_by=current_user["id"] if current_user else None,
    )
    return RedirectResponse(url="/admin/shop/subscription-categories", status_code=status.HTTP_303_SEE_OTHER)


@app.post(
    "/shop/admin/subscription-category/{category_id}/update",
    status_code=status.HTTP_303_SEE_OTHER,
    summary="Update a subscription category",
    tags=["Shop"],
)
async def admin_update_subscription_category(
    request: Request,
    category_id: int,
    name: str = Form(...),
    description: str = Form(""),
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    category = await subscription_categories_repo.get_category(category_id)
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription category not found")

    cleaned_name = name.strip()
    if not cleaned_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Category name cannot be empty")

    cleaned_description = description.strip() if description else None

    try:
        await subscription_categories_repo.update_category(
            category_id,
            name=cleaned_name,
            description=cleaned_description,
        )
    except aiomysql.IntegrityError as exc:
        if exc.args and exc.args[0] == 1062:
            detail = "A subscription category with that name already exists."
        else:
            detail = "Unable to update subscription category."
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc

    log_info(
        "Subscription category updated",
        category_id=category_id,
        name=cleaned_name,
        description=cleaned_description,
        updated_by=current_user["id"] if current_user else None,
    )
    return RedirectResponse(url="/admin/shop/subscription-categories", status_code=status.HTTP_303_SEE_OTHER)


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
    description: str | None = Form(default=None),
    price: str = Form(...),
    stock: str = Form(...),
    vip_price: str | None = Form(default=None),
    category_id: str | None = Form(default=None),
    image: UploadFile | None = File(default=None),
    cross_sell_product_ids: list[int] | None = Form(default=None),
    upsell_product_ids: list[int] | None = Form(default=None),
    subscription_category_id: str | None = Form(default=None),
    commitment_type: str | None = Form(default=None),
    payment_frequency: str | None = Form(default=None),
    price_monthly_commitment: str | None = Form(default=None),
    price_annual_monthly_payment: str | None = Form(default=None),
    price_annual_annual_payment: str | None = Form(default=None),
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

    description_value = description.strip() if description and description.strip() else None

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

    subscription_category_value: int | None = None
    if subscription_category_id:
        try:
            subscription_category_value = int(subscription_category_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid subscription category selection")
        sub_category = await subscription_categories_repo.get_category(subscription_category_value)
        if not sub_category:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Selected subscription category does not exist")

    # Validate commitment type and payment frequency for subscriptions
    commitment_value, payment_freq_value = _validate_subscription_commitment_and_payment(
        subscription_category_value,
        commitment_type,
        payment_frequency,
    )

    # Parse pricing fields
    price_monthly_comm: Decimal | None = None
    if price_monthly_commitment not in (None, ""):
        try:
            price_monthly_comm = Decimal(price_monthly_commitment).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if price_monthly_comm < 0:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Monthly commitment price must be at least zero")
        except (TypeError, InvalidOperation):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Monthly commitment price must be a valid number")

    price_annual_monthly: Decimal | None = None
    if price_annual_monthly_payment not in (None, ""):
        try:
            price_annual_monthly = Decimal(price_annual_monthly_payment).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if price_annual_monthly < 0:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Annual commitment with monthly payment price must be at least zero")
        except (TypeError, InvalidOperation):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Annual commitment with monthly payment price must be a valid number")

    price_annual_annual: Decimal | None = None
    if price_annual_annual_payment not in (None, ""):
        try:
            price_annual_annual = Decimal(price_annual_annual_payment).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if price_annual_annual < 0:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Annual commitment with annual payment price must be at least zero")
        except (TypeError, InvalidOperation):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Annual commitment with annual payment price must be a valid number")

    cross_sell_ids = await _validate_recommendation_product_ids(
        cross_sell_product_ids,
        category_id=category_value,
        field_label="Cross-sell",
    )
    upsell_ids = await _validate_recommendation_product_ids(
        upsell_product_ids,
        category_id=category_value,
        field_label="Up-sell",
    )

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
            description=description_value,
            price=price_decimal,
            stock=stock_int,
            vip_price=vip_decimal,
            category_id=category_value,
            image_url=image_url,
            cross_sell_product_ids=cross_sell_ids,
            upsell_product_ids=upsell_ids,
            subscription_category_id=subscription_category_value,
            commitment_type=commitment_value,
            payment_frequency=payment_freq_value,
            price_monthly_commitment=price_monthly_comm,
            price_annual_monthly_payment=price_annual_monthly,
            price_annual_annual_payment=price_annual_annual,
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
    "/shop/admin/product/{product_id}",
    status_code=status.HTTP_303_SEE_OTHER,
    summary="Update a shop product",
    tags=["Shop"],
)
async def admin_update_shop_product(
    request: Request,
    product_id: int,
    name: str = Form(...),
    sku: str = Form(...),
    vendor_sku: str = Form(...),
    description: str | None = Form(default=None),
    price: str = Form(...),
    stock: str = Form(...),
    vip_price: str | None = Form(default=None),
    category_id: str | None = Form(default=None),
    image: UploadFile | None = File(default=None),
    features: str | None = Form(default=None),
    cross_sell_product_ids: list[int] | None = Form(default=None),
    upsell_product_ids: list[int] | None = Form(default=None),
    cross_sell_sku: str | None = Form(default=None),
    upsell_sku: str | None = Form(default=None),
    subscription_category_id: str | None = Form(default=None),
    commitment_type: str | None = Form(default=None),
    payment_frequency: str | None = Form(default=None),
    price_monthly_commitment: str | None = Form(default=None),
    price_annual_monthly_payment: str | None = Form(default=None),
    price_annual_annual_payment: str | None = Form(default=None),
    scheduled_price: str | None = Form(default=None),
    scheduled_vip_price: str | None = Form(default=None),
    scheduled_buy_price: str | None = Form(default=None),
    price_change_date: str | None = Form(default=None),
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    product = await shop_repo.get_product_by_id(product_id, include_archived=True)
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    cleaned_name = name.strip()
    if not cleaned_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Product name cannot be empty")

    cleaned_sku = sku.strip()
    if not cleaned_sku:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="SKU cannot be empty")

    cleaned_vendor_sku = vendor_sku.strip()
    if not cleaned_vendor_sku:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Vendor SKU cannot be empty")

    description_value = description.strip() if description and description.strip() else None

    feature_payload: list[dict[str, Any]] | None = None
    if features not in (None, ""):
        try:
            raw_features = json.loads(features)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid feature payload",
            ) from exc
        if not isinstance(raw_features, list):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid feature payload",
            )
        parsed_features: list[dict[str, Any]] = []
        for index, entry in enumerate(raw_features):
            if not isinstance(entry, Mapping):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid feature payload",
                )
            name_value = str(entry.get("name") or "").strip()
            value_value = str(entry.get("value") or "").strip()
            if not name_value:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Feature name cannot be empty",
                )
            parsed_features.append(
                {"name": name_value, "value": value_value, "position": index}
            )
        feature_payload = parsed_features

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

    subscription_category_value: int | None = None
    if subscription_category_id:
        try:
            subscription_category_value = int(subscription_category_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid subscription category selection")
        sub_category = await subscription_categories_repo.get_category(subscription_category_value)
        if not sub_category:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Selected subscription category does not exist")

    # Validate commitment type and payment frequency for subscriptions
    commitment_value, payment_freq_value = _validate_subscription_commitment_and_payment(
        subscription_category_value,
        commitment_type,
        payment_frequency,
    )

    # Parse pricing fields
    price_monthly_comm: Decimal | None = None
    if price_monthly_commitment not in (None, ""):
        try:
            price_monthly_comm = Decimal(price_monthly_commitment).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if price_monthly_comm < 0:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Monthly commitment price must be at least zero")
        except (TypeError, InvalidOperation):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Monthly commitment price must be a valid number")

    price_annual_monthly: Decimal | None = None
    if price_annual_monthly_payment not in (None, ""):
        try:
            price_annual_monthly = Decimal(price_annual_monthly_payment).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if price_annual_monthly < 0:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Annual commitment with monthly payment price must be at least zero")
        except (TypeError, InvalidOperation):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Annual commitment with monthly payment price must be a valid number")

    price_annual_annual: Decimal | None = None
    if price_annual_annual_payment not in (None, ""):
        try:
            price_annual_annual = Decimal(price_annual_annual_payment).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if price_annual_annual < 0:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Annual commitment with annual payment price must be at least zero")
        except (TypeError, InvalidOperation):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Annual commitment with annual payment price must be a valid number")

    # Parse scheduled price change fields
    scheduled_price_decimal: Decimal | None = None
    if scheduled_price not in (None, ""):
        try:
            scheduled_price_decimal = Decimal(scheduled_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if scheduled_price_decimal < 0:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Scheduled price must be at least zero")
        except (TypeError, InvalidOperation):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Scheduled price must be a valid number")

    scheduled_vip_price_decimal: Decimal | None = None
    if scheduled_vip_price not in (None, ""):
        try:
            scheduled_vip_price_decimal = Decimal(scheduled_vip_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if scheduled_vip_price_decimal < 0:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Scheduled VIP price must be at least zero")
        except (TypeError, InvalidOperation):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Scheduled VIP price must be a valid number")

    scheduled_buy_price_decimal: Decimal | None = None
    if scheduled_buy_price not in (None, ""):
        try:
            scheduled_buy_price_decimal = Decimal(scheduled_buy_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if scheduled_buy_price_decimal < 0:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Scheduled buy price must be at least zero")
        except (TypeError, InvalidOperation):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Scheduled buy price must be a valid number")

    # Parse price change date
    from datetime import datetime as dt
    price_change_date_value: Any | None = None
    if price_change_date and price_change_date.strip():
        try:
            price_change_date_value = dt.strptime(price_change_date.strip(), "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Price change date must be in YYYY-MM-DD format")

    previous_image_url = product.get("image_url")
    image_url = previous_image_url
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

    cross_sell_candidates = _normalise_related_product_inputs(cross_sell_product_ids)
    resolved_cross_id = await _resolve_related_product_id_by_sku(cross_sell_sku)
    if resolved_cross_id:
        cross_sell_candidates.append(resolved_cross_id)

    cross_sell_ids = await _validate_recommendation_product_ids(
        cross_sell_candidates,
        category_id=category_value,
        field_label="Cross-sell",
        disallow_product_id=product_id,
    )
    upsell_candidates = _normalise_related_product_inputs(upsell_product_ids)
    resolved_upsell_id = await _resolve_related_product_id_by_sku(upsell_sku)
    if resolved_upsell_id:
        upsell_candidates.append(resolved_upsell_id)

    upsell_ids = await _validate_recommendation_product_ids(
        upsell_candidates,
        category_id=category_value,
        field_label="Up-sell",
        disallow_product_id=product_id,
    )

    try:
        updated = await shop_repo.update_product(
            product_id,
            name=cleaned_name,
            sku=cleaned_sku,
            vendor_sku=cleaned_vendor_sku,
            description=description_value,
            price=price_decimal,
            stock=stock_int,
            vip_price=vip_decimal,
            category_id=category_value,
            image_url=image_url,
            cross_sell_product_ids=cross_sell_ids,
            upsell_product_ids=upsell_ids,
            subscription_category_id=subscription_category_value,
            commitment_type=commitment_value,
            payment_frequency=payment_freq_value,
            price_monthly_commitment=price_monthly_comm,
            price_annual_monthly_payment=price_annual_monthly,
            price_annual_annual_payment=price_annual_annual,
            scheduled_price=scheduled_price_decimal,
            scheduled_vip_price=scheduled_vip_price_decimal,
            scheduled_buy_price=scheduled_buy_price_decimal,
            price_change_date=price_change_date_value,
        )
    except aiomysql.IntegrityError as exc:
        if stored_path:
            stored_path.unlink(missing_ok=True)
        if exc.args and exc.args[0] == 1062:
            detail = "A product with that SKU or vendor SKU already exists."
        else:
            detail = "Unable to update product."
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc
    except Exception as exc:
        if stored_path:
            stored_path.unlink(missing_ok=True)
        log_error(
            "Failed to update product",
            product_id=product_id,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to update product",
        ) from exc

    if not updated:
        if stored_path:
            stored_path.unlink(missing_ok=True)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    if feature_payload is not None:
        try:
            await shop_repo.replace_product_features(product_id, feature_payload)
        except Exception as exc:  # pragma: no cover - safety
            if stored_path:
                stored_path.unlink(missing_ok=True)
            log_error(
                "Failed to update product features",
                product_id=product_id,
                error=str(exc),
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Unable to update product features",
            ) from exc

    if stored_path and previous_image_url and previous_image_url != updated.get("image_url"):
        try:
            delete_stored_file(previous_image_url, _private_uploads_path)
        except HTTPException as exc:
            log_error(
                "Failed to remove replaced product image",
                product_id=product_id,
                error=str(exc),
            )
        except OSError as exc:
            log_error(
                "Failed to remove replaced product image",
                product_id=product_id,
                error=str(exc),
            )

    log_info(
        "Shop product updated",
        product_id=product_id,
        updated_by=current_user["id"] if current_user else None,
    )
    return RedirectResponse(url="/admin/shop", status_code=status.HTTP_303_SEE_OTHER)


async def _handle_shop_product_archive(
    request: Request,
    product_id: int,
    *,
    archived: bool,
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    product = await shop_repo.get_product_by_id(product_id, include_archived=True)
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    if bool(product.get("archived")) == archived:
        return RedirectResponse(url="/admin/shop", status_code=status.HTTP_303_SEE_OTHER)

    updated = await shop_repo.set_product_archived(product_id, archived=archived)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    log_info(
        "Shop product archived" if archived else "Shop product unarchived",
        product_id=product_id,
        updated_by=current_user["id"] if current_user else None,
    )
    return RedirectResponse(url="/admin/shop", status_code=status.HTTP_303_SEE_OTHER)


@app.post(
    "/shop/admin/product/{product_id}/archive",
    status_code=status.HTTP_303_SEE_OTHER,
    summary="Archive a shop product",
    tags=["Shop"],
)
async def admin_archive_shop_product(request: Request, product_id: int):
    return await _handle_shop_product_archive(request, product_id, archived=True)


@app.post(
    "/shop/admin/product/{product_id}/unarchive",
    status_code=status.HTTP_303_SEE_OTHER,
    summary="Unarchive a shop product",
    tags=["Shop"],
)
async def admin_unarchive_shop_product(request: Request, product_id: int):
    return await _handle_shop_product_archive(request, product_id, archived=False)


@app.post(
    "/shop/admin/product/{product_id}/visibility",
    status_code=status.HTTP_303_SEE_OTHER,
    summary="Update shop product visibility",
    tags=["Shop"],
)
async def admin_update_shop_product_visibility(
    request: Request,
    product_id: int,
    excluded: list[str] = Form(default=[]),
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    product = await shop_repo.get_product_by_id(product_id, include_archived=True)
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    excluded_ids: set[int] = set()
    for value in excluded:
        if value in (None, ""):
            continue
        try:
            company_id = int(value)
        except (TypeError, ValueError):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid company selection")
        excluded_ids.add(company_id)

    for company_id in excluded_ids:
        company = await company_repo.get_company_by_id(company_id)
        if not company:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Selected company does not exist")

    await shop_repo.replace_product_exclusions(product_id, excluded_ids)

    log_info(
        "Shop product visibility updated",
        product_id=product_id,
        excluded_companies=sorted(excluded_ids),
        updated_by=current_user["id"] if current_user else None,
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


async def _render_portal_tickets_page(
    request: Request,
    user: dict[str, Any],
    *,
    status_filter: str | None = None,
    search_term: str | None = None,
    success_message: str | None = None,
    error_message: str | None = None,
    form_values: dict[str, str] | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    try:
        user_id = int(user.get("id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied") from None

    available_companies = await company_access.list_accessible_companies(user)
    active_company_id = getattr(request.state, "active_company_id", None)
    company_lookup: dict[int, dict[str, Any]] = {}
    available_company_ids: list[int] = []
    for entry in available_companies:
        company_id = entry.get("company_id")
        try:
            numeric_id = int(company_id)
        except (TypeError, ValueError):
            continue
        available_company_ids.append(numeric_id)
        company_lookup[numeric_id] = entry if isinstance(entry, dict) else dict(entry)

    active_company_ids: list[int] = []
    if active_company_id is not None:
        try:
            active_company_ids = [int(active_company_id)]
        except (TypeError, ValueError):
            active_company_ids = []
    if not active_company_ids:
        active_company_ids = available_company_ids

    status_definitions = await tickets_service.list_status_definitions()

    def _normalise_status_slug(value: str | None) -> str | None:
        if value in (None, ""):
            return None
        slug = str(value).strip().lower()
        if not slug:
            return None
        for character in slug:
            if not (character.isalnum() or character in {"_", "-"}):
                return None
        return slug

    def _encode_status_value(slugs: Sequence[str]) -> str:
        unique = []
        seen: set[str] = set()
        for slug in slugs:
            normalised = _normalise_status_slug(slug)
            if not normalised or normalised in seen:
                continue
            seen.add(normalised)
            unique.append(normalised)
        if not unique:
            return ""
        if len(unique) == 1:
            return unique[0]
        unique.sort()
        return ",".join(unique)

    grouped_statuses: dict[str, dict[str, Any]] = {}
    slug_to_group: dict[str, str] = {}
    for definition in status_definitions:
        label = definition.public_status
        group_key = label.casefold()
        entry = grouped_statuses.setdefault(
            group_key,
            {
                "label": label,
                "slugs": [],
            },
        )
        normalised_slug = _normalise_status_slug(definition.tech_status)
        if not normalised_slug:
            continue
        if normalised_slug not in entry["slugs"]:
            entry["slugs"].append(normalised_slug)
        slug_to_group[normalised_slug] = group_key

    status_label_map = {
        definition.tech_status: definition.public_status for definition in status_definitions
    }

    for entry in grouped_statuses.values():
        entry["slugs"].sort()
        entry["value"] = _encode_status_value(entry["slugs"])

    value_to_group: dict[str, str] = {
        entry["value"]: key
        for key, entry in grouped_statuses.items()
        if entry.get("value")
    }

    raw_status_filter = (status_filter or "").strip()
    status_filter_value: str | None = None
    selected_status_slugs: list[str] | None = None
    if raw_status_filter:
        candidate = _normalise_status_slug(raw_status_filter)
        if candidate and candidate in value_to_group:
            group_key = value_to_group[candidate]
            entry = grouped_statuses.get(group_key) or {}
            selected_status_slugs = list(entry.get("slugs") or [])
            status_filter_value = entry.get("value")
        else:
            parts = [segment for segment in raw_status_filter.split(",") if segment.strip()]
            slugs: list[str] = []
            for part in parts:
                slug = _normalise_status_slug(part)
                if not slug or slug in slugs:
                    continue
                slugs.append(slug)
            if slugs:
                selected_status_slugs = slugs
                status_filter_value = _encode_status_value(slugs)

    search_value = (search_term or "").strip()
    effective_search = search_value or None

    try:
        tickets = await tickets_repo.list_tickets_for_user(
            user_id,
            company_ids=active_company_ids or None,
            status=selected_status_slugs,
            search=effective_search,
            limit=200,
        )
        total_count = await tickets_repo.count_tickets_for_user(
            user_id,
            company_ids=active_company_ids or None,
            status=selected_status_slugs,
            search=effective_search,
        )
    except Exception as exc:  # pragma: no cover - defensive fallback
        log_error("Failed to load portal tickets", error=str(exc))
        tickets = []
        total_count = 0
        if not error_message:
            error_message = "Unable to load tickets right now. Please try again."

    status_counts: Counter[str] = Counter()
    formatted_tickets: list[dict[str, Any]] = []
    for record in tickets:
        status_value = str(record.get("status") or "open").lower()
        status_counts[status_value] += 1
        status_label = status_label_map.get(status_value) or status_value.replace("_", " ").title()
        priority_value = str(record.get("priority") or "normal")
        priority_label = priority_value.replace("_", " ").title()
        company_name = None
        company_identifier = record.get("company_id")
        try:
            company_numeric = int(company_identifier) if company_identifier is not None else None
        except (TypeError, ValueError):
            company_numeric = None
        if company_numeric is not None:
            company_entry = company_lookup.get(company_numeric) or {}
            company_name = (
                str(company_entry.get("company_name") or company_entry.get("name") or "").strip()
                or None
            )
        updated_at = record.get("updated_at")
        created_at = record.get("created_at")
        updated_iso = (
            updated_at.astimezone(timezone.utc).isoformat()
            if hasattr(updated_at, "astimezone")
            else ""
        )
        created_iso = (
            created_at.astimezone(timezone.utc).isoformat()
            if hasattr(created_at, "astimezone")
            else ""
        )
        formatted_tickets.append(
            {
                "id": record.get("id"),
                "subject": record.get("subject"),
                "status": status_value,
                "status_label": status_label,
                "status_badge": _PORTAL_STATUS_BADGE_MAP.get(status_value, "badge--muted"),
                "priority_label": priority_label,
                "company_name": company_name,
                "company_id": record.get("company_id"),
                "updated_iso": updated_iso,
                "created_iso": created_iso,
            }
        )

    for slug, group_key in slug_to_group.items():
        label = grouped_statuses[group_key]["label"]
        status_label_map.setdefault(slug, label)

    for slug, count in status_counts.items():
        if slug in slug_to_group:
            continue
        label = slug.replace("_", " ").title()
        group_key = f"dynamic::{slug}"
        grouped_statuses[group_key] = {
            "label": label,
            "slugs": [slug],
            "value": _encode_status_value([slug]),
        }
        slug_to_group[slug] = group_key
        value_to_group[grouped_statuses[group_key]["value"]] = group_key
        status_label_map.setdefault(slug, label)

    summary_entries: list[dict[str, Any]] = []
    for entry in sorted(grouped_statuses.values(), key=lambda item: item["label"].lower()):
        count = sum(status_counts.get(slug, 0) for slug in entry["slugs"])
        if count <= 0:
            continue
        summary_entries.append(
            {
                "slug": entry.get("value"),
                "label": entry["label"],
                "count": count,
            }
        )

    status_options = [
        {"value": entry["value"], "label": entry["label"]}
        for entry in sorted(grouped_statuses.values(), key=lambda item: item["label"].lower())
        if entry.get("value")
    ]

    if status_filter_value is None and selected_status_slugs:
        status_filter_value = _encode_status_value(selected_status_slugs)
    if selected_status_slugs:
        selected_status_slugs = list(dict.fromkeys(selected_status_slugs))
    else:
        selected_status_slugs = None

    extra = {
        "title": "Tickets",
        "tickets": formatted_tickets,
        "tickets_total": total_count,
        "status_options": status_options,
        "status_filter": status_filter_value,
        "status_summary": summary_entries,
        "search_term": search_value,
        "filters_active": bool(status_filter_value or search_value),
        "success_message": success_message,
        "error_message": error_message,
        "form_values": form_values or {},
    }
    response = await _render_template("tickets/index.html", request, user, extra=extra)
    response.status_code = status_code
    return response


async def _render_portal_ticket_detail(
    request: Request,
    user: dict[str, Any],
    *,
    ticket_id: int,
    success_message: str | None = None,
    error_message: str | None = None,
    reply_error: str | None = None,
    reply_body: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    has_helpdesk_access = await _is_helpdesk_technician(user, request)
    is_super_admin = bool(user.get("is_super_admin"))

    user_id = user.get("id")
    try:
        user_id_int = int(user_id) if user_id is not None else None
    except (TypeError, ValueError):
        user_id_int = None

    is_requester = user_id_int is not None and ticket.get("requester_id") == user_id_int
    is_watcher = False
    if user_id_int is not None and not is_requester:
        try:
            is_watcher = await tickets_repo.is_ticket_watcher(ticket_id, user_id_int)
        except Exception as exc:  # pragma: no cover - defensive logging
            log_error("Failed to determine ticket watcher state", error=str(exc))
            is_watcher = False

    if not (has_helpdesk_access or is_super_admin or is_requester or is_watcher):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    sanitized_description = sanitize_rich_text(str(ticket.get("description") or ""))
    status_label_map = await tickets_service.get_public_status_map()
    status_value = str(ticket.get("status") or "open").lower()
    status_label = status_label_map.get(status_value) or status_value.replace("_", " ").title()
    priority_value = str(ticket.get("priority") or "normal")
    priority_label = priority_value.replace("_", " ").title()

    created_at = ticket.get("created_at")
    updated_at = ticket.get("updated_at")
    created_iso = (
        created_at.astimezone(timezone.utc).isoformat()
        if hasattr(created_at, "astimezone")
        else ""
    )
    updated_iso = (
        updated_at.astimezone(timezone.utc).isoformat()
        if hasattr(updated_at, "astimezone")
        else ""
    )
    billed_at = ticket.get("billed_at")
    billed_at_iso = (
        billed_at.astimezone(timezone.utc).isoformat()
        if hasattr(billed_at, "astimezone")
        else ""
    )

    company_record: Mapping[str, Any] | None = None
    company_name = None
    company_identifier = ticket.get("company_id")
    try:
        company_numeric = int(company_identifier) if company_identifier is not None else None
    except (TypeError, ValueError):
        company_numeric = None
    if company_numeric is not None:
        company_record = await company_repo.get_company_by_id(company_numeric)
        if isinstance(company_record, Mapping):
            company_name = (
                str(company_record.get("name") or "").strip()
                or None
            )
        else:
            company_record = None

    replies = await tickets_repo.list_replies(ticket_id, include_internal=has_helpdesk_access)
    ordered_replies = list(reversed(replies))
    
    # Fetch call recordings linked to this ticket
    from app.repositories import call_recordings as call_recordings_repo
    call_recordings = await call_recordings_repo.list_ticket_call_recordings(ticket_id)

    related_user_ids: set[int] = set()
    for key in ("assigned_user_id", "requester_id"):
        value = ticket.get(key)
        try:
            if value is not None:
                related_user_ids.add(int(value))
        except (TypeError, ValueError):
            continue
    for reply in ordered_replies:
        author_id = reply.get("author_id")
        try:
            if author_id is not None:
                related_user_ids.add(int(author_id))
        except (TypeError, ValueError):
            continue

    user_lookup: dict[int, Mapping[str, Any]] = {}
    if related_user_ids:
        lookup_results = await asyncio.gather(
            *(user_repo.get_user_by_id(identifier) for identifier in related_user_ids),
            return_exceptions=True,
        )
        for record in lookup_results:
            if isinstance(record, Mapping) and record.get("id") is not None:
                try:
                    identifier = int(record["id"])
                except (TypeError, ValueError):
                    continue
                user_lookup[identifier] = record

    def _format_user_label(user_record: Mapping[str, Any] | None) -> str:
        if not isinstance(user_record, Mapping):
            return "System"
        first = str(user_record.get("first_name") or "").strip()
        last = str(user_record.get("last_name") or "").strip()
        name_parts = [part for part in (first, last) if part]
        if name_parts:
            return " ".join(name_parts)
        email = str(user_record.get("email") or "").strip()
        return email or "System"

    requester_record = user_lookup.get(ticket.get("requester_id"))
    assigned_record = user_lookup.get(ticket.get("assigned_user_id"))

    timeline_entries: list[dict[str, Any]] = []
    for reply in ordered_replies:
        sanitized_reply = sanitize_rich_text(str(reply.get("body") or ""))
        minutes_value = reply.get("minutes_spent")
        minutes_spent = minutes_value if isinstance(minutes_value, int) and minutes_value >= 0 else None
        billable_flag = bool(reply.get("is_billable"))
        labour_type_name = str(reply.get("labour_type_name") or "").strip() or None
        time_summary = tickets_service.format_reply_time_summary(
            minutes_spent,
            billable_flag,
            labour_type_name,
        )
        created_at = reply.get("created_at")
        created_iso = (
            created_at.astimezone(timezone.utc).isoformat()
            if hasattr(created_at, "astimezone")
            else ""
        )
        author_record = user_lookup.get(reply.get("author_id"))
        timeline_entries.append(
            {
                "id": reply.get("id"),
                "type": "reply",
                "author": author_record,
                "author_label": _format_user_label(author_record),
                "created_iso": created_iso,
                "body_html": sanitized_reply.html,
                "has_content": sanitized_reply.has_rich_content,
                "time_summary": time_summary,
                "is_internal": bool(reply.get("is_internal")),
                "labour_type_name": labour_type_name,
                "labour_type_code": reply.get("labour_type_code"),
            }
        )
    
    # Add call recordings to timeline
    for recording in call_recordings:
        call_date = recording.get("call_date")
        call_date_iso = (
            call_date.astimezone(timezone.utc).isoformat()
            if hasattr(call_date, "astimezone")
            else ""
        )
        
        minutes_value = recording.get("minutes_spent")
        minutes_spent = minutes_value if isinstance(minutes_value, int) and minutes_value >= 0 else None
        billable_flag = bool(recording.get("is_billable"))
        labour_type_name = str(recording.get("labour_type_name") or "").strip() or None
        time_summary = tickets_service.format_reply_time_summary(
            minutes_spent,
            billable_flag,
            labour_type_name,
        )
        
        # Format caller/callee information
        caller_name = None
        if recording.get("caller_first_name") or recording.get("caller_last_name"):
            caller_name = f"{recording.get('caller_first_name', '')} {recording.get('caller_last_name', '')}".strip()
        elif recording.get("caller_number"):
            caller_name = recording.get("caller_number")
        
        callee_name = None
        if recording.get("callee_first_name") or recording.get("callee_last_name"):
            callee_name = f"{recording.get('callee_first_name', '')} {recording.get('callee_last_name', '')}".strip()
        elif recording.get("callee_number"):
            callee_name = recording.get("callee_number")
        
        timeline_entries.append(
            {
                "id": recording.get("id"),
                "type": "call_recording",
                "created_iso": call_date_iso,
                "file_name": recording.get("file_name"),
                "caller_name": caller_name or "Unknown",
                "callee_name": callee_name or "Unknown",
                "duration_seconds": recording.get("duration_seconds"),
                "transcription": recording.get("transcription"),
                "time_summary": time_summary,
                "minutes_spent": minutes_spent,
                "is_billable": billable_flag,
                "labour_type_name": labour_type_name,
                "labour_type_code": recording.get("labour_type_code"),
            }
        )
    
    # Sort timeline entries by date
    timeline_entries.sort(key=lambda e: e.get("created_iso", ""), reverse=True)

    # Find relevant knowledge base articles based on AI tag matching
    relevant_articles: list[dict[str, Any]] = []
    ticket_ai_tags = ticket.get("ai_tags") or []
    if ticket_ai_tags:
        min_matching_tags = settings.ai_tag_threshold
        relevant_articles = await knowledge_base_repo.find_relevant_articles_for_ticket(
            ticket_ai_tags=ticket_ai_tags,
            min_matching_tags=min_matching_tags,
        )

    extra = {
        "title": f"Ticket {ticket_id}",
        "ticket": {
            **ticket,
            "status_label": status_label,
            "status_badge": _PORTAL_STATUS_BADGE_MAP.get(status_value, "badge--muted"),
            "priority_label": priority_label,
            "description_html": sanitized_description.html,
            "description_has_content": sanitized_description.has_rich_content,
            "company": company_record,
            "company_name": company_name,
            "requester": requester_record,
            "requester_label": _format_user_label(requester_record) if requester_record else None,
            "assigned_user": assigned_record,
            "assigned_label": _format_user_label(assigned_record) if assigned_record else None,
            "created_iso": created_iso,
            "updated_iso": updated_iso,
            "billed_at_iso": billed_at_iso,
        },
        "ticket_replies": timeline_entries,
        "can_reply": bool(has_helpdesk_access or is_super_admin or is_requester),
        "is_requester": is_requester,
        "is_watcher": is_watcher,
        "has_helpdesk_access": has_helpdesk_access,
        "relevant_kb_articles": relevant_articles,
        "success_message": success_message,
        "error_message": error_message,
        "reply_error": reply_error,
        "reply_body": reply_body or "",
    }
    response = await _render_template("tickets/detail.html", request, user, extra=extra)
    response.status_code = status_code
    return response


async def _render_tickets_dashboard(
    request: Request,
    user: dict[str, Any],
    *,
    success_message: str | None = None,
    error_message: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    dashboard = await tickets_service.load_dashboard_state(
        status_filter=None,
        module_filter=None,
        limit=200,
    )
    dashboard_endpoint = "/api/tickets/dashboard"
    status_definitions_payload = [
        {
            "tech_status": definition.tech_status,
            "tech_label": definition.tech_label,
            "public_status": definition.public_status,
        }
        for definition in dashboard.status_definitions
    ]
    status_label_map = {
        definition.tech_status: definition.tech_label for definition in dashboard.status_definitions
    }
    public_status_map = {
        definition.tech_status: definition.public_status for definition in dashboard.status_definitions
    }
    labour_types = await labour_types_service.list_labour_types()
    extra = {
        "title": "Ticketing workspace",
        "tickets": dashboard.tickets,
        "ticket_total": dashboard.total,
        "ticket_status_counts": dashboard.status_counts,
        "ticket_available_statuses": dashboard.available_statuses,
        "ticket_status_definitions": status_definitions_payload,
        "ticket_status_label_map": status_label_map,
        "ticket_public_status_map": public_status_map,
        "ticket_modules": dashboard.modules,
        "ticket_company_options": dashboard.companies,
        "ticket_user_options": dashboard.technicians,
        "ticket_company_lookup": dashboard.company_lookup,
        "ticket_user_lookup": dashboard.user_lookup,
        "ticket_labour_types": labour_types,
        "can_bulk_delete_tickets": bool(user.get("is_super_admin")),
        "success_message": success_message,
        "error_message": error_message,
        "ticket_dashboard_endpoint": dashboard_endpoint,
        "ticket_refresh_topics": ["tickets"],
    }
    response = await _render_template("admin/tickets.html", request, user, extra=extra)
    response.status_code = status_code
    return response


async def _render_syncro_ticket_import(
    request: Request,
    user: dict[str, Any],
    *,
    success_message: str | None = None,
    error_message: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    module = await _load_syncro_module()
    module_description = _describe_syncro_module(module)
    if not module_description.get("enabled"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Syncro ticket import is not available")
    extra = {
        "title": "Syncro ticket import",
        "success_message": success_message,
        "error_message": error_message,
        "syncro_module": module_description,
    }
    response = await _render_template("admin/syncro_ticket_import.html", request, user, extra=extra)
    response.status_code = status_code
    return response


async def _render_ticket_detail(
    request: Request,
    user: dict[str, Any],
    *,
    ticket_id: int,
    success_message: str | None = None,
    error_message: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    sanitized_description = sanitize_rich_text(str(ticket.get("description") or ""))
    ticket = {
        **ticket,
        "description_html": sanitized_description.html,
        "description_text": sanitized_description.text_content,
    }

    replies = await tickets_repo.list_replies(ticket_id)
    watchers = await tickets_repo.list_watchers(ticket_id)

    related_user_ids: set[int] = set()
    for key in ("assigned_user_id", "requester_id"):
        value = ticket.get(key)
        if value:
            try:
                related_user_ids.add(int(value))
            except (TypeError, ValueError):
                continue
    for reply in replies:
        author_id = reply.get("author_id")
        if author_id:
            related_user_ids.add(int(author_id))
    for watcher in watchers:
        watcher_user_id = watcher.get("user_id")
        if watcher_user_id:
            related_user_ids.add(int(watcher_user_id))

    user_lookup: dict[int, dict[str, Any]] = {}
    if related_user_ids:
        lookup_results = await asyncio.gather(
            *(user_repo.get_user_by_id(user_id) for user_id in related_user_ids)
        )
        for record in lookup_results:
            if record and record.get("id") is not None:
                try:
                    identifier = int(record["id"])
                except (TypeError, ValueError):
                    continue
                user_lookup[identifier] = record

    company: dict[str, Any] | None = None
    ticket_company_id: int | None = None
    company_id_value = ticket.get("company_id")
    if company_id_value is not None:
        try:
            ticket_company_id = int(company_id_value)
        except (TypeError, ValueError):
            ticket_company_id = None
        else:
            company = await company_repo.get_company_by_id(ticket_company_id)

    modules = await modules_service.list_modules()

    module_info: dict[str, Any] | None = None
    module_slug = ticket.get("module_slug")
    if module_slug:
        for module in modules:
            if module.get("slug") == module_slug:
                module_info = module
                break

    tactical_module = next((module for module in modules if module.get("slug") == "tacticalrmm"), None)
    tactical_base_url = ""
    if tactical_module:
        tactical_settings = tactical_module.get("settings") or {}
        if isinstance(tactical_settings, Mapping):
            base_url = str(tactical_settings.get("base_url") or "").strip()
            if base_url:
                tactical_base_url = base_url.rstrip("/")

    ordered_replies = list(reversed(replies))
    
    # Fetch call recordings linked to this ticket
    from app.repositories import call_recordings as call_recordings_repo
    call_recordings = await call_recordings_repo.list_ticket_call_recordings(ticket_id)

    total_billable_minutes = 0
    total_non_billable_minutes = 0
    enriched_replies: list[dict[str, Any]] = []
    for reply in ordered_replies:
        author_id = reply.get("author_id")
        author = user_lookup.get(author_id) if author_id else None
        sanitized_reply = sanitize_rich_text(str(reply.get("body") or ""))
        minutes_value = reply.get("minutes_spent")
        minutes_spent = minutes_value if isinstance(minutes_value, int) and minutes_value >= 0 else None
        billable_flag = bool(reply.get("is_billable"))
        if minutes_spent is not None:
            if billable_flag:
                total_billable_minutes += minutes_spent
            else:
                total_non_billable_minutes += minutes_spent
        labour_type_name = str(reply.get("labour_type_name") or "").strip() or None
        time_summary = tickets_service.format_reply_time_summary(
            minutes_spent,
            billable_flag,
            labour_type_name,
        )
        enriched_replies.append(
            {
                **reply,
                "author": author,
                "body": sanitized_reply.html,
                "text_body": sanitized_reply.text_content,
                "minutes_spent": minutes_spent,
                "is_billable": billable_flag,
                "time_summary": time_summary,
                "labour_type_name": labour_type_name,
            }
        )
    
    # Process call recordings
    enriched_recordings: list[dict[str, Any]] = []
    for recording in call_recordings:
        minutes_value = recording.get("minutes_spent")
        minutes_spent = minutes_value if isinstance(minutes_value, int) and minutes_value >= 0 else None
        billable_flag = bool(recording.get("is_billable"))
        if minutes_spent is not None:
            if billable_flag:
                total_billable_minutes += minutes_spent
            else:
                total_non_billable_minutes += minutes_spent
        labour_type_name = str(recording.get("labour_type_name") or "").strip() or None
        time_summary = tickets_service.format_reply_time_summary(
            minutes_spent,
            billable_flag,
            labour_type_name,
        )
        
        # Format caller/callee information
        caller_name = None
        if recording.get("caller_first_name") or recording.get("caller_last_name"):
            caller_name = f"{recording.get('caller_first_name', '')} {recording.get('caller_last_name', '')}".strip()
        elif recording.get("caller_number"):
            caller_name = recording.get("caller_number")
        
        callee_name = None
        if recording.get("callee_first_name") or recording.get("callee_last_name"):
            callee_name = f"{recording.get('callee_first_name', '')} {recording.get('callee_last_name', '')}".strip()
        elif recording.get("callee_number"):
            callee_name = recording.get("callee_number")
        
        enriched_recordings.append(
            {
                **recording,
                "caller_name": caller_name,
                "callee_name": callee_name,
                "minutes_spent": minutes_spent,
                "is_billable": billable_flag,
                "time_summary": time_summary,
                "labour_type_name": labour_type_name,
            }
        )

    enriched_watchers: list[dict[str, Any]] = []
    for watcher in watchers:
        watcher_user = user_lookup.get(watcher.get("user_id"))
        enriched_watchers.append({**watcher, "user": watcher_user})

    labour_types = await labour_types_service.list_labour_types()

    status_definitions = await tickets_service.list_status_definitions()
    status_label_map = {definition.tech_status: definition.tech_label for definition in status_definitions}
    public_status_map = {definition.tech_status: definition.public_status for definition in status_definitions}
    available_statuses = [definition.tech_status for definition in status_definitions]
    ticket_status_slug = ticket.get("status") or "open"
    if ticket_status_slug not in available_statuses:
        available_statuses.append(ticket_status_slug)

    companies = await company_repo.list_companies()
    technician_users = await membership_repo.list_users_with_permission(
        HELPDESK_PERMISSION_KEY
    )
    requester_options: list[dict[str, Any]] = []
    watcher_staff_options: list[dict[str, Any]] = []
    if ticket_company_id is not None:
        requester_options = await staff_repo.list_enabled_staff_users(ticket_company_id)
        # Get all enabled staff for the company as watcher options
        watcher_staff_options = await staff_repo.list_enabled_staff_users(ticket_company_id)

    current_requester_id = ticket.get("requester_id")
    if isinstance(current_requester_id, int):
        existing_ids = {
            int(option.get("id"))
            for option in requester_options
            if option.get("id") is not None
        }
        if current_requester_id not in existing_ids:
            current_requester = user_lookup.get(current_requester_id)
            if current_requester:
                requester_options.append(current_requester)

        def _requester_sort_key(record: dict[str, Any]) -> tuple[str, int]:
            email_value = str(record.get("email") or "").lower()
            identifier = record.get("id")
            try:
                identifier_int = int(identifier)
            except (TypeError, ValueError):
                identifier_int = 0
            return email_value, identifier_int

        requester_options.sort(key=_requester_sort_key)

    default_priorities = ["urgent", "high", "normal", "low"]
    current_priority = str(ticket.get("priority") or "normal")
    seen_priorities: set[str] = set()
    priority_options: list[str] = []
    for option in [*default_priorities, current_priority]:
        option_str = str(option)
        normalised = option_str.lower()
        if normalised in seen_priorities:
            continue
        seen_priorities.add(normalised)
        priority_options.append(option_str)

    ticket_assets = await tickets_repo.list_ticket_assets(ticket_id)
    asset_selection: list[int] = []
    for linked in ticket_assets:
        asset_id = linked.get("asset_id")
        try:
            asset_selection.append(int(asset_id))
        except (TypeError, ValueError):
            continue

    serialisable_ticket_assets: list[dict[str, Any]] = []
    for asset in ticket_assets:
        if not isinstance(asset, Mapping):
            continue
        asset_identifier = asset.get("asset_id")
        serialisable_ticket_assets.append(
            {
                "id": asset_identifier,
                "asset_id": asset_identifier,
                "name": str(asset.get("name") or "").strip() or (f"Asset {asset_identifier}" if asset_identifier else "Asset"),
                "serial_number": (str(asset.get("serial_number") or "").strip() or None),
                "status": (str(asset.get("status") or "").strip() or None),
                "tactical_asset_id": (str(asset.get("tactical_asset_id") or "").strip() or None),
            }
        )

    asset_options: list[dict[str, Any]] = []
    if ticket_company_id is not None:
        company_assets = await assets_repo.list_company_assets(ticket_company_id)

        def _format_asset_label(asset_row: Mapping[str, Any]) -> str:
            asset_name = str(asset_row.get("name") or "").strip() or "Asset"
            serial_value = str(asset_row.get("serial_number") or "").strip()
            status_value = str(asset_row.get("status") or "").strip()
            parts = [asset_name]
            if serial_value:
                parts.append(f"SN {serial_value}")
            if status_value:
                parts.append(status_value.title())
            return " · ".join(parts)

        for asset_row in company_assets:
            asset_id = asset_row.get("id")
            if asset_id is None:
                continue
            try:
                asset_id_int = int(asset_id)
            except (TypeError, ValueError):
                continue
            asset_options.append(
                {
                    "id": asset_id_int,
                    "label": _format_asset_label(asset_row),
                    "name": str(asset_row.get("name") or "").strip() or f"Asset {asset_id_int}",
                    "serial_number": str(asset_row.get("serial_number") or "").strip() or None,
                    "status": str(asset_row.get("status") or "").strip() or None,
                    "tactical_asset_id": str(asset_row.get("tactical_asset_id") or "").strip() or None,
                }
            )

    asset_options.sort(key=lambda option: option["label"].lower())

    # Find relevant knowledge base articles based on AI tag matching
    relevant_articles: list[dict[str, Any]] = []
    ticket_ai_tags = ticket.get("ai_tags") or []
    if ticket_ai_tags:
        min_matching_tags = settings.ai_tag_threshold
        relevant_articles = await knowledge_base_repo.find_relevant_articles_for_ticket(
            ticket_ai_tags=ticket_ai_tags,
            min_matching_tags=min_matching_tags,
        )

    extra = {
        "title": f"Ticket #{ticket_id}",
        "ticket": ticket,
        "ticket_company": company,
        "ticket_module": module_info,
        "ticket_assigned_user": user_lookup.get(ticket.get("assigned_user_id")),
        "ticket_requester": user_lookup.get(ticket.get("requester_id")),
        "ticket_replies": enriched_replies,
        "ticket_call_recordings": enriched_recordings,
        "ticket_watchers": enriched_watchers,
        "ticket_labour_types": labour_types,
        "ticket_billable_minutes": total_billable_minutes,
        "ticket_non_billable_minutes": total_non_billable_minutes,
        "ticket_available_statuses": available_statuses,
        "ticket_status_definitions": [
            {
                "tech_status": definition.tech_status,
                "tech_label": definition.tech_label,
                "public_status": definition.public_status,
            }
            for definition in status_definitions
        ],
        "ticket_status_label_map": status_label_map,
        "ticket_public_status_map": public_status_map,
        "ticket_company_options": companies,
        "ticket_user_options": technician_users,
        "ticket_requester_options": requester_options,
        "ticket_watcher_staff_options": watcher_staff_options,
        "ticket_priority_options": priority_options,
        "ticket_return_url": request.url.path,
        "ticket_assets": ticket_assets,
        "ticket_asset_options": asset_options,
        "ticket_asset_selection": asset_selection,
        "ticket_asset_linked_data": serialisable_ticket_assets,
        "tacticalrmm_base_url": tactical_base_url,
        "can_delete_ticket": bool(user.get("is_super_admin")),
        "relevant_kb_articles": relevant_articles,
        "success_message": success_message,
        "error_message": error_message,
    }
    response = await _render_template("admin/ticket_detail.html", request, user, extra=extra)
    response.status_code = status_code
    return response


def _get_current_user_id(user: Mapping[str, Any] | None) -> int | None:
    if not user:
        return None
    try:
        return int(user.get("id"))  # type: ignore[arg-type]
    except (TypeError, ValueError, AttributeError):
        return None


def _format_issue_overview_for_template(
    overview: issues_service.IssueOverview,
) -> dict[str, Any]:
    assignments = []
    for assignment in overview.assignments:
        assignments.append(
            {
                "assignment_id": assignment.assignment_id,
                "issue_id": assignment.issue_id,
                "company_id": assignment.company_id,
                "company_name": assignment.company_name,
                "status": assignment.status,
                "status_label": assignment.status_label,
                "updated_at_iso": assignment.updated_at_iso,
            }
        )
    return {
        "issue_id": overview.issue_id,
        "name": overview.name,
        "description": overview.description,
        "created_at_iso": overview.created_at_iso,
        "updated_at_iso": overview.updated_at_iso,
        "assignments": assignments,
        "assignment_count": len(assignments),
    }


@app.get("/admin/issues", response_class=HTMLResponse)
async def admin_issue_tracker(
    request: Request,
    search: str | None = Query(default=None, max_length=255),
    status: str | None = Query(default=None, max_length=32),
    company_id: int | None = Query(default=None, alias="companyId"),
    issue_id: int | None = Query(default=None, alias="issueId"),
):
    current_user, redirect = await _require_issue_tracker_access(request)
    if redirect:
        return redirect

    search_term = search.strip() if search else ""
    company_filter: int | None = None
    if company_id is not None:
        try:
            company_filter = int(company_id)
        except (TypeError, ValueError):
            company_filter = None

    status_filter: str | None = None
    if status:
        try:
            status_filter = issues_service.normalise_status(status)
        except ValueError:
            status_filter = None

    overviews = await issues_service.build_issue_overview(
        search=search_term,
        status=status_filter,
        company_id=company_filter,
    )
    issues_payload = [_format_issue_overview_for_template(item) for item in overviews]

    editing_issue: dict[str, Any] | None = None
    edit_error: str | None = None
    if issue_id:
        lookup = await issues_service.get_issue_overview(issue_id)
        if lookup:
            editing_issue = _format_issue_overview_for_template(lookup)
        else:
            edit_error = "Selected issue could not be found."

    companies = await company_repo.list_companies()
    company_options: list[dict[str, Any]] = []
    for record in companies:
        raw_id = record.get("id")
        name = (record.get("name") or "").strip()
        try:
            option_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        company_options.append(
            {
                "id": option_id,
                "name": name or f"Company #{option_id}",
            }
        )
    company_options.sort(key=lambda item: item["name"].lower())

    if editing_issue:
        assigned_company_ids = {
            assignment.get("company_id")
            for assignment in editing_issue.get("assignments", [])
            if assignment.get("company_id") is not None
        }
        available_companies = [
            option for option in company_options if option["id"] not in assigned_company_ids
        ]
        editing_issue["available_companies"] = available_companies

    issue_status_options = [
        {"value": value, "label": label} for value, label in issues_service.STATUS_OPTIONS
    ]

    success_message = request.query_params.get("success")
    error_message = request.query_params.get("error") or edit_error

    extra = {
        "title": "Issue tracker",
        "issues": issues_payload,
        "issue_count": len(issues_payload),
        "issue_status_options": issue_status_options,
        "selected_status": status_filter,
        "selected_company_id": company_filter,
        "search_term": search_term,
        "company_options": company_options,
        "editing_issue": editing_issue,
        "success_message": success_message,
        "error_message": error_message,
    }

    response = await _render_template("admin/issues.html", request, current_user, extra=extra)
    return response


@app.post(
    "/admin/issues",
    response_class=HTMLResponse,
    status_code=status.HTTP_303_SEE_OTHER,
    summary="Create an issue record",
)
async def admin_create_issue(
    request: Request,
    name: str = Form(...),
    description: str | None = Form(default=None),
    company_ids: list[int] | None = Form(default=None),
    initial_status: str = Form(default=issues_service.DEFAULT_STATUS, alias="initialStatus"),
):
    current_user, redirect = await _require_issue_tracker_access(request)
    if redirect:
        return redirect

    cleaned_name = name.strip()
    cleaned_description = description.strip() if description and description.strip() else None
    if not cleaned_name:
        url = f"/admin/issues?error={quote('Issue name is required.')}"
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)

    try:
        await issues_service.ensure_issue_name_available(cleaned_name)
    except ValueError as exc:
        url = f"/admin/issues?error={quote(str(exc))}"
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)

    user_id = _get_current_user_id(current_user)
    try:
        issue_record = await issues_repo.create_issue(
            name=cleaned_name,
            description=cleaned_description,
            created_by=user_id,
        )
    except aiomysql.IntegrityError as exc:
        detail = "Issue name already exists." if exc.args and exc.args[0] == 1062 else "Unable to create issue."
        url = f"/admin/issues?error={quote(detail)}"
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)

    issue_id = issue_record.get("issue_id")
    try:
        issue_id_int = int(issue_id) if issue_id is not None else None
    except (TypeError, ValueError):
        issue_id_int = None
    if issue_id_int is None:
        url = f"/admin/issues?error={quote('Issue identifier missing.')}"
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)

    try:
        status_value = issues_service.normalise_status(initial_status)
    except ValueError:
        status_value = issues_service.DEFAULT_STATUS

    selected_companies: list[int] = []
    if company_ids:
        if isinstance(company_ids, list):
            for raw in company_ids:
                try:
                    selected_companies.append(int(raw))
                except (TypeError, ValueError):
                    continue
        else:
            try:
                selected_companies.append(int(company_ids))
            except (TypeError, ValueError):
                selected_companies = []

    for company_id_value in selected_companies:
        company = await company_repo.get_company_by_id(company_id_value)
        if not company:
            continue
        await issues_repo.assign_issue_to_company(
            issue_id=issue_id_int,
            company_id=company_id_value,
            status=status_value,
            updated_by=user_id,
        )

    log_info(
        "Issue created via admin",
        issue_id=issue_id_int,
        name=cleaned_name,
        created_by=user_id,
    )
    url = f"/admin/issues?success={quote('Issue created.')}"
    return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)


@app.post(
    "/admin/issues/{issue_id}/update",
    response_class=HTMLResponse,
    status_code=status.HTTP_303_SEE_OTHER,
    summary="Update issue details",
)
async def admin_update_issue(
    issue_id: int,
    request: Request,
    name: str = Form(...),
    description: str | None = Form(default=None),
    new_company_ids: list[int] | None = Form(default=None, alias="newCompanyIds"),
    new_company_status: str = Form(default=issues_service.DEFAULT_STATUS, alias="newCompanyStatus"),
):
    current_user, redirect = await _require_issue_tracker_access(request)
    if redirect:
        return redirect

    issue = await issues_repo.get_issue_by_id(issue_id)
    if not issue:
        url = f"/admin/issues?error={quote('Issue not found.')}"
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)

    cleaned_name = name.strip()
    cleaned_description = description.strip() if description and description.strip() else None
    if not cleaned_name:
        url = f"/admin/issues?issueId={issue_id}&error={quote('Issue name is required.')}"
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)

    updates: dict[str, Any] = {}
    if cleaned_name != (issue.get("name") or ""):
        try:
            await issues_service.ensure_issue_name_available(cleaned_name, exclude_issue_id=issue_id)
        except ValueError as exc:
            url = f"/admin/issues?issueId={issue_id}&error={quote(str(exc))}"
            return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)
        updates["name"] = cleaned_name

    if cleaned_description != (issue.get("description") or None):
        updates["description"] = cleaned_description

    if updates:
        updates["updated_by"] = _get_current_user_id(current_user)
        try:
            await issues_repo.update_issue(issue_id, **updates)
        except aiomysql.IntegrityError as exc:
            detail = "Issue name already exists." if exc.args and exc.args[0] == 1062 else "Unable to update issue."
            url = f"/admin/issues?issueId={issue_id}&error={quote(detail)}"
            return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)

    try:
        status_value = issues_service.normalise_status(new_company_status)
    except ValueError:
        status_value = issues_service.DEFAULT_STATUS

    selected_companies: list[int] = []
    if new_company_ids:
        if isinstance(new_company_ids, list):
            for raw in new_company_ids:
                try:
                    selected_companies.append(int(raw))
                except (TypeError, ValueError):
                    continue
        else:
            try:
                selected_companies.append(int(new_company_ids))
            except (TypeError, ValueError):
                selected_companies = []

    for company_id_value in selected_companies:
        company = await company_repo.get_company_by_id(company_id_value)
        if not company:
            continue
        await issues_repo.assign_issue_to_company(
            issue_id=issue_id,
            company_id=company_id_value,
            status=status_value,
            updated_by=_get_current_user_id(current_user),
        )

    log_info(
        "Issue updated via admin",
        issue_id=issue_id,
        updated_by=_get_current_user_id(current_user),
    )
    url = f"/admin/issues?issueId={issue_id}&success={quote('Issue updated.')}"
    return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)


@app.post(
    "/admin/issues/{issue_id}/assignments/{assignment_id}/status",
    response_class=HTMLResponse,
    status_code=status.HTTP_303_SEE_OTHER,
    summary="Update assignment status",
)
async def admin_update_issue_assignment_status(
    issue_id: int,
    assignment_id: int,
    request: Request,
    status_value: str = Form(..., alias="status"),
    return_url: str | None = Form(default=None, alias="returnUrl"),
):
    current_user, redirect = await _require_issue_tracker_access(request)
    if redirect:
        return redirect

    try:
        normalised_status = issues_service.normalise_status(status_value)
    except ValueError:
        url = f"/admin/issues?issueId={issue_id}&error={quote('Invalid status selection.')}"
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)

    try:
        await issues_repo.update_assignment_status(
            assignment_id,
            status=normalised_status,
            updated_by=_get_current_user_id(current_user),
        )
    except ValueError:
        url = f"/admin/issues?issueId={issue_id}&error={quote('Assignment not found.')}"
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)

    log_info(
        "Issue assignment status updated",
        issue_id=issue_id,
        assignment_id=assignment_id,
        status=normalised_status,
        updated_by=_get_current_user_id(current_user),
    )

    destination = return_url or f"/admin/issues?issueId={issue_id}&success={quote('Status updated.')}"
    if "success=" not in destination:
        separator = "&" if "?" in destination else "?"
        destination = f"{destination}{separator}success={quote('Status updated.')}"
    return RedirectResponse(url=destination, status_code=status.HTTP_303_SEE_OTHER)


@app.post(
    "/admin/issues/{issue_id}/assignments/{assignment_id}/delete",
    response_class=HTMLResponse,
    status_code=status.HTTP_303_SEE_OTHER,
    summary="Remove company assignment from issue",
)
async def admin_delete_issue_assignment(
    issue_id: int,
    assignment_id: int,
    request: Request,
    return_url: str | None = Form(default=None, alias="returnUrl"),
):
    current_user, redirect = await _require_issue_tracker_access(request)
    if redirect:
        return redirect

    await issues_repo.delete_assignment(assignment_id)
    log_info(
        "Issue assignment removed",
        issue_id=issue_id,
        assignment_id=assignment_id,
        removed_by=_get_current_user_id(current_user),
    )

    destination = return_url or f"/admin/issues?issueId={issue_id}"
    separator = "&" if "?" in destination else "?"
    destination = f"{destination}{separator}success={quote('Assignment removed.')}"
    return RedirectResponse(url=destination, status_code=status.HTTP_303_SEE_OTHER)


@app.get("/admin/tickets", response_class=HTMLResponse)
async def admin_tickets_page(
    request: Request,
    success: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    current_user, redirect = await _require_helpdesk_page(request)
    if redirect:
        return redirect
    return await _render_tickets_dashboard(
        request,
        current_user,
        success_message=_sanitize_message(success),
        error_message=_sanitize_message(error),
    )


@app.get("/admin/tickets/syncro-import", response_class=HTMLResponse)
async def admin_syncro_ticket_import_page(
    request: Request,
    success: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    current_user, redirect = await _require_helpdesk_page(request)
    if redirect:
        return redirect
    return await _render_syncro_ticket_import(
        request,
        current_user,
        success_message=_sanitize_message(success),
        error_message=_sanitize_message(error),
    )


@app.get("/admin/tickets/{ticket_id}", response_class=HTMLResponse)
async def admin_ticket_detail(
    ticket_id: int,
    request: Request,
    success: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    current_user, redirect = await _require_helpdesk_page(request)
    if redirect:
        return redirect
    return await _render_ticket_detail(
        request,
        current_user,
        ticket_id=ticket_id,
        success_message=_sanitize_message(success),
        error_message=_sanitize_message(error),
    )


@app.post("/admin/tickets", response_class=HTMLResponse)
async def admin_create_ticket(request: Request):
    current_user, redirect = await _require_helpdesk_page(request)
    if redirect:
        return redirect
    form = await request.form()
    subject = str(form.get("subject", "")).strip()
    description = (str(form.get("description", "")).strip() or None)
    priority = (str(form.get("priority", "")).strip() or "normal")
    module_slug = (str(form.get("moduleSlug", "")).strip() or None)
    status_raw = str(form.get("status", "")).strip()
    company_raw = form.get("companyId")
    assigned_raw = form.get("assignedUserId")
    requester_raw = form.get("requesterId")
    try:
        company_id = int(company_raw) if company_raw else None
    except (TypeError, ValueError):
        company_id = None
    try:
        assigned_user_id = int(assigned_raw) if assigned_raw else None
    except (TypeError, ValueError):
        assigned_user_id = None
    try:
        requester_id = int(requester_raw) if requester_raw else current_user.get("id")
    except (TypeError, ValueError):
        requester_id = current_user.get("id")
    
    # Validate that the requester belongs to the selected company if both are provided
    if requester_id and company_id and requester_id != current_user.get("id"):
        try:
            staff_member = await staff_repo.get_staff_by_id(requester_id)
            if not staff_member or staff_member.get("company_id") != company_id:
                return await _render_tickets_dashboard(
                    request,
                    current_user,
                    error_message="The selected requester does not belong to the selected company.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
        except Exception:
            return await _render_tickets_dashboard(
                request,
                current_user,
                error_message="Invalid requester selection.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
    
    if not subject:
        return await _render_tickets_dashboard(
            request,
            current_user,
            error_message="Enter a ticket subject.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    try:
        if status_raw:
            status_value = await tickets_service.validate_status_choice(status_raw)
        else:
            status_value = await tickets_service.resolve_status_or_default(None)
        created = await tickets_service.create_ticket(
            subject=subject,
            description=description,
            requester_id=requester_id,
            company_id=company_id,
            assigned_user_id=assigned_user_id,
            priority=priority,
            status=status_value,
            category=str(form.get("category", "")).strip() or None,
            module_slug=module_slug,
            external_reference=str(form.get("externalReference", "")).strip() or None,
            trigger_automations=True,
            initial_reply_author_id=current_user.get("id"),
        )
        await tickets_repo.add_watcher(created["id"], current_user.get("id"))
        await tickets_service.refresh_ticket_ai_summary(created["id"])
        await tickets_service.refresh_ticket_ai_tags(created["id"])
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to create ticket", error=str(exc))
        if isinstance(exc, ValueError):
            error_detail = str(exc)
            status_code_value = status.HTTP_400_BAD_REQUEST
        else:
            log_error("Failed to create ticket", error=str(exc))
            error_detail = "Unable to create ticket. Please try again."
            status_code_value = status.HTTP_500_INTERNAL_SERVER_ERROR
        return await _render_tickets_dashboard(
            request,
            current_user,
            error_message=error_detail,
            status_code=status_code_value,
        )
    return RedirectResponse(
        url="/admin/tickets?success=" + quote("Ticket created."),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/tickets/{ticket_id}/status", response_class=HTMLResponse)
async def admin_update_ticket_status(ticket_id: int, request: Request):
    current_user, redirect = await _require_helpdesk_page(request)
    if redirect:
        return redirect
    form = await request.form()
    status_raw = str(form.get("status", "")).strip()
    return_url_raw = form.get("returnUrl")
    return_url = str(return_url_raw).strip() if isinstance(return_url_raw, str) else None
    try:
        status_value = await tickets_service.validate_status_choice(status_raw)
    except ValueError as exc:
        error_message = str(exc)
        if return_url and return_url.startswith(f"/admin/tickets/{ticket_id}"):
            return await _render_ticket_detail(
                request,
                current_user,
                ticket_id=ticket_id,
                error_message=error_message,
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        return await _render_tickets_dashboard(
            request,
            current_user,
            error_message=error_message,
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    await tickets_repo.set_ticket_status(ticket_id, status_value)
    await tickets_service.refresh_ticket_ai_summary(ticket_id)
    await tickets_service.refresh_ticket_ai_tags(ticket_id)
    await tickets_service.broadcast_ticket_event(action="updated", ticket_id=ticket_id)
    await tickets_service.emit_ticket_updated_event(
        ticket_id,
        actor_type="technician",
        actor=current_user,
    )
    message = quote(f"Ticket {ticket_id} updated.")
    destination = f"/admin/tickets?success={message}"
    if return_url and return_url.startswith("/") and not return_url.startswith("//"):
        separator = "&" if "?" in return_url else "?"
        destination = f"{return_url}{separator}success={message}"
    return RedirectResponse(url=destination, status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/tickets/statuses", response_class=HTMLResponse)
async def admin_replace_ticket_statuses(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    form = await request.form()
    tech_labels = form.getlist("techLabel")
    public_labels = form.getlist("publicLabel")
    existing_slugs = form.getlist("existingSlug")

    statuses: list[dict[str, Any]] = []
    max_length = max(len(tech_labels), len(public_labels), len(existing_slugs))
    for index in range(max_length):
        tech_label = tech_labels[index] if index < len(tech_labels) else ""
        public_status = public_labels[index] if index < len(public_labels) else ""
        existing_slug = existing_slugs[index] if index < len(existing_slugs) else None
        if not tech_label and not public_status:
            continue
        statuses.append(
            {
                "techLabel": tech_label,
                "publicStatus": public_status,
                "existingSlug": existing_slug,
            }
        )

    try:
        await tickets_service.replace_ticket_statuses(statuses)
    except ValueError as exc:
        return await _render_tickets_dashboard(
            request,
            current_user,
            error_message=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    return RedirectResponse(
        url="/admin/tickets?success=" + quote("Ticket statuses updated."),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/tickets/labour-types", response_class=HTMLResponse)
async def admin_replace_labour_types(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    form = await request.form()
    ids = form.getlist("labourId")
    codes = form.getlist("labourCode")
    names = form.getlist("labourName")
    rates = form.getlist("labourRate")
    default_id = form.get("defaultLabourType")

    definitions: list[dict[str, Any]] = []
    max_length = max(len(ids), len(codes), len(names), len(rates))
    for index in range(max_length):
        identifier = ids[index] if index < len(ids) else None
        code = codes[index] if index < len(codes) else ""
        name = names[index] if index < len(names) else ""
        rate_str = rates[index] if index < len(rates) else ""
        if not code and not name:
            continue
        rate_value: float | None = None
        if rate_str and rate_str.strip():
            try:
                rate_value = float(rate_str.strip())
            except (ValueError, TypeError):
                rate_value = None
        # Check if this labour type should be default
        is_default = (identifier and str(identifier) == str(default_id))
        definitions.append({
            "id": identifier,
            "code": code,
            "name": name,
            "rate": rate_value,
            "is_default": is_default,
        })

    try:
        await labour_types_service.replace_labour_types(definitions)
    except ValueError as exc:
        return await _render_tickets_dashboard(
            request,
            current_user,
            error_message=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    return RedirectResponse(
        url="/admin/tickets?success=" + quote("Labour types updated."),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/tickets/{ticket_id}/description", response_class=HTMLResponse)
async def admin_update_ticket_description(ticket_id: int, request: Request):
    current_user, redirect = await _require_helpdesk_page(request)
    if redirect:
        return redirect

    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    form = await request.form()

    description_raw = form.get("description")
    description_value: str | None = None
    if isinstance(description_raw, str):
        normalised_description = description_raw.replace("\r\n", "\n").replace("\r", "\n")
        if normalised_description.strip():
            description_value = normalised_description
        else:
            description_value = None

    return_url_raw = form.get("returnUrl")
    return_url = str(return_url_raw).strip() if isinstance(return_url_raw, str) else ""

    await tickets_service.update_ticket_description(ticket_id, description_value)
    await tickets_service.refresh_ticket_ai_summary(ticket_id)
    await tickets_service.refresh_ticket_ai_tags(ticket_id)
    await tickets_service.emit_ticket_updated_event(
        ticket_id,
        actor_type="technician",
        actor=current_user,
    )

    message = quote("Ticket description updated.")
    destination = f"/admin/tickets/{ticket_id}?success={message}"
    if return_url and return_url.startswith(f"/admin/tickets/{ticket_id}"):
        separator = "&" if "?" in return_url else "?"
        destination = f"{return_url}{separator}success={message}"

    return RedirectResponse(url=destination, status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/tickets/{ticket_id}/description/replace", response_class=JSONResponse)
async def admin_replace_ticket_description(ticket_id: int, request: Request):
    current_user, redirect = await _require_helpdesk_page(request)
    if redirect:
        return redirect

    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    summary = ticket.get("ai_summary")
    summary_text = str(summary) if summary is not None else ""
    if not summary_text.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="AI summary is not available. Generate a summary before replacing the description.",
        )

    normalised_summary = summary_text.replace("\r\n", "\n").replace("\r", "\n")

    updated = await tickets_service.update_ticket_description(ticket_id, normalised_summary)
    if not updated:
        updated = await tickets_repo.get_ticket(ticket_id)

    sanitized = sanitize_rich_text(str((updated or {}).get("description") or ""))

    await tickets_service.emit_ticket_updated_event(
        ticket_id,
        actor_type="technician",
        actor=current_user,
    )

    return JSONResponse(
        {
            "status": "success",
            "message": "Ticket description replaced with the AI summary.",
            "description": str((updated or {}).get("description") or ""),
            "descriptionHtml": sanitized.html,
            "descriptionText": sanitized.text_content,
        }
    )


@app.post("/admin/tickets/{ticket_id}/details", response_class=HTMLResponse)
async def admin_update_ticket_details(ticket_id: int, request: Request):
    current_user, redirect = await _require_helpdesk_page(request)
    if redirect:
        return redirect

    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    form = await request.form()

    description_raw = form.get("description")
    description_value: str | None = None
    if isinstance(description_raw, str):
        normalised_description = description_raw.replace("\r\n", "\n").replace("\r", "\n")
        if normalised_description.strip():
            description_value = normalised_description
        else:
            description_value = None

    existing_company_id: int | None = None
    raw_existing_company = ticket.get("company_id")
    if raw_existing_company is not None:
        try:
            existing_company_id = int(raw_existing_company)
        except (TypeError, ValueError):
            existing_company_id = None

    def _clean_text(value: Any) -> str:
        return str(value).strip() if isinstance(value, str) else ""

    status_raw = _clean_text(form.get("status"))
    priority_value = _clean_text(form.get("priority")).lower()
    requester_raw = form.get("requesterId")
    assigned_raw = form.get("assignedUserId")
    company_raw = form.get("companyId")
    category_value = _clean_text(form.get("category")) or None
    external_reference = _clean_text(form.get("externalReference")) or None
    return_url_raw = form.get("returnUrl")
    return_url = _clean_text(return_url_raw)

    if status_raw:
        try:
            status_value = await tickets_service.validate_status_choice(status_raw)
        except ValueError as exc:
            return await _render_ticket_detail(
                request,
                current_user,
                ticket_id=ticket_id,
                error_message=str(exc),
                status_code=status.HTTP_400_BAD_REQUEST,
            )
    else:
        status_value = ticket.get("status") or await tickets_service.resolve_status_or_default(None)

    default_priorities = {"urgent", "high", "normal", "low"}
    ticket_priority = (ticket.get("priority") or "normal").lower()
    allowed_priorities = default_priorities | {ticket_priority}
    if not priority_value:
        return await _render_ticket_detail(
            request,
            current_user,
            ticket_id=ticket_id,
            error_message="Select a priority.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if priority_value not in allowed_priorities:
        return await _render_ticket_detail(
            request,
            current_user,
            ticket_id=ticket_id,
            error_message="Select a valid priority.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if category_value and len(category_value) > 64:
        return await _render_ticket_detail(
            request,
            current_user,
            ticket_id=ticket_id,
            error_message="Category must be 64 characters or fewer.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if external_reference and len(external_reference) > 128:
        return await _render_ticket_detail(
            request,
            current_user,
            ticket_id=ticket_id,
            error_message="External reference must be 128 characters or fewer.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    requester_id: int | None = None
    if requester_raw:
        try:
            requester_id = int(requester_raw)
        except (TypeError, ValueError):
            return await _render_ticket_detail(
                request,
                current_user,
                ticket_id=ticket_id,
                error_message="Select a valid requester.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        requester = await user_repo.get_user_by_id(requester_id)
        if not requester:
            return await _render_ticket_detail(
                request,
                current_user,
                ticket_id=ticket_id,
                error_message="Select a valid requester.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

    assigned_user_id: int | None = None
    if assigned_raw:
        try:
            assigned_user_id = int(assigned_raw)
        except (TypeError, ValueError):
            return await _render_ticket_detail(
                request,
                current_user,
                ticket_id=ticket_id,
                error_message="Select a valid assignee.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        has_permission = await membership_repo.user_has_permission(
            assigned_user_id,
            HELPDESK_PERMISSION_KEY,
        )
        if not has_permission:
            return await _render_ticket_detail(
                request,
                current_user,
                ticket_id=ticket_id,
                error_message="Selected user cannot be assigned to tickets.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

    company_id: int | None = None
    if company_raw:
        try:
            company_id = int(company_raw)
        except (TypeError, ValueError):
            return await _render_ticket_detail(
                request,
                current_user,
                ticket_id=ticket_id,
                error_message="Select a valid company.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        company_record = await company_repo.get_company_by_id(company_id)
        if not company_record:
            return await _render_ticket_detail(
                request,
                current_user,
                ticket_id=ticket_id,
                error_message="Select a valid company.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

    final_company_id = company_id
    if company_raw is None:
        final_company_id = existing_company_id

    if requester_id is not None:
        if final_company_id is None:
            return await _render_ticket_detail(
                request,
                current_user,
                ticket_id=ticket_id,
                error_message="Link the ticket to a company before selecting a requester.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        allowed_requesters = await staff_repo.list_enabled_staff_users(final_company_id)
        allowed_ids = {
            int(option.get("id"))
            for option in allowed_requesters
            if option.get("id") is not None
        }
        if requester_id not in allowed_ids:
            return await _render_ticket_detail(
                request,
                current_user,
                ticket_id=ticket_id,
                error_message="Select a requester from the linked company's enabled staff list.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

    update_fields: dict[str, Any] = {
        "priority": priority_value,
        "requester_id": requester_id,
        "assigned_user_id": assigned_user_id,
        "company_id": company_id,
        "category": category_value,
        "external_reference": external_reference,
    }

    raw_asset_values = form.getlist("assetIds") if hasattr(form, "getlist") else []
    selected_asset_ids: list[int] = []
    for raw_value in raw_asset_values:
        try:
            asset_id_value = int(raw_value)
        except (TypeError, ValueError):
            continue
        if asset_id_value <= 0 or asset_id_value in selected_asset_ids:
            continue
        selected_asset_ids.append(asset_id_value)

    if selected_asset_ids and final_company_id is None:
        return await _render_ticket_detail(
            request,
            current_user,
            ticket_id=ticket_id,
            error_message="Link the ticket to a company before assigning assets.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    validated_asset_ids: list[int] = []
    if selected_asset_ids and final_company_id is not None:
        for asset_id in selected_asset_ids:
            asset = await assets_repo.get_asset_by_id(asset_id)
            if not asset or asset.get("company_id") != final_company_id:
                return await _render_ticket_detail(
                    request,
                    current_user,
                    ticket_id=ticket_id,
                    error_message="Assets must belong to the linked company.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            validated_asset_ids.append(asset_id)
    elif not selected_asset_ids:
        validated_asset_ids = []

    await tickets_repo.update_ticket(ticket_id, **update_fields)
    await tickets_repo.set_ticket_status(ticket_id, status_value)
    if description_raw is not None:
        await tickets_service.update_ticket_description(ticket_id, description_value)
    await tickets_service.refresh_ticket_ai_summary(ticket_id)
    await tickets_service.refresh_ticket_ai_tags(ticket_id)
    await tickets_service.broadcast_ticket_event(action="updated", ticket_id=ticket_id)
    await tickets_service.emit_ticket_updated_event(
        ticket_id,
        actor_type="technician",
        actor=current_user,
    )

    await tickets_repo.replace_ticket_assets(ticket_id, validated_asset_ids)

    message = quote("Ticket details updated.")
    destination = f"/admin/tickets/{ticket_id}?success={message}"
    if return_url and return_url.startswith(f"/admin/tickets/{ticket_id}"):
        separator = "&" if "?" in return_url else "?"
        destination = f"{return_url}{separator}success={message}"

    return RedirectResponse(url=destination, status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/tickets/{ticket_id}/ai/reprocess", response_class=JSONResponse)
async def admin_reprocess_ticket_ai(ticket_id: int, request: Request):
    current_user, redirect = await _require_helpdesk_page(request)
    if redirect:
        return redirect

    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    try:
        await tickets_service.refresh_ticket_ai_summary(ticket_id)
    except Exception as exc:  # pragma: no cover - defensive against unexpected failures
        log_error("Failed to queue ticket AI summary refresh", ticket_id=ticket_id, user_id=current_user.get("id"), error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to refresh AI summary.",
        ) from exc

    try:
        await tickets_service.refresh_ticket_ai_tags(ticket_id)
    except Exception as exc:  # pragma: no cover - defensive against unexpected failures
        log_error("Failed to queue ticket AI tags refresh", ticket_id=ticket_id, user_id=current_user.get("id"), error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to refresh AI tags.",
        ) from exc

    return JSONResponse(
        {
            "status": "queued",
            "message": "AI summary and tags will be regenerated shortly.",
        }
    )


@app.post("/admin/tickets/{ticket_id}/delete", response_class=HTMLResponse)
async def admin_delete_ticket(ticket_id: int, request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    try:
        await tickets_repo.delete_ticket(ticket_id)
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to delete ticket", ticket_id=ticket_id, error=str(exc))
        return await _render_ticket_detail(
            request,
            current_user,
            ticket_id=ticket_id,
            error_message="Unable to delete the ticket. Please try again.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    log_info(
        "Ticket deleted",
        ticket_id=ticket_id,
        deleted_by=current_user.get("id") if current_user else None,
    )
    await tickets_service.broadcast_ticket_event(action="deleted", ticket_id=ticket_id)

    message = quote(f"Ticket {ticket_id} deleted.")
    return RedirectResponse(
        url=f"/admin/tickets?success={message}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/tickets/bulk-delete", response_class=HTMLResponse)
async def admin_bulk_delete_tickets(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    form = await request.form()
    raw_ids = form.getlist("ticketIds")
    ticket_ids: list[int] = []
    seen: set[int] = set()
    for raw in raw_ids:
        try:
            identifier = int(raw)
        except (TypeError, ValueError):
            continue
        if identifier <= 0 or identifier in seen:
            continue
        seen.add(identifier)
        ticket_ids.append(identifier)

    if not ticket_ids:
        return await _render_tickets_dashboard(
            request,
            current_user,
            error_message="Select at least one ticket to delete.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        deleted_count = await tickets_repo.delete_tickets(ticket_ids)
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error(
            "Failed to bulk delete tickets",
            ticket_ids=ticket_ids,
            error=str(exc),
        )
        return await _render_tickets_dashboard(
            request,
            current_user,
            error_message="Unable to delete the selected tickets. Please try again.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if deleted_count == 0:
        return await _render_tickets_dashboard(
            request,
            current_user,
            error_message="No matching tickets were found to delete.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    log_info(
        "Tickets bulk deleted",
        deleted_count=deleted_count,
        deleted_by=current_user.get("id") if current_user else None,
        ticket_ids=ticket_ids,
    )
    await tickets_service.broadcast_ticket_event(action="deleted")

    message_suffix = "ticket" if deleted_count == 1 else "tickets"
    redirect_message = f"Deleted {deleted_count} {message_suffix}."
    if deleted_count < len(ticket_ids):
        redirect_message = (
            f"Deleted {deleted_count} {message_suffix}."
            " Some selected tickets were not found."
        )
    message = quote(redirect_message)

    return_url_raw = form.get("returnUrl")
    return_url = str(return_url_raw) if isinstance(return_url_raw, str) else ""
    if return_url and return_url.startswith("/") and not return_url.startswith("//"):
        separator = "&" if "?" in return_url else "?"
        destination = f"{return_url}{separator}success={message}"
    else:
        destination = f"/admin/tickets?success={message}"

    return RedirectResponse(url=destination, status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/tickets/{ticket_id}/replies", response_class=HTMLResponse)
async def admin_create_ticket_reply(ticket_id: int, request: Request):
    current_user, redirect = await _require_helpdesk_page(request)
    if redirect:
        return redirect
    form = await request.form()
    body_value = form.get("body", "")
    body_raw = str(body_value) if isinstance(body_value, str) else ""
    sanitized_body = sanitize_rich_text(body_raw)
    is_internal = str(form.get("isInternal", "")).lower() in {"1", "true", "on", "yes"}
    minutes_input_raw = form.get("minutesSpent", "")
    minutes_input = str(minutes_input_raw).strip() if isinstance(minutes_input_raw, str) else ""
    minutes_spent: int | None = None
    if minutes_input:
        try:
            minutes_candidate = int(minutes_input)
        except (TypeError, ValueError):
            return await _render_ticket_detail(
                request,
                current_user,
                ticket_id=ticket_id,
                error_message="Enter the time spent in minutes as a whole number.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if minutes_candidate < 0:
            return await _render_ticket_detail(
                request,
                current_user,
                ticket_id=ticket_id,
                error_message="Minutes cannot be negative.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if minutes_candidate > 1440:
            return await _render_ticket_detail(
                request,
                current_user,
                ticket_id=ticket_id,
                error_message="Minutes cannot exceed 1440 per reply.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        minutes_spent = minutes_candidate
    is_billable = str(form.get("isBillable", "")).lower() in {"1", "true", "on", "yes"}
    labour_type_raw = form.get("labourTypeId")
    labour_type_id: int | None = None
    if isinstance(labour_type_raw, str):
        labour_type_text = labour_type_raw.strip()
        if labour_type_text:
            try:
                labour_candidate = int(labour_type_text)
            except (TypeError, ValueError):
                return await _render_ticket_detail(
                    request,
                    current_user,
                    ticket_id=ticket_id,
                    error_message="Select a valid labour type.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            if labour_candidate <= 0:
                return await _render_ticket_detail(
                    request,
                    current_user,
                    ticket_id=ticket_id,
                    error_message="Select a valid labour type.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            labour_record = await labour_types_service.get_labour_type(labour_candidate)
            if not labour_record:
                return await _render_ticket_detail(
                    request,
                    current_user,
                    ticket_id=ticket_id,
                    error_message="Selected labour type could not be found.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            labour_type_id = labour_candidate
    if not sanitized_body.has_rich_content:
        return await _render_ticket_detail(
            request,
            current_user,
            ticket_id=ticket_id,
            error_message="Enter a reply before submitting.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    
    # Prevent adding time to billed tickets
    if ticket.get("xero_invoice_number"):
        return await _render_ticket_detail(
            request,
            current_user,
            ticket_id=ticket_id,
            error_message="Cannot add replies to a billed ticket. This ticket has been invoiced and closed.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    
    try:
        author_id = current_user.get("id")
        await tickets_repo.create_reply(
            ticket_id=ticket_id,
            author_id=author_id if isinstance(author_id, int) else None,
            body=sanitized_body.html,
            is_internal=is_internal,
            minutes_spent=minutes_spent,
            is_billable=is_billable,
            labour_type_id=labour_type_id,
        )
        if isinstance(author_id, int):
            await tickets_repo.add_watcher(ticket_id, author_id)
        await tickets_service.refresh_ticket_ai_summary(ticket_id)
        await tickets_service.refresh_ticket_ai_tags(ticket_id)
        await tickets_service.broadcast_ticket_event(action="reply", ticket_id=ticket_id)
        await tickets_service.emit_ticket_updated_event(
            ticket_id,
            actor_type="technician",
            actor=current_user,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to create ticket reply", error=str(exc))
        return await _render_ticket_detail(
            request,
            current_user,
            ticket_id=ticket_id,
            error_message="Unable to save the reply. Please try again.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return RedirectResponse(
        url=f"/admin/tickets/{ticket_id}?success=" + quote("Reply posted."),
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def _render_automations_dashboard(
    request: Request,
    user: dict[str, Any],
    *,
    status_filter: str | None = None,
    kind_filter: str | None = None,
    success_message: str | None = None,
    error_message: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    automations = await automation_repo.list_automations(
        status=status_filter,
        kind=kind_filter,
        limit=200,
    )
    status_counts = Counter((automation.get("status") or "inactive").lower() for automation in automations)
    kind_counts = Counter((automation.get("kind") or "scheduled").lower() for automation in automations)
    extra = {
        "title": "Automation orchestration",
        "automations": automations,
        "automation_status_counts": status_counts,
        "automation_kind_counts": kind_counts,
        "automation_filters": {"status": status_filter, "kind": kind_filter},
        "success_message": success_message,
        "error_message": error_message,
    }
    response = await _render_template("admin/automations.html", request, user, extra=extra)
    response.status_code = status_code
    return response


async def _render_automation_form(
    request: Request,
    user: dict[str, Any],
    *,
    kind: str,
    form_values: Mapping[str, Any] | None = None,
    success_message: str | None = None,
    error_message: str | None = None,
    status_code: int = status.HTTP_200_OK,
    mode: str = "create",
    automation_id: int | None = None,
) -> HTMLResponse:
    kind_normalised = "event" if str(kind).lower() == "event" else "scheduled"
    modules = await modules_service.list_trigger_action_modules()
    modules_payload = _serialise_for_json(modules)
    trigger_options = automations_service.list_trigger_events()
    mode_normalised = "edit" if str(mode).lower() == "edit" else "create"
    is_edit_mode = mode_normalised == "edit"
    base_values: dict[str, Any] = {
        "name": "",
        "description": "",
        "status": "inactive",
        "cadence": "",
        "cronExpression": "",
        "triggerEvent": "",
        "triggerFiltersRaw": "",
        "actionModule": "",
        "actionPayloadRaw": "",
    }
    if form_values:
        for key, value in form_values.items():
            if value is None:
                continue
            base_values[key] = value
    trigger_event = str(base_values.get("triggerEvent") or "").strip()
    option_values = {str(option.get("value") or "") for option in trigger_options}
    if trigger_event and trigger_event not in option_values:
        trigger_select_value = "__custom__"
        trigger_custom_value = trigger_event
    else:
        trigger_select_value = trigger_event
        trigger_custom_value = ""
    base_values["triggerSelectValue"] = trigger_select_value
    base_values["triggerCustomValue"] = trigger_custom_value
    if automation_id is not None:
        base_values.setdefault("id", automation_id)
    template_name = (
        "admin/automations_create_event.html"
        if kind_normalised == "event"
        else "admin/automations_create_scheduled.html"
    )
    if kind_normalised == "event":
        page_title = "Edit event automation" if is_edit_mode else "Create event automation"
        page_subtitle = (
            "Link webhook payloads and application events to integration modules for immediate processing."
        )
        alternate_link = None
        if not is_edit_mode:
            alternate_link = {
                "url": "/admin/automations/create/scheduled",
                "label": "Switch to scheduled automation",
            }
    else:
        page_title = "Edit scheduled automation" if is_edit_mode else "Create scheduled automation"
        page_subtitle = (
            "Configure cadence, triggers, and action payloads to run on a predictable rhythm."
        )
        alternate_link = None
        if not is_edit_mode:
            alternate_link = {
                "url": "/admin/automations/create/event",
                "label": "Switch to event automation",
            }
    form_action = (
        f"/admin/automations/{automation_id}"
        if is_edit_mode and automation_id is not None
        else "/admin/automations"
    )
    submit_label = "Update automation" if is_edit_mode else "Save automation"
    extra = {
        "title": page_title,
        "automation_modules": modules_payload,
        "automation_trigger_options": trigger_options,
        "form_values": base_values,
        "kind": kind_normalised,
        "success_message": success_message,
        "error_message": error_message,
        "page_title": page_title,
        "page_subtitle": page_subtitle,
        "alternate_link": alternate_link,
        "form_action": form_action,
        "submit_label": submit_label,
        "is_edit_mode": is_edit_mode,
        "automation_id": automation_id,
    }
    response = await _render_template(template_name, request, user, extra=extra)
    response.status_code = status_code
    return response


def _automation_to_form_values(automation: Mapping[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {
        "name": str(automation.get("name") or ""),
        "description": str(automation.get("description") or ""),
        "status": str(automation.get("status") or "inactive"),
        "cadence": str(automation.get("cadence") or ""),
        "cronExpression": str(automation.get("cron_expression") or ""),
        "triggerEvent": str(automation.get("trigger_event") or ""),
        "triggerFiltersRaw": "",
        "actionModule": str(automation.get("action_module") or ""),
        "actionPayloadRaw": "",
    }
    filters = automation.get("trigger_filters")
    if filters is not None:
        try:
            values["triggerFiltersRaw"] = json.dumps(filters, indent=2, sort_keys=True)
        except (TypeError, ValueError):
            values["triggerFiltersRaw"] = json.dumps(filters, default=str)
    payload = automation.get("action_payload")
    if payload is not None:
        try:
            values["actionPayloadRaw"] = json.dumps(payload, indent=2, sort_keys=True)
        except (TypeError, ValueError):
            values["actionPayloadRaw"] = json.dumps(payload, default=str)
    return values


def _parse_automation_form_submission(
    form: FormData,
    *,
    kind: str,
) -> tuple[dict[str, Any] | None, dict[str, Any], str | None, int]:
    kind_normalised = "event" if str(kind).lower() == "event" else "scheduled"
    name = str(form.get("name", "")).strip()
    description_value = str(form.get("description", "")).strip()
    status_raw = str(form.get("status", "")).strip().lower()
    status_value = "active" if status_raw == "active" else "inactive"
    cadence_raw = str(form.get("cadence", "")).strip()
    cron_raw = str(form.get("cronExpression", "")).strip()
    trigger_event_raw = str(form.get("triggerEvent", "")).strip()
    trigger_filters_raw = str(form.get("triggerFilters", "")).strip()
    action_module_raw = str(form.get("actionModule", "")).strip()
    action_payload_raw = str(form.get("actionPayload", "")).strip()

    form_state = {
        "name": name,
        "description": description_value,
        "status": status_value,
        "cadence": cadence_raw,
        "cronExpression": cron_raw,
        "triggerEvent": trigger_event_raw,
        "triggerFiltersRaw": trigger_filters_raw,
        "actionModule": action_module_raw,
        "actionPayloadRaw": action_payload_raw,
    }

    if not name:
        return None, form_state, "Enter an automation name.", status.HTTP_400_BAD_REQUEST

    cadence = cadence_raw or None
    cron_expression = cron_raw or None
    trigger_event = trigger_event_raw or None
    action_module = action_module_raw or None

    try:
        trigger_filters = json.loads(trigger_filters_raw) if trigger_filters_raw else None
    except json.JSONDecodeError:
        return (
            None,
            form_state,
            "Trigger filters must be valid JSON.",
            status.HTTP_400_BAD_REQUEST,
        )

    try:
        action_payload = json.loads(action_payload_raw) if action_payload_raw else None
    except json.JSONDecodeError:
        return (
            None,
            form_state,
            "Action payload must be valid JSON.",
            status.HTTP_400_BAD_REQUEST,
        )

    normalised_actions: list[dict[str, Any]] = []
    if isinstance(action_payload, dict) and "actions" in action_payload:
        actions_value = action_payload.get("actions")
        if not isinstance(actions_value, list):
            return (
                None,
                form_state,
                "Trigger actions must be provided as a list.",
                status.HTTP_400_BAD_REQUEST,
            )
        for index, entry in enumerate(actions_value, start=1):
            if not isinstance(entry, dict):
                return (
                    None,
                    form_state,
                    f"Trigger action {index} is invalid.",
                    status.HTTP_400_BAD_REQUEST,
                )
            module_value = str(entry.get("module") or "").strip()
            if not module_value:
                return (
                    None,
                    form_state,
                    f"Select an action module for trigger action {index}.",
                    status.HTTP_400_BAD_REQUEST,
                )
            payload_value = entry.get("payload") or {}
            if not isinstance(payload_value, dict):
                return (
                    None,
                    form_state,
                    f"Trigger action {index} payload must be an object.",
                    status.HTTP_400_BAD_REQUEST,
                )
            normalised_actions.append({"module": module_value, "payload": payload_value})
        updated_payload = dict(action_payload)
        updated_payload["actions"] = normalised_actions
        action_payload = updated_payload
        action_module = normalised_actions[0]["module"] if normalised_actions else None
        form_state["actionPayloadRaw"] = json.dumps(action_payload)
        form_state["actionModule"] = action_module or ""

    data = {
        "name": name,
        "description": description_value or None,
        "kind": kind_normalised,
        "cadence": cadence if kind_normalised == "scheduled" else None,
        "cron_expression": cron_expression if kind_normalised == "scheduled" else None,
        "trigger_event": trigger_event,
        "trigger_filters": trigger_filters,
        "action_module": action_module,
        "action_payload": action_payload,
        "status": status_value,
    }

    return data, form_state, None, status.HTTP_200_OK


@app.get("/admin/automations", response_class=HTMLResponse)
async def admin_automations_page(
    request: Request,
    status: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    success: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    return await _render_automations_dashboard(
        request,
        current_user,
        status_filter=status,
        kind_filter=kind,
        success_message=_sanitize_message(success),
        error_message=_sanitize_message(error),
    )


@app.get("/admin/automations/create/scheduled", response_class=HTMLResponse)
async def admin_create_scheduled_automation_page(
    request: Request,
    success: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    return await _render_automation_form(
        request,
        current_user,
        kind="scheduled",
        success_message=_sanitize_message(success),
        error_message=_sanitize_message(error),
    )


@app.get("/admin/automations/create/event", response_class=HTMLResponse)
async def admin_create_event_automation_page(
    request: Request,
    success: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    return await _render_automation_form(
        request,
        current_user,
        kind="event",
        success_message=_sanitize_message(success),
        error_message=_sanitize_message(error),
    )


@app.post("/admin/automations", response_class=HTMLResponse)
async def admin_create_automation(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    name = str(form.get("name", "")).strip()
    description_value = str(form.get("description", "")).strip()
    kind_raw = str(form.get("kind", "")).strip()
    kind = "event" if kind_raw.lower() == "event" else "scheduled"
    data, form_state, error_message, error_status = _parse_automation_form_submission(form, kind=kind)
    if error_message:
        return await _render_automation_form(
            request,
            current_user,
            kind=kind,
            form_values=form_state,
            error_message=error_message,
            status_code=error_status,
        )
    next_run = None
    if data.get("status") == "active":
        next_run = automations_service.calculate_next_run(data)
    try:
        record = await automation_repo.create_automation(next_run_at=next_run, **data)
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to create automation", error=str(exc))
        return await _render_automation_form(
            request,
            current_user,
            kind=kind,
            form_values=form_state,
            error_message="Unable to create automation. Please try again.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    if record and record.get("status") == "active":
        await automations_service.refresh_schedule(int(record["id"]))
    return RedirectResponse(
        url="/admin/automations?success=" + quote("Automation created."),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.get("/admin/automations/{automation_id}/edit", response_class=HTMLResponse)
async def admin_edit_automation_page(automation_id: int, request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    automation = await automation_repo.get_automation(automation_id)
    if not automation:
        return RedirectResponse(
            url="/admin/automations?error=" + quote("Automation not found."),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    kind = str(automation.get("kind") or "scheduled")
    form_defaults = _automation_to_form_values(automation)
    return await _render_automation_form(
        request,
        current_user,
        kind=kind,
        form_values=form_defaults,
        mode="edit",
        automation_id=automation_id,
    )


@app.post("/admin/automations/{automation_id}", response_class=HTMLResponse)
async def admin_update_automation(automation_id: int, request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    automation = await automation_repo.get_automation(automation_id)
    if not automation:
        return RedirectResponse(
            url="/admin/automations?error=" + quote("Automation not found."),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    form = await request.form()
    kind = str(automation.get("kind") or "scheduled")
    data, form_state, error_message, error_status = _parse_automation_form_submission(form, kind=kind)
    if error_message:
        return await _render_automation_form(
            request,
            current_user,
            kind=kind,
            form_values=form_state,
            error_message=error_message,
            status_code=error_status,
            mode="edit",
            automation_id=automation_id,
        )
    update_fields = dict(data)
    if update_fields.get("status") != "active":
        update_fields["next_run_at"] = None
    try:
        await automation_repo.update_automation(automation_id, **update_fields)
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to update automation", automation_id=automation_id, error=str(exc))
        return await _render_automation_form(
            request,
            current_user,
            kind=kind,
            form_values=form_state,
            error_message="Unable to update automation. Please try again.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            mode="edit",
            automation_id=automation_id,
        )
    if update_fields.get("status") == "active":
        await automations_service.refresh_schedule(automation_id)
    else:
        await automation_repo.set_next_run(automation_id, None)
    return RedirectResponse(
        url="/admin/automations?success=" + quote("Automation updated."),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/automations/{automation_id}/status", response_class=HTMLResponse)
async def admin_update_automation_status(automation_id: int, request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    status_value = str(form.get("status", "")).strip()
    if status_value not in {"active", "inactive"}:
        return await _render_automations_dashboard(
            request,
            current_user,
            error_message="Select a valid automation status.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    automation = await automation_repo.get_automation(automation_id)
    if not automation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Automation not found")
    await automation_repo.update_automation(automation_id, status=status_value)
    await automations_service.refresh_schedule(automation_id)
    return RedirectResponse(
        url="/admin/automations?success=" + quote(f"Automation {automation_id} updated."),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/automations/{automation_id}/execute", response_class=HTMLResponse)
async def admin_execute_automation(automation_id: int, request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    try:
        result = await automations_service.execute_now(automation_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    message = f"Automation {automation_id} executed with status {result.get('status')}."
    return RedirectResponse(
        url="/admin/automations?success=" + quote(message),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/automations/{automation_id}/delete", response_class=HTMLResponse)
async def admin_delete_automation(automation_id: int, request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    automation = await automation_repo.get_automation(automation_id)
    if not automation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Automation not found")

    try:
        await automation_repo.delete_automation(automation_id)
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error(
            "Failed to delete automation",
            automation_id=automation_id,
            error=str(exc),
        )
        return await _render_automations_dashboard(
            request,
            current_user,
            error_message="Unable to delete the automation. Please try again.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    log_info(
        "Automation deleted",
        automation_id=automation_id,
        deleted_by=current_user.get("id") if isinstance(current_user, Mapping) else None,
    )

    message = quote(f"Automation {automation_id} deleted.")
    return RedirectResponse(
        url=f"/admin/automations?success={message}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.get("/admin/subscriptions", response_class=HTMLResponse)
async def admin_subscriptions_page(
    request: Request,
    status_filter: str | None = Query(default=None, alias="status"),
    category_filter: str | None = Query(default=None, alias="category"),
    success: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    """Admin page for viewing and managing subscriptions."""
    current_user, membership, redirect = await _require_administration_access(request)
    if redirect:
        return redirect
    
    # Check that user has both License and Cart permissions
    has_license = bool(membership and membership.get("can_manage_licenses"))
    has_cart = bool(membership and membership.get("can_access_cart"))
    
    if not (current_user.get("is_super_admin") or (has_license and has_cart)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="License and Cart permissions required to access subscriptions"
        )
    
    # Get active company ID
    active_company_id = getattr(request.state, "active_company_id", None)
    
    # Load subscriptions
    from app.repositories import subscriptions as subscriptions_repo
    from app.repositories import subscription_categories as categories_repo
    from collections import Counter
    
    try:
        subscriptions = await subscriptions_repo.list_subscriptions(
            customer_id=active_company_id if not current_user.get("is_super_admin") else None,
            status=status_filter,
            category_id=int(category_filter) if category_filter else None,
            limit=500,
        )
        
        categories = await categories_repo.list_categories()
        
        # Count by status
        status_counts = Counter(sub.get("status") for sub in subscriptions)
        
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to load subscriptions", error=str(exc))
        subscriptions = []
        categories = []
        status_counts = {}
    
    extra = {
        "title": "Subscriptions",
        "subscriptions": subscriptions,
        "categories": categories,
        "filters": {
            "status": status_filter,
            "category": category_filter,
        },
        "status_counts": status_counts,
        "success_message": _sanitize_message(success),
        "error_message": _sanitize_message(error),
        "is_super_admin": current_user.get("is_super_admin", False),
    }
    
    return await _render_template("admin/subscriptions.html", request, current_user, extra=extra)


async def _render_modules_dashboard(
    request: Request,
    user: dict[str, Any],
    *,
    success_message: str | None = None,
    error_message: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    modules = await modules_service.list_modules()
    extra = {
        "title": "Integration modules",
        "modules": modules,
        "success_message": success_message,
        "error_message": error_message,
    }
    response = await _render_template("admin/modules.html", request, user, extra=extra)
    response.status_code = status_code
    return response


async def _render_imap_dashboard(
    request: Request,
    user: dict[str, Any],
    *,
    editing_account_id: int | None = None,
    success_message: str | None = None,
    error_message: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    accounts = await imap_service.list_accounts()
    editing_account = None
    if editing_account_id is not None:
        for account in accounts:
            if account.get("id") == editing_account_id:
                editing_account = account
                break
        if not editing_account:
            editing_account = await imap_service.get_account(editing_account_id)
    companies = await company_repo.list_companies()
    extra = {
        "title": "IMAP mailboxes",
        "accounts": accounts,
        "editing_account": editing_account,
        "companies": companies,
        "success_message": success_message,
        "error_message": error_message,
    }
    response = await _render_template("admin/imap.html", request, user, extra=extra)
    response.status_code = status_code
    return response


@app.get("/admin/modules", response_class=HTMLResponse)
async def admin_modules_page(
    request: Request,
    success: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    return await _render_modules_dashboard(
        request,
        current_user,
        success_message=_sanitize_message(success),
        error_message=_sanitize_message(error),
    )


@app.post("/admin/modules/{slug}", response_class=HTMLResponse)
async def admin_update_module(slug: str, request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    raw_enabled = form.get("enabled")
    enabled = False
    if raw_enabled is not None:
        if isinstance(raw_enabled, str):
            enabled = raw_enabled.strip().lower() not in {"", "0", "false", "off"}
        else:
            enabled = bool(raw_enabled)
    settings: dict[str, Any] = {}
    for key, value in form.multi_items():
        if not key.startswith("settings."):
            continue
        field = key.split(".", 1)[1]
        if field in settings:
            existing = settings[field]
            if isinstance(existing, list):
                existing.append(value)
            else:
                settings[field] = [existing, value]
        else:
            settings[field] = value
    try:
        await modules_service.update_module(slug, enabled=enabled, settings=settings)
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to update integration module", slug=slug, error=str(exc))
        return await _render_modules_dashboard(
            request,
            current_user,
            error_message="Unable to update module configuration. Please verify the settings.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return RedirectResponse(
        url=f"/admin/modules?success=" + quote(f"Module {slug} updated."),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.get("/admin/modules/imap", response_class=HTMLResponse)
async def admin_imap_accounts_page(
    request: Request,
    account_id: int | None = Query(default=None, alias="accountId"),
    success: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    return await _render_imap_dashboard(
        request,
        current_user,
        editing_account_id=account_id,
        success_message=_sanitize_message(success),
        error_message=_sanitize_message(error),
    )


def _form_bool(form: Mapping[str, Any], key: str) -> bool:
    value = form.get(key)
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "off"}
    return bool(value)


@app.post("/admin/modules/imap/accounts", response_class=HTMLResponse)
async def admin_create_imap_account(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    data: dict[str, Any] = {
        "name": form.get("name", ""),
        "host": form.get("host", ""),
        "port": form.get("port", ""),
        "username": form.get("username", ""),
        "password": form.get("password", ""),
        "folder": form.get("folder", ""),
        "schedule_cron": form.get("scheduleCron", ""),
        "filter_query": form.get("filterQuery"),
        "process_unread_only": _form_bool(form, "processUnreadOnly"),
        "mark_as_read": _form_bool(form, "markAsRead"),
        "active": _form_bool(form, "active"),
    }
    priority_value = form.get("priority")
    if priority_value not in (None, ""):
        try:
            data["priority"] = int(priority_value)
        except (TypeError, ValueError):
            return await _render_imap_dashboard(
                request,
                current_user,
                success_message=None,
                error_message="Priority must be a whole number.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
    company_id = form.get("companyId")
    if company_id not in (None, ""):
        try:
            data["company_id"] = int(company_id)
        except (TypeError, ValueError):
            return await _render_imap_dashboard(
                request,
                current_user,
                success_message=None,
                error_message="Company selection is invalid.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
    try:
        account = await imap_service.create_account(data)
    except ValueError as exc:
        return await _render_imap_dashboard(
            request,
            current_user,
            success_message=None,
            error_message=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to create IMAP account", error=str(exc))
        return await _render_imap_dashboard(
            request,
            current_user,
            success_message=None,
            error_message="Unable to create the IMAP account. Please verify the configuration and try again.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    message = quote(f"Mailbox {account.get('name') or account.get('username') or 'created'} added.")
    return RedirectResponse(
        url=f"/admin/modules/imap?success={message}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/modules/imap/accounts/{account_id}", response_class=HTMLResponse)
async def admin_update_imap_account(account_id: int, request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    updates: dict[str, Any] = {}
    for field in ("name", "host", "port", "username"):
        if field in form:
            value = form.get(field)
            if value is None:
                continue
            if field == "port" and value == "":
                continue
            updates[field] = value
    password_value = form.get("password")
    if password_value:
        updates["password"] = password_value
    if "folder" in form:
        updates["folder"] = form.get("folder")
    if "scheduleCron" in form:
        updates["schedule_cron"] = form.get("scheduleCron")
    if "filterQuery" in form:
        updates["filter_query"] = form.get("filterQuery")
    updates["process_unread_only"] = _form_bool(form, "processUnreadOnly")
    updates["mark_as_read"] = _form_bool(form, "markAsRead")
    updates["active"] = _form_bool(form, "active")
    priority_value = form.get("priority")
    if priority_value not in (None, ""):
        try:
            updates["priority"] = int(priority_value)
        except (TypeError, ValueError):
            return await _render_imap_dashboard(
                request,
                current_user,
                editing_account_id=account_id,
                success_message=None,
                error_message="Priority must be a whole number.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
    company_id = form.get("companyId")
    if company_id in (None, ""):
        updates["company_id"] = None
    else:
        try:
            updates["company_id"] = int(company_id)
        except (TypeError, ValueError):
            return await _render_imap_dashboard(
                request,
                current_user,
                editing_account_id=account_id,
                success_message=None,
                error_message="Company selection is invalid.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
    try:
        account = await imap_service.update_account(account_id, updates)
    except ValueError as exc:
        return await _render_imap_dashboard(
            request,
            current_user,
            editing_account_id=account_id,
            success_message=None,
            error_message=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to update IMAP account", account_id=account_id, error=str(exc))
        return await _render_imap_dashboard(
            request,
            current_user,
            editing_account_id=account_id,
            success_message=None,
            error_message="Unable to update the IMAP account. Please review the settings and try again.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    message = quote(f"Mailbox {account.get('name') or account.get('username') or account_id} updated.")
    return RedirectResponse(
        url=f"/admin/modules/imap?success={message}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/modules/imap/accounts/{account_id}/clone", response_class=HTMLResponse)
async def admin_clone_imap_account(account_id: int, request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    try:
        account = await imap_service.clone_account(account_id)
    except LookupError as exc:
        return await _render_imap_dashboard(
            request,
            current_user,
            success_message=None,
            error_message=str(exc),
            status_code=status.HTTP_404_NOT_FOUND,
        )
    except ValueError as exc:
        return await _render_imap_dashboard(
            request,
            current_user,
            success_message=None,
            error_message=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to clone IMAP account", account_id=account_id, error=str(exc))
        return await _render_imap_dashboard(
            request,
            current_user,
            success_message=None,
            error_message="Unable to clone the IMAP account at this time.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    label = account.get("name") or f"Mailbox {account_id} copy"
    message = quote(f"Mailbox {label} cloned.")
    return RedirectResponse(
        url=f"/admin/modules/imap?success={message}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/modules/imap/accounts/{account_id}/delete", response_class=HTMLResponse)
async def admin_delete_imap_account(account_id: int, request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    account = await imap_service.get_account(account_id)
    try:
        await imap_service.delete_account(account_id)
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to delete IMAP account", account_id=account_id, error=str(exc))
        return await _render_imap_dashboard(
            request,
            current_user,
            success_message=None,
            error_message="Unable to delete the IMAP account at this time.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    label = account.get("name") if account else f"#{account_id}"
    message = quote(f"Mailbox {label} deleted.")
    return RedirectResponse(
        url=f"/admin/modules/imap?success={message}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/modules/imap/accounts/{account_id}/sync", response_class=HTMLResponse)
async def admin_sync_imap_account(account_id: int, request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    result = await imap_service.sync_account(account_id)
    status_value = str(result.get("status") or "").lower()
    processed = int(result.get("processed") or 0)
    error_count = len(result.get("errors") or [])
    if status_value in {"error"}:
        message = result.get("error") or "IMAP synchronisation failed."
        return RedirectResponse(
            url=f"/admin/modules/imap?error={quote(message)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if status_value == "skipped":
        message = result.get("reason") or "IMAP synchronisation skipped."
        return RedirectResponse(
            url=f"/admin/modules/imap?error={quote(message)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if status_value == "completed_with_errors" and error_count:
        message = quote(
            f"IMAP sync completed with {error_count} issue{'s' if error_count != 1 else ''}. Imported {processed} messages."
        )
        return RedirectResponse(
            url=f"/admin/modules/imap?success={message}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    message = quote(f"IMAP sync imported {processed} message{'s' if processed != 1 else ''}.")
    return RedirectResponse(
        url=f"/admin/modules/imap?success={message}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/modules/tacticalrmm/push-companies", response_class=HTMLResponse)
async def admin_push_companies_to_tactical_rmm(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    try:
        await modules_service.ensure_tacticalrmm_ready()
    except ValueError as exc:
        log_error("Unable to synchronise Tactical RMM companies", error=str(exc))
        return await _render_modules_dashboard(
            request,
            current_user,
            error_message=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to synchronise Tactical RMM companies", error=str(exc))
        return await _render_modules_dashboard(
            request,
            current_user,
            error_message="Unable to synchronise companies with Tactical RMM. Please verify the module configuration.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    task_id = uuid4().hex

    async def _on_success(summary: Mapping[str, Any]) -> None:
        created_clients = summary.get("created_clients") or []
        created_sites = summary.get("created_sites") or []
        existing_clients = summary.get("existing_clients") or []
        skipped = summary.get("skipped") or []
        errors = summary.get("errors") or []
        processed = int(summary.get("processed_companies") or 0)

        site_created_count = len(created_sites)
        created_count = len(created_clients)
        existing_count = len(existing_clients)
        skipped_count = len(skipped)
        error_count = len(errors)

        log_info(
            "Tactical RMM company synchronisation completed",
            task_id=task_id,
            processed=processed,
            created_clients=created_count,
            site_creations=site_created_count,
            existing_clients=existing_count,
            skipped=skipped_count,
            errors=error_count,
        )

        if error_count:
            example = errors[0]
            detail = example.get("error") or "Unknown error"
            log_error(
                "Tactical RMM synchronisation encountered errors",
                task_id=task_id,
                error_count=error_count,
                example=detail,
            )

    async def _on_error(exc: Exception) -> None:
        log_error(
            "Tactical RMM company synchronisation failed",
            task_id=task_id,
            error=str(exc),
        )

    background_tasks.queue_background_task(
        lambda: modules_service.push_companies_to_tacticalrmm(),
        task_id=task_id,
        description="tacticalrmm-company-sync",
        on_complete=_on_success,
        on_error=_on_error,
    )

    log_info(
        "Queued Tactical RMM company synchronisation",
        task_id=task_id,
        user_id=current_user.get("id"),
        request_path=str(request.url),
    )

    success_message = f"Tactical RMM company synchronisation queued. Task ID: {task_id[:8]}"
    query = f"success={quote(success_message)}"
    redirect_url = f"/admin/modules?{query}" if query else "/admin/modules"

    return RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/modules/{slug}/test", response_class=HTMLResponse)
async def admin_test_module(slug: str, request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    result = await modules_service.test_module(slug)
    if result.get("status") == "error":
        return RedirectResponse(
            url=f"/admin/modules?error=" + quote(result.get("error") or "Module test failed."),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    success_message = "Module test succeeded."
    if slug == "xero":
        details = result.get("details")
        if isinstance(details, Mapping):
            tenant_id = details.get("discovered_tenant_id") or details.get("tenant_id")
            if tenant_id:
                success_message += f" Tenant ID: {tenant_id}"
    return RedirectResponse(
        url=f"/admin/modules?success=" + quote(success_message),
        status_code=status.HTTP_303_SEE_OTHER,
    )


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

    is_first_user = user_count == 0

    context = {
        "request": request,
        "app_name": settings.app_name,
        "current_year": datetime.utcnow().year,
        "title": "Create super administrator" if is_first_user else "Create your account",
        "is_first_user": is_first_user,
    }
    return templates.TemplateResponse("auth/register.html", context)


@app.get("/health")
async def health_check():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}
