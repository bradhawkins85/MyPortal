"""KB keyword search uses technician feedback on similar tickets."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app.services import ai_learning
from app.services import knowledge_base as kb_service
from app.services.ai_learning import LinkLesson

ARTICLES = [
    {"id": 11, "slug": "vpn", "title": "VPN setup", "content": "<p>VPN</p>", "is_published": True},
    {"id": 12, "slug": "printer", "title": "Printer jams", "content": "<p>Printer</p>", "is_published": True},
]


@pytest.fixture
def search(monkeypatch):
    embed = AsyncMock(return_value=[1.0, 0.0])
    monkeypatch.setattr(kb_service.kb_repo, "list_articles", AsyncMock(return_value=ARTICLES))
    monkeypatch.setattr(kb_service, "_article_visible", lambda article, context: True)
    monkeypatch.setattr(kb_service, "_score_article", lambda article, query, tokens: 10)
    monkeypatch.setattr(kb_service, "embed_text", embed, raising=False)

    async def run(lessons):
        if isinstance(lessons, Exception):
            async def broken():
                raise lessons
            monkeypatch.setattr(ai_learning.quality_repo, "list_link_votes", broken)
            ai_learning.invalidate_cache()
        else:
            monkeypatch.setattr(ai_learning, "lessons_or_empty", AsyncMock(return_value=lessons))
        context = kb_service.ArticleAccessContext(user={"id": 1}, user_id=1, is_super_admin=True, memberships={})
        result = await kb_service.search_articles("printer jam", context, use_ollama=False)
        return [article.get("id") for article in result["results"]]

    run.embed = embed
    return run


def _lessons(article_id, count, embedding):
    now = datetime.now(timezone.utc)
    return {("knowledge_base", str(article_id)): [
        LinkLesson("knowledge_base", str(article_id), -1, embedding, now)] * count}


@pytest.mark.anyio
async def test_no_lessons_means_no_embedding_call(search):
    assert sorted(await search({})) == [11, 12]
    search.embed.assert_not_awaited()


@pytest.mark.anyio
async def test_downvotes_from_similar_tickets_remove_an_article(search):
    # Full penalty keeps 10% of the keyword score: 10 -> 1, under the threshold of 3.
    assert await search(_lessons(11, 10, (1.0, 0.0))) == [12]


@pytest.mark.anyio
async def test_downvotes_from_unrelated_tickets_change_nothing(search):
    assert sorted(await search(_lessons(11, 10, (0.0, 1.0)))) == [11, 12]


@pytest.mark.anyio
async def test_a_few_downvotes_reorder_without_removing(search):
    # Four 👎 (-0.2): 10 * (1 - 0.2 / 0.3 * 0.9) = 4, still above 3, ranked below 12.
    assert await search(_lessons(11, 4, (1.0, 0.0))) == [12, 11]


@pytest.mark.anyio
async def test_learning_failure_keeps_normal_results(search):
    assert sorted(await search(RuntimeError("database unavailable"))) == [11, 12]


@pytest.mark.anyio
async def test_embedding_failure_keeps_normal_results(search):
    search.embed.side_effect = RuntimeError("embedding server down")
    assert sorted(await search(_lessons(11, 10, (1.0, 0.0)))) == [11, 12]
