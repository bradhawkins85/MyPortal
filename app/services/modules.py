from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import urljoin

import httpx
from loguru import logger

from app.core.database import db
from app.repositories import integration_modules as module_repo
from app.services import email as email_service

DEFAULT_MODULES: list[dict[str, Any]] = [
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
]

REQUEST_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


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


def _coerce_settings(slug: str, payload: Mapping[str, Any] | None) -> dict[str, Any]:
    defaults = next((module["settings"] for module in DEFAULT_MODULES if module["slug"] == slug), {})
    merged = _merge_settings(defaults, payload)
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
    return merged


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
    return await module_repo.list_modules()


async def update_module(
    slug: str,
    *,
    enabled: bool | None = None,
    settings: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    coerced = _coerce_settings(slug, settings) if settings is not None else None
    return await module_repo.update_module(slug, enabled=enabled, settings=coerced)


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
        "ollama": _invoke_ollama,
        "smtp": _invoke_smtp,
        "tacticalrmm": _invoke_tacticalrmm,
        "ntfy": _invoke_ntfy,
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


async def test_module(slug: str) -> dict[str, Any]:
    try:
        result = await trigger_module(slug, {})
        return {"status": "ok", "details": result}
    except Exception as exc:  # pragma: no cover - network dependent
        logger.warning("Module test failed", slug=slug, error=str(exc))
        return {"status": "error", "error": str(exc)}
