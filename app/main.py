from __future__ import annotations

import asyncio
import base64
import json
import math
import random
import re
import secrets
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from html import escape
from pathlib import Path, PurePosixPath
from typing import Any, cast
from urllib.parse import parse_qsl, quote, urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from itsdangerous import BadSignature, URLSafeSerializer
from pydantic import ValidationError
from starlette.datastructures import FormData, URL
from http import HTTPStatus

from app.api.routes import (
    agent,
    api_keys,
    asset_custom_fields,
    audit_logs,
    auth,
    automations as automations_api,
    backup_jobs as backup_jobs_api,
    bc5,
    bc11,
    bcp,
    business_continuity_plans as bc_plans_api,
    call_recordings as call_recordings_api,
    companies,
    essential8 as essential8_api,
    compliance_checks as compliance_checks_api,
    forms as forms_api,
    invoices as invoices_api,
    issues as issues_api,
    knowledge_base as knowledge_base_api,
    licenses as licenses_api,
    memberships,
    m365 as m365_api,
    message_templates as message_templates_api,
    modules as modules_api,
    notifications,
    orders as orders_api,
    ports,
    quotes as quotes_api,
    scheduler as scheduler_api,
    roles,
    service_status as service_status_api,
    staff as staff_api,
    subscriptions as subscriptions_api,
    tag_exclusions,
    tickets as tickets_api,
    tray as tray_api,
    users,
    system,
    xero,
    chat as chat_api,
    features as features_api,
)
from uuid import uuid4

from app.core.config import get_settings, get_templates_config
from app.core.database import db
from app.core.features import init_registry
from app.core.logging import configure_logging, log_error, log_info, log_warning
from loguru import logger
from app.repositories import audit_logs as audit_repo
from app.repositories import api_keys as api_key_repo
from app.repositories import auth as auth_repo
from app.repositories import assets as assets_repo
from app.repositories import billing_contacts as billing_contacts_repo
from app.repositories import companies as company_repo
from app.repositories import company_memberships as membership_repo
from app.repositories import company_recurring_invoice_items as recurring_items_repo
from app.repositories import change_log as change_log_repo
from app.repositories import assets as asset_repo
from app.repositories import licenses as license_repo
from app.repositories import license_sku_friendly_names as sku_friendly_repo
from app.repositories import forms as forms_repo
from app.repositories import knowledge_base as knowledge_base_repo
from app.repositories import m365 as m365_repo
from app.repositories import notifications as notifications_repo
from app.repositories import reporting as reporting_repo
from app.repositories import roles as role_repo
from app.repositories import shop as shop_repo
from app.repositories import stock_feed as stock_feed_repo
from app.repositories import cart as cart_repo
from app.repositories import scheduled_tasks as scheduled_tasks_repo
from app.repositories import subscription_categories as subscription_categories_repo
from app.repositories import subscriptions as subscriptions_repo
from app.repositories import staff as staff_repo
from app.repositories import staff_onboarding_workflows as staff_workflow_repo
from app.repositories import staff_requests as staff_requests_repo
from app.repositories import pending_staff_access as pending_staff_access_repo
from app.repositories import tickets as tickets_repo
from app.repositories import ticket_attachments as attachments_repo
from app.repositories import ticket_views as ticket_views_repo
from app.repositories import ticket_statuses as ticket_status_repo
from app.repositories import automations as automation_repo
from app.repositories import integration_modules as integration_modules_repo
from app.repositories import user_companies as user_company_repo
from app.repositories import users as user_repo
from app.repositories import issues as issues_repo
from app.repositories import asset_custom_fields as asset_custom_fields_repo
from app.repositories import staff_custom_fields as staff_custom_fields_repo
from app.repositories import site_settings as site_settings_repo
from app.schemas.staff_onboarding_workflows import (
    CompanyWorkflowPolicyUpsertSchema,
    WorkflowConfigSchema,
)
from app.security.cache_control import CacheControlMiddleware
from app.security.csrf import CSRFMiddleware
from app.security.encryption import decrypt_secret, encrypt_secret
from app.security.ip_whitelist import IPWhitelistMiddleware
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
from app.services.scheduler import scheduler_service, COMMANDS_BY_MODULE
from app.services import audit as audit_service
from app.services import background as background_tasks
from app.services import automations as automations_service
from app.services import change_log as change_log_service
from app.services import company_domains
from app.services import company_access
from app.services import dashboard as dashboard_service
from app.services import email as email_service
from app.services import m365_mail as m365_mail_service
from app.services import knowledge_base as knowledge_base_service
from app.services import m365 as m365_service
from app.services import cis_benchmark as cis_benchmark_service
from app.services import m365_best_practices as m365_best_practices_service
from app.services import modules as modules_service
from app.services import notification_event_settings as event_settings_service
from app.services import message_templates as message_templates_service
from app.services import products as products_service
from app.services import shop as shop_service
from app.services import shop_packages as shop_packages_service
from app.services import staff_access as staff_access_service
from app.services import staff_field_config as staff_field_config_service
from app.services import staff_onboarding_workflows as staff_onboarding_workflow_service
from app.services import labour_types as labour_types_service
from app.services import subscription_shop_integration
from app.services import tickets as tickets_service
from app.services import ticket_attachments as attachments_service
from app.services import template_variables
from app.services import webhook_monitor
from app.services import xero as xero_service
from app.services import issues as issues_service
from app.services import reports as reports_service
from app.services import reporting as reporting_service
from app.services import service_status as service_status_service
from app.services import backup_jobs as backup_jobs_service
from app.services import impersonation as impersonation_service
from app.services.realtime import refresh_notifier
from app.services.redis import close_redis_client, get_redis_client
from app.services.sanitization import sanitize_rich_text
from app.services.opnform import (
    OpnformValidationError,
    extract_allowed_host,
    normalize_opnform_embed_code,
    normalize_opnform_form_url,
)
from app.services.file_storage import delete_stored_file, store_product_image, store_report_cover_image

configure_logging()
settings = get_settings()
templates_config = get_templates_config()
oauth_state_serializer = URLSafeSerializer(settings.secret_key, salt="m365-oauth")
PWA_THEME_COLOR = "#0f172a"
PWA_BACKGROUND_COLOR = "#0f172a"
SHOP_LOW_STOCK_THRESHOLD = 5
_TICKET_DASHBOARD_REFERENCE_TTL_SECONDS = 60
_ticket_dashboard_reference_cache: dict[str, Any] = {
    "expires_at": None,
    "modules": [],
    "companies": [],
    "technicians": [],
    "company_lookup": {},
    "user_lookup": {},
}
_ticket_dashboard_reference_lock = asyncio.Lock()
_M365_PROVISION_PKCE_TTL_SECONDS = 600
_m365_provision_pkce_cache: dict[str, tuple[str, datetime]] = {}
_m365_provision_pkce_lock = asyncio.Lock()


async def _store_m365_provision_code_verifier(verifier: str) -> str:
    """Store a one-time PKCE code verifier for the M365 provision flow."""

    verifier_id = secrets.token_urlsafe(24)
    redis_client = get_redis_client()
    if redis_client is not None:
        await redis_client.setex(
            f"m365:provision:pkce:{verifier_id}",
            _M365_PROVISION_PKCE_TTL_SECONDS,
            verifier,
        )
        return verifier_id

    expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=_M365_PROVISION_PKCE_TTL_SECONDS
    )
    async with _m365_provision_pkce_lock:
        _m365_provision_pkce_cache[verifier_id] = (verifier, expires_at)
    return verifier_id


async def _pop_m365_provision_code_verifier(verifier_id: str | None) -> str | None:
    """Return and remove a previously stored one-time PKCE code verifier."""

    if not verifier_id:
        return None

    redis_client = get_redis_client()
    if redis_client is not None:
        key = f"m365:provision:pkce:{verifier_id}"
        pipeline = redis_client.pipeline()
        pipeline.get(key)
        pipeline.delete(key)
        value, _ = await pipeline.execute()
        if not value:
            return None
        return str(value)

    now = datetime.now(timezone.utc)
    async with _m365_provision_pkce_lock:
        stale_keys = [
            key
            for key, (_, expires_at) in _m365_provision_pkce_cache.items()
            if expires_at <= now
        ]
        for key in stale_keys:
            _m365_provision_pkce_cache.pop(key, None)
        entry = _m365_provision_pkce_cache.pop(verifier_id, None)
    if entry is None:
        return None
    verifier, expires_at = entry
    if expires_at <= now:
        return None
    return verifier

# Load app version for cache busting static files
_APP_VERSION = ""
_version_file = Path(__file__).resolve().parent.parent / "version.txt"
if _version_file.is_file():
    try:
        _APP_VERSION = _version_file.read_text().strip()
    except Exception:
        pass

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


def _random_daily_cron() -> str:
    """Return a randomised daily cron expression (``MM HH * * *``)."""
    minute = random.randint(0, 59)
    hour = random.randint(0, 23)
    return f"{minute} {hour} * * *"


def _opnform_base_url() -> str | None:
    if settings.opnform_base_url:
        base = str(settings.opnform_base_url)
        return base if base.endswith("/") else f"{base}/"
    return "/myforms/"


def _build_xero_redirect_uri() -> str:
    """Build Xero OAuth redirect URI using PORTAL_URL setting.

    Delegates to the xero routes module so the logic is defined in one place.
    """
    from app.api.routes.xero import _build_xero_redirect_uri as _xero_uri

    return _xero_uri()


async def _store_pkce_verifier(code_verifier: str) -> str:
    """Store a PKCE code_verifier server-side and return an opaque handle."""

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=_PKCE_VERIFIER_TTL_SECONDS)
    handle = secrets.token_urlsafe(32)
    async with _pkce_verifier_store_lock:
        # Opportunistically purge expired entries.
        expired_handles = [
            key for key, (_, expiry) in _pkce_verifier_store.items() if expiry <= now
        ]
        for key in expired_handles:
            _pkce_verifier_store.pop(key, None)
        _pkce_verifier_store[handle] = (code_verifier, expires_at)
    return handle


async def _pop_pkce_verifier(handle: str) -> str | None:
    """Consume a stored PKCE verifier handle and return the verifier once."""

    now = datetime.now(timezone.utc)
    async with _pkce_verifier_store_lock:
        value = _pkce_verifier_store.pop(handle, None)
    if not value:
        return None
    verifier, expires_at = value
    if expires_at <= now:
        return None
    return verifier


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

# Human-readable labels for scheduled task commands used when auto-generating task names.
TASK_COMMAND_LABELS: dict[str, str] = {
    "sync_staff": "Sync staff directory",
    "sync_m365_data": "Sync Microsoft 365 data (legacy)",
    "sync_m365_licenses": "Sync Microsoft 365 licenses",
    "sync_m365_contacts": "Sync Microsoft 365 contacts",
    "sync_m365_mailboxes": "Sync Microsoft 365 mailboxes",
    "sync_to_xero": "Sync to Xero",
    "sync_to_xero_auto_send": "Sync to Xero (Auto Send)",
    "generate_invoice": "Generate Invoice",
    "create_scheduled_ticket": "Create scheduled ticket",
    "sync_recordings": "Sync call recordings",
    "sync_unifi_talk_recordings": "Sync Unifi Talk recordings",
    "queue_transcriptions": "Queue transcriptions",
    "process_transcription": "Process transcription",
    "sync_huntress": "Sync Huntress data",
}

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
        await message_templates_service.preload_cache()
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.error("Failed to preload message templates", error=str(exc))


@app.on_event("startup")
async def _start_refresh_notifier() -> None:
    try:
        await refresh_notifier.start(redis_client=get_redis_client())
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.error("Failed to initialise refresh notifier", error=str(exc))


@app.on_event("shutdown")
async def _shutdown_integrations() -> None:
    await refresh_notifier.stop()
    await close_redis_client()

SWAGGER_UI_PATH = settings.swagger_ui_url or "/docs"
PROTECTED_OPENAPI_PATH = "/internal/openapi.json"


async def _get_extra_csp_script_sources() -> list[str]:
    """Get additional CSP script sources from enabled modules.
    
    This function retrieves script sources that need to be allowed in the
    Content-Security-Policy, such as analytics scripts from enabled modules.
    
    Returns:
        List of valid HTTPS URLs to allow as script sources
    """
    sources = []
    
    try:
        # Check for Plausible analytics module
        module_list = await modules_service.list_modules()
        module_lookup = {module.get("slug"): module for module in module_list if module.get("slug")}
        
        plausible_module = module_lookup.get("plausible")
        if plausible_module and plausible_module.get("enabled"):
            plausible_settings = plausible_module.get("settings") or {}
            base_url = (plausible_settings.get("base_url") or "")
            if isinstance(base_url, str):
                base_url = base_url.strip().rstrip("/")
            else:
                base_url = ""
            
            # Validate base_url - must be HTTPS with actual content after the protocol
            if base_url.startswith("https://") and len(base_url) > 8:  # len("https://") = 8
                # Add the base URL as a script source (this allows loading /js/script.js from it)
                sources.append(base_url)
    except Exception:
        # If we fail to get module config, return empty list
        # The CSP will still work with default sources
        pass
    
    return sources


# Configure CORS with security-first defaults
# If ALLOWED_ORIGINS is not configured, only allow same-origin requests (empty list)
allowed_origins = [origin.strip() for origin in settings.allowed_origins.split(",") if origin.strip()] if settings.allowed_origins else []

# Log warning if wildcard CORS is detected (should never happen with current config)
if "*" in allowed_origins:
    logger.warning(
        "SECURITY WARNING: Wildcard CORS origin (*) detected. "
        "This allows any website to make requests to this API. "
        "Configure ALLOWED_ORIGINS in .env for production use."
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    # Restrict to the specific headers the portal actually uses. Using ``*``
    # together with ``allow_credentials=True`` is permissive and hides bugs
    # where the browser would otherwise reject a cross-origin request.
    allow_headers=[
        "Accept",
        "Accept-Language",
        "Authorization",
        "Cache-Control",
        "Content-Language",
        "Content-Type",
        "If-Match",
        "If-None-Match",
        "X-API-Key",
        "X-CSRF-Token",
        "X-CSRFToken",
        "X-Requested-With",
        "X-Request-ID",
    ],
    expose_headers=["X-Request-ID"],
)

# Add IP whitelisting middleware for sensitive endpoints
# This provides an additional layer of security by restricting access based on IP address
if settings.ip_whitelist_enabled and settings.ip_whitelist:
    whitelist_entries = [entry.strip() for entry in settings.ip_whitelist.split(",") if entry.strip()]
    
    # Determine which paths to protect
    protected_paths = ["/admin"]
    if not settings.ip_whitelist_admin_only:
        protected_paths.append("/api")
    
    # Exempt public endpoints from IP whitelisting
    exempt_paths = [
        "/static",
        "/health",
        "/healthz",
        "/readyz",
        "/login",
        "/register",
        "/api/auth/login",
        "/api/auth/register",
        "/api/webhooks",  # Webhooks use signature verification instead
        "/manifest.webmanifest",
        "/service-worker.js",
    ]
    
    app.add_middleware(
        IPWhitelistMiddleware,
        whitelist=whitelist_entries,
        protected_paths=protected_paths,
        exempt_paths=exempt_paths,
        enabled=True,
    )
    
    logger.info(
        "IP whitelist enabled",
        whitelist_count=len(whitelist_entries),
        protected_paths=protected_paths,
    )
elif settings.ip_whitelist_enabled:
    logger.warning(
        "IP whitelist enabled but no IP addresses configured. "
        "Set IP_WHITELIST in .env to enable IP-based access control."
    )

# Add security headers middleware
app.add_middleware(
    SecurityHeadersMiddleware,
    exempt_paths=("/static",),
    get_extra_script_sources=_get_extra_csp_script_sources,
    get_extra_connect_sources=_get_extra_csp_script_sources,
)

# Add request logging middleware
app.add_middleware(
    RequestLoggingMiddleware,
    exempt_paths=("/static", "/health", "/healthz", "/readyz", "/manifest.webmanifest", "/service-worker.js", "/api/users/me/preferences"),
)

# Configure endpoint-specific rate limits per security requirements
_rate_limit_redis = get_redis_client()
endpoint_limiter = EndpointRateLimiter(redis_client=_rate_limit_redis)

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

# API calls: 300 requests per minute per user (applied via general rate limiter)

app.add_middleware(
    EndpointRateLimiterMiddleware,
    endpoint_limiter=endpoint_limiter,
    exempt_paths=(SWAGGER_UI_PATH, PROTECTED_OPENAPI_PATH, "/static"),
)

general_rate_limiter = SimpleRateLimiter(
    limit=300,
    window_seconds=60,
    redis_client=_rate_limit_redis,
    namespace="rate-limit:general",
)
app.add_middleware(
    RateLimiterMiddleware,
    rate_limiter=general_rate_limiter,
    exempt_paths=(SWAGGER_UI_PATH, PROTECTED_OPENAPI_PATH, "/static", "/uploads", "/health", "/healthz", "/readyz"),
)

app.add_middleware(
    CacheControlMiddleware,
    exempt_paths=("/static",),
)

app.add_middleware(
    CSRFMiddleware,
    exempt_paths=(
        "/api/webhooks/smtp2go",
        "/api/integration-modules/uptimekuma/alerts",
        "/api/integration-modules/trello/webhook",
        "/api/backup-status",
        "/api/tray/enrol",
    ),
)

# Add Plausible tracking middleware for authenticated pageviews
# This middleware sends custom events to Plausible Analytics when users access pages
# It includes privacy protections (hashed user IDs) and only tracks authenticated users
from app.security.plausible_tracking import PlausibleTrackingMiddleware

def _get_plausible_module_settings() -> dict[str, Any]:
    """Synchronous function to get Plausible module settings for middleware."""
    # We use a cached module lookup to avoid async issues in middleware
    # This is populated in _build_base_context
    return getattr(_get_plausible_module_settings, '_cached_module', {})

app.add_middleware(
    PlausibleTrackingMiddleware,
    exempt_paths=(
        "/static",
        "/api",
        "/health",
        "/healthz",
        "/readyz",
        "/manifest.webmanifest",
        "/service-worker.js",
        "/ws",
        "/mcp",
    ),
    get_module_settings=_get_plausible_module_settings,
)

templates = Jinja2Templates(directory=str(templates_config.template_path))


def _static_url(path: str) -> str:
    """Generate cache-busted URL for static files.
    
    Appends version query string to force browsers (especially Edge) to fetch
    new versions when files change, preventing stale cached content.
    """
    if _APP_VERSION:
        separator = "&" if "?" in path else "?"
        return f"{path}{separator}v={_APP_VERSION}"
    return path


# Add cache-busting helper to Jinja2 globals
templates.env.globals["static_url"] = _static_url

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


def _sanitize_upload_path(file_path: str) -> PurePosixPath:
    """Normalise an upload path and guard against traversal attacks."""

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

    return PurePosixPath(*sanitized_parts)


def _resolve_private_upload(file_path: str | PurePosixPath) -> Path:
    """Resolve ``/uploads`` paths to the secured private uploads directory.

    Supports legacy nested directory structures while preventing path traversal
    outside the uploads root.
    """

    sanitized_path = _sanitize_upload_path(file_path) if isinstance(file_path, str) else file_path

    candidate = (_private_uploads_path.joinpath(*sanitized_path.parts)).resolve()
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


@app.websocket("/ws/tray/{device_uid}")
async def tray_device_socket(websocket: WebSocket, device_uid: str) -> None:
    """Persistent connection used by the tray client.

    The handshake authenticates with a bearer auth_token supplied via the
    ``Authorization`` header, the ``X-Tray-Token`` header, or the ``token``
    query parameter (the latter for environments where headers cannot be
    set on a websocket open).  Messages are JSON; the protocol is documented
    in ``docs/tray_app.md``.
    """

    from app.repositories import tray as tray_repo
    from app.services import tray as tray_service

    token = (
        websocket.headers.get("X-Tray-Token")
        or websocket.query_params.get("token")
        or ""
    )
    if not token:
        auth_header = websocket.headers.get("Authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header.split(" ", 1)[1].strip()
    if not token:
        await websocket.close(code=4401)
        return

    device = await tray_repo.get_device_by_auth_hash(tray_service.hash_token(token))
    if not device or device.get("device_uid") != device_uid:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    tray_service.register_connection(device_uid, websocket)
    try:
        while True:
            try:
                message = await websocket.receive_json()
            except (WebSocketDisconnect, RuntimeError):
                break
            except Exception:  # pragma: no cover - malformed payload
                continue
            msg_type = message.get("type") if isinstance(message, dict) else None
            if msg_type == "pong":
                continue
            if msg_type == "heartbeat":
                await tray_repo.update_device_heartbeat(
                    int(device["id"]),
                    console_user=message.get("console_user"),
                    last_ip=(websocket.client.host if websocket.client else None),
                    agent_version=message.get("agent_version"),
                )
                continue
            # Other inbound message types (chat_message, env_snapshot, etc.)
            # are handled by feature-specific services in follow-up phases.
    finally:
        tray_service.unregister_connection(device_uid, websocket)


# MCP WebSocket endpoint (only enabled if MCP_ENABLED is true)
if settings.mcp_enabled:
    from app.mcp_server import handle_mcp_connection

    @app.websocket("/mcp/ws")
    async def mcp_websocket_endpoint(websocket: WebSocket) -> None:
        """Model Context Protocol WebSocket endpoint for authorized agent access."""
        await handle_mcp_connection(websocket)


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
        swagger_js_url="/static/js/swagger-ui-bundle.js",
        swagger_css_url="/static/css/swagger-ui.css",
        swagger_favicon_url="/static/favicon.svg",
    )

app.include_router(auth.router)
app.include_router(agent.router)
app.include_router(users.router)
app.include_router(call_recordings_api.router)
app.include_router(companies.router)
app.include_router(essential8_api.router)
app.include_router(compliance_checks_api.router)
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
app.include_router(quotes_api.router)
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
app.include_router(system.router)
app.include_router(service_status_api.router)
app.include_router(backup_jobs_api.router)
app.include_router(asset_custom_fields.router)
app.include_router(tag_exclusions.router)
app.include_router(chat_api.router)
app.include_router(tray_api.router)
app.include_router(features_api.router)

# Initialise the feature pack registry.  Packs are loaded lazily on
# startup (see ``on_startup`` below).  The registry remains empty until
# packs are migrated into ``app/features/`` in follow-up PRs.
feature_registry = init_registry(app)

HELPDESK_PERMISSION_KEY = tickets_service.HELPDESK_PERMISSION_KEY
ISSUE_TRACKER_PERMISSION_KEY = issues_service.ISSUE_TRACKER_PERMISSION_KEY

# Search configuration
_PHONE_SEARCH_LIMIT = 100



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

    sanitized_path = _sanitize_upload_path(file_path)
    is_public_kb_image = sanitized_path.parts and sanitized_path.parts[0] == "knowledge-base"

    if not is_public_kb_image:
        _, redirect = await _require_authenticated_user(request)
        if redirect:
            return redirect

    resolved_path = _resolve_private_upload(sanitized_path)
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


_NOTIFICATION_METADATA_HIDDEN_KEYS: frozenset[str] = frozenset({"staff_id"})


def _prepare_notification_metadata(metadata: Any) -> list[dict[str, str]]:
    if metadata is None:
        return []

    serialised = _serialise_for_json(metadata)

    if isinstance(serialised, Mapping):
        items: list[dict[str, str]] = []
        for key in sorted(serialised.keys(), key=lambda item: str(item)):
            if str(key) in _NOTIFICATION_METADATA_HIDDEN_KEYS:
                continue
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


def _strip_internal_shop_product_fields(products: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Remove internal-only product fields before sending customer-facing JSON."""
    hidden_fields = {"buy_price", "vendor_sku"}
    return [
        {key: value for key, value in product.items() if key not in hidden_fields}
        for product in products
    ]


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


def _resolve_timezone(value: str | None) -> tuple[ZoneInfo | None, str | None]:
    zone_name = str(value or "").strip()
    if not zone_name:
        return None, None
    try:
        return ZoneInfo(zone_name), zone_name
    except ZoneInfoNotFoundError:
        return None, None


def _raw_value_includes_time(value: str | None) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return bool(re.search(r"\d{1,2}:\d{2}", text))


def _parse_local_datetime_to_utc(
    value: str | None,
    *,
    timezone_name: str | None = None,
    assume_midnight: bool = False,
) -> datetime | None:
    parsed = _parse_input_datetime(value, assume_midnight=assume_midnight)
    if parsed is None:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc)
    local_zone, _normalized_timezone = _resolve_timezone(timezone_name)
    if local_zone is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.replace(tzinfo=local_zone).astimezone(timezone.utc)


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


def _request_accepts_html(request: Request) -> bool:
    if _request_prefers_json(request):
        return False
    accept = (request.headers.get("accept") or "*").lower()
    if "text/html" in accept or "application/xhtml+xml" in accept:
        return True
    return "*/*" in accept or accept == "*"


def _get_status_phrase(status_code: int) -> str:
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return "Error"


def _get_request_id(request: Request) -> str | None:
    request_id = getattr(request.state, "request_id", None)
    if isinstance(request_id, str) and request_id.strip():
        return request_id
    header_request_id = request.headers.get("x-request-id")
    if header_request_id and header_request_id.strip():
        return header_request_id.strip()
    return None


def _error_payload(*, detail: str, request_id: str | None) -> dict[str, Any]:
    payload: dict[str, Any] = {"detail": detail}
    if request_id:
        payload["request_id"] = request_id
        payload["error_reference"] = request_id
    return payload


def _apply_request_id_header(response: Response, request_id: str | None) -> Response:
    if request_id:
        response.headers["X-Request-ID"] = request_id
    return response


def _format_error_detail(detail: Any) -> str | None:
    if detail is None:
        return None
    if isinstance(detail, str):
        return detail
    try:
        return json.dumps(detail, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return str(detail)


def _generate_error_reference() -> str:
    return uuid4().hex[:12]


def _get_safe_error_path(request: Request) -> str:
    path = request.url.path.strip()
    return path or "/"


def _should_show_error_detail(*, request: Request, user: dict[str, Any] | None) -> bool:
    if bool(getattr(request.app, "debug", False)):
        return True
    if settings.environment.strip().lower() != "production":
        return True
    return bool(user and user.get("is_super_admin"))


async def _render_error_page(
    request: Request,
    *,
    status_code: int,
    title: str | None = None,
    message: str,
    detail: str | None = None,
    error_reference: str | None = None,
) -> HTMLResponse:
    status_message = _get_status_phrase(status_code)
    document_title = title or f"{status_code} {status_message}"
    request_id = _get_request_id(request)
    resolved_error_reference = error_reference or _generate_error_reference()
    try:
        user, _ = await _get_optional_user(request)
    except Exception as exc:  # pragma: no cover - defensive fallback
        log_error(
            "Failed to load user context for error page",
            error=str(exc),
            request_id=request_id,
            error_reference=resolved_error_reference,
            request_path=_get_safe_error_path(request),
        )
        user = None
    show_error_detail = _should_show_error_detail(request=request, user=user)
    context = await _build_portal_context(
        request,
        user,
        extra={
            "title": document_title,
            "error_title": title or status_message,
            "error_message": message,
            "error_status_code": status_code,
            "error_status_message": status_message,
            "error_detail": detail if show_error_detail else None,
            "show_error_detail": show_error_detail,
            "error_path": _get_safe_error_path(request),
            "request_id": request_id,
            "error_reference": resolved_error_reference,
        },
    )
    return templates.TemplateResponse(
        context["request"],
        "errors/error.html",
        context,
        status_code=status_code,
    )


@app.exception_handler(RequestValidationError)
async def handle_request_validation_error(request: Request, exc: RequestValidationError):
    path = request.url.path
    request_id = _get_request_id(request)
    if path.startswith("/api/integration-modules/"):
        logger.warning(
            "Webhook payload validation failed",
            request_id=request_id,
            path=path,
            errors=exc.errors(),
            content_type=request.headers.get("content-type"),
            user_agent=request.headers.get("user-agent"),
        )
    response = JSONResponse(
        content=jsonable_encoder({"detail": exc.errors()}),
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
    )
    return _apply_request_id_header(response, request_id)


@app.exception_handler(HTTPException)
async def handle_http_exception(request: Request, exc: HTTPException):
    request_id = _get_request_id(request)
    error_reference = _generate_error_reference()
    if _request_prefers_json(request) or not _request_accepts_html(request):
        if exc.status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
            response = JSONResponse(
                _error_payload(detail="Internal server error", request_id=request_id),
                status_code=exc.status_code,
            )
            if exc.headers:
                for header, value in exc.headers.items():
                    response.headers[header] = value
            return _apply_request_id_header(response, request_id)
        response = await http_exception_handler(request, exc)
        return _apply_request_id_header(response, request_id)
    detail_text = _format_error_detail(exc.detail)
    friendly_titles = {
        status.HTTP_404_NOT_FOUND: "Page not found",
        status.HTTP_403_FORBIDDEN: "Access denied",
        status.HTTP_401_UNAUTHORIZED: "Sign in required",
    }
    friendly_messages = {
        status.HTTP_404_NOT_FOUND: "We couldn't find the page you were looking for. Use the navigation menu to continue.",
        status.HTTP_403_FORBIDDEN: "You don't have permission to view this page. Choose another destination from the menu.",
        status.HTTP_401_UNAUTHORIZED: "Please sign in to continue. You can return to the dashboard to start again.",
    }
    message = friendly_messages.get(exc.status_code) or detail_text or _get_status_phrase(exc.status_code)
    detail_for_template = None
    if detail_text and detail_text != message:
        detail_for_template = detail_text
    log_info(
        "Rendering HTTP error page",
        status_code=exc.status_code,
        request_id=request_id,
        error_reference=error_reference,
        request_path=_get_safe_error_path(request),
    )
    response = await _render_error_page(
        request,
        status_code=exc.status_code,
        title=friendly_titles.get(exc.status_code),
        message=message,
        detail=detail_for_template,
        error_reference=error_reference,
    )
    if exc.headers:
        for header, value in exc.headers.items():
            response.headers[header] = value
    return _apply_request_id_header(response, request_id)


@app.exception_handler(Exception)
async def handle_unexpected_exception(request: Request, exc: Exception):  # pragma: no cover - defensive
    request_id = _get_request_id(request)
    error_reference = _generate_error_reference()
    log_error(
        "Unhandled application error",
        exc=exc,
        event="app.unhandled_exception",
        request_id=request_id,
        error_reference=error_reference,
        path=_get_safe_error_path(request),
    )
    if _request_prefers_json(request):
        response = JSONResponse(
            _error_payload(detail="Internal server error", request_id=request_id),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
        return _apply_request_id_header(response, request_id)
    if not _request_accepts_html(request):
        response = PlainTextResponse("Internal Server Error", status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return _apply_request_id_header(response, request_id)
    response = await _render_error_page(
        request,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        title="Something went wrong",
        message="We ran into a problem while loading this page. Try again, or pick another destination from the menu.",
        detail=_format_error_detail(exc),
        error_reference=error_reference,
    )
    return _apply_request_id_header(response, request_id)


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
    
    # Check BCP permissions - use new continuity.access permission system
    can_view_bcp = is_super_admin or _has_permission("can_view_bcp")
    can_edit_bcp = is_super_admin
    if not is_super_admin and active_company_id is not None:
        user_id = user.get("id")
        if user_id:
            try:
                # Also check legacy bcp:edit permission for backward compatibility
                can_edit_bcp = await membership_repo.user_has_permission(int(user_id), "bcp:edit")
            except Exception as exc:  # pragma: no cover - defensive fallback
                log_error("Failed to check BCP edit permissions", error=str(exc))
                can_edit_bcp = False

    permission_flags = {
        "can_access_shop": is_super_admin or _has_permission("can_access_shop"),
        "can_access_cart": is_super_admin or _has_permission("can_access_cart"),
        "can_access_orders": is_super_admin or _has_permission("can_access_orders"),
        "can_access_quotes": is_super_admin or _has_permission("can_access_quotes"),
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
        "can_view_compliance": is_super_admin or _has_permission("can_view_compliance"),
        "can_view_bcp": can_view_bcp,
        "can_edit_bcp": can_edit_bcp,
        "can_view_m365_best_practices": is_super_admin or _has_permission("can_view_m365_best_practices"),
        "can_view_compliance_checks": is_super_admin or _has_permission("can_view_compliance_checks"),
        "can_manage_compliance_checks": is_super_admin or _has_permission("can_manage_compliance_checks"),
        "can_view_m365_user_mailboxes": is_super_admin or _has_permission("can_view_m365_user_mailboxes"),
        "can_view_m365_shared_mailboxes": is_super_admin or _has_permission("can_view_m365_shared_mailboxes"),
        "can_access_chat": is_super_admin or _has_permission("can_access_chat"),
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
    
    # Cache Plausible module for middleware use
    plausible_module = (module_lookup or {}).get("plausible")
    if plausible_module:
        # Store in function attribute for middleware to access
        _get_plausible_module_settings._cached_module = plausible_module

    # Get Plausible analytics configuration for app-wide tracking
    plausible_config = {"enabled": False}
    if plausible_module and plausible_module.get("enabled"):
        plausible_settings = plausible_module.get("settings") or {}
        base_url = str(plausible_settings.get("base_url") or "").strip().rstrip("/")
        site_domain = str(plausible_settings.get("site_domain") or "").strip()
        track_pageviews = bool(plausible_settings.get("track_pageviews"))
        pepper = str(plausible_settings.get("pepper") or "").strip()
        send_pii = bool(plausible_settings.get("send_pii"))
        
        # Validate base_url and site_domain to prevent injection attacks
        # base_url must be a valid HTTPS URL
        # site_domain must be a valid domain name (alphanumeric, dots, hyphens)
        valid_base_url = False
        valid_site_domain = False
        
        if base_url:
            try:
                from urllib.parse import urlparse
                parsed = urlparse(base_url)
                # Must be https or http, have a netloc, and no suspicious characters
                if parsed.scheme in ("https", "http") and parsed.netloc and not any(c in base_url for c in ["<", ">", '"', "'"]):
                    valid_base_url = True
            except Exception:
                pass
        
        if site_domain:
            # Domain must only contain alphanumeric, dots, hyphens, underscores and optional port
            # No spaces, quotes, or HTML-like characters
            if re.match(r"^[A-Za-z0-9._-]+(?::\d+)?$", site_domain) and not any(
                c in site_domain for c in ["<", ">", '"', "'"]
            ):
                valid_site_domain = True
        
        if valid_base_url and valid_site_domain:
            plausible_config = {
                "enabled": True,
                "base_url": base_url,
                "site_domain": site_domain,
                "track_pageviews": track_pageviews,
            }
            
            # Add hashed user ID for client-side tracking if pageview tracking enabled
            if track_pageviews and user and user.get("id"):
                from app.security.plausible_tracking import hash_user_id_for_plausible
                
                user_id = user.get("id")
                # Hash user ID for privacy using shared utility
                hashed_user_id = hash_user_id_for_plausible(user_id, pepper, send_pii)
                
                plausible_config["hashed_user_id"] = hashed_user_id

    context: dict[str, Any] = {
        "request": request,
        "app_name": settings.app_name,
        "current_year": datetime.utcnow().year,
        "user": user,
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
        "matrix_chat_enabled": settings.matrix_enabled,
        "is_impersonating": is_impersonating,
        "impersonator_user": impersonator_user,
        "impersonation_started_at": impersonation_started_at,
        "has_issue_tracker_access": has_issue_tracker_access,
        "can_access_tickets": True,
        "plausible_config": plausible_config,
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


async def _build_public_context(
    request: Request,
    *,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context: dict[str, Any] = {
        "request": request,
        "app_name": settings.app_name,
        "current_year": datetime.utcnow().year,
        "user": None,
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
        "can_access_quotes": False,
        "can_access_forms": False,
        "can_manage_assets": False,
        "can_manage_licenses": False,
        "can_manage_invoices": False,
        "can_manage_staff": False,
        "can_view_compliance": False,
        "can_view_m365_best_practices": False,
        "can_view_compliance_checks": False,
        "can_manage_compliance_checks": False,
        "can_view_m365_user_mailboxes": False,
        "can_view_m365_shared_mailboxes": False,
        "can_access_chat": False,
        "plausible_config": {"enabled": False},
        "cart_summary": {"item_count": 0, "total_quantity": 0, "subtotal": Decimal("0")},
        "notification_unread_count": 0,
        "enable_auto_refresh": bool(settings.enable_auto_refresh),
        "matrix_chat_enabled": settings.matrix_enabled,
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
    """Build the home-page dashboard payload.

    Delegates to :func:`app.services.dashboard.build_dashboard`. The wrapper
    is kept so other tests/code monkey-patching this name keep working.
    """
    return await dashboard_service.build_dashboard(request, user)




async def _render_template(
    template_name: str,
    request: Request,
    user: dict[str, Any],
    *,
    extra: dict[str, Any] | None = None,
):
    context = await _build_base_context(request, user, extra=extra)
    return templates.TemplateResponse(context["request"], template_name, context)


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

_PORTAL_STATUS_BADGE_MAP: dict[str, str] = {
    "open": "badge--warning",
    "in_progress": "badge--warning",
    "pending": "badge--warning",
    "resolved": "badge--success",
    "closed": "badge--muted",
}


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
    {"field": "can_access_quotes", "label": "Quotes"},
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


def _sanitize_local_redirect_target(
    candidate: str | None,
    *,
    fallback: str,
    allowed_prefixes: Sequence[str] | None = None,
) -> str:
    """Return a safe local redirect target, or fallback when invalid."""
    if not isinstance(candidate, str):
        return fallback

    target = candidate.strip()
    if not target:
        return fallback

    parsed = URL(target)
    if parsed.scheme or parsed.netloc:
        return fallback

    if not target.startswith("/") or target.startswith("//") or "\\" in target:
        return fallback

    if any(ord(char) < 32 for char in target):
        return fallback

    if allowed_prefixes and not any(target.startswith(prefix) for prefix in allowed_prefixes):
        return fallback

    return target


async def _validate_recommendation_product_ids(
    raw_ids: Sequence[int | str] | None,
    *,
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
        context["request"],
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
        "admin_credentials_configured": bool(all(await _get_m365_admin_credentials())),
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
    show_inactive_tasks: bool = False,
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
        "hudu_id": _string_value("hudu_id", (company_record.get("hudu_id") or "").strip()),
        "huntress_organization_id": _string_value(
            "huntress_organization_id",
            (company_record.get("huntress_organization_id") or "").strip(),
        ),
        "email_domains": _string_value("email_domains", default_email_domains),
        "is_vip": _bool_value("is_vip", bool(company_record.get("is_vip"))),
        "payment_method": _string_value(
            "payment_method", (company_record.get("payment_method") or "invoice_prepay").strip()
        ),
        "require_po": _bool_value("require_po", bool(company_record.get("require_po"))),
        "offboarding_email_forwarding_enabled": _bool_value(
            "offboarding_email_forwarding_enabled",
            bool(int(company_record.get("offboarding_email_forwarding_enabled", 1) or 1)),
        ),
        "trello_board_id": _string_value(
            "trello_board_id", (company_record.get("trello_board_id") or "").strip()
        ),
        "trello_api_key": _string_value(
            "trello_api_key", (company_record.get("trello_api_key") or "").strip()
        ),
        "trello_token": _string_value(
            "trello_token", (company_record.get("trello_token") or "").strip()
        ),
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
        # Build the set of commands that belong to disabled modules so they can be excluded.
        try:
            all_modules = await modules_service.list_modules()
            disabled_module_slugs = {m["slug"] for m in all_modules if not m.get("enabled")}
        except Exception:  # pragma: no cover - defensive fallback
            disabled_module_slugs = set()
        disabled_commands: set[str] = set()
        for mod_slug, cmds in COMMANDS_BY_MODULE.items():
            if mod_slug in disabled_module_slugs:
                disabled_commands.update(cmds)

        automation_command_options = [
            {"value": "sync_staff", "label": "Sync staff directory"},
            {"value": "sync_m365_data", "label": "Sync Microsoft 365 data (legacy)"},
            {"value": "sync_m365_licenses", "label": "Sync Microsoft 365 licenses"},
            {"value": "sync_m365_contacts", "label": "Sync Microsoft 365 contacts"},
            {"value": "sync_m365_mailboxes", "label": "Sync Microsoft 365 mailboxes"},
            {"value": "sync_huntress", "label": "Sync Huntress data"},
            {"value": "sync_to_xero", "label": "Sync to Xero"},
            {"value": "sync_to_xero_auto_send", "label": "Sync to Xero (Auto Send)"},
            {"value": "generate_invoice", "label": "Generate Invoice"},
            {"value": "create_scheduled_ticket", "label": "Create scheduled ticket"},
            {"value": "sync_recordings", "label": "Sync call recordings"},
            {
                "value": "sync_unifi_talk_recordings",
                "label": "Sync Unifi Talk recordings",
            },
            {"value": "queue_transcriptions", "label": "Queue transcriptions"},
            {"value": "process_transcription", "label": "Process transcription"},
        ]
        automation_command_options = [o for o in automation_command_options if o["value"] not in disabled_commands]
        default_command_values = {option["value"] for option in automation_command_options}

        try:
            tasks = await scheduled_tasks_repo.list_tasks(include_inactive=show_inactive_tasks)
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
            if command and command not in default_command_values and command not in disabled_commands:
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

    # Fetch Microsoft 365 credentials for the company
    m365_credential_view: dict[str, Any] | None = None
    if is_super_admin:
        try:
            m365_creds = await m365_service.get_credentials(company_id)
            if m365_creds:
                expires = m365_creds.get("token_expires_at")
                if isinstance(expires, datetime):
                    expires_display = expires.replace(tzinfo=timezone.utc).isoformat()
                elif expires:
                    expires_display = str(expires)
                else:
                    expires_display = None
                m365_credential_view = {
                    "tenant_id": m365_creds.get("tenant_id"),
                    "client_id": m365_creds.get("client_id"),
                    "token_expires_at": expires_display,
                }
        except RuntimeError as exc:  # pragma: no cover - defensive guard for tests
            if "Database pool not initialised" in str(exc):
                pass
            else:
                raise

    staff_field_config: list[dict[str, Any]] = []
    staff_custom_field_definitions: list[dict[str, Any]] = []
    if is_super_admin:
        staff_field_config = (
            await staff_field_config_service.load_effective_company_staff_fields(company_id)
        )
        staff_custom_field_definitions = (
            await staff_custom_fields_repo.list_company_owned_definitions(company_id)
        )

    # Fetch tray install tokens for this company
    tray_tokens: list[dict[str, Any]] = []
    if is_super_admin:
        try:
            from app.repositories import tray as tray_repo
            tray_tokens = await tray_repo.list_install_tokens(company_id=company_id)
        except RuntimeError as exc:  # pragma: no cover - defensive guard for tests
            if "Database pool not initialised" in str(exc):
                tray_tokens = []
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
        "show_inactive_tasks": show_inactive_tasks,
        "m365_credential": m365_credential_view,
        "m365_has_credentials": m365_credential_view is not None,
        "m365_admin_credentials_configured": bool(all(await _get_m365_admin_credentials(company_id))),
        "staff_field_config": staff_field_config,
        "staff_custom_field_definitions": staff_custom_field_definitions,
        "tray_tokens": tray_tokens,
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

    async def _migrate_sync_m365_data_tasks() -> None:
        """For companies that only have legacy sync_m365_data tasks, create the three
        split tasks (sync_m365_licenses, sync_m365_contacts, sync_m365_mailboxes) at
        staggered times and deactivate the old task to avoid gateway timeouts."""
        legacy_commands = {"sync_m365_data", "sync_o365"}
        new_commands = {"sync_m365_licenses", "sync_m365_contacts", "sync_m365_mailboxes"}
        all_tasks = await scheduled_tasks_repo.list_tasks(include_inactive=False)
        # Group tasks by company_id
        from collections import defaultdict
        by_company: dict[int, list[dict]] = defaultdict(list)
        for t in all_tasks:
            cid = t.get("company_id")
            if cid is not None:
                by_company[int(cid)].append(t)
        migrated = 0
        for company_id, company_tasks in by_company.items():
            commands_for_company = {t["command"] for t in company_tasks}
            has_legacy = bool(legacy_commands & commands_for_company)
            has_new = bool(new_commands & commands_for_company)
            if not has_legacy or has_new:
                continue
            # Find an existing company name from the legacy task name if possible
            legacy_task = next(
                (t for t in company_tasks if t.get("command") in legacy_commands), None
            )
            task_name_prefix = ""
            if legacy_task:
                raw_name: str = legacy_task.get("name") or ""
                for suffix in (" - Sync Microsoft 365 data", " - Sync O365", " - Sync M365"):
                    if raw_name.endswith(suffix):
                        task_name_prefix = raw_name[: -len(suffix)]
                        break
            for command, label_suffix in (
                ("sync_m365_licenses", "Sync Microsoft 365 licenses"),
                ("sync_m365_contacts", "Sync Microsoft 365 contacts"),
                ("sync_m365_mailboxes", "Sync Microsoft 365 mailboxes"),
            ):
                if command not in commands_for_company:
                    label = (
                        f"{task_name_prefix} - {label_suffix}"
                        if task_name_prefix
                        else label_suffix
                    )
                    await scheduled_tasks_repo.create_task(
                        name=label,
                        command=command,
                        cron=_random_daily_cron(),
                        company_id=company_id,
                        active=True,
                    )
            # Deactivate the legacy task so data is no longer synced twice
            for t in company_tasks:
                if t.get("command") in legacy_commands:
                    await scheduled_tasks_repo.set_task_active(t["id"], False)
            migrated += 1
            log_info(
                "Migrated legacy sync_m365_data task to split tasks",
                company_id=company_id,
            )
        if migrated:
            log_info("sync_m365_data migration complete", companies_migrated=migrated)

    async def _seed_demo_data_once() -> None:
        from app.services import demo_seeding as demo_seeding_service

        result = await demo_seeding_service.seed_demo_data()
        if result.get("skipped"):
            log_info("Demo data already seeded – skipping startup seed")
        else:
            log_info("Demo data seeded on startup", **{k: v for k, v in result.items() if k != "skipped"})

    async def _fetch_tray_msi() -> None:
        from app.services import tray_installer as tray_installer_service

        await tray_installer_service.fetch_latest_tray_msi(
            repo=settings.github_tray_msi_repo,
            github_token=settings.github_token,
        )

    startup_tasks = [
        ("sync_change_log_sources", change_log_service.sync_change_log_sources()),
        ("ensure_default_modules", modules_service.ensure_default_modules()),
        ("refresh_all_schedules", automations_service.refresh_all_schedules()),
        ("bootstrap_default_bcp_template", _bootstrap_default_bcp_template()),
        ("migrate_sync_m365_data_tasks", _migrate_sync_m365_data_tasks()),
        ("seed_demo_data_once", _seed_demo_data_once()),
        ("fetch_latest_tray_msi", _fetch_tray_msi()),
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
    if settings.matrix_enabled:
        from app.services import matrix_sync
        import asyncio as _asyncio
        _asyncio.create_task(matrix_sync.run_sync_loop())

    # Load feature packs.  Empty by default; populated as areas of the
    # app are migrated under ``app/features/`` in follow-up PRs.
    pack_slugs = [
        slug.strip()
        for slug in (getattr(settings, "feature_packs", "") or "").split(",")
        if slug.strip()
    ]
    if pack_slugs:
        await feature_registry.load_many(pack_slugs)

    # Optional dev-only auto-reload: when ``FEATURE_PACK_WATCH=true`` is
    # set we start a per-pack ``watchfiles`` watcher so editing any
    # file under ``app/features/<slug>/`` triggers a debounced reload
    # of just that pack.  Off by default in production.
    global _feature_pack_watcher
    _feature_pack_watcher = None
    if getattr(settings, "feature_pack_watch", False) and pack_slugs:
        from app.core.feature_watcher import FeaturePackWatcher

        _feature_pack_watcher = FeaturePackWatcher(feature_registry)
        await _feature_pack_watcher.start()

    global _app_ready
    _app_ready = True
    log_info("Application started", environment=settings.environment)


@app.on_event("shutdown")
async def on_shutdown() -> None:
    global _app_ready
    _app_ready = False
    if settings.matrix_enabled:
        from app.services import matrix_sync
        matrix_sync.stop_sync_loop()
    if _feature_pack_watcher is not None:
        await _feature_pack_watcher.stop()
    await feature_registry.unload_all()
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

    extra: dict[str, Any] = {
        "title": "Dashboard",
        "dashboard": overview,
    }
    if isinstance(overview, dict) and "unread_notifications" in overview:
        extra["notification_unread_count"] = overview.get("unread_notifications", 0)

    return await _render_template(
        "dashboard.html",
        request,
        user,
        extra=extra,
    )


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
        "can_manage_sku_mappings": is_super_admin,
        "webhook_enabled": bool(settings.licenses_webhook_url and settings.licenses_webhook_api_key),
        "has_m365_credentials": bool(credentials),
    }
    return await _render_template("licenses/index.html", request, user, extra=extra)


@app.get("/licenses/sku-mappings", response_class=JSONResponse)
async def list_license_sku_mappings(request: Request):
    user, _membership, _company, _company_id, redirect = await _load_license_context(request)
    if redirect:
        return redirect
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin privileges required")
    mappings = await sku_friendly_repo.list_mappings()
    return JSONResponse({"items": mappings})


@app.post("/licenses/sku-mappings", response_class=JSONResponse)
async def upsert_license_sku_mapping(request: Request):
    user, _membership, _company, _company_id, redirect = await _load_license_context(request)
    if redirect:
        return redirect
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin privileges required")
    try:
        payload = await request.json()
    except Exception as exc:  # pragma: no cover - defensive parsing
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payload") from exc
    sku = str(payload.get("sku") or "").strip().upper()
    friendly_name = str(payload.get("friendly_name") or "").strip()
    hidden = bool(payload.get("hidden"))
    if not sku:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="SKU is required")
    if not friendly_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Friendly name is required")
    mapping = await sku_friendly_repo.upsert_mapping(sku, friendly_name, hidden=hidden)
    return JSONResponse({"item": mapping, "success": True})


@app.delete("/licenses/sku-mappings/{sku}", response_class=JSONResponse)
async def delete_license_sku_mapping(request: Request, sku: str):
    user, _membership, _company, _company_id, redirect = await _load_license_context(request)
    if redirect:
        return redirect
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin privileges required")
    cleaned_sku = sku.strip().upper()
    if not cleaned_sku:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="SKU is required")
    await sku_friendly_repo.delete_mapping(cleaned_sku)
    return JSONResponse({"success": True})


# ---------------------------------------------------------------------------
# Company overview report
# ---------------------------------------------------------------------------


def _delete_cover_image_file(relative_path: str) -> None:
    """Safely remove a stored PDF cover image file.

    ``relative_path`` is the value returned by
    :func:`~app.services.file_storage.store_report_cover_image` and stored in
    the ``site_settings`` table (e.g. ``private_uploads/report-cover/abc.png``).
    Any path traversal attempt is silently ignored to avoid surfacing errors.
    """
    try:
        base = _private_uploads_path.parent.resolve()
        candidate = (base / relative_path).resolve()
        candidate.relative_to(base)  # raises ValueError if outside base
        candidate.unlink(missing_ok=True)
    except (ValueError, OSError):  # pragma: no cover - defensive
        pass


async def _load_report_context(request: Request):
    """Authenticate the user and resolve their active company for the report."""
    user, redirect = await _require_authenticated_user(request)
    if redirect:
        return user, None, None, None, redirect
    company_id_raw = user.get("company_id")
    if company_id_raw is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No company associated with the current user",
        )
    try:
        company_id = int(company_id_raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid company identifier",
        ) from exc
    membership = await user_company_repo.get_user_company(user["id"], company_id)
    company = await company_repo.get_company_by_id(company_id)
    return user, membership, company, company_id, None


def _can_configure_report(user: Mapping[str, Any], membership: Mapping[str, Any] | None) -> bool:
    if user.get("is_super_admin"):
        return True
    return bool(membership and membership.get("is_admin"))


async def company_overview_report_page(request: Request):
    user, membership, company, company_id, redirect = await _load_report_context(request)
    if redirect:
        return redirect
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    report = await reports_service.build_company_report(company_id)
    extra = {
        "title": "Company overview report",
        "report": report,
        "company": company,
        "can_configure_report": _can_configure_report(user, membership),
    }
    return await _render_template("reports/index.html", request, user, extra=extra)


async def company_overview_report_pdf(request: Request):
    from fastapi.responses import StreamingResponse

    user, _membership, company, company_id, redirect = await _load_report_context(request)
    if redirect:
        return redirect
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")

    try:
        from weasyprint import HTML  # type: ignore
    except (ImportError, OSError) as exc:  # pragma: no cover - depends on system packages
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "PDF export requires WeasyPrint and its native dependencies. "
                "See https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#installation"
            ),
        ) from exc

    # Load the global PDF cover image and encode it as a data URI so WeasyPrint
    # can embed it without needing an authenticated HTTP request.
    pdf_cover_image_data_uri: str | None = None
    cover_image_path = await site_settings_repo.get_pdf_cover_image()
    if cover_image_path:
        cover_file = (_private_uploads_path.parent / cover_image_path).resolve()
        uploads_root = _private_uploads_path.parent.resolve()
        try:
            cover_file.relative_to(uploads_root)
            if cover_file.is_file():
                suffix = cover_file.suffix.lower().lstrip(".")
                mime = {
                    "jpg": "image/jpeg",
                    "jpeg": "image/jpeg",
                    "png": "image/png",
                    "gif": "image/gif",
                    "webp": "image/webp",
                }.get(suffix, "image/jpeg")
                encoded = base64.b64encode(cover_file.read_bytes()).decode("ascii")
                pdf_cover_image_data_uri = f"data:{mime};base64,{encoded}"
        except (ValueError, OSError):
            pass

    report = await reports_service.build_company_report(company_id)
    base_context = await _build_base_context(
        request,
        user,
        extra={
            "report": report,
            "company": company,
            "title": "Company overview report",
            "pdf_cover_image_data_uri": pdf_cover_image_data_uri,
        },
    )
    template = templates.get_template("reports/pdf.html")
    rendered_html = template.render(base_context)

    await audit_service.log_action(
        action="report.company_overview.export_pdf",
        user_id=user.get("id"),
        entity_type="company",
        entity_id=company_id,
        metadata={"company_id": company_id},
        request=request,
    )

    pdf_bytes = HTML(
        string=rendered_html,
        base_url=str(request.base_url),
    ).write_pdf()

    safe_name = "".join(
        ch if ch.isalnum() or ch in (" ", "-", "_") else "_"
        for ch in (company.get("name") or f"company_{company_id}")
    ).strip().replace(" ", "_") or f"company_{company_id}"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"company_overview_{safe_name}_{timestamp}.pdf"

    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


async def company_overview_report_settings_page(request: Request):
    user, membership, company, company_id, redirect = await _load_report_context(request)
    if redirect:
        return redirect
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    if not _can_configure_report(user, membership):
        return RedirectResponse(
            url="/reports/company-overview", status_code=status.HTTP_303_SEE_OTHER
        )
    visibility = await reports_service.get_section_visibility(company_id)
    detail_visibility = await reports_service.get_section_detail_visibility(company_id)
    report_settings = await reports_service.get_company_report_settings(company_id)
    # Apply saved order to the section list for display.
    section_order: list[str] | None = report_settings.get("section_order")
    all_sections = list(reports_service.REPORT_SECTIONS)
    if section_order:
        key_to_section = {s.key: s for s in all_sections}
        ordered = [key_to_section[k] for k in section_order if k in key_to_section]
        remaining = [s for s in all_sections if s.key not in set(section_order)]
        all_sections = ordered + remaining
    extra = {
        "title": "Report sections",
        "company": company,
        "sections": all_sections,
        "visibility": visibility,
        "detail_visibility": detail_visibility,
        "auto_hide_empty": report_settings.get("auto_hide_empty", True),
    }
    return await _render_template("reports/settings.html", request, user, extra=extra)


async def company_overview_report_settings_save(request: Request):
    user, membership, company, company_id, redirect = await _load_report_context(request)
    if redirect:
        return redirect
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    if not _can_configure_report(user, membership):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to configure reports.",
        )
    form = await request.form()
    enabled_keys = set(form.getlist("sections"))
    preferences = {
        section.key: (section.key in enabled_keys)
        for section in reports_service.REPORT_SECTIONS
    }
    await reports_service.save_section_visibility(company_id, preferences)
    # Persist detail-page preferences.
    detailed_keys = set(form.getlist("detailed_sections"))
    detail_preferences = {
        section.key: (section.key in detailed_keys and section.key in enabled_keys)
        for section in reports_service.REPORT_SECTIONS
    }
    await reports_service.save_section_detail_visibility(company_id, detail_preferences)
    # Persist auto-hide and section order settings.
    auto_hide_empty = form.get("auto_hide_empty") == "1"
    raw_order = form.get("section_order", "")
    section_order_list: list[str] | None = (
        [k for k in raw_order.split(",") if k] if raw_order else None
    )
    await reports_service.save_company_report_settings(
        company_id, auto_hide_empty, section_order_list
    )
    await audit_service.log_action(
        action="report.company_overview.configure",
        user_id=user.get("id"),
        entity_type="company",
        entity_id=company_id,
        metadata={
            "enabled_sections": sorted(enabled_keys),
            "detailed_sections": sorted(detailed_keys & enabled_keys),
            "auto_hide_empty": auto_hide_empty,
        },
        request=request,
    )
    return RedirectResponse(
        url="/reports/company-overview", status_code=status.HTTP_303_SEE_OTHER
    )


async def admin_report_cover_image_page(request: Request):
    user, redirect = await _require_authenticated_user(request)
    if redirect:
        return redirect
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin access required")
    current_image = await site_settings_repo.get_pdf_cover_image()
    extra = {
        "title": "PDF cover image",
        "current_image": current_image,
    }
    return await _render_template("admin/report_cover_image.html", request, user, extra=extra)


async def admin_report_cover_image_upload(request: Request, image: UploadFile = File(None)):
    user, redirect = await _require_authenticated_user(request)
    if redirect:
        return redirect
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin access required")

    if image is None or not image.filename:
        return RedirectResponse(
            url="/admin/reports/pdf-cover-image?error=No+file+selected",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    try:
        relative_path, _dest = await store_report_cover_image(
            upload=image,
            uploads_root=_private_uploads_path,
        )
    except HTTPException as exc:
        return RedirectResponse(
            url=f"/admin/reports/pdf-cover-image?error={quote(exc.detail)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # Remove previous cover image file if one existed.
    previous = await site_settings_repo.get_pdf_cover_image()
    if previous:
        _delete_cover_image_file(previous)

    await site_settings_repo.set_pdf_cover_image(relative_path)
    await audit_service.log_action(
        action="admin.report.pdf_cover_image.upload",
        user_id=user.get("id"),
        entity_type="site_settings",
        entity_id=1,
        metadata={"path": relative_path},
        request=request,
    )
    return RedirectResponse(
        url="/admin/reports/pdf-cover-image?success=Cover+image+updated",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def admin_report_cover_image_delete(request: Request):
    user, redirect = await _require_authenticated_user(request)
    if redirect:
        return redirect
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin access required")

    current = await site_settings_repo.get_pdf_cover_image()
    if current:
        _delete_cover_image_file(current)
    await site_settings_repo.set_pdf_cover_image(None)
    await audit_service.log_action(
        action="admin.report.pdf_cover_image.delete",
        user_id=user.get("id"),
        entity_type="site_settings",
        entity_id=1,
        metadata={},
        request=request,
    )
    return RedirectResponse(
        url="/admin/reports/pdf-cover-image?success=Cover+image+removed",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def admin_report_cover_image_preview(request: Request):
    """Serve the current PDF cover image for admin preview (super admin only)."""
    user, redirect = await _require_authenticated_user(request)
    if redirect:
        return redirect
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin access required")
    cover_image_path = await site_settings_repo.get_pdf_cover_image()
    if not cover_image_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No cover image set")
    cover_file = (_private_uploads_path.parent / cover_image_path).resolve()
    uploads_root = _private_uploads_path.parent.resolve()
    try:
        cover_file.relative_to(uploads_root)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid file path") from exc
    if not cover_file.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cover image not found")
    return FileResponse(cover_file, headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------------------
# Reporting (super-admin authored SELECT queries)
# ---------------------------------------------------------------------------

_REPORTING_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _reporting_message(value: str | None, *, max_length: int = 240) -> str | None:
    if not value:
        return None
    cleaned = str(value).strip()
    if not cleaned:
        return None
    return cleaned[:max_length]


async def _require_reporting_access(
    request: Request,
) -> tuple[dict[str, Any] | None, bool, RedirectResponse | None]:
    """Authenticate and ensure the user is super admin or helpdesk technician.

    Returns ``(user, is_super_admin, redirect)``. Raises 403 if the user is
    authenticated but lacks reporting privileges.
    """
    user, redirect = await _require_authenticated_user(request)
    if redirect:
        return None, False, redirect
    is_super_admin = bool(user.get("is_super_admin"))
    is_tech = await _is_helpdesk_technician(user, request)
    if not (is_super_admin or is_tech):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Reporting access requires super admin or helpdesk technician privileges.",
        )
    return user, is_super_admin, None


def _reporting_user_label(record: Mapping[str, Any]) -> str:
    first = (record.get("first_name") or "").strip()
    last = (record.get("last_name") or "").strip()
    name = (f"{first} {last}").strip()
    email = (record.get("email") or "").strip()
    if name and email:
        return f"{name} <{email}>"
    return name or email or f"User #{record.get('id')}"


async def _list_reporting_eligible_users() -> list[dict[str, Any]]:
    """Return non-super-admin users sorted by display label.

    These are the users that can be granted per-report access. Super admins
    always have access regardless of grants and are intentionally excluded.
    """
    rows = await user_repo.list_users()
    eligible: list[dict[str, Any]] = []
    for record in rows or []:
        if record.get("is_super_admin"):
            continue
        try:
            user_id = int(record.get("id"))
        except (TypeError, ValueError):
            continue
        eligible.append({"id": user_id, "label": _reporting_user_label(record)})
    eligible.sort(key=lambda item: item["label"].lower())
    return eligible


async def _resolve_user_can_run_report(
    user: Mapping[str, Any], is_super_admin: bool, query_id: int
) -> bool:
    if is_super_admin:
        return True
    user_id = user.get("id")
    if user_id is None:
        return False
    try:
        return await reporting_repo.user_has_permission(int(query_id), int(user_id))
    except Exception as exc:  # pragma: no cover - defensive
        log_error("Failed to check reporting permission", error=str(exc))
        return False


async def reporting_page(
    request: Request,
    report: int | None = Query(default=None),
    error: str | None = Query(default=None),
):
    user, is_super_admin, redirect = await _require_reporting_access(request)
    if redirect:
        return redirect

    user_id = int(user.get("id")) if user.get("id") is not None else 0
    if is_super_admin:
        available = await reporting_repo.list_queries()
    else:
        available = await reporting_repo.list_queries_for_user(user_id)
    available_reports = [
        {
            "id": entry["id"],
            "name": entry["name"],
            "slug": entry.get("slug"),
        }
        for entry in available
    ]

    selected_report = None
    result = None
    error_message = _reporting_message(error)
    generated_at_iso: str | None = None
    if report is not None:
        record = await reporting_repo.get_query(int(report))
        if not record:
            error_message = "The requested report no longer exists."
        else:
            allowed = await _resolve_user_can_run_report(user, is_super_admin, record["id"])
            if not allowed:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You do not have permission to run this report.",
                )
            selected_report = record
            try:
                result = await reporting_service.run_query(record["sql_query"])
                generated_at_iso = datetime.now(timezone.utc).isoformat()
                await audit_service.record(
                    action="reporting.report.run",
                    request=request,
                    user_id=user.get("id"),
                    entity_type="reporting_query",
                    entity_id=int(record["id"]),
                    metadata={"slug": record.get("slug")},
                )
            except reporting_service.ReportingQueryError as exc:
                error_message = f"Report query is invalid: {exc}"
            except Exception as exc:  # pragma: no cover - defensive
                log_error("Reporting query execution failed", error=str(exc))
                error_message = f"Report failed to execute: {exc}"

    extra = {
        "title": "Reporting",
        "available_reports": available_reports,
        "selected_report": selected_report,
        "result": result or {"columns": [], "rows": [], "row_count": 0, "truncated": False},
        "generated_at_iso": generated_at_iso,
        "max_rows": reporting_service.MAX_RESULT_ROWS,
        "error_message": error_message,
        "can_admin_reporting": is_super_admin,
    }
    return await _render_template("reporting/index.html", request, user, extra=extra)


async def reporting_export(request: Request, report_id: int, format: str = Query(default="csv")):
    user, is_super_admin, redirect = await _require_reporting_access(request)
    if redirect:
        return redirect

    record = await reporting_repo.get_query(int(report_id))
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found.")
    allowed = await _resolve_user_can_run_report(user, is_super_admin, record["id"])
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to run this report.",
        )

    fmt = (format or "csv").strip().lower()
    if fmt not in {"csv", "json", "xml", "pdf"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported export format.",
        )

    try:
        result = await reporting_service.run_query(record["sql_query"])
    except reporting_service.ReportingQueryError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await audit_service.record(
        action="reporting.report.export",
        request=request,
        user_id=user.get("id"),
        entity_type="reporting_query",
        entity_id=int(record["id"]),
        metadata={"slug": record.get("slug"), "format": fmt, "row_count": result["row_count"]},
    )

    base_filename = (record.get("slug") or f"report-{record['id']}")
    columns = result["columns"]
    rows = result["rows"]

    if fmt == "csv":
        body = reporting_service.export_csv(columns, rows)
        return Response(
            content=body,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{base_filename}.csv"'},
        )
    if fmt == "json":
        body = reporting_service.export_json(columns, rows)
        return Response(
            content=body,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{base_filename}.json"'},
        )
    if fmt == "xml":
        body = reporting_service.export_xml(columns, rows)
        return Response(
            content=body,
            media_type="application/xml; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{base_filename}.xml"'},
        )
    # PDF
    try:
        from weasyprint import HTML  # type: ignore
    except (ImportError, OSError) as exc:  # pragma: no cover - depends on system packages
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "PDF export requires WeasyPrint and its native dependencies. "
                "See https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#installation"
            ),
        ) from exc
    html = reporting_service.export_html_for_pdf(
        record.get("name") or "Report",
        record.get("description"),
        columns,
        rows,
        datetime.now(timezone.utc),
    )
    pdf_bytes = HTML(string=html, base_url=str(request.base_url)).write_pdf()
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{base_filename}.pdf"'},
    )


async def admin_reporting(
    request: Request,
    success: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    records = await reporting_repo.list_queries()
    reports_payload: list[dict[str, Any]] = []
    for record in records:
        prepared = dict(record)
        prepared["updated_at_iso"] = _to_iso(record.get("updated_at"))
        reports_payload.append(prepared)
    extra = {
        "title": "Reporting · Manage reports",
        "reports": reports_payload,
        "success_message": _reporting_message(success),
        "error_message": _reporting_message(error),
    }
    return await _render_template("admin/reporting.html", request, user, extra=extra)


async def admin_reporting_new(request: Request, error: str | None = Query(default=None)):
    user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    eligible = await _list_reporting_eligible_users()
    extra = {
        "title": "New report",
        "form_heading": "New report",
        "submit_label": "Create report",
        "form_action": "/admin/reporting",
        "report": {},
        "eligible_users": eligible,
        "granted_user_ids": set(),
        "max_rows": reporting_service.MAX_RESULT_ROWS,
        "error_message": _reporting_message(error),
    }
    return await _render_template("admin/reporting_form.html", request, user, extra=extra)


async def admin_reporting_edit(
    request: Request, report_id: int, error: str | None = Query(default=None)
):
    user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    record = await reporting_repo.get_query(int(report_id))
    if not record:
        return RedirectResponse(
            url="/admin/reporting?error=Report+not+found",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    eligible = await _list_reporting_eligible_users()
    granted_ids = set(await reporting_repo.list_permission_user_ids(int(report_id)))
    extra = {
        "title": f"Edit report · {record['name']}",
        "form_heading": f"Edit report · {record['name']}",
        "submit_label": "Save changes",
        "form_action": f"/admin/reporting/{int(report_id)}",
        "report": record,
        "eligible_users": eligible,
        "granted_user_ids": granted_ids,
        "max_rows": reporting_service.MAX_RESULT_ROWS,
        "error_message": _reporting_message(error),
    }
    return await _render_template("admin/reporting_form.html", request, user, extra=extra)


def _parse_reporting_form(form: FormData) -> dict[str, Any]:
    name = (form.get("name") or "").strip()
    slug = (form.get("slug") or "").strip().lower()
    description = (form.get("description") or "").strip() or None
    sql_query = (form.get("sql_query") or "").strip()
    raw_user_ids = form.getlist("permission_user_ids") if hasattr(form, "getlist") else []
    user_ids: list[int] = []
    for raw in raw_user_ids or []:
        try:
            user_ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    return {
        "name": name,
        "slug": slug,
        "description": description,
        "sql_query": sql_query,
        "user_ids": user_ids,
    }


def _validate_reporting_input(payload: dict[str, Any]) -> str | None:
    if not payload["name"]:
        return "Report name is required."
    if len(payload["name"]) > 255:
        return "Report name must be 255 characters or fewer."
    if not payload["slug"]:
        return "Slug is required."
    if len(payload["slug"]) > 120:
        return "Slug must be 120 characters or fewer."
    if not _REPORTING_SLUG_RE.match(payload["slug"]):
        return "Slug may only contain letters, digits, underscores, and hyphens."
    if not payload["sql_query"]:
        return "SQL query is required."
    if payload["description"] and len(payload["description"]) > 1000:
        return "Description must be 1000 characters or fewer."
    try:
        reporting_service.validate_select_query(payload["sql_query"])
    except reporting_service.ReportingQueryError as exc:
        return str(exc)
    return None


async def admin_reporting_create(request: Request):
    user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    payload = _parse_reporting_form(form)
    error = _validate_reporting_input(payload)
    if error:
        encoded = urlencode({"error": error})
        return RedirectResponse(
            url=f"/admin/reporting/new?{encoded}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    existing = await reporting_repo.get_query_by_slug(payload["slug"])
    if existing:
        encoded = urlencode({"error": "That slug is already in use."})
        return RedirectResponse(
            url=f"/admin/reporting/new?{encoded}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    new_id = await reporting_repo.create_query(
        slug=payload["slug"],
        name=payload["name"],
        description=payload["description"],
        sql_query=payload["sql_query"],
        created_by=user.get("id"),
    )
    await reporting_repo.replace_permissions(int(new_id), payload["user_ids"])
    await audit_service.record(
        action="reporting.report.create",
        request=request,
        user_id=user.get("id"),
        entity_type="reporting_query",
        entity_id=int(new_id),
        after={
            "slug": payload["slug"],
            "name": payload["name"],
            "description": payload["description"],
            "permission_user_ids": payload["user_ids"],
        },
    )
    encoded = urlencode({"success": "Report created."})
    return RedirectResponse(
        url=f"/admin/reporting?{encoded}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def admin_reporting_update(request: Request, report_id: int):
    user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    record = await reporting_repo.get_query(int(report_id))
    if not record:
        return RedirectResponse(
            url="/admin/reporting?error=Report+not+found",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    form = await request.form()
    payload = _parse_reporting_form(form)
    error = _validate_reporting_input(payload)
    if error:
        encoded = urlencode({"error": error})
        return RedirectResponse(
            url=f"/admin/reporting/{int(report_id)}/edit?{encoded}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if payload["slug"] != record.get("slug"):
        clash = await reporting_repo.get_query_by_slug(payload["slug"])
        if clash and int(clash["id"]) != int(report_id):
            encoded = urlencode({"error": "That slug is already in use."})
            return RedirectResponse(
                url=f"/admin/reporting/{int(report_id)}/edit?{encoded}",
                status_code=status.HTTP_303_SEE_OTHER,
            )
    before_snapshot = {
        "slug": record.get("slug"),
        "name": record.get("name"),
        "description": record.get("description"),
        "sql_query": record.get("sql_query"),
    }
    await reporting_repo.update_query(
        int(report_id),
        slug=payload["slug"],
        name=payload["name"],
        description=payload["description"],
        sql_query=payload["sql_query"],
    )
    await reporting_repo.replace_permissions(int(report_id), payload["user_ids"])
    await audit_service.record(
        action="reporting.report.update",
        request=request,
        user_id=user.get("id"),
        entity_type="reporting_query",
        entity_id=int(report_id),
        before=before_snapshot,
        after={
            "slug": payload["slug"],
            "name": payload["name"],
            "description": payload["description"],
            "sql_query": payload["sql_query"],
            "permission_user_ids": payload["user_ids"],
        },
    )
    encoded = urlencode({"success": "Report updated."})
    return RedirectResponse(
        url=f"/admin/reporting?{encoded}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def admin_reporting_delete(request: Request, report_id: int):
    user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    record = await reporting_repo.get_query(int(report_id))
    if not record:
        return RedirectResponse(
            url="/admin/reporting?error=Report+not+found",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    await reporting_repo.delete_query(int(report_id))
    await audit_service.record(
        action="reporting.report.delete",
        request=request,
        user_id=user.get("id"),
        entity_type="reporting_query",
        entity_id=int(report_id),
        before={
            "slug": record.get("slug"),
            "name": record.get("name"),
            "description": record.get("description"),
        },
    )
    encoded = urlencode({"success": f"Deleted report '{record.get('name')}'."})
    return RedirectResponse(
        url=f"/admin/reporting?{encoded}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


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

    companies = await company_access.list_accessible_companies(user)
    request.state.available_companies = companies

    if any(company.get("company_id") == company_id for company in companies):
        await session_manager.set_active_company(session, company_id)
        user["company_id"] = company_id
        request.state.active_company_id = company_id

    destination = _sanitize_local_redirect_target(return_url, fallback="/")

    return RedirectResponse(url=destination, status_code=status.HTTP_303_SEE_OTHER)


@app.get("/m365", response_class=HTMLResponse)
async def m365_page(request: Request, error: str | None = None, success: str | None = None):
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

    # Fetch per-company admin credentials for super admins
    admin_credential_view = None
    if user.get("is_super_admin"):
        admin_creds = await m365_service.get_company_admin_credentials(company_id)
        if admin_creds:
            admin_expires = admin_creds.get("client_secret_expires_at")
            if isinstance(admin_expires, datetime):
                admin_expires_display = admin_expires.replace(tzinfo=timezone.utc).isoformat()
            elif admin_expires:
                admin_expires_display = str(admin_expires)
            else:
                admin_expires_display = None
            admin_credential_view = {
                "client_id": admin_creds.get("client_id"),
                "tenant_id": admin_creds.get("tenant_id"),
                "client_secret_expires_at": admin_expires_display,
            }

    extra = {
        "title": "Office 365",
        "company": company,
        "credential": credential_view,
        "admin_credential": admin_credential_view,
        "error": error,
        "success": success,
        "is_super_admin": bool(user.get("is_super_admin")),
        "has_credentials": bool(credentials),
        "has_admin_credentials": bool(admin_credential_view),
        "admin_credentials_configured": bool(all(await _get_m365_admin_credentials(company_id))),
    }
    return await _render_template("m365/index.html", request, user, extra=extra)


@app.get("/m365/benchmarks", response_class=RedirectResponse)
async def m365_benchmarks_redirect(request: Request):
    """Redirect the old CIS Benchmarks page to the merged Best Practices page."""
    return RedirectResponse(url="/m365/best-practices", status_code=status.HTTP_301_MOVED_PERMANENTLY)


@app.post("/m365/benchmarks/run", response_class=RedirectResponse)
async def run_m365_benchmarks(request: Request):
    user, membership, _, company_id, redirect = await _load_license_context(request)
    if redirect:
        return redirect
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin privileges required")
    try:
        await cis_benchmark_service.run_benchmarks(company_id)
        log_info("CIS benchmarks run", company_id=company_id, user_id=user.get("id"))
    except m365_service.M365Error as exc:
        return RedirectResponse(
            url=f"/m365/best-practices?error={quote(str(exc))}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(url="/m365/best-practices?success=Benchmarks+completed", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/m365/benchmarks/exclude", response_class=RedirectResponse)
async def exclude_benchmark_check(
    request: Request,
    check_id: str = Form(..., alias="checkId"),
    reason: str = Form("", alias="reason"),
):
    user, membership, _, company_id, redirect = await _load_license_context(request)
    if redirect:
        return redirect
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin privileges required")
    await cis_benchmark_service.add_exclusion(company_id, check_id, reason)
    return RedirectResponse(url="/m365/best-practices?success=Check+excluded", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/m365/benchmarks/exclude/remove", response_class=RedirectResponse)
async def remove_benchmark_exclusion(
    request: Request,
    check_id: str = Form(..., alias="checkId"),
):
    user, membership, _, company_id, redirect = await _load_license_context(request)
    if redirect:
        return redirect
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin privileges required")
    await cis_benchmark_service.remove_exclusion(company_id, check_id)
    return RedirectResponse(url="/m365/best-practices?success=Exclusion+removed", status_code=status.HTTP_303_SEE_OTHER)


# ---------------------------------------------------------------------------
# Microsoft 365 Best Practices
# ---------------------------------------------------------------------------


async def _load_m365_best_practices_context(request: Request, *, super_admin_only: bool = False):
    """Load context for the M365 Best Practices pages.

    A user may view the page if they are a super admin or if their company
    membership grants ``can_view_m365_best_practices``.  Pass
    ``super_admin_only=True`` for endpoints that mutate global settings or
    trigger evaluations.
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
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid company identifier") from exc
    membership = await user_company_repo.get_user_company(user["id"], company_id)
    can_view = bool(membership and membership.get("can_view_m365_best_practices"))
    if super_admin_only:
        if not is_super_admin:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin privileges required")
    elif not (is_super_admin or can_view):
        return (
            user,
            membership,
            None,
            company_id,
            RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER),
        )
    company = await company_repo.get_company_by_id(company_id)
    return user, membership, company, company_id, None


@app.get("/m365/best-practices", response_class=HTMLResponse)
async def m365_best_practices_page(request: Request, error: str | None = None, success: str | None = None):
    user, membership, company, company_id, redirect = await _load_m365_best_practices_context(request)
    if redirect:
        return redirect
    credentials = await m365_service.get_credentials(company_id)
    results = await m365_best_practices_service.get_last_results(company_id)
    catalog = m365_best_practices_service.list_best_practices()
    enabled_ids = await m365_best_practices_service.get_enabled_check_ids()
    enabled_catalog = [bp for bp in catalog if bp["id"] in enabled_ids]
    extra = {
        "title": "M365 Best Practices",
        "company": company,
        "results": results,
        "catalog": enabled_catalog,
        "has_credentials": bool(credentials),
        "is_super_admin": bool(user.get("is_super_admin")),
        "error": error,
        "success": success,
    }
    return await _render_template("m365/best_practices.html", request, user, extra=extra)


@app.post("/m365/best-practices/run", response_class=RedirectResponse)
async def run_m365_best_practices(request: Request):
    user, membership, _, company_id, redirect = await _load_m365_best_practices_context(
        request, super_admin_only=True,
    )
    if redirect:
        return redirect

    user_id = user.get("id")
    reset_count = await m365_best_practices_service.reset_enabled_results_to_unknown(company_id)

    def _on_complete(_results: list[dict]) -> None:
        log_info(
            "M365 best practices run completed",
            company_id=company_id,
            user_id=user_id,
            check_count=len(_results),
        )

    def _on_error(exc: Exception) -> None:
        log_error(
            "M365 best practices run failed",
            company_id=company_id,
            user_id=user_id,
            error=str(exc),
        )

    background_tasks.queue_background_task(
        lambda: m365_best_practices_service.run_best_practices(company_id),
        description="m365-best-practices-run",
        on_complete=_on_complete,
        on_error=_on_error,
    )
    log_info(
        "M365 best practices run queued",
        company_id=company_id,
        user_id=user_id,
        reset_to_unknown_count=reset_count,
    )
    return RedirectResponse(
        url="/m365/best-practices?success=Best+practice+evaluation+started+in+the+background",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/m365/best-practices/check/{check_id}", response_class=RedirectResponse)
async def run_single_m365_best_practice_check(request: Request, check_id: str):
    """Run a single best-practice check for the current company."""
    user, membership, _, company_id, redirect = await _load_m365_best_practices_context(
        request, super_admin_only=True,
    )
    if redirect:
        return redirect
    known_ids = {bp["id"] for bp in m365_best_practices_service.list_best_practices()}
    if check_id not in known_ids:
        return RedirectResponse(
            url=f"/m365/best-practices?error={quote('Unknown best-practice check ID')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    try:
        await m365_best_practices_service.run_single_check(
            company_id=company_id,
            check_id=check_id,
        )
        log_info(
            "M365 single best practice check run",
            company_id=company_id,
            check_id=check_id,
            user_id=user.get("id"),
        )
    except ValueError as exc:
        return RedirectResponse(
            url=f"/m365/best-practices?error={quote(str(exc))}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except m365_service.M365Error as exc:
        return RedirectResponse(
            url=f"/m365/best-practices?error={quote(str(exc))}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        url="/m365/best-practices?success=Check+evaluated",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/m365/best-practices/remediate/{check_id}", response_class=RedirectResponse)
async def remediate_m365_best_practice(request: Request, check_id: str):
    """Run automated remediation for a single best-practice check."""
    user, membership, _, company_id, redirect = await _load_m365_best_practices_context(
        request, super_admin_only=True,
    )
    if redirect:
        return redirect
    # Validate that the check_id is a known best practice to prevent unintended operations
    known_ids = {bp["id"] for bp in m365_best_practices_service.list_best_practices()}
    if check_id not in known_ids:
        return RedirectResponse(
            url=f"/m365/best-practices?error={quote('Unknown best-practice check ID')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    result = await m365_best_practices_service.remediate_check(
        company_id=company_id,
        check_id=check_id,
    )
    log_info(
        "M365 best practice remediation triggered",
        company_id=company_id,
        check_id=check_id,
        user_id=user.get("id"),
        success=result.get("success"),
    )
    message = quote(result.get("message", "Remediation attempted"))
    if result.get("success"):
        return RedirectResponse(
            url=f"/m365/best-practices?success={message}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        url=f"/m365/best-practices?error={message}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.get("/m365/best-practices/settings", response_class=HTMLResponse)
async def m365_best_practices_settings_page(
    request: Request,
    error: str | None = None,
    success: str | None = None,
):
    user, membership, company, company_id, redirect = await _load_m365_best_practices_context(
        request, super_admin_only=True,
    )
    if redirect:
        return redirect
    catalog_with_settings = await m365_best_practices_service.list_settings_with_catalog(company_id)
    extra = {
        "title": "M365 Best Practices Settings",
        "company": company,
        "catalog": catalog_with_settings,
        "is_super_admin": True,
        "error": error,
        "success": success,
    }
    return await _render_template(
        "m365/best_practices_settings.html",
        request,
        user,
        extra=extra,
    )


@app.post("/m365/best-practices/settings", response_class=RedirectResponse)
async def save_m365_best_practices_settings(request: Request):
    user, membership, _, company_id, redirect = await _load_m365_best_practices_context(
        request, super_admin_only=True,
    )
    if redirect:
        return redirect
    form = await request.form()
    enabled_ids = {value for value in form.getlist("enabled")}
    auto_remediate_ids = {value for value in form.getlist("auto_remediate")}
    excluded_ids = {value for value in form.getlist("excluded")}
    await m365_best_practices_service.set_enabled_checks(enabled_ids, auto_remediate_ids)
    await m365_best_practices_service.save_company_exclusions(company_id, excluded_ids)
    log_info(
        "M365 best practice settings updated",
        user_id=user.get("id"),
        enabled_count=len(enabled_ids),
        auto_remediate_count=len(auto_remediate_ids),
        excluded_count=len(excluded_ids),
    )
    return RedirectResponse(
        url="/m365/best-practices/settings?success=Settings+saved",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def _load_m365_mailbox_context(request: Request, *, mailbox_permission: str):
    """Load context for M365 mailbox pages.

    A user may access the page if they are a super admin, have
    ``can_manage_licenses``, or have the specific ``mailbox_permission`` flag
    (e.g. ``can_view_m365_user_mailboxes`` or ``can_view_m365_shared_mailboxes``).
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
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid company identifier") from exc
    membership = await user_company_repo.get_user_company(user["id"], company_id)
    can_access = bool(
        is_super_admin
        or (membership and membership.get("can_manage_licenses"))
        or (membership and membership.get(mailbox_permission))
    )
    if not can_access:
        return (
            user,
            membership,
            None,
            company_id,
            RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER),
        )
    company = await company_repo.get_company_by_id(company_id)
    return user, membership, company, company_id, None


@app.get("/m365/mailboxes/users", response_class=HTMLResponse)
async def m365_user_mailboxes_page(request: Request, error: str | None = None, success: str | None = None):
    user, membership, company, company_id, redirect = await _load_m365_mailbox_context(
        request, mailbox_permission="can_view_m365_user_mailboxes"
    )
    if redirect:
        return redirect
    credentials = await m365_service.get_credentials(company_id)
    mailboxes = await m365_service.get_user_mailboxes(company_id)
    synced_at = await m365_repo.get_mailbox_synced_at(company_id)
    extra = {
        "title": "User Mailboxes",
        "company": company,
        "mailboxes": mailboxes,
        "synced_at": synced_at,
        "has_credentials": bool(credentials),
        "error": error,
        "success": success,
    }
    return await _render_template("m365/user_mailboxes.html", request, user, extra=extra)


@app.get("/m365/mailboxes/shared", response_class=HTMLResponse)
async def m365_shared_mailboxes_page(request: Request, error: str | None = None, success: str | None = None):
    user, membership, company, company_id, redirect = await _load_m365_mailbox_context(
        request, mailbox_permission="can_view_m365_shared_mailboxes"
    )
    if redirect:
        return redirect
    credentials = await m365_service.get_credentials(company_id)
    mailboxes = await m365_service.get_shared_mailboxes(company_id)
    synced_at = await m365_repo.get_mailbox_synced_at(company_id)
    extra = {
        "title": "Shared Mailboxes",
        "company": company,
        "mailboxes": mailboxes,
        "synced_at": synced_at,
        "has_credentials": bool(credentials),
        "error": error,
        "success": success,
    }
    return await _render_template("m365/shared_mailboxes.html", request, user, extra=extra)


@app.post("/m365/mailboxes/sync", response_class=JSONResponse, tags=["Microsoft 365"])
async def sync_m365_mailboxes(request: Request):
    """Queue the sync_m365_data scheduled task for this company in the background.

    Returns 202 Accepted immediately so the browser never waits for the long-running
    sync and never hits a gateway timeout.
    """
    user, membership, _, company_id, redirect = await _load_license_context(request)
    if redirect:
        return JSONResponse({"error": "Authentication required"}, status_code=401)
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin privileges required")
    task = await scheduled_tasks_repo.get_first_task_for_company_by_commands(
        company_id, ["sync_m365_mailboxes", "sync_m365_data", "sync_o365"]
    )
    if task is not None:
        asyncio.create_task(scheduler_service.run_now(task["id"]))
    else:
        asyncio.create_task(m365_service.sync_mailboxes(company_id))
    log_info("M365 mailbox sync queued", company_id=company_id, user_id=user.get("id"))
    return JSONResponse({"queued": True}, status_code=202)


@app.post("/m365/mailboxes/enable-archive", response_class=JSONResponse, tags=["Microsoft 365"])
async def enable_m365_user_archive(request: Request):
    """Enable the in-place archive mailbox for a user via Exchange Online PowerShell.

    Issues ``Enable-Mailbox -Identity <upn> -Archive`` for the supplied UPN.  The
    UPN must belong to a known user mailbox in this company; otherwise a 404 is
    returned.  Requires super-admin privileges.
    """
    user, membership, company, company_id, redirect = await _load_license_context(request)
    if redirect:
        return JSONResponse({"error": "Authentication required"}, status_code=401)
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin privileges required")

    try:
        body = await request.json()
    except (ValueError, json.JSONDecodeError):
        body = {}
    upn = str((body or {}).get("upn") or "").strip()
    if not upn:
        return JSONResponse({"error": "A user principal name is required"}, status_code=400)

    user_mbs = await m365_service.get_user_mailboxes(company_id)
    matching = next((mb for mb in user_mbs if mb.get("user_principal_name") == upn), None)
    if matching is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mailbox not found")
    if matching.get("has_archive"):
        return JSONResponse({"enabled": True, "already_enabled": True})

    try:
        await m365_service.enable_user_archive(company_id, upn)
    except m365_service.M365Error as exc:
        logger.exception("Failed to enable in-place archive for UPN %s", upn)
        return JSONResponse(
            {"error": "Unable to enable in-place archive at this time."},
            status_code=503,
        )
    log_info("M365 in-place archive enable requested", company_id=company_id, user_id=user.get("id"), upn=upn)
    return JSONResponse({"enabled": True})


@app.get("/m365/mailboxes/permissions", response_class=JSONResponse, tags=["Microsoft 365"])
async def get_m365_mailbox_permissions(request: Request, upn: str):
    """Return mailbox permission details for a given mailbox UPN.

    Returns a JSON object with ``can_access`` (mailboxes this identity can access
    via M365 group membership) and ``accessible_by`` (members of the M365 group
    backing this mailbox).
    """
    user, membership, company, company_id, redirect = await _load_license_context(request)
    if redirect:
        return JSONResponse({"error": "Authentication required"}, status_code=401)

    # Validate the UPN belongs to a known mailbox for this company to prevent
    # arbitrary Graph API queries with user-supplied input.
    user_mbs = await m365_service.get_user_mailboxes(company_id)
    shared_mbs = await m365_service.get_shared_mailboxes(company_id)
    known_upns = {mb["user_principal_name"] for mb in user_mbs + shared_mbs}
    if upn not in known_upns:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mailbox not found")

    try:
        permissions = await m365_service.get_mailbox_permissions(company_id, upn)
        return JSONResponse(permissions)
    except m365_service.M365Error as exc:
        logger.exception("Failed to get mailbox permissions for UPN %s", upn)
        return JSONResponse(
            {"error": "Unable to retrieve mailbox permissions at this time."},
            status_code=503,
        )


@app.post("/m365/checks/report-privacy", response_class=RedirectResponse)
async def check_m365_report_privacy(request: Request):
    """Check whether Microsoft 365 report privacy concealment is active for this tenant.

    Detects the *Display concealed user, group, and site names in all reports*
    setting.  When enabled, mailbox identifiers in reports are replaced with hex
    hashes, breaking mailbox sync.  Redirects back to ``/m365`` with a success or
    error message.
    """
    user, membership, _, company_id, redirect = await _load_license_context(request)
    if redirect:
        return redirect
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin privileges required")
    try:
        concealed = await m365_service.check_report_privacy(company_id)
    except m365_service.M365Error as exc:
        return RedirectResponse(
            url=f"/m365?error={quote(str(exc))}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if concealed:
        msg = (
            "Mailbox sync failed because Microsoft 365 reports are concealing mailbox identifiers. "
            "Disable the Microsoft 365 admin center privacy option "
            "'Display concealed user, group, and site names in all reports', "
            "then run mailbox sync again."
        )
        return RedirectResponse(
            url=f"/m365?error={quote(msg)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        url=f"/m365?success={quote('Mailbox report privacy check passed – identifiers are not concealed')}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/m365/permissions/verify")
async def verify_m365_permissions(request: Request):
    """Check (and optionally auto-grant) missing Graph API permissions for this company.

    Returns a JSON object with:
    - ``all_ok``  – True if all required permissions are present
    - ``missing`` – list of role IDs that are not yet granted
    - ``present`` – list of role IDs that are currently granted
    - ``updated`` – True if missing permissions were successfully auto-granted
    - ``error``   – human-readable message when the check or update failed
    """
    user, membership, _, company_id, redirect = await _load_license_context(request)
    if redirect:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin privileges required")
    try:
        result = await m365_service.verify_tenant_permissions(company_id)
    except m365_service.M365Error as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return JSONResponse(result)


@app.get("/m365/diagnostics", response_class=HTMLResponse)
async def m365_diagnostics_page(request: Request, error: str | None = None, success: str | None = None):
    """Display the enterprise app permission diagnostics page."""
    user, _, company, company_id, redirect = await _load_license_context(request)
    if redirect:
        return redirect
    if not user.get("is_super_admin"):
        return RedirectResponse(url="/m365", status_code=status.HTTP_303_SEE_OTHER)

    credentials = await m365_service.get_credentials(company_id)
    last_results = await m365_service.get_last_enterprise_app_permissions(company_id)

    extra = {
        "title": "Office 365 Diagnostics",
        "company": company,
        "has_credentials": bool(credentials),
        "catalog": m365_service.ENTERPRISE_APP_CATALOG,
        "results": last_results,
        "error": error,
        "success": success,
        "is_super_admin": True,
    }
    return await _render_template("m365/diagnostics.html", request, user, extra=extra)


@app.post("/m365/diagnostics/check", response_class=RedirectResponse)
async def run_m365_diagnostics_check(request: Request):
    """Run the enterprise app permission check and store results."""
    user, _, __, company_id, redirect = await _load_license_context(request)
    if redirect:
        return redirect
    if not user.get("is_super_admin"):
        return RedirectResponse(url="/m365", status_code=status.HTTP_303_SEE_OTHER)
    try:
        await m365_service.check_enterprise_app_permissions(company_id)
    except m365_service.M365Error as exc:
        return RedirectResponse(
            url=f"/m365/diagnostics?error={quote(str(exc))}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        url="/m365/diagnostics?success=Permission+check+completed",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/m365/diagnostics/repair", response_class=RedirectResponse)
async def repair_m365_permissions(request: Request):
    """Grant any missing enterprise app permissions and re-check results.

    Uses the stored delegated refresh token from the admin connect flow.
    If no token is available the admin is redirected to the connect flow
    (with ``return_to=diagnostics`` in state) so that the repair runs
    automatically after they re-authorise.
    """
    user, _, __, company_id, redirect = await _load_license_context(request)
    if redirect:
        return redirect
    if not user.get("is_super_admin"):
        return RedirectResponse(url="/m365", status_code=status.HTTP_303_SEE_OTHER)

    try:
        outcome = await m365_service.repair_enterprise_app_permissions(company_id)
    except m365_service.M365NoDelegatedTokenError:
        # No delegated token available – redirect to the connect flow so the
        # admin can re-authorise; the callback will automatically grant missing
        # permissions and then return to the diagnostics page.
        credentials = await m365_service.get_credentials(company_id)
        if credentials:
            state = oauth_state_serializer.dumps({
                "company_id": company_id,
                "user_id": user.get("id"),
                "flow": "connect",
                "return_to": "diagnostics",
            })
            params = {
                "client_id": credentials["client_id"],
                "response_type": "code",
                "redirect_uri": _build_m365_redirect_uri(request),
                "response_mode": "query",
                "scope": m365_service.CONNECT_SCOPE,
                "state": state,
                "prompt": "consent",
            }
            authorize_url = (
                f"https://login.microsoftonline.com/{credentials['tenant_id']}"
                f"/oauth2/v2.0/authorize?{urlencode(params)}"
            )
            return RedirectResponse(url=authorize_url, status_code=status.HTTP_303_SEE_OTHER)
        return RedirectResponse(
            url="/m365/diagnostics?error=No+credentials+configured",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except m365_service.M365Error as exc:
        return RedirectResponse(
            url=f"/m365/diagnostics?error={quote(str(exc))}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    if outcome.get("granted"):
        msg = "Missing permissions have been granted successfully."
    else:
        msg = "No new permissions were needed – all permissions are already granted."

    return RedirectResponse(
        url=f"/m365/diagnostics?success={quote(msg)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


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


@app.post("/m365/admin-credentials", response_class=RedirectResponse)
async def save_m365_admin_credentials(
    request: Request,
    client_id: str = Form(..., alias="adminClientId"),
    client_secret: str = Form("", alias="adminClientSecret"),
    tenant_id: str = Form("", alias="adminTenantId"),
):
    """Save per-company M365 admin enterprise app credentials."""
    user, membership, _, company_id, redirect = await _load_license_context(request)
    if redirect:
        return redirect
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin privileges required")

    # Only update if client_secret is provided (non-empty)
    # If empty, we might be updating other fields only
    existing = await m365_service.get_company_admin_credentials(company_id)
    if existing and not client_secret.strip():
        # Keep existing secret when none provided
        client_secret = existing.get("client_secret", "")

    if not client_id.strip() or not client_secret.strip():
        encoded = urlencode({"error": "Admin Client ID and Client Secret are required."})
        return RedirectResponse(url=f"/m365?{encoded}", status_code=status.HTTP_303_SEE_OTHER)

    await m365_service.upsert_company_admin_credentials(
        company_id=company_id,
        client_id=client_id.strip(),
        client_secret=client_secret.strip(),
        tenant_id=tenant_id.strip() if tenant_id.strip() else None,
    )
    log_info("M365 admin credentials updated", company_id=company_id, user_id=user.get("id"))
    encoded = urlencode({"success": "Admin credentials saved successfully."})
    return RedirectResponse(url=f"/m365?{encoded}", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/m365/admin-credentials/delete", response_class=RedirectResponse)
async def delete_m365_admin_credentials(request: Request):
    """Delete per-company M365 admin enterprise app credentials."""
    user, membership, _, company_id, redirect = await _load_license_context(request)
    if redirect:
        return redirect
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin privileges required")
    await m365_service.delete_company_admin_credentials(company_id)
    log_info("M365 admin credentials deleted", company_id=company_id, user_id=user.get("id"))
    return RedirectResponse(url="/m365", status_code=status.HTTP_303_SEE_OTHER)


def _build_m365_redirect_uri(request: Request) -> str:
    """Return the absolute redirect URI for the M365 OAuth callback.

    Uses PORTAL_URL when configured so that the redirect URI is stable
    regardless of which host the request arrives on.  Falls back to
    request.url_for and forces the scheme to HTTPS (required by Azure AD).
    """
    if settings.portal_url:
        base = str(settings.portal_url).rstrip("/")
        return f"{base}/m365/callback"
    uri = str(request.url_for("m365_callback"))
    if uri.startswith("http://"):
        uri = "https://" + uri[len("http://"):]
    return uri


@app.post("/m365/test", response_class=RedirectResponse)
async def test_m365_connectivity(request: Request):
    user, membership, _, company_id, redirect = await _load_license_context(request)
    if redirect:
        return redirect
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin privileges required")
    try:
        result = await m365_service.test_connectivity(company_id)
    except m365_service.M365Error as exc:
        encoded = urlencode({"error": f"Microsoft 365 connectivity test failed: {exc}"})
        return RedirectResponse(url=f"/m365?{encoded}", status_code=status.HTTP_303_SEE_OTHER)

    org_name = result.get("organization_name")
    summary = "Microsoft 365 connectivity test succeeded"
    if org_name:
        summary = f"Microsoft 365 connectivity test succeeded for {org_name}"
    log_info(summary, company_id=company_id, user_id=user.get("id"))
    encoded = urlencode({"success": summary})
    return RedirectResponse(url=f"/m365?{encoded}", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/m365/sync", response_class=JSONResponse)
async def sync_m365(request: Request):
    user, membership, _, company_id, redirect = await _load_license_context(request)
    if redirect:
        return redirect
    try:
        await m365_service.sync_company_licenses(company_id)
    except m365_service.M365Error as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    mailboxes_synced = 0
    try:
        mailboxes_synced = await m365_service.sync_mailboxes(company_id)
    except Exception:
        pass
    log_info("Microsoft 365 license sync triggered", company_id=company_id, user_id=user.get("id"))
    return JSONResponse({"success": True, "mailboxes_synced": mailboxes_synced})


@app.get("/m365/connect")
async def m365_connect(request: Request):
    user, membership, _, company_id, redirect = await _load_license_context(request)
    if redirect:
        return redirect
    credentials = await m365_service.get_credentials(company_id)
    if not credentials:
        return RedirectResponse(url="/m365", status_code=status.HTTP_303_SEE_OTHER)
    redirect_uri = _build_m365_redirect_uri(request)
    state = oauth_state_serializer.dumps({
        "company_id": company_id,
        "user_id": user.get("id"),
    })
    params = {
        "client_id": credentials["client_id"],
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": m365_service.CONNECT_SCOPE,
        "state": state,
        "prompt": "consent",
    }
    authorize_url = (
        f"https://login.microsoftonline.com/{credentials['tenant_id']}/oauth2/v2.0/authorize"
        f"?{urlencode(params)}"
    )
    return RedirectResponse(url=authorize_url, status_code=status.HTTP_303_SEE_OTHER)


@app.get("/m365/provision")
async def m365_provision(request: Request, tenant_id: str = Query(...)):
    """Start the admin-consent OAuth flow to auto-provision an enterprise app."""
    user, membership, _, company_id, redirect = await _load_license_context(request)
    if redirect:
        return redirect
    if not user.get("is_super_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin privileges required",
        )
    tenant_id = tenant_id.strip()
    if not tenant_id:
        encoded = urlencode({"error": "Tenant ID is required to auto-provision."})
        return RedirectResponse(url=f"/m365?{encoded}", status_code=status.HTTP_303_SEE_OTHER)
    redirect_uri = _build_m365_redirect_uri(request)
    code_verifier, code_challenge = m365_service.generate_pkce_pair()
    verifier_id = await _store_m365_provision_code_verifier(code_verifier)
    state = oauth_state_serializer.dumps(
        {
            "company_id": company_id,
            "user_id": user.get("id"),
            "tenant_id": tenant_id,
            "flow": "provision",
            "verifier_id": verifier_id,
        }
    )
    oauth_client_id = await m365_service.get_effective_pkce_client_id_for_company(
        company_id, redirect_uri=redirect_uri
    )
    params = {
        "client_id": oauth_client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": m365_service.PROVISION_SCOPE,
        "state": state,
        "prompt": "consent",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        # domain_hint directs the admin to sign in to the correct customer
        # tenant without requiring the PKCE client to be registered in that
        # tenant (avoids AADSTS700016 for single-tenant partner apps).
        "domain_hint": tenant_id,
    }
    # Use the /organizations endpoint so that the PKCE public client does not
    # need to be registered or consented in every customer tenant.  The
    # domain_hint above steers the Global Admin to the correct tenant.
    authorize_url = (
        "https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize"
        f"?{urlencode(params)}"
    )
    return RedirectResponse(url=authorize_url, status_code=status.HTTP_303_SEE_OTHER)


async def admin_company_m365_provision(
    company_id: int, request: Request, tenant_id: str = Query(...)
):
    """Start admin-consent OAuth flow to auto-provision an enterprise app for a company."""
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    tenant_id = tenant_id.strip()
    if not tenant_id:
        return _company_edit_redirect(
            company_id=company_id,
            error="Tenant ID is required to auto-provision.",
        )
    redirect_uri = _build_m365_redirect_uri(request)
    code_verifier, code_challenge = m365_service.generate_pkce_pair()
    verifier_id = await _store_m365_provision_code_verifier(code_verifier)
    state = oauth_state_serializer.dumps(
        {
            "company_id": company_id,
            "user_id": current_user.get("id"),
            "tenant_id": tenant_id,
            "flow": "provision",
            "return_to": "company_edit",
            "verifier_id": verifier_id,
        }
    )
    oauth_client_id = await m365_service.get_effective_pkce_client_id_for_company(
        company_id, redirect_uri=redirect_uri
    )
    params = {
        "client_id": oauth_client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": m365_service.PROVISION_SCOPE,
        "state": state,
        "prompt": "consent",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        # domain_hint directs the admin to sign in to the correct customer
        # tenant without requiring the PKCE client to be registered in that
        # tenant (avoids AADSTS700016 for single-tenant partner apps).
        "domain_hint": tenant_id,
    }
    # Use the /organizations endpoint so that the PKCE public client does not
    # need to be registered or consented in every customer tenant.
    authorize_url = (
        "https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize"
        f"?{urlencode(params)}"
    )
    return RedirectResponse(url=authorize_url, status_code=status.HTTP_303_SEE_OTHER)



async def _get_m365_admin_credentials(company_id: int | None = None) -> tuple[str | None, str | None]:
    """Return (client_id, client_secret) for the M365 admin app flow.

    Resolution order:
    1. Per-company admin credentials from company_m365_credentials (if company_id provided).
    2. Global ``m365-admin`` integration module settings.
    3. Environment variables ``M365_ADMIN_CLIENT_ID`` / ``M365_ADMIN_CLIENT_SECRET``.

    The ``client_secret`` is decrypted if it was stored as ciphertext;
    plaintext values (manually configured) are returned unchanged because
    :func:`decrypt_secret` is a no-op for non-ciphertext strings.
    """
    # 1. Per-company admin credentials (when company_id is provided)
    if company_id is not None:
        company_admin_creds = await m365_service.get_company_admin_credentials(company_id)
        if company_admin_creds:
            client_id = company_admin_creds.get("client_id")
            client_secret = company_admin_creds.get("client_secret")
            if client_id and client_secret:
                return client_id, client_secret

    # 2. Global module credentials
    try:
        module = await modules_service.get_module("m365-admin", redact=False)
    except RuntimeError:
        module = None
    if module:
        module_settings = module.get("settings") or {}
        client_id = str(module_settings.get("client_id") or "").strip() or None
        raw_secret = str(module_settings.get("client_secret") or "").strip() or None
        if client_id and raw_secret:
            client_secret = decrypt_secret(raw_secret)
            return client_id, client_secret

    # 3. Fall back to environment variables
    return settings.m365_admin_client_id or None, settings.m365_admin_client_secret or None


@app.get("/m365/discover")
async def m365_discover(request: Request):
    """Sign in as Global Admin to discover the tenant ID automatically."""
    user, membership, _, company_id, redirect = await _load_license_context(request)
    if redirect:
        return redirect
    if not user.get("is_super_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin privileges required",
        )
    redirect_uri = _build_m365_redirect_uri(request)

    # Always use PKCE for the discover flow regardless of whether admin
    # credentials are configured.  The discover step only needs to extract
    # a tenant ID from the id_token - it requires no confidential-client
    # capabilities.  Using PKCE avoids AADSTS700025 ("Client is public so
    # neither client_assertion nor client_secret should be presented")
    # which occurs when the configured admin client_id belongs to a public
    # PKCE app rather than a confidential app.
    code_verifier, code_challenge = m365_service.generate_pkce_pair()
    oauth_client_id = await m365_service.get_effective_pkce_client_id_for_company(
        company_id, redirect_uri=redirect_uri
    )

    state_payload: dict = {
        "company_id": company_id,
        "user_id": user.get("id"),
        "flow": "discover",
        "code_verifier": code_verifier,
    }

    state = oauth_state_serializer.dumps(state_payload)
    params: dict = {
        "client_id": oauth_client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": m365_service.DISCOVER_SCOPE,
        "state": state,
        "prompt": "select_account",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }

    authorize_url = (
        "https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize"
        f"?{urlencode(params)}"
    )
    return RedirectResponse(url=authorize_url, status_code=status.HTTP_303_SEE_OTHER)


async def admin_company_m365_discover(company_id: int, request: Request):
    """Sign in as Global Admin to discover the tenant ID for a company."""
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    redirect_uri = _build_m365_redirect_uri(request)

    # Always use PKCE for the discover flow regardless of whether admin
    # credentials are configured.  See the /m365/discover handler for the
    # full rationale (avoids AADSTS700025 on reprovision with public client).
    code_verifier, code_challenge = m365_service.generate_pkce_pair()
    oauth_client_id = await m365_service.get_effective_pkce_client_id_for_company(
        company_id, redirect_uri=redirect_uri
    )

    state_payload: dict = {
        "company_id": company_id,
        "user_id": current_user.get("id"),
        "flow": "discover",
        "return_to": "company_edit",
        "code_verifier": code_verifier,
    }

    state = oauth_state_serializer.dumps(state_payload)
    params: dict = {
        "client_id": oauth_client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": m365_service.DISCOVER_SCOPE,
        "state": state,
        "prompt": "select_account",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }

    authorize_url = (
        "https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize"
        f"?{urlencode(params)}"
    )
    return RedirectResponse(url=authorize_url, status_code=status.HTTP_303_SEE_OTHER)


@app.get("/admin/csp/provision")
async def admin_csp_provision(request: Request):
    """Provision the CSP/Lighthouse admin app registration automatically.

    Redirects the super-admin to Microsoft's OAuth consent page.  The admin
    must sign in with an account that has ``Application.ReadWrite.All`` and
    ``AppRoleAssignment.ReadWrite.All`` permissions in their partner tenant.
    After consent, the callback creates a dedicated app registration in the
    partner tenant and stores the resulting credentials in the ``m365-admin``
    integration module so that subsequent CSP sign-in can use them.

    When no bootstrap credentials are configured the flow uses PKCE with the
    well-known Azure CLI public client so that the admin can log in without
    needing to pre-create an Azure app registration.
    """
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    redirect_uri = str(request.url_for("m365_callback"))

    # Prefer existing admin credentials, then an explicitly configured
    # bootstrap client, and finally fall back to PKCE with the well-known
    # Azure CLI public client so no manual credential setup is required.
    existing_client_id, _ = await _get_m365_admin_credentials()
    bootstrap_client_id = str(settings.m365_bootstrap_client_id or "").strip()

    code_verifier: str | None = None
    pkce_handle: str | None = None
    if existing_client_id:
        oauth_client_id = existing_client_id
    elif bootstrap_client_id:
        oauth_client_id = bootstrap_client_id
    else:
        # No credentials configured – use PKCE with a public client.
        # Persist the code_verifier server-side and send only an opaque
        # one-time handle in state so the verifier is never exposed in URLs.
        # Use M365_PKCE_CLIENT_ID if configured; otherwise fall back to the
        # Azure CLI public client (which may be blocked in some tenants).
        code_verifier, code_challenge = m365_service.generate_pkce_pair()
        pkce_handle = await _store_pkce_verifier(code_verifier)
        oauth_client_id = m365_service.get_pkce_client_id()

    state_payload: dict = {
        "company_id": company_id,
        "user_id": current_user.get("id"),
        "flow": "discover",
        "return_to": "company_edit",
        "code_verifier": code_verifier,
    }
    if pkce_handle:
        state_payload["pkce_handle"] = pkce_handle

    state = oauth_state_serializer.dumps(state_payload)
    params: dict = {
        "client_id": oauth_client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": m365_service.DISCOVER_SCOPE,
        "state": state,
        "prompt": "select_account",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }

    authorize_url = (
        "https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize"
        f"?{urlencode(params)}"
    )
    return RedirectResponse(url=authorize_url, status_code=status.HTTP_303_SEE_OTHER)


async def _best_effort_sync_m365_email_domains(company_id: int) -> None:
    """Fire-and-forget helper that syncs M365 tenant domains to the company's email domains list.

    Failures are logged but never propagate so that the OAuth callback redirect
    is not affected.
    """
    try:
        result = await m365_service.sync_email_domains(company_id)
        log_info(
            "M365 email domain sync triggered from callback",
            company_id=company_id,
            added=result.get("added"),
        )
    except Exception as exc:  # noqa: BLE001
        log_warning(
            "M365 email domain sync failed (best-effort)",
            company_id=company_id,
            error=str(exc),
        )


@app.get("/m365/callback", name="m365_callback")
async def m365_callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
    if error:
        message = request.query_params.get("error_description", error)
        # Try to determine the flow from state so we can redirect to the
        # correct page and clear any stale PKCE client IDs. If state is
        # unparseable we fall back to /m365.
        error_redirect = "/m365"
        state_data: dict[str, Any] = {}
        if state:
            try:
                state_data = oauth_state_serializer.loads(state)
                if state_data.get("flow") == "m365_mail_auth":
                    error_redirect = "/admin/modules/m365-mail"
            except Exception:
                pass
        # AADSTS700016 means the PKCE app registration no longer exists in the
        # tenant (it was deleted). Clear the stale pkce_client_id (including
        # any company-specific value) so that the next sign-in attempt falls
        # back to the Azure CLI public client, and guide the admin to re-
        # provision so a fresh PKCE app is created.
        if "AADSTS700016" in message:
            company_id_raw = state_data.get("company_id")
            if company_id_raw is not None:
                try:
                    await m365_service.clear_company_pkce_client_id(int(company_id_raw))
                except (TypeError, ValueError):
                    log_warning(
                        "Skipping per-company PKCE clear; invalid company_id in state",
                        company_id_raw=company_id_raw,
                    )
                except Exception as exc:
                    log_warning(
                        "Failed to clear per-company PKCE client ID after AADSTS700016",
                        company_id_raw=company_id_raw,
                        error=str(exc),
                    )
            try:
                await m365_service.clear_pkce_client_id()
            except Exception as exc:
                log_warning(
                    "Failed to clear global PKCE client ID after AADSTS700016",
                    error=str(exc),
                )
            message = (
                "The PKCE app registration was not found in Azure AD (AADSTS700016). "
                "The cached app ID has been cleared. Please sign in again; if the problem "
                "persists, re-provision the M365 integration via Admin → M365."
            )
        encoded = urlencode({"error": message})
        return RedirectResponse(url=f"{error_redirect}?{encoded}", status_code=status.HTTP_303_SEE_OTHER)
    if not code or not state:
        return RedirectResponse(url="/m365?error=invalid+response", status_code=status.HTTP_303_SEE_OTHER)
    try:
        state_data = oauth_state_serializer.loads(state)
    except BadSignature:
        return RedirectResponse(url="/m365?error=invalid+state", status_code=status.HTTP_303_SEE_OTHER)
    company_id_raw = state_data.get("company_id")
    try:
        company_id = int(company_id_raw)
    except (TypeError, ValueError):
        company_id = 0
    flow = state_data.get("flow", "connect")

    if flow == "m365_mail_auth":
        from app.features.m365_mail.oauth import handle_m365_mail_auth_callback as _pack_handler

        return await _pack_handler(
            request,
            state_data=state_data,
            code=code,
            company_id=company_id,
        )

    if flow == "discover":
        # ── Tenant-discovery flow ──────────────────────────────────────────
        # Exchange the auth code to get a token, then extract the tid claim.
        return_to_company_edit = state_data.get("return_to") == "company_edit"
        redirect_uri = _build_m365_redirect_uri(request)

        def _discover_error(msg: str) -> RedirectResponse:
            if return_to_company_edit:
                return _company_edit_redirect(company_id=company_id, error=msg)
            encoded = urlencode({"error": msg})
            return RedirectResponse(
                url=f"/m365?{encoded}", status_code=status.HTTP_303_SEE_OTHER
            )

        _discover_cid, _discover_csec = await _get_m365_admin_credentials(company_id)

        # Determine the token exchange method.  When the flow was initiated
        # using PKCE (state contains a verifier handle), the exchange is done
        # with the configured PKCE public client – no client secret required.
        # Otherwise fall back to the traditional secret-based exchange using
        # existing admin credentials or the M365_BOOTSTRAP_* env vars.
        code_verifier: str | None = None
        pkce_handle = state_data.get("pkce_handle")
        if isinstance(pkce_handle, str) and pkce_handle:
            code_verifier = await _pop_pkce_verifier(pkce_handle)
            if not code_verifier:
                return _csp_provision_error(
                    "Provisioning session expired. Please restart the CSP provisioning flow."
                )
        elif state_verifier := state_data.get("code_verifier"):
            # code_verifier stored directly in the signed state by the discover flow.
            code_verifier = str(state_verifier)
        token_endpoint = (
            "https://login.microsoftonline.com/organizations/oauth2/v2.0/token"
        )
        if code_verifier:
            token_data: dict = {
                "client_id": await m365_service.get_effective_pkce_client_id_for_company(
                    company_id, redirect_uri=redirect_uri
                ),
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
                "scope": m365_service.DISCOVER_SCOPE,
            }
        else:
            # code_verifier is always included by the discover endpoints now.
            # This branch handles legacy state tokens that pre-date the PKCE-
            # always change.  Using admin credentials here risks AADSTS700025
            # if the configured client_id belongs to a public PKCE app, so we
            # only fall back when the credentials are actually present and
            # surface a clear error on failure.
            if not _discover_cid or not _discover_csec:
                return _discover_error(
                    "Sign-in session is incomplete. Please click 'Sign in as Global Admin' again."
                )
            token_data = {
                "client_id": _discover_cid,
                "client_secret": _discover_csec,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "scope": m365_service.DISCOVER_SCOPE,
            }
        async with httpx.AsyncClient(timeout=30) as client:
            token_response = await client.post(token_endpoint, data=token_data)
        if token_response.status_code != 200:
            log_error(
                "Microsoft 365 discover token exchange failed",
                status=token_response.status_code,
                body=token_response.text,
            )
            # AADSTS700025 means the client_id used is a public client but
            # client_secret was presented.  This happens when the configured
            # admin credentials reference a public PKCE app instead of a
            # confidential app.  Provide an actionable error message.
            error_body = token_response.text or ""
            if "AADSTS700025" in error_body:
                return _discover_error(
                    "Tenant discovery failed: the configured admin client is a "
                    "public app and cannot use a client secret (AADSTS700025). "
                    "Please click 'Sign in as Global Admin' to retry using PKCE."
                )
            return _discover_error("Sign-in failed during tenant discovery.")

        token_payload = token_response.json()
        # Prefer id_token (contains tid reliably); fall back to access_token
        id_token = token_payload.get("id_token") or token_payload.get("access_token", "")
        if not id_token:
            return _discover_error("No token received during tenant discovery.")

        try:
            discovered_tenant_id = m365_service.extract_tenant_id_from_token(id_token)
        except m365_service.M365Error as exc:
            log_error(
                "Failed to extract tenant ID from token",
                company_id=company_id,
                error=str(exc),
            )
            return _discover_error(f"Could not determine Tenant ID: {exc}")

        log_info(
            "Tenant ID discovered via Global Admin sign-in",
            company_id=company_id,
            tenant_id=discovered_tenant_id,
        )

        # Redirect to the provision flow using the discovered tenant ID
        if return_to_company_edit:
            return RedirectResponse(
                url=f"/admin/companies/{company_id}/m365-provision"
                f"?{urlencode({'tenant_id': discovered_tenant_id})}",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        return RedirectResponse(
            url=f"/m365/provision?{urlencode({'tenant_id': discovered_tenant_id})}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    if flow == "csp_admin_provision":
        # ── Auto-provision the CSP/Lighthouse admin app registration ──────
        # The admin signed in with PROVISION_SCOPE targeting /organizations,
        # so their token has Application.ReadWrite.All + AppRoleAssignment.ReadWrite.All
        # in their own partner tenant.  We use it to create a dedicated app
        # registration that will serve as the M365 admin OAuth client.
        redirect_uri = _build_m365_redirect_uri(request)

        def _csp_provision_error(msg: str) -> RedirectResponse:
            encoded = urlencode({"error": msg})
            return RedirectResponse(
                url=f"/admin/csp/customers?{encoded}", status_code=status.HTTP_303_SEE_OTHER
            )

        # Determine the token exchange method.  When the flow was initiated
        # using PKCE (state contains a code_verifier), the exchange is done
        # with the public Azure CLI client – no client secret is required.
        # Otherwise fall back to the traditional secret-based exchange using
        # existing admin credentials or the M365_BOOTSTRAP_* env vars.
        code_verifier: str | None = state_data.get("code_verifier")
        token_endpoint = (
            "https://login.microsoftonline.com/organizations/oauth2/v2.0/token"
        )
        if code_verifier:
            token_data: dict = {
                "client_id": m365_service.get_pkce_client_id(),
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
                "scope": m365_service.PROVISION_SCOPE,
            }
        else:
            bootstrap_client_id = str(settings.m365_bootstrap_client_id or "").strip()
            existing_client_id, existing_client_secret = await _get_m365_admin_credentials()
            oauth_client_id = existing_client_id or bootstrap_client_id
            bootstrap_client_secret = str(settings.m365_bootstrap_client_secret or "").strip()
            oauth_client_secret = existing_client_secret or bootstrap_client_secret

            if not oauth_client_id or not oauth_client_secret:
                return _csp_provision_error(
                    "No client credentials available to complete provisioning. "
                    "Configure M365_BOOTSTRAP_CLIENT_ID / M365_BOOTSTRAP_CLIENT_SECRET or "
                    "enter credentials in the M365 Admin module."
                )

            token_data = {
                "client_id": oauth_client_id,
                "client_secret": oauth_client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "scope": m365_service.PROVISION_SCOPE,
            }
        async with httpx.AsyncClient(timeout=30) as client:
            token_response = await client.post(token_endpoint, data=token_data)
        if token_response.status_code != 200:
            log_error(
                "CSP admin provision token exchange failed",
                status=token_response.status_code,
                body=token_response.text,
            )
            return _csp_provision_error("Authorization failed during CSP admin provisioning.")

        token_payload = token_response.json()
        access_token = token_payload.get("access_token", "")
        if not access_token:
            return _csp_provision_error("No access token received during CSP admin provisioning.")

        # Extract the partner tenant ID from the token
        try:
            partner_tenant_id = m365_service.extract_tenant_id_from_token(access_token)
        except m365_service.M365Error:
            try:
                id_token = token_payload.get("id_token", "")
                partner_tenant_id = m365_service.extract_tenant_id_from_token(id_token)
            except m365_service.M365Error:
                return _csp_provision_error(
                    "Unable to determine partner tenant ID from token."
                )

        try:
            provision_result = await m365_service.provision_csp_admin_app_registration(
                access_token=access_token,
                tenant_id=partner_tenant_id,
                display_name="MyPortal CSP Admin",
                redirect_uri=redirect_uri,
            )
        except m365_service.M365Error as exc:
            log_error(
                "CSP admin app provisioning failed",
                tenant_id=partner_tenant_id,
                error=str(exc),
            )
            return _csp_provision_error(f"Provisioning failed: {exc}")

        await m365_service.update_admin_m365_credentials(
            client_id=provision_result["client_id"],
            client_secret=provision_result["client_secret"],
            tenant_id=provision_result["tenant_id"],
            app_object_id=provision_result.get("app_object_id"),
            client_secret_key_id=provision_result.get("client_secret_key_id"),
            client_secret_expires_at=provision_result.get("client_secret_expires_at"),
        )
        log_info(
            "M365 CSP admin app provisioned and credentials stored",
            tenant_id=partner_tenant_id,
            client_id=provision_result["client_id"],
        )
        encoded = urlencode({"success": "Microsoft 365 CSP admin app provisioned successfully. You can now sign in with your CSP account."})
        return RedirectResponse(
            url=f"/admin/csp/customers?{encoded}", status_code=status.HTTP_303_SEE_OTHER
        )

    if flow == "provision":
        # ── Auto-provision flow ────────────────────────────────────────────
        tenant_id = str(state_data.get("tenant_id", "")).strip()
        return_to_company_edit = state_data.get("return_to") == "company_edit"
        redirect_uri = _build_m365_redirect_uri(request)

        def _provision_error(msg: str) -> RedirectResponse:
            if return_to_company_edit:
                return _company_edit_redirect(company_id=company_id, error=msg)
            encoded = urlencode({"error": msg})
            return RedirectResponse(
                url=f"/m365?{encoded}", status_code=status.HTTP_303_SEE_OTHER
            )

        if not tenant_id:
            return _provision_error("Missing tenant ID in provision state.")

        # Always use PKCE for the provision flow so the customer's Global Admin
        # can grant consent without requiring the CSP admin app to have a service
        # principal in the customer tenant (avoids AADSTS700016).
        verifier_id = state_data.get("verifier_id")
        code_verifier = await _pop_m365_provision_code_verifier(verifier_id)
        token_endpoint = (
            f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
        )
        if code_verifier:
            token_data = {
                "client_id": await m365_service.get_effective_pkce_client_id_for_company(
                    company_id, redirect_uri=redirect_uri
                ),
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
                "scope": m365_service.PROVISION_SCOPE,
            }
        else:
            # Backward-compatibility: fall back to admin credentials when no
            # verifier_id/code_verifier is present (e.g. old state tokens in flight).
            code_verifier = state_data.get("code_verifier")
            if code_verifier:
                token_data = {
                    "client_id": m365_service.get_pkce_client_id(),
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "code_verifier": code_verifier,
                    "scope": m365_service.PROVISION_SCOPE,
                }
            else:
                _provision_cid, _provision_csec = await _get_m365_admin_credentials()
                if not _provision_cid or not _provision_csec:
                    return _provision_error("Admin M365 credentials are not configured.")
                token_data = {
                    "client_id": _provision_cid,
                    "client_secret": _provision_csec,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "scope": m365_service.PROVISION_SCOPE,
                }
        async with httpx.AsyncClient(timeout=30) as client:
            token_response = await client.post(token_endpoint, data=token_data)
        if token_response.status_code != 200:
            log_error(
                "Microsoft 365 provision token exchange failed",
                status=token_response.status_code,
                body=token_response.text,
            )
            return _provision_error("Authorization failed during provision flow.")

        token_payload = token_response.json()
        access_token = token_payload.get("access_token", "")
        if not access_token:
            return _provision_error("No access token received during provision.")

        # Resolve tenant from the returned token when possible. This protects
        # against mismatches where a Global Admin signs into a different tenant
        # than the tenant_id carried in OAuth state, which can otherwise leave
        # credentials stored against the wrong tenant and cause Graph failures.
        try:
            token_tenant_id = m365_service.extract_tenant_id_from_token(access_token)
        except m365_service.M365Error:
            token_tenant_id = ""

        effective_tenant_id = token_tenant_id.strip() or tenant_id
        if token_tenant_id and token_tenant_id != tenant_id:
            log_info(
                "M365 provision callback tenant mismatch; using token tenant",
                company_id=company_id,
                requested_tenant_id=tenant_id,
                token_tenant_id=token_tenant_id,
            )

        # Load company name for a descriptive app display name
        company_record = await company_repo.get_company_by_id(company_id)
        company_name = (company_record.get("name") or "").strip() if company_record else ""
        display_name = f"MyPortal – {company_name}" if company_name else "MyPortal Integration"

        try:
            provision_result = await m365_service.provision_app_registration(
                access_token=access_token,
                display_name=display_name,
                redirect_uri=redirect_uri,
            )
        except m365_service.M365Error as exc:
            log_error(
                "M365 app provisioning failed",
                company_id=company_id,
                tenant_id=effective_tenant_id,
                error=str(exc),
            )
            return _provision_error(f"Provisioning failed: {exc}")

        await m365_service.upsert_credentials(
            company_id=company_id,
            tenant_id=effective_tenant_id,
            client_id=provision_result["client_id"],
            client_secret=provision_result["client_secret"],
            app_object_id=provision_result.get("app_object_id"),
            client_secret_key_id=provision_result.get("client_secret_key_id"),
            client_secret_expires_at=provision_result.get("client_secret_expires_at"),
        )
        log_info(
            "M365 enterprise app provisioned and credentials stored",
            company_id=company_id,
            tenant_id=effective_tenant_id,
            client_id=provision_result["client_id"],
        )

        # Best-effort: provision a dedicated PKCE public client for this company
        try:
            await m365_service.auto_provision_company_pkce_client_id(
                company_id,
                redirect_uri=redirect_uri,
                company_admin_creds={
                    "tenant_id": effective_tenant_id,
                    "client_id": provision_result["client_id"],
                    "client_secret": provision_result["client_secret"],
                    "app_object_id": provision_result.get("app_object_id"),
                    "client_secret_key_id": provision_result.get("client_secret_key_id"),
                    "client_secret_expires_at": provision_result.get(
                        "client_secret_expires_at"
                    ),
                },
            )
        except Exception as exc:  # pragma: no cover - best-effort helper
            log_warning(
                "Per-company PKCE auto-provision failed after M365 app provisioning",
                company_id=company_id,
                error=str(exc),
            )

        # Auto-create default sync tasks for the company if not already present.
        existing_commands = await scheduled_tasks_repo.get_commands_for_company(company_id)
        has_m365_sync_task = bool(
            {"sync_m365_data", "sync_o365", "sync_m365_licenses", "sync_m365_contacts", "sync_m365_mailboxes"}
            & existing_commands
        )
        sync_staff_task_name = (
            f"{company_name} - Sync staff directory"
            if company_name
            else "Sync staff directory"
        )
        # Create the three split M365 sync tasks if no M365 sync tasks exist yet
        if not has_m365_sync_task:
            for command, label_suffix in (
                ("sync_m365_licenses", "Sync Microsoft 365 licenses"),
                ("sync_m365_contacts", "Sync Microsoft 365 contacts"),
                ("sync_m365_mailboxes", "Sync Microsoft 365 mailboxes"),
            ):
                label = f"{company_name} - {label_suffix}" if company_name else label_suffix
                await scheduled_tasks_repo.create_task(
                    name=label,
                    command=command,
                    cron=_random_daily_cron(),
                    company_id=company_id,
                    active=True,
                )
                log_info(
                    "Auto-created scheduled task after M365 provisioning",
                    command=command,
                    company_id=company_id,
                )
        if "sync_staff" not in existing_commands:
            await scheduled_tasks_repo.create_task(
                name=sync_staff_task_name,
                command="sync_staff",
                cron=_random_daily_cron(),
                company_id=company_id,
                active=True,
            )
            log_info(
                "Auto-created scheduled task after M365 provisioning",
                command="sync_staff",
                company_id=company_id,
            )
        await scheduler_service.refresh()

        asyncio.create_task(
            _best_effort_sync_m365_email_domains(company_id),
            name=f"sync_m365_email_domains_{company_id}",
        )

        if return_to_company_edit:
            return _company_edit_redirect(
                company_id=company_id,
                success="Microsoft 365 enterprise app provisioned successfully.",
            )
        return RedirectResponse(url="/m365", status_code=status.HTTP_303_SEE_OTHER)

    # ── Standard connect/token-refresh flow ────────────────────────────────
    credentials = await m365_service.get_credentials(company_id)
    if not credentials:
        return RedirectResponse(url="/m365?error=missing+credentials", status_code=status.HTTP_303_SEE_OTHER)
    token_endpoint = f"https://login.microsoftonline.com/{credentials['tenant_id']}/oauth2/v2.0/token"
    redirect_uri = _build_m365_redirect_uri(request)
    data = {
        "client_id": credentials["client_id"],
        "client_secret": credentials.get("client_secret") or "",
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "scope": m365_service.CONNECT_SCOPE,
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
        access_token=None,
        token_expires_at=None,
    )
    # Best-effort: grant any newly-required app role assignments (e.g. the
    # permissions added for mailbox sync) using the admin's delegated token.
    # This ensures existing deployments pick up new permissions automatically
    # when an administrator re-runs "Authorize portal access".
    if access_token:
        new_permissions_granted = await m365_service.try_grant_missing_permissions(
            company_id=company_id,
            access_token=access_token,
        )
        if new_permissions_granted:
            log_info(
                "Granted missing M365 permissions via connect flow",
                company_id=company_id,
            )
    log_info("Microsoft 365 OAuth callback processed", company_id=company_id)
    asyncio.create_task(
        _best_effort_sync_m365_email_domains(company_id),
        name=f"sync_m365_email_domains_{company_id}",
    )
    if state_data.get("return_to") == "diagnostics":
        # Re-run the diagnostics check so the page shows fresh results after repair.
        try:
            await m365_service.check_enterprise_app_permissions(company_id)
        except m365_service.M365Error:
            pass
        return RedirectResponse(
            url="/m365/diagnostics?success=Permissions+repaired+and+re-checked",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(url="/m365", status_code=status.HTTP_303_SEE_OTHER)


async def shop_page(
    request: Request,
    category: str | None = Query(None),
    show_out_of_stock: bool = Query(False, alias="showOutOfStock"),
    q: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(24, alias="pageSize", ge=1, le=100),
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
    available_category_ids_task = asyncio.create_task(
        shop_repo.get_category_ids_with_available_products(
            company_id=company_id,
            include_out_of_stock=show_out_of_stock,
        )
    )

    products: list[dict[str, Any]]
    total_count = 0
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
        total_count = len(products)
        offset = (page - 1) * page_size
        products = products[offset: offset + page_size]
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
            in_stock_only=not show_out_of_stock,
            sort="name_asc",
        )

        products = await shop_repo.list_products_summary(filters)

        products = [
            product
            for product in products
            if not shop_service.is_price_below_dbp_threshold(product, is_vip=is_vip)
        ]

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
        total_count = len(products)
        offset = (page - 1) * page_size
        products = products[offset: offset + page_size]

    products = _strip_internal_shop_product_fields(products)
    products = cast(list[dict[str, Any]], _serialise_for_json(products))

    categories = await categories_task
    available_category_ids = await available_category_ids_task

    def _filter_categories(
        cats: list[dict[str, Any]], available_ids: set[int]
    ) -> list[dict[str, Any]]:
        result = []
        for cat in cats:
            filtered_children = _filter_categories(cat.get("children", []), available_ids)
            if cat["id"] in available_ids or filtered_children:
                result.append({**cat, "children": filtered_children})
        return result

    categories = _filter_categories(categories, available_category_ids)

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
        "total_count": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, math.ceil(total_count / page_size)) if page_size else 1,
    }
    return await _render_template("shop/index.html", request, user, extra=extra)


async def shop_product_detail_api(request: Request, product_id: int):
    (
        _user,
        _membership,
        company,
        company_id,
        redirect,
    ) = await _load_company_section_context(
        request,
        permission_field="can_access_shop",
    )
    if redirect:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    product = await shop_repo.get_product_by_id(product_id, company_id=company_id)
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    is_vip = bool(company and int(company.get("is_vip") or 0) == 1)
    product = _public_shop_product_payload(product, is_vip=is_vip)

    return JSONResponse(content=cast(dict[str, Any], _serialise_for_json(product)))


def _public_shop_product_payload(product: Mapping[str, Any], *, is_vip: bool) -> dict[str, Any]:
    payload = {
        "id": product.get("id"),
        "name": product.get("name"),
        "sku": product.get("sku"),
        "description": product.get("description"),
        "image_url": product.get("image_url"),
        "price": product.get("price"),
        "vip_price": product.get("vip_price"),
        "stock": product.get("stock"),
        "stock_nsw": product.get("stock_nsw"),
        "stock_qld": product.get("stock_qld"),
        "stock_vic": product.get("stock_vic"),
        "stock_sa": product.get("stock_sa"),
        "category_id": product.get("category_id"),
        "category_name": product.get("category_name"),
        "features": product.get("features") or [],
        "cross_sell_products": product.get("cross_sell_products") or [],
        "cross_sell_product_ids": product.get("cross_sell_product_ids") or [],
        "upsell_products": product.get("upsell_products") or [],
        "upsell_product_ids": product.get("upsell_product_ids") or [],
    }

    if is_vip and payload.get("vip_price") is not None:
        payload["price"] = payload["vip_price"]
    return payload




async def admin_shop_product_search_api(
    request: Request,
    q: str = Query("", min_length=1),
    limit: int = Query(10, ge=1, le=25),
):
    _current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    results = await shop_repo.search_products_for_admin_lookup(q, limit=limit)
    return JSONResponse(content=cast(list[dict[str, Any]], _serialise_for_json(results)))


async def admin_shop_product_restrictions_api(request: Request, product_id: int):
    _current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    restrictions = await shop_repo.list_product_restrictions_for_product(product_id)
    return JSONResponse(content=cast(list[dict[str, Any]], _serialise_for_json(restrictions)))


async def admin_shop_product_detail_api(request: Request, product_id: int):
    _current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    product = await shop_repo.get_product_by_id(product_id, include_archived=True)
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    return JSONResponse(content=cast(dict[str, Any], _serialise_for_json(product)))


async def admin_shop_product_price_history_api(request: Request, product_id: int):
    _current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    product = await shop_repo.get_product_by_id(product_id, include_archived=True)
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    vendor_sku = str(product.get("vendor_sku") or "").strip()
    if not vendor_sku:
        return JSONResponse(content=[])

    history = await stock_feed_repo.get_price_history(vendor_sku)
    return JSONResponse(content=cast(list[dict[str, Any]], _serialise_for_json(history)))


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
@app.get("/search/", response_class=HTMLResponse)
async def search_by_phone_number(request: Request):
    """
    Search for tickets by requester's phone number.
    Example: /search/?phoneNumber=%2B61412345678
    """
    user, redirect = await _require_authenticated_user(request)
    if redirect:
        return redirect
    
    # Get phone number from query parameter
    phone_number = request.query_params.get("phoneNumber", "").strip()
    
    if not phone_number:
        # No phone number provided, redirect to tickets page
        return RedirectResponse(url="/tickets", status_code=status.HTTP_303_SEE_OTHER)
    
    # Search for tickets by requester's phone number within the authenticated user's scope
    available_companies = await company_access.list_accessible_companies(user)
    available_company_ids: list[int] = []
    for entry in available_companies:
        company_id = entry.get("company_id")
        try:
            available_company_ids.append(int(company_id))
        except (TypeError, ValueError):
            continue

    active_company_id = getattr(request.state, "active_company_id", None)
    active_company_ids: list[int] = []
    if active_company_id is not None:
        try:
            active_company_ids = [int(active_company_id)]
        except (TypeError, ValueError):
            active_company_ids = []
    if not active_company_ids:
        active_company_ids = available_company_ids

    # Search for tickets by requester's phone number
    try:
        tickets = await tickets_repo.list_tickets_by_requester_phone(
            phone_number,
            limit=_PHONE_SEARCH_LIMIT,
            user_id=user.get("id"),
            company_ids=active_company_ids or None,
        )
    except Exception as exc:
        log_error(
            "Error searching tickets by phone number",
            exc=exc,
            event="tickets.phone_search_failed",
            request_id=_get_request_id(request),
            path=request.url.path,
            user_id=user.get("id"),
        )
        # On error, redirect to tickets page with error message
        error_msg = quote("Failed to search tickets by phone number")
        return RedirectResponse(
            url=f"/tickets?error={error_msg}",
            status_code=status.HTTP_303_SEE_OTHER
        )
    
    if not tickets:
        # No tickets found, redirect to tickets page with a message
        error_msg = quote(f"No tickets found for phone number {phone_number}")
        return RedirectResponse(
            url=f"/tickets?error={error_msg}",
            status_code=status.HTTP_303_SEE_OTHER
        )
    
    if len(tickets) == 1:
        # Exactly one ticket found, redirect directly to it
        ticket_id = tickets[0].get("id")
        if ticket_id is None:
            # Handle case where ticket doesn't have an ID (defensive)
            error_msg = quote("Invalid ticket data received")
            return RedirectResponse(
                url=f"/tickets?error={error_msg}",
                status_code=status.HTTP_303_SEE_OTHER
            )
        return RedirectResponse(
            url=f"/tickets/{ticket_id}",
            status_code=status.HTTP_303_SEE_OTHER
        )
    
    # Multiple tickets found, redirect to tickets page with search
    # We'll use the phone number as a search term
    search_param = quote(phone_number)
    return RedirectResponse(
        url=f"/tickets?q={search_param}",
        status_code=status.HTTP_303_SEE_OTHER
    )


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




@app.head("/admin/service-status", response_class=HTMLResponse)
@app.get("/admin/service-status", response_class=HTMLResponse)
async def admin_service_status_page(request: Request):
    user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    services = await service_status_service.list_services(include_inactive=True)
    summary = service_status_service.summarise_services(services)
    companies = await company_repo.list_companies()
    service_id_param = request.query_params.get("serviceId")
    editing_service = None
    if service_id_param:
        try:
            editing_service = await service_status_service.get_service(int(service_id_param))
        except (TypeError, ValueError):
            editing_service = None
    status_lookup = {entry["value"]: entry for entry in service_status_service.STATUS_DEFINITIONS}
    company_lookup = {
        int(company["id"]): company.get("name")
        for company in companies
        if company.get("id") is not None
    }
    return await _render_template(
        "admin/service_status.html",
        request,
        user,
        extra={
            "title": "Service status",
            "service_status_entries": services,
            "service_status_summary": summary,
            "service_status_definitions": service_status_service.STATUS_DEFINITIONS,
            "service_status_lookup": status_lookup,
            "company_options": companies,
            "service_status_company_lookup": company_lookup,
            "service_status_editing": editing_service,
            "service_status_default": service_status_service.DEFAULT_STATUS,
        },
    )


def _extract_service_status_form(form: FormData) -> tuple[dict[str, Any], list[int]]:
    payload = {
        "name": form.get("name"),
        "description": form.get("description"),
        "status": form.get("status") or service_status_service.DEFAULT_STATUS,
        "status_message": form.get("status_message"),
        "display_order": form.get("display_order"),
        "is_active": bool(form.get("is_active")),
        # AI lookup fields
        "ai_lookup_enabled": bool(form.get("ai_lookup_enabled")),
        "ai_lookup_url": form.get("ai_lookup_url"),
        "ai_lookup_prompt": form.get("ai_lookup_prompt"),
        "ai_lookup_model_override": form.get("ai_lookup_model_override"),
        "ai_lookup_frequency_operational": form.get("ai_lookup_frequency_operational"),
        "ai_lookup_frequency_degraded": form.get("ai_lookup_frequency_degraded"),
        "ai_lookup_frequency_partial_outage": form.get("ai_lookup_frequency_partial_outage"),
        "ai_lookup_frequency_outage": form.get("ai_lookup_frequency_outage"),
        "ai_lookup_frequency_maintenance": form.get("ai_lookup_frequency_maintenance"),
    }
    # Handle tags - can be comma-separated string
    tags_input = form.get("tags")
    if tags_input:
        payload["tags"] = tags_input
    company_ids = form.getlist("companyIds") if hasattr(form, "getlist") else []
    return payload, company_ids


@app.post("/admin/service-status", response_class=HTMLResponse)
async def admin_create_service_status(request: Request):
    user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    payload, company_ids = _extract_service_status_form(form)
    try:
        await service_status_service.create_service(
            payload,
            company_ids=company_ids,
            updated_by=int(user.get("id")) if user.get("id") else None,
        )
    except ValueError as exc:
        url = f"/admin/service-status?error={quote(str(exc))}"
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse(
        url=f"/admin/service-status?success={quote('Service created.')}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/service-status/{service_id}", response_class=HTMLResponse)
async def admin_update_service_status(request: Request, service_id: int):
    user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    payload, company_ids = _extract_service_status_form(form)
    try:
        updated = await service_status_service.update_service(
            service_id,
            payload,
            company_ids=company_ids,
            updated_by=int(user.get("id")) if user.get("id") else None,
        )
    except ValueError as exc:
        url = f"/admin/service-status?serviceId={service_id}&error={quote(str(exc))}"
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)
    if not updated:
        url = f"/admin/service-status?error={quote('Service not found.')}"
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse(
        url=f"/admin/service-status?success={quote('Service updated.')}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/service-status/{service_id}/delete", response_class=HTMLResponse)
async def admin_delete_service_status(request: Request, service_id: int):
    user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    try:
        await service_status_service.delete_service(service_id)
    except Exception as exc:  # pragma: no cover - defensive
        url = f"/admin/service-status?error={quote(str(exc))}"
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse(
        url=f"/admin/service-status?success={quote('Service deleted.')}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/service-status/{service_id}/refresh-tags", response_class=HTMLResponse)
async def admin_refresh_service_tags(request: Request, service_id: int):
    user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    try:
        await service_status_service.refresh_service_tags(service_id)
    except ValueError as exc:
        url = f"/admin/service-status?serviceId={service_id}&error={quote(str(exc))}"
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)
    except Exception as exc:  # pragma: no cover - defensive
        url = f"/admin/service-status?serviceId={service_id}&error={quote('Failed to refresh tags.')}"
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse(
        url=f"/admin/service-status?serviceId={service_id}&success={quote('Tags refreshed.')}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def _admin_service_status_check_now_handler(request: Request, service_id: int):
    user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    try:
        result = await service_status_service.run_ai_lookup_for_service(service_id)
    except Exception as exc:
        log_error(
            "Service status AI lookup raised an unexpected error",
            service_id=service_id,
            error=str(exc),
        )
        url = f"/admin/service-status?serviceId={service_id}&error={quote('AI lookup failed unexpectedly. Check server logs for details.')}"
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)
    if result.get("error"):
        url = f"/admin/service-status?serviceId={service_id}&error={quote(result['error'])}"
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)
    msg = "AI lookup completed."
    if result.get("changed"):
        msg = "AI lookup completed — status updated."
    return RedirectResponse(
        url=f"/admin/service-status?serviceId={service_id}&success={quote(msg)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/service-status/{service_id}/check-now", response_class=HTMLResponse)
@app.post("/admin/service-status/{service_id}/check_now", response_class=HTMLResponse)
@app.post("/admin/service-status/check-now/{service_id}", response_class=HTMLResponse)
async def admin_service_status_check_now(request: Request, service_id: int):
    """
    Trigger an immediate AI lookup for a service.

    Multiple route aliases are kept for backward compatibility with earlier UI
    paths and bookmarked/admin-proxied URLs.
    """
    return await _admin_service_status_check_now_handler(request, service_id)


# ---------------------------------------------------------------------------
# Backup History admin pages
# ---------------------------------------------------------------------------


def _backup_status_webhook_url(request: Request) -> str:
    if settings.portal_url:
        base = str(settings.portal_url).rstrip("/")
    else:
        scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
        base = f"{scheme}://{request.url.netloc}"
    return f"{base}/api/backup-status"


async def admin_backup_jobs_page(request: Request):
    user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    company_filter_raw = (request.query_params.get("company_id") or "").strip()
    status_filter = (request.query_params.get("status_filter") or "").strip().lower()
    company_filter: int | None = None
    if company_filter_raw:
        try:
            company_filter = int(company_filter_raw)
        except ValueError:
            company_filter = None

    jobs = await backup_jobs_service.list_jobs_with_latest(
        company_id=company_filter, include_inactive=True
    )
    if status_filter and status_filter in backup_jobs_service.KNOWN_STATUSES:
        jobs = [job for job in jobs if job.get("today_status") == status_filter]

    summary = backup_jobs_service.summarise_jobs(jobs)
    companies = await company_repo.list_companies()
    company_lookup = {
        int(company["id"]): company.get("name")
        for company in companies
        if company.get("id") is not None
    }

    job_id_param = request.query_params.get("jobId")
    editing_job: dict[str, Any] | None = None
    if job_id_param:
        try:
            editing_job = await backup_jobs_service.get_job(int(job_id_param))
        except (TypeError, ValueError):
            editing_job = None

    extra = {
        "title": "Backup history",
        "backup_jobs": jobs,
        "backup_jobs_summary": summary,
        "backup_status_definitions": backup_jobs_service.STATUS_DEFINITIONS,
        "backup_status_default": backup_jobs_service.DEFAULT_STATUS,
        "backup_companies": companies,
        "backup_company_lookup": company_lookup,
        "backup_company_filter": company_filter,
        "backup_status_filter": status_filter,
        "backup_editing_job": editing_job,
        "backup_status_url": _backup_status_webhook_url(request),
    }
    return await _render_template("admin/backup_jobs.html", request, user, extra=extra)


async def admin_backup_summary_page(request: Request):
    user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    company_filter_raw = (request.query_params.get("company_id") or "").strip()
    status_filter = (request.query_params.get("status_filter") or "").strip().lower()
    company_filter: int | None = None
    if company_filter_raw:
        try:
            company_filter = int(company_filter_raw)
        except ValueError:
            company_filter = None

    jobs = await backup_jobs_service.list_jobs_with_latest(
        company_id=company_filter, include_inactive=True
    )
    if status_filter and status_filter in backup_jobs_service.KNOWN_STATUSES:
        jobs = [job for job in jobs if job.get("today_status") == status_filter]

    summary = backup_jobs_service.summarise_jobs(jobs)
    companies = await company_repo.list_companies()
    company_lookup = {
        int(company["id"]): company.get("name")
        for company in companies
        if company.get("id") is not None
    }

    history = await backup_jobs_service.build_history_grid(
        company_id=company_filter, days=14, include_inactive=True
    )

    extra = {
        "title": "Backup Summary",
        "backup_jobs": jobs,
        "backup_jobs_summary": summary,
        "backup_status_definitions": backup_jobs_service.STATUS_DEFINITIONS,
        "backup_status_default": backup_jobs_service.DEFAULT_STATUS,
        "backup_companies": companies,
        "backup_company_lookup": company_lookup,
        "backup_company_filter": company_filter,
        "backup_status_filter": status_filter,
        "backup_history": history,
    }
    return await _render_template("admin/backup_summary.html", request, user, extra=extra)


def _extract_backup_job_form(form: FormData) -> dict[str, Any]:
    company_id_raw = (form.get("company_id") or "").strip()
    try:
        company_id = int(company_id_raw)
    except (TypeError, ValueError):
        company_id = 0

    def _parse_alert_days(key: str) -> int | None:
        raw = (form.get(key) or "").strip()
        if not raw:
            return None
        try:
            val = int(raw)
            return val if val > 0 else None
        except (TypeError, ValueError):
            return None

    return {
        "company_id": company_id,
        "name": (form.get("name") or "").strip(),
        "description": (form.get("description") or "").strip() or None,
        "is_active": form.get("is_active") in {"on", "true", "1", "yes"},
        "pass_protection": form.get("pass_protection") in {"on", "true", "1", "yes"},
        "alert_no_success_days": _parse_alert_days("alert_no_success_days"),
        "alert_fail_days": _parse_alert_days("alert_fail_days"),
        "alert_unknown_days": _parse_alert_days("alert_unknown_days"),
    }


async def admin_create_backup_job(request: Request):
    user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    payload = _extract_backup_job_form(form)
    try:
        job = await backup_jobs_service.create_job(
            company_id=payload["company_id"],
            name=payload["name"],
            description=payload["description"],
            is_active=payload["is_active"],
            created_by=int(user.get("id")) if user.get("id") else None,
            alert_no_success_days=payload["alert_no_success_days"],
            alert_fail_days=payload["alert_fail_days"],
            alert_unknown_days=payload["alert_unknown_days"],
            pass_protection=payload["pass_protection"],
        )
    except ValueError as exc:
        url = f"/admin/backup-jobs?error={quote(str(exc))}"
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)
    await audit_service.log_action(
        action="backup_job.create",
        user_id=user.get("id"),
        entity_type="backup_job",
        entity_id=job["id"],
        metadata={"company_id": job["company_id"], "name": job["name"]},
        request=request,
    )
    return RedirectResponse(
        url=f"/admin/backup-jobs?success={quote('Backup job created.')}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def admin_update_backup_job(request: Request, job_id: int):
    user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    payload = _extract_backup_job_form(form)
    try:
        updated = await backup_jobs_service.update_job(
            job_id,
            company_id=payload["company_id"] or None,
            name=payload["name"],
            description=payload["description"],
            is_active=payload["is_active"],
            alert_no_success_days=payload["alert_no_success_days"],
            alert_fail_days=payload["alert_fail_days"],
            alert_unknown_days=payload["alert_unknown_days"],
            clear_alert_no_success_days=payload["alert_no_success_days"] is None,
            clear_alert_fail_days=payload["alert_fail_days"] is None,
            clear_alert_unknown_days=payload["alert_unknown_days"] is None,
            pass_protection=payload["pass_protection"],
        )
    except ValueError as exc:
        url = f"/admin/backup-jobs?jobId={int(job_id)}&error={quote(str(exc))}"
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)
    if not updated:
        url = f"/admin/backup-jobs?error={quote('Backup job not found.')}"
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)
    await audit_service.log_action(
        action="backup_job.update",
        user_id=user.get("id"),
        entity_type="backup_job",
        entity_id=job_id,
        metadata={
            "company_id": updated["company_id"],
            "name": updated["name"],
            "is_active": updated["is_active"],
        },
        request=request,
    )
    return RedirectResponse(
        url=f"/admin/backup-jobs?success={quote('Backup job updated.')}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def admin_delete_backup_job(request: Request, job_id: int):
    user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    try:
        await backup_jobs_service.delete_job(job_id)
    except Exception as exc:  # pragma: no cover - defensive
        url = f"/admin/backup-jobs?error={quote(str(exc))}"
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)
    await audit_service.log_action(
        action="backup_job.delete",
        user_id=user.get("id"),
        entity_type="backup_job",
        entity_id=job_id,
        request=request,
    )
    return RedirectResponse(
        url=f"/admin/backup-jobs?success={quote('Backup job deleted.')}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def admin_regenerate_backup_job_token(request: Request, job_id: int):
    user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    updated = await backup_jobs_service.regenerate_token(job_id)
    if not updated:
        url = f"/admin/backup-jobs?error={quote('Backup job not found.')}"
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)
    await audit_service.log_action(
        action="backup_job.regenerate_token",
        user_id=user.get("id"),
        entity_type="backup_job",
        entity_id=job_id,
        request=request,
    )
    return RedirectResponse(
        url=f"/admin/backup-jobs?jobId={int(job_id)}&success={quote('Token regenerated.')}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


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
    return templates.TemplateResponse(context["request"], "admin/profile.html", context)


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


async def admin_company_edit_page(
    company_id: int,
    request: Request,
    success: str | None = Query(default=None),
    error: str | None = Query(default=None),
    show_inactive: bool = Query(default=False),
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
        show_inactive_tasks=show_inactive,
    )


async def admin_create_company(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    name = str(form.get("name", "")).strip()
    syncro_company_id = (str(form.get("syncroCompanyId", "")).strip() or None)
    tactical_client_id = (str(form.get("tacticalClientId", "")).strip() or None)
    xero_id = (str(form.get("xeroId", "")).strip() or None)
    hudu_id = (str(form.get("huduId", "")).strip() or None)
    huntress_organization_id = (str(form.get("huntressOrganizationId", "")).strip() or None)
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
    if hudu_id:
        payload["hudu_id"] = hudu_id
    if huntress_organization_id:
        payload["huntress_organization_id"] = huntress_organization_id
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
                can_access_quotes=permission_values.get("can_access_quotes", False),
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


async def admin_update_company(company_id: int, request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    name = str(form.get("name", "")).strip()
    syncro_company_raw = str(form.get("syncroCompanyId", "")).strip()
    tactical_client_raw = str(form.get("tacticalClientId", "")).strip()
    xero_id_raw = str(form.get("xeroId", "")).strip()
    hudu_id_raw = str(form.get("huduId", "")).strip()
    huntress_organization_id_raw = str(form.get("huntressOrganizationId", "")).strip()
    trello_board_id_raw = str(form.get("trelloBoardId", "")).strip()
    trello_api_key_raw = str(form.get("trelloApiKey", "")).strip()
    trello_token_raw = str(form.get("trelloToken", "")).strip()
    is_vip = _parse_bool(form.get("isVip"))
    invoice_prepay_enabled = bool(form.get("invoicePrepay"))
    invoice_postpay_enabled = bool(form.get("invoicePostpay"))
    stripe_enabled = bool(form.get("stripeEnabled"))
    require_po = bool(form.get("requirePo"))
    offboarding_email_forwarding_enabled = bool(form.get("offboardingEmailForwardingEnabled"))
    _selected_methods = [
        m for m, enabled in [
            ("invoice_prepay", invoice_prepay_enabled),
            ("invoice_postpay", invoice_postpay_enabled),
            ("stripe", stripe_enabled),
        ] if enabled
    ]
    payment_method = ",".join(_selected_methods) if _selected_methods else "invoice_prepay"
    raw_email_domains = form.get("emailDomains")
    email_domains_text = str(raw_email_domains) if raw_email_domains is not None else ""
    existing = await company_repo.get_company_by_id(company_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    form_values = {
        "name": name,
        "syncro_company_id": syncro_company_raw,
        "tacticalrmm_client_id": tactical_client_raw,
        "xero_id": xero_id_raw,
        "hudu_id": hudu_id_raw,
        "huntress_organization_id": huntress_organization_id_raw,
        "trello_board_id": trello_board_id_raw,
        "trello_api_key": trello_api_key_raw or existing.get("trello_api_key"),
        "trello_token": trello_token_raw or existing.get("trello_token"),
        "email_domains": email_domains_text,
        "is_vip": is_vip,
        "payment_method": payment_method,
        "require_po": require_po,
        "offboarding_email_forwarding_enabled": offboarding_email_forwarding_enabled,
    }
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
    hudu_id = hudu_id_raw or None
    huntress_organization_id = huntress_organization_id_raw or None
    trello_board_id = trello_board_id_raw or None
    # Only update Trello credentials when a new value was submitted; blank means keep existing.
    trello_api_key: str | None = trello_api_key_raw if trello_api_key_raw else (existing.get("trello_api_key") or None)
    trello_token: str | None = trello_token_raw if trello_token_raw else (existing.get("trello_token") or None)
    updates: dict[str, Any] = {
        "name": name,
        "is_vip": 1 if is_vip else 0,
        "syncro_company_id": syncro_company_id,
        "tacticalrmm_client_id": tactical_client_id,
        "xero_id": xero_id,
        "hudu_id": hudu_id,
        "huntress_organization_id": huntress_organization_id,
        "trello_board_id": trello_board_id,
        "trello_api_key": trello_api_key,
        "trello_token": trello_token,
        "email_domains": email_domains,
        "payment_method": payment_method,
        "require_po": 1 if require_po else 0,
        "offboarding_email_forwarding_enabled": 1 if offboarding_email_forwarding_enabled else 0,
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
    if huntress_organization_id:
        existing_commands = await scheduled_tasks_repo.get_commands_for_company(company_id)
        if "sync_huntress" not in existing_commands:
            huntress_task_name = (
                f"{name} - Sync Huntress data" if name else "Sync Huntress data"
            )
            await scheduled_tasks_repo.create_task(
                name=huntress_task_name,
                command="sync_huntress",
                cron=_random_daily_cron(),
                company_id=company_id,
                active=True,
            )
            log_info(
                "Auto-created scheduled task after Huntress organization ID was set",
                command="sync_huntress",
                company_id=company_id,
            )
            asyncio.create_task(scheduler_service.refresh())
    return _company_edit_redirect(
        company_id=company_id,
        success=f"Company {name} updated.",
    )


async def admin_company_shop_items_api(request: Request, company_id: int):
    """Return all non-archived shop products with their hidden status for a company."""
    _current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    company = await company_repo.get_company_by_id(company_id)
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")

    products = await shop_repo.list_products_with_exclusion_status_for_company(company_id)
    return JSONResponse(content=cast(list[dict[str, Any]], _serialise_for_json(products)))


async def admin_update_company_shop_visibility(
    request: Request,
    company_id: int,
    hidden: list[str] = Form(default=[]),
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    company = await company_repo.get_company_by_id(company_id)
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")

    hidden_product_ids: set[int] = set()
    for value in hidden:
        if value in (None, ""):
            continue
        try:
            hidden_product_ids.add(int(value))
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid product ID: {value!r}",
            )

    await shop_repo.replace_company_exclusions(company_id, hidden_product_ids)

    log_info(
        "Company shop visibility updated",
        company_id=company_id,
        hidden_product_count=len(hidden_product_ids),
        updated_by=current_user["id"] if current_user else None,
    )
    await audit_service.record(
        action="shop.company.visibility_change",
        request=request,
        entity_type="company",
        entity_id=company_id,
        after={"hidden_product_ids": sorted(hidden_product_ids)},
    )
    return _company_edit_redirect(
        company_id=company_id,
        success="Shop item visibility saved.",
    )


async def admin_update_company_staff_fields(company_id: int, request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    existing = await company_repo.get_company_by_id(company_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    form = await request.form()
    form_data = {str(key): form.get(key) for key in form.keys()}
    await staff_field_config_service.save_company_staff_field_admin_config(
        company_id, form_data
    )
    return _company_edit_redirect(
        company_id=company_id,
        success="Staff intake field configuration updated.",
    )


def _parse_custom_field_options(options_text: str) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    for part in (options_text or "").split(","):
        item = part.strip()
        if not item:
            continue
        if ":" in item:
            value_part, label_part = item.split(":", 1)
            value = value_part.strip()
            label = label_part.strip() or value
        else:
            value = item
            label = item
        if not value:
            continue
        options.append({"value": value, "label": label})
    return options


def _parse_staff_custom_field_condition(
    *,
    parent_name_value: str,
    operator_value: str,
    condition_value: str,
) -> tuple[str | None, str | None, str | None]:
    parent_name = str(parent_name_value or "").strip().lower().replace(" ", "_")
    operator = str(operator_value or "").strip().lower()
    normalized_condition_value = str(condition_value or "").strip()
    if not parent_name:
        return None, None, None
    if operator not in {"equals", "not_equals", "one_of", "is_checked", "is_not_checked", "select_map"}:
        operator = "equals"
    if operator == "select_map":
        if normalized_condition_value.startswith("{"):
            try:
                parsed_map = json.loads(normalized_condition_value)
            except (TypeError, ValueError):
                return parent_name, operator, normalized_condition_value or None
            if isinstance(parsed_map, dict):
                return parent_name, operator, json.dumps(parsed_map, separators=(",", ":"))
        return parent_name, operator, normalized_condition_value or None
    if operator in {"is_checked", "is_not_checked"}:
        normalized_condition_value = None
    if operator in {"equals", "not_equals"} and not normalized_condition_value:
        # Treat empty equals/not-equals conditions as checkbox-style toggles so
        # parent-linked fields still behave predictably when no value is provided.
        fallback_operator = "is_checked" if operator == "equals" else "is_not_checked"
        return parent_name, fallback_operator, None
    return parent_name, operator, normalized_condition_value or None


async def admin_create_company_staff_custom_field(company_id: int, request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    existing = await company_repo.get_company_by_id(company_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    form = await request.form()
    name = str(form.get("name") or "").strip().lower().replace(" ", "_")
    display_name = str(form.get("display_name") or "").strip() or None
    help_text = str(form.get("help_text") or "").strip() or None
    field_type = str(form.get("field_type") or "text").strip().lower()
    field_group = str(form.get("field_group") or "").strip() or None
    try:
        display_order = int(str(form.get("display_order") or "0").strip())
    except ValueError:
        display_order = 0
    condition_parent_name, condition_operator, condition_value = _parse_staff_custom_field_condition(
        parent_name_value=str(form.get("condition_parent_name") or ""),
        operator_value=str(form.get("condition_operator") or ""),
        condition_value=str(form.get("condition_value") or ""),
    )
    options = _parse_custom_field_options(str(form.get("options") or ""))
    if not name:
        return _company_edit_redirect(company_id=company_id, error="Custom field name is required.")
    if field_type not in {"text", "checkbox", "date", "select", "multiselect"}:
        return _company_edit_redirect(company_id=company_id, error="Invalid custom field type.")
    await staff_custom_fields_repo.create_company_definition(
        company_id=company_id,
        name=name,
        display_name=display_name,
        help_text=help_text,
        field_type=field_type,
        field_group=field_group,
        display_order=display_order,
        condition_parent_name=condition_parent_name,
        condition_operator=condition_operator,
        condition_value=condition_value,
        options=options,
    )
    return _company_edit_redirect(company_id=company_id, success="Staff custom field created.")


async def admin_update_company_staff_custom_field(
    company_id: int, definition_id: int, request: Request
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    existing = await company_repo.get_company_by_id(company_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    form = await request.form()
    display_name = str(form.get("display_name") or "").strip() or None
    help_text = str(form.get("help_text") or "").strip() or None
    field_type = str(form.get("field_type") or "text").strip().lower()
    field_group = str(form.get("field_group") or "").strip() or None
    try:
        display_order = int(str(form.get("display_order") or "0").strip())
    except ValueError:
        display_order = 0
    is_active = str(form.get("is_active") or "").lower() in {"1", "true", "on", "yes"}
    condition_parent_name, condition_operator, condition_value = _parse_staff_custom_field_condition(
        parent_name_value=str(form.get("condition_parent_name") or ""),
        operator_value=str(form.get("condition_operator") or ""),
        condition_value=str(form.get("condition_value") or ""),
    )
    options = _parse_custom_field_options(str(form.get("options") or ""))
    await staff_custom_fields_repo.update_company_definition(
        definition_id,
        company_id=company_id,
        display_name=display_name,
        help_text=help_text,
        field_type=field_type,
        field_group=field_group,
        display_order=display_order,
        is_active=is_active,
        condition_parent_name=condition_parent_name,
        condition_operator=condition_operator,
        condition_value=condition_value,
        options=options,
    )
    return _company_edit_redirect(company_id=company_id, success="Staff custom field updated.")


async def admin_delete_company_staff_custom_field(
    company_id: int, definition_id: int, request: Request
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    await staff_custom_fields_repo.delete_company_definition(definition_id, company_id)
    return _company_edit_redirect(company_id=company_id, success="Staff custom field deleted.")


async def admin_save_company_m365_credentials(company_id: int, request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    existing_company = await company_repo.get_company_by_id(company_id)
    if not existing_company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    form = await request.form()
    tenant_id = str(form.get("tenantId", "")).strip()
    client_id = str(form.get("clientId", "")).strip()
    client_secret = str(form.get("clientSecret", "")).strip()
    if not tenant_id or not client_id:
        return _company_edit_redirect(
            company_id=company_id,
            error="Tenant ID and Client ID are required.",
        )
    existing_creds = await m365_repo.get_credentials(company_id)
    if not client_secret and not existing_creds:
        return _company_edit_redirect(
            company_id=company_id,
            error="Client secret is required when adding Microsoft 365 credentials for the first time.",
        )
    if client_secret:
        await m365_service.upsert_credentials(
            company_id=company_id,
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
        )
    else:
        existing_secret = existing_creds.get("client_secret")
        if not existing_secret:
            return _company_edit_redirect(
                company_id=company_id,
                error="Existing client secret is missing. Please provide a new client secret.",
            )
        await m365_repo.upsert_credentials(
            company_id=company_id,
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=existing_secret,
            refresh_token=existing_creds.get("refresh_token"),
            access_token=existing_creds.get("access_token"),
            token_expires_at=existing_creds.get("token_expires_at"),
        )
    log_info(
        "Microsoft 365 credentials updated via admin company edit",
        company_id=company_id,
        user_id=current_user.get("id"),
    )
    return _company_edit_redirect(
        company_id=company_id,
        success="Microsoft 365 credentials saved.",
    )


async def admin_delete_company_m365_credentials(company_id: int, request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    existing_company = await company_repo.get_company_by_id(company_id)
    if not existing_company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    await m365_service.delete_credentials(company_id)
    log_info(
        "Microsoft 365 credentials deleted via admin company edit",
        company_id=company_id,
        user_id=current_user.get("id"),
    )
    return _company_edit_redirect(
        company_id=company_id,
        success="Microsoft 365 credentials removed.",
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


async def admin_remove_company_assignment(company_id: int, user_id: int, request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    await user_company_repo.remove_assignment(user_id=user_id, company_id=company_id)
    return JSONResponse({"success": True})


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


async def admin_remove_billing_contact(company_id: int, staff_id: int, request: Request):
    """Remove a staff member as a billing contact for a company."""
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    
    await billing_contacts_repo.remove_billing_contact(company_id, staff_id)
    return JSONResponse({"success": True})


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
async def admin_automation(request: Request, show_inactive: bool = Query(default=False)):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    tasks = await scheduled_tasks_repo.list_tasks(include_inactive=show_inactive)
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
    # Build the set of commands that belong to disabled modules so they can be excluded.
    try:
        all_modules = await modules_service.list_modules()
        disabled_module_slugs = {m["slug"] for m in all_modules if not m.get("enabled")}
    except Exception:  # pragma: no cover - defensive fallback
        disabled_module_slugs = set()
    disabled_commands_global: set[str] = set()
    for mod_slug, cmds in COMMANDS_BY_MODULE.items():
        if mod_slug in disabled_module_slugs:
            disabled_commands_global.update(cmds)

    command_options = [
        {"value": "sync_staff", "label": "Sync staff directory"},
        {"value": "sync_m365_data", "label": "Sync Microsoft 365 data (legacy)"},
        {"value": "sync_m365_licenses", "label": "Sync Microsoft 365 licenses"},
        {"value": "sync_m365_contacts", "label": "Sync Microsoft 365 contacts"},
        {"value": "sync_m365_mailboxes", "label": "Sync Microsoft 365 mailboxes"},
        {"value": "sync_huntress", "label": "Sync Huntress data"},
        {"value": "sync_to_xero", "label": "Sync to Xero"},
        {"value": "sync_to_xero_auto_send", "label": "Sync to Xero (Auto Send)"},
        {"value": "generate_invoice", "label": "Generate Invoice"},
        {"value": "create_scheduled_ticket", "label": "Create scheduled ticket"},
        {"value": "sync_recordings", "label": "Sync call recordings"},
        {
            "value": "sync_unifi_talk_recordings",
            "label": "Sync Unifi Talk recordings",
        },
        {"value": "queue_transcriptions", "label": "Queue transcriptions"},
        {"value": "process_transcription", "label": "Process transcription"},
    ]
    command_options = [o for o in command_options if o["value"] not in disabled_commands_global]
    existing_commands = {task.get("command") for task in tasks if task.get("command")}
    for command in sorted(existing_commands):
        if command and command not in {option["value"] for option in command_options} and command not in disabled_commands_global:
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
        "show_inactive_tasks": show_inactive,
    }
    return await _render_template("admin/automation.html", request, current_user, extra=extra)


@app.get("/admin/scheduled-tasks", response_class=HTMLResponse)
async def admin_scheduled_tasks(
    request: Request,
    show_inactive: bool = Query(default=False),
    success: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    tasks = await scheduled_tasks_repo.list_tasks(include_inactive=show_inactive)
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
    for task in tasks:
        serialised_task = _serialise_mapping(task)
        serialised_task["last_run_iso"] = _to_iso(task.get("last_run_at"))
        raw_company_id = task.get("company_id")
        company_key: int | None = None
        if raw_company_id is not None:
            try:
                company_key = int(raw_company_id)
            except (TypeError, ValueError):
                company_key = None
        if company_key is None:
            serialised_task["company_name"] = "All companies"
            serialised_task["company_edit_url"] = None
        else:
            company_name = company_lookup.get(company_key, f"Company #{company_key}")
            serialised_task["company_name"] = company_name
            serialised_task["company_edit_url"] = f"/admin/companies/{company_key}/edit"
        prepared_tasks.append(serialised_task)

    extra = {
        "title": "Scheduled Tasks",
        "tasks": prepared_tasks,
        "show_inactive": show_inactive,
        "success_message": success,
        "error_message": error,
    }
    return await _render_template("admin/scheduled_tasks.html", request, current_user, extra=extra)


@app.post("/admin/scheduled-tasks/bulk-delete", response_class=HTMLResponse)
async def admin_bulk_delete_scheduled_tasks(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    form = await request.form()
    raw_ids = form.getlist("taskIds")
    task_ids: list[int] = []
    seen: set[int] = set()
    for raw in raw_ids:
        try:
            identifier = int(raw)
        except (TypeError, ValueError):
            continue
        if identifier <= 0 or identifier in seen:
            continue
        seen.add(identifier)
        task_ids.append(identifier)

    if not task_ids:
        return RedirectResponse(
            url="/admin/scheduled-tasks?error=Select+at+least+one+task+to+delete.",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    try:
        deleted_count = await scheduled_tasks_repo.delete_tasks(task_ids)
        await scheduler_service.refresh()
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error(
            "Failed to bulk delete scheduled tasks",
            task_ids=task_ids,
            error=str(exc),
        )
        return RedirectResponse(
            url="/admin/scheduled-tasks?error=Unable+to+delete+the+selected+tasks.+Please+try+again.",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    log_info(
        "Scheduled tasks bulk deleted",
        deleted_count=deleted_count,
        deleted_by=current_user.get("id") if current_user else None,
        task_ids=task_ids,
    )

    message_suffix = "task" if deleted_count == 1 else "tasks"
    redirect_message = f"Deleted {deleted_count} {message_suffix}."
    if deleted_count < len(task_ids):
        redirect_message = f"Deleted {deleted_count} {message_suffix}. Some selected tasks were not found."

    show_inactive_raw = form.get("show_inactive")
    show_inactive_param = "1" if show_inactive_raw else ""
    base_url = "/admin/scheduled-tasks"
    params: list[str] = [f"success={quote(redirect_message)}"]
    if show_inactive_param:
        params.append(f"show_inactive={show_inactive_param}")
    return RedirectResponse(
        url=f"{base_url}?{'&'.join(params)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/scheduled-tasks/bulk-rename", response_class=HTMLResponse)
async def admin_bulk_rename_scheduled_tasks(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    form = await request.form()
    raw_ids = form.getlist("taskIds")
    task_ids: list[int] = []
    seen: set[int] = set()
    for raw in raw_ids:
        try:
            identifier = int(raw)
        except (TypeError, ValueError):
            continue
        if identifier <= 0 or identifier in seen:
            continue
        seen.add(identifier)
        task_ids.append(identifier)

    if not task_ids:
        return RedirectResponse(
            url="/admin/scheduled-tasks?error=Select+at+least+one+task+to+rename.",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    companies = await company_repo.list_companies()
    company_lookup: dict[int, str] = {}
    for company in companies:
        try:
            cid = int(company.get("id")) if company.get("id") is not None else None
        except (TypeError, ValueError):
            cid = None
        if cid is None:
            continue
        company_lookup[cid] = str(company.get("name") or f"Company #{cid}")

    renamed_count = 0
    for task_id in task_ids:
        task = await scheduled_tasks_repo.get_task(task_id)
        if not task:
            continue
        raw_company_id = task.get("company_id")
        company_key: int | None = None
        if raw_company_id is not None:
            try:
                company_key = int(raw_company_id)
            except (TypeError, ValueError):
                company_key = None
        if company_key is None:
            company_label = "All companies"
        else:
            company_label = company_lookup.get(company_key, f"Company #{company_key}")

        command = str(task.get("command") or "")
        command_label = TASK_COMMAND_LABELS.get(command, command)

        new_name = f"{company_label} \u2014 {command_label}"
        await scheduled_tasks_repo.rename_task(task_id, new_name)
        renamed_count += 1

    log_info(
        "Scheduled tasks bulk renamed",
        renamed_count=renamed_count,
        renamed_by=current_user.get("id") if current_user else None,
        task_ids=task_ids,
    )

    message_suffix = "task" if renamed_count == 1 else "tasks"
    redirect_message = f"Renamed {renamed_count} {message_suffix}."

    show_inactive_raw = form.get("show_inactive")
    show_inactive_param = "1" if show_inactive_raw else ""
    base_url = "/admin/scheduled-tasks"
    params: list[str] = [f"success={quote(redirect_message)}"]
    if show_inactive_param:
        params.append(f"show_inactive={show_inactive_param}")
    return RedirectResponse(
        url=f"{base_url}?{'&'.join(params)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# ---------------------------------------------------------------------------
# Tray app admin pages
# ---------------------------------------------------------------------------


@app.get("/admin/tray/devices", response_class=HTMLResponse)
async def admin_tray_devices_page(
    request: Request,
    search: str | None = None,
    status: str | None = None,
):
    current_user, redirect = await _require_authenticated_user(request)
    if redirect:
        return redirect
    if not (current_user.get("is_super_admin") or current_user.get("is_helpdesk_technician")):
        return RedirectResponse(url="/", status_code=303)

    from app.repositories import tray as tray_repo

    devices = await tray_repo.list_devices(status=(status or None))
    if search:
        needle = search.strip().lower()
        devices = [
            d for d in devices
            if needle in str(d.get("hostname") or "").lower()
            or needle in str(d.get("device_uid") or "").lower()
            or needle in str(d.get("console_user") or "").lower()
        ]
    extra = {
        "title": "Tray devices",
        "devices": devices,
        "filters": {"search": search or "", "status": status or ""},
        "matrix_enabled": settings.matrix_enabled,
    }
    return await _render_template("admin/tray/devices.html", request, current_user, extra=extra)


@app.post("/admin/tray/devices/{device_id}/revoke", response_class=HTMLResponse)
async def admin_tray_revoke_device(device_id: int, request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    from app.repositories import tray as tray_repo

    await tray_repo.revoke_device(device_id)
    return RedirectResponse(url="/admin/tray/devices?status=", status_code=303)


@app.get("/admin/tray/install-tokens", response_class=HTMLResponse)
async def admin_tray_install_tokens_page(
    request: Request,
    new_token: str | None = None,
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    from app.repositories import tray as tray_repo
    from app.repositories import companies as companies_repo

    tokens = await tray_repo.list_install_tokens()
    companies = await companies_repo.list_companies(include_archived=False)
    extra = {
        "title": "Tray install tokens",
        "tokens": tokens,
        "companies": companies,
        "new_token": new_token,
        "now_iso": datetime.now(timezone.utc).isoformat(),
        "portal_url": str(request.base_url).rstrip("/"),
    }
    return await _render_template(
        "admin/tray/install_tokens.html", request, current_user, extra=extra
    )


@app.post("/admin/tray/install-tokens", response_class=HTMLResponse)
async def admin_tray_create_install_token(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    label = (str(form.get("label", "")).strip() or "Untitled token")[:150]
    company_raw = str(form.get("company_id", "")).strip()
    company_id = int(company_raw) if company_raw.isdigit() else None

    from app.repositories import tray as tray_repo
    from app.services import tray as tray_service

    raw_token = tray_service.generate_install_token()
    await tray_repo.create_install_token(
        label=label,
        company_id=company_id,
        token_hash=tray_service.hash_token(raw_token),
        token_prefix=tray_service.token_prefix(raw_token),
        created_by_user_id=int(current_user["id"]),
    )
    # Redirect with the raw token in the query string so the admin sees it once.
    # The token cannot be reconstructed later — only the prefix is displayed.
    return RedirectResponse(
        url=f"/admin/tray/install-tokens?new_token={raw_token}", status_code=303
    )


@app.post("/admin/tray/install-tokens/{token_id}/revoke", response_class=HTMLResponse)
async def admin_tray_revoke_install_token(token_id: int, request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    from app.repositories import tray as tray_repo

    await tray_repo.revoke_install_token(token_id)
    return RedirectResponse(url="/admin/tray/install-tokens", status_code=303)


@app.get("/admin/tray/configurations", response_class=HTMLResponse)
async def admin_tray_configurations_page(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    from app.repositories import tray as tray_repo

    configurations = await tray_repo.list_menu_configs()
    for cfg in configurations:
        scope = cfg.get("scope")
        ref = cfg.get("scope_ref_id")
        cfg["scope_target_label"] = f"#{ref}" if ref else None
    extra = {
        "title": "Tray menu configurations",
        "configurations": configurations,
    }
    return await _render_template(
        "admin/tray/configurations.html", request, current_user, extra=extra
    )


@app.get("/admin/tray/configurations/new", response_class=HTMLResponse)
async def admin_tray_new_configuration_page(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    extra = {
        "title": "New tray configuration",
        "heading": "New tray menu configuration",
        "form_action": "/admin/tray/configurations",
        "config": {
            "name": "",
            "scope": "global",
            "scope_ref_id": None,
            "enabled": True,
            "display_text": "",
            "env_allowlist_csv": "",
            "branding_icon_url": "",
            "payload_json": "[]",
        },
    }
    return await _render_template(
        "admin/tray/configuration_form.html", request, current_user, extra=extra
    )


@app.get("/admin/tray/configurations/{config_id}/edit", response_class=HTMLResponse)
async def admin_tray_edit_configuration_page(config_id: int, request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    from app.repositories import tray as tray_repo

    record = await tray_repo.get_menu_config(config_id)
    if not record:
        return RedirectResponse(url="/admin/tray/configurations", status_code=303)
    extra = {
        "title": "Edit tray configuration",
        "heading": f"Edit configuration: {record.get('name')}",
        "form_action": f"/admin/tray/configurations/{config_id}",
        "config": {
            "name": record.get("name"),
            "scope": record.get("scope"),
            "scope_ref_id": record.get("scope_ref_id"),
            "enabled": bool(record.get("enabled")),
            "display_text": record.get("display_text") or "",
            "env_allowlist_csv": record.get("env_allowlist") or "",
            "branding_icon_url": record.get("branding_icon_url") or "",
            "payload_json": record.get("payload_json") or "[]",
        },
    }
    return await _render_template(
        "admin/tray/configuration_form.html", request, current_user, extra=extra
    )


async def _save_tray_configuration_from_form(form, current_user, *, config_id: int | None):
    import json as _json
    from app.repositories import tray as tray_repo
    from app.services import sanitization

    name = (str(form.get("name", "")).strip() or "Untitled")[:150]
    scope = str(form.get("scope", "global")).strip().lower()
    if scope not in {"global", "company", "tag", "device"}:
        scope = "global"
    scope_ref_raw = str(form.get("scope_ref_id", "")).strip()
    scope_ref_id = int(scope_ref_raw) if scope_ref_raw.isdigit() else None
    enabled = str(form.get("enabled", "")).lower() in {"1", "true", "on", "yes"}
    payload_raw = str(form.get("payload_json", "[]")).strip() or "[]"
    try:
        parsed = _json.loads(payload_raw)
        if not isinstance(parsed, list):
            raise ValueError("Menu payload must be a JSON array")
    except (ValueError, TypeError):
        parsed = []
    payload_json = _json.dumps(parsed)
    display_text = str(form.get("display_text", "") or "")
    if display_text:
        sanitized = sanitization.sanitize_rich_text(display_text)
        display_text = sanitized.html if sanitized else None
    else:
        display_text = None
    env_csv = ",".join(
        v.strip()
        for v in str(form.get("env_allowlist", "") or "").split(",")
        if v.strip()
    )
    branding = (str(form.get("branding_icon_url", "")).strip() or None)

    if config_id is None:
        await tray_repo.create_menu_config(
            name=name,
            scope=scope,
            scope_ref_id=scope_ref_id,
            payload_json=payload_json,
            display_text=display_text,
            env_allowlist=env_csv,
            branding_icon_url=branding,
            enabled=enabled,
            created_by_user_id=int(current_user["id"]),
        )
    else:
        await tray_repo.update_menu_config(
            config_id,
            name=name,
            payload_json=payload_json,
            display_text=display_text,
            env_allowlist=env_csv,
            branding_icon_url=branding,
            enabled=enabled,
            updated_by_user_id=int(current_user["id"]),
        )


@app.post("/admin/tray/configurations", response_class=HTMLResponse)
async def admin_tray_create_configuration(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    await _save_tray_configuration_from_form(form, current_user, config_id=None)
    return RedirectResponse(url="/admin/tray/configurations", status_code=303)


@app.post("/admin/tray/configurations/{config_id}", response_class=HTMLResponse)
async def admin_tray_update_configuration(config_id: int, request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    await _save_tray_configuration_from_form(form, current_user, config_id=config_id)
    return RedirectResponse(url="/admin/tray/configurations", status_code=303)


@app.post("/admin/tray/configurations/{config_id}/delete", response_class=HTMLResponse)
async def admin_tray_delete_configuration(config_id: int, request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    from app.repositories import tray as tray_repo

    await tray_repo.delete_menu_config(config_id)
    return RedirectResponse(url="/admin/tray/configurations", status_code=303)


# ---------------------------------------------------------------------------
# Phase 5 – Admin: diagnostics page
# ---------------------------------------------------------------------------


@app.get("/admin/tray/diagnostics", response_class=HTMLResponse)
async def admin_tray_diagnostics_page(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    from app.repositories import tray as tray_repo

    bundles = await tray_repo.list_diagnostics()
    return await _render_template(
        "admin/tray/diagnostics.html",
        request,
        current_user,
        extra={"bundles": bundles},
    )


@app.get("/admin/tray/diagnostics/{diag_id}/download")
async def admin_tray_diagnostics_download(diag_id: int, request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    from app.repositories import tray as tray_repo
    from fastapi.responses import FileResponse

    row = await tray_repo.get_diagnostic(diag_id)
    if not row:
        raise HTTPException(status_code=404, detail="Diagnostic not found.")
    path = row.get("stored_path", "")
    import os
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Diagnostic file not found on disk.")
    return FileResponse(
        path,
        media_type="application/zip",
        filename=row.get("filename", f"diag-{diag_id}.zip"),
    )


# ---------------------------------------------------------------------------
# Phase 5 – Admin: versions page
# ---------------------------------------------------------------------------


@app.get("/admin/tray/versions", response_class=HTMLResponse)
async def admin_tray_versions_page(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    from app.repositories import tray as tray_repo

    versions = await tray_repo.list_tray_versions()
    return await _render_template(
        "admin/tray/versions.html",
        request,
        current_user,
        extra={"versions": versions},
    )


@app.post("/admin/tray/versions", response_class=HTMLResponse)
async def admin_tray_versions_publish(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    from app.repositories import tray as tray_repo

    form = await request.form()
    version = str(form.get("version", "")).strip()
    platform = str(form.get("platform", "all")).strip().lower()
    download_url = str(form.get("download_url", "")).strip()
    required = bool(form.get("required"))
    if version and download_url:
        await tray_repo.publish_tray_version(
            version=version,
            platform=platform,
            download_url=download_url,
            required=required,
            release_notes=None,
            published_by_user_id=int(current_user["id"]),
        )
    return RedirectResponse(url="/admin/tray/versions", status_code=303)


# ---------------------------------------------------------------------------
# Tray icon (system-tray branding)
# ---------------------------------------------------------------------------


_TRAY_ICON_DIR = _private_uploads_path / "tray-icon"
_TRAY_ICON_MAX_BYTES = 1 * 1024 * 1024  # 1 MB is plenty for a multi-res .ico


def _tray_icon_uploads_root() -> Path:
    _TRAY_ICON_DIR.mkdir(parents=True, exist_ok=True)
    return _private_uploads_path


@app.get(
    "/tray/icon.ico",
    include_in_schema=False,
)
async def tray_icon_endpoint() -> Response:
    """Serve the active tray-icon ``.ico`` file.

    Returns the admin-uploaded override when present, otherwise a default
    icon generated at runtime from the website favicon palette. No
    authentication is required — the icon is public branding consumed by
    every tray client at startup.
    """
    from app.services import tray_icon as tray_icon_service

    data = await tray_icon_service.get_tray_icon_bytes(_private_uploads_path)
    return Response(
        content=data,
        media_type="image/x-icon",
        headers={"Cache-Control": "public, max-age=300"},
    )


@app.get("/admin/tray/branding", response_class=HTMLResponse)
async def admin_tray_branding_page(
    request: Request,
    success: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    current_path = await site_settings_repo.get_tray_icon_path()
    extra = {
        "title": "Tray icon",
        "current_path": current_path,
        "success_message": _sanitize_message(success),
        "error_message": _sanitize_message(error),
    }
    return await _render_template(
        "admin/tray/branding.html", request, current_user, extra=extra
    )


@app.post("/admin/tray/branding", response_class=HTMLResponse)
async def admin_tray_branding_upload(
    request: Request,
    icon: UploadFile = File(None),
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    from app.services import tray_icon as tray_icon_service

    if icon is None or not icon.filename:
        return RedirectResponse(
            url="/admin/tray/branding?error=No+file+selected",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    data = await icon.read(_TRAY_ICON_MAX_BYTES + 1)
    if len(data) > _TRAY_ICON_MAX_BYTES:
        return RedirectResponse(
            url="/admin/tray/branding?error=File+too+large+%28max+1+MB%29",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if not tray_icon_service.is_valid_ico(data):
        return RedirectResponse(
            url="/admin/tray/branding?error=Not+a+valid+.ico+file",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    uploads_root = _tray_icon_uploads_root()
    target = _TRAY_ICON_DIR / "tray-icon.ico"
    try:
        target.write_bytes(data)
    except OSError as exc:
        log_error(f"Failed to write tray icon: {exc}")
        return RedirectResponse(
            url="/admin/tray/branding?error=Failed+to+save+icon",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    relative_path = str(target.relative_to(uploads_root)).replace("\\", "/")
    await site_settings_repo.set_tray_icon_path(relative_path)
    await audit_service.log_action(
        action="admin.tray.icon.upload",
        user_id=current_user.get("id"),
        entity_type="site_settings",
        entity_id=1,
        metadata={"path": relative_path, "bytes": len(data)},
        request=request,
    )
    return RedirectResponse(
        url="/admin/tray/branding?success=Tray+icon+updated",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/tray/branding/delete", response_class=HTMLResponse)
async def admin_tray_branding_delete(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    current_path = await site_settings_repo.get_tray_icon_path()
    if current_path:
        try:
            candidate = (_private_uploads_path / current_path).resolve()
            uploads_root_resolved = _private_uploads_path.resolve()
            if (
                uploads_root_resolved == candidate
                or uploads_root_resolved in candidate.parents
            ) and candidate.is_file():
                candidate.unlink()
        except OSError:
            pass
    await site_settings_repo.set_tray_icon_path(None)
    await audit_service.log_action(
        action="admin.tray.icon.delete",
        user_id=current_user.get("id"),
        entity_type="site_settings",
        entity_id=1,
        metadata={},
        request=request,
    )
    return RedirectResponse(
        url="/admin/tray/branding?success=Tray+icon+reset+to+default",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# ---------------------------------------------------------------------------
# Per-company tray settings
# ---------------------------------------------------------------------------


async def admin_company_tray_settings_page(
    company_id: int,
    request: Request,
    new_token: str | None = None,
    success: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    from app.repositories import companies as companies_repo
    from app.repositories import tray as tray_repo

    company = await companies_repo.get_company_by_id(company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    tokens = await tray_repo.list_install_tokens(company_id=company_id)
    extra = {
        "title": f"Tray settings — {company.get('name') or 'company'}",
        "company": company,
        "tokens": tokens,
        "new_token": new_token,
        "now_iso": datetime.now(timezone.utc).isoformat(),
        "portal_url": str(request.base_url).rstrip("/"),
        "success_message": _sanitize_message(success),
        "error_message": _sanitize_message(error),
    }
    return await _render_template(
        "admin/tray/company_settings.html", request, current_user, extra=extra
    )


async def admin_company_tray_settings_save(company_id: int, request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    form = await request.form()

    from app.repositories import companies as companies_repo

    company = await companies_repo.get_company_by_id(company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    tray_chat_enabled = 1 if form.get("tray_chat_enabled") else 0
    tray_notifications_enabled = 1 if form.get("tray_notifications_enabled") else 0

    await companies_repo.update_company(
        company_id,
        tray_chat_enabled=tray_chat_enabled,
        tray_notifications_enabled=tray_notifications_enabled,
    )
    cid = int(company_id)
    return RedirectResponse(
        url=f"/admin/companies/{cid}/tray?" + urlencode({"success": "Tray settings saved."}),
        status_code=303,
    )


async def admin_company_tray_create_token(company_id: int, request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    form = await request.form()

    from app.repositories import companies as companies_repo
    from app.repositories import tray as tray_repo
    from app.services import tray as tray_service

    company = await companies_repo.get_company_by_id(company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    label = (str(form.get("label", "")).strip() or f"{company.get('name') or 'Company'} token")[:150]
    raw_token = tray_service.generate_install_token()
    await tray_repo.create_install_token(
        label=label,
        company_id=company_id,
        token_hash=tray_service.hash_token(raw_token),
        token_prefix=tray_service.token_prefix(raw_token),
        created_by_user_id=int(current_user["id"]),
    )
    cid = int(company_id)
    return RedirectResponse(
        url=f"/admin/companies/{cid}/tray?" + urlencode({"new_token": raw_token}),
        status_code=303,
    )


async def admin_company_tray_revoke_token(
    company_id: int, token_id: int, request: Request
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    from app.repositories import tray as tray_repo

    await tray_repo.revoke_install_token(token_id)
    cid = int(company_id)
    return RedirectResponse(
        url=f"/admin/companies/{cid}/tray?" + urlencode({"success": "Token revoked."}),
        status_code=303,
    )



async def admin_audit_logs(
    request: Request,
    entity_type: str | None = None,
    entity_id: int | None = None,
    user_id: int | None = None,
    action: str | None = None,
    request_id: str | None = None,
    ip_address: str | None = None,
    since: str | None = None,
    until: str | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    limit = max(1, min(limit, 500))
    offset = max(0, int(offset))

    def _parse_filter_dt(value: str | None):
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    since_dt = _parse_filter_dt(since)
    until_dt = _parse_filter_dt(until)

    filter_kwargs = dict(
        entity_type=entity_type or None,
        entity_id=entity_id,
        user_id=user_id,
        action=(action or "").strip() or None,
        request_id=(request_id or "").strip() or None,
        ip_address=(ip_address or "").strip() or None,
        since=since_dt,
        until=until_dt,
        search=(search or "").strip() or None,
    )
    logs = await audit_repo.list_audit_logs(
        **filter_kwargs,
        limit=limit,
        offset=offset,
    )
    total = await audit_repo.count_audit_logs(**filter_kwargs)
    available_actions = await audit_repo.list_distinct_actions()

    for log in logs:
        log["created_at_iso"] = _to_iso(log.get("created_at"))
        log["diff_rows"] = _build_audit_diff_rows(
            log.get("previous_value"), log.get("new_value")
        )

    extra = {
        "title": "Audit trail",
        "logs": logs,
        "available_actions": available_actions,
        "total": total,
        "filters": {
            "entity_type": entity_type or "",
            "entity_id": entity_id or "",
            "user_id": user_id or "",
            "action": action or "",
            "request_id": request_id or "",
            "ip_address": ip_address or "",
            "since": since or "",
            "until": until or "",
            "search": search or "",
            "limit": limit,
            "offset": offset,
        },
        "pagination": {
            "limit": limit,
            "offset": offset,
            "total": total,
            "has_prev": offset > 0,
            "has_next": (offset + limit) < total,
            "prev_offset": max(0, offset - limit),
            "next_offset": offset + limit,
        },
    }
    return await _render_template("admin/audit_logs.html", request, current_user, extra=extra)


def _build_audit_diff_rows(previous: Any, current: Any) -> list[dict[str, Any]]:
    """Convert previous/new JSON snapshots into a per-field table for the UI.

    Returns a list of ``{"field", "previous", "current", "changed"}`` entries
    sorted by field name. The diff helper already filtered to changed fields
    when callers used ``audit.record``, but we still flag rows so legacy
    entries (which stored full snapshots) render with a visual hint.
    """

    if isinstance(previous, str):
        try:
            previous = json.loads(previous)
        except (TypeError, ValueError):
            previous = {"value": previous}
    if isinstance(current, str):
        try:
            current = json.loads(current)
        except (TypeError, ValueError):
            current = {"value": current}

    if not isinstance(previous, dict) and not isinstance(current, dict):
        if previous is None and current is None:
            return []
        return [
            {
                "field": "value",
                "previous": previous,
                "current": current,
                "changed": previous != current,
            }
        ]

    keys: set[str] = set()
    if isinstance(previous, dict):
        keys.update(str(k) for k in previous.keys())
    if isinstance(current, dict):
        keys.update(str(k) for k in current.keys())

    rows: list[dict[str, Any]] = []
    for key in sorted(keys):
        prev_value = previous.get(key) if isinstance(previous, dict) else None
        curr_value = current.get(key) if isinstance(current, dict) else None
        rows.append(
            {
                "field": key,
                "previous": prev_value,
                "current": curr_value,
                "changed": prev_value != curr_value,
            }
        )
    return rows


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
        context["request"],
        "admin/tag_exclusions.html",
        context,
    )



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
    await audit_service.record_create(
        action="shop.package.create",
        request=request,
        entity_type="shop.package",
        entity_id=int(package_id) if package_id else None,
        after={
            "id": package_id,
            "sku": cleaned_sku,
            "name": cleaned_name,
            "description": cleaned_description,
        },
    )
    return RedirectResponse(url="/admin/shop/packages", status_code=status.HTTP_303_SEE_OTHER)


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

    existing_package = await shop_repo.get_package(package_id, include_archived=True)
    if not existing_package:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Package not found")

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
    await audit_service.record(
        action="shop.package.update",
        request=request,
        entity_type="shop.package",
        entity_id=package_id,
        before={
            "sku": existing_package.get("sku"),
            "name": existing_package.get("name"),
            "description": existing_package.get("description"),
            "archived": bool(existing_package.get("archived")),
        },
        after={
            "sku": cleaned_sku,
            "name": cleaned_name,
            "description": cleaned_description,
            "archived": archived_flag,
        },
    )
    return RedirectResponse(
        url=f"/admin/shop/packages/{package_id}",
        status_code=status.HTTP_303_SEE_OTHER,
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
    await audit_service.record(
        action="shop.package.archive" if archived_flag else "shop.package.unarchive",
        request=request,
        entity_type="shop.package",
        entity_id=package_id,
        before={"archived": bool(package.get("archived"))},
        after={"archived": archived_flag},
    )
    return RedirectResponse(url="/admin/shop/packages", status_code=status.HTTP_303_SEE_OTHER)


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
    await audit_service.record_delete(
        action="shop.package.delete",
        request=request,
        entity_type="shop.package",
        entity_id=package_id,
        before=package,
    )
    return RedirectResponse(url="/admin/shop/packages", status_code=status.HTTP_303_SEE_OTHER)


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
    await audit_service.record(
        action="shop.package.item.add",
        request=request,
        entity_type="shop.package",
        entity_id=package_id,
        metadata={"product_id": product_identifier, "quantity": quantity_value},
    )
    return RedirectResponse(
        url=f"/admin/shop/packages/{package_id}",
        status_code=status.HTTP_303_SEE_OTHER,
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
    await audit_service.record(
        action="shop.package.item.update",
        request=request,
        entity_type="shop.package",
        entity_id=package_id,
        metadata={"product_id": product_id, "quantity": quantity_value},
    )
    return RedirectResponse(
        url=f"/admin/shop/packages/{package_id}",
        status_code=status.HTTP_303_SEE_OTHER,
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
    await audit_service.record(
        action="shop.package.item.alternate.add",
        request=request,
        entity_type="shop.package",
        entity_id=package_id,
        metadata={
            "product_id": primary_id,
            "alternate_product_id": alternate_id,
            "priority": priority_value,
        },
    )

    return RedirectResponse(
        url=f"/admin/shop/packages/{package_id}",
        status_code=status.HTTP_303_SEE_OTHER,
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
    await audit_service.record(
        action="shop.package.item.alternate.remove",
        request=request,
        entity_type="shop.package",
        entity_id=package_id,
        metadata={
            "product_id": product_id,
            "alternate_product_id": alternate_product_id,
        },
    )

    return RedirectResponse(
        url=f"/admin/shop/packages/{package_id}",
        status_code=status.HTTP_303_SEE_OTHER,
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
    await audit_service.record(
        action="shop.package.item.remove",
        request=request,
        entity_type="shop.package",
        entity_id=package_id,
        metadata={"product_id": product_id},
    )
    return RedirectResponse(
        url=f"/admin/shop/packages/{package_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def admin_shop_page(
    request: Request,
    show_archived: bool = Query(False, alias="showArchived"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, alias="pageSize", ge=1, le=200),
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect
    categories_task = asyncio.create_task(shop_repo.list_all_categories_flat())
    filter_categories_task = asyncio.create_task(shop_repo.list_categories_with_products())
    offset = (page - 1) * page_size
    filters = shop_repo.ProductFilters(
        include_archived=show_archived,
        limit=page_size,
        offset=offset,
        sort="name_asc",
    )
    products_task = asyncio.create_task(
        shop_repo.list_products_summary(filters)
    )
    total_count_task = asyncio.create_task(shop_repo.count_products(filters))
    companies_task = asyncio.create_task(company_repo.list_companies())
    subscription_categories_task = asyncio.create_task(subscription_categories_repo.list_categories())

    categories, filter_categories, products, total_count, companies, subscription_categories = await asyncio.gather(
        categories_task,
        filter_categories_task,
        products_task,
        total_count_task,
        companies_task,
        subscription_categories_task,
    )

    for product in products:
        product["price_below_threshold"] = shop_service.is_price_below_dbp_threshold(
            product, is_vip=False
        )
        product["vip_price_below_threshold"] = product.get(
            "vip_price"
        ) is not None and shop_service.is_price_below_dbp_threshold(product, is_vip=True)
        _profit = shop_service.calculate_profit(product, is_vip=False)
        product["profit"] = float(_profit) if _profit is not None else None
        _vip_profit = shop_service.calculate_profit(product, is_vip=True)
        product["vip_profit"] = float(_vip_profit) if _vip_profit is not None else None

    # Collect the SKU used for price-history look-ups (vendor_sku preferred).
    history_skus = [
        product["vendor_sku"] or product["sku"]
        for product in products
        if product.get("vendor_sku") or product.get("sku")
    ]
    dbp_trends: dict[str, str | None] = {}
    if history_skus:
        dbp_trends = await stock_feed_repo.get_recent_dbp_trends(history_skus)
    for product in products:
        lookup_sku = product.get("vendor_sku") or product.get("sku") or ""
        product["dbp_trend"] = dbp_trends.get(lookup_sku)

    extra = {
        "title": "Shop admin",
        "categories": categories,
        "filter_categories": filter_categories,
        "products": products,
        "all_companies": companies,
        "show_archived": show_archived,
        "subscription_categories": subscription_categories,
        "total_count": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, math.ceil(total_count / page_size)) if page_size else 1,
    }
    return await _render_template("admin/shop.html", request, current_user, extra=extra)


async def admin_shop_optional_accessories_page(
    request: Request, show: str = "pending"
):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    if show not in ("pending", "dismissed"):
        show = "pending"

    show_dismissed = show == "dismissed"
    if show_dismissed:
        accessories = await shop_repo.list_dismissed_optional_accessories()
    else:
        accessories = await shop_repo.list_pending_optional_accessories()

    extra = {
        "title": "Optional accessories",
        "accessories": accessories,
        "show_dismissed": show_dismissed,
    }
    return await _render_template(
        "admin/shop_optional_accessories.html", request, current_user, extra=extra
    )


async def admin_sync_optional_accessories(request: Request):
    """Re-scan the stock feed and refresh the pending optional accessories table."""
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    await shop_repo.sync_pending_optional_accessories()
    await audit_service.record(
        action="shop.optional_accessory.sync",
        request=request,
        entity_type="shop.optional_accessory",
    )
    return RedirectResponse(
        url="/admin/shop/optional-accessories",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def admin_import_optional_accessory(
    request: Request, accessory_id: int
):
    """Import a pending optional accessory from the staging table into shop_products."""
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    accessory = await shop_repo.get_pending_optional_accessory(accessory_id)
    if not accessory:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pending optional accessory not found",
        )

    imported = await products_service.import_product_by_vendor_sku(accessory["sku"])
    if imported:
        await shop_repo.dismiss_pending_optional_accessory(accessory_id)

    await audit_service.record(
        action="shop.optional_accessory.import",
        request=request,
        entity_type="shop.optional_accessory",
        entity_id=accessory_id,
        metadata={"vendor_sku": accessory["sku"], "imported": bool(imported)},
    )
    return RedirectResponse(
        url="/admin/shop/optional-accessories",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def admin_dismiss_optional_accessory(
    request: Request, accessory_id: int
):
    """Soft-dismiss a pending optional accessory without importing."""
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    await shop_repo.dismiss_pending_optional_accessory(accessory_id)
    await audit_service.record(
        action="shop.optional_accessory.dismiss",
        request=request,
        entity_type="shop.optional_accessory",
        entity_id=accessory_id,
    )
    return RedirectResponse(
        url="/admin/shop/optional-accessories",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def admin_bulk_dismiss_optional_accessories(request: Request):
    """Soft-dismiss multiple pending optional accessories at once."""
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    form = await request.form()
    raw_ids = form.getlist("accessory_ids")
    ids: list[int] = []
    for raw in raw_ids:
        try:
            ids.append(int(raw))
        except (ValueError, TypeError):
            pass

    if ids:
        await shop_repo.bulk_dismiss_pending_optional_accessories(ids)
    await audit_service.record(
        action="shop.optional_accessory.bulk_dismiss",
        request=request,
        entity_type="shop.optional_accessory",
        metadata={"accessory_ids": ids, "count": len(ids)},
    )
    return RedirectResponse(
        url="/admin/shop/optional-accessories",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def admin_restore_optional_accessory(
    request: Request, accessory_id: int
):
    """Restore a dismissed optional accessory back to pending."""
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    await shop_repo.restore_dismissed_optional_accessory(accessory_id)
    await audit_service.record(
        action="shop.optional_accessory.restore",
        request=request,
        entity_type="shop.optional_accessory",
        entity_id=accessory_id,
    )
    return RedirectResponse(
        url="/admin/shop/optional-accessories?show=dismissed",
        status_code=status.HTTP_303_SEE_OTHER,
    )


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


async def admin_shop_product_create_page(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    categories_task = asyncio.create_task(shop_repo.list_all_categories_flat())
    products_task = asyncio.create_task(
        shop_repo.list_products_summary(shop_repo.ProductFilters(include_archived=False))
    )
    subscription_categories_task = asyncio.create_task(subscription_categories_repo.list_categories())

    categories, products, subscription_categories = await asyncio.gather(
        categories_task, products_task, subscription_categories_task
    )

    extra = {
        "title": "Add product",
        "categories": categories,
        "products": products,
        "subscription_categories": subscription_categories,
        "product_restrictions": [],
    }
    return await _render_template(
        "admin/shop_product_create.html", request, current_user, extra=extra
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
    await audit_service.record_create(
        action="shop.category.create",
        request=request,
        entity_type="shop.category",
        entity_id=int(category_id) if category_id else None,
        after={"id": category_id, "name": cleaned_name, "parent_id": parsed_parent_id},
    )
    return RedirectResponse(url="/admin/shop/categories", status_code=status.HTTP_303_SEE_OTHER)


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
    await audit_service.record_delete(
        action="shop.category.delete",
        request=request,
        entity_type="shop.category",
        entity_id=category_id,
        before=category,
    )
    return RedirectResponse(url="/admin/shop/categories", status_code=status.HTTP_303_SEE_OTHER)


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
    await audit_service.record(
        action="shop.category.update",
        request=request,
        entity_type="shop.category",
        entity_id=category_id,
        before={"name": category.get("name"), "parent_id": category.get("parent_id")},
        after={"name": cleaned_name, "parent_id": parsed_parent_id},
    )
    return RedirectResponse(url="/admin/shop/categories", status_code=status.HTTP_303_SEE_OTHER)


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
    await audit_service.record_create(
        action="shop.subscription_category.create",
        request=request,
        entity_type="shop.subscription_category",
        after={"name": cleaned_name, "description": cleaned_description},
    )
    return RedirectResponse(url="/admin/shop/subscription-categories", status_code=status.HTTP_303_SEE_OTHER)


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
    await audit_service.record_delete(
        action="shop.subscription_category.delete",
        request=request,
        entity_type="shop.subscription_category",
        entity_id=category_id,
        before=category,
    )
    return RedirectResponse(url="/admin/shop/subscription-categories", status_code=status.HTTP_303_SEE_OTHER)


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
    await audit_service.record(
        action="shop.subscription_category.update",
        request=request,
        entity_type="shop.subscription_category",
        entity_id=category_id,
        before={"name": category.get("name"), "description": category.get("description")},
        after={"name": cleaned_name, "description": cleaned_description},
    )
    return RedirectResponse(url="/admin/shop/subscription-categories", status_code=status.HTTP_303_SEE_OTHER)


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

    await audit_service.record(
        action="shop.product.import",
        request=request,
        entity_type="shop.product",
        metadata={"vendor_sku": cleaned_vendor_sku},
    )
    return RedirectResponse(url="/admin/shop", status_code=status.HTTP_303_SEE_OTHER)


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
        field_label="Cross-sell",
    )
    upsell_ids = await _validate_recommendation_product_ids(
        upsell_product_ids,
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
    await audit_service.record_create(
        action="shop.product.create",
        request=request,
        entity_type="shop.product",
        entity_id=int(product["id"]),
        after=product,
        sensitive_extra_keys=("buy_price",),
    )
    return RedirectResponse(url="/admin/shop", status_code=status.HTTP_303_SEE_OTHER)


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
        field_label="Cross-sell",
        disallow_product_id=product_id,
    )
    upsell_candidates = _normalise_related_product_inputs(upsell_product_ids)
    resolved_upsell_id = await _resolve_related_product_id_by_sku(upsell_sku)
    if resolved_upsell_id:
        upsell_candidates.append(resolved_upsell_id)

    upsell_ids = await _validate_recommendation_product_ids(
        upsell_candidates,
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
    await audit_service.record(
        action="shop.product.update",
        request=request,
        entity_type="shop.product",
        entity_id=product_id,
        before=product,
        after=updated,
        sensitive_extra_keys=("buy_price",),
    )
    redirect_params: dict[str, str] = {}
    try:
        # request.query_params accesses scope["query_string"] which may be absent
        # in synthetic test requests; guard with KeyError to stay safe in production
        qp = request.query_params
        if qp.get("showArchived"):
            redirect_params["showArchived"] = "1"
        page_str = qp.get("page", "")
        if page_str.isdigit() and int(page_str) > 1:
            redirect_params["page"] = page_str
        page_size_str = qp.get("pageSize", "")
        if page_size_str.isdigit() and int(page_size_str) > 0:
            redirect_params["pageSize"] = page_size_str
    except KeyError:
        pass
    redirect_url = f"/admin/shop?{urlencode(redirect_params)}" if redirect_params else "/admin/shop"
    return RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)


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
    await audit_service.record(
        action="shop.product.archive" if archived else "shop.product.unarchive",
        request=request,
        entity_type="shop.product",
        entity_id=product_id,
        before={"archived": bool(product.get("archived"))},
        after={"archived": archived},
    )
    return RedirectResponse(url="/admin/shop", status_code=status.HTTP_303_SEE_OTHER)


async def admin_archive_shop_product(request: Request, product_id: int):
    return await _handle_shop_product_archive(request, product_id, archived=True)


async def admin_unarchive_shop_product(request: Request, product_id: int):
    return await _handle_shop_product_archive(request, product_id, archived=False)


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
    await audit_service.record(
        action="shop.product.visibility_change",
        request=request,
        entity_type="shop.product",
        entity_id=product_id,
        before={"excluded_company_ids": sorted(int(cid) for cid in (product.get("excluded_company_ids") or []))},
        after={"excluded_company_ids": sorted(excluded_ids)},
    )
    return RedirectResponse(url="/admin/shop", status_code=status.HTTP_303_SEE_OTHER)


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
    await audit_service.record_delete(
        action="shop.product.delete",
        request=request,
        entity_type="shop.product",
        entity_id=product_id,
        before=product,
        sensitive_extra_keys=("buy_price",),
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


def _format_attachment_uploaded_iso(uploaded_at: datetime | None) -> str | None:
    """Normalize attachment timestamps to UTC ISO strings."""
    if not isinstance(uploaded_at, datetime):
        return None
    uploaded_dt = (
        uploaded_at.replace(tzinfo=timezone.utc)
        if uploaded_at.tzinfo is None
        else uploaded_at.astimezone(timezone.utc)
    )
    return uploaded_dt.isoformat()


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

    # Per-recipient delivery counts power the click-through delivery-status
    # popup. We only show the click trigger when there is more than one
    # recipient on the send.
    from app.services import email_recipients as _email_recipients_svc

    reply_recipient_counts: dict[int, int] = {}
    try:
        reply_ids_for_counts = [r.get("id") for r in ordered_replies if r.get("id") is not None]
        reply_recipient_counts = await _email_recipients_svc.get_recipient_count_map(reply_ids_for_counts)
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to load per-reply recipient counts", error=str(exc))
        reply_recipient_counts = {}

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
        
        # Get email tracking status if available
        email_tracking_id = reply.get("email_tracking_id")
        email_opened_at = reply.get("email_opened_at")
        email_open_count = reply.get("email_open_count", 0)
        email_sent_at = reply.get("email_sent_at")
        has_tracking = email_tracking_id is not None
        is_email_opened = email_opened_at is not None
        try:
            recipient_count_for_reply = int(reply_recipient_counts.get(int(reply.get("id")), 0))
        except (TypeError, ValueError):
            recipient_count_for_reply = 0
        
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
                "external_reference": reply.get("external_reference"),
                "email_tracking_id": email_tracking_id,
                "email_sent_at": email_sent_at,
                "email_opened_at": email_opened_at,
                "email_open_count": email_open_count,
                "has_email_tracking": has_tracking,
                "is_email_opened": is_email_opened,
                "recipient_count": recipient_count_for_reply,
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

    # Get ticket watchers
    watcher_records = await tickets_repo.list_watchers(ticket_id)
    watchers = []
    for watcher in watcher_records:
        if watcher.get("user_id"):
            watcher_user = user_lookup.get(watcher.get("user_id"))
            watchers.append({
                "id": watcher.get("user_id"),
                "label": _format_user_label(watcher_user) if watcher_user else "Unknown User",
                "email": watcher.get("email"),
            })
        elif watcher.get("email"):
            watchers.append({
                "id": None,
                "label": watcher.get("email"),
                "email": watcher.get("email"),
            })

    # Get ticket attachments
    attachment_records: list[Mapping[str, Any]] = []
    try:
        if has_helpdesk_access or is_super_admin:
            attachment_records = await attachments_repo.list_attachments(ticket_id)
        else:
            attachment_records = await attachments_repo.list_attachments(
                ticket_id, access_levels=("open", "closed")
            )
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to load ticket attachments", ticket_id=ticket_id, error=str(exc))
        attachment_records = []

    formatted_attachments: list[dict[str, Any]] = []
    for attachment in attachment_records:
        uploaded_at = attachment.get("uploaded_at")
        uploaded_iso = _format_attachment_uploaded_iso(uploaded_at)
        try:
            file_size = int(attachment.get("file_size", 0) or 0)
        except (TypeError, ValueError):
            file_size = 0

        formatted_attachments.append(
            {
                **attachment,
                "uploaded_iso": uploaded_iso,
                "file_size": file_size,
            }
        )

    # Get linked assets
    ticket_assets = await tickets_repo.list_ticket_assets(ticket_id)
    
    # Find relevant service statuses based on AI tag matching
    from app.services import service_status as service_status_service
    relevant_services: list[dict[str, Any]] = []
    if ticket_ai_tags:
        relevant_services = await service_status_service.find_relevant_services_for_ticket(
            ticket_ai_tags=ticket_ai_tags,
            company_id=company_numeric,
        )
    
    # Create service status lookup for consistent styling
    service_status_lookup = {entry["value"]: entry for entry in service_status_service.STATUS_DEFINITIONS}

    # Fetch linked chat room for this ticket (if matrix chat is enabled)
    ticket_chat_room: dict[str, Any] | None = None
    if settings.matrix_enabled:
        from app.repositories import chat as chat_repo
        try:
            ticket_chat_room = await chat_repo.get_room_by_ticket_id(ticket_id)
        except Exception as exc:
            log_error("Failed to load linked chat room for ticket", ticket_id=ticket_id, error=str(exc))

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
        "assigned_user": assigned_record,
        "ticket_replies": timeline_entries,
        "ticket_watchers": watchers,
        "ticket_attachments": formatted_attachments,
        "ticket_assets": ticket_assets,
        "relevant_services": relevant_services,
        "service_status_lookup": service_status_lookup,
        "matrix_chat_enabled": settings.matrix_enabled,
        "ticket_chat_room": ticket_chat_room,
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


async def _get_ticket_dashboard_reference_data() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    expires_at = _ticket_dashboard_reference_cache.get("expires_at")
    if isinstance(expires_at, datetime) and expires_at > now:
        return {
            "modules": list(_ticket_dashboard_reference_cache.get("modules") or []),
            "companies": list(_ticket_dashboard_reference_cache.get("companies") or []),
            "technicians": list(_ticket_dashboard_reference_cache.get("technicians") or []),
            "company_lookup": dict(_ticket_dashboard_reference_cache.get("company_lookup") or {}),
            "user_lookup": dict(_ticket_dashboard_reference_cache.get("user_lookup") or {}),
        }

    async with _ticket_dashboard_reference_lock:
        expires_at = _ticket_dashboard_reference_cache.get("expires_at")
        if isinstance(expires_at, datetime) and expires_at > datetime.now(timezone.utc):
            return {
                "modules": list(_ticket_dashboard_reference_cache.get("modules") or []),
                "companies": list(_ticket_dashboard_reference_cache.get("companies") or []),
                "technicians": list(_ticket_dashboard_reference_cache.get("technicians") or []),
                "company_lookup": dict(_ticket_dashboard_reference_cache.get("company_lookup") or {}),
                "user_lookup": dict(_ticket_dashboard_reference_cache.get("user_lookup") or {}),
            }

        dashboard = await tickets_service.load_dashboard_state(
            status_filter=None,
            module_filter=None,
            limit=0,
            include_reference_data=True,
        )
        _ticket_dashboard_reference_cache.update(
            {
                "expires_at": datetime.now(timezone.utc) + timedelta(seconds=_TICKET_DASHBOARD_REFERENCE_TTL_SECONDS),
                "modules": list(dashboard.modules),
                "companies": list(dashboard.companies),
                "technicians": list(dashboard.technicians),
                "company_lookup": dict(dashboard.company_lookup),
                "user_lookup": dict(dashboard.user_lookup),
            }
        )
        return {
            "modules": list(dashboard.modules),
            "companies": list(dashboard.companies),
            "technicians": list(dashboard.technicians),
            "company_lookup": dict(dashboard.company_lookup),
            "user_lookup": dict(dashboard.user_lookup),
        }


async def _render_tickets_dashboard(
    request: Request,
    user: dict[str, Any],
    *,
    success_message: str | None = None,
    error_message: str | None = None,
    phone_number: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    # If phone number is provided, search by phone
    if phone_number:
        phone_number_stripped = phone_number.strip()
        if phone_number_stripped:
            try:
                company_memberships = await company_access.list_accessible_companies(user)
                available_company_ids: list[int] = []
                for entry in company_memberships:
                    company_id = entry.get("company_id")
                    try:
                        available_company_ids.append(int(company_id))
                    except (TypeError, ValueError):
                        continue

                active_company_id = getattr(request.state, "active_company_id", None)
                active_company_ids: list[int] = []
                if active_company_id is not None:
                    try:
                        active_company_ids = [int(active_company_id)]
                    except (TypeError, ValueError):
                        active_company_ids = []
                if not active_company_ids:
                    active_company_ids = available_company_ids

                phone_tickets = await tickets_repo.list_tickets_by_requester_phone(
                    phone_number_stripped,
                    limit=_PHONE_SEARCH_LIMIT,
                    user_id=user.get("id"),
                    company_ids=active_company_ids or None,
                )
                # Get minimal dashboard state with only the phone search results
                dashboard = await tickets_service.load_dashboard_state(
                    status_filter=None,
                    module_filter=None,
                    limit=0,  # Don't load default tickets, we'll use phone search results
                    include_reference_data=False,
                )
                # Replace the tickets with phone search results
                dashboard.tickets = phone_tickets
                dashboard.total = len(phone_tickets)
                
                # Add info message about phone search
                if phone_tickets:
                    success_message = f"Found {len(phone_tickets)} ticket(s) for phone number {phone_number_stripped}"
                else:
                    error_message = f"No tickets found for phone number {phone_number_stripped}"
            except Exception as exc:
                log_error(
                    "Error searching tickets by phone number",
                    exc=exc,
                    event="tickets.phone_search_failed",
                    request_id=_get_request_id(request),
                    path=request.url.path,
                    user_id=user.get("id"),
                    phone_number_provided=True,
                )
                error_message = "Failed to search tickets by phone number. Please try again."
                # Load normal dashboard on error
                dashboard = await tickets_service.load_dashboard_state(
                    status_filter=None,
                    module_filter=None,
                    limit=200,
                    include_reference_data=False,
                )
        else:
            # Empty phone number after stripping, load normal dashboard
            dashboard = await tickets_service.load_dashboard_state(
                status_filter=None,
                module_filter=None,
                limit=200,
                include_reference_data=False,
            )
    else:
        # Normal dashboard load without phone search
        dashboard = await tickets_service.load_dashboard_state(
            status_filter=None,
            module_filter=None,
            limit=200,
            include_reference_data=False,
        )
    reference_data = await _get_ticket_dashboard_reference_data()
    dashboard_endpoint = "/api/tickets/dashboard"
    status_definitions_payload = [
        {
            "tech_status": definition.tech_status,
            "tech_label": definition.tech_label,
            "public_status": definition.public_status,
            "is_default": definition.is_default,
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
    ticket_ids = [int(t.get("id")) for t in dashboard.tickets if t.get("id") is not None]
    ticket_time_lookup = await tickets_repo.get_time_totals_by_ticket_ids(ticket_ids)
    extra = {
        "title": "Ticketing workspace",
        "tickets": dashboard.tickets,
        "ticket_total": dashboard.total,
        "ticket_status_counts": dashboard.status_counts,
        "ticket_available_statuses": dashboard.available_statuses,
        "ticket_status_definitions": status_definitions_payload,
        "ticket_status_label_map": status_label_map,
        "ticket_public_status_map": public_status_map,
        "ticket_modules": reference_data["modules"],
        "ticket_company_options": reference_data["companies"],
        "ticket_user_options": reference_data["technicians"],
        "ticket_company_lookup": reference_data["company_lookup"],
        "ticket_user_lookup": reference_data["user_lookup"],
        "ticket_labour_types": labour_types,
        "ticket_time_lookup": ticket_time_lookup,
        "can_bulk_delete_tickets": bool(user.get("is_super_admin")),
        "success_message": success_message,
        "error_message": error_message,
        "ticket_dashboard_endpoint": dashboard_endpoint,
        "ticket_refresh_topics": ["tickets"],
    }
    response = await _render_template("admin/tickets.html", request, user, extra=extra)
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
            base_rmm_url = str(
                tactical_settings.get("base_rmm_url")
                or tactical_settings.get("base_url")
                or ""
            ).strip()
            if base_rmm_url:
                tactical_base_url = base_rmm_url.rstrip("/")

    hudu_module = next((module for module in modules if module.get("slug") == "hudu"), None)
    hudu_base_url = ""
    hudu_company_url = ""
    if hudu_module and hudu_module.get("enabled"):
        hudu_settings = hudu_module.get("settings") or {}
        if isinstance(hudu_settings, Mapping):
            hudu_base_url = str(hudu_settings.get("base_url") or "").strip().rstrip("/")
        if hudu_base_url and company and company.get("hudu_id"):
            from app.services import hudu as hudu_service
            from app.services.hudu import HuduConfigurationError
            try:
                hudu_company_url = await hudu_service.get_company_url(str(company["hudu_id"])) or ""
            except HuduConfigurationError:
                # Hudu not configured - fall back to constructing URL from base_url
                hudu_company_url = f"{hudu_base_url}/companies/{company['hudu_id']}"
            except Exception as _exc:
                log_error(
                    "Failed to resolve Hudu company URL, using fallback",
                    company_id=ticket_company_id,
                    hudu_id=company.get("hudu_id"),
                    error=str(_exc),
                )
                hudu_company_url = f"{hudu_base_url}/companies/{company['hudu_id']}"

    # Resolve Solidtime project link / timer URL for the top-right action menu.
    solidtime_links: dict[str, Any] = {
        "enabled": False,
        "project_url": "",
        "timer_url": "",
        "project_id": "",
        "organization_id": "",
        "last_synced_at": None,
        "sync_status": "",
    }
    try:
        from app.services import solidtime as _solidtime_service

        solidtime_links = await _solidtime_service.get_ticket_links(int(ticket_id))
    except Exception as _exc:  # pragma: no cover - defensive logging
        log_error(
            "Failed to resolve Solidtime ticket links",
            ticket_id=ticket_id,
            error=str(_exc),
        )

    ordered_replies = list(reversed(replies))

    # Per-recipient delivery counts so the delivery-status badge in the admin
    # ticket detail can be rendered as a click trigger when the email had
    # more than one recipient (To/CC/BCC).
    from app.services import email_recipients as _email_recipients_svc

    admin_reply_recipient_counts: dict[int, int] = {}
    try:
        _admin_reply_ids = [r.get("id") for r in ordered_replies if r.get("id") is not None]
        admin_reply_recipient_counts = await _email_recipients_svc.get_recipient_count_map(_admin_reply_ids)
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to load per-reply recipient counts (admin)", error=str(exc))
        admin_reply_recipient_counts = {}

    # Fetch call recordings linked to this ticket
    from app.repositories import call_recordings as call_recordings_repo
    call_recordings = await call_recordings_repo.list_ticket_call_recordings(ticket_id)

    attachment_records: list[Mapping[str, Any]] = []
    try:
        attachment_records = await attachments_repo.list_attachments(ticket_id)
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error(
            "Failed to load ticket attachments",
            ticket_id=ticket_id,
            error=str(exc),
        )
        attachment_records = []

    formatted_attachments: list[dict[str, Any]] = []
    for attachment in attachment_records:
        uploaded_at = attachment.get("uploaded_at")
        uploaded_iso = _format_attachment_uploaded_iso(uploaded_at)
        try:
            file_size = int(attachment.get("file_size", 0) or 0)
        except (TypeError, ValueError):
            file_size = 0

        formatted_attachments.append(
            {
                **attachment,
                "uploaded_iso": uploaded_iso,
                "file_size": file_size,
            }
        )

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
        
        # Get email tracking status if available
        email_tracking_id = reply.get("email_tracking_id")
        email_opened_at = reply.get("email_opened_at")
        email_open_count = reply.get("email_open_count", 0)
        email_sent_at = reply.get("email_sent_at")
        email_delivered_at = reply.get("email_delivered_at")
        email_bounced_at = reply.get("email_bounced_at")
        smtp2go_message_id = reply.get("smtp2go_message_id")
        
        has_tracking = email_tracking_id is not None or smtp2go_message_id is not None
        is_email_opened = email_opened_at is not None
        is_email_delivered = email_delivered_at is not None
        is_email_bounced = email_bounced_at is not None
        try:
            recipient_count_for_reply = int(admin_reply_recipient_counts.get(int(reply.get("id")), 0))
        except (TypeError, ValueError):
            recipient_count_for_reply = 0

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
                "email_tracking_id": email_tracking_id,
                "email_sent_at": email_sent_at,
                "email_opened_at": email_opened_at,
                "email_open_count": email_open_count,
                "email_delivered_at": email_delivered_at,
                "email_bounced_at": email_bounced_at,
                "smtp2go_message_id": smtp2go_message_id,
                "has_email_tracking": has_tracking,
                "is_email_opened": is_email_opened,
                "is_email_delivered": is_email_delivered,
                "is_email_bounced": is_email_bounced,
                "recipient_count": recipient_count_for_reply,
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

    # Find relevant service statuses based on AI tag matching
    from app.services import service_status as service_status_service
    relevant_services: list[dict[str, Any]] = []
    if ticket_ai_tags:
        relevant_services = await service_status_service.find_relevant_services_for_ticket(
            ticket_ai_tags=ticket_ai_tags,
            company_id=ticket_company_id,
        )
    
    # Create service status lookup for consistent styling
    service_status_lookup = {entry["value"]: entry for entry in service_status_service.STATUS_DEFINITIONS}

    # Fetch linked chat room for this ticket (if matrix chat is enabled)
    ticket_chat_room: dict[str, Any] | None = None
    if settings.matrix_enabled:
        from app.repositories import chat as chat_repo
        try:
            ticket_chat_room = await chat_repo.get_room_by_ticket_id(ticket_id)
        except Exception as exc:
            log_error("Failed to load linked chat room for ticket", ticket_id=ticket_id, error=str(exc))

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
        "ticket_attachments": formatted_attachments,
        "ticket_labour_types": labour_types,
        "ticket_billable_minutes": total_billable_minutes,
        "ticket_non_billable_minutes": total_non_billable_minutes,
        "ticket_available_statuses": available_statuses,
        "ticket_status_definitions": [
            {
                "tech_status": definition.tech_status,
                "tech_label": definition.tech_label,
                "public_status": definition.public_status,
                "is_default": definition.is_default,
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
        "hudu_base_url": hudu_base_url,
        "hudu_company_url": hudu_company_url,
        "solidtime_links": solidtime_links,
        "can_delete_ticket": bool(user.get("is_super_admin")),
        "relevant_kb_articles": relevant_articles,
        "relevant_services": relevant_services,
        "service_status_lookup": service_status_lookup,
        "ticket_chat_room": ticket_chat_room,
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


async def admin_create_issue(request: Request):
    current_user, redirect = await _require_issue_tracker_access(request)
    if redirect:
        return redirect

    form = await request.form()
    name = str(form.get("name", "")).strip()
    description = str(form.get("description", "")).strip() or None
    initial_status = str(form.get("initialStatus", issues_service.DEFAULT_STATUS)).strip()

    if not name:
        url = f"/admin/issues?error={quote('Issue name is required.')}"
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)

    try:
        await issues_service.ensure_issue_name_available(name)
    except ValueError as exc:
        url = f"/admin/issues?error={quote(str(exc))}"
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)

    user_id = _get_current_user_id(current_user)
    try:
        issue_record = await issues_repo.create_issue(
            name=name,
            description=description,
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

    company_ids_raw = form.getlist("company_ids")
    selected_companies: list[int] = []
    for raw in company_ids_raw:
        try:
            selected_companies.append(int(raw))
        except (TypeError, ValueError):
            continue

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


async def admin_update_issue(issue_id: int, request: Request):
    current_user, redirect = await _require_issue_tracker_access(request)
    if redirect:
        return redirect

    issue = await issues_repo.get_issue_by_id(issue_id)
    if not issue:
        url = f"/admin/issues?error={quote('Issue not found.')}"
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)

    form = await request.form()
    name = str(form.get("name", "")).strip()
    description = str(form.get("description", "")).strip() or None
    new_company_status = str(form.get("newCompanyStatus", issues_service.DEFAULT_STATUS)).strip()

    if not name:
        url = f"/admin/issues?issueId={issue_id}&error={quote('Issue name is required.')}"
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)

    updates: dict[str, Any] = {}
    if name != (issue.get("name") or ""):
        try:
            await issues_service.ensure_issue_name_available(name, exclude_issue_id=issue_id)
        except ValueError as exc:
            url = f"/admin/issues?issueId={issue_id}&error={quote(str(exc))}"
            return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)
        updates["name"] = name

    if description != (issue.get("description") or None):
        updates["description"] = description

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

    new_company_ids_raw = form.getlist("newCompanyIds")
    selected_companies: list[int] = []
    for raw in new_company_ids_raw:
        try:
            selected_companies.append(int(raw))
        except (TypeError, ValueError):
            continue

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


async def admin_update_issue_assignment_status(
    issue_id: int,
    assignment_id: int,
    request: Request,
):
    current_user, redirect = await _require_issue_tracker_access(request)
    if redirect:
        return redirect

    form = await request.form()
    status_value = str(form.get("status", "")).strip()
    return_url = str(form.get("returnUrl", "")).strip() or None

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

    destination = _sanitize_local_redirect_target(
        return_url,
        fallback=f"/admin/issues?issueId={issue_id}&success={quote('Status updated.')}",
        allowed_prefixes=("/admin/issues",),
    )
    if "success=" not in destination:
        separator = "&" if "?" in destination else "?"
        destination = f"{destination}{separator}success={quote('Status updated.')}"
    return RedirectResponse(url=destination, status_code=status.HTTP_303_SEE_OTHER)


async def admin_delete_issue_assignment(
    issue_id: int,
    assignment_id: int,
    request: Request,
):
    current_user, redirect = await _require_issue_tracker_access(request)
    if redirect:
        return redirect

    form = await request.form()
    return_url = str(form.get("returnUrl", "")).strip() or None

    await issues_repo.delete_assignment(assignment_id)
    log_info(
        "Issue assignment removed",
        issue_id=issue_id,
        assignment_id=assignment_id,
        removed_by=_get_current_user_id(current_user),
    )

    destination = _sanitize_local_redirect_target(
        return_url,
        fallback=f"/admin/issues?issueId={issue_id}",
        allowed_prefixes=("/admin/issues",),
    )
    separator = "&" if "?" in destination else "?"
    destination = f"{destination}{separator}success={quote('Assignment removed.')}"
    return RedirectResponse(url=destination, status_code=status.HTTP_303_SEE_OTHER)


async def admin_create_ticket_reply(ticket_id: int, request: Request):
    """Compatibility wrapper for direct imports in tests and helper callers."""
    from app.features.tickets.admin_routes import admin_create_ticket_reply as _pack_handler

    return await _pack_handler(ticket_id, request)


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
        "executionOrder": int(automation.get("execution_order") or 0),
        "cadence": str(automation.get("cadence") or ""),
        "cronExpression": str(automation.get("cron_expression") or ""),
        "runOnce": bool(automation.get("run_once", False)),
        "scheduledTime": "",
        "triggerEvent": str(automation.get("trigger_event") or ""),
        "triggerFiltersRaw": "",
        "actionModule": str(automation.get("action_module") or ""),
        "actionPayloadRaw": "",
    }
    scheduled_time = automation.get("scheduled_time")
    if scheduled_time and isinstance(scheduled_time, datetime):
        # Format as HTML datetime-local input format (YYYY-MM-DDTHH:MM)
        # Convert to local timezone for display
        local_time = scheduled_time.astimezone()
        values["scheduledTime"] = local_time.strftime("%Y-%m-%dT%H:%M")
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
    def _get_str_value(key: str) -> str:
        value = form.get(key)
        if value is None:
            return ""
        return str(value)

    kind_normalised = "event" if str(kind).lower() == "event" else "scheduled"
    name = _get_str_value("name").strip()
    description_value = _get_str_value("description").strip()
    status_raw = _get_str_value("status").strip().lower()
    status_value = "active" if status_raw == "active" else "inactive"
    execution_order_raw = _get_str_value("executionOrder").strip()
    try:
        execution_order = max(0, int(execution_order_raw)) if execution_order_raw else 0
    except (ValueError, TypeError):
        execution_order = 0
    cadence_raw = _get_str_value("cadence").strip()
    cron_raw = _get_str_value("cronExpression").strip()
    run_once_raw = _get_str_value("runOnce").strip().lower()
    run_once = run_once_raw in ("true", "1", "yes", "on")
    scheduled_time_raw = _get_str_value("scheduledTime").strip()
    trigger_event_raw = _get_str_value("triggerEvent").strip()
    trigger_filters_raw = _get_str_value("triggerFilters").strip()
    trigger_filters_mode_raw = _get_str_value("triggerFiltersMode").strip().lower()
    trigger_filters_mode = "advanced" if trigger_filters_mode_raw == "advanced" else "builder"
    action_module_raw = _get_str_value("actionModule").strip()
    action_payload_raw = _get_str_value("actionPayload").strip()

    form_state = {
        "name": name,
        "description": description_value,
        "status": status_value,
        "executionOrder": execution_order,
        "cadence": cadence_raw,
        "cronExpression": cron_raw,
        "runOnce": run_once,
        "scheduledTime": scheduled_time_raw,
        "triggerEvent": trigger_event_raw,
        "triggerFiltersRaw": trigger_filters_raw,
        "triggerFiltersMode": trigger_filters_mode,
        "actionModule": action_module_raw,
        "actionPayloadRaw": action_payload_raw,
    }

    if not name:
        return None, form_state, "Enter an automation name.", status.HTTP_400_BAD_REQUEST

    cadence = cadence_raw or None
    cron_expression = cron_raw or None
    trigger_event = trigger_event_raw or None
    action_module = action_module_raw or None
    
    # Parse scheduled_time if provided
    scheduled_time = None
    if kind_normalised == "scheduled" and run_once:
        # One-time scheduling requires a scheduled time
        if not scheduled_time_raw:
            return (
                None,
                form_state,
                "Scheduled time is required for one-time automations.",
                status.HTTP_400_BAD_REQUEST,
            )
        try:
            # Parse datetime-local format (YYYY-MM-DDTHH:MM)
            scheduled_time = datetime.fromisoformat(scheduled_time_raw)
            # Treat as local time, convert to UTC for storage
            if scheduled_time.tzinfo is None:
                # Assume local timezone
                local_tz = datetime.now().astimezone().tzinfo
                scheduled_time = scheduled_time.replace(tzinfo=local_tz).astimezone(timezone.utc)
        except (ValueError, TypeError):
            return (
                None,
                form_state,
                "Invalid scheduled time format. Use YYYY-MM-DDTHH:MM format.",
                status.HTTP_400_BAD_REQUEST,
            )

    try:
        trigger_filters = json.loads(trigger_filters_raw) if trigger_filters_raw else None
    except json.JSONDecodeError:
        invalid_section = "Advanced JSON trigger filters" if trigger_filters_mode == "advanced" else "Trigger filter builder payload"
        return (
            None,
            form_state,
            f"{invalid_section} is invalid JSON.",
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
            try:
                modules_service.validate_action_payload(module_value, payload_value)
            except ValueError as exc:
                return (
                    None,
                    form_state,
                    f"Trigger action {index}: {exc}",
                    status.HTTP_400_BAD_REQUEST,
                )
            action_entry: dict[str, Any] = {"module": module_value, "payload": payload_value}
            raw_order = entry.get("order")
            if raw_order is not None:
                try:
                    action_entry["order"] = int(raw_order)
                except (TypeError, ValueError):
                    pass
            note_value = str(entry.get("note") or "").strip()
            if note_value:
                action_entry["note"] = note_value
            normalised_actions.append(action_entry)
        updated_payload = dict(action_payload)
        updated_payload["actions"] = normalised_actions
        action_payload = updated_payload
        action_module = normalised_actions[0]["module"] if normalised_actions else None
        form_state["actionPayloadRaw"] = json.dumps(action_payload)
        form_state["actionModule"] = action_module or ""
    elif action_module and isinstance(action_payload, dict):
        try:
            modules_service.validate_action_payload(action_module, action_payload)
        except ValueError as exc:
            return (
                None,
                form_state,
                str(exc),
                status.HTTP_400_BAD_REQUEST,
            )

    data = {
        "name": name,
        "description": description_value or None,
        "kind": kind_normalised,
        "execution_order": execution_order,
        "cadence": cadence if kind_normalised == "scheduled" else None,
        "cron_expression": cron_expression if kind_normalised == "scheduled" else None,
        "scheduled_time": scheduled_time,
        "run_once": run_once,
        "trigger_event": trigger_event,
        "trigger_filters": trigger_filters,
        "action_module": action_module,
        "action_payload": action_payload,
        "status": status_value,
    }

    return data, form_state, None, status.HTTP_200_OK


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


async def _render_modules_dashboard(
    request: Request,
    user: dict[str, Any],
    *,
    success_message: str | None = None,
    error_message: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    modules = await modules_service.list_modules()
    uptimekuma_webhook_url = str(request.url_for("uptimekuma_receive_alert").replace(scheme="https"))
    trello_webhook_url = str(request.url_for("trello_webhook_receive").replace(scheme="https"))
    # Build the Xero OAuth redirect/callback URL so it matches what the app
    # actually sends to Xero during the OAuth flow. Prefer the configured
    # PORTAL_URL (which already includes the correct https scheme) and fall
    # back to the incoming request URL forced to https so the value shown to
    # admins is never http when the app is reverse-proxied behind TLS.
    xero_callback_url = _build_xero_redirect_uri()
    if xero_callback_url.startswith("/"):
        xero_callback_url = str(
            request.url_for("xero_callback").replace(scheme="https")
        )
    from app.services import huntress as huntress_service
    huntress_credentials = huntress_service.credentials_status()
    extra = {
        "title": "Integration modules",
        "modules": modules,
        "uptimekuma_webhook_url": uptimekuma_webhook_url,
        "trello_webhook_url": trello_webhook_url,
        "xero_callback_url": xero_callback_url,
        "huntress_credentials": huntress_credentials,
        "success_message": success_message,
        "error_message": error_message,
    }
    response = await _render_template("admin/modules.html", request, user, extra=extra)
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


@app.get("/admin/feature-packs", response_class=HTMLResponse)
async def admin_feature_packs_page(
    request: Request,
    success: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    """Admin UI wrapping the feature pack registry.

    Lists every loaded ``app/features/<slug>/`` pack with its current
    version, last-loaded timestamp, in-flight request count, last
    reload duration, and last error.  Each row has a Reload button
    that POSTs to ``/api/features/{slug}/reload`` (CSRF-protected,
    super-admin only) — the same API documented in
    ``docs/feature_packs.md``.
    """

    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    packs = sorted(feature_registry.list(), key=lambda p: p["slug"])

    extra = {
        "title": "Feature packs",
        "packs": packs,
        "success_message": _sanitize_message(success),
        "error_message": _sanitize_message(error),
    }
    return await _render_template(
        "admin/feature_packs.html", request, current_user, extra=extra
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


def _form_bool(form: Mapping[str, Any], key: str) -> bool:
    value = form.get(key)
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "off"}
    return bool(value)


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


async def admin_pull_companies_from_tactical_rmm(request: Request):
    current_user, redirect = await _require_super_admin_page(request)
    if redirect:
        return redirect

    try:
        await modules_service.ensure_tacticalrmm_ready()
    except ValueError as exc:
        log_error("Unable to pull Tactical RMM companies", error=str(exc))
        return await _render_modules_dashboard(
            request,
            current_user,
            error_message=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to pull Tactical RMM companies", error=str(exc))
        return await _render_modules_dashboard(
            request,
            current_user,
            error_message="Unable to pull companies from Tactical RMM. Please verify the module configuration.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    task_id = uuid4().hex

    async def _on_success(summary: Mapping[str, Any]) -> None:
        fetched = int(summary.get("fetched") or 0)
        created = int(summary.get("created") or 0)
        updated = int(summary.get("updated") or 0)
        skipped = int(summary.get("skipped") or 0)
        errors = summary.get("errors") or []
        error_count = len(errors)

        log_info(
            "Tactical RMM company pull completed",
            task_id=task_id,
            fetched=fetched,
            created=created,
            updated=updated,
            skipped=skipped,
            errors=error_count,
        )

        if error_count:
            example = errors[0]
            detail = example.get("error") or "Unknown error"
            log_error(
                "Tactical RMM pull encountered errors",
                task_id=task_id,
                error_count=error_count,
                example=detail,
            )

    async def _on_error(exc: Exception) -> None:
        log_error(
            "Tactical RMM company pull failed",
            task_id=task_id,
            error=str(exc),
        )

    background_tasks.queue_background_task(
        lambda: modules_service.pull_companies_from_tacticalrmm(),
        task_id=task_id,
        description="tacticalrmm-company-pull",
        on_complete=_on_success,
        on_error=_on_error,
    )

    log_info(
        "Queued Tactical RMM company pull",
        task_id=task_id,
        user_id=current_user.get("id"),
        request_path=str(request.url),
    )

    success_message = f"Tactical RMM company pull queued. Task ID: {task_id[:8]}"
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
        "plausible_config": {"enabled": False},
    }
    return templates.TemplateResponse(context["request"], "auth/login.html", context)


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
        "plausible_config": {"enabled": False},
    }
    return templates.TemplateResponse(context["request"], "auth/register.html", context)


@app.get("/health")
async def health_check():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


# ---------------------------------------------------------------------------
# Liveness and readiness probes
# ---------------------------------------------------------------------------
# ``/healthz`` is the cheap "is the process alive" check that nginx,
# systemd, and load balancers can poll constantly.  It must succeed even
# before the database is reachable.
#
# ``/readyz`` is the deeper "is this instance ready to serve traffic"
# check.  It returns 503 until startup has finished, the database is
# reachable, and every registered feature pack is in a healthy state.
# nginx uses this to decide when a blue/green instance is back online
# during a rolling deploy (see ``docs/zero_downtime_upgrades.md``).
_app_ready: bool = False

# Optional dev-only file watcher; started in ``on_startup`` when the
# ``FEATURE_PACK_WATCH`` setting is true.  Held module-level so the
# shutdown hook can cancel it.
_feature_pack_watcher: Any = None


@app.get("/healthz")
async def liveness_probe() -> dict[str, str]:
    """Liveness: the process is running and event loop is responsive."""

    return {"status": "ok"}


@app.get("/readyz")
async def readiness_probe() -> JSONResponse:
    """Readiness: startup has finished, DB is reachable, packs healthy."""

    checks: dict[str, str] = {"startup": "ok" if _app_ready else "pending"}
    ok = _app_ready

    if _app_ready:
        try:
            await db.fetch_one("SELECT 1")
            checks["database"] = "ok"
        except Exception as exc:
            checks["database"] = f"error: {exc.__class__.__name__}"
            ok = False

    if feature_registry.list() and not feature_registry.all_loaded():
        checks["feature_packs"] = "degraded"
        ok = False
    else:
        checks["feature_packs"] = "ok"

    status_code = HTTPStatus.OK if ok else HTTPStatus.SERVICE_UNAVAILABLE
    return JSONResponse(
        status_code=status_code,
        content={"status": "ok" if ok else "not_ready", "checks": checks},
    )


# Chat page routes have been migrated to the ``chat`` feature pack
# at ``app/features/chat/``.  They are loaded on startup via the
# ``FEATURE_PACKS`` setting and can be hot-reloaded without restarting
# the application.
