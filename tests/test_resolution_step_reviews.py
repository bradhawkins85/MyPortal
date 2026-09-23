from unittest.mock import AsyncMock
import asyncio

import pytest

from app.services import resolution_step_reviews as service


def test_extract_steps_preserves_list_item_order():
    assert service.extract_steps("<ol><li>Restart the service.</li><li>Verify the queue.</li></ol>") == [
        "Restart the service.",
        "Verify the queue.",
    ]


def test_generate_article_creates_ordered_draft_with_twenty_word_title(monkeypatch):
    monkeypatch.setattr(
        service.review_repo,
        "get_entry",
        AsyncMock(return_value={
            "ticket_id": 42,
            "subject": "Printer queue stopped",
            "resolution_steps": "<ul><li>Restart spooler</li><li>Print a test page</li></ul>",
            "article_id": None,
        }),
    )
    monkeypatch.setattr(
        service.modules,
        "trigger_module",
        AsyncMock(return_value={"status": "succeeded", "response": "One two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty extra"}),
    )
    monkeypatch.setattr(service.modules, "module_result_succeeded", lambda result: True)
    create = AsyncMock(return_value={"id": 7, "slug": "ticket-42-resolution"})
    monkeypatch.setattr(service.knowledge_base, "create_article", create)
    link = AsyncMock()
    monkeypatch.setattr(service.review_repo, "link_article", link)

    article = asyncio.run(service.generate_article(42, author_id=3))

    payload = create.await_args.args[0]
    assert payload["is_published"] is False
    assert len(payload["title"].split()) == 20
    assert [section["content"] for section in payload["sections"]] == [
        "<p>Restart spooler</p>",
        "<p>Print a test page</p>",
    ]
    assert article["id"] == 7
    link.assert_awaited_once_with(42, 7, 3)


def test_generate_article_rejects_duplicate(monkeypatch):
    monkeypatch.setattr(
        service.review_repo,
        "get_entry",
        AsyncMock(return_value={"ticket_id": 42, "resolution_steps": "<li>Done</li>", "article_id": 7}),
    )
    with pytest.raises(ValueError, match="already been generated"):
        asyncio.run(service.generate_article(42, author_id=3))
