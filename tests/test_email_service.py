import asyncio

import pytest

from app.core.config import get_settings
from app.services import email as email_service


def test_send_email_success(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_port", 587)
    monkeypatch.setattr(settings, "smtp_user", "noreply@example.com")
    monkeypatch.setattr(settings, "smtp_password", "secret")
    monkeypatch.setattr(settings, "smtp_use_tls", True)

    captured: dict[str, object] = {}

    class DummySMTP:
        def __init__(self, host: str, port: int, timeout: float):
            captured["host"] = host
            captured["port"] = port
            captured["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def ehlo(self):
            captured.setdefault("ehlo", 0)
            captured["ehlo"] = int(captured["ehlo"]) + 1

        def starttls(self, context=None):
            captured["starttls"] = True

        def login(self, username: str, password: str):
            captured["login"] = (username, password)

        def send_message(self, message):
            captured["message"] = message

    monkeypatch.setattr(email_service.smtplib, "SMTP", DummySMTP)

    result = asyncio.run(
        email_service.send_email(
            subject="Subject",
            recipients=["user@example.com"],
            text_body="Hello",
            html_body="<p>Hello</p>",
        )
    )

    assert result is True
    assert captured["host"] == "smtp.example.com"
    assert captured["port"] == 587
    assert captured["login"] == ("noreply@example.com", "secret")
    message = captured["message"]
    assert message["Subject"] == "Subject"
    assert "user@example.com" in message["To"]


def test_send_email_skips_without_smtp(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "smtp_host", None)

    result = asyncio.run(
        email_service.send_email(
            subject="No SMTP",
            recipients=["user@example.com"],
            text_body="Body",
            html_body="<p>Body</p>",
        )
    )

    assert result is False


def test_send_email_raises_on_failure(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_port", 587)
    monkeypatch.setattr(settings, "smtp_user", "noreply@example.com")
    monkeypatch.setattr(settings, "smtp_password", "secret")
    monkeypatch.setattr(settings, "smtp_use_tls", False)

    class FailingSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def ehlo(self):
            return None

        def login(self, *_args, **_kwargs):
            return None

        def send_message(self, _message):
            raise email_service.smtplib.SMTPException("failure")

    monkeypatch.setattr(email_service.smtplib, "SMTP", FailingSMTP)

    with pytest.raises(email_service.EmailDispatchError):
        asyncio.run(
            email_service.send_email(
                subject="Failure",
                recipients=["user@example.com"],
                text_body="Body",
                html_body="<p>Body</p>",
            )
        )
