"""Tests for knowledge base article PDF exports."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import HTTPException
import pytest
from starlette.requests import Request

from app.features.knowledge_base import routes


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "server": ("portal.example", 443),
            "path": "/knowledge-base/articles/getting-started/export.pdf",
            "query_string": b"",
            "headers": [],
        }
    )


def test_article_pdf_uses_the_same_access_context_as_article_view(monkeypatch):
    article = {
        "title": "Getting started",
        "summary": "A quick guide",
        "sections": [{"heading": "First step", "content": "<p>Sign in.</p>"}],
    }
    get_article = AsyncMock(return_value=article)
    build_context = AsyncMock(return_value={"user_id": 42})
    template = SimpleNamespace(render=lambda **context: f"PDF for {context['kb_article']['title']}")
    fake_main = SimpleNamespace(
        _get_optional_user=AsyncMock(return_value=({"id": 42}, None)),
        templates=SimpleNamespace(get_template=lambda name: template),
    )
    monkeypatch.setattr(routes, "_main", lambda: fake_main)
    monkeypatch.setattr(routes.knowledge_base_service, "build_access_context", build_context)
    monkeypatch.setattr(
        routes.knowledge_base_service,
        "get_article_by_slug_for_context",
        get_article,
    )
    monkeypatch.setattr(routes, "_write_pdf", lambda html, base_url: b"%PDF-test")

    response = asyncio.run(
        routes.knowledge_base_article_pdf(_request(), "getting-started")
    )

    assert response.body == b"%PDF-test"
    assert response.media_type == "application/pdf"
    assert response.headers["content-disposition"] == 'attachment; filename="getting-started.pdf"'
    get_article.assert_awaited_once_with(
        "getting-started",
        {"user_id": 42},
        include_unpublished=False,
        include_permissions=False,
    )


def test_article_pdf_returns_not_found_when_article_is_not_accessible(monkeypatch):
    fake_main = SimpleNamespace(
        _get_optional_user=AsyncMock(return_value=(None, None)),
    )
    monkeypatch.setattr(routes, "_main", lambda: fake_main)
    monkeypatch.setattr(
        routes.knowledge_base_service,
        "build_access_context",
        AsyncMock(return_value={"anonymous": True}),
    )
    monkeypatch.setattr(
        routes.knowledge_base_service,
        "get_article_by_slug_for_context",
        AsyncMock(return_value=None),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(routes.knowledge_base_article_pdf(_request(), "private-article"))

    assert exc_info.value.status_code == 404
