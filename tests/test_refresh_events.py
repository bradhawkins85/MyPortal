import pytest
from unittest.mock import AsyncMock

from app.services import knowledge_base as kb_service
from app.services import modules as modules_service
from app.services.realtime import RefreshNotifier


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_create_article_broadcasts_refresh(monkeypatch):
    notifier = AsyncMock(spec=RefreshNotifier)

    monkeypatch.setattr(kb_service, "_schedule_article_ai_tags", AsyncMock())
    monkeypatch.setattr(kb_service.kb_repo, "create_article", AsyncMock(return_value={"id": 11, "slug": "intro"}))
    monkeypatch.setattr(kb_service.kb_repo, "replace_article_sections", AsyncMock())
    monkeypatch.setattr(kb_service.kb_repo, "replace_article_users", AsyncMock())
    monkeypatch.setattr(kb_service.kb_repo, "replace_article_companies", AsyncMock())
    monkeypatch.setattr(
        kb_service.kb_repo,
        "get_article_by_id",
        AsyncMock(return_value={"id": 11, "slug": "intro", "title": "Intro"}),
    )

    payload = {
        "slug": "intro",
        "title": "Intro",
        "summary": "Welcome",
        "permission_scope": "anonymous",
        "is_published": True,
        "sections": [{"heading": "Overview", "content": "<p>Hello</p>"}],
    }

    await kb_service.create_article(payload, author_id=7, notifier=notifier)

    notifier.broadcast_refresh.assert_awaited_once_with(reason="knowledge_base:article_created")


@pytest.mark.anyio("asyncio")
async def test_update_article_broadcasts_refresh(monkeypatch):
    notifier = AsyncMock(spec=RefreshNotifier)

    current_article = {"id": 21, "slug": "intro", "title": "Intro", "sections": [], "permission_scope": "anonymous"}
    refreshed_article = dict(current_article, title="Intro Updated")

    monkeypatch.setattr(
        kb_service.kb_repo,
        "get_article_by_id",
        AsyncMock(side_effect=[current_article, refreshed_article]),
    )
    monkeypatch.setattr(kb_service, "_schedule_article_ai_tags", AsyncMock())
    monkeypatch.setattr(kb_service.kb_repo, "update_article", AsyncMock(return_value=refreshed_article))
    monkeypatch.setattr(kb_service.kb_repo, "replace_article_sections", AsyncMock())
    monkeypatch.setattr(kb_service.kb_repo, "replace_article_users", AsyncMock())
    monkeypatch.setattr(kb_service.kb_repo, "replace_article_companies", AsyncMock())

    payload = {
        "title": "Intro Updated",
        "sections": [{"heading": "Overview", "content": "<p>Updated</p>"}],
    }

    await kb_service.update_article(21, payload, notifier=notifier)

    notifier.broadcast_refresh.assert_awaited_once_with(reason="knowledge_base:article_updated")


@pytest.mark.anyio("asyncio")
async def test_delete_article_broadcasts_refresh(monkeypatch):
    notifier = AsyncMock(spec=RefreshNotifier)
    delete_mock = AsyncMock()
    monkeypatch.setattr(kb_service.kb_repo, "delete_article", delete_mock)

    await kb_service.delete_article(33, notifier=notifier)

    delete_mock.assert_awaited_once_with(33)
    notifier.broadcast_refresh.assert_awaited_once_with(reason="knowledge_base:article_deleted")


@pytest.mark.anyio("asyncio")
async def test_update_module_broadcasts_refresh(monkeypatch):
    notifier = AsyncMock(spec=RefreshNotifier)
    existing = {"slug": "smtp", "enabled": True, "settings": {"from_address": "test@example.com"}}
    updated = dict(existing, enabled=False)

    monkeypatch.setattr(modules_service.module_repo, "get_module", AsyncMock(return_value=existing))
    monkeypatch.setattr(modules_service, "_coerce_settings", lambda slug, settings, existing=None: settings)
    monkeypatch.setattr(modules_service.module_repo, "update_module", AsyncMock(return_value=updated))

    await modules_service.update_module(
        "smtp",
        enabled=False,
        settings={"from_address": "noreply@example.com"},
        notifier=notifier,
    )

    notifier.broadcast_refresh.assert_awaited_once_with(reason="modules:updated:smtp")
