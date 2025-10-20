from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app.services import knowledge_base as knowledge_base_service


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _stub_company_memberships(monkeypatch):
    monkeypatch.setattr(
        knowledge_base_service.user_company_repo,
        "list_companies_for_user",
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
        "permission_scope": "anonymous",
        "is_published": True,
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
