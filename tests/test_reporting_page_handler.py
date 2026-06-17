"""Regression coverage for the reporting page handler."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from starlette.requests import Request

from app.features.reporting import handlers as reporting_handlers
from app.repositories import reporting as reporting_repo


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/reporting", "headers": []})


def test_reporting_page_renders_without_error_query(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_require_reporting_access(request):
        return {"id": 1, "is_super_admin": True}, True, None

    async def fake_list_queries():
        return []

    async def fake_render_template(template, request, user, *, extra):
        captured["template"] = template
        captured["extra"] = extra
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(reporting_handlers, "_require_reporting_access", fake_require_reporting_access)
    monkeypatch.setattr(
        reporting_handlers,
        "_main",
        lambda: SimpleNamespace(_render_template=fake_render_template),
    )
    monkeypatch.setattr(reporting_repo, "list_queries", fake_list_queries)

    async def _run() -> None:
        response = await reporting_handlers.reporting_page(_request(), report=None, error=None)
        assert response.status_code == 200

    asyncio.new_event_loop().run_until_complete(_run())

    assert captured["template"] == "reporting/index.html"
    assert captured["extra"]["error_message"] is None


def test_reporting_page_sanitises_error_query(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_require_reporting_access(request):
        return {"id": 1, "is_super_admin": True}, True, None

    async def fake_list_queries():
        return []

    async def fake_render_template(template, request, user, *, extra):
        captured["extra"] = extra
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(reporting_handlers, "_require_reporting_access", fake_require_reporting_access)
    monkeypatch.setattr(
        reporting_handlers,
        "_main",
        lambda: SimpleNamespace(_render_template=fake_render_template),
    )
    monkeypatch.setattr(reporting_repo, "list_queries", fake_list_queries)

    async def _run() -> None:
        await reporting_handlers.reporting_page(
            _request(),
            report=None,
            error="  Failed to run report  ",
        )

    asyncio.new_event_loop().run_until_complete(_run())

    assert captured["extra"]["error_message"] == "Failed to run report"
