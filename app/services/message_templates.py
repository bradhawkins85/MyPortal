from __future__ import annotations

import re
import json
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


def _normalise_slug(slug: str) -> str:
    candidate = slug.strip().lower().replace(" ", "-")
    if not _SLUG_PATTERN.fullmatch(candidate):
        raise ValueError(
            "Slug must contain only lowercase letters, numbers, dots, hyphens, or underscores"
        )
    return candidate


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
