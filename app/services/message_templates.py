from __future__ import annotations

import re
import json
from html import escape as html_escape
from dataclasses import asdict, dataclass
from datetime import datetime
from threading import RLock
from typing import Any, Mapping

from loguru import logger
from redis.exceptions import RedisError

from app.repositories import message_templates as template_repo
from app.services.redis import get_redis_client


@dataclass(slots=True)
class MessageTemplate:
    id: int
    slug: str
    name: str
    description: str | None
    content_type: str
    content: str
    created_at: datetime | None
    updated_at: datetime | None


_SLUG_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,118}[a-z0-9])?$")
_CONTENT_TYPE_MAP = {
    "text": "text/plain",
    "plain": "text/plain",
    "text/plain": "text/plain",
    "html": "text/html",
    "text/html": "text/html",
}

_CACHE_LOCK = RLock()
_TEMPLATE_CACHE: dict[str, MessageTemplate] = {}
_REDIS_CACHE_KEY = "message-templates:records"



_TOKEN_PATTERN = re.compile(r"{{\s*([a-zA-Z0-9_.-]+)\s*}}")


def _resolve_context_value(context: Mapping[str, Any], path: str) -> Any:
    current: Any = context
    for part in path.split("."):
        if isinstance(current, Mapping):
            current = current.get(part)
        else:
            current = getattr(current, part, None)
        if current is None:
            return ""
    return current


def render_content(content: str, context: Mapping[str, Any], *, escape_html: bool = False) -> str:
    """Render ``{{ dotted.path }}`` tokens using the supplied context."""

    def replace(match: re.Match[str]) -> str:
        value = _resolve_context_value(context, match.group(1))
        rendered = "" if value is None else str(value)
        return html_escape(rendered, quote=True) if escape_html else rendered

    return _TOKEN_PATTERN.sub(replace, content)


async def render_template_content(
    slug: str,
    context: Mapping[str, Any],
    *,
    default_content: str,
    default_content_type: str = "text/html",
) -> tuple[str, str]:
    """Render a stored message template or a caller-provided default."""

    template = await get_template_by_slug(slug)
    content = str((template or {}).get("content") or default_content)
    content_type = _normalise_content_type((template or {}).get("content_type") or default_content_type)
    return render_content(content, context, escape_html=content_type == "text/html"), content_type


def _normalise_slug(slug: str) -> str:
    candidate = slug.strip().lower().replace(" ", "-")
    if not _SLUG_PATTERN.fullmatch(candidate):
        raise ValueError(
            "Slug must contain only lowercase letters, numbers, dots, hyphens, or underscores"
        )
    return candidate


def _build_clone_slug_candidate(source_slug: str, index: int | None = None) -> str:
    suffix = "-copy" if index is None else f"-copy-{index}"
    max_base_length = 120 - len(suffix)
    base = source_slug[:max_base_length].rstrip("._-")
    return f"{base}{suffix}"


async def _generate_clone_slug(source_slug: str) -> str:
    candidate = _build_clone_slug_candidate(source_slug)
    if not await template_repo.get_template_by_slug(candidate):
        return candidate
    for index in range(2, 1000):
        candidate = _build_clone_slug_candidate(source_slug, index)
        if not await template_repo.get_template_by_slug(candidate):
            return candidate
    raise ValueError("Unable to generate a unique clone slug")


def _normalise_content_type(content_type: str | None) -> str:
    if not content_type:
        return "text/plain"
    candidate = content_type.strip().lower()
    return _CONTENT_TYPE_MAP.get(candidate, "text/plain")


def _coerce_template(record: Mapping[str, Any]) -> MessageTemplate:
    slug = _normalise_slug(str(record.get("slug") or ""))
    content_type = _normalise_content_type(record.get("content_type"))
    description = record.get("description")
    if description is not None and not isinstance(description, str):
        description = str(description)
    created_at = record.get("created_at")
    updated_at = record.get("updated_at")
    if created_at is not None and not isinstance(created_at, datetime):
        created_at = None
    if updated_at is not None and not isinstance(updated_at, datetime):
        updated_at = None
    return MessageTemplate(
        id=int(record.get("id") or 0),
        slug=slug,
        name=str(record.get("name") or slug),
        description=description,
        content_type=content_type,
        content=str(record.get("content") or ""),
        created_at=created_at,
        updated_at=updated_at,
    )


def _to_dict(template: MessageTemplate) -> dict[str, Any]:
    return asdict(template)


def _set_cache(records: list[MessageTemplate]) -> None:
    with _CACHE_LOCK:
        _TEMPLATE_CACHE.clear()
        for template in records:
            _TEMPLATE_CACHE[template.slug] = template


async def _persist_cache_to_redis(records: list[MessageTemplate]) -> None:
    client = get_redis_client()
    if not client:
        return
    payload = [_to_dict(template) for template in records]
    try:
        await client.set(_REDIS_CACHE_KEY, json.dumps(payload))
    except RedisError as exc:
        logger.warning("Failed to persist message template cache to Redis", error=str(exc))


async def _load_cache_from_redis() -> bool:
    client = get_redis_client()
    if not client:
        return False
    try:
        cached = await client.get(_REDIS_CACHE_KEY)
    except RedisError as exc:
        logger.warning("Failed to load message template cache from Redis", error=str(exc))
        return False
    if not cached:
        return False
    try:
        records = json.loads(cached)
    except (TypeError, ValueError):
        return False
    if not isinstance(records, list):
        return False
    templates: list[MessageTemplate] = []
    for record in records:
        if isinstance(record, Mapping):
            templates.append(_coerce_template(record))
    _set_cache(templates)
    return True


async def preload_cache() -> None:
    if await _load_cache_from_redis():
        logger.debug("Loaded message templates from Redis cache")
        return
    await refresh_cache()


async def refresh_cache() -> None:
    """Reload the in-memory template cache from the database."""

    logger.debug("Refreshing message template cache")
    templates: list[MessageTemplate] = []
    offset = 0
    batch_size = 200
    while True:
        rows = await template_repo.list_templates(limit=batch_size, offset=offset)
        if not rows:
            break
        templates.extend(_coerce_template(row) for row in rows)
        if len(rows) < batch_size:
            break
        offset += batch_size
    _set_cache(templates)
    await _persist_cache_to_redis(templates)


def iter_templates() -> list[dict[str, Any]]:
    with _CACHE_LOCK:
        return [_to_dict(template) for template in _TEMPLATE_CACHE.values()]


def get_template_from_cache(slug: str) -> dict[str, Any] | None:
    key = _normalise_slug(slug)
    with _CACHE_LOCK:
        template = _TEMPLATE_CACHE.get(key)
        if not template:
            return None
        return _to_dict(template)


async def list_templates(
    *,
    search: str | None = None,
    content_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    rows = await template_repo.list_templates(
        search=search,
        content_type=_normalise_content_type(content_type) if content_type else None,
        limit=limit,
        offset=offset,
    )
    return [_to_dict(_coerce_template(row)) for row in rows]


async def get_template(template_id: int) -> dict[str, Any] | None:
    record = await template_repo.get_template(template_id)
    if not record:
        return None
    return _to_dict(_coerce_template(record))


async def get_template_by_slug(slug: str) -> dict[str, Any] | None:
    try:
        normalised = _normalise_slug(slug)
    except ValueError:
        return None
    record = await template_repo.get_template_by_slug(normalised)
    if not record:
        return None
    return _to_dict(_coerce_template(record))


async def create_template(
    *,
    slug: str,
    name: str,
    description: str | None,
    content_type: str,
    content: str,
) -> dict[str, Any]:
    normalised_slug = _normalise_slug(slug)
    normalised_content_type = _normalise_content_type(content_type)
    existing = await template_repo.get_template_by_slug(normalised_slug)
    if existing:
        raise ValueError("A template with this slug already exists")
    clean_description = description.strip() if isinstance(description, str) else description
    if isinstance(clean_description, str) and not clean_description:
        clean_description = None
    record = await template_repo.create_template(
        slug=normalised_slug,
        name=name.strip(),
        description=clean_description,
        content_type=normalised_content_type,
        content=content,
    )
    template = _coerce_template(record)
    await refresh_cache()
    return _to_dict(template)


async def clone_template(template_id: int) -> dict[str, Any] | None:
    source_record = await template_repo.get_template(template_id)
    if not source_record:
        return None
    source = _coerce_template(source_record)
    clone_slug = await _generate_clone_slug(source.slug)
    clone_name = f"{source.name} (Copy)"
    record = await template_repo.create_template(
        slug=clone_slug,
        name=clone_name,
        description=source.description,
        content_type=source.content_type,
        content=source.content,
    )
    template = _coerce_template(record)
    await refresh_cache()
    return _to_dict(template)


async def update_template(template_id: int, **fields: Any) -> dict[str, Any] | None:
    updates: dict[str, Any] = {}
    if "slug" in fields and fields["slug"] is not None:
        updates["slug"] = _normalise_slug(str(fields["slug"]))
        existing_with_slug = await template_repo.get_template_by_slug(updates["slug"])
        if existing_with_slug and int(existing_with_slug.get("id") or 0) != template_id:
            raise ValueError("A template with this slug already exists")
    if "name" in fields and fields["name"] is not None:
        updates["name"] = str(fields["name"]).strip()
    if "description" in fields:
        desc = fields["description"]
        if isinstance(desc, str):
            desc = desc.strip()
            updates["description"] = desc or None
        else:
            updates["description"] = desc
    if "content_type" in fields and fields["content_type"] is not None:
        updates["content_type"] = _normalise_content_type(str(fields["content_type"]))
    if "content" in fields and fields["content"] is not None:
        updates["content"] = str(fields["content"])
    record = await template_repo.update_template(template_id, **updates)
    if not record:
        return None
    await refresh_cache()
    return _to_dict(_coerce_template(record))


async def delete_template(template_id: int) -> bool:
    await template_repo.delete_template(template_id)
    await refresh_cache()
    return True
