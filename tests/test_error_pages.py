from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("SESSION_SECRET", "test-session-secret")
os.environ.setdefault("TOTP_ENCRYPTION_KEY", "A" * 64)
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "user")
os.environ.setdefault("DB_PASSWORD", "password")
os.environ.setdefault("DB_NAME", "testdb")

import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from app import main


def _make_request(path: str = "/boom", query_string: str = "token=secret") -> Request:
    app = FastAPI()
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": query_string.encode("utf-8"),
        "headers": [(b"accept", b"text/html"), (b"x-request-id", b"req-123")],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 443),
        "app": app,
    }
    request = Request(scope)
    request.state.request_id = "req-123"
    return request


@pytest.mark.anyio
async def test_render_error_page_uses_safe_path_and_hides_detail_in_production(monkeypatch):
    request = _make_request()

    async def fake_get_optional_user(_request: Request):
        return None, None

    async def fake_build_portal_context(_request: Request, _user, *, extra=None):
        return {"request": _request, **(extra or {})}

    def fake_template_response(name: str, context: dict, status_code: int):
        return SimpleNamespace(template=name, context=context, status_code=status_code, headers={})

    monkeypatch.setattr(main, "_get_optional_user", fake_get_optional_user)
    monkeypatch.setattr(main, "_build_portal_context", fake_build_portal_context)
    monkeypatch.setattr(main.templates, "TemplateResponse", fake_template_response)
    monkeypatch.setattr(main.settings, "environment", "production")

    response = await main._render_error_page(
        request,
        status_code=500,
        title="Something went wrong",
        message="Friendly message",
        detail="stack trace",
    )

    assert response.template == "errors/error.html"
    assert response.status_code == 500
    assert response.context["error_path"] == "/boom"
    assert response.context["request_id"] == "req-123"
    assert response.context["show_error_detail"] is False
    assert response.context["error_detail"] is None
    assert response.context["error_reference"]
    assert response.context["error_reference"] != "req-123"


@pytest.mark.anyio
async def test_render_error_page_shows_detail_for_super_admin_in_production(monkeypatch):
    request = _make_request(path="/admin/boom", query_string="secret=value")

    async def fake_get_optional_user(_request: Request):
        return {"id": 1, "is_super_admin": True}, None

    async def fake_build_portal_context(_request: Request, _user, *, extra=None):
        return {"request": _request, "is_super_admin": True, **(extra or {})}

    def fake_template_response(name: str, context: dict, status_code: int):
        return SimpleNamespace(template=name, context=context, status_code=status_code, headers={})

    monkeypatch.setattr(main, "_get_optional_user", fake_get_optional_user)
    monkeypatch.setattr(main, "_build_portal_context", fake_build_portal_context)
    monkeypatch.setattr(main.templates, "TemplateResponse", fake_template_response)
    monkeypatch.setattr(main.settings, "environment", "production")

    response = await main._render_error_page(
        request,
        status_code=403,
        title="Access denied",
        message="Friendly message",
        detail="admin-only detail",
        error_reference="err-admin-1",
    )

    assert response.context["error_path"] == "/admin/boom"
    assert response.context["show_error_detail"] is True
    assert response.context["error_detail"] == "admin-only detail"
    assert response.context["error_reference"] == "err-admin-1"


@pytest.mark.anyio
async def test_unexpected_exception_logs_and_renders_matching_error_reference(monkeypatch):
    request = _make_request(path="/explode", query_string="api_key=secret")
    logged: dict[str, object] = {}
    rendered: dict[str, object] = {}

    async def fake_render_error_page(_request: Request, **kwargs):
        rendered.update(kwargs)
        return SimpleNamespace(headers={})

    def fake_log_error(message: str, **meta):
        logged["message"] = message
        logged.update(meta)

    monkeypatch.setattr(main, "_render_error_page", fake_render_error_page)
    monkeypatch.setattr(main, "log_error", fake_log_error)

    response = await main.handle_unexpected_exception(request, RuntimeError("kaboom"))

    assert response.headers["X-Request-ID"] == "req-123"
    assert logged["message"] == "Unhandled application error"
    assert logged["request_id"] == "req-123"
    assert logged["request_path"] == "/explode"
    assert logged["error"] == "kaboom"
    assert rendered["detail"] == "kaboom"
    assert rendered["error_reference"] == logged["error_reference"]
    assert rendered["error_reference"] != "req-123"


@pytest.mark.anyio
async def test_http_exception_logs_generated_error_reference_for_html_pages(monkeypatch):
    request = _make_request(path="/missing", query_string="token=secret")
    logged: dict[str, object] = {}
    rendered: dict[str, object] = {}

    async def fake_render_error_page(_request: Request, **kwargs):
        rendered.update(kwargs)
        return SimpleNamespace(headers={})

    def fake_log_info(message: str, **meta):
        logged["message"] = message
        logged.update(meta)

    monkeypatch.setattr(main, "_render_error_page", fake_render_error_page)
    monkeypatch.setattr(main, "log_info", fake_log_info)

    response = await main.handle_http_exception(
        request,
        HTTPException(status_code=404, detail="missing record for token=secret"),
    )

    assert response.headers["X-Request-ID"] == "req-123"
    assert logged["message"] == "Rendering HTTP error page"
    assert logged["request_path"] == "/missing"
    assert logged["request_id"] == "req-123"
    assert logged["error_reference"] == rendered["error_reference"]
    assert rendered["status_code"] == 404
    assert rendered["title"] == "Page not found"
