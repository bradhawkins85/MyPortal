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
from app.services import system_state as system_state_service
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


@app.post("/m365/mailboxes/start-managed-folder-assistant", response_class=JSONResponse, tags=["Microsoft 365"])
async def start_m365_managed_folder_assistant(request: Request):
    """Start Managed Folder Assistant for a specific mailbox."""
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
    shared_mbs = await m365_service.get_shared_mailboxes(company_id)
    known_upns = {str(mb.get("user_principal_name") or "").strip().lower() for mb in user_mbs + shared_mbs}
    if upn.lower() not in known_upns:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mailbox not found")

    try:
        await m365_service.start_managed_folder_assistant(company_id, upn)
    except m365_service.M365Error:
        logger.exception("Failed to start Managed Folder Assistant for UPN %s", upn)
        return JSONResponse(
            {"error": "Unable to start Managed Folder Assistant at this time."},
            status_code=503,
        )
    log_info("M365 managed folder assistant requested", company_id=company_id, user_id=user.get("id"), upn=upn)
    return JSONResponse({"started": True})


@app.post("/m365/mailboxes/start-managed-folder-assistant/all", response_class=JSONResponse, tags=["Microsoft 365"])
async def start_m365_managed_folder_assistant_all(request: Request):
    """Start Managed Folder Assistant for all mailboxes in the tenant."""
    user, membership, company, company_id, redirect = await _load_license_context(request)
    if redirect:
        return JSONResponse({"error": "Authentication required"}, status_code=401)
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin privileges required")

    try:
        result = await m365_service.start_managed_folder_assistant_all_mailboxes(company_id)
    except m365_service.M365Error:
        logger.exception("Failed to start Managed Folder Assistant for all mailboxes")
        return JSONResponse(
            {"error": "Unable to start Managed Folder Assistant for all mailboxes at this time."},
            status_code=503,
        )
    log_info(
        "M365 managed folder assistant all-mailbox run requested",
        company_id=company_id,
        user_id=user.get("id"),
        started=result.get("started", 0),
        failed=result.get("failed", 0),
    )
    return JSONResponse({
        "started": int(result.get("started") or 0),
        "failed": int(result.get("failed") or 0),
    })


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
    return RedirectResponse(url="/admin/scheduled-tasks", status_code=status.HTTP_301_MOVED_PERMANENTLY)


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
    missing_company_ids: set[int] = set()
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
            company_name = company_lookup.get(company_key)
            if not company_name:
                company_name = f"Company #{company_key}"
                missing_company_ids.add(company_key)
            serialised_task["company_name"] = company_name
            serialised_task["company_edit_url"] = f"/admin/companies/{company_key}/edit"
        prepared_tasks.append(serialised_task)

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
        {"value": "sync_unifi_talk_recordings", "label": "Sync Unifi Talk recordings"},
        {"value": "queue_transcriptions", "label": "Queue transcriptions"},
        {"value": "process_transcription", "label": "Process transcription"},
    ]
    command_options = [o for o in command_options if o["value"] not in disabled_commands_global]
    existing_commands = {task.get("command") for task in tasks if task.get("command")}
    for command in sorted(existing_commands):
        if command and command not in {option["value"] for option in command_options} and command not in disabled_commands_global:
            command_options.append({"value": str(command), "label": str(command)})

    company_options = [{"value": "", "label": "All companies"}]
    for cid, cname in sorted(company_lookup.items(), key=lambda item: item[1].lower()):
        company_options.append({"value": str(cid), "label": cname})
    for cid in sorted(missing_company_ids):
        company_options.append({"value": str(cid), "label": f"Company #{cid}"})

    extra = {
        "title": "Scheduled Tasks",
        "tasks": prepared_tasks,
        "show_inactive": show_inactive,
        "success_message": success,
        "error_message": error,
        "upgrade_status": system_state_service.get_upgrade_status(),
        "command_options": command_options,
        "company_options": company_options,
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
