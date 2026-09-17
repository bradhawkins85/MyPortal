from __future__ import annotations

import json
import os
from copy import deepcopy
from typing import Any

from app.repositories import integration_modules as module_repo
from app.services.module_constants import ALWAYS_ON_TICKET_ACTION_MODULE_SLUGS


# Keep these defaults aligned with the matching ``DEFAULT_MODULES[*]["settings"]``
# entries in ``app.services.modules``. This lightweight helper intentionally
# covers only the modules that need to read runtime configuration without
# importing the higher-level module orchestration service.
_DEFAULT_SETTINGS: dict[str, dict[str, Any]] = {
    "call-recordings": {
        "recordings_path": "/var/lib/myportal/call_recordings",
        "phone_system_type": "generic",
    },
    "ollama": {
        "provider": "ollama",
        "base_url": "http://127.0.0.1:11434",
        "model": "llama3",
        "prompt": "",
        "api_key": "",
    },
    "plausible": {
        "base_url": "",
        "site_domain": "",
        "api_key": "",
        "track_opens": True,
        "track_clicks": True,
        "send_to_plausible": False,
        "track_pageviews": False,
        "pepper": "",
        "send_pii": False,
    },
    "smtp2go": {
        "api_key": "",
        "enable_tracking": True,
        "track_opens": True,
        "track_clicks": True,
        "webhook_secret": "",
        "disable_webhook_signature_verification": False,
        "manage_url": "/admin/modules/smtp2go",
        "rate_limit_max_retries": 3,
        "retry_backoff_seconds": 60,
        "not_engaged_delay_seconds": 86400,
        "ab_campaigns": [],
    },
    "solidtime": {
        "base_url": "",
        "api_token": "",
        "organization_id": "",
        "default_client_id": "",
        "sync_tickets_to_projects": False,
        "sync_projects_to_tickets": False,
        "sync_time_entries_to_solidtime": False,
        "sync_time_entries_from_solidtime": False,
        "only_billable_to_solidtime": False,
        "labour_type_to_task": False,
        "webhook_secret": "",
        "rate_limit_per_minute": 120,
        "reconcile_interval_minutes": 15,
        "monitor_successful_api_requests": False,
        "manage_url": "/admin/modules/solidtime",
    },
}

def _ensure_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() not in {"", "0", "false", "no", "off"}


def _coerce_int(
    value: Any,
    default: int,
    *,
    minimum: int = 0,
    maximum: int = 10**9,
) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    if result < minimum:
        return minimum
    if result > maximum:
        return maximum
    return result


def _default_settings_for_slug(slug: str) -> dict[str, Any]:
    return deepcopy(_DEFAULT_SETTINGS.get(slug, {}))


def _merge_settings(
    base: dict[str, Any],
    overrides: dict[str, Any] | None,
) -> dict[str, Any]:
    merged = deepcopy(base)
    if not overrides:
        return merged
    for key, value in overrides.items():
        if (
            isinstance(value, dict)
            and isinstance(merged.get(key), dict)
        ):
            merged[key] = _merge_settings(merged[key], value)
        else:
            merged[key] = value
    return merged


def _coerce_settings_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return dict(parsed)
    return {}


def _resolve_module_settings(slug: str, settings: dict[str, Any] | None) -> dict[str, Any]:
    resolved = _merge_settings(_default_settings_for_slug(slug), settings)
    if slug == "ollama":
        env_base_url = str(os.getenv("OLLAMA_BASE_URL", "")).strip()
        env_api_key = str(os.getenv("OLLAMA_API_KEY", "")).strip()
        if env_base_url:
            resolved["base_url"] = env_base_url.rstrip("/")
        if env_api_key:
            resolved["api_key"] = env_api_key
    elif slug == "smtp2go":
        env_api_key = str(os.getenv("SMTP2GO_API_KEY", "")).strip()
        env_webhook_secret = str(os.getenv("SMTP2GO_WEBHOOK_SECRET", "")).strip()
        if env_api_key:
            resolved["api_key"] = env_api_key
        if env_webhook_secret:
            resolved["webhook_secret"] = env_webhook_secret
        resolved["enable_tracking"] = _ensure_bool(
            resolved.get("enable_tracking"), True
        )
        resolved["track_opens"] = _ensure_bool(resolved.get("track_opens"), True)
        resolved["track_clicks"] = _ensure_bool(resolved.get("track_clicks"), True)
        resolved["disable_webhook_signature_verification"] = _ensure_bool(
            resolved.get("disable_webhook_signature_verification"), False
        )
        resolved["rate_limit_max_retries"] = _coerce_int(
            resolved.get("rate_limit_max_retries"),
            3,
            minimum=0,
            maximum=10_000,
        )
        resolved["retry_backoff_seconds"] = _coerce_int(
            resolved.get("retry_backoff_seconds"),
            60,
            minimum=0,
            maximum=86400,
        )
        resolved["not_engaged_delay_seconds"] = _coerce_int(
            resolved.get("not_engaged_delay_seconds"),
            86400,
            minimum=0,
            maximum=31_536_000,
        )
    elif slug == "solidtime":
        for env_key, setting_key in (
            ("SOLIDTIME_BASE_URL", "base_url"),
            ("SOLIDTIME_API_TOKEN", "api_token"),
            ("SOLIDTIME_ORGANIZATION_ID", "organization_id"),
            ("SOLIDTIME_DEFAULT_CLIENT_ID", "default_client_id"),
        ):
            env_value = str(os.getenv(env_key, "")).strip()
            if env_value:
                resolved[setting_key] = env_value
        for env_key, setting_key in (
            ("SOLIDTIME_SYNC_TICKETS_TO_PROJECTS", "sync_tickets_to_projects"),
            ("SOLIDTIME_SYNC_PROJECTS_TO_TICKETS", "sync_projects_to_tickets"),
            ("SOLIDTIME_SYNC_TIME_ENTRIES_TO_SOLIDTIME", "sync_time_entries_to_solidtime"),
            ("SOLIDTIME_SYNC_TIME_ENTRIES_FROM_SOLIDTIME", "sync_time_entries_from_solidtime"),
            ("SOLIDTIME_ONLY_BILLABLE_TO_SOLIDTIME", "only_billable_to_solidtime"),
            ("SOLIDTIME_LABOUR_TYPE_TO_TASK", "labour_type_to_task"),
            ("SOLIDTIME_MONITOR_SUCCESSFUL_API_REQUESTS", "monitor_successful_api_requests"),
        ):
            env_value = os.getenv(env_key)
            if env_value is not None and str(env_value).strip():
                resolved[setting_key] = _ensure_bool(env_value, False)
        rate_limit_value = os.getenv("SOLIDTIME_RATE_LIMIT_PER_MINUTE")
        if rate_limit_value is None:
            resolved["rate_limit_per_minute"] = _coerce_int(
                resolved.get("rate_limit_per_minute"),
                120,
                minimum=1,
                maximum=600,
            )
        else:
            resolved["rate_limit_per_minute"] = _coerce_int(
                rate_limit_value,
                120,
                minimum=1,
                maximum=600,
            )
        reconcile_value = os.getenv("SOLIDTIME_RECONCILE_INTERVAL_MINUTES")
        if reconcile_value is None:
            resolved["reconcile_interval_minutes"] = _coerce_int(
                resolved.get("reconcile_interval_minutes"),
                15,
                minimum=5,
                maximum=1440,
            )
        else:
            resolved["reconcile_interval_minutes"] = _coerce_int(
                reconcile_value,
                15,
                minimum=5,
                maximum=1440,
            )
    return resolved


def _redact_module_settings(slug: str, settings: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(settings)
    for field in {
        "ollama": ("api_key",),
        "plausible": ("api_key", "pepper"),
        "smtp2go": ("api_key", "webhook_secret"),
        "solidtime": ("api_token", "webhook_secret"),
    }.get(slug, ()):
        if field in redacted and redacted[field]:
            redacted[field] = "***REDACTED***"
    return redacted


async def get_module(slug: str, *, redact: bool = True) -> dict[str, Any] | None:
    module = await module_repo.get_module(slug)
    if not module:
        return None
    resolved = dict(module)
    resolved["settings"] = _resolve_module_settings(
        slug,
        _coerce_settings_payload(module.get("settings")),
    )
    if redact:
        resolved["settings"] = _redact_module_settings(slug, resolved["settings"])
    return resolved


async def list_modules() -> list[dict[str, Any]]:
    """Return runtime-resolved modules for UI/service consumers.

    This mirrors ``app.services.modules.list_modules()`` so callers can obtain
    the same redacted module inventory without importing the higher-level
    orchestration service. Always-on ticket action pseudo-modules are excluded
    here for parity with that existing UI-facing listing behavior.
    """

    modules = await module_repo.list_modules()
    resolved_modules: list[dict[str, Any]] = []
    for module in modules:
        slug = str(module.get("slug") or "").strip()
        if slug in ALWAYS_ON_TICKET_ACTION_MODULE_SLUGS:
            continue
        resolved = dict(module)
        resolved["settings"] = _redact_module_settings(
            slug,
            _resolve_module_settings(
                slug,
                _coerce_settings_payload(module.get("settings")),
            ),
        )
        resolved_modules.append(resolved)
    return resolved_modules


async def get_module_settings(slug: str) -> dict[str, Any] | None:
    module = await get_module(slug, redact=False)
    if not module:
        return None
    return dict(module.get("settings") or {})
