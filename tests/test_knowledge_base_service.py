from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.services import knowledge_base as knowledge_base_service


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _stub_company_memberships(monkeypatch):
    monkeypatch.setattr(
        knowledge_base_service.company_access,
        "list_accessible_companies",
        AsyncMock(return_value=[]),
    )


def _article_factory(**overrides):
    now = datetime.now(timezone.utc)
    base = {
        "id": 1,
        "slug": "public",
        "title": "Public article",
        "summary": "",
        "content": "Public content",
        "sections": [
            {"position": 1, "heading": "Overview", "content": "<p>Public content</p>"}
        ],
        "permission_scope": "anonymous",
        "is_published": True,
        "ai_tags": ["public"],
        "excluded_ai_tags": [],
        "created_by": 1,
        "created_at": now,
        "updated_at": now,
        "published_at": now,
        "created_at_utc": now,
        "updated_at_utc": now,
        "published_at_utc": now,
        "allowed_user_ids": [],
        "company_ids": [],
        "company_admin_ids": [],
    }
    base.update(overrides)
    return base


@pytest.mark.anyio("asyncio")
async def test_list_articles_filters_unreachable_scopes(monkeypatch):
    articles = [
        _article_factory(),
        _article_factory(
            id=2,
            slug="personal",
            permission_scope="user",
            allowed_user_ids=[9],
        ),
    ]
    monkeypatch.setattr(
        knowledge_base_service.kb_repo,
        "list_articles",
        AsyncMock(return_value=articles),
    )
    context = await knowledge_base_service.build_access_context(None)
    visible = await knowledge_base_service.list_articles_for_context(context)
    assert len(visible) == 1
    assert visible[0]["slug"] == "public"


@pytest.mark.anyio("asyncio")
async def test_get_article_by_slug_respects_user_permissions(monkeypatch):
    restricted = _article_factory(
        id=3,
        slug="restricted",
        permission_scope="user",
        allowed_user_ids=[12],
    )
    monkeypatch.setattr(
        knowledge_base_service.kb_repo,
        "get_article_by_slug",
        AsyncMock(return_value=restricted),
    )
    denied_context = await knowledge_base_service.build_access_context({"id": 8, "is_super_admin": False})
    assert await knowledge_base_service.get_article_by_slug_for_context("restricted", denied_context) is None

    allowed_context = await knowledge_base_service.build_access_context({"id": 12, "is_super_admin": False})
    article = await knowledge_base_service.get_article_by_slug_for_context("restricted", allowed_context)
    assert article is not None
    assert article["slug"] == "restricted"
    assert article["sections"]


@pytest.mark.anyio("asyncio")
async def test_search_articles_returns_ollama_summary(monkeypatch):
    articles = [
        _article_factory(content="Portal onboarding guide", slug="guide"),
        _article_factory(id=4, slug="internal", permission_scope="super_admin"),
    ]
    monkeypatch.setattr(
        knowledge_base_service.kb_repo,
        "list_articles",
        AsyncMock(return_value=articles),
    )
    monkeypatch.setattr(
        knowledge_base_service.modules_service,
        "trigger_module",
        AsyncMock(
            return_value={
                "status": "succeeded",
                "model": "llama3",
                "response": {"response": "Use the guide article for onboarding."},
            }
        ),
    )
    context = await knowledge_base_service.build_access_context({"id": 2, "is_super_admin": True})
    result = await knowledge_base_service.search_articles("onboarding", context)
    assert result["results"]
    assert result["ollama_status"] == "succeeded"
    assert "guide" in {item["slug"] for item in result["results"]}
    assert "onboarding" in (result["ollama_summary"] or "")


@pytest.mark.anyio("asyncio")
async def test_search_articles_skips_irrelevant_results(monkeypatch):
    articles = [
        _article_factory(slug="backup-procedures", title="Backup procedures", summary="Nightly backup steps"),
        _article_factory(
            id=7,
            slug="network-hardening",
            title="Network hardening",
            summary="Firewall lockdowns",
            content="<p>Follow the VPN hardening checklist.</p>",
            ai_tags=["vpn", "firewall"],
        ),
    ]

    monkeypatch.setattr(
        knowledge_base_service.kb_repo,
        "list_articles",
        AsyncMock(return_value=articles),
    )

    async def _fail_trigger(*args, **kwargs):  # pragma: no cover - should not be called
        raise AssertionError("Ollama should not run without matches")

    monkeypatch.setattr(
        knowledge_base_service.modules_service,
        "trigger_module",
        AsyncMock(side_effect=_fail_trigger),
    )

    context = await knowledge_base_service.build_access_context({"id": 2, "is_super_admin": False})
    result = await knowledge_base_service.search_articles("vpn", context, use_ollama=False)

    slugs = [item["slug"] for item in result["results"]]
    assert "network-hardening" in slugs
    assert "backup-procedures" not in slugs
    assert result["ollama_status"] == "skipped"


@pytest.mark.anyio("asyncio")
async def test_create_article_generates_ai_tags(monkeypatch):
    captured: dict[str, Any] = {}

    async def _create_article(**kwargs):
        captured.update(kwargs)
        return {"id": 21, "slug": kwargs["slug"]}

    refreshed_article = _article_factory(
        id=21,
        slug="vpn-guide",
        title="VPN Guide",
        summary="Configure remote access",
        ai_tags=["vpn", "remote access"],
    )

    update_article_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(knowledge_base_service.kb_repo, "create_article", AsyncMock(side_effect=_create_article))
    monkeypatch.setattr(knowledge_base_service.kb_repo, "replace_article_sections", AsyncMock())
    monkeypatch.setattr(knowledge_base_service.kb_repo, "replace_article_users", AsyncMock())
    monkeypatch.setattr(knowledge_base_service.kb_repo, "replace_article_companies", AsyncMock())
    monkeypatch.setattr(
        knowledge_base_service.kb_repo,
        "get_article_by_id",
        AsyncMock(return_value=refreshed_article),
    )
    monkeypatch.setattr(knowledge_base_service.kb_repo, "update_article", update_article_mock)

    async def fake_trigger(slug, payload, *, background=True, on_complete=None):
        assert slug == "ollama"
        result = {
            "status": "succeeded",
            "response": {"response": '["vpn", "remote access", "security"]'},
        }
        if on_complete:
            await on_complete(result)
        return {"status": "queued", "event_id": 101}

    trigger_mock = AsyncMock(side_effect=fake_trigger)
    monkeypatch.setattr(knowledge_base_service.modules_service, "trigger_module", trigger_mock)

    payload = {
        "slug": "vpn-guide",
        "title": "VPN Guide",
        "summary": "Configure remote access",
        "permission_scope": "anonymous",
        "is_published": True,
        "sections": [{"heading": "Overview", "content": "<p>Use the VPN client.</p>"}],
    }

    article = await knowledge_base_service.create_article(payload, author_id=7)

    assert captured["ai_tags"] is None
    assert article["ai_tags"] == ["vpn", "remote access"]
    trigger_mock.assert_awaited_once()
    assert update_article_mock.await_args.kwargs == {"ai_tags": ["vpn", "remote access", "security"]}


@pytest.mark.anyio("asyncio")
async def test_update_article_refreshes_ai_tags(monkeypatch):
    current_article = _article_factory(
        id=31,
        slug="security-basics",
        title="Security basics",
        summary="Initial guidance",
        ai_tags=["legacy"],
    )

    updated_article = _article_factory(
        id=31,
        slug="security-basics",
        title="Security fundamentals",
        summary="Initial guidance",
        ai_tags=["security", "hardening"],
    )

    monkeypatch.setattr(
        knowledge_base_service.kb_repo,
        "get_article_by_id",
        AsyncMock(side_effect=[current_article, updated_article, current_article, updated_article]),
    )
    update_mock = AsyncMock(return_value=dict(current_article, title="Security fundamentals"))
    monkeypatch.setattr(knowledge_base_service.kb_repo, "update_article", update_mock)
    monkeypatch.setattr(knowledge_base_service.kb_repo, "replace_article_sections", AsyncMock())
    monkeypatch.setattr(knowledge_base_service.kb_repo, "replace_article_users", AsyncMock())
    monkeypatch.setattr(knowledge_base_service.kb_repo, "replace_article_companies", AsyncMock())
    async def fake_trigger(slug, payload, *, background=True, on_complete=None):
        result = {"status": "succeeded", "response": {"response": '["security", "hardening"]'}}
        if on_complete:
            await on_complete(result)
        return {"status": "queued"}

    trigger_mock = AsyncMock(side_effect=fake_trigger)
    monkeypatch.setattr(knowledge_base_service.modules_service, "trigger_module", trigger_mock)

    payload = {
        "title": "Security fundamentals",
        "sections": [{"heading": "Overview", "content": "<p>Update your systems weekly.</p>"}],
    }

    article = await knowledge_base_service.update_article(31, payload)

    # First update call handles metadata changes, callback updates ai_tags
    assert len(update_mock.await_args_list) >= 2
    first_call_kwargs = update_mock.await_args_list[0].kwargs
    assert "ai_tags" not in first_call_kwargs
    ai_tag_call = next(
        kwargs for kwargs in (args.kwargs for args in update_mock.await_args_list) if "ai_tags" in kwargs
    )
    assert ai_tag_call["ai_tags"] == ["security", "hardening"]
    assert article["ai_tags"] == ["security", "hardening"]
    trigger_mock.assert_awaited_once()
