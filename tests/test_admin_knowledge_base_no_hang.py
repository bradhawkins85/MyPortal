"""Guarantee ``GET /admin/knowledge-base`` can never hang a worker indefinitely.

Regression test for the production incident where the admin knowledge base page
hung forever with no client error. The page wraps its work in an
``asyncio.wait_for`` backstop, so a stalling downstream database or Redis call
must surface as a bounded 504 instead of blocking the request forever.

The tests are dependency-free: every external call (auth, services, template
render) is mocked so they run without a live MySQL/Redis.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from starlette.testclient import TestClient

import app.main as main_module
import app.services.knowledge_base as kb_service
import app.services.rag_relationships as rag_service
from app.features.knowledge_base import routes as kb_routes


SUPER_ADMIN = {"id": 1, "is_super_admin": True}


def _make_client() -> TestClient:
    app = FastAPI()
    app.include_router(kb_routes.router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def fast_page_timeout(monkeypatch):
    """Shrink the page backstop so the hang test resolves in well under a second."""
    monkeypatch.setattr(kb_routes, "KB_PAGE_TIMEOUT", 0.5)
    return 0.5


@pytest.fixture
def mocked_deps(monkeypatch):
    """Stub auth, data services, and template rendering so no DB/Redis is touched."""
    monkeypatch.setattr(
        main_module, "_require_super_admin_page",
        lambda request: asyncio.sleep(0, result=(SUPER_ADMIN, None)),
    )

    async def _render_template(name, request, user, *, extra=None):
        return HTMLResponse("<html><title>Knowledge base admin</title></html>")

    monkeypatch.setattr(main_module, "_render_template", _render_template)

    async def _no_signals(*args, **kwargs):
        return {}

    monkeypatch.setattr(
        kb_service, "build_access_context",
        AsyncMock(side_effect=AssertionError("Catalogue must not load company memberships")),
    )
    monkeypatch.setattr(kb_routes, "KB_REVIEW_SIGNALS_TIMEOUT", 0.05)
    monkeypatch.setattr(rag_service, "kb_articles_needing_update", _no_signals)
    return monkeypatch


def test_admin_kb_page_renders_when_services_are_fast(
    mocked_deps, fast_page_timeout, monkeypatch
):
    async def _articles(context, **kwargs):
        return []

    monkeypatch.setattr(kb_service, "list_admin_article_summaries", _articles)

    client = _make_client()
    with client:
        response = client.get("/admin/knowledge-base")

    assert response.status_code == 200
    assert "Knowledge base admin" in response.text


def test_admin_kb_page_returns_504_instead_of_hanging(
    mocked_deps, fast_page_timeout, monkeypatch
):
    async def _stall(context, **kwargs):
        # Simulate a downstream call that blocks far longer than the page backstop.
        await asyncio.sleep(5)
        return []

    monkeypatch.setattr(kb_service, "list_admin_article_summaries", _stall)

    client = _make_client()
    started = time.monotonic()
    with client:
        response = client.get("/admin/knowledge-base")
    elapsed = time.monotonic() - started

    assert response.status_code == 504
    # Must resolve at the backstop (~0.5s), not after the 5s stall.
    assert elapsed < 3.0
    assert "too long" in response.text.lower()


@pytest.mark.parametrize("failure", ["stall", "error"])
def test_admin_kb_still_renders_when_optional_review_signals_fail(
    mocked_deps, fast_page_timeout, monkeypatch, failure
):
    article = {"id": 7, "is_published": False, "lifecycle_status": "draft"}
    monkeypatch.setattr(
        kb_service, "list_admin_article_summaries", AsyncMock(return_value=[article])
    )

    async def _signals():
        if failure == "error":
            raise RuntimeError("Relationship database unavailable")
        await asyncio.sleep(5)
        return {}

    render = AsyncMock(return_value=HTMLResponse("Knowledge base admin"))
    monkeypatch.setattr(rag_service, "kb_articles_needing_update", _signals)
    monkeypatch.setattr(main_module, "_render_template", render)
    with _make_client() as client:
        response = client.get("/admin/knowledge-base")
    assert response.status_code == 200
    extra = render.call_args.kwargs["extra"]
    assert extra["kb_articles"][0]["id"] == 7
    assert extra["kb_status_counts"]["draft"] == 1
    assert extra["kb_review_signals_unavailable"] is True


def test_admin_kb_keeps_review_signals_when_available(
    mocked_deps, fast_page_timeout, monkeypatch
):
    monkeypatch.setattr(
        kb_service, "list_admin_article_summaries",
        AsyncMock(return_value=[{"id": 7, "is_published": True}]),
    )
    signal = {"ticket_count": 3, "tickets": []}
    monkeypatch.setattr(rag_service, "kb_articles_needing_update", AsyncMock(return_value={7: signal}))
    render = AsyncMock(return_value=HTMLResponse("Knowledge base admin"))
    monkeypatch.setattr(main_module, "_render_template", render)
    with _make_client() as client:
        assert client.get("/admin/knowledge-base").status_code == 200
    extra = render.call_args.kwargs["extra"]
    assert extra["kb_articles"][0]["needs_update"] == signal
    assert extra["kb_status_counts"]["needs_update"] == 1
    assert extra["kb_review_signals_unavailable"] is False


def test_admin_kb_redirect_does_not_load_catalogue(monkeypatch):
    from fastapi.responses import RedirectResponse

    monkeypatch.setattr(
        main_module, "_require_super_admin_page",
        AsyncMock(return_value=(None, RedirectResponse("/login"))),
    )
    load = AsyncMock()
    monkeypatch.setattr(kb_service, "list_admin_article_summaries", load)
    with _make_client() as client:
        response = client.get("/admin/knowledge-base", follow_redirects=False)
    assert response.status_code == 307
    load.assert_not_awaited()
