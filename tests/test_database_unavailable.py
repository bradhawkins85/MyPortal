"""Temporary database failures return promptly without querying error-page context."""

from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import main
from app.core.database import DatabasePoolTimeout, DatabaseQueryTimeout, DatabaseTimeoutError


@pytest.mark.parametrize("error_type", [DatabasePoolTimeout, DatabaseQueryTimeout])
@pytest.mark.parametrize("path, accept, expects_json", [
    ("/api/rmm/gitea/identity", "*/*", True),
    ("/dashboard", "application/json", True),
    ("/dashboard", "text/html", False),
    ("/dashboard", "text/plain", False),
])
def test_database_timeout_returns_safe_retryable_response(
    monkeypatch, error_type, path, accept, expects_json,
):
    render_error = AsyncMock(side_effect=AssertionError("Do not load database-backed error context"))
    optional_user = AsyncMock(side_effect=AssertionError("Do not query the user again"))
    log_warning = Mock()
    monkeypatch.setattr(main, "_render_error_page", render_error)
    monkeypatch.setattr(main, "_get_optional_user", optional_user)
    monkeypatch.setattr(main, "log_warning", log_warning)
    app = FastAPI(exception_handlers={
        DatabaseTimeoutError: main.app.exception_handlers[DatabaseTimeoutError],
    })

    @app.get(path)
    async def unavailable():
        raise error_type("private database details must not appear in the response")

    with TestClient(app) as client:
        response = client.get(
            path + "?token=secret-query-value",
            headers={"Accept": accept, "X-Request-ID": "database-request-1"},
        )

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Request-ID"] == "database-request-1"
    assert "private database details" not in response.text
    assert "secret-query-value" not in response.text
    if expects_json:
        assert response.json() == {
            "detail": "Service temporarily unavailable. Please try again shortly.",
            "request_id": "database-request-1",
            "error_reference": "database-request-1",
        }
    else:
        assert response.headers["Content-Type"].startswith("text/plain")
    render_error.assert_not_awaited()
    optional_user.assert_not_awaited()
    log_warning.assert_called_once_with(
        "Database temporarily unavailable",
        event="database.unavailable",
        request_id="database-request-1",
        path=path,
        error_type=error_type.__name__,
    )


def test_unrelated_timeouts_are_not_classified_as_database_overload():
    app = FastAPI(exception_handlers={
        DatabaseTimeoutError: main.handle_database_timeout,
    })

    @app.get("/timeout")
    async def unrelated_timeout():
        raise TimeoutError("some other operation timed out")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/timeout")

    assert response.status_code == 500
    assert "Retry-After" not in response.headers


def test_database_timeout_in_middleware_avoids_database_backed_error_page(monkeypatch):
    render_error = AsyncMock(side_effect=AssertionError("Do not load database-backed error context"))
    monkeypatch.setattr(main, "_render_error_page", render_error)
    monkeypatch.setattr(main, "log_warning", Mock())
    app = FastAPI(exception_handlers={
        DatabaseTimeoutError: main.handle_database_timeout,
        Exception: main.handle_unexpected_exception,
    })

    class UnavailableMiddleware:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http":
                raise DatabasePoolTimeout("middleware database query exhausted the pool")
            await self.app(scope, receive, send)

    app.add_middleware(UnavailableMiddleware)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/dashboard", headers={"Accept": "text/html"})

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    assert "middleware database query" not in response.text
    render_error.assert_not_awaited()
