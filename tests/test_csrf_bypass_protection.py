"""Test that CSRF validation is still applied when a session cookie is
present, even if the request also carries an Authorization/X-API-Key
header – this prevents a trivial CSRF bypass where an attacker forges a
cross-origin POST that just happens to include one of those headers.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.security.csrf import CSRFMiddleware


@dataclass
class _DummySession:
    csrf_token: str
    user_id: int = 1


class _DummySessionManager:
    def __init__(self, token: str) -> None:
        self._session = _DummySession(csrf_token=token)

    async def load_session(self, request, allow_inactive: bool = False):
        return self._session


def _make_app() -> TestClient:
    app = FastAPI()
    app.add_middleware(CSRFMiddleware, manager=_DummySessionManager("expected"))

    @app.post("/api/things")
    async def _things_endpoint() -> JSONResponse:
        return JSONResponse({"ok": True})

    return TestClient(app)


def test_bearer_without_session_cookie_skips_csrf():
    """Pure API clients (no session cookie) are unaffected – still skipped."""

    client = _make_app()
    response = client.post(
        "/api/things",
        json={"x": 1},
        headers={"Authorization": "Bearer abc"},
    )
    assert response.status_code == 200


def test_bearer_with_session_cookie_still_requires_csrf():
    """Attacker sending Authorization header with a stolen-cookie browser
    should NOT bypass CSRF. Without a matching CSRF token the request
    must be rejected.
    """

    client = _make_app()
    from app.core.config import get_settings

    cookie_name = get_settings().session_cookie_name
    response = client.post(
        "/api/things",
        json={"x": 1},
        headers={"Authorization": "Bearer attacker"},
        cookies={cookie_name: "any"},
    )
    assert response.status_code == 403


def test_api_key_with_session_cookie_still_requires_csrf():
    client = _make_app()
    from app.core.config import get_settings

    cookie_name = get_settings().session_cookie_name
    response = client.post(
        "/api/things",
        json={"x": 1},
        headers={"X-API-Key": "attacker"},
        cookies={cookie_name: "any"},
    )
    assert response.status_code == 403


def test_bearer_with_session_cookie_accepts_valid_csrf():
    client = _make_app()
    from app.core.config import get_settings

    cookie_name = get_settings().session_cookie_name
    response = client.post(
        "/api/things",
        json={"x": 1},
        headers={"Authorization": "Bearer abc", "X-CSRF-Token": "expected"},
        cookies={cookie_name: "any"},
    )
    assert response.status_code == 200
