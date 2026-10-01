from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.services import message_templates as service


class _FakeRedis:
    def __init__(self):
        self.value = None

    async def set(self, key, value):
        assert key == "message-templates:records"
        self.value = value

    async def get(self, key):
        assert key == "message-templates:records"
        return self.value


@pytest.mark.asyncio
async def test_redis_cache_round_trips_template_datetimes(monkeypatch):
    redis = _FakeRedis()
    created_at = datetime(2026, 9, 29, 14, 28, 57, tzinfo=timezone.utc)
    updated_at = datetime(2026, 9, 29, 14, 29, 1, tzinfo=timezone.utc)
    template = service.MessageTemplate(
        id=7,
        slug="welcome.email",
        name="Welcome Email",
        description=None,
        content_type="text/html",
        content="<p>Welcome</p>",
        created_at=created_at,
        updated_at=updated_at,
    )
    monkeypatch.setattr(service, "get_redis_client", lambda: redis)

    await service._persist_cache_to_redis([template])

    persisted = json.loads(redis.value)
    assert persisted[0]["created_at"] == "2026-09-29T14:28:57+00:00"
    assert persisted[0]["updated_at"] == "2026-09-29T14:29:01+00:00"

    service._set_cache([])
    assert await service._load_cache_from_redis() is True
    cached = service.get_template_from_cache("welcome.email")
    assert cached is not None
    assert cached["created_at"] == created_at
    assert cached["updated_at"] == updated_at


@pytest.mark.asyncio
async def test_clone_template_copies_content_with_unique_slug(monkeypatch):
    records = [
        {
            "id": 7,
            "slug": "welcome.email",
            "name": "Welcome Email",
            "description": "Sent to new users",
            "content_type": "text/html",
            "content": "<p>Hello {{ user.first_name }}</p>",
            "created_at": None,
            "updated_at": None,
        },
        {"id": 8, "slug": "welcome.email-copy"},
    ]
    created_payload = {}

    async def fake_get_template(template_id):
        assert template_id == 7
        return records[0]

    async def fake_get_template_by_slug(slug):
        return next((record for record in records if record.get("slug") == slug), None)

    async def fake_create_template(**kwargs):
        created_payload.update(kwargs)
        return {"id": 9, "created_at": None, "updated_at": None, **kwargs}

    async def fake_refresh_cache():
        return None

    monkeypatch.setattr(service.template_repo, "get_template", fake_get_template)
    monkeypatch.setattr(service.template_repo, "get_template_by_slug", fake_get_template_by_slug)
    monkeypatch.setattr(service.template_repo, "create_template", fake_create_template)
    monkeypatch.setattr(service, "refresh_cache", fake_refresh_cache)

    cloned = await service.clone_template(7)

    assert cloned["id"] == 9
    assert cloned["slug"] == "welcome.email-copy-2"
    assert cloned["name"] == "Welcome Email (Copy)"
    assert cloned["description"] == "Sent to new users"
    assert cloned["content_type"] == "text/html"
    assert cloned["content"] == "<p>Hello {{ user.first_name }}</p>"
    assert created_payload["slug"] == "welcome.email-copy-2"


@pytest.mark.asyncio
async def test_clone_template_returns_none_when_source_missing(monkeypatch):
    async def fake_get_template(template_id):
        return None

    monkeypatch.setattr(service.template_repo, "get_template", fake_get_template)

    assert await service.clone_template(404) is None
