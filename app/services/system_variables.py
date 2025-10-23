from __future__ import annotations

import os
import platform
import socket
import sys
import time
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlparse

from app.core.config import get_settings, get_templates_config


_SENSITIVE_MARKERS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "PASS", "PRIVATE")


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return ", ".join(
            f"{str(key)}={_stringify(val)}" for key, val in sorted(value.items(), key=lambda item: str(item[0]))
        )
    if isinstance(value, (set, tuple, list)):
        return ", ".join(_stringify(item) for item in value)
    return str(value)


def _read_version() -> str | None:
    version_file = Path(__file__).resolve().parent.parent.parent / "version.txt"
    try:
        raw = version_file.read_text(encoding="utf-8")
    except FileNotFoundError:  # pragma: no cover - defensive guard
        return None
    value = raw.strip()
    return value or None


def _database_backend(settings) -> str:
    if settings.database_host and settings.database_user and settings.database_name:
        return "mysql"
    return "sqlite"


def _portal_origin(portal_url: str | None) -> tuple[str, str]:
    if not portal_url:
        return "", ""
    parsed = urlparse(portal_url)
    if not parsed.scheme or not parsed.netloc:
        return portal_url, ""
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return origin, parsed.hostname or ""


@lru_cache(maxsize=1)
def _static_variables() -> dict[str, str]:
    settings = get_settings()
    templates = get_templates_config()

    portal_url = str(settings.portal_url) if settings.portal_url else ""
    portal_origin, portal_hostname = _portal_origin(portal_url)

    allowed_origins = [str(origin) for origin in settings.allowed_origins]

    version = _read_version()

    static: dict[str, Any] = {
        "APP_NAME": settings.app_name,
        "APP_ENVIRONMENT": settings.environment,
        "APP_ENV": settings.environment,
        "APP_PORTAL_URL": portal_url,
        "APP_PORTAL_ORIGIN": portal_origin,
        "APP_PORTAL_HOSTNAME": portal_hostname,
        "APP_DEFAULT_TIMEZONE": settings.default_timezone,
        "APP_CRON_TIMEZONE": settings.default_timezone,
        "APP_ENABLE_CSRF": bool(settings.enable_csrf),
        "APP_ENABLE_AUTO_REFRESH": bool(settings.enable_auto_refresh),
        "APP_SWAGGER_UI_URL": settings.swagger_ui_url,
        "APP_SESSION_COOKIE_NAME": settings.session_cookie_name,
        "APP_ALLOWED_ORIGINS": allowed_origins,
        "APP_ALLOWED_ORIGIN_COUNT": len(allowed_origins),
        "APP_DATABASE_BACKEND": _database_backend(settings),
        "APP_REDIS_ENABLED": bool(settings.redis_url),
        "APP_SMTP_ENABLED": bool(settings.smtp_host),
        "APP_STOCK_FEED_ENABLED": bool(settings.stock_feed_url),
        "APP_SYNCRO_WEBHOOK_ENABLED": bool(settings.syncro_webhook_url),
        "APP_VERIFY_WEBHOOK_ENABLED": bool(settings.verify_webhook_url),
        "APP_LICENSES_WEBHOOK_ENABLED": bool(settings.licenses_webhook_url),
        "APP_SHOP_WEBHOOK_ENABLED": bool(settings.shop_webhook_url),
        "APP_SMS_ENDPOINT_CONFIGURED": bool(settings.sms_endpoint),
        "APP_PORTAL_CONFIGURED": bool(settings.portal_url),
        "APP_OPNFORM_BASE_URL": str(settings.opnform_base_url) if settings.opnform_base_url else "",
        "APP_FAIL2BAN_LOG_PATH": str(settings.fail2ban_log_path) if settings.fail2ban_log_path else "",
        "APP_MIGRATION_LOCK_TIMEOUT": settings.migration_lock_timeout,
        "APP_THEME": templates.theme_name,
        "APP_STATIC_PATH": templates.static_path,
        "APP_TEMPLATE_PATH": templates.template_path,
        "PYTHON_IMPLEMENTATION": platform.python_implementation(),
        "PYTHON_VERSION": platform.python_version(),
        "PYTHON_RUNTIME": f"{platform.python_implementation()} {platform.python_version()}",
        "SYSTEM_HOSTNAME": socket.gethostname(),
        "SYSTEM_FQDN": socket.getfqdn(),
        "SYSTEM_PLATFORM": platform.system(),
        "SYSTEM_PLATFORM_RELEASE": platform.release(),
        "SYSTEM_PLATFORM_VERSION": platform.version(),
        "SYSTEM_ARCHITECTURE": platform.machine(),
        "SYSTEM_PROCESSOR": platform.processor(),
        "SYSTEM_PATH_SEPARATOR": os.pathsep,
        "SYSTEM_LINE_SEPARATOR": "\\n" if os.linesep == "\n" else os.linesep,
        "SYSTEM_CWD": Path.cwd(),
        "SYSTEM_APP_ROOT": Path(__file__).resolve().parent.parent.parent,
        "SYSTEM_ENVIRONMENT_VARIABLE_COUNT": len(os.environ),
        "SYSTEM_PYTHON_EXECUTABLE": sys.executable,
    }

    if version:
        static["APP_VERSION"] = version

    return {key: _stringify(value) for key, value in static.items()}


def _runtime_variables() -> dict[str, str]:
    now_utc = datetime.now(timezone.utc)
    local_now = datetime.now().astimezone()
    tzname = time.tzname[0] if time.tzname else ""
    offset = local_now.utcoffset() or timezone.utc.utcoffset(now_utc)
    offset_minutes = int(offset.total_seconds() // 60) if offset else 0

    runtime: dict[str, Any] = {
        "NOW_UTC": now_utc.isoformat(),
        "SYSTEM_TIME_UTC": now_utc.isoformat(),
        "SYSTEM_TIME_UTC_HUMAN": now_utc.strftime("%Y-%m-%d %H:%M:%SZ"),
        "SYSTEM_UNIX_TIMESTAMP": int(now_utc.timestamp()),
        "SYSTEM_UNIX_TIMESTAMP_MS": int(now_utc.timestamp() * 1000),
        "SYSTEM_DATE_UTC": now_utc.date(),
        "SYSTEM_YEAR_UTC": now_utc.year,
        "SYSTEM_MONTH_UTC": now_utc.month,
        "SYSTEM_DAY_UTC": now_utc.day,
        "SYSTEM_ISO_WEEK_UTC": now_utc.isocalendar().week,
        "SYSTEM_DAY_OF_YEAR_UTC": now_utc.timetuple().tm_yday,
        "SYSTEM_TIME_LOCAL": local_now.isoformat(),
        "SYSTEM_DATE_LOCAL": local_now.date(),
        "SYSTEM_TIMEZONE_NAME": tzname,
        "SYSTEM_TIMEZONE_OFFSET_MINUTES": offset_minutes,
        "SYSTEM_TIMEZONE_OFFSET_HOURS": offset_minutes / 60,
    }
    return {key: _stringify(value) for key, value in runtime.items()}


def _safe_environment_variables() -> dict[str, str]:
    safe: dict[str, str] = {}
    for key, value in os.environ.items():
        upper_key = key.upper()
        if any(marker in upper_key for marker in _SENSITIVE_MARKERS):
            continue
        if upper_key.startswith("DB_") or upper_key.startswith("REDIS_"):
            continue
        if upper_key.startswith("SMTP_") or upper_key.startswith("AZURE_"):
            continue
        if upper_key.startswith("SYNCRO_") or upper_key.startswith("VERIFY_"):
            continue
        if upper_key.startswith("LICENSES_") or upper_key.startswith("SHOP_"):
            continue
        if upper_key.startswith("SMS_") or upper_key.startswith("M365_"):
            continue
        if upper_key.startswith("TACTICALRMM_") or upper_key.startswith("UPTIMEKUMA_"):
            continue
        if upper_key.startswith("CHATGPT_") or upper_key.startswith("OLLAMA_"):
            continue
        # Only include variables that look safe for interpolation.
        if upper_key.startswith("APP_") or upper_key in {
            "ENVIRONMENT",
            "PORTAL_URL",
            "CRON_TIMEZONE",
            "ENABLE_CSRF",
            "ENABLE_AUTO_REFRESH",
            "SWAGGER_UI_URL",
            "OPNFORM_BASE_URL",
            "FAIL2BAN_LOG_PATH",
            "SYSTEMD_SERVICE_NAME",
            "APP_RESTART_COMMAND",
            "TZ",
            "LANG",
            "LC_ALL",
        }:
            safe[upper_key] = _stringify(value)
    return safe


def _flatten_context_tokens(
    value: Any,
    path: list[str],
    tokens: dict[str, Any],
    *,
    stringify: bool,
    seen: set[int],
) -> None:
    if isinstance(value, Mapping):
        identifier = id(value)
        if identifier in seen:
            return
        seen.add(identifier)
        for key, item in value.items():
            if not isinstance(key, str):
                continue
            _flatten_context_tokens(
                item,
                path + [key],
                tokens,
                stringify=stringify,
                seen=seen,
            )
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        identifier = id(value)
        if identifier in seen:
            return
        seen.add(identifier)
        for index, item in enumerate(value):
            _flatten_context_tokens(
                item,
                path + [str(index)],
                tokens,
                stringify=stringify,
                seen=seen,
            )
        return
    if not path:
        return
    token_name = "_".join(segment.upper() for segment in path if segment)
    if not token_name:
        return
    stored = _stringify(value) if stringify else value
    tokens[token_name] = stored
    if token_name.endswith("_SUBJECT"):
        summary_alias = token_name[:-8] + "_SUMMARY"
        tokens.setdefault(summary_alias, stored)
    elif token_name.endswith("_SUMMARY"):
        subject_alias = token_name[:-8] + "_SUBJECT"
        tokens.setdefault(subject_alias, stored)


def build_context_variables(
    context: Any,
    *,
    prefix: str | None = None,
    stringify: bool = False,
) -> dict[str, Any]:
    tokens: dict[str, Any] = {}
    if context is None:
        return tokens
    initial_path: list[str] = [prefix] if prefix else []
    seen: set[int] = set()
    _flatten_context_tokens(context, initial_path, tokens, stringify=stringify, seen=seen)
    return tokens


def get_system_variables(*, ticket: Mapping[str, Any] | None = None) -> dict[str, str]:
    """Return a merged map of safe system variables for template interpolation."""

    variables = dict(_static_variables())
    variables.update(_safe_environment_variables())
    variables.update(_runtime_variables())
    if ticket:
        variables.update(build_context_variables(ticket, prefix="ticket", stringify=True))
        company_name = variables.get("TICKET_COMPANY_NAME")
        if company_name and not variables.get("COMPANY_NAME"):
            variables["COMPANY_NAME"] = company_name
    return variables
