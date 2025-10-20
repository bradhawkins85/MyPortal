from __future__ import annotations

import hashlib
import json
import os
import string
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import urljoin

import httpx
from loguru import logger

from app.core.database import db
from app.repositories import integration_modules as module_repo
from app.services import email as email_service

REQUEST_TIMEOUT = httpx.Timeout(15.0, connect=5.0)

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
        "slug": "chatgpt-mcp",
        "name": "ChatGPT MCP",
        "description": "Expose ticketing tools to ChatGPT via the Model Context Protocol.",
        "icon": "🤖",
        "settings": _default_chatgpt_settings(),
    },
]


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
        merged.update(
            {
                "base_url": str(merged.get("base_url", "")).strip(),
                "api_key": str(merged.get("api_key", "")).strip(),
                "verify_ssl": _ensure_bool(merged.get("verify_ssl"), True),
            }
        )
    elif slug == "ntfy":
        merged.update(
            {
                "base_url": str(merged.get("base_url", "")).strip() or "https://ntfy.sh",
                "topic": str(merged.get("topic", "")).strip(),
                "auth_token": str(merged.get("auth_token", "")).strip(),
            }
        )
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
    return merged


def _redact_module_settings(module: dict[str, Any]) -> dict[str, Any]:
    slug = module.get("slug")
    if slug not in {"chatgpt-mcp", "syncro"}:
        return module
    redacted = dict(module)
    settings = dict(redacted.get("settings") or {})
    if slug == "chatgpt-mcp" and settings.get("shared_secret_hash"):
        settings["shared_secret_hash"] = "********"
    if slug == "syncro" and settings.get("api_key"):
        settings["api_key"] = "********"
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
) -> dict[str, Any] | None:
    existing = await module_repo.get_module(slug)
    coerced = _coerce_settings(slug, settings, existing) if settings is not None else None
    updated = await module_repo.update_module(slug, enabled=enabled, settings=coerced)
    return _redact_module_settings(updated) if updated else None


async def trigger_module(slug: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    module = await module_repo.get_module(slug)
    if not module:
        raise ValueError(f"Module {slug} is not configured")
    if not module.get("enabled"):
        return {"status": "skipped", "reason": "Module disabled"}
    raw_settings = module.get("settings")
    if isinstance(raw_settings, Mapping):
        settings = _coerce_settings(slug, raw_settings)
    else:
        try:
            parsed = json.loads(raw_settings) if isinstance(raw_settings, str) else None
        except json.JSONDecodeError:
            parsed = None
        settings = _coerce_settings(slug, parsed)
    handler = {
        "syncro": _validate_syncro,
        "ollama": _invoke_ollama,
        "smtp": _invoke_smtp,
        "tacticalrmm": _invoke_tacticalrmm,
        "ntfy": _invoke_ntfy,
        "chatgpt-mcp": _invoke_chatgpt_mcp,
    }.get(slug)
    if not handler:
        raise ValueError(f"No handler registered for module {slug}")
    return await handler(settings, payload or {})


async def _invoke_ollama(settings: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    base_url = str(settings.get("base_url") or "http://127.0.0.1:11434").rstrip("/")
    model = str(settings.get("model") or "llama3")
    default_prompt = str(settings.get("prompt") or "")
    prompt = str(payload.get("prompt") or payload.get("text") or default_prompt)
    if not prompt:
        raise ValueError("Ollama prompt cannot be empty")
    endpoint = urljoin(f"{base_url}/", "api/generate")
    body = {"model": model, "prompt": prompt, "stream": False}
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await client.post(endpoint, json=body)
        response.raise_for_status()
        data = response.json()
    return {
        "status": "succeeded",
        "response": data,
        "model": model,
        "endpoint": endpoint,
    }


async def _invoke_smtp(settings: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    recipients = _ensure_list(payload.get("recipients")) or _ensure_list(settings.get("default_recipients"))
    subject_prefix = str(settings.get("subject_prefix") or "").strip()
    subject = str(payload.get("subject") or "Automation notification")
    if subject_prefix:
        subject = f"{subject_prefix} {subject}".strip()
    html_body = str(payload.get("html") or payload.get("body") or "<p>Automation triggered.</p>")
    text_body = payload.get("text")
    sender = str(settings.get("from_address") or "") or None
    sent = await email_service.send_email(
        subject=subject,
        recipients=recipients,
        html_body=html_body,
        text_body=str(text_body) if text_body is not None else None,
        sender=sender,
    )
    return {"status": "succeeded" if sent else "skipped", "recipients": recipients, "subject": subject}


async def _invoke_tacticalrmm(settings: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    base_url = str(settings.get("base_url") or "").rstrip("/")
    if not base_url:
        raise ValueError("Tactical RMM base URL is not configured")
    api_key = str(settings.get("api_key") or "").strip()
    verify_ssl = _ensure_bool(settings.get("verify_ssl"), True)
    endpoint_path = str(payload.get("endpoint") or payload.get("path") or "/api/v3/tasks/run").lstrip("/")
    method = str(payload.get("method") or "POST").upper()
    headers = {"Content-Type": "application/json"}
    auth_header = str(payload.get("auth_header") or "Authorization")
    if api_key:
        headers[auth_header] = payload.get("auth_prefix", "Token ") + api_key
    extra_headers = payload.get("headers")
    if isinstance(extra_headers, Mapping):
        for key, value in extra_headers.items():
            headers[str(key)] = str(value)
    request_body = payload.get("body")
    url = urljoin(f"{base_url}/", endpoint_path)
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, verify=verify_ssl) as client:
        response = await client.request(method, url, json=request_body, headers=headers)
        response.raise_for_status()
        try:
            data = response.json()
        except json.JSONDecodeError:
            data = response.text
    return {
        "status": "succeeded",
        "response": data,
        "status_code": response.status_code,
        "url": url,
        "method": method,
    }


async def _invoke_ntfy(settings: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    base_url = str(settings.get("base_url") or "https://ntfy.sh").rstrip("/")
    topic = str(payload.get("topic") or settings.get("topic") or "").strip()
    if not topic:
        raise ValueError("ntfy topic must be configured")
    message = str(payload.get("message") or payload.get("body") or "Automation triggered")
    priority = str(payload.get("priority") or "default").strip()
    url = f"{base_url}/{topic}"
    headers: dict[str, str] = {"Title": str(payload.get("title") or "Automation event")}
    token = str(settings.get("auth_token") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    headers["Priority"] = priority
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await client.post(url, data=message.encode("utf-8"), headers=headers)
        response.raise_for_status()
    return {
        "status": "succeeded",
        "topic": topic,
        "priority": priority,
        "url": url,
    }


async def _invoke_chatgpt_mcp(settings: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
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


async def _validate_syncro(settings: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
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


async def test_module(slug: str) -> dict[str, Any]:
    try:
        result = await trigger_module(slug, {})
        return {"status": "ok", "details": result}
    except Exception as exc:  # pragma: no cover - network dependent
        logger.warning("Module test failed", slug=slug, error=str(exc))
        return {"status": "error", "error": str(exc)}
