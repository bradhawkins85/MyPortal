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
    event_store: dict[int, dict[str, object]] = {}

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

    async def fake_manual_event(**kwargs):
        captured["enqueue_event"] = kwargs
        event = {
            "id": 101,
            "status": "pending",
            "payload": kwargs.get("payload"),
            "target_url": kwargs.get("target_url"),
        }
        event_store[event["id"]] = event
        return event

    async def fake_record_manual_success(event_id: int, *, attempt_number: int, response_status: int | None, response_body: str | None):
        event = dict(event_store.get(event_id, {}))
        event.update(
            {
                "status": "succeeded",
                "attempt_count": attempt_number,
                "response_status": response_status,
                "response_body": response_body,
            }
        )
        event_store[event_id] = event
        return event

    async def fake_record_manual_failure(*_args, **_kwargs):  # pragma: no cover - success path only
        raise AssertionError("Failure recorder should not be called in success test")

    monkeypatch.setattr(email_service.webhook_monitor, "create_manual_event", fake_manual_event)
    monkeypatch.setattr(email_service.webhook_monitor, "record_manual_success", fake_record_manual_success)
    monkeypatch.setattr(email_service.webhook_monitor, "record_manual_failure", fake_record_manual_failure)

    result = asyncio.run(
        email_service.send_email(
            subject="Subject",
            recipients=["user@example.com"],
            text_body="Hello",
            html_body="<p>Hello</p>",
        )
    )

    sent, event_metadata = result
    assert sent is True
    assert captured["host"] == "smtp.example.com"
    assert captured["port"] == 587
    assert captured["login"] == ("noreply@example.com", "secret")
    message = captured["message"]
    assert message["Subject"] == "Subject"
    assert "user@example.com" in message["To"]
    assert captured["enqueue_event"]["payload"]["recipients"] == ["user@example.com"]
    assert event_metadata["status"] == "succeeded"
    assert event_metadata["response_status"] == 250


def test_send_email_skips_without_smtp(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "smtp_host", None)

    async def fail_enqueue(**_kwargs):  # pragma: no cover - should not be called
        raise AssertionError("create_manual_event should not be invoked when SMTP is disabled")

    monkeypatch.setattr(email_service.webhook_monitor, "create_manual_event", fail_enqueue)

    result = asyncio.run(
        email_service.send_email(
            subject="No SMTP",
            recipients=["user@example.com"],
            text_body="Body",
            html_body="<p>Body</p>",
        )
    )

    assert result == (False, None)


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

    event_state: dict[str, object] = {"status": "pending", "id": 202}

    async def fake_manual_event(**kwargs):
        event_state.update({"payload": kwargs.get("payload"), "target_url": kwargs.get("target_url")})
        return dict(event_state)

    async def fake_record_manual_failure(event_id: int, *, attempt_number: int, status: str, error_message: str | None, response_status: int | None, response_body: str | None):
        event_state.update(
            {
                "id": event_id,
                "status": "failed",
                "attempt_count": attempt_number,
                "response_status": response_status,
                "response_body": response_body,
                "last_error": error_message,
            }
        )
        return dict(event_state)

    async def fake_record_manual_success(*_args, **_kwargs):  # pragma: no cover - failure path only
        raise AssertionError("Success recorder should not be called in failure test")

    monkeypatch.setattr(email_service.webhook_monitor, "create_manual_event", fake_manual_event)
    monkeypatch.setattr(email_service.webhook_monitor, "record_manual_failure", fake_record_manual_failure)
    monkeypatch.setattr(email_service.webhook_monitor, "record_manual_success", fake_record_manual_success)

    with pytest.raises(email_service.EmailDispatchError):
        asyncio.run(
            email_service.send_email(
                subject="Failure",
                recipients=["user@example.com"],
                text_body="Body",
                html_body="<p>Body</p>",
            )
        )

    assert event_state["status"] == "failed"
    assert event_state["attempt_count"] == 1
    assert event_state["last_error"] == "failure"
