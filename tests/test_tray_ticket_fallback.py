"""Regression tests for the unauthenticated tray ticket fallback."""

from urllib.parse import urlencode

import pytest
from starlette.requests import Request

from app.api.routes import tray


def _post_request(data: dict[str, str]) -> Request:
    body = urlencode(data).encode()
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "server": ("portal.example.com", 443),
            "path": "/api/tray/ticket-form/fallback",
            "query_string": b"",
            "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
            "client": ("192.0.2.1", 1234),
        },
        receive,
    )


def test_fallback_form_uses_invisible_recaptcha_and_disclosure(monkeypatch):
    monkeypatch.setattr(tray._settings, "recaptcha_site_key", "site-key")

    html = tray._render_ticket_form(
        token="",
        csrf="",
        mode="myportal",
        questions=[],
        unauthenticated=True,
    )

    assert 'data-size="invisible"' in html
    assert 'data-callback="submitFallbackTicket"' in html
    assert "This site is protected by reCAPTCHA" in html
    assert "https://policies.google.com/privacy" in html
    assert "https://policies.google.com/terms" in html


@pytest.mark.anyio
async def test_fallback_rejects_missing_recaptcha(monkeypatch):
    monkeypatch.setattr(tray._settings, "recaptcha_site_key", "site-key")
    monkeypatch.setattr(tray._settings, "recaptcha_secret_key", "secret-key")
    monkeypatch.setattr(
        tray.tq_service, "get_questions_for_company", lambda _id: _empty()
    )
    monkeypatch.setattr(tray.site_settings_repo, "get_tray_icon_tooltip_name", _brand)

    response = await tray.tray_ticket_form_fallback_submit(
        _post_request(
            {
                "name": "Jamie",
                "email": "jamie@example.com",
                "subject": "Help",
                "computer_name": "RECEPTION-PC",
            }
        )
    )

    assert response.status_code == 400
    assert "Spam verification failed" in response.body.decode()


@pytest.mark.anyio
async def test_fallback_includes_computer_name_in_created_ticket(monkeypatch):
    monkeypatch.setattr(tray._settings, "recaptcha_site_key", "site-key")
    monkeypatch.setattr(tray._settings, "recaptcha_secret_key", "secret-key")
    monkeypatch.setattr(tray, "_verify_recaptcha", _verified)
    monkeypatch.setattr(
        tray.tq_service, "get_questions_for_company", lambda _id: _empty()
    )
    monkeypatch.setattr(tray.site_settings_repo, "get_tray_icon_tooltip_name", _brand)
    monkeypatch.setattr(tray.users_repo, "get_user_by_email", _no_user)
    monkeypatch.setattr(tray.tickets_service, "resolve_status_or_default", _status)
    captured = {}

    async def create_ticket(**kwargs):
        captured.update(kwargs)
        return {"id": 91, "ticket_number": "T-91"}

    monkeypatch.setattr(tray.tickets_service, "create_ticket", create_ticket)
    response = await tray.tray_ticket_form_fallback_submit(
        _post_request(
            {
                "name": "Jamie",
                "email": "jamie@example.com",
                "subject": "Help",
                "computer_name": "RECEPTION-PC",
                "g-recaptcha-response": "valid",
            }
        )
    )

    assert response.status_code == 200
    assert "T-91" in response.body.decode()
    assert "RECEPTION-PC" in captured["description"]
    assert captured["requester_email"] == "jamie@example.com"


async def _empty():
    return []


async def _brand():
    return "Support"


async def _verified(_token, _request):
    return True


async def _no_user(_email):
    return None


async def _status(_value):
    return "open"
