from __future__ import annotations

import pytest

from app.services import message_templates as service


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
