from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import database as database_dependencies
from app.api.routes import auth as auth_routes
from app.core.config import get_settings
from app.core.database import db
from app.main import app, scheduler_service


@pytest.fixture(autouse=True)
def mock_startup(monkeypatch):
    async def fake_connect():
        return None

    async def fake_disconnect():
        return None

    async def fake_run_migrations():
        return None

    async def fake_start():
        return None

    async def fake_stop():
        return None

    monkeypatch.setattr(db, "connect", fake_connect)
    monkeypatch.setattr(db, "disconnect", fake_disconnect)
    monkeypatch.setattr(db, "run_migrations", fake_run_migrations)
    monkeypatch.setattr(scheduler_service, "start", fake_start)
    monkeypatch.setattr(scheduler_service, "stop", fake_stop)


def test_password_forgot_sends_email(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "portal_url", "https://portal.example.com")
    monkeypatch.setattr(settings, "enable_csrf", False)

    tokens: dict[str, object] = {}
    email_payload: dict[str, object] = {}

    async def fake_get_user_by_email(email: str):
        return {"id": 42, "email": email, "first_name": "Test"}

    async def fake_create_password_reset_token(*, user_id: int, token: str, expires_at: datetime):
        tokens["user_id"] = user_id
        tokens["token"] = token
        tokens["expires_at"] = expires_at

    async def fake_send_email(**kwargs):
        email_payload.update(kwargs)
        return True, {"id": 11, "status": "succeeded"}

    def fake_token_hex(size: int) -> str:
        return "fixedtoken"

    monkeypatch.setattr(auth_routes.user_repo, "get_user_by_email", fake_get_user_by_email)
    monkeypatch.setattr(auth_routes.auth_repo, "create_password_reset_token", fake_create_password_reset_token)
    monkeypatch.setattr(auth_routes.email_service, "send_email", fake_send_email)
    monkeypatch.setattr(auth_routes.secrets, "token_hex", fake_token_hex)

    app.dependency_overrides[database_dependencies.require_database] = lambda: None

    try:
        with TestClient(app) as client:
            response = client.post(
                "/auth/password/forgot",
                json={"email": "user@example.com"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert tokens["user_id"] == 42
    assert tokens["token"] == "fixedtoken"
    assert email_payload["recipients"] == ["user@example.com"]
    assert "fixedtoken" in email_payload["text_body"]
    assert "https://portal.example.com/reset-password?token=fixedtoken" in email_payload["text_body"]


def test_reset_password_page_accepts_email_token_link(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "enable_csrf", False)

    async def fake_load_session(request):
        return None

    monkeypatch.setattr("app.main.session_manager.load_session", fake_load_session)

    with TestClient(app) as client:
        response = client.get("/reset-password?token=fixedtoken")

    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "Choose a new password" in response.text
    assert 'value="fixedtoken"' in response.text
    assert 'data-endpoint="/auth/password/reset"' in response.text


def test_forgot_password_page_loads_form(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "enable_csrf", False)

    async def fake_load_session(request):
        return None

    monkeypatch.setattr("app.main.session_manager.load_session", fake_load_session)

    with TestClient(app) as client:
        response = client.get("/forgot-password")

    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "Reset your password" in response.text
    assert 'data-endpoint="/auth/password/forgot"' in response.text
