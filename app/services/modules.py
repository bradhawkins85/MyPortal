from __future__ import annotations

import hashlib
import json
import os
import string
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import asyncio
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import urljoin

import httpx
from loguru import logger

from app.core.database import db
from app.repositories import companies as company_repo
from app.repositories import integration_modules as module_repo
from app.repositories import webhook_events as webhook_repo
from app.services import email as email_service, webhook_monitor
from app.services.realtime import RefreshNotifier, refresh_notifier

REQUEST_TIMEOUT = httpx.Timeout(15.0, connect=5.0)

_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()

DEFAULT_CHATGPT_TOOLS = [
    "listTickets",
    "getTicket",
    "createTicketReply",
    "updateTicket",
]


def _hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _merge_settings(defaults: Mapping[str, Any], overrides: Mapping[str, Any] | None) -> dict[str, Any]:
    merged = dict(defaults)
    if not overrides:
        return merged
    for key, value in overrides.items():
        merged[key] = value
    return merged


def _ensure_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, list):
        if not value:
            return default
        return _ensure_bool(value[-1], default)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    return default


def _ensure_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


def _coerce_int(value: Any, *, minimum: int | None = None, maximum: int | None = None) -> int | None:
    if value is None or value == "":
        return None
    try:
        integer = int(value)
    except (TypeError, ValueError):
        return None
    if minimum is not None:
        integer = max(minimum, integer)
    if maximum is not None:
        integer = min(maximum, integer)
    return integer


def _normalise_tool_names(value: Any) -> list[str]:
    requested = _ensure_list(value)
    if not requested:
        return list(DEFAULT_CHATGPT_TOOLS)
    normalised: list[str] = []
    for name in requested:
        candidate = name.strip()
        if not candidate:
            continue
        if candidate not in DEFAULT_CHATGPT_TOOLS:
            continue
        if candidate not in normalised:
            normalised.append(candidate)
    return normalised or list(DEFAULT_CHATGPT_TOOLS)


def _normalise_statuses(value: Any) -> list[str]:
    statuses = _ensure_list(value)
    cleaned: list[str] = []
    for status in statuses:
        lowered = status.strip().lower()
        if not lowered:
            continue
        if lowered not in cleaned:
            cleaned.append(lowered)
    if cleaned:
        return cleaned
    return ["open", "pending", "in_progress", "resolved", "closed"]


def _default_chatgpt_settings() -> dict[str, Any]:
    shared_secret = str(os.getenv("CHATGPT_MCP_SHARED_SECRET", "")).strip()
    shared_secret_hash = _hash_secret(shared_secret) if shared_secret else ""
    allowed_actions = _normalise_tool_names(os.getenv("CHATGPT_MCP_ALLOWED_ACTIONS"))
    max_results = _coerce_int(os.getenv("CHATGPT_MCP_MAX_RESULTS"), minimum=1, maximum=200) or 50
    allow_updates = _ensure_bool(os.getenv("CHATGPT_MCP_ALLOW_UPDATES"), False)
    allowed_statuses = _normalise_statuses(os.getenv("CHATGPT_MCP_ALLOWED_STATUSES"))
    system_user_id = _coerce_int(os.getenv("CHATGPT_MCP_SYSTEM_USER_ID"))
    return {
        "shared_secret_hash": shared_secret_hash,
        "allowed_actions": allowed_actions,
        "max_results": max_results,
        "allow_ticket_updates": allow_updates,
        "allowed_statuses": allowed_statuses,
        "system_user_id": system_user_id,
    }


def _default_uptimekuma_settings() -> dict[str, Any]:
    shared_secret = str(os.getenv("UPTIMEKUMA_SHARED_SECRET", "")).strip()
    shared_secret_hash = _hash_secret(shared_secret) if shared_secret else ""
    return {
        "shared_secret_hash": shared_secret_hash,
    }


def _default_xero_settings() -> dict[str, Any]:
    def _clean_env(key: str) -> str:
        return str(os.getenv(key, "")).strip()

    def _format_rate(value: str) -> str:
        if not value:
            return ""
        try:
            decimal_value = Decimal(value)
        except (InvalidOperation, ValueError):
            return ""
        quantised = decimal_value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return f"{quantised:f}"

    return {
        "client_id": _clean_env("XERO_CLIENT_ID"),
        "client_secret": _clean_env("XERO_CLIENT_SECRET"),
        "refresh_token": _clean_env("XERO_REFRESH_TOKEN"),
        "tenant_id": _clean_env("XERO_TENANT_ID"),
        "default_hourly_rate": _format_rate(_clean_env("XERO_DEFAULT_HOURLY_RATE")),
        "account_code": _clean_env("XERO_ACCOUNT_CODE") or "400",
        "tax_type": _clean_env("XERO_TAX_TYPE"),
        "line_amount_type": _clean_env("XERO_LINE_AMOUNT_TYPE") or "Exclusive",
        "reference_prefix": _clean_env("XERO_REFERENCE_PREFIX") or "Support",
        "billable_statuses": _normalise_statuses(_clean_env("XERO_BILLABLE_STATUSES")),
        "line_item_description_template": _clean_env("XERO_LINE_ITEM_TEMPLATE")
        or "Ticket {ticket_id}: {ticket_subject}{labour_suffix}",
    }


DEFAULT_MODULES: list[dict[str, Any]] = [
    {
        "slug": "syncro",
        "name": "Syncro",
        "description": "Synchronise tickets and contacts from SyncroMSP.",
        "icon": "🧾",
        "settings": {
            "base_url": "",
            "api_key": "",
            "rate_limit_per_minute": 180,
        },
    },
    {
        "slug": "ollama",
        "name": "Ollama",
        "description": "Generate ticket summaries using an on-prem Ollama model.",
        "icon": "🧠",
        "settings": {
            "base_url": "http://127.0.0.1:11434",
            "model": "llama3",
            "prompt": "",
        },
    },
    {
        "slug": "smtp",
        "name": "SMTP Relay",
        "description": "Trigger outbound email notifications using the platform SMTP server.",
        "icon": "✉️",
        "settings": {
            "from_address": "",
            "default_recipients": [],
            "subject_prefix": "",
        },
    },
    {
        "slug": "imap",
        "name": "IMAP Mailboxes",
        "description": "Import support emails from mailboxes into the ticketing queue.",
        "icon": "📥",
        "settings": {
            "manage_url": "/admin/modules/imap",
        },
    },
    {
        "slug": "tacticalrmm",
        "name": "Tactical RMM",
        "description": "Call Tactical RMM webhook endpoints for automation actions.",
        "icon": "🛡️",
        "settings": {
            "base_url": "",
            "api_key": "",
            "verify_ssl": True,
        },
    },
    {
        "slug": "ntfy",
        "name": "ntfy",
        "description": "Broadcast automation alerts to ntfy topics.",
        "icon": "📣",
        "settings": {
            "base_url": "https://ntfy.sh",
            "topic": "",
            "auth_token": "",
        },
    },
    {
        "slug": "uptimekuma",
        "name": "Uptime Kuma",
        "description": "Ingest uptime alerts from Uptime Kuma webhooks.",
        "icon": "📈",
        "settings": _default_uptimekuma_settings(),
    },
    {
        "slug": "chatgpt-mcp",
        "name": "ChatGPT MCP",
        "description": "Expose ticketing tools to ChatGPT via the Model Context Protocol.",
        "icon": "🤖",
        "settings": _default_chatgpt_settings(),
    },
    {
        "slug": "xero",
        "name": "Xero",
        "description": "Synchronise invoice data with Xero.",
        "icon": "💼",
        "settings": _default_xero_settings(),
    },
]


def _default_module_setting(slug: str, key: str, fallback: str) -> str:
    for module in DEFAULT_MODULES:
        if module.get("slug") == slug:
            settings = module.get("settings") or {}
            value = settings.get(key)
            if isinstance(value, str) and value:
                return value
    return fallback


_DEFAULT_OLLAMA_MODEL = _default_module_setting("ollama", "model", "llama3")
_DEFAULT_OLLAMA_BASE_URL = _default_module_setting("ollama", "base_url", "http://127.0.0.1:11434")


def _coerce_settings(
    slug: str,
    payload: Mapping[str, Any] | None,
    existing: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    defaults = next((module["settings"] for module in DEFAULT_MODULES if module["slug"] == slug), {})
    existing_settings: Mapping[str, Any] | None = None
    if existing and isinstance(existing.get("settings"), Mapping):
        existing_settings = existing["settings"]
    base = _merge_settings(defaults, existing_settings)
    merged = _merge_settings(base, payload)
    if slug == "ollama":
        base_url = str(merged.get("base_url", "")).strip() or defaults.get("base_url")
        model = str(merged.get("model", "")).strip() or defaults.get("model")
        prompt = str(merged.get("prompt", "")).strip()
        merged.update({"base_url": base_url, "model": model, "prompt": prompt})
    elif slug == "smtp":
        merged.update(
            {
                "from_address": str(merged.get("from_address", "")).strip(),
                "default_recipients": _ensure_list(merged.get("default_recipients")),
                "subject_prefix": str(merged.get("subject_prefix", "")).strip(),
            }
        )
    elif slug == "syncro":
        base_url = str(merged.get("base_url") or "").strip().rstrip("/")
        api_key_override = payload.get("api_key") if payload else None
        if api_key_override is None:
            api_key = str(merged.get("api_key") or "").strip()
        else:
            api_key = str(api_key_override or "").strip()
            if not api_key and existing_settings and existing_settings.get("api_key"):
                api_key = str(existing_settings.get("api_key") or "").strip()
        rate_limit = _coerce_int(merged.get("rate_limit_per_minute"), minimum=1, maximum=600) or 180
        merged.update(
            {
                "base_url": base_url,
                "api_key": api_key,
                "rate_limit_per_minute": rate_limit,
            }
        )
    elif slug == "tacticalrmm":
        overrides = payload or {}
        api_key_override = overrides.get("api_key")
        if api_key_override is None:
            api_key = str(merged.get("api_key") or "").strip()
        else:
            candidate = str(api_key_override or "").strip()
            if not candidate and existing_settings and existing_settings.get("api_key"):
                api_key = str(existing_settings.get("api_key") or "").strip()
            else:
                api_key = candidate
        merged.update(
            {
                "base_url": str(merged.get("base_url", "")).strip().rstrip("/"),
                "api_key": api_key,
                "verify_ssl": _ensure_bool(merged.get("verify_ssl"), True),
            }
        )
    elif slug == "ntfy":
        overrides = payload or {}
        auth_token_override = overrides.get("auth_token")
        if auth_token_override is None:
            auth_token = str(merged.get("auth_token") or "").strip()
        else:
            candidate = str(auth_token_override or "").strip()
            if not candidate and existing_settings and existing_settings.get("auth_token"):
                auth_token = str(existing_settings.get("auth_token") or "").strip()
            else:
                auth_token = candidate
        base_url_value = str(merged.get("base_url", "")).strip()
        base_url = base_url_value.rstrip("/") if base_url_value else ""
        merged.update(
            {
                "base_url": base_url or "https://ntfy.sh",
                "topic": str(merged.get("topic", "")).strip(),
                "auth_token": auth_token,
            }
        )
    elif slug == "imap":
        manage_url = str(merged.get("manage_url") or "").strip() or "/admin/modules/imap"
        merged.update({"manage_url": manage_url})
    elif slug == "chatgpt-mcp":
        overrides = payload or {}
        shared_secret_override = overrides.get("shared_secret")
        shared_secret_hash_override = overrides.get("shared_secret_hash")
        if shared_secret_override is not None or shared_secret_hash_override is not None:
            candidate = shared_secret_override
            if candidate in (None, ""):
                candidate = shared_secret_hash_override
            candidate_str = str(candidate or "").strip()
            if not candidate_str:
                merged["shared_secret_hash"] = ""
            elif (
                len(candidate_str) == 64
                and all(char in string.hexdigits for char in candidate_str)
                and shared_secret_override in (None, "")
            ):
                merged["shared_secret_hash"] = candidate_str.lower()
            else:
                merged["shared_secret_hash"] = _hash_secret(candidate_str)
        # ensure a hash is always present even if override absent
        merged["shared_secret_hash"] = str(merged.get("shared_secret_hash", "")).strip()
        merged["allowed_actions"] = _normalise_tool_names(merged.get("allowed_actions"))
        merged["max_results"] = _coerce_int(merged.get("max_results"), minimum=1, maximum=200) or 50
        merged["allow_ticket_updates"] = _ensure_bool(
            merged.get("allow_ticket_updates"), False
        )
        merged["allowed_statuses"] = _normalise_statuses(merged.get("allowed_statuses"))
        merged["system_user_id"] = _coerce_int(merged.get("system_user_id"))
        merged.pop("shared_secret", None)
    elif slug == "uptimekuma":
        overrides = payload or {}
        shared_secret_override = overrides.get("shared_secret")
        shared_secret_hash_override = overrides.get("shared_secret_hash")
        if shared_secret_override is not None or shared_secret_hash_override is not None:
            candidate = shared_secret_override
            if candidate in (None, ""):
                candidate = shared_secret_hash_override
            candidate_str = str(candidate or "").strip()
            if not candidate_str:
                merged["shared_secret_hash"] = ""
            elif (
                len(candidate_str) == 64
                and all(char in string.hexdigits for char in candidate_str)
                and shared_secret_override in (None, "")
            ):
                merged["shared_secret_hash"] = candidate_str.lower()
            else:
                merged["shared_secret_hash"] = _hash_secret(candidate_str)
        merged["shared_secret_hash"] = str(merged.get("shared_secret_hash", "")).strip()
        merged.pop("shared_secret", None)
    elif slug == "xero":
        overrides = payload or {}

        def _preserve_secret(field: str) -> str:
            override = overrides.get(field)
            if override is None:
                return str(merged.get(field, "") or "").strip()
            candidate = str(override or "").strip()
            if candidate and candidate != "********":
                return candidate
            if existing_settings and existing_settings.get(field):
                return str(existing_settings.get(field) or "").strip()
            return ""

        def _normalise_rate(value: Any) -> str:
            if value in (None, ""):
                return ""
            try:
                decimal_value = Decimal(str(value))
            except (InvalidOperation, ValueError):
                return ""
            quantised = decimal_value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            return f"{quantised:f}"

        merged.update(
            {
                "client_id": str(merged.get("client_id", "")).strip(),
                "client_secret": _preserve_secret("client_secret"),
                "refresh_token": _preserve_secret("refresh_token"),
                "tenant_id": str(merged.get("tenant_id", "")).strip(),
                "default_hourly_rate": _normalise_rate(merged.get("default_hourly_rate")),
                "account_code": str(merged.get("account_code", "")).strip(),
                "tax_type": str(merged.get("tax_type", "")).strip(),
                "line_amount_type": str(merged.get("line_amount_type", "")).strip() or "Exclusive",
                "reference_prefix": str(merged.get("reference_prefix", "")).strip() or "Support",
                "billable_statuses": _normalise_statuses(merged.get("billable_statuses")),
                "line_item_description_template": str(
                    merged.get("line_item_description_template", "")
                ).strip()
                or "Ticket {ticket_id}: {ticket_subject}{labour_suffix}",
            }
        )
    return merged


def _redact_module_settings(module: dict[str, Any]) -> dict[str, Any]:
    slug = module.get("slug")
    fields_to_redact: dict[str, tuple[str, ...]] = {
        "chatgpt-mcp": ("shared_secret_hash",),
        "syncro": ("api_key",),
        "uptimekuma": ("shared_secret_hash",),
        "tacticalrmm": ("api_key",),
        "ntfy": ("auth_token",),
        "xero": ("client_secret", "refresh_token"),
    }
    targets = fields_to_redact.get(slug)
    if not targets:
        return module
    redacted = dict(module)
    settings = dict(redacted.get("settings") or {})
    for field in targets:
        if settings.get(field):
            settings[field] = "********"
    redacted["settings"] = settings
    return redacted


async def ensure_default_modules() -> None:
    if not db.is_connected():
        logger.info(
            "Skipping default module synchronisation because the database is not connected."
        )
        return

    try:
        existing = await module_repo.list_modules()
    except RuntimeError as exc:
        logger.warning(
            "Unable to synchronise default modules due to database error", error=str(exc)
        )
        return
    existing_by_slug = {module["slug"]: module for module in existing}
    for default in DEFAULT_MODULES:
        current = existing_by_slug.get(default["slug"])
        if not current:
            await module_repo.upsert_module(
                slug=default["slug"],
                name=default["name"],
                description=default["description"],
                icon=default["icon"],
                enabled=False,
                settings=default["settings"],
            )
            continue
        updates: dict[str, Any] = {}
        if current.get("name") != default["name"]:
            updates["name"] = default["name"]
        if current.get("description") != default["description"]:
            updates["description"] = default["description"]
        if current.get("icon") != default["icon"]:
            updates["icon"] = default["icon"]
        if updates:
            await module_repo.update_module(default["slug"], **updates)


async def list_modules() -> list[dict[str, Any]]:
    modules = await module_repo.list_modules()
    return [_redact_module_settings(module) for module in modules]


async def get_module(slug: str, *, redact: bool = True) -> dict[str, Any] | None:
    module = await module_repo.get_module(slug)
    if not module:
        return None
    return _redact_module_settings(module) if redact else module


async def update_module(
    slug: str,
    *,
    enabled: bool | None = None,
    settings: Mapping[str, Any] | None = None,
    notifier: RefreshNotifier | None = None,
) -> dict[str, Any] | None:
    existing = await module_repo.get_module(slug)
    coerced = _coerce_settings(slug, settings, existing) if settings is not None else None
    updated = await module_repo.update_module(slug, enabled=enabled, settings=coerced)
    if updated:
        resolved_notifier = notifier or refresh_notifier
        await resolved_notifier.broadcast_refresh(reason=f"modules:updated:{slug}")
    return _redact_module_settings(updated) if updated else None


async def trigger_module(
    slug: str,
    payload: Mapping[str, Any] | None = None,
    *,
    background: bool = True,
    on_complete: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    module = await module_repo.get_module(slug)
    if not module:
        raise ValueError(f"Module {slug} is not configured")
    if not module.get("enabled"):
        return {"status": "skipped", "reason": "Module disabled", "module": slug}
    raw_settings = module.get("settings")
    if isinstance(raw_settings, Mapping):
        settings = _coerce_settings(slug, raw_settings)
    else:
        try:
            parsed = json.loads(raw_settings) if isinstance(raw_settings, str) else None
        except json.JSONDecodeError:
            parsed = None
        settings = _coerce_settings(slug, parsed)
    handler_map: dict[str, Callable[..., Awaitable[dict[str, Any]]]] = {
        "syncro": _validate_syncro,
        "ollama": _invoke_ollama,
        "smtp": _invoke_smtp,
        "tacticalrmm": _invoke_tacticalrmm,
        "ntfy": _invoke_ntfy,
        "uptimekuma": _validate_uptimekuma,
        "chatgpt-mcp": _invoke_chatgpt_mcp,
    }
    handler = handler_map.get(slug)
    if not handler:
        raise ValueError(f"No handler registered for module {slug}")

    async def _invoke_handler(
        *,
        event_future: asyncio.Future[int | None] | None,
    ) -> dict[str, Any]:
        try:
            result = await handler(settings, payload or {}, event_future=event_future)
        except Exception as exc:
            logger.error("Module background task encountered an error", module=slug, error=str(exc))
            if event_future and not event_future.done():
                event_future.set_result(None)
            result = {"status": "error", "error": str(exc), "module": slug}
        if event_future and not event_future.done():
            event_id_value = result.get("event_id")
            event_future.set_result(event_id_value if isinstance(event_id_value, int) else None)
        if on_complete:
            try:
                await on_complete(result)
            except Exception as callback_exc:  # pragma: no cover - defensive logging
                logger.error(
                    "Module completion callback failed",
                    module=slug,
                    error=str(callback_exc),
                )
        return result

    if not background:
        return await _invoke_handler(event_future=None)

    loop = asyncio.get_running_loop()
    event_future: asyncio.Future[int | None] = loop.create_future()

    async def _runner() -> dict[str, Any]:
        return await _invoke_handler(event_future=event_future)

    task: asyncio.Task[dict[str, Any]] = asyncio.create_task(_runner())
    _BACKGROUND_TASKS.add(task)

    def _cleanup(completed: asyncio.Task[dict[str, Any]]) -> None:
        _BACKGROUND_TASKS.discard(completed)
        try:
            completed.result()
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.error(
                "Module background task failed", module=slug, error=str(exc)
            )

    task.add_done_callback(_cleanup)

    event_id_value: int | None = None
    try:
        event_id_value = await asyncio.wait_for(event_future, timeout=0.5)
    except asyncio.TimeoutError:  # pragma: no cover - timing dependent
        event_id_value = None

    queued_result: dict[str, Any] = {"status": "queued", "module": slug}
    if event_id_value is not None:
        queued_result["event_id"] = event_id_value
    return queued_result


def _parse_event_response(event: Mapping[str, Any]) -> Any:
    response_body = event.get("response_body")
    if response_body is None:
        return None
    if isinstance(response_body, (dict, list)):
        return response_body
    if isinstance(response_body, str):
        try:
            return json.loads(response_body)
        except json.JSONDecodeError:
            return response_body
    return response_body


def _build_event_result(event: Mapping[str, Any], extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if extra:
        result.update(extra)
    event_id = event.get("id")
    if event_id is not None:
        result["event_id"] = int(event_id)
    status = str(event.get("status") or "pending")
    result["status"] = status
    result["event_status"] = status
    if event.get("response_status") is not None:
        result["response_status"] = event.get("response_status")
    if event.get("attempt_count") is not None:
        result["attempt_count"] = event.get("attempt_count")
    if event.get("last_error"):
        result["last_error"] = event.get("last_error")
    parsed_response = _parse_event_response(event)
    if parsed_response is not None:
        result["response"] = parsed_response
    return result


async def _record_success(
    event_id: int,
    *,
    attempt_number: int,
    response_status: int | None,
    response_body: str | None,
) -> dict[str, Any]:
    await webhook_repo.record_attempt(
        event_id=event_id,
        attempt_number=attempt_number,
        status="succeeded",
        response_status=response_status,
        response_body=response_body,
        error_message=None,
    )
    await webhook_repo.mark_event_completed(
        event_id,
        attempt_number=attempt_number,
        response_status=response_status,
        response_body=response_body,
    )
    refreshed = await webhook_repo.get_event(event_id)
    return refreshed or {"id": event_id, "status": "succeeded"}


async def _record_failure(
    event_id: int,
    *,
    attempt_number: int,
    status: str,
    error_message: str | None,
    response_status: int | None,
    response_body: str | None,
) -> dict[str, Any]:
    await webhook_repo.record_attempt(
        event_id=event_id,
        attempt_number=attempt_number,
        status=status,
        response_status=response_status,
        response_body=response_body,
        error_message=error_message,
    )
    await webhook_repo.mark_event_failed(
        event_id,
        attempt_number=attempt_number,
        error_message=error_message,
        response_status=response_status,
        response_body=response_body,
    )
    refreshed = await webhook_repo.get_event(event_id)
    return refreshed or {"id": event_id, "status": "failed", "last_error": error_message}


async def _invoke_ollama(
    settings: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    event_future: asyncio.Future[int | None] | None = None,
) -> dict[str, Any]:
    configured_base_url = str(settings.get("base_url") or "").strip()
    base_url = configured_base_url or _DEFAULT_OLLAMA_BASE_URL
    base_url = base_url.rstrip("/")

    payload_model = payload.get("model")
    configured_model = str(settings.get("model") or "").strip()
    model = str(payload_model or "").strip() or configured_model or _DEFAULT_OLLAMA_MODEL

    default_prompt = str(settings.get("prompt") or "")
    prompt = str(payload.get("prompt") or payload.get("text") or default_prompt)
    if not prompt:
        raise ValueError("Ollama prompt cannot be empty")
    endpoint = urljoin(f"{base_url}/", "api/generate")
    body = {"model": model, "prompt": prompt, "stream": False}
    event = await webhook_monitor.create_manual_event(
        name="module.ollama.generate",
        target_url=endpoint,
        payload={"request_body": body},
        headers={"Content-Type": "application/json"},
        max_attempts=1,
        backoff_seconds=60,
    )
    event_id = int(event.get("id")) if event.get("id") is not None else None
    if event_id is None:
        raise RuntimeError("Failed to create webhook event for Ollama request")
    if event_future and not event_future.done():
        event_future.set_result(event_id)
    attempt_number = 1
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.post(endpoint, json=body)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        response_body = exc.response.text if exc.response is not None else None
        updated_event = await _record_failure(
            event_id,
            attempt_number=attempt_number,
            status="failed",
            error_message=f"HTTP {exc.response.status_code}" if exc.response else str(exc),
            response_status=exc.response.status_code if exc.response else None,
            response_body=response_body,
        )
        return _build_event_result(
            updated_event,
            extra={"model": model, "endpoint": endpoint},
        )
    except Exception as exc:  # pragma: no cover - defensive
        updated_event = await _record_failure(
            event_id,
            attempt_number=attempt_number,
            status="error",
            error_message=str(exc),
            response_status=None,
            response_body=None,
        )
        return _build_event_result(
            updated_event,
            extra={"model": model, "endpoint": endpoint},
        )

    response_body = response.text
    updated_event = await _record_success(
        event_id,
        attempt_number=attempt_number,
        response_status=response.status_code,
        response_body=response_body,
    )
    return _build_event_result(
        updated_event,
        extra={"model": model, "endpoint": endpoint},
    )


async def _invoke_smtp(
    settings: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    event_future: asyncio.Future[int | None] | None = None,
) -> dict[str, Any]:
    recipients = _ensure_list(payload.get("recipients")) or _ensure_list(settings.get("default_recipients"))
    subject_prefix = str(settings.get("subject_prefix") or "").strip()
    subject = str(payload.get("subject") or "Automation notification")
    if subject_prefix:
        subject = f"{subject_prefix} {subject}".strip()
    html_body = str(payload.get("html") or payload.get("body") or "<p>Automation triggered.</p>")
    text_body = payload.get("text")
    sender = str(settings.get("from_address") or "") or None
    event = await webhook_monitor.create_manual_event(
        name="module.smtp.send",
        target_url="smtp://send",
        payload={
            "subject": subject,
            "recipients": recipients,
            "html": html_body,
            "text": text_body,
            "sender": sender,
        },
        headers={"X-Module": "smtp"},
        max_attempts=1,
        backoff_seconds=60,
    )
    event_id = int(event.get("id")) if event.get("id") is not None else None
    if event_id is None:
        raise RuntimeError("Failed to create webhook event for SMTP request")
    if event_future and not event_future.done():
        event_future.set_result(event_id)
    attempt_number = 1
    try:
        sent, email_event_metadata = await email_service.send_email(
            subject=subject,
            recipients=recipients,
            html_body=html_body,
            text_body=str(text_body) if text_body is not None else None,
            sender=sender,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        updated_event = await _record_failure(
            event_id,
            attempt_number=attempt_number,
            status="error",
            error_message=str(exc),
            response_status=None,
            response_body=None,
        )
        return _build_event_result(
            updated_event,
            extra={"recipients": recipients, "subject": subject},
        )

    if not sent:
        updated_event = await _record_failure(
            event_id,
            attempt_number=attempt_number,
            status="failed",
            error_message="SMTP service declined to send message",
            response_status=None,
            response_body=None,
        )
        return _build_event_result(
            updated_event,
            extra={
                "recipients": recipients,
                "subject": subject,
                "email_event_id": (email_event_metadata or {}).get("id")
                if isinstance(email_event_metadata, dict)
                else None,
            },
        )

    response_body = json.dumps({"recipients": recipients, "subject": subject})
    updated_event = await _record_success(
        event_id,
        attempt_number=attempt_number,
        response_status=250,
        response_body=response_body,
    )
    return _build_event_result(
        updated_event,
        extra={
            "recipients": recipients,
            "subject": subject,
            "email_event_id": (email_event_metadata or {}).get("id")
            if isinstance(email_event_metadata, dict)
            else None,
        },
    )


async def _invoke_tacticalrmm(
    settings: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    event_future: asyncio.Future[int | None] | None = None,
) -> dict[str, Any]:
    base_url = str(settings.get("base_url") or "").rstrip("/")
    if not base_url:
        raise ValueError("Tactical RMM base URL is not configured")
    api_key = str(settings.get("api_key") or "").strip()
    verify_ssl = _ensure_bool(settings.get("verify_ssl"), True)
    task_identifier = payload.get("task") or payload.get("task_id")
    endpoint_path: str
    if payload.get("endpoint") or payload.get("path"):
        endpoint_path = str(payload.get("endpoint") or payload.get("path")).lstrip("/")
    elif task_identifier is not None:
        endpoint_path = f"automation/tasks/{task_identifier}/run/"
    else:
        endpoint_path = "automation/tasks/run/"
    method = str(payload.get("method") or "POST").upper()
    headers = {"Content-Type": "application/json"}
    auth_header = str(payload.get("auth_header") or "X-API-KEY")
    if api_key:
        headers[auth_header] = payload.get("auth_prefix", "") + api_key
    extra_headers = payload.get("headers")
    if isinstance(extra_headers, Mapping):
        for key, value in extra_headers.items():
            headers[str(key)] = str(value)
    request_body = payload.get("body")
    url = urljoin(f"{base_url}/", endpoint_path)
    event = await webhook_monitor.create_manual_event(
        name="module.tacticalrmm.invoke",
        target_url=url,
        payload={
            "method": method,
            "json": request_body,
            "verify_ssl": verify_ssl,
        },
        headers=headers,
        max_attempts=1,
        backoff_seconds=60,
    )
    event_id = int(event.get("id")) if event.get("id") is not None else None
    if event_id is None:
        raise RuntimeError("Failed to create webhook event for TacticalRMM request")
    if event_future and not event_future.done():
        event_future.set_result(event_id)
    attempt_number = 1
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, verify=verify_ssl) as client:
            response = await client.request(method, url, json=request_body, headers=headers)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        response_body = exc.response.text if exc.response is not None else None
        updated_event = await _record_failure(
            event_id,
            attempt_number=attempt_number,
            status="failed",
            error_message=f"HTTP {exc.response.status_code}" if exc.response else str(exc),
            response_status=exc.response.status_code if exc.response else None,
            response_body=response_body,
        )
        result = _build_event_result(
            updated_event,
            extra={"url": url, "method": method},
        )
        if "response_status" in result:
            result["status_code"] = result["response_status"]
        return result
    except Exception as exc:  # pragma: no cover - defensive
        updated_event = await _record_failure(
            event_id,
            attempt_number=attempt_number,
            status="error",
            error_message=str(exc),
            response_status=None,
            response_body=None,
        )
        result = _build_event_result(
            updated_event,
            extra={"url": url, "method": method},
        )
        if "response_status" in result:
            result["status_code"] = result["response_status"]
        return result

    response_body = response.text
    updated_event = await _record_success(
        event_id,
        attempt_number=attempt_number,
        response_status=response.status_code,
        response_body=response_body,
    )
    result = _build_event_result(
        updated_event,
        extra={"url": url, "method": method},
    )
    if "response_status" in result:
        result["status_code"] = result["response_status"]
    return result


async def _invoke_ntfy(
    settings: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    event_future: asyncio.Future[int | None] | None = None,
) -> dict[str, Any]:
    def _lookup(source: Mapping[str, Any], *keys: str) -> tuple[Any, bool]:
        seen: set[str] = set()
        for key in keys:
            if key is None:
                continue
            candidates = [key]
            lower = key.lower()
            upper = key.upper()
            title = key.title()
            capitalized = key.capitalize()
            candidates.extend([lower, upper, title, capitalized])
            swapped_dash = key.replace("-", "_")
            swapped_underscore = key.replace("_", "-")
            candidates.extend([swapped_dash, swapped_underscore])
            candidates.extend([swapped_dash.lower(), swapped_dash.upper()])
            candidates.extend([swapped_underscore.lower(), swapped_underscore.upper()])
            camel_source = swapped_dash
            parts = [part for part in camel_source.split("_") if part]
            if parts:
                camel = parts[0].lower() + "".join(part.capitalize() for part in parts[1:])
                pascal = "".join(part.capitalize() for part in parts)
                candidates.extend([camel, pascal])
            for candidate in candidates:
                if candidate is None:
                    continue
                if candidate in seen:
                    continue
                seen.add(candidate)
                if candidate in source:
                    return source[candidate], True
        return None, False

    def _coerce_header_value(value: Any) -> str:
        if isinstance(value, Mapping):
            return json.dumps(value)
        if isinstance(value, (list, tuple)):
            return ",".join(str(item) for item in value if item is not None)
        if isinstance(value, set):
            return ",".join(str(item) for item in sorted(value))
        if isinstance(value, bool):
            return "true" if value else "false"
        if value is None:
            return ""
        return str(value)

    def _canonical_header(name: str) -> str:
        aliases = {
            "title": "Title",
            "priority": "Priority",
            "tags": "Tags",
            "tag": "Tags",
            "icon": "Icon",
            "click": "Click",
            "actions": "Actions",
            "attach": "Attach",
            "attachment": "Attach",
            "filename": "Filename",
            "email": "Email",
            "delay": "Delay",
            "cache": "Cache",
            "content-type": "Content-Type",
            "content_type": "Content-Type",
            "authorization": "Authorization",
        }
        lowered = name.lower()
        return aliases.get(lowered, name)

    payload_dict: dict[str, Any] = dict(payload) if isinstance(payload, Mapping) else {}
    json_overrides = payload_dict.get("json")
    if isinstance(json_overrides, Mapping):
        merged_payload: dict[str, Any] = dict(json_overrides)
        for key, value in payload_dict.items():
            if key == "json":
                continue
            merged_payload[key] = value
    else:
        merged_payload = dict(payload_dict)

    base_url_value, has_base_url = _lookup(merged_payload, "base_url", "base-url", "baseUrl")
    if not has_base_url:
        base_url_value = settings.get("base_url")
    base_url = str(base_url_value or "https://ntfy.sh").rstrip("/")

    topic_value, has_topic = _lookup(merged_payload, "topic", "Topic")
    if not has_topic:
        topic_value = settings.get("topic")
    topic = str(topic_value or "").strip()
    if not topic:
        raise ValueError("ntfy topic must be configured")

    message_value, has_message = _lookup(merged_payload, "message", "body", "Message", "Body")
    if not has_message:
        message_value = "Automation triggered"
    if isinstance(message_value, Mapping) or (
        isinstance(message_value, (list, tuple)) and not isinstance(message_value, (str, bytes, bytearray))
    ):
        message_text = json.dumps(message_value)
    elif message_value is None:
        message_text = ""
    else:
        message_text = str(message_value)

    priority_value, has_priority = _lookup(merged_payload, "priority", "Priority")
    if not has_priority:
        priority_value = "default"
    priority_text = ""
    if priority_value is not None:
        priority_text = str(priority_value).strip()
    if not priority_text:
        priority_text = "default"

    title_value, has_title = _lookup(merged_payload, "title", "Title")
    if not has_title:
        title_value = "Automation event"
    title_text = ""
    if title_value is not None:
        title_text = str(title_value).strip()
    if not title_text:
        title_text = "Automation event"

    headers: dict[str, str] = {}
    raw_headers = merged_payload.get("headers")
    if isinstance(raw_headers, Mapping):
        for name, value in raw_headers.items():
            if value is None:
                continue
            canonical = _canonical_header(str(name))
            headers[canonical] = _coerce_header_value(value)

    header_aliases = {
        "title": "Title",
        "priority": "Priority",
        "tags": "Tags",
        "tag": "Tags",
        "icon": "Icon",
        "click": "Click",
        "actions": "Actions",
        "attach": "Attach",
        "attachment": "Attach",
        "filename": "Filename",
        "email": "Email",
        "delay": "Delay",
        "cache": "Cache",
        "content_type": "Content-Type",
        "content-type": "Content-Type",
    }
    for key, canonical in header_aliases.items():
        value, exists = _lookup(merged_payload, key, key.capitalize())
        if not exists or value is None:
            continue
        if canonical not in headers:
            headers[canonical] = _coerce_header_value(value)

    if "Title" in headers:
        title_text = headers["Title"]
    else:
        headers["Title"] = _coerce_header_value(title_text)
        title_text = headers["Title"]

    if "Priority" in headers:
        priority_text = headers["Priority"]
    else:
        headers["Priority"] = _coerce_header_value(priority_text)
        priority_text = headers["Priority"]

    token_value, has_token = _lookup(
        merged_payload, "auth_token", "token", "auth-token", "authToken"
    )
    if not has_token:
        token_value = settings.get("auth_token")
    token_text = str(token_value).strip() if token_value else ""
    if token_text and "Authorization" not in headers:
        headers["Authorization"] = f"Bearer {token_text}"

    url = f"{base_url}/{topic}"

    # Ensure any remaining canonical header aliases are applied exactly once more.
    for header_name, value in list(headers.items()):
        headers[header_name] = _coerce_header_value(value)

    message = message_text
    priority = priority_text
    title = title_text

    event_payload = {
        "topic": topic,
        "message": message,
        "priority": priority,
        "title": title,
        "headers": headers,
    }
    event = await webhook_monitor.create_manual_event(
        name="module.ntfy.publish",
        target_url=url,
        payload=event_payload,
        headers=headers,
        max_attempts=1,
        backoff_seconds=60,
    )
    event_id = int(event.get("id")) if event.get("id") is not None else None
    if event_id is None:
        raise RuntimeError("Failed to create webhook event for ntfy request")
    if event_future and not event_future.done():
        event_future.set_result(event_id)
    attempt_number = 1
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.post(
                url,
                data=message.encode("utf-8"),
                headers=headers,
            )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        response_body = exc.response.text if exc.response is not None else None
        updated_event = await _record_failure(
            event_id,
            attempt_number=attempt_number,
            status="failed",
            error_message=f"HTTP {exc.response.status_code}" if exc.response else str(exc),
            response_status=exc.response.status_code if exc.response else None,
            response_body=response_body,
        )
        return _build_event_result(
            updated_event,
            extra={"topic": topic, "priority": priority, "title": title, "url": url},
        )
    except Exception as exc:  # pragma: no cover - defensive
        updated_event = await _record_failure(
            event_id,
            attempt_number=attempt_number,
            status="error",
            error_message=str(exc),
            response_status=None,
            response_body=None,
        )
        return _build_event_result(
            updated_event,
            extra={"topic": topic, "priority": priority, "title": title, "url": url},
        )

    response_body = response.text
    updated_event = await _record_success(
        event_id,
        attempt_number=attempt_number,
        response_status=response.status_code,
        response_body=response_body,
    )
    return _build_event_result(
        updated_event,
        extra={"topic": topic, "priority": priority, "title": title, "url": url},
    )


async def _invoke_chatgpt_mcp(
    settings: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    event_future: asyncio.Future[int | None] | None = None,
) -> dict[str, Any]:
    secret_hash = str(settings.get("shared_secret_hash") or "").strip()
    if not secret_hash:
        raise ValueError("Shared secret hash is not configured")
    allowed_actions = settings.get("allowed_actions") or list(DEFAULT_CHATGPT_TOOLS)
    allow_updates = _ensure_bool(settings.get("allow_ticket_updates"), False)
    max_results = _coerce_int(settings.get("max_results"), minimum=1, maximum=200) or 50
    return {
        "status": "ok",
        "allowed_actions": allowed_actions,
        "allow_ticket_updates": allow_updates,
        "max_results": max_results,
    }


async def _validate_syncro(
    settings: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    event_future: asyncio.Future[int | None] | None = None,
) -> dict[str, Any]:
    base_url = str(settings.get("base_url") or "").strip()
    api_key = str(settings.get("api_key") or "").strip()
    rate_limit = _coerce_int(settings.get("rate_limit_per_minute"), minimum=1, maximum=600) or 180
    if not base_url:
        raise ValueError("Syncro base URL is not configured")
    return {
        "status": "ok",
        "base_url": base_url,
        "has_api_key": bool(api_key),
        "rate_limit_per_minute": rate_limit,
    }


async def _validate_uptimekuma(
    settings: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    event_future: asyncio.Future[int | None] | None = None,
) -> dict[str, Any]:
    shared_secret_hash = str(settings.get("shared_secret_hash") or "").strip()
    return {
        "status": "ok",
        "has_shared_secret": bool(shared_secret_hash),
    }


async def test_module(slug: str) -> dict[str, Any]:
    try:
        result = await trigger_module(slug, {}, background=False)
        return {"status": "ok", "details": result}
    except Exception as exc:  # pragma: no cover - network dependent
        logger.warning("Module test failed", slug=slug, error=str(exc))
        return {"status": "error", "error": str(exc)}


def _summarise_event_error(result: Mapping[str, Any]) -> str:
    if not isinstance(result, Mapping):
        return "Unknown error"
    messages: list[str] = []
    last_error = result.get("last_error")
    if last_error:
        messages.append(str(last_error))
    response_status = result.get("response_status") or result.get("status_code")
    if response_status:
        messages.append(f"HTTP {response_status}")
    response = result.get("response")
    if not last_error and response:
        if isinstance(response, Mapping):
            detail = response.get("detail") or response.get("error")
            if detail:
                messages.append(str(detail))
        elif isinstance(response, str) and response:
            messages.append(response)
    status_text = result.get("status")
    if status_text and status_text not in {"succeeded"}:
        messages.append(str(status_text))
    if messages:
        unique_messages = list(dict.fromkeys(messages))
        return "; ".join(unique_messages)
    return "Unknown error"


async def _load_tacticalrmm_settings() -> dict[str, Any]:
    module = await module_repo.get_module("tacticalrmm")
    if not module:
        raise ValueError("Tactical RMM module is not configured")

    raw_settings: Mapping[str, Any] | None = None
    if isinstance(module.get("settings"), Mapping):
        raw_settings = module["settings"]
    elif isinstance(module.get("settings"), str):
        try:
            raw_settings = json.loads(module["settings"])
        except json.JSONDecodeError:
            raw_settings = None

    settings = _coerce_settings("tacticalrmm", raw_settings, module)
    base_url = str(settings.get("base_url") or "").strip()
    if not base_url:
        raise ValueError("Tactical RMM base URL is not configured")
    api_key = str(settings.get("api_key") or "").strip()
    if not api_key:
        raise ValueError("Tactical RMM API key is not configured")
    return settings


async def ensure_tacticalrmm_ready() -> None:
    """Validate Tactical RMM configuration before executing operations."""

    await _load_tacticalrmm_settings()


async def push_companies_to_tacticalrmm(
    default_site_name: str = "Default",
) -> dict[str, Any]:
    settings = await _load_tacticalrmm_settings()

    default_site = str(default_site_name or "").strip() or "Default"
    default_site_key = default_site.casefold()

    companies = await company_repo.list_companies()
    unique_companies: list[tuple[str, Mapping[str, Any]] | tuple[str, dict[str, Any]]] = []
    skipped: list[dict[str, str]] = []
    seen: set[str] = set()

    for company in companies:
        name = str(company.get("name") or "").strip()
        if not name:
            identifier = str(company.get("id") or "unknown")
            skipped.append({"company": identifier, "reason": "missing_name"})
            continue
        key = name.casefold()
        if key in seen:
            skipped.append({"company": name, "reason": "duplicate_name"})
            continue
        seen.add(key)
        unique_companies.append((name, company))

    logger.info(
        "Synchronising companies with Tactical RMM", count=len(unique_companies)
    )

    existing_clients_result = await _invoke_tacticalrmm(
        settings,
        {"endpoint": "/clients/", "method": "GET"},
        event_future=None,
    )

    if existing_clients_result.get("status") != "succeeded":
        error = _summarise_event_error(existing_clients_result)
        raise RuntimeError(f"Failed to fetch Tactical RMM clients: {error}")

    response = existing_clients_result.get("response")
    raw_clients: list[Mapping[str, Any]] = []
    if isinstance(response, list):
        raw_clients = [item for item in response if isinstance(item, Mapping)]
    elif isinstance(response, Mapping):
        results = response.get("results")
        if isinstance(results, list):
            raw_clients = [item for item in results if isinstance(item, Mapping)]

    client_lookup: dict[str, Mapping[str, Any]] = {}
    for client in raw_clients:
        name = str(client.get("name") or "").strip()
        if not name:
            continue
        key = name.casefold()
        if key not in client_lookup:
            client_lookup[key] = client

    summary: dict[str, Any] = {
        "processed_companies": len(unique_companies),
        "created_clients": [],
        "existing_clients": [],
        "created_sites": [],
        "existing_sites": [],
        "skipped": skipped,
        "errors": [],
    }

    for company_name, company in unique_companies:
        client_key = company_name.casefold()
        existing_client = client_lookup.get(client_key)

        if not existing_client:
            logger.info(
                "Creating Tactical RMM client", company=company_name
            )
            create_payload = {
                "endpoint": "/clients/",
                "method": "POST",
                "body": {
                    "client": {"name": company_name},
                    "site": {"name": default_site},
                },
            }
            create_result = await _invoke_tacticalrmm(
                settings, create_payload, event_future=None
            )
            if create_result.get("status") == "succeeded":
                summary["created_clients"].append(company_name)
                summary["created_sites"].append(
                    {
                        "company": company_name,
                        "site": default_site,
                        "action": "created_with_client",
                    }
                )
                client_lookup[client_key] = {
                    "name": company_name,
                    "id": None,
                    "sites": [{"name": default_site}],
                }
            else:
                error_message = _summarise_event_error(create_result)
                summary["errors"].append(
                    {
                        "company": company_name,
                        "action": "create_client",
                        "error": error_message,
                        "event_id": create_result.get("event_id"),
                    }
                )
            continue

        summary["existing_clients"].append(company_name)
        existing_sites = []
        raw_sites = (
            existing_client.get("sites")
            if isinstance(existing_client, Mapping)
            else None
        )
        if isinstance(raw_sites, list):
            existing_sites = [
                site for site in raw_sites if isinstance(site, Mapping)
            ]

        has_default_site = False
        for site in existing_sites:
            site_name = str(site.get("name") or "").strip()
            if site_name.casefold() == default_site_key:
                has_default_site = True
                summary["existing_sites"].append(
                    {"company": company_name, "site": site_name}
                )
                break

        if has_default_site:
            continue

        client_id = existing_client.get("id")
        try:
            client_id_int = int(client_id) if client_id is not None else None
        except (TypeError, ValueError):
            client_id_int = None

        if client_id_int is None:
            summary["errors"].append(
                {
                    "company": company_name,
                    "action": "resolve_client_id",
                    "error": "Client ID missing; unable to create default site.",
                    "event_id": existing_clients_result.get("event_id"),
                }
            )
            continue

        logger.info(
            "Creating Tactical RMM default site",
            company=company_name,
            client_id=client_id_int,
        )
        site_payload = {
            "endpoint": "/clients/sites/",
            "method": "POST",
            "body": {"site": {"client": client_id_int, "name": default_site}},
        }
        site_result = await _invoke_tacticalrmm(
            settings, site_payload, event_future=None
        )
        if site_result.get("status") == "succeeded":
            summary["created_sites"].append(
                {
                    "company": company_name,
                    "site": default_site,
                    "action": "created_for_existing_client",
                }
            )
        else:
            error_message = _summarise_event_error(site_result)
            summary["errors"].append(
                {
                    "company": company_name,
                    "action": "create_site",
                    "error": error_message,
                    "event_id": site_result.get("event_id"),
                }
            )

    return summary
